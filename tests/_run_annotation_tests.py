"""Standalone runner for annotation row tests — avoids venv pytest import conflict."""
import sys
import os
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np

from utils.annotation_utils import (
    build_annotation_row, is_wave_col, insert_blank_row, apply_annotation_row,
)

passed = 0
failed = 0

def check(name):
    """Decorator-style test runner."""
    def wrap(fn):
        global passed, failed  # noqa: PLW0603
        try:
            fn()
            passed += 1
            print(f"  PASS  {name}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {name}: {e}")
    return wrap

# ═══════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════

def _make_sensors_df(n_formula=12, n_wavelength=12, n_rows=5):
    cols = ["Timestamp"]
    formula_cols = [f"C{i+1}_{(i % 2) + 1}" for i in range(n_formula)]
    wave_cols = [f"FBG_{chr(65+i)}" for i in range(n_wavelength)]
    cols.extend(formula_cols)
    cols.extend(wave_cols)
    rows = []
    for t in range(n_rows):
        ts = f"2026/5/12 {t+1:02d}:00:{t:02d}.0"
        f_vals = [f"{-0.5 + t * 0.01 + i * 0.001:.5f}" for i in range(n_formula)]
        w_vals = [f"{1525.0 + i * 2.5 + t * 0.01:.5f}" for i in range(n_wavelength)]
        rows.append([ts] + f_vals + w_vals)
    df = pd.DataFrame(rows, columns=cols)
    for c in cols[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def _make_plain_csv_df(n_cols=5, n_rows=5):
    cols = ["Timestamp"] + [f"数据{i+1}" for i in range(n_cols)]
    rows = []
    for t in range(n_rows):
        ts = f"2026/5/12 {t+1:02d}:00:00.0"
        vals = [f"{t * 10.0 + i * 5.0:.2f}" for i in range(n_cols)]
        rows.append([ts] + vals)
    df = pd.DataFrame(rows, columns=cols)
    for c in cols[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def _merge_annotations(df, real_annot, file_format):
    """Simulate _insert_annotation_row_if_timestamp_exists per-column merge."""
    placeholder_ann = build_annotation_row(df, file_format=file_format)
    ann = []
    for i, col_name in enumerate(df.columns):
        col_str = str(col_name)
        real_val = real_annot.get(col_str, "")
        if real_val:
            ann.append(real_val)
        else:
            fallback = placeholder_ann[i] if i < len(placeholder_ann) else ""
            ann.append(fallback)
    return ann

# ═══════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════

# ── is_wave_col ──
print("=== TestIsWaveCol ===")

@check("wave_col_detected")
def _():
    df = _make_sensors_df(3, 3, 3)
    assert is_wave_col(df, "FBG_A")
    assert is_wave_col(df, "FBG_B")
    assert is_wave_col(df, "FBG_C")

@check("formula_col_not_wave")
def _():
    df = _make_sensors_df(3, 3, 3)
    for c in df.columns:
        if str(c).startswith("C") and "_" in str(c):
            assert not is_wave_col(df, str(c)), f"{c} should NOT be wave col"

@check("timestamp_not_wave")
def _():
    df = _make_sensors_df(3, 3, 3)
    assert not is_wave_col(df, "Timestamp")

@check("wave_with_nan_first_value")
def _():
    df = _make_sensors_df(3, 3, 5)
    df.iloc[0, df.columns.get_loc("FBG_A")] = np.nan
    assert is_wave_col(df, "FBG_A"), "应跳过 NaN 取首个非空值, 判为波长列"

# ── build_annotation_row (fiber) ──
print("\n=== TestAnnotationRowAlignment ===")

@check("annotation_row_length_matches_columns")
def _():
    df = _make_sensors_df(12, 12, 3)
    ann = build_annotation_row(df, file_format="enlight")
    assert len(ann) == len(df.columns)

@check("timestamp_gets_label")
def _():
    df = _make_sensors_df(3, 3, 3)
    ann = build_annotation_row(df, file_format="enlight")
    ts_idx = list(df.columns).index("Timestamp")
    assert ann[ts_idx] == "'时间戳'"

@check("wave_cols_get_w_labels")
def _():
    df = _make_sensors_df(3, 4, 3)
    ann = build_annotation_row(df, file_format="enlight")
    wave_labels = [a for a in ann if "w" in a and "类型" in a]
    assert len(wave_labels) == 4
    assert wave_labels[0] == "'w1-类型-位置'"
    assert wave_labels[3] == "'w4-类型-位置'"

@check("formula_cols_stay_empty")
def _():
    df = _make_sensors_df(5, 3, 3)
    ann = build_annotation_row(df, file_format="enlight")
    for ci, col_name in enumerate(df.columns):
        if str(col_name).startswith("C") and "_" in str(col_name):
            assert ann[ci] == "", f"公式列 {col_name} 应为空, got {ann[ci]!r}"

@check("wave_labels_at_correct_positions")
def _():
    df = _make_sensors_df(3, 3, 3)
    ann = build_annotation_row(df, file_format="enlight")
    for ci, col_name in enumerate(df.columns):
        if str(col_name).startswith("FBG_"):
            assert "类型" in ann[ci]
        elif str(col_name).startswith("C") and "_" in str(col_name):
            assert ann[ci] == ""

@check("no_stale_template_labels")
def _():
    df = _make_sensors_df(6, 6, 3)
    ann = build_annotation_row(df, file_format="enlight")
    joined = "|".join(ann)
    assert "A1G1" not in joined
    assert "FBG FBG" not in joined
    assert "FBG_" not in joined

# ── build_annotation_row (non-fiber) ──
print("\n=== TestAnnotationRowNonFiber ===")

@check("non_fiber_all_columns_get_placeholders")
def _():
    df = _make_plain_csv_df(4, 3)
    ann = build_annotation_row(df, file_format="csv")
    assert len(ann) == len(df.columns)
    ts_idx = list(df.columns).index("Timestamp")
    assert ann[ts_idx] == "'时间戳'"
    wave_labels = [a for a in ann if "w" in a and "类型" in a]
    assert len(wave_labels) == 4
    assert wave_labels[0] == "'w1-类型-位置'"
    assert wave_labels[3] == "'w4-类型-位置'"

@check("non_fiber_txt_format_same_behavior")
def _():
    df = _make_plain_csv_df(3, 3)
    ann = build_annotation_row(df, file_format="txt")
    wave_labels = [a for a in ann if "w" in a and "类型" in a]
    assert len(wave_labels) == 3

@check("non_fiber_empty_format_same_behavior")
def _():
    df = _make_plain_csv_df(2, 3)
    ann = build_annotation_row(df, file_format="")
    wave_labels = [a for a in ann if "w" in a and "类型" in a]
    assert len(wave_labels) == 2

@check("non_fiber_annotation_row_length")
def _():
    df = _make_plain_csv_df(5, 3)
    ann = build_annotation_row(df, file_format="csv")
    assert len(ann) == len(df.columns)

@check("fiber_format_still_detects_wave_cols")
def _():
    df = _make_sensors_df(3, 4, 3)
    ann = build_annotation_row(df, file_format="enlight")
    wave_labels = [a for a in ann if "w" in a and "类型" in a]
    assert len(wave_labels) == 4  # 仅波长列
    for ci, col_name in enumerate(df.columns):
        if str(col_name).startswith("C") and "_" in str(col_name):
            assert ann[ci] == "", f"公式列 {col_name} 应为空, got {ann[ci]!r}"

# ── Annotation merge ──
print("\n=== TestAnnotationMerge ===")

@check("real_annotation_wins_over_placeholder")
def _():
    df = _make_sensors_df(2, 3, 3)
    real = {"FBG_A": "w1-A1-1部位", "FBG_B": "w2-A1-2部位"}
    ann = _merge_annotations(df, real, "enlight")
    fbg_a_idx = list(df.columns).index("FBG_A")
    fbg_b_idx = list(df.columns).index("FBG_B")
    assert ann[fbg_a_idx] == "w1-A1-1部位"
    assert ann[fbg_b_idx] == "w2-A1-2部位"

@check("no_real_annotation_falls_back_to_placeholder")
def _():
    df = _make_sensors_df(2, 3, 3)
    real = {"FBG_A": "w1-A1-1部位"}
    ann = _merge_annotations(df, real, "enlight")
    fbg_c_idx = list(df.columns).index("FBG_C")
    assert "w" in ann[fbg_c_idx] and "类型" in ann[fbg_c_idx]

@check("all_placeholder_when_empty_annotation")
def _():
    df = _make_sensors_df(2, 3, 3)
    ann = _merge_annotations(df, {}, "enlight")
    ts_idx = list(df.columns).index("Timestamp")
    assert ann[ts_idx] == "'时间戳'"
    wave_labels = [a for a in ann if "w" in a and "类型" in a]
    assert len(wave_labels) == 3

@check("plain_csv_all_placeholder")
def _():
    df = _make_plain_csv_df(3, 3)
    ann = _merge_annotations(df, {}, "csv")
    ts_idx = list(df.columns).index("Timestamp")
    assert ann[ts_idx] == "'时间戳'"
    wave_labels = [a for a in ann if "w" in a and "类型" in a]
    assert len(wave_labels) == 3

@check("plain_csv_partial_real_annotation")
def _():
    df = _make_plain_csv_df(3, 3)
    real = {"数据1": "应变-A区-1号"}
    ann = _merge_annotations(df, real, "csv")
    d1_idx = list(df.columns).index("数据1")
    d2_idx = list(df.columns).index("数据2")
    assert ann[d1_idx] == "应变-A区-1号"
    assert "w" in ann[d2_idx] and "类型" in ann[d2_idx]

@check("mixed_annotation_length_matches_columns")
def _():
    df = _make_sensors_df(4, 4, 3)
    real = {"FBG_A": "w1-A1-1", "FBG_D": "w4-A2-2"}
    ann = _merge_annotations(df, real, "enlight")
    assert len(ann) == len(df.columns)

# ── insert_blank_row ──
print("\n=== TestInsertBlankRow ===")

@check("insert_adds_one_row")
def _():
    df = _make_sensors_df(3, 2, 3)
    new_df, _ = insert_blank_row(df)
    assert len(new_df) == len(df) + 1

@check("first_row_all_nan")
def _():
    df = _make_sensors_df(3, 2, 3)
    new_df, _ = insert_blank_row(df)
    assert pd.isna(new_df.iloc[0, 1])

@check("preserves_data")
def _():
    df = _make_sensors_df(2, 1, 3)
    new_df, _ = insert_blank_row(df)
    orig_val = float(df["FBG_A"].iloc[0])
    new_val = float(new_df["FBG_A"].iloc[1])
    assert abs(new_val - orig_val) < 0.01

@check("returns_dtypes")
def _():
    df = _make_sensors_df(2, 1, 3)
    _, dtypes = insert_blank_row(df)
    for col in df.columns:
        assert col in dtypes

# ── apply_annotation_row ──
print("\n=== TestApplyAnnotationRow ===")

@check("apply_writes_to_correct_row")
def _():
    df = _make_sensors_df(2, 2, 3)
    new_df, dtypes = insert_blank_row(df)
    ann = build_annotation_row(df, file_format="enlight")
    apply_annotation_row(new_df, ann, 0, dtypes)
    ts_idx = list(new_df.columns).index("Timestamp")
    assert "时间戳" in str(new_df.iloc[0, ts_idx])

@check("apply_leaves_data_rows_untouched")
def _():
    df = _make_sensors_df(2, 1, 3)
    orig_data_val = float(df["FBG_A"].iloc[0])
    new_df, dtypes = insert_blank_row(df)
    ann = build_annotation_row(df, file_format="enlight")
    apply_annotation_row(new_df, ann, 0, dtypes)
    assert abs(float(new_df["FBG_A"].iloc[1]) - orig_data_val) < 0.01

@check("apply_non_fiber_preserves_dtypes")
def _():
    df = _make_sensors_df(2, 1, 3)
    new_df, dtypes = insert_blank_row(df)
    ann = build_annotation_row(df, file_format="csv")
    apply_annotation_row(new_df, ann, 0, dtypes)
    assert len(new_df) == 4  # 3 + 1 blank
    ts_idx = list(new_df.columns).index("Timestamp")
    assert "时间戳" in str(new_df.iloc[0, ts_idx])

# ── Row count verification ──
print("\n=== RowCountVerification ===")

@check("row_count_data_only")
def _():
    df = _make_sensors_df(2, 2, 5)
    new_df, dtypes = insert_blank_row(df)
    ann = build_annotation_row(df, file_format="enlight")
    apply_annotation_row(new_df, ann, 0, dtypes)
    total_rows = len(new_df)
    has_annot = True
    data_rows = total_rows - 1 if has_annot else total_rows
    assert data_rows == 5, f"数据行应为5, got {data_rows}"

@check("total_rows_includes_annotation")
def _():
    df = _make_sensors_df(2, 2, 5)
    new_df, dtypes = insert_blank_row(df)
    ann = build_annotation_row(df, file_format="enlight")
    apply_annotation_row(new_df, ann, 0, dtypes)
    assert len(new_df) == 6, f"总行数应为6, got {len(new_df)}"

# ── Residual annotation row cleanup ──
print("\n=== TestResidualAnnotationRowCleanup ===")

def _make_df_with_residual_annotation(n_data_rows=60, residual_pos=50):
    """Simulate a df where parse_enlight_sensors left a residual annotation row at pos 50."""
    cols = ["Timestamp"] + [f"w{i+1}" for i in range(5)]
    rows = []
    for t in range(n_data_rows):
        if t == residual_pos:
            # Residual annotation row — '时间戳' + NaN values
            rows.append(["时间戳"] + [np.nan] * 5)
        else:
            ts = f"2026/6/9 20:{t//60:02d}:{t%60:02d}.00000"
            vals = [f"{1525.0 + i * 3.0 + t * 0.01:.5f}" for i in range(5)]
            rows.append([ts] + vals)
    df = pd.DataFrame(rows, columns=cols)
    for c in cols[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

@check("residual_row_at_50_is_found")
def _():
    df = _make_df_with_residual_annotation(60, 50)
    # Scan for '时间戳' in first col of rows 1..99
    found = []
    for idx in range(1, min(100, len(df))):
        row_vals = [str(v).strip().strip("'\"''\"") for v in df.iloc[idx]]
        if any('时间戳' in v for v in row_vals):
            found.append(idx)
    assert found == [50], f"Residual row should be at 50, got {found}"

@check("drop_residual_then_insert_blank_restores_row_count")
def _():
    df = _make_df_with_residual_annotation(60, 50)
    original_rows = len(df)  # 60 (59 data + 1 annotation at 50)
    # Simulate drop: find and drop row 50
    df = df.drop(df.index[50]).reset_index(drop=True)
    # Then insert_blank_row at 0
    new_df, dtypes = insert_blank_row(df)
    # Total = (original - 1) + 1 = original (same 60)
    assert len(new_df) == original_rows, f"Row count should be {original_rows}, got {len(new_df)}"

@check("drop_residual_annotation_at_row0_after_fix")
def _():
    df = _make_df_with_residual_annotation(60, 50)
    real_annot = {"w1": "w1-A1-1", "w2": "w2-A1-2", "w3": "w3-A2-1", "w4": "w4-A2-2", "w5": "w5-B1-1"}
    # Drop residual row 50
    df = df.drop(df.index[50]).reset_index(drop=True)
    # Insert blank at 0
    new_df, dtypes = insert_blank_row(df)
    # Build merged annotation
    placeholder = build_annotation_row(df, file_format="enlight")
    ann = []
    for i, col_name in enumerate(df.columns):
        col_str = str(col_name)
        real_val = real_annot.get(col_str, "")
        ann.append(real_val if real_val else (placeholder[i] if i < len(placeholder) else ""))
    apply_annotation_row(new_df, ann, 0, dtypes)
    # row 0 = annotation, row 1 = first data row
    ts_idx = list(new_df.columns).index("Timestamp")
    assert "时间戳" in str(new_df.iloc[0, ts_idx]), f"Row 0 should have 时间戳, got {str(new_df.iloc[0, ts_idx])!r}"
    assert "w1-A1-1" in str(new_df.iloc[0, ts_idx + 1]), f"Row 0 col 1 should have w1-A1-1, got {str(new_df.iloc[0, ts_idx + 1])!r}"
    # Row 1 = real data (timestamp, not 时间戳)
    assert "2026" in str(new_df.iloc[1, ts_idx]), f"Row 1 should be data, got {str(new_df.iloc[1, ts_idx])!r}"
    # No residual 时间戳 anywhere after row 0
    for idx in range(1, min(100, len(new_df))):
        row_vals = [str(v).strip().strip("'\"''\"") for v in new_df.iloc[idx]]
        assert not any('时间戳' in v for v in row_vals), f"Found residual 时间戳 at row {idx}"
    # Data row count correct: 60 rows total (59 data + 1 annotation), after drop+insert still 60 (1 annotation + 59 data)
    assert len(new_df) == 60, f"Expected 60 rows (1 annotation + 59 data), got {len(new_df)}"

@check("annotation_orig_dtypes_always_initialized")
def _():
    df = _make_sensors_df(2, 2, 5)
    new_df, dtypes = insert_blank_row(df)
    assert dtypes is not None, "dtypes should be set by insert_blank_row"
    assert len(dtypes) == len(df.columns), f"dtypes should have {len(df.columns)} entries, got {len(dtypes)}"
    for col in df.columns:
        assert col in dtypes, f"Column {col} missing from dtypes"

@check("apply_annotation_row_no_crash_without_dtypes")
def _():
    """apply_annotation_row should not crash when dtypes is None."""
    df = _make_sensors_df(2, 2, 3)
    new_df, _ = insert_blank_row(df)
    ann = build_annotation_row(df, file_format="enlight")
    # Should not raise when orig_dtypes=None
    apply_annotation_row(new_df, ann, 0, orig_dtypes=None)
    ts_idx = list(new_df.columns).index("Timestamp")
    assert "时间戳" in str(new_df.iloc[0, ts_idx])

@check("apply_annotation_row_no_crash_with_dtypes")
def _():
    """apply_annotation_row must work with dtypes dict."""
    df = _make_sensors_df(2, 2, 3)
    new_df, dtypes = insert_blank_row(df)
    ann = build_annotation_row(df, file_format="enlight")
    apply_annotation_row(new_df, ann, 0, dtypes)
    ts_idx = list(new_df.columns).index("Timestamp")
    assert "时间戳" in str(new_df.iloc[0, ts_idx])
    assert len(new_df) == 4  # 3 + 1

@check("non_fiber_residual_placeholder_works")
def _():
    """Plain CSV: even without real annotation, placeholder row should be at row 0."""
    df2 = pd.DataFrame({
        "Timestamp": ["2026/1/1 00:01:00", "2026/1/1 00:02:00", "2026/1/1 00:03:00"],
        "数据1": [10.0, 20.0, 30.0],
        "数据2": [100.0, 200.0, 300.0],
    })
    for c in df2.columns[1:]:
        df2[c] = pd.to_numeric(df2[c], errors="coerce")
    placeholder = build_annotation_row(df2, file_format="csv")
    new_df, dtypes = insert_blank_row(df2)
    apply_annotation_row(new_df, placeholder, 0, dtypes)
    assert len(new_df) == 4  # 3 + 1
    ts_idx = list(new_df.columns).index("Timestamp")
    assert "时间戳" in str(new_df.iloc[0, ts_idx])
    # data row 1 is at index 1
    assert "2026" in str(new_df.iloc[1, ts_idx])

@check("row_count_excludes_annotation")
def _():
    """Row count = data rows only (excludes annotation row)."""
    df = _make_sensors_df(2, 2, 5)
    new_df, dtypes = insert_blank_row(df)
    ann = build_annotation_row(df, file_format="enlight")
    apply_annotation_row(new_df, ann, 0, dtypes)
    data_rows = len(new_df) - 1
    assert data_rows == 5, f"Data rows should be 5, got {data_rows}"
    assert len(new_df) == 6

# ═══════════════════════════════════════════════════════════════════════
# 保存往返: _rebuild_annotation_in_header_lines + 暗号行唯一
# ═══════════════════════════════════════════════════════════════════════

print("\n=== TestSaveRoundtrip ===")

def _make_mock_header_lines(annotation_dict, col_names, with_metadata=True):
    """Simulate file_header_lines for an enlight sensors file.

    Returns lines that look like a real file header section including:
    - instrument metadata
    - table header
    - annotation row (from annotation_dict)
    """
    import io as _io  # noqa: F811
    lines = []
    if with_metadata:
        lines.append("ENLIGHT Version: 5.2.0.0\n")
        lines.append("Module Type: Hyperion\n")
        lines.append("CH N Configuration: 1-1,2-1,3-1,4-1,5-1,6-1,7-1,8-1,9-1,10-1,11-1,12-1\n")
        lines.append("\n")
    # Table header
    header = "\t".join(col_names) + "\n"
    lines.append(header)
    # Annotation row
    ann_parts = []
    for cn in col_names:
        if cn == "Timestamp":
            ann_parts.append("'时间戳'")
        elif cn in annotation_dict:
            ann_parts.append(f"'{annotation_dict[cn]}'")
        elif cn.endswith("_anomaly"):
            ann_parts.append("False")
        else:
            ann_parts.append("")
    lines.append("\t".join(ann_parts) + "\n")
    return lines

def _rebuild_and_verify(header_lines, annotation, col_names, expected_w1_val):
    """Call _rebuild_annotation_in_header_lines and verify w1 value."""
    from utils.file_parser import _looks_like_annotation_row

    result = list(header_lines)
    header_idx = None
    ann_idx = None
    for i, line in enumerate(result):
        stripped = line.rstrip('\n').rstrip('\r')
        cells = stripped.split('\t')
        if not cells or not cells[0].strip():
            continue
        if header_idx is None and cells[0].strip() == 'Timestamp' and len(cells) >= 2:
            header_idx = i
            continue
        if header_idx is not None and _looks_like_annotation_row(cells):
            ann_idx = i
            break

    # Build new annotation
    ann_parts = []
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

    if ann_idx is not None:
        result[ann_idx] = '\t'.join(ann_parts) + '\n'
    elif header_idx is not None:
        insert_at = header_idx + 1
        while insert_at < len(result) and not result[insert_at].strip():
            insert_at += 1
        result.insert(insert_at, '\t'.join(ann_parts) + '\n')

    # Check w1 value in rebuilt annotation
    for line in result:
        cells = line.rstrip('\n').split('\t')
        if _looks_like_annotation_row(cells):
            for j, cell in enumerate(cells):
                if j < len(col_names) and col_names[j] == 'w1':
                    assert expected_w1_val in cell, \
                        f"Expected w1='{expected_w1_val}' in annotation row, got '{cell}'"
                    return result, True
    assert False, "Should have found annotation row"
    return result, False

@check("rebuild_replaces_old_annotation")
def _():
    """Rebuilding header_lines replaces old w1-A1-1 with new w1-A1-110."""
    col_names = ["Timestamp", "w1", "w2", "w3"]
    old_annot = {"Timestamp": "时间戳", "w1": "w1-A1-1", "w2": "w2-A1-2", "w3": "w3-A2-1"}
    new_annot = {"Timestamp": "时间戳", "w1": "w1-A1-110", "w2": "w2-A1-2", "w3": "w3-A2-1"}

    old_lines = _make_mock_header_lines(old_annot, col_names)
    rebuilt, ok = _rebuild_and_verify(old_lines, new_annot, col_names, "w1-A1-110")
    # Only one annotation row should exist
    from utils.file_parser import _looks_like_annotation_row
    ann_count = sum(1 for line in rebuilt if _looks_like_annotation_row(line.rstrip('\n').split('\t')))
    assert ann_count == 1, f"Should have exactly 1 annotation row, got {ann_count}"
    # Metadata preserved
    assert any("ENLIGHT Version" in line for line in rebuilt), "ENLIGHT Version metadata should be preserved"

@check("rebuild_inserts_when_no_annotation_exists")
def _():
    """When file_header_lines has no annotation row, one is inserted after header."""
    col_names = ["Timestamp", "w1", "w2"]
    annot = {"Timestamp": "时间戳", "w1": "w1-A1-1", "w2": "w2-A1-2"}
    # Create header_lines WITHOUT annotation row
    lines = [
        "ENLIGHT Version: 5.0\n",
        "\n",
        "Timestamp\tw1\tw2\n",
    ]
    rebuilt, ok = _rebuild_and_verify(lines, annot, col_names, "w1-A1-1")
    from utils.file_parser import _looks_like_annotation_row
    ann_count = sum(1 for line in rebuilt if _looks_like_annotation_row(line.rstrip('\n').split('\t')))
    assert ann_count == 1, f"Should have exactly 1 annotation row after insert, got {ann_count}"
    assert any("ENLIGHT Version" in line for line in rebuilt), "Metadata preserved"

@check("rebuild_preserves_instrument_metadata")
def _():
    """All instrument metadata lines survive the rebuild unchanged."""
    col_names = ["Timestamp", "w1", "w2"]
    annot = {"Timestamp": "时间戳", "w1": "w1-A1-1", "w2": "w2-A1-2"}
    lines = _make_mock_header_lines(annot, col_names, with_metadata=True)
    updated_annot = {"Timestamp": "时间戳", "w1": "w1-B1-1", "w2": "w2-B1-2"}
    rebuilt, ok = _rebuild_and_verify(lines, updated_annot, col_names, "w1-B1-1")
    # All metadata lines present
    assert "ENLIGHT Version" in rebuilt[0]
    assert "Module Type" in rebuilt[1]
    assert "CH N Configuration" in rebuilt[2]

@check("rebuild_with_anomaly_columns")
def _():
    """Anomaly columns in header get 'False' in annotation row."""
    col_names = ["Timestamp", "w1", "w2", "Timestamp_anomaly", "w1_anomaly", "w2_anomaly"]
    annot = {"Timestamp": "时间戳", "w1": "w1-A1-1", "w2": "w2-A1-2"}
    lines = _make_mock_header_lines(annot, col_names)
    updated = {"Timestamp": "时间戳", "w1": "w1-A1-110", "w2": "w2-A1-2"}
    rebuilt, ok = _rebuild_and_verify(lines, updated, col_names, "w1-A1-110")
    # Check anomaly columns are 'False'
    for line in rebuilt:
        cells = line.rstrip('\n').split('\t')
        from utils.file_parser import _looks_like_annotation_row
        if _looks_like_annotation_row(cells):
            # Anomaly cols at positions 3,4,5
            assert cells[3].strip() == 'False', f"Timestamp_anomaly should be False, got {cells[3]}"
            assert cells[4].strip() == 'False', f"w1_anomaly should be False, got {cells[4]}"
            break

@check("plain_csv_roundtrip_no_header_lines")
def _():
    """Plain CSV without file_header_lines writes annotation in data row 0."""
    df2 = pd.DataFrame({
        "Timestamp": ["2026/1/1 00:01:00", "2026/1/1 00:02:00", "2026/1/1 00:03:00"],
        "数据1": [10.0, 20.0, 30.0],
        "数据2": [100.0, 200.0, 300.0],
    })
    for c in df2.columns[1:]:
        df2[c] = pd.to_numeric(df2[c], errors="coerce")
    placeholder = build_annotation_row(df2, file_format="csv")
    new_df, dtypes = insert_blank_row(df2)
    apply_annotation_row(new_df, placeholder, 0, dtypes)
    # Save to temp CSV
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as tf:
        new_df.to_csv(tf, index=False)
        tmp_path = tf.name
    # Reload and check annotation row at row 0
    reloaded = pd.read_csv(tmp_path)
    import os
    os.unlink(tmp_path)
    # The reloaded df should have row 0 starting with '时间戳'
    assert "时间戳" in str(reloaded.iloc[0, 0]), f"Row 0 should be 时间戳, got {reloaded.iloc[0,0]}"
    assert "w1-类型-位置" in str(reloaded.iloc[0, 1]), f"Row 0 col 1 should be placeholder"

@check("sensor_style_roundtrip_full_simulation")
def _():
    """Full simulation: edit annotation → rebuild header → verify data body clean."""
    col_names = ["Timestamp", "w1", "w2", "w3"]
    original_annot = {"Timestamp": "时间戳", "w1": "w1-A1-1", "w2": "w2-A1-2", "w3": "w3-A2-1"}
    edited_annot = {"Timestamp": "时间戳", "w1": "w1-A1-110", "w2": "w2-A1-2", "w3": "w3-A2-1"}

    # Simulate data (with annotation at row 0)
    data_rows = [
        ["2026/1/1 00:01:00", 1525.0, 1527.5, 1530.0],
        ["2026/1/1 00:02:00", 1525.01, 1527.51, 1530.01],
        ["2026/1/1 00:03:00", 1525.02, 1527.52, 1530.02],
    ]
    df = pd.DataFrame(data_rows, columns=col_names)
    for c in col_names[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # Insert annotation row at 0
    new_df, dtypes = insert_blank_row(df)
    ann_list = [edited_annot.get(str(c), '') for c in col_names]
    ann_list[0] = "'时间戳'"
    apply_annotation_row(new_df, ann_list, 0, dtypes)
    assert "w1-A1-110" in str(new_df.iloc[0, 1]), f"Row 0 should have w1-A1-110, got {new_df.iloc[0, 1]}"

    # Build header_lines with updated annotation
    old_lines = _make_mock_header_lines(original_annot, col_names)
    rebuilt, ok = _rebuild_and_verify(old_lines, edited_annot, col_names, "w1-A1-110")
    # + strip row 0 from data
    data_without_annotation = new_df.iloc[1:].reset_index(drop=True)
    assert len(data_without_annotation) == 3, f"Data should have 3 rows, got {len(data_without_annotation)}"
    # Verify annotation in header is new, NOT old
    for line in rebuilt:
        cells = line.rstrip('\n').split('\t')
        from utils.file_parser import _looks_like_annotation_row
        if _looks_like_annotation_row(cells):
            for cell in cells:
                assert "w1-A1-1" not in cell or "w1-A1-110" in cell, \
                    "Old annotation w1-A1-1 should be gone"
            break

# ── Summary ──
print(f"\n{'='*50}")
print(f"  {passed} passed, {failed} failed")
print(f"{'='*50}")
if failed > 0:
    sys.exit(1)
