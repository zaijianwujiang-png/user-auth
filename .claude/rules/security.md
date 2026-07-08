---
description: 密钥、PII 与外部调用安全规范
---

# 安全规范

- 密钥(JWT、Claude API key、Telegram bot token、交易所 key)一律走 SAM Parameters(NoEcho)/环境变量,绝不进代码库与日志
- 交易所 API key 只开最小权限:现货交易,禁提币,配 IP 白名单(v2+ 适用)
- 密码只存 bcrypt 哈希;token 用 PyJWT,校验 exp
- 日志不打印 PII、完整 token、密钥;外部响应体入日志前截断
- 对外 HTTP 全部设超时 + 重试上限;解析外部 JSON 按不可信输入处理(字段缺失/类型异常不崩)
- LLM 输出按不可信数据处理:先 schema 校验再使用,不直接拼进后续指令
