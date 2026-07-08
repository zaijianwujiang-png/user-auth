# 路线图 PLAN

## Features

| Feature | 说明 | 状态 | 路线图 |
|---------|------|------|--------|
| 1. user-auth | 用户注册/登录/当前用户(MVP) | ✅ 实现完成(22/22, 测试 11 passed) | [roadmap](user-auth/roadmap.md) |
| 2. change-password | 登录态下旧密码换新密码 | ✅ 实现完成(6/6, 测试 13 passed) | [specs](2.change-password/) |
| 3. trump-signal-trader | 大V信号交易：Truth Social 采集 + AI 信号 + 交叉验证 + Telegram 通知 | ✅ v1 实现完成(12/12, 测试 17 passed) | [specs](3.trump-signal-trader/) |

## 本次 PRD（2026-06-17）切分

| 序号 | feature | 说明 | 依赖 | 状态 |
| ---- | ------- | ---- | ---- | ---- |
| 2 | change-password | 登录态下旧密码换新密码 | 1.user-auth | ✅ 完成 |

**推荐执行顺序**：2（单 feature）
**ID 约定**：跨 feature 引用加 `{序号}.` 前缀，如 `2.T-002`。

## 本次 PRD（2026-07-07）切分

| 序号 | feature | 说明 | 依赖 | 状态 |
| ---- | ------- | ---- | ---- | ---- |
| 3 | trump-signal-trader | 大V信号交易（v1 信号+通知） | 无（独立于 user-auth） | ✅ v1 完成 |

**推荐执行顺序**：3（单 feature，本期只做 v1）

### trump-signal-trader 版本路线

| 版本 | 内容 | 状态 |
|------|------|------|
| v1 | Truth Social 采集 + Claude 双模型信号提取与交叉验证（去重/双模型/行情/事实核查）+ Telegram 通知，完全不碰交易 | 待开发（本期，12 tasks） |
| v2 | 币安测试网自动下单（ccxt 现货）+ 风控（仓位上限/日熔断/冷却期/止损止盈）+ 交易记录 + 通知/半自动/全自动三模式 | 展望 |
| v3 | 真实账户（半自动起步）+ 复盘统计（胜率/累计收益）+ 完整监控告警 | 展望 |

## user-auth 版本

| 版本 | 内容 | 状态 |
|------|------|------|
| v1-mvp | 注册 + 登录 + 当前用户 + 安全约束 | ✅ 完成 22/22,11 测试通过,待 sam deploy |
| v2-recovery | 找回密码/邮箱验证/登出/三方登录 | 展望 |
