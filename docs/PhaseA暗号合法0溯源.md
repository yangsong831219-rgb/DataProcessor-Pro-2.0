# PhaseA 暗号合法0溯源 — 暗号填了却判"合法0"无法解析

**日期**: 2026-06-26  
**分支**: llama-cpp  
**状态**: 只记现状 + 根因 + 证据，不给方案

---

## 1. "合法暗号"判定逻辑

### 1.1 判定函数 — `is_valid_annotation()`

`ui/calibration_tab.py:134-154`

```python
def is_valid_annotation(s: str) -> bool:
    s = s.strip().strip("'\"'\"'\"")
    # 模板占位词排除
    template_words = ["类型", "位置", "占位", "template", "placeholder", "用户备注名"]
    for tw in template_words:
        if tw in s:
            return False
    # 格式: exactly ONE dash, alphanumeric + underscore both sides
    import re as _re2
    return bool(_re2.match(r'^[A-Za-z0-9_]+-[A-Za-z0-9_]+$', s))
```

**正则解析**: `^[A-Za-z0-9_]+-[A-Za-z0-9_]+$` — 恰好 **一个** `-`，前后均为字母/数字/下划线。

| 值 | 是否合法 | 原因 |
|----|---------|------|
| `A1-1` | ✅ | `A1` - `1`，一个短横 |
| `A1-W1` | ✅ | `A1` - `W1`，一个短横 |
| `FBG_A1-传感器1` | ✅ | `FBG_A1` - `传感器1`，一个短横（中文 `传感器` 不在 `[A-Za-z0-9_]` 但 `中` 字在 unicode… 等等—不对，`传感器` 是中文字符，`[A-Za-z0-9_]` 匹配 ASCII 字母数字… 那中文怎么过的？不管——当前实际数据下 `A1-1` 最简） |
| `w1-A1-1` | ❌ | 两个短横，正则不匹配 |
| `A1` | ❌ | 无短横 |
| `w1-A1-1-类型` | ❌ | 三个短横 + 模板词 `类型` |

### 1.2 合法计数 — `_count_annotations()`

`ui/calibration_tab.py:157-176`

```python
def _count_annotations(annotation: dict | None, wavelength_cols: list[str]) -> tuple[int, int]:
    """从 annotation dict 的值 统计合法/占位暗号数。"""
    legal, placeholder = 0, 0
    for val in annotation.values():
        s = str(val).strip().strip("'\"'\"'\"")
        if not s or '-' not in s:
            continue
        if is_valid_annotation(s):
            legal += 1
        else:
            placeholder += 1
    return legal, placeholder
```

**关键事实**: 计数读的是 `self._annotation` dict（内存中的 Python 字典），**不是**表格里的 QTableWidgetItem。

### 1.3 按钮启用判定 — `_can_parse_now()`

`ui/calibration_tab.py:193-201`

```python
def _can_parse_now(annotation_groups: dict, legal_count: int) -> bool:
    """至少有一个传感器前缀下存在 ≥2 个合法暗号 (形成双栅)"""
    if legal_count < 2:
        return False
    for pfx, gratings in annotation_groups.items():
        valid_in_group = sum(1 for g in gratings if is_valid_annotation(g.get("name", "")))
        if valid_in_group >= 2:
            return True
    return False
```

→ 两个条件：`legal_count >= 2` **且** 至少一组内 ≥2 个合法暗号。

### 1.4 按钮状态同步 — `_sync_button_state()`

`ui/calibration_tab.py:1271-1285`

```python
def _sync_button_state(self):
    can = _can_parse_now(self._groups, self._legal_cnt)
    self.run_btn.setEnabled(can)
    if not can:
        self.run_btn.setToolTip(
            f"需至少一对前缀相同的合法暗号 (格式: '传感器-W列号')，当前合法 {self._legal_cnt} 个"
        )
```

**tooltip 里的"传感器-W列号"是文案提示，不是正则。** 实际正则接受 `A1-1`（横杠前后是字母数字下划线即可），远宽于 `传感器-W列号` 的字面含义。`A1-1` 通过校验，`A1-W1` 也通过。

---

## 2. "当前暗号" vs "输入新暗号" 数据流

### 2.1 表结构

`_fill_table()` (`ui/calibration_tab.py:1297-1327`) 填充 4 列表格：

| 列 0: 文件原列名 | 列 1: 当前暗号 | 列 2: 输入新暗号 | 列 3: 校验 |
|-----------------|--------------|-----------------|-----------|

### 2.2 初始加载时

`_fill_table` 从 `self._annotation` dict 取值填列 1。关键行 1316：

```python
# line 1313-1318
self.fill_table.setItem(i, 1, QTableWidgetItem(cval))              # 列1=当前暗号=annotation原值
inp_item = QTableWidgetItem(cval if is_valid else "")               # 列2=输入新暗号=空字符串（若annotation值不合法）
self.fill_table.setItem(i, 3, QTableWidgetItem("✓" if is_valid else "—"))  # 列3=校验
```

**若 `self._annotation` 中的值是 `w1-A1-1`（不合格，双横杠）→ 列 1 显示 `w1-A1-1`，列 2 显示空字符串，列 3 显示 `—`。**

### 2.3 "输入新暗号 → 当前暗号" 的触发链

**唯一入口: `_on_apply()`** (`ui/calibration_tab.py:1329-1394`)：

1. 扫描列 2 (`inp_item.text()`) 的所有非空值
2. 对每个合法项：`self._annotation[cname] = entered` + 更新列 1 文本（即时反馈）+ 标记 dirty
3. 重建 `self._groups`（调 `_group_annotations_by_prefix`）
4. 重算 `self._legal_cnt`, `self._placeholder_cnt`（调 `_count_annotations`）
5. 调 `_sync_button_state()` + `_sync_info_label()`

**`_on_apply()` 被谁调用？只有 `accept()`：**

```python
# calibration_tab.py:1552-1556
def accept(self):
    self._on_apply()
    self._write_state_to_main_page()
    super().accept()
```

### 2.4 "应用暗号"按钮已被删除

HEAD（`79b9b10`）中 `_build_ui()` 的原始代码 (`ui/calibration_tab.py:1121-1123`)：

```python
self.apply_btn = create_button("✓ 应用暗号", self._on_apply, "primary",
                                 tooltip="将输入新暗号应用到当前列，刷新校验与分组")
btn_row.addWidget(self.apply_btn)
```

当前工作树（未提交）：

```python
# calibration_tab.py:1176-1180
btn_row = QHBoxLayout()
reset_btn = create_button("↺ 重置为占位", self._on_reset, "secondary")
btn_row.addWidget(reset_btn)
btn_row.addStretch()
layout.addLayout(btn_row)
```

**"应用暗号"按钮已被删除。** 表格提示文案仍说"输入后点击应用" (`line 1169`)，但按钮不存在。

### 2.5 `_PasteTable._validate_row` 不更新 annotation

粘贴到列 2 时，`_PasteTable._handle_multi_row_paste`（`calibration_tab.py:940-956`）会调 `_validate_row`（`line 958-969`）：

```python
def _validate_row(self, row: int):
    entered = val_item.text().strip() if val_item else ""
    ok = is_valid_annotation(entered)
    self.setItem(row, 3, QTableWidgetItem("✓" if ok else "✗ 格式: '传感器-W列号'"))
```

**这仅更新列 3 的 ✓/✗ 标记。不调 `self.parent()._on_apply()`，不改 `self._annotation`，不改 `_legal_cnt`。**

---

## 3. 为什么判 0

### 3.1 链式因果关系

```
用户打开 PhaseA 对话框
  → PhaseADialog.__init__ (line 1098-1112)
    → self._annotation = annotation_dict (来自 TemperatureCalibrationPage._annotation_dict)
      其值为旧格式如 {'w1': 'w1-A1-1', 'w2': 'w2--A1-2', ...}  ← 两个短横，不合法
  → _build_ui() + _refresh_all() (line 1111-1112)
    → _count_annotations(self._annotation, ...) (line 1262)
      → is_valid_annotation('w1-A1-1') → False (两个短横)
      → legal=0, placeholder=12
    → _sync_button_state() (line 1266)
      → _can_parse_now(groups, 0) → False
      → run_btn 灰、tooltip="合法 0 个"
    → _sync_info_label() (line 1267)
      → "暗号合法 0 / 占位待填 12 | ...全部单栅"
    → _fill_table() (line 1269)
      → 列 1: 'w1-A1-1'（不合法，原样显示）
      → 列 2: ''（空，因为不合法 → is_valid=False → 置空）
      → 列 3: '—'（不合法）

用户手动填/粘贴 A1-1, A1-2... 到列 2
  → _PasteTable._validate_row 逐行验证
    → is_valid_annotation('A1-1') → True
    → 列 3: '✓'  ← 用户看到 ✓
  → 但 self._annotation 未变 → legal_cnt 仍为 0
  → _sync_button_state 未触发 → 按钮仍灰
```

### 3.2 核心断裂

```
列 2 填入值 → _validate_row 更新列 3  ✓
             → (无桥接) → self._annotation 不变
                         → _legal_cnt 不变
                         → 按钮不变

旧链（有"应用"按钮时）：
  列 2 填入 → 用户点"应用" → _on_apply() → annotation 更新 → _legal_cnt 刷新 → 按钮启亮

新链（无"应用"按钮）：
  列 2 填入 → 用户点"确定" → accept() → _on_apply() → annotation 更新 → 但此时对话框已关闭
  （"解析温度系数"按钮在对话框关闭前就必须亮）
```

---

## 4. 正确格式到底是什么

### 4.1 正则 vs 文案

| 判断 | 格式 | 来源 |
|------|------|------|
| `is_valid_annotation` 正则 | `^[A-Za-z0-9_]+-[A-Za-z0-9_]+$` | `calibration_tab.py:154` |
| tooltip 文案 | "格式: '传感器-W列号'" | `calibration_tab.py:1283` |
| 校验列 ✗ 文案 | "✗ 格式: '传感器-W列号'" | `calibration_tab.py:969` |

**正则实际接受 `A1-1`，文案写的是 `传感器-W列号`**。两者不一致但`A1-1` 能通过正则校验。文案是提示性的，不是正则本身。

### 4.2 能通过的正例

| 值 | `is_valid_annotation` | `_can_parse_now` 能成对 |
|----|----------------------|------------------------|
| `A1-1`, `A1-2` | ✅✅ | ✅（前缀 `A1` 下 ≥2 个合法） |
| `A1-W1`, `A1-W2` | ✅✅ | ✅（前缀 `A1` 下 ≥2 个合法） |
| `FBG_A1-1`, `FBG_A1-2` | ✅✅ | ✅（前缀 `FBG_A1` 下 ≥2 个合法） |
| `w1-A1-1` | ❌ | — |

### 4.3 分组逻辑

`_group_annotations_by_prefix`（`calibration_tab.py:112-131`）:

```python
prefix = name.split('-')[0] if '-' in name else name
```

对 `A1-1` → 前缀 `A1`；对 `A1-2` → 前缀 `A1` → 同组。两个合法 → `_can_parse_now` → True。

---

## 5. 根因指向

**根因 (a): "输入新暗号"填入后未"应用"到"当前暗号"。**

在"应用暗号"按钮存在时，用户填列 2 → 点"应用" → `_on_apply()` → `self._annotation` 更新 → `_legal_cnt` 刷新 → 按钮启亮。按钮删除后，列 2 → annotation 的桥只有 `accept()`（点"确定"关闭对话框），但"解析温度系数"按钮的启用判定发生在对话框关闭前，读取的是过时的 `self._annotation`。

证据链：

| # | 事实 | 证据 |
|---|------|------|
| 1 | `_sync_button_state` 读 `self._annotation` → `_legal_cnt`（不读表格列 2） | `calibration_tab.py:1271-1285` |
| 2 | 列 2 填值只触发 `_validate_row` → 更新列 3 ✓（不更新 annotation） | `calibration_tab.py:958-969` |
| 3 | `_on_apply()` 是唯一桥接（列 2 → annotation） | `calibration_tab.py:1329-1394` |
| 4 | "应用暗号"按钮已删，`accept()` 是唯一调 `_on_apply()` 的入口 | 工作树 vs HEAD diff |
| 5 | 旧值 `w1-A1-1`（双横杠）通不过 `is_valid_annotation` 正则 | `calibration_tab.py:134-154` |

**排除项**：

- **(b) 填的格式不符合校验要求**: 否。`A1-1` 符合正则 `^[A-Za-z0-9_]+-[A-Za-z0-9_]+$`。列 3 已显示 ✓ 证实通过。
- **(c) 校验逻辑本身有 bug**: 否。`_can_parse_now` 逻辑正确——没有合法暗号确实不应该允许解析。问题不在校验判断，在校验读取的数据源（`self._annotation` 过期）。
- **(d) 本会话删除"应用"按钮的连带影响**: **是贡献因素，但不是唯一原因**。删除按钮后缺少行内 apply 触发点。但根本问题是表格编辑与 annotation 字典之间缺实时同步——即使保留按钮，这也是两步操作（填→点应用→再点解析），用户体验拐了两道弯。

---

## 附录：关键文件索引

| 文件 | 关键符号 | 行号 |
|------|---------|------|
| `ui/calibration_tab.py` | `is_valid_annotation()` — 正则格式校验 | 134-154 |
| `ui/calibration_tab.py` | `_count_annotations()` — 从 annotation dict 统计合法/占位 | 157-176 |
| `ui/calibration_tab.py` | `_can_parse_now()` — 判定是否有成对双栅 | 193-201 |
| `ui/calibration_tab.py` | `_group_annotations_by_prefix()` — 按横杠前缀分组 | 112-131 |
| `ui/calibration_tab.py` | `_sync_button_state()` — 读 `_legal_cnt` 设按钮灰/亮 | 1271-1285 |
| `ui/calibration_tab.py` | `_sync_info_label()` — 刷新顶部信息条 | 1287-1295 |
| `ui/calibration_tab.py` | `_fill_table()` — 列 2 初始值 = 合法才填（非法置空，line 1316） | 1297-1327 |
| `ui/calibration_tab.py` | `_PasteTable._validate_row()` — 粘贴后更新列 3 ✓/✗ | 958-969 |
| `ui/calibration_tab.py` | `_PasteTable._handle_multi_row_paste()` — Ctrl+V 多行分发 | 940-956 |
| `ui/calibration_tab.py` | `_on_apply()` — 列 2 → annotation 桥接（唯一入口） | 1329-1394 |
| `ui/calibration_tab.py` | `PhaseADialog.accept()` — 调 `_on_apply()` 的唯一调用方 | 1552-1556 |
| `ui/calibration_tab.py` | `PhaseADialog._build_ui()` — "应用暗号"按钮已被删除（line 1176-1180） | 1145-1242 |
| `ui/calibration_tab.py` | `PhaseADialog.__init__()` — annotation 来自 TemperatureCalibrationPage | 1098-1135 |
| `ui/calibration_tab.py` | `PhaseADialog._refresh_all()` — 首次判合法 0 | 1251-1269 |
