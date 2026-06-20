# ENLIGHT/Hyperion Sensors（光纤传感·公式版）解析优化规格 + 提示词

> 交付给 Claude Code 执行。目标：优化 `utils/file_parser.py` 中 **ENLIGHT Sensors（公式版）** 分支，
> 修正 **BOM 污染 / 内嵌暗号行污染数值 / 混合引号漏剥** 三类问题，
> **绝不破坏插入备注暗号(annotation)的功能**，遵守 `CLAUDE.md` 全部硬纪律。
> 本规格与上一份《ENLIGHT Peaks 解析修复规格》要求一致，但针对的是**另一种模板/格式**。

---

## 0. 一句话结论

公式版(Sensors)是**干净矩形表**（每行字段数恒定，无错位），痛点不在列错位，而在
**①带 UTF-8 BOM ②文件已内嵌暗号行 ③暗号用混合引号**。
修复 = 去 BOM + 把暗号行识别/抽取为 annotation（不污染数值）+ 引号剥离兼容直引号与弯引号。

> 与 Peaks 的区别：Peaks 有元数据块 + `# CH n` 计数列 + 变长波长（会错位）；
> Sensors 无元数据块、首行即表头、每行字段恒定（本样本 17 列 × 15656 数据行）。两条分支**互不影响**。

---

## 1. 文件格式权威说明（以实测文件为准）

样本：`Sensors_20260519093854_sampled_10pct.txt`（UTF-8 **带 BOM**，`\t` 分隔，`\r\n` 行尾）。

### 1.1 整体结构（无元数据块）
```
[字节头]  EF BB BF                      ← UTF-8 BOM，必须先剥
[第 0 行] 表头                          ← Timestamp \t <8个公式/传感列> \t <8个 FBG_ 波长列>
[第 1 行] 暗号行(annotation，已内嵌)     ← 引号包裹的中文标注，仅标注部分列，其余为空
[第 2 行起] 数据区                       ← 全标量，每行字段数 == 表头列数(17)
```

### 1.2 表头（17 列，本样本）
```
[0] Timestamp
[1..8]  A1G1 A1G2 A2G1 A2G2 B1G1 B1G2 B2G1 B2G2   ← 公式/物理量列（小幅有符号数，如应变）
[9..16] FBG_A1 FBG_A2 FBG_B1 FBG_D1 FBG_F1 FBG_F2 FBG_G1 FBG_G2  ← 原始波长列（~1525–1551 nm）
```
- `FBG_` 前缀列是原始波长，供 `_auto_populate_fbgs`（正则 `FBG_`）与公式引擎使用。
- 列集合由文件决定，**不要写死列名/列数**；仅 `Timestamp` 为固定关键列。

### 1.3 暗号行（第 1 行，核心）
实测：
```
[0] "'时间戳'"        ← 直引号 0x27 包裹
[1] "‘应变-光纤1’"    ← 弯引号 0x2018/0x2019 包裹
[2] "‘应变-光纤2’"    ← 弯引号
[3..16] ""           ← 未标注列为空（稀疏标注，必须保留这种稀疏性）
```
- 抽取后应得 annotation = `{"Timestamp":"时间戳","A1G1":"应变-光纤1","A1G2":"应变-光纤2"}`。
- **混合引号陷阱**：同一行里直引号(`'` `0x27`)与弯引号(`'``'` `0x2018/0x2019`)并存；
  另需兼容中文/英文双引号(`"` `"` `"`)。剥离必须覆盖整套。

### 1.4 数据行（第 2 行起）
- 字段数恒定 == 表头列数；`Timestamp` 为完整日期时间串 `2026/5/19 09:38:55.04646`；其余列为浮点。

---

## 2. 当前实现的三处隐患（修复目标）

1. **BOM 未剥**：用普通 `utf-8`/默认编码读，表头首格 = `"\ufeffTimestamp"`，`== "Timestamp"` 判定失败 →
   时间列识别/对齐错乱、格式判别误判。**必须 `decode("utf-8-sig")` 或 `encoding="utf-8-sig"`。**
2. **暗号行被当数据**：第 1 行(暗号)若进入数值解析，`A1G1/A1G2` 列被拉成 `object`（字符串+浮点混合），
   首行数据被暗号串/NaN 占据 → `_is_fiber_data`、`pd.to_numeric`、绘图、公式求值全部受污染。
   **必须识别暗号行 → 抽取为 annotation → 从数据区剔除 → 数值列保持 float。**
3. **引号漏剥**：只剥直引号或只剥弯引号 → 暗号键残留引号字符 → 与列名/`annotated_cols`/模板对不上。
   **必须用统一引号集合剥离。**

---

## 3. 修复方案

### 3.1 改前必查（硬纪律 #5 / 代理准则 #1）
1. `codegraph_context parse_enlight_file` → 看清 Sensors 分支与返回契约 `(df, annotation, meta)`。
2. `codegraph_callers parse_enlight_file` / `parse_file` → 确认调用点，**签名与三元组返回不变**。
3. `codegraph_context` 暗号提取函数、`_is_fiber_data`、`_auto_populate_fbgs`、`annotated_cols`、
   以及暗号行**插入(显示)**路径（`CLAUDE.md` 纪律 #1 的 `df.astype(object)` + `pd.concat`）。
   → 明确"清洗后 df 是否内嵌暗号行用于显示"的现有约定，修复后**保持一致**。

### 3.2 核心原则
- **格式判别优先级**（避免与 Peaks 撞车）：
  1) 含 `Module Type: Hyperion`/`ENLIGHT Version` 元数据块 且 有 `Timestamp\t# CH 1` 表头 → **Peaks**；
  2) 否则（去 BOM 后）首行以 `Timestamp\t` 开头 → **Sensors / 公式版**；
  3) 否则 → Legacy。
- 一律 `utf-8-sig` 读取（去 BOM）。
- 暗号行**识别即抽取**，不进数值区；数值列保持 `float64`。
- 引号剥离兼容 `' " ' ' " "`（`0x27 0x22 0x2018 0x2019 0x201c 0x201d`）。
- **暗号列命名/键沿用现状**，不改名（保护已存项目的 `annotated_cols`/模板键）。
- 暗号行的**显示插入**继续走既有安全拼接路径，本次不改其逻辑。

### 3.3 参考实现（类型干净，pyright 友好）
```python
from __future__ import annotations

import io
import re

import pandas as pd

# 引号集合：直引号/双引号 + 中文弯引号
_QUOTES = "'\"\u2018\u2019\u201c\u201d"
_STRIP_RE = re.compile(rf"^[{_QUOTES}]+|[{_QUOTES}]+$")


def _strip_code_quotes(s: str) -> str:
    """剥离暗号单元首尾引号（兼容直/弯/中英双引号）。"""
    return _STRIP_RE.sub("", s.strip())


def _looks_like_annotation_row(cells: list[str]) -> bool:
    """暗号行判定：存在引号包裹的非数值单元，且无任何裸数值。"""
    has_quoted = False
    for c in cells:
        c = c.strip()
        if not c:
            continue
        if c[0] in _QUOTES:
            has_quoted = True
            continue
        try:
            float(c)
            return False  # 出现裸数值 → 不是暗号行（是数据行）
        except ValueError:
            continue
    return has_quoted


def parse_enlight_sensors(
    path: str,
) -> tuple[pd.DataFrame, dict[str, str], dict[str, object]]:
    """解析 ENLIGHT Sensors（公式版）。

    返回 (df, annotation, meta)：
      df         — 干净数值 DataFrame（不含暗号行；Timestamp 为字符串，其余列 float）
      annotation — {列名: 暗号字符串}，引号已剥；稀疏（未标注列不入字典）
      meta       — {format, had_bom, annotation_row, num_data_cols, ...}
    """
    text = open(path, "rb").read().decode("utf-8-sig")  # ★ 去 BOM
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    if not lines or not lines[0]:
        raise ValueError("空文件或无表头")

    header = lines[0].split("\t")
    annotation: dict[str, str] = {}
    data_start = 1

    if len(lines) > 1 and lines[1].strip():
        row1 = lines[1].split("\t")
        if _looks_like_annotation_row(row1):
            for col, cell in zip(header, row1):
                cell = cell.strip()
                if cell:
                    annotation[col] = _strip_code_quotes(cell)
            data_start = 2

    body = "\n".join(lines[data_start:])
    df = pd.read_csv(io.StringIO("\t".join(header) + "\n" + body), sep="\t")

    # 数值化（暗号已剔除，不会再污染 dtype）
    for col in df.columns:
        if col != "Timestamp":
            df[col] = pd.to_numeric(df[col], errors="coerce")

    meta: dict[str, object] = {
        "format": "hyperion_sensors",
        "had_bom": True,
        "annotation_row": (data_start == 2),
        "num_data_cols": len(header) - 1,
    }
    return df, annotation, meta
```

### 3.4 接入 `parse_enlight_file`
- Sensors 分支：按 §3.2 判别命中后 → `df, annotation, meta = parse_enlight_sensors(path)` → 直接返回三元组。
- 不要改动 Peaks / Legacy 分支。
- **禁止** `on_bad_lines='skip'` 静默丢行；本格式恒定列宽，畸形行应记录告警。

---

## 4. 暗号(annotation)保护要点（不得破坏）

1. **本次只修"读取/抽取"，不改暗号插入/显示逻辑**。错误后果是暗号被当数据污染或键残留引号；
   修复后暗号被正确抽取为字典，键 == 列名，值为纯中文标注。
2. **暗号键命名沿用现状**，禁止改名（`annotated_cols`/模板/已存项目依赖列名一致）。
3. **稀疏标注必须保留**：仅 `Timestamp/A1G1/A1G2` 有暗号，其余列无 → 字典只含这 3 项，不要给空列塞默认值。
4. **显示用暗号行的"再插入"** 继续走既有安全拼接（`df.astype(object)` → `pd.concat([暗号行, 数据])`，
   见 `CLAUDE.md` 关键纪律 #1）。若现有 Sensors 分支约定"返回的 df 内嵌暗号行用于显示"，
   则在 §3.3 完成**数值化之后**再用安全拼接把暗号行加回顶部；否则保持"干净 df + 独立 annotation"。
   两种都行，**以 codegraph 查到的现有契约为准，保持一致**。
5. 引号剥离覆盖 `' " ' ' " "`，避免暗号键/值残留任何引号字符。

---

## 5. 必配测试（smoke + 断言，pyright 零红线）

新增 `tests/test_parse_enlight_sensors.py`，至少覆盖：

1. **BOM 处理**：表头第一列严格 `== "Timestamp"`（不含 `\ufeff`）。
2. **暗号抽取**：`annotation == {"Timestamp":"时间戳","A1G1":"应变-光纤1","A1G2":"应变-光纤2"}`；
   断言键/值**不含任何引号字符**（直引号与弯引号都验）。
3. **数值纯净**：除 `Timestamp` 外所有列 dtype 为 float；`df.shape[0] == 15656`（样本）；
   `df["A1G1"].iloc[0] == -0.637`；暗号串**未出现**在任何数据行。
4. **稀疏性**：未标注列（如 `A2G1`/`FBG_A1`）**不在** annotation 字典中。
5. **混合引号单测**：构造合成暗号行同时含直引号与弯引号 → 均被正确剥离。
6. **格式判别**：Sensors 文件被判为 `hyperion_sensors`；上一份 Peaks 文件仍判为 `hyperion_peaks`（互不误判）。
7. **暗号往返 smoke**：解析 → 既有暗号显示插入 → 既有暗号提取 → 字典键与列名一致；全程 no exception。
8. **回归隔离**：Peaks / Legacy 既有测试保持通过。

测试夹具：手写**合成**小文件（带 BOM + 暗号行 + 几行数据），不要把真实大文件入库（硬纪律：synthetic-only fixtures）。

---

## 6. 提交前检查清单（CLAUDE.md 硬纪律）

- [ ] 改前 `codegraph_callers parse_enlight_file`/`parse_file` 已确认调用点，签名/返回三元组不变。
- [ ] `utf-8-sig` 去 BOM；表头 `Timestamp` 判定通过。
- [ ] 暗号键命名与现状一致（不改名）；稀疏标注保留；引号集合覆盖直/弯/中英双引号。
- [ ] `pyright utils/file_parser.py tests/test_parse_enlight_sensors.py` 零红线；
      无裸 `# type: ignore`，必要处 `# pyright: ignore[具体rule]  # 原因`。
- [ ] DataFrame 判空用 `df is None or df.empty`，禁止 `if df`。
- [ ] 缺键+None 双守卫：`meta`/配置取值用 `d.get(k) or default`。
- [ ] `python run_tests.py` 全绿；新测试覆盖 §5 全部条目。
- [ ] 未触碰暗号插入/显示逻辑、Peaks/Legacy 分支、下游 `_is_fiber_data`/`_auto_populate_fbgs` 契约。

---

## 7. 可直接粘贴的提示词（Prompt for Claude Code）

```
任务：优化 utils/file_parser.py 中 ENLIGHT「Sensors / 光纤传感公式版」的解析，修正三处隐患，
不得破坏插入/抽取备注暗号(annotation)的功能。严格遵守 CLAUDE.md 全部硬纪律。

【格式事实】（已实测，样本 Sensors_20260519093854_sampled_10pct.txt）
- 文件带 UTF-8 BOM；制表符分隔；\r\n 行尾；无元数据块。
- 第0行=表头：Timestamp + 8个公式/物理量列(A1G1..B2G2) + 8个原始波长列(FBG_*)；本样本共17列。
- 第1行=已内嵌的暗号行：引号包裹中文标注，仅标注部分列(其余为空)，
  且引号混用——'时间戳'用直引号(0x27)，'应变-光纤1/2'用弯引号(0x2018/0x2019)。
- 第2行起=数据：每行字段数恒定==表头列数(无错位)；Timestamp为完整日期时间串，其余为浮点。

【三处必修隐患】
1) BOM未剥→表头首格变 "\ufeffTimestamp"，Timestamp判定失败：改用 decode("utf-8-sig")。
2) 暗号行被当数据→A1G1/A1G2列被污染成object、首行变NaN：必须识别暗号行→抽取为annotation字典→
   从数据区剔除→其余列保持float64。
3) 引号漏剥→暗号键残留引号：剥离须兼容 ' " ' ' " "（0x27 0x22 0x2018 0x2019 0x201c 0x201d）。

【实现要求】
- 先 codegraph_context/codegraph_callers 查清 parse_enlight_file 的 Sensors 分支、返回契约
  (df, annotation, meta)、暗号显示插入路径、_is_fiber_data/_auto_populate_fbgs/annotated_cols，
  保持函数签名与三元组返回不变，所有调用点不受影响。
- 格式判别优先级：有Hyperion元数据块+「Timestamp\t# CH 1」表头→Peaks；否则去BOM后首行以
  「Timestamp\t」开头→Sensors；否则Legacy。三分支互不误判。
- 暗号键命名沿用现状不改名；稀疏标注保留(未标注列不入字典)；不得用 on_bad_lines='skip' 静默丢行。
- 暗号行的「显示再插入」继续走既有安全拼接(df.astype(object)+pd.concat)，本次不改其逻辑；
  数值化必须在拼接之前完成，避免dtype污染。
- 参考实现见《ENLIGHT Sensors 解析优化规格》§3.3（parse_enlight_sensors / _strip_code_quotes /
  _looks_like_annotation_row），按现有命名约定落地。

【测试】新增 tests/test_parse_enlight_sensors.py，用合成小文件(带BOM+混合引号暗号行+数据)覆盖：
表头无BOM；annotation=={Timestamp:时间戳,A1G1:应变-光纤1,A1G2:应变-光纤2}且键值无引号；
除Timestamp外列为float、暗号串不出现在数据；未标注列不在字典；混合引号均被剥；
格式判别Sensors↔Peaks互不误判；暗号往返smoke无异常；Peaks/Legacy既有测试不回归。

【提交门禁】pyright零红线(无裸type:ignore)；DataFrame判空用 is None/.empty；
缺键+None双守卫；python run_tests.py 全绿。
```
