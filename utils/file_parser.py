"""文件解析器 — 自动检测分隔符、表头、编码，解析 CSV/TXT/ENLIGHT 格式

从 main.py 抽离的纯函数，无 UI 依赖。
"""

from __future__ import annotations

import csv
import re
from typing import Any, Optional, Tuple

import pandas as pd

from utils.parse_validation import validate_parsed_data, ParseValidationError  # noqa: F401  # re-exported for callers


def _run_parse_validation(
    df: pd.DataFrame | None,
    meta: dict,
    annotation: dict[str, str],
    source_path: str,
) -> None:
    """对 parse_enlight_file 各分支的解析结果做强制校验。"""
    try:
        validate_parsed_data(df, meta, annotation, source_path=source_path, strict=True)
    except ParseValidationError:
        raise
    except Exception as exc:
        # 校验本身不应崩溃；若意外失败，升级为 ParseValidationError 含原信息
        raise ParseValidationError(
            f"校验器内部异常: {exc} [format={meta.get('format','?')} source={source_path!r}]"
        ) from exc


def detect_format(
    file_path: str,
    encoding: str,
    template_skip_rows: int,
) -> Tuple[Optional[str], int]:
    """从文件前50KB样本中检测分隔符和数据起始行

    Returns:
        (delimiter, skip_rows) — delimiter 为 None 表示检测失败
    """
    common_delimiters = [',', '\t', ';', '|']
    try:
        with open(file_path, 'r', encoding=encoding) as f:
            sample = f.read(51200)
    except UnicodeDecodeError:
        return None, template_skip_rows

    lines = sample.split('\n')
    if len(lines) < 3:
        return None, template_skip_rows

    effective_lines = lines[template_skip_rows:]
    if len(effective_lines) < 2:
        return None, template_skip_rows

    # csv.Sniffer 检测
    try:
        dialect = csv.Sniffer().sniff('\n'.join(effective_lines[:50]), delimiters=''.join(common_delimiters))
        return dialect.delimiter, template_skip_rows
    except Exception:
        pass

    # 手工检测：找每行列数最一致且>1的分隔符
    best_delim, best_score, best_skip = None, 0, template_skip_rows
    for d in common_delimiters:
        for offset, line in enumerate(effective_lines):
            if not line.strip():
                continue
            segment = [l for l in effective_lines[offset:offset + 50] if l.strip()]
            if len(segment) < 10:
                continue
            counts = [len(l.split(d)) for l in segment]
            if max(counts) <= 1:
                continue
            mode = max(set(counts), key=counts.count)
            consistency = counts.count(mode)
            if consistency > best_score and mode > 1:
                best_score = consistency
                best_delim = d
                best_skip = template_skip_rows + offset

    return best_delim, best_skip


def detect_header(
    file_path: str,
    delimiter: str,
    skip_rows: int,
    encoding: str,
) -> Optional[int]:
    """检测跳过 skip_rows 后的第一行是表头还是数据

    Returns:
        0 表示是表头，None 表示是数据
    """
    try:
        with open(file_path, 'r', encoding=encoding) as f:
            for i, line in enumerate(f):
                if i < skip_rows:
                    continue
                parts = line.strip().split(delimiter)
                if len(parts) < 2:
                    continue
                data_count = 0
                header_count = 0
                for p in parts:
                    p = p.strip()
                    if not p:
                        continue
                    try:
                        float(p)
                        data_count += 1
                        continue
                    except ValueError:
                        pass
                    if re.match(r'^\d{2,4}[/-]\d{1,2}[/-]\d{1,2}', p):
                        data_count += 1
                        continue
                    if re.match(r'^[A-Za-z_]', p) or any(kw in p for kw in ['波长', '时间', '温度', '应变']):
                        header_count += 1
                        continue
                    data_count += 1
                total = data_count + header_count
                if total > 0:
                    ratio = data_count / total if total > 0 else 0
                    return None if ratio >= 0.5 else 0
                break
    except Exception:
        pass
    return None


def try_read_csv(
    file_path: str,
    delimiter: str,
    skip_rows: int,
    encoding: str,
    header: Any = 'infer',
) -> Tuple[pd.DataFrame, str]:
    """读取 CSV/TXT，自动检测分隔符和 skip_rows

    header: None = 无表头, 0 = 第一行为表头, 'infer' = 自动检测, 'detect' = 智能检测
    """
    detected_delim, detected_skip = detect_format(file_path, encoding, skip_rows)
    if detected_delim:
        delimiter = detected_delim
    if header in (None, 'infer') and detected_skip:
        skip_rows = detected_skip

    if not delimiter:
        delimiter = ','

    if header == 'infer':
        header = None
    elif header == 'detect':
        header = detect_header(file_path, delimiter, skip_rows, encoding)

    for enc in (encoding, 'gbk', 'latin-1'):
        try:
            df = pd.read_csv(
                file_path, delimiter=delimiter, skiprows=skip_rows,
                header=header, encoding=enc, on_bad_lines='skip', low_memory=False,
            )
            if df.shape[1] > 1 or len(df) > 0:
                for col in df.columns:
                    try:
                        df[col] = pd.to_numeric(df[col])
                    except (ValueError, TypeError):
                        pass
                return df, delimiter
        except UnicodeDecodeError:
            continue
        except pd.errors.ParserError:
            break

    raise pd.errors.ParserError(
        f"无法解析文件 '{file_path}'，请检查文件格式或手动指定分隔符和跳过的行数"
    )


def parse_file(file_path: str, template: Any) -> pd.DataFrame:
    """根据模板解析数据文件

    Args:
        file_path: 数据文件路径
        template: DataTemplate 实例，需有 file_format, delimiter, skip_rows 等属性
    """
    if not hasattr(template, 'file_format'):
        ext = file_path.lower().split('.')[-1]
        if ext == 'csv':
            template.file_format = 'csv'
        elif ext in ('xlsx', 'xls'):
            template.file_format = 'xlsx'
        else:
            template.file_format = 'txt'

    if template.file_format in ('csv', 'txt', 'fiber_custom'):
        has_header = detect_header(file_path, template.delimiter, template.skip_rows, 'utf-8')
        use_header = 0 if has_header is not None else None
        df, _ = try_read_csv(file_path, template.delimiter, template.skip_rows, 'utf-8', header=use_header)
        if hasattr(template, 'columns') and template.columns:
            col_names = [c['name'] for c in template.columns]
            if len(col_names) == len(df.columns):
                df.columns = col_names
        df.columns = df.columns.astype(str)
        # ── 通用路径解析后校验 (lenient 档) ──
        validate_parsed_data(
            df, {"format": template.file_format}, source_path=file_path, strict=False,
        )
        return df

    elif template.file_format in ('xlsx', 'xls'):
        df = pd.read_excel(file_path, header=None)
        df.columns = df.columns.astype(str)
        # ── 通用路径解析后校验 (lenient 档) ──
        validate_parsed_data(
            df, {"format": template.file_format}, source_path=file_path, strict=False,
        )
        return df

    elif template.file_format == 'enlight':
        # 委托给新的统一 ENLIGHT/Hyperion 解析器
        df, _annotation, _meta = parse_enlight_file(file_path)
        return df

    else:
        raise ValueError(f"不支持的文件格式: {template.file_format}")


# ═══════════════════════════════════════════════════════════════════════
# ENLIGHT / Hyperion 统一解析器
# ═══════════════════════════════════════════════════════════════════════

# 引号剥离正则 — 同时收直引 + 全角弯引 (safe char-class form)
_QUOTE_CHARS = "'\"‘’“”"
_QUOTE_RE = re.compile(rf"^[{_QUOTE_CHARS}](.*?)[{_QUOTE_CHARS}]$")
_UNWRAP_RE = re.compile(rf"^[{_QUOTE_CHARS}](.*?)[{_QUOTE_CHARS}]$")
_STRIP_RE = re.compile(rf"^[{_QUOTE_CHARS}]+|[{_QUOTE_CHARS}]+$")
_TIMESTAMP_LIKE = re.compile(r'^\d{2,4}[-/.]\d{1,2}[-/.]\d{1,2}')


def _strip_code_quotes(s: str) -> str:
    """剥离暗号单元首尾引号（兼容直/弯/中英双引号）。"""
    return _STRIP_RE.sub("", s.strip())


def _looks_like_annotation_row(cells: list[str]) -> bool:
    """暗号行判定：存在引号包裹的非数值单元，且无任何裸数值。"""
    has_quoted = False
    for c in cells:
        c = c.strip()
        if not c:
            continue
        if c and c[0] in _QUOTE_CHARS:
            has_quoted = True
            continue
        try:
            float(c)
            return False  # 出现裸数值 → 不是暗号行（是数据行）
        except ValueError:
            continue
    return has_quoted


def parse_enlight_file(
    path: str,
    encoding: str | None = None,
) -> tuple[pd.DataFrame, dict[str, str], dict]:
    """统一 ENLIGHT / Hyperion 解析器 — 内容感知格式判别。

    三种模式 (按优先级):
      1. Hyperion Peaks  — 含元数据头 "ENLIGHT Version" / "Module Type: Hyperion"
      2. Hyperion Sensors — UTF-8 BOM + 首行 "Timestamp\t..." 且无上述元数据
      3. Legacy ENLIGHT   — 回退到原 _parse_enlight_like 逻辑

    Args:
        path: 文件路径
        encoding: 文件编码，None 则自动探测

    Returns:
        (df, annotation, meta)
          - df:         清洗后的数据 DataFrame，列名已去引号
          - annotation: {列名: 暗号字符串} 从引号包裹格提取
          - meta:       {format, header_idx, num_meta_skipped, num_numeric_cols, ...}
    """
    meta: dict = {"format": "unknown", "header_idx": 0, "num_meta_skipped": 0}

    # ── 读取全文件做内容感知探查 ──
    probe_enc = "utf-8-sig"
    try:
        with open(path, "r", encoding=probe_enc, errors="replace") as f:
            probe_lines = [ln.rstrip("\r") for ln in f.readlines()]
    except (UnicodeDecodeError, UnicodeError):
        probe_enc = encoding or "utf-8"
        try:
            with open(path, "r", encoding=probe_enc, errors="replace") as f:
                probe_lines = [ln.rstrip("\r") for ln in f.readlines()]
        except UnicodeDecodeError:
            probe_enc = "gb18030"
            with open(path, "r", encoding=probe_enc, errors="replace") as f:
                probe_lines = [ln.rstrip("\r") for ln in f.readlines()]

    # ── 检查 UTF-8 BOM ──
    has_bom = False
    try:
        with open(path, "rb") as f:
            bom = f.read(3)
            has_bom = (bom == b"\xef\xbb\xbf")
    except Exception:
        pass

    # ── 内容感知: Hyperion 标志检测 ──
    is_hyperion = False
    has_timestamp_header = False
    for line in probe_lines:
        if "ENLIGHT Version" in line or "Module Type: Hyperion" in line or "CH N Configuration:" in line:
            is_hyperion = True
        if line.startswith("Timestamp\t") or line.startswith("时间戳\t"):
            has_timestamp_header = True

    # ── 分支 1: Hyperion Peaks (元数据标志 + # CH 计数列表头) ──
    _peaks_hdr = _find_peaks_header(probe_lines)
    if is_hyperion and _peaks_hdr is not None:
        meta["format"] = "hyperion_peaks"
        df, annotation, sub_meta = _parse_hyperion_peaks(path, probe_enc, meta, has_bom)
        _run_parse_validation(df, meta, annotation, path)
        return df, annotation, sub_meta

    # ── 分支 2: Hyperion Sensors ──
    # 条件: 找到 Timestamp\\t 表头 + 非 Peaks (# CH 已排前面)
    # Sensors 公式版文件不一定有 BOM 或 Hyperion 元数据标志 —
    #   只要全文件内存在 Timestamp\\t 表头就可能是 Sensors。
    if has_timestamp_header:
        meta["format"] = "hyperion_sensors"
        df, annotation, sub_meta = _parse_hyperion_sensors(path, meta, has_bom)
        _run_parse_validation(df, meta, annotation, path)
        return df, annotation, sub_meta

    # ── 分支 3: Legacy ──
    meta["format"] = "legacy_enlight"
    df, annotation, sub_meta = _parse_legacy_enlight(path, probe_enc, meta)
    _run_parse_validation(df, meta, annotation, path)
    return df, annotation, sub_meta


# ═══════════════════════════════════════════════════════════════════════
# Peaks 格式专用解析器 — 显式按字段解析，消费 16 个计数列
# ═══════════════════════════════════════════════════════════════════════

_N_CH = 16


def _is_peaks_data_row(f: list[str]) -> bool:
    """判定一行是否为合法的 Peaks 数据行。

    合法条件:
      1. 字段数 >= 1 + 16 计数列
      2. 第一个字段非空 (时间戳)
      3. 16 个计数列字段全为整数 (仅检查 f[1:1+_N_CH]，波长列不参与)
    """
    if len(f) < 1 + _N_CH or not f[0].strip():
        return False
    for x in f[1:1 + _N_CH]:
        s = x.strip()
        if not s:
            return False
        try:
            int(s)
        except ValueError:
            return False
    return True


def _find_peaks_header(lines: list[str]) -> int | None:
    """返回数据表头行号；非 Peaks 文件返回 None。"""
    for i, ln in enumerate(lines):
        if ln.startswith("Timestamp\t# CH 1"):
            return i
    return None


def _wl_column_name(flat_index: int, channel: int, k: int) -> str:
    """波长列命名钩子。flat_index 从 0 起；channel 为 1..16；k 为该通道内第几个峰(从1起)。"""
    return f"w{flat_index + 1}"


def parse_hyperion_peaks(lines: list[str], header_idx: int) -> pd.DataFrame:
    """解析 Hyperion Peaks 数据块。

    行布局: <时间戳> \\t <16个峰值数量整数> \\t <sum(数量)个波长>。
    '# CH n' 是峰值数量，不是波长。返回 [Timestamp, <N个波长列>]，
    按通道槽位对齐（峰丢失填 NaN），列集合在全文件内稳定。
    """
    # 1) 扫描数据行，按"每通道峰数的全局最大值"确定稳定槽位布局
    raw_rows: list[list[str]] = []
    max_counts = [0] * _N_CH
    skipped_count = 0
    _diag_printed = 0
    for ln in lines[header_idx + 1:]:
        ln = ln.rstrip("\r")
        if not ln.strip():
            continue
        f = ln.split("\t")
        if not _is_peaks_data_row(f):
            if _diag_printed < 3:
                import sys
                print(
                    f"[Peaks][skip] len={len(f)} f0={f[0]!r} "
                    f"counts(f[1:{1 + _N_CH}])={f[1:1 + _N_CH]!r}",
                    file=sys.stderr,
                )
                _diag_printed += 1
            skipped_count += 1
            continue
        counts = [int(x.strip()) for x in f[1:1 + _N_CH]]
        for c in range(_N_CH):
            if counts[c] > max_counts[c]:
                max_counts[c] = counts[c]
        raw_rows.append(f)

    if skipped_count > 0:
        import sys
        print(f"[Peaks] 跳过 {skipped_count} 行畸形行 (共 {len(lines) - header_idx - 1} 行候选)",
              file=sys.stderr)

    if not raw_rows:
        # 0 行有效数据 → 硬错误，禁止静默成功
        preview = ""
        if header_idx + 1 < len(lines):
            preview = repr(lines[header_idx + 1][:200])
        raise ValueError(
            f"Peaks 解析得到 0 行有效数据；表头行={header_idx}，"
            f"首条原始数据行={preview}"
        )

    # 2) 固定列名（通道顺序展开槽位）
    slot_channel: list[int] = []  # 每个输出波长列对应的通道(1..16)
    for ch in range(_N_CH):
        slot_channel.extend([ch + 1] * max_counts[ch])
    n_wl = len(slot_channel)
    wl_names: list[str] = []
    per_ch_k = [0] * _N_CH
    for idx, ch in enumerate(slot_channel):
        per_ch_k[ch - 1] += 1
        wl_names.append(_wl_column_name(idx, ch, per_ch_k[ch - 1]))

    # 3) 逐行填充，通道槽位对齐，峰丢失填 NaN
    timestamps: list[str] = []
    matrix: list[list[float | None]] = []
    for f in raw_rows:
        counts = [int(x.strip()) for x in f[1:1 + _N_CH]]
        vals = f[1 + _N_CH:]  # 该行波长（可能含末尾空 pad）
        timestamps.append(f[0].strip())
        row_out: list[float | None] = [None] * n_wl
        vi = 0  # 该行波长游标
        oi = 0  # 输出槽位游标
        for ch in range(_N_CH):
            for _k in range(max_counts[ch]):
                if _k < counts[ch] and vi < len(vals):
                    txt = vals[vi].strip()
                    row_out[oi] = float(txt) if txt else None
                    vi += 1
                oi += 1
        matrix.append(row_out)

    df = pd.DataFrame(matrix, columns=wl_names)
    df.insert(0, "Timestamp", timestamps)
    return df


def _parse_peaks_rectangular(
    lines: list[str], header_idx: int
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Timestamp + N个标量列 的矩形 Peaks 文件（可带内嵌暗号行，时间戳可真可0）。

    返回 (df, annotation):
      df         — 列 = [Timestamp, w1..wN]; Timestamp 保留原字符串; w 列为 float
      annotation — {列名: 暗号字符串}，引号已剥，稀疏（未标注列不入字典）
    """
    annotation: dict[str, str] = {}
    data_start = header_idx + 1
    ann_cells: list[str] | None = None

    # ── 检测内嵌暗号行 ──
    if data_start < len(lines):
        c = lines[data_start].split("\t")
        if _looks_like_annotation_row(c):
            ann_cells = c
            data_start += 1

    # ── 取第一条真实数据行确定列宽 ──
    probe = ann_cells
    if probe is None:
        probe = next(
            (ln.split("\t") for ln in lines[data_start:] if ln.strip()), None
        )
    if probe is None:
        raise ValueError("矩形 Peaks 文件无数据行")

    width = len(probe)
    n_wl = width - 1
    wl_names = [f"w{i + 1}" for i in range(n_wl)]
    columns = ["Timestamp"] + wl_names

    # ── 从暗号行抽取 annotation ──
    if ann_cells is not None:
        for col, cell in zip(columns, ann_cells):
            cell = cell.strip()
            if cell:
                annotation[col] = _strip_code_quotes(cell)

    # ── 逐行填充 ──
    ts: list[str] = []
    mat: list[list[float | None]] = []
    for ln in lines[data_start:]:
        if not ln.strip():
            continue
        f = ln.split("\t")
        if len(f) < width:
            f = f + [""] * (width - len(f))
        f = f[:width]
        ts.append(f[0])
        mat.append(
            [float(x.strip()) if x.strip() else None for x in f[1:width]]
        )

    df = pd.DataFrame(mat, columns=wl_names)
    df.insert(0, "Timestamp", ts)
    return df, annotation


# ═══════════════════════════════════════════════════════════════════════
# Sensors 格式专用解析器 — 去 BOM + 暗号行抽取 + 混合引号剥离
# ═══════════════════════════════════════════════════════════════════════

def parse_enlight_sensors(
    path: str,
) -> tuple[pd.DataFrame, dict[str, str], dict[str, object]]:
    """解析 ENLIGHT Sensors（公式版）。

    支持两种变体:
      (a) 原始仪器导出: 无 BOM、有元数据块、Timestamp\\t 表头(不在第0行)、无暗号行
      (b) 旧保存样本:   有 BOM、无元数据块、Timestamp\\t 表头(在第0行)、有/无暗号行

    返回 (df, annotation, meta)：
      df         — 干净数值 DataFrame（不含暗号行；Timestamp 为字符串，其余列 float）
      annotation — {列名: 暗号字符串}，引号已剥；稀疏（未标注列不入字典）
      meta       — {format, header_idx, had_bom, annotation_row, num_data_cols, fbg_cols, ...}
    """
    import io as _io

    text: str = open(path, "rb").read().decode("utf-8-sig")  # ★ 兼容有无 BOM
    lines = [ln.rstrip("\r") for ln in text.split("\n")]

    # ── 定位真实表头: 以 "Timestamp\\t" 开头 (制表符, 排除 "Timestamp Format: Full" 空格行) ──
    header_idx: int | None = None
    for i, ln in enumerate(lines):
        if ln.startswith("Timestamp\t"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("未找到 Sensors 数据表头 (Timestamp\\t...)")

    header = lines[header_idx].split("\t")

    # ── 可选: 若误投 Peaks 文件, 第二列是 "# CH" 则提示 ──
    if len(header) > 1 and header[1].startswith("# CH"):
        raise ValueError(
            "这是 Peaks 计数列文件，请改用『ENLIGHT(光纤传感)』模板"
        )

    # ── 检测暗号行 (表头后紧跟的行) ──
    annotation: dict[str, str] = {}
    data_start = header_idx + 1
    if (
        data_start < len(lines)
        and lines[data_start].strip()
        and _looks_like_annotation_row(lines[data_start].split("\t"))
    ):
        ann = lines[data_start].split("\t")
        for col, cell in zip(header, ann):
            cell = cell.strip()
            if cell:
                annotation[str(col)] = _strip_code_quotes(cell)
        data_start += 1

    # ── 逐行解析数据 ──
    ncol = len(header)
    ts: list[str] = []
    rows: list[list[str | None]] = []
    for ln in lines[data_start:]:
        if not ln.strip():
            continue
        f = ln.split("\t")
        if len(f) < ncol:
            f = f + [""] * (ncol - len(f))
        f = f[:ncol]
        ts.append(f[0])
        rows.append(f[1:ncol])

    if not ts:
        raise ValueError("Sensors 文件无数据行")

    df = pd.DataFrame(rows, columns=header[1:ncol])
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.insert(0, "Timestamp", ts)

    # ── FBG_ 列识别 ──
    fbg_cols = [str(c) for c in header if str(c).startswith("FBG_")]

    # ═══════════════════════════════════════════════════════════════════
    # ★ 硬断言 — 杜绝静默失败（把元数据行当表头）
    # ═══════════════════════════════════════════════════════════════════
    if "Timestamp" not in df.columns:
        raise ValueError(
            f"Sensors 硬断言失败: Timestamp 列缺失, 表头={df.columns[:3].tolist()!r} "
            f"header_idx={header_idx}"
        )
    if len(df) < 1:
        raise ValueError(
            f"Sensors 硬断言失败: 无数据行 header_idx={header_idx}"
        )
    if len(df) < 10 and len(lines) > 200:
        # 文件很大但数据行极少 → 几乎肯定是表头定位错误
        raise ValueError(
            f"Sensors 硬断言失败: 文件 {len(lines)} 行但仅解析出 {len(df)} 行数据, "
            f"表头定位错误 header_idx={header_idx}, "
            f"首列={df.columns[0]!r}"
        )
    bad_cols = [c for c in df.columns if "(FBG):" in str(c) or "Range (" in str(c)]
    if bad_cols:
        raise ValueError(
            f"表头定位错误，疑似把元数据行当成了表头: {bad_cols[:3]!r} "
            f"(header_idx={header_idx})\n"
            f"请确认文件 Timestamp\\t 表头行号与 scan 结果一致。"
        )
    if fbg_cols:
        actual_fbg = len([c for c in df.columns if str(c).startswith("FBG_")])
        if actual_fbg != len(fbg_cols):
            raise ValueError(
                f"Sensors FBG 列数不一致: 表头声明 {len(fbg_cols)} 个 "
                f"({fbg_cols[:5]}...), DataFrame 实际 {actual_fbg} 个"
            )

    # ── 检测是否真的有 BOM ──
    had_bom = False
    try:
        with open(path, "rb") as f:
            had_bom = (f.read(3) == b"\xef\xbb\xbf")
    except Exception:
        pass

    meta: dict[str, object] = {
        "format": "hyperion_sensors",
        "header_idx": header_idx,
        "had_bom": had_bom,
        "annotation_row": (data_start != header_idx + 1),
        "num_data_cols": ncol - 1,
        "fbg_cols": fbg_cols,
    }
    return df, annotation, meta


# ═══════════════════════════════════════════════════════════════════════
# 分支实现
# ═══════════════════════════════════════════════════════════════════════

def _parse_hyperion_peaks(
    path: str, encoding: str, meta: dict, has_bom: bool
) -> tuple[pd.DataFrame, dict[str, str], dict]:
    """Hyperion Peaks 格式: 显式字段解析，消费 # CH 计数列，输出矩形波长表。"""
    use_enc = "utf-8-sig" if has_bom else encoding
    with open(path, "r", encoding=use_enc, errors="replace") as f:
        text = f.read()

    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    header_idx = _find_peaks_header(lines)
    if header_idx is None:
        raise ValueError("Hyperion Peaks: 未找到 Timestamp\t# CH 1 表头行")

    # ── 格式判别: 计数列(A) vs 矩形(B) ──
    # 1) 检测表头后是否紧跟内嵌暗号行，确定数据起点
    data_start = header_idx + 1
    if data_start < len(lines) and _looks_like_annotation_row(
        lines[data_start].split("\t")
    ):
        data_start += 1

    # 2) 取第一条真实数据行，判别 A/B
    first = next(
        (ln.split("\t") for ln in lines[data_start:] if ln.strip()), None
    )
    if first is None:
        raise ValueError("Peaks 文件无数据行")

    is_count_format = (
        len(first) > 1 + _N_CH
        and all(
            first[i].strip().lstrip("+-").isdigit()
            for i in range(1, 1 + _N_CH)
        )
    )

    # 3) 分流
    if is_count_format:
        df = parse_hyperion_peaks(lines, header_idx)
        annotation: dict[str, str] = {}
        meta["format"] = "hyperion_peaks_count"

        # 构建 channel_slots 供排查
        channel_slots: dict[str, int] = {}
        for col in df.columns:
            if str(col) == "Timestamp":
                continue
        n_wl = df.shape[1] - 1  # 减去 Timestamp
        meta["header_idx"] = header_idx
        meta["num_meta_skipped"] = header_idx
        meta["num_wavelength_cols"] = n_wl
        meta["channel_slots"] = channel_slots

        # 暗号提取 — 原始 Peaks 文件无暗号行，返回空 dict
        if len(df) > 0:
            first_row = df.iloc[0]
            for col in df.columns:
                val = str(first_row[col]).strip()
                m = _UNWRAP_RE.match(val)
                if m:
                    annotation[str(col)] = m.group(1).strip()
            # 若首行含引号包裹值（已嵌入暗号），删除该行
            if annotation:
                df = df.iloc[1:].reset_index(drop=True)
    else:
        df, annotation = _parse_peaks_rectangular(lines, header_idx)
        meta["format"] = "hyperion_peaks_rect"
        n_wl = df.shape[1] - 1
        meta["header_idx"] = header_idx
        meta["num_meta_skipped"] = header_idx
        meta["num_wavelength_cols"] = n_wl

    return df, annotation, meta


def _parse_hyperion_sensors(
    path: str, meta: dict, has_bom: bool = False
) -> tuple[pd.DataFrame, dict[str, str], dict]:
    """Hyperion Sensors 格式: 委托给 parse_enlight_sensors。"""
    df, annotation, sensors_meta = parse_enlight_sensors(path)

    # 合并 meta — Sensors 内部已定位真实 header_idx
    header_idx = sensors_meta.get("header_idx", 0)
    meta["header_idx"] = header_idx
    meta["num_meta_skipped"] = header_idx
    for key in ("format", "had_bom", "annotation_row", "num_data_cols", "fbg_cols"):
        if key in sensors_meta:
            meta[key] = sensors_meta[key]

    return df, annotation, meta


def _parse_legacy_enlight(
    path: str, encoding: str, meta: dict
) -> tuple[pd.DataFrame, dict[str, str], dict]:
    """Legacy ENLIGHT 解析 — 从原有 _parse_enlight_like 逻辑移植"""
    meta["header_idx"] = 0
    meta["num_meta_skipped"] = 0

    with open(path, "r", encoding=encoding, errors="replace") as f:
        raw_lines = f.readlines()

    def _first_field_is_timestamp(line: str) -> bool:
        fields = line.strip().split("\t")
        if not fields:
            return False
        first = fields[0].strip()
        # Match: YYYY-MM-DD, HH:MM:SS, mm:ss.s
        if _TIMESTAMP_LIKE.match(first):
            if not re.search(r"[a-zA-Z一-鿿]", first):
                return True
        if re.match(r'^\d{1,2}:\d{2}', first) and not re.search(r'[a-zA-Z]', first):
            return True
        return False

    data_start_line = None
    # Scan for 3 consecutive lines where first field is timestamp-like
    for i in range(min(len(raw_lines), 1000) - 2):
        trio = [raw_lines[i + o] for o in (0, 1, 2)]
        valid = [_first_field_is_timestamp(line) for line in trio]
        if sum(valid) >= 2 and any(valid):  # 宽松: 3个中至少2个匹配
            data_start_line = i
            break

    if data_start_line is None or data_start_line < 1:
        # Fallback: try reading as plain TSV with first non-empty line as header
        non_empty = [l for l in raw_lines if l.strip() and '\t' in l]
        if len(non_empty) >= 2:
            header_line = non_empty[0].strip()
            headers = [h.strip() for h in header_line.split('\t')]
            if len(headers) >= 2:
                data_rows = []
                for line in non_empty[1:]:
                    fields = line.strip().split('\t')
                    if len(fields) == len(headers):
                        data_rows.append(fields)
                if data_rows:
                    df = pd.DataFrame(data_rows, columns=headers)
                    for col in df.columns:
                        try:
                            df[col] = pd.to_numeric(df[col])
                        except (ValueError, TypeError):
                            pass
                    df.columns = df.columns.astype(str)
                    meta["header_idx"] = 0
                    meta["num_meta_skipped"] = 0
                    return df, {}, meta
        raise ValueError("Legacy ENLIGHT: 无法找到数据起始行")

    header_line_idx = data_start_line - 1
    header_line = raw_lines[header_line_idx].strip()
    headers = [h.strip() for h in header_line.split("\t") if h.strip()]

    if len(headers) < 2:
        raise ValueError("Legacy ENLIGHT: 表头列数不足")

    data_rows = []
    for line in raw_lines[data_start_line:]:
        stripped = line.strip()
        if not stripped:
            continue
        fields = stripped.split("\t")
        if len(fields) == len(headers):
            data_rows.append(fields)

    if not data_rows:
    # Fallback 2: variable-width columns -> try pd.read_csv directly
            try:
                df = pd.read_csv(path, sep="\t", encoding=encoding, low_memory=False, on_bad_lines="skip")
                df.columns = df.columns.astype(str)
                meta["header_idx"] = 0
                meta["num_meta_skipped"] = 0
                return df, {}, meta
            except Exception:
                raise ValueError("Legacy ENLIGHT: 无数据行")

    df = pd.DataFrame(data_rows, columns=headers)
    for col in df.columns:
        try:
            df[col] = pd.to_numeric(df[col])
        except (ValueError, TypeError):
            pass
    df.columns = df.columns.astype(str)

    meta["header_idx"] = header_line_idx
    meta["num_meta_skipped"] = header_line_idx

    return df, {}, meta


def _unwrap_quotes(val: str) -> str:
    """剥离首尾引号（直引+全角弯引）"""
    v = val.strip()
    m = _QUOTE_RE.match(v)
    return m.group(1).strip() if m else v


# ═══════════════════════════════════════════════════════════════════════
# 数值 / 波长列识别
# ═══════════════════════════════════════════════════════════════════════

def detect_numeric_wavelength_columns(
    df: pd.DataFrame, *, valid_ratio_threshold: float = 0.8,
) -> tuple[list[str], list[str]]:
    """识别数值列与波长列。

    数值列: pd.to_numeric(errors='coerce') 有效比例 >= valid_ratio_threshold
    波长列: 均值在 1500~1600 nm 之间的数值列

    Returns:
        (numeric_cols, wavelength_cols) — 都是 [(列名, 均值)] 列表
    """
    numeric_cols: list[str] = []
    wavelength_cols: list[str] = []

    for c in df.columns:
        col_name = str(c)
        try:
            series = pd.to_numeric(df[c], errors="coerce")
            valid_mask = series.notna()
            valid_count = valid_mask.sum()
            total = len(series)
            if total == 0:
                continue
            if valid_count / total >= valid_ratio_threshold:
                numeric_cols.append(col_name)
                mean_val = float(series[valid_mask].mean())
                if 1500.0 <= mean_val <= 1600.0:
                    wavelength_cols.append(col_name)
        except Exception:
            continue

    return numeric_cols, wavelength_cols
