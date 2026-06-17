# 项目说明

这是一个用 **SDD（规格驱动开发）** 方式组织的项目。

## 目录约定

- `docs/` —— 原始需求文档（人写的、最初的想法）
- `specs/` —— 规格文档，每个功能一个子文件夹，内含三件套：
  - `requirements.md` 需求（验收标准）
  - `design.md` 技术设计
  - `tasks.md` 可执行任务清单（带 `[ ]` 勾选）
- `specs/PLAN.md` —— 版本路线图（拆成 v1/v2…，列出所有功能）
- `src/` —— 真正的代码（照着 tasks.md 实现）

## 工作流程

1. 在 `docs/` 写原始需求
2. `/sdd-requirements` 生成 requirements.md
3. `/sdd-split` 拆版本，生成 PLAN.md
4. `/sdd-design` 生成 design.md
5. `/sdd-tasks` 生成 tasks.md
6. `/sdd-implement` 照任务写代码
7. `/sdd-test` 跑测试核验
8. `/sdd` 随时看进度

## 规则

- 改代码前先看对应的 spec 文档
- 每完成一条 task，在 tasks.md 里打勾
