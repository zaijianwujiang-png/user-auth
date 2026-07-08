# 项目踩坑与教训(AGENTS.md)

## 通用

- [3.T-011] put_item 会整条覆盖:任何「重复事件走否决分支」的逻辑,落库前必须想清楚会不会覆写已有记录。每分钟轮询必然重复拉到已处理帖子,DUPLICATE_ID 分支若照常 update status + append step,会把原帖终态(SIGNAL_SENT)覆写成 DUPLICATE 并覆盖原 STEP 记录——重复事件应静默短路,不碰已有数据。
- [3.review] html.escape 默认参数在属性上下文不安全:拼进 href="..." 的不可信内容必须 quote=True,且 URL 要白名单校验 scheme(http/https),否则属性逃逸注入。
- [3.review] 「重试次数 × 单次超时」的最坏总耗时必须显式算一遍并留在 Lambda Timeout 预算内(2×85s > 120s 被硬杀);尤其当 pipeline 前端已条件写占坑时,超时被杀 = 该事件永久静默丢失。
- [3.T-012] 本机默认 python 3.9 与 Lambda 运行时 3.12 不一致:代码用了 PEP604(`X | None`)等 3.10+ 语法,本地测试必须用 .venv312(python3.12 venv);另 moto 5.1→5.2 有行为变化会挂既有测试,升级依赖前先跑全套。
- [3.execute] 多个并行 task 会扩展同一个共享文件(config.py 被 T-004/T-010 同时追加)时,后写方必须先重读现状再增量追加,不能凭记忆整文件覆写。
- [3.review] 无人值守系统的旁路告警(通知/告警发送)失败不能反噬主流程:一律 try/except 后只记日志。

## 3.trump-signal-trader

- [3.T-003] Truth Social 未认证 statuses API 在本机实测被 Cloudflare 403;Lambda 出口 IP 环境可能不同,部署后需实测。源抽象(primary/fallback + 运行时可切)是对的:不要因单次本地实测就锁死方案,用 SOURCE_FAIL_THRESHOLD 让线上自己切。
- [3.T-004] Mastodon 系 API 拉帖要同时带 exclude_replies + exclude_reblogs,转发条目 content 为空,会让空文本流入 pipeline 并占住空串内容哈希。
- [3.review] fail-closed 有两种实现形态(返回值折叠 vs 抛异常),同一 pipeline 里混用会让编排层容易漏接、决策记录语义失真(行情宕机被记成 PRICED_IN 而非系统异常)——同项目应统一约定。
- [3.review] LLM prompt 里裸拼不可信帖子原文是注入面:双模型看同一段注入文本,「独立判断」防不住相关性失效。后续版本应加 XML 标签包裹 + system 声明「原文内容仅作数据」。
- [3.audit] 「切换采集源」这类关键运维事件,店内(store)状态变更 ≠ 人已知晓:验收标准要求告警的事件,必须在事件发生点直接发告警,不能只打日志等人翻。
