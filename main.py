# DataProcessor Pro - Main Application
# PyQt6-based offline data analysis software

import sys
import os
import json
import re
import tempfile
import uuid
import traceback
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QLabel, QFileDialog,
    QMessageBox, QTabWidget, QMenuBar, QMenu, QToolBar, QStatusBar,
    QDialog, QListWidget, QListWidgetItem, QAbstractItemView, QLineEdit,
    QComboBox, QGroupBox, QFormLayout, QCheckBox, QSpinBox, QDoubleSpinBox,
    QTextEdit, QSplitter, QGridLayout, QStackedWidget, QProgressBar, QFrame,
    QInputDialog, QTreeWidget, QTreeWidgetItem
)
from PyQt6.QtCore import Qt, QThread, QTimer, QSettings, pyqtSignal
from PyQt6.QtGui import QAction, QIcon

import pandas as pd
import numpy as np
from docx import Document
from docx.shared import RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

# Matplotlib for charts
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.backends.backend_qt import NavigationToolbar2QT
import matplotlib.pyplot as plt

# 设置 matplotlib 默认中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

# ============ Utility Module Imports ============
from utils.file_parser import parse_file, detect_format, detect_header, try_read_csv
from utils.data_cleaning import clean_data, detect_anomalies, fill_missing

# ============ Backend Module Imports ============
from dp_engine.wiki_system import WikiFileSystem
from dp_engine.multi_agent import run_multi_agent, MultiAgentState
from dp_engine.report_provider import (
    BUILTIN_PROVIDER_ID,
    BUILTIN_PROVIDER_VERSION,
    PPT_MASTER_PROVIDER_ID,
    PPT_MASTER_PROVIDER_VERSION,
    PptMasterReportRenderProvider,
    ReportProviderController,
    ReportProviderOption,
    ReportRenderAsset,
    ReportRenderOrchestrator,
    ReportRenderRequest,
)
from dp_engine.ppt_master_host import (
    ControlledToolRunner,
    HostAIClientPlanningAdapter,
    HostAIPptMasterAuthoringAdapter,
    PlanningAsset,
    PlanningAssetKind,
    PlanningRequest,
    PptMasterPlanningWorkflow,
    PptMasterWorkflowError,
    TemplateMode,
    fingerprint_ppt_master_inputs,
    prepare_ppt_master_template_workspace,
    probe_expected_ppt_master_installation,
)

# ============ UI Module Imports ============
from ui.report_workbench import ReportWorkbenchWidget
from ui.ai_diagnosis import AiDiagnosisWidget
from ui.analysis_tab import AnalysisTabWidget
from ui.global_parameter_dialog import GlobalParameterDialog
from ui.data_tab import DataTabWidget
from ui.cleaning_tab import CleaningTabWidget
from ui.sensor_tab import SensorTabWidget
from ui.project_tab import ProjectManagerWidget
from ui.wiki_tab import WikiTabWidget
from ui.clipper_tab import WebClipperWidget
from ui.fbg_edit_dialog import FBGEditDialog
from ui.sensor_edit_dialog import SensorEditDialog
from ui.ai_model_config_dialog import AIModelConfigDialog
from ui.report_worker import ReportWorker
from ui.skill_tab import AgentSkillWidget
from ui.skill_install_controller import SkillInstallTaskOwner
from ui.compare_tab import CompareTabWidget
from ui.calibration_tab import CalibrationTabWidget

# ============ Report Generation ============
from core.report_engine import generate_outline, generate_structured_report


_REPORT_LOGGER = logging.getLogger(__name__)

# ============ Report Bridge (Batch 3.3.2) ============
from dp_engine.report_bridge.coordinator import ArtifactOperationCoordinator
from dp_engine.report_bridge.models import (
    ReportBridgeStatus,
    ReportBridgePublicResult,
    ReportAssetSummary,
    SAFE_ERROR_MESSAGES,
)

# ============ Core Data Models (SSOT) ============
from core.models import (
    DataTemplate, CleaningRule, FBG, GlobalParameter, Sensor, SensorSystem,
    DEFAULT_TEMPLATES,
)

# ═══════════════════════════════════════════════
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============ App State (SSOT) ============
from state.app_state import AppState

# ============ Main Window ============

def validate_docx_template(tmpl_path: str) -> str | None:
    """向后兼容入口：委托统一模板校验模块。"""
    from utils.report_template_validation import validate_word_template

    return validate_word_template(tmpl_path)


def validate_pptx_template(tmpl_path: str) -> str | None:
    """校验 PPTX 模板类型、包结构和演示页面尺寸。"""
    from utils.report_template_validation import validate_ppt_template

    return validate_ppt_template(tmpl_path)


# ═══════════════════════════════════════════════════════════════════════
# 项目根反推 — 从关联文件路径反推项目根 (用项目资料库做锚)
# ═══════════════════════════════════════════════════════════════════════

_KNOWN_PROJECT_SUBDIRS = {'数据', '方案', '图纸', '图片', '视频', '其它', '报告'}


def _resolve_project_root_from_files(
    file_paths: list[str],
    library_root: str | None = None,
) -> str:
    """从关联文件路径反推统一项目根目录 (用项目资料库做锚)。

    项目根 = 项目资料库/ 的直接子目录。
    对每个文件: abspath → realpath → 核对是否在 library_root 下
    → 取 relpath 第一段为项目名 → 比较所有文件的项目名是否一致。

    方案 B — 有明确锚点，不依赖「非白名单即项目根」的排除法，
    在自建子目录/文件不在库下/路径含白名单词 等异常路径下明确报错，不静默假根。

    Args:
        file_paths: 非空文件路径列表
        library_root: 项目资料库根路径。缺省 = 软件根/项目资料库/

    Returns:
        统一项目根目录绝对路径 (library_root/项目名)

    Raises:
        ValueError: 文件列表为空 / 文件不属于任何项目 / 跨多个项目
    """
    if not file_paths:
        raise ValueError("请先添加项目关联资料")

    # ── 库根锚点 ──
    if library_root is None:
        library_root = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "项目资料库")
    lib_root = os.path.realpath(library_root)

    project_names: set[str] = set()
    for f in file_paths:
        abs_path = os.path.realpath(os.path.abspath(f))

        # 前缀判断: 文件必须在项目资料库下
        prefix = lib_root + os.sep
        if not abs_path.startswith(prefix):
            raise ValueError("文件不属于任何项目，请添加项目库内的关联资料")

        # 取项目资料库的直接子目录作为项目名
        rel = os.path.relpath(abs_path, lib_root)
        project_name = rel.split(os.sep)[0]

        # 双保险: relpath 不应以 .. 开头（前缀已挡，但兜底）
        if project_name.startswith(".."):
            raise ValueError("文件不属于任何项目，请添加项目库内的关联资料")

        project_names.add(project_name)

    if len(project_names) > 1:
        raise ValueError("关联资料跨多个项目，请统一")

    return os.path.join(lib_root, project_names.pop())


# ── Atomic report transaction helpers (Batch 3.3.1B) ──


def _check_cancel(worker) -> None:
    """Raise RuntimeError if the worker has been cancelled."""
    if getattr(worker, '_cancelled', False):
        raise RuntimeError("报告生成已取消")


def _cleanup_temp(temp_path: str) -> None:
    """Best-effort delete of a temp file; never raises."""
    try:
        if temp_path and os.path.isfile(temp_path):
            os.unlink(temp_path)
    except OSError:
        pass


def _validate_temp_output(temp_path: str, report_type: str) -> None:
    """Validate that a temp report file is non-empty and can be re-opened.

    Raises RuntimeError on validation failure.
    """
    if not os.path.isfile(temp_path):
        raise RuntimeError("报告临时文件不存在")
    if os.path.getsize(temp_path) == 0:
        raise RuntimeError("报告临时文件为空")
    if report_type == 'ppt':
        try:
            from pptx import Presentation
            Presentation(temp_path)
        except Exception:
            raise RuntimeError("PPT 临时文件验证失败：无法打开")
    else:
        try:
            from docx import Document
            Document(temp_path)
        except Exception:
            raise RuntimeError("Word 临时文件验证失败：无法打开")


def _fsync_path(path: str) -> None:
    """fsync a file to durable storage.

    Opens the file in binary read/write mode, flushes, and fsyncs.
    Raises RuntimeError on failure.
    """
    try:
        with open(path, 'r+b') as f:
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        raise RuntimeError("报告临时文件持久化失败") from e


def _inject_figures_by_reference(
    docx_path: str, manifest, warnings: list[str],
) -> None:
    """扫描 docx 正文中的「图N」引用，注入对应图片+图题到首次引用段落之后。
    未被引用的图优先按语义归入相关章节，无法匹配时才追加到图表附录。
    引用不存在的图N → 警告。
    """
    import re as _re
    try:
        doc = Document(docx_path)
    except Exception as e:
        warnings.append(f"图注入失败(无法打开docx): {e}")
        return

    # 收集所有段落文本和索引
    para_data = [(i, p.text) for i, p in enumerate(doc.paragraphs)]

    # 建立图N→fig 映射
    fig_map: dict[int, object] = {}
    for rf in manifest:
        fig_map[rf.fig_no] = rf

    # 扫描正文中的「图N」引用
    cited: set[int] = set()
    fig_to_para: dict[int, int] = {}  # fig_no → 首次引用段落索引
    pattern = _re.compile(r'图(\d+)')
    for idx, text in para_data:
        found = {int(n) for n in pattern.findall(text)}
        for fn in found:
            if fn not in cited and fn in fig_map:
                cited.add(fn)
                fig_to_para[fn] = idx
            elif fn not in fig_map:
                if fn not in cited:  # 只报一次
                    cited.add(fn)  # mark as seen
                    warnings.append(f"图引用不存在: 正文引用了「图{fn}」，但该图号未生成")

    # 在图首次引用段落后注入 (从后往前插入以保持索引)
    for fig_no in sorted(fig_to_para.keys(), reverse=True):
        rf = fig_map[fig_no]
        insert_idx = fig_to_para[fig_no]
        # 在 insert_idx 段落后插入图片 + 图题
        ref_para = doc.paragraphs[insert_idx]
        try:
            # 添加图片
            img_para = doc.add_paragraph()
            run = img_para.add_run()
            run.add_picture(rf.png_path, width=Inches(5.0))
            img_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            # 移动到引用段落之后
            ref_para._element.addnext(img_para._element)
            # 图题
            cap_para = doc.add_paragraph(rf.caption)
            cap_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            img_para._element.addnext(cap_para._element)
        except Exception as e:
            warnings.append(f"图{fig_no} 插入失败: {e}")

    # 未被引用的图 → 按所属模块语义归位到最相关章节。
    unreferenced = [fn for fn in fig_map if fn not in fig_to_para]
    if unreferenced:
        from core.report_figure_planner import best_matching_section_index

        chapter_blocks: list[tuple[object, str]] = []
        heading_indexes: list[int] = []
        for index, paragraph in enumerate(doc.paragraphs):
            style_id = str(getattr(getattr(paragraph, "style", None), "style_id", ""))
            if style_id == "Heading1":
                heading_indexes.append(index)
        for position, heading_index in enumerate(heading_indexes):
            next_index = (
                heading_indexes[position + 1]
                if position + 1 < len(heading_indexes)
                else len(doc.paragraphs)
            )
            block_paragraphs = doc.paragraphs[heading_index:next_index]
            block_text = " ".join(
                paragraph.text for paragraph in block_paragraphs if paragraph.text
            )
            anchor = (
                block_paragraphs[-1]
                if block_paragraphs
                else doc.paragraphs[heading_index]
            )
            chapter_blocks.append((anchor, block_text))

        matched: dict[int, list[int]] = {}
        still_unmatched: list[int] = []
        section_texts = [block_text for _, block_text in chapter_blocks]
        for fig_no in sorted(unreferenced):
            figure = fig_map[fig_no]
            planned = {
                "module": str(getattr(figure, "section", "") or ""),
                "title": str(getattr(figure, "title", "") or ""),
            }
            target_index = best_matching_section_index(section_texts, planned)
            if target_index is None:
                still_unmatched.append(fig_no)
            else:
                matched.setdefault(target_index, []).append(fig_no)

        for target_index in sorted(matched, reverse=True):
            anchor: Any = chapter_blocks[target_index][0]._element  # type: ignore[reportAttributeAccessIssue]
            for fig_no in matched[target_index]:
                figure: Any = fig_map[fig_no]  # type: ignore[reportAttributeAccessIssue]
                try:
                    img_para = doc.add_paragraph()
                    img_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    img_para.add_run().add_picture(
                        figure.png_path, width=Inches(5.0)
                    )
                    cap_para = doc.add_paragraph(figure.caption)
                    cap_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    anchor.addnext(img_para._element)
                    img_para._element.addnext(cap_para._element)
                    anchor = cap_para._element
                except Exception as e:
                    warnings.append(
                        f"图{fig_no}({figure.title}) 章节归位插入失败: {e}"
                    )

        if matched:
            matched_count = sum(len(figures) for figures in matched.values())
            warnings.append(
                f"{matched_count}张图未被模型正文显式引用，已按所属章节自动归位。"
            )

        if still_unmatched:
            doc.add_heading('图表附录', level=1)
            for fig_no in still_unmatched:
                figure: Any = fig_map[fig_no]  # type: ignore[reportAttributeAccessIssue]
                try:
                    img_para = doc.add_paragraph()
                    img_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    img_para.add_run().add_picture(
                        figure.png_path, width=Inches(5.0)
                    )
                    cap_para = doc.add_paragraph(figure.caption)
                    cap_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                except Exception as e:
                    warnings.append(
                        f"图{fig_no}({figure.title}) 附录插入失败: {e}"
                    )
            warnings.append(
                f"图表附录: {len(still_unmatched)}张图无法匹配正文章节，已追加到末尾 "
                f"({' '.join(f'图{fn}' for fn in still_unmatched)})"
            )

    try:
        doc.save(docx_path)
    except Exception as e:
        warnings.append(f"图注入后保存失败: {e}")


def _audit_chart_manifest(
    chart_manifest: list[Any],
    charts_dir: str,
) -> list[str]:
    """把“诊断记录有图条目但实际不可用于报告”的情况显式反馈给用户。"""
    warnings: list[str] = []
    missing_files: list[str] = []
    skipped_phase_b: list[str] = []
    for entry in chart_manifest:
        if bool(getattr(entry, "produced", False)):
            rel_path = str(getattr(entry, "rel_path", "") or "")
            full_path = os.path.join(charts_dir, rel_path) if rel_path else ""
            if not rel_path or not os.path.isfile(full_path):
                missing_files.append(
                    str(getattr(entry, "chart_id", "") or getattr(entry, "title", "未知图"))
                )
            continue
        if str(getattr(entry, "module", "")) == "phaseb":
            title = str(getattr(entry, "title", "") or getattr(entry, "chart_id", "迟滞图"))
            reason = str(getattr(entry, "skip_reason", "") or "无有效绘图数据")
            skipped_phase_b.append(f"{title}（{reason}）")

    if missing_files:
        warnings.append(
            "诊断图文件缺失: "
            + "、".join(missing_files)
            + "。报告未插入这些图片，请重新保存诊断记录。"
        )
    if skipped_phase_b:
        warnings.append(
            f"诊断记录中的 {len(skipped_phase_b)} 张阶段B迟滞图没有有效数据，"
            "报告不会虚构图片；请重新运行阶段B并保存新的诊断记录。详情: "
            + "；".join(skipped_phase_b)
        )
    return warnings


def _inject_figures_to_pptx(
    pptx_path: str,
    manifest,
    warnings: list[str],
    assigned_filenames: set[str] | None = None,
    template_used: bool = False,
) -> None:
    """把模型未选中的图表编排为章节内证据页，而不是末尾附件堆叠。"""
    if manifest is None or manifest.count == 0:
        return

    try:
        from pptx import Presentation
        prs = Presentation(pptx_path)
    except Exception as e:
        warnings.append(f"PPT图表注入失败(无法打开pptx): {e}")
        return

    from collections import defaultdict
    from pathlib import Path as _Path

    from pptx.dml.color import RGBColor as _RGBColor
    from pptx.enum.shapes import MSO_SHAPE as _MSO_SHAPE
    from pptx.util import Emu as _Emu, Pt as _Pt
    from pptx.enum.text import PP_ALIGN as _PP_ALIGN
    from pptx.enum.shapes import PP_PLACEHOLDER as _PP_PLACEHOLDER
    from core.report_figure_planner import (
        build_figure_catalog,
        figure_relevance_score,
    )

    layouts = list(prs.slide_layouts)
    if not layouts:
        warnings.append("PPT图表注入失败: 演示文稿不含可用版式")
        return

    def _layout_score(layout) -> tuple[int, int]:
        name = str(getattr(layout, "name", "")).strip().lower()
        blank_rank = 0 if name in {"blank", "空白", "blanc"} else 1
        return blank_rank, len(layout.placeholders)

    evidence_layout = min(layouts, key=_layout_score)
    slide_w = int(prs.slide_width or 9144000)
    slide_h = int(prs.slide_height or 5143500)
    assigned = {
        _Path(filename).name
        for filename in (assigned_filenames or set())
        if filename
    }
    figures = [
        figure
        for figure in manifest
        if _Path(str(figure.png_path)).name not in assigned
    ]
    if not figures:
        return

    original_slides = list(prs.slides)
    slide_titles: list[str] = []
    section_texts: list[str] = []
    for slide in original_slides:
        title_shape = slide.shapes.title
        slide_titles.append(
            title_shape.text.strip()
            if title_shape is not None and title_shape.has_text_frame
            else ""
        )
        texts = [
            getattr(shape, "text", "").strip()
            for shape in slide.shapes
            if getattr(shape, "has_text_frame", False) and getattr(shape, "text", "").strip()
        ]
        section_texts.append(" ".join(texts))

    def _evidence_group_key(figure) -> str:
        module = str(getattr(figure, "section", "") or "other")
        title = str(getattr(figure, "title", "") or "").lower()
        fig_id = str(getattr(figure, "fig_id", "") or "").lower()
        if module == "temperature_calib":
            if "阶段 a" in title or "回归" in title or "tempa" in fig_id:
                return "temperature_calib_a"
            sensor_name = str(getattr(figure, "title", "") or "").split(
                "阶段 B",
                1,
            )[0].strip()
            return f"temperature_calib_b::{sensor_name or '未命名传感器'}"
        if module == "compare":
            if "scatter" in fig_id or "散点" in title:
                return "compare_scatter"
            return "compare_timeseries"
        return module

    grouped: dict[str, list[object]] = defaultdict(list)
    for figure in figures:
        grouped[_evidence_group_key(figure)].append(figure)

    group_titles = {
        "data_analysis": "数据分析｜时程证据",
        "data_cleaning": "数据清洗｜质量证据",
        "compare_timeseries": "多源对比｜时程证据",
        "compare_scatter": "多源对比｜相关性证据",
        "temperature_calib_a": "温度标定｜阶段 A 回归证据",
        "temperature_calib_b": "温度标定｜阶段 B 诊断证据",
        "strain_calib": "应变标定｜曲线证据",
    }

    def _best_target(figure: dict[str, object]) -> int | None:
        # 第 1 页是报告封面；其标题常含“应变/诊断”等宽泛词，不能因此
        # 把证据页插到正文论点之前。正文用“全文分 + 标题分”显式加权：
        # 保留标题优先级，同时允许正文中的明确异常/评级证据胜过泛化标题。
        content_start = 1 if len(original_slides) > 1 else 0
        scores = [
            figure_relevance_score(figure, section_texts[index])
            + figure_relevance_score(figure, slide_titles[index])
            for index in range(content_start, len(original_slides))
        ]
        if not scores or max(scores) <= 0:
            return None
        return content_start + scores.index(max(scores))

    def _evidence_group_priority(group_key: str) -> int:
        """同一正文页后的证据顺序：清洗/分析→对比→温标A/B→应变标定。"""
        fixed = {
            "data_cleaning": 10,
            "data_analysis": 20,
            "compare_timeseries": 30,
            "compare_scatter": 31,
            "temperature_calib_a": 40,
            "strain_calib": 60,
        }
        if group_key in fixed:
            return fixed[group_key]
        if group_key.startswith("temperature_calib_b::"):
            sensor_name = group_key.split("::", 1)[1].upper()
            sensor_order = {
                "A1": 0,
                "A2": 1,
                "B1": 2,
                "B2": 3,
                "C1": 4,
                "C2": 5,
            }
            return 50 + sensor_order.get(sensor_name, 9)
        return 90

    insertion_plans: list[tuple[int, int, str, list[object]]] = []
    for module, module_figures in grouped.items():
        catalog = build_figure_catalog(module_figures)
        target_candidates = [_best_target(figure) for figure in catalog]
        target_index = next(
            (index for index in target_candidates if index is not None),
            max(0, len(original_slides) - 1),
        )
        group_title = group_titles.get(module)
        if group_title is None and module.startswith("temperature_calib_b::"):
            sensor_name = module.split("::", 1)[1]
            group_title = f"温度标定｜{sensor_name} 阶段 B 补偿前后对比"
        insertion_plans.append((
            target_index,
            _evidence_group_priority(module),
            group_title or "分析证据",
            module_figures,
        ))

    def _remove_content_placeholders(slide) -> None:
        removable_types = {
            _PP_PLACEHOLDER.TITLE,
            _PP_PLACEHOLDER.CENTER_TITLE,
            _PP_PLACEHOLDER.VERTICAL_TITLE,
            _PP_PLACEHOLDER.SUBTITLE,
            _PP_PLACEHOLDER.BODY,
            _PP_PLACEHOLDER.OBJECT,
            _PP_PLACEHOLDER.VERTICAL_BODY,
            _PP_PLACEHOLDER.VERTICAL_OBJECT,
            _PP_PLACEHOLDER.PICTURE,
            _PP_PLACEHOLDER.BITMAP,
        }
        for placeholder in list(slide.placeholders):
            if placeholder.placeholder_format.type in removable_types:
                placeholder._element.getparent().remove(placeholder._element)

    def _move_last_slide_to(index: int) -> None:
        slide_ids = prs.slides._sldIdLst
        new_slide_id = slide_ids[-1]
        slide_ids.remove(new_slide_id)
        slide_ids.insert(index, new_slide_id)

    def _add_evidence_slide(
        title: str,
        chunk: list[object],
        chunk_index: int,
        chunk_total: int,
    ) -> None:
        slide = prs.slides.add_slide(evidence_layout)
        _remove_content_placeholders(slide)
        if not template_used:
            background = slide.background.fill
            background.solid()
            background.fore_color.rgb = _RGBColor(0xF7, 0xF9, 0xFC)
            accent = slide.shapes.add_shape(
                _MSO_SHAPE.RECTANGLE,
                _Emu(0),
                _Emu(0),
                _Emu(slide_w),
                _Emu(int(slide_h * 0.025)),
            )
            accent.fill.solid()
            accent.fill.fore_color.rgb = _RGBColor(0x1D, 0xA7, 0xA1)
            accent.line.fill.background()

        display_title = (
            f"{title}（{chunk_index}/{chunk_total}）"
            if chunk_total > 1
            else title
        )
        title_box = slide.shapes.add_textbox(
            _Emu(int(slide_w * 0.06)),
            _Emu(int(slide_h * 0.06)),
            _Emu(int(slide_w * 0.88)),
            _Emu(int(slide_h * 0.11)),
        )
        title_p = title_box.text_frame.paragraphs[0]
        title_p.text = display_title
        title_p.font.size = _Pt(30)
        title_p.font.bold = True
        if not template_used:
            title_p.font.color.rgb = _RGBColor(0x0B, 0x1F, 0x33)

        count = len(chunk)
        if count == 1:
            slots = [(0.08, 0.20, 0.84, 0.62)]
        else:
            slots = [
                (0.055, 0.22, 0.43, 0.56),
                (0.515, 0.22, 0.43, 0.56),
            ]
        for figure, (left_f, top_f, width_f, height_f) in zip(chunk, slots):
            fig: Any = figure  # type: ignore[reportAttributeAccessIssue]
            try:
                max_width = int(slide_w * width_f)
                max_height = int(slide_h * height_f)
                from PIL import Image as _Image

                with _Image.open(fig.png_path) as image:
                    image_width, image_height = image.size
                scale = min(max_width / image_width, max_height / image_height)
                width = int(image_width * scale)
                height = int(image_height * scale)
                left = int(slide_w * left_f) + max(0, (max_width - width) // 2)
                top = int(slide_h * top_f) + max(0, (max_height - height) // 2)
                slide.shapes.add_picture(
                    fig.png_path,
                    _Emu(left),
                    _Emu(top),
                    width=_Emu(width),
                    height=_Emu(height),
                )
                caption_box = slide.shapes.add_textbox(
                    _Emu(int(slide_w * left_f)),
                    _Emu(int(slide_h * 0.82)),
                    _Emu(max_width),
                    _Emu(int(slide_h * 0.08)),
                )
                caption_p = caption_box.text_frame.paragraphs[0]
                caption_p.text = fig.caption
                caption_p.font.size = _Pt(14)
                caption_p.alignment = _PP_ALIGN.CENTER
                if not template_used:
                    caption_p.font.color.rgb = _RGBColor(0x42, 0x52, 0x66)
            except Exception as e:
                warnings.append(
                    f"图{fig.fig_no}({fig.title}) PPT插入失败: {e}"
                )

    # 从后向前插入，保证原内容页索引在处理过程中保持有效。
    for target_index, _priority, group_title, module_figures in sorted(
        insertion_plans,
        key=lambda item: (item[0], item[1]),
        reverse=True,
    ):
        chunks = [
            module_figures[index:index + 2]
            for index in range(0, len(module_figures), 2)
        ]
        for reverse_index in range(len(chunks) - 1, -1, -1):
            _add_evidence_slide(
                group_title,
                chunks[reverse_index],
                reverse_index + 1,
                len(chunks),
            )
            _move_last_slide_to(target_index + 1)

    try:
        prs.save(pptx_path)
    except Exception as e:
        warnings.append(f"PPT图表注入后保存失败: {e}")


def _build_report_render_assets(
    manifest,
    *,
    bridge_workspace=None,
    bridge_assets=(),
) -> tuple[tuple[ReportRenderAsset, ...], tuple[str, ...]]:
    """Build the path-minimal semantic image snapshot for one Provider call."""
    assets: list[ReportRenderAsset] = []
    required_ids: list[str] = []

    if manifest is not None:
        for figure in manifest:
            source = Path(str(getattr(figure, 'png_path', '') or ''))
            if not source.is_absolute():
                raise RuntimeError('报告图表路径必须为绝对路径。')
            suffix = source.suffix.lower()
            if suffix not in {'.png', '.jpg', '.jpeg'}:
                raise RuntimeError('报告图表必须为 PNG 或 JPEG。')
            fig_id = str(getattr(figure, 'fig_id', '') or '').strip()
            title = str(getattr(figure, 'title', '') or '').strip()
            key_stat = str(getattr(figure, 'key_stat', '') or '').strip()
            section = str(getattr(figure, 'section', '') or '').strip()
            semantic_label = title or fig_id
            if key_stat:
                semantic_label = f'{semantic_label}；{key_stat}'
            assets.append(ReportRenderAsset(
                host_id=fig_id,
                source_path=source.resolve(strict=False),
                media_type=(
                    'image/png' if suffix == '.png' else 'image/jpeg'
                ),
                semantic_label=semantic_label,
                target=section or '报告正文',
            ))
            required_ids.append(fig_id)

    if bridge_workspace is not None:
        workspace = Path(bridge_workspace).resolve(strict=False)
        for asset in bridge_assets:
            role = getattr(getattr(asset, 'role', None), 'value', '')
            if role != 'image':
                continue
            order = int(getattr(asset, 'order', 0))
            filename = str(getattr(asset, 'managed_filename', '') or '')
            source = (workspace / filename).resolve(strict=False)
            suffix = source.suffix.lower()
            if suffix not in {'.png', '.jpg', '.jpeg'}:
                continue
            host_id = f'bridge_{order:03d}'
            authoritative = getattr(asset, 'authoritative_artifact', None)
            display_name = str(
                getattr(authoritative, 'display_name', '') or '技能图片素材'
            ).strip()
            assets.append(ReportRenderAsset(
                host_id=host_id,
                source_path=source,
                media_type=(
                    'image/png' if suffix == '.png' else 'image/jpeg'
                ),
                semantic_label=display_name,
                target='技能输出素材',
            ))
            required_ids.append(host_id)

    return tuple(assets), tuple(required_ids)


def _execute_report_build_transaction(
    config: dict,
    outline: str,
    report_type: str,
    generate_fn,
    template_path: str,
    final_output_path: str,
    report_dir: str,
    project_dir: str,
    candidate: str,
    *,
    worker=None,
    bridge_workspace=None,
    bridge_assets=(),
    _provider=None,
    render_orchestrator=None,
    structured_report_override=None,
):
    """报告构建事务 — 原子 no-clobber 提交 (Batch 3.3.1B-R2: 机械提取).

    所有构建、后处理和提交操作在临时文件上执行。
    os.link 是唯一提交点。任何步骤失败 → 回滚删除 temp，
    final 不变或不存在。
    """
    ext = '.pptx' if report_type == 'ppt' else '.docx'
    _report_warnings: list[str] = []
    diagnosis_loaded: bool = False
    manifest = None
    provider_provenance: dict[str, str] | None = None
    effective_template_path = template_path
    temp_output_path: str | None = None
    temp_fd: int = -1
    try:
        # 0. 预检
        from core.ai_client import AIClient
        ai = AIClient.get_instance()
        if not ai.is_available():
            raise RuntimeError(
                "AI 模型未配置。\n\n"
                "请在「报告生成工作台」左侧点击「AI 模型配置」，\n"
                "完成在线模型配置并点击「连接」。"
            )
        # 0.5 模板预校验
        if template_path:
            from utils.report_template_preparation import (
                load_prepared_template_profile,
                prepare_report_template,
            )

            prepared_profile = load_prepared_template_profile(template_path)
            if (
                prepared_profile is None
                or prepared_profile.get('report_type') != report_type
            ):
                template_result = prepare_report_template(
                    template_path,
                    report_type,
                )
                if not template_result.accepted or not template_result.usable_path:
                    raise RuntimeError(
                        '\n'.join(template_result.reasons)
                        or '模板未通过准入检查。'
                    )
                effective_template_path = template_result.usable_path
                if template_result.status == 'normalized':
                    _report_warnings.append(
                        '模板已在生成前自动标准化：'
                        + '；'.join(template_result.reasons)
                    )

        # ═══════════════════════════════════════════════
        # 1. 图表 — 优先读已存 manifest (母本B: 诊断保存时已产图落盘)
        # ═══════════════════════════════════════════════
        from core.chart_store import (
            build_chart_store, chart_manifest_to_figure_manifest,
            chart_manifest_from_dict,
        )
        diag_rec = config.get('_diagnosis_record')
        chart_data = (diag_rec or {}).get('chart_data', {}) or {}

        # record_id 优先取保存时写入的持久化字段 (母本B P1/P2 对齐)
        record_id = (diag_rec or {}).get('record_id')
        if not record_id:
            # 降级: 旧记录无 record_id → 从 timestamp 推导 (向后兼容)
            ts = (diag_rec or {}).get('timestamp',
                                      datetime.now().strftime('%Y%m%d_%H%M%S'))
            record_id = ts.replace(' ', '_').replace(':', '')
            print(f"[图表诊断] 旧记录降级推导 record_id={record_id}")

        stored_manifest = (diag_rec or {}).get('chart_manifest')
        if stored_manifest:
            # 读取路径: 图已在诊断保存时落盘到记录目录, 报告直接引用
            charts_dir = os.path.join(candidate, '数据', '诊断记录',
                                      record_id, 'charts')
            _chart_manifest = chart_manifest_from_dict(stored_manifest)
            print(f"[图表诊断] 从已存 manifest 读取 (非重产), "
                  f"record_id={record_id}, "
                  f"produced={sum(1 for e in _chart_manifest if e.produced)}")
        else:
            # 降级: 旧记录无 manifest, 重新产图到记录目录 (向后兼容)
            charts_dir = os.path.join(candidate, '数据', '诊断记录',
                                      record_id, 'charts')
            os.makedirs(charts_dir, exist_ok=True)
            print(f"[图表诊断] 旧记录降级产图, record_id={record_id}")
            _chart_manifest = build_chart_store(
                chart_data, charts_dir, _report_warnings)

        for manifest_warning in _audit_chart_manifest(
            _chart_manifest, charts_dir
        ):
            if manifest_warning not in _report_warnings:
                _report_warnings.append(manifest_warning)

        manifest = chart_manifest_to_figure_manifest(
            _chart_manifest, charts_dir)
        from core.report_figure_planner import build_figure_catalog
        figure_catalog = build_figure_catalog(manifest)

        print(f"[图表诊断] manifest 图数: {manifest.count}")
        pngs = (
            [f for f in os.listdir(charts_dir) if f.endswith('.png')]
            if os.path.isdir(charts_dir) else []
        )
        print(f"[图表诊断] charts/ 落盘 PNG 数: {len(pngs)} "
              f"({', '.join(pngs[:8])}{'…' if len(pngs) > 8 else ''})")
        charts_context = manifest.to_llm_context(max_chars=600)

        render_assets, required_figure_ids = _build_report_render_assets(
            manifest,
            bridge_workspace=bridge_workspace,
            bridge_assets=bridge_assets,
        )

        # 2. 结构化数据。PPT Master consumes the confirmed Planning Snapshot
        # payload; all other Providers retain the legacy section-generation path.
        if structured_report_override is None:
            builder_data = generate_structured_report(
                config, outline, report_type, generate_fn,
                charts_context=charts_context,
                chart_catalog=figure_catalog,
                progress_callback=(
                    lambda d: worker.progress.emit(d) if worker else None
                ),
                cancel_check=(
                    lambda: getattr(worker, '_cancelled', False) if worker else False
                ),
            )
            _report_warnings.extend(builder_data.pop('_report_warnings', []))
            diagnosis_loaded = builder_data.pop('_diagnosis_loaded', False)
        else:
            try:
                builder_data = json.loads(json.dumps(
                    structured_report_override,
                    ensure_ascii=False,
                ))
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    '已确认的 PPT Master 结构化方案不可序列化。'
                ) from error
            diagnosis_loaded = isinstance(
                config.get('_diagnosis_record'),
                dict,
            )

        # 2b. 注入标定/异常表格 (优先 from chart_data, 降级 from_providers)
        try:
            from core.chart_bundle import ChartBundle, extract_four_tables, _rows_to_md_table
            cd = (diag_rec or {}).get('chart_data', {}) or {}
            if not cd and _provider is not None:
                bundle = ChartBundle.from_providers(_provider)
                cd = bundle.to_dict()
            four_tables = extract_four_tables(cd)
            if four_tables:
                # 等价重建 markdown — 与 gen_markdown_tables_from_bundle 同输出格式
                # 等价性: extract_four_tables 解析原 md 字符串 → _rows_to_md_table 重生成
                #         cell 内容不变（strip→rejoin），分隔行格式一致，heading 前缀一致
                md_parts: list[str] = []
                for tbl in four_tables:
                    md = _rows_to_md_table([tbl.headers] + tbl.rows)
                    md_parts.append(f"\n### {tbl.heading}\n\n{md}\n")
                tables_md = "\n".join(md_parts)
                builder_data.setdefault("sections", []).append({
                    "heading": "数据汇总附表",
                    "content_paragraphs": [tables_md],
                    "image_anchors": [],
                    "tables": [],
                })
                print(f"[报告] 已注入数据汇总附表 (表数={len(four_tables)}, "
                      f"标题={[t.heading for t in four_tables]})")
            else:
                print("[报告] 数据汇总附表为空 — 跳过")
        except Exception as e:
            import traceback as _tb
            _report_warnings.append(
                f"数据汇总附表注入阶段异常：{type(e).__name__}: {e}")
            print(f"[报告] 数据汇总附表注入阶段异常: {_tb.format_exc()}")

        # ── 3. 创建临时输出文件（同目录、同文件系统） ──
        try:
            temp_fd, temp_output_path = tempfile.mkstemp(
                dir=report_dir,
                prefix=".dp-report-",
                suffix=ext,
            )
            os.close(temp_fd)
            temp_fd = -1
        except OSError:
            raise RuntimeError(
                "无法创建报告临时文件，请检查磁盘空间和目录权限。"
            )

        # 3b. Provider 渲染 docx/pptx — 写入 temp_output_path
        try:
            render_request = ReportRenderRequest(
                report_type=report_type,
                structured_report=builder_data,
                template_path=effective_template_path,
                output_path=temp_output_path,
                project_dir=(charts_dir if report_type == 'ppt' else project_dir),
                bridge_workspace=bridge_workspace,
                bridge_assets=tuple(bridge_assets),
                assets=render_assets,
                required_figure_ids=required_figure_ids,
                inclusion_summary={
                    'diagnosis_loaded': diagnosis_loaded,
                    'figure_count': len(required_figure_ids),
                    'project_source_count': len(
                        config.get('project_files', []) or []
                    ),
                    'bridge_asset_count': len(tuple(bridge_assets)),
                },
                cancel_check=(
                    lambda: _check_cancel(worker)
                    if worker else None
                ),
            )
            orchestrator = render_orchestrator or ReportRenderOrchestrator()
            render_result = orchestrator.render(render_request)
            provider_provenance = render_result.provenance.to_dict()
            provider_label = (
                f"{render_result.provenance.provider_id}@"
                f"{render_result.provenance.provider_version}"
            )
            _REPORT_LOGGER.info(
                "Report rendered by provider=%s",
                provider_label,
            )
            print(f"[报告] 实际生成后端: {provider_label}")
            _report_warnings.extend(render_result.warnings)
            for img in render_result.missing_images:
                _report_warnings.append(f"图片缺失: {img}")
        except Exception:
            # Provider render failed → cleanup temp, no final
            _cleanup_temp(temp_output_path)
            raise

        # 4. 图N 引用驱动放置 (构建时后处理: 扫描正文→注入图+图题) — 作用于 temp
        if (
            render_result.requires_host_postprocessing
            and manifest
            and manifest.count > 0
        ):
            try:
                if report_type == 'ppt':
                    assigned_filenames: set[str] = set()
                    for slide in builder_data.get("slides", []):
                        anchor = str(slide.get("image_anchor") or "")
                        match = re.search(
                            r"\[INSERT_IMAGE:\s*([^\]]+)\]",
                            anchor,
                        )
                        if match:
                            assigned_filenames.add(
                                os.path.basename(match.group(1).strip())
                            )
                    _inject_figures_to_pptx(
                        temp_output_path,
                        manifest,
                        _report_warnings,
                        assigned_filenames=assigned_filenames,
                        template_used=bool(effective_template_path),
                    )
                else:
                    _inject_figures_by_reference(
                        temp_output_path, manifest, _report_warnings,
                    )
            except Exception:
                # Figure injection failed → cleanup temp, no final
                _cleanup_temp(temp_output_path)
                raise

        # 5. 「资料纳入情况」段 — 作用于 temp
        if render_result.requires_host_postprocessing:
            try:
                DataProcessorWindow._append_inclusion_footer(
                    temp_output_path, report_type,
                    _report_warnings, diagnosis_loaded,
                )
            except Exception:
                _cleanup_temp(temp_output_path)
                raise

        # ── 6. 验证临时文件可打开 ──
        try:
            _validate_temp_output(temp_output_path, report_type)
        except Exception:
            _cleanup_temp(temp_output_path)
            raise

        # ── 7. fsync temp ──
        try:
            _fsync_path(temp_output_path)
        except Exception:
            _cleanup_temp(temp_output_path)
            raise

        # ── 8. 最后一次 cancel 检查 ──
        if worker is not None and getattr(worker, '_cancelled', False):
            _cleanup_temp(temp_output_path)
            raise RuntimeError("报告生成已取消")

        # ── 9. os.link 原子 no-clobber 提交 ──
        try:
            os.link(temp_output_path, final_output_path)
        except FileExistsError:
            _cleanup_temp(temp_output_path)
            raise RuntimeError(
                "报告提交冲突：目标文件已被外部创建，请重试。"
            )
        except OSError:
            _cleanup_temp(temp_output_path)
            raise RuntimeError(
                "报告提交失败：当前文件系统不支持原子提交。"
            )

        # ── 10. 提交后清理 temp ──
        try:
            os.unlink(temp_output_path)
        except OSError:
            # temp cleanup failed, final is still valid
            _report_warnings.append(
                "报告已成功保存，临时文件清理失败（不影响报告完整性）。"
            )

        return {
            'path': final_output_path,
            'warnings': _report_warnings,
            'diagnosis_loaded': diagnosis_loaded,
            'provider_provenance': provider_provenance,
        }
    except Exception as e:
        # ── 回滚：确保 temp 已删除 ──
        if temp_output_path is not None:
            _cleanup_temp(temp_output_path)
        import traceback as _tb
        msg = f'{e}'
        if _report_warnings:
            msg += '\n\n已收集的警告/降级信息:'
            for w in _report_warnings:
                msg += f'\n  - {w}'
        msg += f'\n{_tb.format_exc()}'
        raise RuntimeError(msg) from e


class DataProcessorWindow(QMainWindow):
    def __init__(self, skill_task_owner: "SkillInstallTaskOwner | None" = None):
        super().__init__()
        self.current_data = None
        self._annotation_orig_dtypes: dict[str, Any] | None = None
        self.current_annotation: dict[str, str] = {}
        self.current_columns = []
        self.sampled_data = None
        self.file_header_lines = []
        self.sampled_file_path = None
        self.current_template = None
        self.sensor_results = {}  # 存储计算后的传感器物理量
        self.cleaning_rules = [
            CleaningRule(name='数值范围', rule_type='range', enabled=True, min_value=0, max_value=100, fill_method='linear'),
            CleaningRule(name='负数检测', rule_type='negative', enabled=True, fill_method='forward'),
        ]
        self.sensor_system = SensorSystem()
        self.init_sensor_system()
        
        # SSOT: 全局应用状态
        self.state = AppState()
        self.state = self.state.with_fbgs(self.sensor_system.fbgs)
        self.state = self.state.with_sensors(self.sensor_system.sensors)
        # 全局参数 → SSOT
        initial_gp = {
            name: GlobalParameter(
                name=name,
                value=v if isinstance(v, (int, float)) else v.get('value', 0),
                unit=v.get('unit', '') if isinstance(v, dict) else '',
                description=v.get('description', '') if isinstance(v, dict) else '',
            )
            for name, v in self.sensor_system.global_parameters.items()
        }
        self.state = self.state.with_global_parameters(initial_gp)

        # 传感器系统

        # ── Skill install task owner (Batch 2.4) ──
        # Application-level owner for in-flight install/uninstall workers.
        # Owned by QApplication (not DataProcessorWindow) so workers survive
        # main window close. DataProcessorWindow holds a reference only.
        # Workers are transferred here when the skill tab widget closes,
        # ensuring they complete naturally even after UI destruction.
        self._skill_task_owner = skill_task_owner  # May be None in tests

        from utils.app_paths import get_skills_paths
        skill_paths = get_skills_paths()
        self._report_provider_controller = ReportProviderController(
            skill_paths.registry_file,
            skill_paths.installed_dir,
        )
        self._ppt_master_workflow: PptMasterPlanningWorkflow | None = None

        # ── Report Bridge: singleton coordinator + controller (Batch 3.3.2) ──
        self._bridge_coordinator = ArtifactOperationCoordinator()
        from ui.report_bridge_controller import ReportBridgeController
        self._bridge_controller = ReportBridgeController(
            artifact_store=None,  # Will be lazily resolved by service
            coordinator=self._bridge_coordinator,
            parent=self,
        )
        self._bridge_controller.result_ready.connect(
            self._on_bridge_result_ready
        )
        self._bridge_closing = False

        self.init_ui()

        # AI诊断Widget延迟加载模型配置

    def closeEvent(self, event) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        """Handle main window close: cancel active skill operations.

        Batch 2.5: Tasks survive window close — TaskOwner keeps QApplication alive.
        1. Source inspection: cancel QNetworkReply via source controller
        2. Download: cancel QNetworkReply → delete .part → no InstallWorker started
        3. InstallWorker/UninstallWorker: cancel token set → controller transferred
           to TaskOwner → worker completes naturally
        4. If tasks are still running: keep QApplication alive via TaskOwner,
           schedule app.quit() when safe
        5. Original cleanup (super().closeEvent) still runs

        TaskOwner lives on QApplication, so workers survive window close.
        Controller cleanup exceptions are logged, not silenced.
        """
        import logging
        _close_logger = logging.getLogger(__name__)

        # ── Path 1: Cancel skill source inspection ──
        try:
            if hasattr(self, 'skill_tab_widget'):
                widget = self.skill_tab_widget
                if widget is not None and hasattr(widget, '_source_controller'):
                    widget._source_controller.cancel()
        except Exception:
            _close_logger.exception("Error cancelling source controller during close")

        # ── Path 2-4: Cancel install/download, transfer workers to TaskOwner ──
        try:
            if hasattr(self, 'skill_tab_widget'):
                widget = self.skill_tab_widget
                if widget is not None and hasattr(widget, '_install_controller'):
                    ctrl = widget._install_controller
                    # Disconnect UI signals to prevent callbacks after widget destruction
                    for sig_name in ('result_ready', 'error_occurred', 'progress_changed',
                                     'stage_changed', 'running_changed'):
                        try:
                            getattr(ctrl, sig_name).disconnect()
                        except (TypeError, RuntimeError):
                            pass
                    # Transfer controller ownership to app-level TaskOwner
                    if self._skill_task_owner is not None:
                        self._skill_task_owner.add_controller(ctrl)
                    # Cancel active operations (sets cancel token, aborts downloads)
                    ctrl.cancel()
        except Exception:
            _close_logger.exception("Error cancelling install controller during close")

        # ── Path 2b: Cancel runtime healthcheck, transfer RuntimeWorker (Batch 3.0) ──
        try:
            if hasattr(self, 'skill_tab_widget'):
                widget = self.skill_tab_widget
                if widget is not None and hasattr(widget, '_runtime_controller'):
                    rctrl = widget._runtime_controller
                    for sig_name in ('result_ready', 'error_occurred', 'running_changed'):
                        try:
                            getattr(rctrl, sig_name).disconnect()
                        except (TypeError, RuntimeError):
                            pass
                    if self._skill_task_owner is not None:
                        self._skill_task_owner.add_controller(rctrl)  # type: ignore[reportArgumentType]
                    rctrl.cancel()
                    rctrl.close()
        except Exception:
            _close_logger.exception("Error cancelling runtime controller during close")

        # ── Path 3: Bridge controller shutdown (Batch 3.3.2) ──
        self._bridge_closing = True
        try:
            # Cancel bridge preparation if active
            self._bridge_controller.cancel()
            # Close bridge controller (handles thread lifecycle)
            self._bridge_controller.close()
        except Exception:
            _close_logger.exception("Error shutting down bridge controller")

        # ── Path 3.5: Cancel active report worker (Batch 3.3.2-R5) ──
        if hasattr(self, '_report_worker') and self._report_worker is not None:
            try:
                active_provider = getattr(
                    self,
                    '_active_report_provider',
                    None,
                )
                cancel_provider = getattr(active_provider, 'cancel', None)
                if callable(cancel_provider):
                    cancel_provider()
            except Exception:
                _close_logger.exception(
                    "Error cancelling report Provider during close"
                )
            try:
                if self._report_worker.isRunning():
                    self._report_worker.cancel()
            except Exception:
                _close_logger.exception("Error cancelling report worker during close")

        # ── Path 4: Keep QApplication alive if tasks are still running ──
        if self._skill_task_owner is not None and self._skill_task_owner.has_running_tasks():
            _close_logger.info(
                "Skill tasks still running — QApplication stays alive until safe"
            )
            self._skill_task_owner.begin_application_shutdown()
            self._skill_task_owner.schedule_app_quit_when_safe()

        # ── Allow Qt to clean up child widgets and their resources ──
        super().closeEvent(event)

    # ── AI诊断兼容性代理 (AiDiagnosisWidget 独立管理模型配置) ──

    @property
    def ai_model_combo(self):
        if hasattr(self, 'ai_diagnosis_widget'):
            return self.ai_diagnosis_widget.ai_model_combo
        return None

    @property
    def ai_models_config(self):
        if hasattr(self, 'ai_diagnosis_widget'):
            return self.ai_diagnosis_widget._ai_models_config
        return {}

    def load_ai_models_config(self):
        pass

    def save_ai_models_config(self):
        if hasattr(self, 'ai_diagnosis_widget'):
            self.ai_diagnosis_widget._save_models_config()

    # ── App 本地设置持久化 ──

    @staticmethod
    def _get_app_settings_path() -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), '.app_settings.json')

    @classmethod
    def _load_app_settings(cls) -> dict:
        path = cls._get_app_settings_path()
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    @classmethod
    def _save_app_setting(cls, key: str, value) -> None:
        settings = cls._load_app_settings()
        settings[key] = value
        path = cls._get_app_settings_path()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)

    def refresh_ai_model_selector(self):
        if hasattr(self, 'ai_diagnosis_widget'):
            self.ai_diagnosis_widget.refresh_model_selector()

    # ── 数据分析兼容性代理 (AnalysisTabWidget 独立管理图表控件) ──

    @property
    def data_source_combo(self):
        if hasattr(self, 'analysis_tab_widget'):
            return self.analysis_tab_widget.data_source_combo
        return None

    @property
    def range_type_combo(self):
        if hasattr(self, 'analysis_tab_widget'):
            return self.analysis_tab_widget.range_type_combo
        return None

    @property
    def range_start(self):
        if hasattr(self, 'analysis_tab_widget'):
            return self.analysis_tab_widget.range_start
        return None

    @property
    def range_end(self):
        if hasattr(self, 'analysis_tab_widget'):
            return self.analysis_tab_widget.range_end
        return None

    def refresh_analysis_sensors(self):
        if hasattr(self, 'analysis_tab_widget'):
            self.analysis_tab_widget.refresh_analysis_sensors()

    def init_sensor_system(self):
        """初始化传感器系统"""
        # 添加默认FBG（根据ENLIGHT数据格式）
        # FBG(id, channel, wavelength_min, wavelength_max)
        self.sensor_system.add_fbg(FBG('W1', '波长1', 1520, 1590))
        self.sensor_system.add_fbg(FBG('W2', '波长2', 1520, 1590))
        self.sensor_system.add_fbg(FBG('W3', '波长3', 1520, 1590))
        self.sensor_system.add_fbg(FBG('W4', '波长4', 1520, 1590))
        self.sensor_system.add_fbg(FBG('W5', '波长5', 1520, 1590))
        self.sensor_system.add_fbg(FBG('W6', '波长6', 1520, 1590))
        self.sensor_system.add_fbg(FBG('W7', '波长7', 1520, 1590))
        self.sensor_system.add_fbg(FBG('W8', '波长8', 1520, 1590))

        # 传感器列表初始为空，由用户通过 UI 添加
        # （旧默认传感器 '应变1'/'温度1' 已移除，避免干扰用户配置）

    def init_ui(self):
        self.setWindowTitle('DataProcessor Pro - 数据分析软件')
        self.setGeometry(100, 100, 1400, 900)

        # Create menu bar
        self.create_menu_bar()

        # Create central widget with tabs
        self.central_widget = QTabWidget()
        self.setCentralWidget(self.central_widget)

        # Data tab
        self.data_tab_widget = DataTabWidget()
        self.central_widget.addTab(self.data_tab_widget, '数据文件')

        # Cleaning tab
        self.cleaning_tab_widget = CleaningTabWidget()
        self.central_widget.addTab(self.cleaning_tab_widget, '数据清洗')

        # Sensor tab
        self.sensor_tab_widget = SensorTabWidget()
        self.central_widget.addTab(self.sensor_tab_widget, '光纤公式配置')

        # Analysis tab
        self.analysis_tab = QWidget()
        self.create_analysis_tab()
        self.central_widget.addTab(self.analysis_tab, '数据分析')

        # Compare tab (多源数据对比)
        self.compare_tab_widget = CompareTabWidget()
        self.central_widget.addTab(self.compare_tab_widget, '多源对比')

        # Calibration tab (传感器标定)
        self.calibration_tab_widget = CalibrationTabWidget()
        self.central_widget.addTab(self.calibration_tab_widget, '传感器标定')

        # Report tab
        self.report_tab = QWidget()
        self.create_report_tab()
        self.central_widget.addTab(self.report_tab, '成果输出与报告')

        # ── Signal bindings ──
        self.data_tab_widget.open_file_requested.connect(self.open_file)
        self.data_tab_widget.clear_data_requested.connect(self.clear_data)
        self.data_tab_widget.sample_data_requested.connect(self.sample_data)
        self.data_tab_widget.save_data_requested.connect(self.save_sampled_data)
        self.data_tab_widget.save_template_requested.connect(self.save_template_to_file)

        # ── 表格编辑 → 底层 DataFrame 双向同步 ──
        self.data_tab_widget.data_cell_edited.connect(self._on_data_table_cell_edited)

        self.cleaning_tab_widget.apply_cleaning_requested.connect(
            lambda c: self.apply_cleaning(config=c)
        )

        self.sensor_tab_widget.fbg_add_requested.connect(self.add_fbg)
        self.sensor_tab_widget.fbg_edit_requested.connect(
            lambda row: self.edit_fbg(row)
        )
        self.sensor_tab_widget.fbg_delete_requested.connect(
            lambda row: self.delete_fbg(row)
        )
        self.sensor_tab_widget.sensor_add_requested.connect(self.add_sensor)
        self.sensor_tab_widget.sensor_edit_requested.connect(
            lambda row: self.edit_sensor(row)
        )
        self.sensor_tab_widget.sensor_delete_requested.connect(
            lambda row: self.delete_sensor(row)
        )
        self.sensor_tab_widget.sensor_copy_requested.connect(
            lambda row: self.copy_sensor(row)
        )
        self.sensor_tab_widget.calculate_requested.connect(
            lambda ref_row: self.calculate_sensors(ref_row)
        )
        self.sensor_tab_widget.save_sensor_requested.connect(
            lambda name, fmt: self.save_sensor_data(name, fmt)
        )
        self.sensor_tab_widget.export_sensor_requested.connect(self.export_sensor_data)
        self.sensor_tab_widget.global_params_requested.connect(self.open_global_parameter_dialog)

        self.project_tab_widget.project_selected.connect(self.on_project_selected)
        self.project_tab_widget.new_project_requested.connect(self.on_new_project)
        self.project_tab_widget.delete_project_requested.connect(self.on_delete_project)
        self.project_tab_widget.open_project_folder_requested.connect(self.on_open_project_folder)
        self.project_tab_widget.add_folder_requested.connect(self.on_add_folder_to_project)
        self.project_tab_widget.add_file_requested.connect(self.on_add_file_to_project)
        self.project_tab_widget.delete_item_requested.connect(self.on_delete_project_item)
        self.project_tab_widget.open_item_requested.connect(self.on_open_project_item)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage('就绪')

    def create_menu_bar(self):
        menubar = self.menuBar()

        # 全局配置 menu
        config_menu = menubar.addMenu('全局配置')

        save_config_action = QAction('保存配置', self)
        save_config_action.setShortcut('Ctrl+Shift+S')
        save_config_action.triggered.connect(self.save_config)
        config_menu.addAction(save_config_action)

        load_config_action = QAction('读取配置', self)
        load_config_action.triggered.connect(self.load_config)
        config_menu.addAction(load_config_action)

        config_menu.addSeparator()

        reset_config_action = QAction('重置配置', self)
        reset_config_action.triggered.connect(self.reset_config)
        config_menu.addAction(reset_config_action)

        # File menu
        file_menu = menubar.addMenu('文件')

        open_action = QAction('打开文件...', self)
        open_action.setShortcut('Ctrl+O')
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)

        save_action = QAction('保存数据...', self)
        save_action.setShortcut('Ctrl+S')
        save_action.triggered.connect(self.save_data)
        file_menu.addAction(save_action)

        file_menu.addSeparator()

        export_action = QAction('导出报告...', self)
        export_action.triggered.connect(self.export_report)
        file_menu.addAction(export_action)

        file_menu.addSeparator()

        exit_action = QAction('退出', self)
        exit_action.setShortcut('Ctrl+Q')
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def add_fbg(self):
        """添加FBG"""
        dialog = FBGEditDialog(self)
        if dialog.exec():
            fbg = dialog.get_fbg()
            self.sensor_system.add_fbg(fbg)
            self.sensor_tab_widget.set_fbg_list(self.sensor_system.fbgs)

    def edit_fbg(self, row: int):
        """编辑选中FBG"""
        if 0 <= row < len(self.sensor_system.fbgs):
            fbg = self.sensor_system.fbgs[row]
            dialog = FBGEditDialog(self, fbg)
            if dialog.exec():
                self.sensor_system.fbgs[row] = dialog.get_fbg()
                self.sensor_tab_widget.set_fbg_list(self.sensor_system.fbgs)

    def delete_fbg(self, row: int):
        """删除选中FBG"""
        if 0 <= row < len(self.sensor_system.fbgs):
            self.sensor_system.fbgs.pop(row)
            self.sensor_tab_widget.set_fbg_list(self.sensor_system.fbgs)

    def _auto_populate_fbgs(self, df):
        """从数据文件列名自动识别FBG传感器并填充FBG定义表。

        检测逻辑委托给 utils.column_utils.detect_fbg_columns。
        """
        from utils.column_utils import detect_fbg_columns

        fbg_cols = detect_fbg_columns(df)
        if not fbg_cols:
            return 0

        self.sensor_system.fbgs.clear()
        for i, col in enumerate(fbg_cols):
            self.sensor_system.add_fbg(FBG(f'W{i+1}', col, 1520, 1590))

        self.sensor_tab_widget.set_fbg_list(self.sensor_system.fbgs)
        print(f"[FBG] 注册 {len(fbg_cols)} 个: {[(f.id, f.channel) for f in self.sensor_system.fbgs]}")
        return len(fbg_cols)

    def add_sensor(self):
        """添加传感器"""
        dialog = SensorEditDialog(self)
        if dialog.exec():
            sensor = dialog.get_sensor()
            self.sensor_system.add_sensor(sensor)
            self.sensor_tab_widget.set_sensor_list(self.sensor_system.sensors)

    def edit_sensor(self, row: int):
        """编辑选中传感器"""
        if 0 <= row < len(self.sensor_system.sensors):
            sensor = self.sensor_system.sensors[row]
            dialog = SensorEditDialog(self, sensor)
            if dialog.exec():
                self.sensor_system.sensors[row] = dialog.get_sensor()
                self.sensor_tab_widget.set_sensor_list(self.sensor_system.sensors)

    def delete_sensor(self, row: int):
        """删除选中传感器"""
        if 0 <= row < len(self.sensor_system.sensors):
            self.sensor_system.sensors.pop(row)
            self.sensor_tab_widget.set_sensor_list(self.sensor_system.sensors)

    def copy_sensor(self, row: int):
        """复制选中传感器，自动递增ID和公式中的W编号"""
        if 0 <= row < len(self.sensor_system.sensors):
            original = self.sensor_system.sensors[row]

            # 从原始ID提取数字，创建新ID
            # 例如: "传感器1" -> "传感器2", "传感器A1" -> "传感器A2"
            import re
            id_match = re.search(r'(\d+)$', original.id)
            if id_match:
                base_id = original.id[:id_match.start()]
                old_num = int(id_match.group(1))
                new_num = old_num + 1
                new_id = f'{base_id}{new_num}'
            else:
                new_id = original.id + '_copy'
                old_num = 0
                new_num = 1

            # 替换公式中的W编号：W1->W2, W2->W3, etc.
            # 如果原公式使用W{old_num}则改为W{new_num}
            new_formula = original.formula
            if old_num > 0:
                # 替换公式中的 W{old_num} 为 W{new_num}
                new_formula = re.sub(rf'\bW{old_num}\b', f'W{new_num}', original.formula)

            copied = Sensor(
                new_id,
                original.sensor_type,
                new_formula,
                dict(original.constants),  # 深拷贝常量字典
                original.active
            )
            self.sensor_system.add_sensor(copied)
            self.sensor_tab_widget.set_sensor_list(self.sensor_system.sensors)

    def open_global_parameter_dialog(self):
        dialog = GlobalParameterDialog(self)
        dialog.set_parameters(self.state.global_parameters)

        def _on_param_updated(name: str, entry: dict):
            # 更新 AppState (SSOT)
            gp = GlobalParameter(
                name=name,
                value=entry.get('value', 0),
                unit=entry.get('unit', ''),
                description=entry.get('description', ''),
            )
            new_params = dict(self.state.global_parameters)
            new_params[name] = gp
            self.state = self.state.with_global_parameters(new_params)

            # 同步到 SensorSystem（公式计算引擎使用）
            self.sensor_system.global_parameters[name] = {
                'value': entry.get('value', 0),
                'unit': entry.get('unit', ''),
                'description': entry.get('description', ''),
            }

            # 自动重算
            self._recalc_after_global_param_update()

        def _on_param_deleted(name: str):
            new_params = dict(self.state.global_parameters)
            new_params.pop(name, None)
            self.state = self.state.with_global_parameters(new_params)
            self.sensor_system.global_parameters.pop(name, None)
            self._recalc_after_global_param_update()

        dialog.param_updated.connect(_on_param_updated)
        dialog.param_deleted.connect(_on_param_deleted)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.status_bar.showMessage('全局参数已更新')
        else:
            self.status_bar.showMessage('全局参数已关闭')

    def _recalc_after_global_param_update(self):
        """全局参数变更后自动重新计算传感器."""
        if self.current_data is not None and self.sensor_system.sensors:
            try:
                analysis_df, _, _ = self._get_analysis_data()
                if analysis_df is None or analysis_df.empty:
                    return
                results = self.sensor_system.calculate(analysis_df, self.current_columns)
                cleaned = self._clean_sensor_keys(results)
                self.sensor_results = cleaned
                self.state = self.state.with_analysis_results(cleaned)
                self.sensor_tab_widget.set_result_preview(cleaned, analysis_df)
                self.analysis_tab_widget.set_sensor_results(cleaned)
                self.refresh_analysis_sensors()
                self.status_bar.showMessage('全局参数已更新，传感器已重新计算')
            except Exception as e:
                print(f'全局参数更新后重算失败: {e}')

    def calculate_sensors(self, ref_row: int = 0):
        """计算所有传感器"""
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '请先加载数据')
            return

        try:
            self.sensor_system.set_reference_row(ref_row)

            # 使用跳过暗号行后的清洗数据
            analysis_df, _, _ = self._get_analysis_data()

            if analysis_df is None or analysis_df.empty:
                QMessageBox.warning(self, '警告', '有效数据为空')
                return

            results = self.sensor_system.calculate(analysis_df, self.current_columns)
            cleaned = self._clean_sensor_keys(results)

            n_preview_rows = min(100, len(analysis_df))
            self.sensor_tab_widget.set_result_preview(cleaned, analysis_df)
            self.sensor_results = cleaned
            self.state = self.state.with_analysis_results(cleaned)
            self.analysis_tab_widget.set_sensor_results(cleaned)
            self.refresh_analysis_sensors()
            self.status_bar.showMessage(f'传感器计算完成，预览显示前{n_preview_rows}行')

        except Exception as e:
            import traceback
            QMessageBox.critical(self, '错误', f'计算失败: {str(e)}\n\n{traceback.format_exc()}')

    def save_sensor_data(self, filename: str = '', fmt: str = 'csv'):
        """保存传感器数据到指定路径"""
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '请先加载数据')
            return

        if not filename:
            QMessageBox.warning(self, '警告', '请输入文件名')
            return

        if fmt == 'csv':
            file_filter = 'CSV Files (*.csv)'
            ext = '.csv'
        elif fmt == 'txt':
            file_filter = 'Text Files (*.txt)'
            ext = '.txt'
        else:
            file_filter = 'Excel Files (*.xlsx)'
            ext = '.xlsx'

        file_path, _ = QFileDialog.getSaveFileName(
            self, '保存传感器数据',
            filename + ext,
            file_filter
        )
        if file_path:
            try:
                ref_row = self.sensor_tab_widget.ref_row_spin.value()
                self.sensor_system.set_reference_row(ref_row)
                analysis_df, time_col, _ = self._get_analysis_data()
                if analysis_df is None or analysis_df.empty:
                    QMessageBox.warning(self, '警告', '有效数据为空')
                    return
                results = self.sensor_system.calculate(analysis_df, self.current_columns)
                cleaned = self._clean_sensor_keys(results)

                export_df = pd.DataFrame()
                export_df[time_col] = analysis_df[time_col]
                for sensor_id, values in cleaned.items():
                    export_df[sensor_id] = values

                if fmt == 'csv':
                    export_df.to_csv(file_path, index=False, encoding='utf-8-sig')
                elif fmt == 'txt':
                    export_df.to_csv(file_path, index=False, sep='\t', encoding='utf-8-sig')
                else:
                    export_df.to_excel(file_path, index=False)

                QMessageBox.information(self, '成功', f'传感器数据已保存到:\n{file_path}')
            except Exception as e:
                QMessageBox.critical(self, '错误', f'保存失败: {str(e)}')

    def export_sensor_data(self):
        """导出传感器数据"""
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '请先计算传感器数据')
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, '导出传感器数据', '', 'CSV Files (*.csv)'
        )
        if file_path:
            try:
                analysis_df, time_col, _ = self._get_analysis_data()
                if analysis_df is None or analysis_df.empty:
                    QMessageBox.warning(self, '警告', '有效数据为空')
                    return
                results = self.sensor_system.calculate(analysis_df, self.current_columns)
                cleaned = self._clean_sensor_keys(results)

                export_df = pd.DataFrame()
                export_df[time_col] = analysis_df[time_col]
                for sensor_id, values in cleaned.items():
                    export_df[sensor_id] = values

                export_df.to_csv(file_path, index=False)
                QMessageBox.information(self, '成功', f'数据已导出到: {file_path}')
            except Exception as e:
                QMessageBox.critical(self, '错误', f'导出失败: {str(e)}')

    def create_analysis_tab(self):
        """数据分析选项卡"""
        self.analysis_tab_widget = AnalysisTabWidget(self)
        layout = QVBoxLayout()
        layout.addWidget(self.analysis_tab_widget)
        self.analysis_tab.setLayout(layout)

    def create_report_tab(self):
        # 成果输出与报告Tab
        main_layout = QHBoxLayout()

        # 左侧：功能选项列表
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 成果输出与报告标题
        title_label = QLabel('成果输出与报告')
        title_label.setStyleSheet('font-size: 16px; font-weight: bold; padding: 5px;')
        left_layout.addWidget(title_label)

        # 功能按钮列表 (已移除项目内容，合并到项目资料管理)
        self.info_menu_list = QListWidget()
        self.info_menu_list.setMaximumWidth(180)
        self.info_menu_list.addItem('项目资料管理')
        self.info_menu_list.addItem('报告生成工作台')
        self.info_menu_list.addItem('AI诊断')
        self.info_menu_list.addItem('网络剪藏')
        self.info_menu_list.addItem('知识库管理')
        self.info_menu_list.addItem('技能插件中心')
        self.info_menu_list.currentRowChanged.connect(self.on_info_menu_changed)
        left_layout.addWidget(self.info_menu_list)

        left_panel.setLayout(left_layout)
        main_layout.addWidget(left_panel)

        # 右侧：内容面板
        self.report_content_stack = QStackedWidget()
        main_layout.addWidget(self.report_content_stack, 1)


        # 项目资料管理页面
        self.project_tab_widget = ProjectManagerWidget()
        self.report_content_stack.addWidget(self.project_tab_widget)
        self.load_project_list()

        # 报告生成工作台 (合并 Word + PPT)
        report_workbench = self.create_report_workbench_page()
        self.report_content_stack.addWidget(report_workbench)

        # AI诊断页面
        ai_diagnosis_page = self.create_ai_diagnosis_page()
        self.report_content_stack.addWidget(ai_diagnosis_page)

        # 网络剪藏页面
        self.clipper_tab_widget = WebClipperWidget()
        self.report_content_stack.addWidget(self.clipper_tab_widget)

        # 知识库管理页面
        self.wiki_tab_widget = WikiTabWidget()
        self.report_content_stack.addWidget(self.wiki_tab_widget)

        # 技能插件中心页面
        self.skill_tab_widget = AgentSkillWidget(
            task_owner=self._skill_task_owner,
        )
        # Batch 3.3.2: Wire bridge coordinator + send-to-report signal
        self.skill_tab_widget.set_bridge_coordinator(self._bridge_coordinator)
        self.skill_tab_widget.send_to_report_requested.connect(
            self._handle_bridge_send
        )
        self.skill_tab_widget.registry_changed.connect(
            self._refresh_report_provider_options
        )
        self.report_content_stack.addWidget(self.skill_tab_widget)

        self._refresh_report_provider_options()

        self.report_tab.setLayout(main_layout)

        # 加载已保存的 Cookie
        saved_cookie = self._load_app_settings().get('zhihu_cookie', '')
        self.clipper_tab_widget.set_cookie(saved_cookie)
        self.clipper_tab_widget.cookie_changed.connect(
            lambda c: self._save_app_setting('zhihu_cookie', c)
        )
        self.clipper_tab_widget.wiki_page_saved.connect(
            self.wiki_tab_widget.refresh_wiki_pages
        )

        self.analysis_tab_widget.set_sensor_system(self.sensor_system)
        # 分析页"刷新"按钮 → 重新从暗号标注获取分析数据
        self.analysis_tab_widget.data_refresh_requested.connect(self._on_analysis_refresh_requested)

    def create_report_workbench_page(self):
        """统一的报告生成工作台 (合并 Word + PPT)"""
        self.report_workbench_widget = ReportWorkbenchWidget(self)
        # 信号绑定
        self.report_workbench_widget.outline_requested.connect(
            self._handle_outline_generation
        )
        self.report_workbench_widget.full_report_requested.connect(
            self._handle_full_report_generation
        )
        self.report_workbench_widget.load_diagnosis_requested.connect(
            self._handle_load_diagnosis
        )
        self.report_workbench_widget.report_provider_filter_changed.connect(
            self._refresh_report_provider_options
        )
        self.report_workbench_widget.report_inputs_changed.connect(
            self._handle_report_inputs_changed
        )
        self.report_workbench_widget.ppt_master_planning_requested.connect(
            self._handle_ppt_master_planning_action
        )
        # Batch 3.3.2: Bridge signals
        self.report_workbench_widget.bridge_clear_requested.connect(
            self._handle_bridge_clear
        )
        self.report_workbench_widget.bridge_cancel_prepare_requested.connect(
            self._handle_bridge_cancel_prepare
        )
        return self.report_workbench_widget

    def _refresh_report_provider_options(
        self,
        artifact_type: str | None = None,
        template_mode: str | None = None,
    ) -> None:
        """Refresh only Providers compatible with the current report inputs."""
        if artifact_type is None or template_mode is None:
            artifact_type, template_mode = (
                self.report_workbench_widget.current_report_provider_filter()
            )
        report_type = 'ppt' if artifact_type == 'pptx' else 'word'
        try:
            options = self._report_provider_controller.list_options(
                report_type=report_type,
                template_mode=template_mode,
            )
        except Exception as error:
            _REPORT_LOGGER.warning(
                "Report provider catalog refresh failed: %s",
                type(error).__name__,
            )
            options = (ReportProviderOption(
                provider_id=BUILTIN_PROVIDER_ID,
                provider_version=BUILTIN_PROVIDER_VERSION,
                display_name='内置标准生成器',
                is_builtin=True,
            ),)
        if report_type == 'ppt' and probe_expected_ppt_master_installation():
            host_option = ReportProviderOption(
                provider_id=PPT_MASTER_PROVIDER_ID,
                provider_version=PPT_MASTER_PROVIDER_VERSION,
                display_name='PPT Master 专业演示生成器',
                is_builtin=False,
            )
            if not any(
                option.provider_id == PPT_MASTER_PROVIDER_ID
                for option in options
            ):
                options = (*options, host_option)
        self.report_workbench_widget.set_report_provider_options(options)

    def _handle_report_inputs_changed(self) -> None:
        """Invalidate Host confirmation fingerprints after any UI input change."""
        workflow = self._ppt_master_workflow
        if workflow is None or not workflow.snapshot:
            return
        reason = '需求、资料、诊断、模板、后端或技能素材发生变化'
        workflow.invalidate(reason)
        self.report_workbench_widget.invalidate_ppt_master_workflow(reason)

    def _disconnect_report_cancel_handler(self) -> None:
        """Remove the per-generation cancel closure after terminal state."""
        handler = getattr(self, '_report_cancel_handler', None)
        if handler is None:
            return
        try:
            self.report_workbench_widget.cancel_requested.disconnect(handler)
        except (TypeError, RuntimeError):
            pass
        self._report_cancel_handler = None

    # ═══════════════════════════════════════════════
    # 后台 Worker (QThread)
    # ═══════════════════════════════════════════════


    # ═══════════════════════════════════════════════
    # 大纲生成 — 后台 AI 调用
    # ═══════════════════════════════════════════════

    def _get_generate_fn(self):
        """获取 LLM 生成回调 — 统一入口 AIClient (按 backend 路由 online/local).

        AIClient 不可用时返回 mock 降级（明确提示"未配置"）。
        """
        from core.ai_client import AIClient
        ai = AIClient.get_instance()
        if ai.is_available():
            return ai.get_generate_fn(enable_thinking=False)

        # 降级：无 AI 配置时返回 mock 函数（用户在 UI 看到模拟模板内容）
        print("[主窗口] AI 未配置，大纲/报告将使用模拟降级")
        def _fallback(prompt: str) -> str:
            return (
                '# ⚠ AI 未配置 — 以下为降级模板，非真实分析结果\n\n'
                '# 传感器数据分析报告\n\n'
                '## 数据概述\n'
                '- 数据来源与采集方式\n'
                '- 传感器布设方案\n\n'
                '## 数据质量评估\n'
                '- 异常值检测结果\n'
                '- 缺失值统计\n\n'
                '## 物理量分析\n'
                '- 应变分析\n'
                '- 温度分析\n\n'
                '## 结论与建议\n'
                '- 主要发现\n'
                '- 后续工作建议\n'
            )
        return _fallback

    def _handle_outline_generation(self, config: dict):
        """后台生成大纲，完成后填入编辑器."""
        provider = config.get('report_provider') or {}
        if provider.get('provider_id') == PPT_MASTER_PROVIDER_ID:
            self._start_ppt_master_planning(config)
            return
        generate_fn = self._get_generate_fn()
        self.report_workbench_widget.outline_btn.setEnabled(False)
        self.report_workbench_widget.outline_btn.setText('⏳ 正在生成大纲...')
        self.status_bar.showMessage('正在生成大纲...')

        def _on_outline_done(markdown: str):
            self.report_workbench_widget.set_outline_text(markdown)
            self.report_workbench_widget.outline_btn.setEnabled(True)
            self.report_workbench_widget.outline_btn.setText('📝 生成/预览报告大纲')
            self.status_bar.showMessage('大纲生成完成')

        def _on_outline_error(msg: str):
            self.report_workbench_widget.outline_btn.setEnabled(True)
            self.report_workbench_widget.outline_btn.setText('📝 生成/预览报告大纲')
            self.status_bar.showMessage('大纲生成失败')
            QMessageBox.critical(self, '大纲生成失败', msg)

        self._outline_worker = ReportWorker(
            generate_outline,
            args=(config, generate_fn),
        )
        self._outline_worker.finished.connect(_on_outline_done)
        self._outline_worker.error.connect(_on_outline_error)
        self._outline_worker.start()

    def _start_ppt_master_planning(self, config: dict) -> None:
        """Build a fresh Host workflow and generate its unconfirmed outline."""
        self.report_workbench_widget.outline_btn.setEnabled(False)
        self.report_workbench_widget.outline_btn.setText(
            '⏳ 正在生成 PPT Master 叙事大纲...'
        )
        self.report_workbench_widget.ppt_master_confirm_btn.setEnabled(False)
        self.report_workbench_widget.full_report_btn.setEnabled(False)
        self.status_bar.showMessage('正在生成 PPT Master 叙事大纲...')

        def _build_workflow(worker=None):
            return self._create_ppt_master_workflow(config, worker=worker)

        def _done(workflow: PptMasterPlanningWorkflow):
            self._ppt_master_workflow = workflow
            self._apply_ppt_master_workflow_view(workflow)
            self.report_workbench_widget.outline_btn.setEnabled(True)
            self.report_workbench_widget.outline_btn.setText(
                '🧭 生成/重新生成 PPT Master 方案'
            )
            self.status_bar.showMessage('PPT Master 大纲待确认')

        def _error(message: str):
            self.report_workbench_widget.outline_btn.setEnabled(True)
            self.report_workbench_widget.outline_btn.setText(
                '🧭 生成/重新生成 PPT Master 方案'
            )
            self.status_bar.showMessage('PPT Master 规划失败')
            friendly = message.split('\nTraceback', 1)[0].strip()
            QMessageBox.warning(
                self,
                'PPT Master 规划失败',
                friendly
                + '\n\n系统不会自动回退；你可修正输入后重试，或明确选择内置后端。',
            )

        self._outline_worker = ReportWorker(_build_workflow)
        self._outline_worker.finished.connect(_done)
        self._outline_worker.error.connect(_error)
        self._outline_worker.start()

    def _create_ppt_master_workflow(
        self,
        config: dict,
        *,
        worker=None,
    ) -> PptMasterPlanningWorkflow:
        from core.ai_client import AIClient
        from core.chart_store import (
            chart_manifest_from_dict,
            chart_manifest_to_figure_manifest,
        )
        from core.report_engine import build_report_source_context

        ai = AIClient.get_instance()
        if not ai.is_available():
            raise RuntimeError(
                'PPT Master 需要已连接的真实 AI 模型；模拟降级不能用于专业报告。'
            )
        if getattr(worker, '_cancelled', False):
            raise RuntimeError('PPT Master 规划已取消')
        bridge_info = self.report_workbench_widget.get_bridge_claim_info()
        if bridge_info:
            raise RuntimeError(
                'PPT Master 当前批次尚未接纳技能桥接素材。请先清除“技能输出素材”，'
                '或明确选择内置后端；系统不会遗漏后继续。'
            )

        project_files = list(config.get('project_files') or [])
        req_file = str(config.get('req_file') or '')
        all_files = [path for path in (*project_files, req_file) if path]
        candidate = _resolve_project_root_from_files(
            all_files,
            library_root=self.get_project_library_dir(),
        )
        diagnosis = config.get('_diagnosis_record')
        if not isinstance(diagnosis, dict):
            raise RuntimeError('请先加载诊断记录。')
        stored_manifest = diagnosis.get('chart_manifest')
        record_id = str(diagnosis.get('record_id') or '').strip()
        if not stored_manifest or not record_id:
            raise RuntimeError(
                'PPT Master 需要带 record_id 和已存 chart_manifest 的诊断记录；'
                '请重新保存诊断后加载。'
            )
        charts_dir = os.path.join(
            candidate,
            '数据',
            '诊断记录',
            record_id,
            'charts',
        )
        chart_manifest = chart_manifest_from_dict(stored_manifest)
        figure_manifest = chart_manifest_to_figure_manifest(
            chart_manifest,
            charts_dir,
        )
        render_assets, required_ids = _build_report_render_assets(
            figure_manifest
        )
        if len(render_assets) > 76:
            raise RuntimeError(
                'PPT Master 每页最多接纳 2 张诊断图片；当前图片超过 76 张，'
                '无法在 40 页上限内保留叙事页。'
            )
        missing = [
            asset.host_id for asset in render_assets
            if not asset.source_path.is_file()
        ]
        if missing:
            raise RuntimeError(
                '诊断图片路径缺失，无法建立确认指纹：'
                + '、'.join(missing[:8])
            )
        required_set = set(required_ids)
        planning_assets = tuple(
            PlanningAsset(
                asset_id=asset.host_id,
                kind=PlanningAssetKind.CHART,
                semantic_label=asset.semantic_label,
                summary=f'目标章节：{asset.target}',
                required=asset.host_id in required_set,
            )
            for asset in render_assets
        )

        template_path = str(config.get('template_file') or '')
        template_workspace = None
        template_mode = TemplateMode.FREE_DESIGN
        template_summary = ''
        if template_path:
            template_workspace = prepare_ppt_master_template_workspace(
                template_path
            )
            template_mode = TemplateMode.VALIDATED_WORKSPACE
            template_summary = template_workspace.style_summary

        source_context, context_warnings = build_report_source_context(config)
        if context_warnings:
            source_context += (
                '\n\n## 资料读取警告\n'
                + '\n'.join(f'- {warning}' for warning in context_warnings)
            )
        report_title = (
            f'{Path(req_file).stem} — 专业诊断汇报'
            if req_file else '传感器数据专业诊断汇报'
        )
        slide_count = max(10, (len(planning_assets) + 1) // 2 + 2)
        input_fingerprint = fingerprint_ppt_master_inputs(
            config,
            bridge_claim_info=bridge_info,
        )
        workflow = PptMasterPlanningWorkflow(
            HostAIClientPlanningAdapter(ai),
            input_fingerprint=input_fingerprint,
            template_workspace=template_workspace,
        )
        request = PlanningRequest(
            request_id=f'ppt-{uuid.uuid4().hex[:24]}',
            report_title=report_title,
            objective='基于已加载的诊断事实形成可审核、可决策的专业技术汇报。',
            audience='项目技术负责人、试验人员与质量审核人员',
            source_context=source_context,
            requested_slide_count=slide_count,
            template_mode=template_mode,
            template_summary=template_summary,
            assets=planning_assets,
        )
        workflow.generate_outline(request)
        return workflow

    def _handle_ppt_master_planning_action(
        self,
        action: str,
        config: dict,
    ) -> None:
        workflow = self._ppt_master_workflow
        if workflow is None:
            QMessageBox.warning(
                self,
                'PPT Master 规划不存在',
                '请先生成 PPT Master 方案。',
            )
            return
        bridge_info = self.report_workbench_widget.get_bridge_claim_info()
        fingerprint = fingerprint_ppt_master_inputs(
            config,
            bridge_claim_info=bridge_info,
        )
        self.report_workbench_widget.ppt_master_confirm_btn.setEnabled(False)
        self.report_workbench_widget.outline_btn.setEnabled(False)
        self.status_bar.showMessage('正在推进 PPT Master 确认流程...')

        def _advance():
            workflow.advance(
                action,  # type: ignore[arg-type]
                current_input_fingerprint=fingerprint,
            )
            return workflow

        def _done(updated: PptMasterPlanningWorkflow):
            self._apply_ppt_master_workflow_view(updated)
            self.report_workbench_widget.outline_btn.setEnabled(True)
            self.status_bar.showMessage(updated.view().status_text)
            # TEMPORARY BATCH 3.6.6 ACCEPTANCE CAPTURE — diagnostic trace
            # Real capture now happens inside workflow.advance() at each phase.
            _capture_diag = Path(__file__).resolve().parent / "tests" / ".artifacts" / "batch-3.6.6" / "diag.txt"
            try:
                _capture_diag.parent.mkdir(parents=True, exist_ok=True)
                _phase = updated.snapshot.phase.value if updated.snapshot else "NO_SNAPSHOT"
                _flag = os.environ.get("DPP_BATCH_366_CAPTURE_PLAN", "UNSET")
                _capture_diag.write_text(
                    f"hook_reached=1\nenv_flag={_flag}\nphase={_phase}\n"
                    f"snapshot_exists={updated.snapshot is not None}\n",
                    encoding="utf-8",
                )
            except Exception:
                pass
            # END TEMPORARY CAPTURE

        def _error(message: str):
            self.report_workbench_widget.outline_btn.setEnabled(True)
            try:
                self._apply_ppt_master_workflow_view(workflow)
            except Exception:
                pass
            self.status_bar.showMessage('PPT Master 确认流程失败')
            QMessageBox.warning(
                self,
                'PPT Master 确认失败',
                message.split('\nTraceback', 1)[0].strip(),
            )

        self._ppt_master_planning_worker = ReportWorker(_advance)
        self._ppt_master_planning_worker.finished.connect(_done)
        self._ppt_master_planning_worker.error.connect(_error)
        self._ppt_master_planning_worker.start()

    def _apply_ppt_master_workflow_view(
        self,
        workflow: PptMasterPlanningWorkflow,
    ) -> None:
        view = workflow.view()
        self.report_workbench_widget.set_ppt_master_workflow_view(
            status_text=view.status_text,
            preview_markdown=view.preview_markdown,
            next_action=view.next_action or '',
            next_action_label=view.next_action_label,
            ready_for_authoring=view.ready_for_authoring,
            valid_for_current_inputs=view.valid_for_current_inputs,
        )

    # ═══════════════════════════════════════════════
    # 从已存诊断加载 — 浏览 diagnoses/ 目录
    # ═══════════════════════════════════════════════

    def _handle_load_diagnosis(self) -> None:
        """从关联资料反推项目根 → 扫 数据/诊断记录/ → 用户选择加载。"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QListWidget, QPushButton, QHBoxLayout, QLabel

        config = self.report_workbench_widget.get_config()
        project_files: list[str] = config.get('project_files', [])

        # ★ 强约束: 关联资料为空 → 硬拦 (只看 project_files, 不被 req_file 僵尸值绕过)
        if not project_files:
            QMessageBox.warning(self, '缺少项目关联资料',
                '请先在报告工作台添加项目关联资料，再加载诊断记录。')
            return

        # ★ 反推项目根 (含 req_file 兜底，供 resolver 有更多路径可推)
        req_file = config.get('req_file', '')
        all_files = [f for f in project_files + ([req_file] if req_file else []) if f]

        # ★ 反推项目根
        try:
            project_root = _resolve_project_root_from_files(
                all_files, library_root=self.get_project_library_dir())
        except ValueError as e:
            QMessageBox.warning(self, '无法定位项目', str(e))
            return

        diag_dir = os.path.join(project_root, '数据', '诊断记录')
        if not os.path.isdir(diag_dir):
            QMessageBox.information(self, '暂无诊断记录',
                f'该项目暂无诊断记录。\n\n'
                f'请在 AI 诊断页运行诊断后，点击「保存诊断到项目」保存到此项目。')
            return

        # 扫诊断 JSON
        entries: list[tuple[str, str, int]] = []  # (stem, path, size)
        try:
            for f_name in sorted(os.listdir(diag_dir)):
                if f_name.endswith('.json'):
                    f_path = os.path.join(diag_dir, f_name)
                    stem = os.path.splitext(f_name)[0]
                    sz = os.path.getsize(f_path)
                    entries.append((stem, f_path, sz))
        except OSError:
            pass

        if not entries:
            QMessageBox.information(self, '暂无诊断记录',
                f'该项目暂无诊断记录。\n\n'
                f'请在 AI 诊断页运行诊断后，点击「保存诊断到项目」保存到此项目。')
            return

        dlg = QDialog(self)
        dlg.setWindowTitle('从已存诊断加载')
        dlg.setMinimumWidth(600)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(f'项目: {os.path.basename(project_root)}\n选择一份已保存的诊断结果:'))

        lst = QListWidget()
        for stem, _path, sz in entries:
            lst.addItem(f'{stem}  ({sz // 1024}KB)')
        layout.addWidget(lst)

        btn_row = QHBoxLayout()
        load_btn = QPushButton('加载')
        load_btn.setStyleSheet(
            'QPushButton { background: #722ed1; color: white; padding: 8px 16px; '
            'border-radius: 4px; font-weight: bold; }'
        )
        cancel_btn = QPushButton('取消')

        def on_load():
            row = lst.currentRow()
            if row < 0 or row >= len(entries):
                return
            f_path = entries[row][1]
            try:
                with open(f_path, 'r', encoding='utf-8') as fh:
                    rec = json.load(fh)
            except Exception as e:
                QMessageBox.warning(self, '读取失败', f'无法读取诊断文件:\n{e}')
                return

            sv = str(rec.get('schema_version', '1.0'))
            if sv not in ('1.0', '1.1', '1.2'):
                QMessageBox.warning(self, '版本不兼容',
                    f"该记录 schema 版本为 {sv}，当前只支持 1.0 / 1.1")
                return
            from core.report_engine import summarize_diagnosis_record
            self.report_workbench_widget.set_diagnosis_record(rec)
            s = summarize_diagnosis_record(rec)
            QMessageBox.information(self, '成功',
                f"已加载诊断记录 v{sv}\n时间: {rec.get('timestamp')}\n"
                f"诊断来源: {s['source_label']}\n"
                f"传感器数: {s['sensor_count']}\n"
                f"KB 规则: {s['kb_count']} 条\n"
                f"多智能体报告: {'有' if s['has_multi_agent'] else '无'}")
            dlg.accept()

        load_btn.clicked.connect(on_load)
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(load_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        dlg.exec()

    # ═══════════════════════════════════════════════
    # 完整报告生成 — 后台 AI + 渲染
    # ═══════════════════════════════════════════════

    # ═══════════════════════════════════════════════════
    # 资料纳入情况 — 代码确定性直插 docx 末尾 (不经过 LLM)
    # ═══════════════════════════════════════════════════

    @staticmethod
    def _append_inclusion_footer(
        docx_path: str, report_type: str,
        warnings: list[str], diagnosis_loaded: bool,
    ) -> None:
        """在已生成的 docx 文件末尾追加「资料纳入情况」段。

        此段由代码直写，不走 LLM — 用于去静默验收。
        失败时不影响已存报告，仅追加进 warnings 列表后被模态对话框展示。
        """
        import traceback
        if report_type != 'word':
            return  # PPT 暂不处理
        try:
            doc = Document(docx_path)
            doc.add_heading('资料纳入情况', level=1)

            # 诊断数据状态
            if diagnosis_loaded:
                doc.add_paragraph('诊断数据: 已加载 — 本报告包含诊断结论。')
            else:
                p = doc.add_paragraph(
                    '诊断数据: 未加载 — 本报告不含诊断结论。'
                    '如需包含诊断结论，请先在「报告生成工作台」中'
                    '点击「从已存诊断加载」载入诊断记录，再重新生成报告。'
                )
                for run in p.runs:
                    run.font.color.rgb = RGBColor(0xFF, 0x4D, 0x4F)  # 红色提示

            # 关联资料读入情况
            if warnings:
                doc.add_heading('读入警告', level=2)
                for w in warnings:
                    p = doc.add_paragraph(f'• {w}')
                    for run in p.runs:
                        run.font.color.rgb = RGBColor(0xFF, 0x4D, 0x4F)
            else:
                doc.add_paragraph('所有关联资料均已成功读入，无警告。')

            doc.save(docx_path)
        except Exception:
            warnings.append(
                f'资料纳入情况段写入失败 — {traceback.format_exc()[:120]}'
            )
            # 不重抛: 主报告已保存完好, 仅丢失此追加段

    def _handle_full_report_generation(self, config: dict, outline: str):
        """后台生成结构化报告 + 渲染保存 + 自动打开输出目录."""
        report_type = config.get('report_type', 'word')
        ext = '.pptx' if report_type == 'ppt' else '.docx'
        type_label = 'PPT演示' if report_type == 'ppt' else 'Word报告'
        provider_selection = config.get('report_provider') or {}
        provider_id = str(
            provider_selection.get('provider_id') or BUILTIN_PROVIDER_ID
        )
        provider_version = str(
            provider_selection.get('provider_version')
            or BUILTIN_PROVIDER_VERSION
        )
        if provider_id == PPT_MASTER_PROVIDER_ID:
            from core.ai_client import AIClient

            ai = AIClient.get_instance()
            if not ai.is_available():
                QMessageBox.warning(
                    self,
                    'PPT Master AI 模型不可用',
                    'PPT Master 不使用模拟降级。请先连接在线或本地模型后重试。',
                )
                return
            generate_fn = ai.get_generate_fn(enable_thinking=False)
        else:
            generate_fn = self._get_generate_fn()
        template_mode = (
            'normalized' if config.get('template_file') else 'none'
        )
        structured_report_override: dict[str, object] | None = None
        try:
            if provider_id == PPT_MASTER_PROVIDER_ID:
                if report_type != 'ppt':
                    raise PptMasterWorkflowError(
                        'report_type_unsupported',
                        'PPT Master 仅支持 PPT 演示汇报。',
                    )
                workflow = self._ppt_master_workflow
                if workflow is None:
                    raise PptMasterWorkflowError(
                        'workflow_not_started',
                        '请先生成并确认 PPT Master 方案。',
                    )
                bridge_info = self.report_workbench_widget.get_bridge_claim_info()
                workflow.require_current_inputs(
                    fingerprint_ppt_master_inputs(
                        config,
                        bridge_claim_info=bridge_info,
                    )
                )
                if not workflow.ready_for_authoring or workflow.snapshot is None:
                    raise PptMasterWorkflowError(
                        'plan_not_confirmed',
                        '请依次确认大纲、设计和逐页计划。',
                    )
                from utils.app_paths import get_app_data_root

                runner = ControlledToolRunner(
                    get_app_data_root()
                    / 'toolchains'
                    / 'ppt-master'
                    / 'runs'
                )
                selected_provider = PptMasterReportRenderProvider(
                    planning_snapshot=workflow.snapshot,
                    runner=runner,
                    authoring_adapter=HostAIPptMasterAuthoringAdapter(),
                    template_workspace=workflow.template_workspace,
                )
                structured_report_override = workflow.structured_report()
            else:
                selected_provider = self._report_provider_controller.create_provider(
                    provider_id=provider_id,
                    provider_version=provider_version,
                    report_type=report_type,
                    template_mode=template_mode,
                )
            render_orchestrator = ReportRenderOrchestrator(
                providers=(selected_provider,),
                default_provider_id=provider_id,
            )
        except Exception as error:
            QMessageBox.warning(
                self,
                '报告后端不可用',
                str(error),
            )
            self.status_bar.showMessage(
                f'报告生成取消: 后端 {provider_id}@{provider_version} 不可用'
            )
            self._refresh_report_provider_options()
            return

        self.report_workbench_widget.set_generation_running(True)
        self.status_bar.showMessage(
            f'正在生成完整报告... 后端 {provider_id}@{provider_version}'
        )

        # 构建输出路径：项目根/报告 (与数据/方案同级的一级目录)
        project_files: list[str] = config.get('project_files', [])

        # ★ 强约束: 关联资料为空 → 硬拦 (只看 project_files, 不被 req_file 僵尸值绕过)
        if not project_files:
            QMessageBox.warning(self, '缺少项目关联资料',
                '请先在报告工作台添加项目关联资料，再生成完整报告。')
            self.report_workbench_widget.set_generation_running(False)
            self.status_bar.showMessage('报告生成取消: 缺少项目关联资料')
            return

        req_file = config.get('req_file', '')
        all_files = [f for f in project_files + ([req_file] if req_file else []) if f]

        try:
            candidate = _resolve_project_root_from_files(
                all_files, library_root=self.get_project_library_dir())
        except ValueError as e:
            QMessageBox.warning(self, '无法生成报告', str(e))
            self.report_workbench_widget.set_generation_running(False)
            self.status_bar.showMessage('报告生成取消')
            return

        report_dir = os.path.join(candidate, '报告')

        os.makedirs(report_dir, exist_ok=True)

        # ── Batch 3.3.2: Bridge claim for report ──
        bridge_claim = self._claim_bridge_for_report()
        bridge_workspace = None
        bridge_assets = ()
        bridge_req_id = ""
        bridge_gen = 0
        if bridge_claim is not None:
            bridge_workspace = bridge_claim.get("workspace_path")
            bridge_assets = bridge_claim.get("bridge_assets", ())
            bridge_req_id = bridge_claim.get("request_id", "")
            bridge_gen = bridge_claim.get("generation", 0)

        # ── Safe unique final filename (Batch 3.3.1B: atomic no-clobber) ──
        utc_ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        uid = uuid.uuid4().hex
        project_name = os.path.splitext(os.path.basename(req_file))[0] if req_file else '数据分析报告'
        # Sanitize project_name: no path separators, no reserved device names
        safe_base = re.sub(r'[\\/:*?"<>|]', '_', project_name)
        final_filename = f'{safe_base}_{utc_ts}_{uid}{ext}'
        final_output_path = os.path.join(report_dir, final_filename)

        template_path = config.get('template_file', '')

        # 项目根目录（用于拼接图片绝对路径）
        project_dir = os.path.dirname(os.path.abspath(req_file)) if req_file else ''

        def _build_and_save(worker=None):
            return _execute_report_build_transaction(
                config=config,
                outline=outline,
                report_type=report_type,
                generate_fn=generate_fn,
                template_path=template_path,
                final_output_path=final_output_path,
                report_dir=report_dir,
                project_dir=project_dir,
                candidate=candidate,
                worker=worker,
                bridge_workspace=bridge_workspace,
                bridge_assets=bridge_assets,
                _provider=self,
                render_orchestrator=render_orchestrator,
                structured_report_override=structured_report_override,
            )

        def _on_report_done(result: dict):
            self._disconnect_report_cancel_handler()
            self._active_report_provider = None
            path: str = result['path']
            warnings: list[str] = result.get('warnings', [])
            diagnosis_loaded: bool = result.get('diagnosis_loaded', False)
            provenance = result.get('provider_provenance') or {}
            actual_provider = (
                f"{provenance.get('provider_id', provider_id)}@"
                f"{provenance.get('provider_version', provider_version)}"
            )

            # Batch 3.3.2: Finish bridge generation as SUCCEEDED
            if bridge_req_id and bridge_gen:
                self._finish_bridge_generation(bridge_req_id, bridge_gen, "succeeded")

            self.report_workbench_widget.set_generation_running(False)
            self.status_bar.showMessage(
                f'报告已保存: {os.path.basename(path)} '
                f'(后端 {actual_provider})'
            )
            _REPORT_LOGGER.info(
                "Report committed: provider=%s output=%s",
                actual_provider,
                os.path.basename(path),
            )

            # 组装模态对话框文案
            lines = [
                '报告已保存至:',
                path,
                '',
                f'实际生成后端: {actual_provider}',
                '',
            ]
            if not diagnosis_loaded:
                lines.append(
                    '诊断数据: 未加载 — 本报告不含诊断结论。'
                    '如需包含诊断结论，请在生成前点击「从已存诊断加载」。'
                )
            if warnings:
                lines.append(f'资料纳入警告 ({len(warnings)} 项):')
                for w in warnings:
                    lines.append(f'  • {w}')
            else:
                lines.append('所有关联资料均已成功读入。')

            QMessageBox.information(self, '生成成功', '\n'.join(lines))
            # 自动打开报告所在的真实保存目录
            try:
                os.startfile(os.path.dirname(path))
            except Exception:
                pass

        def _on_report_error(msg: str):
            self._disconnect_report_cancel_handler()
            self._active_report_provider = None
            cancelled = (
                '报告生成已取消' in msg
                or 'status=cancelled' in msg
            )
            # Batch 3.3.2: Finish bridge generation as FAILED
            if bridge_req_id and bridge_gen:
                self._finish_bridge_generation(
                    bridge_req_id,
                    bridge_gen,
                    "cancelled" if cancelled else "failed",
                )

            self.report_workbench_widget.set_generation_running(False)
            if cancelled:
                self.status_bar.showMessage(
                    f'报告生成已取消: 后端 {provider_id}@{provider_version}'
                )
                return
            self.status_bar.showMessage(
                f'报告生成失败: 后端 {provider_id}@{provider_version}'
            )

            if "RuntimeError:" in msg:
                # 安全错误 (含已收集 warnings) — 友好展示, 不甩栈
                friendly = msg.split("\nTraceback")[0].strip() if "\nTraceback" in msg else msg.strip()
                QMessageBox.warning(self, '报告生成失败', friendly)
            else:
                QMessageBox.critical(self, '报告生成错误', msg)

        def _on_report_progress(progress: dict):
            stage = progress.get('stage', '')
            if stage == 'section':
                cur = progress.get('current', 0)
                tot = progress.get('total', 0)
                heading = progress.get('heading', '')
                msg = f'⏳ 生成中… 第{cur}/{tot}节: {heading}'
                self.status_bar.showMessage(msg)

        # Custom cancel wrapper that also finishes bridge as CANCELLED
        def _cancel_with_bridge():
            if bridge_req_id and bridge_gen:
                self._finish_bridge_generation(bridge_req_id, bridge_gen, "cancelled")
            cancel_provider = getattr(selected_provider, 'cancel', None)
            if callable(cancel_provider):
                cancel_provider()
            self._report_worker.cancel()

        self._disconnect_report_cancel_handler()
        self._active_report_provider = selected_provider
        self._report_worker = ReportWorker(_build_and_save)
        self._report_worker.finished.connect(_on_report_done)
        self._report_worker.error.connect(_on_report_error)
        self._report_worker.progress.connect(_on_report_progress)
        self._report_cancel_handler = _cancel_with_bridge
        self.report_workbench_widget.cancel_requested.connect(
            self._report_cancel_handler
        )
        self._report_worker.start()

    # ═══════════════════════════════════════════════════════════
    # Report Bridge handlers (Batch 3.3.2)
    # ═══════════════════════════════════════════════════════════

    def _handle_bridge_send(self, selections: list) -> None:
        """Handle send-to-report from Skill Tab.

        Checks current bridge state and either starts a new prepare
        or prompts for READY replacement.
        """
        if self._bridge_closing:
            return

        state = self._bridge_controller.state
        # IDLE/RELEASED/FAILED/CANCELLED/SUCCEEDED → start new prepare
        state_str = state.value if hasattr(state, 'value') else str(state)

        if state_str in ("idle", "released", "failed", "cancelled", "succeeded"):
            self._bridge_controller.start_preparation(selections)
            return

        # PREPARING → reject duplicate
        if state_str == "preparing":
            QMessageBox.information(
                self, '素材准备中',
                '正在准备素材，请等待完成后再发送新的素材。'
            )
            return

        # GENERATING → reject
        if state_str == "generating":
            QMessageBox.information(
                self, '报告生成中',
                '报告正在生成，无法替换素材。'
            )
            return

        # READY → confirm replacement
        if state_str == "ready":
            reply = QMessageBox.question(
                self, '替换确认',
                '当前已有准备完成的报告素材。\n替换后旧素材将被释放。是否继续？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return  # User cancelled — old READY lease stays

            # Explicit discard before new prepare
            req_id = self._bridge_controller.request_id or ""
            gen = self._bridge_controller.generation
            discarded = self._bridge_controller.discard_ready_generation(req_id, gen)
            if not discarded:
                QMessageBox.warning(
                    self, '操作失败',
                    '释放旧素材失败，请稍后重试。'
                )
                return

            # Start new preparation
            self._bridge_controller.start_preparation(selections)

    def _on_bridge_result_ready(self, result: ReportBridgePublicResult) -> None:
        """Receive bridge result from controller — update Workbench."""
        if self._bridge_closing:
            return

        status = result.status.value if hasattr(result.status, 'value') else str(result.status)
        safe_error = result.safe_error_message or ""
        assets = result.assets

        self.report_workbench_widget.set_bridge_status(
            status=status,
            assets=assets,
            safe_error=safe_error,
            request_id=result.request_id,
            generation=result.generation,
        )

    def _handle_bridge_clear(self) -> None:
        """Handle 'clear assets' button — discard READY generation."""
        info = self.report_workbench_widget.get_bridge_claim_info()
        if not info:
            return
        req_id = info.get("request_id", "")
        gen = info.get("generation", 0)
        if not req_id or not gen:
            return

        discarded = self._bridge_controller.discard_ready_generation(req_id, gen)
        if discarded:
            self.report_workbench_widget.set_bridge_status(status="released")
        else:
            QMessageBox.warning(
                self, '操作失败',
                '清除素材失败，素材可能已用于报告生成。'
            )

    def _handle_bridge_cancel_prepare(self) -> None:
        """Handle 'cancel prepare' button — set cancel on controller."""
        self._bridge_controller.cancel()

    # ═══════════════════════════════════════════════════════════
    # Bridge-aware report generation (Batch 3.3.2)
    # ═══════════════════════════════════════════════════════════

    def _claim_bridge_for_report(self) -> dict | None:
        """Claim READY bridge generation for report.

        Returns dict with workspace_path, bridge_assets if claim succeeds.
        Returns None if no READY assets or claim fails.
        """
        info = self.report_workbench_widget.get_bridge_claim_info()
        if not info:
            return None

        req_id = info.get("request_id", "")
        gen = info.get("generation", 0)
        if not req_id or not gen:
            return None

        generation_input = self._bridge_controller.claim_ready_generation(req_id, gen)
        if generation_input is None:
            QMessageBox.warning(
                self, '素材已失效',
                '报告素材已失效或已被使用，请重新选择素材。'
            )
            self.report_workbench_widget.set_bridge_status(status="released")
            return None

        return {
            "request_id": req_id,
            "generation": gen,
            "workspace_path": str(generation_input.workspace_path),
            "bridge_assets": generation_input.assets,
        }

    def _finish_bridge_generation(self, request_id: str, generation: int,
                                   status_str: str) -> None:
        """Finish bridge generation with terminal status."""
        status_map = {
            "succeeded": ReportBridgeStatus.SUCCEEDED,
            "failed": ReportBridgeStatus.FAILED,
            "cancelled": ReportBridgeStatus.CANCELLED,
        }
        status = status_map.get(status_str)
        if status is None:
            return
        self._bridge_controller.finish_generation(
            request_id, generation, status=status,
        )

    def create_ai_diagnosis_page(self):
        """AI诊断页面"""
        self.ai_diagnosis_widget = AiDiagnosisWidget(self)
        return self.ai_diagnosis_widget

    # ═══════════════════════════════════════════════════════════
    # 项目资料库 — 三级目录 (Phase 3)
    # ═══════════════════════════════════════════════════════════

    LIBRARY_FOLDER_NAME = "项目资料库"

    @staticmethod
    def _get_project_library_dir(projects_dir: str) -> str:
        """返回项目资料库一级容器路径: <projects_dir>/项目资料库/。"""
        return os.path.join(projects_dir, DataProcessorWindow.LIBRARY_FOLDER_NAME)

    @staticmethod
    def get_software_root_dir() -> str:
        """软件根目录 (main.py 所在目录)。"""
        return os.path.dirname(os.path.abspath(__file__))

    def get_project_library_dir(self) -> str:
        """SSOT: 返回实际存在的项目资料库路径。

        确定性：根植软件根目录/<项目资料库>/。与项目资料管理页同一解析。
        不存在时创建并返回。
        """
        root = DataProcessorWindow.get_software_root_dir()
        lib_dir = os.path.join(root, DataProcessorWindow.LIBRARY_FOLDER_NAME)
        os.makedirs(lib_dir, exist_ok=True)
        return lib_dir

    def _ensure_library_exists(self, projects_dir: str) -> str:
        """确保项目资料库目录存在，返回其路径。"""
        lib_dir = self._get_project_library_dir(projects_dir)
        os.makedirs(lib_dir, exist_ok=True)
        return lib_dir

    def _migrate_projects_to_library(self, projects_dir: str, known_projects: list[str]) -> int:
        """将 <projects_dir>/ 根目录下已登记的项目迁移到 项目资料库/ 下。

        幂等: 已在库下的跳过；未登记的文件夹不动（避免误伤）。
        返回迁移数量。
        """
        import shutil
        lib_dir = self._ensure_library_exists(projects_dir)
        migrated = 0
        for proj_name in known_projects:
            old_path = os.path.join(projects_dir, proj_name)
            new_path = os.path.join(lib_dir, proj_name)
            if not os.path.isdir(old_path):
                continue  # 项目不在旧位置
            if os.path.exists(new_path):
                continue  # 已在库下，跳过
            try:
                shutil.move(old_path, new_path)
                migrated += 1
            except Exception as e:
                print(f"迁移项目 '{proj_name}' 失败: {e}")
        return migrated

    def load_project_list(self):
        """加载项目列表 (从软件根/项目资料库/ 扫描)。"""
        lib_dir = self.get_project_library_dir()
        items: list[str] = []
        if os.path.isdir(lib_dir):
            try:
                for entry in os.listdir(lib_dir):
                    if os.path.isdir(os.path.join(lib_dir, entry)):
                        items.append(entry)
            except Exception as e:
                print(f"加载项目列表失败: {e}")
        self.project_tab_widget.set_project_list(items)

    def on_project_selected(self, project_name: str):
        """选择项目时加载内容"""
        lib_dir = self.get_project_library_dir()
        self.current_project_path = os.path.join(lib_dir, project_name)
        self.project_tab_widget.set_current_project_label(project_name, exists=True)
        self.project_tab_widget.load_project_tree(self.current_project_path)

    def on_new_project(self):
        """新建项目 — 直接在软件根/项目资料库/下创建。"""
        # 输入项目名称
        project_name, ok = QInputDialog.getText(self, '新建项目', '请输入项目名称:')
        if not ok or not project_name.strip():
            return

        project_name = project_name.strip()
        lib_dir = self.get_project_library_dir()
        project_path = os.path.join(lib_dir, project_name)

        if os.path.exists(project_path):
            QMessageBox.warning(self, '警告', '项目已存在!')
            return

        try:
            # 创建项目文件夹 (二级目录)
            os.makedirs(project_path)

            # 创建默认子文件夹 (三级目录: 图片/图纸/数据/方案/总结/视频/其它)
            default_folders = ['图片', '图纸', '数据', '方案', '报告', '视频', '其它']
            for folder in default_folders:
                os.makedirs(os.path.join(project_path, folder))

            # 创建README文件
            readme_path = os.path.join(project_path, '项目说明.txt')
            with open(readme_path, 'w', encoding='utf-8') as f:
                f.write(f"项目名称: {project_name}\n")
                f.write(f"创建时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write("项目文件夹结构:\n")
                for folder in default_folders:
                    f.write(f"- {folder}/\n")

            self.load_project_list()
            QMessageBox.information(self, '成功', f'项目 "{project_name}" 创建成功!')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'创建项目失败: {str(e)}')

    def on_delete_project(self, project_name: str):
        """删除项目"""
        reply = QMessageBox.question(
            self, '确认删除',
            f'确定删除项目 "{project_name}" 吗?\n此操作不可恢复!',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            import shutil
            lib_dir = self.get_project_library_dir()
            shutil.rmtree(os.path.join(lib_dir, project_name))
            self.load_project_list()
            self.project_tab_widget.clear_tree()
            self.project_tab_widget.set_current_project_label("")
            QMessageBox.information(self, '成功', f'项目 "{project_name}" 已删除')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'删除项目失败: {str(e)}')

    def on_open_project_folder(self):
        """用资源管理器打开项目文件夹"""
        current_item = self.project_tab_widget.project_list_widget.currentItem()
        if not current_item:
            QMessageBox.warning(self, '提示', '请先选择要打开的项目')
            return

        project_name = current_item.text()
        lib_dir = self.get_project_library_dir()
        project_path = os.path.join(lib_dir, project_name)
        if os.path.exists(project_path):
            os.startfile(project_path)

    def on_add_folder_to_project(self):
        """在项目中新建文件夹"""
        if not hasattr(self, 'current_project_path') or not os.path.exists(self.current_project_path):
            QMessageBox.warning(self, '提示', '请先选择项目')
            return

        folder_name, ok = QInputDialog.getText(self, '新建文件夹', '请输入文件夹名称:')
        if not ok or not folder_name.strip():
            return

        folder_path = os.path.join(self.current_project_path, folder_name.strip())
        if os.path.exists(folder_path):
            QMessageBox.warning(self, '警告', '文件夹已存在!')
            return

        try:
            os.makedirs(folder_path)
            self.project_tab_widget.load_project_tree(self.current_project_path)
            QMessageBox.information(self, '成功', f'文件夹 "{folder_name.strip()}" 创建成功!')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'创建文件夹失败: {str(e)}')

    def on_add_file_to_project(self):
        """在项目中添加文件"""
        if not hasattr(self, 'current_project_path') or not os.path.exists(self.current_project_path):
            QMessageBox.warning(self, '提示', '请先选择项目')
            return

        settings = QSettings('DataProcessor', 'Pro')
        last_dir = settings.value('project_file_last_dir', self.current_project_path)

        file_paths, _ = QFileDialog.getOpenFileNames(
            self, '选择要添加的文件', last_dir,
            '所有文件 (*.*);;文档 (*.doc *.docx *.pdf *.txt);;图片 (*.png *.jpg *.jpeg *.gif);;视频 (*.mp4 *.avi *.mov)'
        )
        if not file_paths:
            return

        try:
            settings.setValue('project_file_last_dir', os.path.dirname(file_paths[0]))

            tree = self.project_tab_widget.project_tree_widget
            current_item = tree.currentItem()
            target_dir = self.current_project_path
            if current_item and current_item.text(1) == "文件夹":
                target_dir = os.path.join(self.current_project_path, current_item.text(0))

            import shutil
            for fp in file_paths:
                shutil.copy2(fp, os.path.join(target_dir, os.path.basename(fp)))

            self.project_tab_widget.load_project_tree(self.current_project_path)
            QMessageBox.information(self, '成功', f'已添加 {len(file_paths)} 个文件!')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'添加文件失败: {str(e)}')

    def on_delete_project_item(self):
        """删除选中的文件或文件夹"""
        tree = self.project_tab_widget.project_tree_widget
        current_item = tree.currentItem()
        if not current_item:
            QMessageBox.warning(self, '提示', '请先选择要删除的内容')
            return

        item_name = current_item.text(0)
        item_type = current_item.text(1)

        if item_type == "文件夹" and item_name == os.path.basename(self.current_project_path):
            QMessageBox.warning(self, '警告', '不能删除项目根目录')
            return

        reply = QMessageBox.question(
            self, '确认删除',
            f'确定删除 {"文件夹" if item_type == "文件夹" else "文件"} "{item_name}" 吗?\n此操作不可恢复!',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            item_path = os.path.join(self.current_project_path, item_name)
            if os.path.isdir(item_path):
                import shutil
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)

            self.project_tab_widget.load_project_tree(self.current_project_path)
            QMessageBox.information(self, '成功', f'"{item_name}" 已删除')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'删除失败: {str(e)}')

    def on_open_project_item(self):
        """打开选中的文件"""
        current_item = self.project_tab_widget.project_tree_widget.currentItem()
        if not current_item:
            QMessageBox.warning(self, '提示', '请先选择要打开的文件')
            return

        item_name = current_item.text(0)
        item_type = current_item.text(1)

        if item_type == "文件夹":
            item_path = os.path.join(self.current_project_path, item_name)
            os.startfile(item_path)
        else:
            item_path = os.path.join(self.current_project_path, item_name)
            if os.path.exists(item_path):
                os.startfile(item_path)
            else:
                QMessageBox.warning(self, '错误', '文件不存在')

    def refresh_project_files(self):
        """刷新项目文件列表"""
        self.load_project_list()

    def clear_all_project_files(self):
        """清空所有项目文件"""
        self.project_tab_widget.set_project_list([])
        self.project_tab_widget.clear_tree()
        self.project_tab_widget.set_current_project_label("")

    # ============ [wiki_tab.py] 知识库管理页面已提取 ============

    # ============ [clipper_tab.py] 网络剪藏页面已提取 ============

    # ============ [skill_tab.py] 技能插件中心已提取 ============

    def on_info_menu_changed(self, row):
        """切换成果输出功能页面"""
        self.report_content_stack.setCurrentIndex(row)

    # ============ 全局配置操作 ============

    def save_config(self):
        """保存当前配置到文件（四个模块完整状态）"""
        file_path, _ = QFileDialog.getSaveFileName(
            self, '保存配置', '', 'JSON Files (*.json)'
        )
        if not file_path:
            return

        try:
            import json
            import numpy as np

            config = {
                'version': '2.0',
                'fbgs': [],
                'sensors': [],
                'global_parameters': {
                    name: {'value': p.value, 'unit': p.unit, 'description': p.description}
                    for name, p in self.state.global_parameters.items()
                },
                'project_files': [],
                'data_tab': {},
                'cleaning_tab': {},
                'analysis_tab': {},
            }

            # ---- 光纤公式配置模块 ----
            for fbg in self.sensor_system.fbgs:
                config['fbgs'].append({
                    'id': fbg.id,
                    'channel': fbg.channel,
                    'wavelength_min': fbg.wavelength_min,
                    'wavelength_max': fbg.wavelength_max,
                })

            for sensor in self.sensor_system.sensors:
                config['sensors'].append({
                    'id': sensor.id,
                    'sensor_type': sensor.sensor_type,
                    'formula': sensor.formula,
                    'constants': sensor.constants,
                    'active': sensor.active,
                    'decoupling_config': sensor.decoupling_config,
                    'location': sensor.location,
                })

            # ---- 数据文件模块 ----
            config['data_tab'] = {
                'file_path': self.sampled_file_path,
                'template_id': self.current_template.id if self.current_template else None,
                'template_name': self.current_template.name if self.current_template else None,
                'file_header_lines': self.file_header_lines,
                'current_columns': self.current_columns,
            }

            # ---- 数据清洗模块 ----
            config['cleaning_tab'] = self.cleaning_tab_widget.get_config()

            # ---- 数据分析模块 ----
            # 保存传感器计算结果（从 SSOT 读取）
            serializable_results = {}
            for key, values in self.state.analysis_tab.sensor_results.items():
                arr = np.array(list(values), dtype=np.float64)
                arr[np.isnan(arr)] = None  # NaN -> null for JSON
                serializable_results[key] = [None if v is None else float(v) for v in arr]

            config['analysis_tab'] = {
                'data_source': self.data_source_combo.currentText(),
                'range_type': self.range_type_combo.currentText(),
                'range_start': self.range_start.value(),
                'range_end': self.range_end.value(),
                'sensor_results': serializable_results,
            }

            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)

            # 统计摘要
            data_info = f'数据文件: {os.path.basename(self.sampled_file_path) if self.sampled_file_path else "无"}'
            result_count = len(self.state.analysis_tab.sensor_results)
            QMessageBox.information(self, '成功',
                f'配置已保存到:\n{file_path}\n\n'
                f'  FBG: {len(config["fbgs"])} 个\n'
                f'  传感器: {len(config["sensors"])} 个\n'
                f'  全局参数: {len(config["global_parameters"])} 个\n'
                f'  {data_info}\n'
                f'  清洗规则: 已保存\n'
                f'  分析结果: {result_count} 个结果列')

        except Exception as e:
            import traceback
            QMessageBox.critical(self, '错误', f'保存配置失败: {str(e)}\n\n{traceback.format_exc()}')

    def load_config(self):
        """从文件加载配置（四个模块完整恢复）"""
        last_dir = QSettings('DataProcessor', 'Pro').value('config_load_last_dir', '')
        file_path, _ = QFileDialog.getOpenFileName(
            self, '读取配置', last_dir, 'JSON Files (*.json);;All Files (*)'
        )
        if file_path:
            QSettings('DataProcessor', 'Pro').setValue('config_load_last_dir', os.path.dirname(file_path))
        if not file_path:
            return

        try:
            import json
            import numpy as np
            with open(file_path, 'r', encoding='utf-8') as f:
                config = json.load(f)

            fbg_count, sensor_count = 0, 0
            data_loaded, cleaning_restored, analysis_restored = False, False, False
            messages = []

            # ---- 光纤公式配置模块 ----
            self.sensor_system.fbgs.clear()
            for fbg_data in config.get('fbgs', []):
                wl_min = fbg_data.get('wavelength_min', fbg_data.get('k1'))
                wl_max = fbg_data.get('wavelength_max', fbg_data.get('k2'))
                fbg = FBG(fbg_data['id'], fbg_data['channel'], wl_min, wl_max)
                self.sensor_system.add_fbg(fbg)
                fbg_count += 1

            self.sensor_system.sensors.clear()

            # 加载全局参数 (SSOT)，旧配置自动迁移
            if 'global_parameters' in config:
                raw = config['global_parameters']
                self.sensor_system.global_parameters = raw
                # 同步到 AppState
                restored_gp = {}
                for name, entry in raw.items():
                    restored_gp[name] = GlobalParameter(
                        name=name,
                        value=entry.get('value', 0) if isinstance(entry, dict) else float(entry),
                        unit=entry.get('unit', '') if isinstance(entry, dict) else '',
                        description=entry.get('description', '') if isinstance(entry, dict) else '',
                    )
                self.state = self.state.with_global_parameters(restored_gp)
            else:
                # 自动迁移: 扫描所有传感器，提取同名同值的共用常量
                const_usage = {}  # name -> [(value, sensor_id), ...]
                for s in config.get('sensors', []):
                    for name, value in s.get('constants', {}).items():
                        const_usage.setdefault(name, []).append((value, s['id']))
                migrated = {}
                for name, entries in const_usage.items():
                    if len(entries) >= 2:
                        values = [v for v, _ in entries]
                        if len(set(values)) == 1:
                            migrated[name] = values[0]
                if migrated:
                    print(f"[迁移] 从传感器局部常量提取全局参数: {migrated}")
                self.sensor_system.global_parameters = migrated
                # 迁移结果同步到 AppState
                restored_gp = {}
                for name, value in migrated.items():
                    restored_gp[name] = GlobalParameter(
                        name=name, value=value if isinstance(value, (int, float)) else 0,
                        unit='', description='',
                    )
                self.state = self.state.with_global_parameters(restored_gp)

            for sensor_data in config.get('sensors', []):
                sensor_constants = sensor_data.get('constants', {})
                # 从局部常量中剔除已迁移到全局的参数
                if self.sensor_system.global_parameters:
                    sensor_constants = {
                        k: v for k, v in sensor_constants.items()
                        if k not in self.sensor_system.global_parameters
                    }
                sensor = Sensor(
                    sensor_data['id'],
                    sensor_data['sensor_type'],
                    sensor_data.get('formula'),
                    sensor_constants,
                    sensor_data.get('active', True),
                    sensor_data.get('decoupling_config'),
                    sensor_data.get('location', ''),
                )
                self.sensor_system.add_sensor(sensor)
                sensor_count += 1

            self.sensor_tab_widget.set_fbg_list(self.sensor_system.fbgs)
            self.sensor_tab_widget.set_sensor_list(self.sensor_system.sensors)
            messages.append(f'FBG: {fbg_count} 个, 传感器: {sensor_count} 个')

            # ---- 数据文件模块 ----
            data_tab = config.get('data_tab', {})
            data_path = data_tab.get('file_path') if data_tab else None
            if data_path and os.path.exists(data_path):
                try:
                    template_id = data_tab.get('template_id')
                    # 查找模板（内置 + 自定义）
                    DataProcessorWindow.load_custom_templates()
                    template = None
                    for t in DEFAULT_TEMPLATES:
                        if t.id == template_id:
                            template = t
                            break
                    if not template:
                        template = self.auto_detect_template(data_path)
                    if not template:
                        template = DataTemplate('custom_txt', '自定义文本', 'txt', '\t', 0, [])

                    df, annotation = parse_file(data_path, template)
                    self.current_data = df
                    self.current_annotation = annotation
                    self.sampled_file_path = data_path
                    self.current_template = template
                    self.file_header_lines = data_tab.get('file_header_lines', [])
                    self.current_columns = data_tab.get('current_columns', [str(c) for c in df.columns])
                    self._insert_annotation_row_if_timestamp_exists()
                    self.update_data_table()
                    if fbg_count == 0:
                        self._auto_populate_fbgs(df)
                    self.add_recent_file(data_path, template.id)
                    data_loaded = True
                    messages.append(f'数据文件: {os.path.basename(data_path)} ({len(df)} 行)')
                except Exception as e:
                    messages.append(f'数据文件恢复失败: {str(e)}')
            elif data_path:
                messages.append(f'数据文件不存在: {os.path.basename(data_path)}')

            # ---- 数据清洗模块 ----
            cleaning_tab = config.get('cleaning_tab', {})
            if cleaning_tab:
                try:
                    self.cleaning_tab_widget.set_config(cleaning_tab)
                    cleaning_restored = True
                    messages.append('清洗规则: 已恢复')
                except Exception as e:
                    messages.append(f'清洗规则恢复失败: {str(e)}')

            # 数据文件和清洗规则都恢复后，自动应用清洗
            if data_loaded and cleaning_restored:
                try:
                    self.apply_cleaning(silent=True)
                    messages.append('数据清洗: 已自动应用')
                except Exception:
                    pass

            # ---- 数据分析模块 ----
            analysis_tab = config.get('analysis_tab', {})
            if analysis_tab:
                try:
                    ds = analysis_tab.get('data_source', '原始数据')
                    idx = self.data_source_combo.findText(ds)
                    if idx >= 0:
                        self.data_source_combo.setCurrentIndex(idx)
                    rt = analysis_tab.get('range_type', '序号范围')
                    idx = self.range_type_combo.findText(rt)
                    if idx >= 0:
                        self.range_type_combo.setCurrentIndex(idx)
                    self.range_start.setValue(analysis_tab.get('range_start', 0))
                    if data_loaded and self.current_data is not None:
                        self.range_end.setValue(min(analysis_tab.get('range_end', 999999), len(self.current_data) - 1))

                    # 恢复传感器计算结果 (同步到 SSOT)
                    saved_results = analysis_tab.get('sensor_results', {})
                    if saved_results:
                        restored_results = {}
                        for key, values in saved_results.items():
                            restored_results[key] = [np.nan if v is None else v for v in values]
                        self.sensor_results = restored_results
                        self.state = self.state.with_analysis_results(restored_results)
                        if self.current_data is not None:
                            analysis_df, _, _ = self._get_analysis_data()
                            self.sensor_tab_widget.set_result_preview(restored_results, analysis_df)
                        analysis_restored = True
                        messages.append(f'分析结果: {len(restored_results)} 个列')
                    else:
                        self.sensor_results = {}
                        self.state = self.state.with_analysis_results({})
                except Exception as e:
                    messages.append(f'分析状态恢复失败: {str(e)}')
                    self.sensor_results = {}
                    self.state = self.state.with_analysis_results({})
            else:
                self.sensor_results = {}

            # 更新分析页面传感器列表
            if hasattr(self, 'analysis_tab_widget'):
                self.refresh_analysis_sensors()

            # ---- 切换到数据文件页展示结果 ----
            if data_loaded:
                self.central_widget.setCurrentWidget(self.data_tab_widget)
            else:
                self.central_widget.setCurrentWidget(self.sensor_tab_widget)

            QMessageBox.information(self, '成功',
                f'配置已加载:\n' + '\n'.join(f'  {m}' for m in messages) +
                f'\n\n来自: {file_path}')

        except Exception as e:
            import traceback
            QMessageBox.critical(self, '错误', f'加载配置失败: {str(e)}\n\n{traceback.format_exc()}')

    def reset_config(self):
        """重置所有配置"""
        reply = QMessageBox.question(self, '确认', '确定要重置所有配置吗？\n这将清除所有传感器和项目文件设置。',
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.sensor_system.fbgs.clear()
        self.sensor_system.sensors.clear()

        self.sensor_tab_widget.set_fbg_list([])
        self.sensor_tab_widget.set_sensor_list([])
        self.load_project_list()

        QMessageBox.information(self, '成功', '配置已重置')

    # ============ File Operations ============

    MAX_RECENT_FILES = 5

    def get_recent_files(self, template_id=None):
        """获取最近打开的文件路径列表，可按模板筛选"""
        key = f'dataprocessor_recent_files_{template_id}' if template_id else 'dataprocessor_recent_files'
        recent = []
        stored = QSettings().value(key)
        if stored:
            recent = stored
        return recent

    def add_recent_file(self, file_path, template_id=None):
        """添加文件路径到最近列表"""
        key = f'dataprocessor_recent_files_{template_id}' if template_id else 'dataprocessor_recent_files'
        recent = self.get_recent_files(template_id)
        if file_path in recent:
            recent.remove(file_path)
        recent.insert(0, file_path)
        recent = recent[:self.MAX_RECENT_FILES]
        QSettings().setValue(key, recent)

    def open_file(self):
        # 两级菜单：光纤光栅数据 / 其它数据
        main_dialog = QDialog(self)
        main_dialog.setWindowTitle('选择数据类型')
        main_dialog.setMinimumWidth(400)
        layout = QVBoxLayout()

        # 类型选择
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel('数据类型:'))
        self.file_type_combo = QComboBox()
        self.file_type_combo.addItems(['光纤光栅数据', '其它数据'])
        type_layout.addWidget(self.file_type_combo)
        layout.addLayout(type_layout)

        # 模板列表和历史路径
        list_layout = QHBoxLayout()

        # 左侧：模板列表
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel('选择模板:'))
        self.template_list = QListWidget()
        left_layout.addWidget(self.template_list)

        # 右侧：最近打开
        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel('最近打开:'))
        self.recent_combo = QComboBox()
        self.recent_combo.setEditable(True)
        self.recent_combo.addItem('选择新路径...')
        right_layout.addWidget(self.recent_combo)

        list_layout.addLayout(left_layout)
        list_layout.addLayout(right_layout)
        layout.addLayout(list_layout)

        # 按钮
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton('确认')
        ok_btn.clicked.connect(main_dialog.accept)
        btn_layout.addWidget(ok_btn)
        cancel_btn = QPushButton('取消')
        cancel_btn.clicked.connect(main_dialog.reject)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        main_dialog.setLayout(layout)

        # 根据数据类型更新模板列表
        def update_templates():
            file_type = self.file_type_combo.currentText()
            self.template_list.clear()
            self.recent_combo.clear()
            self.recent_combo.addItem('选择新路径...')

            if file_type == '光纤光栅数据':
                # 光纤光栅数据：ENLIGHT模板 + 自定义模板
                fiber_templates = [t for t in DEFAULT_TEMPLATES if t.file_format in ('enlight', 'fiber_custom')]
                for t in fiber_templates:
                    self.template_list.addItem(t.name)
                # 添加"添加新模板"选项
                self.template_list.addItem('+ 添加新模板...')
            else:
                # 其它数据：自定义模板（非光纤格式）
                other_templates = [t for t in DEFAULT_TEMPLATES if t.file_format not in ('enlight', 'fiber_custom')]
                for t in other_templates:
                    self.template_list.addItem(t.name)
                # 添加"添加新模板"选项
                self.template_list.addItem('+ 添加新模板...')

        # 根据选择更新历史路径
        def update_recent_files():
            selected = self.template_list.currentItem()
            if not selected:
                return
            template_name = selected.text()
            if template_name.startswith('+'):
                return

            # 查找对应模板
            for t in DEFAULT_TEMPLATES:
                if t.name == template_name:
                    template_id = t.id
                    recent_files = self.get_recent_files(template_id)
                    self.recent_combo.clear()
                    self.recent_combo.addItem('选择新路径...')
                    for path in recent_files:
                        self.recent_combo.addItem(path)
                    break

        self.file_type_combo.currentTextChanged.connect(update_templates)
        self.template_list.itemSelectionChanged.connect(update_recent_files)
        update_templates()

        if main_dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # 记录用户选择的数据类型
        selected_file_type = self.file_type_combo.currentText()

        # 获取选择的模板名称
        selected_item = self.template_list.currentItem()
        if not selected_item:
            return
        template_name = selected_item.text()

        # 如果选择"添加新模板"
        if template_name.startswith('+'):
            last_dir = QSettings('DataProcessor', 'Pro').value('open_file_last_dir', '')
            sample_path, _ = QFileDialog.getOpenFileName(
                self, '选择数据文件', last_dir,
                '数据文件 (*.txt *.csv *.xlsx);;所有文件 (*.*)'
            )
            if not sample_path:
                return

            QSettings('DataProcessor', 'Pro').setValue('open_file_last_dir', os.path.dirname(sample_path))

            # 根据文件扩展名确定格式和分隔符
            ext = sample_path.lower().split('.')[-1]
            if ext == 'csv':
                selected_template = DataTemplate('custom_csv', '自定义CSV', 'csv', ',', 0, [])
            elif ext == 'xlsx':
                selected_template = DataTemplate('custom_xlsx', '自定义Excel', 'xlsx', ',', 0, [])
            else:
                selected_template = DataTemplate('custom_txt', '自定义文本', 'txt', '\t', 0, [])

            # 尝试自动检测更精确的格式（如ENLIGHT）
            detected = self.auto_detect_template(sample_path)
            if detected:
                selected_template = detected

            print(f"DEBUG: Created template: id={selected_template.id}, name={selected_template.name}, file_format={getattr(selected_template, 'file_format', 'NOT_FOUND')}")

            # 直接加载文件
            try:
                # 确保切换到数据文件标签页
                if hasattr(self, 'central_widget'):
                    self.central_widget.setCurrentIndex(0)
                df, annotation = parse_file(sample_path, selected_template)
                self.current_data = df
                self.current_annotation = annotation
                self.sampled_file_path = sample_path
                self.current_template = selected_template
                self._load_file_header_lines(sample_path, selected_template.skip_rows)
                if hasattr(selected_template, 'columns') and selected_template.columns:
                    self.current_columns = [col['name'] for col in selected_template.columns]
                else:
                    self.current_columns = [str(c) for c in df.columns]
                self._insert_annotation_row_if_timestamp_exists()
                self.update_data_table()
                if selected_file_type == '光纤光栅数据':
                    self._auto_populate_fbgs(df)
                self.add_recent_file(sample_path, selected_template.id)
                self.status_bar.showMessage(f'已加载 {len(df)} 行数据')
                QMessageBox.information(self, '成功', f'成功加载 {len(df)} 行数据')
            except Exception as e:
                import traceback
                error_msg = f"加载文件失败: {str(e)}\n\n详细信息:\n{traceback.format_exc()}"
                QMessageBox.critical(self, '错误', error_msg)
            return

        # 查找对应模板
        selected_template = None
        for t in DEFAULT_TEMPLATES:
            if t.name == template_name:
                selected_template = t
                break

        if not selected_template:
            QMessageBox.warning(self, '警告', '未找到对应模板')
            return

        # 获取文件路径
        selected_path = self.recent_combo.currentText()
        recent_files = self.get_recent_files(selected_template.id)
        if selected_path == '选择新路径...' or selected_path not in recent_files:
            file_path = None
        else:
            file_path = selected_path

        # 打开文件对话框
        if selected_template.file_format == 'enlight':
            file_filter = 'ENLIGHT Files (*.txt);;All Files (*)'
        else:
            file_filter = {
                'txt': 'Text Files (*.txt)',
                'csv': 'CSV Files (*.csv)',
                'xlsx': 'Excel Files (*.xlsx *.xls)',
            }.get(selected_template.file_format, 'All Files (*)')

        if not file_path:
            last_dir = QSettings('DataProcessor', 'Pro').value('open_file_last_dir', '')
            file_path, _ = QFileDialog.getOpenFileName(self, '选择数据文件', last_dir, file_filter)

        if not file_path:
            return

        QSettings('DataProcessor', 'Pro').setValue('open_file_last_dir', os.path.dirname(file_path))

        try:
            df, annotation = parse_file(file_path, selected_template)
            self.current_data = df
            self.current_annotation = annotation
            self.sampled_file_path = file_path
            self.current_template = selected_template
            self._load_file_header_lines(file_path, selected_template.skip_rows)
            self.current_columns = [col['name'] for col in selected_template.columns]
            self._insert_annotation_row_if_timestamp_exists()
            self.update_data_table()
            if selected_file_type == '光纤光栅数据':
                self._auto_populate_fbgs(df)
            self.add_recent_file(file_path, selected_template.id)
            self.status_bar.showMessage(f'已加载 {len(df)} 行数据')
            QMessageBox.information(self, '成功', f'成功加载 {len(df)} 行数据')
        except Exception as e:
            import traceback
            error_msg = f"加载文件失败: {str(e)}\n\n详细信息:\n{traceback.format_exc()}"
            QMessageBox.critical(self, '错误', error_msg)

    def auto_detect_template(self, file_path):
        """自动检测数据格式并创建模板。委托给 utils.file_parser。"""
        from utils.file_parser import auto_detect_template as _adt
        return _adt(file_path)
    def save_custom_template(self, template):
        """保存自定义模板"""
        DEFAULT_TEMPLATES.append(template)

    def load_test_data(self):
        """Load built-in test data"""
        data = [
            {'时间': 0, '应变1': 0.0, '应变2': 0.0, '位移': 0.000, '压力': 101325},
            {'时间': 0.1, '应变1': 5.2, '应变2': 3.8, '位移': 0.052, '压力': 101328},
            {'时间': 0.2, '应变1': 10.5, '应变2': 7.9, '位移': 0.105, '压力': 101330},
            {'时间': 0.3, '应变1': 15.9, '应变2': 12.1, '位移': 0.159, '压力': 101333},
            {'时间': 0.4, '应变1': 21.4, '应变2': 16.4, '位移': 0.214, '压力': 101335},
            {'时间': 0.5, '应变1': 27.0, '应变2': 20.8, '位移': 0.270, '压力': 101338},
            {'时间': 0.6, '应变1': 32.7, '应变2': 25.3, '位移': 0.327, '压力': 101340},
            {'时间': 0.7, '应变1': 38.5, '应变2': 30.0, '位移': 0.385, '压力': 101343},
            {'时间': 0.8, '应变1': 44.4, '应变2': 34.8, '位移': 0.444, '压力': 101345},
            {'时间': 0.9, '应变1': 50.4, '应变2': 39.7, '位移': 0.504, '压力': 101348},
            {'时间': 1.0, '应变1': 56.5, '应变2': 44.8, '位移': 0.565, '压力': 101350},
            {'时间': 1.1, '应变1': 62.7, '应变2': 50.0, '位移': 0.627, '压力': 101353},
            {'时间': 1.2, '应变1': 69.0, '应变2': 55.3, '位移': 0.690, '压力': 101355},
            {'时间': 1.3, '应变1': 75.4, '应变2': 60.7, '位移': 0.754, '压力': 101358},
            {'时间': 1.4, '应变1': 81.9, '应变2': 66.3, '位移': 0.819, '压力': 101360},
            {'时间': 1.5, '应变1': 88.5, '应变2': 72.0, '位移': 0.885, '压力': 101363},
            {'时间': 1.6, '应变1': 95.2, '应变2': 77.8, '位移': 0.952, '压力': 101365},
            {'时间': 1.7, '应变1': 102.0, '应变2': 83.7, '位移': 1.020, '压力': 101368},
            {'时间': 1.8, '应变1': 108.9, '应变2': 89.8, '位移': 1.089, '压力': 101370},
            {'时间': 1.9, '应变1': 115.9, '应变2': 95.9, '位移': 1.159, '压力': 101373},
            {'时间': 2.0, '应变1': 123.0, '应变2': 102.2, '位移': 1.230, '压力': 101375},
        ]

        df = pd.DataFrame(data)
        self.current_data = df
        self.current_annotation = {}
        self.current_columns = list(data[0].keys())
        self._insert_annotation_row_if_timestamp_exists()
        self.update_data_table()
        self.status_bar.showMessage(f'已加载 {len(df)} 行测试数据')
        QMessageBox.information(self, '成功', f'已加载 {len(df)} 行测试数据')

    @staticmethod
    def _is_data_row(parts):
        """判断一行数据是真实数据还是表头。委托给 utils.column_utils.is_data_row。"""
        from utils.column_utils import is_data_row
        return is_data_row(parts)

    def _load_file_header_lines(self, file_path, skip_rows):
        """读取文件的格式头行。委托给 utils.file_parser。"""
        from utils.file_parser import read_file_header_lines

        self.file_header_lines = read_file_header_lines(file_path, skip_rows)
    def clear_data(self):
        self.current_data = None
        self.current_annotation = {}
        self.current_columns = []
        self.sampled_data = None
        self.file_header_lines = []
        self.sampled_file_path = None
        self.current_template = None
        self.data_tab_widget.update_data_table(None)
        self.status_bar.showMessage('数据已清除')

    def _find_first_timestamp_row(self, df):
        """找到第一个包含有效时间戳的数据行索引。委托给 utils.dataframe_utils。"""
        from utils.dataframe_utils import find_first_timestamp_row
        return find_first_timestamp_row(df)

    def sample_data(self):
        """按等间隔抽取数据"""
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '没有数据可抽取')
            return

        # 获取抽样间隔
        interval, ok = QInputDialog.getInt(
            self, '抽取数据', '输入抽样间隔 N (每N行抽取1行):',
            10, 2, 1000000, 1
        )
        if not ok:
            return

        df = self.current_data

        # 找到数据起始行（第一个有效时间戳行）
        data_start = self._find_first_timestamp_row(df)

        # 从数据起始行开始等间隔抽取
        data_df = df.iloc[data_start:]
        indices = list(range(0, len(data_df), interval))
        sampled = data_df.iloc[indices].reset_index(drop=True)

        self.sampled_data = sampled

        QMessageBox.information(
            self, '抽取完成',
            f'原始数据: {len(df)} 行 (数据从第{data_start + 1}行开始)\n'
            f'抽取间隔: 每{interval}行取1行\n'
            f'抽取结果: {len(sampled)} 行\n'
            f'格式头: {data_start} 行已保留'
        )

    def save_sampled_data(self):
        """保存数据（优先保存抽样数据），保持原始文件格式"""
        data_to_save = self.sampled_data if self.sampled_data is not None else self.current_data
        if data_to_save is None:
            QMessageBox.warning(self, '警告', '没有数据可保存')
            return

        template = self.current_template
        fmt = template.file_format if template and hasattr(template, 'file_format') else 'csv'
        delim = template.delimiter if template and hasattr(template, 'delimiter') else ','

        # 文件过滤器
        if fmt in ('enlight', 'txt', 'fiber_custom'):
            file_filter = 'Text Files (*.txt);;CSV Files (*.csv);;Excel Files (*.xlsx)'
        elif fmt == 'csv':
            file_filter = 'CSV Files (*.csv);;Text Files (*.txt);;Excel Files (*.xlsx)'
        else:
            file_filter = 'Excel Files (*.xlsx);;CSV Files (*.csv)'

        file_path, _ = QFileDialog.getSaveFileName(self, '保存数据', '', file_filter)
        if not file_path:
            return

        try:
            # ── ★ 格式头暗号行: 用 current_annotation 最新值重建 ──
            annot = getattr(self, 'current_annotation', None) or {}
            header_lines = list(self.file_header_lines) if self.file_header_lines else []
            annot_in_header = False
            if header_lines and fmt in ('enlight', 'fiber_custom') and annot:
                header_lines, annot_in_header = self._rebuild_annotation_in_header_lines(
                    header_lines, annot,
                    col_names=[str(c) for c in data_to_save.columns],
                )

            # ── 暗号行已在格式头 → 数据体剥掉 row 0 (暗号行) ──
            if annot_in_header and len(data_to_save) > 1:
                data_to_save = data_to_save.iloc[1:].reset_index(drop=True)

            # 确定输出格式
            if file_path.endswith('.xlsx'):
                data_to_save.to_excel(file_path, index=False)
            else:
                with open(file_path, 'w', encoding='utf-8') as f:
                    if header_lines:
                        for line in header_lines:
                            f.write(line)
                            if not line.endswith('\n'):
                                f.write('\n')
                    write_header = len(header_lines) == 0
                    data_to_save.to_csv(f, sep=delim, index=False, header=write_header)

            self.sampled_data = None
            self.file_header_lines = []
            QMessageBox.information(self, '成功', f'数据已保存到:\n{file_path}')
        except Exception as e:
            QMessageBox.critical(self, '错误', f'保存失败: {str(e)}')

    def _rebuild_annotation_in_header_lines(
        self, header_lines: list[str], annotation: dict[str, str],
        col_names: list[str],
    ) -> tuple[list[str], bool]:
        """在 file_header_lines 中定位暗号行并替换为 current_annotation 最新值。

        未找到暗号行 → 在表头行后插入一条新暗号行。
        保持原文件的引号格式 ('w1-A1-1') 和列顺序。

        Returns:
            (updated_lines, annot_in_header): 更新后的行列表 + 是否成功重建
        """
        from utils.file_parser import _looks_like_annotation_row

        result = list(header_lines)
        header_idx: int | None = None
        ann_idx: int | None = None

        # ── 定位表头行 + 暗号行 ──
        for i, line in enumerate(result):
            stripped = line.rstrip('\n').rstrip('\r')
            cells = stripped.split('\t')
            if not cells or not cells[0].strip():
                continue
            # 表头行: 以 Timestamp 开头，含多个列名
            if header_idx is None and cells[0].strip() == 'Timestamp' and len(cells) >= 2:
                header_idx = i
                continue
            # 暗号行: 在表头行之后查找
            if header_idx is not None and _looks_like_annotation_row(cells):
                ann_idx = i
                break

        # ── 构建新暗号行 (逐列，与 header col_names 对齐) ──
        ann_parts: list[str] = []
        for col_name in col_names:
            col_str = str(col_name)
            val = annotation.get(col_str, '')
            if val:
                ann_parts.append(f"'{val}'")
            elif col_str == 'Timestamp':
                ann_parts.append("'时间戳'")
            elif col_str.endswith('_anomaly'):
                ann_parts.append('False')
            else:
                ann_parts.append('')
        ann_line = '\t'.join(ann_parts) + '\n'

        # ── 替换或插入 ──
        if ann_idx is not None:
            result[ann_idx] = ann_line
        elif header_idx is not None:
            # 在表头行后插入 (跳过表头后的空行)
            insert_at = header_idx + 1
            while insert_at < len(result) and not result[insert_at].strip():
                insert_at += 1
            result.insert(insert_at, ann_line)
        else:
            # 无表头行 (异常) → 追加到末尾
            result.append(ann_line)

        return result, True

    def save_template_to_file(self):
        """将当前文件的解析参数保存为可复用模板"""
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '请先打开文件')
            return

        # 输入模板名称
        name, ok = QInputDialog.getText(self, '保存模板', '输入模板名称:')
        if not ok or not name.strip():
            return

        name = name.strip()

        template = self.current_template
        if template is None:
            QMessageBox.warning(self, '警告', '当前文件没有关联模板')
            return

        # 从原始文件读取真实格式信息
        file_format = template.file_format if hasattr(template, 'file_format') else 'txt'
        delimiter = template.delimiter if hasattr(template, 'delimiter') else '\t'
        skip_rows = template.skip_rows if hasattr(template, 'skip_rows') else 0
        raw_headers = None

        # 尝试从原始文件头行获取真实的列名
        if self.sampled_file_path and os.path.exists(self.sampled_file_path):
            try:
                with open(self.sampled_file_path, 'r', encoding='utf-8') as f:
                    raw_lines = f.readlines()

                # 检测ENLIGHT/光纤格式
                is_fiber = False
                header_line_idx = -1
                for i, line in enumerate(raw_lines):
                    if 'Timestamp' in line and ('# CH' in line or 'CH' in line):
                        file_format = 'enlight'
                        is_fiber = True
                        header_line_idx = i
                        skip_rows = i + 1
                        delimiter = '\t'
                        break

                # 找到含有列名的行（header行）
                if header_line_idx < 0:
                    for i, line in enumerate(raw_lines):
                        parts = line.strip().split(delimiter)
                        if len(parts) > 1:
                            # 智能判断该行是表头还是数据
                            is_data_row = self._is_data_row(parts)
                            header_line_idx = i
                            if is_data_row:
                                # 无表头文件：此行为数据，不跳过
                                skip_rows = i
                            else:
                                # 有表头文件：此行为表头，跳过
                                skip_rows = i + 1
                            # 检查是否为光纤传感文件
                            line_lower = line.lower()
                            if 'timestamp' in line_lower or any(
                                'fbg' in p.lower() or '波长' in p or 'wavelength' in p.lower()
                                or (p.upper().startswith('W') and p[1:].isdigit())
                                for p in parts
                            ):
                                file_format = 'fiber_custom'
                                is_fiber = True
                            break

                # 从header行获取列名
                if header_line_idx >= 0:
                    raw_headers = raw_lines[header_line_idx].strip().split(delimiter)
            except Exception:
                pass

        # 构建列定义
        columns = []
        is_data = self._is_data_row(raw_headers) if raw_headers else True

        if raw_headers and len(raw_headers) > 0 and not is_data:
            # 真实表头：使用文件中的列名
            w_index = 1
            for h in raw_headers:
                h = h.strip()
                if not h:
                    continue
                h_lower = h.lower()
                if 'timestamp' in h_lower or '时间' in h:
                    columns.append({'name': h, 'comment': '时间戳', 'data_type': 'time'})
                elif 'wavelength' in h_lower or '波长' in h_lower or 'fbg' in h_lower or (h_lower.startswith('w') and h[1:].isdigit()):
                    columns.append({'name': h, 'comment': f'FBG {h}', 'data_type': 'wavelength'})
                elif 'ch' in h_lower and ('计数' in h or 'count' in h_lower):
                    columns.append({'name': h, 'comment': h, 'data_type': 'numeric'})
                elif '应变' in h or 'strain' in h_lower:
                    columns.append({'name': h, 'comment': h, 'data_type': 'strain'})
                elif '温度' in h or 'temperature' in h_lower:
                    columns.append({'name': h, 'comment': h, 'data_type': 'temperature'})
                else:
                    columns.append({'name': h, 'comment': h, 'data_type': 'numeric'})
        elif raw_headers and len(raw_headers) > 0 and is_data:
            # 无表头文件：生成合成列名
            import re
            for i, h in enumerate(raw_headers):
                h = h.strip()
                if not h:
                    continue
                if i == 0 and re.match(r'^\d{2,4}[/-]', h):
                    columns.append({'name': '时间', 'comment': '时间戳', 'data_type': 'time'})
                elif i == 0:
                    columns.append({'name': f'列{i+1}', 'comment': '', 'data_type': 'numeric'})
                else:
                    if is_fiber:
                        columns.append({'name': f'波长{i}', 'comment': f'FBG 波长{i}', 'data_type': 'wavelength'})
                    else:
                        columns.append({'name': f'列{i+1}', 'comment': '', 'data_type': 'numeric'})
        elif hasattr(template, 'columns') and template.columns:
            columns = template.columns
        else:
            for col in self.current_data.columns:
                col_str = str(col)
                col_lower = col_str.lower()
                if '波长' in col_str or 'wavelength' in col_lower:
                    columns.append({'name': col_str, 'comment': col_str, 'data_type': 'wavelength'})
                elif '时间' in col_str or 'timestamp' in col_lower:
                    columns.append({'name': col_str, 'comment': col_str, 'data_type': 'time'})
                elif '应变' in col_str or 'strain' in col_lower:
                    columns.append({'name': col_str, 'comment': col_str, 'data_type': 'strain'})
                elif '温度' in col_str or 'temperature' in col_lower:
                    columns.append({'name': col_str, 'comment': col_str, 'data_type': 'temperature'})
                else:
                    columns.append({'name': col_str, 'comment': col_str, 'data_type': 'numeric'})

        # 如果文件格式不是光纤类型但包含FBG/波长列，自动升级为fiber_custom
        if file_format not in ('enlight', 'fiber_custom'):
            has_fbg = any(
                'fbg' in c.get('name', '').lower() or
                '波长' in c.get('name', '') or
                c.get('data_type') == 'wavelength'
                for c in columns
            )
            if has_fbg:
                file_format = 'fiber_custom'

        new_template = DataTemplate(
            f'custom_{name}',
            name,
            file_format,
            delimiter,
            skip_rows,
            columns
        )

        # 保存到 DEFAULT_TEMPLATES 和持久化文件
        self._persist_custom_template(new_template)
        DEFAULT_TEMPLATES.append(new_template)

        QMessageBox.information(self, '成功', f'模板 "{name}" 已保存，下次打开文件时可在模板列表中选择')

    def _persist_custom_template(self, template):
        """持久化自定义模板到JSON文件。委托给 utils.file_parser.persist_custom_template。"""
        from utils.file_parser import persist_custom_template

        templates_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
        persist_custom_template(template, templates_dir)

    @staticmethod
    def load_custom_templates():
        """启动时加载持久化的自定义模板。委托给 utils.file_parser.load_custom_templates。"""
        from utils.file_parser import load_custom_templates as _load

        templates_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
        _load(templates_dir, DEFAULT_TEMPLATES, DataTemplate)

    MAX_DISPLAY_ROWS = 1000

    def _on_analysis_refresh_requested(self):
        """分析页点击"刷新"时：重新从暗号标注获取分析数据并推送到列选择列表。"""
        analysis_df, _, annotated_cols = self._get_analysis_data()
        if analysis_df is not None and not analysis_df.empty:
            # 有 FBG 传感器时保留真实计算结果，不覆盖
            has_fbg = bool(self.sensor_system.fbgs)
            if annotated_cols and not has_fbg:
                self._push_annotation_sensor_results(analysis_df, annotated_cols)
            elif not has_fbg:
                self.analysis_tab_widget.set_sensor_results({})
            self.analysis_tab_widget.set_current_data(analysis_df, annotated_cols)

    def _push_annotation_sensor_results(self, analysis_df, annotated_cols):
        """将标注数据列转换为 sensor_results 格式。委托给 utils.annotation_utils。"""
        from utils.annotation_utils import build_sensor_results_dict

        sensor_results = build_sensor_results_dict(analysis_df, annotated_cols)
        if sensor_results:
            self.analysis_tab_widget.set_sensor_results(sensor_results)

    def update_data_table(self):
        if self.current_data is None:
            self.data_tab_widget.update_data_table(None)
            return
        # ★ 永远有暗号行 → 始终样式化 row 0；行计数 = 数据行数（不含暗号行）
        annot = getattr(self, 'current_annotation', None) or True
        self.data_tab_widget.update_data_table(
            self.current_data, self.MAX_DISPLAY_ROWS, annotation=annot,
        )
        # 分析模块接收清洗后的数据（跳过暗号行 + 重命名列）并填充列选择列表
        if hasattr(self, 'analysis_tab_widget'):
            analysis_df, _, annotated_cols = self._get_analysis_data()
            # 有 FBG 传感器时保留真实计算结果，不覆盖（通用数据才推标注列）
            has_fbg = bool(self.sensor_system.fbgs)
            if annotated_cols and not has_fbg:
                self._push_annotation_sensor_results(analysis_df, annotated_cols)
            elif not has_fbg:
                self.analysis_tab_widget.set_sensor_results({})
            self.analysis_tab_widget.set_current_data(analysis_df, annotated_cols)

    # ══════════════════════════════════════════════════════════
    # 暗号行自动插入（数据加载时触发）
    # ══════════════════════════════════════════════════════════

    def _insert_annotation_row_if_timestamp_exists(self):
        """数据加载后在 row 0 插入暗号备注行，供用户查看/确认暗号。

        规则:
          - ★ 永远在 row 0 插入暗号行
          - ★ df 中段残留暗号行 → drop 后再统一 insert_blank_row
          - ★ 逐列：真暗号优先 → 占位符回退
          - 暗号行逐列对齐 df.columns，杜绝错位
        """
        try:
            from utils.annotation_utils import (
                build_annotation_row,
                insert_blank_row,
                apply_annotation_row,
            )

            if self.current_data is None or self.current_data.empty:
                return
            df_before = self.current_data
            real_annot = getattr(self, 'current_annotation', None) or {}
            template = getattr(self, 'current_template', None)
            file_fmt = getattr(template, 'file_format', '') if template else ''

            # ── ★ 占位符：永远生成，用作 fallback ──
            placeholder_ann = build_annotation_row(df_before, file_format=file_fmt)

            # ── 1. 检测并 drop 中段残留暗号行 ──
            df = df_before
            annot_values = set(real_annot.values())
            # scan rows 1..99 (skip row 0 which may already be a valid annotation row)
            for idx in range(1, min(100, len(df))):
                row_vals = [str(v).strip().strip("'\"''\"") for v in df.iloc[idx]]
                has_timestamp_mark = any('时间戳' in v for v in row_vals)
                has_annot_vals = sum(1 for v in row_vals if v in annot_values) >= 2
                if has_timestamp_mark or has_annot_vals:
                    df = df.drop(df.index[idx]).reset_index(drop=True)
                    break  # only one residual expected
            if df is not df_before:
                self.current_data = df

            # ── 2. ★ 统一：永远 insert_blank_row 在 row 0 ──
            new_df, dtypes = insert_blank_row(df)
            self._annotation_orig_dtypes = dtypes
            self.current_data = new_df
            annotation_row = 0

            # ── 3. 暗号行填充: 真暗号优先 → 占位符回退（逐列合并）──
            data_row = annotation_row + 1
            if data_row >= len(self.current_data):
                return

            ann: list[str] = []
            for i, col_name in enumerate(df.columns):
                col_str = str(col_name)
                real_val = real_annot.get(col_str, "")
                if real_val:
                    ann.append(real_val)
                else:
                    fallback = placeholder_ann[i] if i < len(placeholder_ann) else ""
                    ann.append(fallback)
            apply_annotation_row(
                self.current_data, ann, annotation_row, self._annotation_orig_dtypes,
            )
        except Exception:
            traceback.print_exc()

    # ══════════════════════════════════════════════════════════
    # 表格编辑双向同步（用户编辑 UI → 写回 self.current_data）
    # ══════════════════════════════════════════════════════════

    def _on_data_table_cell_edited(self, row: int, col: int, text: str):
        """用户在数据表格编辑单元格后，将新值写回底层 DataFrame。

        写回同时同步更新 current_annotation dict (暗号 SSOT)，
        确保编辑 → 保存 → 重载 全程暗号一致。
        """
        if self.current_data is None or not (0 <= row < len(self.current_data)):
            return
        col_name = str(self.current_data.columns[col])
        try:
            self.current_data.iat[row, col] = text
        except (ValueError, TypeError):
            self.current_data[col_name] = self.current_data[col_name].astype(object)
            self.current_data.iat[row, col] = text
        except Exception as e:
            traceback.print_exc()
            print(f'[表格回写失败] row={row}, col={col}: {e}')
            return
        # 同步 current_annotation (暗号 SSOT): row 0 编辑 → 更新 dict
        if row == 0:
            annot = getattr(self, 'current_annotation', None) or {}
            if annot.get(col_name, ''):
                annot[col_name] = text

    # ══════════════════════════════════════════════════════════
    # 被动暗号标注识别（通用工具方法）
    # ══════════════════════════════════════════════════════════

    def get_annotated_columns(self):
        """扫描 current_data 寻找暗号行，被动识别列角色。

        查找包含'时间戳'的标记行，解析每列的含义：
          - '时间戳' → 时间列
          - '温度'、"应力"等引号包裹 → 数据列，引号内为自定义列名
          - 无特殊标记 → 无关列（丢弃）

        Returns:
            (time_col_idx, data_cols, signal_row_idx)
            - time_col_idx:  时间戳列的整数索引，None 表示未找到
            - data_cols:     {列索引: 自定义列名} 字典
            - signal_row_idx: 暗号行在 DataFrame 中的行号，None 表示未找到
        """
        from utils.annotation_utils import extract_annotation_info

        if self.current_data is None or self.current_data.empty:
            return None, {}, None
        return extract_annotation_info(self.current_data)

    def is_fiber_data(self) -> bool:
        """公开 getter：判定当前数据是否为光纤光栅数据。

        消除 ai_diagnosis 与 analysis_tab 的重复判定逻辑。
        列名匹配 FBG/ENLIGHT/光纤传感/W\\d+ 或模板名含"光纤"/"ENLIGHT"。
        """
        import re as _re
        if self.current_data is None:
            return False
        for c in self.current_data.columns:
            name = str(c)
            if 'FBG' in name or 'ENLIG' in name or '光纤传感' in name:
                return True
            if _re.match(r'^W\d+$', name):
                return True
        if self.current_template and hasattr(self.current_template, 'name'):
            tname = str(self.current_template.name)
            if '光纤' in tname or 'ENLIGHT' in tname:
                return True
        return False

    def get_timestamp_column(self) -> str | None:
        """返回检测到的时间戳列名，或 None。

        优先：detector/template 记录的 timestamp_col；
        否则启发式：datetime 类型 → 列名含时间/time/timestamp/(h)。
        """
        import pandas as pd
        if self.current_data is None:
            return None
        # 1. 优先从 template/detector 读
        if self.current_template and hasattr(self.current_template, 'timestamp_col'):
            tc = getattr(self.current_template, 'timestamp_col', None)
            if tc and str(tc) in self.current_data.columns:
                return str(tc)
        # 2. datetime 类型
        for c in self.current_data.columns:
            if pd.api.types.is_datetime64_any_dtype(self.current_data[c]):
                return str(c)
        # 3. 列名匹配
        for c in self.current_data.columns:
            cn = str(c).lower()
            if any(kw in cn for kw in ('时间', 'time', 'timestamp', '(h)')):
                return str(c)
        return None

    # ══════════════════════════════════════════════════════════
    # 根据暗号标注获取清洗后的分析数据
    # ══════════════════════════════════════════════════════════

    def _get_analysis_data(self):
        """如果存在暗号行，返回跳过暗号行及之前行、并重命名列的数据。

        Returns:
            (cleaned_df, time_col_name) — 无暗号时返回 (self.current_data, '时间')
        """
        from utils.annotation_utils import get_analysis_data

        if self.current_data is None:
            return self.current_data, "时间", []
        return get_analysis_data(self.current_data)

    @staticmethod
    def _clean_sensor_keys(results: dict) -> dict:
        """剥离结果字典键中的中英文括号及单位后缀。委托给 utils.column_utils.clean_dict_keys。"""
        from utils.column_utils import clean_dict_keys
        return clean_dict_keys(results)

    # ============ Cleaning Operations ============

    def apply_cleaning(self, silent=False, config=None):
        if self.current_data is None:
            if not silent:
                QMessageBox.warning(self, '警告', '请先加载数据')
            return False

        if config is None:
            config = self.cleaning_tab_widget.get_config()

        rules = []

        if config.get('adjacent_enabled'):
            rules.append(CleaningRule(
                name='相邻差值',
                rule_type='adjacent_diff',
                enabled=True,
                threshold=config.get('diff_threshold', 1.0),
                fill_method=config.get('fill_method', 'linear'),
                fill_value=config.get('fill_value', 0) if config.get('fill_method') == 'custom' else None,
            ))

        if config.get('nan_enabled'):
            rules.append(CleaningRule(
                name='缺失值',
                rule_type='nan',
                enabled=True,
                fill_method=config.get('fill_method', 'linear'),
                fill_value=config.get('fill_value', 0) if config.get('fill_method') == 'custom' else None,
            ))

        try:
            col_pattern = None
            if self.current_template:
                fmt = getattr(self.current_template, 'file_format', '')
                if fmt == 'enlight':
                    col_pattern = r'^波长'
                elif fmt == 'fiber_custom':
                    col_pattern = r'^FBG'
            df = clean_data(self.current_data, rules, column_pattern=col_pattern)
            self.current_data = df
            self.update_data_table()

            anomaly_cols = [col for col in df.columns if col.endswith('_anomaly')]
            total_anomalies = sum(df[col].sum() for col in anomaly_cols)

            # ── 落盘结构化 anomaly_info 到 SSOT（供 AI 诊断/报告读取） ──
            struct_anomaly: dict[str, dict] = {}
            for ac in anomaly_cols:
                count = int(df[ac].sum())
                if count > 0:
                    orig_col = str(ac).replace('_anomaly', '')
                    indices = df.index[df[ac] == True].tolist()
                    struct_anomaly[orig_col] = {
                        'count': count,
                        'indices': indices[:20],
                        'total_indices': len(indices),
                    }
            self._anomaly_info = struct_anomaly
            self.cleaning_tab_widget._anomaly_info = struct_anomaly
            self.cleaning_tab_widget._cleaning_has_run = True  # 独立标志

            self.cleaning_tab_widget.set_result_text(
                f'检测到 {total_anomalies} 个异常数据点\n'
                f'异常列: {[col.replace("_anomaly", "") for col in anomaly_cols]}'
            )

            if not silent:
                self.status_bar.showMessage(f'数据清洗完成，发现 {total_anomalies} 个异常')
            return True

        except Exception as e:
            import traceback
            if not silent:
                QMessageBox.critical(self, '错误', f'清洗失败: {str(e)}\n\n{traceback.format_exc()}')
            return False

    def save_data(self):
        if self.current_data is None:
            QMessageBox.warning(self, '警告', '没有数据可保存')
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, '保存数据', '', 'CSV Files (*.csv);;Excel Files (*.xlsx)'
        )

        if file_path:
            try:
                if file_path.endswith('.xlsx'):
                    self.current_data.to_excel(file_path, index=False)
                else:
                    self.current_data.to_csv(file_path, index=False)
                QMessageBox.information(self, '成功', f'数据已保存到: {file_path}')
            except Exception as e:
                QMessageBox.critical(self, '错误', f'保存失败: {str(e)}')

    def export_report(self):
        """导出报告 - 跳转到信息整合页面"""
        self.central_widget.setCurrentIndex(5)  # Switch to report tab
        QMessageBox.information(self, '提示', '请在成果输出与报告页面选择相应功能生成报告')

# ============ Dialogs ============

# ============ Application Entry ============

def main():
    import logging
    _main_logger = logging.getLogger(__name__)

    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # ── Application-level SkillInstallTaskOwner (Batch 2.4) ──
    # Owned by QApplication — outlives all windows and widgets.
    # Workers register here and complete naturally; terminate() is never called.
    from ui.skill_install_controller import get_skill_install_task_owner
    task_owner = get_skill_install_task_owner(app)

    # ── Application exit protocol (Batch 2.5) ──
    # Safety net only: aboutToQuit MUST NOT be the primary shutdown path.
    # All actual exits go through TaskOwner.begin_application_shutdown() first.
    # If we reach aboutToQuit with active workers, something went wrong —
    # log CRITICAL and let the OS clean up as last resort (the safety net
    # fired — we already exhausted all proper shutdown paths).
    def _on_about_to_quit() -> None:
        if task_owner.has_running_tasks():
            _main_logger.critical(
                "Application reached aboutToQuit with active skill workers — "
                "this is a bug: begin_application_shutdown() was not called "
                "or did not complete before app.quit(). "
                "Remaining workers will be cleaned up by OS."
            )

    app.aboutToQuit.connect(_on_about_to_quit)

    # ── TEMPORARY BATCH 3.6.6: acceptance capture arm signal ──
    # Must fire early so operator can verify capture is ARMED before
    # beginning Scenario A.  DELETE after Scenario A/B stable.
    try:
        from dp_engine.ppt_master_host.acceptance_capture import startup_signal
        _signal = startup_signal()
        print(_signal, file=sys.stderr, flush=True)
    except Exception:
        pass

    # 加载自定义模板
    DataProcessorWindow.load_custom_templates()

    window = DataProcessorWindow(skill_task_owner=task_owner)
    window.show()

    sys.exit(app.exec())

if __name__ == '__main__':
    main()
