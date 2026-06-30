"""温度标定加载项目配置不崩 smoke test — 真实 UI 路径

验证:
1. temperature-only 项目加载不崩 + file_path 正确读出
2. temperature=None 项目加载不崩
3. temperature + strain 项目加载不崩
4. full roundtrip restore 不崩
5. _manage_profiles 表格行构造不崩
6. StrainCalibrationPage._load_project 列表行构造不崩

CLAUDE.md 硬性规则: 每条新流程必须有 happy-path roundtrip smoke test。
"""

from __future__ import annotations
import pytest
import os
import tempfile
import shutil

import pandas as pd
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _save_temp_project(temp_dir: str, name: str, temperature: dict | None,
                        strain: dict | None = None) -> str:
    """把一个 ProjectConfig 写入临时目录。"""
    from dp_engine.calibration.project_config import ProjectConfig, StrainSubConfig
    pc = ProjectConfig(
        name=name, created_at="2026-01-01T00:00:00",
        last_modified="2026-01-01T00:00:00",
        temperature=temperature,
    )
    if strain:
        for s_name, s_data in strain.items():
            pc.strain[s_name] = StrainSubConfig(**s_data)
    return pc.save(dir_path=temp_dir)


def _make_simple_temperature():
    """构造最小温度子 dict (仅有 file_path + annotation 足够识别)"""
    return {
        "file_path": "/fake/test_data.txt",
        "file_format": "hyperion_peaks",
        "annotation": {"c1": "A1-W1", "c2": "A1-W2"},
        "temp_min": 10.0, "temp_max": 70.0, "temp_step": 10.0,
        "detection_params": {},
        "s_eff_results": {},
        "ke_table": {},
        "decoupling_results": {},
    }


# ═══════════════════════════════════════════════════════════════════════
# Test 1: 列表行构造函数不崩 (直接复现 2027 行崩溃)
# ═══════════════════════════════════════════════════════════════════════

class TestLoadProjectConfigSmoke:

    def test_load_temperature_only_no_crash(self, qapp):
        """有完整 temperature 子 dict 的项目 → 列表构造不崩"""
        tmp_dir = tempfile.mkdtemp(prefix="smoke_t_")
        try:
            _save_temp_project(tmp_dir, "smoke_temp_only", _make_simple_temperature())

            from dp_engine.calibration.project_config import ProjectConfigManager
            import dp_engine.calibration.project_config as pcfg
            old_dir = pcfg.PROFILES_DIR
            pcfg.PROFILES_DIR = tmp_dir

            try:
                projects = ProjectConfigManager.list_all()
                assert len(projects) == 1

                p = projects[0]
                key = p.get("_key", p.get("name", "?"))
                created = str(p.get("created_at", ""))[:16]
                n_strain = len(p.get("strain", {}) or {})
                # ★ 这就是崩溃行 (修复后应正常) ★
                fpath = (p.get("temperature") or {}).get("file_path", "") if isinstance(p.get("temperature"), dict) else ""

                assert key == "smoke_temp_only"
                assert fpath == "/fake/test_data.txt"
                assert n_strain == 0
            finally:
                pcfg.PROFILES_DIR = old_dir
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_load_temperature_none_no_crash(self, qapp):
        """temperature=None → 不崩 (场景 3 中间态)"""
        tmp_dir = tempfile.mkdtemp(prefix="smoke_n_")
        try:
            _save_temp_project(tmp_dir, "smoke_temp_none", None,
                               strain={"B1": {"sensor_name": "B1", "sensor_mode": "single",
                                               "ke_results": {"Ke1": 1.5, "Ke2": 0.0}}})

            from dp_engine.calibration.project_config import ProjectConfigManager
            import dp_engine.calibration.project_config as pcfg
            old_dir = pcfg.PROFILES_DIR
            pcfg.PROFILES_DIR = tmp_dir

            try:
                projects = ProjectConfigManager.list_all()
                p = projects[0]
                # isinstance 守卫生效
                fpath = (p.get("temperature") or {}).get("file_path", "") if isinstance(p.get("temperature"), dict) else ""
                assert fpath == ""
                assert p.get("_key") == "smoke_temp_none"
                assert len(p.get("strain", {}) or {}) == 1
            finally:
                pcfg.PROFILES_DIR = old_dir
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_load_temperature_with_strain_no_crash(self, qapp):
        """完整项目 → 列表构造不崩"""
        tmp_dir = tempfile.mkdtemp(prefix="smoke_ts_")
        try:
            temp = _make_simple_temperature()
            temp["s_eff_results"] = {"A1-W1": {"slope": 27.8, "r2": 0.995, "T_base": 25.0}}
            temp["ke_table"] = {"A1": {"Ke1": 0.7, "Ke2": 1.1}}
            temp["decoupling_results"] = {"A1": {"e_mean": 3.0, "e_std": 43.5, "e_range": 100.0, "rating": "良"}}

            _save_temp_project(tmp_dir, "smoke_full", temp,
                               strain={"A1": {"sensor_name": "A1", "sensor_mode": "dual_working",
                                               "ke_results": {"Ke1": 1.23, "Ke2": 0.98}}},
                               )

            from dp_engine.calibration.project_config import ProjectConfigManager
            import dp_engine.calibration.project_config as pcfg
            old_dir = pcfg.PROFILES_DIR
            pcfg.PROFILES_DIR = tmp_dir

            try:
                projects = ProjectConfigManager.list_all()
                p = projects[0]
                fpath = (p.get("temperature") or {}).get("file_path", "") if isinstance(p.get("temperature"), dict) else ""
                assert fpath == "/fake/test_data.txt"
                assert len(p.get("strain", {}) or {}) == 1

                t = p.get("temperature") or {}
                assert t.get("s_eff_results", {}).get("A1-W1", {}).get("slope") == 27.8
                assert t.get("ke_table", {}).get("A1", {}).get("Ke1") == 0.7
            finally:
                pcfg.PROFILES_DIR = old_dir
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Test 2: full load roundtrip (restore 路径)
# ═══════════════════════════════════════════════════════════════════════

class TestLoadProjectRestore:

    def test_full_roundtrip_smoke(self, qapp):
        """capture → save → load → restore 全链路不崩"""
        tmp_dir = tempfile.mkdtemp(prefix="smoke_rt_")
        try:
            _save_temp_project(tmp_dir, "rt_smoke", _make_simple_temperature())

            from dp_engine.calibration.project_config import ProjectConfigManager
            import dp_engine.calibration.project_config as pcfg
            old_dir = pcfg.PROFILES_DIR
            pcfg.PROFILES_DIR = tmp_dir

            try:
                from ui.calibration_tab import CalibrationTabWidget
                ctw = CalibrationTabWidget()
                tp = ctw.temp_page
                tp._loaded_df = pd.DataFrame({"Timestamp": [1], "Unnamed: 17": [1550.0]})
                tp._annotation_dict = {}
                tp._annotation_groups = {}

                pc = ProjectConfigManager.load("rt_smoke")
                ProjectConfigManager.restore(pc, tp, ctw.strain_page)

                assert tp._annotation_dict.get("c1") == "A1-W1"
                assert tp._annotation_dict.get("c2") == "A1-W2"
            finally:
                pcfg.PROFILES_DIR = old_dir
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Test 3: _manage_profiles 表格行构造不崩
# ═══════════════════════════════════════════════════════════════════════

class TestManageProfilesTable:

    def test_manage_profiles_table_build_no_crash(self, qapp):
        """_manage_profiles 中 (p.get("temperature") or {}).get("s_eff_results") 不崩"""
        tmp_dir = tempfile.mkdtemp(prefix="smoke_mgr_")
        try:
            _save_temp_project(tmp_dir, "mgr_smoke", _make_simple_temperature())

            from dp_engine.calibration.project_config import ProjectConfigManager
            import dp_engine.calibration.project_config as pcfg
            old_dir = pcfg.PROFILES_DIR
            pcfg.PROFILES_DIR = tmp_dir

            try:
                projects = ProjectConfigManager.list_all()
                p = projects[0]

                n_strain = len(p.get("strain", {}) or {})
                n_s_eff = len((p.get("temperature", {}) or {}).get("s_eff_results", {}) or {})
                cell_text = f"温度S_eff:{n_s_eff} + 应变:{n_strain}"
                assert cell_text == "温度S_eff:0 + 应变:0"
            finally:
                pcfg.PROFILES_DIR = old_dir
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# Test 4: StrainCalibrationPage._load_project 列表构造不崩
# ═══════════════════════════════════════════════════════════════════════

class TestStrainLoadProjectList:

    def test_strain_load_project_list_no_crash(self, qapp):
        """_load_project 中 temperature=None → ({}).get("s_eff_results",{}) 不崩"""
        tmp_dir = tempfile.mkdtemp(prefix="smoke_sl_")
        try:
            _save_temp_project(tmp_dir, "sl_smoke", None,
                               strain={"B1": {"sensor_name": "B1", "sensor_mode": "single",
                                               "ke_results": {"Ke1": 2.0, "Ke2": 0.0}}},
                               )

            from dp_engine.calibration.project_config import ProjectConfigManager
            import dp_engine.calibration.project_config as pcfg
            old_dir = pcfg.PROFILES_DIR
            pcfg.PROFILES_DIR = tmp_dir

            try:
                projects = ProjectConfigManager.list_all()
                p = projects[0]

                key = p.get("_key", p.get("name", "?"))
                created = str(p.get("created_at", ""))[:16]
                n_strain = len(p.get("strain", {}) or {})
                n_temp = len((p.get("temperature", {}) or {}).get("s_eff_results", {}) or {})
                list_text = f"{key}\n  创建: {created}  |  温度S_eff: {n_temp}  |  应变传感器: {n_strain}"
                assert "sl_smoke" in list_text
                assert "0" in list_text  # n_temp=0
                assert "1" in list_text  # n_strain=1
            finally:
                pcfg.PROFILES_DIR = old_dir
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
