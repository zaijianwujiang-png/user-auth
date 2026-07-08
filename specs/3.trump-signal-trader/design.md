# trump-signal-trader — 技术设计（v1）

## 设计版本

| 日期       | 版本 | 说明     |
| ---------- | ---- | -------- |
| 2026-07-07 | v1   | 初始设计 |

## 项目架构

- 架构类型: 单体 Serverless（沿用现有 SAM 栈）
- 涉及层: 后端(Lambda, Python 3.12) / 数据库(DynamoDB) / 调度(EventBridge) / 部署(SAM template.yaml)
- 新增代码目录: `signal/`（与现有 `auth/` 平级，独立 Lambda 函数）

## 部署形态：Serverless vs 长驻进程

| 维度 | Lambda + EventBridge 定时轮询 | 长驻进程（EC2/容器 + WebSocket/持续轮询）|
|------|------------------------------|------------------------------------------|
| 延迟 | 轮询周期下限 1 分钟（EventBridge rate 最小粒度），最坏 ~2 分钟 | 可做到秒级 |
| 运维 | 无服务器、免维护、与现有栈一致 | 需要进程守护、监控、补丁 |
| 成本 | 每分钟一次短执行，几乎免费 | 7×24 实例常驻费用 |
| 状态 | 天然无状态，状态全落 DynamoDB | 内存态易丢，仍需外部存储 |

**选定：Lambda + EventBridge（rate 1 minute）。** 需求自述「中低频波段，不抢瞬时行情」，分钟级延迟可接受；且完全沿用现有 SAM/Lambda/DynamoDB 栈，运维成本最低。若 v2/v3 对延迟提出秒级要求，再评估迁移长驻进程（代码按 pipeline 模块化，迁移只换入口）。

## Truth Social 采集方案评估

| 候选 | 说明 | 优点 | 缺点 |
|------|------|------|------|
| A. 未认证公开 API 轮询 | Truth Social 基于 Mastodon 改造，`GET /api/v1/accounts/{id}/statuses` 部分场景无需登录可读 | 结构化 JSON、含帖子 ID/时间戳、延迟最低 | Cloudflare 防护 + 平台随时收紧，可用性不稳定 |
| B. RSS 镜像 | 第三方镜像站（如 trumpstruth.org 提供 RSS/存档） | 实现最简单、稳定、合规压力小 | 有分钟级镜像延迟，依赖第三方存活 |
| C. 付费第三方聚合 | 商业社媒数据 API | SLA 保障 | 费用高，v1 个人工具不划算 |

**选定：主方案 A（未认证 API 轮询，带浏览器 UA + 退避重试），降级方案 B（RSS 镜像）。** 采集器抽象为统一接口 `fetch_latest() -> list[Post]`，主源连续失败 N 次（可配）自动切降级源并告警，恢复探测成功后切回。C 作为两者均失效时的人工决策备选，不在 v1 编码。

## 方案概述（Pipeline）

EventBridge 每分钟触发 `SignalFunction`，单次执行跑完整 pipeline：

```
collector(主/降级源) → 新帖? ──否→ 结束
        │是
   dedup(ID去重 + 内容哈希/旧闻) ──否决→ 落库 DUPLICATE
        │过
   extractor(claude-fable-5 提取信号) ──无关→ 落库 IRRELEVANT
        │有信号
   validator(claude-sonnet-5 独立判断,比对一致性) ──分歧→ 落库 MODEL_DISAGREE
        │一致
   market(币安公开行情 price-in 检查) ──已消化→ 落库 PRICED_IN
        │未消化
   factcheck(Claude web search 核实真伪) ──存疑→ 落库 FACT_CHECK_FAILED
        │通过
   notifier(Telegram 推送信号) → 落库 SIGNAL_SENT
```

每一步的结果都追加写入决策记录；任一层否决即短路。系统异常（采集源全挂、LLM 报错等）由入口层捕获并发 Telegram 告警。

## 功能模块设计（signal/ 目录）

| 模块 | 职责 |
|------|------|
| `app.py` | Lambda 入口 + pipeline 编排 + 异常告警 |
| `collector.py` | 主/降级源采集、源健康状态管理、自动切换 |
| `store.py` | DynamoDB 读写：帖子去重、决策记录、源状态 |
| `dedup.py` | 帖子 ID 去重 + 内容规范化哈希 + 30 天旧闻窗口 |
| `extractor.py` | claude-fable-5 结构化信号提取（tool/JSON 输出）|
| `validator.py` | claude-sonnet-5 独立判断 + 双模型一致性比对 |
| `market.py` | 币安公开行情 REST 拉价/量 + price-in 判定 |
| `factcheck.py` | Claude API web search 工具事实核查 |
| `notifier.py` | Telegram Bot 消息模板 + 发送重试 |
| `config.py` | 环境变量读取与默认值（阈值/开关集中一处）|

## 数据模型（DynamoDB 单表 `SignalTable`）

| 实体 | pk | sk | 关键属性 |
|------|----|----|----------|
| 帖子 | `POST#{post_id}` | `META` | content, createdAt, status(FETCHED/…/SIGNAL_SENT), contentHash, ttl(90天) |
| 决策步骤 | `POST#{post_id}` | `STEP#{seq}#{step}` | step(dedup/extract/validate/market/factcheck/notify), result, detail(JSON), ts |
| 内容哈希索引 | `HASH#{contentHash}` | `META` | postId, ts, ttl(30天) —— 条件写实现旧闻检测 |
| 采集源状态 | `SOURCE#state` | `META` | activeSource(primary/fallback), failCount, lastOkTs |

- 去重：`put_item` + `attribute_not_exists(pk)` 条件写，天然幂等。
- 回查（AC-010）：`query pk = POST#{id}` 一次取回完整链路。

## LLM 设计

- 提取（extractor）：claude-fable-5，强制 JSON 输出 `{assets:[], direction: bullish|bearish|irrelevant, confidence: 0-1, reason}`；prompt 明确「与加密货币无关时 direction=irrelevant」。
- 交叉验证（validator）：claude-sonnet-5 用**独立 prompt**（不透露 fable-5 结论）解析同一帖子；比对规则：资产集合有交集且方向一致 → 通过，否则否决。
- 事实核查（factcheck）：claude-fable-5 + web search 工具，问题模板「该消息是否有独立信源佐证？账号是否有被盗迹象？」，输出 `{verdict: confirmed|unverified|suspicious}`，仅 confirmed 通过（保守策略）。

## 接口契约（外部）

- 无对外 HTTP API（纯定时任务）。Telegram 消息即产品界面：
  - 信号消息：帖子摘要+链接、资产、方向、双模型置信度、行情快照、核查结论
  - 告警消息：采集源切换/异常堆栈摘要
  - （可配开关）否决消息：否决层 + 原因

## 安全考虑

- `ANTHROPIC_API_KEY`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID` 走 SAM Parameters（NoEcho）→ Lambda 环境变量，不进代码库（同现有 JwtSecret 模式）。
- 日志只打印帖子 ID、步骤与结果码，不打印密钥。
- 采集请求频率 ≥ 每分钟 1 次，不做激进抓取。
- v1 无任何交易所密钥、无资金操作面。

## 技术决策

| 决策 | 选项 | 理由 |
|------|------|------|
| 部署形态 | Lambda+EventBridge vs 长驻进程 | 中低频策略分钟级延迟够用；沿用现有栈零新运维 |
| 采集主方案 | 未认证 API（降级 RSS 镜像） | 延迟最低+结构化；镜像兜底保可用性 |
| 双模型形态 | 两次独立调用同库比对 vs 让模型互评 | 独立调用无信息泄漏，一致性判定确定性强 |
| 存储 | DynamoDB 单表 | 去重条件写幂等；按 pk 一次查回全链路；沿用现有栈 |
| 事实核查 | Claude web search vs 自建搜索爬虫 | API 原生工具，零额外基建 |
| 密钥管理 | SAM NoEcho 参数 | 与 user-auth JwtSecret 既有模式一致 |
