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

**8. 配置/字典取值缺键 + None 双守卫（硬纪律）**
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
- **后端引擎 (`py/`):** 信号处理 (`analyzer.py`)、数学公式解析 (`formula.py`)，Word/PPT 报告引擎 (`report_builder/`)、LangGraph 多智能体审查系统 (`multi_agent.py`)。
- **标定引擎 (`py/calibration/`):** 温度标定 (平台检测 + KMeans 映射 + 灵敏度回归 + 解耦诊断)、应变标定 (手填表驱动 + 灵敏度/线性度/重复性/迟滞/双栅分析)、Excel 导出。
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
3. **公式求值统一走 `py/formula.py` 的 asteval 引擎:** 所有公式求值必须通过 `py.formula.calculate()`（基于 `asteval.Interpreter` 安全向量化求值），**禁止再用 `eval` 或字符串替换**。参数与列变量均作为 symtable 注入，彻底消除 k1/k10 误匹配风险。

---

## 🔧 标定模块约定 (v2.1 新增)

1. **标定系数闭环仅限当前会话:** 通过「应用标定系数到当前传感器」按钮注入的实测 k 系数，**仅写入当前 `sensor_system` 实例的 `Sensor.constants`**（会话级）。**严禁**写回全局 `Sensor.TYPE_PARAMS` 或持久化为默认值。
2. **标定模块不得改动暗号标注机制:** 标定模块有自己独立的列角色指派 UI（`col_table`），**绝不插入标注行**（annotation row），**不复用** `analysis_tab` 的 `_annotated_cols` 解析逻辑。
3. **温度标定独立加载文件:** 温度标定子页加载的连续波长文件**不写入**主窗口的 `current_data`，在独立的 DataFrame 中完成全流程。
4. **解耦矩阵与 `core/models.py` 公式一致:** `py/calibration/temperature_calibration.decouple()` 的 2×2 矩阵求逆公式与 `SensorSystem._evaluate_decoupling()` **逐元素等价**。
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

## 🚀 常用开发命令

```bash
python main.py              # 运行主程序
python test_run.py          # 快速启动测试（仅测试 UI 加载是否崩溃）
python -c "import py_compile; py_compile.compile('main.py', doraise=True)" # 快速语法树静态检查
pyright <changed_files>                                   # 类型检查（提交前必须零红线）
python run_tests.py                                       # 运行所有测试 (公式引擎 + 标定引擎)