# 诊断质量 Backlog — 待修项 (2026-06-25)

**状态**: Backlog — 本批不修改，仅登记
**批次**: 诊断质量收口 (系数批封板后)

---

## ✅ X — 评级字段未入快照 (getattr 同类 bug) — 已封板

**状态**: ✅ 已修复 (2026-06-25)
**根因**: `get_summary()` 从 `compensation[s_name]["grade"]` 读到的值是 dict，用 `getattr(dict, 'grade', '?')` 把 dict 当对象取了 — 与缺陷1 `getattr(dict, 'Ke1', 0)` 同源。

**修复**: `core/data_providers.py` — `CalibrationProvider.get_summary()` 三处全部从 `getattr(dict, key, default)` 改为 `dict.get(key, default)`，加 `isinstance(dict)` 守卫。函数内 14 处同类 getattr-on-dict 一并清除。

| 区域 | 行号 | 替换 |
|------|------|------|
| 单栅评级 | 376-380 | `getattr(grade, 'grade', grade)` → `grade.get('grade', '?')` |
| 补偿指标 (8处) | 421-443 | `getattr(metrics, key, d)` → `_m.get(key, d)` |
| 评级 grade (3处) | 447-449 | `getattr(grade, key, d)` → `_g.get(key, d)` |

**真机验证**: 报告分级 C2=正常 / A1=警告 / A2·B1·B2·C1=严重，与 Phase B 真值对应；"良"不再带"未通过"后缀；均值/范围显真值。红线未破 (backend=local, model=qwen3.5-9b)。

---

## Y — 模型把真值写成 None → 降级为观察中

**状态**: ⏸️ 观察中 (原优先级: 中)
**变更**: X 修复后本次未复现，疑似随 X 修复连带解决（评级/数值不再全为 ?/空 → 模型有可用的真值 → 不再自行填 null）。但未做提示工程硬约束，不关闭 — 需多数据条件验证决定是否关闭。

| 位置 | 文件:行号 | 说明 |
|------|-----------|------|
| 快照注入 (值源) | `data_providers.py:412-414` | `e_mean = dec_d.get('e_mean')` |
| AI JSON Schema | `ui/ai_diagnosis.py:1798-1806` | `sensor_analysis[].statistics` |
| 系统提示 | `ui/ai_diagnosis.py:1877-1881` | "严禁臆造数值" |

---

## Z · 脆性 — 对话框未 accept 读空 (不动)

**状态**: Backlog — 仍待修
**不变**: 涉及位置均未动 (`data_providers.py:293-294`, `calibration_tab.py:1827`, `calibration_tab.py:2060-2068`)。
