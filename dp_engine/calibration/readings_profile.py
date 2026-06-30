"""应变读数 Profile — 独立 sidecar, 与 ProjectConfig 零耦合。

存盘: <项目根>/readings_profiles/readings_<name>.json
不带派生量 (不存 ke_results)。加载后由用户点"拟合"经 calibrate_strain 真算。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

# ═══════════════════════════════════════════════════════════════════════
# 路径
# ═══════════════════════════════════════════════════════════════════════

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
READINGS_PROFILES_DIR = os.path.join(_PROJECT_ROOT, "readings_profiles")


# ═══════════════════════════════════════════════════════════════════════
# 异常
# ═══════════════════════════════════════════════════════════════════════

class ReadingsProfileError(Exception):
    """读数 profile 加载/校验异常 — 响亮不静默。"""


# ═══════════════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════════════

_KIND_TO_SENSOR_MODE: dict[str, str] = {
    "single": "single",
    "dual_both": "dual_working",
    "dual_anchored": "dual_anchored",
}
_SENSOR_MODE_TO_KIND: dict[str, str] = {
    "single": "single",
    "dual_working": "dual_both",
    "dual_anchored": "dual_anchored",
}

_READINGS_JSON_KEYS: tuple[str, ...] = ("load", "unload")


@dataclass
class ReadingsProfile:
    """应变标定原始读数 — 仅用户输入项, 零派生量。

    字段:
        schema_version: 1 — 加载时版本不符即 raise, 不允许降级静默。
        sensor_mode: single / dual_working / dual_anchored (映射自 _config["grating_kind"])
        mode: tension_only / tension_return (决定有无退回列)
        grating_map: 光栅 → 暗号映射 {"G1": "A1-W1", "G2": "A1-W2"}
        readings: 全量原始波长读数 dict — 键 1/2 (int) → cycle 1..n (int) → {"load": [...], "unload": [...]}
        levels: 位移等级列表, 与 readings 的行数一致
    """

    schema_version: int = 1
    name: str = ""
    created_at: str = ""
    last_modified: str = ""
    sensor_mode: str = "single"         # single / dual_working / dual_anchored
    mode: str = "tension_only"          # tension_only / tension_return
    n_cycles: int = 1
    gauge_length_mm: float = 80.0
    grating_map: dict[str, str] = field(default_factory=dict)
    readings: dict[int, dict[int, dict[str, list[float]]]] = field(default_factory=dict)
    levels: list[float] = field(default_factory=list)

    # ── 序列化 ──

    def to_dict(self) -> dict[str, Any]:
        """序列化 readings 的 int 键 → str 键 (JSON 兼容)。"""
        rd_serialized: dict[str, dict[str, dict[str, list[float]]]] = {}
        for g_idx, cycles in self.readings.items():
            g_key = str(g_idx)
            rd_serialized[g_key] = {}
            for c_idx, directions in cycles.items():
                c_key = str(c_idx)
                rd_serialized[g_key][c_key] = {
                    dk: list(dv) for dk, dv in directions.items()
                    if dk in _READINGS_JSON_KEYS
                }
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "created_at": self.created_at,
            "last_modified": self.last_modified,
            "sensor_mode": self.sensor_mode,
            "mode": self.mode,
            "n_cycles": self.n_cycles,
            "gauge_length_mm": self.gauge_length_mm,
            "grating_map": dict(self.grating_map),
            "readings": rd_serialized,
            "levels": [float(v) for v in self.levels],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReadingsProfile:
        """反序列化 — str 键 → int 键; schema_version 不匹配 → 响亮 raise。"""
        sv = d.get("schema_version")
        if sv != 1:
            raise ReadingsProfileError(
                f"读数 profile 版本不兼容: 文件 version={sv}, "
                f"当前支持 version=1。请使用兼容版本的程序加载。"
            )
        # 还原 readings int 键
        readings: dict[int, dict[int, dict[str, list[float]]]] = {}
        raw_rd = d.get("readings", {}) or {}
        if isinstance(raw_rd, dict):
            for g_str, cycles in raw_rd.items():
                try:
                    g_idx = int(g_str)
                except (ValueError, TypeError):
                    continue
                if not isinstance(cycles, dict):
                    continue
                readings[g_idx] = {}
                for c_str, directions in cycles.items():
                    try:
                        c_idx = int(c_str)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(directions, dict):
                        continue
                    entry: dict[str, list[float]] = {}
                    for dk in _READINGS_JSON_KEYS:
                        arr = directions.get(dk)
                        if isinstance(arr, list):
                            entry[dk] = [float(v) if v is not None else 0.0 for v in arr]
                    if "load" in entry:
                        readings[g_idx][c_idx] = entry
        return cls(
            schema_version=1,
            name=str(d.get("name", "")),
            created_at=str(d.get("created_at", "")),
            last_modified=str(d.get("last_modified", "")),
            sensor_mode=str(d.get("sensor_mode", "single")),
            mode=str(d.get("mode", "tension_only")),
            n_cycles=int(d.get("n_cycles", 1)),
            gauge_length_mm=float(d.get("gauge_length_mm", 80.0)),
            grating_map={str(k): str(v) for k, v in (d.get("grating_map", {}) or {}).items()},
            readings=readings,
            levels=[float(v) for v in (d.get("levels", []) or [])],
        )

    # ── 文件 I/O ──

    def save(self, dir_path: str | None = None) -> str:
        """保存到 {dir_path or READINGS_PROFILES_DIR}/readings_{name}.json"""
        target_dir = dir_path or READINGS_PROFILES_DIR
        os.makedirs(target_dir, exist_ok=True)
        self.last_modified = datetime.now().isoformat()
        fname = f"readings_{self.name}.json"
        path = os.path.join(target_dir, fname)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return path

    @classmethod
    def load(cls, name: str, dir_path: str | None = None) -> ReadingsProfile:
        """从 {dir_path or READINGS_PROFILES_DIR}/readings_{name}.json 加载。"""
        target_dir = dir_path or READINGS_PROFILES_DIR
        path = os.path.join(target_dir, f"readings_{name}.json")
        if not os.path.isfile(path):
            raise ReadingsProfileError(f"读数 profile 文件不存在: {path}")
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return cls.from_dict(d)

    @staticmethod
    def list_all(dir_path: str | None = None) -> list[dict[str, str]]:
        """列出所有已保存读数 profile (仅元数据，不加载内容)。"""
        target_dir = dir_path or READINGS_PROFILES_DIR
        os.makedirs(target_dir, exist_ok=True)
        profiles: list[dict[str, str]] = []
        for fname in sorted(os.listdir(target_dir)):
            if fname.startswith("readings_") and fname.endswith(".json"):
                key = fname[len("readings_"):-5]
                path = os.path.join(target_dir, fname)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    profiles.append({
                        "_key": key,
                        "name": d.get("name", key),
                        "created_at": str(d.get("created_at", ""))[:16],
                        "sensor_mode": d.get("sensor_mode", "single"),
                        "mode": d.get("mode", "tension_only"),
                        "n_levels": str(len(d.get("levels", []) or [])),
                    })
                except Exception:
                    profiles.append({
                        "_key": key, "name": key,
                        "created_at": "", "sensor_mode": "?",
                        "mode": "?", "n_levels": "?",
                    })
        return profiles

    @staticmethod
    def delete(name: str, dir_path: str | None = None) -> None:
        """删除指定名称的读数 profile。"""
        target_dir = dir_path or READINGS_PROFILES_DIR
        path = os.path.join(target_dir, f"readings_{name}.json")
        if os.path.isfile(path):
            os.remove(path)


# ═══════════════════════════════════════════════════════════════════════
# capture / apply — 与 StrainCalibrationPage 的接口
# ═══════════════════════════════════════════════════════════════════════

def capture_from_page(sp) -> ReadingsProfile:
    """从应变标定页活状态抓取读数 profile — 纯读, 不改 sp。"""
    now = datetime.now().isoformat()
    kind = sp._config.get("grating_kind", "single") if isinstance(sp._config, dict) else "single"
    sensor_mode = _KIND_TO_SENSOR_MODE.get(kind, "single")
    mode = sp._config.get("mode", "tension_only") if isinstance(sp._config, dict) else "tension_only"
    n_cycles = int(sp._config.get("n_cycles", 1)) if isinstance(sp._config, dict) else 1
    gauge = float(sp._config.get("gauge_length_mm", 80.0)) if isinstance(sp._config, dict) else 80.0
    grating_map = {str(k): str(v) for k, v in (getattr(sp, '_grating_map', None) or {}).items()}
    # ★ 深拷贝 readings — int 键原样保留
    readings_copy: dict[int, dict[int, dict[str, list[float]]]] = {}
    raw = getattr(sp, '_readings', None) or {}
    for g_idx, cycles in raw.items():
        if not isinstance(cycles, dict):
            continue
        g_int = int(g_idx) if not isinstance(g_idx, int) else g_idx
        readings_copy[g_int] = {}
        for c_idx, directions in cycles.items():
            if not isinstance(directions, dict):
                continue
            c_int = int(c_idx) if not isinstance(c_idx, int) else c_idx
            entry: dict[str, list[float]] = {}
            for dk in _READINGS_JSON_KEYS:
                arr = directions.get(dk)
                if isinstance(arr, (list, tuple)):
                    entry[dk] = [float(v) if v is not None else 0.0 for v in arr]
            if "load" in entry:
                readings_copy[g_int][c_int] = entry
    levels = [float(v) for v in (getattr(sp, '_levels', None) or [])]
    return ReadingsProfile(
        schema_version=1,
        name="",
        created_at=now,
        last_modified=now,
        sensor_mode=sensor_mode,
        mode=mode,
        n_cycles=n_cycles,
        gauge_length_mm=gauge,
        grating_map=grating_map,
        readings=readings_copy,
        levels=levels,
    )


def apply_to_page(sp, profile: ReadingsProfile) -> None:
    """将读数 profile 回填到应变标定页 — 写 sp 活状态, 不触发信号。"""
    if not isinstance(profile, ReadingsProfile):
        raise TypeError(f"profile 必须是 ReadingsProfile, 实际: {type(profile)}")
    kind = _SENSOR_MODE_TO_KIND.get(profile.sensor_mode, "single")
    # 更新 sp._config
    if isinstance(getattr(sp, '_config', None), dict):
        sp._config["gauge_length_mm"] = float(profile.gauge_length_mm)
        sp._config["mode"] = str(profile.mode)
        sp._config["n_cycles"] = int(profile.n_cycles)
        sp._config["grating_kind"] = kind
        # 锚固光栅 — 双栅锚固模式在加载时不恢复 (用户需手动重设)
        if kind != "dual_anchored":
            sp._config["anchored_grating"] = None
    # 回填 _levels
    sp._levels = [float(v) for v in profile.levels]
    # 回填 _readings — int 键原样
    readings: dict[int, dict[int, dict[str, list[float]]]] = {}
    for g_idx, cycles in profile.readings.items():
        g_int = int(g_idx) if not isinstance(g_idx, int) else g_idx
        readings[g_int] = {}
        for c_idx, directions in cycles.items():
            if not isinstance(directions, dict):
                continue
            c_int = int(c_idx) if not isinstance(c_idx, int) else c_idx
            entry: dict[str, list[float]] = {}
            for dk in _READINGS_JSON_KEYS:
                arr = directions.get(dk)
                if isinstance(arr, (list, tuple)):
                    entry[dk] = [float(v) if v is not None else 0.0 for v in arr]
            if "load" in entry:
                readings[g_int][c_int] = entry
    sp._readings = readings
    # 回填 _grating_map
    sp._grating_map = {str(k): str(v) for k, v in profile.grating_map.items()}
    # 重置 _grating_map_fresh — 允许后续 _load_sensor_to_workspace 覆盖
    if hasattr(sp, '_grating_map_fresh'):
        sp._grating_map_fresh = False
    # 清除 _dialog_table — 下次 _open_readings 会新建
    if hasattr(sp, '_dialog_table'):
        sp._dialog_table = None
