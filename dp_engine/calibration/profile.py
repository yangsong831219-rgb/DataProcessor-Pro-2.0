"""温度标定参数文件管理 — CalibrationProfile 数据模型

对齐全局配置 (main.py save_config/load_config) 的 JSON 风格:
  - ensure_ascii=False, indent=2
  - 字典直读直写，无嵌套 dataclass
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


PROFILES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "calibration_profiles",
)


@dataclass
class CalibrationProfile:
    """温度标定完整状态"""
    # meta
    name: str = ""
    created_at: str = ""        # ISO 8601
    last_modified: str = ""
    # source
    file_path: str = ""         # 原文件绝对路径
    file_format: str = ""       # hyperion_peaks / hyperion_sensors / generic
    # phase A
    annotation: dict[str, str] = field(default_factory=dict)   # 列原名 → 暗号
    temp_min: float = 10.0
    temp_max: float = 70.0
    temp_step: float = 10.0
    detection_params: dict = field(default_factory=dict)
    s_eff_results: dict = field(default_factory=dict)          # 暗号 → {slope, R², T_base}
    # phase B
    ke_table: dict[str, list] = field(default_factory=dict)    # sensor → [Ke1, Ke2]
    decoupling_results: dict = field(default_factory=dict)     # sensor → {e_mean, e_std, e_range, rating}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CalibrationProfile":
        # 容错: 兼容旧版本缺少字段
        defaults = {
            "name": "", "created_at": "", "last_modified": "",
            "file_path": "", "file_format": "",
            "annotation": {}, "temp_min": 10.0, "temp_max": 70.0, "temp_step": 10.0,
            "detection_params": {}, "s_eff_results": {},
            "ke_table": {}, "decoupling_results": {},
        }
        for k, v in defaults.items():
            d.setdefault(k, v)
        return cls(**{k: d[k] for k in defaults})

    @classmethod
    def from_state(cls, name: str, temperature_page) -> "CalibrationProfile":
        """从 TemperatureCalibrationPage 的当前状态构造 profile"""
        import numpy as np
        now = datetime.now().isoformat()

        params = dict(temperature_page._detection_params or {})
        annotation = dict(temperature_page._annotation_dict or {})

        s_eff = {}
        if temperature_page._last_result:
            for wcol, s in temperature_page._last_result.get("S_eff", {}).items():
                s_eff[wcol] = {
                    "slope": s.get("slope", 0),
                    "r2": s.get("r2", 0),
                    "T_base": s.get("T_base", 0),
                }

        # Ke table: 从 _phase_b_state 取 (统一 dict 形状)
        ke_table = {}
        pb_state = temperature_page._phase_b_state or {}
        raw_ke = pb_state.get("ke_table", {})
        for s, v in raw_ke.items():
            if isinstance(v, dict):
                ke_table[s] = {"Ke1": float(v.get("Ke1", 0)), "Ke2": float(v.get("Ke2", 0))}
            elif isinstance(v, (list, tuple)) and len(v) == 2:
                ke_table[s] = {"Ke1": float(v[0]), "Ke2": float(v[1])}

        decoupling = {}
        pb = temperature_page._phase_b_result or {}
        if isinstance(pb, dict):
            for s_name, r in pb.get("sensors", {}).items():
                if isinstance(r, dict):
                    eps = np.asarray(r.get("eps_corr", []), dtype=float)
                    e_mean = float(np.nanmean(eps)) if len(eps) > 0 else 0.0
                    e_std = float(np.nanstd(eps)) if len(eps) > 0 else 0.0
                    e_range = float(np.nanmax(eps) - np.nanmin(eps)) if len(eps) > 0 else 0.0
                    rating = "优" if e_std <= 30 else ("良" if e_std <= 60 else "差")
                    decoupling[s_name] = {
                        "e_mean": e_mean, "e_std": e_std, "e_range": e_range,
                        "rating": rating,
                    }

        pa = temperature_page._phase_a_state or {}
        return cls(
            name=name,
            created_at=now, last_modified=now,
            file_path=getattr(temperature_page, "file_path_edit", None),
            file_format="",
            annotation=annotation,
            temp_min=pa.get("tmin", 10.0) if isinstance(pa, dict) else 10.0,
            temp_max=pa.get("tmax", 70.0) if isinstance(pa, dict) else 70.0,
            temp_step=pa.get("tstep", 10.0) if isinstance(pa, dict) else 10.0,
            detection_params=params,
            s_eff_results=s_eff,
            ke_table=ke_table,
            decoupling_results=decoupling,
        )
    def save(self) -> str:
        """保存到 {PROFILES_DIR}/{name}.json"""
        os.makedirs(PROFILES_DIR, exist_ok=True)
        path = self._path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return path

    @classmethod
    def load(cls, name: str) -> "CalibrationProfile":
        path = os.path.join(PROFILES_DIR, f"{name}.json")
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def load_path(cls, path: str) -> "CalibrationProfile":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def list_all(cls) -> list[dict]:
        """列举所有保存的 profile (不加载内容，仅元数据)"""
        os.makedirs(PROFILES_DIR, exist_ok=True)
        profiles = []
        for fname in sorted(os.listdir(PROFILES_DIR)):
            if fname.endswith(".json"):
                p = os.path.join(PROFILES_DIR, fname)
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    # 补充 last_modified from filesystem
                    d["last_modified"] = d.get("last_modified", "")
                    d["_name"] = fname[:-5]  # strip .json
                    profiles.append(d)
                except Exception:
                    profiles.append({"_name": fname[:-5], "name": fname[:-5],
                                     "created_at": "", "file_path": "", "error": True})
        # 按 last_modified 倒序
        profiles.sort(key=lambda x: x.get("last_modified", ""), reverse=True)
        return profiles

    @classmethod
    def delete(cls, name: str) -> None:
        path = cls._path_static(name)
        if os.path.isfile(path):
            os.remove(path)

    @classmethod
    def rename(cls, old_name: str, new_name: str) -> None:
        old = cls._path_static(old_name)
        new = cls._path_static(new_name)
        if os.path.isfile(old):
            os.rename(old, new)

    def _path(self) -> str:
        return os.path.join(PROFILES_DIR, f"{self.name}.json")

    @classmethod
    def _path_static(cls, name: str) -> str:
        return os.path.join(PROFILES_DIR, f"{name}.json")
