"""dp_engine/calibration/readings_profile.py 合成数据测试

覆盖:
- 往返一致性: capture → save → load → apply 后 readings/levels/grating_map 逐键逐值相等
- int↔str 键转换闭环
- schema_version 不匹配 → raise ReadingsProfileError
- 空 readings 优雅处理
- tension_return 模式不掉退回列
- tension_only 模式无 unload 键
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

import pytest

from dp_engine.calibration.readings_profile import (
    ReadingsProfile,
    ReadingsProfileError,
    READINGS_PROFILES_DIR,
    capture_from_page,
    apply_to_page,
)


# ═══════════════════════════════════════════════════════════════════════════
# Mock StrainCalibrationPage
# ═══════════════════════════════════════════════════════════════════════════

class MockStrainPage:
    """模拟应变标定子页 — 仅暴露 capture/apply 需要的属性。"""

    def __init__(
        self,
        grating_kind: str = "dual_both",
        mode: str = "tension_return",
        n_cycles: int = 3,
        gauge_length_mm: float = 80.0,
        levels: list[float] | None = None,
        readings: dict | None = None,
        grating_map: dict[str, str] | None = None,
    ):
        self._config: dict[str, Any] = {
            "gauge_length_mm": gauge_length_mm,
            "mode": mode,
            "n_cycles": n_cycles,
            "grating_kind": grating_kind,
            "anchored_grating": None,
        }
        self._levels: list[float] = list(levels or _default_levels(gauge_length_mm))
        self._readings: dict = dict(readings) if readings else {}
        self._grating_map: dict[str, str] = dict(grating_map or {"G1": "A1-W1", "G2": "A1-W2"})
        self._grating_map_fresh: bool = False
        self._dialog_table = None


def _default_levels(gauge_mm: float = 80.0, n: int = 11) -> list[float]:
    """与 StrainCalibrationPage._generate_default_levels 同公式。"""
    return [i * gauge_mm / 10000.0 for i in range(n)]


# ═══════════════════════════════════════════════════════════════════════════
# fixtures
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def full_readings_dual_return() -> dict:
    """双栅 + 3 循环 + 张拉退回 — 全填充读数。"""
    rng = __import__("numpy").random.default_rng(42)
    return {
        1: {
            1: {"load": (1540.0 + rng.normal(0, 0.01, 11)).tolist(),
                "unload": (1540.0 + rng.normal(0, 0.015, 11)).tolist()},
            2: {"load": (1540.5 + rng.normal(0, 0.01, 11)).tolist(),
                "unload": (1540.5 + rng.normal(0, 0.015, 11)).tolist()},
            3: {"load": (1541.0 + rng.normal(0, 0.01, 11)).tolist(),
                "unload": (1541.0 + rng.normal(0, 0.015, 11)).tolist()},
        },
        2: {
            1: {"load": (1539.0 + rng.normal(0, 0.01, 11)).tolist(),
                "unload": (1539.0 + rng.normal(0, 0.015, 11)).tolist()},
            2: {"load": (1539.5 + rng.normal(0, 0.01, 11)).tolist(),
                "unload": (1539.5 + rng.normal(0, 0.015, 11)).tolist()},
            3: {"load": (1540.0 + rng.normal(0, 0.01, 11)).tolist(),
                "unload": (1540.0 + rng.normal(0, 0.015, 11)).tolist()},
        },
    }


@pytest.fixture
def full_readings_single_tension_only() -> dict:
    """单栅 + 2 循环 + 仅张拉。"""
    rng = __import__("numpy").random.default_rng(99)
    return {
        1: {
            1: {"load": (1540.0 + rng.normal(0, 0.01, 11)).tolist()},
            2: {"load": (1540.5 + rng.normal(0, 0.01, 11)).tolist()},
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# to_dict / from_dict
# ═══════════════════════════════════════════════════════════════════════════

class TestToDictFromDict:
    """序列化往返 — JSON 层面"""

    def test_roundtrip_via_dict(self, full_readings_dual_return):
        sp = MockStrainPage(
            grating_kind="dual_both", mode="tension_return", n_cycles=3,
            readings=full_readings_dual_return,
        )
        profile = capture_from_page(sp)
        d = profile.to_dict()

        # 验证 readings 的键是 str (JSON 兼容)
        rd = d["readings"]
        assert isinstance(rd, dict)
        first_g_key = next(iter(rd.keys()))
        assert isinstance(first_g_key, str), f"grating key 应是 str, 实际 {type(first_g_key)}"
        first_c_key = next(iter(rd[first_g_key].keys()))
        assert isinstance(first_c_key, str), f"cycle key 应是 str, 实际 {type(first_c_key)}"

        # 从 dict 还原
        p2 = ReadingsProfile.from_dict(d)

        # 验证 int 键还原
        first_g_int_key = next(iter(p2.readings.keys()))
        assert isinstance(first_g_int_key, int), f"还原后 grating key 应是 int, 实际 {type(first_g_int_key)}"
        first_c_int_key = next(iter(p2.readings[first_g_int_key].keys()))
        assert isinstance(first_c_int_key, int), f"还原后 cycle key 应是 int, 实际 {type(first_c_int_key)}"

        # 数值比对
        for g_idx in (1, 2):
            for c_idx in (1, 2, 3):
                orig = profile.readings[g_idx][c_idx]["load"]
                restored = p2.readings[g_idx][c_idx]["load"]
                assert len(orig) == len(restored)
                for i, (o, r) in enumerate(zip(orig, restored)):
                    assert abs(o - r) < 1e-9, f"G{g_idx}_C{c_idx}_load[{i}] 不匹配"
                orig_u = profile.readings[g_idx][c_idx]["unload"]
                restored_u = p2.readings[g_idx][c_idx]["unload"]
                assert len(orig_u) == len(restored_u)
                for i, (o, r) in enumerate(zip(orig_u, restored_u)):
                    assert abs(o - r) < 1e-9, f"G{g_idx}_C{c_idx}_unload[{i}] 不匹配"

        # 标量字段
        assert p2.sensor_mode == "dual_working"
        assert p2.mode == "tension_return"
        assert p2.n_cycles == 3
        assert p2.gauge_length_mm == 80.0
        assert p2.grating_map == {"G1": "A1-W1", "G2": "A1-W2"}

    def test_single_tension_only(self, full_readings_single_tension_only):
        sp = MockStrainPage(
            grating_kind="single", mode="tension_only", n_cycles=2,
            gauge_length_mm=100.0,
            readings=full_readings_single_tension_only,
            grating_map={"G1": "B1-W1"},
        )
        profile = capture_from_page(sp)
        d = profile.to_dict()
        p2 = ReadingsProfile.from_dict(d)

        assert p2.sensor_mode == "single"
        assert p2.mode == "tension_only"
        assert p2.n_cycles == 2
        assert abs(p2.gauge_length_mm - 100.0) < 0.01
        assert p2.grating_map == {"G1": "B1-W1"}
        assert 1 in p2.readings
        assert "load" in p2.readings[1][1]
        assert "unload" not in p2.readings[1][1]  # ★ tension_only 不存 unload

    def test_empty_readings(self):
        sp = MockStrainPage(grating_kind="single")
        profile = capture_from_page(sp)
        d = profile.to_dict()
        p2 = ReadingsProfile.from_dict(d)
        assert p2.readings == {}
        assert len(p2.levels) == 11  # 默认 11 行

    def test_schema_version_mismatch_raises(self):
        d = {
            "schema_version": 99,
            "name": "test",
            "levels": [0.0, 0.01],
            "mode": "tension_only",
            "readings": {},
        }
        with pytest.raises(ReadingsProfileError, match="版本不兼容"):
            ReadingsProfile.from_dict(d)

    def test_schema_version_missing_raises(self):
        """schema_version 缺失 → None != 1 → raise"""
        d = {
            "name": "no_version",
            "levels": [0.0],
            "mode": "tension_only",
            "readings": {},
        }
        with pytest.raises(ReadingsProfileError, match="版本不兼容"):
            ReadingsProfile.from_dict(d)


# ═══════════════════════════════════════════════════════════════════════════
# file I/O roundtrip
# ═══════════════════════════════════════════════════════════════════════════

class TestFileIO:
    """save / load / list_all / delete"""

    def test_save_load_roundtrip(self, full_readings_dual_return):
        sp = MockStrainPage(
            grating_kind="dual_both", mode="tension_return", n_cycles=3,
            readings=full_readings_dual_return,
        )
        profile = capture_from_page(sp)
        profile.name = "roundtrip_test"

        with tempfile.TemporaryDirectory() as tmp:
            path = profile.save(dir_path=tmp)
            assert os.path.isfile(path), f"文件未生成: {path}"
            assert os.path.getsize(path) > 100

            p2 = ReadingsProfile.load("roundtrip_test", dir_path=tmp)
            assert p2.name == "roundtrip_test"
            assert p2.mode == "tension_return"
            assert len(p2.levels) == 11

            # 验证 readings int 键原样
            assert 1 in p2.readings
            assert 2 in p2.readings
            assert isinstance(list(p2.readings.keys())[0], int)

    def test_load_missing_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(ReadingsProfileError, match="文件不存在"):
                ReadingsProfile.load("nonexistent", dir_path=tmp)

    def test_list_all(self, full_readings_single_tension_only):
        sp = MockStrainPage(grating_kind="single", readings=full_readings_single_tension_only)
        profile = capture_from_page(sp)

        with tempfile.TemporaryDirectory() as tmp:
            profile.name = "alpha"
            profile.save(dir_path=tmp)
            profile.name = "beta"
            profile.save(dir_path=tmp)

            items = ReadingsProfile.list_all(dir_path=tmp)
            assert len(items) >= 2
            keys = {it["_key"] for it in items}
            assert "alpha" in keys
            assert "beta" in keys

    def test_delete(self, full_readings_single_tension_only):
        sp = MockStrainPage(grating_kind="single", readings=full_readings_single_tension_only)
        profile = capture_from_page(sp)
        profile.name = "to_delete"

        with tempfile.TemporaryDirectory() as tmp:
            path = profile.save(dir_path=tmp)
            assert os.path.isfile(path)
            ReadingsProfile.delete("to_delete", dir_path=tmp)
            assert not os.path.isfile(path)


# ═══════════════════════════════════════════════════════════════════════════
# capture_from_page / apply_to_page 往返
# ═══════════════════════════════════════════════════════════════════════════

class TestCaptureApply:
    """capture → apply 后 sp 状态一致性"""

    def test_roundtrip_dual_return(self, full_readings_dual_return):
        sp = MockStrainPage(
            grating_kind="dual_both", mode="tension_return", n_cycles=3,
            readings=full_readings_dual_return, grating_map={"G1": "A1-W1", "G2": "A1-W2"},
        )
        profile = capture_from_page(sp)
        # 写入一个新的空页
        sp2 = MockStrainPage(grating_kind="single", mode="tension_only", n_cycles=1,
                             readings={}, grating_map={})
        apply_to_page(sp2, profile)

        # 验证状态被完整回填
        assert sp2._config["mode"] == "tension_return"
        assert sp2._config["n_cycles"] == 3
        assert sp2._config["grating_kind"] == "dual_both"
        assert sp2._config["gauge_length_mm"] == 80.0
        assert sp2._grating_map == {"G1": "A1-W1", "G2": "A1-W2"}
        assert len(sp2._levels) == 11

        # ★ readings int 键逐值比对
        for g_idx in (1, 2):
            assert g_idx in sp2._readings, f"缺少 G{g_idx}"
            for c_idx in (1, 2, 3):
                orig_load = full_readings_dual_return[g_idx][c_idx]["load"]
                restored_load = sp2._readings[g_idx][c_idx]["load"]
                assert len(orig_load) == len(restored_load)
                for i, (o, r) in enumerate(zip(orig_load, restored_load)):
                    assert abs(o - r) < 1e-9, f"G{g_idx}_C{c_idx}_load[{i}]"
                orig_unload = full_readings_dual_return[g_idx][c_idx]["unload"]
                restored_unload = sp2._readings[g_idx][c_idx]["unload"]
                for i, (o, r) in enumerate(zip(orig_unload, restored_unload)):
                    assert abs(o - r) < 1e-9, f"G{g_idx}_C{c_idx}_unload[{i}]"

    def test_roundtrip_single_tension_only(self, full_readings_single_tension_only):
        sp = MockStrainPage(
            grating_kind="single", mode="tension_only", n_cycles=2,
            gauge_length_mm=100.0,
            readings=full_readings_single_tension_only,
            grating_map={"G1": "B1-W1"},
        )
        profile = capture_from_page(sp)
        sp2 = MockStrainPage()
        apply_to_page(sp2, profile)

        assert sp2._config["mode"] == "tension_only"
        assert sp2._config["grating_kind"] == "single"
        assert abs(sp2._config["gauge_length_mm"] - 100.0) < 0.01
        assert sp2._grating_map == {"G1": "B1-W1"}

        # 验证 single 只有 G1, 没有 G2
        assert 1 in sp2._readings
        assert 2 not in sp2._readings
        # 验证只有 load, 没有 unload
        assert "load" in sp2._readings[1][1]
        assert "unload" not in sp2._readings[1][1]

    def test_empty_readings_roundtrip(self):
        sp = MockStrainPage(grating_kind="dual_both", mode="tension_only")
        profile = capture_from_page(sp)
        sp2 = MockStrainPage(grating_kind="single", mode="tension_return")
        apply_to_page(sp2, profile)

        assert sp2._readings == {}
        assert len(sp2._levels) == 11
        assert sp2._config["mode"] == "tension_only"  # profile 的 mode 覆盖了 sp2 的旧 mode
        assert sp2._config["grating_kind"] == "dual_both"

    def test_apply_resets_grating_map_fresh(self, full_readings_single_tension_only):
        sp = MockStrainPage(
            grating_kind="single", readings=full_readings_single_tension_only,
        )
        profile = capture_from_page(sp)
        sp2 = MockStrainPage()
        sp2._grating_map_fresh = True  # simulate user edit state
        apply_to_page(sp2, profile)
        assert sp2._grating_map_fresh is False  # ★ 应用后复位

    def test_apply_clears_dialog_table(self, full_readings_single_tension_only):
        sp = MockStrainPage(
            grating_kind="single", readings=full_readings_single_tension_only,
        )
        profile = capture_from_page(sp)
        sp2 = MockStrainPage()
        sp2._dialog_table = object()  # pyright: ignore[reportAttributeAccessIssue]  # 模拟旧表的引用，测试 apply 后清空
        apply_to_page(sp2, profile)
        assert sp2._dialog_table is None  # ★ 下次 _open_readings 会新建

    def test_apply_wrong_type_raises(self):
        sp = MockStrainPage()
        with pytest.raises(TypeError, match="ReadingsProfile"):
            apply_to_page(sp, {"key": "not a profile"})  # pyright: ignore[reportArgumentType]  # 有意测非 ReadningsProfile 入参


# ═══════════════════════════════════════════════════════════════════════════
# to_dict 中的 int→str 键转换深验证
# ═══════════════════════════════════════════════════════════════════════════

class TestKeyConversion:
    """int↔str 键转换一致性"""

    def test_all_keys_become_str_in_json(self, full_readings_dual_return):
        sp = MockStrainPage(
            grating_kind="dual_both", mode="tension_return", n_cycles=3,
            readings=full_readings_dual_return,
        )
        profile = capture_from_page(sp)
        d = profile.to_dict()
        rd = d["readings"]
        for g_str, cycles in rd.items():
            assert isinstance(g_str, str)
            for c_str, directions in cycles.items():
                assert isinstance(c_str, str)
                assert isinstance(directions, dict)

    def test_all_keys_become_int_after_load(self, full_readings_dual_return):
        sp = MockStrainPage(
            grating_kind="dual_both", mode="tension_return", n_cycles=3,
            readings=full_readings_dual_return,
        )
        profile = capture_from_page(sp)
        profile.name = "key_test"

        with tempfile.TemporaryDirectory() as tmp:
            path = profile.save(dir_path=tmp)
            # 验证文件内容中的键是 str
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            rd_raw = raw["readings"]
            for g_str in rd_raw:
                assert isinstance(g_str, str)

            # load 后是 int
            p2 = ReadingsProfile.load("key_test", dir_path=tmp)
            for g_idx in p2.readings:
                assert isinstance(g_idx, int)
                for c_idx in p2.readings[g_idx]:
                    assert isinstance(c_idx, int)


# ═══════════════════════════════════════════════════════════════════════════
# grating_kind ↔ sensor_mode 映射
# ═══════════════════════════════════════════════════════════════════════════

class TestModeMapping:
    """grating_kind ↔ sensor_mode 一致性"""

    @pytest.mark.parametrize("kind,expected", [
        ("single", "single"),
        ("dual_both", "dual_working"),
        ("dual_anchored", "dual_anchored"),
    ])
    def test_capture_maps_correctly(self, kind, expected):
        sp = MockStrainPage(grating_kind=kind)
        profile = capture_from_page(sp)
        assert profile.sensor_mode == expected

    @pytest.mark.parametrize("sensor_mode,expected_kind", [
        ("single", "single"),
        ("dual_working", "dual_both"),
        ("dual_anchored", "dual_anchored"),
    ])
    def test_apply_maps_correctly(self, sensor_mode, expected_kind):
        sp = MockStrainPage(grating_kind="single")
        profile = ReadingsProfile(
            sensor_mode=sensor_mode, mode="tension_only",
            levels=[0.0, 0.01], n_cycles=1,
        )
        apply_to_page(sp, profile)
        assert sp._config["grating_kind"] == expected_kind


# ═══════════════════════════════════════════════════════════════════════════
# 退回列不掉
# ═══════════════════════════════════════════════════════════════════════════

class TestTensionReturnPreserved:
    """tension_return → unload 键全保留"""

    def test_unload_keys_preserved(self, full_readings_dual_return):
        sp = MockStrainPage(
            grating_kind="dual_both", mode="tension_return", n_cycles=3,
            readings=full_readings_dual_return,
        )
        profile = capture_from_page(sp)
        sp2 = MockStrainPage()
        apply_to_page(sp2, profile)

        assert sp2._config["mode"] == "tension_return"
        for g_idx in (1, 2):
            for c_idx in (1, 2, 3):
                assert "unload" in sp2._readings[g_idx][c_idx], (
                    f"G{g_idx}_C{c_idx} 缺少 unload 键 — 退回列丢失"
                )
                assert len(sp2._readings[g_idx][c_idx]["unload"]) == 11

    def test_mode_preserved_after_json_roundtrip(self, full_readings_dual_return):
        sp = MockStrainPage(
            grating_kind="dual_both", mode="tension_return",
            readings=full_readings_dual_return,
        )
        profile = capture_from_page(sp)
        profile.name = "mode_test"
        with tempfile.TemporaryDirectory() as tmp:
            profile.save(dir_path=tmp)
            p2 = ReadingsProfile.load("mode_test", dir_path=tmp)
            assert p2.mode == "tension_return"

            sp2 = MockStrainPage()
            apply_to_page(sp2, p2)
            assert sp2._config["mode"] == "tension_return"
