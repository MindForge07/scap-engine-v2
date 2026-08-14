/**
 * L1.5 联想通道纯逻辑测试（零 LLM，确定性）。
 *
 * 覆盖：
 *  - matchMechanisms：任务文本 → 机制命中（中/英文触发词）
 *  - isPoolAsset：联想池识别（lesson 含「元模式」）
 *  - findAssociativeCues：结构同构召回（机制 × 元模式）
 *  - buildSnapshot assocLane：注入格式与开关行为
 *
 * 运行（需要 DSH checkout 的 tsx + @types）：
 *   cd <deepseek-harness>
 *   node --import tsx/esm <scap-repo>/dsh/verify/l15-check.ts
 */
import assert from 'node:assert/strict'
import {
  buildSnapshot,
  findAssociativeCues,
  isPoolAsset,
  matchMechanisms,
  type MemoryExperience,
  type MemoryJson,
} from '../scap-injection.ts'

let passed = 0
function ok(name: string) {
  passed += 1
  console.log(`  ok  ${name}`)
}

// ── 1. 机制词典匹配 ──
console.log('== matchMechanisms ==')
const m1 = matchMechanisms('为分布式知识库设计同步方案，多设备编辑笔记，需要处理冲突检测和版本管理')
assert.ok(m1.some(m => m.id === 'event-sourcing'), 'event-sourcing 未命中（同步方案）')
assert.ok(m1.some(m => m.id === 'concurrency'), 'concurrency 未命中（冲突检测）')
ok('中文任务命中 事件溯源 + 并发')

const m2 = matchMechanisms('Design a rate limiting scheme for a public API with token bucket')
assert.ok(m2.some(m => m.id === 'rate-limit'), 'rate-limit 未命中')
ok('英文任务命中 rate-limit')

const m3 = matchMechanisms('写一个 hello world 脚本')
assert.equal(m3.length, 0, '无关任务不应命中任何机制')
ok('无关任务零命中（零噪声保底）')

// ── 2. 联想池识别 ──
console.log('== isPoolAsset ==')
const poolLike: MemoryExperience = {
  id: 'EX-1', situation: '[v0.7 认知资产 CA-0188 SI] 事件溯源',
  action: '', lesson: '该架构属于 EDA…（元模式：事件流 + 状态重建 + 版本控制）',
  importance: 2, created_at: '2026-06-26',
}
const normal: MemoryExperience = {
  id: 'EX-2', situation: 'PowerShell 编码问题', action: '用 ASCII',
  lesson: '无 BOM UTF-8 在 PS 5.1 按 GBK 读', importance: 5, created_at: '2026-08-14',
}
assert.equal(isPoolAsset(poolLike), true, '含元模式的资产应识别为联想池')
assert.equal(isPoolAsset(normal), false, '普通经验不应识别为联想池')
ok('联想池识别正确')

// ── 3. 结构同构召回 ──
console.log('== findAssociativeCues ==')
const cues = findAssociativeCues(
  '设计事件驱动的账务系统，订单状态变更需要可回放',
  [poolLike, normal],
)
assert.equal(cues.length, 1, '应召回 1 条联想线索')
assert.equal(cues[0].id, 'EX-1', '应召回事件溯源资产')
ok('任务机制 × 资产元模式 结构匹配命中')

const noCues = findAssociativeCues('写一个 hello world 脚本', [poolLike, normal])
assert.equal(noCues.length, 0, '无关任务不应召回联想线索')
ok('无关任务零联想召回')

// ── 4. buildSnapshot 注入（含真实生产投影形状）──
console.log('== buildSnapshot assocLane ==')
const mem: MemoryJson = {
  project: 'test',
  context: { tech_stack: ['Python'], conventions: [], insights: [] },
  decisions: [],
  experiences: [poolLike, normal],
}
const opts = {
  heading: '[SCAP Project Memory]', maxChars: 0, recallTopN: 5,
  residentImportance: 4, residentMaxAgeDays: 7,
}
const task = '设计事件驱动的账务系统，订单状态变更需要可回放'
const off = buildSnapshot(mem, task, opts)
assert.ok(!off.includes('联想线索'), 'assocLane 默认关闭：不应出现联想线索')
ok('assocLane 默认关闭（诚实默认）')

const on = buildSnapshot(mem, task, { ...opts, assocLane: true, assocTopN: 1 })
assert.ok(on.includes('## 联想线索（跨域）'), 'assocLane 开启后应出现联想线索节')
assert.ok(on.includes('[联想]'), '联想线索应有 [联想] 标注')
assert.ok(on.includes('元模式：事件流 + 状态重建 + 版本控制'), '应内嵌元模式信息')
ok('assocLane 开启：联想线索节 + [联想] 标注 + 元模式')

const onNoTask = buildSnapshot(mem, undefined, { ...opts, assocLane: true })
assert.ok(!onNoTask.includes('联想线索'), '无任务文本时不应注入联想线索')
ok('无任务文本零联想注入')

console.log(`\nL1.5 CHECK PASS (${passed} assertions)`)
