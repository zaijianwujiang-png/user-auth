---
description: pytest + moto 测试规范
globs: "tests/**/*.py"
---

# 测试规范

- 框架:pytest;AWS 资源一律用 moto mock(@mock_aws),不打真实云
- 外部 HTTP(Truth Social、Claude API、Telegram)必须 mock,测试不出网
- 每个 handler 至少覆盖:正常路径、参数校验失败、依赖故障(如 DynamoDB 抛错)
- 测试文件按 feature 命名:tests/test_<feature>.py
- 跑法:pytest tests/ -v;提交前必须全绿
