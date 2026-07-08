---
description: Python 代码风格约定
globs: "**/*.py"
---

# 代码风格

- Python 3.12,标准库优先;Lambda 运行时只带必要三方依赖(bcrypt、PyJWT、boto3 由层/打包提供)
- 模块划分沿用 auth/ 的模式:app.py(路由入口)、handlers.py(业务)、store.py(数据访问)、validators.py(校验)、security.py(安全工具)
- 中文注释,解释「为什么」而不是「做什么」;公共函数带简短 docstring
- 常量集中在模块顶部,魔法数字要命名
- 错误返回统一 JSON:{"error": "<机器可读码>", "message": "<人话>"}
