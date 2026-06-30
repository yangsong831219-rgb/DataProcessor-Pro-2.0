# 报告 Worker 跑题溯源

真机事实：报告工作台 → Word 深度报告 → 生成大纲 → 基于大纲生成完整报告，产出正文跑偏为通用 IT 运维/服务器/安全报告，与 FBG 光纤传感标定诊断无关。

---

## 1. 大纲生成 Prompt 全文

**调用链：**
```
main.py:1044-1050  _handle_outline_generation
  → ReportWorker(generate_outline, args=(config, generate_fn))
  → core/report_engine.py:520  generate_outline(config, generate_fn)
```

**大纲 Prompt 模板 — `core/report_engine.py:59-73`：**

```
你是一个专业的技术报告写作专家。请根据以下信息，生成一份结构化的报告大纲。

报告类型: {report_type}

{context}

要求：
- 输出 Markdown 格式大纲
- 第一行为报告标题：# 标题
- 后续每行为章节标题：## 章节名
- 每个章节下列出 2-4 个关键论点，用 - 开头
- Word 报告：5-8 章，每章可详述
- PPT 报告：8-10 页，每页 2-4 个要点
- 语言：中文
- 只输出大纲，不要额外说明
```

**`{context}` 来源 — `core/report_engine.py:534`：**
```python
context, _ = _build_context(config)
```

**`{report_type}` 值：** `config['report_type']`，来自 `ReportWorkbenchWidget.get_config()` (`ui/report_workbench.py:126-132`)，此处为 `'word'` → `"Word 深度技术报告"`。

### 结论：Prompt 无 FBG/光纤传感/标定诊断领域限定

- Prompt 标题："你是一个专业的技术报告写作专家"
- **未**提到：FBG、光纤光栅、光纤传感、应变标定、温度解耦、波长解调
- `{context}` 的内容决定了大纲走向——若 context 为空或含通用 IT 内容，模型自由发挥成 IT 运维报告
- 日志中大纲标题"系统运行状态深度技术分析报告"——标题本身就是 LLM 自由生成的泛用标题，证明 prompt 无领域锚定

---

## 2. 逐 Section 正文生成 Prompt + 上下文

**调用链：**
```
main.py:1262  generate_structured_report(config, outline, report_type, generate_fn, ...)
  → core/report_engine.py:545  generate_structured_report()
    → core/report_engine.py:585  _generate_word_markdown_report()
      → core/report_engine.py:632-657  逐节: SECTION_PROMPT_WORD.format(...)
```

**Section Prompt 模板 — `core/report_engine.py:75-93`：**

```
你是一位工程技术报告撰写专家。请根据以下大纲章节，撰写该章节的完整正文内容。

报告标题: {report_title}
当前章节: {section_heading}
该章节关键论点:
{key_points}

项目资料上下文:
{project_context}

{charts_context}

正文用 Markdown 格式输出本节的完整内容。要求：
- 本节的章标题已由代码生成，你不需要再输出章标题
- 子标题从 ## 起（不要用 # 开头——# 是章标题级，你的是子节）
- 可用 **加粗**、- 列表、1. 编号列表
- 可输出 Markdown 表格（| 列1 | 列2 |），需有 |---|---| 分隔行
- {chart_instruction}
- 语言：中文。只输出 Markdown 正文，不要 JSON、不要额外说明、不要代码围栏。
```

**变量注入点 (`core/report_engine.py:645-652`)：**

| 变量 | 来源 | 是否含 FBG 标定数据 |
|------|------|-------------------|
| `report_title` | `outline_text` 第一行的 `#` 标题 | ❌ LLM 自拟 |
| `section_heading` | `_extract_sections_from_outline()` 的 `##` 行 | ❌ 来自大纲 |
| `key_points` | 大纲中对应章节的 `-` 行 | ❌ 来自大纲 |
| `project_context` | `_build_context(config)` 返回值 | ⚠️ 见下节 |
| `charts_context` | `manifest.to_llm_context(max_chars=600)` | ⚠️ 图号列表，无数值 |

### 结论：逐 Section 生成时，每节只拿到大纲标题+论点+project_context，无标定数值注入

---

## 3. 数据注入缺口

### 3.1 `_build_context` — `core/report_engine.py:210-258`

```python
def _build_context(config: dict) -> tuple[str, list[str]]:
    # ① req_file 内容读取 (line 219-224)
    # ② project_files (前5个, 每文件≤4000字符) (line 226-233)
    # ③ _diagnosis_record 诊断产物 (line 236-253)
    #    有 → _build_diagnosis_summary(diag_rec, max_chars=2500)
    #    无 → "本次生成未加载诊断数据……" 兜底文本
    # ④ 全空 → "请根据通用传感器数据分析场景生成报告" (line 255)
```

### 3.2 `_build_diagnosis_summary` — `core/report_engine.py:289-342`

**已提取** (每传感器文本摘要，≤2500 字符)：
- 传感器诊断结论 (sensor_analysis: sensor_id, status, findings, suggestions) — line 298-308
- KB 诊断规则命中 (kb_hits: id, severity, meaning, recommendation) — line 311-322
- 物理诊断 (physical_diagnosis: phenomenon, causes, actions) — line 325-331
- 多智能体报告 (multi_agent.report, 截断 800 字) — line 334-337

**缺失 — 未注入到 LLM prompt 的数据：**

| 数据 | 存储位置 | 状态 |
|------|---------|------|
| 传感器分级汇总 (G1) | `rec["chart_data"]["grade_table_md"]` | ❌ 未提取 |
| 温度解耦标量 (T5) | `rec["chart_data"]["decoupling_table_md"]` | ❌ 未提取 |
| 应变 Ke 汇总 (S2) | `rec["chart_data"]["ke_table_md"]` | ❌ 未提取 |
| 异常统计 (A1) | `rec["chart_data"]["anomaly_table_md"]` | ❌ 未提取 |
| 补偿模型指标 | `rec["chart_data"]` → compensation metrics | ❌ 未提取 |
| 时序数据 | `rec["chart_data"]["time_h"]` / `series` | ❌ 未提取 |
| 迟滞回线数据 | `rec["chart_data"]["hyst_sensors"]` | ❌ 未提取 |

### 3.3 Charts Context — `main.py:1259`

```python
charts_context = manifest.to_llm_context(max_chars=600)
```

仅注入图号列表 (`图1: 时序图(3通道)`, `图2: 标定图(...)`)，**无**标定数值、**无**传感器指标。

### 诊断：双重缺口

1. **未加载诊断记录时**：`_build_context` 降级为 `"请根据通用传感器数据分析场景生成报告"` → LLM 收到一个极度模糊的指令，只知道是"传感器"相关，但不知道是 FBG 光纤光栅应变/温度传感，模型漂移到 IT 运维传感器 (CPU 利用率 / 网络 / 服务器状态) 是典型行为。

2. **已加载诊断记录时**：`_build_diagnosis_summary` **仅注入 AI 生成的文本摘要** (sensor_analysis findings/suggestions、KB hit meaning)，**不注入**结构化标定数据 (Ke 值 / 解耦残差 / 异常点统计 / 分级评级)。LLM 看到的是"AI 说某某传感器 OK"这样的二手信息，看不到一手数值 —— 正文无法写具体标定结论。

---

## 4. 路径 A (报告 Worker) vs 路径 B (专家组诊断) 对比

| 维度 | 路径 A (报告 Worker) | 路径 B (专家组诊断 docx) |
|------|---------------------|------------------------|
| 入口 | `main.py:1172` `_handle_full_report_generation` | `ai_diagnosis.py:2865` `_build_word_document` |
| 核心机制 | **LLM 生成全部正文**（大纲 + 逐 Section AI 撰写） | **代码直写全部正文**（python-docx add_table/add_paragraph） |
| 数据来源 | `_build_context` → 仅文本摘要 (≤2500 字符) | `rec` 全量字段：`chart_data`、`diagnosis_json`、`kb_hits`、`multi_agent` |
| 标定表格 | ✅ 代码后插（Phase 1 新增，`main.py:1275-1301`） | ✅ 代码直写（Phase 1 新增，`ai_diagnosis.py:3077-3091`） |
| 传感器分级 | ❌ 仅 LLM 文本摘要，无具体评级数值 | ✅ `诊断摘要` 段：`sensor_analysis` 三线表 (`ai_diagnosis.py:2957`) |
| KB 规则命中 | ❌ 仅 LLM 文本摘要 | ✅ `1.6 命中诊断规则` 三线表 (`ai_diagnosis.py:3044`) |
| 图表 | ✅ 代码生成真图 PNG，LLM 引用图号 | ❌ 无图表 |
| 标定数值 (Ke/σ/ε) | ❌ **完全缺失** —— prompt 不含任何 Ke 值、残差 σ、评级 grade | ✅ `数据汇总附表` 四表直写 (G1/T5/S2/A1) |
| 领域锚定 | ❌ Prompt 无 FBG 字样，"传感器"可被模型解释为 IT 传感器 | ✅ 诊断上下文天然绑定 FBG（输入数据是波长/波长差/应变） |

### 路径 A 缺了什么：三项致命缺口

1. **Prompt 无领域约束**：`OUTLINE_PROMPT` (`report_engine.py:59`) 和 `SECTION_PROMPT_WORD` (`report_engine.py:75`) 两个模板单词 "FBG" / "光纤光栅" / "光纤传感" / "应变标定" 均零出现。"传感器"一词在 IT 语境中可指 CPU 温度传感器/网络流量传感器，模型自由联想为 IT 运维。

2. **`_build_context` 无标定数值**：即使加载了诊断记录，`_build_diagnosis_summary` (`report_engine.py:289`) **故意不提取** `chart_data` 中的四表 (grade/anomaly/Ke/decoupling) 和补偿模型指标。LLM 拿到的 context 只有 AI 文本摘要——"传感器 A1 状态优"这种定性结论，没有 0.05%FS / 1.2 pm/με / ε_range=123.4 με 这种可以写入正文的具体数字。

3. **文段生成全寄于 LLM**：路径 A 每一节的正文都由 `generate_fn(SECTION_PROMPT_WORD)` 产出 (`report_engine.py:657`)。路径 B 不依赖 LLM 写正文——它直接读 `rec` 的 JSON 字段渲染 docx。当 `_build_context` 只给出模糊上下文时，LLM 必然填上训练数据中最匹配的报告模板：IT 系统运行状态分析报告。

---

## 防线现状 — 未加载诊断时的代码路径与 fail-loud 落点评估

### 1. 诊断数据加载状态怎么判

**变量**：`ReportWorkbenchWidget._diagnosis_record: dict | None` — `ui/report_workbench.py:109`

```python
self._diagnosis_record: dict | None = None  # 从已存诊断 JSON 加载
```

**`None` = 未加载**。任何非 None dict = 已加载。

**UI 展示**：`set_diagnosis_record()` — `ui/report_workbench.py:397-408`

```python
def set_diagnosis_record(self, rec: dict | None) -> None:
    self._diagnosis_record = rec
    if rec:
        ... # 绿字: "诊断数据: 2026-… (N 传感器)"
    else:
        self.diag_loaded_label.setText('诊断数据: (未加载)')  # 灰字
```

**传入 config**：`get_config()` — `ui/report_workbench.py:124-132`

```python
return {
    ...
    '_diagnosis_record': self._diagnosis_record,  # 可能是 None
}
```

**消费端**：`_build_context(config)` — `core/report_engine.py:236-253`

```python
diag_rec = config.get('_diagnosis_record')
diagnosis_loaded = bool(diag_rec)  # False if None
if diag_rec and isinstance(diag_rec, dict):
    parts.append(_build_diagnosis_summary(diag_rec))
else:
    parts.append('# 诊断数据\n\n本次生成未加载诊断数据。...')  # 告知 LLM "没诊断"
```

### 2. 两个按钮的 enable 条件

| 按钮 | 初始状态 | 解锁条件 | 诊断前置校验 |
|------|---------|---------|------------|
| "📝 生成/预览报告大纲" | **始终可点** (`ui/report_workbench.py:250`) | 无前置条件 | ❌ 无 |
| "🚀 基于大纲生成完整报告" | `setEnabled(False)` (`:270`) | 大纲生成成功 → `set_outline_text()` 调 `setEnabled(True)` (`:119`) | ❌ 只依赖大纲存在 |

**关键发现**：两个按钮对"诊断记录是否加载"**均无前置校验**。用户可以不加载诊断记录，直接点大纲 → 大纲出 → 点生成报告，全程无阻断。

**`_on_full_report_requested`** (`ui/report_workbench.py:416-420`)：

```python
def _on_full_report_requested(self) -> None:
    outline = self.outline_editor.toPlainText().strip()
    if not outline:
        outline = '(空大纲 — 将使用默认模板)'
    self.full_report_requested.emit(self.get_config(), outline)
```

空大纲也能发——连大纲编辑器为空都有兜底文案，诊断加载状态完全没检查。

### 3. 兜底文案的精确落点与触发条件

两层兜底，不同触发条件：

**第一层 — "未加载诊断"告知**：`report_engine.py:242-253`

```python
else:  # diag_rec 为 None 或非 dict
    parts.append(
        '\n'.join([
            '# 诊断数据',
            '',
            '本次生成未加载诊断数据。',
            '',
            '（说明：若需要包含诊断结论，请先在「报告生成工作台」中点击'
            '「从已存诊断加载」载入诊断记录，再重新生成报告。）',
        ])
    )
```

触发条件：`config.get('_diagnosis_record')` 为 `None`/非 dict。**这只是一段告知 LLM "没诊断"的文本**，不是错误/中止。

**第二层 — "通用传感器数据分析场景"兜底**：`report_engine.py:255-256`

```python
if not parts:  # parts 列表完全为空
    parts.append('（未提供具体需求文件，请根据通用传感器数据分析场景生成报告）')
```

触发条件：`parts` 列表全空 = **没有** req_file、**没有** project_files、**且没有**诊断记录。三者全缺 → 这条兜底。

**⚠️ 修正第 1 节结论**：真机跑题那次，如果用户选了 req_file（需求文件 .docx/.txt），则 `parts` 非空 → **第二层兜底不触发**。跑题不是这句话导致的——是 `OUTLINE_PROMPT` + `SECTION_PROMPT_WORD` 模板本身无 "FBG/光纤传感/应变标定" 领域限定，LLM 把 context 里的文件内容理解成什么领域就写什么领域。文件内容是通用 IT 描述 → LLM 生成 IT 运维报告。

### 4. 大纲生成是否同源

**完全同源**。`generate_outline()` — `core/report_engine.py:534`：

```python
context, _ = _build_context(config)  # ← 和正文生成同一个函数

prompt = OUTLINE_PROMPT.format(
    report_type='...',
    context=context,     # ← 同一个 context
)
```

大纲和正文走**同一个 `_build_context`**、同一个兜底逻辑。未加载诊断时，大纲 prompt 的 context 就是 "未加载诊断 + (可能的需求文件内容)"。大纲跑题 → 正文一定跑题。

### 5. fail-loud 三个落点评估

| 落点 | 位置 | 机制 | 治本程度 | 副作用 | 推荐 |
|------|------|------|---------|--------|------|
| **(a) 按钮 enable** | `report_workbench.py:119` / `:270` | 未加载诊断 → 灰掉"生成完整报告"按钮 | 中 — 只挡正文，不挡大纲跑题 | 用户可先点大纲（也跑题），再用灰按钮困惑 | ⭐⭐ |
| **(b) 生成入口校验** | `report_workbench.py:416` `_on_full_report_requested` | 未加载 → `QMessageBox.warning("请先加载诊断记录")` → return（不 emit） | **高** — 在用户操作和 token 消耗之前中止 | 需要配合大纲按钮也加提示（大纲本身也走同一兜底，大纲已跑题后正文 block 意义减半） | ⭐⭐⭐ |
| **(c) 兜底文案抛错** | `report_engine.py:255-256` | `if not parts:` → `raise RuntimeError` | 低 — 仅 `parts` 全空时触发，选了 req_file 就走不到 | 覆盖窄：有需求文件但无诊断时，不抛错、不提示、照常跑题 | ⭐ |

**最佳落点：按钮 enable + 入口校验双保险，且大纲按钮也接入**

- **大纲按钮** (`report_workbench.py:250`)：不改 enable 条件（允许无诊断预览大纲模板结构），但应在 `_on_outline_requested` (`:413`) 检测无诊断 → 弹出确认框 "未加载诊断记录，生成的大纲将不包含诊断结论，是否继续？" → 用户确认后才 emit。
- **完整报告按钮** (`report_workbench.py:270`)：改为**始终灰**，直到**同时满足**两个条件：① 大纲已生成 ② 诊断记录已加载。即 `set_outline_text()` 中改为 `if self._diagnosis_record: self.full_report_btn.setEnabled(True)`。
- **生成入口** (`report_workbench.py:416`)：作为纵深防线，`_on_full_report_requested` 加断言 `if not self._diagnosis_record: QMessageBox.warning(...); return`。即使按钮因某种原因被绕过（如未来重构遗漏入口校验），也能在 emit 之前拦住。

**不推荐落点 (c)**：`report_engine.py:255-256` 的兜底文案 coverage 太窄——只挡"什么都没提供"的极端情况。有需求文件但无诊断 → 绕过，不生效。

**不推荐报告 worker 内部抛错**：报告生成在子线程 (`ReportWorker`)，抛错只能走 `error` 信号 → QMessageBox 展示。此时 LLM 已经被调用了、token 已经花了。最佳止损点在按钮 click handler 里——没进子线程就中止。
