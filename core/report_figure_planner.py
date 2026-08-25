"""报告图表语义规划。

把确定性 FigureManifest 映射到 Word 章节和 PPT 页面。LLM 负责决定
图表如何支撑当前论点，代码负责限制候选范围并提供漏配兜底。
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable, Sequence


_MODULE_KEYWORDS: dict[str, tuple[tuple[str, int], ...]] = {
    "data_analysis": (
        ("波长", 8),
        ("时程", 5),
        ("数据分析", 7),
        ("数据总览", 6),
        ("原始数据", 4),
    ),
    "data_cleaning": (
        ("数据清洗", 9),
        ("异常", 4),
        ("数据质量", 5),
    ),
    "compare": (
        ("多源", 10),
        ("对比", 8),
        ("相关性", 6),
        ("不相关", 9),
        ("负相关", 9),
        ("独立", 6),
        ("波动", 6),
        ("异常", 4),
        ("一致性", 5),
        ("交叉验证", 4),
    ),
    "temperature_calib": (
        ("温度标定", 9),
        ("温度", 6),
        ("解耦", 7),
        ("补偿", 5),
        ("阶段a", 5),
        ("阶段b", 5),
        ("标定致命缺陷", 14),
        ("拦截线", 10),
        ("迟滞", 8),
        ("fail", 8),
        ("废品", 7),
        ("严禁", 5),
        ("评级", 5),
        ("诊断", 3),
        ("标定", 3),
    ),
    "strain_calib": (
        ("应变标定", 10),
        ("标定致命缺陷", 14),
        ("拦截线", 10),
        ("标定", 7),
        ("评级", 5),
        ("迟滞", 4),
        ("fail", 8),
        ("废品", 7),
        ("严禁", 5),
        ("传感器性能", 3),
    ),
}

_GENERIC_OVERVIEW_TOKENS = (
    "框架",
    "评估体系",
    "核心任务",
    "实验目标",
)

_TITLE_TOKENS = (
    "波长",
    "时程",
    "多源",
    "对比",
    "相关",
    "应变",
    "温度",
    "标定",
    "解耦",
    "回归",
    "诊断",
    "阶段a",
    "阶段b",
)


def _normalise(text: object) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def build_figure_catalog(manifest: Iterable[Any] | None) -> list[dict[str, Any]]:
    """把 FigureManifest 转为可序列化、可注入 prompt 的图表目录。"""
    if manifest is None:
        return []
    catalog: list[dict[str, Any]] = []
    for figure in manifest:
        png_path = str(getattr(figure, "png_path", "") or "")
        if not png_path:
            continue
        catalog.append({
            "fig_id": str(getattr(figure, "fig_id", "") or ""),
            "fig_no": int(getattr(figure, "fig_no", 0) or 0),
            "module": str(getattr(figure, "section", "") or ""),
            "title": str(getattr(figure, "title", "") or ""),
            "key_stat": str(getattr(figure, "key_stat", "") or ""),
            "filename": os.path.basename(png_path),
            "png_path": png_path,
        })
    return catalog


def figure_relevance_score(figure: dict[str, Any], section_text: object) -> int:
    """计算单张图与章节/幻灯片标题的语义相关度。"""
    text = _normalise(section_text)
    module = str(figure.get("module") or "")
    score = sum(
        weight
        for keyword, weight in _MODULE_KEYWORDS.get(module, ())
        if keyword in text
    )

    title = _normalise(figure.get("title"))
    for token in _TITLE_TOKENS:
        if token in title and token in text:
            score += 4

    # 阶段 B 四联图应优先靠近诊断/机理章节，而不是普通标定表格。
    if "阶段b" in title and "诊断" in text:
        score += 6
    # 阶段 A 回归和应变曲线则优先靠近标定方法/结果章节。
    if ("阶段a" in title or "回归" in title) and "标定" in text:
        score += 5
    if "应变标定" in title and "评级" in text:
        score += 5
    # 概览/框架页可以保留弱候选，但不应吸走更具体的异常或标定证据页。
    if any(token in text for token in _GENERIC_OVERVIEW_TOKENS):
        score -= 8
    return score


def best_matching_section_index(
    section_texts: Sequence[object],
    figure: dict[str, Any],
) -> int | None:
    """返回最佳章节索引；完全无匹配时返回 None。"""
    if not section_texts:
        return None
    scores = [
        figure_relevance_score(figure, section_text)
        for section_text in section_texts
    ]
    best_score = max(scores)
    if best_score <= 0:
        return None
    return scores.index(best_score)


def plan_figures_for_sections(
    sections: Sequence[dict[str, Any]],
    catalog: Sequence[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    """每张图只分配给一个最相关章节，避免 LLM 跨页重复选图。"""
    plan: dict[int, list[dict[str, Any]]] = {
        index: [] for index in range(len(sections))
    }
    section_texts = [
        " ".join([
            str(section.get("heading") or ""),
            *[str(point) for point in (section.get("key_points") or [])],
        ])
        for section in sections
    ]
    for figure in catalog:
        section_index = best_matching_section_index(section_texts, figure)
        if section_index is not None:
            plan[section_index].append(dict(figure))
    return plan


def format_figures_for_prompt(
    figures: Sequence[dict[str, Any]],
    *,
    include_filename: bool,
) -> str:
    """把本节候选图格式化为短、精确且不可编造的 prompt 目录。"""
    if not figures:
        return "（本节无候选图表）"
    lines: list[str] = []
    for figure in figures:
        line = f"- 图{figure.get('fig_no')}: {figure.get('title')}"
        key_stat = str(figure.get("key_stat") or "")
        if key_stat:
            line += f"；关键统计={key_stat}"
        if include_filename:
            line += f"；锚点=[INSERT_IMAGE: {figure.get('filename')}]"
        lines.append(line)
    return "\n".join(lines)
