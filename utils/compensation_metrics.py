"""补偿后指标计算与传感器评级模块

在 Phase 1 建完 ApparentStrainLUT 后，对每支双栅传感器计算出厂指标
并自动评级。单栅传感器走显式 N/A 路径，不产假 LUT/假评级。

核心约束：
- 单栅传感器 → evaluate_compensation 抛 ValueError；评级必须调 grade_sensor_na()
- <3 循环 → low_confidence=True → 不得判「优」
- hysteresis_max > thr_hys → FAIL (废品拦截)
- %FS 由外部传入 (StrainSubConfig / 标定参数)，不硬编码
- 阈值可配置 (GradeThresholds dataclass)，默认值仅为出厂基线
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from utils.apparent_strain_comp import (
    ApparentStrainLUT, ApparentStrainPoly,
    build_apparent_strain_lut, fit_apparent_strain_poly,
    CompensationModel, apply_compensation_model,
)


# ═══════════════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class GradeThresholds:
    """Sensor grading thresholds (configurable, %FS-based).

    Default values (factory baseline for FS=1000 ue):
    - Hysteresis > 5% FS -> FAIL (scrap intercept)
    - Residual sigma <= 1% FS -> Excellent (you)
    - Residual sigma <= 2% FS -> Good (liang)
    - Residual sigma <= 4% FS -> Pass (hege)
    - Residual sigma > 4% FS -> Fail (buhege)

    Additional quality gate (decided Phase 3b):
    - "You" grade also requires worst_case_single <= thr_worst_case_excellent_pct_fs.
      Default 1.5%FS (15 ue for FS=1000). Exceeding this caps grade at "liang"
      (not FAIL -- the sensor is still usable, just not "best in class").
      Set to 0 to disable this extra check.

    Attributes:
        thr_hysteresis_fail_pct_fs: Hysteresis FAIL line (%FS)
        thr_sigma_excellent_pct_fs: Residual sigma excellent cap (%FS)
        thr_sigma_good_pct_fs: Residual sigma good cap (%FS)
        thr_sigma_pass_pct_fs: Residual sigma pass cap (%FS)
        thr_worst_case_excellent_pct_fs: Worst-case single-point cap for "you" (%FS).
            0 = disabled. Sensors exceeding this get capped at "liang".
    """
    thr_hysteresis_fail_pct_fs: float = 5.0
    thr_sigma_excellent_pct_fs: float = 1.0
    thr_sigma_good_pct_fs: float = 2.0
    thr_sigma_pass_pct_fs: float = 4.0
    thr_worst_case_excellent_pct_fs: float = 1.5

    def to_dict(self) -> dict:
        return {
            "thr_hysteresis_fail_pct_fs": self.thr_hysteresis_fail_pct_fs,
            "thr_sigma_excellent_pct_fs": self.thr_sigma_excellent_pct_fs,
            "thr_sigma_good_pct_fs": self.thr_sigma_good_pct_fs,
            "thr_sigma_pass_pct_fs": self.thr_sigma_pass_pct_fs,
            "thr_worst_case_excellent_pct_fs": self.thr_worst_case_excellent_pct_fs,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GradeThresholds":
        return cls(
            thr_hysteresis_fail_pct_fs=float(d.get("thr_hysteresis_fail_pct_fs", 5.0)),
            thr_sigma_excellent_pct_fs=float(d.get("thr_sigma_excellent_pct_fs", 1.0)),
            thr_sigma_good_pct_fs=float(d.get("thr_sigma_good_pct_fs", 2.0)),
            thr_sigma_pass_pct_fs=float(d.get("thr_sigma_pass_pct_fs", 4.0)),
            thr_worst_case_excellent_pct_fs=float(
                d.get("thr_worst_case_excellent_pct_fs", 1.5)),
        )


@dataclass
class CompensationMetrics:
    """单支双栅传感器的补偿后出厂指标。

    所有带 _pct_fs 后缀的字段为 %FS 量纲。
    low_confidence = True 表示循环数不足 (<3)，指标参考性降低。
    """
    sensor: str
    # 核心指标 (με)
    residual_sigma: float          # LOOCV 残余 σ (με)；<3 循环退化为 in-sample
    repeatability: float           # 各 T 箱循环间 std 的最大值 (με)
    hysteresis_max: float          # 同循环升/降支同温最大差 (με)；无升降支时为 NaN
    noise_floor: float             # 高频滚动残差 std (με)
    temp_sensitivity_max: float    # max |dε_app/dT| (με/°C)，从补偿模型计算
    worst_case_single: float       # hysteresis_max / 2 (με)；NaN 若迟滞不可算
    # 旗标
    low_confidence: bool           # < 3 循环 → True
    fs: float                      # 满量程 (με)
    # 补偿形式元数据
    comp_form: str = "lut"         # "lut" | "poly"
    poly_order: int = 0            # form="poly" 时的多项式阶数；form="lut" 时为 0
    # %FS 版本
    residual_sigma_pct_fs: float = 0.0
    repeatability_pct_fs: float = 0.0
    hysteresis_max_pct_fs: float = 0.0
    noise_floor_pct_fs: float = 0.0
    worst_case_single_pct_fs: float = 0.0

    def to_dict(self) -> dict:
        return {
            "sensor": self.sensor,
            "residual_sigma": self.residual_sigma,
            "repeatability": self.repeatability,
            "hysteresis_max": self.hysteresis_max,
            "noise_floor": self.noise_floor,
            "temp_sensitivity_max": self.temp_sensitivity_max,
            "worst_case_single": self.worst_case_single,
            "low_confidence": self.low_confidence,
            "fs": self.fs,
            "comp_form": self.comp_form,
            "poly_order": self.poly_order,
            "residual_sigma_pct_fs": self.residual_sigma_pct_fs,
            "repeatability_pct_fs": self.repeatability_pct_fs,
            "hysteresis_max_pct_fs": self.hysteresis_max_pct_fs,
            "noise_floor_pct_fs": self.noise_floor_pct_fs,
            "worst_case_single_pct_fs": self.worst_case_single_pct_fs,
        }


@dataclass
class SensorGrade:
    """传感器出厂评级。

    Attributes:
        sensor: 传感器名
        grade: "优" | "良" | "合格" | "FAIL" | "N/A"
        passed: True = 可用 (合格及以上)；False = FAIL 或 N/A
        reasons: 人类可读的评级理由
        is_single_grating: True = 单栅，走了 N/A 路径
    """
    sensor: str
    grade: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    is_single_grating: bool = False

    def to_dict(self) -> dict:
        return {
            "sensor": self.sensor,
            "grade": self.grade,
            "passed": self.passed,
            "reasons": self.reasons,
            "is_single_grating": self.is_single_grating,
        }


# ═══════════════════════════════════════════════════════════════════════
# 单栅显式 N/A
# ═══════════════════════════════════════════════════════════════════════


def grade_sensor_na(sensor: str, reason: str = "") -> SensorGrade:
    """为单栅传感器创建显式 N/A 评级。

    单栅传感器不进 Phase B 解耦，无 ε/T 时序可建 LUT，
    走 S_eff 温补路径，不参与表观应变补偿流程。
    """
    reasons = ["N/A — 单栅传感器，不进 Phase B 解耦"]
    if reason:
        reasons.append(reason)
    return SensorGrade(
        sensor=sensor,
        grade="N/A",
        passed=False,
        reasons=reasons,
        is_single_grating=True,
    )


# ═══════════════════════════════════════════════════════════════════════
# 循环检测
# ═══════════════════════════════════════════════════════════════════════



def detect_cycles_from_T(
    T: np.ndarray,
    *,
    method: str = "auto",
    smooth_win: int | None = None,
    prominence_degC: float = 12.0,
    distance_pts: int = 3000,
    plateau_tol: float = 1.0,
) -> np.ndarray:
    """从温度序列检测循环分段，返回 cycle_ids (0-indexed 整数数组)。

    统一算法 (Phase 4 稳健重构):
    1. 强平滑 (uniform_filter1d, win ~ 2% of data) → 得到温度包络
    2. scipy.signal.find_peaks 在负包络上找谷
    3. prominence ≥ prominence_degC (绝对值, 默认 12°C)
       distance ≥ distance_pts (默认 3000, 保证谷间距)
    4. 谷间为一个循环; 首谷前 / 末谷后各为一循环

    兼容连续扫温、台阶 dwell、混合波形。method 参数保留兼容，默认 auto 直接走统一路径。

    Args:
        T: 温度序列 (°C), shape (N,)
        smooth_win: 平滑窗口 (点数)。None → 自适应 max(2000, n // 50)
        prominence_degC: 谷 prominence 阈值 (°C 绝对值)
        distance_pts: 谷间最小距离 (点数)
        plateau_tol: 已废弃, 保留签名兼容

    Returns:
        cycle_ids: int array, shape (N,), 0-indexed 循环编号。
                   未分段区域填充 -1。

    Raises:
        ValueError: T 为空
    """
    T = np.asarray(T, dtype=np.float64)
    if len(T) == 0:
        raise ValueError("T 为空，无法检测循环")

    n = len(T)
    T_range = float(np.max(T) - np.min(T))

    # 温差极小 → 单循环
    if T_range < 2.0:
        return np.zeros(n, dtype=int)

    # ── 1. 强平滑 ──
    if smooth_win is None:
        smooth_win = max(2000, n // 50)
    # guard: can't exceed 1/8 data, and scales down for short data
    smooth_win = min(smooth_win, max(n // 8, 5))
    if smooth_win < 3:
        return np.zeros(n, dtype=int)

    # distance / prominence: scale down for short data
    _distance = min(distance_pts, max(n // 8, 10))
    _prominence = min(prominence_degC, T_range * 0.3) if T_range < prominence_degC * 2 else prominence_degC

    # scipy.ndimage.uniform_filter1d: 纯 C 实现, 远快于 np.convolve
    try:
        from scipy.ndimage import uniform_filter1d  # type: ignore[import-untyped]
    except ImportError:
        # fallback: numpy convolve
        kernel = np.ones(smooth_win) / smooth_win
        T_smooth = np.convolve(T, kernel, mode="same")
    else:
        T_smooth = uniform_filter1d(T, smooth_win)

    # ── 2. 找谷 (scipy.signal.find_peaks) ──
    try:
        from scipy.signal import find_peaks  # type: ignore[import-untyped]
    except ImportError:
        # fallback: 简化找谷 (min in sliding windows)
        valley_step = max(1, smooth_win // 2)
        valley_indices: list[int] = []
        for i in range(0, n - valley_step, valley_step):
            window = T_smooth[i:i + valley_step]
            valley_indices.append(i + int(np.argmin(window)))
        filtered: list[int] = []
        for vi in valley_indices:
            if not filtered or vi - filtered[-1] >= _distance:
                filtered.append(vi)
        peaks = np.array(filtered, dtype=int)
    else:
        peaks, _props = find_peaks(
            -T_smooth,
            prominence=_prominence,
            distance=_distance,
        )

    # ── 3. 分段 ──
    # 边界补充: 若首/尾温度接近全局谷底，自动插入边界谷
    T_smooth_min = float(np.min(T_smooth))
    peaks_list = list(peaks)
    boundary_margin = max(smooth_win // 2, 5)
    if peaks_list and peaks_list[0] > boundary_margin:
        if abs(T_smooth[0] - T_smooth_min) < max(_prominence * 0.5, T_range * 0.05):
            peaks_list.insert(0, 0)
    if peaks_list and peaks_list[-1] < n - boundary_margin:
        if abs(T_smooth[-1] - T_smooth_min) < max(_prominence * 0.5, T_range * 0.05):
            peaks_list.append(n - 1)
    peaks = np.array(peaks_list, dtype=int)

    if len(peaks) < 2:
        return np.zeros(n, dtype=int)

    cycle_ids = np.full(n, -1, dtype=int)

    # 首谷前 → 循环 0
    if peaks[0] > 0:
        cycle_ids[:peaks[0]] = 0

    # 谷间 → 循环 ci (谷0→谷1 = cycle 0, etc.)
    for ci in range(len(peaks) - 1):
        start = peaks[ci]
        end = peaks[ci + 1]
        cycle_ids[start:end] = ci

    # 末谷后 → 最后循环
    cycle_ids[peaks[-1]:] = len(peaks) - 1

    # ── 4. 合并短尾 ──
    unique_ids = sorted(set(int(c) for c in cycle_ids if c >= 0))
    if len(unique_ids) >= 2:
        last_id = unique_ids[-1]
        last_mask = cycle_ids == last_id
        if np.sum(last_mask) < smooth_win:
            prev_id = unique_ids[-2]
            cycle_ids[last_mask] = prev_id

    # ── 5. 兜底 ──
    unfilled = cycle_ids == -1
    if np.any(unfilled):
        result = cycle_ids.copy()
        last_valid = 0
        for i in range(n):
            if result[i] == -1:
                result[i] = last_valid
            else:
                last_valid = int(result[i])
        return result

    return cycle_ids



# ═══════════════════════════════════════════════════════════════════════
# 指标计算
# ═══════════════════════════════════════════════════════════════════════


def evaluate_compensation(
    T: np.ndarray,
    eps: np.ndarray,
    cycle_ids: np.ndarray,
    *,
    fs: float,
    form: str = "lut",
    poly_order: Optional[int] = None,
    noise_win: int = 120,
    sensor: str = "",
    subsample_step: Optional[int] = None,
) -> CompensationMetrics:
    """从解耦输出的瞬态 ε/T 时序计算补偿后出厂指标。

    残差按所发形式重算: 每折 LOOCV 用指定 form/order 重建模型，
    在留出循环上算残差 → 即该形式的真实残余 σ。

    Args:
        T: 解耦温度序列 (degC), shape (N,)
        eps: 解耦应变序列 (ue), shape (N,)
        cycle_ids: 循环编号数组, shape (N,), -1 = unassigned
        fs: 满量程 (ue) — 从应变标定配置传入，严禁硬编码
        form: "lut" | "poly"
        poly_order: form="poly" 时的阶数 (默认: poly=4, lut 忽略)
        noise_win: 高频噪声检测窗口 (点数)
        sensor: 传感器名
        subsample_step: LOOCV/建模均匀抽样步长 (None=不抽样)。
           例 step=10 → 只用 1/10 数据建模型做 LOOCV。
           ★ 统计量 (e_std/噪声底/滞回) 始终用全量数据。

    Returns:
        CompensationMetrics

    Raises:
        ValueError: 输入为单栅传感器或数据长度不一致
    """
    T = np.asarray(T, dtype=np.float64)
    eps = np.asarray(eps, dtype=np.float64)
    cycle_ids_arr = np.asarray(cycle_ids, dtype=int)

    if len(T) == 0 or len(eps) == 0:
        raise ValueError(
            "单栅传感器无 eps/T 时序，请调 grade_sensor_na() 走 N/A 路径"
        )

    if len(T) != len(eps) or len(T) != len(cycle_ids_arr):
        raise ValueError(
            f"length mismatch: T={len(T)}, eps={len(eps)}, cycle_ids={len(cycle_ids_arr)}"
        )

    if fs <= 0:
        raise ValueError(f"fs must be > 0, got fs={fs}")

    if form not in ("lut", "poly"):
        raise ValueError(f"unsupported form: {form}, use 'lut' or 'poly'")

    # default poly_order
    if form == "poly" and poly_order is None:
        poly_order = 4

    # ── 清理 cycle_ids ──
    cycle_ids_clean = _fill_unassigned_cycles(cycle_ids_arr)
    unique_cycles = sorted(set(int(c) for c in cycle_ids_clean))
    n_cycles = len(unique_cycles)
    low_confidence = n_cycles < 3

    T_base = float(np.median(T))

    if not sensor:
        sensor = f"sensor_{id(T)}"

    # ── 抽样: LOOCV/建模用子集，统计量用全量 ──
    if subsample_step is not None and subsample_step > 1:
        T_loocv = T[::subsample_step]
        eps_loocv = eps[::subsample_step]
        cids_loocv = cycle_ids_clean[::subsample_step]
    else:
        T_loocv = T
        eps_loocv = eps
        cids_loocv = cycle_ids_clean

    # ── 1. 残余 σ: 按 form/order 做 LOOCV (抽样数据建模型) ──
    if n_cycles >= 3:
        residual_sigma = _compute_loocv_sigma_by_form(
            T_loocv, eps_loocv, cids_loocv, unique_cycles,
            T_base, form=form, poly_order=poly_order,
        )
    else:
        residual_sigma = _compute_insample_sigma_by_form(
            T_loocv, eps_loocv, T_base, form=form, poly_order=poly_order,
        )
        low_confidence = True

    # ── 2-4: 统计量始终用全量数据 (不受抽样影响) ──
    repeatability = _compute_repeatability(T, eps, cycle_ids_clean,
                                            unique_cycles, n_bins=15)

    hysteresis_max = _compute_hysteresis(T, eps, cycle_ids_clean, unique_cycles)

    if np.isnan(hysteresis_max):
        low_confidence = True

    noise_floor = _compute_noise_floor(eps, win=noise_win)

    # ── 5. 温度灵敏度: 从抽样数据建模型 (与 LOOCV 一致) ──
    try:
        cm_full = _build_model_for_form(T_loocv, eps_loocv, T_base, form=form, poly_order=poly_order)
        temp_sensitivity_max = _compute_temp_sensitivity_from_cm(cm_full)
    except ValueError:
        temp_sensitivity_max = 0.0

    # ── 6. 最坏单点 ──
    worst_case_single = hysteresis_max / 2.0 if not np.isnan(hysteresis_max) else float("nan")

    # ── %FS ──
    def pct(val: float) -> float:
        return (val / fs * 100.0) if not np.isnan(val) else float("nan")

    order_val = poly_order if (form == "poly" and poly_order is not None) else 0

    return CompensationMetrics(
        sensor=sensor,
        residual_sigma=residual_sigma,
        repeatability=repeatability,
        hysteresis_max=hysteresis_max,
        noise_floor=noise_floor,
        temp_sensitivity_max=temp_sensitivity_max,
        worst_case_single=worst_case_single,
        low_confidence=low_confidence,
        fs=fs,
        comp_form=form,
        poly_order=order_val,
        residual_sigma_pct_fs=pct(residual_sigma),
        repeatability_pct_fs=pct(repeatability),
        hysteresis_max_pct_fs=pct(hysteresis_max),
        noise_floor_pct_fs=pct(noise_floor),
        worst_case_single_pct_fs=pct(worst_case_single),
    )


# ═══════════════════════════════════════════════════════════════════════
# 评级
# ═══════════════════════════════════════════════════════════════════════


def grade_sensor(
    metrics: CompensationMetrics,
    thresholds: Optional[GradeThresholds] = None,
) -> SensorGrade:
    """根据补偿后指标对传感器进行出厂评级。

    评级规则 (按优先级):
    1. hysteresis_max_pct_fs > thr_hysteresis_fail_pct_fs → FAIL (废品拦截)
    2. NaN 滞回 (未测出) → 强制 low_confidence + 上限良
    3. residual_sigma_pct_fs > thr_sigma_pass_pct_fs → 不合格
    4. residual_sigma_pct_fs ≤ thr_sigma_excellent_pct_fs → 优
       (但 low_confidence 时上限为良 — 含 NaN 滞回和 <3 循环)
    5. residual_sigma_pct_fs ≤ thr_sigma_good_pct_fs → 良
    6. 其余 → 合格

    阈值以 %FS 为单位，适配任何满量程。出厂默认值:
    迟滞>5%FS→FAIL, 优≤1%FS, 良≤2%FS, 合格≤4%FS。

    Args:
        metrics: evaluate_compensation 输出
        thresholds: 评级阈值。None 时使用出厂默认值。

    Returns:
        SensorGrade
    """
    if thresholds is None:
        thresholds = GradeThresholds()

    reasons: list[str] = []
    grade: str = "合格"  # 默认
    passed: bool = True

    # ── 废品拦截: 迟滞超标 (%FS) ──
    hys_pct = metrics.hysteresis_max_pct_fs
    if not np.isnan(hys_pct) and \
       hys_pct > thresholds.thr_hysteresis_fail_pct_fs:
        grade = "FAIL"
        passed = False
        reasons.append(
            f"迟滞超标: hysteresis_max={metrics.hysteresis_max:.1f} με "
            f"({hys_pct:.1f}%FS) > {thresholds.thr_hysteresis_fail_pct_fs:.1f}%FS (废品拦截线)"
        )
        return SensorGrade(sensor=metrics.sensor, grade=grade,
                           passed=passed, reasons=reasons)

    # ── NaN 滞回 (未测出) → 强制降权 + 不得判优 ──
    if np.isnan(hys_pct):
        reasons.append(
            f"迟滞未测出 (hysteresis_max=NaN) — 升降支数据不足，"
            f"最高判良，请补做含往返的温度标定"
        )
        # 强制压到良及以下 (与 <3 循环同等待遇)
        if metrics.residual_sigma_pct_fs <= thresholds.thr_sigma_good_pct_fs:
            grade = "良"
            passed = True
            reasons.append(
                f"残余σ={metrics.residual_sigma:.1f} με "
                f"({metrics.residual_sigma_pct_fs:.1f}%FS) ≤ "
                f"{thresholds.thr_sigma_good_pct_fs:.1f}%FS"
            )
        elif metrics.residual_sigma_pct_fs <= thresholds.thr_sigma_pass_pct_fs:
            grade = "合格"
            passed = True
            reasons.append(
                f"残余σ={metrics.residual_sigma:.1f} με "
                f"({metrics.residual_sigma_pct_fs:.1f}%FS) ≤ "
                f"{thresholds.thr_sigma_pass_pct_fs:.1f}%FS"
            )
        else:
            grade = "不合格"
            passed = False
            reasons.append(
                f"残余σ超标: residual_sigma={metrics.residual_sigma:.1f} με "
                f"({metrics.residual_sigma_pct_fs:.1f}%FS) > "
                f"{thresholds.thr_sigma_pass_pct_fs:.1f}%FS"
            )
        return SensorGrade(sensor=metrics.sensor, grade=grade,
                           passed=passed, reasons=reasons)

    # ── 残余 σ 评级 (%FS) ──
    rs_pct = metrics.residual_sigma_pct_fs

    if rs_pct > thresholds.thr_sigma_pass_pct_fs:
        grade = "不合格"
        passed = False
        reasons.append(
            f"残余σ超标: residual_sigma={metrics.residual_sigma:.1f} με "
            f"({rs_pct:.1f}%FS) > {thresholds.thr_sigma_pass_pct_fs:.1f}%FS"
        )
    elif rs_pct <= thresholds.thr_sigma_excellent_pct_fs:
        if metrics.low_confidence:
            grade = "良"
            reasons.append(
                f"残余σ优秀 (residual_sigma={metrics.residual_sigma:.1f} με, "
                f"{rs_pct:.1f}%FS ≤ {thresholds.thr_sigma_excellent_pct_fs:.1f}%FS)"
            )
            cap_reason = _low_confidence_reason(metrics)
            if cap_reason:
                reasons.append(cap_reason)
        else:
            # ★ 优级额外门槛: 最坏单点 ≤ thr_worst_case_excellent_pct_fs
            wcs_pct = metrics.worst_case_single_pct_fs
            wcs_thr = thresholds.thr_worst_case_excellent_pct_fs
            if (wcs_thr > 0 and not np.isnan(wcs_pct)
                    and wcs_pct > wcs_thr):
                grade = "良"
                reasons.append(
                    f"残余σ优秀 (residual_sigma={metrics.residual_sigma:.1f} με, "
                    f"{rs_pct:.1f}%FS ≤ {thresholds.thr_sigma_excellent_pct_fs:.1f}%FS)"
                )
                reasons.append(
                    f"最坏单点超标: worst_case_single={metrics.worst_case_single:.1f} με "
                    f"({wcs_pct:.1f}%FS) > {wcs_thr:.1f}%FS，最高判良"
                )
            else:
                grade = "优"
                reasons.append(
                    f"残余σ优秀: residual_sigma={metrics.residual_sigma:.1f} με, "
                    f"{rs_pct:.1f}%FS ≤ {thresholds.thr_sigma_excellent_pct_fs:.1f}%FS"
                )
    elif rs_pct <= thresholds.thr_sigma_good_pct_fs:
        grade = "良"
        reasons.append(
            f"残余σ良好: residual_sigma={metrics.residual_sigma:.1f} με, "
            f"{rs_pct:.1f}%FS ≤ {thresholds.thr_sigma_good_pct_fs:.1f}%FS"
        )
    else:
        reasons.append(
            f"残余σ合格: residual_sigma={metrics.residual_sigma:.1f} με, "
            f"{rs_pct:.1f}%FS ≤ {thresholds.thr_sigma_pass_pct_fs:.1f}%FS"
        )

    return SensorGrade(sensor=metrics.sensor, grade=grade,
                       passed=passed, reasons=reasons)


def _low_confidence_reason(metrics: CompensationMetrics) -> str:
    """生成 low_confidence 降权原因文本。"""
    if np.isnan(metrics.hysteresis_max):
        return "迟滞未测出，最高判良"
    else:
        return "循环数不足 (<3)，最高判良"


# ═══════════════════════════════════════════════════════════════════════
# 内部实现
# ═══════════════════════════════════════════════════════════════════════


def _fill_unassigned_cycles(cycle_ids: np.ndarray) -> np.ndarray:
    """将 -1 向前填充为最近的有效循环编号。"""
    result = cycle_ids.copy()
    n = len(result)

    # 第一遍: 前向填充
    last_valid: int = 0
    for i in range(n):
        if result[i] == -1:
            result[i] = last_valid
        else:
            last_valid = int(result[i])

    return result


def _build_model_for_form(
    T: np.ndarray, eps: np.ndarray, T_base: float,
    *, form: str, poly_order: Optional[int],
) -> CompensationModel:
    """按 form/order 构建 compensation model。"""
    if form == "poly":
        order = poly_order or 4
        poly = fit_apparent_strain_poly(T, eps, T_base, order)
        return CompensationModel(form="poly", model=poly)
    else:
        lut = build_apparent_strain_lut(T, eps, T_base, bin_width=2.0, min_count=5)
        return CompensationModel(form="lut", model=lut)


def _compute_loocv_sigma_by_form(
    T: np.ndarray, eps: np.ndarray,
    cycle_ids: np.ndarray, unique_cycles: list[int],
    T_base: float,
    *, form: str, poly_order: Optional[int],
) -> float:
    """LOOCV: 每折用指定 form/order 建模型 → 在留出循环上算残余 std。

    这就是"该形式的真实残差"——residual of the form。
    """
    residuals_all: list[float] = []

    for held_out in unique_cycles:
        mask_train = cycle_ids != held_out
        mask_test = cycle_ids == held_out

        if np.sum(mask_train) < 10 or np.sum(mask_test) < 5:
            continue

        try:
            cm_train = _build_model_for_form(
                T[mask_train], eps[mask_train], T_base,
                form=form, poly_order=poly_order,
            )
        except ValueError:
            continue

        # apply to test set
        T_test = T[mask_test]
        eps_test = eps[mask_test]

        try:
            _eps_corr, _oob = apply_compensation_model(eps_test, T_test, cm_train)
        except ValueError:
            continue

        residuals_all.extend(_eps_corr.tolist())

    if len(residuals_all) < 3:
        return float("nan")

    return float(np.std(residuals_all))


def _compute_insample_sigma_by_form(
    T: np.ndarray, eps: np.ndarray, T_base: float,
    *, form: str, poly_order: Optional[int],
) -> float:
    """In-sample 残余 sigma (cycle < 3 时使用)。"""
    try:
        cm = _build_model_for_form(T, eps, T_base, form=form, poly_order=poly_order)
        eps_corr, _oob = apply_compensation_model(eps, T, cm)
        return float(np.std(eps_corr))
    except ValueError:
        return float(np.std(eps))


def _compute_repeatability(
    T: np.ndarray, eps: np.ndarray,
    cycle_ids: np.ndarray, unique_cycles: list[int],
    n_bins: int = 15,
) -> float:
    """各 T 箱内循环间 std 的最大值。O(N) 向量化，与循环数无关。"""
    if len(unique_cycles) < 2:
        return 0.0

    T_min = float(np.min(T))
    T_max = float(np.max(T))
    if T_max - T_min < 1.0:
        return 0.0

    import pandas as pd
    bins = np.linspace(T_min, T_max, n_bins + 1)
    # np.digitize: bins[i-1] <= x < bins[i] → 减 1 得 0-indexed
    # (与旧 for-loop 的 [bins[i], bins[i+1]) 左闭右开语义一致)
    t_bin = np.clip(np.digitize(T, bins) - 1, 0, n_bins - 1)

    df_tmp = pd.DataFrame({"bin": t_bin, "cycle": cycle_ids, "eps": eps})
    # mean per (bin, cycle), require >= 2 points
    means = df_tmp.groupby(["bin", "cycle"])["eps"].agg(["mean", "count"])
    valid = means[means["count"] >= 2]["mean"]  # type: ignore[call-overload]
    if len(valid) < 2:
        return 0.0
    # std of cycle means within each bin → worst bin
    # (ddof=0 to match legacy np.std default)
    bin_stds = valid.groupby("bin").std(ddof=0)
    return float(bin_stds.max())


def _compute_hysteresis(
    T: np.ndarray, eps: np.ndarray,
    cycle_ids: np.ndarray, unique_cycles: list[int],
) -> float:
    """稳健温度分箱迟滞: |mean(ε_up) − mean(ε_down)| 每bin取max。

    算法 (Phase 5 重构):
    1. 排除首循环 (瞬态, 设备未热稳定)
    2. 对每个循环, 平滑 T 找峰值区域 → 升/降支 + 死区
    3. 1.5°C 温度分箱, 每支每bin ≥20 点
    4. 去边界 bin (T_min+2°C, T_max−2°C)
    5. hysteresis(bin) = |mean(ε_up) − mean(ε_down)|
    6. hysteresis_max = 全循环全bin的 max

    旧算法 (argmax 一刀切 + 20 点插值取单点极大) 被瞬态首循环 + 噪声采样
    点系统性放大 ~2×，已废弃。
    """
    if len(unique_cycles) < 2:
        # 单循环无法区分升/降支 → NaN
        if len(unique_cycles) == 1:
            return float("nan")
        return float("nan")

    # ── 排除首循环 (瞬态) ──
    valid_cycles = unique_cycles[1:] if len(unique_cycles) >= 2 else unique_cycles

    T_range_global = float(np.max(T) - np.min(T))
    T_global_min = float(np.min(T))
    T_global_max = float(np.max(T))

    bin_width = 1.5       # °C
    deadband_C = 2.0      # °C, 近峰死区
    boundary_margin_C = 2.0  # °C, 丢弃 T_min/T_max 边界
    smooth_win = max(200, min(2000, len(T) // 200))

    # 点数阈值：大工程量数据用严格值；短测试数据自动放宽
    avg_cycle_len = int(np.median([int(np.sum(cycle_ids == c)) for c in valid_cycles]))
    # 预估每bin每支点数 ≈ avg_cycle_len / (T_range_global / bin_width)
    pts_per_bin_per_branch_est = max(1, int(avg_cycle_len / max(1, T_range_global / bin_width)))
    min_pts_per_branch = max(3, min(30, pts_per_bin_per_branch_est // 2))
    min_total_per_bin = max(6, min(80, pts_per_bin_per_branch_est))

    all_bin_hys: list[float] = []

    for ci in valid_cycles:
        mask = cycle_ids == ci
        n_mask = int(np.sum(mask))
        if n_mask < 100:  # too few points
            continue

        T_c = T[mask]
        eps_c = eps[mask]
        n_c = len(T_c)

        # ── 平滑 T 找峰值 ──
        try:
            import pandas as pd
            T_smooth = pd.Series(T_c).rolling(
                window=min(smooth_win, n_c // 4),
                center=True, min_periods=1).median().values
        except Exception:
            T_smooth = T_c.copy()

        peak_i = int(np.argmax(T_smooth))
        T_peak = T_smooth[peak_i]

        # ── 死区: 峰值附近 dT ≈ 0 → 不参与任一支 ──
        # 左/右分别找第一个 T < T_peak - deadband_C 的位置
        up_end = peak_i
        for i in range(peak_i, 0, -1):
            if T_smooth[i] < T_peak - deadband_C:
                up_end = i
                break
        down_start = peak_i
        for i in range(peak_i, n_c):
            if T_smooth[i] < T_peak - deadband_C:
                down_start = i
                break

        if up_end < 10 or down_start > n_c - 10:
            continue  # deadband too wide or peak at edge

        up_mask_local = np.arange(n_c) <= up_end
        down_mask_local = np.arange(n_c) >= down_start

        T_up = T_c[up_mask_local]
        eps_up = eps_c[up_mask_local]
        T_down = T_c[down_mask_local]
        eps_down = eps_c[down_mask_local]

        if len(T_up) < min_pts_per_branch or len(T_down) < min_pts_per_branch:
            continue

        # ── 重叠温区 ──
        T_ol_min = max(float(np.min(T_up)), float(np.min(T_down)))
        T_ol_max = min(float(np.max(T_up)), float(np.max(T_down)))

        # 去边界
        T_ol_min += boundary_margin_C
        T_ol_max -= boundary_margin_C

        if T_ol_max - T_ol_min < bin_width:
            continue

        # ── 分箱 ──
        bins = np.arange(T_ol_min, T_ol_max, bin_width)
        if len(bins) < 2:
            continue

        for b in range(len(bins) - 1):
            lo, hi = bins[b], bins[b + 1]
            in_up = (T_up >= lo) & (T_up < hi)
            in_down = (T_down >= lo) & (T_down < hi)
            n_up, n_down = int(np.sum(in_up)), int(np.sum(in_down))
            if (n_up >= min_pts_per_branch and n_down >= min_pts_per_branch
                    and n_up + n_down >= min_total_per_bin):
                d = abs(float(np.mean(eps_up[in_up])) - float(np.mean(eps_down[in_down])))
                all_bin_hys.append(d)

    if not all_bin_hys:
        return float("nan")

    return float(np.max(all_bin_hys))


def _compute_noise_floor(eps: np.ndarray, win: int = 120) -> float:
    """高频噪声底: 减去滚动中位数后的 std (pandas 向量化)。"""
    n = len(eps)
    if n < win:
        return float(np.std(eps))

    import pandas as pd
    trend = pd.Series(eps).rolling(window=win, center=True, min_periods=1).median()
    residual = eps - trend.values
    return float(np.std(residual))


def _compute_temp_sensitivity_from_cm(cm: CompensationModel) -> float:
    """从 CompensationModel 计算最大温度灵敏度 max|deps_app/dT| (ue/degC)。"""
    if cm.form == "lut":
        lut: ApparentStrainLUT = cm.model  # type: ignore[assignment]
        if len(lut.T_grid) < 2:
            return 0.0
        T_arr = np.array(lut.T_grid, dtype=np.float64)
        eps_arr = np.array(lut.eps_app, dtype=np.float64)
    else:
        poly: ApparentStrainPoly = cm.model  # type: ignore[assignment]
        # dense sample within range
        T_arr = np.linspace(poly.T_min, poly.T_max, 200, dtype=np.float64)
        x = T_arr - poly.T_base
        eps_arr = np.polyval(poly.coeffs, x)

    dT = np.diff(T_arr)
    valid = dT > 1e-9
    if not np.any(valid):
        return 0.0

    dEps_dT = np.abs(np.diff(eps_arr)[valid] / dT[valid])
    return float(np.max(dEps_dT))


# ═══════════════════════════════════════════════════════════════════════
# 补偿形式比较 (选型视图)
# ═══════════════════════════════════════════════════════════════════════


def compare_compensation_forms(
    T: np.ndarray,
    eps: np.ndarray,
    cycle_ids: np.ndarray,
    *,
    fs: float,
    candidates: tuple = ("lut", "poly2", "poly4"),
    noise_win: int = 120,
    subsample_step: Optional[int] = None,
) -> dict:
    """比较不同补偿形式的 LOOCV 残余 sigma。

    返回每种候选形式的 residual_sigma，供选型视图用。
    "poly2" → form="poly", poly_order=2; "poly4" → form="poly", poly_order=4.

    ★ Phase 1 优化: 共享状态 (fill_unassigned + subsample + unique_cycles)
    只计算一次，纯 LOOCV 按候选形式分别计算。不再全量调 evaluate_compensation，
    避免 repeatability/hysteresis/noise_floor 的 3× 冗余计算。

    Args:
        T: 温度序列 (degC), shape (N,)
        eps: 解耦应变序列 (ue), shape (N,)
        cycle_ids: 循环编号数组
        fs: 满量程 (ue)
        candidates: 候选形式元组，如 ("lut", "poly2", "poly4")
        noise_win: 高频噪声窗口 (当前未使用，保留签名兼容)
        subsample_step: 均匀抽样步长 (None=不抽样)，仅影响 LOOCV/建模

    Returns:
        {form_key: residual_sigma (ue), ...}
    """
    T_arr = np.asarray(T, dtype=np.float64)
    eps_arr = np.asarray(eps, dtype=np.float64)
    cids_arr = np.asarray(cycle_ids, dtype=int)

    if len(T_arr) == 0 or len(eps_arr) == 0:
        return {c: float("nan") for c in candidates}

    # ── 共享状态 (计算一次，所有候选复用) ──
    cids_clean = _fill_unassigned_cycles(cids_arr)
    unique_cycles = sorted(set(int(c) for c in cids_clean))
    n_cycles = len(unique_cycles)
    T_base = float(np.median(T_arr))

    # ── 抽样: LOOCV/建模用子集 ──
    if subsample_step is not None and subsample_step > 1:
        T_use = T_arr[::subsample_step]
        eps_use = eps_arr[::subsample_step]
        cids_use = cids_clean[::subsample_step]
    else:
        T_use = T_arr
        eps_use = eps_arr
        cids_use = cids_clean

    results: dict = {}
    for cand in candidates:
        try:
            if cand == "lut":
                form = "lut"
                poly_order = None
            elif cand.startswith("poly"):
                form = "poly"
                poly_order = int(cand[4:])
            else:
                results[cand] = float("nan")
                continue

            if n_cycles >= 3:
                sigma = _compute_loocv_sigma_by_form(
                    T_use, eps_use, cids_use, unique_cycles,
                    T_base, form=form, poly_order=poly_order,
                )
            else:
                sigma = _compute_insample_sigma_by_form(
                    T_use, eps_use, T_base, form=form, poly_order=poly_order,
                )
            results[cand] = sigma
        except Exception:
            results[cand] = float("nan")

    return results
