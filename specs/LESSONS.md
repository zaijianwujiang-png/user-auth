# LESSONS — 项目级经验沉淀（wj:ai 自动维护）

## 2026-06-17 — change-password / token 解析复用

- `handle_me` 和 `handle_change_password` 都要从 `Authorization` 头解析认证用户。抽出 `_email_from_headers(headers)`（返回 email 或 None）复用，避免两处重复的 Bearer 解析逻辑。
- 改密码用 DynamoDB `update_item` + `ConditionExpression=attribute_exists(email)`，只改 `passwordHash` 不动 `createdAt`，且用户不存在时不会误建空项。
- 校验顺序：先验旧密码(401) → 再校验新密码强度(422) → 最后查新旧是否相同(422)。先确保新密码合规，"新旧相同"提示才有意义。
- 复用既有 `validators.is_strong_password` / `security.hash_password`，新功能零新增依赖。
- **并发改密的 lost-update 竞态（Codex N4 复审发现）**：先验旧密码、再写新密码两步非原子，两个并发请求可能都验过同一旧密码，导致用过期旧密码覆盖新密码。修复：`update_password` 的 DynamoDB 条件改为 `attribute_exists(email) AND passwordHash = :old`（compare-and-swap，带上已验证的旧哈希），冲突时抛 StalePassword → 409。与注册防重复同一思路：把并发约束交给数据库条件写入。
