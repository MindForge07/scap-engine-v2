# SCAP 验证体系（持续存在的机制）

本目录把"真实环境验证"从一次性冒烟固化为**可复现、可回归、可审计**的机制。

## 验证分层

| 层 | 位置 | 时机 | 门禁 |
|---|---|---|---|
| 单元/集成测试 | `tests/`（225+） | 每次 `pytest` | **覆盖率 ≥85% 自动门禁**（pyproject fail_under，`addopts` 自带 --cov） |
| golden 快照 | `tests/test_snapshots.py` | 每次 `pytest` | 注入/导出格式变更必须同步更新 golden |
| **真实组合冒烟** | `dsh/verify/smoke.ps1` | **每次机制/注入变更** | 真实 DSH + 真实 LLM 断言注入分层行为 |
| 价值实验 | `dsh/verify/value-check.md`（协议） | 按需（机制大改/发布前） | grounding/一致性行为不回归 |

## 何时跑什么

- **任何代码变更**：`pytest tests/`（自动含覆盖率门禁 + golden）
- **注入插件/导出格式变更**：先 `pytest`，再 `dsh/verify/smoke.ps1`（真实组合冒烟）
- **记忆机制大改（写入/检索/质量逻辑）**：跑价值实验协议（value-check.md）确认正效益行为不回归
- **发布前**：全量 + 冒烟 + 价值实验

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
