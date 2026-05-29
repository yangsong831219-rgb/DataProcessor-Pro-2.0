"""图表生成工具 — AI Function Calling 可调用。

AI 在撰写报告时，通过 generate_sensor_plot 工具
动态生成传感器趋势图/分布图，返回的文件名嵌入 [INSERT_IMAGE: xxx] 标签，
最终由渲染器自动插入 Word / PPT。
"""

from __future__ import annotations

import os
from typing import Any, Dict

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 支持中文字体
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False


# ═══════════════════════════════════════════════════════════════════
# OpenAI Function Calling 工具定义
# ═══════════════════════════════════════════════════════════════════

GENERATE_SENSOR_PLOT_TOOL = {
    "type": "function",
    "function": {
        "name": "generate_sensor_plot",
        "description": (
            "生成传感器趋势图或分布直方图。"
            "当你需要展示某个传感器的物理量随时间变化的趋势、"
            "或数值分布情况时，调用此工具。"
            "返回的字符串就是图片文件名，请将其放入 [INSERT_IMAGE: 文件名] 标签中插入报告。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sensor_id": {
                    "type": "string",
                    "description": (
                        "传感器 ID，例如 'F1'、'温度1'、'应变_1' 等，"
                        "与 sensors 列表中的 id 字段匹配"
                    ),
                },
                "plot_type": {
                    "type": "string",
                    "enum": ["trend", "histogram"],
                    "description": "trend=趋势折线图, histogram=数值分布直方图",
                },
            },
            "required": ["sensor_id", "plot_type"],
        },
    },
}


# ═══════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════


def generate_sensor_plot(
    sensor_id: str,
    plot_type: str,
    project_dir: str,
    state_data: Dict[str, Any],
) -> str:
    """根据 state_data 中的传感器数据绘制图表并保存到 project_dir.

    Args:
        sensor_id: 传感器 ID
        plot_type: 图表类型 (trend / histogram)
        project_dir: 保存图表的目录
        state_data: AppState.to_dict() 序列化数据

    Returns:
        图片文件名（不含路径），如 "sensor_F1_trend.png"
    """
    # ── 1. 从 state_data 提取传感器数值 ──
    sensor_results: Dict[str, list] = (
        state_data.get("analysis_tab", {}).get("sensor_results", {})
    )

    values = _find_sensor_values(sensor_id, sensor_results)

    if values is None or len(values) == 0:
        return _create_placeholder_plot(sensor_id, plot_type, project_dir)

    # 过滤 None / NaN
    arr = np.array(
        [
            v
            for v in values
            if v is not None
            and not (isinstance(v, float) and np.isnan(v))
        ],
        dtype=float,
    )
    if len(arr) == 0:
        return _create_placeholder_plot(sensor_id, plot_type, project_dir)

    # ── 2. 绘图 ──
    fig, ax = plt.subplots(figsize=(8, 4))

    if plot_type == "histogram":
        ax.hist(arr, bins=20, color="#1890FF", alpha=0.7, edgecolor="white")
        ax.set_xlabel(f"{sensor_id} 数值")
        ax.set_ylabel("频次")
        ax.set_title(f"{sensor_id} 数值分布")
        ax.grid(True, alpha=0.3)
    else:  # trend
        ax.plot(
            range(len(arr)),
            arr,
            color="#1890FF",
            linewidth=1.5,
            marker="o",
            markersize=2,
        )
        ax.set_xlabel("采样点序号")
        ax.set_ylabel(f"{sensor_id} 数值")
        ax.set_title(f"{sensor_id} 变化趋势")
        ax.grid(True, alpha=0.3)

    plt.tight_layout()

    # ── 3. 保存 ──
    safe_id = sensor_id.replace(" ", "_").replace("/", "_")
    filename = f"sensor_{safe_id}_{plot_type}.png"
    filepath = os.path.join(project_dir, filename)
    fig.savefig(filepath, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return filename


# ═══════════════════════════════════════════════════════════════════
# 内部工具函数
# ═══════════════════════════════════════════════════════════════════


def _find_sensor_values(
    sensor_id: str,
    sensor_results: Dict[str, list],
) -> list | None:
    """模糊匹配 sensor_id 在 sensor_results 中的数值列表."""
    # 精确匹配
    if sensor_id in sensor_results:
        return sensor_results[sensor_id]

    # 包含匹配
    for key, vals in sensor_results.items():
        if sensor_id in key or key in sensor_id:
            return vals

    return None


def _create_placeholder_plot(
    sensor_id: str, plot_type: str, project_dir: str
) -> str:
    """数据缺失时创建占位提示图，确保不崩溃."""
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.text(
        0.5,
        0.5,
        f"[{sensor_id} 无可用数据]",
        ha="center",
        va="center",
        fontsize=14,
        color="#999999",
    )
    ax.set_title(f"{sensor_id} — {plot_type}")
    ax.axis("off")
    plt.tight_layout()

    safe_id = sensor_id.replace(" ", "_").replace("/", "_")
    filename = f"sensor_{safe_id}_{plot_type}.png"
    filepath = os.path.join(project_dir, filename)
    fig.savefig(filepath, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return filename
