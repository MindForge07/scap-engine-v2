# 联想通道设计：从检索悖论到双联想创新（L1.5）

> 状态：**设计草案**（未实现） | 关联：`dsh/scap-injection.ts`（L1 现网）、`dsh/migrate/learnings-to-scap.py`（联想池素材）、`scap/embedder.py`
> 触发：用户理念「旧思考 × 新情境 → 碰撞出新灵感」+ 检索悖论研究结论

## 1. 问题：L1 纯相关性召回 = 检索悖论

当前 L1 按任务相关性打分（CJK bigram + 拉丁词 + IDF + recency + importance），
只注入**与当前任务相似**的记忆。这正是记忆检索的经典悖论
（[The retrieval paradox in agent memory](https://moltbook.com/post/8894d2e0-6b1e-4eab-9478-039097398794)，
[Retrieval Paradox · Wikimolt](https://wikimolt.org/page/Retrieval%20Paradox)）：

> **最相似的记忆强化既有思维路径，抑制创新。** 纯相关性召回让 agent
> 在旧框架内打转——旧思考只能被"像它"的新情境唤起，永远无法跨域碰撞。

已有实验证据表明跨域映射注入能提升 LLM 创造力
（[Serendipity by Design (arXiv 2603.19087)](https://ar5iv.labs.arxiv.org/html/2603.19087)），
而机制级跨域检索（结构同构而非语义相似）是可行路径
（[Mechanism-centric cross-domain retrieval](https://www.sciencedirect.com/science/article/abs/pii/S0950705126013936)）。
理论根基：Koestler 双联想（bisociation）、Schank 动态记忆的 cross-contextual reminding、
孵化效应（[睡眠重放提升创造性问题解决](https://pubmed.ncbi.nlm.nih.gov/29776467/)）。

## 2. 设计目标

在不破坏 L1 确定性/相关性质量的前提下，增加**低频、低噪、可验证**的联想通道：

- **探索-利用权衡**：L1 是纯利用（exploitation），L1.5 是受控探索（exploration）
- **结构匹配优先**：按元模式/机制相似而非表面关键词召回（Schank reminding）
- **默认休眠**：联想池素材低 importance（=2），不参与 L1 常驻注入，只在结构匹配时被唤起
- **零 LLM 高频路径**：联想打分用规则（与 L1 同哲学），低频碰撞配对可选 LLM

## 3. 机制设计

### 3.1 联想池（已有素材）

`migrate/learnings-to-scap.py --assets-dir` 已把 v0.7 认知资产（13 个 CA-*）迁入
XDXLC 项目，importance=2，tags 含 `asset` + `asset_type` + 元模式（lesson 内嵌
`（元模式：…）`）。**这就是联想池**：L1 排序（relevance-first）天然不唤起它们，
L1.5 可以按结构索引召回。

### 3.2 L1.5 结构同构召回（规则版）

在 `scap-injection.ts` 的 L1 之后追加：

```
输入：任务文本 T（source.kind==='user'）
1. 提取 T 的机制词：与 SCAP 内建机制词典（限流/熔断/事件溯源/锁/队列/缓存/幂等/
   重试/状态机/触发器/反馈环/降级/一致性/并发…）做子串/词形匹配
   → 命中机制 m
2. 在联想池（lesson 含「元模式」的经验）中找包含 m 或元模式同类的资产
   → 若命中且与 L1 结果无重复：注入 top-1（明确标注 [联想线索]）
3. 未命中机制词典：不注入（保底零噪声）
```

- 机制词典是**纯规则**（高频路径零 LLM），可测试、可审计
- 元模式解析：迁移脚本已在 lesson 内嵌 `（元模式：X）`，注入器可用正则提取
- 标注 [联想线索]：防止模型把联想当直接答案（诚实注入）

### 3.3 碰撞配对（低频、可选 LLM）

离线（session 间隙 / scap_audit 时）：把**新决策**与联想池中**元模式不同**
的高 fitness 资产配对，生成「双联想提示」一行注入下会话开头：

```
[联想线索] 当前状态 X 与资产 CA-0180（限流）元模式同为「资源分配约束」，
但领域不同——能否把其降级策略迁移到 Y？
```

这与 `scap_consolidate`（收敛合并相似）互补：consolidate 收敛，碰撞配对发散。

### 3.4 参数（可配置）

| 键 | 缺省 | 说明 |
|---|---|---|
| `assocLane` | false | 启用 L1.5 联想通道（默认关，实验后开） |
| `assocTopN` | 1 | 结构匹配注入条数上限 |
| `assocMechanisms` | 内置词典 | 机制词表（可扩展） |
| `assocPairing` | false | 启用离线碰撞配对 |

## 4. 验证方案（价值实验，参照 dsh/verify/value-check.md）

**假设**：联想线索注入后，创意类任务（方案设计/架构选型）产出新颖性↑，任务
准确率不降。

**实验设计**：
1. 基线：L1 仅（现状）；实验组：L1 + L1.5
2. 任务集：3 个跨域架构任务（如"设计一个知识库同步方案"——触发"事件流/状态
   重建"机制词 → 应唤起 CA-0185/0188 事件溯源资产）
3. 指标：方案新颖性（人工/LLM 评分 1-5）、方案可行性（对照）、召回准确性
   （联想线索与任务无关时不得注入——零噪声检查）
4. 记录：注入快照 + 模型产出，对比分数

**边界与失败条件**：
- 联想噪声 > 收益（创意分不升反降）→ 关闭 assocLane，机制词典收缩
- 机制词典误命中（词形歧义）→ 词典条目加领域限定
- 该实验只证明「联想注入是否有益」，不改变 L1 相关性质量（L1 保持不变）

## 5. 现状与下一步

- [x] 联想池素材：13 个 CA 资产已迁入 XDXLC（importance=2，CA-0179=4）
- [x] 元模式可检索：lesson 内嵌（元模式：X）格式
- [ ] 机制词典（内建 ~20 词 + 可扩展）
- [ ] scap-injection.ts L1.5 实现 + 配置项
- [ ] 价值实验（上节方案）→ 通过后默认开启

## 6. 相关来源

- [Serendipity by Design (arXiv 2603.19087)](https://ar5iv.labs.arxiv.org/html/2603.19087)
- [The retrieval paradox in agent memory (moltbook)](https://moltbook.com/post/8894d2e0-6b1e-4eab-9478-039097398794)
- [Retrieval Paradox · Wikimolt](https://wikimolt.org/page/Retrieval%20Paradox)
- [Mechanism-centric cross-domain retrieval (ScienceDirect)](https://www.sciencedirect.com/science/article/abs/pii/S0950705126013936)
- [HippoRAG (NeurIPS'24)](https://github.com/osu-nlp-group/hipporag)
- [Evolving Generalist Virtual Agents with Generative and Associative Memory (AAAI)](https://ojs.aaai.org/index.php/AAAI/article/view/38300)
- [Sleep replay boosts creative problem-solving (PubMed)](https://pubmed.ncbi.nlm.nih.gov/29776467/)
- [Sleep-Like Memory Consolidation for AI Agents](https://docs.bswen.com/blog/2026-03-24-memory-consolidation-sleep-ai/)
