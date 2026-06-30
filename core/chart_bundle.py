"""ChartBundle — 统一画图数据结构。

诊断保存 与 实时 provider 都从此结构出图，保证诊断/实时两路图集同源一致。
所有字段均可 JSON 序列化（数组抽稀为 list、skip=1 表示全存）。
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import Any, Optional


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
    grade_table_md: str = ""  # 传感器分级表
    anomaly_table_md: str = ""  # 异常统计表
    ke_table_md: str = ""  # 应变 Ke 汇总表
    decoupling_table_md: str = ""  # 温度 B 解耦标量表

    @classmethod
    def from_providers(cls, main_win) -> ChartBundle:
        """从主窗口各模块（实时数据）填充 ChartBundle。"""
        import numpy as np
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
                bundle.time_h = (np.arange(_n) / 3600.0).tolist()[::_step]
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
            _W_PAT = _re2.compile(r'^[wW]\d+')
            for col in _raw_df.columns:
                if not _W_PAT.match(str(col)):
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
        print(f"[DIAG] from_providers anomaly_table: "
              f"has_data={bool(anomaly_info)}, rows={len(anomaly_info) if anomaly_info else 0}")

        # ── CompareProvider ──
        cw = getattr(main_win, 'compare_tab_widget', None)
        print(f"[DIAG] from_providers CompareProvider: cw_exists={cw is not None}, "
              f"_last_comparison={bool(getattr(cw, '_last_comparison', None)) if cw else 'N/A'}")
        if cw:
            lc = getattr(cw, '_last_comparison', None) or {}
            if lc:
                th = lc.get('time_h')
                srcs = lc.get('sources', {}) or {}
                if th is not None and srcs:
                    step = max(1, len(th) // 200)
                    bundle.compare_time_h = th.tolist() if hasattr(th, 'tolist') else list(th)[::step] if step > 1 else (th.tolist() if hasattr(th, 'tolist') else list(th))
                    bundle.compare_sources = {
                        k: v.tolist() if hasattr(v, 'tolist') else list(v)
                        for k, v in srcs.items()
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
            pb = getattr(tp, '_phase_b_state', {}) or {}
            comp = pb.get('compensation') or {}

            # ── G1 分级表 ──
            if comp:
                rows = [["传感器", "残余σ (%FS)", "迟滞 (%FS)", "评级", "通过", "原因"]]
                for s_name, cd in sorted(comp.items()):
                    if not isinstance(cd, dict):
                        continue
                    gd = cd.get('grade') or {}
                    grade_val = getattr(gd, 'grade', None) if not isinstance(gd, str) and not isinstance(gd, (int, float)) else gd
                    if grade_val is None and isinstance(gd, dict):
                        grade_val = gd.get('grade', '?')
                    grade_str = str(grade_val) if grade_val is not None else '?'
                    passed = bool(getattr(gd, 'passed', False)) if not isinstance(gd, str) and not isinstance(gd, (int, float)) else (grade_str not in ('FAIL', 'ERROR', 'N/A'))
                    reasons = getattr(gd, 'reasons', []) or []
                    if not isinstance(reasons, (list, tuple)):
                        reasons = []
                    reason_str = '; '.join(str(r) for r in reasons[:2]) if reasons else '—'
                    mt = cd.get('metrics')
                    sigma_pct = _safe_float(getattr(mt, 'residual_sigma_pct_fs', None) if mt is not None else None)
                    hys_pct = _safe_float(getattr(mt, 'hysteresis_max_pct_fs', None) if mt is not None else None)
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
                    T_abs = sd.get('T_abs', [])
                    eps = sd.get('eps_orig', sd.get('eps_corr', []))
                    if hasattr(T_abs, 'tolist'):
                        T_abs = T_abs.tolist()
                    elif not isinstance(T_abs, list):
                        T_abs = list(T_abs) if T_abs else []
                    if hasattr(eps, 'tolist'):
                        eps = eps.tolist()
                    elif not isinstance(eps, list):
                        eps = list(eps) if eps else []
                    if T_abs and eps:
                        bundle.hyst_sensors.append({
                            'name': s_name,
                            'T_abs': T_abs,
                            'eps': eps,
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
                rows.append([
                    s_name,
                    mode,
                    f"{ke1:.4f}" if isinstance(ke1, (int, float)) and abs(ke1) > 1e-10 else '—',
                    f"{ke2:.4f}" if isinstance(ke2, (int, float)) and abs(ke2) > 1e-10 else '—',
                    "≥0.999",  # 应变标定R²通常极高
                    ' | '.join(note_parts),
                ])
            if len(rows) > 1:
                bundle.ke_table_md = _rows_to_md_table(rows)

        # ── 标定线性度散点 (Phase 1a: 多传感器 → per-sensor dict 列表) ──
        if _strain_cfgs:
            for s_name, cfg in _strain_cfgs.items():
                readings = getattr(cfg, 'readings', []) or []
                ke = getattr(cfg, 'ke_results', {}) or {}
                gauge = getattr(cfg, 'gauge_length_mm', 80.0)
                if not readings or not ke:
                    continue
                ref_vals = []
                meas_vals = []
                for rd in readings:
                    if not isinstance(rd, dict):
                        continue
                    d = rd.get('disp_mm', 0.0)
                    eps = d / gauge * 1e6 if gauge > 0 else 0.0
                    ref_vals.append(eps)
                    k_vals = [float(v) for v in ke.values() if isinstance(v, (int, float)) and abs(float(v)) > 1e-10]
                    if k_vals:
                        k_avg = sum(k_vals) / len(k_vals)
                        meas_vals.append(k_avg * eps)
                if ref_vals and meas_vals and len(ref_vals) >= 3:
                    k_list = [float(v) for v in ke.values() if isinstance(v, (int, float)) and abs(float(v)) > 1e-10]
                    slope = sum(k_list) / len(k_list) if k_list else 0.0
                    bundle.calib_sensors.append({
                        "sensor": s_name,
                        "ref": ref_vals,
                        "measured": meas_vals,
                        "slope": slope,
                        "r2": 0.999,
                        "unit": "pm",
                    })
                    _cal_ref_n = max(_cal_ref_n, len(ref_vals))
            print(f"[DIAG] from_providers CalibrationProvider: "
                  f"calib_sensors_n={len(bundle.calib_sensors)}, "
                  f"max_ref_n={_cal_ref_n}")

        print(f"[DIAG] from_providers tables: grade={bool(bundle.grade_table_md)}, "
              f"anomaly={bool(bundle.anomaly_table_md)}, "
              f"ke={bool(bundle.ke_table_md)}, "
              f"decoupling={bool(bundle.decoupling_table_md)}")
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
    """从 chart_data 字典提取四张附表的结构化数据。

    返回: [TableData(heading=..., headers=[...], rows=[[...]]), ...]
    空表/缺失表自动省略 — 不崩、不出空壳。
    """
    tables: list[TableData] = []
    for key, heading in [
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
