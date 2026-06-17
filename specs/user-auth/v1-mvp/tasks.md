# Tasks — user-auth / v1-mvp

> 按此清单逐条实现。每完成一条，把 `[ ]` 改成 `[x]`。
> 追溯：每条任务标注其满足的需求(Req)与设计章节(§)。

## T1 项目脚手架

- [x] **T1.1** 创建目录结构 `auth/`（app.py / handlers.py / validators.py / security.py / store.py / requirements.txt）与 `frontend/index.html`。〔§4〕
- [x] **T1.2** `requirements.txt` 写入依赖：`bcrypt`、`PyJWT`（boto3 由 Lambda 运行时自带）。〔§4〕

## T2 校验模块 validators.py

- [x] **T2.1** `is_valid_email(email)`：标准邮箱格式校验。〔Req 1.1〕
- [x] **T2.2** `is_strong_password(pw)`：长度≥8 且含字母和数字。〔Req 1.2/1.7〕
- [x] **T2.3** `normalize_email(email)`：`.strip().lower()` 规范化。〔Req 4.4〕

## T3 安全模块 security.py

- [x] **T3.1** `hash_password(pw)` / `verify_password(pw, hash)`：bcrypt 加盐哈希与校验。〔Req 4.1〕
- [x] **T3.2** `make_token(email)`：PyJWT 签发，含用户标识 + 24h 过期，密钥读环境变量 `JWT_SECRET`。〔Req 2.4〕
- [x] **T3.3** `parse_token(token)`：校验签名与过期，返回 email 或抛出未认证。〔Req 3.3〕

## T4 存储模块 store.py（DynamoDB）

- [x] **T4.1** `get_user(email)`：按主键读取用户，找不到返回 None。〔Req 2.1/3.1〕
- [x] **T4.2** `create_user(email, passwordHash)`：写入，带 `attribute_not_exists(email)` 条件防重复。〔Req 1.3/1.5/4.2〕
- [x] **T4.3** 表名从环境变量 `TABLE_NAME` 读取。〔§6〕

## T5 业务处理 handlers.py

- [x] **T5.1** `handle_register(body)`：校验邮箱+密码→判重→哈希→写库→返回用户(不含密码)。处理 409/422。〔Req 1.1-1.8〕
- [x] **T5.2** `handle_login(body)`：查用户→校验密码→签 token；失败统一返回"邮箱或密码错误"。〔Req 2.1-2.5〕
- [x] **T5.3** `handle_me(headers)`：解析 Authorization 头→校验 token→返回用户信息；无/失效 token 返回 401。〔Req 3.1-3.3〕

## T6 入口路由 app.py

- [x] **T6.1** `lambda_handler(event, context)`：按 `path` + `httpMethod` 分发到三个 handler。〔§4〕
- [x] **T6.2** 统一响应封装：带 CORS 头、JSON body、正确状态码；500 兜底。〔§8, Req 4.3 日志不打印密码/token〕

## T7 SAM 模板 template.yaml

- [x] **T7.1** 定义 `AuthFunction`，挂 3 个 Api Event（/register POST、/login POST、/me GET）。〔§6〕
- [x] **T7.2** 定义 DynamoDB 表 `Users`（主键 email），给函数加 `DynamoDBCrudPolicy`。〔§3/§6〕
- [x] **T7.3** 配置环境变量 `JWT_SECRET`、`TABLE_NAME`；`Outputs` 打印 API 网址。〔§6〕

## T8 前端 frontend/index.html

- [x] **T8.1** 注册表单 + 登录表单 + "查看我的信息"按钮（沿用 lambda-demo 单页风格）。〔§7〕
- [x] **T8.2** 登录成功把 token 存 localStorage，调 `/me` 时放进 Authorization 头；展示结果与错误。〔§7, Req 3.1〕

## T9 测试与核验

- [x] **T9.1** 本地单元测试：validators、security 的纯函数。〔Req 1.1/1.2/4.1〕
- [x] **T9.2** 端到端：注册→登录拿 token→/me 取信息，全链路打通。〔Req 1/2/3 闭环〕

---

**进度：22 / 22 任务完成 ✅**
