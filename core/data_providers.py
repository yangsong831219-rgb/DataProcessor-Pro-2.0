"""数据上下文提供器 — 为 AI 诊断/报告组装结构化数据摘要。

每个 provider 负责一个数据源模块，通过 get_summary(budget_chars) 返回紧凑文本。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DataProvider(ABC):
    """数据上下文提供器基类."""

    # 显示名 (在 UI 复选框中使用)
    display_name: str = ""

    # 禁用时的提示文案 (具体启用条件，供 UI 读取)
    disabled_hint: str = ""

    @abstractmethod
    def is_available(self, main_win: Any) -> bool:
        """当前是否有可用数据."""
        ...

    @abstractmethod
    def get_summary(self, main_win: Any, budget_chars: int = 2000) -> str:
        """返回紧凑结构化摘要文本（标量/结论，不含原始序列）。

        Args:
            main_win: DataProcessorWindow 主窗口
            budget_chars: 输出文本上限，超出应优雅截断并标注"(已截断)"

        Returns:
            格式化摘要字符串
        """
        ...


# ═══════════════════════════════════════════════════════════════════════
# ① 数据文件 provider
# ═══════════════════════════════════════════════════════════════════════


class DataFileProvider(DataProvider):
    display_name = "数据文件"
    disabled_hint = "未加载数据 — 加载数据文件后可用"

    @staticmethod
    def _get_timestamp_column(df: Any) -> str | None:
        """启发式查找时间戳列。"""
        import pandas as pd
        # 1. datetime 类型列
        for c in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[c]):
                return str(c)
        # 2. object 列尝试解析
        for c in df.columns:
            if df[c].dtype == object:
                try:
                    s = pd.to_datetime(df[c].astype(str), errors='coerce')
                    if s.notna().sum() > len(df) * 0.5:
                        return str(c)
                except Exception:
                    pass
        # 3. 列名匹配
        for c in df.columns:
            cn = str(c).lower()
            if any(kw in cn for kw in ('时间', 'time', 'timestamp', '(h)')):
                return str(c)
        return None

    @staticmethod
    def _parse_timestamp(df: Any, col: str) -> Any:
        """将列转为 datetime (小时单位→秒等换算)。"""
        import pandas as pd
        s = df[col]
        if pd.api.types.is_datetime64_any_dtype(s):
            return s
        s_str = s.astype(str)
        ts = pd.to_datetime(s_str, errors='coerce', format='mixed')
        # 如果多数值像纯浮点 (如小时 h)，用数值直接转为 timedelta-like 或保留浮点
        if ts.notna().sum() < len(df) * 0.3:
            # fallback: try numeric interpretation (hours → seconds for diff)
            numeric = pd.to_numeric(s_str, errors='coerce')
            if numeric.notna().sum() >= len(df) * 0.5:
                # Treat as hours → timedelta
                # Return numeric (the caller will handle)
                return numeric
        return ts

    def _derive_sampling_interval(self, timestamps: Any) -> tuple[str, bool]:
        """从中位间隔推导采样间隔。

        Returns:
            (label, is_uniform) 如 ("0.01s", True), ("2.3h", False)
        """
        import numpy as np
        is_datetime = hasattr(timestamps, 'dt')
        if is_datetime:
            diffs = timestamps.dropna().sort_values().diff().dropna()
            if len(diffs) < 2:
                return ("未知 (样本不足)", False)
            # diffs are Timedelta
            median_diff = diffs.median()
            total_seconds = median_diff.total_seconds()
            # compute cv to judge uniformity
            vals_s = diffs.dt.total_seconds().to_numpy(dtype=np.float64)
            cv = float(np.std(vals_s) / np.mean(vals_s)) if np.mean(vals_s) > 0 else 0
        else:
            s = timestamps.dropna().sort_values()
            if len(s) < 2:
                return ("未知 (样本不足)", False)
            diffs = s.diff().dropna()
            median_diff = float(diffs.median())
            vals = diffs.to_numpy(dtype=np.float64)
            cv = float(np.std(vals) / np.mean(vals)) if np.mean(vals) > 0 else 0
            total_seconds = median_diff * 3600  # assume hours

        uniform = cv < 0.1
        if total_seconds >= 3600:
            label = f"{total_seconds / 3600:.2f}h"
        elif total_seconds >= 60:
            label = f"{total_seconds / 60:.1f}min"
        elif total_seconds >= 1:
            label = f"{total_seconds:.1f}s"
        else:
            label = f"{total_seconds * 1000:.0f}ms"
        return (label, uniform)

    def is_available(self, main_win: Any) -> bool:
        data = getattr(main_win, 'current_data', None)
        return data is not None and not data.empty

    def get_summary(self, main_win: Any, budget_chars: int = 2000) -> str:
        data = getattr(main_win, 'current_data', None)
        assert data is not None and not data.empty
        import pandas as pd

        lines: list[str] = []
        lines.append("【数据文件】")

        # ── 模板 / 类型 ──
        template = getattr(main_win, 'current_template', None)
        tpl_name = str(getattr(template, 'name', '') or '无模板')
        lines.append(f"模板: {tpl_name}")

        # 类型判定：使用 main_win 的公开 getter (= 消除重复实现)
        is_fiber = False
        if hasattr(main_win, 'is_fiber_data'):
            is_fiber = bool(main_win.is_fiber_data())
        dtype_label = "光纤光栅传感器数据" if is_fiber else "通用数据 (TXT/CSV)"
        lines.append(f"类型: {dtype_label}")

        # ── 规模 ──
        rows, cols = data.shape
        lines.append(f"规模: {rows} 行 x {cols} 列")

        # ── 时间范围 + 采样间隔 ──
        ts_col = self._get_timestamp_column(data)
        if ts_col is not None:
            ts_raw = self._parse_timestamp(data, ts_col)
            if isinstance(ts_raw, pd.Series):
                ts_clean = ts_raw.dropna().sort_values()
                if len(ts_clean) >= 2:
                    t_min = ts_clean.iloc[0]
                    t_max = ts_clean.iloc[-1]
                    # format
                    if hasattr(t_min, 'strftime'):
                        t_min_s = t_min.strftime('%Y-%m-%d %H:%M:%S')
                        t_max_s = t_max.strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        t_min_s = f"{float(t_min):.4f}"
                        t_max_s = f"{float(t_max):.4f}"
                    interval, uniform = self._derive_sampling_interval(ts_raw)
                    uniform_tag = "" if uniform else ", 非均匀"
                    lines.append(f"时间列: {ts_col}")
                    lines.append(f"时间范围: {t_min_s} ~ {t_max_s}")
                    lines.append(f"采样间隔: ~{interval}{uniform_tag}")
            else:
                lines.append(f"时间列: {ts_col} (解析失败)")
        else:
            lines.append("时间列: 无 (未检测到时间戳列)")

        # ── 列→传感器映射 ──
        # 多源注解：template.column_notes > 暗号行 > 标定页 annotation
        col_notes: dict[str, str] = {}
        if template and hasattr(template, 'column_notes'):
            cn = getattr(template, 'column_notes', {}) or {}
            col_notes = {str(k): str(v) for k, v in cn.items()}
        # _get_calibration_annotation 兜底：读标定页 _annotation
        if not col_notes:
            cal_ann = _get_calibration_annotation(main_win)
            if cal_ann:
                col_notes = cal_ann

        # 暗号标注列
        annotated = getattr(main_win, 'get_annotated_columns', None)
        if callable(annotated):
            try:
                ann_data = annotated()
                if ann_data and isinstance(ann_data, tuple):
                    # get_annotated_columns returns (time_col, data_cols_dict, row_idx)
                    ann_dict = ann_data[1] if len(ann_data) > 1 else {}
                    if isinstance(ann_dict, dict) and ann_dict:
                        for idx_str, lbl in ann_dict.items():
                            idx = int(idx_str) if isinstance(idx_str, (int, str)) and str(idx_str).isdigit() else None
                            if idx is not None and idx < len(data.columns):
                                col_notes[str(data.columns[idx])] = str(lbl)
            except Exception:
                pass

        col_segs: list[str] = []
        all_unknown = True
        for c in data.columns:
            cn = str(c)
            note = col_notes.get(cn, '')
            if note:
                all_unknown = False
                col_segs.append(f"{cn}({note})")
            else:
                col_segs.append(f"{cn}")
        if all_unknown:
            lines.append("列→传感器: (无标定注解，列身份未知)")
        else:
            if len(col_segs) > 30:
                col_segs = col_segs[:30]
                col_segs.append("... (共 {} 列)".format(len(data.columns)))
            lines.append(f"列→传感器: {', '.join(col_segs)}")

        # ── 数值列统计摘要 ──
        numeric_cols = data.select_dtypes(include=['number']).columns.tolist()
        if numeric_cols:
            lines.append("数值列统计:")
            shown = 0
            for c in numeric_cols:
                col_data = data[c].dropna()
                if len(col_data) > 0:
                    lines.append(
                        f"  {c}: 有效={len(col_data)}, 缺失={int(data[c].isna().sum())}, "
                        f"均值={float(col_data.mean()):.4f}, σ={float(col_data.std()):.4f}, "
                        f"范围=[{float(col_data.min()):.4f}, {float(col_data.max()):.4f}]"
                    )
                    shown += 1
                    if shown >= 20:
                        lines.append(f"  ... (共 {len(numeric_cols)} 列, 仅显示前 20)")
                        break

        # ── 拼接 + 截断 ──
        text = '\n'.join(lines)
        if len(text) > budget_chars:
            text = text[:budget_chars - 20] + "\n(已截断)"
        return text


# ═══════════════════════════════════════════════════════════════════════
# ⑤ 传感器标定 provider
# ═══════════════════════════════════════════════════════════════════════


class CalibrationProvider(DataProvider):
    display_name = "传感器标定"
    disabled_hint = "未标定 — 运行传感器标定后可用"

    def is_available(self, main_win: Any) -> bool:
        tp = self._get_temp_page(main_win)
        if tp is None:
            return False
        pa = getattr(tp, '_phase_a_state', {}) or {}
        pb = getattr(tp, '_phase_b_state', {}) or {}
        has_a = bool(pa.get('seff_result'))
        has_b = bool(pb.get('decoupling_results') or pb.get('compensation'))
        return has_a or has_b

    def _get_temp_page(self, main_win: Any) -> Any:
        """获取温度标定子页。"""
        ct = getattr(main_win, 'calibration_tab_widget', None)
        if ct is None:
            return None
        return getattr(ct, 'temperature_page', None)

    def get_summary(self, main_win: Any, budget_chars: int = 2000) -> str:
        tp = self._get_temp_page(main_win)
        if tp is None:
            return "【传感器标定】未运行 (无标定页面)"

        pa = getattr(tp, '_phase_a_state', {}) or {}
        pb = getattr(tp, '_phase_b_state', {}) or {}
        seff = pa.get('seff_result', {}) or {}
        seff_data = seff.get('S_eff', {}) if isinstance(seff, dict) else {}
        ke_table = _normalize_coeffs(pb.get('ke_table', {}))
        decoupling = pb.get('decoupling_results', {}) or {}
        compensation = pb.get('compensation', {}) or {}

        has_a = bool(seff_data)
        has_b = bool(decoupling or compensation)

        lines: list[str] = []
        lines.append("【传感器标定】（传感器标识: A1=FBG_A1, A2=FBG_A2, ...）")
        if not has_a and not has_b:
            lines.append("  (未运行)")
            return '\n'.join(lines)

        # 收集传感器 ID — 以 ke_table 和 decoupling 为准，
        # s_eff 里的纯波长通道 (w1-w12) 不是传感器，过滤掉
        sensor_ids: set[str] = set()
        sensor_ids.update(ke_table.keys())
        sensor_ids.update(d for d in decoupling.keys() if d in ke_table or d not in seff_data)
        # 如果 decoupling 中有不在 ke_table 的（单栅），也加
        for dk in decoupling:
            if dk not in sensor_ids:
                sensor_ids.add(dk)

        # 从 s_eff 中只取已确认为传感器的 KT 值
        # s_eff key 可能是波长通道名 (w1) 或传感器名 (A1)
        # 按传感器名前缀匹配
        seff_for_sensor: dict[str, float] = {}
        for s_id in sensor_ids:
            if s_id in seff_data:
                seff_for_sensor[s_id] = float(seff_data[s_id])

        if not sensor_ids:
            lines.append("  (无传感器数据)")
            return '\n'.join(lines)

        for s_name in sorted(sensor_ids):
            parts = [f"  {s_name}:"]
            is_single = False

            # Phase B 解耦 → single_grating 判定
            dec_d = decoupling.get(s_name, {})
            if isinstance(dec_d, dict):
                if dec_d.get('single_grating') is True:
                    is_single = True
            # 降级（旧 profile 无 single_grating 字段）：不在 ke_table → 单栅
            if not is_single and not dec_d.get('single_grating', True):
                pass  # explicitly False → dual
            elif not is_single and s_name not in ke_table and dec_d:
                is_single = True

            # KT
            if s_name in seff_for_sensor:
                kt = seff_for_sensor[s_name]
                if isinstance(kt, dict):
                    kt = kt.get('slope', kt) if isinstance(kt, dict) else kt
                parts.append(f"KT={float(kt):.4f}")

            if is_single:
                parts.append("单栅")
                comp_d = compensation.get(s_name, {})
                if isinstance(comp_d, dict):
                    grade = comp_d.get('grade')
                    g = str(getattr(grade, 'grade', grade)) if grade else '?'
                    parts.append(f"评级=N/A (单栅不解耦)")
                else:
                    parts.append("评级=N/A (单栅不解耦)")
            else:
                # 双栅：Ke — 优先从应变 section 取精值，ke_table 为回退
                strain_ke = _get_strain_ke(main_win, s_name) if main_win else None
                if strain_ke:
                    ke1_s = f"{float(strain_ke['Ke1']):.4f}"
                    ke2_s = f"{float(strain_ke['Ke2']):.4f}"
                else:
                    ke = ke_table.get(s_name, {}) if ke_table else {}
                    if isinstance(ke, dict):
                        ke1_s = str(ke.get('Ke1', '?'))
                        ke2_s = str(ke.get('Ke2', '?'))
                    else:
                        ke1_s, ke2_s = '?', '?'
                parts.append(f"Ke1/Ke2={ke1_s}/{ke2_s}")

                # 解耦
                if isinstance(dec_d, dict):
                    e_std = dec_d.get('e_std', dec_d.get('e_range'))
                    if e_std is not None:
                        parts.append(f"ε_std={float(e_std):.2f}με")
                    e_mean = dec_d.get('e_mean')
                    if e_mean is not None:
                        parts.append(f"ε_mean={float(e_mean):.2f}με")

                # 补偿 + 评级
                comp_d = compensation.get(s_name, {})
                if isinstance(comp_d, dict):
                    metrics = comp_d.get('metrics')
                    if metrics is not None and not isinstance(metrics, (int, float)):
                        fs = float(getattr(metrics, 'fs', 1000) or 1000)
                        parts.append(f"FS={fs:.0f}με")
                        # 补偿后 σ (%FS, LOOCV 口径)
                        sigma_pct = getattr(metrics, 'residual_sigma_pct_fs', None)
                        if sigma_pct is not None:
                            parts.append(f"补偿后σ={float(sigma_pct):.3f}%FS(LOOCV)")
                        # 迟滞
                        hys_pct = getattr(metrics, 'hysteresis_max_pct_fs', None)
                        if hys_pct is not None and not (hys_pct != hys_pct):  # non-NaN
                            parts.append(f"迟滞={float(hys_pct):.3f}%FS")
                        # 重复性
                        rep_pct = getattr(metrics, 'repeatability_pct_fs', None)
                        if rep_pct is not None:
                            parts.append(f"重复性={float(rep_pct):.3f}%FS")
                        # 旗标
                        lc = getattr(metrics, 'low_confidence', False)
                        if lc:
                            parts.append("(低置信度)")
                        cf = getattr(metrics, 'comp_form', '')
                        if cf:
                            po = getattr(metrics, 'poly_order', 0)
                            parts.append(f"{cf}" + (f"_{po}" if cf == 'poly' else ''))

                    grade = comp_d.get('grade')
                    if grade is not None and not isinstance(grade, (int, float, str)):
                        g = str(getattr(grade, 'grade', '?'))
                        passed = bool(getattr(grade, 'passed', False))
                        reasons = getattr(grade, 'reasons', []) or []
                        if g in ('FAIL', 'ERROR'):
                            reason_str = '; '.join(str(r) for r in reasons[:2])
                            parts.append(f"评级={g}({reason_str})")
                        else:
                            parts.append(f"评级={g}" if passed else f"评级={g}(未通过)")
                    elif isinstance(grade, str):
                        parts.append(f"评级={grade}")

            lines.append(' | '.join(parts))

        text = '\n'.join(lines)
        if len(text) > budget_chars:
            text = text[:budget_chars - 20] + "\n(已截断)"
        return text


# ═══════════════════════════════════════════════════════════════════════
# 纯特征函数（数据分析 + 清洗 provider 共用，④ 以后也复用）
# ═══════════════════════════════════════════════════════════════════════


def compute_time_series_features(series: Any) -> dict:
    """对一维数值序列计算时域特征（纯函数，无 UI 依赖）。

    Returns:
        dict with keys: n, mean, std, min, max, range, drift_slope, stability
    """
    import numpy as np
    arr = np.asarray(series, dtype=np.float64)
    mask = np.isfinite(arr)
    arr_clean = arr[mask]
    n = len(arr_clean)
    if n < 2:
        return {'n': n, 'mean': float(np.nan), 'std': float(np.nan),
                'min': float(np.nan), 'max': float(np.nan),
                'range': float(np.nan), 'drift_slope': 0.0, 'stability': 'N/A'}

    mean_val = float(np.mean(arr_clean))
    std_val = float(np.std(arr_clean))
    min_val = float(np.min(arr_clean))
    max_val = float(np.max(arr_clean))
    range_val = max_val - min_val

    # 漂移斜率（线性回归，物理量/样本索引）
    x = np.arange(len(arr_clean), dtype=np.float64)
    slope = float(np.polyfit(x, arr_clean, 1)[0]) if n > 2 else 0.0
    # 按 1000 样本归一化（便于跨序列比较）
    drift_per_1k = slope * 1000.0

    # 稳定性：CV(标准差/均值) 判定
    cv = abs(std_val / mean_val) if abs(mean_val) > 1e-9 else float('inf')
    if cv < 0.01:
        stability = '极稳定(CV<1%)'
    elif cv < 0.05:
        stability = '稳定(CV<5%)'
    elif cv < 0.2:
        stability = '一般波动(CV<20%)'
    else:
        stability = '显著波动(CV≥20%)'

    return {
        'n': n, 'mean': mean_val, 'std': std_val,
        'min': min_val, 'max': max_val, 'range': range_val,
        'drift_slope_per_1k': drift_per_1k,
        'stability': stability,
    }


# ═══════════════════════════════════════════════════════════════════════
# ② 数据清洗 provider
# ═══════════════════════════════════════════════════════════════════════


class CleaningProvider(DataProvider):
    display_name = "数据清洗"
    disabled_hint = "未执行 — 运行数据清洗后可用"

    def is_available(self, main_win: Any) -> bool:
        ct = getattr(main_win, 'cleaning_tab_widget', None)
        if ct is None:
            return False
        return bool(getattr(ct, '_cleaning_has_run', False))

    def _get_cleaning_config(self, ct: Any) -> dict | None:
        try:
            if hasattr(ct, 'get_config'):
                return ct.get_config()
        except Exception:
            pass
        return None

    def get_summary(self, main_win: Any, budget_chars: int = 2000) -> str:
        ct = getattr(main_win, 'cleaning_tab_widget', None)
        if ct is None or not getattr(ct, '_cleaning_has_run', False):
            return "【数据清洗】未执行/无最新结果"

        info = getattr(ct, '_anomaly_info', {}) or {}
        config = self._get_cleaning_config(ct)

        lines = ["【数据清洗】"]

        if not info:
            # 跑了但 0 异常
            threshold = ''
            if config:
                if config.get('adjacent_enabled'):
                    threshold = f"，相邻差值阈值={config.get('diff_threshold', '?')}"
            lines.append(f"已清洗，未发现异常"
                        f"{threshold}")

        else:
            total = sum(v['count'] for v in info.values())
            lines.append(f"异常总数: {total}")
            cols_shown = 0
            for col, d in sorted(info.items(), key=lambda x: -x[1]['count']):
                # 紧凑：只报列名和计数，不给位置列表
                sample_idx = str(d['indices'][:2]) if d.get('total_indices', 0) <= 20 else str(d['indices'][:2]) + f"..."
                lines.append(f"  {col}: {d['count']} 异常 (例: {sample_idx})")
                cols_shown += 1
                if cols_shown >= 15:  # 最多 15 列
                    remaining = len(info) - cols_shown
                    if remaining > 0:
                        lines.append(f"  ... 还有 {remaining} 列有异常")
                    break

        # 清洗策略
        if config:
            method = config.get('fill_method', 'linear')
            rules = []
            if config.get('adjacent_enabled'):
                rules.append(f"相邻差值检测(阈值={config.get('diff_threshold', '?')})")
            if config.get('nan_enabled'):
                rules.append("缺失值检测")
            if rules:
                lines.append(f"策略: {method} | 规则: {', '.join(rules)}")

        text = '\n'.join(lines)
        if len(text) > budget_chars:
            text = text[:budget_chars - 20] + "\n(已截断)"
        return text


# ═══════════════════════════════════════════════════════════════════════
# ③ 数据分析 provider
# ═══════════════════════════════════════════════════════════════════════


class AnalysisProvider(DataProvider):
    display_name = "数据分析"
    disabled_hint = "无结果 — 运行数据分析后可用"

    def is_available(self, main_win: Any) -> bool:
        sr = getattr(main_win, 'sensor_results', None)
        return bool(sr)

    def get_summary(self, main_win: Any, budget_chars: int = 2000) -> str:
        sr = getattr(main_win, 'sensor_results', {})
        if not sr:
            return "【数据分析】无传感器计算结果"

        lines = ["【数据分析】"]
        for sensor_id, values in sorted(sr.items()):
            clean_vals = [v for v in values if v is not None]
            if not clean_vals:
                lines.append(f"  {sensor_id}: (无有效值)")
                continue
            feat = compute_time_series_features(clean_vals)
            lines.append(
                f"  {sensor_id}: n={feat['n']} | "
                f"均值={feat['mean']:.3f} | σ={feat['std']:.3f} | "
                f"范围=[{feat['min']:.3f}, {feat['max']:.3f}] ({feat['range']:.3f}) | "
                f"漂移≈{feat['drift_slope_per_1k']:.4f}με/千样本 | "
                f"{feat['stability']}"
            )
            if len('\n'.join(lines)) > budget_chars:
                current = '\n'.join(lines)
                return current[:budget_chars - 20] + "\n(已截断)"

        return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════════════
# ④ 多源对比 provider
# ═══════════════════════════════════════════════════════════════════════


class CompareProvider(DataProvider):
    display_name = "多源对比"
    disabled_hint = "未执行 — 运行多源对比后可用"

    def is_available(self, main_win: Any) -> bool:
        cw = getattr(main_win, 'compare_tab_widget', None)
        if cw is None:
            return False
        lc = getattr(cw, '_last_comparison', None)
        return bool(lc)

    def get_summary(self, main_win: Any, budget_chars: int = 2000) -> str:
        cw = getattr(main_win, 'compare_tab_widget', None)
        lc = getattr(cw, '_last_comparison', {}) if cw else {}
        if not lc:
            return "【多源对比】未执行/无最新结果"

        lines = ["【多源对比】"]
        lines.append(f"对齐方法: {lc.get('align_method', '?')} | 基准: {lc.get('baseline_method', '?')}")
        lines.append(f"重叠样本: {lc.get('n_points', 0)} 点")

        dev_feats = lc.get('device_features', {})
        if dev_feats:
            for dev, feat in dev_feats.items():
                lines.append(
                    f"  {dev}: mean={feat['mean']:.3f} σ={feat['std']:.3f} "
                    f"漂移≈{feat['drift_slope_per_1k']:.3f}/千样本 {feat['stability']}"
                )

        pairs = lc.get('pairs', [])
        if pairs:
            lines.append("成对对比:")
            for p in pairs:
                corr_tag = ("高度一致" if p['corr'] > 0.95 else
                           "一致" if p['corr'] > 0.8 else
                           "偏差异常" if p['corr'] < 0.5 else "一般相关")
                lines.append(
                    f"  {p['device_a']}↔{p['device_b']}: "
                    f"corr={p['corr']:.4f}({corr_tag}) | "
                    f"MAE={p['mae']:.4f} | RMSE={p['rmse']:.4f} | "
                    f"max_dev={p['max_error']:.4f}"
                )

        text = '\n'.join(lines)
        if len(text) > budget_chars:
            text = text[:budget_chars - 20] + "\n(已截断)"
        return text


# ═══════════════════════════════════════════════════════════════════════
# Provider 注册表
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# ⑥ 外部数据文件 provider
# ═══════════════════════════════════════════════════════════════════════


class ExternalDataProvider(DataProvider):
    """外部数据源 — 用户提交的独立文件 (csv/xlsx/txt/json/md)。

    不依赖主窗口内任何模块，可单独使用。
    状态存储在 AiDiagnosisWidget._external_files。
    """

    display_name = "外部数据文件"
    disabled_hint = '未加载 — 点击「提交外部数据文件」加载'

    @staticmethod
    def _get_external_files(main_win: Any) -> list[str] | None:
        """从 AiDiagnosisWidget 取已加载的外部文件路径列表。"""
        ai_widget = getattr(main_win, 'ai_diagnosis_widget', None)
        if ai_widget is None:
            return None
        return getattr(ai_widget, '_external_files', None)

    def is_available(self, main_win: Any) -> bool:
        files = self._get_external_files(main_win)
        return bool(files)

    def get_summary(self, main_win: Any, budget_chars: int = 2000) -> str:
        files = self._get_external_files(main_win)
        if not files:
            return "【外部数据文件】未加载"

        import os as _os
        import pandas as pd

        lines: list[str] = ["【外部数据文件】"]
        remaining_budget = budget_chars - len(lines[0]) - 20

        for fpath in files:
            if remaining_budget < 100:  # 预算不足，标注截断
                lines.append("(剩余文件因预算截断省略)")
                break

            fname = _os.path.basename(fpath)
            ext = _os.path.splitext(fname)[1].lower()
            entry_lines: list[str] = []
            entry_start = f"--- {fname} ---"
            entry_lines.append(entry_start)

            # CSV / Excel: pandas 读
            if ext in ('.csv',):
                try:
                    df = pd.read_csv(fpath, nrows=1000)  # 最多读 1000 行
                    entry_lines.append(f"形状: {df.shape[0]} 行 × {df.shape[1]} 列")
                    entry_lines.append(f"列名: {', '.join(str(c) for c in df.columns[:30])}")
                    # describe 数值统计
                    num_cols = df.select_dtypes(include=['number']).columns.tolist()
                    if num_cols:
                        desc = df[num_cols].describe()
                        for c in num_cols[:8]:
                            row = desc[c]
                            entry_lines.append(
                                f"  {c}: mean={row['mean']:.4f} std={row['std']:.4f} "
                                f"min={row['min']:.4f} max={row['max']:.4f}"
                            )
                        if len(num_cols) > 8:
                            entry_lines.append(f"  ... 共 {len(num_cols)} 数值列")
                    # head
                    entry_lines.append("前5行样例:")
                    entry_lines.append(df.head(5).to_string(max_colwidth=40))
                except Exception as e:
                    entry_lines.append(f"读取失败: {str(e)[:80]}")

            elif ext in ('.xlsx', '.xls'):
                try:
                    df = pd.read_excel(fpath, nrows=1000)
                    entry_lines.append(f"形状: {df.shape[0]} 行 × {df.shape[1]} 列")
                    entry_lines.append(f"列名: {', '.join(str(c) for c in df.columns[:30])}")
                    num_cols = df.select_dtypes(include=['number']).columns.tolist()
                    if num_cols:
                        desc = df[num_cols].describe()
                        for c in num_cols[:8]:
                            row = desc[c]
                            entry_lines.append(
                                f"  {c}: mean={row['mean']:.4f} std={row['std']:.4f} "
                                f"min={row['min']:.4f} max={row['max']:.4f}"
                            )
                    entry_lines.append("前5行样例:")
                    entry_lines.append(df.head(5).to_string(max_colwidth=40))
                except Exception as e:
                    entry_lines.append(f"读取失败: {str(e)[:80]}")

            elif ext in ('.txt', '.json', '.md'):
                try:
                    raw = _read_text_file(fpath, budget_chars=3000)
                    entry_lines.append(f"类型: 文本 ({ext})")
                    entry_lines.append(f"内容: {raw}")
                except Exception as e:
                    entry_lines.append(f"读取失败: {str(e)[:80]}")

            else:
                entry_lines.append(f"不支持的文件类型: {ext}")

            # 拼接 + 预算检查
            entry_text = '\n'.join(entry_lines)
            if len(entry_text) > remaining_budget:
                cut_at = max(100, remaining_budget - 20)
                entry_text = entry_text[:cut_at] + "\n(已截断)"
            lines.append(entry_text)
            remaining_budget -= len(entry_text) + 1

        text = '\n'.join(lines)
        if len(text) > budget_chars:
            text = text[:budget_chars - 20] + "\n(已截断)"
        return text


def _read_text_file(path: str, budget_chars: int = 3000) -> str:
    """读文本文件，按预算截断。"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read(budget_chars + 500)
        if len(content) > budget_chars:
            remaining = len(content) - budget_chars
            # 尝试读完以获取总长
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    full = f.read(100000)
                total = len(full)
                content = full[:budget_chars] + f"\n(已截断，共 {total} 字符)"
            except Exception:
                content = content[:budget_chars] + "\n(已截断)"
        return content
    except UnicodeDecodeError:
        try:
            with open(path, 'r', encoding='gbk') as f:
                content = f.read(budget_chars + 500)
            if len(content) > budget_chars:
                content = content[:budget_chars] + "\n(已截断)"
            return content
        except Exception as e:
            return f"无法读取: {str(e)[:60]}"


def get_all_providers() -> list[DataProvider]:
    """返回所有 provider 实例（注册顺序即 UI 顺序）。"""
    return [
        DataFileProvider(),
        CleaningProvider(),
        AnalysisProvider(),
        CompareProvider(),
        CalibrationProvider(),
        ExternalDataProvider(),
    ]


# ═══════════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════════


def _normalize_coeffs(coeffs: dict) -> dict[str, dict[str, float]]:
    """统一 Ke 系数为 dict 形状: {sensor: {"Ke1": float, "Ke2": float}}"""
    if not coeffs:
        return {}
    out = {}
    for s, v in coeffs.items():
        if isinstance(v, (list, tuple)) and len(v) == 2:
            out[s] = {"Ke1": float(v[0]), "Ke2": float(v[1])}
        elif isinstance(v, dict):
            out[s] = {
                "Ke1": float(v.get("Ke1", float("nan"))),
                "Ke2": float(v.get("Ke2", float("nan"))),
            }
    return out


def _get_calibration_annotation(main_win: Any) -> dict[str, str] | None:
    """从温度标定页或应变标定页读取列注解（兜底来源）。"""
    ct = getattr(main_win, 'calibration_tab_widget', None)
    if ct is None:
        return None
    tp = getattr(ct, 'temperature_page', None)
    if tp and hasattr(tp, '_annotation'):
        ann = tp._annotation  # type: ignore[union-attr]
        if ann:
            return {str(k): str(v) for k, v in ann.items()}
    return None


def _get_strain_ke(main_win: Any, sensor: str) -> dict[str, float] | None:
    """从应变标定页读取更精密的 Ke 值（profile 中 strain section ke_results）。"""
    ct = getattr(main_win, 'calibration_tab_widget', None)
    if ct is None:
        return None
    sp = getattr(ct, 'strain_page', None)
    if sp and hasattr(sp, '_phase_b_state'):
        sb = sp._phase_b_state or {}  # type: ignore[union-attr]
        strain_data = sb.get(sensor, {})
        if isinstance(strain_data, dict):
            ke = strain_data.get('ke_results', {})
            if ke:
                return {"Ke1": float(ke.get("Ke1", 0)), "Ke2": float(ke.get("Ke2", 0))}
    # Fallback: check ProfileConfig on strain page
    if sp and hasattr(sp, '_strain_configs'):
        sc = sp._strain_configs or {}  # type: ignore[union-attr]
        for _, cfg in sc.items():
            ke = getattr(cfg, 'ke_results', None)
            if ke and getattr(cfg, 'sensor_name', '') == sensor:
                return {"Ke1": float(getattr(ke, 'Ke1', 0)), "Ke2": float(getattr(ke, 'Ke2', 0))}
    return None
