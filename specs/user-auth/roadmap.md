# user-auth 路线图

## 特性清单

| 特性 | 简述 | 包含需求 |
|------|------|----------|
| **registration** 注册 | 邮箱+密码注册、校验、唯一性、哈希存储 | Requirement 1 |
| **login** 登录 | 凭据校验、签发 Token | Requirement 2 |
| **identity** 当前用户 | 凭 Token 获取自身信息、认证中间件 | Requirement 3 |
| **security** 安全约束 | 哈希算法、邮箱唯一/规范化、日志脱敏 | Requirement 4（横切，贯穿全部）|

> security 是横切约束，不单独成版本，融入每个特性实现中。

## 版本路线图

### v1-mvp（本次交付）

- **目标**：交付可用的最小认证闭环 —— 用户能注册、登录、凭 Token 拿到自己的信息。
- **包含特性**：registration + login + identity + security
- **对应需求**：Requirement 1、2、3、4
- **前置依赖**：无
- **闭环验证**：注册 → 登录拿 token → 用 token 访问当前用户接口，全链路打通。

### v2-recovery（未来展望，本次不做）

- 找回密码 / 重置密码（依赖邮件服务）
- 邮箱验证（注册后激活）
- 退出登录 / Token 吊销
- 第三方登录（Google 等）

## 版本 × 特性 × 需求 映射

| 版本 | registration | login | identity | security | 需求编号 |
|------|:---:|:---:|:---:|:---:|---|
| **v1-mvp** | ✅ | ✅ | ✅ | ✅ | 1,2,3,4 |
| v2-recovery | — | — | — | — | （新增）|
