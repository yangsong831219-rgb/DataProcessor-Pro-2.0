"""标定结果导出 — Excel 多 sheet 输出

移植 FBG.py export_excel 结构，扩展支持应变标定结果。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def export_temperature_excel(
    df: pd.DataFrame,
    time_h: np.ndarray,
    sensor_results: dict,
    plateaus: pd.DataFrame,
    S_eff: dict,
    output_path: str,
    *,
    wavelength_cols: Optional[list[str]] = None,
    compensation: Optional[dict] = None,
) -> str:
    """导出温度标定结果到 Excel。

    Sheet 结构:
      1. 诊断摘要 — 各传感器标定 vs 实测对比
      2. 平台回归数据 — 平台 dL 均值 + T_set
      3. 出厂指标 — 补偿后指标 (residual_sigma, hysteresis, noise, grade…)
      4. {sensor}传感器数据 — 时间序列 (dL, 修正温度/应变, 原始温度/应变)

    Args:
        df: 含 _d 列的原始数据
        time_h: 时间 (小时)
        sensor_results: run_temperature_calibration 输出的 sensors dict
        plateaus: 平台表
        S_eff: 灵敏度回归结果
        output_path: 输出 .xlsx 路径
        wavelength_cols: 波长列名列表
        compensation: 补偿数据 dict {s_name: {"lut":..., "metrics":..., "grade":...}}

    Returns:
        output_path
    """
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:

        # ── Sheet 1: 诊断摘要 ──
        summary_rows = []
        for s_name, r in sensor_results.items():
            summary_rows.append({
                "传感器": s_name,
                "W1_S_eff(pm/°C)": round(r.get("S1", float("nan")), 2),
                "W2_S_eff(pm/°C)": round(r.get("S2", float("nan")), 2),
                "T_base(°C)": round(r.get("T_base", float("nan")), 1),
                "原始e_std(με)": round(_safe_std(r.get("eps_orig")), 1),
                "修正e_std(με)": round(_safe_std(r.get("eps_corr")), 1),
            })
        pd.DataFrame(summary_rows).to_excel(
            writer, sheet_name="诊断摘要", index=False
        )

        # ── Sheet 2: 平台回归数据 ──
        if not plateaus.empty:
            # 只输出关键列
            pcols = ["T_set"]
            for c in plateaus.columns:
                if c.endswith("_d") and not c.startswith("T_set"):
                    pcols.append(c)
            pcols = [c for c in pcols if c in plateaus.columns]
            plateaus[pcols].to_excel(
                writer, sheet_name="平台回归数据", index=False
            )

        # ── Sheet 3: 出厂指标 (Phase 3c: compensation model + metrics + grade) ──
        if compensation:
            metrics_rows = []
            for s_name, comp in sorted(compensation.items()):
                if not isinstance(comp, dict):
                    continue
                comp_metrics = comp.get("metrics")
                comp_grade = comp.get("grade")
                # compat: old key "lut" vs new "compensation_model"
                # compat: old key "lut" stores bare LUT dict, new "compensation_model" stores CM dict
                comp_model_d = comp.get("compensation_model") or comp.get("lut")
                if isinstance(comp_model_d, dict) and "form" not in comp_model_d:
                    comp_model_d = {"form": "lut", "model": comp_model_d}

                row: dict = {"传感器": s_name}

                if isinstance(comp_grade, dict):
                    row["评级"] = str(comp_grade.get("grade", "—"))
                    row["判定"] = "通过" if comp_grade.get("passed") else "拦截"
                else:
                    row["评级"] = "N/A"
                    row["判定"] = "—"

                if isinstance(comp_metrics, dict):
                    # ★ 补偿形式: 标明残差对应哪种形式
                    cm_form = comp_metrics.get("comp_form", "—")
                    cm_order = comp_metrics.get("poly_order", 0)
                    if cm_form == "poly" and cm_order:
                        row["补偿形式"] = f"poly (order {cm_order})"
                    else:
                        row["补偿形式"] = cm_form or "—"

                    row["量程(με)"] = comp_metrics.get("fs", "")
                    row["残余σ_RMS(με)"] = comp_metrics.get("residual_sigma", "")
                    row["残余σ(%FS)"] = comp_metrics.get("residual_sigma_pct_fs", "")
                    row["重复性(με)"] = comp_metrics.get("repeatability", "")
                    row["迟滞_max(με)"] = comp_metrics.get("hysteresis_max", "")
                    row["迟滞(%FS)"] = comp_metrics.get("hysteresis_max_pct_fs", "")
                    row["最坏单点(με)"] = comp_metrics.get("worst_case_single", "")
                    row["最坏单点(%FS)"] = comp_metrics.get("worst_case_single_pct_fs", "")
                    row["噪声底(με)"] = comp_metrics.get("noise_floor", "")
                    row["测温灵敏度(με/°C)"] = comp_metrics.get("temp_sensitivity_max", "")
                    row["低置信度"] = "是" if comp_metrics.get("low_confidence") else "否"

                # model metadata (works for both LUT and poly via CompensationModel dict)
                if isinstance(comp_model_d, dict):
                    row["T_base(°C)"] = comp_model_d.get("T_base", "")
                    row["有效温度范围(°C)"] = (
                        f"{comp_model_d.get('T_min','')}~{comp_model_d.get('T_max','')}")
                    row["建表循环数"] = comp_model_d.get("n_cycles", "")
                    row["建表方法"] = str(comp_model_d.get("source", ""))
                    if comp_model_d.get("form") == "poly":
                        row["多项式阶数"] = comp_model_d.get("order", "")
                    elif "T_grid" in comp_model_d:
                        row["LUT点数"] = len(comp_model_d.get("T_grid", []))

                metrics_rows.append(row)

            if metrics_rows:
                pd.DataFrame(metrics_rows).to_excel(
                    writer, sheet_name="出厂指标", index=False
                )

        # ── Sheet 4: 各传感器时间序列 ──
        for s_name, r in sensor_results.items():
            out_data = {
                "时间(h)": np.round(time_h, 4),
            }
            # dL 列
            dL_cols = [c for c in df.columns if c.endswith("_d")]
            for dc in dL_cols:
                out_data[f"{dc}(pm)"] = np.round(df[dc].values, 2)

            out_data["修正_温度(°C)"] = np.round(
                r.get("dT_corr", np.full(len(df), np.nan))
                + r.get("T_base", 0.0), 2
            )
            out_data["修正_应变(με)"] = np.round(r.get("eps_corr", np.full(len(df), np.nan)), 1)
            out_data["原始_dT(°C)"] = np.round(r.get("dT_orig", np.full(len(df), np.nan)), 2)
            out_data["原始_应变(με)"] = np.round(r.get("eps_orig", np.full(len(df), np.nan)), 1)

            pd.DataFrame(out_data).to_excel(
                writer, sheet_name=f"{s_name}传感器数据", index=False
            )

    print(f"  Excel 已保存: {output_path}")
    return output_path


def export_strain_excel(
    result,  # StrainCalibrationResult
    output_path: str,
) -> str:
    """导出应变标定结果到 Excel。

    Sheet 结构:
      1. 标定摘要 — 各光栅 k, R², 非线性, 重复性, 迟滞
      2. 原始数据 — 位移 / 理论应变 / 各光栅 Δλ
      3. 循环漂移 — 每循环斜率/截距

    Args:
        result: StrainCalibrationResult 实例
        output_path: 输出 .xlsx 路径

    Returns:
        output_path
    """
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:

        # ── Sheet 1: 标定摘要 ──
        summary_rows = []
        for g in result.gratings:
            summary_rows.append({
                "光栅": f"G{g.grating_index}",
                "灵敏度_k(pm/με)": round(g.k_pm_per_ue, 4),
                "R²": round(g.R2, 6) if not np.isnan(g.R2) else "",
                "非线性(%FS)": round(g.nonlinearity_pct_fs, 2) if not np.isnan(g.nonlinearity_pct_fs) else "",
                "重复性(%FS)": round(g.repeatability_pct_fs, 2) if not np.isnan(g.repeatability_pct_fs) else "",
                "迟滞(%FS)": round(g.hysteresis_pct_fs, 2) if not np.isnan(g.hysteresis_pct_fs) else "",
            })
        pd.DataFrame(summary_rows).to_excel(
            writer, sheet_name="标定摘要", index=False
        )

        # ── Sheet 2: 配置与理论应变 ──
        config_data = {
            "标距(mm)": [result.gauge_length_mm],
            "模式": [result.mode],
            "循环数": [result.n_cycles],
            "光栅类型": [result.grating_kind],
        }
        pd.DataFrame(config_data).to_excel(
            writer, sheet_name="配置", index=False
        )

        levels_df = pd.DataFrame({
            "位移(mm)": result.levels,
            "理论应变(με)": result.eps_theory,
        })
        levels_df.to_excel(writer, sheet_name="理论应变", index=False)

        # ── Sheet 3: 循环漂移 ──
        drift_rows = []
        for g in result.gratings:
            for ci, (s, i) in enumerate(zip(
                g.cycle_drift_slopes, g.cycle_drift_intercepts
            )):
                drift_rows.append({
                    "光栅": f"G{g.grating_index}",
                    "循环": ci + 1,
                    "斜率(pm/με)": round(s, 4),
                    "截距(pm)": round(i, 2),
                })
        if drift_rows:
            pd.DataFrame(drift_rows).to_excel(
                writer, sheet_name="循环漂移", index=False
            )

        # ── Sheet 4: 双栅指标 (如适用) ──
        if result.dual_avg_strain:
            dual_df = pd.DataFrame({
                "两栅平均应变(με)": np.round(result.dual_avg_strain, 2),
            })
            if result.dual_diff_strain:
                dual_df["两栅应变差(με)"] = np.round(result.dual_diff_strain, 2)
            dual_df.to_excel(writer, sheet_name="双栅诊断", index=False)

    print(f"  Excel 已保存: {output_path}")
    return output_path


def _safe_std(arr) -> float:
    """安全计算 std，处理 None/NaN"""
    if arr is None:
        return float("nan")
    try:
        return float(np.nanstd(arr))
    except (ValueError, TypeError):
        return float("nan")
