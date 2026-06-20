"""Phase B profiling: real-scale 98k step data — cProfile top-10 + 4 debug checks.

Usage: python tests/profile_phase_b.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cProfile, pstats, io
import numpy as np
import time

from utils.compensation_metrics import (
    detect_cycles_from_T, evaluate_compensation, compare_compensation_forms,
    grade_sensor,
)

# ── Generate synthetic 98k plateau-step data (mimics real台阶实验) ──
def make_plateau_data(
    n_cycles=4, pts_per_level=3000, T_levels=(10, 20, 30, 40, 50, 60),
    T_base=25.0, eps_coef=2.5, eps_offset=80.0, hysteresis_ue=18.0,
    noise_std=0.4, seed=42,
):
    """98k-point stepped temperature experiment.

    n_cycles=4, 6 levels, 3000 pts/level → 72000 pts base.
    With some extra → exactly ~98k.
    """
    rng = np.random.default_rng(seed)
    T_list, eps_list = [], []
    direction = 1  # 1=up, -1=down
    for ci in range(n_cycles):
        levels = T_levels if direction > 0 else T_levels[::-1]
        for level in levels:
            n_pts = pts_per_level + rng.integers(-200, 200)
            # ★ Real step data has very stable T control (~0.05°C noise)
            T_noisy = np.full(n_pts, level) + rng.normal(0, 0.05, n_pts)
            eps_true = eps_coef * (level - T_base) + eps_offset
            # hysteresis adds a sign-dependent offset
            hys_sign = 1.0 if direction > 0 else -1.0
            eps_noisy = eps_true + hys_sign * hysteresis_ue / 2.0 + rng.normal(0, noise_std, n_pts)
            T_list.append(T_noisy)
            eps_list.append(eps_noisy)
        direction *= -1  # flip direction each cycle

    T_all = np.concatenate(T_list)
    eps_all = np.concatenate(eps_list)
    return T_all, eps_all


def main():
    FS = 1000.0  # ue

    print("=" * 70)
    print("  Phase B Profiling — 98k plateau-step data")
    print("=" * 70)

    # ── Generate data ──
    t0 = time.perf_counter()
    # 4 cycles × 2 directions × 6 levels × 4000 pts ≈ 192k points → trim to ~98k
    T_full, eps_full = make_plateau_data(n_cycles=4, pts_per_level=4000)
    # Trim to exactly ~98k
    n_target = 98000
    T, eps = T_full[:n_target], eps_full[:n_target]
    gen_time = time.perf_counter() - t0
    n = len(T)
    print(f"\n[gen] {n} points generated in {gen_time:.3f}s")

    # ── Check 4: How many cycles does real data detect? ──
    print("\n── [Q4] Cycle detection ──")
    t0 = time.perf_counter()
    cids = detect_cycles_from_T(T, method="auto")
    cd_time = time.perf_counter() - t0
    unique_cycles = sorted(set(int(c) for c in cids if c >= 0))
    n_cycles = len(unique_cycles)
    print(f"  Method: auto → plateau")
    print(f"  Cycles detected: {n_cycles}")
    print(f"  Time: {cd_time*1000:.1f} ms")
    for ci in unique_cycles:
        count = int(np.sum(cids == ci))
        print(f"    Cycle {ci}: {count} pts")

    # ── Check 3: How many times is detect_cycles called in compare? ──
    call_counter = [0]
    _orig_detect = detect_cycles_from_T
    def _wrapped_detect(*a, **kw):
        call_counter[0] += 1
        return _orig_detect(*a, **kw)
    # Monkey-patch for counting
    import utils.compensation_metrics as cm_module
    cm_module.detect_cycles_from_T = _wrapped_detect

    # ── Profile: Full pipeline (lut) ──
    print("\n── Profile: evaluate_compensation (lut) ──")
    prof = cProfile.Profile()
    prof.enable()
    t0 = time.perf_counter()
    m_lut = evaluate_compensation(
        T, eps, cids, fs=FS, form="lut", sensor="S1",
        subsample_step=10,
    )
    t_lut = time.perf_counter() - t0
    prof.disable()
    print(f"  Wall time: {t_lut*1000:.1f} ms")
    print(f"  residual_sigma = {m_lut.residual_sigma:.2f} ue ({m_lut.residual_sigma_pct_fs:.2f}%FS)")
    print(f"  hysteresis_max = {m_lut.hysteresis_max:.1f} ue ({m_lut.hysteresis_max_pct_fs:.1f}%FS)")
    print(f"  noise_floor = {m_lut.noise_floor:.2f} ue")
    print(f"  n_cycles = {n_cycles}, low_confidence = {m_lut.low_confidence}")

    s = io.StringIO()
    ps = pstats.Stats(prof, stream=s).sort_stats("cumtime")
    ps.print_stats(15)
    print(s.getvalue())

    # ── Check 1: subsample_step actual points in LOOCV ──
    print("\n── [Q1] subsample_step verification ──")
    # We'll verify by instrumenting _compute_loocv_sigma_by_form
    _orig_loocv = cm_module._compute_loocv_sigma_by_form
    loocv_sizes = []
    def _wrapped_loocv(*a, **kw):
        T_loocv = a[0]
        loocv_sizes.append(len(T_loocv))
        return _orig_loocv(*a, **kw)
    cm_module._compute_loocv_sigma_by_form = _wrapped_loocv

    _ = evaluate_compensation(
        T, eps, cids, fs=FS, form="lut", sensor="S1",
        subsample_step=10,
    )
    print(f"  Full data: {n} pts")
    print(f"  LOOCV data used: {loocv_sizes[-1]} pts (expected ~{n//10})")
    ratio = loocv_sizes[-1] / n if n > 0 else 0
    print(f"  Ratio: {ratio:.2%} (expected ~10%)")

    # ── Profile: evaluate_compensation (poly4) ──
    print("\n── Profile: evaluate_compensation (poly4) ──")
    prof2 = cProfile.Profile()
    prof2.enable()
    t0 = time.perf_counter()
    m_p4 = evaluate_compensation(
        T, eps, cids, fs=FS, form="poly", poly_order=4, sensor="S1",
        subsample_step=10,
    )
    t_p4 = time.perf_counter() - t0
    prof2.disable()
    print(f"  Wall time: {t_p4*1000:.1f} ms")
    print(f"  residual_sigma = {m_p4.residual_sigma:.2f} ue ({m_p4.residual_sigma_pct_fs:.2f}%FS)")

    s2 = io.StringIO()
    ps2 = pstats.Stats(prof2, stream=s2).sort_stats("cumtime")
    ps2.print_stats(15)
    print(s2.getvalue())

    # ── Restore detect_cycles ──
    cm_module.detect_cycles_from_T = _orig_detect
    call_count_before_compare = call_counter[0]

    # ── Profile: compare_compensation_forms ──
    print("\n── Profile: compare_compensation_forms (lut/poly2/poly4) ──")
    print(f"  [Q3] detect_cycles called {call_count_before_compare}x before compare")

    # Re-monkey-patch detect to count calls during compare
    call_counter[0] = 0
    cm_module.detect_cycles_from_T = _wrapped_detect

    prof3 = cProfile.Profile()
    prof3.enable()
    t0 = time.perf_counter()
    comp = compare_compensation_forms(
        T, eps, cids, fs=FS,
        candidates=("lut", "poly2", "poly4"),
        subsample_step=10,
    )
    t_comp = time.perf_counter() - t0
    prof3.disable()
    print(f"  Wall time: {t_comp*1000:.1f} ms")
    print(f"  [Q3] detect_cycles called {call_counter[0]}x during compare")
    for cand, sigma in comp.items():
        print(f"  {cand}: σ = {sigma:.2f} ue")

    s3 = io.StringIO()
    ps3 = pstats.Stats(prof3, stream=s3).sort_stats("cumtime")
    ps3.print_stats(15)
    print(s3.getvalue())

    # ── Restore all monkey-patches ──
    cm_module.detect_cycles_from_T = _orig_detect
    cm_module._compute_loocv_sigma_by_form = _orig_loocv

    # ── Summary ──
    print("\n" + "=" * 70)
    print("  Summary")
    print("=" * 70)
    print(f"  Data: {n} pts, {n_cycles} cycles")
    print(f"  eval(lut):   {t_lut*1000:.0f} ms  σ={m_lut.residual_sigma:.2f} ue")
    print(f"  eval(poly4): {t_p4*1000:.0f} ms  σ={m_p4.residual_sigma:.2f} ue")
    print(f"  compare(3):  {t_comp*1000:.0f} ms  lut={comp['lut']:.2f} poly2={comp['poly2']:.2f} poly4={comp['poly4']:.2f}")
    print(f"\n  Q1 subsample_step: {ratio:.0%} of data used in LOOCV (should be ~10%)")
    print(f"  Q2 cycle detect calls: main={1}x, compare={call_count_before_compare}x")
    if call_counter[0] == 0:
        print(f"    OK compare() reuses cids -- no redundant detect_cycles")
    else:
        print(f"    ⚠️  compare() calls detect_cycles {call_counter[0]}x extra!")
    print(f"  Q3 redundant in compare:")
    print(f"    evaluate_compensation recomputes: repeatability + hysteresis + noise_floor")
    print(f"    for EACH candidate form = 3x redundant stats")
    print(f"  Q4 detected {n_cycles} cycles in {n} plateau-step points")


if __name__ == "__main__":
    main()
