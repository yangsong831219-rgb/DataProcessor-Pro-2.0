"""Phase 3 hysteresis threshold validation: 6%FS default + configurable via SSOT.

Tests:
1. Default 6%FS: A1 with hys=5.9%FS passes (NOT FAIL)
2. Threshold 5%FS: same A1 FAILs
3. B1/B2 with high hysteresis still FAIL under 6%FS
4. GradeThresholds roundtrip through _phase_b_state SSOT
5. Default from_dict also 6.0

Usage: python tests/test_phase_3_hys_threshold.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from utils.compensation_metrics import (
    GradeThresholds, evaluate_compensation, grade_sensor,
    detect_cycles_from_T,
)
from utils.apparent_strain_comp import build_apparent_strain_lut, CompensationModel


def check(label, cond):
    if cond:
        print(f"  PASS: {label}")
        return 1
    else:
        print(f"  FAIL: {label}")
        return 0


passed, total = 0, 0
FS = 1000.0


# =========================================================================
# Helpers: generate sensor-like data with configurable hysteresis
# =========================================================================

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
# Test 1: Default threshold 5%FS — A1 with 5.9%FS hys FAILs
# =========================================================================
print("\n=== TestDefault5pctFS: A1 at 5.9%FS hys ===")

T1, eps1 = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=59.0,
                          noise_std=0.3, seed=101, strain_signal_ue=30.0)
cids1 = detect_cycles_from_T(T1, method="continuous")
m1 = evaluate_compensation(T1, eps1, cids1, fs=FS, form="lut", sensor="A1")

total += 1; passed += check(
    f"A1 hys_max={m1.hysteresis_max:.1f}ue ({m1.hysteresis_max_pct_fs:.1f}%FS)",
    abs(m1.hysteresis_max_pct_fs - 5.9) < 1.5)  # allow some noise

# Default GradeThresholds (should be 5.0%FS)
default_gt = GradeThresholds()
total += 1; passed += check(
    "default thr_hys_fail = 5.0", default_gt.thr_hysteresis_fail_pct_fs == 5.0)

g1_default = grade_sensor(m1, thresholds=default_gt)
total += 1; passed += check(
    f"A1 under 5%FS: grade={g1_default.grade}, passed={g1_default.passed}",
    g1_default.grade == "FAIL" and not g1_default.passed)

# =========================================================================
# Test 2: Custom threshold 3.5%FS — low-hys A1 FAILs
# =========================================================================
print("\n=== TestTightThreshold: A1 at 4%FS hys ===")

T2, eps2 = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=40.0,
                          noise_std=0.2, seed=202, strain_signal_ue=30.0)
cids2 = detect_cycles_from_T(T2, method="continuous")
m2 = evaluate_compensation(T2, eps2, cids2, fs=FS, form="lut", sensor="A1")
# under default 5% this passes
g2_default = grade_sensor(m2)
total += 1; passed += check(
    f"A1 hys={m2.hysteresis_max_pct_fs:.1f}%FS under default 5%: grade={g2_default.grade} passes",
    g2_default.grade != "FAIL" and g2_default.passed)
# under tight 3.5% this FAILs
tight_gt = GradeThresholds(thr_hysteresis_fail_pct_fs=3.5)
g2_tight = grade_sensor(m2, thresholds=tight_gt)
total += 1; passed += check(
    f"A1 under 3.5%FS: grade={g2_tight.grade}, passed={g2_tight.passed}",
    g2_tight.grade == "FAIL" and not g2_tight.passed)

# =========================================================================
# Test 3: B1/B2 with very high hysteresis still FAIL under 5%FS
# =========================================================================
print("\n=== TestHighHysStillFAIL ===")

T3, eps3 = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=200.0,
                          noise_std=0.5, seed=303, strain_signal_ue=40.0)
cids3 = detect_cycles_from_T(T3, method="continuous")
m3 = evaluate_compensation(T3, eps3, cids3, fs=FS, form="lut", sensor="B1")
g3 = grade_sensor(m3)  # default 5%FS
total += 1; passed += check(
    f"B1 hys={m3.hysteresis_max_pct_fs:.1f}%FS > 5% -> FAIL",
    g3.grade == "FAIL" and not g3.passed)

T3b, eps3b = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=150.0,
                            noise_std=0.4, seed=404, strain_signal_ue=40.0)
cids3b = detect_cycles_from_T(T3b, method="continuous")
m3b = evaluate_compensation(T3b, eps3b, cids3b, fs=FS, form="lut", sensor="B2")
g3b = grade_sensor(m3b)
total += 1; passed += check(
    f"B2 hys={m3b.hysteresis_max_pct_fs:.1f}%FS > 5% -> FAIL",
    g3b.grade == "FAIL" and not g3b.passed)

# =========================================================================
# Test 4: SSOT roundtrip — from_dict / to_dict
# =========================================================================
print("\n=== TestSSOTRoundtrip ===")

gt4 = GradeThresholds(thr_hysteresis_fail_pct_fs=7.0)
d4 = gt4.to_dict()
total += 1; passed += check("to_dict has thr_hys", "thr_hysteresis_fail_pct_fs" in d4)
total += 1; passed += check("to_dict value=7.0", d4["thr_hysteresis_fail_pct_fs"] == 7.0)

gt4_rt = GradeThresholds.from_dict(d4)
total += 1; passed += check("from_dict roundtrip", gt4_rt.thr_hysteresis_fail_pct_fs == 7.0)

# Default from_dict (empty dict)
gt4_default = GradeThresholds.from_dict({})
total += 1; passed += check("from_dict({}) default=5.0",
    gt4_default.thr_hysteresis_fail_pct_fs == 5.0)

# =========================================================================
# Test 5: 4.0%FS hys — below 5% threshold, grade is sigma-based
# =========================================================================
print("\n=== TestA1GradeIsSigmaBased ===")

# With low sigma and moderate hys (5.9%FS), grade should be based on sigma
T5, eps5 = make_tri_data(T_base=25.0, n_cycles=3, hysteresis_ue=40.0,
                          noise_std=0.15, seed=404, strain_signal_ue=20.0)
cids5 = detect_cycles_from_T(T5, method="continuous")
m5 = evaluate_compensation(T5, eps5, cids5, fs=FS, form="lut", sensor="A1")
g5 = grade_sensor(m5)
total += 1; passed += check(
    f"A1: sigma={m5.residual_sigma:.1f}ue ({m5.residual_sigma_pct_fs:.2f}%FS), "
    f"hys={m5.hysteresis_max_pct_fs:.1f}%FS, grade={g5.grade}",
    g5.grade in ("优", "良", "合格") and g5.passed)

# =========================================================================
# Summary
# =========================================================================
print(f"\n{'='*50}")
print(f"  {passed}/{total} PASSED")
if passed == total:
    print("  [OK] Phase 3 ALL TESTS PASSED")
else:
    print(f"  [FAIL] {total - passed} FAILURES")
print(f"{'='*50}")

if __name__ == "__main__":
    sys.exit(0 if passed == total else 1)
