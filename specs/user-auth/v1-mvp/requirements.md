# Requirements Document — user-auth / v1-mvp

## 版本范围

本版本交付用户认证的最小可用闭环：**注册、登录、获取当前用户信息**，并满足基本安全约束。

**显式排除（留待 v2）**：找回/重置密码、邮箱验证激活、退出登录/Token 吊销、第三方登录。

> 本文件需求子集源自 master `../requirements.md`，保留原编号以便全局追溯。

## Glossary

- **User（用户）**：系统中的一个账号，唯一标识为邮箱。
- **Credentials（凭据）**：登录所用的邮箱 + 密码。
- **Password Hash（密码哈希）**：密码经单向加盐哈希后存储的值，原始密码永不落库。
- **Token（令牌）**：登录成功后签发的身份凭证（JWT），带过期时间。
- **Auth Service（认证服务）**：负责注册、登录、校验 Token、返回用户信息的后端服务。

## Requirements

### Requirement 1: 用户注册（源自 master Requirement 1）

**User Story:** 作为新访客，我希望用邮箱和密码注册一个账号，以便成为系统用户并使用受保护的功能。

#### Acceptance Criteria

1. WHEN 用户提交邮箱和密码进行注册，THE Auth Service SHALL 校验邮箱格式合法。
2. WHEN 密码长度不少于 8 位且包含字母和数字，THE Auth Service SHALL 接受该密码。
3. WHEN 邮箱与密码均校验通过且邮箱未被占用，THE Auth Service SHALL 创建 User 并以 Password Hash 存储密码。
4. THE Auth Service SHALL 永不以明文形式存储或返回密码。
5. IF 邮箱已被注册，THEN THE Auth Service SHALL 拒绝并返回"邮箱已存在"错误。
6. IF 邮箱格式非法，THEN THE Auth Service SHALL 拒绝并返回字段级校验错误。
7. IF 密码不满足强度要求，THEN THE Auth Service SHALL 拒绝并返回密码强度错误。
8. WHEN 注册成功，THE Auth Service SHALL 返回新建用户的基本信息（不含密码）。

### Requirement 2: 用户登录（源自 master Requirement 2）

**User Story:** 作为已注册用户，我希望用邮箱和密码登录，以便获得身份凭证访问我的数据。

#### Acceptance Criteria

1. WHEN 用户提交 Credentials 登录，THE Auth Service SHALL 根据邮箱查找对应 User。
2. WHEN 邮箱存在且密码与 Password Hash 匹配，THE Auth Service SHALL 签发带过期时间的 Token 并返回。
3. IF 邮箱不存在或密码不匹配，THEN THE Auth Service SHALL 返回统一的"邮箱或密码错误"提示，不透露具体哪项错误。
4. THE Token SHALL 包含用户标识，并设置 24 小时过期时间。
5. IF 请求缺少邮箱或密码字段，THEN THE Auth Service SHALL 返回字段级校验错误。

### Requirement 3: 获取当前用户信息（源自 master Requirement 3）

**User Story:** 作为已登录用户，我希望凭 Token 获取自己的账号信息，以便确认登录状态并展示资料。

#### Acceptance Criteria

1. WHEN 请求携带有效 Token 访问"当前用户"接口，THE Auth Service SHALL 返回用户基本信息（邮箱、标识、创建时间，不含密码）。
2. IF 请求未携带 Token，THEN THE Auth Service SHALL 返回未认证错误（401）。
3. IF Token 无效或已过期，THEN THE Auth Service SHALL 返回未认证错误（401）。

### Requirement 4: 安全与数据约束（源自 master Requirement 4）

**User Story:** 作为系统维护者，我希望认证数据满足基本安全约束，以便保护用户账号安全。

#### Acceptance Criteria

1. THE Auth Service SHALL 使用单向加盐哈希算法（bcrypt）存储密码。
2. THE User 的邮箱 SHALL 在数据存储中唯一。
3. WHILE 处理任意认证请求，THE Auth Service SHALL 不在响应或日志中输出明文密码或完整 Token。
4. THE Auth Service SHALL 对邮箱统一转小写后再做唯一性判断与查找。
