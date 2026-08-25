"""报告生成前先做图表语义规划，模型只在正确章节选图。"""

from __future__ import annotations

import json


def _figure(
    fig_no: int,
    module: str,
    title: str,
    filename: str,
) -> dict:
    return {
        "fig_id": filename.removesuffix(".png"),
        "fig_no": fig_no,
        "module": module,
        "title": title,
        "key_stat": "",
        "filename": filename,
        "png_path": filename,
    }


def test_semantic_plan_places_figures_in_relevant_sections() -> None:
    from core.report_figure_planner import plan_figures_for_sections

    sections = [
        {"heading": "数据总览与波长特征", "key_points": []},
        {"heading": "传感器标定评级", "key_points": []},
        {"heading": "物理诊断与失效机理", "key_points": []},
        {"heading": "多源应变对比与相关性", "key_points": []},
    ]
    catalog = [
        _figure(1, "data_analysis", "波长差时程", "ts_dlambda.png"),
        _figure(2, "temperature_calib", "A1 阶段 B 诊断四联图", "phaseb_A1.png"),
        _figure(3, "strain_calib", "A1 应变标定曲线", "strain_A1.png"),
        _figure(4, "compare", "多源对比时程", "compare.png"),
    ]

    plan = plan_figures_for_sections(sections, catalog)

    assert [figure["fig_no"] for figure in plan[0]] == [1]
    assert [figure["fig_no"] for figure in plan[1]] == [3]
    assert [figure["fig_no"] for figure in plan[2]] == [2]
    assert [figure["fig_no"] for figure in plan[3]] == [4]


def test_temperature_phase_b_prefers_failure_result_over_generic_framework() -> None:
    """通用“标定-诊断框架”不能吸走明确支撑迟滞/FAIL 结论的阶段 B 图。"""
    from core.report_figure_planner import plan_figures_for_sections

    sections = [
        {
            "heading": "三级评估框架：从标定到诊断，锁定长期适用性",
            "key_points": [
                "覆盖标定、实测、诊断与多源一致性",
                "核心任务：评估大温差双波长解耦性能",
                "关键挑战：B1/B2标定FAIL",
            ],
        },
        {
            "heading": "六组双光栅的封装差异决定性能边界",
            "key_points": [
                "比较 A/B/C 三组封装工艺",
                "B组存在胶水滑移，迟滞风险高",
            ],
        },
        {
            "heading": "标定致命缺陷：B1/B2迟滞超15%FS，已触碰废品拦截线",
            "key_points": ["B1/B2 评级 FAIL，严禁定量使用"],
        },
    ]
    figure = _figure(
        8,
        "temperature_calib",
        "B1 阶段 B 原始（补偿前）诊断四联图",
        "phaseb_B1_raw.png",
    )

    plan = plan_figures_for_sections(sections, [figure])

    assert plan[0] == []
    assert plan[2] == [figure]


def test_compare_figure_prefers_anomaly_evidence_over_generic_framework() -> None:
    """“多源一致性”框架页不能吸走明确支撑“不相关”异常的对比图。"""
    from core.report_figure_planner import plan_figures_for_sections

    sections = [
        {
            "heading": "三级评估框架：从标定到诊断，锁定长期适用性",
            "key_points": ["覆盖多源一致性验证"],
        },
        {
            "heading": "应变-光纤1剧烈波动且独立于结构真实应变",
            "key_points": ["与其余三路几乎不相关，需排查映射与物理测点"],
        },
        {
            "heading": "立即执行三项排查，锁定异常根因",
            "key_points": ["补充全时程曲线与四路同窗对比"],
        },
    ]
    figures = [
        _figure(
            2,
            "compare",
            "应变-光纤1↔应变片1 相关散点",
            "corr_strain_1.png",
        ),
        _figure(3, "compare", "多源叠加时程", "compare_ol.png"),
    ]

    plan = plan_figures_for_sections(sections, figures)

    assert plan[0] == []
    assert plan[1] == figures
    assert plan[2] == []


def test_strain_calibration_prefers_failure_result_over_final_summary() -> None:
    from core.report_figure_planner import plan_figures_for_sections

    sections = [
        {
            "heading": "标定致命缺陷：B1/B2迟滞超15%FS，已触碰废品拦截线",
            "key_points": ["严禁用于定量测量，仅A1与C2通过标定"],
        },
        {
            "heading": "综合评级fair：数据完整但传感器可靠性不足",
            "key_points": [
                "B1/B2迟滞>15%FS",
                "应变-光纤1波动剧烈，需整改后复验",
            ],
        },
    ]
    figure = _figure(
        18,
        "strain_calib",
        "A1 应变标定曲线",
        "calib_linearity_A1.png",
    )

    plan = plan_figures_for_sections(sections, [figure])

    assert plan[0] == [figure]
    assert plan[1] == []


def test_ppt_model_receives_single_candidate_and_null_gets_silent_fallback() -> None:
    from core.report_engine import _generate_ppt_report

    prompts: list[str] = []

    def generate(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps(
            {
                "slide_title": "多源一致性不足，结论需谨慎",
                "bullet_points": ["相关性偏低", "需补充测点映射"],
                "speaker_notes": "对比图直接支撑当前结论。",
                "image_anchor": None,
            },
            ensure_ascii=False,
        )

    figure = _figure(4, "compare", "多源对比时程", "compare_ol.png")
    result = _generate_ppt_report(
        "测试汇报",
        [{"heading": "多源对比", "key_points": ["检查相关性"]}],
        "项目上下文",
        generate,
        figure_plan={0: [figure]},
    )

    assert "[INSERT_IMAGE: compare_ol.png]" in prompts[0]
    assert result["slides"][0]["image_anchor"] == (
        "[INSERT_IMAGE: compare_ol.png]"
    )
    assert result.get("_report_warnings", []) == []


def test_ppt_null_anchor_with_multiple_candidates_still_warns() -> None:
    from core.report_engine import _generate_ppt_report

    def generate(_prompt: str) -> str:
        return json.dumps(
            {
                "slide_title": "多源一致性不足，结论需谨慎",
                "bullet_points": ["相关性偏低"],
                "speaker_notes": "候选图不止一张。",
                "image_anchor": None,
            },
            ensure_ascii=False,
        )

    figures = [
        _figure(4, "compare", "多源对比时程", "compare_ol.png"),
        _figure(5, "compare", "多源相关散点", "compare_scatter.png"),
    ]
    result = _generate_ppt_report(
        "测试汇报",
        [{"heading": "多源对比", "key_points": ["检查相关性"]}],
        "项目上下文",
        generate,
        figure_plan={0: figures},
    )

    assert result["slides"][0]["image_anchor"] == (
        "[INSERT_IMAGE: compare_ol.png]"
    )
    assert any(
        "未返回有效候选图表锚点" in warning
        for warning in result["_report_warnings"]
    )


def test_ppt_slide_retries_once_when_model_returns_reasoning_only() -> None:
    """单页偶发只有 reasoning、content 为空时，不应让整份 PPT 立即失败。"""
    from core.ai_errors import AIClientEmptyResponseError
    from core.report_engine import _generate_ppt_report

    calls = 0

    def generate(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AIClientEmptyResponseError(
                "AI 返回空 content（finish_reason=stop, reasoning_len=1493）"
            )
        return json.dumps(
            {
                "slide_title": "补偿前后结果形成闭环",
                "bullet_points": ["补偿后残差显著下降"],
                "speaker_notes": "该页为自动重试后的有效结构化结果。",
                "image_anchor": None,
            },
            ensure_ascii=False,
        )

    result = _generate_ppt_report(
        "测试汇报",
        [{"heading": "温度补偿结论", "key_points": []}],
        "项目上下文",
        generate,
    )

    assert calls == 2
    assert result["slides"][0]["slide_title"] == "补偿前后结果形成闭环"


def test_ppt_slide_second_empty_response_still_fails_loudly() -> None:
    """重试耗尽后必须把类型化异常传到 ReportWorker/UI，禁止伪造幻灯片。"""
    import pytest

    from core.ai_errors import AIClientEmptyResponseError
    from core.report_engine import _generate_ppt_report

    calls = 0

    def generate(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        raise AIClientEmptyResponseError("reasoning-only response")

    with pytest.raises(AIClientEmptyResponseError, match="reasoning-only"):
        _generate_ppt_report(
            "测试汇报",
            [{"heading": "温度补偿结论", "key_points": []}],
            "项目上下文",
            generate,
        )

    assert calls == 2


def test_word_model_is_told_to_cite_each_planned_figure_near_claim() -> None:
    from core.report_engine import _generate_word_markdown_report

    prompts: list[str] = []

    def generate(prompt: str) -> str:
        prompts.append(prompt)
        return "阶段 B 结果反映补偿残差差异。"

    figures = [
        _figure(2, "temperature_calib", "A1 阶段 B 诊断四联图", "A1.png"),
        _figure(3, "temperature_calib", "A2 阶段 B 诊断四联图", "A2.png"),
    ]
    _generate_word_markdown_report(
        "测试报告",
        [{"heading": "物理诊断", "key_points": []}],
        "项目上下文",
        generate,
        charts_context="图2; 图3",
        figure_plan={0: figures},
    )

    assert "本节必须使用 图2、图3" in prompts[0]
    assert "不要把全部图引用集中到段尾" in prompts[0]
