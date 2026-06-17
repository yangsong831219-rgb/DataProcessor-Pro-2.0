"""parse_validation.py — 解析后校验层

任何文件解析完成后强制校验结果，不达标立即带诊断 raise，
杜绝"成功加载 0 行 / 5 行假数据 / 表头是元数据行"这类静默失败。

用法:
    from utils.parse_validation import validate_parsed_data, ParseValidationError

    # 在 parse_enlight_file 返回前
    validate_parsed_data(df, meta, annotation, source_path=path)

    # 通用解析
    validate_parsed_data(df, meta, source_path=path, strict=False)
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

_WAVE_LO, _WAVE_HI = 1400.0, 1700.0

# 元数据泄漏特征 — 任何列的列名/第一行值若匹配这些正则即判定表头定位错误
_META_LEAK_RE = re.compile(
    r"\(FBG\)|Range\s*\(|Configuration|Module Type|#\s*Avg|Distance\s*="
)


class ParseValidationError(ValueError):
    """解析结果未通过校验；消息含诊断信息，供 UI 直接展示。"""


def _is_wave_value(v: object) -> bool:
    """单值是否落在 FBG 波长区间 1400~1700nm。"""
    try:
        x = float(v)  # pyright: ignore[reportArgumentType]  # intentional: 运行时防御非 str
    except (ValueError, TypeError):
        return False
    return _WAVE_LO <= x <= _WAVE_HI


def validate_parsed_data(
    df: pd.DataFrame | None,
    meta: dict[str, object] | None,
    annotation: dict[str, str] | None = None,
    *,
    source_path: str = "",
    strict: bool = True,
    min_rows: int = 1,
) -> None:
    """对解析结果做强制校验，不通过则 raise ParseValidationError。

    Args:
        df:         解析结果 DataFrame（尚未插入暗号行）
        meta:       格式元数据 dict
        annotation: 暗号字典（可选，仅用于诊断）
        source_path: 文件路径（仅用于诊断消息）
        strict:     True  → ENLIGHT/Hyperion 全线校验（FBG 波长区间 + 列名 + 数值纯度）
                    False → 只做基础校验（空表、行数、元数据泄漏）
        min_rows:   最小有效数据行数阈值
    """
    fmt = str((meta or {}).get("format", "unknown"))
    shape = None if df is None else df.shape
    diag = f"[format={fmt} source={source_path!r} shape={shape}]"

    # ── 基础校验（所有格式） ──
    if df is None or df.empty:
        raise ParseValidationError(f"解析得到空表 {diag}")

    if len(df) < min_rows:
        raise ParseValidationError(
            f"有效数据行不足({len(df)}<{min_rows}) {diag} "
            f"首列={list(df.columns)[:3]!r}"
        )

    # ── 元数据泄漏检测（所有格式） ──
    leaked = [
        c for c in df.columns
        if isinstance(c, str) and _META_LEAK_RE.search(c)
    ]
    if leaked:
        raise ParseValidationError(
            f"表头定位错误：疑似把元数据行当表头，列名={leaked[:3]!r} {diag}"
        )

    # ── 宽松模式到此为止 ──
    if not strict:
        return

    # ═══════════════════════════════════════════════════════════════════
    # 严格模式 — ENLIGHT / Hyperion 全线校验
    # ═══════════════════════════════════════════════════════════════════

    # 1) Timestamp 列必须存在 (Sensors/Peaks 格式，Legacy 除外)
    if fmt.startswith("hyperion"):
        if "Timestamp" not in df.columns:
            raise ParseValidationError(
                f"缺少 Timestamp 列 {diag} 实际列={list(df.columns)[:6]!r}"
            )

    from typing import cast as _cast

    # 2) 波长列 (FBG_ 前缀 + meta.fbg_cols) 首值必须在 1400~1700nm
    wave_cols: list[str] = [
        c for c in df.columns
        if isinstance(c, str) and str(c).startswith("FBG_")
    ]
    if meta:
        raw_fbg = meta.get("fbg_cols", [])
        if isinstance(raw_fbg, list):
            for c in raw_fbg:
                sc = str(c)
                if sc in df.columns and sc not in wave_cols:
                    wave_cols.append(sc)

    for c in wave_cols:
        s = df[c].dropna()
        if s.empty:
            continue
        if not _is_wave_value(s.iloc[0]):
            raise ParseValidationError(
                f"波长列 {c!r} 首值={s.iloc[0]!r} "
                f"不在 {int(_WAVE_LO)}~{int(_WAVE_HI)}nm，"
                f"疑似列错位 / 时间戳=0 {diag}"
            )

    # 3) 非 Timestamp 列若为 object 且过半不可转数值 → 暗号/元数据污染
    for c in df.columns:
        if c == "Timestamp":
            continue
        s = df[c]
        if s.dtype == object:
            numeric_series = pd.to_numeric(s, errors="coerce")
            ratio = float(numeric_series.notna().mean())
            if ratio < 0.5:
                raise ParseValidationError(
                    f"列 {c!r} 非数值占比过半({(1-ratio):.0%})，"
                    f"疑似暗号/元数据污染 {diag}"
                )
