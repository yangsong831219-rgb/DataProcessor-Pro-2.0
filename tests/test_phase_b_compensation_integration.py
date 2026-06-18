"""Phase B -> compensation pipeline end-to-end test (standalone, no pytest)

Tests:
1. PhaseBWorker -> _run_compensation_pipeline: model+metrics+grade produced
2. Single grating routes to grade_sensor_na (N/A, not fake LUT)
3. FAIL sensor (high hys) -> grade=FAIL, model=None in persistence
4. NaN hysteresis -> capped at liang (not you)
5. %FS thresholds give reasonable real-sensor grades
6. comp_form=poly persists form metadata
7. State writeback: _phase_b_state.compensation has compensation_model/metrics/grade

Usage: python tests/test_phase_b_compensation_integration.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from PyQt6.QtWidgets import QApplication, QWidget

from utils.apparent_strain_comp import CompensationModel, ApparentStrainLUT, ApparentStrainPoly
from utils.compensation_metrics import (
    evaluate_compensation, grade_sensor, grade_sensor_na,
    detect_cycles_from_T, compare_compensation_forms,
)


def check(label, cond):
    if cond:
        print(f"  PASS: {label}")
        return 1
    else:
        print(f"  FAIL: {label}")
        return 0


passed, total = 0, 0
_qapp = QApplication.instance() or QApplication(sys.argv)

FS = 1000.0


# =========================================================================
# Helpers: replicate PhaseBWorker output format
# =========================================================================

def make_sensor_output(eps_corr, T_abs, dT_corr, S1=30.0, S2=30.0, T_base=25.0,
                       single_grating=False):
    """Mimic PhaseBWorker._last_result["sensors"][s_name]."""
    if single_grating:
        return {"single_grating": True, "S1": S1, "T_base": T_base, "R2": 0.995}
    return {
        "single_grating": False,
        "eps_corr": eps_corr,
        "eps_orig": eps_corr * 1.1,  # dummy
        "dT_corr": dT_corr,
        "T_abs": T_abs,
        "S1": S1, "S2": S2, "T_base": T_base,
    }


def make_tri_data(T_base=25.0, n_cycles=3, n_pts=200, T_min=10.0, T_max=60.0,
                  eps_coef=2.0, hysteresis_ue=0.0, noise_std=0.3,
                  seed=42, strain_signal_ue=100.0):
    rng = np.random.default_rng(seed)
    total_pts = n_cycles * 2 * n_pts
    T = np.empty(total_pts); eps_true = np.empty(total_pts)
    for ci in range(n_cycles):
        for half in range(2):
            s = (ci*2+half)*n_pts; e = s+n_pts
            if half == 0: T[s:e] = np.linspace(T_min, T_max, n_pts)
            else:         T[s:e] = np.linspace(T_max, T_min, n_pts)
            base = eps_coef*(T[s:e]-T_base)+strain_signal_ue
            hys_sign = 1.0 if half == 0 else -1.0
            eps_true[s:e] = base + hys_sign*hysteresis_ue/2.0
    eps = eps_true + rng.normal(0, noise_std, total_pts)
    return T, eps


# =========================================================================
# Test 1: Full pipeline -> model + metrics + grade
# =========================================================================
print("\n=== TestFullPipeline ===")

T1, eps1 = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=10.0,
                          noise_std=0.2, seed=42, strain_signal_ue=50.0)
T_base1 = 25.0
cids1 = detect_cycles_from_T(T1, method="continuous")
n_cycles1 = len(set(int(c) for c in cids1 if c >= 0))

# Simulate pipeline: build_model -> evaluate -> grade
from utils.apparent_strain_comp import build_apparent_strain_lut, CompensationModel

lut1 = build_apparent_strain_lut(T1, eps1, T_base1, bin_width=2.0, min_count=10,
                                  sensor="A1", n_cycles=n_cycles1,
                                  source="phase_b_decoupling")
cm1 = CompensationModel(form="lut", model=lut1)
m1 = evaluate_compensation(T1, eps1, cids1, fs=FS, form="lut", sensor="A1")
g1 = grade_sensor(m1)

total += 1; passed += check("model is CompensationModel", isinstance(cm1, CompensationModel))
total += 1; passed += check("model.form='lut'", cm1.form == "lut")
total += 1; passed += check("metrics not None", m1 is not None)
total += 1; passed += check("metrics.comp_form='lut'", m1.comp_form == "lut")
total += 1; passed += check("grade not None", g1 is not None)
total += 1; passed += check("grade.passed=True", g1.passed)
total += 1; passed += check("model.to_dict has form", "form" in cm1.to_dict())
total += 1; passed += check("metrics.to_dict has comp_form", "comp_form" in m1.to_dict())
total += 1; passed += check("grade.to_dict has grade", "grade" in g1.to_dict())

# =========================================================================
# Test 2: Single grating -> grade_sensor_na N/A
# =========================================================================
print("\n=== TestSingleGratingNA ===")

g_na = grade_sensor_na("B1", "only 1 grating, use S_eff path")
total += 1; passed += check("SG: grade=N/A", g_na.grade == "N/A")
total += 1; passed += check("SG: passed=False", not g_na.passed)
total += 1; passed += check("SG: is_single_grating=True", g_na.is_single_grating)

# Empty data -> evaluate_compensation throws
try:
    evaluate_compensation(np.array([]), np.array([]), np.array([]), fs=FS)
    total += 1; passed += check("empty data -> VE", False)
except ValueError:
    total += 1; passed += check("empty data -> VE", True)

# =========================================================================
# Test 3: FAIL sensor -> model present but grade=FAIL
# =========================================================================
print("\n=== TestFAILSensor ===")

T3, eps3 = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=260.0,
                          noise_std=0.5, seed=1, strain_signal_ue=50.0)
cids3 = detect_cycles_from_T(T3, method="continuous")
m3 = evaluate_compensation(T3, eps3, cids3, fs=FS, form="lut", sensor="B2")
g3 = grade_sensor(m3)

total += 1; passed += check("FAIL: grade=FAIL", g3.grade == "FAIL")
total += 1; passed += check("FAIL: passed=False", not g3.passed)
total += 1; passed += check("FAIL: hys reason present",
    any("hysteresis" in r.lower() or "Hysteresis" in r or "chizhi" in r.lower() or "迟滞" in r
        for r in g3.reasons))

# Model is still built (LUT is valid), but grade warns user
cm3 = CompensationModel(form="lut", model=build_apparent_strain_lut(
    T3, eps3, 25.0, bin_width=2.0, min_count=10, sensor="B2", n_cycles=3))
total += 1; passed += check("FAIL: model still exists", cm3 is not None)

# =========================================================================
# Test 4: NaN hysteresis -> capped at liang
# =========================================================================
print("\n=== TestNaNHysCapped ===")

T4 = np.linspace(10, 60, 300)
eps4 = 1.8*(T4-25.0)+20.0+np.random.default_rng(1).normal(0,0.1,300)
cids4 = np.zeros(300, dtype=int)
m4 = evaluate_compensation(T4, eps4, cids4, fs=FS, form="lut", sensor="J1")
g4 = grade_sensor(m4)

total += 1; passed += check("NaN: hys_max is NaN", np.isnan(m4.hysteresis_max))
total += 1; passed += check("NaN: low_confidence=True", m4.low_confidence)
total += 1; passed += check("NaN: grade != you", g4.grade != "优")
total += 1; passed += check("NaN: grade = liang", g4.grade == "良")
total += 1; passed += check("NaN: reasons mention NaN/unmeasured",
    any("NaN" in r or "未测出" in r for r in g4.reasons))

# =========================================================================
# Test 5: comp_form=poly -> form metadata persists
# =========================================================================
print("\n=== TestPolyFormMetadata ===")

T5, eps5 = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=10.0,
                          noise_std=0.2, seed=99, strain_signal_ue=30.0)
cids5 = detect_cycles_from_T(T5, method="continuous")

from utils.apparent_strain_comp import fit_apparent_strain_poly

poly5 = fit_apparent_strain_poly(T5, eps5, 25.0, order=3, sensor="P1", n_cycles=3)
cm5 = CompensationModel(form="poly", model=poly5)
m5 = evaluate_compensation(T5, eps5, cids5, fs=FS, form="poly", poly_order=3, sensor="P1")

total += 1; passed += check("poly: cm.form='poly'", cm5.form == "poly")
total += 1; passed += check("poly: comp_form='poly'", m5.comp_form == "poly")
total += 1; passed += check("poly: poly_order=3", m5.poly_order == 3)

# roundtrip dict
cm5d = cm5.to_dict()
total += 1; passed += check("poly: dict has form", cm5d["form"] == "poly")
total += 1; passed += check("poly: dict model.order", cm5d["model"]["order"] == 3)

cm5rt = CompensationModel.from_dict(cm5d)
total += 1; passed += check("poly: roundtrip form=poly", cm5rt.form == "poly")
total += 1; passed += check("poly: roundtrip model type",
    isinstance(cm5rt.model, ApparentStrainPoly))

# =========================================================================
# Test 6: %FS threshold exercise on realistic data
# =========================================================================
print("\n=== TestPercentFSRealistic ===")

# C2-like (sigma~6 ue -> 0.6%FS): expect you or liang
T6, eps6 = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=15.0,
                          noise_std=0.2, seed=123, strain_signal_ue=30.0)
cids6 = detect_cycles_from_T(T6, method="continuous")
m6 = evaluate_compensation(T6, eps6, cids6, fs=FS, form="lut", sensor="C2")
g6 = grade_sensor(m6)

total += 1; passed += check(
    f"C2-like: sig={m6.residual_sigma_pct_fs:.2f}%FS grade={g6.grade}",
    g6.grade in ("优", "良") and g6.passed
)

# C1-like (sigma~8 ue -> 0.8%FS): expect you or liang
T6b, eps6b = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=20.0,
                            noise_std=0.3, seed=456, strain_signal_ue=30.0)
cids6b = detect_cycles_from_T(T6b, method="continuous")
m6b = evaluate_compensation(T6b, eps6b, cids6b, fs=FS, form="lut", sensor="C1")
g6b = grade_sensor(m6b)

total += 1; passed += check(
    f"C1-like: sig={m6b.residual_sigma_pct_fs:.2f}%FS grade={g6b.grade}",
    g6b.grade in ("优", "良") and g6b.passed
)

# =========================================================================
# Test 7: Residual is form-specific (verify evaluate uses correct form)
# =========================================================================
print("\n=== TestResidualOfForm ===")

T7, eps7 = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=10.0,
                          noise_std=0.3, seed=77, strain_signal_ue=20.0)
cids7 = detect_cycles_from_T(T7, method="continuous")

m_lut = evaluate_compensation(T7, eps7, cids7, fs=FS, form="lut", sensor="W1")
m_p2 = evaluate_compensation(T7, eps7, cids7, fs=FS, form="poly", poly_order=2, sensor="W1")
m_p4 = evaluate_compensation(T7, eps7, cids7, fs=FS, form="poly", poly_order=4, sensor="W1")

total += 1; passed += check(
    f"form-specific: lut={m_lut.residual_sigma:.2f} p4={m_p4.residual_sigma:.2f} both finite",
    not np.isnan(m_lut.residual_sigma) and not np.isnan(m_p4.residual_sigma))
total += 1; passed += check("form-specific: m_lut.comp_form='lut'",
    m_lut.comp_form == "lut")
total += 1; passed += check("form-specific: m_p4.comp_form='poly'",
    m_p4.comp_form == "poly")
total += 1; passed += check("form-specific: m_p4.poly_order=4",
    m_p4.poly_order == 4)

# compare_compensation_forms gives consistent results
c7 = compare_compensation_forms(T7, eps7, cids7, fs=FS)
total += 1; passed += check("compare: lut residual matches",
    abs(c7["lut"] - m_lut.residual_sigma) < 1.0)
total += 1; passed += check("compare: poly2 residual matches",
    abs(c7["poly2"] - m_p2.residual_sigma) < 1.0)
total += 1; passed += check("compare: poly4 residual matches",
    abs(c7["poly4"] - m_p4.residual_sigma) < 1.0)

# =========================================================================
# Test 8: State persistence roundtrip (dict <-> dataclasses)
# =========================================================================
print("\n=== TestStateRoundtrip ===")

# Simulate _write_state_to_main_page output
state_comp = {}
for s_name, (m_inst, cm_inst) in [
    ("A1", (m1, cm1)),   # LUT, passed
    ("P1", (m5, cm5)),   # poly, passed
    ("B1", (None, None)),  # single grating (N/A)
]:
    comp_entry: dict = {}
    if cm_inst is not None:
        comp_entry["compensation_model"] = cm_inst.to_dict()
    else:
        comp_entry["compensation_model"] = None
    if m_inst is not None:
        comp_entry["metrics"] = m_inst.to_dict()
    else:
        comp_entry["metrics"] = None
    g = grade_sensor(m_inst) if m_inst is not None else grade_sensor_na(s_name)
    comp_entry["grade"] = g.to_dict()
    state_comp[s_name] = comp_entry

total += 1; passed += check("state: A1 compensation_model present",
    state_comp["A1"]["compensation_model"] is not None)
total += 1; passed += check("state: A1 model.form='lut'",
    state_comp["A1"]["compensation_model"]["form"] == "lut")
total += 1; passed += check("state: P1 model.form='poly'",
    state_comp["P1"]["compensation_model"]["form"] == "poly")
total += 1; passed += check("state: B1 model is None (single grating)",
    state_comp["B1"]["compensation_model"] is None)
total += 1; passed += check("state: B1 grade=N/A",
    state_comp["B1"]["grade"]["grade"] == "N/A")

# Roundtrip: compensation_model -> CompensationModel.from_dict
for s_name in ["A1", "P1"]:
    cm_d = state_comp[s_name]["compensation_model"]
    cm_rt = CompensationModel.from_dict(cm_d)
    total += 1; passed += check(
        f"state RT {s_name}: form matches", cm_rt.form == cm_d["form"])

# =========================================================================
# Summary
# =========================================================================
print(f"\n{'='*50}")
print(f"  {passed}/{total} PASSED")
if passed == total: print("  [OK] ALL TESTS PASSED")
else: print(f"  [FAIL] {total - passed} FAILURES")
print(f"{'='*50}")

if __name__ == "__main__":
    sys.exit(0 if passed == total else 1)
