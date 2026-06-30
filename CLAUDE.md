# DataProcessor Pro 2.0 - 系统上下文与规则

本文件设定了 Claude Code 的绝对约束条件。在提出任何代码修改建议前，请务必仔细阅读并严格遵守。

## ⚠️ 关键工程纪律（绝对最高优先级）

**1. Pandas 2.0+ 严格类型安全**
- **严禁**使用切片就地赋值（in-place slice assignment）将对象（object）或字符串直接插入到底层为 float64 的 DataFrame 块中（例如：绝对禁止写 `part[:] = part.astype(object)`）。
- **必须**使用全局类型提升后进行”乐高式”拼接的安全写法：`df = df.astype(object)` -> `pd.concat([top, new_row, bottom])`。
- **禁止对 DataFrame/Series 直接做布尔判断**：`if df` / `if not df` / `if series` 都触发 `ValueError`（多元素歧义）。判空用 `df is None` 或 `df.empty`；判非空用 `df is not None and not df.empty`。

**2. PyQt6 状态与窗口层级穿透**
- **严禁**使用 `self.parent()` 来获取全局或跨标签页的变量（如 `sensor_results`），因为这在 `QTabWidget` 的嵌套结构中会导致指针断裂，拿不到主窗口数据。
- **必须**使用 `self.window()` 来跨模块访问顶层主窗口的缓存数据或属性。

**3. UI 与后端数据对齐（“卸妆”法则）**
- UI 界面的复选框或标签通常包含用于美观显示的单位后缀（如 `A1_应变(με)`）。
- 在将这些字符串作为键（keys）去切片后端的 Pandas DataFrame 或字典**之前**，**必须**使用正则表达式彻底剥离中英文括号及单位：`re.sub(r'[\(（].*?[\)）]', '', name).strip()`。

**4. 强制数据类型防火墙（光纤数据 vs 其它数据）**
- 必须在 `ui/analysis_tab.py` 中保持极其严格的 IF-ELSE 物理隔离逻辑：
  - **光纤数据 (`is_fiber_data`):** 基于波长差，依赖 `SensorSystem.calculate()` 计算，允许用户在“原始数据”和“物理量”之间自由切换。
  - **通用/其它数据 (TXT/CSV):** 属于外部已算好的数据。**必须**将下拉框锁死在“物理量”并禁用，直接绕过计算逻辑，强制读取带有用户暗号的 `annotated_cols`。


**5. 修改前必查 CodeGraph callers（硬纪律）**
- 改任何函数签名/返回值/参数/行为前，**必须**先用 codegraph_callers 确认所有调用点。
- **禁止只改定义不查引用**。

**6. Pyright 零红线提交（硬纪律）**
- 任何 `.py` 文件编辑后、提交前，**必须**跑 `pyright <changed_files>`，红错全清才能 commit。
- 若 pre-commit hook 已启用，`git commit` 会自动 enforce 这条。

**7. 类型忽略注解使用规范（硬纪律）**
- **禁止**使用整体跳过的 `# type: ignore`（无具体规则名），它会吞掉该行所有未来真正的类型错误。
- 只准用 `# pyright: ignore[<具体rule>]` 并**必须**在同一行附原因注释。例：
  ```python
  _ = not df  # pyright: ignore[reportGeneralTypeIssues]  # intentional: testing DataFrame __bool__ raises
  ```
- 文件级关 rule（`# pyright: reportGeneralTypeIssues=false`）**仅限**第三方库 stub 已知误报（如 python-pptx/python-docx 构造器被误识为函数签名），**必须**附注释说明原因。
- **ignore 是最后手段，不是第一手段**。优先用 `cast()` / `field(default_factory=...)` / 补字段 / `str()` 转换等类型收窄手段让 pyright 满意。

**8. LLM 边界禁止静默失败 — 输出必须经 Schema 校验（硬纪律）**
- `AIClient.generate()` / `generate_with_tools()` 失败**必须**抛 `AIClientError` 子类，**严禁**返回空字符串。
- 所有 LLM 输出的结构化数据**必须**经 Pydantic `model_validate_json()` 或等价的显式 schema 校验；校验失败**必须**抛 `ReportSchemaError`（含 `missing_fields`/`type_errors`/`raw_text` 诊断），**严禁** `json.loads` 静默降级为 `raw[:500]` 或空字段。
- 审查裁决（auditor node）**必须**产出结构化 `AuditVerdict`（`{"verdict":"pass"|"reject","reasons":[...],"required_fixes":[...]}`），**严禁**字符串匹配 `"通过"` / `"驳回"`。
- AI 调用链的三条失败路径都必须显式到达用户：`AIClientError` → `ReportWorker.error` → `QMessageBox`；`ReportSchemaError` → 同上；`AuditVerdict.reject` × 3 → `task_status="failed"` → UI 显式呈现。

**9. 配置/字典取值缺键 + None 双守卫（硬纪律）**
- 若字典键可能存在但值为 `None`（如 `self._config["anchored_grating"] = None`），**必须**用 `d.get(key) or default` 而非 `d.get(key, default)`。
- `d.get(key, default)` 的 default 只在**缺键**时生效，不挡 `None` 值。
- 例 `(self._config.get("anchored_grating") or 2) - 1` — 同时挡缺键和 None。
- 同样的陷阱也存在于 `str(dict).get(...)` — 关了 `reportAttributeAccessIssue` 让这类错误静默通过（已改回 warning，见 `pyproject.toml` 行 15-17 注释）。

---

## 🏗 项目拓扑与架构

- **main.py (`~2294 行`):** 主窗口骨架 + 标签页装配 + 信号连线 + `DataProcessorWindow(QMainWindow)`。
  已从原始 3240 行拆出纯函数层和 Qt 对话框（详见下方重构记录），保留 16 个 3-5 行委托壳调用 `utils/` 中的实际实现。
  - **解析与校验层 (`utils/`):** 纯函数模块，零 Qt 依赖。
    - `file_parser.py` — ENLIGHT/Hyperion Peaks/Sensors/Legacy 统一解析 + 模板检测 (`auto_detect_template`) + 文件头读取 + 自定义模板持久化。
    - `parse_validation.py` — **解析后硬门禁**（`validate_parsed_data`, `ParseValidationError`）。strict 模式 (ENLIGHT/Hyperion) 校验 Timestamp 列、FBG 波长区间 1400-1700nm、数值纯度、元数据泄漏；lenient 模式 (通用 TXT/CSV/XLSX) 只做空表/行数/元数据泄漏基础校验。`parse_enlight_file` 的三分支和 `parse_file` 的通用路径均在返回前调用。
    - `annotation_utils.py` — 暗号行构建 (`build_annotation_row`, `insert_blank_row`, `apply_annotation_row`)、暗号扫描与列角色解析 (`extract_annotation_info`)、分析数据提取 (`get_analysis_data`, `build_sensor_results_dict`)、波长列判定 (`is_wave_col`, 1400-1700nm)。
    - `column_utils.py` — 列名清洗 (`strip_bracket_units`/`clean_dict_keys`, 即"卸妆"正则)、行分类 (`is_data_row`)、FBG 列检测 (`detect_fbg_columns`)。
    - `dataframe_utils.py` — DataFrame 行定位 (`find_first_timestamp_row`)。
    - `data_cleaning.py` — 数据清洗 (`clean_data`, `detect_anomalies`, `fill_missing`)。
- **AI 与报告模块 (`core/`):**
    - `ai_client.py` — `AIClient` 单例 (OpenAI 兼容接口)；`generate()` / `generate_with_tools()` 失败抛 `AIClientError` 子类（不再返回 `""`）；`generate_with_retry()` 指数退避仅重试 `retryable=True` 的错误；`generate_structured()` 调用 LLM + Pydantic schema 校验。
    - `ai_errors.py` — 类型化异常层级：`AIClientAuthError`(401)/`RequestError`(400)/`RateLimitError`(429,retryable)/`ServerError`(5xx,retryable)/`TimeoutError`(retryable)/`EmptyResponseError`/`NotConfiguredError`/`ReportSchemaError`(含 missing_fields/type_errors 诊断) + `classify_openai_error()` 分类工厂。
    - `report_models.py` — Pydantic `WordReport`/`WordSection`/`PPTReport`/`PPTSlide` — report_engine 和渲染器的唯一数据契约。
    - `report_engine.py` — `generate_outline`(人审 Markdown 不变) + `generate_structured_report` → 逐节 Pydantic 校验 (Phase 2)。
- **后端引擎 (`dp_engine/`):** 信号处理 (`analyzer.py`)、数学公式解析 (`formula.py`)，Word/PPT 报告引擎 (`report_builder/`)、LangGraph 多智能体审查系统 (`multi_agent.py` — Phase 3: Auditor 产出结构化 `AuditVerdict`，第 3 次驳回 → `task_status="failed"` 显式失败终态)。
- **标定引擎 (`dp_engine/calibration/`):** 温度标定 (平台检测 + KMeans 映射 + 灵敏度回归 + 解耦诊断)、应变标定 (手填表驱动 + 灵敏度/线性度/重复性/迟滞/双栅分析)、Excel 导出。
- **标定 UI (`ui/calibration_tab.py`):** 温度标定子页 (文件加载/列角色指派/设定温度/诊断图/Word报告) + 应变标定子页 (动态填表/自动计算/指标图表/系数闭环)。
  - **对话框 (`ui/` 独立文件):**
    - `fbg_edit_dialog.py` — FBG 编辑/添加对话框。
    - `sensor_edit_dialog.py` — 传感器编辑/添加对话框 (含解耦矩阵配置面板)。
    - `ai_model_config_dialog.py` — AI 模型 API 配置对话框。
    - `report_worker.py` — 通用后台 `ReportWorker(QThread)`，在子线程执行 AI/渲染任务。
    - `global_parameter_dialog.py` — 全局参数编辑对话框（"笨组件"模式，纯信号驱动）。
- **标定图表工具 (`core/tools/calibration_chart_tool.py`):** 封装标定图表渲染函数 (回归图/诊断四联图/应变曲线/指标柱状图)，供报告引擎调用。
- **核心数据流向:** `文件解析 -> _auto_populate_fbgs (正则提取 FBG_) -> SensorSystem.calculate (公式计算) -> 异常清洗/FFT分析 -> 导出报告`。
- **机密隔离:** `ai_models_config.json` 文件内含有敏感的 API 密钥。**严禁**在对话中打印其内容或将其提交入库（commit）。

---

## 🧠 代理行为准则（AI 工作流规范）

1. **优先使用 CodeGraph 符号搜索（硬纪律）:** 
   - **禁止**盲目的纯文本 `grep` 匹配。每次查符号、找挂载点、定位函数定义/调用关系的**第一步必须是 `codegraph_context`**，它在一个调用中组合了 search + node + callers + callees。
   - `codegraph_search` → 按名称找符号位置。
   - `codegraph_context` → 查"某功能/某区域怎么工作"的首选工具（取代多轮 grep + Read 循环）。
   - `codegraph_trace` → "X 怎么到达 Y"的完整调用路径。
   - `codegraph_callers` / `codegraph_callees` → 查谁调用了它 / 它调用了谁。
   - `codegraph_impact` → 改一个符号前评估影响半径。
   - `codegraph_explore` → 批量查看多个相关符号的源码（取代多个 Read 调用）。
   - 只有在 CodeGraph 无法覆盖（如索引尚未就绪的 .txt / .md / .json 文件）时才降级到 Grep/Read。
   - **修改前必查 callers**：改任何函数的签名或行为前，必须用 `codegraph_callers` 确认所有调用点。
2. **派生子代理（Sub-agent）探路:** 在调试复杂的 Pandas 索引对齐或切片 Bug 时，不要直接在主会话里盲改 `main.py`。请先派生一个子代理（Sub-agent）写个独立的测试脚本，摸清底层的 `.index` 行为后，再在主会话中给出最终代码。
3. **公式求值统一走 `dp_engine/formula.py` 的 asteval 引擎:** 所有公式求值必须通过 `dp_engine.formula.calculate()`（基于 `asteval.Interpreter` 安全向量化求值），**禁止再用 `eval` 或字符串替换**。参数与列变量均作为 symtable 注入，彻底消除 k1/k10 误匹配风险。

---

## 🔧 标定模块约定 (v2.1 新增)

1. **标定系数闭环仅限当前会话:** 通过「应用标定系数到当前传感器」按钮注入的实测 k 系数，**仅写入当前 `sensor_system` 实例的 `Sensor.constants`**（会话级）。**严禁**写回全局 `Sensor.TYPE_PARAMS` 或持久化为默认值。
2. **标定模块不得改动暗号标注机制:** 标定模块有自己独立的列角色指派 UI（`col_table`），**绝不插入标注行**（annotation row），**不复用** `analysis_tab` 的 `_annotated_cols` 解析逻辑。
3. **温度标定独立加载文件:** 温度标定子页加载的连续波长文件**不写入**主窗口的 `current_data`，在独立的 DataFrame 中完成全流程。
4. **解耦矩阵与 `core/models.py` 公式一致:** `dp_engine/calibration/temperature_calibration.decouple()` 的 2×2 矩阵求逆公式与 `SensorSystem._evaluate_decoupling()` **逐元素等价**。
5. **对话框状态对称恢复:** 任何对话框 `close/accept()` 时写入主 Tab 状态 dict 的数据，**必须在 `__init__` 末尾完整 restore 到 UI**（含表格内容、表格样式、单元格背景色、状态条文本）。禁止只存不取或只取部分。Phase A/B 必须同构——修一边必须同时检查另一边。
6. **对称 Phase 改动同步:** 凡涉及"Phase A 和 Phase B 对称功能"的改动（状态恢复、对话框形态、暗号显示、结果渲染），**写完一边立即 LSP grep 对偶位置检查另一边是否有相同路径**。禁止"Phase A 修了但 Phase B 漏了"的反复 bug。
7. **新对话框/流程必须配 smoke test:** 每加一个对话框或完整流程，**必须**配一个 happy-path roundtrip smoke test（构造完整状态→执行→assert no exception）。比单元测试便宜但拦截 80% 的回归。参照 `tests/test_phase_b_dialog.py::test_full_temp_calibration_roundtrip_smoke`。
8. **对话框写回必须覆盖全部关闭路径:** 任何对话框的状态写回必须在 `accept()` + `reject()` 双覆写中触发（或 `closeEvent` 统一拦截）。**禁止只绑到单一按钮的 click handler**。Qt 的四条关闭路径（确定按钮 / Esc / X / Alt+F4）必须全部触发写回。每个对话框配 3 条 close-path 回归测试 (accept / reject Esc / reject X)。参照 `tests/test_phase_a_dialog.py::TestPhaseAWriteback`。

## 🚧 已知待办 (v2.1 未完成)

- **main.py 重构状态 (Phase 1 ✅ + Phase 3 ✅, Phase 2 缓做):**
  - Phase 1 — 16 个纯函数已提取至 `utils/` 模块 (`annotation_utils`, `column_utils`, `dataframe_utils`, `file_parser`)，main.py 保留委托壳（3-5 行/个）调用 `utils.*`，签名不变。
  - Phase 3 — 4 个嵌入的 QDialog/QThread 类已提取至 `ui/` 独立文件 (`fbg_edit_dialog`, `sensor_edit_dialog`, `ai_model_config_dialog`, `report_worker`)。
  - Phase 2 — Mixin 多继承方案评估后缓做：按职责分拆 DataTabMixin/SensorFbgMixin/ReportMixin 等的收益主要是文件组织而非解耦，多继承+Protocol 维护成本需权衡。ConfigMixin 的方向是改走 AppState 序列化(SSOT)而非横切全属性。
  - main.py 中 16 个委托壳可逐步去壳（调用点直指 utils 函数），但非紧急，不阻塞其他功能。
- **test_run.py**: 需要 GUI 环境才能运行，无头环境（CI）下会崩溃。
- **pytest 预存失败 14f+1e**: 全部来自前置提交 79b9b10 / 7fad573 / a7b25b0。完整清单见 `wiki_vault/diagnoses/pytest预存失败清单_20260625.md`。每轮 pytest 跑完对照清单确认无新增回归。
- **积压（不阻塞）**:
  - `dp_engine/report_builder/flow_controller.py` — `ReportFlowController.generate_outline()` / `expand_section()` 当前无外部调用者，是死代码；若被调用会裸抛异常。建议或删或补兜底。
  - `AIClient.generate_structured()` 当前走 Pydantic 校验 + 1 次回喂修复，在线模型（DeepSeek/GPT）可切原生 `response_format=json_schema` 强约束省掉重试往返。不必现在做。
  - `dp_engine/agent_worker.py` — `DeepSeekAgentWorker` 当前无 Python 调用者（仅文档引用）；有 `except Exception → error_signal` 守卫。若未来启用需确认 caller 同时绑定 `error_signal`。
  - `parse_enlight_sensors` — 当文件含中段第二条暗号行时，`_looks_like_annotation_row` 漏检（`'nan'` 被 float 解析为 NaN → 判定为数据行）。当前显示层 `_insert_annotation_row` 事后 drop 归一 row 0，能用。可考虑解析层一并剔 + 抽查 drop 点附近数据连续性。
  - `classify_openai_error` 在 `core/ai_errors.py:190` 用 `"connection" in msg_lower` 匹配，导致 `ConnectError`（`[WinError 10061]` 连接拒绝，本地 llama.cpp 未运行）被误分类为 `AIClientTimeoutError`，提示文案误导用户去调 timeout 而非启动服务。应拆分 connection-refused 为独立异常（如 `AIClientConnectionError`）。仅登记，不修。
  - **四表注入 docx — 三条已知尾巴 (2026-06-25 封板)**:
    - `A1 异常表非空验证`: 迄今所有真机数据 `anomaly=False`，A1 异常表"有异常时正常出现"未真机验（只验了空表降级省略）。待有异常数据时补。
    - `空表 fail-loud`: 四表全空时 `ai_diagnosis.py:3082` `if four_tables:` → `False` → 整段静默跳过（无 heading、无表、无日志、无 UI 提示）。建议加可见提示（如 `doc.add_paragraph("标定数据不可用，跳过数据汇总附表。")`），满足 fail-loud 原则。低优先级。
    - `[A1]/[B1]` 等预存调试打印: `ui/calibration_tab.py:1448,1450,2114,2123,2128,2144` 共 6 处 `print("[A1]..."/"[B1]..."...)`（来自 `7fad573` 标定 Phase A/B 调试，非本批）。待清理，改为 `logging.debug()` 或删除。
  - **报告 prompt 领域约束 (甲-1)** — 砍掉。经真机实证：加载诊断记录后 `_build_context` 已含诊断摘要，大纲自然锚定 FBG 领域，不再跑题为 IT 运维报告。硬拦 (甲-2) 已堵死无数据路径。保留观察项：若出现"已加载数据仍跑题"，重启甲-1。
  - **PPT 报告未选模板 Padding not found at ''**: `ppt_builder.py:274` `build_ppt_report()` 缺空模板 fallback — `Presentation('')` 直接抛错。同文件 `build()` (`line 54`) 有正确的 `if path.exists() else Presentation()` guard，`build_ppt_report()` 漏抄。Word 路径 `word_builder.py:567` 有 `Document(path) if path else Document()`。修复最小：`ppt_builder.py:274` 加同构 guard。评估见 `docs/PPT模板缺失溯源.md`。
  - **✅ 基建: `py/` → `dp_engine/` 目录改名，pytest 收集冲突已根治 (2026-06-26)**。原 `py/` 目录撞 PyPI `py` 包名（pytest 传递依赖）→ `import py` 时遮蔽导致 `AttributeError: module 'py' has no attribute 'path'`。已 `git mv py dp_engine` + 全仓 import 替换（36 文件 × 153 行）。`run_tests.py` 救火脚本同步退役。`python -m pytest tests/` 现在可正常收集。

## 🔧 表观应变补偿 + 出厂指标 + 评级 (Phase 1-3b 新增)

**10. 补偿流水线 (Phase 1-3b，硬纪律):**
- Phase B 解耦完成后**立即**运行补偿流水线: `build model (lut|poly) → evaluate_compensation (按形式重算残差) → grade_sensor (%FS 阈值)`。产物 (`compensation_model` + `metrics` + `grade`) 持久化到 `_phase_b_state["compensation"]` / `ProjectConfig.temperature.compensation`。原始 ε/T 时序**不持久化**（从 transient `_last_result["sensors"]` 消费后丢弃）。
- **补偿形式可配**: `comp_form`="lut"|"poly", `poly_order`=2|3|4。配置走 `_phase_b_state["comp_form"]` / `_phase_b_state["poly_order"]` (SSOT)，可单传感器覆盖。用户通过 `compare_compensation_forms()` 选型，残差按所发形式诚实重算。
- **越界钳位 + oob fail-loud**: `apply_compensation_model` 两种形式一致：越界钳位到端点值 + `oob_mask` 置位。**多项式严禁外推** (4 阶在标定区外发散极快)。调用方**必须**检查 `np.any(oob_mask)` 并弹 UI 告警。
- **废品拦截线**: `hysteresis_max_pct_fs > 5%FS` → `grade_sensor()` 返回 `grade="FAIL"`, `passed=False`。FAIL 传感器 UI 显式横幅呈现 (红色) + 补偿模型不生效。两处评级 (`_render_result_table` + `_write_state_to_main_page`) **同时**使用 `grade_sensor()`。
- **评级去橡皮图章**: 旧 `e_std<=30→优` 已替换为 `grade_sensor()` 的 %FS 阈值 (优≤1%FS, 良≤2%FS, 合格≤4%FS)。
- **单栅显式 N/A**: `single_grating=True` → `grade_sensor_na()` → `grade="N/A"`, `passed=False`。不建双栅补偿模型。
- **NaN 滞回降权**: `hysteresis_max=NaN` (未测升降支) → `evaluate_compensation` 强制 `low_confidence=True` → 评级上限 "良"，禁止判优。
- **残差按形式重算**: `evaluate_compensation(T, eps, cids, fs=1000, form="poly", poly_order=4)` → LOOCV 按指定 form/order 重建模型在留出循环上算 sigma。导出/报告的 σ 列对应所发形式。
- **意外异常 ≠ 单栅 N/A (硬纪律)**: 补偿流水线异常 (`except Exception`) → `grade="ERROR"`, `passed=False`, reason 含完整 traceback。**严禁** grade="N/A"（与单栅路径混淆）。
- **★ 阶段B 任何重计算只在 PhaseBWorker 子线程 (绝对铁律)**: 主线程/`__init__`/任何按钮 handler/`_ensure_decoupled_result` 内**禁止**同步执行解耦/补偿/LOOCV/compare。重活唯一产地: `PhaseBWorker.run()`。`_ensure_decoupled_result` 仅返回缓存结果(快速路径)或弹提示/返回 None(缺数据)，**绝不**执行任何 numpy 重算。
- **性能关键**: `_compute_noise_floor` 用 pandas `rolling().median()` 向量化 (非 Python 循环逐点 `np.median`)。`evaluate_compensation` / `compare_compensation_forms` 支持 `subsample_step` 参数 (均匀抽样)，LOOCV/建模用抽样数据，统计量 (e_std/噪声底/滞回) 始终用全量数据。实测 9.8万行: noise_floor 887ms→35ms (25×), compare_forms 2798ms→194ms (14×), LOOCV 残差偏差 <2%。
- **按钮异步模式 (硬纪律)**: 四个结果按钮按以下模式: 若 `_last_result` 有 → 调 `_do_*` 继续；若 `_last_result=None` → 设 `_pending_callback` → 调 `_on_run()` 启动 worker。`_on_done` 末尾检查 callback 并继续。`_on_error` **必须**清除 `_pending_callback` 和 `progress_label`。
- **compare_forms 按需单传感器惰性算 (硬纪律)**: `compare_compensation_forms`(3×LOOCV) **不在**主 `PhaseBWorker.run()` 内预算全量。由 `ComputeFormsCompareWorker`(独立 QThread) 按需对选中传感器计算，结果缓存到 `_forms_compare_cache`。再次打开/切换可直接复用。选型对话框含传感器 QComboBox，FAIL 传感器禁用选择。
- **结果表渲染仅从持久化标量读取 (硬纪律)**: PhaseBDialog `__init__` 状态恢复**必须**从 `_phase_b_state` 的持久化标量 (`e_std`/`e_range`/`grade`) 直接渲染，**禁止**在 `__init__` 内调用 `_ensure_decoupled_result` 或任何重算。`saved_ratings` 优先用 `compensation[s_name]["grade"]["grade"]` 而非旧 `decoupling_results[s_name]["rating"]`。
- **补偿流水线已静态化为 `_run_compensation_pipeline_static(sensors, fs_map, comp_form, poly_order)`** (模块级函数，可被子线程调用)。旧实例方法 `_run_compensation_pipeline` 已删除。
- `except: pass` 静默吞异常 → **禁止**。异常恢复必须 `traceback.print_exc()` 或等价日志。
- **选型容差带**: 三残差差 < 0.5με 或 < 0.2%FS 时判为"并列"，推荐 2 阶多项式（最简/最互操作），标注"并列，按互操作性选"，不声称"残差最小"。
- **FAIL 传感器选型拦截**: FAIL 传感器 (passed=False) 选型时显示"补偿形式选择无意义"提示，不允许选择。
- 模块位置: `utils/apparent_strain_comp.py` (LUT + poly + CompensationModel + apply_compensation_model), `utils/compensation_metrics.py` (指标/评级/循环检测/compare_compensation_forms)。测试: `tests/test_apparent_strain_comp.py` (36), `tests/test_compensation_metrics.py` (84), `tests/test_phase_b_compensation_integration.py` (45), `tests/test_phase_b_guard_regression.py` (15)。

## 🚀 常用开发命令

```bash
python main.py              # 运行主程序
python test_run.py          # 快速启动测试（仅测试 UI 加载是否崩溃）
python -c "import py_compile; py_compile.compile('main.py', doraise=True)" # 快速语法树静态检查
pyright <changed_files>                                   # 类型检查（提交前必须零红线）
python run_tests.py                                       # 运行所有测试 (公式引擎 + 标定引擎)
```

---

## 📋 经验教训 (Lessons Learned)

**11. 枚举/分类字段原样透传 (硬纪律):**
- 注入 prompt 或生成输出时，枚举字段 (`grade`="优"/"良"/"合格"/"FAIL"/"N/A"、`comp_form`="lut"/"poly"、`single_grating` 等) 必须与数值同等对待，**原样透传**，不得改写。
- 模型会悄悄软化枚举值（实测 `优 → 良`、`FAIL → 不合格`）。指示语固定 **"评级/数值一律照摘要原文，不得改写"**。
- 验收时分类字段也逐项对照，不只对数字。

**12. 判 live/dead code 看 "从入口可达性" (硬纪律):**
- **判断依据 = 能否从 `main.py` / UI 栈 / 用户可触达路径 到达**。禁止以 "被 import 了 / 引用了某 live 模块" 判 live。
- 反例：`report_word.py` 引用了 `ollama_client`（live 模块），但自身从未被 `main.py` 实例化 → 孤儿死代码（Phase 0.5 误判为 LIVE）。

**13. 验收: "绿勾/自评" 不是证据，ground-truth 对照才是 (硬纪律):**
- "测试通过 / 规则正确应用 / 数值一致" 是实现方自评，不是验收证据。
- 关键项（数值交叉核对、模型输出原文、prompt 片段）必须贴 **ground-truth 对照原文**，由审阅方判断。
- 不接受 summary / 绿勾表 代替 artifact。反例：Phase 3 验收用 "规则正确应用 ✅" 代替模型输出原文，被驳回重交。