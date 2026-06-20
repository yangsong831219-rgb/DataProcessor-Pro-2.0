# ENLIGHT/Hyperion Peaks 解析错位修复规格

> 交付给 Claude Code 执行。目标：修复 `utils/file_parser.py` 中 ENLIGHT Peaks 解析的**时间戳列错位**，
> 同时**绝不破坏暗号(annotation)插入与提取机制**。遵守 `CLAUDE.md` 全部硬纪律。

---

## 0. 一句话结论

ENLIGHT **Peaks** 格式的每个数据行 = `时间戳 + 16个"峰值数量"整数 + sum(数量)个波长`。
当前解析把 `# CH n` 当成了数据列、没有识别中间这 16 个**计数列**，导致行宽(25) > 表头有效宽(17)，
pandas 右对齐错位，时间戳被挤出、波长落进 `# CH` 列。修复 = **按计数列块显式解析**，输出干净矩形表
`Timestamp + N个波长列`。

---

## 1. 文件格式权威说明（以实测文件为准）

样本：`Peaks_20260512143425.txt`（3278 数据行，恒定 8 光栅）。

### 1.1 整体结构
```
[元数据块] 第 0 ~ 103 行   ← Culture/Date/ENLIGHT Version/Module Type: Hyperion/各 CH Configuration
[数据表头] 第 104 行       ← "Timestamp\t# CH 1\t# CH 2\t…\t# CH 16" + 8 个空 pad 单元 (共 25 字段)
[数据区]   第 105 行 起
```
- 编码：UTF-8（可能带 BOM）。分隔符：**制表符 `\t`**，行尾 `\r\n`。
- 元数据块靠特征行识别：含 `ENLIGHT Version` 或 `Module Type: Hyperion`。
- 数据表头靠 **`line.startswith("Timestamp\t# CH 1")`** 精确定位（不要用 csv.Sniffer 猜）。

### 1.2 数据行真实布局（核心）
```
字段[0]      = 时间戳字符串          例如 "34:26.0"
字段[1..16]  = 16 个通道的"峰值数量"  整数，例如 2 2 0 2 0 1 1 0 0 0 0 0 0 0 0 0
字段[17..]   = 波长值，数量 = sum(字段[1..16])，按通道顺序平铺
```
实测首行：计数 `(2,2,0,2,0,1,1,0,…)` → sum=8 → 紧跟 8 个波长：
- CH1 的 2 个：`1525.39761, 1531.24435`
- CH2 的 2 个：`1545.377, 1550.34863`
- CH4 的 2 个：`1534.48452, 1542.29875`
- CH6 的 1 个：`1530.51401`
- CH7 的 1 个：`1538.46837`

> ⚠️ `# CH n` 是**峰值数量**，不是波长。这是全部错位的根源。
> 本文件 8 个波长 + 17 命名列 恰好 = 25，与表头 pad 后宽度相同，纯属巧合；
> 通道数/峰数不同的采集，数据行宽会与 25 不同，**不可依赖固定 25**。

### 1.3 错位机制（已复现）
表头去掉 8 个空 pad 后是 17 列，而数据行 25 字段。pandas 读时行宽>表头宽 → 右对齐：
`"34:26.0 2 2 0 2 0 1 1"` 整串被吞进首列/索引，`# CH` 列整体右移，波长落到最右的
`# CH 9…# CH 16`，左侧 `# CH` 列与 Timestamp 显示 `0`。**与截图症状完全一致。**

---

## 2. 修复方案

### 2.1 改动定位（先查，后改 —— 硬纪律 #5 / 代理准则 #1）
1. `codegraph_context parse_enlight_file` → 看清 Peaks 分支现状、返回契约 `(df, annotation, meta)`。
2. `codegraph_callers parse_enlight_file` 和 `codegraph_callers parse_file` → 确认所有调用点，
   **保持函数签名与返回三元组不变**（避免波及 compare_tab / calibration_tab / analysis_tab）。
3. `codegraph_context _is_fiber_data`、`_auto_populate_fbgs`、`annotated_cols`、暗号提取函数
   → 确认**波长列命名约定**与暗号如何对齐（见 §3）。

### 2.2 核心原则
- 在 Peaks 分支用**显式按字段解析**取代"通用分隔符+表头推断"。
- 输出 DataFrame 列 = `Timestamp + N 个波长列`；时间戳归位，波长按通道槽位对齐。
- **波长列命名沿用代码现有约定**（截图暗号串是 `w1-类型-位置`，列名很可能是 `w1…wN` 之类）。
  本次只修对齐，**不要改名**，否则会让用户已保存的暗号映射/模板失效。命名函数留成可替换钩子，
  默认产出与现状一致的列名。
- 计数列**消费但不保留**为数据列（仅用于定位波长与建立通道槽位）。

### 2.3 参考实现（类型干净，pyright 友好；命名钩子按现状替换）
```python
from __future__ import annotations

import pandas as pd

_N_CH = 16


def _find_peaks_header(lines: list[str]) -> int | None:
    """返回数据表头行号；非 Peaks 文件返回 None。"""
    for i, ln in enumerate(lines):
        if ln.startswith("Timestamp\t# CH 1"):
            return i
    return None


def _wl_column_name(flat_index: int, channel: int, k: int) -> str:
    """波长列命名钩子。务必替换为与现有解析一致的命名，勿擅自改名。
    flat_index 从 0 起；channel 为 1..16；k 为该通道内第几个峰(从1起)。"""
    return f"w{flat_index + 1}"  # ← 占位：替换为代码现有约定


def parse_hyperion_peaks(lines: list[str], header_idx: int) -> pd.DataFrame:
    """解析 Hyperion Peaks 数据块。

    行布局: <时间戳> \\t <16个峰值数量整数> \\t <sum(数量)个波长>。
    '# CH n' 是峰值数量，不是波长。返回 [Timestamp, <N个波长列>]，
    按通道槽位对齐（峰丢失填 NaN），列集合在全文件内稳定。
    """
    # ---- 1) 扫描数据行，按"每通道峰数的全局最大值"确定稳定槽位布局 ----
    raw_rows: list[list[str]] = []
    max_counts = [0] * _N_CH
    for ln in lines[header_idx + 1:]:
        ln = ln.rstrip("\r")
        if not ln.strip():
            continue
        f = ln.split("\t")
        if len(f) < 1 + _N_CH:
            continue  # 非数据行/畸形行，跳过（建议同时计数并 log，勿静默全吞）
        counts = [int(x) for x in f[1:1 + _N_CH]]
        for c in range(_N_CH):
            if counts[c] > max_counts[c]:
                max_counts[c] = counts[c]
        raw_rows.append(f)

    # ---- 2) 固定列名（通道顺序展开槽位）----
    slot_channel: list[int] = []  # 每个输出波长列对应的通道(1..16)
    for ch in range(_N_CH):
        slot_channel.extend([ch + 1] * max_counts[ch])
    n_wl = len(slot_channel)
    wl_names: list[str] = []
    per_ch_k = [0] * _N_CH
    for idx, ch in enumerate(slot_channel):
        per_ch_k[ch - 1] += 1
        wl_names.append(_wl_column_name(idx, ch, per_ch_k[ch - 1]))

    # ---- 3) 逐行填充，通道槽位对齐，峰丢失填 NaN ----
    timestamps: list[str] = []
    matrix: list[list[float | None]] = []
    for f in raw_rows:
        counts = [int(x) for x in f[1:1 + _N_CH]]
        vals = f[1 + _N_CH:]  # 该行波长（可能含末尾空 pad）
        timestamps.append(f[0])
        row_out: list[float | None] = [None] * n_wl
        vi = 0  # 该行波长游标
        oi = 0  # 输出槽位游标
        for ch in range(_N_CH):
            for k in range(max_counts[ch]):
                if k < counts[ch] and vi < len(vals):
                    txt = vals[vi].strip()
                    row_out[oi] = float(txt) if txt else None
                    vi += 1
                oi += 1
        matrix.append(row_out)

    df = pd.DataFrame(matrix, columns=wl_names)
    df.insert(0, "Timestamp", timestamps)
    return df
```

### 2.4 接入 `parse_enlight_file`
- Peaks 分支：读全文 → `lines = text.split("\n")` → `header_idx = _find_peaks_header(lines)` →
  命中则 `df = parse_hyperion_peaks(lines, header_idx)`。
- **保持返回三元组**：`return df, annotation, meta`。
  - `annotation`：对**新解析**的原始 Peaks 文件，文件里本就没有引号包裹的暗号单元，提取结果应为空 dict，
    符合现状（暗号是用户后续通过备注对话框插入的）。沿用现有提取函数，不改逻辑。
  - `meta`：补上 `format="hyperion_peaks"`、`header_idx`、`num_meta_skipped=header_idx`、
    `num_wavelength_cols=n_wl`、`channel_slots`（如 `{"CH1":2,"CH2":2,"CH4":2,"CH6":1,"CH7":1}`），便于排错。
- 不要改动 Sensors / Legacy 两条分支；仅修 Peaks 分支。
- **禁止** `on_bad_lines='skip'` 把宽数据行当畸形行全部丢弃——这正是旧路径致命点之一。

---

## 3. 暗号(annotation)保护要点（不得破坏）

1. **本次只修对齐，不改暗号机制**。错位的真正后果是：波长此前落在 `# CH5…CH16`，于是用户插入的
   暗号串 `w1-类型-位置…` 被贴到了 `# CH` 列上。**修复列对齐后，暗号会自然贴到正确的波长列**，
   暗号插入/提取代码无需改动。
2. **波长列命名必须与现状一致**（§2.2、`_wl_column_name` 钩子）。改名会让已存项目里
   `annotated_cols` / 暗号字典 / 模板 的键对不上。先用 `codegraph` 确认现有列名格式再落钩子。
3. 暗号插入走的是"在数据上方插入一行标注"的既有路径（参考 `CLAUDE.md` 关键纪律 #1 的安全拼接：
   `df = df.astype(object)` → `pd.concat([annotation_row, df])`）。**沿用既有插入函数**，本次不碰。
4. 暗号提取（引号包裹非数值单元 → 去引号 → annotation dict）逻辑**保持不变**；它作用在修复后的
   正确列上即可。
5. `_is_fiber_data` 对 Peaks 数据应继续判为光纤数据（基于波长差走 `SensorSystem.calculate()`，
   允许"原始数据/物理量"切换）。修复不改变这一判定输入的列语义——确认它依据的是波长列特征而非 `# CH`。

---

## 4. 必配测试（硬纪律：smoke + 断言，pyright 零红线）

新增 `tests/test_parse_hyperion_peaks.py`，至少覆盖：

1. **结构断言**（happy path）
   - 列 = `["Timestamp", *N 个波长列]`，且 `N == sum(首行计数) == 8`（样本文件）。
   - `df.shape[0] == 3278`（样本）。
2. **时间戳归位**（直击本 bug）
   - `df["Timestamp"].iloc[0] == "34:26.0"`；任一波长列 dtype 为 float。
   - 断言 Timestamp 列**不含** `0`/小整数计数值。
3. **波长落位正确**
   - 首行 CH1 两峰列 ≈ `1525.39761, 1531.24435`；CH7 峰列 ≈ `1538.46837`（按你最终命名取列）。
4. **计数列已消费**
   - 输出列中**不存在** `# CH 1` 等计数列名。
5. **变峰数稳健性**（构造合成行：某通道某行少一个峰）
   - 该行对应槽位为 NaN，列集合不变、不串列。
6. **暗号往返 smoke**（不破坏暗号）
   - 解析 → 走既有暗号插入 → 标注行的波长标签落在波长列、`时间戳`标签落在 Timestamp 列、
   - 再走既有暗号提取 → annotation dict 的键与波长列名一致；全程 no exception。
7. **回归隔离**
   - Sensors / Legacy 分支既有测试保持通过。

测试夹具：构造**合成**的小 Peaks 文本（按 §1 布局手写 3~5 行），不要把真实大文件入库。

---

## 5. 提交前检查清单（CLAUDE.md 硬纪律）

- [ ] 改前 `codegraph_callers parse_enlight_file` / `parse_file` 已确认调用点，签名/返回不变。
- [ ] 波长列命名与现有约定一致（暗号键不漂移）。
- [ ] `pyright utils/file_parser.py tests/test_parse_hyperion_peaks.py` 零红线；
      无裸 `# type: ignore`，必要处用 `# pyright: ignore[具体rule]  # 原因`。
- [ ] DataFrame 判空用 `df is None or df.empty`，禁止 `if df`。
- [ ] 取值缺键+None 双守卫（如 meta 默认值用 `d.get(k) or default`）。
- [ ] `python run_tests.py` 全绿；新测试覆盖 §4 全部条目。
- [ ] 未触碰暗号插入/提取、Sensors/Legacy 分支、`# CH` 之外的下游契约。
