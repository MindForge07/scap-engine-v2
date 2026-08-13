# SCAP v2 — Project Memory System for LLM Agents

> **让 AI 记住你的项目，而不是每次都从零开始。**

LLM 是一个"顶级大脑"——它知道分布式系统怎么设计、熔断器怎么实现、CAP 定理是什么。但它不知道：

- 你的项目为什么选了 Kafka 而不是 RabbitMQ
- 三个月前你们从 MongoDB 迁移到 PostgreSQL 的理由
- 团队约定所有状态变更必须走事件溯源
- 上次线上事故的根因是 N+1 查询

这些**项目级决策记忆**散落在 Slack、文档、和人的脑子里。SCAP 把它们结构化存储，让 AI 在每次新任务前自动读取——**不重复发明、不前后矛盾、不重蹈覆辙**。

---

## 当前状态（v2.1，2026-08）

- **11 个 MCP 工具**、14 个 CLI 命令、206+ 个测试（全部通过）
- **零 LLM 依赖核心**：存储/检索/注入纯本地（SQLite + FTS5 + 规则打分）；可选 embedding 增强
- **真实环境验证通过**：隔离 DSH 挂载实验证明决策 grounding（详见[验证记录](#验证记录)）
- **架构演进**：结构化决策记忆 → 质量闭环（P0）→ 生命周期管理（P1）→ 分层注入（L1）

---

## 核心设计

```
AI 收到任务
   │
   ├─ L0/L1 自动注入（0 API）：项目卡片 + 任务相关决策已在 system prompt
   │
   ├─ AI 做出重要决策
   │   └─ 调用 scap_remember → 数据库更新 → context.md/.json 自动重新导出
   │      （同标题重新决策自动 supersede 旧记录，重复记录自动去重）
   │
   └─ 记忆质量闭环
       ├─ importance（1-5）+ 写入质量门（无理由的决策降级）
       ├─ recency 衰减：新决策优先于旧决策
       ├─ scap_feedback：反馈驱动 fitness/importance 演化
       ├─ scap_audit：过期决策复核
       └─ scap_reflect：长任务后总结项目洞察
```

**零额外 API 开销**：读取上下文不需要调用任何工具——分层注入已在 system prompt 中。只有记录新决策时才需要 1 次调用。

**零 LLM 依赖**：存储、检索、注入、分层全部不依赖任何 LLM。LLM 只出现在**可选的**写入/总结路径（`scap_reflect` 由模型自总结，embedding 为可选增强）。

**AI 自记录 + 自维护**：MCP instructions 引导 AI 在决策/经验/长任务后主动调用 remember/reflect；audit 引导定期复核。

---

## Quick Start

```bash
# 安装
cd scap-engine-v2
pip install -e .

# 初始化项目
scap init --project myapp --stack "PostgreSQL" --stack "Redis" --stack "Kafka"

# 记录/查询（CLI）
scap list --project myapp
scap search "数据库" --project myapp
scap audit --project myapp              # 过期决策复核
scap feedback DC-20260814-0001 --helpful  # 反馈闭环

# 导出上下文（供 system prompt 注入）
scap export --project myapp --max-chars 12000

# 启动 MCP 服务（AI 集成）
python -m scap.mcp_server
```

---

## MCP Tools（11 个）

| 工具 | 触发时机 | 参数 | 说明 |
|------|---------|------|------|
| `scap_recall` | 任务前（可选） | project + task_description | 检索项目记忆，按任务相关度排序（IDF + recency 加权） |
| `scap_remember` | 决策后 | project + title + decision + [rationale] + [importance] + [source_session] | 记录决策；**四操作**：重复→NOOP、同标题新选择→自动 supersede；自动导出 context |
| `scap_record_experience` | 经验后 | project + situation + action + lesson + [tags] + [importance] | 记录经验教训，自动导出 context |
| `scap_context` | 新会话开始 | project | 完整项目快照 |
| `scap_status` | 任何时候 | (无) | 系统概览（含 latent/fitness 统计） |
| `scap_retrieve_latent` | 语义检索（可选） | project + query | 向量相似度搜索（需 sentence-transformers） |
| `scap_consolidate` | 周期性 | project + similarity_threshold | 合并相似 latent traces，进化代数+1 |
| `scap_evolved_context` | 复杂任务 | project + [task_description] + [min_fitness] | fitness 加权的进化上下文 |
| `scap_feedback` | 记忆被使用后 | entity_id + helpful + [project] | EMA 更新 fitness + 联动 importance（质量闭环） |
| `scap_audit` | 周期性 | project + [older_than_days] + [limit] | 列出长期未复核的 active 决策（按 importance 排序） |
| `scap_reflect` | 长任务/会话后 | project + insights | 把 1-5 条高层洞察存入项目上下文，随 context 注入 |

### AI 自记录协议（instructions 摘要）

- **决策后** → `scap_remember`（带 rationale；无理由的决策会被降级为 importance=2）
- **经验后** → `scap_record_experience`
- **长任务后** → `scap_reflect`（总结项目级洞察）
- **周期性** → `scap_audit`（复核过期决策）
- **记忆被使用后** → `scap_feedback`（让记忆质量随使用演化）
- **该记什么**：技术选型与理由、架构决策、事故根因与修复模式、团队约定；跳过琐碎问答与通用知识

---

## CLI Commands（14 个）

| 命令 | 说明 |
|------|------|
| `scap init` | 初始化项目（tech_stack） |
| `scap status` | 系统状态（决策/经验/latent 数、项目列表） |
| `scap search "关键词"` | 搜索项目记忆（四层检索） |
| `scap list` | 列出决策 |
| `scap export --project X [--max-chars N]` | 导出 context.md + context.json（注入用） |
| `scap configure --project X --stack ... --convention ...` | 更新项目上下文 |
| `scap ingest --file doc.md` | 从 YAML front-matter markdown 导入决策 |
| `scap latent "query"` | 语义检索（需 embedding） |
| `scap consolidate --project X` | 合并相似 traces |
| `scap evolved --project X` | fitness 加权上下文 |
| `scap audit --project X [--older-than 90]` | 过期决策复核 |
| `scap feedback <id> [--helpful/--unhelpful]` | 记忆反馈闭环 |
| `scap traces` | 列出 latent traces |
| `scap embed --project X` | 为存量记录回填 embedding |

---

## 分层注入机制（L0/L1/L2，零 LLM）

SCAP 的记忆不是"全量塞进 prompt"，而是**分层**：

```
L0 项目卡片（常驻，小预算）
   Tech Stack / Conventions / Insights —— 结构性事实，永远在

L1 任务相关预检索（自动，每 step，纯规则）
   读取最近用户消息 → 打分器（CJK bigram + 拉丁词 + IDF + recency + importance）
   → 相关决策 top-N + 常驻决策（importance≥4 或近 7 天）+ 相关经验 top-2

L2 深度召回（模型自觉）
   scap_recall / scap_retrieve_latent —— 需要更多记忆时主动调用
```

**为什么 L1 用规则而非 LLM/代理**：L1 发生在每个 step（高频路径）——规则召回零成本、确定性、可测试；LLM 只花在低频任务（反思/审计/深度检索）。这是 Generative Agents（三因子加权）、Mem0（读取路径规则化）的共同选择。

### DSH（DeepSeek Harness）集成

`dsh/` 目录提供用户态零依赖插件 `scap-injection.ts`，把分层记忆自动注入 DSH system prompt（0 API）：

```yaml
# $DSH_HOME/cordis.patch.yml 或 --patch 挂载
- insert:
    - id: scap-injection
      name: C:/Users/XDXLC/openclaw/scap-engine-v2/dsh/scap-injection.ts
      config:
        heading: "[SCAP Project Memory]"
        # scapDir/project/maxChars/recallTopN/residentImportance/
        # residentMaxAgeDays/useTaskRecall 均可配置（见 dsh/README.md）
```

数据通路：scap 导出时同步写 `.scap/{project}.md`（人类可读）+ `.scap/{project}.json`（机器可读结构化投影）——注入插件读 JSON 做本地分层，与 SQLite 解耦；旧目录只有 md 时自动回退全量注入。

---

## 记忆质量机制

| 机制 | 作用 | 实现 |
|------|------|------|
| **importance（1-5）** | 高重要决策常驻注入、优先排序、audit 优先复核 | 写入参数 + 字段 |
| **写入质量门** | 空 decision/lesson 拒绝；无 rationale 的决策 importance 封顶 2 | remember/record_experience 校验 |
| **四操作写入** | 重复记录→NOOP；同标题新选择→自动 supersede（决策演化而非堆积） | remember 规则 |
| **recency 衰减** | 检索/注入时新决策优先于旧决策（45 天特征衰减） | 打分器 |
| **fitness 反馈** | `scap_feedback` EMA 更新 latent trace fitness + 联动 importance | 闭环工具 |
| **过期复核** | `scap_audit` 列出 N 天未更新的 active 决策 | 审计工具 |
| **反思洞察** | `scap_reflect` 把高层洞察存入 ProjectContext.insights，随卡片注入 | 反思工具 |

---

## 数据模型（4 实体）

### Decision（决策记录）
```python
{
  "id": "DC-20260626-0001",
  "project": "acme",
  "title": "消息队列选型",
  "decision": "Kafka",
  "rationale": "吞吐量需求 50k msg/s, RabbitMQ 在 10k+ 时性能下降明显",
  "alternatives": [{"name": "RabbitMQ", "reason_rejected": "性能瓶颈"}],
  "constraints": ["必须支持 15 种货币"],
  "status": "active",            # active | superseded | deprecated
  "superseded_by": null,          # 被哪条决策取代（同标题重新决策自动写入）
  "importance": 3,                # 1-5
  "source_session": "",           # 产生该决策的会话
  "tags": ["消息队列", "基础设施"]
}
```

### Experience（经验教训）· ProjectContext（项目上下文）· LatentTrace（可选向量）
- `Experience`: situation / action / lesson + importance + tags
- `ProjectContext`: tech_stack / conventions / active_goals / **insights**（反思洞察）
- `LatentTrace`: 向量 + fitness + evolution_gen（可选 embedding 层）

---

## 上下文导出格式

每次写入后自动生成 `.scap/{project}.md`（人类可读）+ `.scap/{project}.json`（机器可读）：

```markdown
# Project Memory: acme

## Tech Stack
PostgreSQL 15, Redis 7, Kafka 3.5

## Conventions
- 所有状态变更必须走事件溯源

## Insights
- 事件溯源是我们状态变更的默认模式

## Decisions          （按 importance 优先排序）
### 消息队列选型 (2026-06-26)
**Chosen:** Kafka
**Why:** 吞吐量需求 50k msg/s
- ~~RabbitMQ~~ (rejected: 10k+ 性能下降)
```

---

## 验证记录（诚实文档）

### 已实证（隔离真实环境，非仅单元测试）

| 验证项 | 方法 | 结论 |
|--------|------|------|
| **决策 grounding** | 反默认约束实验（约束只在记忆里：合规要求 Oracle） | 有 SCAP 选 Oracle（跟随记忆）；无 SCAP 选 PostgreSQL（通用答案，且**否认合规约束存在**）——决策从"通用正确"翻转为"项目正确" |
| **跨会话一致性** | 3 会话生命周期实验（S 组 vs N 组） | S 组跨会话继承决策并主动记录复核决策；N 组每次独立推理 |
| **注入链路** | 真实 DSH 挂载（隔离 headless + 真实 LLM） | `[SCAP Project Memory]` 快照真实到达模型；写入后自动更新 |
| **四操作** | 真实链路 | 模型重复记录同决策 → NOOP 拦截，DB 无堆积 |
| **L1 分层注入** | 真实 DSH 冒烟（预置 4 决策 + 旧化 1 条） | 任务相关注入 ✓、无关旧低重要过滤 ✓、常驻保留 ✓、写入后注入更新 ✓ |

### 正效益边界（实证结论）

- **正效应成立的条件**：① 记忆内容正确（**充分必要条件**——错误记忆的负效益与正效益同构且更持久）；② 项目约束偏离模型默认知识（偏离越大增益越大）；③ 跨会话/长生命周期（单次短任务注入是净开销）；④ 上下文预算充足
- **已缓解的负效益敞口**：错误记忆固化（质量门 + importance + feedback 闭环）、过时锚定（recency 衰减 + audit + supersede）
- **未验证**：真实生产环境的长期效果、记忆错误时的负效益定量（需要更大规模 A/B）

### 测试

```bash
pytest tests/ -v    # 209 个测试，~27s
```

覆盖：模型校验、存储 CRUD/FTS/迁移、四层检索与中文召回质量、并发/Unicode/大数据量压力、MCP 工具、CLI、质量门/recency/fitness、四操作/audit/reflect、分层注入 JSON 投影。

---

## 已知限制

- **写入靠模型自觉**：自记录依赖 instructions 引导；无事件驱动强制捕获（DSH agent-loop 钩子是规划项）
- **单连接并发**：SQLite 单连接 + 锁；并发读写极端场景需连接池
- **向量检索全表扫描**：`search_by_vector` 线性扫描；万级记录需倒排/专用向量库
- **ID 当日上限**：`DC-YYYYMMDD-NNNN` 每日 9999 条内自增
- **latent 层为可选增强**：未装 sentence-transformers 时整层降级为空转（不影响核心）

---

## 路线图

| 阶段 | 内容 | 状态 |
|------|------|------|
| v2.0 | 结构化决策记忆 + FTS 检索 + 注入导出 | ✅ |
| v2.1 | latent 空间进化（可选向量层） | ✅ |
| P0 | 质量门 / importance / recency / fitness 闭环 | ✅ |
| P1 | 四操作写入 / audit / reflect / 时间感知 | ✅ |
| L1 | 分层注入（任务相关预检索，零 LLM） | ✅ |
| P2 | 图链接 / 可插拔存储 / DSH 事件驱动写入 | 待真实使用反馈 |

---

## 设计哲学

1. **LLM 不缺通用知识，缺的是项目上下文。** SCAP 不教模型怎么设计系统——它记住你的项目为什么选了 A 不选 B。
2. **黑箱 → 白箱。** 决策外部化为可检查、可修改、可演化的结构化记录。
3. **高频廉价、低频昂贵。** 读取路径（每 step 注入/检索）用规则，LLM 只花在低频任务（反思/审计/深度检索）。
4. **记忆要会"整理"。** 写入去重、同题 supersede、过期复核、反馈演化——记忆是活的，不是档案柜。
5. **零摩擦。** context 自动注入，记录决策只需 3 个参数。

---

## License

MIT
