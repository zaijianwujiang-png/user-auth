---
description: DynamoDB 数据建模规范
---

# DynamoDB 规范

- 单表设计优先,PK/SK 组合表达实体与关系;新表在 template.yaml 定义,按量计费(PAY_PER_REQUEST)
- 幂等/去重用条件写(attribute_not_exists),不要先读后写
- 时序数据(帖子、决策记录)SK 带 ISO8601 时间戳便于范围查询;需要过期的配 TTL 属性
- 数据访问收敛在 store.py,handler 不直接摸 boto3
- 测试用 moto mock,建表逻辑与 template.yaml 保持一致
