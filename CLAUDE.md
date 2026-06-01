# DataProcessor Pro 2.0 - 系统上下文与规则

本文件设定了 Claude Code 的绝对约束条件。在提出任何代码修改建议前，请务必仔细阅读并严格遵守。

## ⚠️ 关键工程纪律（绝对最高优先级）

**1. Pandas 2.0+ 严格类型安全（禁止 LossySetitem）**
- **严禁**使用切片就地赋值（in-place slice assignment）将对象（object）或字符串直接插入到底层为 float64 的 DataFrame 块中（例如：绝对禁止写 `part[:] = part.astype(object)`）。
- **必须**使用全局类型提升后进行“乐高式”拼接的安全写法：`df = df.astype(object)` -> `pd.concat([top, new_row, bottom])`。

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

---

## 🏗 项目拓扑与架构

- **巨石架构警告:** 前端 UI 集中在庞大的单文件（`main.py`，约 8800 行）中。当你使用 `edit_file` 工具时，**必须**提供极其明确的上下文特征字符串，以防止灾难性的全局搜索替换错误。
- **后端引擎 (`py/`):** 信号处理 (`analyzer.py`)、数学公式解析 (`formula.py`)、Word/PPT 报告引擎 (`report_builder/`)，以及 LangGraph 多智能体审查系统 (`multi_agent.py`)。
- **核心数据流向:** `文件解析 -> _auto_populate_fbgs (正则提取 FBG_) -> SensorSystem.calculate (公式计算) -> 异常清洗/FFT分析 -> 导出报告`。
- **机密隔离:** `ai_models_config.json` 文件内含有敏感的 API 密钥。**严禁**在对话中打印其内容或将其提交入库（commit）。

---

## 🧠 代理行为准则（AI 工作流规范）

1. **优先使用 LSP/符号搜索:** 不要依赖盲目的纯文本 `grep` 匹配。如果你需要查找 `current_plot_df` 或 `sensor_results` 在哪里被实例化，请优先使用语言服务器（LSP）能力进行精准的语义符号搜索。
2. **派生子代理（Sub-agent）探路:** 在调试复杂的 Pandas 索引对齐或切片 Bug 时，不要直接在主会话里盲改 `main.py`。请先派生一个子代理（Sub-agent）写个独立的测试脚本，摸清底层的 `.index` 行为后，再在主会话中给出最终代码。
3. **正则优于 `.replace`:** 在解析和计算传感器公式（如 `k1 * W1`）时，**必须**使用带有单词边界的正则表达式（`\b`）来安全替换常数，严禁使用脆弱的纯字符串 `.replace`（防止将 `k10` 误替换为 `k1`）。

---

## 🚀 常用开发命令

```bash
python main.py              # 运行主程序
python test_run.py          # 快速启动测试（仅测试 UI 加载是否崩溃）
python -c "import py_compile; py_compile.compile('main.py', doraise=True)" # 快速语法树静态检查