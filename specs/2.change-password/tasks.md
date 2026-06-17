# change-password — 任务清单

## 任务版本

| 日期       | 版本 | 说明     |
| ---------- | ---- | -------- |
| 2026-06-17 | v1   | 初始任务 |

## 项目信息

- 项目名: user-auth
- 架构类型: 单体 Serverless
- specs 路径: specs/2.change-password/

## 任务列表

### 功能 1: 后端修改密码

- [x] T-001: store.py 增加 `update_password(email, new_hash)`，update_item 更新 passwordHash ~15min
- [x] T-002: handlers.py 抽 `_email_from_headers` 复用，新增 `handle_change_password`（token→验旧→校验新→新旧不同→哈希→更新）~30min
- [x] T-003: app.py 路由新增 `POST /change-password` 分发 ~5min

### 功能 2: 部署与前端

- [x] T-004: template.yaml 给 AuthFunction 增加 /change-password POST 事件 ~5min
- [x] T-005: frontend/index.html 增加"修改密码"表单（旧/新密码 + 按钮，复用 api()）~15min

### 集成与测试

- [x] T-006: tests/test_auth.py 增加修改密码用例（成功/旧密码错/新密码弱/新旧相同/未认证）~15min

## 依赖关系

- T-002 依赖 T-001
- T-003 依赖 T-002
- T-006 依赖 T-001~T-003
- 跨 feature：本 feature(序号 2) 复用 feature 1(user-auth) 的 auth/ 五模块

## 风险点

- update_item 若用户不存在会创建空项 → 在 handler 中先确认用户存在（token 已解析出 email 且 get_user 命中）后再更新。
