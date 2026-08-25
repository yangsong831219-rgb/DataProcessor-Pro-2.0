"""ChartBundle — 统一画图数据结构。

诊断保存 与 实时 provider 都从此结构出图，保证诊断/实时两路图集同源一致。
所有字段均可 JSON 序列化（数组抽稀为 list、skip=1 表示全存）。
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import Any, Optional


_CALIB_READING_RE = re.compile(r"^G(?P<grating>\d+)_C(?P<cycle>\d+)_load$")
_MIN_ACTIVE_STRAIN_SENSITIVITY_PM_PER_UE = 0.01


def _build_time_hours(df: Any) -> list[float]:
    """优先从真实时间戳构建小时轴；无可用时间戳时保持旧的 1 秒/行降级。"""
    import numpy as np
    import pandas as pd

    n_rows = len(df)
    candidates = [
        c for c in df.columns
        if str(c).strip().lower() in {"timestamp", "time", "datetime"}
        or "时间" in str(c)
    ]
    for column in candidates:
        series = df[column]
        if pd.api.types.is_numeric_dtype(series.dtype):
            continue
        parsed = pd.to_datetime(series, errors="coerce")
        valid = parsed.notna()
        if int(valid.sum()) < 2:
            continue
        elapsed = (parsed - parsed[valid].min()).dt.total_seconds().to_numpy(dtype=float)
        valid_elapsed = np.isfinite(elapsed)
        if int(valid_elapsed.sum()) < 2:
            continue
        span = float(np.nanmax(elapsed) - np.nanmin(elapsed))
        if span <= 0.0:
            continue
        if not bool(np.all(valid_elapsed)):
            row_index = np.arange(n_rows, dtype=float)
            elapsed[~valid_elapsed] = np.interp(
                row_index[~valid_elapsed], row_index[valid_elapsed], elapsed[valid_elapsed]
            )
        return (elapsed / 3600.0).tolist()
    return (np.arange(n_rows, dtype=float) / 3600.0).tolist()


def _extract_calibration_series(cfg: Any) -> dict[str, Any] | None:
    """构建单传感器标定曲线。

    新配置优先使用真实加载波长读数；旧项目若只保存了 eps_theory + Ke，
    则退化为明确标记的拟合响应曲线，避免把推算值冒充实测散点。
    """
    import numpy as np

    readings = getattr(cfg, "readings", []) or []
    if len(readings) < 3 or not all(isinstance(row, dict) for row in readings):
        return None

    gauge = float(getattr(cfg, "gauge_length_mm", 80.0) or 80.0)
    if gauge <= 0.0:
        return None

    ref_values: list[float] = []
    for row in readings:
        raw_eps = row.get("eps_theory")
        try:
            eps = float(raw_eps) if raw_eps is not None else float(row.get("disp_mm", 0.0)) / gauge * 1e6
        except (TypeError, ValueError):
            eps = float("nan")
        ref_values.append(eps)

    measurement_keys = sorted({
        str(key)
        for row in readings
        for key in row
        if _CALIB_READING_RE.fullmatch(str(key))
    })
    ke = getattr(cfg, "ke_results", {}) or {}
    measured_by_grating: dict[int, list[np.ndarray]] = {}
    for key in measurement_keys:
        match = _CALIB_READING_RE.fullmatch(key)
        if match is None:
            continue
        grating_index = int(match.group("grating"))
        ke_value = ke.get(f"Ke{grating_index}", 0.0) if isinstance(ke, dict) else 0.0
        try:
            if abs(float(ke_value)) < _MIN_ACTIVE_STRAIN_SENSITIVITY_PM_PER_UE:
                continue
        except (TypeError, ValueError):
            continue

        wavelength = np.full(len(readings), np.nan, dtype=float)
        for index, row in enumerate(readings):
            try:
                value = float(row.get(key))
            except (TypeError, ValueError):
                continue
            if 1400.0 <= value <= 1700.0:
                wavelength[index] = value
        finite = np.isfinite(wavelength)
        if int(finite.sum()) < 3:
            continue
        baseline = float(wavelength[np.flatnonzero(finite)[0]])
        measured_by_grating.setdefault(grating_index, []).append(
            (wavelength - baseline) * 1000.0
        )

    ref_all = np.asarray(ref_values, dtype=float)
    ref_valid = np.isfinite(ref_all)
    if int(ref_valid.sum()) < 3 or float(np.ptp(ref_all[ref_valid])) <= 0.0:
        return None

    source = "measured"
    curves: dict[str, list[float]] = {}
    for grating_index, columns in sorted(measured_by_grating.items()):
        matrix = np.vstack(columns)
        counts = np.sum(np.isfinite(matrix), axis=0)
        averaged = np.divide(
            np.nansum(matrix, axis=0),
            counts,
            out=np.full(len(readings), np.nan, dtype=float),
            where=counts > 0,
        )
        curves[f"G{grating_index}"] = averaged[ref_valid].tolist()

    if not curves and isinstance(ke, dict):
        source = "fitted_from_ke"
        for key, raw_ke in sorted(ke.items()):
            match = re.fullmatch(r"Ke(?P<grating>\d+)", str(key))
            if match is None:
                continue
            try:
                ke_value = float(raw_ke)
            except (TypeError, ValueError):
                continue
            if abs(ke_value) < _MIN_ACTIVE_STRAIN_SENSITIVITY_PM_PER_UE:
                continue
            curves[f"G{int(match.group('grating'))}"] = (
                ref_all[ref_valid] * ke_value
            ).tolist()

    if not curves:
        return None

    curve_matrix = np.vstack([
        np.asarray(values, dtype=float) for values in curves.values()
    ])
    finite_counts = np.sum(np.isfinite(curve_matrix), axis=0)
    measured = np.divide(
        np.nansum(curve_matrix, axis=0),
        finite_counts,
        out=np.full(int(ref_valid.sum()), np.nan, dtype=float),
        where=finite_counts > 0,
    )
    ref = ref_all[ref_valid]
    valid_pairs = np.isfinite(ref) & np.isfinite(measured)
    ref = ref[valid_pairs]
    measured = measured[valid_pairs]
    if len(ref) < 3 or float(np.ptp(ref)) <= 0.0:
        return None

    slope, intercept = np.polyfit(ref, measured, 1)
    predicted = slope * ref + intercept
    ss_res = float(np.sum((measured - predicted) ** 2))
    ss_tot = float(np.sum((measured - measured.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return {
        "ref": ref.tolist(),
        "measured": measured.tolist(),
        "slope": float(slope),
        "r2": float(r2),
        "curves": curves,
        "contributing_gratings": sorted(curves),
        "source": source,
    }


def _extract_saved_fit_series(cfg: Any) -> dict[str, Any] | None:
    """Rebuild a sensor-specific curve from its saved regression metadata.

    This is intentionally a recovery path for legacy projects whose saved raw
    readings were duplicated across sensors.  It never presents the result as
    measured wavelengths: the caller must label it as a saved fit.
    """
    import numpy as np

    charts_meta = getattr(cfg, "charts_meta", {}) or {}
    if not isinstance(charts_meta, dict):
        return None
    result = charts_meta.get("strain_result", {}) or {}
    if not isinstance(result, dict):
        return None

    raw_eps = result.get("eps_theory", []) or []
    try:
        eps_all = np.asarray(raw_eps, dtype=float)
    except (TypeError, ValueError):
        return None
    eps_valid = np.isfinite(eps_all)
    if int(eps_valid.sum()) < 3 or float(np.ptp(eps_all[eps_valid])) <= 0.0:
        return None

    ke = getattr(cfg, "ke_results", {}) or {}
    if not isinstance(ke, dict):
        ke = {}
    curves: dict[str, list[float]] = {}
    for item in result.get("gratings", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            grating_index = int(item.get("grating_index") or 0)
            sensitivity = float(item.get("k_pm_per_ue") or float("nan"))
        except (TypeError, ValueError):
            continue
        if (
            grating_index <= 0
            or not np.isfinite(sensitivity)
            or abs(sensitivity) < _MIN_ACTIVE_STRAIN_SENSITIVITY_PM_PER_UE
        ):
            continue

        raw_ke = ke.get(f"Ke{grating_index}")
        if raw_ke is not None:
            try:
                if abs(float(raw_ke)) < _MIN_ACTIVE_STRAIN_SENSITIVITY_PM_PER_UE:
                    continue
            except (TypeError, ValueError):
                continue

        cycle_fits: list[np.ndarray] = []
        raw_slopes = item.get("cycle_drift_slopes", []) or []
        raw_intercepts = item.get("cycle_drift_intercepts", []) or []
        if isinstance(raw_slopes, list) and isinstance(raw_intercepts, list):
            for raw_slope, raw_intercept in zip(raw_slopes, raw_intercepts):
                try:
                    slope = float(raw_slope)
                    intercept = float(raw_intercept)
                except (TypeError, ValueError):
                    continue
                if np.isfinite(slope) and np.isfinite(intercept):
                    cycle_fits.append(slope * eps_all + intercept)

        if cycle_fits:
            curve = np.mean(np.vstack(cycle_fits), axis=0)
        else:
            curve = sensitivity * eps_all
        curves[f"G{grating_index}"] = curve[eps_valid].tolist()

    if not curves:
        return None

    ref = eps_all[eps_valid]
    curve_matrix = np.vstack([
        np.asarray(values, dtype=float) for values in curves.values()
    ])
    measured = np.mean(curve_matrix, axis=0)
    valid_pairs = np.isfinite(ref) & np.isfinite(measured)
    ref = ref[valid_pairs]
    measured = measured[valid_pairs]
    if len(ref) < 3 or float(np.ptp(ref)) <= 0.0:
        return None

    slope, intercept = np.polyfit(ref, measured, 1)
    predicted = slope * ref + intercept
    ss_res = float(np.sum((measured - predicted) ** 2))
    ss_tot = float(np.sum((measured - measured.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return {
        "ref": ref.tolist(),
        "measured": measured.tolist(),
        "slope": float(slope),
        "r2": float(r2),
        "curves": curves,
        "contributing_gratings": sorted(curves),
        "source": "saved_regression",
    }


def _sequence_to_list(values: Any) -> list[Any]:
    """把 ndarray/Series/tuple 安全转为 list，避免多元素布尔判断。"""
    if values is None:
        return []
    if hasattr(values, "tolist"):
        converted = values.tolist()
        return converted if isinstance(converted, list) else [converted]
    if isinstance(values, list):
        return values
    try:
        return list(values)
    except TypeError:
        return []


def _finite_pair_count(x_values: list[Any], y_values: list[Any]) -> int:
    """返回两条序列按最短长度对齐后的有限数值对数量。"""
    import numpy as np

    pair_count = min(len(x_values), len(y_values))
    if pair_count == 0:
        return 0
    try:
        x = np.asarray(x_values[:pair_count], dtype=float)
        y = np.asarray(y_values[:pair_count], dtype=float)
    except (TypeError, ValueError):
        return 0
    return int(np.sum(np.isfinite(x) & np.isfinite(y)))


def _select_hysteresis_eps(sensor_data: dict[str, Any], t_values: list[Any]) -> list[Any]:
    """选择与温度配对后有效点最多的应变序列，平分时优先修正值 eps_corr。"""
    candidates = (
        _sequence_to_list(sensor_data.get("eps_corr")),
        _sequence_to_list(sensor_data.get("eps_orig")),
    )
    selected: list[Any] = []
    selected_score = -1
    for values in candidates:
        score = _finite_pair_count(t_values, values)
        if score > selected_score:
            selected = values
            selected_score = score
    pair_count = min(len(t_values), len(selected))
    return selected[:pair_count]


def _downsample_series(
    series: dict[str, Any],
    *,
    max_points: int = 2000,
) -> dict[str, list[Any]]:
    """按同一步长抽稀多条对齐序列，保证图表各轴不发生错位。"""
    converted = {
        key: _sequence_to_list(values)
        for key, values in series.items()
    }
    lengths = [len(values) for values in converted.values() if values]
    if not lengths:
        return {key: [] for key in converted}
    count = min(lengths)
    limit = max(1, max_points)
    step = max(1, (count + limit - 1) // limit)
    return {
        key: values[:count:step]
        for key, values in converted.items()
    }


def _extract_phase_a_regressions(temp_page: Any) -> list[dict[str, Any]]:
    """从阶段 A 活状态提取可 JSON 序列化的回归图数据。"""
    import numpy as np
    import pandas as pd

    phase_a_state = getattr(temp_page, "_phase_a_state", {}) or {}
    result = (
        phase_a_state.get("seff_result")
        if isinstance(phase_a_state, dict)
        else None
    )
    if not isinstance(result, dict):
        candidate = getattr(temp_page, "_last_result", None)
        result = candidate if isinstance(candidate, dict) else None
    if not isinstance(result, dict):
        return []

    plateaus = result.get("plateaus")
    if not isinstance(plateaus, pd.DataFrame) or plateaus.empty:
        return []
    if "T_set" not in plateaus.columns:
        return []

    s_eff = result.get("S_eff")
    if not isinstance(s_eff, dict):
        return []
    raw_wcols = result.get("wavelength_cols")
    wcols = _sequence_to_list(raw_wcols) if raw_wcols is not None else list(s_eff)
    annotations = getattr(temp_page, "_annotation_dict", {}) or {}

    regressions: list[dict[str, Any]] = []
    for raw_wcol in wcols:
        wcol = str(raw_wcol)
        drift_col = f"{wcol}_d"
        fit = s_eff.get(wcol)
        if drift_col not in plateaus.columns or not isinstance(fit, dict):
            continue
        temperature = np.asarray(
            pd.to_numeric(plateaus.loc[:, "T_set"], errors="coerce"),
            dtype=np.float64,
        ).reshape(-1)
        drift = np.asarray(
            pd.to_numeric(plateaus.loc[:, drift_col], errors="coerce"),
            dtype=np.float64,
        ).reshape(-1)
        valid = np.isfinite(temperature) & np.isfinite(drift)
        if int(valid.sum()) < 2:
            continue
        slope = _safe_float(fit.get("slope"))
        r2 = _safe_float(fit.get("r2"))
        if slope is None:
            continue
        intercept = _safe_float(fit.get("intercept"))
        if intercept is None:
            intercept = float(
                np.mean(drift[valid]) - slope * np.mean(temperature[valid])
            )
        regressions.append({
            "name": wcol,
            "display": str(annotations.get(wcol, fit.get("display", wcol))),
            "temperature": temperature[valid].tolist(),
            "drift_pm": drift[valid].tolist(),
            "slope": slope,
            "intercept": intercept,
            "r2": r2,
        })
    return regressions


def _extract_phase_b_diagnostics(temp_page: Any) -> list[dict[str, Any]]:
    """从阶段 B 状态提取每个双栅传感器的诊断四联图数据。"""
    import numpy as np

    phase_b_state = getattr(temp_page, "_phase_b_state", {}) or {}
    if not isinstance(phase_b_state, dict):
        return []
    phase_b_result = getattr(temp_page, "_phase_b_result", None)
    result_sensors = (
        phase_b_result.get("sensors")
        if isinstance(phase_b_result, dict)
        else None
    )
    sensors = result_sensors or phase_b_state.get("sensors") or {}
    if not isinstance(sensors, dict):
        return []

    result_time = (
        phase_b_result.get("time_h")
        if isinstance(phase_b_result, dict)
        else None
    )
    diagnostic_series = (
        phase_b_result.get("diagnostic_series") or {}
        if isinstance(phase_b_result, dict)
        else {}
    )
    ke_table = phase_b_state.get("ke_table") or {}
    diagnostics: list[dict[str, Any]] = []

    for sensor_name, sensor_data in sorted(sensors.items()):
        if not isinstance(sensor_data, dict) or sensor_data.get("single_grating", False):
            continue
        saved_series = (
            diagnostic_series.get(sensor_name, {})
            if isinstance(diagnostic_series, dict)
            else {}
        )
        if not isinstance(saved_series, dict):
            saved_series = {}
        eps_raw = _sequence_to_list(
            saved_series.get("eps_raw", sensor_data.get("eps_corr"))
        )
        eps_compensated = _sequence_to_list(
            saved_series.get("eps_compensated")
        )
        d_temperature = _sequence_to_list(sensor_data.get("dT_corr"))
        absolute_temperature = _sequence_to_list(sensor_data.get("T_abs"))
        count = min(
            len(eps_raw),
            len(d_temperature),
            len(absolute_temperature),
        )
        if count < 3:
            continue

        raw_time = _sequence_to_list(result_time)
        if len(raw_time) < count:
            raw_time = (np.arange(count, dtype=float) * 2.0 / 3600.0).tolist()

        sensor_ke = ke_table.get(sensor_name, {}) if isinstance(ke_table, dict) else {}
        ke1 = _safe_float(sensor_ke.get("Ke1")) if isinstance(sensor_ke, dict) else None
        ke2 = _safe_float(sensor_ke.get("Ke2")) if isinstance(sensor_ke, dict) else None
        s1 = _safe_float(sensor_data.get("S1"))
        s2 = _safe_float(sensor_data.get("S2"))
        if None in (ke1, ke2, s1, s2):
            continue

        eps_arr = np.asarray(eps_raw[:count], dtype=float)
        d_temperature_arr = np.asarray(d_temperature[:count], dtype=float)
        dl1 = ke1 * eps_arr + s1 * d_temperature_arr
        dl2 = ke2 * eps_arr + s2 * d_temperature_arr
        aligned = _downsample_series({
            "time_h": raw_time[:count],
            "dl1_pm": dl1,
            "dl2_pm": dl2,
            "d_temperature": d_temperature_arr,
            "absolute_temperature": absolute_temperature[:count],
            "eps_raw": eps_arr,
            "eps_compensated": eps_compensated[:count],
        })
        diagnostics.append({
            "name": str(sensor_name),
            **aligned,
            # 兼容旧消费者；新报告渲染使用明确的 raw/compensated 字段。
            "eps_corr": aligned["eps_raw"],
        })
    return diagnostics


@dataclass
class ChartBundle:
    """统一画图数据束。字段一一对应 report_charts 各图函数所需输入。

    每个字段存着可直接喂给对应 chart 函数的(已抽稀)数组。
    空数组/None → 该图不生成。
    """

    # ── 时序 / 增强时序 ──
    time_h: list[float] = field(default_factory=list)  # 时间轴 (h), 已抽稀
    series: dict[str, list[float]] = field(default_factory=dict)  # {通道名: 值数组} — 原始波长 (前5列)
    cleaned: dict[str, list[float]] = field(default_factory=dict)  # {通道名: 清洗后数组}
    time_downsample: int = 1  # 抽稀步长 (1=全存)

    # ── 物理量时程序列 (Phase 1-a: T2 物理量数据源接入) ──
    # {sensor_physical_key: 降采样后值列表}
    # 例: {"A1_应变": [201pts], "A1_温度": [201pts], "B1": [201pts], ...}
    phys_series: dict[str, list[float]] = field(default_factory=dict)
    # {W列名: 降采样后 Δλ}  例: {"W1": [201pts], "W2": [201pts], ...}
    series_delta: dict[str, list[float]] = field(default_factory=dict)

    # ── 多源对比 ──
    compare_time_h: list[float] = field(default_factory=list)
    compare_sources: dict[str, list[float]] = field(default_factory=dict)
    compare_pairs: list[dict] = field(default_factory=list)  # [{device_a, device_b, corr, rmse, mae}]

    # ── 迟滞回线 ──
    hyst_sensors: list[dict] = field(default_factory=list)
    # [{name, T_abs: list, eps: list}]

    # ── 温度标定标准图 ──
    # 阶段 A: 每光栅回归数据；由一个 producer 合成为回归网格图
    temp_regressions: list[dict] = field(default_factory=list)
    # 阶段 B: 每双栅传感器一份四联图数据
    phaseb_diagnostics: list[dict] = field(default_factory=list)

    # ── 标定线性度 (Phase 1a: 多传感器支持) ──
    # ★ 旧单字段 (DEPRECATED — 仅保留用于向后兼容旧诊断记录加载。
    #    产图不再读这些字段；新数据全走 calib_sensors。
    #    第二段产图层改完后可移除)
    calib_ref: list[float] = field(default_factory=list)
    calib_measured: list[float] = field(default_factory=list)
    calib_sensor: str = ""
    calib_unit: str = "με"
    calib_r2: float = 0.0
    calib_slope: float = 0.0

    # ★ 新多传感器字段 (Phase 1a)
    # 每项: {sensor:str, ref:list[float], measured:list[float], slope:float, r2:float, unit:str}
    calib_sensors: list[dict] = field(default_factory=list)

    # ── 表格数据 (Batch A: 替换空白/崩坏柱状图) ──
    # 每项为 markdown 表格字符串 (pipe 格式)，由 word_builder 自动渲染为 docx 表格
    cleaning_table_md: str = ""  # 数据清洗统计表（清洗执行后始终产）
    grade_table_md: str = ""  # 传感器分级表
    anomaly_table_md: str = ""  # 异常统计表
    ke_table_md: str = ""  # 应变 Ke 汇总表
    decoupling_table_md: str = ""  # 温度 B 解耦标量表

    @classmethod
    def from_providers(cls, main_win) -> ChartBundle:
        """从主窗口各模块（实时数据）填充 ChartBundle。"""
        import numpy as np
        import pandas as pd
        bundle = cls()

        # ── AnalysisProvider ──
        atw = getattr(main_win, 'analysis_tab_widget', None)
        print(f"[DIAG] from_providers AnalysisProvider: atw_exists={atw is not None}")
        _n = 0
        _step = 1
        _raw_df = None
        if atw:
            _raw_df = getattr(atw, '_current_data', None)
            _raw_shape = _raw_df.shape if _raw_df is not None else 'None'
            _main_shape = main_win.current_data.shape if getattr(main_win, 'current_data', None) is not None else 'None'
            print(f"[DIAG] from_providers AnalysisProvider: "
                  f"_current_data(private)={_raw_shape}, "
                  f"main_win.current_data={_main_shape}")
            if _raw_df is not None and len(_raw_df) > 0:
                _n = len(_raw_df)
                _step = max(1, _n // 200)  # ★ 同一个 step — 所有降采样共用
                bundle.time_h = _build_time_hours(_raw_df)[::_step]
                num_cols = _raw_df.select_dtypes(include=[np.number]).columns.tolist()[:5]
                for c in num_cols:
                    bundle.series[c] = _raw_df[c].values.tolist()[::_step]
                bundle.time_downsample = _step

        # ── Phase 1-a: 物理量序列 (T2 sensor_results) ──
        sensor_results = getattr(main_win, 'sensor_results', {}) or {}
        if sensor_results and _n > 0 and _step >= 1:
            for key, values in sensor_results.items():
                try:
                    arr = np.array(values, dtype=float)[::_step]
                    bundle.phys_series[str(key)] = arr.tolist()
                except (ValueError, TypeError) as e:
                    print(f"[DIAG] phys_series skip key={key!r}: {e}")
        print(f"[DIAG] from_providers phys_series: "
              f"phys_series_n={len(bundle.phys_series)}, "
              f"sensor_results_n={len(sensor_results)}, step={_step}")

        # ── Phase 1-a: 波长差 Δλ (W-column λ − λ0) ──
        ss = getattr(main_win, 'sensor_system', None)
        ref_row = getattr(ss, 'reference_row', 0) if ss is not None else None
        _delta_n = 0
        if _raw_df is not None and _n > 0 and ref_row is not None and _step >= 1:
            import re as _re2
            _W_PAT = _re2.compile(r'^[wW]\d+$')
            for col in _raw_df.columns:
                if not _W_PAT.fullmatch(str(col)):
                    continue
                try:
                    w_vals = _raw_df[col].values
                    initial = float(w_vals[ref_row]) if ref_row < len(w_vals) else float(w_vals[0])
                    delta = [float(v) - initial for v in w_vals]
                    bundle.series_delta[str(col)] = delta[::_step]
                    _delta_n += 1
                except (ValueError, TypeError, IndexError) as e:
                    print(f"[DIAG] series_delta skip col={col!r}: {e}")
        print(f"[DIAG] from_providers series_delta: "
              f"series_delta_n={_delta_n}, ref_row={ref_row}, step={_step}")

        # ── 异常统计表 (A1): 从 cleaning_tab_widget._anomaly_info ──
        clw = getattr(main_win, 'cleaning_tab_widget', None)
        anomaly_info = getattr(clw, '_anomaly_info', {}) or {}
        cleaning_has_run = bool(getattr(clw, '_cleaning_has_run', False))
        if cleaning_has_run:
            cleaning_df = getattr(main_win, "current_data", None)
            if cleaning_df is None:
                cleaning_df = _raw_df
            if cleaning_df is not None and len(cleaning_df) > 0:
                numeric_series: dict[str, Any] = {}
                for col in cleaning_df.columns:
                    if str(col).endswith("_anomaly"):
                        continue
                    converted = pd.to_numeric(
                        cleaning_df.loc[:, col],
                        errors="coerce",
                    )
                    # 暗号行会把整列提升为 object；只要多数数据行仍可转成
                    # 数值，就应把它保留为统计通道。
                    if int(converted.notna().sum()) >= max(
                        2,
                        int(len(converted) * 0.5),
                    ):
                        numeric_series[str(col)] = converted
                config = {}
                try:
                    config_getter = getattr(clw, "get_config", None)
                    config_candidate = config_getter() if callable(config_getter) else {}
                    config = config_candidate if isinstance(config_candidate, dict) else {}
                except Exception as exc:
                    print(f"[DIAG] cleaning config unavailable: {exc}")
                fill_method = str(config.get("fill_method", "—"))
                rows = [[
                    "通道", "有效数", "缺失数", "异常点数",
                    "均值", "标准差", "最小值", "最大值", "填充方式",
                ]]
                for col, values in numeric_series.items():
                    valid_values = values.dropna()
                    anomaly = anomaly_info.get(col, {})
                    anomaly_count = (
                        int(anomaly.get("count", 0))
                        if isinstance(anomaly, dict)
                        else 0
                    )

                    def _stat_text(value: Any) -> str:
                        parsed = _safe_float(value)
                        return f"{parsed:.6g}" if parsed is not None and np.isfinite(parsed) else "—"

                    rows.append([
                        str(col),
                        str(int(valid_values.count())),
                        str(int(values.isna().sum())),
                        str(anomaly_count),
                        _stat_text(valid_values.mean() if not valid_values.empty else None),
                        _stat_text(valid_values.std() if len(valid_values) > 1 else None),
                        _stat_text(valid_values.min() if not valid_values.empty else None),
                        _stat_text(valid_values.max() if not valid_values.empty else None),
                        fill_method,
                    ])
                if len(rows) > 1:
                    bundle.cleaning_table_md = _rows_to_md_table(rows)
        if anomaly_info:
            rows = [["通道", "异常点数", "总索引数", "占比(%)"]]
            for col, d in sorted(anomaly_info.items(), key=lambda x: -x[1].get('count', 0)):
                if not isinstance(d, dict):
                    continue
                cnt = d.get('count', 0)
                total = d.get('total_indices', 0)
                pct = f"{cnt / total * 100:.1f}" if total > 0 else "—"
                rows.append([str(col), str(cnt), str(total), pct])
            if len(rows) > 1:
                bundle.anomaly_table_md = _rows_to_md_table(rows)
        print(f"[DIAG] from_providers cleaning tables: "
              f"has_run={cleaning_has_run}, stats={bool(bundle.cleaning_table_md)}, "
              f"anomaly={bool(anomaly_info)}, rows={len(anomaly_info) if anomaly_info else 0}")

        # ── CompareProvider ──
        cw = getattr(main_win, 'compare_tab_widget', None)
        print(f"[DIAG] from_providers CompareProvider: cw_exists={cw is not None}, "
              f"_last_comparison={bool(getattr(cw, '_last_comparison', None)) if cw else 'N/A'}")
        if cw:
            lc = getattr(cw, '_last_comparison', None) or {}
            if lc:
                th = lc.get('time_h')
                srcs = lc.get('sources', {}) or {}
                if th is not None and isinstance(srcs, dict) and srcs:
                    aligned_compare = _downsample_series(
                        {
                            "time_h": th,
                            **{str(name): values for name, values in srcs.items()},
                        },
                        max_points=200,
                    )
                    bundle.compare_time_h = [
                        float(value)
                        for value in aligned_compare.pop("time_h", [])
                    ]
                    bundle.compare_sources = {
                        name: [float(value) for value in values]
                        for name, values in aligned_compare.items()
                    }
                bundle.compare_pairs = lc.get('pairs', []) or []

        # ── CalibrationProvider ──
        ct = getattr(main_win, 'calibration_tab_widget', None)
        tp = getattr(ct, 'temp_page', None) if ct else None
        sp = getattr(ct, 'strain_page', None) if ct else None
        _pb = getattr(tp, '_phase_b_state', {}) or {} if tp else {}
        _comp = _pb.get('compensation') or {} if isinstance(_pb, dict) else {}
        _dec = _pb.get('decoupling_results') or {} if isinstance(_pb, dict) else {}
        _strain_cfgs = getattr(sp, '_strain_configs', {}) or {} if sp else {}
        _cal_ref_n = 0
        print(f"[DIAG] from_providers CalibrationProvider: ct_exists={ct is not None}, "
              f"temp_page={tp is not None}, strain_page={sp is not None}, "
              f"phase_b_state={bool(_pb)}, compensation_n={len(_comp)}, "
              f"decoupling_n={len(_dec)}, strain_configs_n={len(_strain_cfgs)}")
        if tp:
            bundle.temp_regressions = _extract_phase_a_regressions(tp)
            bundle.phaseb_diagnostics = _extract_phase_b_diagnostics(tp)
            pb = getattr(tp, '_phase_b_state', {}) or {}
            comp = pb.get('compensation') or {}

            # ── G1 分级表 ──
            if comp:
                rows = [["传感器", "残余σ (%FS)", "迟滞 (%FS)", "评级", "通过", "原因"]]
                for s_name, cd in sorted(comp.items()):
                    if not isinstance(cd, dict):
                        continue
                    gd = cd.get('grade') or {}
                    if isinstance(gd, dict):
                        grade_val = gd.get('grade', '?')
                        passed = bool(gd.get('passed', str(grade_val) not in ('FAIL', 'ERROR', 'N/A')))
                        reasons = gd.get('reasons', []) or []
                    elif isinstance(gd, (str, int, float)):
                        grade_val = gd
                        passed = str(grade_val) not in ('FAIL', 'ERROR', 'N/A')
                        reasons = []
                    else:
                        grade_val = getattr(gd, 'grade', '?')
                        passed = bool(getattr(gd, 'passed', False))
                        reasons = getattr(gd, 'reasons', []) or []
                    grade_str = str(grade_val) if grade_val is not None else '?'
                    if not isinstance(reasons, (list, tuple)):
                        reasons = []
                    reason_str = '; '.join(str(r) for r in reasons[:2]) if reasons else '—'
                    mt = cd.get('metrics')
                    if isinstance(mt, dict):
                        sigma_raw = mt.get('residual_sigma_pct_fs')
                        hys_raw = mt.get('hysteresis_max_pct_fs')
                    else:
                        sigma_raw = getattr(mt, 'residual_sigma_pct_fs', None) if mt is not None else None
                        hys_raw = getattr(mt, 'hysteresis_max_pct_fs', None) if mt is not None else None
                    sigma_pct = _safe_float(sigma_raw)
                    hys_pct = _safe_float(hys_raw)
                    rows.append([
                        s_name,
                        f"{sigma_pct:.2f}" if sigma_pct is not None else '—',
                        f"{hys_pct:.2f}" if hys_pct is not None else '—',
                        grade_str,
                        "✓" if passed else "✗",
                        reason_str,
                    ])
                if len(rows) > 1:
                    bundle.grade_table_md = _rows_to_md_table(rows)

            # ── T5 温度B解耦标量表 ──
            dec = pb.get('decoupling_results') or {}
            if dec:
                rows = [["传感器", "ε_mean (με)", "ε_std (με)", "ε_range (με)", "评级"]]
                for s_name, d in sorted(dec.items()):
                    if not isinstance(d, dict):
                        continue
                    rows.append([
                        s_name,
                        f"{_safe_float(d.get('e_mean')):.2f}" if d.get('e_mean') is not None else '—',
                        f"{_safe_float(d.get('e_std')):.2f}" if d.get('e_std') is not None else '—',
                        f"{_safe_float(d.get('e_range')):.2f}" if d.get('e_range') is not None else '—',
                        str(d.get('rating', '—')),
                    ])
                if len(rows) > 1:
                    bundle.decoupling_table_md = _rows_to_md_table(rows)

            # ── 迟滞回线数据 (hyst_sensors) ──
            sensors = pb.get('sensors') or {}
            if sensors:
                for s_name, sd in sensors.items():
                    if not isinstance(sd, dict):
                        continue
                    T_abs = _sequence_to_list(sd.get('T_abs'))
                    eps = _select_hysteresis_eps(sd, T_abs)
                    pair_count = min(len(T_abs), len(eps))
                    T_abs = T_abs[:pair_count]
                    eps = eps[:pair_count]
                    if T_abs and eps:
                        aligned = _downsample_series({
                            "T_abs": T_abs,
                            "eps": eps,
                        })
                        bundle.hyst_sensors.append({
                            'name': s_name,
                            'T_abs': aligned["T_abs"],
                            'eps': aligned["eps"],
                        })

        # ── S2 应变Ke汇总表 ──
        if _strain_cfgs:
            rows = [["传感器", "模式", "Ke1 (pm/με)", "Ke2 (pm/με)", "R²", "备注"]]
            for s_name, cfg in _strain_cfgs.items():
                ke = getattr(cfg, 'ke_results', {}) or {}
                mode = getattr(cfg, 'sensor_mode', 'single')
                gauge = getattr(cfg, 'gauge_length_mm', 80.0)
                ke1 = ke.get('Ke1', ke.get('Ke1', 0)) if isinstance(ke, dict) else 0
                ke2 = ke.get('Ke2', ke.get('Ke2', 0)) if isinstance(ke, dict) else 0
                note_parts = []
                if mode == 'single':
                    note_parts.append("单栅")
                elif mode == 'dual_anchored':
                    note_parts.append("双栅-锚固")
                elif mode == 'dual_working':
                    note_parts.append("双栅-双工作")
                note_parts.append(f"标距={gauge:.0f}mm")
                calibration = _extract_calibration_series(cfg)
                rows.append([
                    s_name,
                    mode,
                    f"{ke1:.4f}" if isinstance(ke1, (int, float)) and abs(ke1) > 1e-10 else '—',
                    f"{ke2:.4f}" if isinstance(ke2, (int, float)) and abs(ke2) > 1e-10 else '—',
                    f"{calibration['r2']:.5f}" if calibration is not None else '—',
                    ' | '.join(note_parts),
                ])
            if len(rows) > 1:
                bundle.ke_table_md = _rows_to_md_table(rows)

        # ── 标定线性度散点 (Phase 1a: 多传感器 → per-sensor dict 列表) ──
        if _strain_cfgs:
            for s_name, cfg in _strain_cfgs.items():
                calibration = _extract_calibration_series(cfg)
                if calibration is not None:
                    ref_vals = calibration["ref"]
                    meas_vals = calibration["measured"]
                    bundle.calib_sensors.append({
                        "sensor": s_name,
                        "ref": ref_vals,
                        "measured": meas_vals,
                        "slope": calibration["slope"],
                        "r2": calibration["r2"],
                        "unit": "pm",
                        "curves": calibration["curves"],
                        "source": calibration["source"],
                    })
                    _cal_ref_n = max(_cal_ref_n, len(ref_vals))
            print(f"[DIAG] from_providers CalibrationProvider: "
                  f"calib_sensors_n={len(bundle.calib_sensors)}, "
                  f"max_ref_n={_cal_ref_n}")

        print(f"[DIAG] from_providers tables: grade={bool(bundle.grade_table_md)}, "
              f"cleaning={bool(bundle.cleaning_table_md)}, "
              f"anomaly={bool(bundle.anomaly_table_md)}, "
              f"ke={bool(bundle.ke_table_md)}, "
              f"decoupling={bool(bundle.decoupling_table_md)}")
        print(f"[DIAG] from_providers calibration charts: "
              f"phase_a_regressions={len(bundle.temp_regressions)}, "
              f"phase_b_diagnostics={len(bundle.phaseb_diagnostics)}")
        return bundle

    @classmethod
    def from_diagnosis_record(cls, rec: dict) -> ChartBundle:
        """从已存诊断记录还原 ChartBundle (1.2 schema 的 chart_data 段)。"""
        cd = rec.get('chart_data', {}) or {}
        return cls(**{k: v for k, v in cd.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        """序列化为 JSON。"""
        from dataclasses import asdict
        return asdict(self)


# ═══════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════

def _safe_float(v) -> float | None:
    """安全取 float，None/non-numeric → None。"""
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _rows_to_md_table(rows: list[list[str]]) -> str:
    """将行列数据转为 pipe 格式 markdown 表格 (word_builder 自动渲染)。
    rows[0] 为表头，其余为数据行。
    """
    if not rows:
        return ""
    lines: list[str] = []
    lines.append("| " + " | ".join(rows[0]) + " |")
    lines.append("|" + "|".join("---" for _ in rows[0]) + "|")
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def gen_markdown_tables_from_bundle(cd: dict) -> str:
    """从 ChartBundle 字典生成全部 markdown 表格文本（Batch A）。

    返回的 markdown 可直接喂给 word_builder，自动渲染为 docx 表格。
    表之间的空行保证 word_builder 的 _extract_md_tables 正确分割。
    """
    tables: list[str] = []
    for key, heading in [
        ("cleaning_table_md", "### 数据清洗统计"),
        ("grade_table_md", "### 传感器分级汇总"),
        ("decoupling_table_md", "### 温度标定 — 解耦诊断标量"),
        ("ke_table_md", "### 应变标定 — Ke 系数汇总"),
        ("anomaly_table_md", "### 异常统计"),
    ]:
        md = cd.get(key, "") or ""
        if md.strip():
            tables.append(f"\n{heading}\n\n{md}\n")
    return "\n".join(tables) if tables else ""


@dataclass
class TableData:
    """单张附表的结构化数据 — extract_four_tables 的返回单元。

    由调用方用各自 API 渲染（WordBuilder markdown pipeline / python-docx add_table）。
    """
    heading: str
    headers: list[str]
    rows: list[list[str]]


def extract_four_tables(cd: dict) -> list[TableData]:
    """从 chart_data 字典提取报告附表的结构化数据。

    返回: [TableData(heading=..., headers=[...], rows=[[...]]), ...]
    空表/缺失表自动省略 — 不崩、不出空壳。

    函数名为旧公开 API，保留以兼容现有调用方；当前可返回五张表。
    """
    tables: list[TableData] = []
    for key, heading in [
        ("cleaning_table_md", "数据清洗统计"),
        ("grade_table_md", "传感器分级汇总"),
        ("decoupling_table_md", "温度标定 — 解耦诊断标量"),
        ("ke_table_md", "应变标定 — Ke 系数汇总"),
        ("anomaly_table_md", "异常统计"),
    ]:
        md = (cd.get(key, "") or "").strip()
        if not md:
            continue
        parsed = _parse_single_md_table(md)
        if parsed is None:
            continue
        headers, rows = parsed
        if not headers or not rows:
            continue
        tables.append(TableData(heading=heading, headers=headers, rows=rows))
    return tables


def _parse_single_md_table(md: str) -> tuple[list[str], list[list[str]]] | None:
    """解析单个 markdown pipe 表格，返回 (headers, rows) 或 None。

    容错：无分隔行或数据行不足 → None（调用方自动省略）。
    """
    lines = md.strip().split('\n')
    data_rows: list[list[str]] = []
    has_sep = False
    for ln in lines:
        stripped = ln.strip()
        if not stripped.startswith('|'):
            continue
        cells = [c.strip() for c in stripped.strip('|').split('|')]
        if len(cells) < 2:
            continue
        # 检测分隔行 (|---|, |:---|, |---:|, 等)
        if all(re.match(r'^:?-{2,}:?$', c) for c in cells if c):
            has_sep = True
            continue
        data_rows.append(cells)
    if len(data_rows) < 2 or not has_sep:
        return None
    return data_rows[0], data_rows[1:]
