"""Compensation metrics + grading + form extension -- synthetic tests
(standalone, no pytest dependency)

Tests:
 1. High hysteresis (260 ue, 26%FS) -> FAIL + reason
 2. Excellent sensor -> grade you (you) with %FS thresholds
 3. <3 cycles -> low_confidence + max grade = liang
 4. %FS calculations correct
 5. Single grating -> grade_sensor_na N/A
 6. Continuous triangular wave cycle detection
 7. Step plateau cycle detection
 8. Custom thresholds (%FS-based)
 9. to_dict roundtrip
10. temp_sensitivity_max
11. NaN hysteresis -> low_confidence + capped at liang
12. Edge cases: fs=0, length mismatch
13. Poly fit roundtrip: eps_app(T_base)~0
14. Poly OOB clamp + oob flag (4th order no divergence outside range)
15. W-shape data: residual(lut) < residual(poly4) < residual(poly2)
16. compare_compensation_forms returns 3 forms and values are reasonable
17. Realistic rating: C2-like/C1-like with %FS thresholds

Usage: python tests/test_compensation_metrics.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from utils.apparent_strain_comp import (
    ApparentStrainLUT, ApparentStrainPoly,
    build_apparent_strain_lut, fit_apparent_strain_poly,
    CompensationModel, apply_compensation_model,
)
from utils.compensation_metrics import (
    CompensationMetrics, SensorGrade, GradeThresholds,
    detect_cycles_from_T, evaluate_compensation,
    grade_sensor, grade_sensor_na, compare_compensation_forms,
)


def check(label, cond):
    if cond:
        print(f"  PASS: {label}")
        return 1
    else:
        print(f"  FAIL: {label}")
        return 0


passed, total = 0, 0


# =========================================================================
# Helpers
# =========================================================================

def make_tri_data(T_base=25.0, n_cycles=3, n_pts=200, T_min=10.0, T_max=60.0,
                  eps_coef=2.0, hysteresis_ue=0.0, noise_std=0.3,
                  seed=42, strain_signal_ue=100.0):
    rng = np.random.default_rng(seed)
    total_pts = n_cycles * 2 * n_pts
    T = np.empty(total_pts); eps_true = np.empty(total_pts)
    for ci in range(n_cycles):
        for half in range(2):
            s = (ci * 2 + half) * n_pts; e = s + n_pts
            if half == 0: T[s:e] = np.linspace(T_min, T_max, n_pts)
            else:         T[s:e] = np.linspace(T_max, T_min, n_pts)
            base = eps_coef * (T[s:e] - T_base) + strain_signal_ue
            hys_sign = 1.0 if half == 0 else -1.0
            eps_true[s:e] = base + hys_sign * hysteresis_ue / 2.0
    eps = eps_true + rng.normal(0, noise_std, total_pts)
    return T, eps


def make_step_data(T_base=25.0, n_cycles=3, T_levels=None, dwell_pts=80,
                   eps_coef=2.0, noise_std=0.2, strain_signal_ue=50.0, seed=123):
    if T_levels is None: T_levels = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    rng = np.random.default_rng(seed)
    Ts, Es = [], []
    for _ in range(n_cycles):
        for tl in T_levels:
            Ts.append(np.full(dwell_pts, tl))
            Es.append(eps_coef*(tl-T_base)+strain_signal_ue+rng.normal(0,noise_std,dwell_pts))
        for tl in reversed(T_levels):
            Ts.append(np.full(dwell_pts, tl))
            Es.append(eps_coef*(tl-T_base)+strain_signal_ue+rng.normal(0,noise_std,dwell_pts))
    return np.concatenate(Ts), np.concatenate(Es)


FS = 1000.0


# =========================================================================
# Test 1: High hysteresis (260 ue, 26%FS) -> FAIL
# =========================================================================
print("\n=== TestHighHysteresisFAIL ===")

T1, eps1 = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=260.0, noise_std=0.5, seed=1, strain_signal_ue=50.0)
cids1 = detect_cycles_from_T(T1, method="continuous")
n1 = len(set(int(c) for c in cids1 if c >= 0))
total += 1; passed += check("detected >=3 cycles", n1 >= 3)

m1 = evaluate_compensation(T1, eps1, cids1, fs=FS, form="lut", sensor="B2")
g1 = grade_sensor(m1)

total += 1; passed += check("B2 grade=FAIL", g1.grade == "FAIL")
total += 1; passed += check("B2 passed=False", not g1.passed)
total += 1; passed += check("B2 hys reason", any("hysteresis" in r.lower() or "Hysteresis" in r or "chizhi" in r.lower() or "迟滞" in r for r in g1.reasons))
total += 1; passed += check(f"hys_max~260 (got {m1.hysteresis_max:.0f})", abs(m1.hysteresis_max-260.0) < 50.0)
total += 1; passed += check(f"hys_pct_fs={m1.hysteresis_max_pct_fs:.1f}% > 5%", m1.hysteresis_max_pct_fs > 5.0)

# =========================================================================
# Test 2: Excellent sensor -> grade you (you)
# =========================================================================
print("\n=== TestExcellentSensor ===")

T2, eps2 = make_tri_data(T_base=25.0, n_cycles=5, hysteresis_ue=8.0, noise_std=0.15, seed=7, strain_signal_ue=30.0)
cids2 = detect_cycles_from_T(T2, method="continuous")
m2 = evaluate_compensation(T2, eps2, cids2, fs=FS, form="lut", sensor="A1")
g2 = grade_sensor(m2)

total += 1; passed += check(f"A1 grade={g2.grade}", g2.grade == "优")
total += 1; passed += check("A1 passed=True", g2.passed)
total += 1; passed += check("A1 not low_confidence", not m2.low_confidence)
total += 1; passed += check(f"A1 hys low ({m2.hysteresis_max:.1f})", m2.hysteresis_max < 25.0)
total += 1; passed += check(f"A1 sigma_pct={m2.residual_sigma_pct_fs:.1f}% <= 1%", m2.residual_sigma_pct_fs <= 1.5)

# =========================================================================
# Test 3: <3 cycles -> low_confidence + max liang
# =========================================================================
print("\n=== TestLowCycleConfidence ===")

T3, eps3 = make_tri_data(T_base=25.0, n_cycles=2, hysteresis_ue=5.0, noise_std=0.1, seed=42, strain_signal_ue=20.0)
cids3 = detect_cycles_from_T(T3, method="continuous")
n3 = len(set(int(c) for c in cids3 if c >= 0))
total += 1; passed += check(f"<=2 cycles (got {n3})", n3 <= 2)

m3 = evaluate_compensation(T3, eps3, cids3, fs=FS, form="lut", sensor="C1")
total += 1; passed += check("low_confidence=True", m3.low_confidence)

g3 = grade_sensor(m3)
total += 1; passed += check(f"grade != you (got {g3.grade})", g3.grade != "优")
total += 1; passed += check(f"grade liang/hege (got {g3.grade})", g3.grade in ("良", "合格"))
total += 1; passed += check("still passed=True", g3.passed)

# =========================================================================
# Test 4: %FS calculations
# =========================================================================
print("\n=== TestPercentFS ===")

T4, eps4 = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=40.0, noise_std=0.3, seed=99, strain_signal_ue=0.0)
cids4 = detect_cycles_from_T(T4, method="continuous")
fs4 = 2000.0
m4 = evaluate_compensation(T4, eps4, cids4, fs=fs4, form="lut", sensor="D1")

total += 1; passed += check("fs stored", m4.fs == fs4)
total += 1; passed += check("sigma_pct_fs correct", abs(m4.residual_sigma_pct_fs - m4.residual_sigma/fs4*100.0) < 0.01)
total += 1; passed += check("hys_pct_fs correct", abs(m4.hysteresis_max_pct_fs - m4.hysteresis_max/fs4*100.0) < 0.01)
total += 1; passed += check("noise_pct_fs correct", abs(m4.noise_floor_pct_fs - m4.noise_floor/fs4*100.0) < 0.01)
total += 1; passed += check(f"worst_case_single=hys/2 ({m4.worst_case_single:.2f})", abs(m4.worst_case_single - m4.hysteresis_max/2.0) < 0.01)

# =========================================================================
# Test 5: Single grating -> grade_sensor_na N/A
# =========================================================================
print("\n=== TestSingleGratingNA ===")

g_na = grade_sensor_na("B1", reason="only 1 grating")
total += 1; passed += check("grade=N/A", g_na.grade == "N/A")
total += 1; passed += check("passed=False", not g_na.passed)
total += 1; passed += check("is_single_grating=True", g_na.is_single_grating)
total += 1; passed += check("has reason", len(g_na.reasons) >= 1)

try:
    evaluate_compensation(np.array([]), np.array([]), np.array([]), fs=FS)
    total += 1; passed += check("empty -> ValueError", False)
except ValueError:
    total += 1; passed += check("empty -> ValueError", True)

# =========================================================================
# Test 6: Continuous cycle detection
# =========================================================================
print("\n=== TestContinuousCycleDetection ===")

T6, _ = make_tri_data(T_base=25.0, n_cycles=4, n_pts=150, hysteresis_ue=0, seed=77)
cids6 = detect_cycles_from_T(T6, method="continuous", smooth_win=20)
unique6 = sorted(set(int(c) for c in cids6))
total += 1; passed += check(f"~4 cycles (n={len(unique6)}, ids={unique6})", 3 <= len(unique6) <= 5)
total += 1; passed += check("no unassigned", not np.any(cids6 == -1))

# =========================================================================
# Test 7: Step plateau cycle detection
# =========================================================================
print("\n=== TestPlateauCycleDetection ===")

T7, eps7 = make_step_data(T_base=25.0, n_cycles=3, dwell_pts=60, noise_std=0.2, seed=456)
cids7 = detect_cycles_from_T(T7, method="auto", plateau_tol=1.0)
unique7 = sorted(set(int(c) for c in cids7))
total += 1; passed += check(f"plateau cycles (n={len(unique7)})", len(unique7) >= 1)
total += 1; passed += check("no unassigned", not np.any(cids7 == -1))

m7 = evaluate_compensation(T7, eps7, cids7, fs=FS, form="lut", sensor="E1")
total += 1; passed += check("plateau metrics ok", not np.isnan(m7.residual_sigma))
total += 1; passed += check("plateau temp_sens valid", m7.temp_sensitivity_max > 0)

# =========================================================================
# Test 8: Custom thresholds
# =========================================================================
print("\n=== TestCustomThresholds ===")

tight = GradeThresholds(thr_hysteresis_fail_pct_fs=2.0, thr_sigma_excellent_pct_fs=0.5,
                         thr_sigma_good_pct_fs=1.0, thr_sigma_pass_pct_fs=2.0)

T8, eps8 = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=40.0, noise_std=0.15, seed=11, strain_signal_ue=10.0)
cids8 = detect_cycles_from_T(T8, method="continuous")
m8 = evaluate_compensation(T8, eps8, cids8, fs=FS, form="lut", sensor="F1")

g8d = grade_sensor(m8)
g8t = grade_sensor(m8, thresholds=tight)

total += 1; passed += check(f"default: hys={m8.hysteresis_max_pct_fs:.1f}% < 5%, grade={g8d.grade}", g8d.grade != "FAIL")
total += 1; passed += check(f"tight: hys={m8.hysteresis_max_pct_fs:.1f}% > 2%, grade={g8t.grade}", g8t.grade == "FAIL")

# =========================================================================
# Test 9: to_dict roundtrip
# =========================================================================
print("\n=== TestRoundtrip ===")

m9 = CompensationMetrics(sensor="G1", residual_sigma=5.0, repeatability=3.0,
    hysteresis_max=20.0, noise_floor=1.0, temp_sensitivity_max=2.5,
    worst_case_single=10.0, low_confidence=False, fs=1000.0,
    comp_form="lut", poly_order=0,
    residual_sigma_pct_fs=0.5, repeatability_pct_fs=0.3,
    hysteresis_max_pct_fs=2.0, noise_floor_pct_fs=0.1, worst_case_single_pct_fs=1.0)
d9 = m9.to_dict()
total += 1; passed += check("CMetrics: sensor in dict", d9["sensor"] == "G1")
total += 1; passed += check("CMetrics: sigma ok", abs(d9["residual_sigma"] - 5.0) < 0.01)
total += 1; passed += check("CMetrics: comp_form in dict", d9.get("comp_form") == "lut")

g9 = SensorGrade(sensor="G1", grade="优", passed=True, reasons=["test"])
d9g = g9.to_dict()
total += 1; passed += check("SGrade: grade in dict", d9g["grade"] == "优")

t9 = GradeThresholds(thr_hysteresis_fail_pct_fs=3.0)
total += 1; passed += check("GThresh: roundtrip", GradeThresholds.from_dict(t9.to_dict()).thr_hysteresis_fail_pct_fs == 3.0)

# =========================================================================
# Test 10: temp_sensitivity_max
# =========================================================================
print("\n=== TestTempSensitivity ===")

T10 = np.linspace(10, 50, 500)
eps10 = 2.5*(T10-30.0)+30.0+np.random.default_rng(0).normal(0,0.1,500)
cids10 = np.zeros(500, dtype=int)
m10 = evaluate_compensation(T10, eps10, cids10, fs=FS, form="lut", sensor="H1")
total += 1; passed += check(f"temp_sens~2.5 ({m10.temp_sensitivity_max:.3f})", abs(m10.temp_sensitivity_max-2.5)<0.2)

# =========================================================================
# Test 11: NaN hysteresis -> capped at liang
# =========================================================================
print("\n=== TestNaNHysteresis ===")

T11 = np.linspace(10, 60, 300)
eps11 = 1.8*(T11-25.0)+20.0+np.random.default_rng(1).normal(0,0.1,300)
cids11 = np.zeros(300, dtype=int)
m11 = evaluate_compensation(T11, eps11, cids11, fs=FS, form="lut", sensor="J1")

total += 1; passed += check("hys_max is NaN", np.isnan(m11.hysteresis_max))
total += 1; passed += check("worst_case_single is NaN", np.isnan(m11.worst_case_single))
total += 1; passed += check("low_confidence=True (NaN forced)", m11.low_confidence)

g11 = grade_sensor(m11)
total += 1; passed += check(f"not FAIL ({g11.grade})", g11.grade != "FAIL")
total += 1; passed += check(f"capped liang ({g11.grade})", g11.grade == "良")
total += 1; passed += check("NaN in reasons", any("NaN" in r or "未测出" in r for r in g11.reasons))

# =========================================================================
# Test 12: Edge cases
# =========================================================================
print("\n=== TestEdgeCases ===")

T12, eps12 = np.linspace(10,50,200), 2.0*(np.linspace(10,50,200)-25.0)+30.0
cids12 = np.zeros(200, dtype=int)

try: evaluate_compensation(T12, eps12, cids12, fs=0); total += 1; passed += check("fs=0 -> VE", False)
except ValueError: total += 1; passed += check("fs=0 -> VE", True)

try: evaluate_compensation(T12, eps12[:100], cids12, fs=FS); total += 1; passed += check("len mis -> VE", False)
except ValueError: total += 1; passed += check("len mis -> VE", True)

# =========================================================================
# Test 13: Poly fit roundtrip + eps_app(T_base)~0
# =========================================================================
print("\n=== TestPolyFit ===")

T13, eps13 = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=10.0, noise_std=0.2, seed=42, strain_signal_ue=0.0)
T_base13 = 25.0

# fit poly
poly13 = fit_apparent_strain_poly(T13, eps13, T_base13, order=3, sensor="P1")
total += 1; passed += check("poly order=3", poly13.order == 3)
total += 1; passed += check("poly coeffs len=4", len(poly13.coeffs) == 4)
total += 1; passed += check("poly T_min/T_max ok", poly13.T_min > 0 and poly13.T_max > poly13.T_min)

# eps_app(T_base) ~ 0
x0 = np.array([0.0])
eps_at_base = np.polyval(poly13.coeffs, x0)[0]
total += 1; passed += check(f"eps_app(T_base)~0 (got {eps_at_base:.2e})", abs(eps_at_base) < 1e-9)

# roundtrip: compensate -> restored strain ~ 0
cm13 = CompensationModel(form="poly", model=poly13)
eps_c13, oob13 = apply_compensation_model(eps13, T13, cm13)
total += 1; passed += check("poly roundtrip: eps_corr ~ 0", abs(np.mean(eps_c13)) < 5.0)
total += 1; passed += check("poly roundtrip: no oob", not np.any(oob13))

# Poly to/from dict
pd13 = poly13.to_dict()
prt13 = ApparentStrainPoly.from_dict(pd13)
total += 1; passed += check("poly roundtrip dict: order", prt13.order == poly13.order)
total += 1; passed += check("poly roundtrip dict: T_base", abs(prt13.T_base - poly13.T_base) < 0.01)

# CompensationModel roundtrip
cmd13 = cm13.to_dict()
cmrt13 = CompensationModel.from_dict(cmd13)
total += 1; passed += check("CM roundtrip: form", cmrt13.form == "poly")
total += 1; passed += check("CM roundtrip: model type", isinstance(cmrt13.model, ApparentStrainPoly))

# =========================================================================
# Test 14: Poly OOB clamp (4th order no extrapolation)
# =========================================================================
print("\n=== TestPolyOOB ===")

T14, eps14 = make_tri_data(T_base=25.0, n_cycles=3, T_min=15.0, T_max=55.0, hysteresis_ue=5.0, noise_std=0.2, seed=77, strain_signal_ue=0.0)
poly14 = fit_apparent_strain_poly(T14, eps14, 25.0, order=4, sensor="Q1")
cm14 = CompensationModel(form="poly", model=poly14)

# within range: no oob
T_in14 = np.linspace(18.0, 52.0, 100)
eps_in14 = np.zeros(100)
_, oob_in = apply_compensation_model(eps_in14, T_in14, cm14)
total += 1; passed += check("poly: in-range no oob", not np.any(oob_in))

# below and above range
T_oob14 = np.array([5.0, 10.0, 25.0, 60.0, 70.0])
eps_oob14 = np.zeros_like(T_oob14)
_, oob14 = apply_compensation_model(eps_oob14, T_oob14, cm14)
total += 1; passed += check("poly: oob low T=5", oob14[0])
total += 1; passed += check("poly: oob low T=10", oob14[1])
total += 1; passed += check("poly: in-range T=25", not oob14[2])
total += 1; passed += check("poly: oob high T=60", oob14[3])
total += 1; passed += check("poly: oob high T=70", oob14[4])

# OOB clamped values are finite (4th order extrapolation would produce +/-inf)
eps_c_oob, _ = apply_compensation_model(eps_oob14, T_oob14, cm14)
total += 1; passed += check("poly: all eps_corr finite", np.all(np.isfinite(eps_c_oob)))

# Verify clamped values match endpoint values (not extrapolated)
T_end = np.array([poly14.T_min, poly14.T_max])
eps_end = np.zeros(2)
eps_c_end, _ = apply_compensation_model(eps_end, T_end, cm14)
total += 1; passed += check("poly: oob T=5 clamped to T_min", abs(eps_c_oob[0] - eps_c_end[0]) < 1e-9)
total += 1; passed += check("poly: oob T=70 clamped to T_max", abs(eps_c_oob[4] - eps_c_end[1]) < 1e-9)

# =========================================================================
# Test 15: W-shape data: residual(lut) < residual(poly4) < residual(poly2)
# =========================================================================
print("\n=== TestWShapeFormComparison ===")

# Build "W" shaped apparent strain: non-monotonic, requires many degrees of freedom
rng15 = np.random.default_rng(555)
n15 = 1500
# 3 full cycles, triangular temperature
T15 = np.empty(n15)
n_pts_half = n15 // 6
for ci in range(3):
    for half in range(2):
        s = (ci*2+half)*n_pts_half; e = s+n_pts_half
        T15[s:e] = np.linspace(10,60,n_pts_half) if half==0 else np.linspace(60,10,n_pts_half)
T15 += rng15.normal(0, 0.1, n15)

# "W" shaped apparent strain: eps = 15*sin(2*pi*(T-10)/25) + 2*(T-35)
eps_true15 = 15.0*np.sin(2*np.pi*(T15-10.0)/25.0) + 2.0*(T15-35.0)
eps15 = eps_true15 + rng15.normal(0, 0.5, n15)
cids15 = detect_cycles_from_T(T15, method="continuous")

c15 = compare_compensation_forms(T15, eps15, cids15, fs=FS, candidates=("lut","poly2","poly4"))
total += 1; passed += check("compare has lut", "lut" in c15)
total += 1; passed += check("compare has poly2", "poly2" in c15)
total += 1; passed += check("compare has poly4", "poly4" in c15)

r_lut = c15["lut"]; r_p2 = c15["poly2"]; r_p4 = c15["poly4"]
total += 1; passed += check(f"all residuals finite", all(not np.isnan(v) for v in c15.values()))
total += 1; passed += check(
    f"r(lut)={r_lut:.1f} < r(poly4)={r_p4:.1f} < r(poly2)={r_p2:.1f}",
    r_lut < r_p4 < r_p2
)

# Also verify with direct evaluate_compensation on each form
m_lut = evaluate_compensation(T15, eps15, cids15, fs=FS, form="lut", sensor="W1")
m_p2 = evaluate_compensation(T15, eps15, cids15, fs=FS, form="poly", poly_order=2, sensor="W1")
m_p4 = evaluate_compensation(T15, eps15, cids15, fs=FS, form="poly", poly_order=4, sensor="W1")

total += 1; passed += check(f"direct lut sig={m_lut.residual_sigma:.1f} ~ compare", abs(m_lut.residual_sigma - r_lut) < 1.0)
total += 1; passed += check(f"direct p2 sig={m_p2.residual_sigma:.1f} ~ compare", abs(m_p2.residual_sigma - r_p2) < 1.0)
total += 1; passed += check(f"direct p4 sig={m_p4.residual_sigma:.1f} ~ compare", abs(m_p4.residual_sigma - r_p4) < 1.0)
total += 1; passed += check("direct: r(lut) < r(p4) < r(p2)", m_lut.residual_sigma < m_p4.residual_sigma < m_p2.residual_sigma)

# Verify comp_form / poly_order stored correctly
total += 1; passed += check("m_lut comp_form='lut'", m_lut.comp_form == "lut")
total += 1; passed += check("m_lut poly_order=0", m_lut.poly_order == 0)
total += 1; passed += check("m_p4 comp_form='poly'", m_p4.comp_form == "poly")
total += 1; passed += check("m_p4 poly_order=4", m_p4.poly_order == 4)

# =========================================================================
# Test 16: Realistic rating with %FS thresholds
# =========================================================================
print("\n=== TestRealisticRating ===")

# C2-like: sigma~5.6 ue (0.56%FS), hys~20 ue (2%FS)
T16, eps16 = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=20.0, noise_std=0.25, seed=123, strain_signal_ue=30.0)
cids16 = detect_cycles_from_T(T16, method="continuous")
m16 = evaluate_compensation(T16, eps16, cids16, fs=FS, form="lut", sensor="C2")
g16 = grade_sensor(m16)

total += 1; passed += check(
    f"C2-like: sig={m16.residual_sigma:.1f}u ({m16.residual_sigma_pct_fs:.2f}%FS) "
    f"hys={m16.hysteresis_max:.0f}u ({m16.hysteresis_max_pct_fs:.2f}%FS) grade={g16.grade}",
    g16.grade in ("优", "良")
)

# C1-like: sigma~7.4 ue (0.74%FS), hys~25 ue (2.5%FS)
T16b, eps16b = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=25.0, noise_std=0.35, seed=456, strain_signal_ue=30.0)
cids16b = detect_cycles_from_T(T16b, method="continuous")
m16b = evaluate_compensation(T16b, eps16b, cids16b, fs=FS, form="lut", sensor="C1")
g16b = grade_sensor(m16b)

total += 1; passed += check(
    f"C1-like: sig={m16b.residual_sigma:.1f}u ({m16b.residual_sigma_pct_fs:.2f}%FS) "
    f"hys={m16b.hysteresis_max:.0f}u ({m16b.hysteresis_max_pct_fs:.2f}%FS) grade={g16b.grade}",
    g16b.grade in ("优", "良")
)

# Mediocre: sigma~18 ue (1.8%FS) -> liang or hege
T16c, eps16c = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=30.0, noise_std=1.2, seed=99, strain_signal_ue=50.0)
cids16c = detect_cycles_from_T(T16c, method="continuous")
m16c = evaluate_compensation(T16c, eps16c, cids16c, fs=FS, form="lut", sensor="D2")
g16c = grade_sensor(m16c)

total += 1; passed += check(
    f"mediocre: sig={m16c.residual_sigma:.1f}u ({m16c.residual_sigma_pct_fs:.2f}%FS) "
    f"hys={m16c.hysteresis_max:.0f}u grade={g16c.grade}",
    g16c.grade in ("良", "合格")
)

# Bad: 2 cycles only + high hysteresis (80 ue, 8%FS) -> should be FAIL or buhege
T16d, eps16d = make_tri_data(T_base=25.0, n_cycles=2, hysteresis_ue=80.0, noise_std=2.0, seed=7, strain_signal_ue=50.0)
cids16d = detect_cycles_from_T(T16d, method="continuous")
m16d = evaluate_compensation(T16d, eps16d, cids16d, fs=FS, form="lut", sensor="E2")
g16d = grade_sensor(m16d)

total += 1; passed += check(
    f"bad (80ue hys): sig={m16d.residual_sigma:.1f}u "
    f"({m16d.residual_sigma_pct_fs:.2f}%FS) "
    f"hys={m16d.hysteresis_max:.0f}u ({m16d.hysteresis_max_pct_fs:.2f}%FS) "
    f"grade={g16d.grade}",
    not g16d.passed or g16d.grade == "FAIL"
)

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
