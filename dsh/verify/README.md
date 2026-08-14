# SCAP 验证体系（持续存在的机制）

本目录把"真实环境验证"从一次性冒烟固化为**可复现、可回归、可审计**的机制。

## 验证分层

| 层 | 位置 | 时机 | 门禁 |
|---|---|---|---|
| 单元/集成测试 | `tests/`（230+） | 每次 `run-checks.ps1` / CI | **覆盖率 ≥85% 门禁**（pyproject fail_under） |
| golden 快照 | `tests/test_snapshots.py` | 同上 | 注入/导出格式变更必须同步更新 golden |
| **压力/规模** | `tests/test_scale.py`（5k 决策 + 2k 经验） | 同上 | 检索/注入/导出性能预算 + 大库迁移完整性 |
| **真实组合冒烟** | `dsh/verify/smoke.ps1` | **每次机制/注入变更** | 真实 DSH + 真实 LLM 断言注入分层行为 |
| **生产流量回放** | `dsh/verify/replay.py` | 每次机制/检索变更 | 真实 DSH 会话消息离线回放，零异常 |
| 价值实验 | `dsh/verify/value-check.md`（协议） | 按需（机制大改/发布前） | grounding/一致性行为不回归 |

## 何时跑什么

- **任何代码变更**：`dsh/verify/run-checks.ps1`（单元 + golden + 规模 + 覆盖率门禁，CI 同命令）
- **注入插件/导出格式变更**：先 run-checks，再 `dsh/verify/smoke.ps1`（真实组合冒烟）
- **检索/写入/质量逻辑变更**：run-checks + `dsh/verify/replay.py`（生产流量回放）
- **记忆机制大改（写入/检索/质量逻辑）**：加跑价值实验协议（value-check.md）
- **发布前**：全部

> 单文件调试请直接 `pytest tests/test_x.py`——覆盖率门禁只在 run-checks/CI 触发，不干扰调试循环。

## 快速开始

```powershell
# 1. 单元 + golden + 覆盖率门禁
pytest tests/

# 2. 真实组合冒烟（需 DSH checkout 与真实 LLM 凭据，自动隔离、测后清理）
.\dsh\verify\smoke.ps1 -Harness C:\path\to\deepseek-harness -ScapRepo C:\path\to\scap-engine-v2 -Python C:\Python314\python.exe
```

## 铁律（防回归的机制）

1. **行为先行**：新机制先写行为断言，再实现；"测试通过"不等于"效果达标"——效果由冒烟/价值实验负责
2. **覆盖率只升不降**：fail_under 只在覆盖率提高后上调，绝不下调
3. **golden 是契约**：注入/导出格式改动必须同步更新 golden 断言（格式是 dsh/scap-injection 与人类读的契约）
4. **真实组合测试不可省**：mock/单测通过后，机制变更必须跑一次真实冒烟（防"单测全绿但真实链路坏"——本项目真实踩过）
