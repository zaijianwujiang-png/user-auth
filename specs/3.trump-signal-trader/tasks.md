# trump-signal-trader — 任务清单（v1）

## 任务版本

| 日期       | 版本 | 说明                           |
| ---------- | ---- | ------------------------------ |
| 2026-07-07 | v1   | 初始任务（仅 v1：采集+信号+验证+通知，不含交易）|

## 项目信息

- 项目名: trump-signal-trader
- 架构类型: 单体 Serverless（Python 3.12 Lambda + EventBridge + DynamoDB）
- specs 路径: specs/3.trump-signal-trader/
- 代码目录: signal/（新建，与 auth/ 平级）

## 任务列表

### Phase 1: 基础设施与数据层

- [x] T-001: template.yaml 新增 SignalFunction（EventBridge rate 1 minute 触发）+ SignalTable + NoEcho 参数（AnthropicApiKey/TelegramBotToken/TelegramChatId）+ DynamoDB 权限 `other:template.yaml` ~30min
- [x] T-002: signal/config.py + signal/store.py — 环境变量配置集中读取；DynamoDB 单表读写（帖子 META、决策 STEP 追加、HASH 索引条件写、SOURCE 状态），含 TTL `database:signal/config.py, signal/store.py` ~30min

### Phase 2: 采集与去重

- [x] T-003: signal/collector.py — 主方案：Truth Social 未认证 statuses API 轮询（浏览器 UA、超时、退避重试），统一返回 `list[Post]` `backend:signal/collector.py` ~30min
- [x] T-004: signal/collector.py — 降级方案：RSS 镜像解析；主源连续失败 N 次自动切换 + 恢复切回，源状态读写走 store `backend:signal/collector.py` ~30min
- [x] T-005: signal/dedup.py — 帖子 ID 幂等（条件写）+ 内容规范化哈希 + 30 天旧闻窗口检测 `backend:signal/dedup.py` ~15min

### Phase 3: 信号提取与交叉验证

- [x] T-006: signal/extractor.py — claude-fable-5 结构化提取（JSON: assets/direction/confidence/reason），无关帖标记 IRRELEVANT 短路 `backend:signal/extractor.py` ~30min
- [x] T-007: signal/validator.py — claude-sonnet-5 独立判断 + 双模型一致性比对（资产交集 + 方向一致），分歧记录双方结论 `backend:signal/validator.py` ~30min
- [x] T-008: signal/market.py — 币安公开行情 REST 拉价格/成交量，price-in 判定（阈值可配），输出行情快照 `backend:signal/market.py` ~30min
- [x] T-009: signal/factcheck.py — Claude web search 工具事实核查，输出 confirmed/unverified/suspicious，仅 confirmed 通过 `backend:signal/factcheck.py` ~30min

### Phase 4: 通知与编排

- [x] T-010: signal/notifier.py — Telegram Bot 发送（信号/告警/否决三类消息模板 + 失败重试）`backend:signal/notifier.py` ~30min
- [x] T-011: signal/app.py — Lambda 入口 + pipeline 编排（采集→去重→提取→双模型→行情→核查→通知），每步落库决策记录，任一层否决短路，异常捕获发告警 `backend:signal/app.py` ~30min

### Phase 5: 测试

- [x] T-012: tests/test_signal.py — 单元测试（moto 模拟 DynamoDB + mock 外部 HTTP/LLM）：ID 去重、旧闻哈希、无关帖短路、双模型分歧否决、price-in 否决、核查否决、全通过发信号、回查全链路 `other:tests/test_signal.py` ~1h

## 依赖关系

- T-002 依赖 T-001（表名/参数约定）
- T-004 依赖 T-003（同文件的降级扩展）、T-002（源状态存储）
- T-005 依赖 T-002
- T-011 依赖 T-002~T-010（编排全部模块）
- T-012 依赖 T-011
- **可并行**：T-003 与 T-002 并行；T-005~T-010 六条在 T-002 完成后可全部并行（互不依赖，分属不同文件）
- 工种标注：`backend` = wj-backend-engineer，`database` = wj-database-engineer，`other` = 主流程直接执行

## 风险点

- Truth Social 未认证接口可能被 Cloudflare 拦截 → T-003 实测失败就把 RSS 镜像（T-004）升为主源，接口抽象已隔离该变化。
- EventBridge 最小粒度 1 分钟，极端情况下延迟 ~2 分钟 → 需求定位中低频可接受；写入 requirements F-001 说明。
- LLM 返回非法 JSON → extractor/validator 做 schema 校验 + 一次重试，仍失败按系统异常告警，不误发信号。
- web search 核查耗时可能拉长单次执行 → Lambda Timeout 设 120s，且仅在前四层全过后才调用。
