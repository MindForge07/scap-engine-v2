# SCAP × DeepSeek Harness 集成（闭环断点 A：注入管道）

`scap-injection.ts` 是 SCAP → DSH 的**输出注入插件**：把 `.scap/{project}.md`
（scap 每次写入后自动导出的项目记忆）注册为 DSH system-prompt 的动态上下文。
DSH 的 agent-loop 在每个 step 的 `assemble()` 后把它物化为 durable user message
（内容不变不重复注入），AI 从第一句话就能看到项目记忆——**零工具调用、零 API 开销**。

## 工作原理

```
scap_remember / scap_record_experience（MCP 工具）
   │  写入
   ▼
SQLite + 自动导出 .scap/{project}.md
   │  scap-injection 插件（本文件）读取
   ▼
DSH systemPrompt.context('scap:project-memory')
   │  agent-loop 每 step assemble
   ▼
RuntimeContextProjection → durable user message → LLM 上下文
   │  记忆影响行为
   ▼
新决策 → 又调用 scap_remember → 新 .scap/{project}.md → 下一 step 自动刷新
```

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
| `project` | cwd basename | 项目名，读取 `{scapDir}/{project}.md` |
| `maxChars` | 0（不限） | 注入文本字符上限 |
| `heading` | `[SCAP Project Memory]` | 快照标题行；空串不输出 |

## 验证

`tests/` 之外，仓库根有完整验证方法（见 `scap-closed-loop-analysis.md` 断点 A）：
1. 写入决策 → 自动导出；
2. 新会话 assemble 输出包含记忆快照（内容不变不重复注入）；
3. 记忆被模型消费后产生新决策 → 闭环。
