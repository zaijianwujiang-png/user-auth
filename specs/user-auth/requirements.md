# Requirements Document

## Introduction

本功能为系统提供最基础的**用户身份能力**：注册、登录、获取当前用户信息。用户使用邮箱 + 密码注册账号，登录成功后获得一个身份凭证（Token），后续凭该 Token 访问需要登录的接口。这是绝大多数应用的地基，目标是先交付一个安全、可用的 MVP：密码加密存储、输入严格校验、邮箱唯一。

## Glossary

- **User（用户）**：系统中的一个账号，唯一标识为邮箱。
- **Credentials（凭据）**：用户登录所用的邮箱 + 密码组合。
- **Password Hash（密码哈希）**：用户密码经单向加密算法处理后存储的值，原始密码永不落库。
- **Token（令牌）**：登录成功后签发的身份凭证（如 JWT），有过期时间，用于证明请求者身份。
- **Auth Service（认证服务）**：负责注册、登录、校验 Token、返回用户信息的后端服务。

## Requirements

### Requirement 1: 用户注册

**User Story:** 作为新访客，我希望用邮箱和密码注册一个账号，以便成为系统用户并使用受保护的功能。

#### Acceptance Criteria

1. WHEN 用户提交邮箱和密码进行注册，THE Auth Service SHALL 校验邮箱格式合法（符合标准邮箱格式）。
2. WHEN 用户提交的密码长度不少于 8 位且包含字母和数字，THE Auth Service SHALL 接受该密码。
3. WHEN 邮箱与密码均校验通过且邮箱未被占用，THE Auth Service SHALL 创建 User，并以 Password Hash 形式存储密码。
4. THE Auth Service SHALL 永不以明文形式存储或返回密码。
5. IF 提交的邮箱已被注册，THEN THE Auth Service SHALL 拒绝注册并返回"邮箱已存在"错误。
6. IF 邮箱格式非法，THEN THE Auth Service SHALL 拒绝注册并返回字段级校验错误。
7. IF 密码不满足强度要求，THEN THE Auth Service SHALL 拒绝注册并返回密码强度错误。
8. WHEN 注册成功，THE Auth Service SHALL 返回新建用户的基本信息（不含密码）。

### Requirement 2: 用户登录

**User Story:** 作为已注册用户，我希望用邮箱和密码登录，以便获得身份凭证访问我的数据。

#### Acceptance Criteria

1. WHEN 用户提交 Credentials 登录，THE Auth Service SHALL 根据邮箱查找对应 User。
2. WHEN 邮箱存在且提交密码与 Password Hash 匹配，THE Auth Service SHALL 签发一个带过期时间的 Token 并返回。
3. IF 邮箱不存在或密码不匹配，THEN THE Auth Service SHALL 返回统一的"邮箱或密码错误"提示，且不透露具体是哪一项错误。
4. THE Token SHALL 包含用户标识，并设置合理的过期时间（如 24 小时）。
5. IF 请求缺少邮箱或密码字段，THEN THE Auth Service SHALL 返回字段级校验错误。

### Requirement 3: 获取当前用户信息

**User Story:** 作为已登录用户，我希望凭 Token 获取自己的账号信息，以便确认登录状态并展示资料。

#### Acceptance Criteria

1. WHEN 请求携带有效的 Token 访问"当前用户"接口，THE Auth Service SHALL 返回该用户的基本信息（邮箱、用户标识、创建时间等，不含密码）。
2. IF 请求未携带 Token，THEN THE Auth Service SHALL 拒绝访问并返回未认证错误（401）。
3. IF Token 无效或已过期，THEN THE Auth Service SHALL 拒绝访问并返回未认证错误（401）。

### Requirement 4: 安全与数据约束

**User Story:** 作为系统维护者，我希望认证相关数据满足基本安全约束，以便保护用户账号安全。

#### Acceptance Criteria

1. THE Auth Service SHALL 使用单向加盐哈希算法（如 bcrypt）存储密码。
2. THE User 的邮箱 SHALL 在数据存储中唯一。
3. WHILE 处理任意认证请求，THE Auth Service SHALL 不在响应或日志中输出明文密码或完整 Token。
4. THE Auth Service SHALL 对邮箱做大小写规范化（统一转小写）后再做唯一性判断与查找。
