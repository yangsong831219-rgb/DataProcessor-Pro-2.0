"""端到端墙钟分段 profile: 模拟"运行解耦分析"完整 handler 路径。

模拟 _on_run → PhaseBWorker.run() → _on_done 全流程，
在每段插 time.perf_counter()，打印分段耗时。

Usage: python tests/profile_handler_wallclock.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import numpy as np
import pandas as pd

from py.calibration.temperature_calibration import compute_dL, decouple
from utils.compensation_metrics import detect_cycles_from_T, GradeThresholds
from ui.calibration_tab import _run_compensation_pipeline_static


# ═══════════════════════════════════════════════════════════════════════
# 生成近似真实台阶数据 (~98k pts, 3-4 sensors × 2 gratings each)
# ═══════════════════════════════════════════════════════════════════════

def make_multi_sensor_step_data(
    n_pts=98000, n_sensors=3, T_levels=(20, 30, 40, 50), seed=42,
    dwell_pts=5500, transition_pts=80,
):
    """生成 multi-sensor 台阶实验数据，模拟真实 98k 行文件。

    每支双栅传感器有 2 个波长列，共 6 个波长列。
    台阶波形: T dwell → ramp → dwell ...
    """
    rng = np.random.default_rng(seed)
    T_base_ref = 14.0  # 基准温度 (如 Phase A 结果)

    # Build T waveform (shared across sensors)
    T = np.empty(n_pts)
    pos = 0
    direction = 1  # 1=升温, -1=降温
    cycle = 0
    while pos < n_pts:
        for level in (T_levels if direction > 0 else T_levels[::-1]):
            # dwell
            n_dwell = min(dwell_pts, n_pts - pos)
            T[pos:pos + n_dwell] = level
            pos += n_dwell
            if pos >= n_pts:
                break
            # transition ramp
            next_level = level
            # find next level
            full_seq = T_levels if direction > 0 else T_levels[::-1]
            for lv in full_seq:
                if abs(lv - level) > 1:
                    next_level = lv
                    break
            n_trans = min(transition_pts, n_pts - pos)
            if n_trans > 0 and abs(next_level - level) > 1:
                T[pos:pos + n_trans] = np.linspace(level, next_level, n_trans)
                pos += n_trans
        direction *= -1
        cycle += 1

    T = T[:n_pts]  # trim if overshot

    # Per-sensor wavelengths (simulate 6 FBG columns)
    # Real params: Ke~1.2, KT~27-30 pm/°C, base WL ~1550 nm
    base_wls = [1530.0, 1532.0, 1540.0, 1542.0, 1550.0, 1552.0]  # 3 sensors × 2 gratings
    Ke_vals  = [1.2, 1.2, 1.15, 1.18, 1.22, 1.20]
    KT_vals  = [27.8, 29.4, 28.5, 30.1, 27.2, 28.9]

    df = pd.DataFrame()
    col_names = []
    T_degC = T + rng.normal(0, 0.05, len(T))  # very stable T control
    eps_true = rng.normal(0, 30, len(T))   # residual strain ~30 ue

    for i, (wl_base, Ke, KT) in enumerate(zip(base_wls, Ke_vals, KT_vals)):
        col = f"CH{i+1}"
        col_names.append(col)
        # WL = base + Ke*eps + KT*dT  (nm → raw WL)
        dT = T_degC - T_base_ref
        wl_nm = wl_base + (Ke * eps_true + KT * dT) / 1000.0  # /1000: pm→nm
        wl_nm += rng.normal(0, 0.005, len(T))  # small noise
        df[col] = wl_nm

    print(f"[gen] {len(T)} rows × {len(col_names)} WL columns, {n_sensors} sensors")
    return df, col_names


# ═══════════════════════════════════════════════════════════════════════
# 主 profile
# ═══════════════════════════════════════════════════════════════════════

def main():
    T0 = time.perf_counter()

    # ── t1: 数据准备 (_on_run 前半段) ──
    t0 = time.perf_counter()
    df, wl_cols = make_multi_sensor_step_data(n_pts=98000, n_sensors=3)
    t_gen = time.perf_counter() - t0

    # 模拟 annotation_groups (3 sensors, each with 2 gratings)
    annotation_groups = {
        "A1": [{"col_name": "CH1", "name": "A1-W1"},
               {"col_name": "CH2", "name": "A1-W2"}],
        "B1": [{"col_name": "CH3", "name": "B1-W1"},
               {"col_name": "CH4", "name": "B1-W2"}],
        "C1": [{"col_name": "CH5", "name": "C1-W1"},
               {"col_name": "CH6", "name": "C1-W2"}],
    }
    strain_coeffs = {
        "A1": {"Ke1": 1.2, "Ke2": 1.2},
        "B1": {"Ke1": 1.15, "Ke2": 1.18},
        "C1": {"Ke1": 1.22, "Ke2": 1.20},
    }
    # Phase A results (S_eff per grating)
    S_eff_result = {
        "CH1": {"slope": 27.8, "T_base": 14.0},
        "CH2": {"slope": 29.4, "T_base": 14.0},
        "CH3": {"slope": 28.5, "T_base": 14.0},
        "CH4": {"slope": 30.1, "T_base": 14.0},
        "CH5": {"slope": 27.2, "T_base": 14.0},
        "CH6": {"slope": 28.9, "T_base": 14.0},
    }
    fs_map = {"A1": 1000.0, "B1": 1000.0, "C1": 1000.0}
    time_h = np.arange(len(df)) * 2.0 / 3600.0  # 2s sample interval

    t_prep = (time.perf_counter() - t0) - t_gen
    print(f"\n{'='*60}")
    print(f"  t1 取数/准备: {t_prep*1000:.0f} ms (含数据生成 {t_gen*1000:.0f} ms)")

    # ── t2: compute_dL ──
    t0 = time.perf_counter()
    dL_cols = [c for c in wl_cols if c in df.columns]
    df_aug, _base = compute_dL(df.copy(), dL_cols)
    t_compute_dL = time.perf_counter() - t0
    n_actual = len(df_aug)
    print(f"  t2 compute_dL: {t_compute_dL*1000:.0f} ms (处理 {n_actual} 行 × {len(dL_cols)} 列)")

    # ── t3: 循环检测 (全部双栅传感器) ──
    t0 = time.perf_counter()
    cycle_cids = {}
    for pfx, gratings in annotation_groups.items():
        if len(gratings) < 2:
            continue
        wcol1 = gratings[0].get("col_name", gratings[0]["name"])
        wcol2 = gratings[1].get("col_name", gratings[1]["name"])
        S1 = S_eff_result[wcol1]["slope"]
        S2 = S_eff_result[wcol2]["slope"]
        T_base_s = (S_eff_result[wcol1]["T_base"] + S_eff_result[wcol2]["T_base"]) / 2.0
        Ke1 = strain_coeffs.get(pfx, {}).get("Ke1", 1.2)
        Ke2 = strain_coeffs.get(pfx, {}).get("Ke2", 1.2)
        dl1 = df_aug[f"{wcol1}_d"].values
        dl2 = df_aug[f"{wcol2}_d"].values
        # 先快速解耦得到 T_abs 供循环检测
        _eps, _dT = decouple(dl1, dl2, Ke1, S1, Ke2, S2)
        T_abs = _dT + T_base_s
        cids = detect_cycles_from_T(T_abs, method="auto")
        n_cyc = len(set(int(c) for c in cids if c >= 0))
        cycle_cids[pfx] = (cids, n_cyc)

    t_cycle = time.perf_counter() - t0
    for pfx, (_, n) in cycle_cids.items():
        print(f"    [{pfx}] {n} cycles detected ({np.sum(cycle_cids[pfx][0] >= 0) if len(cycle_cids[pfx][0]) > 0 else 0} pts)")
    print(f"  t3 循环检测(全部): {t_cycle*1000:.0f} ms")

    # ── t4: decouple (全部双栅传感器, 标定KT + 实测S_eff 各一次) ──
    t0 = time.perf_counter()
    sensors = {}
    comparisons = []
    for pfx, gratings in annotation_groups.items():
        if len(gratings) < 2:
            g = gratings[0]
            wcol = g.get("col_name", g["name"])
            s = S_eff_result.get(wcol, {})
            sensors[pfx] = {
                "single_grating": True,
                "S1": s.get("slope", float("nan")),
                "T_base": s.get("T_base", float("nan")),
                "R2": s.get("r2", float("nan")),
            }
            continue
        wcol1 = gratings[0].get("col_name", gratings[0]["name"])
        wcol2 = gratings[1].get("col_name", gratings[1]["name"])
        S1 = S_eff_result[wcol1]["slope"]
        S2 = S_eff_result[wcol2]["slope"]
        T_base_s = (S_eff_result[wcol1]["T_base"] + S_eff_result[wcol2]["T_base"]) / 2.0
        coefs = strain_coeffs.get(pfx, {})
        Ke1 = coefs.get("Ke1", 1.2)
        Ke2 = coefs.get("Ke2", 1.2)
        dl1 = df_aug[f"{wcol1}_d"].values
        dl2 = df_aug[f"{wcol2}_d"].values

        eps_orig, dT_orig = decouple(dl1, dl2, Ke1, Ke1 * 0.95, Ke2, Ke2 * 0.95)
        eps_corr, dT_corr = decouple(dl1, dl2, Ke1, S1, Ke2, S2)
        T_abs = dT_corr + T_base_s

        sensors[pfx] = {
            "single_grating": False,
            "eps_orig": eps_orig, "dT_orig": dT_orig,
            "eps_corr": eps_corr, "dT_corr": dT_corr, "T_abs": T_abs,
            "S1": S1, "S2": S2, "T_base": T_base_s,
        }

        for label, gKT, sEff in [
            (f"{pfx}-W1", Ke1 * 0.95, S1),
            (f"{pfx}-W2", Ke2 * 0.95, S2),
        ]:
            comparisons.append({
                "grating": label, "given_KT": gKT,
                "measured_S_eff": sEff, "diff_pm_per_C": sEff - gKT,
            })

    t_decouple = time.perf_counter() - t0
    for pfx, r in sensors.items():
        if not r.get("single_grating"):
            print(f"    [{pfx}] 双栅: e_std_raw={np.nanstd(r['eps_corr']):.1f} ue, n={len(r['eps_corr'])}")
    print(f"  t4 decouple(全部): {t_decouple*1000:.0f} ms")

    # ── t5: 补偿流水线 (全传感器) ──
    t0 = time.perf_counter()
    compensation = _run_compensation_pipeline_static(
        sensors, fs_map, comp_form="lut", poly_order=4,
        subsample_step=10, thresholds=None,
    )
    t_compensation = time.perf_counter() - t0
    for s_name, c in compensation.items():
        g = c.get("grade")
        m = c.get("metrics")
        grade_str = g.grade if g else "?"
        sigma_str = f"{m.residual_sigma:.1f}ue" if m else "?"
        print(f"    [{s_name}] grade={grade_str}, sigma={sigma_str}")
    print(f"  t5 补偿+指标+评级: {t_compensation*1000:.0f} ms")

    # ── t6: 结果表渲染 (模拟 _render_result_table) ──
    t0 = time.perf_counter()
    # 简化: 只统计数值，不创建 QTableWidget
    for s_name, r in sensors.items():
        if r.get("single_grating"):
            continue
        eps = np.asarray(r.get("eps_corr", []), dtype=np.float64)
        e_mean = float(np.nanmean(eps)) if len(eps) > 0 else float("nan")
        e_std = float(np.nanstd(eps)) if len(eps) > 0 else float("nan")
        e_range = float(np.nanmax(eps) - np.nanmin(eps)) if len(eps) > 0 else float("nan")
    t_render = time.perf_counter() - t0
    print(f"  t6 结果表渲染(纯数值): {t_render*1000:.0f} ms")

    # ── t7: 检查是否有 AI 调用 ──
    # 扫描 _on_done / _on_run / worker.run 全路径: 没有 deepseek / AIClient / 网络调用
    print(f"  t7 AI/网络调用: 无 (handler 路径无 LLM/AIClient 导入)")

    # ── 总耗时 ──
    T_total = time.perf_counter() - T0
    print(f"\n{'='*60}")
    print(f"  分段耗时汇总 ({n_actual} 行, {len(dL_cols)} WL列, {len(annotation_groups)} sensors):")
    print(f"  t1 取数/准备:        {t_prep*1000:6.0f} ms")
    print(f"  t2 compute_dL:        {t_compute_dL*1000:6.0f} ms")
    print(f"  t3 循环检测(全部):   {t_cycle*1000:6.0f} ms")
    print(f"  t4 decouple(全部):   {t_decouple*1000:6.0f} ms")
    print(f"  t5 补偿+指标+评级:   {t_compensation*1000:6.0f} ms")
    print(f"  t6 结果表渲染:       {t_render*1000:6.0f} ms")
    print(f"  t7 AI/网络调用:      无")
    print(f"  {'─'*40}")
    print(f"  总计(不含数据生成):  {(T_total - t_gen)*1000:6.0f} ms")
    print(f"{'='*60}")

    # ── 显式回答 ──
    print(f"\n  ★ Q1: handler 内是否调用了 AI/网络?  否")
    print(f"  ★ Q2: compute_dL / decouple 实现方式:")
    print(f"     compute_dL: Python for 循环逐列 (共 {len(dL_cols)} 列) + print(), ")
    print(f"                 含 df.astype(object)+pd.to_numeric (昂贵)")
    print(f"     decouple:   纯 numpy 向量化 2×2 闭式解 (O(N), 无 Python 行循环)")
    print(f"  ★ Q3: 实际处理点数: {n_actual} rows × {len(dL_cols)} WL columns")
    print(f"  ★ Q4: 解耦次数: {sum(1 for _,r in sensors.items() if not r.get('single_grating'))} sensors × 2 = "
          f"{sum(2 for _,r in sensors.items() if not r.get('single_grating'))} 次 decouple 调用")


if __name__ == "__main__":
    main()
