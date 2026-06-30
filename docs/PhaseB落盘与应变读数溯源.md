# PhaseB 落盘与应变读数溯源

**日期**: 2026-06-28  
**分支**: llama-cpp  
**状态**: 只记现状 + 根因 + 证据，不给方案

---

## 第一部分 — PhaseB 结果落盘

### 1. PhaseB "确定" 时的落盘代码

`PhaseBDialog._write_state_to_main_page()`（`ui/calibration_tab.py:2054-2138`）

落盘字段（`line 2129-2138`）：

```python
tp._phase_b_state = {
    "ke_table": ke_table,                    # ← Ke 系数表
    "decoupling_results": decoupling,        # ← 解耦标量 (e_mean, e_std, e_range, rating)
    "compensation": compensation,            # ← 补偿模型 + metrics + grade
    "grade_thresholds": gt.to_dict(),
    "comp_form": comp_form,
    "poly_order": poly_order,
    "subsample_step": subsample_step,
}
```

**触发条件**：仅 `accept()` 调用（`line 2140-2142`）：
```python
def accept(self):
    self._write_state_to_main_page()
    super().accept()
```

**`reject()` 不写回**（`line 2144-2146`）：
```python
def reject(self):
    # reject 不写回 — 只有 accept(确定) 才持久化状态
    super().reject()
```

**`closeEvent` 无覆盖** → Esc / X / Alt+F4 → QDialog 默认 reject → 不写回。

### 2. `_write_state_to_main_page` 落盘的字段 vs `from_providers` 读取的字段

| 数据用途 | `from_providers` 读的字段 | `_phase_b_state` 写的字段 | 读得到？ | 代码行 |
|---------|--------------------------|--------------------------|---------|--------|
| 分级表 (G1) | `pb.get('compensation')` | `"compensation"` | ✅ 字段名一致 | chart_bundle.py:128 |
| 解耦表 (T5) | `pb.get('decoupling_results')` | `"decoupling_results"` | ✅ 字段名一致 | chart_bundle.py:161 |
| Ke 汇总 (S2) | `sp._strain_configs` | `"ke_table"` (不同源!) | ✅ 从应变页读，与 PhaseB 无关 | chart_bundle.py:178 |
| 迟滞回线 | `pb.get('sensors')` | **未写** | ❌ **`_phase_b_state` 从不含 `sensors`** | chart_bundle.py:184-196 |

**关键发现**：`_write_state_to_main_page` 从不写 `sensors`（含 `T_abs`/`eps_orig` 原始时序）。这意味着即使 PhaseB 点击"确定"，`from_providers` 的 `hyst_sensors` 仍为空。

### 3. 为什么这次 compensation_n=0 / decoupling_n=0 / hyst_sensors=0

**三层原因（同时生效）：**

| 层 | 原因 | 证据 | 影响 |
|----|------|------|------|
| **A** | PhaseBDialog 关闭时未点"确定"（Esc/X）→ `reject()` → `_write_state_to_main_page` 未执行 | `calibration_tab.py:2144-2146` 不写回；`closeEvent` 无覆盖 | `compensation_n=0`, `decoupling_n=0` |
| **B** | 即使点了"确定"，`_write_state_to_main_page` 也不写 `sensors` 字段（含 `T_abs`/`eps_orig`） | `calibration_tab.py:2129-2138` 的 dict 无尽 `sensors` key | `hyst_sensors=0`（永远） |
| **C** | "应用标定系数到当前传感器"路径（`calibration_tab.py:4302-4306`）只写 `ke_table` 到 `_phase_b_state` | `line 4303-4306` 仅 `pb_state["ke_table"] = ...` | `ke=True` 但其他全空 |

**为什么 ke_table 有值**：`_phase_b_state["ke_table"]` 来自另一个入口——应变标定页的"应用标定系数"（`line 4302-4306`），它读旧的 `_phase_b_state`、追加 `ke_table`、写回。所以 `ke=True` 但 `compensation`/`decoupling` 为空。

**对比上次成功（compensation_n=6）与这次（=0）**：

上次 PhaseB "确定"被点击 → `_write_state_to_main_page` 执行 → `compensation` + `decoupling` 写入 `_phase_b_state`。这次用户可能：
- 分析后在对话框内看到结果（满意）→ 点了 X 关闭（肌肉记忆）
- 或者先关闭了对话框（reject），后来重新打开时结果已丢失，但 `ke_table` 已由应变页的"应用系数"旁路写入

### 4. 为什么 `hyst_sensors` 读到 0

**两层原因：**

| 层 | 原因 | 代码 |
|----|------|------|
| 1 | `_phase_b_state` 从不含 `sensors` 字段（`_write_state_to_main_page` 不写） | `calibration_tab.py:2129-2138` — 写的8个key中无 `sensors` |
| 2 | `from_providers` 的 `hyst_sensors` 填充代码（本批新增）读 `pb.get('sensors')` — 永远空 | `chart_bundle.py:184-196` |

**注意**：本批（阶段 1）新增的 `hyst_sensors` 填充从 `from_providers` 读 `_phase_b_state['sensors']` 时，`sensors` 从未被任何写入路径覆盖。这是漏写，不是读错。原始时序数据（`T_abs`/`eps_orig`）仅存在于 PhaseBWorker 结果的 `result["sensors"][name].{T_abs, eps_orig}`（`calibration_tab.py:804-809`），在 `_on_done` 时保存到 `self._last_result["sensors"]`（`line 1897`），但 `_write_state_to_main_page` 只从中提取标量进 `decoupling`，丢弃了原始序列。

---

## 第二部分 — 应变读数保存

### 5. "无读数数据" 的判定链

**保存按钮入口**：`StrainCalibrationPage._save_readings()`（`calibration_tab.py:4062-4087`）

```python
def _save_readings(self):
    profile = capture_from_page(self)         # line 4066
    if not profile.readings:                   # line 4067 ← 判空
        QMessageBox.warning(self, "无读数数据", ...)
        return
```

**`capture_from_page`**（`readings_profile.py:219-260`）：

```python
raw = getattr(sp, '_readings', None) or {}   # line 230 ← 读 self._readings
# 遍历 raw → readings_copy
```

**`self._readings` 的数据流**：

```
用户打开读数录入对话框 → _open_readings()
  → 填波长数据 → 关闭对话框 → _on_closed() (line 3577)
    → self._readings = self._extract_table_data(table)  ← 写入

用户切换传感器 → _load_sensor_to_workspace() (line 3900)
  → self._readings = {}  ← 清空! (line 3925)

用户点"▶ 提交并分析" → _run_analysis() → _get_table_data()
  → self._readings  ← 读 (此时还有值)

用户点"💾 保存读数" → _save_readings() → capture_from_page()
  → self._readings  ← 已空!
```

**根因**：`_load_sensor_to_workspace`（`calibration_tab.py:3925`）强制清空 `self._readings = {}`，不从中恢复。而 `_strain_configs[sensor_name].readings` 存储的是 `[{disp_mm, eps_theory}, ...]`（`_build_strain_subconfig` line 3240-3244 从 `self._levels` 构造，不含原始波长），不是 `self._readings` 的 `{g_idx: {cycle: {load: [...], unload: [...]}}}` 格式。两个字段是不同格式、不同用途——`_strain_configs[].readings` 是位移+理论应变列表供标定分析用，`self._readings` 是原始波长供保存/加载用。`_load_sensor_to_workspace` 不恢复原始波长 → `_save_readings` 读到空。

### 5b. 本会话是否连带影响

```diff
# git diff HEAD -- ui/calibration_tab.py | grep _readings
# (无匹配 — 本会话未改动 _readings 相关代码)
```

`_save_readings` 和 `_load_sensor_to_workspace` 均未在本会话改动。**预存问题**，来自 `7fad573`（传感器标定初始提交）。

---

## 根因汇总

| # | 现象 | 直接原因 | 根因分类 | 证据 |
|---|------|---------|---------|------|
| 1 | `compensation_n=0` | `_phase_b_state.compensation` 未被写入 | PhaseB reject 不写回 | `calibration_tab.py:2144-2146` |
| 2 | `decoupling_n=0` | `_phase_b_state.decoupling_results` 未被写入 | PhaseB reject 不写回 | `calibration_tab.py:2144-2146` |
| 3 | `hyst_sensors=0` | `_phase_b_state` 从不含 `sensors` 字段 | `_write_state_to_main_page` 漏写 | `calibration_tab.py:2129-2138` 无 sensors key |
| 4 | `ke=True` 但其他全空 | 应变页"应用系数"旁路只写 ke_table | 设计：跨页数据流不完整 | `calibration_tab.py:4302-4306` |
| 5 | "无读数数据" | `_readings` 被 `_load_sensor_to_workspace` 清空后未恢复 | 状态恢复不完整 | `calibration_tab.py:3925` |

**核心矛盾**：PhaseB 对话框的"确定"是唯一落盘路径，但用户可能因习惯/无提示而 Esc/X 关闭。同时 `_phase_b_state` 的落盘字段不全（缺 `sensors`），即使点了"确定"，迟滞回线数据也丢失。应变读数侧同理——`_readings` 在传感器切换时被清空且不恢复。

---

## 附录：关键文件索引

| 文件 | 关键符号 | 行号 |
|------|---------|------|
| `ui/calibration_tab.py` | `PhaseBDialog._write_state_to_main_page()` — 落盘代码 | 2054-2138 |
| `ui/calibration_tab.py` | `PhaseBDialog.accept()` | 2140-2142 |
| `ui/calibration_tab.py` | `PhaseBDialog.reject()` — 不写回 | 2144-2146 |
| `ui/calibration_tab.py` | `PhaseBWorker.run()` — sensors 含 T_abs/eps_orig | 752-851 |
| `ui/calibration_tab.py` | `PhaseBDialog._on_done()` — `self._compensation_results` 赋值 | 1896-1935 |
| `ui/calibration_tab.py` | 应变页"应用系数" — 只写 ke_table 到 `_phase_b_state` | 4302-4306 |
| `core/chart_bundle.py` | `from_providers` — 读 `compensation`/`decoupling_results`/`sensors`/`strain_configs` | 126-243 |
| `core/chart_bundle.py` | `from_providers` — `hyst_sensors` 读 `pb.get('sensors')`（本批新增） | 184-196 |
| `ui/calibration_tab.py` | `StrainCalibrationPage._save_readings()` — 保存入口 | 4062-4087 |
| `ui/calibration_tab.py` | `StrainCalibrationPage._load_sensor_to_workspace()` — 清空 `_readings` | 3900-3925 |
| `ui/calibration_tab.py` | `StrainCalibrationPage._build_strain_subconfig()` — readings 字段 ≠ `_readings` | 3234-3254 |
| `dp_engine/calibration/readings_profile.py` | `capture_from_page()` — 读 `sp._readings` | 219-260 |
| `ui/calibration_tab.py` | `_open_phase_b()` — 打开 PhaseB 对话框 | 2957-2963 |
| `ui/calibration_tab.py` | `TemperatureCalibrationPage._phase_b_state` — 初始化空 | 2638 |
| `ui/calibration_tab.py` | `TemperatureCalibrationPage._phase_b_state` — 新文件清零 | 2730 |
