# user-auth — 用户登录注册系统

用 **SDD（规格驱动开发）** 方式构建的最小可用认证系统：注册、登录、获取当前用户。

## 技术栈

- 后端：Python 3.12 + AWS Lambda + API Gateway
- 存储：DynamoDB
- 安全：bcrypt 密码哈希 + JWT 令牌
- 部署：AWS SAM
- 前端：单页 HTML

## 目录结构

```
.
├── docs/                  # 原始需求
├── specs/user-auth/       # 规格文档(requirements / design / tasks / LESSONS)
├── auth/                  # 后端 Lambda 代码(5 个模块)
│   ├── app.py             # 入口 + 路由
│   ├── handlers.py        # 业务编排
│   ├── validators.py      # 输入校验
│   ├── security.py        # bcrypt + JWT
│   └── store.py           # DynamoDB 读写
├── frontend/index.html    # 注册/登录/查看信息页面
├── template.yaml          # SAM 部署配置
└── tests/test_auth.py     # 测试(11 个)
```

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/register` | 注册 `{email, password}` |
| POST | `/login` | 登录,返回 `{token}` |
| GET  | `/me` | 当前用户(需 `Authorization: Bearer <token>`) |

## 本地测试

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
AWS_DEFAULT_REGION=us-east-1 ./.venv/bin/python -m pytest -q
```

## 部署

```bash
sam build
sam deploy --guided
# 部署后把输出的 ApiBaseUrl 填进 frontend/index.html 的 API_BASE
```
