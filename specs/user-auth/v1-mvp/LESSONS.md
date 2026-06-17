# LESSONS — user-auth v1 开发经验沉淀

> 按 yd:ai 流程 N5 沉淀:这次开发踩到/确认的关键经验,供以后复用。

## 安全

- **密码永远存哈希不存明文**:用 bcrypt 自带盐,同一密码每次哈希都不同,数据库泄露也难反推。
- **JWT 密钥要 ≥32 字节且放环境变量**:PyJWT 会对短密钥告警(SHA256 推荐下限 32 字节)。这是 N4 自审当场抓到的真问题。
- **登录失败要防账号枚举**:"邮箱不存在"和"密码错误"必须返回完全相同的提示,否则攻击者能探出哪些邮箱已注册。
- **入口层日志不打印 body**:可能含密码/token,只打印异常类型。

## 架构 / DynamoDB

- **唯一性约束交给数据库**:用 `ConditionExpression=attribute_not_exists(email)` 做条件写入,比"先查后写"更安全(防并发竞态)。
- **配置走环境变量**:代码用 `os.environ` 读 TABLE_NAME/JWT_SECRET,模板用 `!Ref` 注入,两边解耦,同一份代码可部 dev/prod。
- **权限按表授予**:`DynamoDBCrudPolicy` 只给 Users 一张表,别图省事给全局权限。
- Lambda 无状态,不能用本地 SQLite,DynamoDB 是天然搭档。

## 测试方法

- **纯函数模块先写先测**:validators/security 不碰 IO,最好测,开头写它们建立信心。
- **用 moto 模拟 AWS**:不联网、不花钱也能完整测 DynamoDB 逻辑(查空→建→防重→脱敏)。
- **CORS 的 OPTIONS 预检容易漏**:漏了会出现"curl 通、浏览器报跨域"的怪现象。

## 环境

- 本地 Python 3.9 将于 2026-04 被 boto3 停止支持,建议升 3.12(与 Lambda runtime 一致)。
- 本地测试用 venv 装真实 bcrypt/PyJWT/moto,才能跑出真实告警。
