# claude(SDD 实验项目)

用规格驱动开发(SDD)组织的 AWS Serverless 项目:已有用户认证(user-auth、change-password),当前在开发大 V 信号交易系统(trump-signal-trader)。

## 技术栈

- Python 3.12,AWS SAM(Lambda + API Gateway + EventBridge + DynamoDB)
- 测试:pytest + moto(DynamoDB mock)
- LLM:Claude API(交易信号双模型交叉验证)

## 常用命令

```bash
pip install -r requirements-dev.txt   # 安装本地开发依赖
pytest tests/ -v                      # 跑全部测试
sam build && sam deploy               # 构建 + 部署(生产记得 --parameter-overrides 传密钥)
sam local start-api                   # 本地起 API
```

## 目录结构

```
docs/       原始需求(人写的)
specs/      规格三件套 requirements/design/tasks + PLAN.md 路线图
auth/       user-auth Lambda 代码
signal/     trump-signal-trader Lambda 代码(开发中)
frontend/   静态页面
tests/      pytest 测试
template.yaml  SAM 模板(所有云资源)
```

## 规范(按需阅读)

- @rules/coding-style.md —— 代码风格
- @rules/testing.md —— 测试规范
- @rules/security.md —— 安全规范(密钥/PII)
- @rules/git-workflow.md —— git 约定
- @rules/backend-api.md —— Lambda/API 规范
- @rules/database.md —— DynamoDB 规范

## 项目踩坑与教训

@AGENTS.md
