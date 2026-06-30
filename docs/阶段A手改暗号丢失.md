# 阶段A手改暗号丢失 — 同会话编辑后重开对话框值回退

**日期**: 2026-06-25
**状态**: 只记现状，不给方案
**现象**: 加载项目后，阶段A 对话框手改"输入新暗号"列 → 点「应用暗号」→ 关闭对话框 → 重开阶段A → 手改值丢失，回到改之前。操作不经过 `restore()`（项目加载）。

---

## §1 「应用暗号」写入路径

### 按钮槽函数

`ui/calibration_tab.py:1290-1355` — `PhaseADialog._on_apply()`

```python
# line 1290
def _on_apply(self):
    """应用暗号: 读取 QTableWidgetItem → 更新 annotation → 刷新校验列 → 同步按钮/信息。"""
    ...
    # Step 1: 快照所有 QTableWidgetItem (col 0=列名, col 2=用户输入值)
    for i in range(self.fill_table.rowCount()):
        ...
        entered = inp_item.text().strip()              # line 1304 — 读取「输入新暗号」列
        ...
    # Step 2: 更新 annotation
    for i, cname, entered in snapshots:
        if ok:  # is_valid_annotation(entered)
            self._annotation[cname] = entered           # line 1317 — ★ 写入 dialog 本地 dict
            self._annotation_dirty.add(cname)            # line 1318 — ★ 标记 dirty
            applied += 1
            cur_item = self.fill_table.item(i, 1)
            if cur_item:
                cur_item.setText(entered)                # line 1323 — 刷新「当前暗号」列显示

    # Step 3: 重建 groups
    self._groups = _group_annotations_by_prefix(...)     # line 1344-1345
```

### 写入目标判定

**「应用暗号」写入的是 `self._annotation`（PhaseADialog 本地 dict），不是温度页的 `tp._annotation_dict`。** 此时更改仅存在于对话框实例内部内存，尚未写回温度页。

### 写回温度页的唯一触发点

`ui/calibration_tab.py:1492-1511` — `PhaseADialog._write_state_to_main_page()`

```python
def _write_state_to_main_page(self):
    """写回主 Tab 状态 (仅 accept 调用)"""
    tp = self._get_temp_page()
    if tp:
        tp._annotation_dict = dict(self._annotation)      # line 1496 — ★ 写入温度页
        tp._annotation_groups = dict(self._groups)         # line 1497
        tp._annotation_dirty = set(self._annotation_dirty) # line 1498 — ★ 持久化 dirty
        ...
        tp._phase_a_state = {
            "annotation": dict(self._annotation),           # line 1503 — ★ 写入 phase_a_state
            "annotation_dirty": list(self._annotation_dirty), # line 1504
            "groups": dict(self._groups),
            ...
        }
```

### 四条关闭路径的写回覆盖

| 关闭路径 | 触发方法 | 调用 `_write_state_to_main_page`? | 行号 |
|----------|---------|----------------------------------|------|
| 确定按钮 | `accept()` | ✅ **是** | `calibration_tab.py:1513-1515` |
| 取消按钮 | `reject()` | ❌ **否** | `calibration_tab.py:1517-1519` |
| Esc 键 | Qt default → `reject()` | ❌ **否** | — (无 `closeEvent` 覆写) |
| X 按钮 / Alt+F4 | Qt default → `reject()` | ❌ **否** | — (无 `closeEvent` 覆写) |

```python
# line 1513
def accept(self):
    self._write_state_to_main_page()
    super().accept()

# line 1517
def reject(self):
    # reject 不写回 — 只有 accept(确定) 才持久化状态     # ★ 注释明确声明: reject 不写回
    super().reject()
```

**违反项目纪律**：CLAUDE.md 标定约定第 8 条要求 `accept() + reject()` 双覆写或 `closeEvent` 统一拦截。`PhaseADialog.reject()` 未调用 `_write_state_to_main_page()`。

---

## §2 重开阶段A 的数据来源

### 调用入口

`ui/calibration_tab.py:2866-2870` — `TemperatureCalibrationPage._open_phase_a()`

```python
def _open_phase_a(self):
    dlg = PhaseADialog(
        self._loaded_df, self._annotation_dict, self._annotation_groups,   # ★
        self._detection_params, self._time_col_idx, self,
    )
    if dlg.exec() == QDialog.DialogCode.Accepted:
        result = dlg.get_result()                # 仅取 Phase A 分析结果 (S_eff)
        ...
```

### PhaseADialog.__init__ — 初始值来源

`ui/calibration_tab.py:1052-1067` — 第一段（before merge）

```python
def __init__(self, loaded_df, annotation_dict, annotation_groups, ...):
    self._df = loaded_df
    self._file_annotation = dict(annotation_dict) if annotation_dict else {}  # line 1057
    self._annotation = dict(self._file_annotation)  # line 1058 — ★ 从 tp._annotation_dict 取值
    self._groups = dict(annotation_groups)           # line 1059
    self._annotation_dirty: set[str] = set()         # line 1065 — ★ 初始化为空 set
    self._build_ui()
    self._refresh_all()                               # line 1067 — ★ 首次填充表格
```

### PhaseADialog.__init__ — 状态恢复合并

`ui/calibration_tab.py:1069-1093` — 第二段（merge from `tp._phase_a_state`）

```python
    # ── 状态恢复: 从主 Tab 状态 dict 读取上次关闭时的值 ──
    tp = self._get_temp_page()
    if tp and tp._phase_a_state:                    # line 1072 — ★ _phase_a_state 为空时整个跳过
        state = tp._phase_a_state
        saved_annot = state.get("annotation", {}) or {}
        self._annotation_dirty = set(state.get("annotation_dirty", []) or [])  # line 1075
        # 合并: 以文件暗号为底, 叠加手改锁定 + profile 回退
        for col_name, saved_val in saved_annot.items():
            if col_name in self._annotation_dirty:
                self._annotation[col_name] = saved_val       # line 1080 — dirty 列锁定为 saved 值
            elif col_name not in self._annotation or not self._annotation.get(col_name):
                self._annotation[col_name] = saved_val       # line 1083 — 缺失列回退
            # else: 文件有非空暗号 → 保持文件值                # line 1084 — ★ 文件优先
        if state.get("groups"):
            self._groups.update(state["groups"])
        ...
        self._refresh_all()                                    # line 1093 — ★ 二次重建表格
```

### 重开时的数据同步分析

**若上次关闭走 `accept()`（确定按钮）**：

| 数据源 | 值 | 写入时间 |
|--------|-----|---------|
| `tp._annotation_dict` | 编辑后的值 (如 "B1-1") | `_write_state_to_main_page:1496` |
| `tp._phase_a_state["annotation"]` | 编辑后的值 (如 "B1-1") | `_write_state_to_main_page:1503` |
| `tp._phase_a_state["annotation_dirty"]` | `["Col_A"]` | `_write_state_to_main_page:1504` |

两者同源一次性写入，**理论上一一致**。重开时 `self._annotation = dict(annotation_dict)` 设初始值 = 编辑后值 → 合并循环中 dirty 列 `self._annotation[col_name] = saved_val`（同值 no-op）→ 表格显示正确。

**若上次关闭走 `reject()`（X/Esc/取消）**：

`_write_state_to_main_page` 从未被调用 → `tp._annotation_dict` 仍是对话框打开前的旧值 → 重开时恢复到改之前。**这就是数据丢失路径。**

---

## §3 dirty 标记的写入与读取

### 写入

| 位置 | 文件:行号 | 写入内容 |
|------|-----------|---------|
| PhaseADialog 初始化 | `calibration_tab.py:1065` | `self._annotation_dirty: set[str] = set()` — 空集 |
| 应用暗号按钮 | `calibration_tab.py:1318` | `self._annotation_dirty.add(cname)` — 标记该列为手动编辑 |
| 写回温度页 | `calibration_tab.py:1498` | `tp._annotation_dirty = set(self._annotation_dirty)` |
| 写回 phase_a_state | `calibration_tab.py:1504` | `"annotation_dirty": list(self._annotation_dirty)` |
| 状态恢复 | `calibration_tab.py:1075` | `self._annotation_dirty = set(state.get("annotation_dirty", []) or [])` |

### 读取（保护手改值不被覆盖）

| 位置 | 文件:行号 | 读取方式 |
|------|-----------|---------|
| reopen merge | `calibration_tab.py:1078` | `if col_name in self._annotation_dirty:` — dirty 列锁定 |

### dirty 保护的完整链路

```
应用暗号 → _annotation_dirty.add(cname)  [line 1318]
         ↓
确定按钮 → accept() → _write_state_to_main_page()
         ↓
    tp._annotation_dirty = set(self._annotation_dirty)  [line 1498]
    tp._phase_a_state["annotation_dirty"] = list(...)   [line 1504]
         ↓
重开对话框 → __init__()
         ↓
    self._annotation_dirty = set(state.get("annotation_dirty", []))  [line 1075]
         ↓
    for col_name, saved_val in saved_annot.items():
        if col_name in self._annotation_dirty:           [line 1078]
            self._annotation[col_name] = saved_val       [line 1080]  ← 锁定
```

**断裂点**：若关闭走 `reject()` → `_write_state_to_main_page` 未被调用 → `tp._phase_a_state["annotation_dirty"]` 仍是旧值（或不存在）→ 重开时 dirty 集为空 → 无列被锁定 → 文件值优先于用户手改值。

---

## §4 与本批 B 改动（restore 合并优先级）的关系

### restore() 合并段

`py/calibration/project_config.py:370-384` — 本批 B 唯一改动点：

```python
merged = dict(profile_annot) if profile_annot else {}       # ← 改后: profile 为底
for col_name, file_val in (file_annot or {}).items():
    if col_name in tp_dirty and col_name in tp_existing:
        merged[col_name] = tp_existing[col_name]
    elif col_name not in merged or not merged.get(col_name):
        merged[col_name] = file_val                          # ← 文件仅兜底
```

### PhaseADialog.__init__ 合并段

`ui/calibration_tab.py:1077-1084` — 独立的合并逻辑（**未被本批 B 改动**）：

```python
for col_name, saved_val in saved_annot.items():
    if col_name in self._annotation_dirty:
        self._annotation[col_name] = saved_val
    elif col_name not in self._annotation or not self._annotation.get(col_name):
        self._annotation[col_name] = saved_val
    # else: 文件有非空暗号 → 保持文件值
```

### 证据：两条路径物理隔离

| 路径 | 入口函数 | 文件:行号 | 是否经过 `restore()`? |
|------|----------|-----------|---------------------|
| 加载项目 | `ProjectConfigManager.restore()` | `project_config.py:340` | — (它就是 restore) |
| 同会话编辑后重开 | `_open_phase_a()` → `PhaseADialog.__init__` | `calibration_tab.py:2866,1052` | **否** — 直接读 `tp._annotation_dict` + `tp._phase_a_state` |

**本批 B 的 `project_config.py:375` 改动不触及 `PhaseADialog.__init__` 的合并逻辑。此 bug 独立于本批 B，是既有缺陷。**

---

## §5 是否既有缺陷

### git log

```
9b089af feat(phase-b): compensation pipeline...   ← 最后提交 (HEAD)
ed79e56 fix: 应变标定「导出 Excel」按钮静默无响应
a4ca33c feat: 多模块修复 — Sensors解析路由 + ...
a7b25b0 feat(calibration): 应变标定全链路 — ...
7fad573 传感器标定模块 v2.1 — 温度/应变标定全链路 + profile 持久化
```

### blame 确认

| 关键位置 | 当前行号 | 最后修改 commit | 是否在本批 B 之前? |
|----------|---------|----------------|-------------------|
| `_on_apply` 写入 `self._annotation` | `1317` | `7fad573` (模块 v2.1) | ✅ 本批 B 之前 |
| `reject()` 不写回 | `1517-1519` | `7fad573` (模块 v2.1) | ✅ 本批 B 之前 |
| `__init__` merge 逻辑 | `1077-1084` | `7fad573` (模块 v2.1) | ✅ 本批 B 之前 |
| `_write_state_to_main_page` | `1492-1511` | `7fad573` (模块 v2.1) | ✅ 本批 B 之前 |
| `_open_phase_a` 传参 | `2867-2870` | `7fad573` (模块 v2.1) | ✅ 本批 B 之前 |

**全部关键代码自 `7fad573`（模块 v2.1）后未再修改。本批 B 的 `restore()` 改动未触及任何 PhaseADialog 代码。**

### 结论

此 bug 是 **既有缺陷**，与两个因素相关：
1. **主因：`reject()` 不写回** — X/Esc/取消 关闭对话框时注释明确说"reject 不写回"，导致「应用暗号」的本地修改被丢弃
2. **次因：文件作底合并** — `__init__` merge（line 1077-1084）即使走 accept 路径也以"文件值"为底，与 `restore()` 旧版 merge 同构，但 accept 路径下 `self._annotation` 和 `saved_annot` 同源同时写入，仅在时序异常时才对不齐

---

## 补证 — 「应用暗号」写回确认 + 对话框合并反转落点 (2026-06-25)

### 补证 1: 「应用暗号」是否写回主页面状态

**答案：否。**

`_on_apply` (`calibration_tab.py:1290-1355`) 的完整写回范围：

```python
# line 1290-1355 — PhaseADialog._on_apply()
def _on_apply(self):
    ...
    for i, cname, entered in snapshots:
        if ok:
            self._annotation[cname] = entered           # line 1317 — ★ 仅写到对话框本地
            self._annotation_dirty.add(cname)            # line 1318 — ★ 仅写到对话框本地
            ...
    # Step 3: 重建 groups
    self._groups = _group_annotations_by_prefix(...)     # line 1344 — ★ 仅写对话框本地
    # Step 4: 刷新计数
    self._legal_cnt, self._placeholder_cnt = ...         # line 1351
    # Step 5: 同步按钮
    self._sync_button_state()                            # line 1354
    self._sync_info_label()                              # line 1355
```

**整个 `_on_apply` 函数体内没有调用 `_write_state_to_main_page()`，也没有直接写入 `tp._annotation_dict` 或 `tp._phase_a_state`。** 搜索 `_on_apply` 全函数体 (line 1290-1355) 不含以下任一词：`tp`、`_write_state`、`_annotation_dict`（温度页的）、`_phase_a_state`（温度页的）。

**对照 `_write_state_to_main_page` (line 1492-1511) 的写入清单：**

| 写入目标 | `_on_apply` 是否写入 | `_write_state_to_main_page` 是否写入 |
|----------|---------------------|-------------------------------------|
| `self._annotation` (对话框本地) | ✅ line 1317 | ✅ line 1496 (→`tp._annotation_dict`) |
| `self._annotation_dirty` (对话框本地) | ✅ line 1318 | ✅ line 1498 (→`tp._annotation_dirty`) |
| `self._groups` (对话框本地) | ✅ line 1344 | ✅ line 1497 (→`tp._annotation_groups`) |
| `tp._annotation_dict` | ❌ **不写** | ✅ line 1496 |
| `tp._phase_a_state` | ❌ **不写** | ✅ line 1502-1511 |

**`_on_apply` 只写对话框本地副本，不写温度页。仅 `accept()` → `_write_state_to_main_page()` 才写回温度页。**

### 补证 2: 重开时的实参来源 — `tp._annotation_dict` 是否被「应用暗号」更新

**答案：否。**

`_open_phase_a` (`calibration_tab.py:2866-2870`)：

```python
def _open_phase_a(self):
    dlg = PhaseADialog(
        self._loaded_df, self._annotation_dict, self._annotation_groups,  # ★
        self._detection_params, self._time_col_idx, self,
    )
```

`self._annotation_dict` 是温度页的实例属性。它的写入入口：
- `_load_and_parse:2655` — 新文件解析 → 直接赋值
- `_write_state_to_main_page:1496` — PhaseADialog.accept() → 写回
- `ProjectConfigManager.restore:385` — 加载项目 → `tp._annotation_dict = merged`

**「应用暗号」不在上述任一项中。** 点击「应用暗号」后 `tp._annotation_dict` 保持旧值 → 用 X/Esc 关闭 → 下次 `_open_phase_a` 传入的仍是旧 `tp._annotation_dict` → PhaseADialog `__init__` 从旧 `annotation_dict` 初始化 → 手改丢失。

**即使走 accept**：`_write_state_to_main_page` 在 `accept()` 中写入 → 下次打开拿到新值 → 但 `__init__` 的 merge (line 1077-1084) 仍以"文件为底"迭代，accept 路径下 `saved_annot` 与 `self._annotation` 同时写入一致，**恰好不丢**。但与修法 B 修过的 `restore()` 同构的 "文件为底"逻辑仍是潜在火药。

### 补证 3: 对话框合并反转落点

**当前代码** (`calibration_tab.py:1057-1058` + `1077-1084`)：

```python
# line 1057-1058 — 初始化: 文件为底
self._file_annotation = dict(annotation_dict) if annotation_dict else {}
self._annotation = dict(self._file_annotation)            # ← ★ 文件为底 (落点 A)

# line 1077-1084 — merge loop: saved_annot 仅补充缺口
for col_name, saved_val in saved_annot.items():
    if col_name in self._annotation_dirty:
        self._annotation[col_name] = saved_val             # dirty → 锁定
    elif col_name not in self._annotation or not self._annotation.get(col_name):
        self._annotation[col_name] = saved_val             # 文件无 → profile 兜底
    # else: 文件有非空暗号 → 保持文件值                    # ← ★ 文件胜出 (落点 B)

# line 1057-1065 — dirty 初始化为空
self._annotation_dirty: set[str] = set()                  # ← ★ 每次重置 (落点 C)
```

**反转落点**（与 `restore()` 修法 B 同招）：

| 落点 | 当前 | 应改为 |
|------|------|--------|
| A (line 1058) | `self._annotation = dict(self._file_annotation)` | `self._annotation = dict(saved_annot or annotation_dict)` — 以已存暗号为底 |
| B (line 1084) | `else: 保持文件值` → 文件胜出 | `else: 保持 saved 值` → saved_annot 胜出 |
| C (line 1065) | 空集 → 1058 初始被 file 占满 | 不变 — dirty 恢复由 line 1075 从 state 读取 |

**但落点 A 有个前置条件**: `saved_annot` 在 line 1074 才从 `state.get("annotation")` 读取 — 在 line 1058 之后。若要在 1058 用 saved_annot 为底，需把 line 1074 的 `saved_annot` 读取**提前**到 1058 之前，或改变初始化顺序：先用 `annotation_dict`（tp 的当前值）为底，然后把 `phase_a_state` 中的旧值作为覆盖层。注意 `annotation_dict` 参数 = `tp._annotation_dict`，它在上次 accept 时已被写回 — 所以直接用 `dict(annotation_dict)` 为底即可（变相实现 "已有暗号为底"），然后 merge 只做 dirty 列锁定 + phase_a_state 兜底。

**简化落点**：改 `calibration_tab.py:1058` 一行即可——

```python
# 改前
self._annotation = dict(self._file_annotation)
# 改后
self._annotation = dict(annotation_dict)  # ← 以传入的当前 annotation 为底 (=上次 accept 写回值)
```

然后将 merge 方向反转：`for col_name, saved_val in saved_annot.items()` → 遍历时 `saved_annot` 中的值优先，`annotation_dict` 中已有值保持（已在底中）。这本质上与修法 B 一样 —— 底从 "file/旧值" 换成 "已存/当前值"。

### 补证 4: 两处合并是否应抽成统一函数

**两处位置**:

| 位置 | 文件:行号 | 底 | 补充源 | dirty 源 |
|------|-----------|-----|--------|---------|
| restore Step 1 | `project_config.py:375-384` | `file_annot` → `profile_annot` (已修) | file_annot | `tp._annotation_dirty` (永远空) |
| PhaseADialog `__init__` | `calibration_tab.py:1058-1084` | `annotation_dict`（= `tp._annotation_dict`）| `saved_annot`（= `tp._phase_a_state["annotation"]`）| `self._annotation_dirty`（从 `phase_a_state` 恢复） |

**差异分析**:

| 维度 | restore | PhaseADialog |
|------|---------|-------------|
| 输入: "已有值" | `profile_annot` (JSON) | `annotation_dict` (tp 实例属性) |
| 输入: "文件值" | `file_annot` (局部变量) | `self._file_annotation` (= `annotation_dict` 副本) |
| 输入: dirty | `tp._annotation_dirty` (实例属性, set) | `self._annotation_dirty` (从 `phase_a_state` 恢复, set) |
| 合并方向 | profile 为底 + 文件兜底 | 应改为: 已存为底 + 文件兜底 |
| 额外逻辑 | data_cols_for_group + _groups 重建 | `_fill_table` + `_refresh_all` + groups update |

**可行性**: 两处的核心形状一致 — `{底: dict, 补充: dict, dirty: set}`。可抽成一个纯函数 `merge_annotation(base, fallback, dirty)` → 返回 merged dict。但 restore 有 data_cols_for_group/_groups 重算 (line 386-398)，PhaseADialog 有 `_fill_table` 重建 (line 1067-1093)。**合并纯 dict 层可行；但 caller 的副作用（_groups 重建 / 表格重填）各自不同，不能并入统一函数。**

**抽纯函数签名建议**（只评估，不实施）：

```python
# 可放在 utils/annotation_utils.py
def merge_annotations(base: dict[str, str], fallback: dict[str, str],
                       dirty: set[str] | None = None) -> dict[str, str]:
    """以 base 为底, fallback 仅填充 base 中缺失/空的列; dirty 中的列锁定不动。"""
    merged = dict(base)
    dirty = dirty or set()
    for col_name, val in (fallback or {}).items():
        if col_name in dirty:
            continue  # dirty 列已在 base 中保持不变
        if col_name not in merged or not merged.get(col_name):
            merged[col_name] = val
    return merged
```

restore 调用: `merged = merge_annotations(profile_annot, file_annot)` (dirty 锁不生效 → 跳过)
PhaseADialog 调用: `self._annotation = merge_annotations(annotation_dict, saved_annot, dirty=self._annotation_dirty)`

**结论**: 纯函数可抽，消除孪生分叉。但 caller 的副作用差异（_groups / _fill_table）保留在原调用点，不并入。
