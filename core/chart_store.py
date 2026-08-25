"""图表存储 — 遍历注册表 → 够条件则产 PNG → 落盘 → 出 ChartManifest。

不接入主流程；入口 build_chart_store 由调用方传入 chart_data + 落盘目录。
"""

from __future__ import annotations

import os as _os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

import numpy as np

from core.chart_registry import (
    ChartProducer,
    CHART_ID_TO_OLD_FIG_ID,
    get_all_producers,
    ensure_draw_fns,
    select_delta_series,
    select_physical_series,
)


# ═══════════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ChartManifestEntry:
    """产出的单条图表记录 (写入 chart_manifest.json)。"""

    chart_id: str
    module: str
    title: str
    rel_path: str = ""          # 相对于 charts_dir 的 PNG 路径
    produced: bool = False      # 产出成功?
    skip_reason: str = ""       # 跳过原因 (不成功时)
    key_stat: str = ""          # 关键统计简述
    data_fingerprint: str = ""  # 留位: 数据指纹
    report_include: bool = True  # False = 仅诊断补充，不注入 Word/PPT


# ═══════════════════════════════════════════════════════════════════════
# 出图 / 落盘
# ═══════════════════════════════════════════════════════════════════════


def _produce_calib_lin(cd: dict, charts_dir: str, entries: list[ChartManifestEntry],
                       warnings: list[str]) -> None:
    """产 strain_calib_lin — 每传感器一张应变标定曲线。"""
    from core.tools.calibration_chart_tool import render_strain_calibration_curves

    calib_sensors = cd.get('calib_sensors', []) or []
    for s in calib_sensors:
        try:
            ref = [float(value) for value in (s.get('ref', []) or [])]
            measured = [float(value) for value in (s.get('measured', []) or [])]
            name = s.get('sensor', '?')
            if len(ref) < 3 or len(measured) < 3:
                reason = f"calib({name}: ref={len(ref)}<3 or measured={len(measured)}<3)"
                entries.append(ChartManifestEntry(
                    chart_id=f"strain_calib_lin_{name}",
                    module="strain_calib",
                    title=f"{name} 应变标定曲线",
                    produced=False,
                    skip_reason=reason,
                ))
                warnings.append(f"标定图[{name}]跳过: 数据点={min(len(ref), len(measured))}, 合格门槛=3")
                continue
            raw_curves = s.get("curves")
            curves = (
                {
                    str(label): [float(value) for value in values]
                    for label, values in raw_curves.items()
                }
                if isinstance(raw_curves, dict) and raw_curves
                else {"G1": measured}
            )
            png_bytes = render_strain_calibration_curves(
                ref,
                curves,
                sensor=str(name),
                source=str(s.get("source") or "measured"),
            )
            if not png_bytes:
                raise ValueError("无可绘制的有限标定点")
            png_name = f"calib_linearity_{name}.png"
            png_path = _os.path.join(charts_dir, png_name)
            with open(png_path, "wb") as file:
                file.write(png_bytes)
            entries.append(ChartManifestEntry(
                chart_id=f"strain_calib_lin_{name}",
                module="strain_calib",
                title=f"{name} 应变标定曲线",
                rel_path=png_name,
                produced=True,
                key_stat="",  # 由注册表 key_stat_fn 覆盖
            ))
        except Exception as e:
            reason = f"calib({e})"
            entries.append(ChartManifestEntry(
                chart_id=f"strain_calib_lin_{s.get('sensor', '?')}",
                module="strain_calib",
                title=f"{s.get('sensor', '?')} 应变标定曲线",
                produced=False,
                skip_reason=reason,
            ))
            warnings.append(f"标定图[{s.get('sensor', '?')}]失败: {e}")
    if not calib_sensors:
        raise ValueError("strain_calib_lin called but calib_sensors is empty")


def _produce_ts_cleaning(cd: dict, charts_dir: str, entry: ChartManifestEntry) -> None:
    """产 data_ts_cleaning。"""
    import numpy as np
    from core.report_charts import make_timeseries, save_figure

    time_h = cd.get('time_h', []) or []
    series = cd.get('series', {}) or {}
    cleaned = cd.get('cleaned', {}) or {}
    t = np.array(time_h, dtype=float)
    s = {k: np.array(v, dtype=float) for k, v in series.items()}
    cl = {k: np.array(v, dtype=float) for k, v in cleaned.items()} if cleaned else None
    fig = make_timeseries(t, s, y_label="值", cleaned=cl, trend=True)
    png_path = _os.path.join(charts_dir, "ts_cleaning.png")
    save_figure(fig, png_path)
    entry.rel_path = "ts_cleaning.png"
    entry.produced = True
    entry.key_stat = f"{len(s)}通道"


def _produce_t2_timeseries(
    cd: dict,
    charts_dir: str,
    entry: ChartManifestEntry,
    *,
    series: dict[str, Any],
    y_label: str,
    png_name: str,
) -> None:
    """复用 make_timeseries 产一张 T2 时程图。"""
    from core.report_charts import make_timeseries, save_figure

    raw_time_h = cd.get("time_h")
    time_h = [] if raw_time_h is None else raw_time_h
    t = np.array(time_h, dtype=float)
    values: dict[str, Sequence[float]] = {
        name: [float(value) for value in np.asarray(data, dtype=float).reshape(-1)]
        for name, data in series.items()
    }
    fig = make_timeseries(t, values, y_label=y_label)
    if fig.axes:
        fig.axes[0].set_title(entry.title)
    png_path = _os.path.join(charts_dir, png_name)
    save_figure(fig, png_path)
    entry.rel_path = png_name
    entry.produced = True
    entry.key_stat = f"{len(values)}通道"


def _produce_ts_dlambda(cd: dict, charts_dir: str, entry: ChartManifestEntry) -> None:
    _produce_t2_timeseries(
        cd,
        charts_dir,
        entry,
        series=select_delta_series(cd),
        y_label="波长差 Δλ (nm)",
        png_name="ts_dlambda.png",
    )


def _produce_ts_strain(cd: dict, charts_dir: str, entry: ChartManifestEntry) -> None:
    _produce_t2_timeseries(
        cd,
        charts_dir,
        entry,
        series=select_physical_series(cd, "strain"),
        y_label="应变 (με)",
        png_name="ts_strain.png",
    )


def _produce_ts_temperature(cd: dict, charts_dir: str, entry: ChartManifestEntry) -> None:
    _produce_t2_timeseries(
        cd,
        charts_dir,
        entry,
        series=select_physical_series(cd, "temperature"),
        y_label="温度 (°C)",
        png_name="ts_temperature.png",
    )


def _produce_ts_formula(cd: dict, charts_dir: str, entry: ChartManifestEntry) -> None:
    _produce_t2_timeseries(
        cd,
        charts_dir,
        entry,
        series=select_physical_series(cd, "formula"),
        y_label="值",
        png_name="ts_formula.png",
    )


def _produce_tempa_regression(
    cd: dict,
    charts_dir: str,
    entry: ChartManifestEntry,
) -> None:
    """产阶段 A 温度系数回归网格图。"""
    from core.tools.calibration_chart_tool import render_temp_regression_grid

    png_bytes = render_temp_regression_grid(cd.get("temp_regressions") or [])
    if not png_bytes:
        raise ValueError("阶段 A 回归数据不可绘制")
    png_name = "temp_phase_a_regression.png"
    with open(_os.path.join(charts_dir, png_name), "wb") as file:
        file.write(png_bytes)
    entry.rel_path = png_name
    entry.produced = True


def _produce_phaseb_diagnostic(
    cd: dict,
    charts_dir: str,
    entries: list[ChartManifestEntry],
    warnings: list[str],
) -> None:
    """产阶段 B 诊断四联图 — 每双栅传感器补偿前/后各一张。"""
    from core.tools.calibration_chart_tool import render_phase_b_diagnostic

    diagnostics = cd.get("phaseb_diagnostics") or []
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue
        sensor_name = str(diagnostic.get("name", "?"))
        variants = (
            ("raw", "原始（补偿前）", "eps_raw"),
            ("compensated", "补偿后", "eps_compensated"),
        )
        for variant, variant_title, series_key in variants:
            chart_id = f"phaseb_diagnostic_{sensor_name}_{variant}"
            title = f"{sensor_name} 阶段 B {variant_title}诊断四联图"
            series = diagnostic.get(series_key)
            if series is None and variant == "raw":
                series = diagnostic.get("eps_corr")
            try:
                series_count = len(series) if series is not None else 0
            except TypeError:
                series_count = 0
            if series_count < 3:
                reason = f"{series_key}有效数据点不足"
                entries.append(ChartManifestEntry(
                    chart_id=chart_id,
                    module="temperature_calib",
                    title=title,
                    produced=False,
                    skip_reason=f"phaseb_diagnostic({reason})",
                ))
                warnings.append(
                    f"阶段 B {variant_title}四联图[{sensor_name}]失败: {reason}"
                )
                continue

            try:
                variant_diagnostic = dict(diagnostic)
                variant_diagnostic["variant"] = variant
                png_bytes = render_phase_b_diagnostic(variant_diagnostic)
                if not png_bytes:
                    raise ValueError("有效数据点不足")
                png_name = (
                    f"temp_phase_b_diagnostic_{sensor_name}_{variant}.png"
                )
                with open(_os.path.join(charts_dir, png_name), "wb") as file:
                    file.write(png_bytes)
                entries.append(ChartManifestEntry(
                    chart_id=chart_id,
                    module="temperature_calib",
                    title=title,
                    rel_path=png_name,
                    produced=True,
                ))
            except Exception as exc:
                entries.append(ChartManifestEntry(
                    chart_id=chart_id,
                    module="temperature_calib",
                    title=title,
                    produced=False,
                    skip_reason=f"phaseb_diagnostic({exc})",
                ))
                warnings.append(
                    f"阶段 B {variant_title}四联图[{sensor_name}]失败: {exc}"
                )


def _produce_compare_ol(cd: dict, charts_dir: str, entry: ChartManifestEntry) -> None:
    """产 compare_ol。"""
    import numpy as np
    from core.report_charts import make_comparison_overlay, save_figure

    cmp_t = cd.get('compare_time_h', []) or []
    cmp_src = cd.get('compare_sources', {}) or {}
    fig = make_comparison_overlay(
        np.array(cmp_t, dtype=float),
        {k: np.array(v, dtype=float) for k, v in cmp_src.items()},
        y_label="应变 (με)",
    )
    png_path = _os.path.join(charts_dir, "compare_ol.png")
    save_figure(fig, png_path)
    entry.rel_path = "compare_ol.png"
    entry.produced = True
    entry.key_stat = f"{len(cmp_src)}源"


def _produce_corr_scatter(cd: dict, charts_dir: str, entries: list[ChartManifestEntry],
                          _warnings: list[str] | None = None) -> None:
    """产 compare_corr_scatter — 每 pair 一张。one producer → N entries。"""
    import numpy as np
    from core.report_charts import make_correlation_scatter, save_figure

    cmp_src = cd.get('compare_sources', {}) or {}
    pairs = cd.get('compare_pairs', []) or []
    for pi, pr in enumerate(pairs[:3]):
        a, b = pr.get('device_a', 'A'), pr.get('device_b', 'B')
        sa = np.array(cmp_src.get(a, []), dtype=float)
        sb = np.array(cmp_src.get(b, []), dtype=float)
        if len(sa) < 2 or len(sb) < 2:
            # per-item 静默跳过 → 记 skip entry
            entries.append(ChartManifestEntry(
                chart_id=f"compare_corr_scatter_{pi}",
                module="compare",
                title=f"{a}↔{b} 相关散点",
                produced=False,
                skip_reason=f"corr_{pi}({a}↔{b}: len={len(sa)},{len(sb)}<2)",
            ))
            continue
        nc = min(len(sa), len(sb))
        fig = make_correlation_scatter(
            sa[:nc], sb[:nc],
            label_x=f"{a} (με)", label_y=f"{b} (με)",
            corr=pr.get('corr'), rmse=pr.get('rmse'),
        )
        png_name = f"corr_{a}_{b}.png"
        png_path = _os.path.join(charts_dir, png_name)
        save_figure(fig, png_path)
        entries.append(ChartManifestEntry(
            chart_id=f"compare_corr_scatter_{pi}",
            module="compare",
            title=f"{a}↔{b} 相关散点",
            rel_path=png_name,
            produced=True,
            key_stat="",  # 由注册表 key_stat_fn 覆盖
        ))
    if not pairs:
        raise ValueError("compare_corr_scatter called but compare_pairs is empty")


def _produce_hyst(cd: dict, charts_dir: str, entries: list[ChartManifestEntry],
                  warnings: list[str]) -> None:
    """产 phaseb_hyst — 每传感器一张。one producer → N entries。"""
    import numpy as np
    from core.report_charts import make_hysteresis_loop, save_figure

    for hs in cd.get('hyst_sensors', []) or []:
        try:
            T_arr = np.array(hs['T_abs'], dtype=float)
            eps_arr = np.array(hs['eps'], dtype=float)
            valid = ~(np.isnan(T_arr) | np.isnan(eps_arr))
            n = hs.get('name', '?')
            vt = int(valid.sum())
            tot = len(T_arr)
            if vt < 3:
                reason = f"hyst({n}: valid={vt}<3, total={tot}, NaN={tot-vt})"
                entries.append(ChartManifestEntry(
                    chart_id=f"phaseb_hyst_{n}",
                    module="phaseb",
                    title=f"{n} 迟滞回线",
                    produced=False,
                    skip_reason=reason,
                ))
                warnings.append(f"迟滞图[{n}]跳过: 有效数据点={vt}, 合格门槛=3, 总数={tot}")
                continue
            fig = make_hysteresis_loop(
                T_arr[valid], eps_arr[valid], sensor=n,
                x_label="温度 (°C)", y_label="应变 (με)",
            )
            png_name = f"hyst_{n}.png"
            png_path = _os.path.join(charts_dir, png_name)
            save_figure(fig, png_path)
            entries.append(ChartManifestEntry(
                chart_id=f"phaseb_hyst_{n}",
                module="phaseb",
                title=f"{n} 迟滞回线",
                rel_path=png_name,
                produced=True,
                key_stat="",  # 由注册表 key_stat_fn 覆盖
            ))
        except Exception as e:
            reason = f"hyst({e})"
            entries.append(ChartManifestEntry(
                chart_id=f"phaseb_hyst_{hs.get('name', '?')}",
                module="phaseb",
                title=f"{hs.get('name', '?')} 迟滞回线",
                produced=False,
                skip_reason=reason,
            ))
            warnings.append(f"迟滞图[{hs.get('name', '?')}]失败: {e}")


# ═══════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════

_PRODUCER_FN: dict[str, Callable] = {
    "strain_calib_lin": _produce_calib_lin,
    "data_ts_cleaning": _produce_ts_cleaning,
    "data_ts_dlambda": _produce_ts_dlambda,
    "data_ts_strain": _produce_ts_strain,
    "data_ts_temperature": _produce_ts_temperature,
    "data_ts_formula": _produce_ts_formula,
    "tempa_regression": _produce_tempa_regression,
    "phaseb_diagnostic": _produce_phaseb_diagnostic,
    "compare_ol": _produce_compare_ol,
    "compare_corr_scatter": _produce_corr_scatter,
    "phaseb_hyst": _produce_hyst,
}

# chart_id 为"列表类"（one producer → N entries）的集合
_LIST_PRODUCER_IDS: set[str] = {
    "strain_calib_lin",
    "phaseb_diagnostic",
    "compare_corr_scatter",
    "phaseb_hyst",
}


def build_chart_store(
    chart_data: dict,
    charts_dir: str,
    warnings: list[str],
) -> list[ChartManifestEntry]:
    """遍历注册表 → 对够条件的图产 PNG → 落盘 → 返回 chart_manifest。

    Args:
        chart_data:  ChartBundle.to_dict() 产物 (或兼容 dict)
        charts_dir:  PNG 落盘根目录 (调用方创建)
        warnings:    出图警告列表 (skip / 异常均追加)

    Returns:
        ChartManifestEntry 列表 (按 chart_id 排序)。
        每条目含 chart_id / produced / rel_path / skip_reason 等。
    """
    ensure_draw_fns()
    _os.makedirs(charts_dir, exist_ok=True)
    result: list[ChartManifestEntry] = []

    for producer in get_all_producers():
        chart_id = producer.chart_id

        # ── 检查 required_keys ──
        missing_keys = [k for k in producer.required_keys
                        if not chart_data.get(k) or
                           (isinstance(chart_data.get(k), (list, dict)) and
                            len(chart_data.get(k, None) or []) == 0)]
        if missing_keys and set(missing_keys) == set(producer.required_keys):
            # 全部 required keys 缺失或为空 → 整图 skip
            skip_id = CHART_ID_TO_OLD_FIG_ID.get(chart_id, chart_id)
            reason = f"{skip_id}(no {'/'.join(missing_keys)})"
            result.append(ChartManifestEntry(
                chart_id=chart_id, module=producer.module, title=producer.title,
                produced=False, skip_reason=reason,
                report_include=producer.report_include,
            ))
            continue

        # ── 检查 produces_when ──
        if producer.produces_when is not None and not producer.produces_when(chart_data):
            skip_id = CHART_ID_TO_OLD_FIG_ID.get(chart_id, chart_id)
            reason = f"{skip_id}(produces_when=False)"
            result.append(ChartManifestEntry(
                chart_id=chart_id, module=producer.module, title=producer.title,
                produced=False, skip_reason=reason,
                report_include=producer.report_include,
            ))
            continue

        # ── 列表类图 (one producer → N entries) ──
        if chart_id in _LIST_PRODUCER_IDS:
            produce_fn = _PRODUCER_FN[chart_id]
            n_before = len(result)
            try:
                produce_fn(chart_data, charts_dir, result, warnings)
            except Exception as e:
                import traceback
                reason = f"{chart_id}({type(e).__name__}: {e})"
                result.append(ChartManifestEntry(
                    chart_id=chart_id, module=producer.module, title=producer.title,
                    produced=False, skip_reason=reason,
                    report_include=producer.report_include,
                ))
                warnings.append(f"图表[{chart_id}]异常: {e}\n{traceback.format_exc()}")
                continue
            for index in range(n_before, len(result)):
                result[index].report_include = producer.report_include
            # ★ key_stat 收敛: 列表类产完后从 key_stat_fn 取 per-item 覆盖
            if producer.key_stat_fn is not None:
                try:
                    key_stats = producer.key_stat_fn(chart_data)
                    if isinstance(key_stats, dict):
                        prefix = chart_id + "_"
                        for i in range(n_before, len(result)):
                            entry = result[i]
                            if entry.produced and entry.chart_id.startswith(prefix):
                                suffix = entry.chart_id[len(prefix):]
                                if suffix in key_stats:
                                    entry.key_stat = key_stats[suffix]
                except Exception as e:
                    warnings.append(f"key_stat_fn[{chart_id}]异常: {e}")
            continue

        # ── 单图类 ──
        entry = ChartManifestEntry(
            chart_id=chart_id,
            module=producer.module,
            title=producer.title,
            report_include=producer.report_include,
        )
        produce_fn = _PRODUCER_FN[chart_id]
        try:
            produce_fn(chart_data, charts_dir, entry)
        except Exception as e:
            import traceback
            entry.produced = False
            entry.skip_reason = f"{chart_id}({type(e).__name__}: {e})"
            warnings.append(f"图表[{chart_id}]异常: {e}\n{traceback.format_exc()}")
        # ★ key_stat 收敛为注册表单源：单图类产完后从 key_stat_fn 取
        if entry.produced and producer.key_stat_fn is not None:
            try:
                ks = producer.key_stat_fn(chart_data)
                if isinstance(ks, str):
                    entry.key_stat = ks
            except Exception as e:
                warnings.append(f"key_stat_fn[{chart_id}]异常: {e}")
        result.append(entry)

    def _manifest_sort_key(entry: ChartManifestEntry) -> str:
        chart_id = entry.chart_id
        if chart_id.startswith("phaseb_diagnostic_"):
            if chart_id.endswith("_raw"):
                return chart_id[:-4] + "_0_raw"
            if chart_id.endswith("_compensated"):
                return chart_id[:-12] + "_1_compensated"
        return chart_id

    return sorted(result, key=_manifest_sort_key)


# ═══════════════════════════════════════════════════════════════════════
# 消费者 API
# ═══════════════════════════════════════════════════════════════════════


def get_chart(
    chart_id: str,
    chart_manifest: list[ChartManifestEntry],
    charts_dir: str,
) -> str | None:
    """按 chart_id 获取已落盘 PNG 的绝对路径。

    Args:
        chart_id: 图标识 (可精确匹配，也可匹配 chart_id 前缀——如 phaseb_hyst_ → 第一个 hit)
        chart_manifest: build_chart_store 返回的清单
        charts_dir: PNG 落盘根目录

    Returns:
        PNG 绝对路径，或 None (未找到/未产出)
    """
    for entry in chart_manifest:
        if entry.chart_id == chart_id and entry.produced and entry.rel_path:
            return _os.path.join(charts_dir, entry.rel_path)
    # 前缀匹配: phaseb_hyst → phaseb_hyst_A1, compare_corr_scatter → compare_corr_scatter_0
    for entry in chart_manifest:
        if entry.chart_id.startswith(chart_id) and entry.produced and entry.rel_path:
            return _os.path.join(charts_dir, entry.rel_path)
    return None


def chart_manifest_to_figure_manifest(
    chart_manifest: list[ChartManifestEntry],
    charts_dir: str,
) -> Any:
    """将 ChartManifestEntry 列表转为 FigureManifest (供 word/ppt 注图兼容)。

    chart_id → fig_id 映射使用 CHART_ID_TO_OLD_FIG_ID (类型级)；
    列表中 hyst/corr 的动态 chart_id 直接作为 fig_id。
    """
    from core.report_charts import FigureManifest
    from core.chart_registry import CHART_ID_TO_OLD_FIG_ID

    fm = FigureManifest()
    for entry in chart_manifest:
        if not entry.produced or not entry.rel_path or not entry.report_include:
            continue
        fig_id = entry.chart_id
        # 若 type-level chart_id 在映射表，用映射后 id
        base_cid = entry.chart_id
        stripped = False
        # 剥离 per-item 后缀 (如 _A1, _0, _1) 以匹配注册表 chart_id
        if "_" in base_cid:
            for prefix in ("phaseb_hyst_", "compare_corr_scatter_"):
                if base_cid.startswith(prefix):
                    base_cid = prefix.rstrip("_")
                    stripped = True
                    break
        if stripped:
            # Per-item 实例：保留原始唯一 chart_id 作为 fig_id，
            # 禁止合并到旧 aggregate fig_id（会破坏 PlanningRequest 唯一性验证）。
            pass  # fig_id already equals entry.chart_id
        else:
            mapped = CHART_ID_TO_OLD_FIG_ID.get(base_cid, fig_id)
            if mapped != base_cid:
                fig_id = mapped
        fm.add(
            fig_id,
            entry.module,
            entry.title,
            entry.key_stat,
            _os.path.join(charts_dir, entry.rel_path),
        )
    return fm


# ═══════════════════════════════════════════════════════════════════════
# 序列化 — manifest ↔ JSON-safe dict (供诊断记录持久化)
# ═══════════════════════════════════════════════════════════════════════


def chart_manifest_to_dict(manifest: list[ChartManifestEntry]) -> list[dict]:
    """将 ChartManifestEntry 列表序列化为 JSON-safe dict 列表（仅相对路径，不含 PNG 字节）。"""
    return [
        {
            "chart_id": e.chart_id,
            "module": e.module,
            "title": e.title,
            "rel_path": e.rel_path,
            "produced": e.produced,
            "skip_reason": e.skip_reason,
            "key_stat": e.key_stat,
            "report_include": e.report_include,
        }
        for e in manifest
    ]


def chart_manifest_from_dict(data: list[dict]) -> list[ChartManifestEntry]:
    """从 JSON-safe dict 列表反序列化为 ChartManifestEntry 列表。"""
    return [
        ChartManifestEntry(
            chart_id=d.get("chart_id", ""),
            module=d.get("module", ""),
            title=d.get("title", ""),
            rel_path=d.get("rel_path", ""),
            produced=d.get("produced", False),
            skip_reason=d.get("skip_reason", ""),
            key_stat=d.get("key_stat", ""),
            report_include=bool(d.get("report_include", True)),
        )
        for d in data
    ]
