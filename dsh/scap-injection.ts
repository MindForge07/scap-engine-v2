/**
 * SCAP → DSH 注入插件（闭环断点 A + 分层注入 L1）
 *
 * 把项目记忆注入 DSH system prompt，分两层（纯规则、零 LLM）：
 *
 *   L0 项目卡片（常驻）：Tech Stack / Conventions / Insights
 *   L1 任务相关预检索（自动）：读取最近 user message，用与 scap recall
 *      相同的打分器（CJK bigram + 拉丁词 + IDF + recency 衰减）把相关决策
 *       换入注入内容；importance ≥ residentImportance 或近
 *       residentMaxAgeDays 天的决策作为"常驻决策"强制保留。
 *   L2 深度召回（模型自觉）：scap_recall / scap_retrieve_latent 工具。
 *
 * 数据通路：scap 导出时在 .scap/{project}.md 旁写机器可读
 * .scap/{project}.json（结构化投影，含 importance/status/时间戳），本插件
 * 读 JSON 做本地打分——零依赖、与 SQLite 解耦。旧目录只有 md 时自动回退
 * 到全量注入（向后兼容）。
 *
 * 挂载：见同目录 README.md（DSH 用户补丁按文件路径加载，零改动 Harness）。
 */
import { existsSync, readFileSync, statSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'

export const name = 'scap-injection'
export const inject = ['systemPrompt']

export interface Config {
  /** 显式 .scap 目录；缺省从会话 cwd 向上查找。 */
  scapDir?: string
  /** 项目名 → {scapDir}/{project}.md/.json；缺省取 cwd 的 basename。 */
  project?: string
  /** 注入文本字符上限，0 = 不限。 */
  maxChars?: number
  /** 快照标题行；空字符串不输出标题。 */
  heading?: string
  /** L1 任务相关决策的数量上限（默认 5）。 */
  recallTopN?: number
  /** importance ≥ 该值的 active 决策作为常驻决策强制注入（默认 4）。 */
  residentImportance?: number
  /** 更新距今 ≤ 该天数（updated_at）的决策作为常驻决策强制注入（默认 7）。 */
  residentMaxAgeDays?: number
  /** 是否启用 L1 任务相关预检索（默认 true；false = 只注入项目卡片+常驻）。 */
  useTaskRecall?: boolean
}

/** 一次 assemble 携带的最小上下文形状。 */
export interface AssembleLike {
  agent?: {
    session?: {
      header?: { cwd?: string }
      events?: readonly SessionEventLike[]
    }
  }
}

interface SessionEventLike {
  type?: string
  data?: {
    content?: readonly { type?: string; text?: string }[]
    source?: { kind?: string }
  }
}

/** system-prompt 服务的最小形状。 */
export interface SystemPromptLike {
  context(entry: { name: string; order: number; text: (assemble: AssembleLike) => string }): () => void
}

/** 插件上下文的最小形状。 */
export interface PluginContext {
  systemPrompt: SystemPromptLike
}

/** .scap/{project}.json 的结构化投影（scap store._export_json 产出）。 */
export interface MemoryJson {
  project: string
  exported_at?: string
  context?: {
    tech_stack?: string[]
    conventions?: string[]
    active_goals?: string[]
    insights?: string[]
  }
  decisions?: MemoryDecision[]
  experiences?: MemoryExperience[]
}

export interface MemoryDecision {
  id: string
  title: string
  decision: string
  rationale: string
  status: string
  importance: number
  created_at: string
  updated_at: string
}

export interface MemoryExperience {
  id: string
  situation: string
  action: string
  lesson: string
  importance: number
  created_at: string
}

// ── 缓存 ──

interface CachedFile {
  mtimeMs: number
  size: number
  text: string
}

const cache = new Map<string, CachedFile>()

function readCached(file: string): string | undefined {
  try {
    const stat = statSync(file)
    if (!stat.isFile()) return undefined
    const hit = cache.get(file)
    if (hit !== undefined && hit.mtimeMs === stat.mtimeMs && hit.size === stat.size) {
      return hit.text
    }
    const text = readFileSync(file, 'utf-8')
    cache.set(file, { mtimeMs: stat.mtimeMs, size: stat.size, text })
    return text
  } catch {
    return undefined
  }
}

// ── 项目发现 ──

/** 从 start 向上查找包含 .scap 目录的最近目录；到达项目根（.git）即停止。 */
export function findScapDir(start: string, maxDepth = 12): string | undefined {
  let dir = start
  for (let depth = 0; depth < maxDepth; depth += 1) {
    const candidate = join(dir, '.scap')
    try {
      if (existsSync(candidate) && statSync(candidate).isDirectory()) return candidate
      if (existsSync(join(dir, '.git'))) return undefined
    } catch {
      return undefined
    }
    const parent = dirname(dir)
    if (parent === dir) return undefined
    dir = parent
  }
  return undefined
}

/** 旧版回退：渲染 {project}.md 全量注入（无 JSON 投影时）。 */
export function renderMemory(
  scapDir: string,
  project: string,
  heading: string,
  maxChars: number,
): string {
  const text = readCached(join(scapDir, `${project}.md`))
  if (!text || !text.trim()) return ''
  const bounded = maxChars > 0 && text.length > maxChars ? text.slice(0, maxChars) : text
  return heading ? `${heading}\n\n${bounded}` : bounded
}

// ── L1 任务相关预检索（与 scap recall 同一套打分器，纯规则零 LLM）──

const CJK_CHAR_RE = /[\u4e00-\u9fff]/g
const LATIN_TOKEN_RE = /[a-z0-9_]+/g
/** 词法相关度下限：命中词条占比 < 1/7 视为偶然重叠而非相关。 */
const LEXICAL_FLOOR = 0.15
/** recency 衰减特征天数（与 scap _RECENCY_DECAY_DAYS 一致）。 */
const RECENCY_DECAY_DAYS = 45.0

/** 任务/文本 → 匹配词条：CJK 双字 + 拉丁词（≥2 字符）。 */
export function taskMatchTerms(text: string): string[] {
  const lower = text.toLowerCase()
  const terms: string[] = []
  for (const word of lower.matchAll(LATIN_TOKEN_RE)) {
    if (word[0].length >= 2) terms.push(word[0])
  }
  const cjk = lower.match(CJK_CHAR_RE)
  if (cjk) {
    for (let i = 0; i < cjk.length - 1; i += 1) terms.push(cjk[i] + cjk[i + 1])
  }
  return terms
}

/** 候选文本集合上的 IDF 权重（罕见词条权重大）。 */
export function computeIdf(terms: string[], corpusTexts: string[]): Map<string, number> {
  const n = Math.max(corpusTexts.length, 1)
  const df = new Map<string, number>()
  for (const text of corpusTexts) {
    const seen = new Set<string>()
    for (const t of terms) {
      if (!seen.has(t) && text.includes(t)) {
        seen.add(t)
        df.set(t, (df.get(t) ?? 0) + 1)
      }
    }
  }
  const idf = new Map<string, number>()
  for (const t of terms) {
    idf.set(t, Math.log(1 + n / (1 + (df.get(t) ?? 0))))
  }
  return idf
}

function ageDays(iso: string | undefined, nowMs: number): number {
  if (!iso) return 0
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return 0
  return Math.max((nowMs - t) / 86_400_000, 0)
}

/** 一条决策对任务的词法相关度（0..1）：floor 判定用计数占比，打分用 IDF 加权，乘 recency 衰减。 */
export function scoreDecision(
  terms: string[],
  text: string,
  importance: number,
  updatedAt: string | undefined,
  idf: Map<string, number> | undefined,
  nowMs: number,
): number {
  let base = 0
  if (terms.length > 0) {
    const haystack = text.toLowerCase()
    let matchedCount = 0
    let matchedWeight = 0
    let totalWeight = 0
    for (const t of terms) {
      const w = idf?.get(t) ?? 1
      totalWeight += w
      if (haystack.includes(t)) {
        matchedCount += 1
        matchedWeight += w
      }
    }
    if (totalWeight > 0 && matchedCount / terms.length >= LEXICAL_FLOOR) {
      base = matchedWeight / totalWeight
    }
  }
  if (base <= 0) return 0
  // importance bonus: 高重要决策在同相关度下优先（±20% 范围）。
  base *= 0.9 + 0.05 * Math.min(Math.max(importance, 1), 5)
  return base * Math.exp(-ageDays(updatedAt, nowMs) / RECENCY_DECAY_DAYS)
}

function textOfEvent(event: SessionEventLike): string | undefined {
  const content = event.data?.content
  if (!content || content.length !== 1) return undefined
  const block = content[0]
  return block?.type === 'text' && block.text ? block.text : undefined
}

/** 最近一条真实用户消息，作为 L1 任务文本。
 *
 * MessageSource is merge-extensible (plugins add kinds like
 * 'agent-instructions'), so a blacklist can never keep up: we accept ONLY
 * `kind: 'user'` — plugin injections (instructions, time context, runtime
 * context) are excluded by construction.
 */
export function lastUserTask(agent: AssembleLike['agent']): string | undefined {
  const events = agent?.session?.events
  if (!events) return undefined
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const event = events[i]
    if (event?.type !== 'user/message') continue
    if (event.data?.source?.kind !== 'user') continue
    const text = textOfEvent(event)
    if (text && text.trim()) return text.trim()
  }
  return undefined
}

// ── L0+L1 分层组装 ──

interface SnapshotOptions {
  heading: string
  maxChars: number
  recallTopN: number
  residentImportance: number
  residentMaxAgeDays: number
}

function dateOf(iso: string | undefined): string {
  if (!iso) return ''
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(iso)
  return m ? m[1] : iso.slice(0, 10)
}

function decisionText(d: MemoryDecision): string {
  return `${d.title} ${d.decision} ${d.rationale}`
}

/** 组装分层快照：项目卡片 + 任务相关决策 + 常驻决策 + 相关经验。 */
export function buildSnapshot(mem: MemoryJson, task: string | undefined, opts: SnapshotOptions): string {
  const now = Date.now()
  const parts: string[] = []
  const ctx = mem.context ?? {}

  // L0 项目卡片（常驻，小预算）
  if (ctx.tech_stack && ctx.tech_stack.length > 0) {
    parts.push(`## Tech Stack\n${ctx.tech_stack.join(', ')}`)
  }
  if (ctx.conventions && ctx.conventions.length > 0) {
    parts.push(`## Conventions\n${ctx.conventions.map(c => `- ${c}`).join('\n')}`)
  }
  if (ctx.insights && ctx.insights.length > 0) {
    parts.push(`## Insights\n${ctx.insights.map(i => `- ${i}`).join('\n')}`)
  }

  // L1 决策：任务相关 top-N + 常驻（importance/时效）强制保留
  const active = (mem.decisions ?? []).filter(d => d.status === 'active')
  const terms = task ? taskMatchTerms(task) : []
  const idf = computeIdf(terms, active.map(decisionText))
  const scored = active
    .map(d => ({ d, score: scoreDecision(terms, decisionText(d), d.importance, d.updated_at, idf, now) }))
    .filter(x => x.score > 0)
    .sort((a, b) => b.score - a.score)
  const resident = active.filter(d =>
    (d.importance ?? 3) >= opts.residentImportance
    || ageDays(d.updated_at, now) <= opts.residentMaxAgeDays,
  )
  const picked: MemoryDecision[] = []
  const seen = new Set<string>()
  for (const { d } of scored.slice(0, opts.recallTopN)) {
    if (!seen.has(d.id)) {
      picked.push(d)
      seen.add(d.id)
    }
  }
  for (const d of resident) {
    if (!seen.has(d.id)) {
      picked.push(d)
      seen.add(d.id)
    }
  }
  if (picked.length > 0) {
    parts.push(
      task
        ? `## 相关决策（任务: ${task.slice(0, 80)}）`
        : '## 项目决策',
    )
    picked.forEach((d, i) => {
      const imp = d.importance ?? 3
      parts.push(`${i + 1}. [${d.status}] ${d.title} (${dateOf(d.created_at)}) importance=${imp}`)
      if (d.decision) parts.push(`   - 决策: ${d.decision}`)
      if (d.rationale) parts.push(`   - 理由: ${d.rationale}`)
    })
  }

  // 相关经验（词法 top-2）
  const experiences = mem.experiences ?? []
  if (task && experiences.length > 0) {
    const expIdf = computeIdf(terms, experiences.map(e => `${e.situation} ${e.action} ${e.lesson}`))
    const expScored = experiences
      .map(e => ({
        e,
        score: scoreDecision(terms, `${e.situation} ${e.action} ${e.lesson}`, e.importance ?? 3, e.created_at, expIdf, now),
      }))
      .filter(x => x.score > 0)
      .sort((a, b) => b.score - a.score)
    if (expScored.length > 0) {
      parts.push('## 相关经验')
      for (const { e } of expScored.slice(0, 2)) {
        parts.push(`- ${e.situation || e.lesson}: ${e.lesson}`)
      }
    }
  }

  if (parts.length === 0) return ''
  let text = `[SCAP Project Memory]\n\n${parts.join('\n\n')}`
  if (opts.maxChars > 0 && text.length > opts.maxChars) {
    text = text.slice(0, opts.maxChars)
  }
  return opts.heading ? `${opts.heading}\n\n${text}` : text
}

// ── 注册注入 ──

/** 注册注入：每个会话 step 的 assemble 携带分层项目记忆快照。 */
export function apply(ctx: PluginContext, config: Config): void {
  const scapDir = config.scapDir?.trim() || undefined
  const project = config.project?.trim() || undefined
  const heading = config.heading ?? '[SCAP Project Memory]'
  const maxChars = config.maxChars ?? 0
  const recallTopN = Math.min(Math.max(config.recallTopN ?? 5, 1), 20)
  const residentImportance = Math.min(Math.max(config.residentImportance ?? 4, 1), 5)
  const residentMaxAgeDays = Math.max(config.residentMaxAgeDays ?? 7, 0)
  const useTaskRecall = config.useTaskRecall !== false
  ctx.systemPrompt.context({
    name: 'scap:project-memory',
    order: 50,
    text: (assemble: AssembleLike): string => {
      const agent = assemble.agent
      const cwd = agent?.session?.header?.cwd
      if (!cwd) return ''
      const dir = scapDir ?? findScapDir(cwd)
      if (!dir) return ''
      const proj = project ?? basename(cwd)
      // L1: 分层注入（JSON 投影）；无 JSON 时回退全量 md（向后兼容）。
      const jsonText = readCached(join(dir, `${proj}.json`))
      if (jsonText) {
        try {
          const mem = JSON.parse(jsonText) as MemoryJson
          const task = useTaskRecall ? lastUserTask(agent) : undefined
          return buildSnapshot(mem, task, {
            heading, maxChars, recallTopN, residentImportance, residentMaxAgeDays,
          })
        } catch {
          // malformed projection — fall through to the markdown fallback
        }
      }
      return renderMemory(dir, proj, heading, maxChars)
    },
  })
}
