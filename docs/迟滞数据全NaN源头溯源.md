# 迟滞数据全 NaN 源头溯源

**日期**: 2026-06-28  
**分支**: llama-cpp  
**状态**: 只记现状 + 根因 + 证据，不给方案

---

## 1. PhaseBWorker 算 T_abs/eps_orig 的代码

### 1.1 计算位置

`ui/calibration_tab.py:752-809`（`PhaseBWorker.run()`）

```python
# line 758-759: compute dL for the full monitoring df
dL_cols = [c for c in self.wavelength_cols if c in self.df.columns]
df_aug, _base = compute_dL(self.df.copy(), dL_cols)

# line 795-796: read dL arrays (length = monitoring data rows ≈ 98450)
dl1 = df_aug[f"{wcol1}_d"].values    # ~98450 elements
dl2 = df_aug[f"{wcol2}_d"].values    # ~98450 elements

# line 799: "标定KT解耦" — eps_orig always NaN (det=0 by construction, see §3)
eps_orig, dT_orig = decouple(dl1, dl2, Ke1, Ke1 * 0.95, Ke2, Ke2 * 0.95)

# line 801: "修正解耦" — eps_corr/dT_corr may be NaN if S1≈S2
eps_corr, dT_corr = decouple(dl1, dl2, Ke1, S1, Ke2, S2)
T_abs = dT_corr + T_base

# line 804-809: stored in sensors dict
sensors[pfx] = {
    "eps_orig": eps_orig,       # ← numpy array, length ~98450
    "dT_orig": dT_orig,
    "eps_corr": eps_corr,
    "dT_corr": dT_corr,
    "T_abs": T_abs,             # ← numpy array, length ~98450
    "S1": S1, "S2": S2, "T_base": T_base,
}
```

### 1.2 数据去向

```
PhaseBWorker.finished(result)
  → PhaseBDialog._on_done → self._last_result = result
    → accept() → _write_state_to_main_page()
      → tp._phase_b_state["sensors"] = self._last_result.get("sensors", {})
        → from_providers 读 pb["sensors"][name].{T_abs, eps_orig}
          → hyst_sensors.append({'name', 'T_abs': list, 'eps': list})
            → gen_charts_from_bundle → np.array(hs['T_abs']) → all NaN → valid=0 → skip
```

---

## 2. 长度 98447 的来源 — 逐行监测数据，非标定台阶

### 2.1 数据源头

`PhaseBDialog._on_run()`（`calibration_tab.py:1848-1849`）：

```python
time_h = np.arange(len(self._df)) * sample_s / 3600.0
```

`self._df` = `TemperatureCalibrationPage._loaded_df` = `parse_enlight_file()` 的输出 — ENLIGHT 监测文件，98450 行。

### 2.2 计算粒度

`compute_dL` 对全 DataFrame 逐行计算 `_d` 列 → `decouple` 对全数组逐元素解耦 → `eps_orig`/`T_abs` 继承 `dl1`/`dl2` 的长度 → 98450 个点。

**这是逐行监测粒度，不是标定台阶粒度。** 迟滞回线每个传感器应有 ~10-100 个点（每个温度台阶一个聚合值），而不是 ~100,000 个点（每个采样瞬间一个值）。

### 2.3 对比正确粒度

`PhaseAWorker.run()` 调 `detect_plateaus` → 产出 `P`（平台 DataFrame），每平台一行，这才是标定台阶粒度。但 `PhaseBWorker.run()` **不对平台聚合**——它直接解耦整个监测时间序列。

---

## 3. 为什么全 NaN

### 3.1 `decouple` 函数

`dp_engine/calibration/temperature_calibration.py:203-239`

```python
def decouple(dl1, dl2, Ke1, KT1, Ke2, KT2):
    det = Ke1 * KT2 - KT1 * Ke2              # line 226
    if abs(det) < 1e-9:                       # line 228
        eps = np.full_like(..., np.nan)       # line 231 ← ALL NaN
        dT = np.full_like(..., np.nan)        # line 232 ← ALL NaN
        return eps, dT
    eps = (KT2 * dl1 - KT1 * dl2) / det      # line 237
    dT = (Ke1 * dl2 - Ke2 * dl1) / det       # line 238
    return eps, dT
```

**`abs(det) < 1e-9` → 整列 NaN，无一幸免。**

### 3.2 `eps_orig` — det=0 永远为真（根因 A）

`calibration_tab.py:799`：

```python
eps_orig, dT_orig = decouple(dl1, dl2, Ke1, Ke1 * 0.95, Ke2, Ke2 * 0.95)
#                                KT1 = Ke1 * 0.95      KT2 = Ke2 * 0.95

det = Ke1 * KT2 - KT1 * Ke2
    = Ke1 * (Ke2 * 0.95) - (Ke1 * 0.95) * Ke2
    = 0.95 * Ke1 * Ke2 - 0.95 * Ke1 * Ke2
    = 0
```

矩阵两列互为标量倍（列 2 = 列 1 × 0.95），**线性相关 → det 恒为 0 → `eps_orig` + `dT_orig` 恒为全 NaN**。这是设计的必然结果，不是偶然巧合——无论 Ke1/Ke2 取什么值，`KT = Ke × 0.95` 这个公式就保证了 det=0。

### 3.3 `eps_corr` / `T_abs` — 可能也为 NaN（根因 B）

`calibration_tab.py:801-802`：

```python
eps_corr, dT_corr = decouple(dl1, dl2, Ke1, S1, Ke2, S2)
T_abs = dT_corr + T_base

det = Ke1 * S2 - S1 * Ke2
```

用实测 S_eff（`S1`, `S2`）替代理应独立的标定 KT，det 通常非零。**但如果 S1 ≈ S2**（两个光栅温度灵敏度相同，FBG 常见情况），`det ≈ 0` → `dT_corr` 全 NaN → `T_abs` 全 NaN。

### 3.4 `from_providers` 读的是哪个

`core/chart_bundle.py:183-184`：

```python
T_abs = sd.get('T_abs', [])                               # ← dT_corr + T_base
eps = sd.get('eps_orig', sd.get('eps_corr', []))           # ← eps_orig 优先!
```

| 字段 | 来源 | 是否 NaN | 原因 |
|------|------|---------|------|
| `eps_orig`（优先） | `decouple(dl1, dl2, Ke1, Ke1*0.95, Ke2, Ke2*0.95)` | **永远全 NaN** | det=0（根因 A） |
| `eps_corr`（降级） | `decouple(dl1, dl2, Ke1, S1, Ke2, S2)` | 可能 NaN | det≈0 若 S1≈S2（根因 B） |
| `T_abs` | `dT_corr + T_base` | 可能 NaN | dT_corr 来自 eps_corr 的 `decouple`（根因 B） |

**`eps` 优先取 `eps_orig` → 永远全 NaN。即使 eps 侥幸有效，`T_abs` 也可能全 NaN。**

---

## 4. 迟滞回线真正该用的数据

### 4.1 迟滞回线的正确含义

迟滞回线 (hysteresis loop) = 升降温过程中温度 T 与表观应变 ε 的关系曲线。每个温度台阶一个点，升程和降程分开连线形成回环。

**正确数据粒度**：每个传感器 ~10-100 个点（N 个温度台阶 × 来回程），不是 ~100,000 个点。

### 4.2 正确数据在 PhaseB 里存在吗

不——至少不在当前落盘路径里。

- `PhaseBWorker.sensors[name].eps_corr`：逐行监测序列，~98450 点，未按温度台阶聚合
- `PhaseAWorker` 产出的 `plateaus` DataFrame：有平台级别的 dL 值，但未被 PhaseB 消费用于迟滞回线
- 迟滞图需要的"每台阶 (T, ε)" 聚合数据：**当前代码不产生**

### 4.3 迟滞回线的输入结构

`gen_charts_from_bundle:400-419` 期望的 `hyst_sensors` 结构：

```python
for hs in cd.get('hyst_sensors', []):
    T_arr = np.array(hs['T_abs'], ...)     # ← 每台阶温度
    eps_arr = np.array(hs['eps'], ...)     # ← 每台阶应变
    fig = make_hysteresis_loop(T_arr, eps_arr, sensor=n, ...)
```

`make_hysteresis_loop` 期望 (T, ε) 数据点连成回线——每台阶一个聚合点，非逐行原始序列。

---

## 5. 根因指向

**三条根因叠加，导致 hyst_sensors=6 但 valid=0：**

| # | 根因 | 影响 | 证据 |
|---|------|------|------|
| **根因 A** | `eps_orig` 计算 det=0 恒为真 → 全 NaN | `eps` 字段永远全 NaN | `decouple` line 226-235：`det = Ke1*(Ke2*0.95) - (Ke1*0.95)*Ke2 = 0` |
| **根因 B** | `dT_corr` + `T_base` 若 S1≈S2 则 det≈0 → `T_abs` 全 NaN | `T_abs` 字段可能全 NaN | `decouple` line 226-235：同一 `abs(det) < 1e-9` 判定 |
| **根因 C** | 粒度错配 — T_abs/eps_orig 是 ~98450 点逐行监测序列，非 ~10-100 点台阶聚合 | 即使值有效，迟滞回线也不该用逐行原始序列 | PhaseBWorker line 795-809：不聚合平台，直接解耦全 df |

**附加发现**：`from_providers` 优先读 `eps_orig`（永远 NaN），降级 `eps_corr`（可能有效但粒度错配）。优先级的实际效果是反过来——永远取到了 NaN 的那一列。正确的优先级应该是 `eps_corr`（有 S_eff 的修正解耦）优先于 `eps_orig`（标定KT估算，且当前实现 det=0 永 NaN）。

---

## 附录：关键文件索引

| 文件 | 关键符号 | 行号 |
|------|---------|------|
| `ui/calibration_tab.py` | `PhaseBWorker.run()` — 算 `eps_orig`/`eps_corr`/`T_abs` | 752-809 |
| `ui/calibration_tab.py` | `eps_orig` 调用 — det=0 恒为真 | 799 |
| `ui/calibration_tab.py` | `eps_corr` 调用 — det 可能为 0 | 801 |
| `dp_engine/calibration/temperature_calibration.py` | `decouple()` — `abs(det) < 1e-9` → 全 NaN | 203-239 |
| `core/chart_bundle.py` | `from_providers` — 读 `eps_orig` 优先（永远 NaN） | 183-184 |
| `core/chart_bundle.py` | `gen_charts_from_bundle` — hyst 循环 valid 判定 | 402-412 |
| `ui/calibration_tab.py` | `_write_state_to_main_page` — sensors 落盘 | 2130-2139 |
| `ui/calibration_tab.py` | `PhaseBWorker.__init__` — `self.df = df`（监测数据） | 733-738 |
