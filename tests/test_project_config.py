"""项目级标定配置 — 数据模型 roundtrip 测试

覆盖:
1. 纯温度 roundtrip (无应变段)
2. 温度 + 2 应变子配置 roundtrip
3. 同传感器重标定覆盖 (keyed by sensor_name)
4. 字段完整性校验 (validate)
5. 空项目创建 + 容错 from_dict
6. list_all / delete 文件操作
"""

from __future__ import annotations

import json
import os
import tempfile
import shutil

import pytest

from dp_engine.calibration.project_config import (
    ProjectConfig, StrainSubConfig,
    TEMPERATURE_FIELDS, STRAIN_SUB_CONFIG_FIELDS, PROJECT_FIELDS,
    PROFILES_DIR,
)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def temp_dir():
    """每次测试用临时目录，隔离文件 I/O"""
    tmp = tempfile.mkdtemp(prefix="projcfg_")
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


def _make_sample_temperature() -> dict:
    """构造完整的 temperature 子 dict (覆盖全部 TEMPERATURE_FIELDS)"""
    return {
        "file_path": "/data/peaks_20260601.txt",
        "file_format": "hyperion_peaks",
        "annotation": {"ch1": "A1-W1", "ch2": "A1-W2", "ch3": "A2-W1", "ch4": "A2-W2"},
        "temp_min": 10.0,
        "temp_max": 70.0,
        "temp_step": 10.0,
        "detection_params": {"rolling_window": 25, "std_percentile": 45.0, "min_plateau_samples": 180},
        "s_eff_results": {
            "A1-W1": {"slope": 27.8, "r2": 0.99, "T_base": 14.1},
            "A1-W2": {"slope": 26.2, "r2": 0.99, "T_base": 11.6},
        },
        "ke_table": {"A1": {"Ke1": 0.7, "Ke2": 1.1}},
        "decoupling_results": {"A1": {"e_mean": 3.0, "e_std": 43.5, "e_range": 100.0, "rating": "良"}},
        "compensation": {},
    }


def _make_sample_strain_a1() -> StrainSubConfig:
    """构造 A1 应变子配置 (双栅-双工作)"""
    return StrainSubConfig(
        sensor_name="A1",
        sensor_mode="dual_working",
        gauge_length_mm=80.0,
        n_cycles=3,
        grating_map={"G1": "A1-W1", "G2": "A1-W2"},
        readings=[
            {"disp_mm": 0.008, "eps_theory": 100.0, "G1_C1_load": 1550.123, "G2_C1_load": 1545.456},
            {"disp_mm": 0.016, "eps_theory": 200.0, "G1_C1_load": 1550.246, "G2_C1_load": 1545.579},
        ],
        ke_results={"Ke1": 1.23, "Ke2": 0.98},
        charts_meta={"r2_g1": 0.9995, "r2_g2": 0.9993},
    )


def _make_sample_strain_a2() -> StrainSubConfig:
    """构造 A2 应变子配置 (单栅)"""
    return StrainSubConfig(
        sensor_name="A2",
        sensor_mode="single",
        gauge_length_mm=50.0,
        n_cycles=1,
        grating_map={"G1": "A2-W1"},
        readings=[
            {"disp_mm": 0.005, "eps_theory": 100.0, "G1_C1_load": 1552.100},
            {"disp_mm": 0.010, "eps_theory": 200.0, "G1_C1_load": 1552.223},
        ],
        ke_results={"Ke1": 2.30, "Ke2": 0.0},
        charts_meta={"r2_g1": 0.9998},
    )


# ═══════════════════════════════════════════════════════════════════════
# Test 1: 纯温度 roundtrip
# ═══════════════════════════════════════════════════════════════════════

class TestSaveLoadRoundtripTemperatureOnly:
    """只有温度数据，无应变子配置"""

    def test_save_load_roundtrip_temperature_only(self, temp_dir):
        temp = _make_sample_temperature()
        proj = ProjectConfig(
            name="温度标定_桥墩A",
            created_at="2026-06-09T10:00:00",
            last_modified="2026-06-09T12:00:00",
            temperature=temp,
            strain={},
        )

        path = proj.save(dir_path=temp_dir)
        assert os.path.isfile(path)

        # 重新加载
        p2 = ProjectConfig.load("温度标定_桥墩A", dir_path=temp_dir)
        assert p2.name == "温度标定_桥墩A"
        assert p2.created_at == "2026-06-09T10:00:00"
        assert p2.temperature is not None
        assert p2.strain == {}

        # 逐字段断言 temperature
        t2 = p2.temperature
        for f in TEMPERATURE_FIELDS:
            assert f in t2, f"temperature 缺少字段: {f}"
        assert t2["file_path"] == temp["file_path"]
        assert t2["file_format"] == temp["file_format"]
        assert t2["annotation"] == temp["annotation"]
        assert t2["temp_min"] == temp["temp_min"]
        assert t2["temp_max"] == temp["temp_max"]
        assert t2["temp_step"] == temp["temp_step"]
        assert t2["detection_params"] == temp["detection_params"]
        assert t2["s_eff_results"] == temp["s_eff_results"]
        assert t2["ke_table"] == temp["ke_table"]
        assert t2["decoupling_results"] == temp["decoupling_results"]

    def test_validate_passes_on_complete_temperature(self, temp_dir):
        """完整 temperature 通过 validate"""
        temp = _make_sample_temperature()
        proj = ProjectConfig(
            name="vtest", created_at="2026-01-01", last_modified="2026-01-01",
            temperature=temp,
        )
        issues = proj.validate()
        assert issues == [], f"validate 失败: {issues}"

    def test_validate_reports_missing_temp_fields(self):
        """temperature 缺少字段时 validate 报出来"""
        proj = ProjectConfig(
            name="bad", created_at="", last_modified="",
            temperature={"file_path": "/x.txt"},  # 缺大量字段
        )
        issues = proj.validate()
        assert len(issues) > 0
        # 至少报缺少 annotation / s_eff_results 等
        missing_fields = [i for i in issues if "缺少字段" in i]
        assert len(missing_fields) >= len(TEMPERATURE_FIELDS) - 1  # 只有 file_path

    def test_none_temperature_ok(self):
        """temperature=None 是合法状态 (尚未做温度标定)"""
        proj = ProjectConfig(
            name="no_temp", created_at="", last_modified="",
            temperature=None,
        )
        issues = proj.validate()
        # 不应报 temperature 子字段缺失 (因为整个为 None)
        temp_issues = [i for i in issues if i.startswith("temperature ")]
        assert len(temp_issues) == 0


# ═══════════════════════════════════════════════════════════════════════
# Test 2: 温度 + 应变 roundtrip
# ═══════════════════════════════════════════════════════════════════════

class TestSaveLoadRoundtripWithStrain:
    """温度 + 多个应变子配置"""

    def test_save_load_roundtrip_with_strain(self, temp_dir):
        temp = _make_sample_temperature()
        strain_a1 = _make_sample_strain_a1()
        strain_a2 = _make_sample_strain_a2()
        proj = ProjectConfig(
            name="完整项目_桥墩A",
            created_at="2026-06-09T10:00:00",
            last_modified="2026-06-09T14:00:00",
            temperature=temp,
            strain={"A1": strain_a1, "A2": strain_a2},
        )

        path = proj.save(dir_path=temp_dir)
        assert os.path.isfile(path)

        p2 = ProjectConfig.load("完整项目_桥墩A", dir_path=temp_dir)
        assert p2.name == "完整项目_桥墩A"
        assert p2.temperature is not None
        assert len(p2.strain) == 2

        # 逐字段断言 strain A1
        s1 = p2.strain["A1"]
        assert isinstance(s1, StrainSubConfig)
        for f in STRAIN_SUB_CONFIG_FIELDS:
            assert hasattr(s1, f), f"StrainSubConfig 缺少字段: {f}"
        assert s1.sensor_name == "A1"
        assert s1.sensor_mode == "dual_working"
        assert s1.gauge_length_mm == 80.0
        assert s1.n_cycles == 3
        assert s1.grating_map == {"G1": "A1-W1", "G2": "A1-W2"}
        assert len(s1.readings) == 2
        assert s1.readings[0]["disp_mm"] == 0.008
        assert s1.readings[0]["eps_theory"] == 100.0
        assert s1.ke_results == {"Ke1": 1.23, "Ke2": 0.98}
        assert s1.charts_meta == {"r2_g1": 0.9995, "r2_g2": 0.9993}

        # 逐字段断言 strain A2
        s2 = p2.strain["A2"]
        assert s2.sensor_mode == "single"
        assert s2.ke_results["Ke2"] == 0.0  # 单栅 Ke2=0

        # validate
        assert p2.validate() == []

    def test_json_on_disk_is_valid(self, temp_dir):
        """写入磁盘的 JSON 结构正确"""
        proj = ProjectConfig(
            name="json_test", created_at="2026-01-01", last_modified="2026-01-01",
            temperature=_make_sample_temperature(),
            strain={"A1": _make_sample_strain_a1()},
        )
        path = proj.save(dir_path=temp_dir)
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # 顶层字段
        for f in PROJECT_FIELDS:
            assert f in raw, f"JSON 顶层缺少: {f}"
        # temperature 子字段
        for f in TEMPERATURE_FIELDS:
            assert f in raw["temperature"], f"JSON temperature 缺少: {f}"
        # strain 子字段
        for f in STRAIN_SUB_CONFIG_FIELDS:
            assert f in raw["strain"]["A1"], f"JSON strain.A1 缺少: {f}"


# ═══════════════════════════════════════════════════════════════════════
# Test 3: 同传感器重标定覆盖
# ═══════════════════════════════════════════════════════════════════════

class TestStrainSubConfigKeyedBySensor:
    """strain dict key = sensor_name，同名传感器重新标定应覆盖而非新增"""

    def test_same_sensor_overwrites(self):
        proj = ProjectConfig(name="覆盖测试")
        proj.strain["A1"] = StrainSubConfig(
            sensor_name="A1", sensor_mode="single",
            gauge_length_mm=80.0, n_cycles=1,
            grating_map={"G1": "A1-W1"},
            ke_results={"Ke1": 1.10, "Ke2": 0.0},
        )
        assert len(proj.strain) == 1

        # 重新标定 A1 (双栅双工作，标距变 50mm)
        proj.strain["A1"] = StrainSubConfig(
            sensor_name="A1", sensor_mode="dual_working",
            gauge_length_mm=50.0, n_cycles=3,
            grating_map={"G1": "A1-W1", "G2": "A1-W2"},
            ke_results={"Ke1": 1.85, "Ke2": 0.92},
        )
        assert len(proj.strain) == 1  # 仍是 1 个，非 2 个
        assert proj.strain["A1"].sensor_mode == "dual_working"
        assert proj.strain["A1"].gauge_length_mm == 50.0
        assert proj.strain["A1"].ke_results["Ke1"] == 1.85

    def test_same_sensor_overwrites_across_save_load(self, temp_dir):
        """保存→重新标定→保存→加载，key 覆盖正确"""
        proj = ProjectConfig(
            name="重标定测试", created_at="2026-01-01", last_modified="2026-01-01",
            temperature=_make_sample_temperature(),
            strain={"A1": StrainSubConfig(sensor_name="A1", sensor_mode="single",
                     gauge_length_mm=80.0, ke_results={"Ke1": 1.10, "Ke2": 0.0})},
        )
        proj.save(dir_path=temp_dir)

        # 第一次加载验证
        p1 = ProjectConfig.load("重标定测试", dir_path=temp_dir)
        assert p1.strain["A1"].sensor_mode == "single"
        assert p1.strain["A1"].ke_results["Ke1"] == 1.10

        # 重标定 A1 → 覆盖保存
        p1.strain["A1"] = StrainSubConfig(
            sensor_name="A1", sensor_mode="dual_working",
            gauge_length_mm=50.0, n_cycles=3,
            grating_map={"G1": "A1-W1", "G2": "A1-W2"},
            ke_results={"Ke1": 1.85, "Ke2": 0.92},
        )
        p1.save(dir_path=temp_dir)

        # 第二次加载
        p2 = ProjectConfig.load("重标定测试", dir_path=temp_dir)
        assert len(p2.strain) == 1
        assert p2.strain["A1"].sensor_mode == "dual_working"
        assert p2.strain["A1"].ke_results["Ke1"] == 1.85
        assert p2.strain["A1"].ke_results["Ke2"] == 0.92

    def test_multiple_different_sensors(self):
        """不同传感器名各自独立存储"""
        proj = ProjectConfig(name="多传感器")
        proj.strain["A1"] = StrainSubConfig(sensor_name="A1", sensor_mode="dual_working")
        proj.strain["A2"] = StrainSubConfig(sensor_name="A2", sensor_mode="dual_working")
        proj.strain["B1"] = StrainSubConfig(sensor_name="B1", sensor_mode="single")
        assert len(proj.strain) == 3
        assert set(proj.strain.keys()) == {"A1", "A2", "B1"}


# ═══════════════════════════════════════════════════════════════════════
# Test 4: 空项目创建 + 容错 from_dict
# ═══════════════════════════════════════════════════════════════════════

class TestEmptyAndCompat:

    def test_create_new_empty_project(self):
        proj = ProjectConfig.create_new("新建项目")
        assert proj.name == "新建项目"
        assert proj.created_at  # 非空 ISO 时间戳
        assert proj.temperature is None
        assert proj.strain == {}

    def test_from_dict_missing_optional_fields(self):
        """仅 name 的旧/最小格式不崩溃"""
        proj = ProjectConfig.from_dict({"name": "最小配置"})
        assert proj.name == "最小配置"
        assert proj.temperature is None
        assert proj.strain == {}

    def test_from_dict_empty_dict(self):
        """完全空 dict 不崩溃"""
        proj = ProjectConfig.from_dict({})
        assert proj.name == ""
        assert proj.temperature is None
        assert proj.strain == {}

    def test_from_dict_strain_as_none(self):
        """strain=None 容错为空 dict"""
        proj = ProjectConfig.from_dict({"name": "t", "strain": None})
        assert proj.strain == {}


# ═══════════════════════════════════════════════════════════════════════
# Test 5: list_all / delete 文件操作
# ═══════════════════════════════════════════════════════════════════════

class TestListAllDelete:

    def test_list_all_empty(self, temp_dir):
        projects = ProjectConfig.list_all(dir_path=temp_dir)
        assert projects == []

    def test_list_all_and_delete(self, temp_dir):
        proj = ProjectConfig(
            name="列表测试", created_at="2026-01-01", last_modified="2026-01-01",
            temperature=_make_sample_temperature(),
        )
        proj.save(dir_path=temp_dir)

        projects = ProjectConfig.list_all(dir_path=temp_dir)
        assert len(projects) == 1
        assert projects[0]["_key"] == "列表测试"
        assert projects[0]["name"] == "列表测试"

        ProjectConfig.delete("列表测试", dir_path=temp_dir)
        assert ProjectConfig.list_all(dir_path=temp_dir) == []

    def test_list_all_skips_legacy_profiles(self, temp_dir):
        """只列出 project_ 前缀文件，跳过旧 CalibrationProfile JSON"""
        # 创建旧格式 profile (不带 project_ 前缀)
        legacy_path = os.path.join(temp_dir, "旧配置.json")
        with open(legacy_path, "w", encoding="utf-8") as f:
            json.dump({"name": "旧配置"}, f)

        # 创建新项目配置
        proj = ProjectConfig(name="新项目", created_at="2026-01-01", last_modified="2026-01-01")
        proj.save(dir_path=temp_dir)

        projects = ProjectConfig.list_all(dir_path=temp_dir)
        assert len(projects) == 1
        assert projects[0]["_key"] == "新项目"


# ═══════════════════════════════════════════════════════════════════════
# Test 6: StrainSubConfig 独立 roundtrip
# ═══════════════════════════════════════════════════════════════════════

class TestStrainSubConfigUnit:

    def test_to_dict_contains_all_fields(self):
        sc = _make_sample_strain_a1()
        d = sc.to_dict()
        for f in STRAIN_SUB_CONFIG_FIELDS:
            assert f in d, f"to_dict 缺少: {f}"

    def test_defaults(self):
        sc = StrainSubConfig()
        assert sc.sensor_name == ""
        assert sc.sensor_mode == "single"
        assert sc.gauge_length_mm == 80.0
        assert sc.n_cycles == 1
        assert sc.grating_map == {}
        assert sc.readings == []
        assert sc.ke_results == {}
        assert sc.charts_meta == {}


# ═══════════════════════════════════════════════════════════════════════
# Test 7: PROFILES_DIR 存在
# ═══════════════════════════════════════════════════════════════════════

class TestProfilesDir:

    def test_profiles_dir_exists(self):
        # PROFILES_DIR 可能还不存在，但路径字符串有效
        assert PROFILES_DIR.endswith("calibration_profiles")
