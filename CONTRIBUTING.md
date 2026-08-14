# Contributing — SCAP 开发纪律

让机制持续存在，靠的不是自觉，是**写进仓库的规则**。本文件是硬性要求。

## 行为先行

1. **先写行为断言，再实现**。新机制的第一步是测试：它应该表现出什么行为？
2. **"测试通过" ≠ "效果达标"**。单元测试验证机制不坏，效果由 `dsh/verify/`
   真实组合冒烟与价值实验负责。机制变更必须跑冒烟（见下）。
3. **改变行为要带着测试改**："Tests describe behavior, not correctness."

## 验证要求（每次变更的检查单）

| 变更类型 | 必须通过 |
|---|---|
| 任何代码 | `pytest tests/`（自带覆盖率 ≥85% 门禁 + golden 快照） |
| 注入插件 / 导出格式 | 上述 + `dsh/verify/smoke.ps1`（真实组合冒烟） |
| 写入/检索/质量逻辑 | 上述 + 按 `dsh/verify/value-check.md` 跑价值实验 |
| 覆盖率 | **只升不降**：`fail_under` 只在提高后上调 |

## 环境坑清单（Windows，踩过并修复的真实坑）

| 坑 | 规避 |
|---|---|
| **PowerShell 5.1 读无 BOM 文件按 GBK** → 中文脚本乱码 | 脚本内中文改用 ASCII/英文，或文件带 BOM |
| **`Set-Content -Encoding utf8` 产生 BOM** → JSON.parse 失败 | 用 `[IO.File]::WriteAllText($p, $c, (New-Object Text.UTF8Encoding $false))` |
| **junction 指向 `profiles` 而非 `profiles\node_modules`** → Test-Path 失败 | 隔离环境 junction 目标必须精确到 node_modules |
| **删除含 junction 的目录会跟随链接删真实内容** | 先 `(Get-Item $link).Delete()` 删链接本身，再删目录 |
| **tsx CLI 与 `node --import tsx/esm` 解析不同** → FiberState 缺失 | DSH 一律用官方启动方式 `node --import tsx/esm apps/cli/src/bin.ts` |
| **相对导入层级数错**（tests → 6 级到用户目录） | 数清 `..` 或改用 file:// 绝对路径 |
| **PowerShell `$g:` 被解析为盘符** | 变量后接冒号用 `${g}` |

## 提交纪律

- 小步提交：一个机制一个 commit，message 说明"为什么"（问题→证据→方案）
- 代码与文档同 commit（README/`dsh/README.md` 同步更新，禁止事后追认）
- 新机制必须同时：行为测试 + golden（如涉及格式）+ 覆盖率不降

## 机制变更是怎么被验证的（流程）

```
提出机制（对应哪个被证明的问题？没有问题的机制不做——latent 层的教训）
  → 行为断言先行
  → 实现 + 单元/golden
  → 覆盖率检查（不降）
  → 真实组合冒烟（注入/格式相关）
  → 需要时价值实验（写/检索/质量逻辑）
  → 文档同 commit
```
