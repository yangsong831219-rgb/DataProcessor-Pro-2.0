"""Deterministic Builder adapters for Report Bridge assets (Batch 3.3.1B).

Responsibilities:
  - Validate bridge asset path safety (workspace-bound, no traversal, no symlinks)
  - Validate asset ordering and type integrity
  - Append Bridge assets to Word/PPT documents deterministically
  - No Artifact export, workspace creation, Lease release, Controller state,
    LLM calls, or final file commits.

All Bridge file access resolves strictly through:
    bridge_workspace / PreparedReportAsset.managed_filename
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

from .models import (
    PreparedReportAsset,
    ReportAssetRole,
    ParsedTable,
)

# ── PPT text-split rules (frozen) ──

_PPT_MAX_BULLETS_PER_SLIDE: int = 8
_PPT_MAX_BULLET_CHARS: int = 200
_PPT_TEXT_SLIDE_TITLE_PREFIX: str = "技能输出素材 — 文本"


# ── Internal error classification (safe, no absolute paths in messages) ──

class BridgeAdapterError(Exception):
    """Controlled internal error for Bridge adapter failures.

    Messages must NOT contain: absolute paths, temp paths, workspace paths,
    raw exception messages, repr, traceback, manifest, sha256, storage_relpath.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    def __str__(self) -> str:
        return f"[{self.code}] {super().__str__()}"


# ── Path security ──

def _resolve_asset_path(
    bridge_workspace: Path,
    asset: PreparedReportAsset,
) -> Path:
    """Resolve managed_filename within bridge_workspace with full security checks.

    Validates:
      - bridge_workspace exists and is a directory
      - managed_filename is a single non-empty filename (no path separators)
      - No "." or ".."
      - No absolute path
      - Resolved path stays within bridge_workspace (no traversal)
      - Not a symlink (or Windows reparse/junction)
      - File exists and is a regular file

    Returns the resolved Path on success.

    Raises BridgeAdapterError on any validation failure.
    """
    fn = asset.managed_filename

    # Filename validation
    if not fn or fn != fn.strip():
        raise BridgeAdapterError(
            "bridge_asset_invalid",
            "素材文件名无效",
        )
    if fn in (".", ".."):
        raise BridgeAdapterError(
            "bridge_asset_invalid",
            "素材文件名无效",
        )
    if "/" in fn or "\\" in fn:
        raise BridgeAdapterError(
            "bridge_asset_invalid",
            "素材文件名含路径分隔符",
        )
    if os.path.isabs(fn):
        raise BridgeAdapterError(
            "bridge_asset_invalid",
            "素材文件名不得为绝对路径",
        )

    # Workspace validation
    try:
        if not bridge_workspace.exists():
            raise BridgeAdapterError(
                "bridge_asset_missing",
                "Bridge 工作区不存在",
            )
        if not bridge_workspace.is_dir():
            raise BridgeAdapterError(
                "bridge_asset_missing",
                "Bridge 工作区路径不是目录",
            )
    except OSError:
        raise BridgeAdapterError(
            "bridge_asset_missing",
            "Bridge 工作区路径不可访问",
        )

    # Resolve and verify
    try:
        resolved = (bridge_workspace / fn).resolve(strict=False)
    except (OSError, RuntimeError):
        raise BridgeAdapterError(
            "bridge_asset_missing",
            "素材路径解析失败",
        )

    # Must stay within workspace (no traversal via symlinks)
    try:
        resolved.relative_to(bridge_workspace.resolve())
    except ValueError:
        raise BridgeAdapterError(
            "bridge_asset_invalid",
            "素材路径逃逸到工作区外部",
        )

    # Must be direct child of workspace (not in subdirectory)
    if resolved.parent.resolve() != bridge_workspace.resolve():
        raise BridgeAdapterError(
            "bridge_asset_invalid",
            "素材不在工作区直接子级",
        )

    # Reject symlinks and Windows reparse/junction points
    try:
        if resolved.is_symlink():
            raise BridgeAdapterError(
                "bridge_asset_invalid",
                "素材路径为符号链接",
            )
    except OSError:
        raise BridgeAdapterError(
            "bridge_asset_missing",
            "素材路径状态不可读",
        )

    # Windows: reject reparse points (junctions, etc.)
    if os.name == "nt":
        try:
            if _is_nt_reparse_point(resolved):
                raise BridgeAdapterError(
                    "bridge_asset_invalid",
                    "素材路径为重解析点",
                )
        except OSError:
            raise BridgeAdapterError(
                "bridge_asset_missing",
                "素材路径状态不可读",
            )

    # Must exist and be a regular file
    if not resolved.exists():
        raise BridgeAdapterError(
            "bridge_asset_missing",
            "素材文件不存在",
        )
    if not resolved.is_file():
        raise BridgeAdapterError(
            "bridge_asset_missing",
            "素材路径不是普通文件",
        )

    return resolved


def _is_nt_reparse_point(path: Path) -> bool:
    """Check if a Windows path is a reparse point (junction, mount point, etc.)."""
    import ctypes
    from ctypes import wintypes

    FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        # GetFileAttributesW returns DWORD; on failure returns INVALID_FILE_ATTRIBUTES
        # which may be -1 (signed) or 0xFFFFFFFF (unsigned) depending on ctypes handling
        if attrs == INVALID_FILE_ATTRIBUTES or attrs == -1:
            return False
        return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
    except Exception:
        return False


# ── Validation ──


def _validate_asset_order(assets: tuple[PreparedReportAsset, ...]) -> None:
    """Validate that asset order values are exactly 0..N-1 with no gaps or duplicates,
    AND that tuple position matches the order field.

    Raises BridgeAdapterError on invalid order.
    """
    n = len(assets)
    orders = sorted(a.order for a in assets)
    expected = list(range(n))
    if orders != expected:
        raise BridgeAdapterError(
            "bridge_asset_invalid",
            f"素材 order 必须恰好为 0..{n - 1} 连续排列，实际: {orders}",
        )
    # Contract: tuple position must match order field
    for i, asset in enumerate(assets):
        if asset.order != i:
            raise BridgeAdapterError(
                "bridge_asset_invalid",
                f"素材 tuple 第 {i} 个元素的 order 字段为 {asset.order}，"
                f"但 tuple 顺序要求 order={i}",
            )


def validate_bridge_assets_for_word(
    bridge_workspace: Path,
    assets: tuple[PreparedReportAsset, ...],
) -> None:
    """Validate Bridge assets are safe and valid for Word appendix.

    Checks:
      - bridge_workspace exists and is a directory
      - asset order is exactly 0..N-1
      - each managed_filename resolves safely within workspace
      - each asset file exists and is a regular file

    Raises BridgeAdapterError on any failure.
    """
    if not assets:
        return

    if not bridge_workspace.exists() or not bridge_workspace.is_dir():
        raise BridgeAdapterError(
            "bridge_asset_missing",
            "Bridge 工作区不存在或不是目录",
        )

    _validate_asset_order(assets)

    # Pre-validate all paths
    for asset in assets:
        _resolve_asset_path(bridge_workspace, asset)

    # Validate payload types match roles
    for asset in assets:
        if asset.role == ReportAssetRole.TABLE_SOURCE:
            if not isinstance(asset.parsed_payload, tuple):
                raise BridgeAdapterError(
                    "bridge_asset_invalid",
                    "TABLE_SOURCE 素材的 parsed_payload 必须为 tuple",
                )
        elif asset.role == ReportAssetRole.TEXT_SOURCE:
            if not isinstance(asset.parsed_payload, str):
                raise BridgeAdapterError(
                    "bridge_asset_invalid",
                    "TEXT_SOURCE 素材的 parsed_payload 必须为 str",
                )
        elif asset.role == ReportAssetRole.IMAGE:
            if asset.parsed_payload is not None:
                raise BridgeAdapterError(
                    "bridge_asset_invalid",
                    "IMAGE 素材的 parsed_payload 必须为 None",
                )


def validate_bridge_assets_for_ppt(
    bridge_workspace: Path,
    assets: tuple[PreparedReportAsset, ...],
) -> None:
    """Validate Bridge assets are safe and valid for PPT appendix.

    Same checks as Word validation, plus:
      - TABLE_SOURCE is REJECTED before presentation modification
        (error code: role_mismatch)

    Raises BridgeAdapterError on any failure.
    """
    if not assets:
        return

    if not bridge_workspace.exists() or not bridge_workspace.is_dir():
        raise BridgeAdapterError(
            "bridge_asset_missing",
            "Bridge 工作区不存在或不是目录",
        )

    _validate_asset_order(assets)

    # Reject TABLE_SOURCE for PPT before any modification
    for asset in assets:
        if asset.role == ReportAssetRole.TABLE_SOURCE:
            raise BridgeAdapterError(
                "role_mismatch",
                "PPT 报告不支持表格素材（TABLE_SOURCE）",
            )

    # Pre-validate all paths
    for asset in assets:
        _resolve_asset_path(bridge_workspace, asset)

    # Validate payload types match roles
    for asset in assets:
        if asset.role == ReportAssetRole.TEXT_SOURCE:
            if not isinstance(asset.parsed_payload, str):
                raise BridgeAdapterError(
                    "bridge_asset_invalid",
                    "TEXT_SOURCE 素材的 parsed_payload 必须为 str",
                )
        elif asset.role == ReportAssetRole.IMAGE:
            if asset.parsed_payload is not None:
                raise BridgeAdapterError(
                    "bridge_asset_invalid",
                    "IMAGE 素材的 parsed_payload 必须为 None",
                )


# ── Safe display name ──

def _safe_display_name(asset: PreparedReportAsset) -> str:
    """Return a safe display name for the asset.

    Uses the authoritative artifact's display_name if available.
    Never uses paths, artifact_id, hash, or workspace paths as titles.
    """
    try:
        art = asset.authoritative_artifact
        name = getattr(art, "display_name", "") or getattr(art, "name", "")
        if name and name.strip():
            return name.strip()
    except Exception:
        pass
    # Fallback: role-based generic name
    role_names = {
        ReportAssetRole.IMAGE: "图片素材",
        ReportAssetRole.TABLE_SOURCE: "表格素材",
        ReportAssetRole.TEXT_SOURCE: "文本素材",
    }
    return role_names.get(asset.role, "素材")


# ── Word appendix ──

_APPENDIX_HEADING = "技能输出素材"


def append_bridge_assets_to_word(
    doc,
    *,
    bridge_workspace: Path,
    assets: tuple[PreparedReportAsset, ...],
    cancel_check: Callable[[], None] | None = None,
) -> None:
    """Append Bridge assets to a python-docx Document deterministically.

    Adds a level-1 heading "技能输出素材" and appends assets in order:
      - IMAGE: inserts picture with safe display_name caption
      - TABLE_SOURCE: inserts Word table (first row as bold header)
      - TEXT_SOURCE: inserts heading + plain text paragraph

    Args:
        doc: python-docx Document object
        bridge_workspace: Bridge workspace directory
        assets: PreparedReportAsset tuple, pre-validated and sorted
        cancel_check: Optional callable that raises on cancel

    Raises BridgeAdapterError on failure.
    """
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    if not assets:
        return

    # Cancel checkpoint: before appendix
    if cancel_check is not None:
        cancel_check()

    # Sort assets by order (defensive: don't trust caller's tuple order)
    sorted_assets = sorted(assets, key=lambda a: a.order)

    doc.add_heading(_APPENDIX_HEADING, level=1)

    for asset in sorted_assets:
        # Cancel checkpoint: before each asset
        if cancel_check is not None:
            cancel_check()

        display_name = _safe_display_name(asset)
        file_path = _resolve_asset_path(bridge_workspace, asset)

        if asset.role == ReportAssetRole.IMAGE:
            _append_word_image(doc, file_path, display_name)
        elif asset.role == ReportAssetRole.TABLE_SOURCE:
            _append_word_table(doc, asset, display_name)
        elif asset.role == ReportAssetRole.TEXT_SOURCE:
            _append_word_text(doc, asset, display_name)
        else:
            raise BridgeAdapterError(
                "bridge_asset_invalid",
                f"不支持的素材角色: {asset.role.value}",
            )


def _append_word_image(doc, file_path: Path, display_name: str) -> None:
    """Insert an image with safe caption into the Word document."""
    from docx.shared import Inches, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    # Title
    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run_title = p_title.add_run(display_name)
    run_title.bold = True
    run_title.font.size = Pt(11)

    # Image — fit to page width
    try:
        img_para = doc.add_paragraph()
        img_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_img = img_para.add_run()
        run_img.add_picture(str(file_path), width=Inches(5.5))
    except Exception:
        raise BridgeAdapterError(
            "bridge_asset_invalid",
            "素材图片插入失败",
        )

    # Small spacing after image
    doc.add_paragraph()


def _append_word_table(
    doc,
    asset: PreparedReportAsset,
    display_name: str,
) -> None:
    """Insert a TABLE_SOURCE asset as a python-docx table.

    First row of parsed_payload is the header (bold).
    """
    from docx.shared import Pt

    payload: ParsedTable = asset.parsed_payload  # type: ignore[assignment]
    if not isinstance(payload, tuple) or len(payload) < 1:
        raise BridgeAdapterError(
            "bridge_asset_invalid",
            "TABLE_SOURCE 素材的 parsed_payload 格式无效",
        )

    headers = payload[0]
    data_rows = payload[1:]

    # Validate rectangular shape
    n_cols = len(headers)
    if n_cols == 0:
        raise BridgeAdapterError(
            "bridge_asset_invalid",
            "TABLE_SOURCE 素材表头为空",
        )
    for ri, row in enumerate(data_rows):
        if len(row) != n_cols:
            raise BridgeAdapterError(
                "bridge_asset_invalid",
                f"TABLE_SOURCE 素材表格非矩形（第{ri + 1}行）",
            )

    # Title
    p_title = doc.add_paragraph()
    run_title = p_title.add_run(display_name)
    run_title.bold = True
    run_title.font.size = Pt(11)

    # Table
    tbl = doc.add_table(rows=1 + len(data_rows), cols=n_cols)
    tbl.style = "Light Grid Accent 1"

    # Header row (bold)
    for ci, h in enumerate(headers):
        cell = tbl.cell(0, ci)
        cell.text = str(h) if h is not None else ""
        for p in cell.paragraphs:
            for r in p.runs:
                r.bold = True

    # Data rows
    for ri, row in enumerate(data_rows):
        for ci, val in enumerate(row):
            cell = tbl.cell(ri + 1, ci)
            cell.text = str(val) if val is not None else ""

    doc.add_paragraph()


def _append_word_text(
    doc,
    asset: PreparedReportAsset,
    display_name: str,
) -> None:
    """Insert a TEXT_SOURCE asset as a heading + plain text paragraph."""
    from docx.shared import Pt

    text: str = asset.parsed_payload  # type: ignore[assignment]
    if not isinstance(text, str):
        raise BridgeAdapterError(
            "bridge_asset_invalid",
            "TEXT_SOURCE 素材的 parsed_payload 必须为 str",
        )

    # Title
    p_title = doc.add_paragraph()
    run_title = p_title.add_run(display_name)
    run_title.bold = True
    run_title.font.size = Pt(11)

    # Text content — deterministic, no LLM transformation
    p_text = doc.add_paragraph()
    p_text.add_run(text)


# ── PPT appendix ──


def append_bridge_assets_to_ppt(
    prs,
    *,
    bridge_workspace: Path,
    assets: tuple[PreparedReportAsset, ...],
    cancel_check: Callable[[], None] | None = None,
) -> None:
    """Append Bridge asset pages to a python-pptx Presentation deterministically.

    Adds slides after existing content in order:
      - IMAGE: one slide with centered image + safe title
      - TEXT_SOURCE: bullet slides (≤8 bullets, ≤200 chars each, split by newlines)
      - TABLE_SOURCE: MUST be rejected by validate_bridge_assets_for_ppt
        before this function is called.

    Args:
        prs: python-pptx Presentation object
        bridge_workspace: Bridge workspace directory
        assets: PreparedReportAsset tuple, pre-validated
        cancel_check: Optional callable that raises on cancel

    Raises BridgeAdapterError on failure.
    """
    from pptx.util import Inches, Pt, Emu
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

    if not assets:
        return

    # Defensive: verify no TABLE_SOURCE slipped through
    for asset in assets:
        if asset.role == ReportAssetRole.TABLE_SOURCE:
            raise BridgeAdapterError(
                "role_mismatch",
                "PPT 报告不支持表格素材（TABLE_SOURCE）",
            )

    # Cancel checkpoint: before appendix
    if cancel_check is not None:
        cancel_check()

    # Sort by order
    sorted_assets = sorted(assets, key=lambda a: a.order)

    slide_w = int(prs.slide_width)
    slide_h = int(prs.slide_height)

    for asset in sorted_assets:
        # Cancel checkpoint: before each asset slide
        if cancel_check is not None:
            cancel_check()

        display_name = _safe_display_name(asset)

        if asset.role == ReportAssetRole.IMAGE:
            file_path = _resolve_asset_path(bridge_workspace, asset)
            _append_ppt_image_slide(
                prs, file_path, display_name, slide_w, slide_h,
            )
        elif asset.role == ReportAssetRole.TEXT_SOURCE:
            _append_ppt_text_slides(
                prs, asset, display_name, slide_w, slide_h,
                cancel_check=cancel_check,
            )
        else:
            raise BridgeAdapterError(
                "bridge_asset_invalid",
                f"不支持的素材角色: {asset.role.value}",
            )


def _append_ppt_image_slide(
    prs,
    file_path: Path,
    display_name: str,
    slide_w: int,
    slide_h: int,
) -> None:
    """Create a single slide with a centered image and safe title."""
    from pptx.util import Inches, Pt, Emu
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

    # Use blank layout
    blank_layout = prs.slide_layouts[6]  # blank layout
    slide = prs.slides.add_slide(blank_layout)

    # Safe title
    title_left = Emu(int(slide_w * 0.06))
    title_top = Emu(int(slide_h * 0.04))
    title_width = Emu(int(slide_w * 0.88))
    title_height = Emu(int(slide_h * 0.12))
    title_box = slide.shapes.add_textbox(
        title_left, title_top, title_width, title_height,
    )
    tf = title_box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = display_name
    p.font.size = Pt(28)
    p.font.bold = True
    p.alignment = PP_ALIGN.LEFT

    # Image — fit within safe area
    try:
        from PIL import Image
        with Image.open(file_path) as img:
            img_w, img_h = img.size
    except Exception:
        raise BridgeAdapterError(
            "bridge_asset_invalid",
            "素材图片无法读取尺寸",
        )

    # Available area (below title, with margins)
    margin = int(slide_w * 0.1)
    avail_w = slide_w - 2 * margin
    avail_h = int(slide_h * 0.75)

    # Scale to fit, preserving aspect ratio
    scale = min(avail_w / img_w, avail_h / img_h)
    display_w = int(img_w * scale)
    display_h = int(img_h * scale)

    left = Emu(margin + (avail_w - display_w) // 2)
    top = Emu(int(slide_h * 0.22) + (avail_h - display_h) // 2)

    try:
        slide.shapes.add_picture(
            str(file_path),
            left, top,
            width=Emu(display_w),
            height=Emu(display_h),
        )
    except Exception:
        raise BridgeAdapterError(
            "bridge_asset_invalid",
            "素材图片插入 PPT 失败",
        )


def _append_ppt_text_slides(
    prs,
    asset: PreparedReportAsset,
    display_name: str,
    slide_w: int,
    slide_h: int,
    *,
    cancel_check: Callable[[], None] | None = None,
) -> None:
    """Create bullet slides from TEXT_SOURCE content.

    Frozen splitting rules:
      1. Split by newlines
      2. Remove blank lines
      3. Lines > 200 chars → split at 200-char boundaries
      4. Max 8 bullets per slide
      5. Maintain original order
      6. No summarization, no rewriting, no LLM
      7. Empty text → one slide with safe empty state
    """
    from pptx.util import Inches, Pt, Emu
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

    text: str = asset.parsed_payload  # type: ignore[assignment]
    if not isinstance(text, str):
        raise BridgeAdapterError(
            "bridge_asset_invalid",
            "TEXT_SOURCE 素材的 parsed_payload 必须为 str",
        )

    # Split by newlines, remove blank lines
    raw_lines = [line for line in text.split("\n") if line.strip()]

    # Split long lines at 200-char boundaries
    bullets: list[str] = []
    for line in raw_lines:
        while len(line) > _PPT_MAX_BULLET_CHARS:
            bullets.append(line[:_PPT_MAX_BULLET_CHARS])
            line = line[_PPT_MAX_BULLET_CHARS:]
        if line.strip():
            bullets.append(line)

    # Handle empty content
    if not bullets:
        bullets = ["（素材文本内容为空）"]

    # Paginate: max 8 bullets per slide
    blank_layout = prs.slide_layouts[6]
    page_idx = 0
    for start in range(0, len(bullets), _PPT_MAX_BULLETS_PER_SLIDE):
        # Cancel checkpoint: between text pagination slides
        if cancel_check is not None:
            cancel_check()

        chunk = bullets[start:start + _PPT_MAX_BULLETS_PER_SLIDE]

        slide = prs.slides.add_slide(blank_layout)

        # Title
        slide_title = (
            f"{display_name}"
            if len(bullets) <= _PPT_MAX_BULLETS_PER_SLIDE
            else f"{display_name} ({page_idx + 1})"
        )
        title_left = Emu(int(slide_w * 0.06))
        title_top = Emu(int(slide_h * 0.04))
        title_width = Emu(int(slide_w * 0.88))
        title_height = Emu(int(slide_h * 0.12))
        title_box = slide.shapes.add_textbox(
            title_left, title_top, title_width, title_height,
        )
        tf_title = title_box.text_frame
        tf_title.word_wrap = True
        p_title = tf_title.paragraphs[0]
        p_title.text = slide_title
        p_title.font.size = Pt(28)
        p_title.font.bold = True
        p_title.alignment = PP_ALIGN.LEFT

        # Bullet text area
        body_left = Emu(int(slide_w * 0.08))
        body_top = Emu(int(slide_h * 0.20))
        body_width = Emu(int(slide_w * 0.84))
        body_height = Emu(int(slide_h * 0.70))
        body_box = slide.shapes.add_textbox(
            body_left, body_top, body_width, body_height,
        )
        tf_body = body_box.text_frame
        tf_body.word_wrap = True
        tf_body.vertical_anchor = MSO_ANCHOR.TOP

        for bi, bullet_text in enumerate(chunk):
            para = tf_body.paragraphs[0] if bi == 0 else tf_body.add_paragraph()
            para.text = f"• {bullet_text}"
            para.font.size = Pt(18)
            para.space_after = Pt(8)
            para.level = 0

        page_idx += 1
