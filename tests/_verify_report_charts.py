"""第三道门 — 真机目视验证：用合成数据生成 4 张核心图 PNG

验证项:
- 中文无方块
- 轴标签带单位
- 拟合+残差/迟滞环/分组柱/时序异常 均正确
- 无臆造数据点

用法: python tests/_verify_report_charts.py
输出: tests/_verify_output/ 下 4 张 PNG
"""

from __future__ import annotations

import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.report_charts import (
    setup_chinese_font,
    make_calibration_linearity,
    make_hysteresis_loop,
    make_sensor_grade_bar,
    make_timeseries,
    save_figure,
    FigureManifest,
)

OUT_DIR = os.path.join(os.path.dirname(__file__), "_verify_output")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    setup_chinese_font()
    rng = np.random.default_rng(42)

    # -- 图1: 标定线性度 --
    ref = np.linspace(0, 1000, 25)
    measured = ref * 1.002 + rng.normal(0, 3, 25)
    fig1 = make_calibration_linearity(ref, measured, sensor="C2", unit="με")
    p1 = save_figure(fig1, os.path.join(OUT_DIR, "01_calibration_linearity.png"))
    print("[OK] Fig 1 - Calibration Linearity ->", p1)

    # -- 图2: 迟滞回线 --
    t_up = np.linspace(10, 60, 200)
    eps_up = 2.0 * (t_up - 10) + rng.normal(0, 0.3, 200)
    t_down = np.linspace(60, 10, 200)
    eps_down = 2.0 * (t_down - 10) + 3.5 + rng.normal(0, 0.3, 200)
    t_all = np.concatenate([t_up, t_down])
    eps_all = np.concatenate([eps_up, eps_down])
    fig2 = make_hysteresis_loop(t_all, eps_all, sensor="C2",
                                x_label="温度 (°C)",
                                y_label="应变 (με)")
    p2 = save_figure(fig2, os.path.join(OUT_DIR, "02_hysteresis_loop.png"))
    print("[OK] Fig 2 - Hysteresis Loop ->", p2)

    # -- 图3: 传感器评级柱状图 --
    sensors = [
        {"name": "C1", "sigma": 2.3, "cv": 0.5, "hysteresis": 4.1, "grade": "优"},
        {"name": "C2", "sigma": 8.1, "cv": 1.2, "hysteresis": 12.3, "grade": "良"},
        {"name": "A1", "sigma": 18.5, "cv": 2.8, "hysteresis": 35.0, "grade": "合格"},
        {"name": "B1", "sigma": 25.0, "cv": 4.1, "hysteresis": 55.0, "grade": "FAIL"},
        {"name": "D1", "sigma": float("nan"), "cv": float("nan"),
         "hysteresis": float("nan"), "grade": "N/A"},
    ]
    fig3 = make_sensor_grade_bar(sensors, metrics=("sigma", "cv", "hysteresis"))
    p3 = save_figure(fig3, os.path.join(OUT_DIR, "03_sensor_grade_bar.png"))
    print("[OK] Fig 3 - Grade Bar ->", p3)

    # -- 图4: 时序曲线 --
    t = np.arange(600) / 3600.0 * 10
    ch1 = 1000 * np.sin(t / 2) + rng.normal(0, 5, 600)
    ch2 = 800 * np.cos(t / 3) + rng.normal(0, 3, 600)
    ch3 = 500 * np.sin(t / 1.5 + 1) + rng.normal(0, 4, 600)
    anomalies = {"C1_应变": [80, 200, 450]}
    fig4 = make_timeseries(t, {"C1_应变": ch1, "C2_应变": ch2,
                                "A1_应变": ch3},
                           y_label="应变 (με)", anomalies=anomalies)
    p4 = save_figure(fig4, os.path.join(OUT_DIR, "04_timeseries.png"))
    print("[OK] Fig 4 - Timeseries ->", p4)

    # -- 清单验证 --
    m = FigureManifest()
    m.add("linearity", "标定结果", "C2 标定线性度",
          "R^2=0.9998; sigma=3.1ue", p1)
    m.add("hysteresis", "标定结果", "C2 迟溞回线",
          "area~45.2; H_max=5.8ue", p2)
    m.add("grade", "评级汇总",
          "传感器核心指标评级",
          "C1=优; C2=良; A1=合格; B1=FAIL", p3)
    m.add("timeseries", "时序分析",
          "多通道应变时序", "anomalies=3", p4)

    print("\nFigureManifest ({} figs):".format(m.count))
    for rf in m:
        print("  {} - {}".format(rf.caption, rf.key_stat))

    print("\nLLM context:")
    print(" ", m.to_llm_context())
    print("\n" + "=" * 50)
    print("  4 verification PNGs ->", OUT_DIR)
    print("  Visually verify: Chinese no tofu, axis labels w/ units, correct content")
    print("=" * 50)


if __name__ == "__main__":
    main()
