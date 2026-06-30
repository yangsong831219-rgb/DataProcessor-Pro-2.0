"""传感器标定计算引擎 — 纯函数，无 UI 依赖

温度标定: 连续波长文件 → 平台检测 → KMeans 温度映射 → 灵敏度回归 → 解耦诊断
应变标定: 手填表驱动 → 理论应变 → 灵敏度/线性度/重复性/迟滞/双栅分析

所有模块均可独立单测，不依赖 PyQt6 或任何 UI 组件。
"""

from .step_extractor import detect_plateaus
from .temperature_calibration import (
    load_continuous,
    assign_setpoints,
    regress_sensitivity,
    decouple,
    compare_given_vs_measured,
)
from .strain_calibration import (
    compute_theoretical_strain,
    StrainCalibrationConfig,
    StrainCalibrationResult,
    calibrate_strain,
)
from .export_utils import export_temperature_excel, export_strain_excel

__all__ = [
    # 平台检测
    "detect_plateaus",
    # 温度标定
    "load_continuous",
    "assign_setpoints",
    "regress_sensitivity",
    "decouple",
    "compare_given_vs_measured",
    # 应变标定
    "compute_theoretical_strain",
    "StrainCalibrationConfig",
    "StrainCalibrationResult",
    "calibrate_strain",
    # 导出
    "export_temperature_excel",
    "export_strain_excel",
]
