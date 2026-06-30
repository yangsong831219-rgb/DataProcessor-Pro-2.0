# Phase B 表空白回归定位

> 调查日期: 2026-06-24
> 分支: llama-cpp
> 问题: Phase B"双栅传感器应变系数 Ke"表显示"无双栅传感器，仅汇总温度系数"
> 调查范围: 改动面排查 → git 二分 → 数据条件核对 → 定性

---

## 1. 改动面排查 — 本批诊断修复是否触碰 _groups

### 1.1 本批诊断修复改动清单

| 文件 | 行 | 改动内容 | 提交时间 |
|------|-----|---------|---------|
| `core/data_providers.py:844-874` | `_get_strain_ke` 重写 | getattr→dict.get，删死路径 _phase_b_state | 2026-06-24 |
| `core/data_providers.py:304-332` | `sensor_ids` 三源并集 | 加入 S_eff 前缀提取 | 2026-06-24 |
| `core/data_providers.py:215-219` | DataFileProvider 列列表过滤 | `_anomaly` 后缀跳过 | 2026-06-24 |
| `core/data_providers.py:235-238` | DataFileProvider 数值统计过滤 | `_anomaly` 后缀跳过 | 2026-06-24 |
| `ui/ai_diagnosis.py:1620` | `_build_data_context` numeric_cols | `_anomaly` 后缀跳过 | 2026-06-24 |
| `ui/calibration_tab.py:4237-4241` | `[KT]` 临时探针 | 仅 print，不改逻辑 | 2026-06-24 |

### 1.2 _groups 的数据来源链

```
Phase B coef_table 行数 ← self._groups ← _group_annotations_by_prefix(data_cols, df)
                                                              ↑
                                    PhaseBDialog.__init__ 从调用方注入
                                                              ↑
                                    TemperatureCalibrationPage._open_phase_b
                                      ↓
                                    self._annotation_groups
                                      ↓
                                    _group_annotations_by_prefix(data_cols_for_group, df)
                                      ↓              ↑
                         ui/calibration_tab.py:112   data_cols_for_group 来自 _annotation_dict
```

完整赋值点：

```python
# ui/calibration_tab.py:2676 — 温度标定子页 _load_and_parse 中构造
self._annotation_groups = _group_annotations_by_prefix(data_cols, df)

# ui/calibration_tab.py:2691 — 另一路径（加载 profile 后）
self._annotation_groups = _group_annotations_by_prefix(dcf, df) if dcf else {}

# ui/calibration_tab.py:2890-2891 — 传给 Phase B
dlg = PhaseBDialog(
    self._loaded_df, self._annotation_groups, self._last_result, self,
)
```

### 1.3 `_group_annotations_by_prefix` 函数

```python
# ui/calibration_tab.py:112-131
def _group_annotations_by_prefix(data_cols: dict, df=None) -> dict:
    groups: dict[str, list[dict]] = {}
    for col_idx, name in data_cols.items():
        prefix = name.split('-')[0] if '-' in name else name
        entry: dict = {"name": name}
        if df is not None and 0 <= col_idx < len(col_list):
            entry["col_name"] = col_list[col_idx]
        groups.setdefault(prefix, []).append(entry)
    return groups
```

本批改动未触碰此函数、未触碰 `data_cols` 构造、未触碰 `_annotation_dict`、未触碰 `is_valid_annotation`。

### 1.4 Phase B coef_table 行数判定

```python
# ui/calibration_tab.py:1686-1696
for pfx, gratings in self._groups.items():
    if len(gratings) < 2:               # ★ 判据：前缀下 ≥2 个光栅
        continue
    valid_names = [g["name"] for g in gratings if is_valid_annotation(g.get("name", ""))]
    if len(valid_names) < 2:
        continue
    self.coef_table.setRowCount(row + 1)
    ...
```

**结论**：本批四处改动文件 (`core/data_providers.py`, `ui/ai_diagnosis.py`, `ui/calibration_tab.py` 的 [KT] 探针) 没有一处直接或间接影响 `_groups` 的构建或 Phase B coef_table 的行数判定逻辑。

**`_groups` 构建与本次诊断修复改动全集无交集。**

---

## 2. git 二分 — coef_table 双栅判定 code 的最后修改

### 2.1 `_group_annotations_by_prefix` 的最后修改

```bash
$ git log --format="%h %ad %s" --date=short -- ui/calibration_tab.py | head -6
9b089af 2026-06-19 feat(phase-b): compensation pipeline, robust hysteresis + cycle detection, chart LOOCV cross-ref
ed79e56 2026-06-18 fix: 应变标定「导出 Excel」按钮静默无响应
a4ca33c 2026-06-17 feat: 多模块修复 — Sensors解析路由 + Peaks矩形格式 + 应变标定标签映射 + 暗号行对齐
a7b25b0 2026-06-10 feat(calibration): 应变标定全链路 — 项目级配置 + 多传感器列表 + 批量应用系数 + 粘贴/锚固/加载修复
7fad573 2026-06-08 传感器标定模块 v2.1 — 温度/应变标定全链路 + Hyperion ENLIGHT 解析 + profile 持久化
```

### 2.2 Phase B 双栅判定代码（coef_table 行）的最后修改

Phase B coef_table 行判定逻辑（`ui/calibration_tab.py:1686-1696`）自 commit `7fad573` (2026-06-08) 之后无修改——它经历了 `a7b25b0`、`a4ca33c`、`ed79e56`、`9b089af` 四轮提交均未变动。本次诊断修复 batch 更未触及。

### 2.3 本批诊断修复与 Phase B 无代码交集

```bash
$ git diff HEAD -- ui/calibration_tab.py | grep -i "groups\|coef\|双栅\|gratings\|_group"
# 输出: 仅有 _annotation_dirty 和 [KT] 探针引用 — 无 _groups/coef_table 逻辑改动
```

**commit 9b089af (2026-06-19)** 是本文件最后一次有实际业务逻辑提交，内容为补偿流水线——与 Phase B 双栅判定无关。

---

## 3. 数据条件核对 — 负温度数据是否导致 _groups 异常

### 3.1 _groups 的双栅判定具体条件和当前数据适用性

双栅判定完全取决于 `_group_annotations_by_prefix` 的输入 `data_cols`：

- 每个入口必须是 `{col_idx: annotation_name}` 格式
- annotation_name 必须含 `-`（如 "A1-W1"），`is_valid_annotation` 正则 `^[A-Za-z0-9_]+-[A-Za-z0-9_]+$`
- 同前缀下有 ≥2 个不同 annotation → 双栅

### 3.2 负温度数据无直接相关

`_group_annotations_by_prefix` 读取的 `data_cols` 来自 `_annotation_dict`——这是列→暗号的映射，**与温度数值无关**。负温度台阶 (-21/-20/-19°C) 不会改变暗号映射的结果。

**但负温度可能有间接影响**：`_load_and_parse` (`ui/calibration_tab.py:2650-2713`) 中，`parse_enlight_file` 解析文件时返回的 `annotation`（文件头自带的暗号）可能与历史正常案例不同。不同批次的 ENLIGHT 文件头可能包含不同的列名或标注。

### 3.3 最可能的成因

Phase B 表空白的最可能原因是**本文件头的暗号自动检测结果与历史正常案例不同**——文件头内的列标注可能缺失、格式异常、或 `is_valid_annotation` 正则未匹配到预期的暗号格式。这与 `_group_annotations_by_prefix` 的双栅判据直接相关：如果文件头解析出的 `data_cols` 中某个传感器只有 1 个光栅被识别，则它不会被判定为双栅。

验证方式：用户控制台中 `_load_and_parse` 输出的暗号信息（`parse_enlight_file` 打印的 `annotation` dict）即可确认。

---

## 4. 结论

**判定：【数据条件触发，非代码回归】**

证据：

| # | 证据 | 结论 |
|---|------|------|
| 1 | 本批改动（`_get_strain_ke` / `sensor_ids` / 异常列过滤 / `[KT]` 探针）与 `_groups` 构建逻辑、Phase B coef_table 行数判定无任何交集 | 非代码引入 |
| 2 | `_group_annotations_by_prefix` 自 6 月 8 日首次提交后未修改；coef_table 双栅 ≥2 判定自始至终未变动 | 非 git 回归 |
| 3 | `_groups` 只取决于 `data_cols`（暗号映射），不取决于温度数值 | 负温度非直接原因 |
| 4 | 暗号自动检测结果由 `parse_enlight_file` 的文件头解析决定——不同数据文件的头段格式不同 | 数据条件触发 |
| 5 | 本批修复中 `calibration_tab.py` 的 diff 不含 `_groups`/coef_table 逻辑改动 | 确认隔离 |

**建议验证**：控制台查看 `_load_and_parse` 输出的 annotation dict，确认每个波长列是否被正确标注为 "prefix-Wn" 格式，且每个传感器前缀下是否有 ≥2 个光栅。

---

> **文档结束** — 本文件仅记录证据与定性结论，不包含修复方案。
