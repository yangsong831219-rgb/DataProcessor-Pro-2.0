"""DataProcessor Pro 全局应用状态

单一数据源 (Single Source of Truth)，将散落在 UI 控件中的状态集中管理。
UI 层变成"笨组件"：只负责渲染和发送信号，所有数据从 AppState 读取。

设计原则 (Matt Pocock 风格):
- @dataclass 不可变数据：状态通过替换整体对象来更新，不原地修改
- 状态与视图隔离：UI 控件通过信号通知意图，状态层决定是否/如何变更
- JSON 序列化/反序列化内聚：to_dict / from_dict 在状态层，不在 UI 层
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from core.models import FBG, Sensor


# ============ 子状态模块 ============

@dataclass
class DataTabState:
    """数据文件 Tab 状态"""
    file_path: str = ''
    template_id: Optional[str] = None
    template_name: Optional[str] = None
    file_header_lines: List[str] = field(default_factory=list)
    current_columns: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'file_path': self.file_path,
            'template_id': self.template_id,
            'template_name': self.template_name,
            'file_header_lines': self.file_header_lines,
            'current_columns': self.current_columns,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'DataTabState':
        return cls(
            file_path=d.get('file_path', ''),
            template_id=d.get('template_id'),
            template_name=d.get('template_name'),
            file_header_lines=d.get('file_header_lines', []),
            current_columns=d.get('current_columns', []),
        )


@dataclass
class CleaningTabState:
    """数据清洗 Tab 状态"""
    adjacent_enabled: bool = True
    diff_threshold: float = 1.0
    nan_enabled: bool = True
    fill_method: str = 'linear'
    custom_fill_value: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'adjacent_enabled': self.adjacent_enabled,
            'diff_threshold': self.diff_threshold,
            'nan_enabled': self.nan_enabled,
            'fill_method': self.fill_method,
            'custom_fill_value': self.custom_fill_value,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'CleaningTabState':
        return cls(
            adjacent_enabled=d.get('adjacent_enabled', True),
            diff_threshold=d.get('diff_threshold', 1.0),
            nan_enabled=d.get('nan_enabled', True),
            fill_method=d.get('fill_method', 'linear'),
            custom_fill_value=d.get('custom_fill_value', 0.0),
        )


@dataclass
class AnalysisTabState:
    """数据分析 Tab 状态"""
    data_source: str = '原始数据'
    range_type: str = '序号范围'
    range_start: int = 0
    range_end: int = 999999
    sensor_results: Dict[str, List[Optional[float]]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        import numpy as np
        serializable: Dict[str, List[Optional[float]]] = {}
        for key, values in self.sensor_results.items():
            arr = np.array(list(values), dtype=np.float64)
            arr[np.isnan(arr)] = None
            serializable[key] = [None if v is None else float(v) for v in arr]
        return {
            'data_source': self.data_source,
            'range_type': self.range_type,
            'range_start': self.range_start,
            'range_end': self.range_end,
            'sensor_results': serializable,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'AnalysisTabState':
        import numpy as np
        saved = d.get('sensor_results', {})
        restored: Dict[str, List[Optional[float]]] = {}
        for key, values in saved.items():
            restored[key] = [float('nan') if v is None else float(v) for v in values]
        return cls(
            data_source=d.get('data_source', '原始数据'),
            range_type=d.get('range_type', '序号范围'),
            range_start=d.get('range_start', 0),
            range_end=d.get('range_end', 999999),
            sensor_results=restored,
        )


# ============ 根状态 ============

@dataclass
class AppState:
    """全局应用状态 (Single Source of Truth)

    所有模块状态集中管理。UI 层通过信号通知意图，
    状态层决定变更策略，然后驱动 UI 刷新。

    Usage:
        state = AppState()
        # 局部更新 (返回新对象，不原地修改)
        new_state = replace(state, data_tab=replace(state.data_tab, file_path='/path/to/data.txt'))
        # 同步到 UI
        sync_state_to_ui(new_state, main_window)
    """
    version: str = '2.0'

    # 传感器系统
    fbgs: List[FBG] = field(default_factory=list)
    sensors: List[Sensor] = field(default_factory=list)

    # 项目文件
    project_files: List[str] = field(default_factory=list)

    # 各模块子状态
    data_tab: DataTabState = field(default_factory=DataTabState)
    cleaning_tab: CleaningTabState = field(default_factory=CleaningTabState)
    analysis_tab: AnalysisTabState = field(default_factory=AnalysisTabState)

    # ── 序列化 ──

    def to_dict(self) -> Dict[str, Any]:
        """完整序列化为 JSON-compatible dict"""
        return {
            'version': self.version,
            'fbgs': [
                {
                    'id': f.id, 'channel': f.channel,
                    'wavelength_min': f.wavelength_min,
                    'wavelength_max': f.wavelength_max,
                }
                for f in self.fbgs
            ],
            'sensors': [
                {
                    'id': s.id, 'sensor_type': s.sensor_type,
                    'formula': s.formula, 'constants': s.constants,
                    'active': s.active, 'decoupling_config': s.decoupling_config,
                    'location': s.location,
                }
                for s in self.sensors
            ],
            'project_files': list(self.project_files),
            'data_tab': self.data_tab.to_dict(),
            'cleaning_tab': self.cleaning_tab.to_dict(),
            'analysis_tab': self.analysis_tab.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'AppState':
        """从 JSON dict 反序列化"""
        fbgs = [
            FBG(
                id=f['id'], channel=f['channel'],
                wavelength_min=f.get('wavelength_min', f.get('k1')),
                wavelength_max=f.get('wavelength_max', f.get('k2')),
            )
            for f in d.get('fbgs', [])
        ]
        sensors = [
            Sensor(
                id=s['id'], sensor_type=s['sensor_type'],
                formula=s.get('formula'), constants=s.get('constants', {}),
                active=s.get('active', True),
                decoupling_config=s.get('decoupling_config'),
                location=s.get('location', ''),
            )
            for s in d.get('sensors', [])
        ]
        return cls(
            version=d.get('version', '1.0'),
            fbgs=fbgs,
            sensors=sensors,
            project_files=d.get('project_files', []),
            data_tab=DataTabState.from_dict(d.get('data_tab', {})),
            cleaning_tab=CleaningTabState.from_dict(d.get('cleaning_tab', {})),
            analysis_tab=AnalysisTabState.from_dict(d.get('analysis_tab', {})),
        )

    # ── 便利方法 ──

    def with_data_file(self, file_path: str, template_id: Optional[str] = None,
                       template_name: Optional[str] = None) -> 'AppState':
        """更新数据文件路径"""
        return replace(self, data_tab=replace(self.data_tab,
            file_path=file_path, template_id=template_id, template_name=template_name))

    def with_fbgs(self, fbgs: List[FBG]) -> 'AppState':
        return replace(self, fbgs=list(fbgs))

    def with_sensors(self, sensors: List[Sensor]) -> 'AppState':
        return replace(self, sensors=list(sensors))

    def with_cleaning_config(self, **kwargs: Any) -> 'AppState':
        return replace(self, cleaning_tab=replace(self.cleaning_tab, **kwargs))

    def with_analysis_results(self, results: Dict[str, List[Optional[float]]]) -> 'AppState':
        return replace(self, analysis_tab=replace(self.analysis_tab, sensor_results=results))

    @property
    def active_sensors(self) -> List[Sensor]:
        return [s for s in self.sensors if s.active]

    @property
    def fbg_count(self) -> int:
        return len(self.fbgs)

    @property
    def sensor_count(self) -> int:
        return len(self.sensors)
