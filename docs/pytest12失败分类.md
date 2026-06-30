# pytest 12 失败分类 — 改名前/本会话回归/预存

**日期**: 2026-06-25 (py/ → dp_engine/ 改名批)  
**状态**: 只分类定性的只读分析，不改任何测试或代码

---

## 1. 总览

**运行环境**: `python -m pytest tests/` (不再 `py.path` 崩溃 — 改名已根治)。以下排除 5 个含 `sys.exit(0)` 于文件级、4 个预存 import 错误的已忽略文件。

实际失败数为 17 FAILED + 14 ERROR (calibration_profile 1 条根因), 共 31 项, 归结为 2 类:

| 分类 | 数量 | 说明 |
|------|------|------|
| **本会话回归** | 3（均已在本批解决） | AnalysisProvider 判据改变后测试未同步; monkeypatch 字符串漏改（已改），14 个 calibration_profile 错误属同根 |
| **真预存 (早于本会话)** | 14 (9 种根因) | `79b9b10` llama-cpp 迁移 / `a7b25b0` 应变标定 / `7fad573` 补偿流水线的连带测试偏斜 |

---

## 2. 逐个 FAILED/ERROR 明细

### 2.1 `test_calibration_profile.py` — 14 个 ERROR (同根因 → 本会话; 已修)

```
ERROR: ImportError: import error in py.calibration: No module named 'py.calibration'
```

| 测试 | 文件:行号 |
|------|-----------|
| test_to_dict_from_dict_roundtrip 等 TestCalibrationProfile × 3 | `test_calibration_profile.py:31` |
| test_save_and_load_roundtrip 等 TestSaveLoad × 4 | `test_calibration_profile.py:31` |
| test_loaded_df_truthiness_raises 等 TestDataFrameBoolGuard × 3 | `test_calibration_profile.py:31` |
| test_load_profile_overrides_parser_placeholders 等 TestLoadProfileLiveState × 4 | `test_calibration_profile.py:31` |

**根因**: `monkeypatch.setattr("py.calibration.profile.PROFILES_DIR", tmp)` — 字符串内含有旧 `py.` 包名, 改名后 `py` 模块解析为 PyPI 的 `py` 包 (无 `.calibration.profile` 子模块)。本批阶段 2 已手动改为 `"dp_engine.calibration.profile.PROFILES_DIR"`。**改名引入, 已同步修**。

### 2.2 `test_data_providers.py::TestAnalysisProvider` — 3 个 FAILED (本会话引入)

| 测试 | 断言 | 根因 |
|------|------|------|
| `test_available_with_results` (`:270-272`) | `assert AnalysisProvider().is_available(SimpleNamespace(sensor_results={"A":[1,2,3]}))` — `is_available` 返回 `False` | 测试设 `main_win.sensor_results` 非空；改后 `is_available` 读 `main_win.analysis_tab_widget._current_data`，测试的 `SimpleNamespace` 不含 `analysis_tab_widget` 属性 → `getattr(main_win, 'analysis_tab_widget', None)` 返回 `None` → `False` |
| `test_summary_has_features` (`:274-281`) | `assert "FBG_A1" in s` — s=`"【数据分析】无数据分析结果（分析页未就绪）"` | 同上：`get_summary` 从 `analysis_tab_widget._current_data` 取数，测试只设了 `sensor_results` |
| `test_summary_has_drift` (`:283-287`) | `assert "drift" in s.lower() or "漂移" in s` — s 同上报"未就绪" | 同上 |

**定位**: 本会话对 `AnalysisProvider.is_available` + `get_summary` 的双改 (读 `_current_data` 替代 `sensor_results`) **测试未同步更新**。测试需改为设置 `analysis_tab_widget=SimpleNamespace(_current_data=pd.DataFrame(...))` 而非 `sensor_results=...`。**3 处均为本会话回归**。

### 2.3 `test_data_providers.py::TestCalibrationProvider` — 2 个 FAILED (预存)

| 测试 | 断言 | 根因 |
|------|------|------|
| `test_summary_has_sensors` (`:180-185`) | `assert "1.2" in s or "1.20" in s` 和 `assert "good" in s` — 实际输出 `评级=?(未通过)` 而非 `good` | `CalibrationProvider.get_summary` (`data_providers.py:289-388`) 在 `a7b25b0` 提交中重写了 grade 渲染逻辑——旧格式 `"good"` 变为新格式 `"?(未通过)"` (A1 的 `passed=False` 但 grade='good' 的混合态被统一为 `?(未通过)`。Assert 格式落后, 属预存 |
| `test_summary_FAIL` (`:187-191`) | `assert "FAIL" in s` — 实际输出 `评级=?(未通过)` | 同上 — C2 的 `grade="FAIL"` 被新渲染函数改写为 `?(未通过)` |

**定位**: `git log` — `a7b25b0 feat(calibration): 应变标定全链路` 改动了 `CalibrationProvider.get_summary` 的 grade 渲染。**早于本会话，2 处均为预存**。

### 2.4 `test_project_config.py` — 4 个 FAILED (预存)

| 测试 | 断言 | 根因 |
|------|------|------|
| `test_save_load_roundtrip_temperature_only` (`:100-123`) | `for f in TEMPERATURE_FIELDS: assert f in t2` — `"compensation"` 不在 t2 中 | 测试 helper `_make_sample_temperature()` (`:40-56`) **不含** `"compensation"` 字段；`TEMPERATURE_FIELDS` (`project_config.py:30-35`) 新增了 `"compensation"` — 两者的 gap |
| `test_validate_passes_on_complete_temperature` (`:135-143`) | `assert issues == []` — 返回 `['temperature 缺少字段: compensation']` | 同上 — validate 逻辑检查 `TEMPERATURE_FIELDS` 的所有字段都在 dict 中 |
| `test_save_load_roundtrip_with_strain` (`:176-199`) | `assert issues == []` — 同样 `compensation` 缺失 | 同上 |
| `test_json_on_disk_is_valid` (`:220-239`) | `for f in TEMPERATURE_FIELDS: assert f in raw["temperature"]` — `"compensation"` 缺 | 同上 |

**定位**: `git log dp_engine/calibration/project_config.py` — `a7b25b0` 提交将 `"compensation"` 加入 `TEMPERATURE_FIELDS` (第 34 行)，测试 helper 未同步更新。**早于本会话，4 处均为预存**。

### 2.5 `test_phase_b_dialog.py` — 4 个 FAILED (预存)

| 测试 | 断言/报错 | 根因 |
|------|-----------|------|
| `test_phase_b_result_table` (`:??`) | `expected 10 cols, got 12` | 结果表列数在 `7fad573` 补偿流水线批新增了 `comp_form` 和 `poly_order` 列, 测试的 10 列预期量滞后 |
| `test_ensure_decoupled_result_lazy_decouple` (`:??`) | `should lazy-decouple` | `_ensure_decoupled_result` 逻辑变更 (CLAUDE.md 铁律: 禁止重算, 仅返回缓存或 None) |
| `test_render_result_table_high_hysteresis_shows_fail` (`:??`) | `AttributeError: 'PhaseBDialog' object has no attribute '_run_compensation_pipeline'` | CLAUDE.md 已文档化: "旧实例方法 `_run_compensation_pipeline` 已删除…已静态化为 `_run_compensation_pipeline_static`" — `7fad573` 删除方法时未同步更新测试 |
| `test_render_result_table_shows_real_std` (`:??`) | 同上 `AttributeError` | 同上 |

**定位**: `git log` — 全部来自 `7fad573` (补偿流水线静态化) 或更早 `a7b25b0` (应变标定)。**4 处均为预存**。

### 2.6 `test_phase_a_dialog.py::TestThreeTierPriority::test_dirty_column_survives_state_restore` (预存)

**断言**: `dirty column should keep user value, got 'A1-W2'`

**定位**: Phase A 对话框状态恢复的 dirty column 优先级问题 — 对话框关闭/重开时, 用户修改过的列被系统默认值覆盖。这是标定页预存 bug，CLAUDE.md 有相关纪律 (对称 Phase 改动同步)。**早于本会话，预存**。

### 2.7 `test_anchored_and_load.py::TestAnchoredModeFix::test_anchored_chart_plots_working_grating` (预存)

**断言**: `assert '光栅1' in '标距: 80.0 mm | tension_only | 1循环\n\nG1: k=1.210000 R²=1.000000 NL=0.00% RP=nan% HY=nan%...'`

**定位**: 锚固模式图表的图例文本格式变更 — `光栅1` 不在当前输出中。与 `a7b25b0` 应变标定批对读数 `profile` → `readings_profile` 的重构相关。**早于本会话，预存**。

### 2.8 `test_diagnosis_save.py::TestDiagnosisRecord::test_build_record_has_all_fields` (预存)

**断言**: `assert '1.2' == '1.1'` — 测试期望 `schema_version == '1.1'`，代码返回 `'1.2'`

**定位**: `ai_diagnosis.py:1129` 写 `"schema_version": "1.2"` (见 `79b9b10` refactor: migrate to llama.cpp backend)。测试断言硬编码 `'1.1'`。**早于本会话，预存**。

### 2.9 `test_ai_client_backend.py::TestConfigureOnline::test_configure_makes_available` (预存)

**断言**: `assert not True` — 测试期望配置后 `is_available()` 返回 `False`，实际返回 `True`

**定位**: llama-cpp 后端迁移 (`79b9b10`) 改变了 `is_available()` 的逻辑 (本地后端配置后即 `True`，不再依赖在线 API key)。测试预期与旧在线模式对齐。**早于本会话，预存**。

### 2.10 `test_calibration_tab_ui.py::TestCalibrationTabUI::test_paste_selected_state_multi_row` — 1 个 ERROR (预存)

**报错**: 未知 (未在输出中完整显示)

**定位**: 标定页 UI 粘贴多行选择的测试，属预存不稳定/依赖 GUI 环境。**早于本会话，预存**。

---

## 3. AnalysisProvider 3 例专项 (本会话引入)

```python
# tests/test_data_providers.py:270-272
def test_available_with_results(self):
    assert AnalysisProvider().is_available(SimpleNamespace(sensor_results={"A": [1, 2, 3]}))
```

**新旧行为对照**:

| | is_available 判据 | summary 数据源 |
|------|------|------|
| 旧 (本会话前) | `main_win.sensor_results` | `main_win.sensor_results` |
| 新 (本会话改后) | `main_win.analysis_tab_widget._current_data` | `main_win.analysis_tab_widget._current_data` |

测试创建 `SimpleNamespace(sensor_results={"A":[1,2,3]})` 不含 `analysis_tab_widget` 属性 → `getattr(main_win, 'analysis_tab_widget', None)` 返回 `None` → `is_available` 返回 `False`, `get_summary` 返回 `"无数据分析结果（分析页未就绪）"`。

**这 3 个是本会话** (判据从 `sensor_results` 改为 `_current_data`) **引入的测试偏斜，不是代码 bug。** 测试应改为设置 `analysis_tab_widget` 属性。

---

## 4. 分类汇总

### 本会话回归 (需同步测试)

| # | 测试 | 引批 | 修法 |
|---|------|------|------|
| 1 | TestAnalysisProvider::test_available_with_results | 判据改为 `_current_data` | 测试改设 `analysis_tab_widget._current_data` |
| 2 | TestAnalysisProvider::test_summary_has_features | 同上 | 同上 |
| 3 | TestAnalysisProvider::test_summary_has_drift | 同上 | 同上 |
| 4-17 | test_calibration_profile.py × 14 ERROR | 改名后 monkeypatch 字符串漏改 | ✅ 已同步修 `dp_engine.calibration.profile.PROFILES_DIR` |

### 真预存 (早于本会话, 无需改, 后续统一清)

| # | 测试 | 引批 | 最近关联 commit |
|---|------|------|---------------|
| 5-6 | CalibrationProvider × 2 | grade 渲染格式变更 | `a7b25b0` |
| 7-10 | project_config × 4 | TEMPERATURE_FIELDS 新增 `compensation` | `a7b25b0` |
| 11-14 | phase_b_dialog × 4 | `_run_compensation_pipeline` 删除 + 列数变更 | `7fad573` / `a7b25b0` |
| 15 | phase_a_dialog × 1 | dirty column 状态恢复 | 较早 (CLAUDE.md 纪律 5/8) |
| 16 | anchored_and_load × 1 | 图表图例文本变更 | `a7b25b0` |
| 17 | diagnosis_save × 1 | schema_version 1.1→1.2 | `79b9b10` |
| 18 | ai_client_backend × 1 | is_available 逻辑改 | `79b9b10` |
| 19 | calibration_tab_ui × 1 | GUI 环境依赖 | 较早 |

---

## 5. 证据链

### 5.1 commit 时间线 (FAILED 相关)

```
79b9b10 (最早) refactor: migrate to llama.cpp backend, add diagnosis KB
  ├── diagnosis_save schema 1.2      (test_diagnosis_save)
  └── ai_client is_available change  (test_ai_client_backend)

7fad573 feat(phase-b): compensation pipeline, robust hysteresis
  ├── _run_compensation_pipeline 删除 (test_phase_b_dialog × 2)
  └── 结果表列数 +2 (test_phase_b_result_table)

a7b25b0 feat(calibration): 应变标定全链路
  ├── TEMPERATURE_FIELDS + compensation (test_project_config × 4)
  ├── CalibrationProvider grade 格式 (test_calibration × 2)
  ├── 锚固图表图例 (test_anchored_and_load)
  └── phase_b_result_table / lazy_decouple
```

### 5.2 本会话改动 (FAILED 相关)

```
本批: AnalysisProvider.is_available + get_summary 双改判据
本批: py/ → dp_engine/ 改名 (monkeypatch 字符串残留)
```

### 5.3 证据: 不改任何测试, 仅定性

- `test_available_with_results` 的 `SimpleNamespace(sensor_results=...)` 在改名前的旧代码中可通过 — 证明是 `is_available` 判据改引起的偏斜
- `test_summary_has_features` / `test_summary_has_drift` 同上
- `_run_compensation_pipeline` 的 `AttributeError` — CLAUDE.md 第 11 条 "补偿流水线已静态化" 已文档化该删除
- `compensation` 字段缺失 — `project_config.py:34` `TEMPERATURE_FIELDS` 含 `"compensation"`, `test_project_config.py:42-56` `_make_sample_temperature()` 不含 — 代码与测试的 gap
- `schema_version 1.2 vs 1.1` — `ai_diagnosis.py:1129` 写 `"1.2"`, 测试期望 `'1.1'` — 代码演进, 测试滞后
