"""DataProcessor Pro 核心数据模型

严格 Type Hints 加固版本。所有业务逻辑与原 main.py 完全一致，
仅增加了完整的类型标注、TypedDict 配置结构、以及 Protocol 接口定义。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import (
    Any, Callable, ClassVar, Dict, List, Literal, Optional, Protocol,
    Sequence, TypedDict, Union,
)

import numpy as np
import pandas as pd


# ============ 字面量类型 ============

SensorType = Literal[
    'strain', 'temperature', 'displacement', 'inclination',
    'pressure', 'decoupling', 'strain_cal', 'temp_cal',
]

CleaningRuleType = Literal['adjacent_diff', 'nan', 'range', 'negative', 'zero']
FillMethod = Literal['linear', 'mean', 'forward', 'backward', 'custom']


# ============ TypedDict 配置结构 ============

class DecouplingConfig(TypedDict):
    """双参量解耦矩阵配置"""
    fbg1: str          # FBG1 ID (λ1)
    fbg2: str          # FBG2 ID (λ2)
    Ke1: float         # 应变系数1 (pm/με)
    Ke2: float         # 应变系数2 (pm/με)
    KT1: float         # 温度系数1 (pm/°C)
    KT2: float         # 温度系数2 (pm/°C)


class SensorTypeParams(TypedDict, total=False):
    """传感器类型参数定义"""
    name: str
    unit: str
    need_temp_compensation: bool


class TimeDomainResult(TypedDict):
    """时域分析结果"""
    mean: float
    max: float
    min: float
    rms: float
    std: float


class FrequencyDomainResult(TypedDict):
    """频域分析结果"""
    frequencies: List[float]
    magnitudes: List[float]
    dominant_freq: float


class ConfigDict(TypedDict, total=False):
    """全局配置 JSON 结构 (v2.0)"""
    version: str
    fbgs: List[dict]
    sensors: List[dict]
    project_files: List[dict]
    data_tab: dict
    cleaning_tab: dict
    analysis_tab: dict


# ============ Protocol 接口 ============

class SupportsValidityCheck(Protocol):
    """可校验有效性的数据点协议"""
    def is_valid(self, wavelength: float) -> bool: ...


class SupportsUnitConversion(Protocol):
    """可获取物理单位的对象协议"""
    def get_unit(self) -> str: ...
    def get_name(self) -> str: ...


# ============ 数据模型 ============

@dataclass
class GlobalParameter:
    """全局参数 / 共享常量定义

    注入到所有传感器公式的计算命名空间中，作为 SSOT 变量池。
    local_dict 策略: 全局参数打底，传感器局部常量覆盖。
    """
    name: str
    value: float
    unit: str = ''
    description: str = ''


@dataclass
class DataTemplate:
    """数据文件格式定义

    描述输入数据文件的结构：分隔符、跳过行数、列定义等。
    columns 为列描述字典列表，每项包含 name, comment, data_type 等键。
    """
    id: str
    name: str
    file_format: str
    delimiter: str = '\t'
    skip_rows: int = 1
    columns: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'file_format': self.file_format,
            'delimiter': self.delimiter,
            'skip_rows': self.skip_rows,
            'columns': [
                {
                    'name': c['name'],
                    'comment': c.get('comment', ''),
                    'data_type': c.get('data_type', 'numeric'),
                }
                for c in self.columns
            ],
        }


@dataclass
class CleaningRule:
    """数据清洗规则定义

    支持类型: adjacent_diff (相邻差值), nan (缺失值), range (范围),
             negative (负值), zero (零值)
    填充方式: linear (线性插值), mean (均值填充), forward (前向填充),
             backward (后向填充), custom (自定义值)
    """
    name: str
    rule_type: CleaningRuleType
    enabled: bool = True
    threshold: Optional[float] = None   # adjacent_diff: 最大允许差值
    min_value: Optional[float] = None   # range: 最小值
    max_value: Optional[float] = None   # range: 最大值
    fill_method: FillMethod = 'linear'
    fill_value: Optional[float] = None  # custom: 自定义填充值


@dataclass
class FBG:
    """FBG 光纤光栅定义 — 对应一列原始波长数据

    Attributes:
        id: FBG 标识符，如 "W1"
        channel: 对应的波长列名，如 "FBG_A1"
        wavelength_min: 波长下限 (nm)，用于校验
        wavelength_max: 波长上限 (nm)，用于校验
    """
    id: str
    channel: str
    wavelength_min: Optional[float] = 1520.0
    wavelength_max: Optional[float] = 1590.0

    def is_valid(self, wavelength: float) -> bool:
        """判断波长是否在有效范围内"""
        if pd.isna(wavelength):
            return False
        if self.wavelength_min is not None and self.wavelength_max is not None:
            return self.wavelength_min <= wavelength <= self.wavelength_max
        return True


@dataclass
class Sensor:
    """虚拟传感器定义

    通过公式 (如 "W1 * k1") 将原始波长差值转换为物理量 (με, °C, mm 等)。

    Attributes:
        id: 传感器标识符
        sensor_type: 传感器类型
        formula: 数学表达式，如 "W1 * k1" 或 "W1 * Ke1 - W2 * Ke2"
        constants: 公式常量，如 {"k1": 1000.0}
        active: 是否参与计算
        decoupling_config: 双参量解耦矩阵配置 (仅 decoupling 类型)
        location: 传感器物理安装位置
    """
    id: str
    sensor_type: SensorType
    formula: Optional[str] = None
    constants: Dict[str, float] = field(default_factory=dict)
    active: bool = True
    decoupling_config: Optional[DecouplingConfig] = None
    location: str = ''

    # 类变量：传感器类型 → 参数映射 (ClassVar 避免被 dataclass 当作实例字段)
    TYPE_PARAMS: ClassVar[Dict[SensorType, SensorTypeParams]] = {
        'strain':           {'name': '应变',   'unit': 'με',      'need_temp_compensation': True},
        'temperature':      {'name': '温度',   'unit': '°C',      'need_temp_compensation': False},
        'displacement':     {'name': '位移',   'unit': 'mm',      'need_temp_compensation': False},
        'inclination':      {'name': '倾角',   'unit': '°',       'need_temp_compensation': False},
        'pressure':         {'name': '压力',   'unit': 'MPa',     'need_temp_compensation': False},
        'decoupling':       {'name': '双参量解耦', 'unit': 'με / °C', 'need_temp_compensation': False},
        'strain_cal':       {'name': '应变标定', 'unit': 'με',    'need_temp_compensation': False},
        'temp_cal':         {'name': '温度标定', 'unit': '°C',    'need_temp_compensation': False},
    }

    def get_unit(self) -> str:
        return self.TYPE_PARAMS.get(self.sensor_type, {}).get('unit', '')

    def get_name(self) -> str:
        return self.TYPE_PARAMS.get(self.sensor_type, {}).get('name', self.sensor_type)


# ============ 传感器系统 ============

class SensorSystem:
    """传感器系统管理器

    管理 FBG 列表 + Sensor 列表，执行物理量计算。

    计算流程:
    1. 匹配 FBG channel 到 DataFrame 列
    2. 计算每行波长差值 Δλ = λ_current - λ_reference
    3. 对每个活跃 Sensor，执行公式求值或解耦矩阵计算
    """

    def __init__(self) -> None:
        self.fbgs: List[FBG] = []
        self.sensors: List[Sensor] = []
        self.reference_row: int = 0
        self.global_parameters: Dict[str, Any] = {}

    def set_reference_row(self, row_index: int) -> None:
        self.reference_row = row_index

    def add_fbg(self, fbg: FBG) -> None:
        self.fbgs.append(fbg)

    def add_sensor(self, sensor: Sensor) -> None:
        self.sensors.append(sensor)

    def calculate(
        self,
        df: pd.DataFrame,
        fbg_columns: Optional[List[str]] = None,
    ) -> Dict[str, List[Optional[float]]]:
        """根据原始波长数据计算所有传感器值

        Args:
            df: 原始波长 DataFrame
            fbg_columns: 保留参数，当前未使用（向后兼容）

        Returns:
            {sensor_id: [value, ...]}  — 每列为一个传感器的物理量序列
        """
        results: Dict[str, List[Optional[float]]] = {}

        if len(df) == 0:
            return results

        ref_row = self.reference_row if self.reference_row < len(df) else 0

        # 构建列数据字典
        column_data: Dict[str, np.ndarray] = {}
        for col in df.columns:
            column_data[col] = df[col].values

        # 自动匹配FBG通道到实际数据列
        wavelength_cols = [
            c for c in df.columns
            if ('波长' in str(c) or 'wavelength' in str(c).lower()
                or str(c).upper().startswith('FBG')
                or (str(c).upper().startswith('W') and str(c)[1:].isdigit()))
        ]
        if not wavelength_cols:
            wavelength_cols = [
                c for c in df.columns
                if '时间' not in str(c) and 'timestamp' not in str(c).lower()
                and pd.api.types.is_numeric_dtype(df[c])
            ]

        fbg_initial: Dict[str, float] = {}
        fbg_delta: Dict[str, List[Optional[float]]] = {}

        print("=" * 60)
        print("[诊断] FBG通道匹配:")
        print(f"  DataFrame 列: {list(df.columns)}")
        print(f"  检测到的波长列: {wavelength_cols}")
        print(f"  已注册 FBG: {[(f.id, f.channel) for f in self.fbgs]}")

        for i, fbg in enumerate(self.fbgs):
            # 先尝试按channel精确匹配
            if fbg.channel in column_data:
                matched_col: str = fbg.channel
            elif i < len(wavelength_cols):
                matched_col = str(wavelength_cols[i])
                fbg.channel = matched_col
            else:
                print(f"  FBG {fbg.id}: 未匹配到任何列!")
                continue

            col_values = column_data[matched_col]
            initial: float = float(col_values[ref_row]) if ref_row < len(col_values) else float(col_values[0])
            fbg_initial[fbg.id] = initial

            # 计算每行与初始值的差值
            delta: List[Optional[float]] = []
            for w in col_values:
                if pd.isna(w) or pd.isna(initial):
                    delta.append(None)
                else:
                    delta.append(float(w) - initial)
            fbg_delta[fbg.id] = delta

            # 同时用 channel（列名）作为别名
            if str(matched_col) != str(fbg.id):
                fbg_delta[str(matched_col)] = delta

            valid_deltas = [d for d in delta if d is not None][:5]
            print(
                f"  FBG {fbg.id} → 列 '{matched_col}' | "
                f"初始波长: {initial:.6f} | "
                f"前5个差值(nm): {[f'{d:.6f}' if d is not None else 'None' for d in valid_deltas]}"
            )
        print("=" * 60)

        # 兜底：从数据列自动检测 FBG_ 列
        if not fbg_delta:
            self._auto_build_delta(column_data, ref_row, fbg_delta)

        # 计算每个传感器
        for sensor in self.sensors:
            if not sensor.active:
                continue

            try:
                if sensor.sensor_type == 'decoupling' and sensor.decoupling_config is not None:
                    strain_r, temp_r = self._evaluate_decoupling(
                        sensor.decoupling_config, fbg_delta, len(df)
                    )
                    results[f'{sensor.id}_应变(με)'] = strain_r
                    results[f'{sensor.id}_温度(°C)'] = temp_r
                else:
                    result = self._evaluate_sensor_formula(
                        sensor.formula or '',
                        sensor.constants,
                        fbg_delta,
                        len(df),
                        self.global_parameters,
                    )
                    results[sensor.id] = result
            except Exception as e:
                print(f"计算传感器 {sensor.id} 失败: {e}")
                import traceback
                traceback.print_exc()
                results[sensor.id] = [None] * len(df)

        return results

    # ── 私有方法 ──

    def _auto_build_delta(
        self,
        column_data: Dict[str, np.ndarray],
        ref_row: int,
        fbg_delta: Dict[str, List[Optional[float]]],
    ) -> None:
        """兜底方案：自动从 FBG_ 前缀列构建 fbg_delta"""
        auto_cols = [str(c) for c in column_data if str(c).upper().startswith('FBG_')]
        if not auto_cols:
            return
        print(f"[自动兜底] fbg_delta 为空，从数据列自动构建: {auto_cols}")
        for col in auto_cols:
            col_values = column_data[col]
            initial = float(col_values[ref_row]) if ref_row < len(col_values) else float(col_values[0])
            delta: List[Optional[float]] = []
            for w in col_values:
                if pd.isna(w) or pd.isna(initial):
                    delta.append(None)
                else:
                    delta.append(float(w) - initial)
            fbg_delta[col] = delta
            idx = auto_cols.index(col) + 1
            fbg_delta[f'W{idx}'] = delta
            print(f"  列 '{col}' → fbg_delta['{col}'] + fbg_delta['W{idx}']")

    def _evaluate_sensor_formula(
        self,
        formula: str,
        constants: Dict[str, float],
        fbg_delta: Dict[str, List[Optional[float]]],
        n_rows: int,
        global_params: Optional[Dict[str, Any]] = None,
    ) -> List[Optional[float]]:
        """计算公式: "W1 * k1" → 将 W1 替换为波长差值, k1 替换为常量

        常量合并策略: 全局参数打底，局部常量覆盖 (局部优先)。
        使用正则单词边界替换，避免 k1 误匹配 k10。
        NaN 安全：eval 前将 None 转为 float('nan')。
        """
        expr = formula
        # 合并: 全局参数打底，局部常量覆盖
        merged: Dict[str, Any] = {}
        if global_params:
            merged.update(global_params)
        merged.update(constants or {})
        for name, value in merged.items():
            if isinstance(value, dict):
                value = value.get('value', 0)
            expr = re.sub(r'\b' + re.escape(name) + r'\b', f'({value})', expr)

        # 检查未定义常量
        unresolved_k = re.findall(r'\b[kK]\d+\b', expr)
        if unresolved_k:
            print(
                f"警告: 公式中常量 {unresolved_k} 未定义，默认按 1.0 计算。"
                f"请在传感器配置中添加常量。"
            )
            for k_var in set(unresolved_k):
                expr = re.sub(r'\b' + re.escape(k_var) + r'\b', '(1.0)', expr)

        result: List[Optional[float]] = []
        for i in range(n_rows):
            row_vars: Dict[str, float] = {}
            for fbg_id, delta in fbg_delta.items():
                v = delta[i] if i < len(delta) else None
                row_vars[fbg_id] = float('nan') if v is None else float(v)

            try:
                val = eval(expr, {"__builtins__": {}}, row_vars)
                if val is None or (isinstance(val, float) and (val != val or abs(val) == float('inf'))):
                    result.append(None)
                else:
                    result.append(val)
            except Exception as e:
                print(f"行 {i} 计算失败: {expr} -> {e}")
                result.append(None)

        return result

    def _evaluate_decoupling(
        self,
        config: DecouplingConfig,
        fbg_delta: Dict[str, List[Optional[float]]],
        n_rows: int,
    ) -> tuple[List[Optional[float]], List[Optional[float]]]:
        """双参量解耦：矢量化矩阵求解应变和温度

        方程:
          Δλ1 = Ke1 * Δε + KT1 * ΔT
          Δλ2 = Ke2 * Δε + KT2 * ΔT

        求解:
          D = Ke1*KT2 - Ke2*KT1
          Δε = (Δλ1*KT2 - Δλ2*KT1) / D
          ΔT = (Δλ2*Ke1 - Δλ1*Ke2) / D

        波长差值从 nm 转换为 pm (×1000) 以避免浮点精度丢失。
        """
        Ke1 = float(config['Ke1'])
        Ke2 = float(config['Ke2'])
        KT1 = float(config['KT1'])
        KT2 = float(config['KT2'])

        delta_source1 = fbg_delta.get(config['fbg1'], [0.0] * n_rows)
        delta_source2 = fbg_delta.get(config['fbg2'], [0.0] * n_rows)

        d1 = np.array([v if v is not None else np.nan for v in delta_source1], dtype=np.float64) * 1000.0
        d2 = np.array([v if v is not None else np.nan for v in delta_source2], dtype=np.float64) * 1000.0

        D = Ke1 * KT2 - Ke2 * KT1

        if abs(D) < 1e-9:
            print(f"[解耦] 传感器行列式接近零 (D={D:.6e})，矩阵不可逆。")
            return (
                [None if np.isnan(x) else x for x in np.full(n_rows, np.nan)],
                [None if np.isnan(x) else x for x in np.full(n_rows, np.nan)],
            )

        strain = (d1 * KT2 - d2 * KT1) / D
        temp = (d2 * Ke1 - d1 * Ke2) / D

        return (strain.tolist(), temp.tolist())


# ============ 数据分析函数 ============

def time_domain_analysis(data: Sequence[float]) -> TimeDomainResult:
    """时域分析"""
    arr = np.array(data, dtype=np.float64)
    return {
        'mean': float(np.mean(arr)),
        'max': float(np.max(arr)),
        'min': float(np.min(arr)),
        'rms': float(np.sqrt(np.mean(arr ** 2))),
        'std': float(np.std(arr)),
    }


def frequency_domain_analysis(
    data: Sequence[float],
    sampling_rate: float = 1000.0,
) -> FrequencyDomainResult:
    """频域分析 - FFT"""
    arr = np.array(data, dtype=np.float64)
    n = len(arr)
    freqs = np.fft.fftfreq(n, 1 / sampling_rate)
    fft_vals = np.fft.fft(arr)
    magnitudes = np.abs(fft_vals[:n // 2])
    return {
        'frequencies': freqs[:n // 2].tolist(),
        'magnitudes': magnitudes.tolist(),
        'dominant_freq': float(freqs[np.argmax(magnitudes)]),
    }


def apply_filter(
    data: Sequence[float],
    filter_type: Literal['lowpass', 'highpass', 'bandpass'],
    cutoff: Union[float, Sequence[float]],
    sampling_rate: float,
) -> List[float]:
    """巴特沃斯滤波"""
    from scipy import signal as scipy_signal
    nyquist = sampling_rate / 2.0
    if filter_type == 'lowpass':
        b, a = scipy_signal.butter(4, float(cutoff) / nyquist, btype='low')
    elif filter_type == 'highpass':
        b, a = scipy_signal.butter(4, float(cutoff) / nyquist, btype='high')
    elif filter_type == 'bandpass':
        cutoffs = list(cutoff) if not isinstance(cutoff, (int, float)) else [float(cutoff)]
        b, a = scipy_signal.butter(4, [cutoffs[0] / nyquist, cutoffs[1] / nyquist], btype='band')
    else:
        return list(data)
    return scipy_signal.filtfilt(b, a, data).tolist()


# ============ 默认数据模板 ============

DEFAULT_TEMPLATES: List[DataTemplate] = [
    DataTemplate('enlight_type', 'ENLIGHT (光纤传感)', 'enlight', '\t', 104, [
        {'name': '时间', 'comment': '时间戳', 'data_type': 'time'},
    ]),
]
