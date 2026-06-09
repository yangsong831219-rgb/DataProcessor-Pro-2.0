"""文件解析器 — 自动检测分隔符、表头、编码，解析 CSV/TXT/ENLIGHT 格式

从 main.py 抽离的纯函数，无 UI 依赖。
"""

from __future__ import annotations

import csv
import re
from typing import Any, Optional, Tuple

import pandas as pd


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
        return df

    elif template.file_format in ('xlsx', 'xls'):
        df = pd.read_excel(file_path, header=None)
        df.columns = df.columns.astype(str)
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
_TIMESTAMP_LIKE = re.compile(r'^\d{2,4}[-/.]\d{1,2}[-/.]\d{1,2}')


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
    import struct

    meta: dict = {"format": "unknown", "header_idx": 0, "num_meta_skipped": 0}

    # ── 读取前 200 行做内容感知探查 ──
    probe_enc = encoding or "utf-8"
    try:
        with open(path, "r", encoding=probe_enc, errors="replace") as f:
            probe_lines = [f.readline() for _ in range(200)]
    except UnicodeDecodeError:
        probe_enc = "gb18030"
        with open(path, "r", encoding=probe_enc, errors="replace") as f:
            probe_lines = [f.readline() for _ in range(200)]

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

    # ── 分支 1: Hyperion Peaks ──
    if is_hyperion:
        meta["format"] = "hyperion_peaks"
        return _parse_hyperion_peaks(path, probe_enc, meta, has_bom)

    # ── 分支 2: Hyperion Sensors ──
    if has_bom and has_timestamp_header:
        meta["format"] = "hyperion_sensors"
        return _parse_hyperion_sensors(path, meta)

    # ── 分支 3: Legacy ──
    meta["format"] = "legacy_enlight"
    return _parse_legacy_enlight(path, probe_enc, meta)


def _parse_hyperion_peaks(
    path: str, encoding: str, meta: dict, has_bom: bool
) -> tuple[pd.DataFrame, dict[str, str], dict]:
    """Hyperion Peaks 格式:
      - 前 N 行是元数据
      - 找到首行 "Timestamp\t..." → header_idx
      - pd.read_csv 跳过元数据行
      - 逐元素清理 \r
      - 首行数据是暗号行，抽出
    """
    # 找出 header 所在行
    header_idx = 0
    with open(path, "r", encoding=encoding, errors="replace") as f:
        for i, line in enumerate(f):
            if line.startswith("Timestamp\t"):
                header_idx = i
                break

    if header_idx == 0:
        raise ValueError("Hyperion Peaks: 未找到 Timestamp 表头行")

    meta["header_idx"] = header_idx
    meta["num_meta_skipped"] = header_idx

    # 读取 — 用 lineterminator 处理双 CR
    use_enc = "utf-8-sig" if has_bom else encoding
    df = pd.read_csv(
        path, sep="\t", skiprows=header_idx,
        encoding=use_enc, dtype=str, low_memory=False,
        lineterminator="\n", on_bad_lines="skip",
    )

    # ── 归一化空列名 (trailing tab 导致空字符串) ──
    df.columns = [
        c if (isinstance(c, str) and c.strip()) else f"Unnamed: {i}"
        for i, c in enumerate(df.columns)
    ]

    # 清理: 每格 strip + rstrip('\r')
    for col in df.columns:
        df[col] = df[col].apply(
            lambda x: str(x).strip().rstrip("\r") if pd.notna(x) else ""
        )

    # 首行 = 暗号行
    annotation: dict[str, str] = {}
    if len(df) > 0:
        first_row = df.iloc[0]
        for col in df.columns:
            val = str(first_row[col]).strip()
            m = _UNWRAP_RE.match(val)
            if m:
                annotation[str(col)] = m.group(1).strip()
        # 删除暗号行
        df = df.iloc[1:].reset_index(drop=True)

    # 列名去引号
    df.columns = [_unwrap_quotes(str(c)) for c in df.columns]
    df.columns = df.columns.astype(str)

    return df, annotation, meta


def _parse_hyperion_sensors(
    path: str, meta: dict
) -> tuple[pd.DataFrame, dict[str, str], dict]:
    """Hyperion Sensors 格式:
      - UTF-8 BOM
      - 首行即 "Timestamp\t..." 表头
      - 使用 utf-8-sig 编码
    """
    meta["header_idx"] = 0
    meta["num_meta_skipped"] = 0

    df = pd.read_csv(
        path, sep="\t", encoding="utf-8-sig",
        dtype=str, low_memory=False,
        lineterminator="\n", on_bad_lines="skip",
    )

    # ── 归一化空列名 ──
    df.columns = [
        c if (isinstance(c, str) and c.strip()) else f"Unnamed: {i}"
        for i, c in enumerate(df.columns)
    ]

    for col in df.columns:
        df[col] = df[col].apply(
            lambda x: str(x).strip().rstrip("\r") if pd.notna(x) else ""
        )

    # 首行 = 暗号行
    annotation: dict[str, str] = {}
    if len(df) > 0:
        first_row = df.iloc[0]
        for col in df.columns:
            val = str(first_row[col]).strip()
            m = _UNWRAP_RE.match(val)
            if m:
                annotation[str(col)] = m.group(1).strip()
        df = df.iloc[1:].reset_index(drop=True)

    df.columns = [_unwrap_quotes(str(c)) for c in df.columns]
    df.columns = df.columns.astype(str)

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
