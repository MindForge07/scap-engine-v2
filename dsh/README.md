# SCAP × DeepSeek Harness 集成（闭环断点 A：注入管道）

`scap-injection.ts` 是 SCAP → DSH 的**输出注入插件**：把 `.scap/{project}.md`
（scap 每次写入后自动导出的项目记忆）注册为 DSH system-prompt 的动态上下文。
DSH 的 agent-loop 在每个 step 的 `assemble()` 后把它物化为 durable user message
（内容不变不重复注入），AI 从第一句话就能看到项目记忆——**零工具调用、零 API 开销**。

## 工作原理（分层注入 L0/L1/L2）

```
scap_remember / scap_record_experience（MCP 工具）
   │  写入
   ▼
SQLite + 自动导出 .scap/{project}.md + .scap/{project}.json（结构化投影）
   │  scap-injection 插件（本文件）读取 JSON
   ▼
L0 项目卡片（常驻）：Tech Stack / Conventions / Insights
L1 任务相关预检索（自动，零 LLM）：
   读取最近 user message（白名单：仅 `source.kind === 'user'`——DSH 的
   MessageSource 是 merge-extensible，插件注入（instructions/时间/运行时
   上下文）一律排除）→ 与 scap recall 同一套打分器
   （CJK bigram + 拉丁词 + IDF + recency 衰减 + importance 加成）
   → top-N 相关决策 + 常驻决策（importance≥4 或近 7 天）换入注入内容
   → 相关经验 top-2
L2 深度召回（模型自觉）：scap_recall / scap_retrieve_latent 工具
   │
   ▼
DSH systemPrompt.context → agent-loop 每 step assemble
   → RuntimeContextProjection 物化为 durable user message（内容不变不重复注入）
   → LLM 上下文
   │  记忆影响行为
   ▼
新决策 → 又调用 scap_remember → 新 .scap/{project}.json → 下一 step 自动刷新
```

**为什么 L1 用规则召回而非 LLM/代理**：L1 发生在每个 step（高频路径），LLM
只该花在低频任务（反思/归档/深度检索）上——规则召回零成本、确定性、可测试，
且决策记忆本身就是项目术语的集合（关键词+IDF+recency 已抓住主要信号），
语义长尾由可选 embedding 覆盖。这与 Generative Agents 三因子加权检索、
Mem0 读取路径规则化是同一选择。

**数据通路**：注入插件读 `.scap/{project}.json`（scap 导出时同步生成，
含 importance/status/时间戳的结构化投影），与 SQLite 解耦、零依赖；
旧目录只有 md 时自动回退全量注入（向后兼容）。

## 挂载（零改动 Harness）

1. 把本目录放入你的工作区（已随 scap 仓库存在）。
2. 在 DSH 用户补丁中挂载插件（二选一）：

```yaml
# $DSH_HOME/cordis.patch.yml（全 profile 生效）
# 或 $DSH_HOME/profiles/<name>/cordis.patch.yml（单 profile）
- insert:
    - id: scap-injection
      name: C:/Users/XDXLC/openclaw/scap-engine-v2/dsh/scap-injection.ts
      config:
        # scapDir 缺省：从会话 cwd 向上找第一个 .scap 目录
        # project 缺省：cwd 的 basename（→ .scap/{cwd名}.md）
        # maxChars 缺省 0：不限（导出文件本身可由 SCAP_EXPORT_MAX_CHARS 预算）
        heading: "[SCAP Project Memory]"
```

3. 重启 DSH（或触发 HMR）。首轮请求的 prompt 即包含 `[SCAP Project Memory]` 快照。

> `name` 指向本文件路径，DSH loader 按路径加载；CLI 以 tsx 启动可直接加载 .ts。
> 纯 Node 构建环境请先 `npx tsc dsh/scap-injection.ts --module nodenext --target es2022` 编译为 .mjs 再挂载。

## 配置项

| 键 | 缺省 | 说明 |
|---|---|---|
| `scapDir` | 自动向上查找 | 显式指定 `.scap` 目录；自动查找时**到达项目根（`.git`）即停止**，不会逃出项目找到无关的 `.scap` |
| `project` | cwd basename | 项目名，读取 `{scapDir}/{project}.md/.json` |
| `maxChars` | 0（不限） | 注入文本字符上限 |
| `heading` | `[SCAP Project Memory]` | 快照标题行；空串不输出 |
| `recallTopN` | 5 | L1 任务相关决策数量上限（1-20） |
| `residentImportance` | 4 | importance ≥ 该值的 active 决策作为常驻决策强制注入 |
| `residentMaxAgeDays` | 7 | 更新距今 ≤ 该天数的决策作为常驻决策强制注入 |
| `useTaskRecall` | true | 启用 L1 任务相关预检索；false = 只注入卡片 + 常驻决策 |

## 历史积累迁移（v1 时代 → 当前格式）

旧版（v1 时代）的经验积累是手写 markdown：`.learnings/{LEARNINGS,ERRORS,FEATURE_REQUESTS}.md`（LRN/ERR/FR 块）。
`migrate/learnings-to-scap.py` 把它们转换为当前 Experience/Decision 记录并 re-export，使分层注入自动生效：

- LRN/ERR → **Experience**（situation=Summary+Details，action=Suggested action/fix，lesson=逐条蒸馏，importance 按 Priority high=5/medium=4）
- FR → **Decision**（已验证的选择，如 .mcp.json 配置方式）
- `--assets-dir`：迁移 v0.7 认知资产（`.scap/assets`，CA-*）为**联想池**经验（importance=2 + asset 标签 + lesson 内嵌元模式），供 L1.5 联想通道结构匹配召回（见 `bisociation-design.md`）；CA-0179（已验证的 Agent-in-the-loop 模式）importance=4
- 幂等：按 (project, title/situation) 查重，重跑全 skip（四操作 NOOP 语义）
- 旧 `data/scap.db`（v2.0 时代）**不迁移**：其中只有 README 演示数据（acme-pay），按记忆正确性原则不入生产

```bash
python dsh/migrate/learnings-to-scap.py                       # 生产 DB + 项目 XDXLC
python dsh/migrate/learnings-to-scap.py --assets-dir C:\Users\XDXLC\.scap\assets
python dsh/migrate/learnings-to-scap.py --dry-run             # 只解析不写入
```

> 联想通道（L1.5，从检索悖论到双联想创新）设计见 [`bisociation-design.md`](bisociation-design.md)。

## 验证

`tests/` 之外，仓库根有完整验证方法（见 `scap-closed-loop-analysis.md` 断点 A）：
1. 写入决策 → 自动导出；
2. 新会话 assemble 输出包含记忆快照（内容不变不重复注入）；
3. 记忆被模型消费后产生新决策 → 闭环。
