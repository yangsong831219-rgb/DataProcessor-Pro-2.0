# 四表未注入 docx 溯源 — 分级/异常/Ke/解耦 表在诊断 docx 中缺失

**日期**: 2026-06-25
**状态**: 只记现状，不给方案
**现象**: `tables(grade=True,ke=True,decoupling=True)` 数据齐，但生成的诊断 docx 没有四张表，也没有"数据汇总附表"段。日志 `gen_charts_from_bundle DONE: made=['calib_lin','ts_cleaning']`、`manifest_count=2`。

---

## §1 表生成函数存在性

### ChartBundle 的四个 table_md 字段

`core/chart_bundle.py:44-49` — `ChartBundle` dataclass 定义：

```python
@dataclass
class ChartBundle:
    ...
    grade_table_md: str = ""       # 传感器分级表
    anomaly_table_md: str = ""     # 异常统计表
    ke_table_md: str = ""          # 应变 Ke 汇总表
    decoupling_table_md: str = ""  # 温度 B 解耦标量表
```

### 四张表的填充位置

`ChartBundle.from_providers()` 中填充：

| 表 | 文件:行号 | 填充语句 |
|----|-----------|---------|
| anomaly_table_md | `chart_bundle.py:89` | `bundle.anomaly_table_md = _rows_to_md_table(rows)` |
| grade_table_md | `chart_bundle.py:156` | `bundle.grade_table_md = _rows_to_md_table(rows)` |
| decoupling_table_md | `chart_bundle.py:173` | `bundle.decoupling_table_md = _rows_to_md_table(rows)` |
| ke_table_md | `chart_bundle.py:201` | `bundle.ke_table_md = _rows_to_md_table(rows)` |

**确认**：字段存在 ✅、填充存在 ✅。

### gen_markdown_tables_from_bundle

`core/chart_bundle.py:398-414`：

```python
def gen_markdown_tables_from_bundle(cd: dict) -> str:
    """从 ChartBundle 字典生成全部 markdown 表格文本 (Batch A)。"""
    tables: list[str] = []
    for key, heading in [
        ("grade_table_md", "### 传感器分级汇总"),
        ("decoupling_table_md", "### 温度标定 — 解耦诊断标量"),
        ("ke_table_md", "### 应变标定 — Ke 系数汇总"),
        ("anomaly_table_md", "### 异常统计"),
    ]:
        md = cd.get(key, "") or ""
        if md.strip():
            tables.append(f"\n{heading}\n\n{md}\n")
    return "\n".join(tables) if tables else ""
```

**确认**：函数存在 ✅。

---

## §2 谁该调用它 — 全仓调用点搜索结果

```
gen_markdown_tables_from_bundle 全仓命中:
  main.py:1277,1279             ← 唯一调用点
  core/chart_bundle.py:292,398  ← 定义 + 注释
  tests/test_chart_bundle_tables.py:27,391,399,406,512  ← 单测
```

### 唯一调用点 — main.py:1275-1296 (报告 worker 路径)

```python
# main.py:1275-1296 — _on_gen_report() 的报告构建流程
# 2b. 注入标定/异常表格 (Batch A: 从 ChartBundle 提取 markdown 表格)
try:
    from core.chart_bundle import ChartBundle, gen_markdown_tables_from_bundle
    bundle = ChartBundle.from_providers(self)
    tables_md = gen_markdown_tables_from_bundle(bundle.to_dict())
    if tables_md:
        builder_data.setdefault("sections", []).append({
            "heading": "数据汇总附表",
            "content_paragraphs": [tables_md],
            "image_anchors": [],
            "tables": [],
        })
        print(f"[报告] 已注入数据汇总附表 (...)")
    else:
        print("[报告] 数据汇总附表为空 — 跳过")
except Exception:
    ...

# 3. 构建 docx
if report_type == 'ppt':
    ...
else:
    from py.report_builder.word_builder import WordBuilder   # line 1307
```

### \_build_word_document 是否调用它

`ui/ai_diagnosis.py:2865-3103` — `_build_word_document()` 全文搜索：

```
gen_markdown_tables → 0 命中
chart_data → 仅在 _build_diagnosis_record:1170,1171,1173 (写入 rec["chart_data"])，
             在 _build_word_document 中 → 0 命中
grade_table_md → 0 命中
```

**结论：`_build_word_document` 从未调用 `gen_markdown_tables_from_bundle`，也从未读取 `rec["chart_data"]` 中的任何 table_md 字段。**

---

## §3 docx 构建走哪条路 — 两条完全独立的路径

### 路径 A — 「生成检测报告」按钮 (表已注入 ✅)

`main.py:1242-1310` → 用户点击报告 workbench 的「生成」按钮 → `WordBuilder` 构建 docx。

调用链：
```
main.py:1277  ChartBundle.from_providers(self)     ← 构建 bundle (含四表 MD)
main.py:1279  gen_markdown_tables_from_bundle(...)  ← 提取 markdown 文本
main.py:1281  builder_data["sections"].append({...}) ← 注入为"数据汇总附表"段
main.py:1307  WordBuilder.build_word_report(...)     ← 构建 docx (含四表)
```

**这条路径会生成四表。**

### 路径 B — 专家组诊断 docx (表缺失 ❌)

`ui/ai_diagnosis.py:2555` → 用户点击「保存多智能体诊断」按钮 → `_build_word_document()` 构建 docx。

调用链：
```
ai_diagnosis.py:1115  _build_diagnosis_record()       ← rec["chart_data"] = bundle.to_dict() (四表 MD 在里)
ai_diagnosis.py:2534  _build_diagnosis_record()       ← 再次构建 rec (保存时)
ai_diagnosis.py:2555  _build_word_document(rec, ...)  ← 构建 docx
```

`_build_word_document` 使用 `rec` 的字段：

| rec 键 | 读取位置 | 用途 |
|--------|---------|------|
| `timestamp` | line 2892 | 文档日期 |
| `backend`, `model` | line 2901 | 元信息表 |
| `selected_sources` | line 2902 | 元信息表 |
| `data_source_snapshot` | line 2904 | 元信息表 |
| `diag` (= `rec["diag"]`) | line 2942-3010 | 数据质量表 + 传感器分析 |
| `kb_hits` | line 3030-3047 | KB 规则表 |
| `ma` (= `rec["multi_agent"]`) | line 3051-3074 | 数据科学家分析 + 审核顾问意见 |
| `chart_data` | **从不读取** | — |

**`rec["chart_data"]` 零引用。四张表 MD 在诊断记录里，但 docx 构建器不看它。**

---

## §4 两个 worker 是否混淆 — 是，表注入加错了 path

### 对照表

| 维度 | 报告 worker (路径 A) | 专家组诊断 docx (路径 B) |
|------|---------------------|------------------------|
| 入口函数 | `main.py:1242` `_on_gen_report()` | `ai_diagnosis.py:2525` `_save_diagnosis("multi")` |
| docx 构建 | `word_builder.py` → `WordBuilder` | `ai_diagnosis.py:2865` `_build_word_document()` |
| ChartBundle.from_providers | ✅ 调了 (`main.py:1278`) | ✅ 调了 (`ai_diagnosis.py:1159` 在 `_build_diagnosis_record`) |
| gen_markdown_tables | ✅ 调了 (`main.py:1279`) | ❌ **没调** |
| 四表 MD 是否在 building 数据里 | ✅ (sections append) | rec["chart_data"] 里有，但构建器不读 |
| 真机跑的路径 | — | ✅ (专家组诊断 docx) |

### 断点

**表注入代码在 `main.py:1277-1296` — 这是「报告 workbench」的路径 A。元器跑的是「专家组诊断 docx」的路径 B (`ai_diagnosis.py:_build_word_document`)。**

两条路径物理隔离：
- 路径 A 用 `WordBuilder` (`py/report_builder/word_builder.py`)
- 路径 B 用 `AiDiagnosisWidget._build_word_document()` (`ui/ai_diagnosis.py:2865`)，自建 `python-docx` Document，不经过 `WordBuilder`

**表注入只加在了路径 A，路径 B 根本没加。**

---

## §5 chart_data 是否携带表

### 写入端

`ai_diagnosis.py:1158-1173` — `_build_diagnosis_record()`：

```python
from core.chart_bundle import ChartBundle
bundle = ChartBundle.from_providers(main_win)          # line 1159 — 填充四表 MD
print(f"[DIAG] _build_diagnosis_record ChartBundle.from_providers: "
      f"tables(grade={bool(bundle.grade_table_md)}, "
      f"anomaly={bool(bundle.anomaly_table_md)}, "
      f"ke={bool(bundle.ke_table_md)}, "
      f"dec={bool(bundle.decoupling_table_md)})")      # line 1163-1166 — 日志证四表非空

if (bundle.series or bundle.compare_sources or bundle.calib_ref
        or bundle.grade_table_md or bundle.anomaly_table_md
        or bundle.ke_table_md or bundle.decoupling_table_md):
    rec["chart_data"] = bundle.to_dict()                # line 1170 — ★ 写入 record
```

`bundle.to_dict()` → `dataclasses.asdict(self)` (`chart_bundle.py:252`) → 包含 `grade_table_md` / `anomaly_table_md` / `ke_table_md` / `decoupling_table_md`。

**`rec["chart_data"]` 中确实有四表 MD 数据。**

### 读取端

`ai_diagnosis.py:2555` — `_build_word_document(rec, kind, path)`：

全文搜索 `rec.get('chart_data')` / `chart_data`：
```
ai_diagnosis.py: 整文件仅 3 处 — 全在 _build_diagnosis_record (写入端)
_build_word_document 内: 0 命中
```

**四表生成了，记录里有，但 docx 构建器从不读 `chart_data`。断点在序列化→渲染的衔接层。**

### 日志 `chart_data_keys` 为何没有 table_md 键

`gen_charts_from_bundle` 的 `manifest_count=2` 是因为它在遍历 `cd.get('series', {})` 等字段出图，跟 table_md 字段无关。日志 `made=['calib_lin','ts_cleaning']` 是出图统计，不代表 dict 里没有 table_md 字段。

---

## 小结 — 断点位置一览

| 层级 | 文件:行号 | 状态 |
|------|-----------|------|
| 表 MD 生成 (from_providers) | `chart_bundle.py:89,156,173,201` | ✅ 正常 — 日志证非空 |
| 表 MD 整合 (gen_markdown_tables) | `chart_bundle.py:398-414` | ✅ 函数存在 |
| chart_data 序列化入 dic | `ai_diagnosis.py:1170` | ✅ 写入 `rec["chart_data"]` |
| gen_markdown_tables 调用 (路径 A) | `main.py:1277-1279` | ✅ — 但这是报告路径 |
| gen_markdown_tables 调用 (路径 B) | `ai_diagnosis.py` | ❌ **不存在** — 缺调用 |
| chart_data 读取 (路径 B) | `ai_diagnosis.py:_build_word_document` | ❌ **不读** — `rec["chart_data"]` 零引用 |
| 路径匹配 | | ❌ **表注入在 A，真机跑的是 B** |

---

## 路径B 核查（Phase 1 后）— `extract_four_tables` 注入是否真生效

**核查日期**: 2026-06-25  
**核查背景**: Phase 1 在 `ai_diagnosis.py:3077-3091` 加了 `extract_four_tables` → `_add_styled_table_docx`。但真机专家组 docx 仍无 G1/T5/S2 三表，仅见"通道|标识|评级|关键指标(迟滞/重复性)|状态判定"表。

### 1. 代码确认 — 注入代码真实存在

`ai_diagnosis.py:3077-3092`：

```python
# ── 数据汇总附表 (从 chart_data 提取四张结构化表) ──
try:
    from core.chart_bundle import extract_four_tables
    cd = rec.get('chart_data', {}) or {}
    four_tables = extract_four_tables(cd)
    if four_tables:
        doc.add_heading('数据汇总附表', level=1)
        for tbl in four_tables:
            self._add_styled_table_docx(
                doc, tbl.headers, tbl.rows,
                caption=tbl.heading,
                table_id=self._next_docx_table_id(),
            )
except Exception:
    import traceback as _tb2
    print(f"[诊断docx] 数据汇总附表注入失败: {_tb2.format_exc()}")
```

代码未被回退，`extract_four_tables` + `_add_styled_table_docx` 调用确实存在。

### 2. 触发条件 — 三段关卡逐一核查

**调用链：**
```
_save_multi()                                    ai_diagnosis.py:2522
  → _save_diagnosis("multi")                     ai_diagnosis.py:2525
    → rec = self._build_diagnosis_record()       ai_diagnosis.py:2534
    → self._build_word_document(rec, kind, path) ai_diagnosis.py:2555
      → extract_four_tables(rec["chart_data"])   ai_diagnosis.py:3079-3081
```

**关卡 1 — `rec["chart_data"]` 是否写入？** `ai_diagnosis.py:1167-1173`：

```python
if (bundle.series or bundle.compare_sources or bundle.calib_ref
        or bundle.grade_table_md or bundle.anomaly_table_md
        or bundle.ke_table_md or bundle.decoupling_table_md):
    rec["chart_data"] = bundle.to_dict()   # line 1170
```

> 只要有数据文件加载（`bundle.series` 非空）或任一表非空，`chart_data` 即写入 record。加载了分析数据就能过。

**关卡 2 — 四表 MD 字段是否非空？** `ChartBundle.from_providers()` (`chart_bundle.py`) 各表产生条件：

| 表 | 字段 | 数据来源 | `from_providers` 位置 |
|----|------|---------|---------------------|
| G1 分级 | `grade_table_md` | `temp_page._phase_b_state["compensation"]` | `chart_bundle.py:128-156` |
| T5 解耦 | `decoupling_table_md` | `temp_page._phase_b_state["decoupling_results"]` | `chart_bundle.py:158-173` |
| S2 Ke | `ke_table_md` | `strain_page._strain_configs` | `chart_bundle.py:175-201` |
| A1 异常 | `anomaly_table_md` | `cleaning_tab_widget._anomaly_info` | `chart_bundle.py:78-91` |

> **除非分别运行了温度标定 Phase B（产出 compensation + decoupling_results）、应变标定（产出 strain_configs）、数据清洗（产出 anomaly_info），否则对应表字段为空字符串。** `extract_four_tables` 对空字符串返回 `None`/跳过——整个四个表都可能不存在。

**关卡 3 — `extract_four_tables` 返回值？** `chart_bundle.py:419-450`：

```python
def extract_four_tables(cd: dict) -> list[TableData]:
    ...
    for key, heading in [...]:
        md = (cd.get(key, "") or "").strip()
        if not md:
            continue                          # 空字符串 → 跳过
        parsed = _parse_single_md_table(md)
        if parsed is None:
            continue                          # 解析失败 → 跳过
        headers, rows = parsed
        if not headers or not rows:
            continue                          # 无数据行 → 跳过
        tables.append(...)
    return tables
```

> 若 G1/T5/S2 三表字段均为空字符串（未运行标定），`four_tables` 返回 `[]` → `if four_tables:` (`ai_diagnosis.py:3082`) 为 `False` → **"数据汇总附表"段整个不出现**，静默通过。

### 3. "通道|标识|评级|关键指标"表溯源

此表**不是** `extract_four_tables` 的产物。G1 表头是 `["传感器", "残余σ (%FS)", "迟滞 (%FS)", "评级", "通过", "原因"]` (`chart_bundle.py:130`)，T5 表头是 `["传感器", "ε_mean (με)", "ε_std (με)", "ε_range (με)", "评级"]` (`chart_bundle.py:161`)，S2 表头是 `["传感器", "模式", "Ke1 (pm/με)", "Ke2 (pm/με)", "R²", "备注"]` (`chart_bundle.py:177`)。都不匹配。

代码全仓搜索 `"通道"` / `"关键指标"` / `"状态判定"` — **零命中**。此表来源是 AI 模型的自由文本输出：

- **入口**: `_build_word_document` → `data_scientist_text` (`rec['multi_agent']['data_scientist_text']`) → `_render_markdown_blocks_to_docx(doc, ds_text)` (`ai_diagnosis.py:3067`)
- **生成侧**: `py/multi_agent.py:305` `DATA_SCIENTIST_PROMPT_TPL` — 数据科学家模型收到传感器数据后，自由撰写的分析报告中**自行编了**一张 markdown 表格，使用了 `| 通道 | 标识 | 评级 | 关键指标(...) | 状态判定 |` 这样的表头
- **渲染侧**: `ai_diagnosis.py:2794-2815` — `_render_markdown_blocks_to_docx` 解析 AI 输出的 `|...|` 行 → 调 `_add_styled_table_docx(doc, headers, rows)`（**无** caption/table_id 参数 → 无表号表题）

换句话说：这张表是 LLM 自作主张画出来的，不是代码从标定数据中提取的。它的存在**可能**说明 AI 模型从数据中归纳出了类似标定的信息，但数据和表头都是 AI 的"创作"，不是代码输出的结构化标定结果。

### 4. 入口确认 — `_build_word_document` 确实是 docx 出口

| 按钮 | handler | 入口函数 | 
|------|---------|---------|
| "保存 AI 诊断" | `_save_ai()` (`ai_diagnosis.py:2519`) | `_save_diagnosis("ai")` → `_build_word_document(rec, "ai", path)` (`2555`) |
| "保存专家组诊断" | `_save_multi()` (`ai_diagnosis.py:2522`) | `_save_diagnosis("multi")` → `_build_word_document(rec, "multi", path)` (`2555`) |

两个按钮都走到同一个 `_build_word_document`。3077-3091 在函数尾部（页码段之前），两个按钮都会执行到。路径匹配无误。

### 5. 结论

| # | 发现 | 文件:行号 |
|---|------|-----------|
| 1 | 注入代码确在、确在执行路径上 | `ai_diagnosis.py:3077-3092` |
| 2 | **四表 MD 字段依赖标定/清洗管线产出数据** — 若用户仅运行专家组诊断而不跑标定，G1/T5/S2 三表均为空字符串 | `chart_bundle.py:128-201` |
| 3 | `extract_four_tables` 空表静默跳过，`if four_tables:` 为 False → 无 Heading、无表、无报错 | `ai_diagnosis.py:3082` |
| 4 | 异常表 (A1) 依赖 `cleaning_tab_widget._anomaly_info` — 若未运行数据清洗，也为空 | `chart_bundle.py:78-91` |
| 5 | 所见"通道\|标识\|评级\|关键指标"表是 **AI Data Scientist 自由生成的 markdown 表格**，非 extract_four_tables 产物 | `py/multi_agent.py:305` → `ai_diagnosis.py:3067` → `2794-2815` |
| 6 | `except Exception` 静默吞异常（仅 print），若渲染出错无 UI 告警 | `ai_diagnosis.py:3090-3092` |

**根因**: `ChartBundle.from_providers()` 的四表字段填充，依赖用户在标定子页/清洗子页完成操作后的状态。若只跑诊断不跑标定 → `chart_data` 里有 `series` 等时序数据、但 `grade_table_md`/`ke_table_md`/`decoupling_table_md` 全为空 → `extract_four_tables` 返回 `[]` → docx 无"数据汇总附表"段。AI 模型在诊断文本中自行生成的那张"通道\|标识\|评级"表是 LLM 的创作输出，不是代码从标定管线提取的结构化数据。

---

## 路径B空表溯源（2026-06-25 补充核查）

**真机证实**: 140821 (AI诊断) 和 141127 (多智能体) 两份 docx 均无"数据汇总附表"段、无 G1/T5/S2 三表。141127 的 table4 "通道|标识|核心指标|状态判定"是 LLM 自编表（上节第 5 条已定性）。

### 1. 四表字段填充前置条件 — 精确判空逻辑

每个表的 MD 字段填充，取决于 `from_providers()` 能否从主窗口子页读到**非空**的标定/清洗状态 dict：

| 表 | 字段 | 判空逻辑 | 数据源 | UI 子页 |
|----|------|---------|--------|---------|
| **G1 分级** | `grade_table_md` | `if comp:` (`chart_bundle.py:131`) — `comp = pb.get('compensation') or {}` (`:128`)。空 dict → 跳过 | `calibration_tab_widget.temp_page._phase_b_state["compensation"]` | **温度标定 → Phase B 运行完成** |
| **T5 解耦** | `decoupling_table_md` | `if dec:` (`chart_bundle.py:162`) — `dec = pb.get('decoupling_results') or {}` (`:161`)。空 dict → 跳过 | `calibration_tab_widget.temp_page._phase_b_state["decoupling_results"]` | **温度标定 → Phase B 运行完成（解耦）** |
| **S2 Ke** | `ke_table_md` | `if _strain_cfgs:` (`chart_bundle.py:178`) — `_strain_cfgs` 来自 `strain_page._strain_configs` (`:120`)。空 dict → 跳过 | `calibration_tab_widget.strain_page._strain_configs` | **应变标定 → 传感器配置完成（含 ke_results）** |
| **A1 异常** | `anomaly_table_md` | `if anomaly_info:` (`chart_bundle.py:81`) — `anomaly_info` 来自 `cleaning_tab_widget._anomaly_info` (`:80`)。空 dict → 跳过 | `cleaning_tab_widget._anomaly_info` | **数据清洗 → 异常检测运行完成** |

**四个字段互相独立**：任一为空时仅该表省略，不影响其他三表。`extract_four_tables` 的 for 循环对每个 key 独立判空。

**`_build_diagnosis_record` 写入 `rec["chart_data"]` 的判空 (`ai_diagnosis.py:1167-1173`) 用的是 OR 连接**：只要 `bundle.series`（有分析数据加载）或任一表非空，整个 `chart_data` 就进 record。所以即使四表全空，只要加载了分析数据文件，`rec["chart_data"]` 仍然存在——只是内部的 `grade_table_md` 等四个字段是 `""`。

### 2. 成功与失败 — provider 计数差

**成功 (路径A 134609)**: 日志打印 `compensation_n=6, decoupling_n=6, strain_configs_n=6`，三表 `grade=True,ke=True,dec=True`。→ 用户在温度标定页跑了 Phase B（6 个传感器的 compensation + decoupling），在应变标定页配置了 6 个传感器。

**失败 (路径B 140821/141127)**: `from_providers` 日志中 `calibration_tab_widget` 打印的 `compensation_n` / `decoupling_n` / `strain_configs_n` 为 0（标定页无状态）。→ 用户只做了"加载分析数据 → 运行诊断 → 保存 docx"，没有进标定子页操作。

### 3. 触发四表的前置操作清单

要让 `extract_four_tables` 在路径B docx 中产出表，用户必须在运行诊断保存**之前**完成：

| 操作 | 产出表 | 具体步骤 |
|------|--------|---------|
| ① 温度标定 → Phase A 加载 | — (前置) | 标定页 → 温度标定 → 加载波长文件、指派列角色 |
| ② 温度标定 → Phase B 运行 | **G1 分级 + T5 解耦** | 点击"运行解耦"→ PhaseBWorker 产出 `decoupling_results` → 补偿流水线产出 `compensation`（含 grade） |
| ③ 应变标定 → 加载读数 + 配置 | **S2 Ke 汇总** | 标定页 → 应变标定 → 加载读数文件、填 Ke 表 → 自动计算 → `_strain_configs` 含 `ke_results` |
| ④ 数据清洗 → 异常检测 | **A1 异常** | 清洗页 → 加载数据 → 运行异常检测 → `_anomaly_info` 非空 |

> **① 和 ② 必须在同一次会话中完成**（`_phase_b_state` 是 `temp_page` 实例属性，不持久化到磁盘）。关闭标定页或退出程序后状态丢失。
> **③** `_strain_configs` 同样是 `strain_page` 实例属性，会话级。
> **④** `_anomaly_info` 是 `cleaning_tab_widget` 实例属性，会话级。

**操作顺序要求**：标定/清洗必须在诊断保存之前完成（`from_providers` 在 `_build_diagnosis_record` 中被调用，读取的是当前时刻的主窗口状态）。

### 4. AI 诊断（单）路径的四表注入 — 确认

`_build_word_document` 中四表注入代码 (`ai_diagnosis.py:3077-3091`) **不在** `if kind == "multi":` 块内。

```python
# Line 3050:    # ── 多智能体段 ──
# Line 3051:    if kind == "multi":         ← 仅限多智能体专属段
# Line 3067:        self._render_markdown_blocks_to_docx(doc, ds_text)
# Line 3075:        doc.add_paragraph()
# Line 3076:                                ← if 块在此结束
# Line 3077:    # ── 数据汇总附表...         ← 对 "ai" 和 "multi" 均执行
```

`kind="ai"` 也会走到 3077-3091。140821 (AI诊断) 没有四表的原因是数据缺（四表字段空），不是代码路径缺失。

### 5. 静默跳过评估

`ai_diagnosis.py:3080-3092`：当 `four_tables` 为空列表时：

```python
cd = rec.get('chart_data', {}) or {}
four_tables = extract_four_tables(cd)   # → []
if four_tables:                          # False — 整段跳过
    ...
except Exception:
    print(...)                           # 仅在此被触发
```

**当前行为**：四表全空 → `[]` → `if four_tables:` False → **无 heading、无表、无日志、无 UI 提示**。用户在 docx 中看不到"数据汇总附表"段，也不知道它为什么没出现。

**`except Exception` 仅捕获渲染异常**（如 `_add_styled_table_docx` 内部 `add_table` 出错），**不捕获"无数据"场景**。`four_tables = []` 不是异常，不会进 except。

**no-silent-failure 评估**：依 CLAUDE.md 纪律 8（"AI 调用链的三条失败路径都必须显式到达用户"），严格来说这不属于 AI 调用失败——是数据源为空。但依同一纪律的精神（禁止静默失败），当四表数据应为非空但实际为空时（如用户跑了标定但状态没进 record），用户无法从 docx 外观分辨"没跑标定所以没表"和"代码 bug 导致表丢失"。**建议增加可见提示**："标定数据不可用，跳过数据汇总附表"（不阻塞生成，仅告知），满足 fail-loud 原则。
