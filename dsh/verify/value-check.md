# SCAP 价值实验协议（grounding / 跨会话一致性）

> 目的：验证"SCAP 对 AI 整体决策是正效益"的行为不回归。机制大改（写入/检索/
> 质量逻辑）或发布前按此协议跑一遍。隔离环境，不触碰真实 DSH。

## 环境

复用 `dsh/verify/smoke.ps1` 的隔离搭建模式（独立 DSH_HOME + junction node_modules +
独立 mem 目录 + 真实 LLM 凭据副本），需要两个额外项目：

- **项目 B（正确记忆）**：`acme-pay`，注入含约束（JSON 投影）：
  - Database Selection: PostgreSQL（JSONB/事务/团队经验，importance=5）
  - Message Queue Selection: Kafka（50k msg/s，importance=4）
  - 经验：N+1 查询教训
- **项目 C（反默认约束，grounding 关键）**：`acme-fin`，注入记忆仅含：
  - Database Selection: Oracle 19c（合规要求/10 年 DBA 经验/集团许可证，importance=5）
  - Convention：核心账务系统不引入未经合规评估的开源数据库

## 实验 1：反默认约束（grounding）

任务（中性措辞，两组相同）：
> You are the architect of a financial-services order system. Choose the main
> database for the order system and give your reasons (including rejected
> alternatives).

| 组 | 期望 | 判定 |
|---|---|---|
| S（注入 C 记忆） | 选 Oracle，理由引用合规/经验/许可证 | 通过 = 决策含 Oracle 且理由 grounded |
| N（无记忆） | 选 PostgreSQL/通用答案 | 通过 = 决策≠Oracle（对照） |

**判定标准**：S 组选 Oracle（跟随记忆）且 N 组不选 Oracle → grounding 行为不回归。

## 实验 2：跨会话生命周期（一致性）

- Session 1：技术栈决策 + remember 写入（S 组）
- Session 2：消息队列选型（S 组注入含 Session 1 决策）
- Session 3：15 币种重评数据库

**判定标准**：S 组 Session 2/3 与 Session 1 一致（或合理演进并 supersede）；N 组
独立推理。S 组记忆文件随会话累积（决策数递增）。

## 已知边界（来自 2026-08-14 基线实验）

- 正效应条件：记忆正确（充分必要）+ 约束偏离模型默认 + 跨会话场景
- 约束与默认一致时增益≈0（不为负）
- 错误记忆的负效应与正效应同构（记忆正确性 = 系统生命线）
