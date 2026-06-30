"""项目级标定配置 — 温度 + 应变跨模块持久化

取代旧 CalibrationProfile (dp_engine/calibration/profile.py)，新增应变段。
旧 profile 作废，无需迁移。

JSON 风格: ensure_ascii=False, indent=2 (对齐全局配置)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

import numpy as np


PROFILES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "calibration_profiles",
)


# ═══════════════════════════════════════════════════════════════════════
# 字段完整性契约
# ═══════════════════════════════════════════════════════════════════════

TEMPERATURE_FIELDS: list[str] = [
    "file_path", "file_format",
    "annotation", "temp_min", "temp_max", "temp_step",
    "detection_params", "s_eff_results", "ke_table", "decoupling_results",
    "compensation",
]
"""温度子 dict 完整字段列表 (来自旧 CalibrationProfile, 不含 project 级 meta)"""

STRAIN_SUB_CONFIG_FIELDS: list[str] = [
    "sensor_name", "sensor_mode", "gauge_length_mm", "n_cycles",
    "grating_map", "readings", "ke_results", "charts_meta",
]
"""StrainSubConfig 完整字段列表"""

PROJECT_FIELDS: list[str] = [
    "name", "created_at", "last_modified",
    "temperature", "strain",
]
"""ProjectConfig 顶层完整字段列表"""


# ═══════════════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class StrainSubConfig:
    """单个传感器的应变标定子配置

    Fields:
        sensor_name: 传感器名 (如 "A1")，与温度 annotation 的 prefix 对应
        sensor_mode: "single" | "dual_working" | "dual_anchored"
        gauge_length_mm: 标距 (mm)
        n_cycles: 加载循环数
        grating_map: 光栅 → 暗号映射 {"G1": "A1-W1", "G2": "A1-W2"}
        readings: 读数录入表全部行 (list[dict])
        ke_results: {"Ke1": 1.23, "Ke2": 0.98} — 单栅时 Ke2=0
        charts_meta: 标定曲线/残差等图表中间数据 (为检测报告预留)
    """
    sensor_name: str = ""
    sensor_mode: str = "single"        # "single" | "dual_working" | "dual_anchored"
    gauge_length_mm: float = 80.0
    n_cycles: int = 1
    grating_map: dict[str, str] = field(default_factory=dict)
    readings: list[dict] = field(default_factory=list)
    ke_results: dict[str, float] = field(default_factory=dict)
    charts_meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProjectConfig:
    """项目级标定配置 — 温度段 + 应变段

    Fields:
        name: 项目名称
        created_at: ISO 8601 创建时间
        last_modified: ISO 8601 最后修改时间
        temperature: 温度标定全部内容 (None = 尚未做温度标定)
            包含字段: file_path, file_format, annotation, temp_min/max/step,
            detection_params, s_eff_results, ke_table, decoupling_results
        strain: sensor_name → StrainSubConfig 映射 (N 个传感器)
    """
    name: str = ""
    created_at: str = ""
    last_modified: str = ""
    temperature: dict | None = None
    strain: dict[str, StrainSubConfig] = field(default_factory=dict)

    # ── 序列化 ──

    def to_dict(self) -> dict:
        d: dict = {
            "name": self.name,
            "created_at": self.created_at,
            "last_modified": self.last_modified,
            "temperature": self.temperature,
            "strain": {k: v.to_dict() for k, v in self.strain.items()},
        }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectConfig":
        strain_raw: dict = d.get("strain", {}) or {}
        strain: dict[str, StrainSubConfig] = {}
        for k, v in strain_raw.items():
            if isinstance(v, dict):
                strain[k] = StrainSubConfig(**{f: v.get(f, default) for f, default in _STRAIN_DEFAULTS.items()})
            else:
                strain[k] = v  # already StrainSubConfig (程式化 roundtrip)
        temperature: dict | None = d.get("temperature", None)
        return cls(
            name=str(d.get("name", "")),
            created_at=str(d.get("created_at", "")),
            last_modified=str(d.get("last_modified", "")),
            temperature=temperature,
            strain=strain,
        )

    # ── 文件 I/O (沿用旧 profile 目录) ──

    def save(self, dir_path: str | None = None) -> str:
        """保存到 {dir_path or PROFILES_DIR}/{name}.json"""
        target_dir = dir_path or PROFILES_DIR
        os.makedirs(target_dir, exist_ok=True)
        self.last_modified = datetime.now().isoformat()
        path = os.path.join(target_dir, f"project_{self.name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return path

    @classmethod
    def load(cls, name: str, dir_path: str | None = None) -> "ProjectConfig":
        """从 {dir_path or PROFILES_DIR}/project_{name}.json 加载"""
        target_dir = dir_path or PROFILES_DIR
        path = os.path.join(target_dir, f"project_{name}.json")
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def load_path(cls, path: str) -> "ProjectConfig":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def list_all(cls, dir_path: str | None = None) -> list[dict]:
        """列举所有保存的项目配置 (仅元数据，不加载内容)"""
        target_dir = dir_path or PROFILES_DIR
        os.makedirs(target_dir, exist_ok=True)
        projects: list[dict] = []
        for fname in sorted(os.listdir(target_dir)):
            if fname.startswith("project_") and fname.endswith(".json"):
                p = os.path.join(target_dir, fname)
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    d["last_modified"] = d.get("last_modified", "")
                    d["_key"] = fname[len("project_"):-5]  # strip prefix + .json
                    projects.append(d)
                except Exception:
                    projects.append({
                        "_key": fname[len("project_"):-5], "name": fname[len("project_"):-5],
                        "created_at": "", "last_modified": "", "error": True,
                    })
        projects.sort(key=lambda x: str(x.get("last_modified", "")), reverse=True)
        return projects

    @classmethod
    def delete(cls, name: str, dir_path: str | None = None) -> None:
        target_dir = dir_path or PROFILES_DIR
        path = os.path.join(target_dir, f"project_{name}.json")
        if os.path.isfile(path):
            os.remove(path)

    # ── 工厂方法 ──

    @classmethod
    def create_new(cls, name: str) -> "ProjectConfig":
        """创建空白项目 (仅 name + 时间戳，无 temperature/strain 数据)"""
        now = datetime.now().isoformat()
        return cls(name=name, created_at=now, last_modified=now)

    # ── 字段完整性校验 ──

    def validate(self) -> list[str]:
        """校验字段完整性，返回缺失/异常字段列表；空列表 = 通过"""
        issues: list[str] = []
        d = self.to_dict()
        for f in PROJECT_FIELDS:
            if f not in d:
                issues.append(f"顶层缺少字段: {f}")
        if self.temperature is not None and isinstance(self.temperature, dict):
            for f in TEMPERATURE_FIELDS:
                if f not in self.temperature:
                    issues.append(f"temperature 缺少字段: {f}")
        for s_name, sc in self.strain.items():
            sd = sc.to_dict()
            for f in STRAIN_SUB_CONFIG_FIELDS:
                if f not in sd:
                    issues.append(f"strain.{s_name} 缺少字段: {f}")
        return issues


# ── StrainSubConfig defaults (供 from_dict 容错) ──

_STRAIN_DEFAULTS: dict[str, object] = {
    "sensor_name": "", "sensor_mode": "single", "gauge_length_mm": 80.0,
    "n_cycles": 1, "grating_map": {}, "readings": [],
    "ke_results": {}, "charts_meta": {},
}


# ═══════════════════════════════════════════════════════════════════════
# ProjectConfigManager — 跨模块 save/load 统一入口
# ═══════════════════════════════════════════════════════════════════════

class ProjectConfigManager:
    """项目配置管理器 — 温度页 + 应变页共用同一套 save/load 逻辑。

    Usage:
      mgr = ProjectConfigManager()
      pc = mgr.capture(temperature_page, strain_page)  # 快照当前双模块状态
      mgr.save(pc)                                      # 持久化到磁盘
      pc2 = mgr.load("项目名称")                         # 从磁盘恢复
      mgr.restore(pc2, temperature_page, strain_page)   # 回填双模块活状态
    """

    @staticmethod
    def capture(temperature_page, strain_page) -> ProjectConfig:
        """从温度页 + 应变页的当前状态构造 ProjectConfig。"""
        from datetime import datetime  # noqa: F811

        now = datetime.now().isoformat()

        # ── 温度段: 直接从 temperature_page 提取 ──
        tp = temperature_page
        temperature: dict | None = None
        if tp is not None:
            tp_annot = dict(getattr(tp, '_annotation_dict', {}) or {})
            tp_params = dict(getattr(tp, '_detection_params', {}) or {})

            # s_eff_results — 从 _last_result 提取
            seff = {}
            lr = getattr(tp, '_last_result', None) or {}
            if isinstance(lr, dict):
                for wcol, s in lr.get("S_eff", {}).items():
                    if isinstance(s, dict):
                        seff[wcol] = {
                            "slope": float(s.get("slope", 0)),
                            "r2": float(s.get("r2", 0)),
                            "T_base": float(s.get("T_base", 0)),
                        }

            # ke_table — 从 _phase_b_state 提取
            ke_table = {}
            pb_state = getattr(tp, '_phase_b_state', None) or {}
            raw_ke = pb_state.get("ke_table", {})
            for s, v in raw_ke.items():
                if isinstance(v, dict):
                    ke_table[s] = {"Ke1": float(v.get("Ke1", 0)), "Ke2": float(v.get("Ke2", 0))}
                elif isinstance(v, (list, tuple)) and len(v) == 2:
                    ke_table[s] = {"Ke1": float(v[0]), "Ke2": float(v[1])}

            # decoupling_results — 从 _phase_b_state 提取
            decoupling = {}
            raw_dec = pb_state.get("decoupling_results", {})
            for s, d in raw_dec.items():
                if isinstance(d, dict):
                    decoupling[s] = {
                        "e_mean": float(d.get("e_mean", 0)),
                        "e_std": float(d.get("e_std", 0)),
                        "e_range": float(d.get("e_range", 0)),
                        "rating": str(d.get("rating", "—")),
                    }

            # compensation — 从 _phase_b_state 提取 (Phase 3: LUT/指标/评级)
            comp_data = pb_state.get("compensation", {}) or {}

            has_temp_data = tp_annot or seff or ke_table or decoupling
            if has_temp_data:
                pa_state = getattr(tp, '_phase_a_state', None) or {}

                temperature = {
                    "file_path": str(getattr(getattr(tp, 'file_path_edit', None), 'text', lambda: '')()),
                    "file_format": str(getattr(tp, '_annotation_meta', {}).get("format", "") if isinstance(getattr(tp, '_annotation_meta', None), dict) else ""),
                    "annotation": tp_annot,
                    "temp_min": float(pa_state.get("tmin", 10.0)) if isinstance(pa_state, dict) else 10.0,
                    "temp_max": float(pa_state.get("tmax", 70.0)) if isinstance(pa_state, dict) else 70.0,
                    "temp_step": float(pa_state.get("tstep", 10.0)) if isinstance(pa_state, dict) else 10.0,
                    "detection_params": tp_params,
                    "s_eff_results": seff,
                    "ke_table": ke_table,
                    "decoupling_results": decoupling,
                    "compensation": comp_data,
                }

        # ── 应变段 ──
        sp = strain_page
        strain: dict[str, StrainSubConfig] = {}
        if sp is not None:
            # 来源 1: self._strain_configs (页面活状态，全部传感器)
            if hasattr(sp, '_strain_configs') and sp._strain_configs:
                for s_name, sub in sp._strain_configs.items():
                    if isinstance(sub, StrainSubConfig):
                        strain[s_name] = sub
            # 来源 2: CalibrationTabWidget.project_config.strain (跨模块缓存)
            if hasattr(sp, '_get_cal_tab_widget'):
                ctw = sp._get_cal_tab_widget()
                if ctw is not None and ctw.project_config is not None:
                    for s_name, sub in ctw.project_config.strain.items():
                        if s_name not in strain:
                            strain[s_name] = sub
            # 来源 3: 当前活跃传感器 (兜底)
            if hasattr(sp, '_ke_results') and sp._ke_results and hasattr(sp, '_build_strain_subconfig'):
                sensor_name = ""
                if hasattr(sp, '_parse_sensor_from_grating_map'):
                    sensor_name = sp._parse_sensor_from_grating_map()
                if sensor_name and sensor_name not in strain:
                    sub = sp._build_strain_subconfig()
                    strain[sensor_name] = sub

        name = f"项目_{now[:16]}"
        return ProjectConfig(
            name=name, created_at=now, last_modified=now,
            temperature=temperature, strain=strain,
        )

    @staticmethod
    def restore(project_config: ProjectConfig, temperature_page, strain_page) -> None:
        """将 ProjectConfig 回填到温度页 + 应变页的活状态。

        温度段: 不走旧 CalibrationProfile，直接写活状态字段。
        应变段: 写入 CalibrationTabWidget.project_config.strain + 页面状态。
        """
        tp = temperature_page
        sp = strain_page

        if tp is not None and project_config.temperature is not None:
            t = project_config.temperature
            import pandas as pd

            # Step 0: 重读源文件
            src_path = t.get("file_path", "")
            if src_path and os.path.isfile(src_path):
                from utils.file_parser import parse_enlight_file
                try:
                    df, file_annot, meta = parse_enlight_file(src_path)
                    tp._loaded_df = df
                    tp._annotation_meta = meta
                    tp._time_col_idx = 0
                    tp.file_path_edit.setText(src_path)
                except Exception:
                    tp._loaded_df = None
                    file_annot = {}
            else:
                tp._loaded_df = None
                file_annot = {}

            # Step 1: annotation — ★ profile 优先, 文件仅对缺失列兜底
            profile_annot = t.get("annotation", {}) or {}
            # 以 profile 暗号为底 (用户保存的暗号是权威来源)
            from utils.annotation_utils import merge_annotations
            merged = merge_annotations(
                base=profile_annot,
                fallback=(file_annot or {}),
            )  # dirty=None → 无列锁定, 与封板B完全等价
            # 向后兼容: profile_annot 为空 (旧项目无 annotation 字段) 时
            #            merged={} → 文件列全量填入 → 等价旧行为
            tp._annotation_dict = merged
            data_cols_for_group = {}
            if tp._loaded_df is not None:
                for col_name, ann_name in merged.items():
                    try:
                        idx = list(tp._loaded_df.columns).index(col_name)
                        s = str(ann_name).strip().strip("'\"'\"'\"")
                        if s and '-' in s:
                            data_cols_for_group[idx] = s
                    except (ValueError, IndexError):
                        pass
            from ui.calibration_tab import _group_annotations_by_prefix
            tp._annotation_groups = _group_annotations_by_prefix(
                data_cols_for_group, tp._loaded_df) if data_cols_for_group else {}

            # Step 2: detection_params
            tp._detection_params = dict(t.get("detection_params", {}))

            # Step 3: Phase A done + last_result
            seff = t.get("s_eff_results", {})
            if seff:
                tp._phase_a_done = True
                tp._last_result = {
                    "S_eff": dict(seff),
                    "wavelength_cols": list(seff.keys()),
                    "plateaus": pd.DataFrame(),
                    "df": tp._loaded_df if tp._loaded_df is not None else pd.DataFrame(),
                }
            else:
                tp._phase_a_done = False
                tp._last_result = None

            # Step 4: Phase B result
            decoupling = t.get("decoupling_results", {})
            if decoupling:
                sensors = {}
                for s_name, d in decoupling.items():
                    is_single = s_name not in t.get("ke_table", {})
                    sensors[s_name] = {
                        "eps_corr": [d.get("e_mean", 0)] * 10,
                        "eps_orig": [], "dT_corr": [], "T_abs": [],
                        "S1": 0, "S2": 0, "T_base": 0,
                        "single_grating": is_single,
                    }
                tp._phase_b_result = {
                    "sensors": sensors,
                    "time_h": np.array([]),
                    "df": tp._loaded_df if tp._loaded_df is not None else pd.DataFrame(),
                }
            else:
                tp._phase_b_result = None

            # Step 5: 同步快照字典 (双副本一致) — 用合并后的 annotation + 保留 dirty
            tp._phase_a_state = {
                "annotation": dict(getattr(tp, '_annotation_dict', {})),
                "annotation_dirty": list(getattr(tp, '_annotation_dirty', set()) or set()),
                "groups": dict(getattr(tp, '_annotation_groups', {})),
                "tmin": t.get("temp_min", 10.0),
                "tmax": t.get("temp_max", 70.0),
                "tstep": t.get("temp_step", 10.0),
                "params": dict(t.get("detection_params", {})),
                "seff_result": tp._last_result,
            }
            tp._phase_b_state = {
                "ke_table": dict(t.get("ke_table", {})),
                "decoupling_results": dict(decoupling),
                "compensation": dict(t.get("compensation", {}) or {}),
            }

            # Step 6: UI 按钮门控
            from PyQt6.QtCore import Qt as _Qt
            df_ready = tp._loaded_df is not None and not (
                hasattr(tp._loaded_df, 'empty') and tp._loaded_df.empty)
            if df_ready:
                _BTN_STYLE_ENABLED = (
                    "QPushButton { background-color: #1677ff; color: white;"
                    " border: none; padding: 8px 18px; border-radius: 4px;"
                    " font-size: 13px; font-weight: 500; }"
                    "QPushButton:hover { background-color: #4096ff; }"
                )
                tp.btn_phase_a.setStyleSheet(_BTN_STYLE_ENABLED)
                tp.btn_phase_a.setCursor(_Qt.CursorShape.PointingHandCursor)
                tp.btn_phase_a.setEnabled(True)
                tp.btn_phase_a.setToolTip("打开阶段 A 对话框")
                tp.btn_phase_b.setStyleSheet(_BTN_STYLE_ENABLED)
                tp.btn_phase_b.setCursor(_Qt.CursorShape.PointingHandCursor)
                tp.btn_phase_b.setEnabled(tp._phase_a_done)
                tp.btn_phase_b.setToolTip(
                    "打开阶段 B 对话框" if tp._phase_a_done else "请先完成阶段 A")
            tp.status_card.setText(
                f"✅ 已加载项目: {project_config.name}\n"
                f"📁 {os.path.basename(src_path) if src_path else '(无文件)'}  "
                f"| 暗号 {len(t.get('annotation', {}))} 个"
                f" | S_eff {len(seff)} 个"
                f" | Ke {len(t.get('ke_table', {}))} 传感器"
                f" | 解耦 {len(decoupling)} 传感器"
            )

        # ── 应变段回填 ──
        if sp is not None and project_config.strain:
            # 写入 CalibrationTabWidget.project_config
            ctw = None
            if hasattr(sp, '_get_cal_tab_widget'):
                ctw = sp._get_cal_tab_widget()
            if ctw is not None:
                if ctw.project_config is None:
                    ctw.project_config = project_config
                else:
                    ctw.project_config.strain = project_config.strain

            # ★ 回填活状态 _strain_configs + 刷新列表面板
            if hasattr(sp, '_strain_configs'):
                sp._strain_configs.clear()
                for s_name, sub in project_config.strain.items():
                    sp._strain_configs[s_name] = sub
            if hasattr(sp, '_refresh_sensor_list'):
                from dp_engine.calibration.project_config import StrainSubConfig
                first_name = next(iter(project_config.strain.keys()), None)
                sp._refresh_sensor_list(select_sensor=first_name)

            # 加载后 dirty 复位 (刚加载是干净的)
            if hasattr(sp, '_clear_dirty'):
                sp._clear_dirty()

            # 如果 strain 有当前活动传感器，回填页面状态
            active = None
            for s_name, sub in project_config.strain.items():
                active = s_name
                break
            if active:
                sub = project_config.strain[active]
                sp._grating_map = dict(sub.grating_map)
                sp._ke_results = dict(sub.ke_results)
                sp._config["gauge_length_mm"] = float(sub.gauge_length_mm)
                sp._config["n_cycles"] = int(sub.n_cycles)
                kind_map = {"single": "single", "dual_working": "dual_both",
                            "dual_anchored": "dual_anchored"}
                sp._config["grating_kind"] = kind_map.get(sub.sensor_mode, "single")
                sp._levels = sp._generate_default_levels()
                sp.config_btn.setText(f"⚙ 标定参数: {sp._config_label()}")

    @staticmethod
    def save(project_config: ProjectConfig, dir_path: str | None = None) -> str:
        project_config.name = project_config.name or "未命名"
        return project_config.save(dir_path=dir_path)

    @staticmethod
    def load(name: str, dir_path: str | None = None) -> ProjectConfig:
        return ProjectConfig.load(name, dir_path=dir_path)

    @staticmethod
    def list_all(dir_path: str | None = None) -> list[dict]:
        return ProjectConfig.list_all(dir_path=dir_path)

    @staticmethod
    def delete(name: str, dir_path: str | None = None) -> None:
        return ProjectConfig.delete(name, dir_path=dir_path)
