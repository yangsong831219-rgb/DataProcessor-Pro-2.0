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
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        header_line = None
        for i, line in enumerate(lines):
            if 'Timestamp' in line and '# CH' in line:
                header_line = i
                break

        if header_line is None:
            raise ValueError("无法找到数据头行")

        data_start = header_line + 1
        data_rows = []
        for line in lines[data_start:]:
            if not line.strip():
                continue
            parts = line.strip().split('\t')
            data_rows.append(parts[:25])  # 17 header cols + up to 8 wavelength cols

        df = pd.DataFrame(data_rows)
        n_cols = len(df.columns)
        col_names = ['时间'] + [f'CH{i}计数' for i in range(1, 17)]
        wave_count = n_cols - len(col_names)
        col_names += [f'波长{i}' for i in range(1, wave_count + 1)]
        df.columns = col_names[:n_cols]

        for col in df.columns:
            try:
                df[col] = pd.to_numeric(df[col])
            except (ValueError, TypeError):
                pass
        df.columns = df.columns.astype(str)
        return df

    if hasattr(template, 'columns') and template.columns:
        df.columns = [col['name'] for col in template.columns]

    df.columns = df.columns.astype(str)
    return df
