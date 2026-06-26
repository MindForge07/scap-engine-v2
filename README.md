# SCAP v2 — Project Memory System

> **不是教模型更聪明，而是让模型记住你的项目。**

## What & Why

LLM 是一个"顶级大脑"——它知道分布式系统怎么设计、熔断器怎么实现、CAP 定理是什么。但它不知道：

- 你的项目为什么选了 Kafka 而不是 RabbitMQ
- 三个月前你们从 MongoDB 迁移到 PostgreSQL 的理由
- 团队约定所有状态变更必须走事件溯源

这些**项目级决策记忆**散落在 Slack、文档、和人的脑子里。SCAP 把它们结构化存储，让模型在每次新任务前都能检索到。

## How

```
任务到达 → scap_recall(检索项目记忆) → 注入上下文 → 模型作答 → scap_remember(记录新决策)
```

SCAP 不依赖 LLM 来存储——决策直接结构化写入。检索用 FTS5 全文搜索，项目域限定。

## Quick Start

```bash
pip install -e .

# Initialize
scap init --project myapp --stack "PostgreSQL" --stack "Redis"

# Record a decision
scap search "网关"  # Search

# MCP integration
python -m scap.mcp_server
```

## MCP Tools

| Tool | When | What |
|------|------|------|
| `scap_recall` | Before task | Retrieve project constraints + related decisions |
| `scap_remember` | After decision | Record new decision with rationale |
| `scap_context` | New session | Full project snapshot |
| `scap_status` | Any time | System overview |

## Architecture

```
scap/
├── models.py        — Decision / ProjectContext / Experience
├── store.py         — SQLite + FTS5
├── mcp_server.py    — FastMCP 4 tools
└── cli.py           — CLI (init/status/search/ingest)
```

## v1 vs v2

| | SCAP v1 | SCAP v2 |
|---|---------|---------|
| **定位** | 认知资产管线 | 项目记忆系统 |
| **存储** | 经 LLM 管线提取的模式 | 结构化决策记录（无需 LLM） |
| **价值** | 模式迁移（通用） | 决策一致性（项目特定） |
| **LLM 依赖** | 全程依赖 | **零依赖** |
| **检索** | 语义 + FTS5 + 重排 | FTS5 项目域限定 |
