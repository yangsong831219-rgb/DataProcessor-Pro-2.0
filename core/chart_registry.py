"""图表生产者注册表 — 声明"哪类图、谁画、需要什么数据、什么时候能产"。

不在此文件中执行产图逻辑；产图由 chart_store 通过遍历注册表驱动。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import matplotlib.pyplot as plt
import numpy as np


# ═══════════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ChartProducer:
    """图表生产者声明。

    Attributes:
        chart_id:   全局唯一图标识，命名规范 <module>_<图义>
        module:     所属模块 (data_analysis / strain_calib / compare / phaseb / ...)
        tier:       数据源层级 (T1 = 基础 chart_data，T2 = 派生物理量序列)
        required_keys: 产出此图必须在 chart_data 中存在的键列表 (全部缺 or 全部空则跳过)
        draw_fn:    绘制函数 — 接收关键字参数 → matplotlib Figure
        produces_when: 额外的触发条件谓词 (chart_data → bool)；None = 仅靠 required_keys
        title:      中文标题
        section:    报告所属节
        key_stat_fn: 从 chart_data 产出关键统计简述的可选函数
        report_include: 是否进入 Word/PPT；False 表示仅保留为诊断补充图
    """

    chart_id: str
    module: str
    tier: str = "T1"
    required_keys: list[str] = field(default_factory=list)
    draw_fn: Optional[Callable[..., Any]] = None
    produces_when: Optional[Callable[[dict], bool]] = None
    title: str = ""
    section: str = ""
    key_stat_fn: Optional[Callable[[dict], str | dict[str, str]]] = None
    report_include: bool = True


# ═══════════════════════════════════════════════════════════════════════
# 注册表
# ═══════════════════════════════════════════════════════════════════════

_CHART_REGISTRY: dict[str, ChartProducer] = {}


def _register(p: ChartProducer) -> ChartProducer:
    """注册一个 ChartProducer（内部 helper）。"""
    if p.chart_id in _CHART_REGISTRY:
        raise ValueError(f"chart_id '{p.chart_id}' already registered")
    _CHART_REGISTRY[p.chart_id] = p
    return p


# ═══════════════════════════════════════════════════════════════════════
# 11 个标准/补充图生产者注册
# ═══════════════════════════════════════════════════════════════════════

# ── 1. 标定线性度 (Phase 2: 列表类 — 每传感器一张) ──
def _calib_lin_produces_when(cd: dict) -> bool:
    calib_sensors = cd.get('calib_sensors', []) or []
    return bool(calib_sensors) and any(
        isinstance(s, dict) and len(s.get('ref', []) or []) >= 3
        for s in calib_sensors
    )


def _calib_lin_key_stat(cd: dict) -> dict[str, str]:
    """列表类 key_stat_fn — 返回 {sensor: stat_string} dict。
    由 build_chart_store 按 chart_id 后缀 (sensor 名) 自动分发到各 entry。"""
    calib_sensors = cd.get('calib_sensors', []) or []
    result: dict[str, str] = {}
    for s in calib_sensors:
        if not isinstance(s, dict):
            continue
        name = s.get('sensor', '?')
        r2 = s.get('r2', 0)
        k = s.get('slope', 0)
        result[name] = f"R²={r2:.4f}, k={k:.2f}"
    return result


_register(ChartProducer(
    chart_id="strain_calib_lin",
    module="strain_calib",
    tier="T1",
    required_keys=["calib_sensors"],
    draw_fn=None,  # 延迟绑定，避免循环 import
    produces_when=_calib_lin_produces_when,
    title="应变标定曲线",
    section="传感器标定",
    key_stat_fn=_calib_lin_key_stat,
))

# ── 2. 清洗时序图 ──
def _ts_cleaning_produces_when(cd: dict) -> bool:
    time_h = cd.get('time_h', []) or []
    series = cd.get('series', {}) or {}
    cleaned = cd.get('cleaned', {}) or {}
    return bool(time_h and series and cleaned)


def _ts_cleaning_key_stat(cd: dict) -> str:
    series = cd.get('series', {}) or {}
    return f"{len(series)}通道"


_register(ChartProducer(
    chart_id="data_ts_cleaning",
    module="data_analysis",
    tier="T1",
    required_keys=["time_h", "series"],
    draw_fn=None,
    produces_when=_ts_cleaning_produces_when,
    title="清洗前后时序对比",
    section="数据清洗",
    key_stat_fn=_ts_cleaning_key_stat,
))

# ── 3-6. 物理量时程图 (Phase 1-b: T2 producer) ──
def _has_series_values(values: Any) -> bool:
    """序列有至少一个元素；避免对 ndarray/Series 直接做布尔判断。"""
    if values is None:
        return False
    try:
        return len(values) > 0
    except TypeError:
        return False


def _physical_series_category(name: object) -> str:
    """按稳定优先级把物理量键分到互斥类别。"""
    text = str(name)
    if "应变" in text:
        return "strain"
    if "温度" in text:
        return "temperature"
    return "formula"


def select_physical_series(cd: dict, category: str) -> dict[str, Any]:
    """从 phys_series 选出指定类别，供注册条件与实际产图共同使用。"""
    if category not in {"strain", "temperature", "formula"}:
        raise ValueError(f"unknown physical series category: {category}")
    raw = cd.get("phys_series")
    if not isinstance(raw, dict):
        return {}
    return {
        str(name): values
        for name, values in raw.items()
        if _physical_series_category(name) == category
        and _has_series_values(values)
    }


def select_delta_series(cd: dict) -> dict[str, Any]:
    """返回有数据的 Δλ 序列，避免注册条件与产图逻辑漂移。"""
    raw = cd.get("series_delta")
    if not isinstance(raw, dict):
        return {}
    return {
        str(name): values
        for name, values in raw.items()
        if _has_series_values(values)
    }


def _t2_time_ready(cd: dict) -> bool:
    return _has_series_values(cd.get("time_h"))


def _dlambda_produces_when(cd: dict) -> bool:
    raw_physical = cd.get("phys_series")
    has_physical = isinstance(raw_physical, dict) and any(
        _has_series_values(values) for values in raw_physical.values()
    )
    return (
        _t2_time_ready(cd)
        and bool(select_delta_series(cd))
        and not has_physical
    )


def _strain_produces_when(cd: dict) -> bool:
    return _t2_time_ready(cd) and bool(select_physical_series(cd, "strain"))


def _temperature_produces_when(cd: dict) -> bool:
    return _t2_time_ready(cd) and bool(select_physical_series(cd, "temperature"))


def _formula_produces_when(cd: dict) -> bool:
    return _t2_time_ready(cd) and bool(select_physical_series(cd, "formula"))


def _dlambda_key_stat(cd: dict) -> str:
    return f"{len(select_delta_series(cd))}通道"


def _strain_key_stat(cd: dict) -> str:
    return f"{len(select_physical_series(cd, 'strain'))}通道"


def _temperature_key_stat(cd: dict) -> str:
    return f"{len(select_physical_series(cd, 'temperature'))}通道"


def _formula_key_stat(cd: dict) -> str:
    return f"{len(select_physical_series(cd, 'formula'))}通道"


_register(ChartProducer(
    chart_id="data_ts_dlambda",
    module="data_analysis",
    tier="T2",
    required_keys=["time_h", "series_delta"],
    draw_fn=None,
    produces_when=_dlambda_produces_when,
    title="波长差时程",
    section="数据分析",
    key_stat_fn=_dlambda_key_stat,
))

_register(ChartProducer(
    chart_id="data_ts_strain",
    module="data_analysis",
    tier="T2",
    required_keys=["time_h", "phys_series"],
    draw_fn=None,
    produces_when=_strain_produces_when,
    title="应变时程",
    section="数据分析",
    key_stat_fn=_strain_key_stat,
))

_register(ChartProducer(
    chart_id="data_ts_temperature",
    module="data_analysis",
    tier="T2",
    required_keys=["time_h", "phys_series"],
    draw_fn=None,
    produces_when=_temperature_produces_when,
    title="温度时程",
    section="数据分析",
    key_stat_fn=_temperature_key_stat,
))

_register(ChartProducer(
    chart_id="data_ts_formula",
    module="data_analysis",
    tier="T2",
    required_keys=["time_h", "phys_series"],
    draw_fn=None,
    produces_when=_formula_produces_when,
    title="自定义公式时程",
    section="数据分析",
    key_stat_fn=_formula_key_stat,
))

# ── 7. 温度标定阶段 A 回归网格 ──
def _tempa_regression_produces_when(cd: dict) -> bool:
    regressions = cd.get("temp_regressions") or []
    return bool(regressions)


def _tempa_regression_key_stat(cd: dict) -> str:
    return f"{len(cd.get('temp_regressions') or [])}光栅"


_register(ChartProducer(
    chart_id="tempa_regression",
    module="temperature_calib",
    tier="T1",
    required_keys=["temp_regressions"],
    draw_fn=None,
    produces_when=_tempa_regression_produces_when,
    title="阶段 A 温度系数回归",
    section="传感器标定",
    key_stat_fn=_tempa_regression_key_stat,
))


# ── 8. 温度标定阶段 B 诊断四联图 ──
def _phaseb_diagnostic_produces_when(cd: dict) -> bool:
    diagnostics = cd.get("phaseb_diagnostics") or []
    return bool(diagnostics)


def _phaseb_diagnostic_key_stat(cd: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in cd.get("phaseb_diagnostics") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "?"))
        for variant, series_key in (
            ("raw", "eps_raw"),
            ("compensated", "eps_compensated"),
        ):
            values = item.get(series_key)
            if values is None and variant == "raw":
                values = item.get("eps_corr")
            try:
                array = np.asarray(values, dtype=float).reshape(-1)
            except (TypeError, ValueError):
                continue
            finite = array[np.isfinite(array)]
            if len(finite) < 3:
                continue
            result[f"{name}_{variant}"] = (
                f"n={len(finite)}; σ={float(np.std(finite)):.2f}με"
            )
    return result


_register(ChartProducer(
    chart_id="phaseb_diagnostic",
    module="temperature_calib",
    tier="T1",
    required_keys=["phaseb_diagnostics"],
    draw_fn=None,
    produces_when=_phaseb_diagnostic_produces_when,
    title="阶段 B 诊断四联图",
    section="传感器标定",
    key_stat_fn=_phaseb_diagnostic_key_stat,
))


# ── 9. 多源叠加时程 ──
def _compare_ol_produces_when(cd: dict) -> bool:
    cmp_t = cd.get('compare_time_h', []) or []
    cmp_src = cd.get('compare_sources', {}) or {}
    return bool(cmp_t and cmp_src)


def _compare_ol_key_stat(cd: dict) -> str:
    cmp_src = cd.get('compare_sources', {}) or {}
    return f"{len(cmp_src)}源"


_register(ChartProducer(
    chart_id="compare_ol",
    module="compare",
    tier="T1",
    required_keys=["compare_time_h", "compare_sources"],
    draw_fn=None,
    produces_when=_compare_ol_produces_when,
    title="多源叠加时程",
    section="多源对比",
    key_stat_fn=_compare_ol_key_stat,
))

# ── 10. 成对相关散点 ──
def _compare_corr_scatter_produces_when(cd: dict) -> bool:
    pairs = cd.get('compare_pairs', []) or []
    return bool(pairs)


def _compare_corr_scatter_key_stat(cd: dict) -> dict[str, str]:
    """列表类: 返回 {str(pair_index): stat_string} 供 per-item 覆盖。"""
    result: dict[str, str] = {}
    for pi, pr in enumerate((cd.get('compare_pairs') or [])):
        result[str(pi)] = f"r={pr.get('corr', 0):.4f}"
    return result


_register(ChartProducer(
    chart_id="compare_corr_scatter",
    module="compare",
    tier="T1",
    required_keys=["compare_pairs"],
    draw_fn=None,
    produces_when=_compare_corr_scatter_produces_when,
    title="成对相关散点",
    section="多源对比",
    key_stat_fn=_compare_corr_scatter_key_stat,
))

# ── 11. 迟滞回线（诊断补充图，不进入正式报告） ──
def _phaseb_hyst_produces_when(cd: dict) -> bool:
    hysts = cd.get('hyst_sensors', []) or []
    return bool(hysts)


def _phaseb_hyst_key_stat(cd: dict) -> dict[str, str]:
    """列表类: 返回 {sensor_name: stat_string} 供 per-item 覆盖。"""
    result: dict[str, str] = {}
    for hs in (cd.get('hyst_sensors') or []):
        n = hs.get('name', '?')
        try:
            T_arr = np.array(hs.get('T_abs', []), dtype=float)
            eps_arr = np.array(hs.get('eps', []), dtype=float)
            valid = ~(np.isnan(T_arr) | np.isnan(eps_arr))
            vt = int(valid.sum())
        except Exception:
            vt = 0
        result[n] = f"n={vt}"
    return result


_register(ChartProducer(
    chart_id="phaseb_hyst",
    module="phaseb",
    tier="T1",
    required_keys=["hyst_sensors"],
    draw_fn=None,
    produces_when=_phaseb_hyst_produces_when,
    title="迟滞回线",
    section="传感器标定",
    key_stat_fn=_phaseb_hyst_key_stat,
    report_include=False,
))


# ═══════════════════════════════════════════════════════════════════════
# 延迟绑定 — import 相关模块后补齐 draw_fn
# ═══════════════════════════════════════════════════════════════════════

def _resolve_draw_fns() -> None:
    """将注册表中 draw_fn 占位补为真实函数引用 (避免循环 import)。"""
    from core.report_charts import (
        make_calibration_linearity,
        make_timeseries,
        make_comparison_overlay,
        make_correlation_scatter,
        make_hysteresis_loop,
    )
    from core.tools.calibration_chart_tool import (
        render_phase_b_diagnostic,
        render_temp_regression_grid,
    )
    _CHART_REGISTRY["strain_calib_lin"].draw_fn = make_calibration_linearity
    _CHART_REGISTRY["data_ts_cleaning"].draw_fn = make_timeseries
    _CHART_REGISTRY["data_ts_dlambda"].draw_fn = make_timeseries
    _CHART_REGISTRY["data_ts_strain"].draw_fn = make_timeseries
    _CHART_REGISTRY["data_ts_temperature"].draw_fn = make_timeseries
    _CHART_REGISTRY["data_ts_formula"].draw_fn = make_timeseries
    _CHART_REGISTRY["tempa_regression"].draw_fn = render_temp_regression_grid
    _CHART_REGISTRY["phaseb_diagnostic"].draw_fn = render_phase_b_diagnostic
    _CHART_REGISTRY["compare_ol"].draw_fn = make_comparison_overlay
    _CHART_REGISTRY["compare_corr_scatter"].draw_fn = make_correlation_scatter
    _CHART_REGISTRY["phaseb_hyst"].draw_fn = make_hysteresis_loop


# ═══════════════════════════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════════════════════════

def ensure_draw_fns() -> None:
    """保证 draw_fn 已绑定（幂等，可安全多调）。"""
    if _CHART_REGISTRY and any(p.draw_fn is None for p in _CHART_REGISTRY.values()):
        _resolve_draw_fns()


def get_all_producers() -> list[ChartProducer]:
    """返回全部注册的生产者。"""
    ensure_draw_fns()
    return list(_CHART_REGISTRY.values())


def get_producer(chart_id: str) -> ChartProducer | None:
    """按 chart_id 获取单个生产者。"""
    ensure_draw_fns()
    return _CHART_REGISTRY.get(chart_id)


def get_producers_by_module(module: str) -> list[ChartProducer]:
    """按模块筛选生产者。"""
    ensure_draw_fns()
    return [p for p in _CHART_REGISTRY.values() if p.module == module]


# ═══════════════════════════════════════════════════════════════════════
# chart_id ↔ 旧 fig_id 映射表 (供行为等价验收对照)
# ═══════════════════════════════════════════════════════════════════════

CHART_ID_TO_OLD_FIG_ID: dict[str, str] = {
    "strain_calib_lin": "calib_lin",
    "data_ts_cleaning": "ts_clean",
    # Phase 1-b 新图无旧 fig_id；自映射保持 chart_id 在报告链路中稳定。
    "data_ts_dlambda": "data_ts_dlambda",
    "data_ts_strain": "data_ts_strain",
    "data_ts_temperature": "data_ts_temperature",
    "data_ts_formula": "data_ts_formula",
    "tempa_regression": "tempa_regression",
    "phaseb_diagnostic": "phaseb_diagnostic",
    "compare_ol": "cmp_ol",
    "compare_corr_scatter": "corr_scatter",  # 旧路径汇总名
    "phaseb_hyst": "hyst_loop",              # 旧路径汇总名
}

OLD_FIG_ID_TO_CHART_ID: dict[str, str] = {
    v: k for k, v in CHART_ID_TO_OLD_FIG_ID.items()
}
