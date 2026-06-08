"""PhaseAWorker 回归测试 — 零 I/O 保证 + 合法暗号断言"""

from __future__ import annotations

import os
import pytest
import numpy as np
import pandas as pd

from PyQt6.QtCore import QThread


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def sample_df():
    """构造 7 级台阶的波长数据 (3 列: A1-W1, A1-W2, B2-W1)"""
    np.random.seed(42)
    plateau_len = 200
    all_parts = []
    for level in range(1, 8):  # 7 levels
        base_wl = 1525.0 + level * 5.0
        segment = {
            "A1-W1": np.full(plateau_len, base_wl) + np.random.normal(0, 0.1, plateau_len),
            "A1-W2": np.full(plateau_len, base_wl + 5.0) + np.random.normal(0, 0.1, plateau_len),
            "B2-W1": np.full(plateau_len, base_wl + 2.0) + np.random.normal(0, 0.1, plateau_len),
        }
        all_parts.append(pd.DataFrame(segment))
    return pd.concat(all_parts, ignore_index=True)


@pytest.fixture
def annotation():
    return {"A1-W1": "A1-W1", "A1-W2": "A1-W2", "B2-W1": "B2-W1"}


@pytest.fixture
def valid_params():
    return {
        "rolling_window": 50, "std_percentile": 85.0,
        "min_plateau_samples": 50, "head_trim_ratio": 0.70,
    }


# ═══════════════════════════════════════════════════════════════════════
# Test: Worker 不接触文件系统
# ═══════════════════════════════════════════════════════════════════════

class TestWorkerNoIO:
    """PhaseAWorker 绝不调用 pd.read_csv 或 open"""

    def test_no_read_csv_in_run(self, sample_df, annotation, valid_params, monkeypatch):
        """monkeypatch pd.read_csv 和 open → worker 不能触发它们"""
        from ui.calibration_tab import PhaseAWorker

        # 屏蔽文件 I/O
        read_csv_called = []
        open_called = []

        def _fake_read_csv(*args, **kwargs):
            read_csv_called.append(1)
            raise RuntimeError("PhaseAWorker 不应调用 pd.read_csv")
        monkeypatch.setattr("pandas.read_csv", _fake_read_csv)
        monkeypatch.setattr(pd, "read_csv", _fake_read_csv)

        import builtins
        _real_open = builtins.open
        def _fake_open(*args, **kwargs):
            open_called.append(1)
            raise RuntimeError("PhaseAWorker 不应调用 open")
        monkeypatch.setattr(builtins, "open", _fake_open)

        worker = PhaseAWorker(
            sample_df, ["A1-W1", "A1-W2", "B2-W1"],
            [10, 20, 30, 40, 50, 60, 70], valid_params,
        )
        worker.run()

        assert not read_csv_called, "PhaseAWorker 不应调用 pd.read_csv"
        assert not open_called, "PhaseAWorker 不应调用 open"


# ═══════════════════════════════════════════════════════════════════════
# Test: Worker 正确生产 S_eff
# ═══════════════════════════════════════════════════════════════════════

class TestWorkerOutput:
    """PhaseAWorker 对每个合法暗号波长列输出 S_eff / intercept / R²"""

    def test_all_wavelength_cols_have_S_eff(self, sample_df, valid_params):
        """3 个波长列 → S_eff 含 3 个键"""
        from ui.calibration_tab import PhaseAWorker

        worker = PhaseAWorker(
            sample_df, ["A1-W1", "A1-W2", "B2-W1"],
            [10, 20, 30, 40, 50, 60, 70], valid_params,
        )
        # 直接调用 run()（同线程，不需要 QApplication）
        worker.run()
        res = worker._last_result
        assert res is not None, "Worker 未设置 _last_result"

        S_eff = res["S_eff"]
        # ★ 公开 API key 必须是原始 df 列名，不得有 _d 后缀
        assert "A1-W1" in S_eff
        assert "A1-W2" in S_eff
        assert "B2-W1" in S_eff

        for wcol in ["A1-W1", "A1-W2", "B2-W1"]:
            s = S_eff[wcol]
            assert not np.isnan(s["slope"]), f"{wcol}: slope is NaN"
            assert s["slope"] > 0, f"{wcol}: slope should be positive, got {s['slope']}"
            assert -10000 < s["intercept"] < 10000, f"{wcol}: intercept {s['intercept']}"
            assert 0.99 <= s["r2"] <= 1.01, f"{wcol}: R²={s['r2']} out of range"

    def test_plateaus_found(self, sample_df, valid_params):
        """7 级台阶 → 检测到 ~7 个平台段"""
        from ui.calibration_tab import PhaseAWorker

        worker = PhaseAWorker(
            sample_df, ["A1-W1", "A1-W2"],
            [10, 20, 30, 40, 50, 60, 70], valid_params,
        )
        worker.run()
        res = worker._last_result
        assert res is not None, "Worker 未设置 _last_result"

        P = res["plateaus"]
        assert 6 <= len(P) <= 10, f"expected ~7 plateaus, got {len(P)}"


# ═══════════════════════════════════════════════════════════════════════
# Test: is_valid_annotation
# ═══════════════════════════════════════════════════════════════════════

class TestAnnotationValidity:
    """暗号合法性判定"""

    def test_valid_annotations(self):
        from ui.calibration_tab import is_valid_annotation
        assert is_valid_annotation("A1-W1")
        assert is_valid_annotation("B2-W2")
        assert is_valid_annotation("S1-WL")
        assert is_valid_annotation("CH1-W1")
        assert is_valid_annotation("CH1-W1")

    def test_invalid_placeholders(self):
        from ui.calibration_tab import is_valid_annotation
        assert not is_valid_annotation("w1-类型-位置")    # 含"类型"+"位置"
        assert not is_valid_annotation("w2-应变-梁底")    # 含中文但格式过宽
        assert not is_valid_annotation("用户备注名")       # 纯模板
        assert not is_valid_annotation("template")         # 模板词
        assert not is_valid_annotation("")                  # 空
        assert not is_valid_annotation("placeholder-w1")   # 含"placeholder"

    def test_boundary(self):
        from ui.calibration_tab import is_valid_annotation
        assert not is_valid_annotation("w1")               # 无连字符
        assert not is_valid_annotation("-w1")              # 前缀空
        assert not is_valid_annotation("w1-")              # 后缀空
        assert is_valid_annotation("A_1-W_2")              # 下划线合法
