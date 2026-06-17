# change-password — 技术设计

## 设计版本

| 日期       | 版本 | 说明     |
| ---------- | ---- | -------- |
| 2026-06-17 | v1   | 初始设计 |

## 项目架构

- 架构类型: 单体 Serverless
- 涉及层: 后端(Lambda) / 数据库(DynamoDB) / 前端(HTML) / 部署(SAM)

## 功能模块设计

### 模块 1: 存储层 — 更新密码

`store.py` 新增 `update_password(email, new_hash)`：用 `update_item` 把指定用户的 `passwordHash` 改为新值。复用现有 `_table()` 与 `TABLE_NAME`。

### 模块 2: 业务层 — handle_change_password

`handlers.py` 新增 `handle_change_password(headers, body)`，编排：
1. 复用 `handle_me` 同款 token 解析（从 `Authorization: Bearer` 取 → `security.parse_token`），失败 401。〔F-002〕
2. 取 `oldPassword`/`newPassword`，缺字段 422。
3. 查用户，`security.verify_password(old, hash)` 不匹配 → 401。〔F-003〕
4. `validators.is_strong_password(new)` 不过 → 422。〔F-004〕
5. 新旧相同 → 422。〔F-006〕
6. `security.hash_password(new)` → `store.update_password`。〔F-005〕
7. 返回 200 `{"message": "密码已更新"}`。

> token 解析逻辑与 handle_me 重复，抽一个内部 `_email_from_headers(headers)` 复用，避免重复。

### 模块 3: 入口层 — 路由

`app.py` 路由表新增 `path.endswith("/change-password") and method == "POST"` → `handle_change_password(headers, _parse_body(event))`。

### 模块 4: 部署 — SAM 事件

`template.yaml` 给 `AuthFunction` 增加一个 Api Event：`/change-password` POST。

### 模块 5: 前端

`frontend/index.html` 增加"修改密码"区块（旧密码 + 新密码 + 按钮），复用已有 `api()` 函数（自动带 token）。

## 接口契约

```
POST /change-password   (需 Authorization: Bearer <token>)
请求: {"oldPassword": "...", "newPassword": "..."}
成功: 200 {"message": "密码已更新"}
失败: 401 未认证 / 旧密码错误；422 新密码不合规 / 与旧密码相同
```

## 数据模型

复用 Users 表，仅更新 `passwordHash` 字段，无新增表/字段。

## 安全考虑

- 旧/新密码不写日志、不回显（沿用入口层只打印异常类型）。
- 新密码 bcrypt 加盐哈希。
- token 校验失败与旧密码校验失败均返回 401（不额外泄露信息）。

## 技术决策

| 决策 | 选项 | 理由 |
| ---- | ---- | ---- |
| 更新方式 | update_item vs put_item | update_item 只改 passwordHash，不覆盖 createdAt |
| token 解析复用 | 抽 `_email_from_headers` | 与 handle_me 去重 |
