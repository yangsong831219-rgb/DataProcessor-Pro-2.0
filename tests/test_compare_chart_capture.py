"""多源对比图表数据必须随结构化指标一起留存。"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd


class _ResultView:
    def __init__(self) -> None:
        self.html = ""
        self.visible = False

    def setHtml(self, html: str) -> None:
        self.html = html

    def setVisible(self, visible: bool) -> None:
        self.visible = visible


def test_compare_runtime_result_keeps_aligned_plot_series() -> None:
    """真实 UI 计算完成后，不能只留指标而丢掉报告绘图所需时程。"""
    from ui.compare_tab import CompareTabWidget

    time = pd.date_range("2026-07-20 10:00:00", periods=6, freq="4s")
    plot_bucket = [
        (time, np.asarray([10.0, 11.0, 13.0, 12.0, 15.0, 16.0]), "应变-光纤1"),
        (time, np.asarray([8.0, 9.0, 11.0, 10.0, 12.0, 13.0]), "应变-应变片1"),
    ]
    tab = SimpleNamespace(_comparison_result=_ResultView(), _last_comparison=None)

    CompareTabWidget._compute_and_display_comparison(tab, plot_bucket)

    result = tab._last_comparison
    assert result is not None
    assert result["time_h"][0] == 0.0
    assert len(result["time_h"]) == 6
    assert set(result["sources"]) == {"应变-光纤1", "应变-应变片1"}
    assert all(len(values) == 6 for values in result["sources"].values())
    assert len(result["pairs"]) == 1
