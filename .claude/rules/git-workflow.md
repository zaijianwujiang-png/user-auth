---
description: git 提交约定
---

# git 约定

- 提交信息:`feat|fix|docs|test: 中文摘要`(沿用现有历史风格,如 "feat: user-auth v1 — …")
- 一个 feature 的开发在 main 上小步提交;每完成一条 task 同步勾选 specs/<feature>/tasks.md
- 不提交:密钥、.aws-sam/、__pycache__、本地配置
- 改代码前先读对应 specs/ 文档;spec 与实现冲突时先改 spec 再改码
