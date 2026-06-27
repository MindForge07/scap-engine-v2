# SCAP v2 — Project Memory System for LLM Agents

> **让 AI 记住你的项目，而不是每次都从零开始。**

LLM 是一个"顶级大脑"——它知道分布式系统怎么设计、熔断器怎么实现、CAP 定理是什么。但它不知道：

- 你的项目为什么选了 Kafka 而不是 RabbitMQ
- 三个月前你们从 MongoDB 迁移到 PostgreSQL 的理由
- 团队约定所有状态变更必须走事件溯源
- 上次线上事故的根因是 N+1 查询

这些**项目级决策记忆**散落在 Slack、文档、和人的脑子里。SCAP 把它们结构化存储，让 AI 在每次新任务前自动读取。

---

## 核心设计

```
AI 收到任务
   │
   ├─ 上下文已在 system prompt 中（自动导出的 context.md）
   │  → AI 知道项目栈、约定、历史决策、经验教训
   │
   └─ AI 做出重要决策
      │
      └─ 调用 scap_remember → 数据库更新 → context.md 自动重新导出
```

**零额外 API 开销**：读取上下文不需要调用任何工具——context.md 已经在 system prompt 中。只有记录新决策时才需要 1 次调用。

**零 LLM 依赖**：存储和检索不依赖任何 LLM。决策直接结构化写入 SQLite，FTS5 全文搜索。

**AI 自记录**：MCP server 的 instructions 引导 AI 主动在决策后调用 `scap_remember`，不需要人工干预。

---

## Quick Start

```bash
# 安装
cd scap-engine-v2
pip install -e .

# 初始化项目
scap init --project myapp --stack "PostgreSQL" --stack "Redis" --stack "Kafka"

# 手动记录一条决策
scap search "数据库"

# 导出上下文到 .scap/myapp.md（放入 system prompt）
scap export --project myapp

# 启动 MCP 服务（用于 AI 集成）
python -m scap.mcp_server
```

### MCP 客户端配置

```json
{
  "mcpServers": {
    "scap": {
      "command": "python",
      "args": ["-m", "scap.mcp_server"],
      "env": {
        "SCAP_EXPORT_DIR": ".scap"
      }
    }
  }
}
```

---

## MCP Tools

| 工具 | 触发时机 | 参数 | 说明 |
|------|---------|------|------|
| `scap_recall` | 任务前（可选） | project + task | 检索项目记忆，按任务相关度排序 |
| `scap_remember` | 决策后 | project + title + decision + [rationale] | 记录决策，自动更新 context.md |
| `scap_record_experience` | 经验后 | project + situation + action + lesson | 记录经验教训，自动更新 context.md |
| `scap_context` | 新会话开始 | project | 获取完整项目快照 |
| `scap_status` | 任何时候 | (无) | 系统概览 |

---

## CLI Commands

| 命令 | 说明 |
|------|------|
| `scap init` | 初始化项目（设置 tech_stack） |
| `scap status` | 查看系统状态（决策数、经验数、项目列表） |
| `scap search "关键词"` | 搜索项目记忆 |
| `scap list` | 列出所有决策 |
| `scap export --project X` | 导出 context.md（放入 system prompt） |
| `scap configure --project X --stack "..."` | 更新项目上下文 |
| `scap ingest --file doc.md` | 从 markdown 文件导入决策 |

---

## Architecture

```
scap/
├── models.py        — 3 个核心实体
│   ├── Decision          技术选型 + 理由 + 否决方案
│   ├── ProjectContext    技术栈 + 约定 + 活跃目标
│   └── Experience        情况 + 行动 + 经验教训
│
├── store.py         — SQLite + FTS5 存储层
│   ├── 项目域隔离查询
│   ├── 中文 bigram 分词回退
│   └── export_context() 导出 markdown
│
├── mcp_server.py    — FastMCP 5 个工具
│   ├── AI 自记录协议（instructions 引导）
│   └── 每次写入后自动导出 context.md
│
└── cli.py           — 8 个 CLI 命令
```

---

## 上下文导出格式

每次写入后自动生成 `.scap/{project}.md`，格式如下：

```markdown
# Project Memory: acme

## Tech Stack
PostgreSQL 15, Redis 7, Kafka 3.5

## Conventions
- 所有状态变更必须走事件溯源

## Decisions

### 消息队列选型 (2026-06-26)
**Chosen:** Kafka
**Why:** 吞吐量需求 50k msg/s
- ~~RabbitMQ~~ (rejected: 10k+ 性能下降)

## Lessons Learned

- **上线后 CPU 飙到 90%**
  Action: 加了 ReadOnly 注解 + fetch join
  → JPA 查询必须加 @EntityGraph 防 N+1
```

这个文件直接放进 MCP 客户端的 system prompt，AI 从第一句话就知道项目的全部历史。

---

## 数据模型

### Decision（决策记录）

```python
{
  "id": "DC-20260626-0001",
  "project": "acme",
  "title": "消息队列选型",
  "decision": "Kafka",
  "rationale": "吞吐量需求 50k msg/s, RabbitMQ 在 10k+ 时性能下降明显",
  "alternatives": [{"name": "RabbitMQ", "reason_rejected": "性能瓶颈"}],
  "constraints": ["必须支持 15 种货币", "团队已有 Kafka 运维经验"],
  "status": "active",  // active | superseded | deprecated
  "tags": ["消息队列", "基础设施"]
}
```

### Experience（经验教训）

```python
{
  "id": "EX-20260626-0001",
  "project": "acme",
  "situation": "上线后 CPU 飙到 90%",
  "action": "加了 ReadOnly 注解 + fetch join",
  "lesson": "JPA 查询必须加 @EntityGraph 防 N+1",
  "tags": ["性能", "JPA"]
}
```

### ProjectContext（项目上下文）

```python
{
  "project": "acme",
  "tech_stack": ["PostgreSQL 15", "Redis 7", "Kafka 3.5"],
  "conventions": ["所有状态变更必须走事件溯源"],
  "active_goals": ["年底测试覆盖率 80%"]
}
```

---

## 测试

```bash
# 运行全部测试（78 个，17 秒）
pytest tests/ -v

# 分模块
pytest tests/test_models.py      # 11 tests — 模型验证
pytest tests/test_store.py       # 15 tests — 存储 CRUD + FTS5
pytest tests/test_mcp.py         # 16 tests — MCP 工具 + 自动导出
pytest tests/test_cli.py         #  7 tests — CLI 命令
pytest tests/test_stress.py      # 29 tests — 并发/大负载/Unicode/搜索质量
```

---

## v1 vs v2

| | SCAP v1 | SCAP v2 |
|---|---------|---------|
| **定位** | 认知资产管线 | 项目记忆系统 |
| **存储** | 经 LLM 管线提取的模式 | 结构化决策记录（无需 LLM） |
| **LLM 依赖** | 5 个 Agent 全程依赖 | **零依赖** |
| **价值** | 通用模式迁移 | 项目级决策一致性 |
| **检索** | 语义向量 + FTS5 + LLM 重排 | FTS5 项目域限定 |
| **上下文获取** | 每次调用 scap_recall（1 API） | 自动导出 context.md（0 API） |
| **触发方式** | 手动 collect_arc | AI 自记录（instructions 引导） |
| **代码量** | ~18,000 行 | **~1,100 行** |

---

## 设计哲学

1. **LLM 不缺通用知识，缺的是项目上下文。** SCAP 不教模型怎么设计系统——它记住了你的项目为什么选了这个方案而不是那个。

2. **黑箱 → 白箱。** 模型的推理过程是不可见的。SCAP 把决策外部化为可检查、可修改、可复用的结构化记录。

3. **零摩擦。** context.md 自动导出，AI 从第一句话就知道项目历史。记录新决策只需要 3 个参数（title + decision + rationale），1 秒完成。

4. **项目域隔离。** 不同项目的记忆完全独立，不会互相干扰。

---

## License

MIT
