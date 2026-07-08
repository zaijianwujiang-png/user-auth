---
description: Lambda / API Gateway 后端规范
globs: "{auth,signal}/**/*.py"
---

# 后端(Lambda)规范

- 一个 feature 一个 Lambda 函数目录,app.py 里 lambda_handler 做路由分发
- 所有资源定义在根 template.yaml;新函数记得配 CORS、超时、内存、最小 IAM 策略
- 配置经环境变量注入(template.yaml Environment.Variables),代码里集中读取
- 定时任务用 EventBridge Schedule 触发;handler 必须幂等(同一事件重放不产生副作用)
- 外部调用(HTTP/boto3)失败要么重试要么明确告警,不允许静默吞错
- 响应统一 JSON + 正确状态码;4xx 用户错误 / 5xx 系统错误分清
