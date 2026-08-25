"""core/chart_registry.py + core/chart_store.py — 阶段0-A 验收测试。

验证:
- 注册表: 11 个 producer 注册、chart_id 唯一、draw_fn 可调用
- 图仓产图: build_chart_store 产 PNG + chart_manifest 清单
- 边界: required_keys 缺失→skip、produces_when 假→skip、异常→不吞
"""

from __future__ import annotations

import os
import tempfile
import numpy as np
import pandas as pd
import pytest

from core.chart_registry import (
    ChartProducer,
    CHART_ID_TO_OLD_FIG_ID,
    OLD_FIG_ID_TO_CHART_ID,
    ensure_draw_fns,
    get_all_producers,
    get_producer,
    get_producers_by_module,
    select_physical_series,
)
from core.chart_store import (
    ChartManifestEntry,
    build_chart_store,
)


# ═══════════════════════════════════════════════════════════════════════
# chart_data (真机等价— 本次 record 的全实况，确保全覆盖)
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def full_chart_data() -> dict:
    """含全套 chart_data（Phase 1a: calib_sensors 多传感器；中间态 calib_lin 因旧字段空而 skip）。
    ts_cleaning + hyst 可产；compare_ol/corr 空从而跳过。"""
    rng = np.random.default_rng(42)
    n = 200
    t = np.linspace(0, 10, n).tolist()
    return {
        "time_h": t,
        "series": {
            "w1": (500 * np.sin(t) + rng.normal(0, 5, n)).tolist(),
            "w2": (400 * np.cos(t) + rng.normal(0, 3, n)).tolist(),
        },
        "cleaned": {
            "w1": (500 * np.sin(t)).tolist(),
            "w2": (400 * np.cos(t)).tolist(),
        },
        "time_downsample": 1,
        # ★ Phase 1a: 旧单字段已废弃 (空值, 产图层不再读取)
        "calib_ref": [],
        "calib_measured": [],
        "calib_sensor": "",
        "calib_unit": "",
        "calib_r2": 0.0,
        "calib_slope": 0.0,
        # ★ Phase 2: 多传感器 calib 字段 (6 传感器)
        "calib_sensors": [
            {"sensor": "A1", "ref": [0, 250, 500, 750, 1000, 1250],
             "measured": [0.1, 250.3, 501.0, 749.5, 1001.2, 1250.8],
             "slope": 1.001, "r2": 0.9998, "unit": "με"},
            {"sensor": "A2", "ref": [0, 250, 500, 750, 1000, 1250],
             "measured": [0.0, 249.8, 499.5, 750.1, 1000.3, 1249.9],
             "slope": 0.999, "r2": 0.9999, "unit": "με"},
            {"sensor": "B1", "ref": [0, 250, 500, 750, 1000, 1250],
             "measured": [0.2, 250.5, 501.2, 749.8, 1000.7, 1251.0],
             "slope": 1.002, "r2": 0.9997, "unit": "με"},
            {"sensor": "B2", "ref": [0, 250, 500, 750, 1000, 1250],
             "measured": [0.1, 249.7, 500.0, 750.5, 1000.0, 1250.5],
             "slope": 1.000, "r2": 0.9999, "unit": "με"},
            {"sensor": "C1", "ref": [0, 250, 500, 750, 1000, 1250],
             "measured": [0.0, 250.1, 500.5, 749.9, 1001.0, 1250.2],
             "slope": 1.001, "r2": 0.9998, "unit": "με"},
            {"sensor": "C2", "ref": [0, 250, 500, 750, 1000, 1250],
             "measured": [0.3, 250.4, 500.8, 750.2, 1000.5, 1250.7],
             "slope": 1.001, "r2": 0.9996, "unit": "με"},
        ],
        "hyst_sensors": [
            {
                "name": "A1",
                "T_abs": np.linspace(25, 85, 100).tolist(),
                "eps": (500 * np.sin(np.linspace(0, np.pi, 100))
                        + rng.normal(0, 5, 100)).tolist(),
            },
            {
                "name": "A2",
                "T_abs": np.linspace(25, 85, 100).tolist(),
                "eps": (400 * np.sin(np.linspace(0, np.pi, 100))
                        + rng.normal(0, 3, 100)).tolist(),
            },
        ],
        "compare_time_h": [],
        "compare_sources": {},
        "compare_pairs": [],
        "grade_table_md": "| A |\n|---|\n| x |",
    }


@pytest.fixture
def cd_with_compare() -> dict:
    """chart_data 含 compare_time_h/compare_sources/pairs — compare_ol + corr 可产"""
    rng = np.random.default_rng(99)
    n = 60
    t = np.linspace(0, 6, n).tolist()
    return {
        "time_h": np.linspace(0, 1, 100).tolist(),
        "series": {"w1": [1550.0] * 100},
        # ★ Phase 1a: 旧单字段已废弃
        "calib_ref": [],
        "calib_measured": [],
        "calib_sensor": "",
        "calib_unit": "",
        # ★ Phase 1a: 新多传感器字段
        "calib_sensors": [
            {"sensor": "B1", "ref": [0, 500, 1000],
             "measured": [0.2, 500.3, 1000.5],
             "slope": 0.0, "r2": 0.0, "unit": "με"},
        ],
        "compare_time_h": t,
        "compare_sources": {
            "设备A": (np.sin(t) * 100 + 500 + rng.normal(0, 2, n)).tolist(),
            "设备B": (np.sin(t) * 100 + 502 + rng.normal(0, 2, n)).tolist(),
        },
        "compare_pairs": [
            {"device_a": "设备A", "device_b": "设备B",
             "corr": 0.97, "rmse": 2.3, "mae": 1.8},
        ],
        "hyst_sensors": [],
    }


@pytest.fixture
def minimal_chart_data() -> dict:
    """仅 ts_cleaning 能产"""
    return {
        "time_h": [0.0, 1.0, 2.0, 3.0],
        "series": {"ch1": [1.0, 2.0, 3.0, 4.0]},
        "cleaned": {"ch1": [1.0, 2.0, 2.9, 4.0]},
    }


@pytest.fixture
def physical_chart_data() -> dict:
    """阶段 1-b：四类物理量时程图均具备数据。"""
    t = [0.0, 0.5, 1.0, 1.5]
    return {
        "time_h": t,
        "series_delta": {
            "W1": [0.0, 0.01, 0.02, 0.03],
            "W2": [0.0, -0.01, -0.02, -0.03],
        },
        "phys_series": {
            "A1_应变": [0.0, 100.0, 200.0, 300.0],
            "A2_应变": [0.0, 90.0, 180.0, 270.0],
            "A1_温度": [25.0, 26.0, 27.0, 28.0],
            "位移1": [0.0, 0.1, 0.2, 0.3],
            "自定义公式1": [1.0, 1.5, 2.0, 2.5],
        },
    }


@pytest.fixture
def standard_calibration_chart_data() -> dict:
    return {
        "temp_regressions": [
            {
                "name": "W1",
                "display": "A1-W1",
                "temperature": [20.0, 30.0, 40.0],
                "drift_pm": [0.0, 280.0, 560.0],
                "slope": 28.0,
                "intercept": -560.0,
                "r2": 0.9999,
            },
            {
                "name": "W2",
                "display": "A1-W2",
                "temperature": [20.0, 30.0, 40.0],
                "drift_pm": [0.0, 300.0, 600.0],
                "slope": 30.0,
                "intercept": -600.0,
                "r2": 0.9998,
            },
        ],
        "phaseb_diagnostics": [
            {
                "name": "A1",
                "time_h": [0.0, 0.1, 0.2, 0.3],
                "dl1_pm": [0.0, 140.0, 280.0, 420.0],
                "dl2_pm": [0.0, 150.0, 300.0, 450.0],
                "d_temperature": [0.0, 5.0, 10.0, 15.0],
                "absolute_temperature": [20.0, 25.0, 30.0, 35.0],
                "eps_raw": [0.0, 10.0, -5.0, 2.0],
                "eps_compensated": [0.0, 1.0, -0.5, 0.2],
            },
        ],
    }


@pytest.fixture
def empty_chart_data() -> dict:
    return {}


# ═══════════════════════════════════════════════════════════════════════
# 注册表测试
# ═══════════════════════════════════════════════════════════════════════

class TestChartRegistry:
    """注册表结构正确性"""

    def test_eleven_producers_registered(self):
        producers = get_all_producers()
        assert len(producers) == 11
        ids = {p.chart_id for p in producers}
        assert ids == {
            "strain_calib_lin",
            "data_ts_cleaning",
            "data_ts_dlambda",
            "data_ts_strain",
            "data_ts_temperature",
            "data_ts_formula",
            "tempa_regression",
            "phaseb_diagnostic",
            "compare_ol",
            "compare_corr_scatter",
            "phaseb_hyst",
        }

    def test_chart_ids_unique(self):
        producers = get_all_producers()
        ids = [p.chart_id for p in producers]
        assert len(ids) == len(set(ids)), f"重复 chart_id: {ids}"

    def test_draw_fns_not_none_after_ensure(self):
        ensure_draw_fns()
        for p in get_all_producers():
            assert p.draw_fn is not None, f"{p.chart_id} draw_fn 为 None"

    def test_get_producer_returns_correct(self):
        p = get_producer("strain_calib_lin")
        assert p is not None
        assert p.chart_id == "strain_calib_lin"
        assert p.module == "strain_calib"
        assert p.title == "应变标定曲线"

    def test_get_producer_not_found(self):
        assert get_producer("nonexistent") is None

    def test_filter_by_module(self):
        compare = get_producers_by_module("compare")
        assert len(compare) == 2
        cids = {p.chart_id for p in compare}
        assert cids == {"compare_ol", "compare_corr_scatter"}

    def test_chart_id_to_old_fig_id_map(self):
        """每个 chart_id 都有对应的旧 fig_id"""
        for p in get_all_producers():
            assert p.chart_id in CHART_ID_TO_OLD_FIG_ID, \
                f"{p.chart_id} 不在映射表中"

    def test_required_keys_and_produces_when_defined(self):
        """每个 producer 都填了 required_keys 和 produces_when"""
        for p in get_all_producers():
            assert p.required_keys, f"{p.chart_id} required_keys 为空"
            assert p.produces_when is not None, f"{p.chart_id} produces_when 为空"

    def test_phase_1b_producers_are_t2_single_charts(self):
        ids = {
            "data_ts_dlambda",
            "data_ts_strain",
            "data_ts_temperature",
            "data_ts_formula",
        }
        for chart_id in ids:
            producer = get_producer(chart_id)
            assert producer is not None
            assert producer.tier == "T2"
            assert producer.module == "data_analysis"

    def test_physical_series_partition_is_mutually_exclusive(self, physical_chart_data):
        strain = select_physical_series(physical_chart_data, "strain")
        temperature = select_physical_series(physical_chart_data, "temperature")
        formula = select_physical_series(physical_chart_data, "formula")

        assert set(strain) == {"A1_应变", "A2_应变"}
        assert set(temperature) == {"A1_温度"}
        assert set(formula) == {"位移1", "自定义公式1"}
        assert not (set(strain) & set(temperature))
        assert not (set(strain) & set(formula))
        assert not (set(temperature) & set(formula))


# ═══════════════════════════════════════════════════════════════════════
# 图仓产图测试 — 行为等价
# ═══════════════════════════════════════════════════════════════════════

class TestBuildChartStoreEquivalence:
    """build_chart_store 产图正确性验证"""

    def test_chart_id_set_full_data(self, full_chart_data):
        """full_chart_data: 6 calib + ts_cleaning + 2 hyst → 全部产且不 skip。
        Phase 2: calib_lin 列表类 — 每传感器一张独立图。"""
        from core.report_charts import FigureManifest

        # ── 新路径 ──
        with tempfile.TemporaryDirectory() as new_dir:
            new_warnings: list[str] = []
            entries = build_chart_store(full_chart_data, new_dir, new_warnings)

            new_made_produced = {e.chart_id for e in entries if e.produced}

            # Phase 2: calib_lin — 6 传感器 → 6 个 produced entries
            calib_entries = [e for e in entries if e.chart_id.startswith("strain_calib_lin_")]
            assert len(calib_entries) == 6, \
                f"calib 应有 6 entries, 实际 {len(calib_entries)}: {[e.chart_id for e in calib_entries]}"
            for e in calib_entries:
                assert e.produced, f"{e.chart_id} 应为 produced"

            # 无旧的单 entry (strain_calib_lin 裸名)
            assert "strain_calib_lin" not in new_made_produced, \
                f"不应有裸 strain_calib_lin entry, made={new_made_produced}"

            # ts_cleaning 必须产
            assert "data_ts_cleaning" in new_made_produced, \
                f"缺 data_ts_cleaning, made={new_made_produced}"

            # hyst: full_chart_data 有有效 eps → 应产 (非 skip)
            hyst_entries = [e for e in entries if e.chart_id.startswith("phaseb_hyst_")]
            assert len(hyst_entries) >= 1, f"hyst 应有 >=1 entry, 实际 {len(hyst_entries)}"
            hyst_produced = sum(1 for e in hyst_entries if e.produced)
            assert hyst_produced >= 1, f"hyst 应产 >=1, 实际 produced={hyst_produced}"

            # compare_ol / corr 应 skip (无 compare 数据)
            cmp_ol = [e for e in entries if e.chart_id == "compare_ol"]
            assert len(cmp_ol) == 1 and not cmp_ol[0].produced, \
                "compare_ol 应 skip (无 compare_time_h)"

    def test_png_files_valid(self, full_chart_data):
        """产出的 PNG 文件存在且大小合理。
        Phase 2: 6 calib PNGs + ts_cleaning + 2 hyst = 9 files。"""
        with tempfile.TemporaryDirectory() as new_dir:
            entries = build_chart_store(full_chart_data, new_dir, [])
            new_pngs = {}
            for e in entries:
                if e.produced and e.rel_path:
                    new_pngs[e.chart_id] = os.path.join(new_dir, e.rel_path)

            # Phase 2: 6 calib PNGs — 每传感器独立文件
            for sensor in ["A1", "A2", "B1", "B2", "C1", "C2"]:
                cid = f"strain_calib_lin_{sensor}"
                assert cid in new_pngs, f"缺 calib_{sensor} PNG, keys={list(new_pngs)}"
                png = new_pngs[cid]
                assert os.path.exists(png), f"PNG 缺失: {png}"
                assert os.path.getsize(png) > 1000, \
                    f"calib_{sensor} PNG 过小: {os.path.getsize(png)}"
                # 文件名唯一, 不互相覆盖
                assert f"calib_linearity_{sensor}.png" in png, \
                    f"文件名不含传感器名: {png}"

            # ts_cleaning PNG 存在且大小 > 1KB
            if "data_ts_cleaning" in new_pngs:
                png = new_pngs["data_ts_cleaning"]
                assert os.path.exists(png), f"PNG 缺失: {png}"
                assert os.path.getsize(png) > 1000, \
                    f"ts_cleaning PNG 过小: {os.path.getsize(png)}"

    def test_compare_ol_skipped_with_reason(self, full_chart_data):
        """无 compare_time_h → compare_ol skip + reason"""
        with tempfile.TemporaryDirectory() as d:
            entries = build_chart_store(full_chart_data, d, [])
            cmp_ol = [e for e in entries if e.chart_id == "compare_ol"]
            assert len(cmp_ol) == 1
            assert not cmp_ol[0].produced
            assert "cmp_ol" in cmp_ol[0].skip_reason or \
                   "compare_time_h" in cmp_ol[0].skip_reason, \
                   f"skip_reason should mention what's missing: {cmp_ol[0].skip_reason!r}"

    def test_compare_ol_with_data_produces_full(self, cd_with_compare):
        """有 compare_time_h + compare_sources + pairs → compare_ol + corr 全产"""
        with tempfile.TemporaryDirectory() as d:
            entries = build_chart_store(cd_with_compare, d, [])

            cmp_ol = [e for e in entries if e.chart_id == "compare_ol"]
            assert len(cmp_ol) == 1 and cmp_ol[0].produced, \
                "compare_ol 应产 (有 compare 数据)"

            corr = [e for e in entries
                    if e.chart_id.startswith("compare_corr_scatter_")]
            assert len(corr) >= 1, f"corr 应有 >=1 entry, 实际 {len(corr)}"
            assert sum(1 for e in corr if e.produced) >= 1, \
                f"corr 应产 >=1, produced={sum(1 for e in corr if e.produced)}"

    def test_minimal_only_ts_cleaning(self, minimal_chart_data):
        """仅 time_h+series → 仅产 ts_cleaning"""
        with tempfile.TemporaryDirectory() as d:
            entries = build_chart_store(minimal_chart_data, d, [])
            produced = {e.chart_id for e in entries if e.produced}
            assert produced == {"data_ts_cleaning"}

    def test_phase_1b_physical_timeseries_replace_dlambda(self, physical_chart_data):
        """有物理量时只产实际物理量图，不再重复输出 Δλ 图。"""
        expected_stats = {
            "data_ts_strain": "2通道",
            "data_ts_temperature": "1通道",
            "data_ts_formula": "2通道",
        }
        with tempfile.TemporaryDirectory() as d:
            entries = build_chart_store(physical_chart_data, d, [])
            by_id = {e.chart_id: e for e in entries}

            for chart_id, key_stat in expected_stats.items():
                entry = by_id[chart_id]
                assert entry.produced, f"{chart_id} 应产图: {entry.skip_reason}"
                assert entry.key_stat == key_stat
                png = os.path.join(d, entry.rel_path)
                assert os.path.exists(png), f"PNG 缺失: {png}"
                assert os.path.getsize(png) > 1000, f"PNG 过小: {png}"

            assert not by_id["data_ts_dlambda"].produced
            assert len({by_id[cid].rel_path for cid in expected_stats}) == 3

    def test_phase_1b_axis_labels_and_titles(self, monkeypatch, physical_chart_data):
        """物理量图复用 make_timeseries，并保留各自物理单位和标题。"""
        import matplotlib.pyplot as plt
        import core.report_charts as report_charts

        captured: dict[str, tuple[str, str]] = {}

        def _capture_figure(fig, path, dpi=200):
            del dpi
            captured[os.path.basename(path)] = (
                fig.axes[0].get_ylabel(),
                fig.axes[0].get_title(),
            )
            plt.close(fig)
            return path

        monkeypatch.setattr(report_charts, "save_figure", _capture_figure)
        with tempfile.TemporaryDirectory() as d:
            build_chart_store(physical_chart_data, d, [])

        assert captured == {
            "ts_strain.png": ("应变 (με)", "应变时程"),
            "ts_temperature.png": ("温度 (°C)", "温度时程"),
            "ts_formula.png": ("值", "自定义公式时程"),
        }

    def test_phase_1b_figure_manifest_keeps_chart_ids(self, physical_chart_data):
        """新图无旧 fig_id，报告 manifest 应保留稳定的 data_ts_* 标识。"""
        from core.chart_store import chart_manifest_to_figure_manifest

        expected = {
            "data_ts_strain",
            "data_ts_temperature",
            "data_ts_formula",
        }
        with tempfile.TemporaryDirectory() as d:
            manifest = build_chart_store(physical_chart_data, d, [])
            figure_manifest = chart_manifest_to_figure_manifest(manifest, d)

        assert expected <= {figure.fig_id for figure in figure_manifest}

    def test_phase_1b_empty_groups_skip_independently(self):
        """phys_series 只有应变时，温度/公式跳过但应变仍产出。"""
        cd = {
            "time_h": [0.0, 1.0, 2.0],
            "series_delta": {},
            "phys_series": {"A1_应变": [0.0, 1.0, 2.0]},
        }
        with tempfile.TemporaryDirectory() as d:
            entries = build_chart_store(cd, d, [])
            by_id = {e.chart_id: e for e in entries}

            assert by_id["data_ts_strain"].produced
            assert not by_id["data_ts_dlambda"].produced
            assert not by_id["data_ts_temperature"].produced
            assert not by_id["data_ts_formula"].produced
            assert "produces_when=False" in by_id["data_ts_temperature"].skip_reason

    def test_no_physical_series_produces_only_dlambda(self):
        cd = {
            "time_h": [0.0, 1.0, 2.0],
            "series_delta": {"W1": [0.0, 0.1, 0.2]},
            "phys_series": {},
        }
        with tempfile.TemporaryDirectory() as d:
            entries = build_chart_store(cd, d, [])
            produced = {entry.chart_id for entry in entries if entry.produced}

        assert produced == {"data_ts_dlambda"}

    def test_standard_temperature_calibration_charts_produced(
        self,
        standard_calibration_chart_data,
    ):
        with tempfile.TemporaryDirectory() as d:
            entries = build_chart_store(standard_calibration_chart_data, d, [])
            by_id = {entry.chart_id: entry for entry in entries}

            phase_a = by_id["tempa_regression"]
            phase_b_raw = by_id["phaseb_diagnostic_A1_raw"]
            phase_b_compensated = by_id[
                "phaseb_diagnostic_A1_compensated"
            ]
            assert phase_a.produced
            assert phase_b_raw.produced
            assert phase_b_compensated.produced
            assert os.path.getsize(os.path.join(d, phase_a.rel_path)) > 1000
            assert os.path.getsize(
                os.path.join(d, phase_b_raw.rel_path)
            ) > 1000
            assert os.path.getsize(
                os.path.join(d, phase_b_compensated.rel_path)
            ) > 1000

    def test_phase_b_produces_raw_and_compensated_diagnostic_pairs(self):
        """每个双栅传感器必须生成补偿前、补偿后两版四联图。"""
        chart_data = {
            "phaseb_diagnostics": [
                {
                    "name": "A1",
                    "time_h": [0.0, 0.1, 0.2, 0.3],
                    "dl1_pm": [0.0, 140.0, 280.0, 420.0],
                    "dl2_pm": [0.0, 150.0, 300.0, 450.0],
                    "d_temperature": [0.0, 5.0, 10.0, 15.0],
                    "absolute_temperature": [20.0, 25.0, 30.0, 35.0],
                    "eps_raw": [0.0, 10.0, -5.0, 2.0],
                    "eps_compensated": [0.0, 1.0, -0.5, 0.2],
                },
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            entries = build_chart_store(chart_data, directory, [])
            produced = {
                entry.chart_id: entry
                for entry in entries
                if entry.produced
            }

            assert {
                "phaseb_diagnostic_A1_raw",
                "phaseb_diagnostic_A1_compensated",
            } <= set(produced)
            pair_ids = [
                entry.chart_id
                for entry in entries
                if entry.chart_id.startswith("phaseb_diagnostic_A1_")
            ]
            assert pair_ids == [
                "phaseb_diagnostic_A1_raw",
                "phaseb_diagnostic_A1_compensated",
            ]
            for entry in produced.values():
                if entry.chart_id.startswith("phaseb_diagnostic_A1_"):
                    assert os.path.getsize(
                        os.path.join(directory, entry.rel_path)
                    ) > 1000
            assert "原始（补偿前）" in produced[
                "phaseb_diagnostic_A1_raw"
            ].title
            assert "补偿后" in produced[
                "phaseb_diagnostic_A1_compensated"
            ].title

    def test_phase_b_missing_compensated_series_is_fail_loud(self):
        """旧数据只能生成补偿前图时，补偿后条目必须显式失败并告警。"""
        chart_data = {
            "phaseb_diagnostics": [
                {
                    "name": "A1",
                    "time_h": [0.0, 0.1, 0.2, 0.3],
                    "dl1_pm": [0.0, 140.0, 280.0, 420.0],
                    "dl2_pm": [0.0, 150.0, 300.0, 450.0],
                    "d_temperature": [0.0, 5.0, 10.0, 15.0],
                    "absolute_temperature": [20.0, 25.0, 30.0, 35.0],
                    "eps_raw": [0.0, 10.0, -5.0, 2.0],
                },
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            warnings: list[str] = []
            entries = build_chart_store(chart_data, directory, warnings)
            by_id = {entry.chart_id: entry for entry in entries}

        assert by_id["phaseb_diagnostic_A1_raw"].produced
        assert not by_id["phaseb_diagnostic_A1_compensated"].produced
        assert "eps_compensated" in by_id[
            "phaseb_diagnostic_A1_compensated"
        ].skip_reason
        assert any("补偿后四联图[A1]失败" in warning for warning in warnings)

    def test_hyst_valid_zero_skips_with_reason(self):
        """hyst_sensors 存在但 valid 全为 0 (全 NaN eps) → 全部 skip + reason"""
        cd = {
            "time_h": [0.0, 1.0],
            "series": {"ch1": [1.0, 2.0]},
            "hyst_sensors": [
                {"name": "B1", "T_abs": [25.0, 85.0],
                 "eps": [float("nan"), float("nan")]},
                {"name": "B2", "T_abs": [25.0, 85.0],
                 "eps": [float("nan"), float("nan")]},
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            warnings: list[str] = []
            entries = build_chart_store(cd, d, warnings)

            hyst_entries = [e for e in entries
                            if e.chart_id.startswith("phaseb_hyst_")]
            assert len(hyst_entries) == 2
            for e in hyst_entries:
                assert not e.produced
                assert "valid=" in e.skip_reason, \
                    f"skip_reason 应含 valid 真值: {e.skip_reason!r}"
            assert any("迟滞" in w for w in warnings), \
                f"warnings 应含迟滞 skip 提示: {warnings}"

    def test_empty_chart_data_all_skip(self, empty_chart_data):
        """全空 → 全部注册图均 skip"""
        with tempfile.TemporaryDirectory() as d:
            warnings: list[str] = []
            entries = build_chart_store(empty_chart_data, d, warnings)
            assert len(entries) == 11
            assert sum(1 for e in entries if e.produced) == 0

    def test_missing_required_keys_skip_not_crash(self):
        """chart_data 缺 calib_sensors → skip 不崩 (Phase 2: required_keys=["calib_sensors"])"""
        cd = {
            "calib_ref": [],  # empty old field — no effect
            "calib_measured": [],
        }
        with tempfile.TemporaryDirectory() as d:
            entries = build_chart_store(cd, d, [])
            calib = [e for e in entries if e.chart_id == "strain_calib_lin"]
            assert len(calib) == 1
            assert not calib[0].produced
            assert "calib_sensors" in calib[0].skip_reason, \
                f"skip_reason 应提缺 calib_sensors: {calib[0].skip_reason!r}"

    def test_exception_in_produce_does_not_break_others(self, monkeypatch, full_chart_data):
        """一个图异常不影响其他图产出。
        Phase 2: calib_lin 正常产6张, hyst 不受 ts_cleaning 异常影响。"""
        def _failing(*args, **kw):
            raise RuntimeError("模拟绘制崩溃")

        from core.chart_store import _PRODUCER_FN
        original_fn = _PRODUCER_FN["data_ts_cleaning"]
        monkeypatch.setitem(_PRODUCER_FN, "data_ts_cleaning", _failing)

        with tempfile.TemporaryDirectory() as d:
            warnings: list[str] = []
            entries = build_chart_store(full_chart_data, d, warnings)

            # ts_cleaning 应 skip (异常)
            ts = [e for e in entries if e.chart_id == "data_ts_cleaning"]
            assert len(ts) == 1 and not ts[0].produced
            assert "RuntimeError" in ts[0].skip_reason

            # Phase 2: calib_lin 应仍产 6 张 (不受 ts_cleaning 异常影响)
            calib = [e for e in entries if e.chart_id.startswith("strain_calib_lin_") and e.produced]
            assert len(calib) == 6, f"calib 应仍产 6 张, 实际 {len(calib)}"

            # hyst 应仍产 (不受 ts_cleaning 异常影响)
            hyst = [e for e in entries if e.chart_id.startswith("phaseb_hyst_") and e.produced]
            assert len(hyst) >= 1, "hyst 应仍产"

        monkeypatch.setitem(_PRODUCER_FN, "data_ts_cleaning", original_fn)

    def test_corr_scatter_per_item_skip_handled(self):
        """compare_pairs 存在但 compare_sources 缺对应源 → 内层 skip 记 reason"""
        cd = {
            "time_h": [0.0, 1.0],
            "series": {"ch1": [1.0, 2.0]},
            "compare_time_h": [0.0, 5.0, 10.0],
            "compare_sources": {},  # 空 → 每 pair 都取不到源
            "compare_pairs": [
                {"device_a": "A", "device_b": "B", "corr": 0.9, "rmse": 1.0},
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            entries = build_chart_store(cd, d, [])
            corr = [e for e in entries
                    if e.chart_id.startswith("compare_corr_scatter_")]
            assert len(corr) >= 1
            for e in corr:
                assert not e.produced
                assert "len=" in e.skip_reason, \
                    f"per-item skip reason 应含 len: {e.skip_reason!r}"

    # ── Phase 2 calib_lin 列表类新增测试 ──

    def test_calib_six_sensors_six_entries(self):
        """6 传感器 calib_sensors → 6 个 produced entries + 6 个不同文件名。"""
        cd = {
            "time_h": [0.0, 1.0],
            "series": {"ch1": [1.0, 2.0]},
            "calib_sensors": [
                {"sensor": "A1", "ref": [0, 250, 500], "measured": [0, 250.3, 501],
                 "slope": 1.0, "r2": 0.999, "unit": "pm"},
                {"sensor": "A2", "ref": [0, 250, 500], "measured": [0, 249.8, 500.5],
                 "slope": 1.0, "r2": 0.999, "unit": "pm"},
                {"sensor": "B1", "ref": [0, 250, 500], "measured": [0, 250.5, 501.2],
                 "slope": 1.0, "r2": 0.999, "unit": "pm"},
                {"sensor": "B2", "ref": [0, 250, 500], "measured": [0, 249.7, 500.0],
                 "slope": 1.0, "r2": 0.999, "unit": "pm"},
                {"sensor": "C1", "ref": [0, 250, 500], "measured": [0, 250.1, 500.5],
                 "slope": 1.0, "r2": 0.999, "unit": "pm"},
                {"sensor": "C2", "ref": [0, 250, 500], "measured": [0, 250.4, 500.8],
                 "slope": 1.0, "r2": 0.999, "unit": "pm"},
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            entries = build_chart_store(cd, d, [])
            calib = [e for e in entries if e.chart_id.startswith("strain_calib_lin_")]
            assert len(calib) == 6, f"应有 6 个 calib entries, 实际 {len(calib)}"
            # 全部 produced
            for e in calib:
                assert e.produced, f"{e.chart_id} 应为 produced"
            # 6 个不同文件名 — 不互相覆盖
            pngs = {e.rel_path for e in calib if e.rel_path}
            assert len(pngs) == 6, f"应有 6 个不同文件名, 实际 {len(pngs)}: {pngs}"
            for name in ["A1", "A2", "B1", "B2", "C1", "C2"]:
                assert any(f"calib_linearity_{name}.png" == p for p in pngs), \
                    f"缺 calib_linearity_{name}.png, pngs={pngs}"

    def test_calib_key_stat_per_sensor(self):
        """Phase 2: key_stat per-sensor — 每个 entry 的 key_stat 是自己的 R²/k。"""
        cd = {
            "time_h": [0.0, 1.0],
            "series": {"ch1": [1.0, 2.0]},
            "calib_sensors": [
                {"sensor": "A1", "ref": [0, 250, 500], "measured": [0, 250.3, 501],
                 "slope": 1.234, "r2": 0.9998, "unit": "pm"},
                {"sensor": "A2", "ref": [0, 250, 500], "measured": [0, 249.8, 500.5],
                 "slope": 0.998, "r2": 0.9999, "unit": "pm"},
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            entries = build_chart_store(cd, d, [])
            calib = {e.chart_id: e for e in entries if e.chart_id.startswith("strain_calib_lin_")}
            assert len(calib) == 2
            # A1: slope=1.234, r2=0.9998
            a1 = calib.get("strain_calib_lin_A1")
            assert a1 is not None and a1.produced
            assert "R²=0.9998" in a1.key_stat and "k=1.23" in a1.key_stat, \
                f"A1 key_stat: {a1.key_stat!r}"
            # A2: slope=0.998, r2=0.9999 — 不同值, 不是拷贝同一份
            a2 = calib.get("strain_calib_lin_A2")
            assert a2 is not None and a2.produced
            assert "R²=0.9999" in a2.key_stat and "k=1.00" in a2.key_stat, \
                f"A2 key_stat: {a2.key_stat!r}"
            assert a1.key_stat != a2.key_stat, \
                "A1/A2 key_stat 不应相同 (不同传感器不同系数)"

    def test_calib_old_flat_format_skip_not_crash(self):
        """旧格式 chart_data (仅旧扁平字段, 无 calib_sensors) → skip 不崩。"""
        cd = {
            "time_h": [0.0, 1.0],
            "series": {"ch1": [1.0, 2.0]},
            "calib_ref": [0, 250, 500],
            "calib_measured": [0, 250.3, 501],
            "calib_sensor": "A1",
            "calib_unit": "με",
        }
        with tempfile.TemporaryDirectory() as d:
            entries = build_chart_store(cd, d, [])
            calib = [e for e in entries if e.chart_id == "strain_calib_lin"]
            assert len(calib) == 1
            assert not calib[0].produced
            assert "calib_sensors" in calib[0].skip_reason, \
                f"应 skip 缺 calib_sensors: {calib[0].skip_reason!r}"

    def test_calib_partial_valid_produces_only_valid(self):
        """部分传感器 ref>=3, 部分不足 → 达标的产, 不达标的 skip。"""
        cd = {
            "time_h": [0.0, 1.0],
            "series": {"ch1": [1.0, 2.0]},
            "calib_sensors": [
                {"sensor": "OK1", "ref": [0, 250, 500], "measured": [0, 250.3, 501],
                 "slope": 1.0, "r2": 0.999, "unit": "pm"},
                {"sensor": "FAIL_short", "ref": [0, 250], "measured": [0, 250],
                 "slope": 1.0, "r2": 0.0, "unit": "pm"},
                {"sensor": "OK2", "ref": [0, 250, 500, 750], "measured": [0, 250, 500, 750],
                 "slope": 1.0, "r2": 1.0, "unit": "pm"},
            ],
        }
        with tempfile.TemporaryDirectory() as d:
            w: list[str] = []
            entries = build_chart_store(cd, d, w)
            calib = {e.chart_id: e for e in entries if e.chart_id.startswith("strain_calib_lin_")}
            assert len(calib) == 3
            # OK1/OK2 produced
            assert calib["strain_calib_lin_OK1"].produced
            assert calib["strain_calib_lin_OK2"].produced
            # FAIL_short skip 记 reason
            assert not calib["strain_calib_lin_FAIL_short"].produced
            assert "ref=2<3" in calib["strain_calib_lin_FAIL_short"].skip_reason, \
                f"FAIL_short skip_reason: {calib['strain_calib_lin_FAIL_short'].skip_reason!r}"
            # warnings 含提示
            assert any("FAIL_short" in x for x in w), \
                f"warnings 应提 FAIL_short: {w}"


# ═══════════════════════════════════════════════════════════════════════
# 0-B 接线集成测试 — build_chart_store → chart_manifest → get_chart → manifest
# ═══════════════════════════════════════════════════════════════════════

class TestBuildChartStoreIntegration:
    """全链: 产图→落盘→get_chart→manifest 转换"""

    def test_full_chain_chart_id_set(self, full_chart_data):
        """build_chart_store → chart_manifest → get_chart → manifest 转换完整链。
        Phase 2: calib_lin 列表类 — 6 传感器全链路。"""
        from core.chart_store import (
            build_chart_store, get_chart, chart_manifest_to_figure_manifest,
        )
        with tempfile.TemporaryDirectory() as charts_dir:
            cm = build_chart_store(full_chart_data, charts_dir, [])
            fm = chart_manifest_to_figure_manifest(cm, charts_dir)

            # 1. chart_id 集合 — Phase 2: 6 calib entries 在 manifest 中
            new_ids = {rf.fig_id for rf in fm}
            assert new_ids, "manifest 不应为空"
            calib_in_fm = [fid for fid in new_ids if "calib_lin" in fid or "strain_calib" in fid]
            assert len(calib_in_fm) >= 1, f"manifest 缺 calib, ids={new_ids}"
            assert any("ts_clean" in fid or "ts_cleaning" in fid
                       for fid in new_ids), f"缺 ts, ids={new_ids}"
            assert not any("hyst" in fid for fid in new_ids), \
                f"迟滞补充图不应进入正式报告, ids={new_ids}"
            hyst_png = get_chart("phaseb_hyst", cm, charts_dir)
            assert hyst_png is not None and os.path.exists(hyst_png), \
                "迟滞补充图仍应保留在诊断图目录"

            # 2. get_chart: 取 strain_calib_lin_A1 → PNG 存在
            png = get_chart("strain_calib_lin_A1", cm, charts_dir)
            assert png is not None and os.path.exists(png), \
                f"get_chart calib A1 应返回有效 PNG: {png}"

            # 3. PNG 落在 charts_dir 下
            for entry in cm:
                if entry.produced and entry.rel_path:
                    full = os.path.join(charts_dir, entry.rel_path)
                    assert os.path.exists(full), \
                        f"PNG 缺失: {entry.rel_path}"

            # 4. key_stat 来自注册表 (非空) — 仅 produced 条目
            for rf in fm:
                assert rf.key_stat, \
                    f"{rf.fig_id} key_stat 不应为空"

            # Phase 2: calib_lin key_stat 应含传感器名 (per-sensor)
            for rf in fm:
                if "calib_lin" in rf.fig_id:
                    assert "R²" in rf.key_stat and "k=" in rf.key_stat, \
                        f"calib key_stat 格式不对: {rf.key_stat!r}"

    def test_key_stat_from_registry(self, full_chart_data):
        """key_stat per-sensor 正确 — Phase 2: calib_lin 每传感器独立 key_stat。"""
        from core.chart_store import build_chart_store, chart_manifest_to_figure_manifest

        with tempfile.TemporaryDirectory() as d:
            cm = build_chart_store(full_chart_data, d, [])
            new_fm = chart_manifest_to_figure_manifest(cm, d)

        # Phase 2: 每个 calib_lin entry 的 key_stat 是自己的 R² + k
        calib_fm = [rf for rf in new_fm if "calib_lin" in rf.fig_id]
        assert len(calib_fm) == 6, f"应有 6 calib entries, 实际 {len(calib_fm)}"
        for rf in calib_fm:
            assert "R²" in rf.key_stat and "k=" in rf.key_stat, \
                f"{rf.fig_id} key_stat 格式不对: {rf.key_stat!r}"

    def test_get_chart_prefix_match(self, full_chart_data):
        """get_chart 用前缀 'phaseb_hyst' 能取到 hyst 图"""
        from core.chart_store import build_chart_store, get_chart
        with tempfile.TemporaryDirectory() as d:
            cm = build_chart_store(full_chart_data, d, [])
            # full_chart_data has valid eps → hyst produced
            png = get_chart("phaseb_hyst_A1", cm, d)
            hyst_entries = [e for e in cm if e.chart_id.startswith("phaseb_hyst_") and e.produced]
            if hyst_entries:
                assert png is not None, "get_chart phaseb_hyst 应找到 PNG"

    def test_get_chart_not_found(self, full_chart_data):
        """get_chart 查找不存在的 chart_id → None"""
        from core.chart_store import build_chart_store, get_chart
        with tempfile.TemporaryDirectory() as d:
            cm = build_chart_store(full_chart_data, d, [])
            assert get_chart("nonexistent_chart", cm, d) is None

    def test_manifest_to_figure_manifest_produces_valid_manifest(self, full_chart_data):
        """chart_manifest_to_figure_manifest 产出可用的 FigureManifest"""
        from core.chart_store import build_chart_store, chart_manifest_to_figure_manifest
        with tempfile.TemporaryDirectory() as d:
            cm = build_chart_store(full_chart_data, d, [])
            fm = chart_manifest_to_figure_manifest(cm, d)
            assert fm.count >= 1
            # 每个 ReportFigure 有 fig_id 和 png_path
            for rf in fm:
                assert rf.fig_id
                assert rf.png_path
                assert rf.key_stat


# ═══════════════════════════════════════════════════════════════
# Batch 3.6.6 P0 — duplicate planning asset IDs regression
# ═══════════════════════════════════════════════════════════════


class TestFigureManifestDuplicatePrevention:
    """chart_manifest_to_figure_manifest 不得把多个 per-item 实例合并到同一个旧 aggregate fig_id。"""

    @staticmethod
    def _entry(chart_id: str, produced: bool = True,
               rel_path: str = "dummy.png", report_include: bool = True,
               module: str = "test", title: str = "",
               key_stat: str = "") -> ChartManifestEntry:
        return ChartManifestEntry(
            chart_id=chart_id, module=module, title=title or chart_id,
            rel_path=rel_path, produced=produced, report_include=report_include,
            key_stat=key_stat,
        )

    def _fig_ids(self, entries: list[ChartManifestEntry],
                 charts_dir: str = ".") -> list[str]:
        from core.chart_store import chart_manifest_to_figure_manifest
        fm = chart_manifest_to_figure_manifest(entries, charts_dir)
        return [rf.fig_id for rf in fm]

    # ── per-item uniqueness ──

    def test_corr_scatter_per_item_ids_unique(self):
        """compare_corr_scatter_0/1/2 → 三个唯一 fig_id，不合并为 corr_scatter."""
        entries = [
            self._entry("compare_corr_scatter_0", title="关联散点 0"),
            self._entry("compare_corr_scatter_1", title="关联散点 1"),
            self._entry("compare_corr_scatter_2", title="关联散点 2"),
        ]
        ids = self._fig_ids(entries)
        assert ids == ["compare_corr_scatter_0", "compare_corr_scatter_1", "compare_corr_scatter_2"]
        assert len(ids) == len(set(ids)) == 3

    def test_hyst_per_item_ids_unique(self):
        """phaseb_hyst_A1/A2/B1 → 三个唯一 fig_id，不合并为 hyst_loop."""
        entries = [
            self._entry("phaseb_hyst_A1", title="A1 迟滞回线"),
            self._entry("phaseb_hyst_A2", title="A2 迟滞回线"),
            self._entry("phaseb_hyst_B1", title="B1 迟滞回线"),
        ]
        ids = self._fig_ids(entries)
        assert ids == ["phaseb_hyst_A1", "phaseb_hyst_A2", "phaseb_hyst_B1"]
        assert len(ids) == len(set(ids)) == 3

    # ── type-level mapping still works ──

    def test_type_level_mapping_still_works(self):
        """非 per-item 的 type-level chart_id 仍走旧 fig_id 映射."""
        entries = [
            self._entry("strain_calib_lin", title="标定线性"),
            self._entry("compare_ol", title="叠加对比"),
        ]
        ids = self._fig_ids(entries)
        assert ids == ["calib_lin", "cmp_ol"]

    def test_non_strippable_underscore_preserved(self):
        """含有 _ 但不是已知前缀的 chart_id 保持原样（不应用 mapping）."""
        entries = [
            self._entry("data_ts_dlambda", title="波长时程"),
            self._entry("tempa_regression", title="温度回归"),
        ]
        ids = self._fig_ids(entries)
        # data_ts_dlambda and tempa_regression are self-mapped (identity) in CHART_ID_TO_OLD_FIG_ID
        assert ids == ["data_ts_dlambda", "tempa_regression"]

    # ── mixed: per-item + type-level must all be unique ──

    def test_mixed_per_item_and_type_level_no_collision(self):
        """Per-item + type-level 混合时全唯一."""
        entries = [
            self._entry("compare_corr_scatter_0", title="关联散点 0"),
            self._entry("compare_corr_scatter_1", title="关联散点 1"),
            self._entry("compare_ol", title="叠加对比"),
            self._entry("strain_calib_lin", title="标定线性"),
        ]
        ids = self._fig_ids(entries)
        assert len(ids) == len(set(ids)) == 4
        # corr_scatter per-item 不合并
        assert ids[0] == "compare_corr_scatter_0"
        assert ids[1] == "compare_corr_scatter_1"

    # ── deterministic ordering ──

    def test_deterministic_ordering(self):
        """同一输入多次构建产出相同 fig_id 顺序."""
        entries = [
            self._entry("compare_corr_scatter_0"),
            self._entry("compare_corr_scatter_1"),
            self._entry("strain_calib_lin"),
            self._entry("phaseb_hyst_A1"),
        ]
        ids1 = self._fig_ids(entries)
        ids2 = self._fig_ids(entries)
        assert ids1 == ids2

    # ── required status preservation ──

    def test_all_entries_still_present(self):
        """修复不丢失任何 entry — 输入 5 个 active entries → 输出 5 个 figures."""
        entries = [
            self._entry("compare_corr_scatter_0"),
            self._entry("compare_corr_scatter_1"),
            self._entry("compare_corr_scatter_2"),
            self._entry("compare_ol"),
            self._entry("strain_calib_lin"),
        ]
        ids = self._fig_ids(entries)
        assert len(ids) == 5

    # ── full PlanningRequest pipeline ──

    def test_planning_assets_unique_from_real_manifest_pattern(self):
        """模拟真实诊断记录的 3×corr_scatter + 其他 → PlanningRequest 构造成功."""
        from core.chart_store import chart_manifest_to_figure_manifest
        from dp_engine.ppt_master_host.planning import (
            PlanningAsset, PlanningAssetKind, PlanningRequest, TemplateMode,
        )
        import tempfile, os as _os

        entries = [
            self._entry("compare_corr_scatter_0", title="关联散点 0"),
            self._entry("compare_corr_scatter_1", title="关联散点 1"),
            self._entry("compare_corr_scatter_2", title="关联散点 2"),
            self._entry("compare_ol", title="叠加对比"),
            self._entry("data_ts_dlambda", title="波长时程"),
            self._entry("strain_calib_lin_A1", title="A1 标定线性"),
            self._entry("strain_calib_lin_A2", title="A2 标定线性"),
            self._entry("phaseb_diagnostic_A1_raw", title="A1 诊断原始"),
            self._entry("phaseb_diagnostic_A1_compensated", title="A1 诊断补偿"),
            self._entry("tempa_regression", title="温度回归"),
        ]

        with tempfile.TemporaryDirectory() as d:
            # Create dummy PNG files
            for e in entries:
                p = _os.path.join(d, e.rel_path)
                with open(p, "wb") as f:
                    f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

            fm = chart_manifest_to_figure_manifest(entries, d)
            planning_assets = tuple(
                PlanningAsset(
                    asset_id=rf.fig_id,
                    kind=PlanningAssetKind.CHART,
                    semantic_label=rf.title,
                    summary=f"目标章节：{rf.section}",
                    required=True,
                )
                for rf in fm
            )
            # 这行在修复前会因为 duplicate asset_ids 抛 ValueError
            request = PlanningRequest(
                request_id="test-dup-regression",
                report_title="测试报告",
                objective="测试唯一性",
                audience="测试人员",
                source_context="测试上下文",
                requested_slide_count=10,
                template_mode=TemplateMode.FREE_DESIGN,
                assets=planning_assets,
            )
            assert len(request.assets) == len(entries) == 10
            asset_ids = [a.asset_id for a in request.assets]
            assert len(asset_ids) == len(set(asset_ids))
