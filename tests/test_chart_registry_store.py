"""core/chart_registry.py + core/chart_store.py — 阶段0-A 验收测试。

验证:
- 注册表: 5 个 producer 注册、chart_id 唯一、draw_fn 可调用
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
        "cleaned": {},
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
    }


@pytest.fixture
def empty_chart_data() -> dict:
    return {}


# ═══════════════════════════════════════════════════════════════════════
# 注册表测试
# ═══════════════════════════════════════════════════════════════════════

class TestChartRegistry:
    """注册表结构正确性"""

    def test_five_producers_registered(self):
        producers = get_all_producers()
        assert len(producers) == 5
        ids = {p.chart_id for p in producers}
        assert ids == {
            "strain_calib_lin",
            "data_ts_cleaning",
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
        assert p.title == "传感器标定线性度"

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
        """全空 → 5 个图均 skip"""
        with tempfile.TemporaryDirectory() as d:
            warnings: list[str] = []
            entries = build_chart_store(empty_chart_data, d, warnings)
            assert len(entries) >= 5  # at least one skip per producer
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
            assert any("hyst" in fid for fid in new_ids), \
                f"缺 hyst, ids={new_ids}"

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
