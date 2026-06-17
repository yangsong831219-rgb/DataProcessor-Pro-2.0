"""传感器标定 Tab — 温度标定 + 应变标定 双子页 (迭代 2: UI 重构 + 暗号复用)

CalibrationTabWidget(QWidget)
  └── QTabWidget
       ├── 温度标定: 暗号解析→两段式流程(解析温度系数→输入应变系数→分析)→ChartPanel
       └── 应变标定: 对话框化参数/读数→ChartPanel 结果区

设计原则:
- 温度标定独立加载文件，不写入主窗口 current_data
- 所有计算放后台 QThread，UI 不卡顿
- 复用暗号标注格式识别时间列/数据列，按前缀分组传感器
- 跨页访问主窗口数据一律 self.window()
- 标定系数注入当前 sensor_system（会话级），不写回全局 Sensor 定义
"""

from __future__ import annotations

import os, re, math
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QPushButton, QLabel, QComboBox, QSpinBox, QDoubleSpinBox,
    QLineEdit, QTextEdit, QFileDialog, QMessageBox, QTableWidget,
    QTableWidgetItem, QTabWidget, QDialog, QScrollArea, QHeaderView,
    QCheckBox, QSplitter, QFrame, QApplication,
    QInputDialog, QListWidget,
)
from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal
from PyQt6.QtGui import QColor
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from ui.components import (
    create_button, create_section_header, create_form_group,
    create_labeled_input, create_labeled_spinbox, create_labeled_int_spinbox,
    create_labeled_combo, create_file_picker, create_info_label, create_separator,
)
from ui.widgets.chart_panel import ChartPanel

from py.calibration.temperature_calibration import (
    load_continuous, assign_setpoints, regress_sensitivity,
    decouple, compare_given_vs_measured, run_temperature_calibration,
)
from py.calibration.strain_calibration import (
    compute_theoretical_strain,
    StrainCalibrationConfig, calibrate_strain,
)
from py.calibration.export_utils import (
    export_temperature_excel, export_strain_excel,
)


# ═══════════════════════════════════════════════════════════════════════
# 暗号行解析 (与 main.py get_annotated_columns 算法同源，作用于标定独立 DF)
# ═══════════════════════════════════════════════════════════════════════

def _parse_annotation_row(df: pd.DataFrame):
    """扫描 DataFrame 查找暗号行，返回 (signal_row_idx, data_cols, time_col_idx)。

    data_cols: {列索引: 引号内自定义名}，如 {2: 'A1-W1', 3: 'A1-W2'}.
    暗号格式与 main.py 完全一致：'时间戳' / '引号包裹的列名'。
    """
    if df is None or df.empty:
        return None, {}, None

    signal_row_idx = None
    for idx in range(min(len(df), 20)):  # 只查前 20 行
        for val in df.iloc[idx]:
            s = str(val).strip().strip("'\"'\"'\"")
            if '时间戳' in s:
                signal_row_idx = idx
                break
        if signal_row_idx is not None:
            break

    if signal_row_idx is None:
        return None, {}, None

    signal_row = df.iloc[signal_row_idx]
    QUOTED_RE = re.compile(r"^[\'\"'\"'\"](.*?)[\'\"'\"'\"]$")
    time_col_idx = None
    data_cols = {}

    for j in range(len(df.columns)):
        col_name = df.columns[j]
        val = str(signal_row[col_name]).strip()
        cleaned = val.strip("'\"'\"'\"").strip()

        if '时间戳' in cleaned:
            time_col_idx = j
        elif QUOTED_RE.match(val):
            m = QUOTED_RE.match(val)
            custom_name = m.group(1).strip()
            data_cols[j] = custom_name

    return signal_row_idx, data_cols, time_col_idx


def _group_annotations_by_prefix(data_cols: dict, df=None) -> dict:
    """按暗号前缀分组传感器。

    'A1-W1', 'A1-W2' → 传感器 'A1' (双栅，含 W1/W2)
    'B1-W1'           → 传感器 'B1' (单栅)

    前缀 = 第一个 '-' 之前的部分。无 '-' 则整个名称作前缀。

    如果传入 df，则同时解析 col_idx → col_name 存在 grating dict 中，
    后续可直接用 g["col_name"] 访问 DataFrame 列，不再需要整数索引。
    """
    groups: dict[str, list[dict]] = {}
    col_list = list(df.columns) if df is not None else []
    for col_idx, name in data_cols.items():
        prefix = name.split('-')[0] if '-' in name else name
        entry: dict = {"name": name}
        if df is not None and 0 <= col_idx < len(col_list):
            entry["col_name"] = col_list[col_idx]
        groups.setdefault(prefix, []).append(entry)
    return groups


def is_valid_annotation(s: str) -> bool:
    """暗号合法性判定。

    合法暗号: 非空 + 恰好一个 '-' + 前后均仅含字母/数字/下划线
             + 不包含模板占位字眼。

    模板字眼: 类型, 位置, 占位, template, placeholder, 用户备注名
    """
    if not s or not isinstance(s, str):
        return False
    s = s.strip().strip("'\"'\"'\"")
    if not s:
        return False
    # 模板占位词排除
    template_words = ["类型", "位置", "占位", "template", "placeholder", "用户备注名"]
    for tw in template_words:
        if tw in s:
            return False
    # 格式: prefix-suffix, 前后均 [A-Za-z0-9_]+
    import re as _re2
    return bool(_re2.match(r'^[A-Za-z0-9_]+-[A-Za-z0-9_]+$', s))


def _count_annotations(annotation: dict | None, wavelength_cols: list[str]) -> tuple[int, int]:
    """统一计数: 从 annotation dict 中统计合法/占位暗号数。

    遍历 annotation 的值 (已去引号), 用 is_valid_annotation() 判定。
    只计入名称含 '-' 的条目 (排除 'Timestamps'/纯列名)。

    Returns: (legal_count, placeholder_count)
    """
    if not annotation:
        return 0, 0
    legal, placeholder = 0, 0
    for val in annotation.values():
        s = str(val).strip().strip("'\"'\"'\"")
        if not s or '-' not in s:
            continue  # 跳过时间戳等非数据列
        if is_valid_annotation(s):
            legal += 1
        else:
            placeholder += 1
    return legal, placeholder


def _count_annotations_from_data_cols(data_cols: dict) -> tuple[int, int]:
    """从 data_cols (暗号行解析结果) 统计合法/占位数"""
    legal, placeholder = 0, 0
    for name in data_cols.values():
        s = str(name).strip().strip("'\"'\"'\"")
        if not s or '-' not in s:
            continue
        if is_valid_annotation(s):
            legal += 1
        else:
            placeholder += 1
    return legal, placeholder


def _can_parse_now(annotation_groups: dict, legal_count: int) -> bool:
    """至少有一个传感器前缀下存在 ≥2 个合法暗号 (形成双栅)"""
    if legal_count < 2:
        return False
    for pfx, gratings in annotation_groups.items():
        valid_in_group = sum(1 for g in gratings if is_valid_annotation(g.get("name", "")))
        if valid_in_group >= 2:
            return True
    return False


def _build_annotation_info_parts(
    groups, legal_cnt, placeholder_cnt, fmt, skipped, num_cols, wave_cols,
) -> list[str]:
    """构建信息条文案片段"""
    parts = []
    if fmt and fmt.startswith("hyperion_peaks"):
        parts.append("已识别 Hyperion Peaks")
    if skipped:
        parts.append(f"跳过 {skipped} 行元数据")
    cnt_n = len(num_cols) if num_cols else 0
    cnt_w = len(wave_cols) if wave_cols else 0
    if cnt_n:
        parts.append(f"数值列 {cnt_n} 个")
    if cnt_w:
        parts.append(f"波长列 {cnt_w} 个")
    parts.append(f"暗号合法 {legal_cnt} / 占位待填 {placeholder_cnt}")
    if groups:
        group_parts = []
        group_items = list(groups.items())
        shown = group_items[:3]
        for pfx, gratings in shown:
            gtype = "双栅" if len(gratings) >= 2 else "单栅"
            names = ", ".join(g["name"] for g in gratings)
            group_parts.append(f"{pfx}({gtype}: {names})")
        if len(group_items) > 3:
            group_parts.append(f"... 等 {len(group_items)} 组")
        parts.append(f"分组: {' | '.join(group_parts)}")
    return parts


def _detect_numeric_columns(df: pd.DataFrame) -> list[str]:
    """稳健检测数值列：is_numeric_dtype 优先 + pd.to_numeric 兜底。

    策略:
      1. 若列的 is_numeric_dtype 为 True → 直接采纳 (low_memory=False 确保类型正确)
      2. 若列为 object/string 类型 → 用 pd.to_numeric 采样判断 (>=50% 可转即采纳)
      3. 排除列名含 '时间'/'time'/'timestamp'/'Unnamed' 且内容不可转数字的列
    """
    numeric = []
    sample = df.iloc[:min(100, len(df))]
    for c in df.columns:
        col_name = str(c)
        # 跳过明确的时间戳列名
        if '时间' in col_name or col_name.lower() in ('time', 'timestamp', 't'):
            continue

        # 优先：类型系统判定
        if pd.api.types.is_numeric_dtype(df[c]):
            numeric.append(col_name)
            continue

        # 兜底：对 object/string 列做采样判定 (NaN 多的宽限到 50%)
        try:
            converted = pd.to_numeric(sample[c], errors="coerce")
            valid_ratio = converted.notna().sum() / max(len(sample), 1)
            if valid_ratio >= 0.5:
                numeric.append(col_name)
        except (ValueError, TypeError):
            continue
    return numeric


# ═══════════════════════════════════════════════════════════════════════
# 检测参数对话框
# ═══════════════════════════════════════════════════════════════════════

class DetectionParamsDialog(QDialog):
    """平台检测参数对话框 (增强版)

    主输入：每级恒温时长(分钟) → 自动换算最短平台
    采样间隔：可从时间列自动识别
    「自动匹配」→ 自动调阈值使检出数=期望数
    「预览」→ 曲线上高亮平台段 + 检出N/期望M
    高级 (折叠)：滚动窗口 / 阈值百分位 / 掐头比例 / 最短平台
    """

    def __init__(self, current_params: dict, df=None, wavelength_cols=None,
                 n_expected=None, time_col_idx=None, parent=None):
        super().__init__(parent)
        self._params = dict(current_params)
        self._df = df
        self._wavelength_cols = wavelength_cols or []
        self._n_expected = n_expected
        self._time_col_idx = time_col_idx  # 暗号识别的时间列索引
        self._build_ui()
        self._auto_detect_sample_interval()

    def _build_ui(self):
        self.setWindowTitle("检测参数设置")
        self.setSizeGripEnabled(True)
        layout = QVBoxLayout(self)

        # ── 主输入 ──
        g1, l1 = create_form_group("基本设置")

        hold_min = self._params.get("hold_time_min", 6.0)
        _, self.hold_time_spin = create_labeled_spinbox(
            "每级恒温时长 (分钟)", 0.5, 480.0, hold_min, decimals=1,
        )
        l1.addLayout(_)
        self.hold_time_spin.valueChanged.connect(self._on_hold_time_changed)
        self.shortest_display = QLabel()
        l1.addWidget(self.shortest_display)

        sample_s = self._params.get("sample_interval_s", 2.0)
        _, self.sample_interval_spin = create_labeled_spinbox(
            "采样间隔 (s)", 0.1, 3600.0, sample_s, decimals=1,
        )
        l1.addLayout(_)
        self.sample_interval_spin.valueChanged.connect(self._on_hold_time_changed)
        layout.addWidget(g1)

        # 两个控件都创建完后再做初始化联动
        self._on_hold_time_changed()

        # ── 自动匹配 + 预览 ──
        btn_row = QHBoxLayout()
        self.auto_match_btn = create_button("🔄 自动匹配", self._auto_match, "primary",
                                             tooltip="自动调阈值使检出平台数=期望数")
        btn_row.addWidget(self.auto_match_btn)
        self.preview_btn = create_button("👁 预览检出平台", self._preview, "secondary",
                                          tooltip="在曲线上高亮检出的平台段")
        btn_row.addWidget(self.preview_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.match_info = QLabel("")
        self.match_info.setStyleSheet("color: #1890ff; font-weight: bold; padding: 4px 0;")
        layout.addWidget(self.match_info)

        # ── 高级 (折叠) ──
        adv_group, adv_layout = create_form_group("高级参数")
        adv_group.setCheckable(True)
        adv_group.setChecked(False)

        _, self.rolling_spin = create_labeled_int_spinbox(
            "滚动窗口", 5, 200, self._params.get("rolling_window", 25),
        )
        adv_layout.addLayout(_)

        _, self.std_pct_spin = create_labeled_spinbox(
            "稳定阈值百分位", 10.0, 95.0,
            self._params.get("std_percentile", 45.0), decimals=1,
        )
        adv_layout.addLayout(_)

        _, self.head_trim_spin = create_labeled_spinbox(
            "掐头比例", 0.30, 0.95,
            self._params.get("head_trim_ratio", 0.70), decimals=2,
        )
        adv_layout.addLayout(_)

        _, self.min_plat_spin = create_labeled_int_spinbox(
            "最短平台 (点)", 10, 5000,
            self._params.get("min_plateau_samples", 180),
        )
        adv_layout.addLayout(_)

        adv_layout.addWidget(create_info_label(
            "物理依据：温箱刚到达设定温度时传感器有热惯性/过冲，"
            "平台前段不稳、末尾最稳。掐头留尾只对尾部求均值。"
        ))
        layout.addWidget(adv_group)

        # ── 确定/取消 ──
        row = QHBoxLayout()
        row.addWidget(create_button("确定", self.accept, "primary"))
        row.addWidget(create_button("取消", self.reject, "secondary"))
        layout.addLayout(row)

    def _auto_detect_sample_interval(self):
        """有暗号时间列时，从数据自动识别采样间隔并禁用输入"""
        if self._df is None or self._time_col_idx is None:
            return
        try:
            col = self._df.columns[self._time_col_idx]
            vals = pd.to_numeric(self._df[col], errors="coerce")
            valid = vals.dropna()
            if len(valid) >= 2:
                diffs = np.diff(valid.values[:50])
                median_diff = np.median(diffs[diffs > 0])
                if 0.1 < median_diff < 3600:
                    self.sample_interval_spin.setValue(round(float(median_diff), 1))
                    self.sample_interval_spin.setEnabled(False)
                    self.match_info.setText(
                        f"✅ 已从时间列 '{col}' 自动识别采样间隔: {median_diff:.1f}s"
                    )
        except Exception:
            pass

    def _on_hold_time_changed(self):
        # 防御: 高级参数区的控件可能还没构造
        if not hasattr(self, 'sample_interval_spin') or not hasattr(self, 'min_plat_spin'):
            return
        hold_min = self.hold_time_spin.value()
        sample_s = self.sample_interval_spin.value()
        pts = max(10, round(hold_min * 60.0 / sample_s * 0.5))
        # 同步更新最短平台 spinbox
        self.min_plat_spin.blockSignals(True)
        self.min_plat_spin.setValue(pts)
        self.min_plat_spin.blockSignals(False)
        self.shortest_display.setText(
            f"→ 自动换算最短平台: {pts} 点 (≈{pts*sample_s:.0f}s = {pts*sample_s/60:.1f}min)"
        )

    def _preview(self):
        """在温度曲线上高亮检出的平台段，显示检出数"""
        from py.calibration.step_extractor import detect_plateaus
        if self._df is None or not self._wavelength_cols:
            self.match_info.setText("⚠ 缺少数据")
            return

        try:
            P = detect_plateaus(
                self._df, self._wavelength_cols,
                rolling_window=self.rolling_spin.value(),
                std_percentile=self.std_pct_spin.value(),
                min_plateau_samples=self.min_plat_spin.value(),
                head_trim_ratio=self.head_trim_spin.value(),
            )
            n = len(P)
            exp = self._n_expected or "?"
            self.match_info.setText(
                f"检出 {n} 个平台 / 期望 {exp} 个"
                + (" ✅" if self._n_expected and n == self._n_expected else "")
            )
        except Exception as e:
            self.match_info.setText(f"⚠ 预览失败: {e}")

    def _auto_match(self):
        """自动调整阈值百分位使检出数=期望数"""
        from py.calibration.step_extractor import detect_plateaus
        if self._df is None or not self._wavelength_cols or not self._n_expected:
            self.match_info.setText("⚠ 缺少数据或期望数")
            return

        best_pct = self.std_pct_spin.value()
        best_n = 0
        for pct in range(10, 95, 5):
            P = detect_plateaus(
                self._df, self._wavelength_cols,
                rolling_window=self.rolling_spin.value(),
                std_percentile=float(pct),
                min_plateau_samples=self.min_plat_spin.value(),
                head_trim_ratio=self.head_trim_spin.value(),
            )
            n = len(P)
            if n == self._n_expected:
                best_pct = float(pct); best_n = n
                break
            if abs(n - self._n_expected) < abs(best_n - self._n_expected):
                best_pct = float(pct); best_n = n

        self.std_pct_spin.setValue(best_pct)
        self.match_info.setText(
            f"检出 {best_n}/{self._n_expected} 个平台 (阈值={best_pct:.0f}%)"
        )

    def get_params(self) -> dict:
        return {
            "rolling_window": self.rolling_spin.value(),
            "std_percentile": self.std_pct_spin.value(),
            "min_plateau_samples": self.min_plat_spin.value(),
            "head_trim_ratio": self.head_trim_spin.value(),
            "sample_interval_s": self.sample_interval_spin.value(),
            "hold_time_min": self.hold_time_spin.value(),
        }


# ═══════════════════════════════════════════════════════════════════════
# 应变系数输入对话框 (Phase B)
# ═══════════════════════════════════════════════════════════════════════

class StrainCoeffDialog(QDialog):
    """为双栅传感器的每个光栅输入已外部标定的应变系数 Ke"""

    def __init__(self, sensor_groups: dict, parent=None):
        super().__init__(parent)
        self._result = {}
        self._sensor_groups = sensor_groups
        self._build_ui()

    def _build_ui(self):
        self.setWindowTitle("输入应变系数 Ke")
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("为每个双栅传感器的光栅输入应变系数 (pm/με):"))

        self._coef_widgets = {}
        for prefix, gratings in self._sensor_groups.items():
            if len(gratings) < 2:
                continue  # 单栅跳过
            gb, gl = create_form_group(f"传感器 {prefix} (双栅)")
            for gi, g in enumerate(gratings):
                _, spin = create_labeled_spinbox(
                    f"{g['name']}  Ke{gi+1} (pm/με):",
                    0.1, 10.0, 1.2, decimals=3,
                )
                gl.addLayout(_)
                self._coef_widgets[f"{prefix}_Ke{gi+1}"] = spin
            layout.addWidget(gb)

        if not self._coef_widgets:
            layout.addWidget(QLabel("(无双栅传感器，无需输入应变系数)"))

        row = QHBoxLayout()
        row.addWidget(create_button("确定", self.accept, "primary"))
        row.addWidget(create_button("取消", self.reject, "secondary"))
        layout.addLayout(row)

    def get_coefficients(self) -> dict:
        """返回 {prefix: {Ke1: val, Ke2: val}}"""
        result = {}
        for key, spin in self._coef_widgets.items():
            parts = key.rsplit("_Ke", 1)
            prefix, ke_num = parts[0], f"Ke{parts[1]}"
            result.setdefault(prefix, {})[ke_num] = spin.value()
        return result


# ═══════════════════════════════════════════════════════════════════════
# 后台工作线程 (保持与迭代 1 一致)
# ═══════════════════════════════════════════════════════════════════════

class PhaseAWorker(QThread):
    """Phase A: df 已解析 → 计算 dL → 平台检测 → KMeans → 灵敏度回归

    零 I/O: 接收已解析的 DataFrame 副本，禁止任何 pd.read_csv / open 调用。
    """
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, df: pd.DataFrame, wavelength_cols: list[str],
                 setpoints: list[float], params: dict):
        super().__init__()
        self.df = df; self.wavelength_cols = wavelength_cols
        self.setpoints = setpoints; self.params = params
        self._last_result = None  # 测试用: run() 结束后可直接读取

    def run(self):
        try:
            from py.calibration.step_extractor import detect_plateaus
            from py.calibration.temperature_calibration import compute_dL

            self.progress.emit("正在计算波长漂移...")
            df, base = compute_dL(self.df, self.wavelength_cols)
            self.progress.emit(f"dL 计算完成: {len(df)} 行 × {len(df.columns)} 列")

            self.progress.emit("检测温度阶梯平台...")
            P = detect_plateaus(
                df, self.wavelength_cols,
                rolling_window=self.params.get("rolling_window", 25),
                std_percentile=self.params.get("std_percentile", 45.0),
                min_plateau_samples=self.params.get("min_plateau_samples", 180),
                head_trim_ratio=self.params.get("head_trim_ratio", 0.70),
            )
            self.progress.emit(f"检测到 {len(P)} 个平台段")

            self.progress.emit("映射平台到设定温度...")
            P = assign_setpoints(P, self.setpoints)

            self.progress.emit("回归各光栅灵敏度...")
            dL_cols = [f"{c}_d" for c in self.wavelength_cols]
            S_eff_raw = {}
            for i, dc in enumerate(dL_cols):
                S_eff_raw[dc] = regress_sensitivity(P, dc, self.wavelength_cols[i])

            # ★ 归一化: S_eff key 必须用原始 df 列名，_d 后缀是内部实现细节禁止外泄
            S_eff = {}
            for i, wcol in enumerate(self.wavelength_cols):
                S_eff[wcol] = S_eff_raw[f"{wcol}_d"]

            self.progress.emit(f"回归完成: {len(S_eff)} 个光栅")
            result = {"df": df, "base": base, "plateaus": P,
                      "S_eff": S_eff, "wavelength_cols": self.wavelength_cols}
            self._last_result = result  # 测试用：run() 结束后直接读取
            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(f"{e}\n{traceback.format_exc()}")


class PhaseBWorker(QThread):
    """Phase B: 用实测S_eff作KT + 用户Ke → 双波长解耦诊断

    双栅传感器: decouple(dl1, dl2, Ke1, S1, Ke2, S2)
    单栅传感器: 仅汇总温度系数，不做解耦
    """
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, df, time_h, wavelength_cols, annotation_groups,
                 S_eff_result, strain_coeffs, sample_interval_s=2.0):
        super().__init__()
        self.df = df; self.time_h = time_h
        self.wavelength_cols = wavelength_cols
        self.annotation_groups = annotation_groups
        self.S_eff_result = S_eff_result
        self.strain_coeffs = strain_coeffs or {}
        self.sample_interval_s = sample_interval_s
        self._last_result = None  # 测试用: run() 结束后可直接读取

    def run(self):
        try:
            from py.calibration.temperature_calibration import compute_dL
            self.progress.emit("正在运行解耦分析...")

            # ★ Phase B 自己算 dL（只传了原始 df，没有 _d 列）
            dL_cols = [c for c in self.wavelength_cols if c in self.df.columns]
            df_aug, _base = compute_dL(self.df.copy(), dL_cols)

            time_h = self.time_h
            sensors = {}
            comparisons = []

            for pfx, gratings in self.annotation_groups.items():
                if len(gratings) < 2:
                    # 单栅：只汇总温度系数
                    g = gratings[0]
                    wcol = g.get("col_name", g["name"])
                    s = self.S_eff_result.get(wcol, {})
                    sensors[pfx] = {
                        "single_grating": True,
                        "S1": s.get("slope", float("nan")),
                        "T_base": s.get("T_base", float("nan")),
                        "R2": s.get("r2", float("nan")),
                    }
                    self.progress.emit(f"  [{pfx}] 单栅: S_eff={s.get('slope', 0):.2f} pm/°C, R²={s.get('r2', 0):.6f}")
                    continue

                # 双栅：取两光栅 — col_name 是原始 df 列名
                wcol1 = gratings[0].get("col_name", gratings[0]["name"])
                wcol2 = gratings[1].get("col_name", gratings[1]["name"])

                # 实测温度系数 (Phase A 结果 — key 已归一化为原始列名)
                S1 = self.S_eff_result[wcol1]["slope"]
                S2 = self.S_eff_result[wcol2]["slope"]
                T_base = (self.S_eff_result[wcol1]["T_base"] + self.S_eff_result[wcol2]["T_base"]) / 2.0

                # 用户输入的应变系数
                coefs = self.strain_coeffs.get(pfx, {})
                Ke1 = coefs.get("Ke1", 1.2)
                Ke2 = coefs.get("Ke2", 1.2)

                # 从 dL 增强 df 读漂移数据 (_d 列由 compute_dL 产生)
                dl1 = df_aug[f"{wcol1}_d"].values
                dl2 = df_aug[f"{wcol2}_d"].values

                # 标定KT解耦 (对比用)
                eps_orig, dT_orig = decouple(dl1, dl2, Ke1, Ke1 * 0.95, Ke2, Ke2 * 0.95)
                # 修正解耦：用实测 S_eff 替 KT
                eps_corr, dT_corr = decouple(dl1, dl2, Ke1, S1, Ke2, S2)
                T_abs = dT_corr + T_base

                sensors[pfx] = {
                    "single_grating": False,
                    "eps_orig": eps_orig, "dT_orig": dT_orig,
                    "eps_corr": eps_corr, "dT_corr": dT_corr, "T_abs": T_abs,
                    "S1": S1, "S2": S2, "T_base": T_base,
                }

                # 标定 vs 实测对比
                for label, gKT, sEff in [
                    (f"{pfx}-W1", Ke1 * 0.95, S1),
                    (f"{pfx}-W2", Ke2 * 0.95, S2),
                ]:
                    comparisons.append({
                        "grating": label,
                        "given_KT": gKT,
                        "measured_S_eff": sEff,
                        "diff_pm_per_C": sEff - gKT,
                        "apparent_strain_ppm_per_C": (sEff - gKT) / Ke1 if abs(Ke1) > 1e-10 else float("nan"),
                    })

                self.progress.emit(
                    f"  [{pfx}] 双栅: S1={S1:.2f} S2={S2:.2f} "
                    f"修正e_std={np.nanstd(eps_corr):.1f}με"
                )

            self.progress.emit("解耦分析完成！")
            result = {
                "sensors": sensors, "comparisons": comparisons,
                "df": self.df, "time_h": time_h,
            }
            self._last_result = result  # 测试用
            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(f"{e}\n{traceback.format_exc()}")


# 保留旧 Worker 别名向后兼容
TemperatureCalibrationWorker = PhaseAWorker


class StrainCalibrationWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, config, readings):
        super().__init__()
        self.config = config; self.readings = readings

    def run(self):
        try:
            self.progress.emit("正在分析应变标定...")
            result = calibrate_strain(self.config, self.readings)
            self.progress.emit("分析完成！")
            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(f"{e}\n{traceback.format_exc()}")


# ═══════════════════════════════════════════════════════════════════════
# 温度标定子页 (迭代 2 重构)
# ═══════════════════════════════════════════════════════════════════════

class _PasteTable(QTableWidget):
    """暗号补填表 — 标准 QTableWidgetItem 可编辑模型 (Excel-like)

    单击 → 选中（不编辑）；双击/F2 → 编辑态；选中态 Ctrl+V → 多行分发
    """

    def __init__(self, parent=None):
        super().__init__(0, 4, parent)
        # 允许双击/F2 进入编辑态
        from PyQt6.QtWidgets import QAbstractItemView
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                             QAbstractItemView.EditTrigger.EditKeyPressed)

    def keyPressEvent(self, ev):
        from PyQt6.QtGui import QKeySequence
        if ev.matches(QKeySequence.StandardKey.Paste):
            # 编辑态下: 让默认编辑器处理 (单格粘贴，手动编辑路径)
            if self.state().value == 2:  # EditingState
                super().keyPressEvent(ev)
                return
            # 选中态: 表级拦截，多行分发
            self._handle_multi_row_paste(self.currentRow())
            ev.accept()
            return
        super().keyPressEvent(ev)

    def _handle_multi_row_paste(self, start_row: int = -1):
        """取剪贴板→拆行→逐行 setItem→逐行校验"""
        text = QApplication.clipboard().text()
        rows_list = [r for r in text.replace('\r\n', '\n').split('\n') if r.strip()]
        if not rows_list:
            return
        start = start_row if start_row >= 0 else (self.currentRow() if self.currentRow() >= 0 else 0)
        INPUT_COL = 2
        for i, value in enumerate(rows_list):
            target = start + i
            if target >= self.rowCount():
                break
            cell_value = value.split('\t')[0].strip()
            item = QTableWidgetItem(cell_value)
            item.setFlags(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.setItem(target, INPUT_COL, item)
            self._validate_row(target)

    def _validate_row(self, row: int):
        """校验单行: 取 col(0)列名 + col(2)输入框文本 → 更新 col(3)校验结果"""
        from ui.calibration_tab import is_valid_annotation
        col_item = self.item(row, 0)
        val_item = self.item(row, 2)
        if not col_item:
            return
        entered = val_item.text().strip() if val_item else ""
        if not entered:
            return
        ok = is_valid_annotation(entered)
        self.setItem(row, 3, QTableWidgetItem("✓" if ok else "✗ 格式: '传感器-W列号'"))

# ═══════════════════════════════════════════════════════════════════════
# Phase A 对话框
class _StrainPasteTable(QTableWidget):
    """应变读数表 — 支持 Ctrl+V 整块粘贴（跳过备注行 row 0）

    单击 → 选中（不编辑）；双击/F2 → 编辑态；选中态 Ctrl+V → 多行分发
    """

    def __init__(self, rows: int, cols: int, parent=None):
        super().__init__(rows, cols, parent)
        from PyQt6.QtWidgets import QAbstractItemView
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                             QAbstractItemView.EditTrigger.EditKeyPressed)

    def keyPressEvent(self, ev):
        from PyQt6.QtGui import QKeySequence
        if ev.matches(QKeySequence.StandardKey.Paste):
            # 编辑态: 默认单格粘贴
            if self.state().value == 2:  # EditingState
                super().keyPressEvent(ev)
                return
            # 选中态: 多行分发（备注行 row 0 永不参与）
            self._paste_block(self.currentRow(), self.currentColumn())
            ev.accept()
            return
        super().keyPressEvent(ev)

    def _paste_block(self, start_row: int = -1, start_col: int = 0):
        """取剪贴板 → 拆行 → 逐行 setItem（跳过 row 0；col 1 自动计算列不覆盖）"""
        from PyQt6.QtWidgets import QApplication
        text = QApplication.clipboard().text()
        if not text.strip():
            return

        lines = [ln for ln in text.replace('\r\n', '\n').split('\n') if ln.strip()]
        if not lines:
            return

        # 起始行: 永远不低于 1（row 0 = 备注行）
        if start_row < 1:
            start_row = self.currentRow() if self.currentRow() >= 1 else 1
        start_row = max(start_row, 1)
        if start_col < 0:
            start_col = self.currentColumn() if self.currentColumn() >= 0 else 0

        # 列跳转表: col 0 → col 2 (跳过 col 1 应变自动计算列)
        # 其他起始列按正常顺序
        _COL_SKIP_1 = (start_col == 0)

        self.blockSignals(True)
        try:
            for i, line in enumerate(lines):
                target_row = start_row + i
                if target_row >= self.rowCount():
                    break
                cells = line.split('\t')
                col_offset = 0
                for cell_text in cells:
                    if start_col == 0 and col_offset == 1:
                        # 跳过 col 1 (理论应变，自动计算)
                        col_offset += 1
                    target_col = start_col + col_offset
                    if target_col >= self.columnCount():
                        break
                    val = cell_text.strip()
                    item = QTableWidgetItem(val)
                    item.setFlags(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                    self.setItem(target_row, target_col, item)
                    col_offset += 1
        finally:
            self.blockSignals(False)

        # 粘贴后触发位移→应变自动计算（仅位移列 col=0）
        if start_col == 0:
            for i in range(len(lines)):
                target_row = start_row + i
                if target_row >= self.rowCount():
                    break
                disp_item = self.item(target_row, 0)
                if disp_item is None or not disp_item.text().strip():
                    continue
                try:
                    disp_mm = float(disp_item.text().strip())
                except (ValueError, TypeError):
                    continue
                gauge = getattr(self, '_paste_gauge_mm', 80.0)
                eps = disp_mm / gauge * 1e6 if gauge > 0 else 0.0
                strain_item = QTableWidgetItem(f"{eps:.2f}")
                strain_item.setFlags(strain_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.setItem(target_row, 1, strain_item)


class _ComboPasteRedirect(QObject):
    """QComboBox 事件过滤器 — Ctrl+V 转发给表格粘贴处理器

    setEditable(True) 的 QComboBox 内部有 QLineEdit 子控件，
    过滤器必须装到 QLineEdit 上才能截获所有键事件。
    """

    def __init__(self, table: _StrainPasteTable, combo: QComboBox, parent=None):
        super().__init__(parent)
        self._table = table
        # 装到 editable ComboBox 的内部 QLineEdit
        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.installEventFilter(self)

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.KeyPress:
            from PyQt6.QtCore import Qt as _QtC
            is_v = event.key() == _QtC.Key.Key_V
            ctrl = bool(event.modifiers() & _QtC.KeyboardModifier.ControlModifier)
            if is_v and ctrl:
                # 转发给表格的粘贴处理器，不吞进 ComboBox
                self._table._paste_block(1, self._table.currentColumn())
                return True  # 拦截，阻止 QLineEdit 处理
        return False


# ═══════════════════════════════════════════════════════════════════════

class PhaseADialog(QDialog):
    """阶段 A 对话框: 暗号补填 + 温度范围 + 检测参数 + S_eff 回归"""

    _DONE = object()

    def __init__(self, loaded_df, annotation_dict, annotation_groups,
                 detection_params, time_col_idx, parent=None):
        super().__init__(parent)
        self._df = loaded_df
        self._annotation = dict(annotation_dict) if annotation_dict else {}
        self._groups = dict(annotation_groups) if annotation_groups else {}
        self._params = dict(detection_params)
        self._time_col_idx = time_col_idx
        self._worker = None
        self._phase_a_result = None
        self._build_ui()
        self._refresh_all()

        # ── 状态恢复: 从主 Tab 状态 dict 读取上次关闭时的值 ──
        tp = self._get_temp_page()
        if tp and tp._phase_a_state:
            state = tp._phase_a_state
            if state.get("annotation"):
                self._annotation = dict(state["annotation"])
            if state.get("groups"):
                self._groups.update(state["groups"])
            if state.get("tmin") is not None:
                self.tmin.setValue(state["tmin"]); self.tmax.setValue(state["tmax"]); self.tstep.setValue(state["tstep"])
            if state.get("params"):
                self._params.update(state["params"])
            if state.get("seff_result"):
                self._on_result(state["seff_result"])
            self._refresh_all()

    def _get_temp_page(self):
        """获取主Tab引用 (TemperatureCalibrationPage)"""
        p = self.parent()
        if p and hasattr(p, '_loaded_df'):
            return p  # TemperatureCalibrationPage itself
        # 通道: CalibrationTabWidget → TemperatureCalibrationPage
        return p.temperature_page if p and hasattr(p, 'temperature_page') else None

    def _build_ui(self):
        self.setWindowTitle("阶段 A: 解析温度系数")
        self.setSizeGripEnabled(True)
        self.resize(1200, 900)

        # ── 全屏切换 ──
        class EscFilter(QObject):
            def eventFilter(self, obj, event):
                if event.type() == event.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                    if obj.isFullScreen():
                        obj.showNormal()
                        return True
                return False
        self._esc_filter = EscFilter(self)
        self.installEventFilter(self._esc_filter)

        layout = QVBoxLayout(self)

        # ── 信息条 ──
        self.info_label = QLabel("...")
        self.info_label.setStyleSheet("color: #1890ff; font-weight: bold; padding: 6px 8px; background: #f0f8ff; border-radius: 4px;")
        layout.addWidget(self.info_label)

        # ── 暗号补填表 (4列) ──
        layout.addWidget(QLabel("💡 支持从 Excel 整列复制后 Ctrl+V 粘贴到输入列 —— 📝 占位暗号补填 (输入后点击应用):"))
        self.fill_table = _PasteTable(self)
        self.fill_table.setHorizontalHeaderLabels(["文件原列名", "当前暗号", "输入新暗号", "校验"])
        self.fill_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.fill_table.setMaximumHeight(320)
        layout.addWidget(self.fill_table)

        btn_row = QHBoxLayout()
        self.apply_btn = create_button("✓ 应用暗号", self._on_apply, "primary",
            tooltip="将新暗号写入 annotation 字典并重新评估")
        btn_row.addWidget(self.apply_btn)
        reset_btn = create_button("↺ 重置为占位", self._on_reset, "secondary")
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── 运行区 ──
        run_gb, run_layout = create_form_group("温度范围与检测参数")
        temp_row = QHBoxLayout()
        temp_row.addWidget(QLabel("温度范围:"))
        self.tmin = QDoubleSpinBox(); self.tmin.setRange(-50,200); self.tmin.setValue(10.0); self.tmin.setPrefix("最低 "); self.tmin.setSuffix(" °C")
        temp_row.addWidget(self.tmin)
        self.tmax = QDoubleSpinBox(); self.tmax.setRange(-50,200); self.tmax.setValue(70.0); self.tmax.setPrefix("最高 "); self.tmax.setSuffix(" °C")
        temp_row.addWidget(self.tmax)
        self.tstep = QDoubleSpinBox(); self.tstep.setRange(1,50); self.tstep.setValue(10.0); self.tstep.setPrefix("步长 "); self.tstep.setSuffix(" °C")
        temp_row.addWidget(self.tstep)
        temp_row.addStretch()
        run_layout.addLayout(temp_row)

        detect_row = QHBoxLayout()
        detect_row.addWidget(create_button("⚙ 检测参数...", self._open_detect, "secondary"))
        detect_row.addWidget(create_button("📖 设置说明", self._show_help, "secondary"))
        detect_row.addStretch()
        run_layout.addLayout(detect_row)
        layout.addWidget(run_gb)

        # ── 运行按钮 + 进度 ──
        run_btn_row = QHBoxLayout()
        self.run_btn = QPushButton("▶ 解析温度系数")
        self.run_btn.setStyleSheet(_BTN_STYLE_DISABLED)
        self.run_btn.setCursor(Qt.CursorShape.ForbiddenCursor)
        self.run_btn.setEnabled(False)
        self.run_btn.setToolTip("需至少一对前缀相同的合法暗号")
        self.run_btn.clicked.connect(self._on_run)
        run_btn_row.addWidget(self.run_btn)
        self.progress_label = QLabel("")
        run_btn_row.addWidget(self.progress_label)
        run_btn_row.addStretch()
        layout.addLayout(run_btn_row)

        # ── 错误条 ──
        self.error_bar = QLabel("")
        self.error_bar.setStyleSheet("color: #ff4d4f; padding: 4px 8px; background: #fff2f0; border-radius: 3px;")
        self.error_bar.setVisible(False)
        self.error_bar.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.error_bar)

        # ── S_eff 结果表 ──
        self.seff_table = QTableWidget(0, 5)
        self.seff_table.setHorizontalHeaderLabels(["暗号", "灵敏度 (pm/°C)", "线性度 R²", "分组", "类型"])
        self.seff_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.seff_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.seff_table.horizontalHeader().setStyleSheet("QHeaderView::section { font-weight: bold; }")
        layout.addWidget(self.seff_table, stretch=1)

        # ── 图表分析按钮 ──
        self.charts_btn = create_button("📊 图表分析…", self._open_charts, "secondary",
                                         tooltip="打开独立对话框展示回归图 (8 子图)")
        self.charts_btn.setEnabled(False)
        layout.addWidget(self.charts_btn)

        # ── 全屏 ──
        row = QHBoxLayout()
        row.addWidget(create_button("⛶ 全屏", self._toggle_fs, "secondary"))
        row.addStretch()
        row.addWidget(create_button("确定", self.accept, "primary"))
        layout.addLayout(row)

    def _toggle_fs(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ── 刷新 (首次加载用，含表格初始化) ──
    def _refresh_all(self):
        if self._df is None or self._df.empty:
            self.info_label.setText("⚠ 无数据 — 请先加载文件")
            self.fill_table.setRowCount(0)
            return
        from utils.file_parser import detect_numeric_wavelength_columns
        num_cols, wave_cols = detect_numeric_wavelength_columns(self._df)
        self._num_cols = num_cols
        self._wave_cols = wave_cols

        from ui.calibration_tab import _count_annotations
        legal, placeholder = _count_annotations(self._annotation, wave_cols)
        self._legal_cnt = legal
        self._placeholder_cnt = placeholder

        self._sync_button_state()
        self._sync_info_label()
        # 首次加载：构建表格 (后续 apply/reset 不重建)
        self._fill_table(placeholder)

    def _sync_button_state(self):
        """仅同步运行按钮 enabled/样式/tooltip"""
        from ui.calibration_tab import _can_parse_now
        can = _can_parse_now(self._groups, self._legal_cnt)
        self.run_btn.setEnabled(can)
        if can:
            self.run_btn.setStyleSheet(_BTN_STYLE_ENABLED)
            self.run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.run_btn.setToolTip("")
        else:
            self.run_btn.setStyleSheet(_BTN_STYLE_DISABLED)
            self.run_btn.setCursor(Qt.CursorShape.ForbiddenCursor)
            self.run_btn.setToolTip(
                f"需至少一对前缀相同的合法暗号 (格式: '传感器-W列号')，当前合法 {self._legal_cnt} 个"
            )

    def _sync_info_label(self):
        """仅刷新信息条文案"""
        from ui.calibration_tab import _build_annotation_info_parts
        fmt = getattr(self.parent(), '_annotation_meta', {}).get('format', '') if hasattr(self.parent(), '_annotation_meta') else ''
        info = _build_annotation_info_parts(
            self._groups, self._legal_cnt, self._placeholder_cnt,
            fmt, 0, self._num_cols, self._wave_cols,
        )
        self.info_label.setText(" | ".join(info))

    def _fill_table(self, placeholder_cnt):
        """填充暗号表 — 显示所有 annotation 条目 (占位+合法, 确保状态恢复可见)"""
        from ui.calibration_tab import is_valid_annotation

        # 收集所有 annotation 条目 (不过滤合法, 保证状态恢复后表行可见)
        all_items = []
        col_list = list(self._df.columns)
        for col_name, val in self._annotation.items():
            s = str(val).strip().strip("'\"'\"'\"")
            if not s or '-' not in s:
                continue
            is_valid = is_valid_annotation(s)
            all_items.append((col_name, s, is_valid))

        self.fill_table.setRowCount(max(8, len(all_items)))
        for i, (cname, cval, is_valid) in enumerate(all_items):
            self.fill_table.setItem(i, 0, QTableWidgetItem(cname))
            self.fill_table.setItem(i, 1, QTableWidgetItem(cval))
            # 输入列: 标准 QTableWidgetItem (可编辑 + 可选中)
            inp_item = QTableWidgetItem(cval if is_valid else "")
            inp_item.setFlags(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.fill_table.setItem(i, 2, inp_item)
            self.fill_table.setItem(i, 3, QTableWidgetItem("✓" if is_valid else "—"))
        # 空余行
        for i in range(len(all_items), self.fill_table.rowCount()):
            self.fill_table.setItem(i, 0, QTableWidgetItem(""))
            self.fill_table.setItem(i, 1, QTableWidgetItem(""))
            inp_item = QTableWidgetItem("")
            inp_item.setFlags(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.fill_table.setItem(i, 2, inp_item)
            self.fill_table.setItem(i, 3, QTableWidgetItem(""))

    def _on_apply(self):
        """应用暗号: 读取 QTableWidgetItem → 更新 annotation → 刷新校验列 → 同步按钮/信息。"""
        from ui.calibration_tab import is_valid_annotation
        col_list = list(self._df.columns)

        # Step 1: 快照所有 QTableWidgetItem
        snapshots = []
        for i in range(self.fill_table.rowCount()):
            col_item = self.fill_table.item(i, 0)
            inp_item = self.fill_table.item(i, 2)
            if not col_item or not inp_item:
                continue
            cname = col_item.text().strip()
            entered = inp_item.text().strip()
            if cname and entered:
                snapshots.append((i, cname, entered))

        # Step 2: 更新 annotation + 校验状态列
        applied = 0
        for i, cname, entered in snapshots:
            ok = is_valid_annotation(entered)
            status_item = self.fill_table.item(i, 3)
            if status_item:
                status_item.setText("✓" if ok else "✗ 格式: '传感器-W列号'")
            else:
                self.fill_table.setItem(i, 3, QTableWidgetItem("✓" if ok else "✗ 格式: '传感器-W列号'"))
            if ok:
                self._annotation[cname] = entered
                applied += 1
                # 同步更新"当前暗号"列 (用户看到即时反馈)
                cur_item = self.fill_table.item(i, 1)
                if cur_item:
                    cur_item.setText(entered)

        # 防御断言: 填入数应等于表中非空行数 (漏空列名则警告)
        expected_rows = sum(
            1 for i in range(self.fill_table.rowCount())
            if self.fill_table.item(i, 0) and self.fill_table.item(i, 0).text().strip()
        )
        if applied < expected_rows:
            print(f"[PhaseADialog] ⚠ 只应用了 {applied}/{expected_rows} 行暗号 — 检查空列名行")

        # Step 3: 重建 groups
        data_cols_for_group = {}
        for col_name, name in self._annotation.items():
            try:
                idx = col_list.index(col_name)
                s = str(name).strip().strip("'\"'\"'\"")
                if s and '-' in s:
                    data_cols_for_group[idx] = s
            except ValueError:
                pass
        if data_cols_for_group:
            from ui.calibration_tab import _group_annotations_by_prefix
            self._groups = _group_annotations_by_prefix(data_cols_for_group, self._df)

        # Step 4: 重新统计合法/占位
        from ui.calibration_tab import _count_annotations
        from utils.file_parser import detect_numeric_wavelength_columns
        _, wave_cols = detect_numeric_wavelength_columns(self._df)
        self._legal_cnt, self._placeholder_cnt = _count_annotations(self._annotation, wave_cols)

        # Step 5: 同步按钮+信息条 (不碰表格)
        self._sync_button_state()
        self._sync_info_label()

    def _on_reset(self):
        """重置为占位: 清空输入列 → 恢复 placeholder annotation → 仅刷新按钮/信息条"""
        from ui.calibration_tab import is_valid_annotation
        for i in range(self.fill_table.rowCount()):
            col_item = self.fill_table.item(i, 0)
            cur_item = self.fill_table.item(i, 1)
            inp_item = self.fill_table.item(i, 2)
            if not (col_item and cur_item):
                continue
            if inp_item:
                inp_item.setText("")
            status_item = self.fill_table.item(i, 3)
            if status_item:
                status_item.setText("—")
            cname = col_item.text().strip()
            old = cur_item.text().strip()
            if old and not is_valid_annotation(old):
                self._annotation[cname] = old  # 恢复占位

        from ui.calibration_tab import _count_annotations
        from utils.file_parser import detect_numeric_wavelength_columns
        _, wave_cols = detect_numeric_wavelength_columns(self._df)
        self._legal_cnt, self._placeholder_cnt = _count_annotations(self._annotation, wave_cols)
        self._sync_button_state()
        self._sync_info_label()

    # ── 运行 ──
    def _get_setpoints(self):
        tmin = self.tmin.value(); tmax = self.tmax.value(); step = self.tstep.value()
        result = []
        t = tmin
        while t <= tmax + 1e-9:
            result.append(round(t, 1)); t += step
        return result

    def _on_run(self):
        setpoints = self._get_setpoints()
        if len(setpoints) < 2:
            self.error_bar.setText("⚠ 至少需要 2 个设定温度"); self.error_bar.setVisible(True)
            return

        wavelength_cols = []
        for pfx, gratings in self._groups.items():
            for g in gratings:
                from ui.calibration_tab import is_valid_annotation
                if is_valid_annotation(g.get("name", "")):
                    wavelength_cols.append(g.get("col_name", g["name"]))

        if not wavelength_cols:
            self.error_bar.setText("⚠ 无合法暗号波长列，请先补填暗号")
            self.error_bar.setVisible(True)
            return

        self.run_btn.setText("运行中..."); self.run_btn.setEnabled(False)
        self.error_bar.setVisible(False)

        from ui.calibration_tab import PhaseAWorker
        self._worker = PhaseAWorker(self._df.copy(), wavelength_cols, setpoints, self._params)
        self._worker.progress.connect(lambda m: self.progress_label.setText(m))
        self._worker.finished.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_result(self, result):
        self._phase_a_result = result
        S_eff = result.get("S_eff", {})

        # ── S_eff 结果表 ──
        self.seff_table.setRowCount(len(S_eff))
        for i, (col_name, s) in enumerate(S_eff.items()):
            display = self._annotation.get(col_name, s.get("display", col_name))
            # 分组：从 groups 中找该列属哪个传感器
            sensor_pfx = ""
            grating_type = "—"
            for pfx, gratings in self._groups.items():
                for g in gratings:
                    if g.get("col_name", g["name"]) == col_name:
                        sensor_pfx = pfx
                        grating_type = "双栅" if len(gratings) >= 2 else "单栅"
                        break
            self.seff_table.setItem(i, 0, QTableWidgetItem(display))
            self.seff_table.setItem(i, 1, QTableWidgetItem(f"{s['slope']:.2f}"))
            self.seff_table.setItem(i, 2, QTableWidgetItem(f"{s['r2']:.6f}"))
            self.seff_table.setItem(i, 3, QTableWidgetItem(sensor_pfx))
            self.seff_table.setItem(i, 4, QTableWidgetItem(grating_type))

        self.charts_btn.setEnabled(True)
        self._phase_a_result = result

        s_eff_keys = list(result.get("S_eff", {}).keys())

        self.run_btn.setText("✓ 完成")
        self.run_btn.setStyleSheet(_BTN_STYLE_ENABLED)
        self.run_btn.setEnabled(True)
        self.progress_label.setText(f"✅ {len(S_eff)} 个光栅回归完成")

    def _open_charts(self):
        if not self._phase_a_result:
            print("[A1] charts btn clicked but _phase_a_result is None, returning")
            return
        print("[A1] charts btn clicked, opening PhaseAChartsDialog")
        from ui.widgets.charts_dialog import PhaseAChartsDialog
        dlg = PhaseAChartsDialog(self._phase_a_result, self._annotation, self)
        dlg.exec()

    def _on_error(self, msg):
        self.run_btn.setText("▶ 解析温度系数")
        self.run_btn.setEnabled(True)
        self.error_bar.setText(f"❌ {msg[:500]}"); self.error_bar.setVisible(True)

    # ── 子对话框 ──
    def _open_detect(self):
        from utils.file_parser import detect_numeric_wavelength_columns
        _, wave_cols = detect_numeric_wavelength_columns(self._df)
        from ui.calibration_tab import DetectionParamsDialog
        dlg = DetectionParamsDialog(self._params, self._df, wave_cols,
                                     n_expected=len(self._get_setpoints()),
                                     time_col_idx=self._time_col_idx, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._params = dlg.get_params()

    def _show_help(self):
        import os
        help_path = "D:/桌面文件/222/files/检测参数说明.txt"
        content = ""
        if os.path.isfile(help_path):
            with open(help_path, "r", encoding="utf-8") as f:
                content = f.read()
        else:
            content = "检测参数说明文件未找到。"
        dlg = QDialog(self); dlg.setWindowTitle("检测参数说明"); dlg.resize(700, 600)
        l = QVBoxLayout(dlg); te = QTextEdit(); te.setReadOnly(True); te.setPlainText(content)
        l.addWidget(te); l.addWidget(create_button("关闭", dlg.accept, "secondary"))
        dlg.exec()

    def _write_state_to_main_page(self):
        """写回主 Tab 状态 (被 accept + reject 共用)"""
        tp = self._get_temp_page()
        if tp:
            tp._annotation_dict = dict(self._annotation)
            tp._annotation_groups = dict(self._groups)
            if self._phase_a_result:
                tp._last_result = self._phase_a_result
                tp._phase_a_done = True
            tp._phase_a_state = {
                "annotation": dict(self._annotation),
                "groups": dict(self._groups),
                "tmin": self.tmin.value(),
                "tmax": self.tmax.value(),
                "tstep": self.tstep.value(),
                "params": dict(self._params),
                "seff_result": self._phase_a_result,
            }

    def accept(self):
        self._write_state_to_main_page()
        super().accept()

    def reject(self):
        self._write_state_to_main_page()
        super().reject()

    def get_result(self):
        return self._phase_a_result


# ═══════════════════════════════════════════════════════════════════════
# Phase B 对话框
# ═══════════════════════════════════════════════════════════════════════

def _normalize_coeffs(coeffs: dict) -> dict[str, dict[str, float]]:
    """统一 Ke 系数为 dict 形状: {sensor: {"Ke1": float, "Ke2": float}}

    兼容旧版 list 格式 [{Ke1, Ke2}] 的自动转换。
    """
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
        else:
            out[s] = {"Ke1": float("nan"), "Ke2": float("nan")}
    return out


class PhaseBDialog(QDialog):
    """阶段 B 对话框: Ke 输入 + 解耦诊断"""

    def __init__(self, loaded_df, annotation_groups, phase_a_result, parent=None):
        super().__init__(parent)
        self._df = loaded_df
        self._groups = annotation_groups
        self._phase_a_result = phase_a_result
        self._coeffs = {}
        self._worker = None
        self._last_result = None
        self._single_excluded_label = None  # 单栅排除提示 label (懒初始化)
        self._build_ui()

        # ── 状态恢复: 从主 Tab _phase_b_state 恢复上次关闭时的值 ──
        tp = self._get_temp_page()
        if tp and tp._phase_b_state:
            state = tp._phase_b_state
            # 恢复 Ke 表 (统一 dict 形状 → self._coeffs)
            ke_table = _normalize_coeffs(state.get("ke_table", {}))
            for i in range(self.coef_table.rowCount()):
                pfx = self.coef_table.item(i, 0).text().strip()
                if pfx in ke_table:
                    ke = ke_table[pfx]
                    self.coef_table.setItem(i, 1, QTableWidgetItem(f"{float(ke['Ke1']):.2f}"))
                    self.coef_table.setItem(i, 2, QTableWidgetItem(f"{float(ke['Ke2']):.2f}"))
            self._coeffs = dict(ke_table)
            # 恢复解耦结果 (渲染 10 列表格)
            decoupling = state.get("decoupling_results", {})
            if decoupling:
                sensors = {}
                for s_name, d in decoupling.items():
                    sensors[s_name] = {
                        "eps_corr": [d.get("e_mean", 0)] * 10,
                        "eps_orig": [], "dT_corr": [], "T_abs": [],
                        "S1": 0, "S2": 0, "T_base": 0,
                        "single_grating": s_name not in ke_table,
                    }
                saved_ratings = {s: d.get("rating", "—") for s, d in decoupling.items()}
                # ★ 不设 _last_result: 让 _open_charts 的 lazy decouple 用真实 df 重算
                self._render_result_table(sensors, self._phase_a_result.get("S_eff", {}),
                                           saved_ratings)
                self.charts_btn.setEnabled(True)

    def _get_temp_page(self):
        p = self.parent()
        if hasattr(p, '_loaded_df'):
            return p
        if hasattr(p, 'temperature_page'):
            return p.temperature_page
        return None

    def _build_ui(self):
        self.setWindowTitle("阶段 B: 分析数据")
        self.resize(1100, 800)
        layout = QVBoxLayout(self)

        # ── Ke 输入表 ──
        layout.addWidget(QLabel("📝 双栅传感器应变系数 Ke (pm/με)"))
        self.coef_table = QTableWidget(0, 3)
        self.coef_table.setHorizontalHeaderLabels(["传感器", "Ke1 (光栅1)", "Ke2 (光栅2)"])
        self.coef_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        from ui.calibration_tab import is_valid_annotation
        row = 0
        for pfx, gratings in self._groups.items():
            if len(gratings) < 2:
                continue
            valid_names = [g["name"] for g in gratings if is_valid_annotation(g.get("name", ""))]
            if len(valid_names) < 2:
                continue
            self.coef_table.setRowCount(row + 1)
            self.coef_table.setItem(row, 0, QTableWidgetItem(pfx))
            self.coef_table.setItem(row, 1, QTableWidgetItem("1.2"))
            self.coef_table.setItem(row, 2, QTableWidgetItem("1.2"))
            row += 1
        layout.addWidget(self.coef_table)

        if self.coef_table.rowCount() == 0:
            layout.addWidget(QLabel("(无双栅传感器，仅汇总温度系数)"))

        # ── 运行 ──
        run_row = QHBoxLayout()
        self.run_btn = QPushButton("▶ 运行解耦分析")
        self.run_btn.setStyleSheet(_BTN_STYLE_ENABLED)
        self.run_btn.clicked.connect(self._on_run)
        run_row.addWidget(self.run_btn)
        self.progress_label = QLabel("")
        run_row.addWidget(self.progress_label)
        run_row.addStretch()
        layout.addLayout(run_row)

        # ── 错误条 ──
        self.error_bar = QLabel("")
        self.error_bar.setStyleSheet("color: #ff4d4f; padding: 4px 8px; background: #fff2f0; border-radius: 3px;")
        self.error_bar.setVisible(False)
        self.error_bar.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.error_bar)

        # ── 解耦结果表 (10列: 传感器/类型/Ke1/Ke2/KT1/KT2/ε_mean/ε_std/ε_range/评级) ──
        self.result_table = QTableWidget(0, 10)
        self.result_table.setHorizontalHeaderLabels([
            "传感器", "类型", "Ke1\n(pm/με)", "Ke2\n(pm/με)",
            "KT1\n(pm/°C)", "KT2\n(pm/°C)",
            "ε_mean\n(με)", "ε_std\n(με)", "ε_range\n(με)", "评级",
        ])
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.result_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.result_table.horizontalHeader().setStyleSheet("QHeaderView::section { font-weight: bold; }")
        self.result_table.setSortingEnabled(True)
        layout.addWidget(self.result_table, stretch=1)

        # ── 图表分析按钮 (启用条件: 解耦已跑完) ──
        self.charts_btn = create_button("📊 图表分析…", self._open_charts, "secondary",
                                         tooltip="打开独立对话框展示诊断四联图")
        self.charts_btn.setEnabled(False)
        layout.addWidget(self.charts_btn)

        # ── 导出 ──
        exp_row = QHBoxLayout()
        exp_row.addWidget(create_button("导出 Excel", self._export_excel, "success"))
        exp_row.addWidget(create_button("生成 Word 报告", self._export_word, "secondary"))
        exp_row.addStretch()
        exp_row.addWidget(create_button("确定", self.accept, "primary"))
        layout.addLayout(exp_row)

    def _on_run(self):
        self.run_btn.setText("运行中..."); self.run_btn.setEnabled(False)
        # Collect Ke coefficients
        for i in range(self.coef_table.rowCount()):
            pfx = self.coef_table.item(i, 0).text().strip()
            try:
                ke1 = float(self.coef_table.item(i, 1).text().strip())
                ke2 = float(self.coef_table.item(i, 2).text().strip())
            except (ValueError, AttributeError):
                ke1 = 1.2; ke2 = 1.2
            self._coeffs[pfx] = {"Ke1": float(ke1), "Ke2": float(ke2)}

        wavelength_cols = []
        for pfx, gratings in self._groups.items():
            from ui.calibration_tab import is_valid_annotation
            for g in gratings:
                if is_valid_annotation(g.get("name", "")):
                    wavelength_cols.append(g.get("col_name", g["name"]))

        sample_s = 2.0
        time_h = np.arange(len(self._df)) * sample_s / 3600.0

        from ui.calibration_tab import PhaseBWorker
        self._worker = PhaseBWorker(
            self._df, time_h, wavelength_cols, self._groups,
            self._phase_a_result["S_eff"], self._coeffs, sample_s,
        )
        self._worker.progress.connect(lambda m: self.progress_label.setText(m))
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, result):
        self._last_result = result
        sensors = result.get("sensors", {})
        S_eff = self._phase_a_result.get("S_eff", {})

        # 渲染结果表
        self._render_result_table(sensors, S_eff)

        self.charts_btn.setEnabled(True)
        self.run_btn.setText("✓ 完成")
        self.run_btn.setEnabled(True)

    def _render_result_table(self, sensors: dict, S_eff: dict, saved_ratings: dict = None):
        """渲染 10 列解耦结果表 — 仅双栅传感器 (单栅跳过，不进解耦)

        Phase B 解耦基于 2×2 矩阵求逆，单栅传感器无第二个光栅参与
        解耦，因此不在此表中显示。单栅的 S_eff 温度系数保留在 Phase A，
        不在 Phase B 处理。
        """
        saved_ratings = saved_ratings or {}
        self.result_table.setSortingEnabled(False)

        # ── 过滤: 仅双栅传感器 ──
        dual_sensors = {k: v for k, v in sensors.items() if not v.get("single_grating", False)}
        single_names = [k for k, v in sensors.items() if v.get("single_grating", False)]

        self.result_table.setRowCount(len(dual_sensors))
        row = 0
        for s_name, r in dual_sensors.items():
            eps = np.asarray(r.get("eps_corr", np.empty(0)), dtype=np.float64)

            kt1 = float("nan"); kt2 = float("nan")
            gratings = self._groups.get(s_name, [])
            for i, g in enumerate(gratings):
                cname = g.get("col_name", g.get("name", ""))
                sval = S_eff.get(cname, {}).get("slope", float("nan"))
                if i == 0: kt1 = sval
                elif i == 1: kt2 = sval

            ke1 = self._coeffs.get(s_name, {}).get("Ke1", float("nan"))
            ke2 = self._coeffs.get(s_name, {}).get("Ke2", float("nan"))

            e_mean = float(np.nanmean(eps)) if len(eps) > 0 else float("nan")
            e_std  = float(np.nanstd(eps))  if len(eps) > 0 else float("nan")
            e_range = float(np.nanmax(eps) - np.nanmin(eps)) if len(eps) > 0 else float("nan")

            if not np.isnan(e_std):
                if e_std <= 30:
                    rating, bg = "优", "#E8F5E9"
                elif e_std <= 60:
                    rating, bg = "良", "#FFF8E1"
                else:
                    rating, bg = "差", "#FFEBEE"
            else:
                rating, bg = saved_ratings.get(s_name, "—"), "#FFFFFF"

            dash = lambda v: "—" if (isinstance(v, float) and np.isnan(v)) else f"{v:.2f}"

            cells = [
                s_name,
                "双栅",
                dash(ke1), dash(ke2),
                dash(kt1), dash(kt2),
                dash(e_mean), dash(e_std), dash(e_range),
                rating,
            ]
            for j, val in enumerate(cells):
                item = QTableWidgetItem(str(val))
                if j == 9:
                    item.setBackground(QColor(bg))
                self.result_table.setItem(row, j, item)
            row += 1
        self.result_table.setSortingEnabled(True)

        # ── 单栅排除提示 ──
        if single_names:
            display_names = ", ".join(single_names)
            label = getattr(self, '_single_excluded_label', None)
            if label is None:
                label = QLabel("")
                label.setStyleSheet("color: #666; font-size: 11px; padding: 4px 0;")
                self._single_excluded_label = label
                # 插入到 result_table 下方
                idx = self.layout().indexOf(self.result_table)
                if idx >= 0:
                    self.layout().insertWidget(idx + 1, label)
            label.setText(f"ⓘ 单栅传感器 ({display_names}) 不进 Phase B 解耦，温度系数 S_eff 保留在 Phase A 结果中")

    def _write_state_to_main_page(self):
        """写回主 Tab 状态 (被 accept + reject 共用)"""
        tp = self._get_temp_page()
        if tp:
            ke_table = {}
            for i in range(self.coef_table.rowCount()):
                pfx = self.coef_table.item(i, 0).text().strip()
                try:
                    k1 = float(self.coef_table.item(i, 1).text().strip())
                    k2 = float(self.coef_table.item(i, 2).text().strip())
                except (ValueError, AttributeError):
                    k1, k2 = 1.2, 1.2
                ke_table[pfx] = {"Ke1": k1, "Ke2": k2}

            decoupling = {}
            if self._last_result:
                for s_name, r in self._last_result.get("sensors", {}).items():
                    if r.get("single_grating", False):
                        continue  # 单栅不参与解耦，不写入 decoupling_results
                    eps = np.asarray(r.get("eps_corr", np.empty(0)), dtype=np.float64)
                    e_mean = float(np.nanmean(eps)) if len(eps) > 0 else float("nan")
                    e_std  = float(np.nanstd(eps)) if len(eps) > 0 else float("nan")
                    e_range = float(np.nanmax(eps) - np.nanmin(eps)) if len(eps) > 0 else float("nan")
                    rating = "优" if (not np.isnan(e_std) and e_std <= 30) else (
                        "良" if (not np.isnan(e_std) and e_std <= 60) else "差")
                    decoupling[s_name] = {
                        "e_mean": e_mean, "e_std": e_std, "e_range": e_range, "rating": rating,
                    }

            tp._phase_b_result = self._last_result
            tp._phase_b_state = {
                "ke_table": ke_table,
                "decoupling_results": decoupling,
            }

    def accept(self):
        self._write_state_to_main_page()
        super().accept()

    def reject(self):
        self._write_state_to_main_page()
        super().reject()

    def _open_charts(self):
        print("[B1] charts btn clicked")
        # ── lazy 重算: _last_result 为 None 时现场解耦 ──
        if not self._last_result and self._df is not None and self._coeffs and self._phase_a_result:
            print("[B2] _last_result is None, triggering lazy decouple...")
            import time as _time
            from py.calibration.temperature_calibration import compute_dL, decouple
            S_eff_result = self._phase_a_result.get("S_eff", {})

            # 构建波长列 + 时间轴
            wavelength_cols = []
            for pfx, gratings in self._groups.items():
                for g in gratings:
                    cname = g.get("col_name", g.get("name", ""))
                    if cname and cname in self._df.columns:
                        wavelength_cols.append(cname)
            n = len(self._df)
            time_h = np.arange(n) * 2.0 / 3600.0

            # 解耦 (同步，复用 PhaseBWorker 核心逻辑)
            df_dL, _ = compute_dL(self._df.copy(), wavelength_cols)
            sensors = {}
            for pfx, gratings in self._groups.items():
                if len(gratings) < 2:
                    continue
                wcol1 = gratings[0].get("col_name", gratings[0]["name"])
                wcol2 = gratings[1].get("col_name", gratings[1]["name"])
                S1 = S_eff_result.get(wcol1, {}).get("slope", float("nan"))
                S2 = S_eff_result.get(wcol2, {}).get("slope", float("nan"))
                T_base = (S_eff_result.get(wcol1, {}).get("T_base", 0) +
                          S_eff_result.get(wcol2, {}).get("T_base", 0)) / 2.0
                coefs = self._coeffs.get(pfx, {})
                Ke1 = coefs.get("Ke1", 1.2); Ke2 = coefs.get("Ke2", 1.2)
                try:
                    dl1 = df_dL[f"{wcol1}_d"].values
                    dl2 = df_dL[f"{wcol2}_d"].values
                except KeyError:
                    continue
                eps_corr, dT_corr = decouple(dl1, dl2, Ke1, S1, Ke2, S2)
                T_abs = dT_corr + T_base
                sensors[pfx] = {
                    "single_grating": False,
                    "eps_corr": eps_corr, "dT_corr": dT_corr, "T_abs": T_abs,
                    "S1": S1, "S2": S2, "T_base": T_base,
                }
            self._last_result = {"sensors": sensors, "time_h": time_h,
                                  "df": self._df}
            print(f"[B2] lazy decouple done: {len(sensors)} sensors")

        if not self._last_result:
            QMessageBox.warning(self, "无数据", "缺少必要数据，无法绘图。请先运行解耦分析。")
            return

        print(f"[B3] about to construct PhaseBChartsDialog")
        if "time_h" not in self._last_result:
            n = self._df.shape[0] if self._df is not None and not self._df.empty else 100
            self._last_result["time_h"] = np.arange(n) * 2.0 / 3600.0
        from ui.widgets.charts_dialog import PhaseBChartsDialog
        dlg = PhaseBChartsDialog(
            self._last_result, df=self._df,
            annotation_groups=self._groups, parent=self,
        )
        print("[B4] PhaseBChartsDialog constructed")
        dlg.exec()

    def _on_error(self, msg):
        self.run_btn.setText("▶ 运行解耦分析"); self.run_btn.setEnabled(True)
        self.error_bar.setText(f"❌ {msg[:500]}"); self.error_bar.setVisible(True)

    def _export_excel(self):
        if not self._last_result: return
        path, _ = QFileDialog.getSaveFileName(self, "导出", "温度标定结果.xlsx", "Excel (*.xlsx)")
        if not path: return
        from py.calibration.export_utils import export_temperature_excel
        ra = self._phase_a_result
        rb = self._last_result
        sample_s = 2.0
        time_h = np.arange(len(self._df)) * sample_s / 3600.0
        export_temperature_excel(ra["df"], time_h, rb["sensors"], ra["plateaus"], ra["S_eff"], path)
        QMessageBox.information(self, "完成", f"已保存: {path}")

    def _export_word(self):
        if not self._last_result: return
        from py.report_builder.word_builder import WordBuilder
        from py.report_builder.models import WordReport, WordSection
        ra = self._phase_a_result
        rb = self._last_result
        lines = ["## 温度标定结果\n"]
        for dc, s in ra.get("S_eff", {}).items():
            lines.append(f"{dc.replace('_d','')}: S_eff={s['slope']:.2f}")
        for s_name, r in rb.get("sensors", {}).items():
            if not r.get("single_grating"):
                lines.append(f"[{s_name}] 修正 e_std={np.nanstd(r.get('eps_corr',[0])):.1f} με")
        report = WordReport(title="温度标定报告", date=datetime.now().strftime("%Y-%m-%d"),
                            sections=[WordSection(heading="结果", content_paragraphs=lines)])
        doc = WordBuilder().build(report)
        path, _ = QFileDialog.getSaveFileName(self, "保存", "温度标定报告.docx", "Word (*.docx)")
        if path:
            with open(path, "wb") as f: f.write(doc)
            QMessageBox.information(self, "完成", f"已保存: {path}")


# ═══════════════════════════════════════════════════════════════════════
# 按钮禁用/启用视觉样式
# ═══════════════════════════════════════════════════════════════════════

_BTN_STYLE_DISABLED = (
    "QPushButton {"
    "  background-color: #E0E0E0; color: #909090;"
    "  border: 1px solid #CCC; border-radius: 4px;"
    "  padding: 8px 18px; font-size: 14px;"
    "}"
)

_BTN_STYLE_ENABLED = (
    "QPushButton {"
    "  background-color: #1890ff; color: white;"
    "  border: 1px solid #1890ff; border-radius: 4px;"
    "  padding: 8px 18px; font-size: 14px;"
    "}"
    "QPushButton:hover { background-color: #40a9ff; }"
    "QPushButton:pressed { background-color: #096dd9; }"
)


# ═══════════════════════════════════════════════════════════════════════
# 温度标定子页 (迭代 3: 对话框化)
# ═══════════════════════════════════════════════════════════════════════

class TemperatureCalibrationPage(QWidget):
    """温度标定 — 主页面仅 文件加载 + 状态卡 + 两个阶段按钮"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loaded_df = None
        self._annotation_dict = {}
        self._annotation_groups = {}
        self._annotation_meta = {}
        self._detection_params = {
            "rolling_window": 25, "std_percentile": 45.0,
            "min_plateau_samples": 180, "head_trim_ratio": 0.70,
            "sample_interval_s": 2.0, "hold_time_min": 6.0,
        }
        self._time_col_idx = None
        self._phase_a_done = False
        self._last_result = None  # Phase A result
        self._phase_a_state = {}  # 关闭后保留 Phase A UI 状态
        self._phase_b_state = {}  # 关闭后保留 Phase B UI 状态
        self._phase_b_result = None  # Phase B 解耦结果缓存
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── 文件选择器 (保持) ──
        file_group, file_layout = create_form_group("数据文件 (独立加载，暗号识别)")
        row = QHBoxLayout()
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setReadOnly(True)
        self.file_path_edit.setPlaceholderText("选择连续波长文件...")
        row.addWidget(self.file_path_edit)
        row.addWidget(create_button("浏览...", self._browse_file, "secondary"))
        file_layout.addLayout(row)
        enc_row, self.enc_combo = create_labeled_combo("编码:", ["utf-8", "gb18030", "gbk", "latin-1"])
        sep_row, self.sep_edit = create_labeled_input("分隔符:", "\\t")
        file_layout.addLayout(enc_row)
        file_layout.addLayout(sep_row)
        load_row = QHBoxLayout()
        load_row.addWidget(create_button("加载并解析", self._load_and_parse, "primary"))
        self.file_info_label = create_info_label("")
        load_row.addWidget(self.file_info_label)
        file_layout.addLayout(load_row)
        layout.addWidget(file_group)

        # ── 状态卡 ──
        self.status_card = QLabel("未加载文件")
        self.status_card.setStyleSheet(
            "color: #333; padding: 10px 12px; background: #fafafa;"
            " border: 1px solid #e8e8e8; border-radius: 4px; font-size: 13px;"
        )
        self.status_card.setWordWrap(True)
        layout.addWidget(self.status_card)

        # ── 参数文件管理按钮 ──
        profile_row = QHBoxLayout()
        profile_row.addWidget(create_button("💾 保存配置", self._save_profile, "secondary",
            tooltip="保存当前全部标定参数到文件"))
        profile_row.addWidget(create_button("📂 加载配置", self._load_profile, "secondary",
            tooltip="从文件恢复标定参数"))
        profile_row.addWidget(create_button("📋 配置管理…", self._manage_profiles, "secondary",
            tooltip="查看/删除/重命名已保存的配置"))
        profile_row.addStretch()
        layout.addLayout(profile_row)

        # ── 阶段 A 按钮 ──
        self.btn_phase_a = QPushButton("阶段 A: 解析温度系数…")
        self.btn_phase_a.setStyleSheet(_BTN_STYLE_DISABLED)
        self.btn_phase_a.setCursor(Qt.CursorShape.ForbiddenCursor)
        self.btn_phase_a.setEnabled(False)
        self.btn_phase_a.setToolTip("请先加载数据文件")
        self.btn_phase_a.clicked.connect(self._open_phase_a)
        layout.addWidget(self.btn_phase_a)

        # ── 阶段 B 按钮 ──
        self.btn_phase_b = QPushButton("阶段 B: 分析数据…")
        self.btn_phase_b.setStyleSheet(_BTN_STYLE_DISABLED)
        self.btn_phase_b.setCursor(Qt.CursorShape.ForbiddenCursor)
        self.btn_phase_b.setEnabled(False)
        self.btn_phase_b.setToolTip("请先完成阶段 A")
        self.btn_phase_b.clicked.connect(self._open_phase_b)
        layout.addWidget(self.btn_phase_b)

        layout.addStretch()

    # ── 文件操作 ──

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择连续波长文件", "",
            "数据文件 (*.txt *.csv *.dat);;所有文件 (*.*)"
        )
        if path:
            self.file_path_edit.setText(path)

    def _load_and_parse(self):
        path = self.file_path_edit.text().strip()
        if not path or not os.path.isfile(path):
            self.file_info_label.setText("⚠ 文件不存在"); return

        try:
            from utils.file_parser import parse_enlight_file, detect_numeric_wavelength_columns

            df, annotation, meta = parse_enlight_file(path)
            self._loaded_df = df
            self._annotation_dict = annotation
            self._annotation_meta = meta
            # 新文件 → 清空旧状态 (避免旧暗号污染新文件)
            self._phase_a_state = {}
            self._phase_b_state = {}

            n_rows = len(df)
            fmt = meta.get("format", "unknown")
            skipped = meta.get("num_meta_skipped", 0)

            # ── 列计数: 始终用 detect_numeric_wavelength_columns ──
            num_cols, wave_cols = detect_numeric_wavelength_columns(df)
            self._num_cols = num_cols
            self._wave_cols = wave_cols

            # ── 暗号解析 ──
            signal_idx, data_cols, time_idx = _parse_annotation_row(df)
            self._time_col_idx = time_idx

            if data_cols:
                self._annotation_groups = _group_annotations_by_prefix(data_cols, df)
                legal_cnt, placeholder_cnt = _count_annotations_from_data_cols(data_cols)
            else:
                legal_cnt, placeholder_cnt = _count_annotations(annotation, wave_cols)
                if annotation and wave_cols:
                    col_list = list(df.columns)
                    dcf = {}
                    for col_name, name in annotation.items():
                        try:
                            idx_val = col_list.index(col_name)
                            s = str(name).strip().strip("'\"'\"'\"")
                            if s and '-' in s:
                                dcf[idx_val] = s
                        except ValueError:
                            pass
                    self._annotation_groups = _group_annotations_by_prefix(dcf, df) if dcf else {}
                else:
                    self._annotation_groups = {}

            # ── 主页门控: 文件加载成功后按钮按 df 状态亮 ──
            df_ready = self._loaded_df is not None and not self._loaded_df.empty
            if df_ready:
                self.btn_phase_a.setStyleSheet(_BTN_STYLE_ENABLED)
                self.btn_phase_a.setCursor(Qt.CursorShape.PointingHandCursor)
                self.btn_phase_a.setEnabled(True)
                self.btn_phase_a.setToolTip("打开阶段 A 对话框 (含暗号补填、温度范围、运行解析)")
            else:
                self.btn_phase_a.setStyleSheet(_BTN_STYLE_DISABLED)
                self.btn_phase_a.setCursor(Qt.CursorShape.ForbiddenCursor)
                self.btn_phase_a.setEnabled(False)
                self.btn_phase_a.setToolTip("数据加载异常，请重新加载文件")

            # ── 状态卡: 用实测列计数 ──
            parts = _build_annotation_info_parts(
                self._annotation_groups, legal_cnt, placeholder_cnt, fmt, skipped,
                num_cols, wave_cols,
            )
            parts.insert(0, f"📁 {os.path.basename(path)} ({n_rows} 行)")
            self.status_card.setText(" | ".join(parts))

            self.file_info_label.setText(f"✅ 已加载 {n_rows} 行 × {len(df.columns)} 列")
        except Exception as e:
            self.file_info_label.setText(f"❌ 加载失败: {e}")
            import traceback; traceback.print_exc()

    # ── 参数文件管理 ──

    def _save_profile(self):
        """保存项目配置 (温度段 + 应变段)"""
        from py.calibration.project_config import ProjectConfigManager
        ctw = self._get_cal_tab_widget()
        sp = ctw.strain_page if ctw is not None else None
        pc = ProjectConfigManager.capture(self, sp)

        default_name = os.path.splitext(os.path.basename(
            self.file_path_edit.text() or "calibration"
        ))[0] + "_" + datetime.now().strftime("%Y%m%d_%H%M")

        name, ok = QInputDialog.getText(
            self, "保存项目配置", "配置名称:", text=default_name)
        if not ok or not name.strip():
            return
        name = name.strip()
        pc.name = name

        try:
            path = ProjectConfigManager.save(pc)
            # 同步到 CalibrationTabWidget
            if ctw is not None:
                ctw.project_config = pc
            QMessageBox.information(self, "已保存",
                f"项目配置已保存到:\n{path}\n\n"
                f"温度段: {'有' if pc.temperature else '无'}  |  "
                f"应变传感器: {len(pc.strain)} 个")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def _get_cal_tab_widget(self):
        """向上搜索 CalibrationTabWidget (最多3层)"""
        w: object = self
        for _ in range(3):
            w = getattr(w, 'parent', lambda: None)()
            if w is not None and hasattr(w, 'project_config'):
                return w
        return None

    def _load_profile(self):
        """加载项目配置，回填温度段 + 应变段"""
        from py.calibration.project_config import ProjectConfigManager, PROFILES_DIR

        projects = ProjectConfigManager.list_all()
        if not projects:
            QMessageBox.information(self, "无配置", "还没有保存的项目配置文件。")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("加载项目配置"); dlg.resize(650, 400)
        layout = QVBoxLayout(dlg)

        lst = QListWidget()
        for p in projects:
            key = p.get("_key", p.get("name", "?"))
            created = str(p.get("created_at", ""))[:16]
            n_strain = len(p.get("strain", {}) or {})
            fpath = (p.get("temperature") or {}).get("file_path", "") if isinstance(p.get("temperature"), dict) else ""
            lst.addItem(f"{key}\n  创建: {created}  |  应变传感器: {n_strain}  |  源文件: {fpath}")
        layout.addWidget(lst)

        btn_row = QHBoxLayout()
        load_btn = create_button("加载选中", dlg.accept, "primary")
        btn_row.addWidget(load_btn)
        btn_row.addWidget(create_button("取消", dlg.reject, "secondary"))
        layout.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted or lst.currentRow() < 0:
            return

        proj_dict = projects[lst.currentRow()]
        name = proj_dict.get("_key", proj_dict.get("name", ""))

        if self._phase_a_state or self._phase_b_state:
            r = QMessageBox.question(self, "确认加载",
                "当前未保存的修改可能丢失，是否继续加载？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return

        try:
            pc = ProjectConfigManager.load(name)

            ctw = self._get_cal_tab_widget()
            sp = ctw.strain_page if ctw is not None else None
            ProjectConfigManager.restore(pc, self, sp)

            QMessageBox.information(self, "已加载",
                f"项目 '{name}' 已恢复。\n\n"
                f"温度段: {'有' if pc.temperature else '无'}  |  "
                f"应变传感器: {len(pc.strain)} 个")
        except Exception as e:
            QMessageBox.critical(self, "加载失败", str(e))

    def _manage_profiles(self):
        """配置管理: 查看/删除已保存的项目配置"""
        from py.calibration.project_config import ProjectConfigManager

        projects = ProjectConfigManager.list_all()
        if not projects:
            QMessageBox.information(self, "无配置", "还没有保存的项目配置文件。")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("配置管理 — 项目配置"); dlg.resize(750, 450)
        layout = QVBoxLayout(dlg)

        tbl = QTableWidget(len(projects), 4)
        tbl.setHorizontalHeaderLabels(["名称", "创建时间", "传感器数", "操作"])
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for i, p in enumerate(projects):
            key = p.get("_key", p.get("name", "?"))
            tbl.setItem(i, 0, QTableWidgetItem(key))
            tbl.setItem(i, 1, QTableWidgetItem(str(p.get("created_at", ""))[:16]))
            n_strain = len(p.get("strain", {}) or {})
            n_s_eff = len((p.get("temperature", {}) or {}).get("s_eff_results", {}) or {})
            tbl.setItem(i, 2, QTableWidgetItem(f"温度S_eff:{n_s_eff} + 应变:{n_strain}"))

            del_btn = QPushButton("删除")
            name = key
            del_btn.clicked.connect(lambda checked, n=name: self._delete_project_and_refresh(n, dlg, tbl))
            tbl.setCellWidget(i, 3, del_btn)
        layout.addWidget(tbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(create_button("关闭", dlg.accept, "primary"))
        layout.addLayout(btn_row)

        dlg.exec()

    def _delete_project_and_refresh(self, name: str, dlg, tbl):
        from py.calibration.project_config import ProjectConfigManager
        r = QMessageBox.question(self, "确认删除", f"确定删除项目配置 '{name}'？")
        if r == QMessageBox.StandardButton.Yes:
            ProjectConfigManager.delete(name)
            dlg.accept()
            QMessageBox.information(self, "已删除", f"项目配置 '{name}' 已删除。")

    # ── 对话框 ──

    def _open_phase_a(self):
        dlg = PhaseADialog(
            self._loaded_df, self._annotation_dict, self._annotation_groups,
            self._detection_params, self._time_col_idx, self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            result = dlg.get_result()
            if result:
                self._last_result = result
                self._phase_a_done = True
                S_eff = result.get("S_eff", {})
                self.status_card.setText(
                    self.status_card.text() + " | ✅ 阶段A完成 | " +
                    f"S_eff: {len(S_eff)} 个光栅"
                )
                # 启用阶段 B
                self.btn_phase_b.setStyleSheet(_BTN_STYLE_ENABLED)
                self.btn_phase_b.setCursor(Qt.CursorShape.PointingHandCursor)
                self.btn_phase_b.setEnabled(True)
                self.btn_phase_b.setToolTip("打开阶段 B 对话框")

    def _open_phase_b(self):
        if not self._phase_a_done:
            return
        dlg = PhaseBDialog(
            self._loaded_df, self._annotation_groups, self._last_result, self,
        )
        dlg.exec()
class StrainCalibrationPage(QWidget):
    """应变标定 — 参数/读数对话框 + ChartPanel 结果区"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None; self._last_result = None
        self._config = {
            "gauge_length_mm": 80.0, "mode": "tension_only",
            "n_cycles": 1, "grating_kind": "single",
            "anchored_grating": None,
        }
        self._levels = self._generate_default_levels()
        self._readings = {}
        self._grating_map: dict[str, str] = {}   # {"G1": "A1-W1", "G2": "A1-W2"}
        self._grating_map_version: int = 0           # ★ 每次 _extract_table_data 写入递增，防缓存污染
        self._grating_map_fresh: bool = False         # ★ True = _extract_table_data 刚写入 (用户编辑态)
        self._ke_results: dict[str, float] = {}  # {"Ke1": 1.23, "Ke2": 0.98}
        self._dialog_table = None  # 每次 _open_readings 新建
        self._annotation_combos: dict[int, QComboBox] = {}  # grating_index → ComboBox
        # ── 多传感器列表管理 ──
        self._strain_configs: dict[str, object] = {}   # sensor_name → StrainSubConfig (镜像 project.strain)
        self._current_sensor: str | None = None          # 当前选中传感器名
        self._working_result: object | None = None        # 当前分析结果 (尚未加入列表)
        self._project_dirty: bool = False                 # 项目有未保存更改
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── 顶部按钮行 ──
        top_row = QHBoxLayout()
        self.config_btn = create_button(
            f"⚙ 标定参数: {self._config_label()}", self._open_config, "secondary"
        )
        top_row.addWidget(self.config_btn)
        self.readings_btn = create_button("📋 读数录入...", self._open_readings, "secondary")
        top_row.addWidget(self.readings_btn)
        top_row.addStretch()
        layout.addLayout(top_row)

        # ── 项目配置管理 ──
        proj_row = QHBoxLayout()
        proj_row.addWidget(create_button("💾 保存项目", self._save_project, "secondary",
            tooltip="保存温度+应变全部标定参数"))
        proj_row.addWidget(create_button("📂 加载项目", self._load_project, "secondary",
            tooltip="从文件恢复温度+应变标定参数"))
        self._dirty_label = QLabel("")
        self._dirty_label.setStyleSheet("color: #fa8c16; font-weight: bold; padding: 2px 6px;")
        proj_row.addWidget(self._dirty_label)
        proj_row.addStretch()
        layout.addLayout(proj_row)

        # ── 提交 + 加入列表 ──
        analyze_row = QHBoxLayout()
        self.analyze_btn = create_button("▶ 提交并分析", self._run_analysis, "primary")
        analyze_row.addWidget(self.analyze_btn)
        self.commit_list_btn = create_button("📋 加入已标定列表", self._commit_to_list, "success")
        self.commit_list_btn.setEnabled(False)
        analyze_row.addWidget(self.commit_list_btn)
        analyze_row.addStretch()
        layout.addLayout(analyze_row)
        self.strain_progress = create_info_label("")
        layout.addWidget(self.strain_progress)

        # ── 结果区: 左右分栏 (传感器列表 + 图表) ──
        result_group, result_layout = create_form_group("分析结果")
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 左: 传感器列表面板 ──
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.addWidget(QLabel("📋 已标定传感器"))
        self.sensor_list = QListWidget()
        self.sensor_list.setMinimumWidth(200)
        self.sensor_list.currentRowChanged.connect(self._on_sensor_selected)
        left_layout.addWidget(self.sensor_list, stretch=1)

        list_btn_row = QHBoxLayout()
        list_btn_row.addWidget(create_button("+ 新建标定", self._new_calibration, "secondary"))
        self.delete_sensor_btn = create_button("🗑 删除", self._delete_sensor, "secondary")
        self.delete_sensor_btn.setEnabled(False)
        list_btn_row.addWidget(self.delete_sensor_btn)
        left_layout.addLayout(list_btn_row)

        splitter.addWidget(left_panel)

        # ── 右: 现有结果标签页 ──
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.strain_result_tabs = QTabWidget()
        self.strain_result_tabs.setMinimumHeight(450)

        self.curve_panel = ChartPanel(figsize=(8, 4))
        self.strain_result_tabs.addTab(self.curve_panel, "Δλ-ε 标定曲线")
        self.resid_panel = ChartPanel(figsize=(8, 4))
        self.strain_result_tabs.addTab(self.resid_panel, "线性残差")
        self.bar_panel = ChartPanel(figsize=(8, 4))
        self.strain_result_tabs.addTab(self.bar_panel, "核心指标")
        self.strain_result_text = QTextEdit(); self.strain_result_text.setReadOnly(True)
        self.strain_result_tabs.addTab(self.strain_result_text, "结果数据")

        right_layout.addWidget(self.strain_result_tabs)

        splitter.addWidget(right_panel)
        splitter.setSizes([220, 800])  # 初始比例: 左侧约 1/4
        result_layout.addWidget(splitter)
        layout.addWidget(result_group)

        # ── 操作按钮 ──
        op_row = QHBoxLayout()
        self.apply_coef_btn = create_button("📌 应用全部系数到温度标定", self._apply_coefficients, "success")
        self.apply_coef_btn.setEnabled(False)
        op_row.addWidget(self.apply_coef_btn)
        self.export_se_btn = create_button("导出 Excel", self._export_strain_excel, "secondary")
        self.export_se_btn.setEnabled(False)
        op_row.addWidget(self.export_se_btn)
        self.export_sw_btn = create_button("生成检测报告", self._on_generate_report, "secondary")
        self.export_sw_btn.setEnabled(False)
        op_row.addWidget(self.export_sw_btn)
        op_row.addStretch()
        layout.addLayout(op_row)
        layout.addStretch()

    def _config_label(self):
        c = self._config
        mode_name = "只张拉" if c["mode"] == "tension_only" else "张拉+退回"
        grating_name = {"single": "单栅", "dual_anchored": "双栅-锚固", "dual_both": "双栅-双工作"}[c["grating_kind"]]
        return f"{c['gauge_length_mm']:.0f}mm, {mode_name}, {c['n_cycles']}循环, {grating_name}"

    # ── 读数录入表 (每次 _open_readings 新建独立 QTableWidget) ──

    # _rebuild_table 已删除 — 无持久表格，每次对话框独立创建

    def _generate_default_levels(self, n: int = 11) -> list[float]:
        """根据标距生成默认位移等级列表（含零点基线行）。

        位移 = i × L / 10000  (i=0→0.000 即 0με, i=10→0.080 即 1000με for L=80)
        标距 80mm → [0.000, 0.008, 0.016, ..., 0.080]  共 11 行
        """
        L = self._config["gauge_length_mm"]
        return [i * L / 10000.0 for i in range(n)]

    def _on_strain_cell_changed(self, row: int, col: int):
        """位移列(col=0)编辑后，自动计算理论应变列(col=1)

        Row 0 = 备注行，不触发自动计算。
        """
        if row < 1 or col != 0:  # 跳过备注行
            return
        table = self._dialog_table
        if table is None:
            return
        item = table.item(row, col)
        if item is None:
            return
        try:
            disp_mm = float(item.text())
        except (ValueError, TypeError):
            return
        gauge = self._config["gauge_length_mm"]
        eps = disp_mm / gauge * 1e6 if gauge > 0 else 0.0
        strain_item = QTableWidgetItem(f"{eps:.2f}")
        strain_item.setFlags(strain_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.blockSignals(True)
        try:
            table.setItem(row, 1, strain_item)
        finally:
            table.blockSignals(False)

    def _get_temp_page(self):
        """从兄弟页获取温度标定页引用。

        走 parent 链 (StrainCalibrationPage → QTabWidget → CalibrationTabWidget)
        浅层嵌套，parent 指针稳定，不走 window() 全树搜索。
        """
        w = self.parent()   # QTabWidget (sub_tabs)
        if w is not None:
            w2 = w.parent()  # CalibrationTabWidget
            if w2 is not None and hasattr(w2, 'temp_page'):
                return w2.temp_page
        # 回退: 通过 window() 搜索 (仅当 parent 链不可用)
        win = self.window()
        if win is not None and hasattr(win, 'findChildren'):
            for child in win.findChildren(QWidget):
                if hasattr(child, '_annotation_groups') and hasattr(child, '_loaded_df'):
                    return child
        return None

    def _get_cal_tab_widget(self):
        """获取 CalibrationTabWidget (持有 project_config)。

        向上遍历 parent 链，最多 3 层 (strain_page → sub_tabs → ctw 或 strain_page → ctw)。
        """
        w: object = self
        for _ in range(3):
            w = getattr(w, 'parent', lambda: None)()
            if w is not None and hasattr(w, 'project_config'):
                return w
        return None

    def _get_or_create_project_config(self):
        """获取或创建 ProjectConfig 实例。

        优先从 CalibrationTabWidget.project_config 获取，
        若不存在则创建并缓存到 self._project_config。
        """
        from py.calibration.project_config import ProjectConfig
        # 先查缓存在页面上的
        cached = getattr(self, '_project_config', None)
        if cached is not None:
            return cached
        ctw = self._get_cal_tab_widget()
        if ctw is not None and ctw.project_config is not None:
            self._project_config = ctw.project_config
            return ctw.project_config
        # 创建新项目
        now = datetime.now().isoformat()
        pc = ProjectConfig(name=f"应变标定_{now[:16]}", created_at=now, last_modified=now)
        self._project_config = pc
        if ctw is not None:
            ctw.project_config = pc
        return pc

    def _get_grating_sources(self) -> dict[str, str]:
        """返回 {光栅标识: 暗号标签} 映射，如 {'G1': 'C2-1', 'G2': 'C2-2'}。

        单一真源 — 传感器名解析 + 图表图例 + 结果文本均从此读取。
        数据由 _extract_table_data 在对话框关闭时直接写入 self._grating_map。
        """
        return dict(self._grating_map)

    def _get_grating_label(self, grating_index: int) -> str:
        """光栅显示标签。优先暗号名 (如 'C2-1')，无暗号回退 'G1'/'G2'。"""
        sources = self._get_grating_sources()
        key = f"G{grating_index}"
        ann = sources.get(key, "").strip()
        return ann if ann else key

    def _parse_sensor_from_grating_map(self) -> str:
        """从 _get_grating_sources() 解析传感器名。

        G1→'C2-1', G2→'C2-2' → 返回 'C2'。
        前缀按最后一个 '-' 或 '_' 切分 (rsplit)，兼容 'C2-1'/'C2_1'/'A1-W1' 等。
        要求所有光栅解析出的前缀一致；不一致或空则返回空字符串。
        """
        sources = self._get_grating_sources()
        if not sources:
            return ""
        prefixes: set[str] = set()
        for ann in sources.values():
            ann = ann.strip()
            if not ann:
                continue
            if '-' in ann:
                prefixes.add(ann.rsplit('-', 1)[0])
            elif '_' in ann:
                prefixes.add(ann.rsplit('_', 1)[0])
            else:
                prefixes.add(ann)  # 无分隔符时整串即为传感器名
        if len(prefixes) == 1:
            return next(iter(prefixes))
        return ""  # 多前缀不一致 → 调用方提示

    def _build_strain_subconfig(self):
        """从当前应变标定状态构造 StrainSubConfig。"""
        from py.calibration.project_config import StrainSubConfig
        kind = self._config["grating_kind"]
        sensor_mode_map = {"single": "single", "dual_anchored": "dual_anchored", "dual_both": "dual_working"}
        # 构建 readings 列表
        readings_list: list[dict] = []
        for r in range(len(self._levels)):
            row: dict = {"disp_mm": self._levels[r]}
            row["eps_theory"] = self._levels[r] / self._config["gauge_length_mm"] * 1e6 if self._config["gauge_length_mm"] > 0 else 0.0
            readings_list.append(row)
        return StrainSubConfig(
            sensor_name=self._parse_sensor_from_grating_map(),
            sensor_mode=sensor_mode_map.get(kind, "single"),
            gauge_length_mm=self._config["gauge_length_mm"],
            n_cycles=self._config["n_cycles"],
            grating_map=dict(self._grating_map),
            readings=readings_list,
            ke_results=dict(self._ke_results),
            charts_meta={},
        )

    def _is_dual_in_temperature(self) -> tuple[bool, str]:
        """检查当前传感器在温度配置中是否为双栅。

        Returns:
            (is_dual, found_name): is_dual 为 True 表示温度配置有该传感器且≥2光栅
                                   found_name 为传感器名
        """
        sensor_name = self._parse_sensor_from_grating_map()
        if not sensor_name:
            return False, ""
        tp = self._get_temp_page()
        if tp is None:
            return False, sensor_name
        groups = getattr(tp, '_annotation_groups', None) or {}
        gratings = groups.get(sensor_name, [])
        return len(gratings) >= 2, sensor_name

    def _has_temperature_data(self) -> bool:
        """温度标定是否已有数据 (暗号 + Phase A 完成)。"""
        tp = self._get_temp_page()
        if tp is None:
            return False
        df = getattr(tp, '_loaded_df', None)
        if df is None or (hasattr(df, 'empty') and df.empty):
            return False
        return getattr(tp, '_phase_a_done', False)

    def _get_temp_annotations(self) -> list[str]:
        """获取温度标定页已识别的全部暗号，供备注行下拉使用。

        只有当温度页已完成 Phase A (有暗号) 时才返回列表；
        未加载或未标注返回空列表，ComboBox 切换为自由输入模式。
        """
        tp = self._get_temp_page()
        if tp is None:
            return []
        groups = getattr(tp, '_annotation_groups', None) or {}
        codes: list[str] = []
        for _pfx, gratings in groups.items():
            for g in gratings:
                name = str(g.get("name", "")).strip()
                if is_valid_annotation(name):
                    codes.append(name)
        # 去重 + 排序
        return sorted(set(codes))

    def _grating_col_indices(self) -> dict[int, list[int]]:
        """返回 {grating_index: [column_indices]} 映射。

        基于当前 _build_table_columns 的列布局，遍历所有数据列
        (从 col=2 开始)，归入对应光栅的列索引列表。
        供备注行 ComboBox 定位 + 同光栅其余列灰显。
        """
        c = self._config
        is_return = c["mode"] == "tension_return"
        n_gratings = 1 if c["grating_kind"] == "single" else 2
        grating_cols: dict[int, list[int]] = {i + 1: [] for i in range(n_gratings)}
        col_idx = 2
        for _ci in range(c["n_cycles"]):
            for gi in range(n_gratings):
                grating_cols[gi + 1].append(col_idx)
                col_idx += 1
            if is_return:
                for gi in range(n_gratings):
                    grating_cols[gi + 1].append(col_idx)
                    col_idx += 1
        return grating_cols

    def _build_table_columns(self):
        """根据当前 config 返回列名列表。

        列顺序: 位移, 理论应变, 循环外层→方向中层→光栅内层
        例如双栅+3循环+退回:
          G1_C1_张, G2_C1_张, G1_C1_退, G2_C1_退,
          G1_C2_张, G2_C2_张, G1_C2_退, G2_C2_退,
          G1_C3_张, G2_C3_张, G1_C3_退, G2_C3_退

        注意: C{n} = 循环号 (非传感器名); 传感器名由备注行暗号下拉框确定。
        """
        c = self._config
        is_return = c["mode"] == "tension_return"
        n_gratings = 1 if c["grating_kind"] == "single" else 2
        cols = ["位移(mm)", "理论应变(με)"]
        for ci in range(c["n_cycles"]):
            for gi in range(n_gratings):
                cols.append(f"G{gi+1}_C{ci+1}_张拉(nm)")
            if is_return:
                for gi in range(n_gratings):
                    cols.append(f"G{gi+1}_C{ci+1}_退回(nm)")
        return cols

    def _populate_table(self, table):
        """从 self._levels / self._readings 填充位移、理论应变、波长读数

        Row 0 = 备注行 (annotation row)，数据从 row 1 开始。
        """
        gauge = self._config["gauge_length_mm"]
        c = self._config
        is_return = c["mode"] == "tension_return"
        n_gratings = 1 if c["grating_kind"] == "single" else 2

        table.blockSignals(True)
        try:
            for r, disp in enumerate(self._levels):
                data_row = r + 1  # 偏移: row 0 = 备注行
                # 位移
                table.setItem(data_row, 0, QTableWidgetItem(f"{disp:.3f}"))
                # 理论应变 (自动计算，不可编辑)
                eps = disp / gauge * 1e6 if gauge > 0 else 0.0
                strain_item = QTableWidgetItem(f"{eps:.2f}")
                strain_item.setFlags(strain_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(data_row, 1, strain_item)

            # 波长读数 — 列顺序: 循环外层 → 光栅内层 → 方向 (与 _build_table_columns 同序)
            col_idx = 2
            for ci in range(c["n_cycles"]):
                for gi in range(n_gratings):
                    load_vals = self._readings.get(gi + 1, {}).get(ci + 1, {}).get("load", [])
                    for r in range(len(self._levels)):
                        data_row = r + 1
                        val = load_vals[r] if r < len(load_vals) else 0.0
                        text = f"{val:.3f}" if val != 0.0 else ""
                        table.setItem(data_row, col_idx, QTableWidgetItem(text))
                    col_idx += 1
                if is_return:
                    for gi in range(n_gratings):
                        unload_vals = self._readings.get(gi + 1, {}).get(ci + 1, {}).get("unload", [])
                        for r in range(len(self._levels)):
                            data_row = r + 1
                            val = unload_vals[r] if r < len(unload_vals) else 0.0
                            text = f"{val:.3f}" if val != 0.0 else ""
                            table.setItem(data_row, col_idx, QTableWidgetItem(text))
                        col_idx += 1
        finally:
            table.blockSignals(False)

    def _extract_table_data(self, table):
        """从对话框表格提取 self._levels + self._readings + self._grating_map

        Row 0 = 备注行 (QComboBox)，数据从 row 1 开始。
        """
        c = self._config
        is_return = c["mode"] == "tension_return"
        n_gratings = 1 if c["grating_kind"] == "single" else 2
        n_rows = table.rowCount()

        # ── 提取 grating_map (row 0, QComboBox) ──
        # ★ 直接写入 self._grating_map（单一真源），使用 clear+update 保持引用稳定
        self._grating_map.clear()
        grating_map: dict[str, str] = self._grating_map  # 别名，最终写入同一个 dict
        grating_cols = self._grating_col_indices()
        for gi, col_indices in grating_cols.items():
            if not col_indices:
                continue
            widget = table.cellWidget(0, col_indices[0])
            if isinstance(widget, QComboBox):
                text = widget.currentText().strip()
                if text and is_valid_annotation(text):
                    grating_map[f"G{gi}"] = text
        self._grating_map_version += 1
        self._grating_map_fresh = True  # ★ 用户编辑态，禁止 _load_sensor_to_workspace 覆盖

        # ── 提取 levels (row 1+) ──
        levels = []
        for r in range(1, n_rows):
            item = table.item(r, 0)
            try:
                levels.append(float(item.text()) if item and item.text().strip() else 0.0)
            except ValueError:
                levels.append(0.0)

        # ── 提取 readings (row 1+, 列顺序同 _build_table_columns) ──
        readings: dict[int, dict] = {}
        col_idx = 2
        for ci in range(c["n_cycles"]):
            for gi in range(n_gratings):
                load_vals = []
                for r in range(1, n_rows):
                    item = table.item(r, col_idx)
                    try:
                        load_vals.append(float(item.text()) if item and item.text().strip() else 0.0)
                    except ValueError:
                        load_vals.append(0.0)
                readings.setdefault(gi + 1, {})[ci + 1] = {"load": load_vals}
                col_idx += 1
            if is_return:
                for gi in range(n_gratings):
                    unload_vals = []
                    for r in range(1, n_rows):
                        item = table.item(r, col_idx)
                        try:
                            unload_vals.append(float(item.text()) if item and item.text().strip() else 0.0)
                        except ValueError:
                            unload_vals.append(0.0)
                    readings[gi + 1][ci + 1]["unload"] = unload_vals
                    col_idx += 1

        return levels, readings, grating_map

    # ── 对话框 ──

    def _open_config(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("标定参数"); dlg.setSizeGripEnabled(True)
        layout = QVBoxLayout(dlg)

        gb, gl = create_form_group("标定参数")
        grid = QGridLayout()
        grid.addWidget(QLabel("标距长度 (mm):"), 0, 0)
        gauge_spin = QDoubleSpinBox(); gauge_spin.setRange(1, 10000); gauge_spin.setValue(self._config["gauge_length_mm"]); gauge_spin.setDecimals(1)
        grid.addWidget(gauge_spin, 0, 1)
        grid.addWidget(QLabel("模式:"), 0, 2)
        mode_combo = QComboBox(); mode_combo.addItems(["只张拉", "张拉+退回"])
        mode_combo.setCurrentIndex(0 if self._config["mode"] == "tension_only" else 1)
        grid.addWidget(mode_combo, 0, 3)
        grid.addWidget(QLabel("循环次数:"), 1, 0)
        cycles_spin = QSpinBox(); cycles_spin.setRange(1, 10); cycles_spin.setValue(self._config["n_cycles"])
        grid.addWidget(cycles_spin, 1, 1)
        grid.addWidget(QLabel("光栅类型:"), 1, 2)
        grating_combo = QComboBox(); grating_combo.addItems(["单栅", "双栅-一栅锚固", "双栅-双工作"])
        kind_map = {"single": 0, "dual_anchored": 1, "dual_both": 2}
        grating_combo.setCurrentIndex(kind_map.get(self._config["grating_kind"], 0))
        grid.addWidget(grating_combo, 1, 3)
        grid.addWidget(QLabel("锚固栅:"), 2, 2)
        anchored_combo = QComboBox(); anchored_combo.addItems(["光栅1", "光栅2"])
        anchored_combo.setCurrentIndex((self._config.get("anchored_grating") or 2) - 1)
        grid.addWidget(anchored_combo, 2, 3)
        gl.addLayout(grid)
        layout.addWidget(gb)

        btn_row = QHBoxLayout()
        btn_row.addWidget(create_button("确定", dlg.accept, "primary"))
        btn_row.addWidget(create_button("取消", dlg.reject, "secondary"))
        layout.addLayout(btn_row)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            old_gauge = self._config["gauge_length_mm"]
            old_kind = self._config["grating_kind"]
            new_gauge = gauge_spin.value()
            new_kind = ["single", "dual_anchored", "dual_both"][grating_combo.currentIndex()]
            self._config.update({
                "gauge_length_mm": new_gauge,
                "mode": "tension_only" if mode_combo.currentIndex() == 0 else "tension_return",
                "n_cycles": cycles_spin.value(),
                "grating_kind": new_kind,
                "anchored_grating": anchored_combo.currentIndex() + 1 if grating_combo.currentIndex() == 1 else None,
            })
            self.config_btn.setText(f"⚙ 标定参数: {self._config_label()}")
            self._readings = {}  # 列结构可能已变，清空旧读数
            if new_kind != old_kind:
                self._grating_map = {}  # 光栅数量/类型变化 → 清空旧映射
                self._grating_map_fresh = False
            if abs(new_gauge - old_gauge) > 0.001:
                self._levels = self._generate_default_levels()  # 标距变化 → 重建位移等级

    def _open_readings(self):
        """每次新建 QDialog + 独立 QTableWidget，关闭时提取数据写回。

        Row 0 = 备注行:
          - 每个光栅的第一个数据列放 QComboBox (暗号标注)
          - 同光栅其余列灰显只读
          - 下拉项: 温度标定已加载 → 温度暗号列表; 未加载 → 可编辑自由输入
        """
        cols = self._build_table_columns()
        dlg = QDialog(self)
        dlg.setWindowTitle("读数录入 — 应变标定"); dlg.resize(1000, 600); dlg.setSizeGripEnabled(True)
        layout = QVBoxLayout(dlg)

        toolbar = QHBoxLayout()
        toolbar.addWidget(create_button("+ 添加行", self._add_level, "secondary"))
        toolbar.addWidget(create_button("- 删除行", self._remove_level, "secondary"))
        toolbar.addWidget(QLabel("💡 支持 Ctrl+V 整块粘贴"))
        toolbar.addStretch()
        toolbar.addWidget(create_button("⛶ 全屏", lambda: self._toggle_readings_fs(dlg), "secondary"))
        layout.addLayout(toolbar)

        # Table = 备注行 + levels 数据行
        table = _StrainPasteTable(len(self._levels) + 1, len(cols))
        table._paste_gauge_mm = self._config["gauge_length_mm"]  # 供粘贴位移列应变计算
        table.setHorizontalHeaderLabels(cols)
        table.cellChanged.connect(self._on_strain_cell_changed)
        self._dialog_table = table
        self._populate_table(table)
        layout.addWidget(table)

        # ── 备注行 (row 0): QComboBox 标注 ──
        self._annotation_combos = {}
        grating_cols = self._grating_col_indices()
        temp_codes = self._get_temp_annotations()
        for gi, col_indices in grating_cols.items():
            if not col_indices:
                continue
            first_col = col_indices[0]
            combo = QComboBox()
            combo.setEditable(True)
            combo.setMinimumWidth(100)
            if temp_codes:
                combo.addItems(temp_codes)
            # 回填已保存的 grating_map
            existing = self._grating_map.get(f"G{gi}", "")
            if existing:
                combo.setCurrentText(existing)
            table.setCellWidget(0, first_col, combo)
            self._annotation_combos[gi] = combo
            # Ctrl+V 在 ComboBox 焦点下 → 转发给表格粘贴、不吞进下拉文本
            combo.installEventFilter(_ComboPasteRedirect(table, combo))

            # 同光栅其余列: 灰显 (不可编辑)
            for other_col in col_indices[1:]:
                gray_item = QTableWidgetItem("—")
                gray_item.setFlags(Qt.ItemFlag.NoItemFlags)
                gray_item.setBackground(QColor(230, 230, 230))
                table.setItem(0, other_col, gray_item)

        btn_row = QHBoxLayout()
        btn_row.addWidget(create_button("确定", dlg.accept, "primary"))
        btn_row.addWidget(create_button("取消", dlg.reject, "secondary"))
        layout.addLayout(btn_row)

        # ── 关闭时统一提取数据 (覆盖 accept/reject/Esc/X/Alt+F4) ──
        def _on_closed():
            self._levels, self._readings, self._grating_map = self._extract_table_data(table)
            self._dialog_table = None
            self._annotation_combos = {}

        dlg.finished.connect(_on_closed)

        from PyQt6.QtCore import QObject as _QObj
        from PyQt6.QtCore import Qt as _Qt

        class _EscFilter(_QObj):
            def eventFilter(self, obj, event):
                if event.type() == event.Type.KeyPress and event.key() == _Qt.Key.Key_Escape:
                    if obj.isFullScreen():
                        obj.showNormal()
                        return True
                return False

        self._readings_esc_filter = _EscFilter(dlg)
        dlg.installEventFilter(self._readings_esc_filter)
        dlg.exec()

    def _toggle_readings_fs(self, dlg):
        if dlg.isFullScreen():
            dlg.showNormal()
        else:
            dlg.showFullScreen()

    def _add_level(self):
        self._levels.append(0.0)
        if self._dialog_table is not None:
            self._dialog_table.setRowCount(len(self._levels) + 1)  # +1 = 备注行

    def _remove_level(self):
        if len(self._levels) > 1:
            self._levels.pop()
            if self._dialog_table is not None:
                self._dialog_table.setRowCount(len(self._levels) + 1)  # +1 = 备注行

    # ── 分析 ──

    def _get_table_data(self):
        """读取已持久化的 self._levels + self._readings (对话框关闭时 _on_closed 已写回)"""
        return self._levels, self._readings

    def _compute_ke_results(self, result) -> dict[str, float]:
        """从 StrainCalibrationResult 提取 Ke (pm/με) 结果。

        规则:
          - 每个光栅的 k_pm_per_ue = Ke
          - dual_anchored: 锚固栅 Ke=0，工作栅参与计算 (动态判断)
          - single: Ke2=0
          - dual_both: G1 → Ke1, G2 → Ke2
        """
        ke: dict[str, float] = {}
        kind = self._config["grating_kind"]
        for g in result.gratings:
            val = float(g.k_pm_per_ue) if not math.isnan(g.k_pm_per_ue) else 0.0
            ke[f"Ke{g.grating_index}"] = val

        if kind == "dual_anchored":
            anchor_idx = self._config.get("anchored_grating") or 2
            ke[f"Ke{anchor_idx}"] = 0.0
        elif kind == "single":
            ke["Ke2"] = 0.0
        elif "Ke2" not in ke:
            ke["Ke2"] = 0.0

        return ke

    def _run_analysis(self):
        _, readings = self._get_table_data()
        gauge = self._config["gauge_length_mm"]

        config = StrainCalibrationConfig(
            gauge_length_mm=gauge, mode=self._config["mode"],
            n_cycles=self._config["n_cycles"],
            grating_kind=self._config["grating_kind"],
            anchored_grating=self._config["anchored_grating"],
            levels=self._levels,
        )

        self.analyze_btn.setEnabled(False)
        self.strain_progress.setText("⏳ 正在分析...")
        self._worker = StrainCalibrationWorker(config, readings)
        self._worker.progress.connect(lambda m: self.strain_progress.setText(m))
        self._worker.finished.connect(self._on_strain_result)
        self._worker.error.connect(lambda e: self.strain_progress.setText(f"❌ {e[:200]}"))
        self._worker.start()

    def _on_strain_result(self, result):
        self._last_result = result
        self._ke_results = self._compute_ke_results(result)

        # ── 校验一致性 ──
        err = self._validate_grating_map_consistency()
        if err:
            self.strain_progress.setText(f"❌ {err}")
            self.analyze_btn.setEnabled(True)
            self.apply_coef_btn.setEnabled(False)
            self.export_se_btn.setEnabled(False)
            self.export_sw_btn.setEnabled(False)
            self.commit_list_btn.setEnabled(False)
            return

        sensor_name = self._parse_sensor_from_grating_map()
        if not sensor_name:
            if not self._grating_map:
                msg = "请在读数录入的备注行为每个光栅选择暗号源（如 C2-1 / C2-2）。"
            else:
                anns = list(self._grating_map.values())
                msg = f"光栅暗号前缀不一致: {', '.join(anns)}。请确保 G1、G2 属于同一传感器。"
            self.strain_progress.setText(f"❌ {msg}")
            self.analyze_btn.setEnabled(True)
            self.apply_coef_btn.setEnabled(False)
            self.export_se_btn.setEnabled(False)
            self.export_sw_btn.setEnabled(False)
            self.commit_list_btn.setEnabled(False)
            return

        # ── 存为当前工作结果（不自动入列表）──
        sub = self._build_strain_subconfig()
        self._working_result = sub

        self.analyze_btn.setEnabled(True)
        self.apply_coef_btn.setEnabled(True)
        self.export_se_btn.setEnabled(True)
        self.export_sw_btn.setEnabled(True)
        self.commit_list_btn.setEnabled(True)  # 允许加入列表
        self.strain_progress.setText(f"✅ 分析完成 — {sensor_name}: Ke1={self._ke_results.get('Ke1', 0):.4f}"
                                     + (f", Ke2={self._ke_results.get('Ke2', 0):.4f}" if self._ke_results.get('Ke2', 0) != 0 else "")
                                     + "  |  点击「加入已标定列表」保存")
        self._plot_strain(result)
        self._show_strain_text(result)

    def _plot_strain(self, result):
        eps = result.eps_theory

        # 标定曲线
        fig = self.curve_panel.get_figure(); fig.clear()
        ax = fig.add_subplot(111)
        if eps:
            for g in result.gratings:
                if np.isnan(g.k_pm_per_ue): continue
                ax.plot(eps, g.k_pm_per_ue * np.array(eps), "-", lw=2,
                        label=f"{self._get_grating_label(g.grating_index)}: k={g.k_pm_per_ue:.4f}, R²={g.R2:.5f}")
        ax.set_xlabel("理论应变 (με)"); ax.set_ylabel("波长漂移 (pm)")
        ax.set_title("Δλ-ε 标定曲线"); ax.legend(fontsize=8); ax.grid(alpha=0.3)
        ax.set_xlim(left=0); ax.set_ylim(bottom=0)
        self.curve_panel.draw()

        # 核心指标柱状图
        fig2 = self.bar_panel.get_figure(); fig2.clear()
        ax2 = fig2.add_subplot(111)
        labels, k_vals, r2_vals, nl_vals, rp_vals, hy_vals = [], [], [], [], [], []
        for g in result.gratings:
            if np.isnan(g.k_pm_per_ue): continue
            labels.append(self._get_grating_label(g.grating_index))
            k_vals.append(g.k_pm_per_ue)
            r2_vals.append(g.R2 * 10 if not np.isnan(g.R2) else 0)
            nl_vals.append(g.nonlinearity_pct_fs if not np.isnan(g.nonlinearity_pct_fs) else 0)
            rp_vals.append(g.repeatability_pct_fs if not np.isnan(g.repeatability_pct_fs) else 0)
            hy_vals.append(g.hysteresis_pct_fs if not np.isnan(g.hysteresis_pct_fs) else 0)
        if labels:
            x = np.arange(len(labels)); w = 0.15
            ax2.bar(x-2*w, k_vals, w, label="k (pm/με)")
            ax2.bar(x-w, r2_vals, w, label="R²×10")
            ax2.bar(x, nl_vals, w, label="非线性(%FS)")
            ax2.bar(x+w, rp_vals, w, label="重复性(%FS)")
            ax2.bar(x+2*w, hy_vals, w, label="迟滞(%FS)")
            ax2.set_xticks(x); ax2.set_xticklabels(labels)
        ax2.set_title("核心指标"); ax2.legend(fontsize=7); ax2.grid(alpha=0.3, axis="y")
        self.bar_panel.draw()

    def _show_strain_text(self, result):
        lines = [f"标距: {result.gauge_length_mm} mm | {result.mode} | {result.n_cycles}循环\n"]
        # 备注行 (grating_map)
        if self._grating_map:
            map_str = ", ".join(f"{k}→{v}" for k, v in self._grating_map.items())
            lines.append(f"备注映射: {map_str}")
        # Ke 汇总
        if self._ke_results:
            ke_str = ", ".join(f"{k}={v:.4f} pm/με" for k, v in self._ke_results.items())
            lines.append(f"应变系数: {ke_str}\n")
        for g in result.gratings:
            lines.append(f"{self._get_grating_label(g.grating_index)}: k={g.k_pm_per_ue:.6f} R²={g.R2:.6f} "
                         f"NL={g.nonlinearity_pct_fs:.2f}% RP={g.repeatability_pct_fs:.2f}% "
                         f"HY={g.hysteresis_pct_fs:.2f}%")
        self.strain_result_text.setText("\n".join(lines))

    # ── 项目 dirty 标记 ──

    def _set_dirty(self):
        self._project_dirty = True
        self._dirty_label.setText("● 有未保存更改")

    def _clear_dirty(self):
        self._project_dirty = False
        self._dirty_label.setText("")

    # ── 多传感器列表管理 ──

    def _validate_grating_map_consistency(self) -> str | None:
        """校验 grating_map 中 G1/G2 暗号前缀一致。返回 None 通过, 否则返回错误消息。"""
        if not self._grating_map:
            return None
        prefixes = set()
        for ann in self._grating_map.values():
            if ann and '-' in ann:
                prefixes.add(ann.split('-')[0])
        if len(prefixes) > 1:
            return f"同一次标定的两个光栅必须属于同一传感器，但找到: {', '.join(sorted(prefixes))}"
        return None

    def _get_list_label(self, sensor_name: str, config: object) -> str:
        """生成列表条目标签: "A1  双栅-双工作  Ke1=1.23 Ke2=0.98" """
        mode = getattr(config, 'sensor_mode', 'single')
        mode_names = {"single": "单栅", "dual_working": "双栅-双工作", "dual_anchored": "双栅-锚固"}
        mode_label = mode_names.get(mode, mode)
        ke = getattr(config, 'ke_results', {}) or {}
        ke1 = ke.get("Ke1", 0)
        ke2 = ke.get("Ke2", 0)
        ke_str = f"Ke1={ke1:.4f}"
        if mode != "single":
            anchor_suffix = "(固定)" if mode == "dual_anchored" else ""
            ke_str += f"  Ke2={ke2:.4f}{anchor_suffix}"
        return f"{sensor_name}  {mode_label}  {ke_str}"

    def _refresh_sensor_list(self, select_sensor: str | None = None):
        """重新生成 QListWidget，可指定选中项。"""
        self.sensor_list.blockSignals(True)
        try:
            self.sensor_list.clear()
            for s_name in sorted(self._strain_configs.keys()):
                cfg = self._strain_configs[s_name]
                label = self._get_list_label(s_name, cfg)
                self.sensor_list.addItem(label)
            # 选中指定项
            if select_sensor is not None:
                for i in range(self.sensor_list.count()):
                    item = self.sensor_list.item(i)
                    if item and select_sensor in (item.text() or ""):
                        self.sensor_list.setCurrentRow(i)
                        break
            # 更新按钮状态
            has_selection = self.sensor_list.currentRow() >= 0
            self.delete_sensor_btn.setEnabled(has_selection)
        finally:
            self.sensor_list.blockSignals(False)

    def _upsert_strain_config(self, sensor_name: str):
        """从当前活动态 (_config/_levels/_readings/_grating_map/_ke_results) 构造 StrainSubConfig，
        写入 self._strain_configs, 同步 project.strain, 刷新列表。"""
        from py.calibration.project_config import StrainSubConfig
        sub = self._build_strain_subconfig()
        self._strain_configs[sensor_name] = sub
        # 同步到 project.strain
        pc = self._get_or_create_project_config()
        pc.strain[sensor_name] = sub
        self._set_dirty()
        self._refresh_sensor_list(select_sensor=sensor_name)

    def _commit_to_list(self):
        """将当前工作结果显式加入已标定列表"""
        if self._working_result is None:
            return  # 按钮 disabled 时不会触发，正常路径无此情况

        sub = self._working_result
        sensor_name = getattr(sub, 'sensor_name', '') or self._parse_sensor_from_grating_map()
        if not sensor_name:
            if not self._grating_map:
                msg = "请在读数录入的备注行为每个光栅选择暗号源（如 C2-1 / C2-2）。"
            else:
                anns = list(self._grating_map.values())
                msg = f"光栅暗号前缀不一致: {', '.join(anns)}。请确保 G1、G2 属于同一传感器。"
            QMessageBox.warning(self, "传感器名缺失", msg)
            return

        # 同名覆盖确认
        if sensor_name in self._strain_configs:
            r = QMessageBox.question(self, "覆盖确认",
                f"传感器 '{sensor_name}' 已在列表中，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return

        # 加入列表
        self._strain_configs[sensor_name] = sub
        pc = self._get_or_create_project_config()
        pc.strain[sensor_name] = sub
        self._set_dirty()
        self._refresh_sensor_list(select_sensor=sensor_name)
        self._grating_map_fresh = False  # ★ 已持久化到列表，后续 _load_sensor_to_workspace 可安全恢复
        self._current_sensor = sensor_name
        self.strain_progress.setText(f"✅ 已加入列表 — {sensor_name}: Ke1={self._ke_results.get('Ke1', 0):.4f}")

    def _clear_workspace(self):
        """清空工作态 (不删列表)"""
        self._current_sensor = None
        self._working_result = None
        self._last_result = None
        self._ke_results = {}
        self._grating_map = {}
        self._grating_map_version = 0   # ★ 清空时重置版本，下次 _extract_table_data 写入递增
        self._grating_map_fresh = False  # ★ 已清空，允许 _load_sensor_to_workspace 恢复
        self._readings = {}
        self._levels = self._generate_default_levels()
        self._config.update({
            "gauge_length_mm": 80.0, "mode": "tension_only",
            "n_cycles": 1, "grating_kind": "single",
            "anchored_grating": None,
        })
        self.config_btn.setText(f"⚙ 标定参数: {self._config_label()}")
        self.strain_progress.setText("")
        self.apply_coef_btn.setEnabled(False)
        self.export_se_btn.setEnabled(False)
        self.export_sw_btn.setEnabled(False)
        self.commit_list_btn.setEnabled(False)
        # 清空图表
        for panel in [self.curve_panel, self.resid_panel, self.bar_panel]:
            fig = panel.get_figure(); fig.clear()
            panel.draw()
        self.strain_result_text.clear()

    def _load_sensor_to_workspace(self, sensor_name: str):
        """加载已保存传感器的配置到当前工作态 (config/levels/readings/grating_map/ke_results)"""
        cfg = self._strain_configs.get(sensor_name)
        if cfg is None:
            return
        # 恢复 config
        mode_map = {"single": "single", "dual_working": "dual_both", "dual_anchored": "dual_anchored"}
        self._config.update({
            "gauge_length_mm": getattr(cfg, 'gauge_length_mm', 80.0),
            "mode": "tension_only",  # readings 结构决定了 mode; 保持简单
            "n_cycles": getattr(cfg, 'n_cycles', 1),
            "grating_kind": mode_map.get(getattr(cfg, 'sensor_mode', 'single'), 'single'),
            "anchored_grating": None,
        })
        self._levels = self._generate_default_levels()  # 标距可能变了
        # ★ 仅当 _grating_map 非用户刚编辑态时才从存档恢复，避免覆盖 _extract_table_data 的最新值
        if not self._grating_map_fresh:
            self._grating_map = dict(getattr(cfg, 'grating_map', {}) or {})
        self._ke_results = dict(getattr(cfg, 'ke_results', {}) or {})
        # 从 readings 重建 _levels
        rd_list = getattr(cfg, 'readings', []) or []
        if rd_list and isinstance(rd_list, list) and isinstance(rd_list[0], dict):
            levels_from_cfg = [r.get("disp_mm", 0.0) for r in rd_list]
            if levels_from_cfg:
                self._levels = levels_from_cfg
        self._readings = {}  # readings 存的是 raw dict 格式，这里不清零等 _open_readings 载入
        self._current_sensor = sensor_name
        self.config_btn.setText(f"⚙ 标定参数: {self._config_label()}")
        # 按钮使能
        if self._ke_results:
            self.apply_coef_btn.setEnabled(True)
            self.export_se_btn.setEnabled(True)
            self.export_sw_btn.setEnabled(True)

    def _lazy_recompute_charts(self, sensor_name: str):
        """从 StrainSubConfig 的 readings/ke_results 懒重算图表。

        DataFrame None+empty 守卫生效，dict 访问全用 .get()。
        """
        cfg = self._strain_configs.get(sensor_name)
        if cfg is None:
            return
        ke = getattr(cfg, 'ke_results', {}) or {}
        rd_list = getattr(cfg, 'readings', []) or []
        gauge = getattr(cfg, 'gauge_length_mm', 80.0)
        mode = getattr(cfg, 'sensor_mode', 'single')
        # 从 readings 提取 eps_theory
        levels = []
        eps_theory = []
        for r in rd_list:
            if not isinstance(r, dict):
                continue
            d = r.get("disp_mm", 0.0)
            levels.append(d)
            eps_theory.append(d / gauge * 1e6 if gauge > 0 else 0.0)

        # 文本
        mode_names = {"single": "单栅", "dual_working": "双栅-双工作", "dual_anchored": "双栅-锚固"}
        lines = [f"传感器: {sensor_name}  |  模式: {mode_names.get(mode, mode)}  |  标距: {gauge:.0f} mm\n"]
        if self._grating_map:
            lines.append(f"备注映射: {', '.join(f'{k}→{v}' for k, v in self._grating_map.items())}")
        if ke:
            lines.append(f"应变系数: {', '.join(f'{k}={v:.4f} pm/με' for k, v in ke.items())}\n")
        lines.append("(图表从保存的 readings 懒重算)")
        self.strain_result_text.setText("\n".join(lines))

        # 标定曲线 (从 Ke 重画拟合线)
        fig = self.curve_panel.get_figure(); fig.clear()
        ax = fig.add_subplot(111)
        if eps_theory:
            eps_arr = np.array(eps_theory, dtype=np.float64)
            for gi, key in enumerate(["Ke1", "Ke2"], start=1):
                k_val = ke.get(key, 0)
                if abs(k_val) > 1e-10:
                    ax.plot(eps_arr, k_val * eps_arr, "-", lw=2,
                            label=f"{self._get_grating_label(gi)}: k={k_val:.4f} pm/με")
        ax.set_xlabel("理论应变 (με)"); ax.set_ylabel("波长漂移 (pm)")
        ax.set_title(f"Δλ-ε 标定曲线 — {sensor_name}"); ax.legend(fontsize=8); ax.grid(alpha=0.3)
        ax.set_xlim(left=0); ax.set_ylim(bottom=0)
        self.curve_panel.draw()

        # 柱状图
        fig2 = self.bar_panel.get_figure(); fig2.clear()
        ax2 = fig2.add_subplot(111)
        labels_ok = []; k_vals = []
        for gi, key in enumerate(["Ke1", "Ke2"], start=1):
            k_val = ke.get(key, 0)
            if abs(k_val) > 1e-10:
                labels_ok.append(self._get_grating_label(gi))
                k_vals.append(k_val)
        if labels_ok:
            x = np.arange(len(labels_ok)); w = 0.3
            ax2.bar(x, k_vals, w, label="Ke (pm/με)")
            ax2.set_xticks(x); ax2.set_xticklabels(labels_ok)
        ax2.set_title(f"核心指标 — {sensor_name}"); ax2.legend(fontsize=7); ax2.grid(alpha=0.3, axis="y")
        self.bar_panel.draw()

        # 残差图 (暂无残差数据 → 空白)
        fig3 = self.resid_panel.get_figure(); fig3.clear()
        fig3.add_subplot(111).set_title("线性残差 — (需重新标定以获取残差数据)")
        self.resid_panel.draw()

    def _on_sensor_selected(self, row: int):
        """列表选中项变化 → 加载传感器配置为当前工作结果 + 懒重算图表"""
        if row < 0:
            self._current_sensor = None
            self.delete_sensor_btn.setEnabled(False)
            return
        item = self.sensor_list.item(row)
        if item is None:
            return
        text = item.text() or ""
        # 从标签首段提取传感器名 ("A1  双栅-双工作...")
        sensor_name = text.split("  ")[0].strip()
        if sensor_name not in self._strain_configs:
            return
        cfg = self._strain_configs[sensor_name]
        self._load_sensor_to_workspace(sensor_name)
        self._working_result = cfg  # 同步为当前工作结果
        self._lazy_recompute_charts(sensor_name)
        self.delete_sensor_btn.setEnabled(True)
        self.commit_list_btn.setEnabled(True)

    def _new_calibration(self):
        """清空工作态，准备录入新传感器"""
        # 只在有未分析草稿 (未进列表) 时警告
        has_draft = bool(self._grating_map or self._readings)
        has_saved = self._current_sensor is not None and self._current_sensor in self._strain_configs
        if has_draft and not has_saved:
            r = QMessageBox.question(self, "新建标定",
                "当前读数录入尚未提交分析，新建将丢弃草稿，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        # 已分析传感器安全留在列表，直接新建
        self.sensor_list.clearSelection()
        self._clear_workspace()
        self.sensor_list.setCurrentRow(-1)

    def _delete_sensor(self):
        """删除选中传感器 (同步 project.strain)"""
        if self._current_sensor is None:
            return
        s_name = self._current_sensor
        r = QMessageBox.question(self, "确认删除",
            f"确定删除传感器 '{s_name}' 的标定数据？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return
        # 从活状态移除
        self._strain_configs.pop(s_name, None)
        # 从 project.strain 移除
        pc = self._get_or_create_project_config()
        pc.strain.pop(s_name, None)
        self._set_dirty()
        # 清空工作态 + 刷新列表
        self._clear_workspace()
        self._refresh_sensor_list()
        self.sensor_list.setCurrentRow(-1)

    # ── 项目配置管理 ──

    def _save_project(self):
        """保存项目配置 (温度段 + 应变段)，可从温度页或应变页调用"""
        from py.calibration.project_config import ProjectConfigManager
        ctw = self._get_cal_tab_widget()
        tp = ctw.temp_page if ctw is not None else None
        pc = ProjectConfigManager.capture(tp, self)

        default_name = f"项目_{datetime.now().strftime('%Y%m%d_%H%M')}"

        name, ok = QInputDialog.getText(
            self, "保存项目配置", "配置名称:", text=default_name)
        if not ok or not name.strip():
            return
        name = name.strip()
        pc.name = name

        try:
            path = ProjectConfigManager.save(pc)
            if ctw is not None:
                ctw.project_config = pc
            self._clear_dirty()
            QMessageBox.information(self, "已保存",
                f"项目配置已保存到:\n{path}\n\n"
                f"温度段: {'有' if pc.temperature else '无'}  |  "
                f"应变传感器: {len(pc.strain)} 个")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def _load_project(self):
        """加载项目配置，回填温度段 + 应变段"""
        if self._project_dirty:
            r = QMessageBox.question(self, "未保存更改",
                "项目有未保存的更改，是否先保存再加载？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                return
            if r == QMessageBox.StandardButton.Yes:
                self._save_project()
                if self._project_dirty:  # 用户取消了保存对话框
                    return

        from py.calibration.project_config import ProjectConfigManager

        projects = ProjectConfigManager.list_all()
        if not projects:
            QMessageBox.information(self, "无配置", "还没有保存的项目配置文件。")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("加载项目配置"); dlg.resize(650, 400)
        layout = QVBoxLayout(dlg)

        lst = QListWidget()
        for p in projects:
            key = p.get("_key", p.get("name", "?"))
            created = str(p.get("created_at", ""))[:16]
            n_strain = len(p.get("strain", {}) or {})
            n_temp = len((p.get("temperature", {}) or {}).get("s_eff_results", {}) or {})
            lst.addItem(f"{key}\n  创建: {created}  |  温度S_eff: {n_temp}  |  应变传感器: {n_strain}")
        layout.addWidget(lst)

        btn_row = QHBoxLayout()
        load_btn = create_button("加载选中", dlg.accept, "primary")
        btn_row.addWidget(load_btn)
        btn_row.addWidget(create_button("取消", dlg.reject, "secondary"))
        layout.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted or lst.currentRow() < 0:
            return

        proj_dict = projects[lst.currentRow()]
        name = proj_dict.get("_key", proj_dict.get("name", ""))

        try:
            pc = ProjectConfigManager.load(name)

            ctw = self._get_cal_tab_widget()
            tp = ctw.temp_page if ctw is not None else None
            ProjectConfigManager.restore(pc, tp, self)

            QMessageBox.information(self, "已加载",
                f"项目 '{name}' 已恢复。\n\n"
                f"温度段: {'有' if pc.temperature else '无'}  |  "
                f"应变传感器: {len(pc.strain)} 个")
        except Exception as e:
            QMessageBox.critical(self, "加载失败", str(e))

    def _apply_coefficients(self):
        """批量应用列表全部传感器的 Ke 系数到项目配置/Phase B。

        遍历 self._strain_configs 中每个传感器：
          - 双栅 (dual_working / dual_anchored) → 写 ke_table + strain 子配置 + 同步 phase_b_state
          - 单栅 (single) → 仅存 strain 子配置
        """
        if not self._strain_configs:
            return  # 按钮 disabled 时不会触发

        pc = self._get_or_create_project_config()
        has_temp = self._has_temperature_data()
        tp = self._get_temp_page()

        dual_count = 0
        single_count = 0
        applied: list[str] = []

        for s_name, cfg in self._strain_configs.items():
            mode = getattr(cfg, 'sensor_mode', 'single')
            ke = getattr(cfg, 'ke_results', {}) or {}
            if not ke:
                continue

            ke_dict = {str(k): float(v) for k, v in ke.items()}

            # 写入 strain 子配置 (所有模式都存)
            if s_name not in pc.strain:
                pc.strain[s_name] = cfg

            # 判单栅/双栅 (在温度数据中的角色)
            is_dual = mode in ("dual_working", "dual_anchored")

            if has_temp and is_dual:
                # 双栅 → 写 ke_table + 同步 phase_b_state
                if pc.temperature is None:
                    pc.temperature = {}
                pc.temperature["ke_table"] = pc.temperature.get("ke_table", {})
                pc.temperature["ke_table"][s_name] = ke_dict

                if tp is not None:
                    pb_state = getattr(tp, '_phase_b_state', None) or {}
                    pb_state["ke_table"] = pb_state.get("ke_table", {})
                    pb_state["ke_table"][s_name] = ke_dict
                    tp._phase_b_state = pb_state

                dual_count += 1
            else:
                # 单栅 (或温度未做) → 仅 strain
                single_count += 1

            applied.append(s_name)

        if not applied:
            QMessageBox.warning(self, "提示", "没有传感器有有效的 Ke 系数。")
            return

        # 汇总
        parts = [f"已应用 {len(applied)} 个传感器："]
        if dual_count:
            parts.append(f"  • {dual_count} 个双栅 — 已写入 Phase B Ke 表，下次打开 Phase B 即可使用")
        if single_count:
            if has_temp:
                parts.append(f"  • {single_count} 个单栅/无温度 — 仅保存应变配置，不参与 Phase B 解耦")
            else:
                parts.append(f"  • {single_count} 个 — 温度标定未做，Coeff保存至项目配置")

        QMessageBox.information(self, "应用完成", "\n".join(parts))

        # 保留旧行为: 写 sensor_system.constants (会话级 — 仅第一个传感器)
        if self._last_result is not None:
            main_win = self.window()
            ss = getattr(main_win, "sensor_system", None)
            if ss is not None and ss.sensors:
                for g in self._last_result.gratings:
                    if not math.isnan(g.k_pm_per_ue):
                        ss.sensors[0].constants[f"k{g.grating_index}"] = float(g.k_pm_per_ue)

    def _export_strain_excel(self):
        if not self._last_result: return
        path, _ = QFileDialog.getSaveFileName(self, "导出", "应变标定.xlsx", "Excel (*.xlsx)")
        if path:
            export_strain_excel(self._last_result, path)
            QMessageBox.information(self, "完成", f"已保存: {path}")

    def _on_generate_report(self):
        """检测报告 — 占位，功能开发中"""
        QMessageBox.information(self, "检测报告",
            "检测报告功能开发中。\n\n"
            "当前已持久化的数据字段:\n"
            "• 应变系数 Ke_results (pm/με)\n"
            "• 应变标定曲线 (charts_meta)\n"
            "• 温度系数 S_eff (pm/°C)\n"
            "• 解耦诊断结果 (e_mean/e_std/rating)\n\n"
            "上述字段已完整保存在项目配置中，为后续报告生成预留。")

    # 保留旧方法名向后兼容 (不再被按钮调用，但可能被外部引用)
    _export_strain_word = _on_generate_report


# ═══════════════════════════════════════════════════════════════════════
# 主标定 Tab Widget
# ═══════════════════════════════════════════════════════════════════════

class CalibrationTabWidget(QWidget):
    """传感器标定 — 温度标定 + 应变标定 双子页

    持有跨模块共享的 ProjectConfig 实例。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project_config = None  # ProjectConfig | None — 阶段 5 统一 save/load
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        header = create_section_header("传感器标定分析", level=1)
        layout.addWidget(header)
        self.sub_tabs = QTabWidget()
        self.temp_page = TemperatureCalibrationPage(self)
        self.strain_page = StrainCalibrationPage(self)
        self.sub_tabs.addTab(self.temp_page, "🌡 温度标定")
        self.sub_tabs.addTab(self.strain_page, "📐 应变标定")
        layout.addWidget(self.sub_tabs)
