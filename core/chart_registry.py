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
        tier:       数据源层级 (T1 = chart_data 字段，以后扩展 T2/T3)
        required_keys: 产出此图必须在 chart_data 中存在的键列表 (全部缺 or 全部空则跳过)
        draw_fn:    绘制函数 — 接收关键字参数 → matplotlib Figure
        produces_when: 额外的触发条件谓词 (chart_data → bool)；None = 仅靠 required_keys
        title:      中文标题
        section:    报告所属节
        key_stat_fn: 从 chart_data 产出关键统计简述的可选函数
    """

    chart_id: str
    module: str
    tier: str = "T1"
    required_keys: list[str] = field(default_factory=list)
    draw_fn: Optional[Callable[..., plt.Figure]] = None
    produces_when: Optional[Callable[[dict], bool]] = None
    title: str = ""
    section: str = ""
    key_stat_fn: Optional[Callable[[dict], str | dict[str, str]]] = None


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
# 5 个 T1 生产者注册
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
    title="传感器标定线性度",
    section="传感器标定",
    key_stat_fn=_calib_lin_key_stat,
))

# ── 2. 清洗时序图 ──
def _ts_cleaning_produces_when(cd: dict) -> bool:
    time_h = cd.get('time_h', []) or []
    series = cd.get('series', {}) or {}
    return bool(time_h and series)


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

# ── 3. 多源叠加时程 ──
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

# ── 4. 成对相关散点 ──
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

# ── 5. 迟滞回线 ──
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
    _CHART_REGISTRY["strain_calib_lin"].draw_fn = make_calibration_linearity
    _CHART_REGISTRY["data_ts_cleaning"].draw_fn = make_timeseries
    _CHART_REGISTRY["compare_ol"].draw_fn = make_comparison_overlay
    _CHART_REGISTRY["compare_corr_scatter"].draw_fn = make_correlation_scatter
    _CHART_REGISTRY["phaseb_hyst"].draw_fn = make_hysteresis_loop


# ═══════════════════════════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════════════════════════

def ensure_draw_fns() -> None:
    """保证 draw_fn 已绑定（幂等，可安全多调）。"""
    if _CHART_REGISTRY and _CHART_REGISTRY.get("strain_calib_lin") and _CHART_REGISTRY["strain_calib_lin"].draw_fn is None:
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
    "compare_ol": "cmp_ol",
    "compare_corr_scatter": "corr_scatter",  # 旧路径汇总名
    "phaseb_hyst": "hyst_loop",              # 旧路径汇总名
}

OLD_FIG_ID_TO_CHART_ID: dict[str, str] = {
    v: k for k, v in CHART_ID_TO_OLD_FIG_ID.items()
}
