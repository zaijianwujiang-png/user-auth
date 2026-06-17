# Design Document — user-auth / v1-mvp

> 技术栈贴合现有 lambda-demo：**Python 3.12 + AWS SAM + API Gateway + DynamoDB**，前端为极简单页 HTML。

## 1. 架构概览

```
浏览器(注册/登录表单)
   │  fetch JSON
   ▼
API Gateway  ── /register  (POST)
             ── /login     (POST)
             ── /me        (GET, 带 Authorization 头)
   │
   ▼
Lambda 函数(Python)
   ├─ 校验输入(邮箱格式 / 密码强度)
   ├─ bcrypt 哈希 / 校验密码
   ├─ PyJWT 签发 / 校验 Token
   │
   ▼
DynamoDB 表 Users (主键 = email)
```

**为什么用 DynamoDB**：Lambda 是无状态的，函数跑完即销毁，不能用本地 SQLite 文件存数据。DynamoDB 是 AWS 的无服务器数据库，按用量计费、零运维，和 Lambda 是天然搭档。

## 2. 接口设计（满足 Req 1/2/3）

| 方法 | 路径 | 说明 | 对应需求 |
|------|------|------|----------|
| POST | `/register` | 注册：body = `{email, password}` | 1 |
| POST | `/login` | 登录：body = `{email, password}`，返回 `{token}` | 2 |
| GET | `/me` | 当前用户：请求头 `Authorization: Bearer <token>` | 3 |

所有响应均带 CORS 头 `Access-Control-Allow-Origin: *`（沿用 lambda-demo 约定）。

### 请求/响应示例

**POST /register**
```
请求:  {"email": "a@x.com", "password": "abc12345"}
成功:  201 {"user": {"email": "a@x.com", "createdAt": "..."}}        # Req 1.8 不含密码
失败:  409 {"error": "邮箱已存在"}                                     # Req 1.5
       422 {"error": "密码至少8位且含字母和数字", "field": "password"} # Req 1.7
```

**POST /login**
```
成功:  200 {"token": "<jwt>"}                                        # Req 2.2
失败:  401 {"error": "邮箱或密码错误"}                                 # Req 2.3 统一提示
```

**GET /me**
```
成功:  200 {"email": "a@x.com", "createdAt": "..."}                  # Req 3.1
失败:  401 {"error": "未认证"}                                        # Req 3.2/3.3
```

## 3. 数据模型（满足 Req 4）

DynamoDB 表 **Users**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `email` | String (分区键) | 规范化为小写后存储，天然保证唯一(Req 4.2/4.4) |
| `passwordHash` | String | bcrypt 加盐哈希，永不返回(Req 4.1) |
| `createdAt` | String | ISO 时间戳 |

注册写入用 `ConditionExpression: attribute_not_exists(email)`，从数据库层面防止重复注册的并发竞态(Req 1.5)。

## 4. 模块划分（代码组织）

```
auth/                       # Lambda 代码目录(对应 SAM CodeUri)
├── app.py                  # 入口:按 path 路由到 register/login/me
├── handlers.py             # 三个业务处理函数
├── validators.py           # 邮箱格式、密码强度校验(Req 1.1/1.2)
├── security.py             # bcrypt 哈希、PyJWT 签发/校验
├── store.py                # DynamoDB 读写封装
└── requirements.txt        # bcrypt, PyJWT, boto3(boto3 Lambda 自带)
```

入口路由风格沿用 lambda-demo 的 `lambda_handler(event, context)`，根据 `event["path"]` 和 `event["httpMethod"]` 分发。

## 5. 安全要点（满足 Req 4）

- 密码用 **bcrypt**（自带盐），绝不明文存储/返回(Req 4.1/4.3)。
- 邮箱**统一 `.lower().strip()`** 后再判重和查找(Req 4.4)。
- 登录失败统一返回"邮箱或密码错误"，不区分是邮箱不存在还是密码错(Req 2.3，防账号枚举)。
- JWT 密钥从环境变量 `JWT_SECRET` 读取（SAM 模板里配置），有效期 24h(Req 2.4)。
- 日志中不打印 password 和完整 token(Req 4.3)。

## 6. SAM 模板要点

- 一个 `AuthFunction`，挂三个 Api Event（/register POST、/login POST、/me GET）。
- 给函数加 DynamoDB 读写权限（`Policies: DynamoDBCrudPolicy`）。
- 环境变量：`JWT_SECRET`、`TABLE_NAME`。
- `Outputs` 打印 API 根网址，供前端 index.html 填入。

## 7. 前端（极简单页）

`frontend/index.html`：沿用 lambda-demo 的单文件风格，含注册表单、登录表单、"查看我的信息"按钮。登录成功把 token 存到 `localStorage`，调用 `/me` 时放进 `Authorization` 头。

## 8. 错误处理策略

| 场景 | 状态码 | 来源需求 |
|------|--------|----------|
| 字段缺失/格式非法 | 422 | 1.6/1.7/2.5 |
| 邮箱已存在 | 409 | 1.5 |
| 登录凭据错误 | 401 | 2.3 |
| 未认证/Token 失效 | 401 | 3.2/3.3 |
| 服务器异常 | 500 | 兜底 |
