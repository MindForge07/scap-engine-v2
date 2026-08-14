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
- [x] **L1.5 已实现**（`scap-injection.ts`）：
  - 机制词典 `DEFAULT_MECHANISMS`（12 机制 × 触发词 × 结构词，可扩展）
  - `matchMechanisms` / `isPoolAsset` / `findAssociativeCues`（纯规则零 LLM）
  - 配置：`assocLane`（默认 false）、`assocTopN`、`assocMechanisms`
  - 注入格式：`## 联想线索（跨域）` + `[联想]` 标注 + 元模式内嵌
- [x] 纯逻辑测试 `dsh/verify/l15-check.ts`（9 断言，零 LLM 确定性）
- [x] 生产数据验证：同步方案→CA-0188、限流→CA-0180、无关任务零召回
- [x] **价值实验**（`dsh/verify/creativity-exp.ps1` + `judge-only.ps1`）：3 任务 × 2 组 × 2 次 + 盲评

## 6. 价值实验结果（2026-08-14）：假设被证伪

**结论：联想线索（当前实现形式）不提升新颖性，甚至略降。`assocLane` 保持默认关闭。**

盲评（真实 LLM，Dn 标签与组别解耦）均值：

| 组 | 新颖性 (1-5) | 可行性 (1-5) |
|---|---|---|
| A 基线（L1 仅） | **2.83** | **4.33** |
| B 联想（L1+L1.5） | 2.50 | 3.83 |

PASS 标准（B 新颖性 > A 且 B 可行性 ≥ A）未满足。三个任务（同步/限流/重试）全部一致：B 组新颖性不高于 A。

**归因分析（judge 评语 + 机制回顾）**：

1. **结构同构 ≠ 跨域碰撞（核心教训）**。联想池资产与任务**同领域同机制**（限流任务→限流资产、同步任务→事件溯源资产）：它们本来就是"教科书架构模式"（judge 评语："All four converge on the same textbook solution"）。注入后模型被**锚定在教科书模式上**，强化既有路径——这正是检索悖论的另一面：结构同构检索同样可能固化思维，因为资产与任务太"同构"。
2. **真正的双联想需要领域无关素材**。Koestler bisociation 的碰撞来自"表面领域不同、底层机制可迁移"——如限流任务撞上水库调度/队列背压、同步任务撞上数据库复制。当前联想池（v0.7 benchmark 架构资产）不具备这种素材，注入的是"同域教科书"，不产生碰撞。
3. **信号存在但噪声淹没**。B 组单份输出确有亮点（judge 评 t2-D3"adaptive controller from backend p95 feedback"、t3-D3"failure taxonomy + DB-driven retry"为最独特），但另一份 B 输出垫底，2 runs 样本下无统计显著提升。

**下一步（重新设计，非关闭）**：
- [ ] 联想池改为收集**跨域类比素材**（非本领域架构模式）：nature-inspired / 物理 / 社会系统 / 其他行业方案，机制词典匹配后注入"看起来无关但机制可迁移"的资产
- [ ] 增加领域距离度量：注入前过滤与任务同领域的候选（同域即跳过——结构同构但领域相同的资产交给 L1 即可）
- [ ] 实验升级：样本 2→5 runs，judge 增加一致性校验（同一设计评两次）
- [ ] 全部通过后才考虑默认开启

## 7. 聚焦类比实验结果（2026-08-14）：迁移发生、质量持平

**实验动机**：L1.5 被证伪后，用户提出新假设——「复杂问题用本质相同的过去经验聚焦思考，是否比直接深思更高效」（Gick & Holyoak 辐射实验的现代版：跨域但同构的案例 + 提示 → 迁移率 30%→80%）。

**设计**（`dsh/verify/analogy-exp.ps1` + `analogy-judge.ps1`，隔离 DSH + 真实 LLM）：
- 3 复杂任务（同步/限流/重试）× 3 组 × 2 次 = 18 输出
- A 直接深思（无注入）；B 同本质经验 + 聚焦指令（"本质相同，先分析如何解决再迁移"）；C 同本质经验无指令
- 本质案例为实验构造（水库泄洪→限流、军事地图同步→知识库同步、银行记账复核→支付重试），**不进生产记忆**
- 盲评 quality/depth + 策略元素迁移标志 E1/E2/E3；首轮 2600 字符截断对 B/C 不公平（Step 1 更长挤掉 Step 2），重评改用完整设计段

**结果（完整设计段盲评均值）**：

| 组 | quality | depth | E2 迁移（限流任务） |
|---|---|---|---|
| A 基线 | 4.33 | 3.67 | 0/2 |
| B 聚焦 | 4.17 | 4.17 | 4/4 |
| C 无指令 | 4.50 | 4.17 | 4/4 |

**结论**：
1. **迁移确凿**：水坝案例的 E2 策略（测输入→显式上限→先于上限行动→先丢低优先级）被 B/C 组 4/4 显式迁移到 API 限流设计（judge："dam lessons applied explicitly with load-tested parameters"），A 组 0/2 用配额/断路器替代——**本质相同的经验可跨域迁移，方向性思考被改变**。
2. **深度略升（+14%）、质量持平**：注入组 depth 4.17 vs 3.67；quality 在噪声内（B 4.17 / C 4.50 / A 4.33）。
3. **无提示效应（B≈C）**：人类实验的「提示相关→迁移率翻倍」在 LLM 上不存在——模型"看到即用"，聚焦指令不增不减。
4. **风险**：t3 B 组质量降（银行类比"最优雅但最不落地"）——过度聚焦类比可能牺牲工程细节；t1 的 E1 是教科书共识，无区分度。

**对 SCAP 的意义**：
- 「聚焦类比」作为**策略多样性通道**成立：它改变思考路径（防教科书锚定），深度有提升趋势——但**不承诺质量提升**，且样本小需复验
- 与已证伪 L1.5 的本质区别：注入**真实解决经验**（situation/action/lesson）而非教科书资产；跨领域同构而非同领域同机制
- 生产化条件：SCAP 需要积累「跨领域本质案例」（当前 XDXLC 经验多为工具坑，覆盖不足）；质量持平意味着**无正效益承诺，默认关闭**，除非复验显示深度提升可复现

## 8. 相关来源

- [Serendipity by Design (arXiv 2603.19087)](https://ar5iv.labs.arxiv.org/html/2603.19087)
- [The retrieval paradox in agent memory (moltbook)](https://moltbook.com/post/8894d2e0-6b1e-4eab-9478-039097398794)
- [Retrieval Paradox · Wikimolt](https://wikimolt.org/page/Retrieval%20Paradox)
- [Mechanism-centric cross-domain retrieval (ScienceDirect)](https://www.sciencedirect.com/science/article/abs/pii/S0950705126013936)
- [HippoRAG (NeurIPS'24)](https://github.com/osu-nlp-group/hipporag)
- [Evolving Generalist Virtual Agents with Generative and Associative Memory (AAAI)](https://ojs.aaai.org/index.php/AAAI/article/view/38300)
- [Sleep replay boosts creative problem-solving (PubMed)](https://pubmed.ncbi.nlm.nih.gov/29776467/)
- [Sleep-Like Memory Consolidation for AI Agents](https://docs.bswen.com/blog/2026-03-24-memory-consolidation-sleep-ai/)
