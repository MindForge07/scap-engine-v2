/**
 * SCAP → DSH 注入插件（闭环断点 A：输出注入管道）
 *
 * 把 .scap/{project}.md（scap 每次写入后自动导出的项目记忆）注册为
 * DeepSeek Harness system-prompt 的动态上下文：每次 assemble 时读取文件，
 * 由 agent-loop 的 RuntimeContextProjection 物化为 durable user message
 * （内容不变不重复注入），实现 README 承诺的「零 API 开销」自动注入。
 *
 * 零依赖设计：不 import 任何 @deepseek-ai/* 运行时包，可放在任意目录
 * （本仓库 dsh/ 目录），通过 DSH 用户补丁按文件路径挂载，无需改动 Harness。
 *
 * 挂载方式（$DSH_HOME/cordis.patch.yml，或 profiles/<name>/cordis.patch.yml）：
 *   - insert:
 *       - id: scap-injection
 *         name: C:/Users/XDXLC/openclaw/scap-engine-v2/dsh/scap-injection.ts
 *         config:
 *           scapDir: ""      # 缺省：从会话 cwd 向上找第一个 .scap 目录
 *           project: ""      # 缺省：cwd 的 basename
 *           maxChars: 0      # 注入文本字符上限，0 = 不限
 *           heading: "[SCAP Project Memory]"
 *
 * 注意：DSH CLI 以 tsx 启动（node --import tsx/esm），可直接加载 .ts；
 * 纯 Node 构建环境请先把本文件编译为 .mjs 再挂载。
 */
import { existsSync, readFileSync, statSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'

export const name = 'scap-injection'
export const inject = ['systemPrompt']

export interface Config {
  /** 显式 .scap 目录；缺省从会话 cwd 向上查找。 */
  scapDir?: string
  /** 项目名 → {scapDir}/{project}.md；缺省取 cwd 的 basename。 */
  project?: string
  /** 注入文本字符上限，0 = 不限。 */
  maxChars?: number
  /** 快照标题行；空字符串不输出标题。 */
  heading?: string
}

/** 一次 assemble 携带的最小上下文形状（只取本插件需要的字段）。 */
export interface AssembleLike {
  agent?: { session?: { header?: { cwd?: string } } }
}

/** system-prompt 服务的最小形状。 */
export interface SystemPromptLike {
  context(entry: { name: string; order: number; text: (assemble: AssembleLike) => string }): () => void
}

/** 插件上下文的最小形状。 */
export interface PluginContext {
  systemPrompt: SystemPromptLike
}

interface MemoryFile {
  mtimeMs: number
  size: number
  text: string
}

/** mtime+size 缓存：文件未变时不重读。 */
const cache = new Map<string, MemoryFile>()

/** 从 start 向上查找包含 .scap 目录的最近目录；到达项目根（.git）即停止，避免逃出项目找到无关的 .scap。 */
export function findScapDir(start: string, maxDepth = 12): string | undefined {
  let dir = start
  for (let depth = 0; depth < maxDepth; depth += 1) {
    const candidate = join(dir, '.scap')
    try {
      if (existsSync(candidate) && statSync(candidate).isDirectory()) return candidate
      if (existsSync(join(dir, '.git'))) return undefined // 项目根已到且无 .scap
    } catch {
      return undefined
    }
    const parent = dirname(dir)
    if (parent === dir) return undefined
    dir = parent
  }
  return undefined
}

/** 渲染 {project}.md 为注入文本；文件不存在或为空返回 ''。 */
export function renderMemory(
  scapDir: string,
  project: string,
  heading: string,
  maxChars: number,
): string {
  const file = join(scapDir, `${project}.md`)
  let text = ''
  try {
    const stat = statSync(file)
    if (!stat.isFile()) return ''
    const hit = cache.get(file)
    if (hit !== undefined && hit.mtimeMs === stat.mtimeMs && hit.size === stat.size) {
      text = hit.text
    } else {
      text = readFileSync(file, 'utf-8')
      cache.set(file, { mtimeMs: stat.mtimeMs, size: stat.size, text })
    }
  } catch {
    return ''
  }
  if (maxChars > 0 && text.length > maxChars) text = text.slice(0, maxChars)
  if (!text.trim()) return ''
  return heading ? `${heading}\n\n${text}` : text
}

/** 注册注入：每个会话 step 的 assemble 都会携带当前项目的记忆快照。 */
export function apply(ctx: PluginContext, config: Config): void {
  const scapDir = config.scapDir?.trim() || undefined
  const project = config.project?.trim() || undefined
  const heading = config.heading ?? '[SCAP Project Memory]'
  const maxChars = config.maxChars ?? 0
  ctx.systemPrompt.context({
    name: 'scap:project-memory',
    order: 50,
    text: (assemble: AssembleLike): string => {
      const cwd = assemble.agent?.session?.header?.cwd
      if (!cwd) return ''
      const dir = scapDir ?? findScapDir(cwd)
      if (!dir) return ''
      return renderMemory(dir, project ?? basename(cwd), heading, maxChars)
    },
  })
}
