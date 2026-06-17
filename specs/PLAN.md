# 路线图 PLAN

## Features

| Feature | 说明 | 状态 | 路线图 |
|---------|------|------|--------|
| 1. user-auth | 用户注册/登录/当前用户(MVP) | ✅ 实现完成(22/22, 测试 11 passed) | [roadmap](user-auth/roadmap.md) |
| 2. change-password | 登录态下旧密码换新密码 | ✅ 实现完成(6/6, 测试 13 passed) | [specs](2.change-password/) |

## 本次 PRD（2026-06-17）切分

| 序号 | feature | 说明 | 依赖 | 状态 |
| ---- | ------- | ---- | ---- | ---- |
| 2 | change-password | 登录态下旧密码换新密码 | 1.user-auth | ✅ 完成 |

**推荐执行顺序**：2（单 feature）
**ID 约定**：跨 feature 引用加 `{序号}.` 前缀，如 `2.T-002`。

## user-auth 版本

| 版本 | 内容 | 状态 |
|------|------|------|
| v1-mvp | 注册 + 登录 + 当前用户 + 安全约束 | ✅ 完成 22/22,11 测试通过,待 sam deploy |
| v2-recovery | 找回密码/邮箱验证/登出/三方登录 | 展望 |
