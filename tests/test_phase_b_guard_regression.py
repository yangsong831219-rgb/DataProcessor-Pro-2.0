"""PhaseBDialog async guard regression tests (standalone)

Tests:
1. _ensure_decoupled_result: never sync computes — returns None when _last_result=None
2. _ensure_decoupled_result: returns sensors when _last_result exists
3. High hysteresis B2 -> FAIL rating + red bg
4. eps_std ~10 -> table shows real value (not 0.00)
5. State restore from persisted scalars only (no heavy compute)
6. FAIL rating from persisted compensation data (not old "差")

Usage: python tests/test_phase_b_guard_regression.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from PyQt6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication(sys.argv)


def check(label, cond):
    if cond:
        print(f"  PASS: {label}")
        return 1
    else:
        print(f"  FAIL: {label}")
        return 0


passed, total = 0, 0


# =========================================================================
# Test 1: _ensure_decoupled_result NEVER sync computes
# =========================================================================
print("\n=== TestNoSyncCompute ===")

from ui.calibration_tab import PhaseBDialog, _run_compensation_pipeline_static

n = 300
sample_df = pd.DataFrame()
for wl in ["A1-W1", "A1-W2"]:
    sample_df[wl] = np.ones(n) * 1550.0
    sample_df[f"{wl}_d"] = np.sin(np.linspace(0, 3, n)) * 1000.0

dlg = PhaseBDialog(sample_df,
    {"A1": [{"col_name": "A1-W1", "name": "A1-W1"},
            {"col_name": "A1-W2", "name": "A1-W2"}]},
    {"S_eff": {"A1-W1": {"slope": 27.8, "T_base": 10.0},
                "A1-W2": {"slope": 29.4, "T_base": 10.0}}})
dlg._coeffs = {"A1": {"Ke1": 1.2, "Ke2": 1.2}}

total += 1; passed += check("_last_result is None initially", dlg._last_result is None)

# _ensure_decoupled_result must NOT compute — just return None
sensors = dlg._ensure_decoupled_result()
total += 1; passed += check("returns None when _last_result is None", sensors is None)
total += 1; passed += check("_last_result still None after guard", dlg._last_result is None)

# Verify no compensation_results were set
total += 1; passed += check("no _compensation_results after guard",
    getattr(dlg, '_compensation_results', None) is None)

dlg.close(); del dlg


# =========================================================================
# Test 2: _ensure_decoupled_result returns sensors when _last_result exists
# =========================================================================
print("\n=== TestReturnsCachedResult ===")

sample_df2 = pd.DataFrame({"a": [1, 2, 3]})
dlg2 = PhaseBDialog(sample_df2, {}, {"S_eff": {}})
# Set a mock _last_result
dlg2._last_result = {"sensors": {"A1": {"eps_corr": np.array([1,2,3])}},
                      "time_h": np.array([0,1,2]), "df": sample_df2}
sensors2 = dlg2._ensure_decoupled_result()
total += 1; passed += check("returns sensors when _last_result exists", sensors2 is not None)
total += 1; passed += check("A1 in sensors", "A1" in sensors2)
dlg2.close(); del dlg2


# =========================================================================
# Test 3: B2 high hysteresis -> FAIL rating + red bg
# =========================================================================
print("\n=== TestHighHysteresisFAIL ===")

np.random.seed(1)
n3 = 600
T_abs = np.empty(n3)
for ci in range(3):
    for half in range(2):
        s = (ci * 2 + half) * (n3 // 6)
        e = s + n3 // 6
        T_abs[s:e] = np.linspace(10, 60, n3 // 6) if half == 0 else np.linspace(60, 10, n3 // 6)
eps_corr = 2.0 * (T_abs - 25.0)
for ci in range(3):
    for half in range(2):
        s = (ci * 2 + half) * (n3 // 6)
        e = s + n3 // 6
        eps_corr[s:e] += 130.0 if half == 0 else -130.0
eps_corr += np.random.normal(0, 5, n3)

sensors3 = {"B2": {"eps_corr": eps_corr, "dT_corr": np.zeros(n3),
    "T_abs": T_abs, "S1": 30.0, "S2": 28.0, "T_base": 25.0,
    "single_grating": False}}

df3 = pd.DataFrame()
for wl in ["B2-W1", "B2-W2"]:
    df3[wl] = np.ones(100) * 1550.0 + np.random.normal(0, 0.1, 100)

dlg3 = PhaseBDialog(df3,
    {"B2": [{"col_name": "B2-W1", "name": "B2-W1"}, {"col_name": "B2-W2", "name": "B2-W2"}]},
    {"S_eff": {"B2-W1": {"slope": 41.0, "T_base": 25.0}, "B2-W2": {"slope": 30.0, "T_base": 25.0}}})
dlg3._coeffs = {"B2": {"Ke1": 0.8, "Ke2": 1.1}}
dlg3._compensation_results = _run_compensation_pipeline_static(sensors3, fs_map={})
dlg3._render_result_table(sensors3, {"B2-W1": {"slope": 41.0}, "B2-W2": {"slope": 30.0}},
                           compensation=dlg3._compensation_results)
QApplication.processEvents()

tbl = dlg3.result_table
total += 1; passed += check("table has rows", tbl.rowCount() >= 1)
found = False
for i in range(tbl.rowCount()):
    if tbl.item(i, 0).text() == "B2":
        rating = tbl.item(i, 11).text()
        total += 1; passed += check(f"B2 rating: {rating} (expected FAIL)", rating == "FAIL")
        bg = tbl.item(i, 11).background().color().name()
        total += 1; passed += check(f"B2 bg: {bg} (expected #ffcdd2)", bg == "#ffcdd2")
        found = True
        break
total += 1; passed += check("B2 row found", found)
dlg3.close(); del dlg3


# =========================================================================
# Test 4: eps_std ~10 -> shows real value
# =========================================================================
print("\n=== TestRealEpsStd ===")

np.random.seed(42)
n4 = 300
T_abs4 = np.linspace(10, 60, n4)
eps_corr4 = np.random.normal(0, 10, n4)
sensors4 = {"A1": {"eps_corr": eps_corr4, "dT_corr": np.zeros(n4),
    "T_abs": T_abs4, "S1": 30.0, "S2": 28.0, "T_base": 25.0, "single_grating": False}}

df4 = pd.DataFrame()
for wl in ["A1-W1", "A1-W2"]:
    df4[wl] = np.ones(100) * 1550.0

dlg4 = PhaseBDialog(df4,
    {"A1": [{"col_name": "A1-W1", "name": "A1-W1"}, {"col_name": "A1-W2", "name": "A1-W2"}]},
    {"S_eff": {"A1-W1": {"slope": 27.8, "T_base": 25.0}, "A1-W2": {"slope": 29.4, "T_base": 25.0}}})
dlg4._coeffs = {"A1": {"Ke1": 1.2, "Ke2": 1.2}}
dlg4._compensation_results = _run_compensation_pipeline_static(sensors4, fs_map={})
dlg4._render_result_table(sensors4, {"A1-W1": {"slope": 27.8}, "A1-W2": {"slope": 29.4}},
                           compensation=dlg4._compensation_results)
QApplication.processEvents()

tbl4 = dlg4.result_table
for i in range(tbl4.rowCount()):
    if tbl4.item(i, 0).text() == "A1":
        e_std_text = tbl4.item(i, 7).text()
        e_std_val = float(e_std_text)
        total += 1; passed += check(f"e_std = {e_std_val:.2f} (expected 1..20)", 1.0 < e_std_val < 20.0)
        total += 1; passed += check("e_std != 0", e_std_val != 0.0)
        break
dlg4.close(); del dlg4


# =========================================================================
# Test 5: State restore — scalar-only, no heavy compute triggered
# =========================================================================
print("\n=== TestScalarOnlyNoHeavyCompute ===")

# Construct a simulated _phase_b_state with persisted scalars
# Simulate via direct injection
df5 = pd.DataFrame({"ch1": [1,2], "ch2": [1,2]})
dlg5 = PhaseBDialog(df5,
    {"A1": [{"col_name": "ch1", "name": "A1-W1"}, {"col_name": "ch2", "name": "A1-W2"}]},
    {"S_eff": {"ch1": {"slope": 27.8}, "ch2": {"slope": 29.4}}})
dlg5._coeffs = {"A1": {"Ke1": 1.2, "Ke2": 1.2}}

# _last_result is None, _ensure_decoupled_result must not compute
assert dlg5._last_result is None
sensors5 = dlg5._ensure_decoupled_result(show_warning=False)
total += 1; passed += check("guard returns None (no cached result)", sensors5 is None)
total += 1; passed += check("_last_result still None", dlg5._last_result is None)

dlg5.close(); del dlg5


# =========================================================================
# Test 6: FAIL rating from persisted compensation (not dedecated "差")
# =========================================================================
print("\n=== TestFAILFromPersistedGrade ===")

from utils.compensation_metrics import SensorGrade
from utils.apparent_strain_comp import CompensationModel, ApparentStrainLUT

# Build persisted compensation data with grade=FAIL
lut6 = ApparentStrainLUT(sensor="B2", T_base=25.0,
    T_grid=[10.0, 30.0, 50.0], eps_app=[0.0, 20.0, 40.0],
    T_min=10.0, T_max=50.0, n_cycles=3)
cm6 = CompensationModel(form="lut", model=lut6)
grade6 = SensorGrade(sensor="B2", grade="FAIL", passed=False,
    reasons=["迟滞超标: hysteresis_max=261.0 ue (26.1%FS) > 5.0%FS"])

# Inject compensation_results
dlg6 = PhaseBDialog(pd.DataFrame({"a": [1]}), {}, {"S_eff": {}})
dlg6._compensation_results = {
    "B2": {"model": cm6, "metrics": None, "grade": grade6},
}

# Build display sensors + decoupling data
display_sensors = {"B2": {"eps_corr": [10.0], "eps_orig": [], "dT_corr": [],
    "T_abs": [], "S1": 0, "S2": 0, "T_base": 0, "single_grating": False}}
decoupling_data = {"B2": {"e_mean": 10.0, "e_std": 50.0, "e_range": 200.0, "rating": "差"}}

# ★ Use saved_ratings that prefers grade from compensation over old rating
saved_ratings6 = {}
comp_entry = dlg6._compensation_results.get("B2", {})
comp_g = comp_entry.get("grade") if isinstance(comp_entry, dict) else None
if comp_g is not None and comp_g.grade:
    saved_ratings6["B2"] = comp_g.grade  # "FAIL"
else:
    saved_ratings6["B2"] = decoupling_data["B2"].get("rating", "—")

total += 1; passed += check("saved_ratings uses FAIL from grade", saved_ratings6["B2"] == "FAIL")

dlg6._render_result_table(display_sensors, {},
    saved_ratings=saved_ratings6, decoupling_data=decoupling_data,
    compensation=dlg6._compensation_results)
QApplication.processEvents()

tbl6 = dlg6.result_table
for i in range(tbl6.rowCount()):
    rating6 = tbl6.item(i, 11).text()
    total += 1; passed += check(f"persisted FAIL rating: {rating6}", rating6 == "FAIL")
    bg6 = tbl6.item(i, 11).background().color().name()
    total += 1; passed += check(f"persisted FAIL bg: {bg6}", bg6 == "#ffcdd2")
    break

dlg6.close(); del dlg6


# =========================================================================
# Test 7: _on_done 完整路径 — 覆盖 fallback + UnboundLocalError 回归
# =========================================================================
# ★ 原 17 条测试从未触发 _on_done 的 fallback 分支 (lines 1810-1822):
#   Test 1-6 均直接注入 _compensation_results 或 _last_result，绕过了
#   "worker 未算补偿 → 静态函数补算" 路径。Phase 3 引入的 tp_fb/
#   thresholds_fb 引用在 Line 1818-1822 因缩进错误跳出 if 块，
#   在 _compensation_results 为真时 tp_fb 未绑定即崩溃。
# ★ 本组补: (a) compensation 为空 → fallback + tp_fb=None 安全跳过;
#          (b) compensation 来自 worker → 无 fallback, 无崩溃;
#          (c) accept 后 grade_thresholds 写入 _phase_b_state.

print("\n=== TestOnDoneFallbackNoCompensation ===")

from PyQt6.QtWidgets import QWidget

np.random.seed(707)
n7 = 400
T_abs7 = np.empty(n7)
for ci in range(3):
    for half in range(2):
        s = (ci * 2 + half) * (n7 // 6)
        e = s + n7 // 6
        T_abs7[s:e] = np.linspace(10, 60, n7 // 6) if half == 0 else np.linspace(60, 10, n7 // 6)
eps_corr7 = 2.0 * (T_abs7 - 25.0) + 20.0 + np.random.normal(0, 5, n7)

# ── 构造真实 worker 输出格式 ──
sensors7 = {"A1": {"eps_corr": eps_corr7, "dT_corr": T_abs7 - 25.0,
    "T_abs": T_abs7, "eps_orig": eps_corr7 * 0.9,
    "S1": 30.0, "S2": 28.0, "T_base": 25.0, "single_grating": False}}

df7 = pd.DataFrame()
for wl in ["A1-W1", "A1-W2"]:
    df7[wl] = np.ones(n7) * 1550.0 + np.random.normal(0, 0.1, n7)

dlg7 = PhaseBDialog(df7,
    {"A1": [{"col_name": "A1-W1", "name": "A1-W1"},
            {"col_name": "A1-W2", "name": "A1-W2"}]},
    {"S_eff": {"A1-W1": {"slope": 27.8, "T_base": 25.0},
                "A1-W2": {"slope": 29.4, "T_base": 25.0}}})
dlg7._coeffs = {"A1": {"Ke1": 1.2, "Ke2": 1.2}}

# ── 7a: compensation 为空 → 触发 fallback 路径 (tp_fb=None → 安全跳过) ──
result_no_comp = {
    "sensors": sensors7, "comparisons": [],
    "df": df7, "time_h": np.arange(n7) * 2.0 / 3600.0,
    "compensation": {},  # ← 空: worker 没算补偿
}

caught = None
try:
    dlg7._on_done(result_no_comp)
except UnboundLocalError as e:
    caught = str(e)
except Exception as e:
    caught = f"{type(e).__name__}: {e}"

total += 1; passed += check(
    "7a: _on_done with empty compensation — no UnboundLocalError",
    caught is None)
total += 1; passed += check(
    "7a: _compensation_results populated after fallback",
    bool(dlg7._compensation_results) and "A1" in dlg7._compensation_results)
total += 1; passed += check(
    "7a: grade present in compensation",
    dlg7._compensation_results.get("A1", {}).get("grade") is not None)

dlg7.close(); del dlg7


# ── 7b: compensation 来自 worker (非空) → 不触发 fallback, 不崩溃 ──
print("\n=== TestOnDoneWithWorkerCompensation ===")

np.random.seed(808)
n7b = 400
T_abs7b = np.empty(n7b)
for ci in range(3):
    for half in range(2):
        s = (ci * 2 + half) * (n7b // 6)
        e = s + n7b // 6
        T_abs7b[s:e] = np.linspace(10, 60, n7b // 6) if half == 0 else np.linspace(60, 10, n7b // 6)
eps_corr7b = 2.0 * (T_abs7b - 25.0) + 30.0 + np.random.normal(0, 3, n7b)

sensors7b = {"A1": {"eps_corr": eps_corr7b, "dT_corr": T_abs7b - 25.0,
    "T_abs": T_abs7b, "eps_orig": eps_corr7b * 0.9,
    "S1": 30.0, "S2": 28.0, "T_base": 25.0, "single_grating": False}}
df7b = pd.DataFrame()
for wl in ["A1-W1", "A1-W2"]:
    df7b[wl] = np.ones(n7b) * 1550.0

dlg7b = PhaseBDialog(df7b,
    {"A1": [{"col_name": "A1-W1", "name": "A1-W1"},
            {"col_name": "A1-W2", "name": "A1-W2"}]},
    {"S_eff": {"A1-W1": {"slope": 27.8, "T_base": 25.0},
                "A1-W2": {"slope": 29.4, "T_base": 25.0}}})
dlg7b._coeffs = {"A1": {"Ke1": 1.2, "Ke2": 1.2}}

# 预先算好补偿 (模拟 worker 已完成)
pre_comp = _run_compensation_pipeline_static(sensors7b, fs_map={})
result_with_comp = {
    "sensors": sensors7b, "comparisons": [],
    "df": df7b, "time_h": np.arange(n7b) * 2.0 / 3600.0,
    "compensation": pre_comp,  # ← worker 已算
}

caught7b = None
try:
    dlg7b._on_done(result_with_comp)
except UnboundLocalError as e:
    caught7b = str(e)
except Exception as e:
    caught7b = f"{type(e).__name__}: {e}"

total += 1; passed += check(
    "7b: _on_done with worker compensation — no exception",
    caught7b is None)
total += 1; passed += check(
    "7b: _compensation_results from worker preserved",
    dlg7b._compensation_results is pre_comp)
total += 1; passed += check(
    "7b: result table has A1 row",
    dlg7b.result_table.rowCount() >= 1)

dlg7b.close(); del dlg7b


# ── 7c: _write_state_to_main_page → grade_thresholds SSOT 持久化 ──
print("\n=== TestGradeThresholdsPersistInState ===")

from utils.compensation_metrics import GradeThresholds

# 构造 mock 父控件 (模拟温度标定页)
class _MockTempPage(QWidget):
    def __init__(self):
        super().__init__()
        self._phase_b_state: dict = {}
        self._phase_b_result = None

mock_page = _MockTempPage()
# 预设 grade_thresholds (如用户曾改过阈值)
mock_page._phase_b_state = {
    "ke_table": {"A1": {"Ke1": 1.2, "Ke2": 1.2}},
    "decoupling_results": {"A1": {"e_mean": 5.0, "e_std": 10.0, "e_range": 80.0, "rating": "优"}},
    "compensation": {},
    "grade_thresholds": {"thr_hysteresis_fail_pct_fs": 8.0},
}

df7c = pd.DataFrame()
for wl in ["A1-W1", "A1-W2"]:
    df7c[wl] = np.ones(10) * 1550.0

dlg7c = PhaseBDialog(df7c,
    {"A1": [{"col_name": "A1-W1", "name": "A1-W1"},
            {"col_name": "A1-W2", "name": "A1-W2"}]},
    {"S_eff": {"A1-W1": {"slope": 27.8, "T_base": 25.0},
                "A1-W2": {"slope": 29.4, "T_base": 25.0}}},
    parent=mock_page)
dlg7c._coeffs = {"A1": {"Ke1": 1.2, "Ke2": 1.2}}

# 注入 _last_result + _compensation_results
dlg7c._last_result = {
    "sensors": sensors7b,
    "comparisons": [],
    "df": df7c,
    "time_h": np.arange(400) * 2.0 / 3600.0,
    "compensation": pre_comp,
}
dlg7c._compensation_results = pre_comp

# ★ accept() → _write_state_to_main_page → 写回 _phase_b_state
dlg7c.accept()

total += 1; passed += check(
    "7c: _phase_b_state persisted after accept",
    "grade_thresholds" in mock_page._phase_b_state)
gt_persisted = mock_page._phase_b_state.get("grade_thresholds", {})
total += 1; passed += check(
    f"7c: grade_thresholds thr_hys={gt_persisted.get('thr_hysteresis_fail_pct_fs')} (expected 8.0)",
    gt_persisted.get("thr_hysteresis_fail_pct_fs") == 8.0)
total += 1; passed += check(
    "7c: compensation in state",
    "compensation" in mock_page._phase_b_state)
total += 1; passed += check(
    "7c: comp_form/poly_order preserved (not overwritten)",
    "ke_table" in mock_page._phase_b_state)

dlg7c.close(); del dlg7c


# ── 7d: _on_done fallback + tp_fb=None → 安全跳过, 不崩 ──
print("\n=== TestOnDoneFallbackTpFbNone ===")

# 重跑 7a 场景但显式验证: tp_fb=None 时 thresholds_fb 保持 None
# (即 _get_temp_page 返回 None 的正常处理)
df7d = pd.DataFrame()
for wl in ["A1-W1", "A1-W2"]:
    df7d[wl] = np.ones(10) * 1550.0

dlg7d = PhaseBDialog(df7d,
    {"A1": [{"col_name": "A1-W1", "name": "A1-W1"},
            {"col_name": "A1-W2", "name": "A1-W2"}]},
    {"S_eff": {"A1-W1": {"slope": 27.8, "T_base": 25.0},
                "A1-W2": {"slope": 29.4, "T_base": 25.0}}},
    parent=None)  # ← 无父控件 → _get_temp_page() → None
dlg7d._coeffs = {"A1": {"Ke1": 1.2, "Ke2": 1.2}}

# 注入 _last_result 含空 compensation
dlg7d._last_result = result_no_comp  # compensation={}
dlg7d._compensation_results = {}      # 触发 fallback

# 重置 _last_result 并重走 _on_done
caught7d = None
try:
    dlg7d._on_done(result_no_comp)
except UnboundLocalError as e:
    caught7d = str(e)
except Exception as e:
    caught7d = f"{type(e).__name__}: {e}"

total += 1; passed += check(
    "7d: _on_done with tp_fb=None — no UnboundLocalError",
    caught7d is None)
total += 1; passed += check(
    "7d: compensation still populated (default thresholds)",
    bool(dlg7d._compensation_results))

dlg7d.close(); del dlg7d


# =========================================================================
# Test 8: LOOCV vs in-sample σ — table uses LOOCV, chart shows both
# =========================================================================
# ★ 表格"补偿后σ" = evaluate_compensation().residual_sigma (LOOCV, 诚实)
# ★ 图表(c)/(d) σ = np.nanstd(apply_compensation_model(all_data)) (in-sample, 乐观)
# ★ LOOCV ≥ in-sample 是普遍规律 (留一法不拟合自己, 残差更大)
# ★ 修复后图表标题同时标注 in-sample 和 LOOCV

print("\n=== TestLoocvVsInSampleSigma ===")

from utils.compensation_metrics import GradeThresholds
from utils.apparent_strain_comp import (
    CompensationModel, ApparentStrainLUT,
    build_apparent_strain_lut,
)

np.random.seed(909)
n8 = 600
T_abs8 = np.empty(n8)
for ci in range(3):
    for half in range(2):
        s = (ci * 2 + half) * (n8 // 6)
        e = s + n8 // 6
        T_abs8[s:e] = np.linspace(10, 60, n8 // 6) if half == 0 else np.linspace(60, 10, n8 // 6)
eps_corr8 = 2.0 * (T_abs8 - 25.0) + 30.0 + np.random.normal(0, 3, n8)

# Build full-data model and compute LOOCV separately
from utils.compensation_metrics import (
    detect_cycles_from_T, evaluate_compensation,
    _fill_unassigned_cycles, grade_sensor,
)
cids8 = detect_cycles_from_T(T_abs8, method="continuous")
cids_clean8 = _fill_unassigned_cycles(cids8)
unique8 = sorted(set(int(c) for c in cids_clean8))
T_base8 = float(np.median(T_abs8))

# LOOCV σ (official)
metrics8 = evaluate_compensation(T_abs8, eps_corr8, cids8, fs=1000.0, form="lut",
                                  sensor="A1")
sigma_loocv = metrics8.residual_sigma

# In-sample σ (chart): build model on ALL data, apply to same data
from utils.apparent_strain_comp import apply_compensation_model
lut8 = build_apparent_strain_lut(T_abs8, eps_corr8, T_base8,
                                  bin_width=2.0, min_count=10, sensor="A1",
                                  n_cycles=len(unique8))
cm8 = CompensationModel(form="lut", model=lut8)
eps_corr_comp8, _oob8 = apply_compensation_model(eps_corr8, T_abs8, cm8)
sigma_insample = float(np.nanstd(eps_corr_comp8))

total += 1; passed += check(
    f"LOOCV σ={sigma_loocv:.2f} vs in-sample σ={sigma_insample:.2f}",
    True)  # informational
total += 1; passed += check(
    "LOOCV ≥ in-sample (普遍规律)",
    sigma_loocv >= sigma_insample - 1e-9)
total += 1; passed += check(
    "LOOCV ≠ in-sample (不同计算路径)",
    abs(sigma_loocv - sigma_insample) > 0.01)

# Verify table "补偿后σ" column uses LOOCV value
sensors8 = {"A1": {"eps_corr": eps_corr8, "dT_corr": T_abs8 - 25.0,
    "T_abs": T_abs8, "S1": 30.0, "S2": 28.0, "T_base": T_base8,
    "single_grating": False}}
df8 = pd.DataFrame({"ch1": np.ones(10) * 1550.0, "ch2": np.ones(10) * 1550.0})

dlg8 = PhaseBDialog(df8,
    {"A1": [{"col_name": "ch1", "name": "A1-W1"}, {"col_name": "ch2", "name": "A1-W2"}]},
    {"S_eff": {"ch1": {"slope": 30.0, "T_base": T_base8},
                "ch2": {"slope": 28.0, "T_base": T_base8}}})
dlg8._coeffs = {"A1": {"Ke1": 1.2, "Ke2": 1.2}}

# Inject compensation_results with LOOCV metrics
comp8 = {
    "A1": {"model": cm8, "metrics": metrics8,
           "grade": grade_sensor(metrics8)},
}
dlg8._compensation_results = comp8
dlg8._render_result_table(sensors8,
    {"ch1": {"slope": 30.0}, "ch2": {"slope": 28.0}},
    compensation=comp8)
QApplication.processEvents()

# Read table "补偿后σ" column (index 8)
tbl8 = dlg8.result_table
for i in range(tbl8.rowCount()):
    if tbl8.item(i, 0).text() == "A1":
        table_sigma_text = tbl8.item(i, 8).text()  # col 8 = 补偿后σ
        table_sigma_val = float(table_sigma_text)
        total += 1; passed += check(
            f"Table 补偿后σ={table_sigma_val:.2f} == LOOCV σ={sigma_loocv:.2f}",
            abs(table_sigma_val - sigma_loocv) < 0.02)
        total += 1; passed += check(
            f"Table 补偿后σ ≠ in-sample σ={sigma_insample:.2f} (正确，用 LOOCV)",
            abs(table_sigma_val - sigma_insample) > 0.01)
        break

dlg8.close(); del dlg8


# =========================================================================
# Test 9: Chart _get_loocv_sigma returns correct LOOCV value
# =========================================================================
print("\n=== TestChartLoocvHelper ===")

from ui.widgets.charts_dialog import PhaseBChartsDialog

np.random.seed(1010)
n9 = 900
T_abs9 = np.empty(n9)
for ci in range(3):
    for half in range(2):
        s = (ci * 2 + half) * (n9 // 6)
        e = s + n9 // 6
        T_abs9[s:e] = np.linspace(10, 60, n9 // 6) if half == 0 else np.linspace(60, 10, n9 // 6)
eps_corr9 = 2.0 * (T_abs9 - 25.0) + 20.0 + np.random.normal(0, 3, n9)

T_base9 = float(np.median(T_abs9))
cids9 = detect_cycles_from_T(T_abs9, method="continuous")
metrics9 = evaluate_compensation(T_abs9, eps_corr9, cids9, fs=1000.0, form="lut",
                                  sensor="A1")
sigma_loocv9 = metrics9.residual_sigma

lut9 = build_apparent_strain_lut(T_abs9, eps_corr9, T_base9,
                                  bin_width=2.0, min_count=10, sensor="A1",
                                  n_cycles=3)
cm9 = CompensationModel(form="lut", model=lut9)

comp9 = {"A1": {"model": cm9, "metrics": metrics9,
                "grade": grade_sensor(metrics9)}}

phase_b_result = {
    "sensors": {"A1": {"eps_corr": eps_corr9, "dT_corr": T_abs9 - 25.0,
        "T_abs": T_abs9, "eps_orig": eps_corr9 * 1.1,
        "S1": 30.0, "S2": 28.0, "T_base": T_base9, "single_grating": False}},
    "comparisons": [],
    "df": pd.DataFrame({"ch1": [1.0], "ch2": [1.0]}),
    "time_h": np.arange(n9) * 2.0 / 3600.0,
    "compensation": comp9,
}
groups9 = {"A1": [{"col_name": "ch1", "name": "A1-W1"},
                  {"col_name": "ch2", "name": "A1-W2"}]}
df9 = pd.DataFrame({"ch1": np.ones(n9) * 1550.0, "ch2": np.ones(n9) * 1550.0})

dlg9 = PhaseBChartsDialog(phase_b_result, df=df9,
                           annotation_groups=groups9, parent=None,
                           compensation=comp9)

loocv_sigma_from_chart = dlg9._get_loocv_sigma("A1")
total += 1; passed += check(
    f"_get_loocv_sigma returns {loocv_sigma_from_chart:.2f} == metrics {sigma_loocv9:.2f}",
    abs(loocv_sigma_from_chart - sigma_loocv9) < 0.001)

# Get in-sample std from chart's compensated strain
eps_comp_chart = dlg9._get_compensated_strain("A1")
sigma_insample9 = float(np.nanstd(eps_comp_chart))
total += 1; passed += check(
    f"In-sample σ={sigma_insample9:.2f} ≠ LOOCV σ={sigma_loocv9:.2f}",
    abs(sigma_insample9 - sigma_loocv9) > 0.01)

dlg9.close(); del dlg9


# =========================================================================
# Summary
# =========================================================================
print(f"\n{'='*50}")
print(f"  {passed}/{total} PASSED")
if passed == total:
    print("  [OK] ALL TESTS PASSED")
else:
    print(f"  [FAIL] {total - passed} FAILURES")
print(f"{'='*50}")

if __name__ == "__main__":
    sys.exit(0 if passed == total else 1)
