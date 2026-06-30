# ke_table 写入读取对账 — _apply_coefficients ↔ Phase B ↔ CalibrationProvider

> 调查日期: 2026-06-24
> 分支: llama-cpp
> 调查范围: ke_table 写入结构 / Phase B 双栅判定 / get_summary 读取 / 三端对账

---

## 1. [KT] 临时探针位置

探针加在 `_apply_coefficients` 写完 `tp._phase_b_state["ke_table"][s_name]` 之后：

```python
# ui/calibration_tab.py:4237-4241 — 临时探针
print(f"[KT] _apply_coefficients wrote ke_table: {list(_kt.keys())} sensors")
for _sn, _kd in _kt.items():
    print(f"[KT]   {_sn}: keys={list(_kd.keys()) if isinstance(_kd, dict) else type(_kd)}, "
          f"val={_kd}")
```

用户点按钮后控制台会输出 ke_table 的完整内容，确认每个传感器写入的键和值。

---

## 2. Phase B 双栅判定 — 不读 ke_table 的类型字段

### 2.1 判据：_annotation_groups（温度暗号分组），不是 ke_table

Phase B 对话框的"双栅传感器应变系数 Ke"表，行数完全由 `self._groups` 决定，**与 ke_table 无关**：

```python
# ui/calibration_tab.py:1686-1696
for pfx, gratings in self._groups.items():
    if len(gratings) < 2:               # ★ 判断：该前缀下是否有 ≥2 个光栅
        continue
    valid_names = [g["name"] for g in gratings if is_valid_annotation(g.get("name", ""))]
    if len(valid_names) < 2:
        continue
    self.coef_table.setRowCount(row + 1)
    self.coef_table.setItem(row, 0, QTableWidgetItem(pfx))
    self.coef_table.setItem(row, 1, QTableWidgetItem("1.2"))  # ★ 默认 1.2，需用户改
    self.coef_table.setItem(row, 2, QTableWidgetItem("1.2"))
    row += 1
```

**双栅判定的唯一依据**：`self._groups[prefix]` 的 gratings 列表长度 ≥ 2。

### 2.2 _groups 数据来源

`PhaseBDialog` 构造时由温度标定页注入：

```python
# ui/calibration_tab.py:2890-2891
dlg = PhaseBDialog(
    self._loaded_df, self._annotation_groups, self._last_result, self,
)
```

`_annotation_groups` 由 `_group_annotations_by_prefix()` 从暗号标注中推算：

```python
# ui/calibration_tab.py:2676
self._annotation_groups = _group_annotations_by_prefix(data_cols, df)
```

格式：`{"A1": [{"name": "A1-W1", ...}, {"name": "A1-W2", ...}], "B1": [{"name": "B1-W1", ...}]}`

- A1 有 2 个 grating → 产生 coef_table 中的一行
- B1 有 1 个 grating → 跳过（单栅，不进 coef_table）

### 2.3 ke_table 恢复时机

初始化完成后，如果 `tp._phase_b_state` 存在，从 `ke_table` 回填当前单元格：

```python
# ui/calibration_tab.py:1571-1578
ke_table = _normalize_coeffs(state.get("ke_table", {}))
for i in range(self.coef_table.rowCount()):
    pfx = self.coef_table.item(i, 0).text().strip()
    if pfx in ke_table:
        ke = ke_table[pfx]
        self.coef_table.setItem(i, 1, QTableWidgetItem(f"{float(ke['Ke1']):.2f}"))
        self.coef_table.setItem(i, 2, QTableWidgetItem(f"{float(ke['Ke2']):.2f}"))
self._coeffs = dict(ke_table)
```

**回填逻辑**：在 `_groups` 已确定的行 → 查找 ke_table 同名 key → 如果存在则替换默认值 1.2。

### 2.4 核心断裂

| 来源 | 决定行数 | 恢复数值 |
|------|---------|---------|
| `_groups`（温度暗号） | ✅ 行是否存在 | ❌ 只读 name |
| `ke_table`（按钮/Phase B accept） | ❌ 不参与 | ✅ 数值来源 |

**结论**：ke_table 类型字段从未被使用。Phase B 不靠 ke_table 的字段区分单栅/双栅——它只看 `_groups`。

---

## 3. 结构对账：写 vs Phase B 读 vs 诊断侧读

### 3.1 写入端：_apply_coefficients

```python
# ui/calibration_tab.py:4212-4235
ke = getattr(cfg, 'ke_results', {}) or {}    # {"Ke1": 1.18, "Ke2": 0.95}
ke_dict = {str(k): float(v) for k, v in ke.items()}
pb_state["ke_table"][s_name] = ke_dict       # ke_table["A1"] = {"Ke1": 1.18, "Ke2": 0.95}
```

**产生的结构**：
```
ke_table = {
    "A1": {"Ke1": 1.18, "Ke2": 0.95},    # ← 仅 Ke1/Ke2 两个键
}
```

### 3.2 Phase B 写入端：_write_state_to_main_page

```python
# ui/calibration_tab.py:1992-2000
ke_table = {}
for i in range(self.coef_table.rowCount()):
    pfx = self.coef_table.item(i, 0).text().strip()
    k1 = float(self.coef_table.item(i, 1).text().strip())
    k2 = float(self.coef_table.item(i, 2).text().strip())
    ke_table[pfx] = {"Ke1": k1, "Ke2": k2}
```

**产生的结构（完全相同）**：
```
ke_table = {
    "A1": {"Ke1": 1.18, "Ke2": 0.95},
}
```

### 3.3 诊断侧读取：get_summary（已修复）

```python
# core/data_providers.py:384-400
ke = ke_table.get(s_name, {}) if ke_table else {}
ke1_raw = ke.get('Ke1') if isinstance(ke, dict) else None
ke2_raw = ke.get('Ke2') if isinstance(ke, dict) else None
if isinstance(ke1_raw, (int, float)) and float(ke1_raw) != 0.0:
    ke1_s = f"{float(ke1_raw):.4f}"
    ...
else:
    # 回退 strain_configs
    strain_ke = _get_strain_ke(main_win, s_name) ...
```

### 3.4 诊断侧读取：_get_strain_ke（已修复）

```python
# core/data_providers.py:858-874
ke = getattr(cfg, 'ke_results', None)    # dict: {"Ke1": 1.18, "Ke2": 0.95}
if ke and getattr(cfg, 'sensor_name', '') == sensor:
    k1 = float(ke.get('Ke1', 0))        # ✅ 用 .get() 取 dict
    k2 = float(ke.get('Ke2', 0))
    # Ke1=Ke2=0 → 返回 None（让 ke_table 兜底）
    if k1 == 0.0 and k2 == 0.0:
        return None
    return {"Ke1": k1, "Ke2": k2}
```

### 3.5 结构逐键对账

| 条目 | _apply_coefficients 写入 | Phase B _write_state 写入 | Phase B _normalize_coeffs 读取 | get_summary 读取 | _get_strain_ke 读取 |
|------|--------------------------|--------------------------|-------------------------------|-----------------|---------------------|
| 传感器名 key | `s_name` (str) | `pfx` (str) | `s` (str) | `s_name` (str) | `cfg.sensor_name` |
| "Ke1" 键 | ✅ `float` | ✅ `float` | ✅ `float(ke.get("Ke1"))` | ✅ `ke.get('Ke1')` | ✅ `ke.get('Ke1', 0)` |
| "Ke2" 键 | ✅ `float` | ✅ `float` | ✅ `float(ke.get("Ke2"))` | ✅ `ke.get('Ke2')` | ✅ `ke.get('Ke2', 0)` |
| type/mode 标记 | ❌ 不写 | ❌ 不写 | ❌ 不读 | ❌ 不读 | ❌ 不读 |

**结论**：所有端对 `ke_table` 的结构期望完全一致——`{传感器名: {"Ke1": float, "Ke2": float}}`。没有差缺"type/mode"键的问题，因为所有端都不期望它。

**Phase B 表"无双栅传感器"的原因不在这**：不是因为 ke_table 结构不匹配——而是因为 `_groups`（温度暗号）中该传感器只有 1 个 grating，coef_table 压根就不为它创建行。与 ke_table 内容无关。

---

## 4. 诊断侧与 Phase B 的读值一致性

### 4.1 读 ke_table 的方式对比

| 读取端 | 位置 | 取值方式 | 传感器名来源 |
|--------|------|---------|------------|
| Phase B init | `ui/calibration_tab.py:1572-1578` | `_normalize_coeffs → ke.get("Ke1")` | `coef_table` 行的第 0 列（来自 `_groups`） |
| CalibrationProvider | `core/data_providers.py:384-400` | `ke.get('Ke1')` | `sensor_ids` 集合（ke_table keys） |

**两端的传感器名来源不同**，可能导致命名不一致：
- Phase B：传感器名 = `_groups` 的 prefix（如 "C2"）
- 应变按钮 `_apply_coefficients`：传感器名 = `_strain_configs` 的 key（如 "C2"）
- `get_summary`：传感器名 = `ke_table` 的 key（来自按钮写入）

如果 `_apply_coefficients` 写入 `ke_table["C2"] = {"Ke1": 1.18, "Ke2": 0.95}`，三端的 key 都是 "C2" → 一致。但如果有命名差异（如应变侧 "A1_应变" vs 温度侧 "A1"），就会出现不匹配。

### 4.2 读空的应急处置

Phase B：ke_table 有 key 但 _groups 没有 → 不产生行 → ke_table 值无法回填到 UI（但 `self._coeffs` 中保留）→ 运行时仍可被 `PhaseBWorker` 使用。

get_summary：ke_table 有值 → `sensor_ids` 包含该 key → 进入 per-sensor 循环 → 直接读 ke_table → 输出 Ke1/Ke2。**不依赖 _groups**。与 Phase B 不同——get_summary 不是从 coef_table UI 读，而是直接从 `pb["ke_table"]` dict 读。

---

> **临时探针位置**: `ui/calibration_tab.py:4237-4241` — 搜 `[KT]` 定位。用户点完按钮后从控制台抄打印内容。
>
> **文档结束** — 本文件仅记录现状事实，不包含改进建议或实施方案。
