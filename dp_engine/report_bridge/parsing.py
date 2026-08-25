"""Restricted parsing for Bridge artifacts (Batch 3.3.1A).

All parsing MUST run on a Worker thread — never on the UI thread.

Supports: PNG/JPEG (pillow header), CSV (ParsedTable),
         JSON (TEXT_SOURCE with deterministic normalization),
         TXT (str).

Freezes parse_constant for NaN/Infinity/-Infinity rejection.
"""

from __future__ import annotations

import csv
import io
import json as _json_module
import logging
from pathlib import Path
from typing import NoReturn

from dp_engine.report_bridge.models import (
    MAX_BRIDGE_CSV_CELL_CHARS,
    MAX_BRIDGE_CSV_COLS,
    MAX_BRIDGE_CSV_ROWS,
    MAX_BRIDGE_CSV_TOTAL_CELLS,
    MAX_BRIDGE_IMAGE_BYTES,
    MAX_BRIDGE_IMAGE_DIMENSION,
    MAX_BRIDGE_IMAGE_PIXELS,
    MAX_BRIDGE_JSON_DEPTH,
    MAX_BRIDGE_JSON_NODES,
    MAX_BRIDGE_JSON_RENDER_CHARS,
    MAX_BRIDGE_JSON_STRING_CHARS,
    MAX_BRIDGE_JSON_TOTAL_STRING_CHARS,
    MAX_BRIDGE_PARSE_FILE_BYTES,
    MAX_BRIDGE_TXT_CHARS,
    ParsedPayload,
    ParsedTable,
)

logger = logging.getLogger(__name__)


# ── Image validation (Pillow header-only, NOT full decode) ──


def validate_image_dimensions(file_path: Path) -> bool:
    """Validate image file dimensions without full decode.

    Uses Pillow to read only the image header (not full pixel data).
    Rejects decompression-bomb warnings/errors.

    Returns:
        True if dimensions are within limits.

    Raises:
        ValueError: Image is corrupt or exceeds limits.
    """
    from PIL import Image, ImageFile

    file_size = file_path.stat().st_size
    if file_size > MAX_BRIDGE_IMAGE_BYTES:
        raise ValueError("Image file exceeds MAX_BRIDGE_IMAGE_BYTES")

    # Configure Pillow to reject decompression bombs
    Image.MAX_IMAGE_PIXELS = MAX_BRIDGE_IMAGE_PIXELS + 1
    ImageFile.LOAD_TRUNCATED_IMAGES = False

    try:
        with Image.open(str(file_path)) as img:
            width, height = img.size
    except Image.DecompressionBombError:
        raise ValueError("Image is a decompression bomb — rejected")
    except Image.DecompressionBombWarning:
        raise ValueError("Image triggers decompression-bomb warning — rejected")
    except Exception as e:
        raise ValueError(f"Image is corrupt or unreadable: {e}")

    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid image dimensions: {width}x{height}")
    if width > MAX_BRIDGE_IMAGE_DIMENSION or height > MAX_BRIDGE_IMAGE_DIMENSION:
        raise ValueError(
            f"Image dimension exceeds {MAX_BRIDGE_IMAGE_DIMENSION}px: {width}x{height}"
        )
    if width * height > MAX_BRIDGE_IMAGE_PIXELS:
        raise ValueError(
            f"Image pixel count {width * height} exceeds {MAX_BRIDGE_IMAGE_PIXELS}"
        )

    return True


# ── CSV parsing ──


def parse_csv_to_table(file_path: Path) -> ParsedTable:
    """Parse a CSV file into an immutable ParsedTable.

    Requirements:
    - UTF-8 or UTF-8-SIG strict decode
    - Reject NUL bytes
    - Controlled csv.field_size_limit
    - No unbounded reads
    - Empty CSV, no header, or non-rectangular → ValueError

    Returns:
        ParsedTable: ((header1, header2, ...), (row1_c1, row1_c2, ...), ...)
    """
    file_size = file_path.stat().st_size
    if file_size > MAX_BRIDGE_PARSE_FILE_BYTES:
        raise ValueError("CSV file exceeds MAX_BRIDGE_PARSE_FILE_BYTES")

    raw = file_path.read_bytes()
    if b"\x00" in raw:
        raise ValueError("CSV contains NUL byte — rejected")

    # Decode UTF-8 or UTF-8-SIG
    try:
        if raw.startswith(b"\xef\xbb\xbf"):
            text = raw.decode("utf-8-sig")
        else:
            text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"CSV is not valid UTF-8: {e}")

    if not text.strip():
        raise ValueError("CSV file is empty")

    # Set controlled field size limit
    saved_limit = csv.field_size_limit()
    try:
        csv.field_size_limit(MAX_BRIDGE_CSV_CELL_CHARS)
    except OverflowError:
        csv.field_size_limit(saved_limit)
    except Exception:
        pass

    try:
        reader = csv.reader(io.StringIO(text))
        rows: list[list[str]] = []

        for row in reader:
            rows.append(list(row))
            # Early bounds check
            if len(rows) > MAX_BRIDGE_CSV_ROWS + 1:
                raise ValueError(
                    f"CSV exceeds max {MAX_BRIDGE_CSV_ROWS} rows"
                )
    except csv.Error as e:
        raise ValueError(f"CSV parse error: {e}")
    finally:
        try:
            csv.field_size_limit(saved_limit)
        except Exception:
            pass

    if len(rows) < 2:
        raise ValueError("CSV must have at least a header row and one data row")

    header = rows[0]
    data_rows = rows[1:]

    # Validate columns
    n_cols = len(header)
    if n_cols < 1 or n_cols > MAX_BRIDGE_CSV_COLS:
        raise ValueError(
            f"CSV column count {n_cols} must be 1..{MAX_BRIDGE_CSV_COLS}"
        )

    # Validate rows
    n_rows = len(data_rows)
    if n_rows < 1 or n_rows > MAX_BRIDGE_CSV_ROWS:
        raise ValueError(
            f"CSV row count {n_rows} must be 1..{MAX_BRIDGE_CSV_ROWS}"
        )

    # Validate total cells
    total_cells = (n_rows + 1) * n_cols
    if total_cells > MAX_BRIDGE_CSV_TOTAL_CELLS:
        raise ValueError(
            f"CSV total cells {total_cells} exceeds {MAX_BRIDGE_CSV_TOTAL_CELLS}"
        )

    # Validate rectangular shape and cell chars
    for i, row in enumerate(data_rows):
        if len(row) != n_cols:
            raise ValueError(
                f"CSV is not rectangular: row {i + 1} has {len(row)} columns, "
                f"expected {n_cols}"
            )
        for j, cell in enumerate(row):
            if len(cell) > MAX_BRIDGE_CSV_CELL_CHARS:
                raise ValueError(
                    f"CSV cell [{i + 1}][{j}] exceeds {MAX_BRIDGE_CSV_CELL_CHARS} chars"
                )
    for cell in header:
        if len(cell) > MAX_BRIDGE_CSV_CELL_CHARS:
            raise ValueError(
                f"CSV header cell exceeds {MAX_BRIDGE_CSV_CELL_CHARS} chars"
            )

    # Build immutable ParsedTable
    header_tuple = tuple(str(c) for c in header)
    data_tuples = tuple(tuple(str(c) for c in row) for row in data_rows)
    return (header_tuple,) + data_tuples


# ── JSON parse_constant (freeze contract) ──


def _reject_non_finite(value: str) -> NoReturn:
    """Reject NaN, Infinity, -Infinity at parse stage.

    This is the parse_constant callback for json.loads().
    Must be called BEFORE json.dumps allow_nan=False as defense-in-depth.
    """
    raise ValueError("non-finite JSON number")


# ── JSON depth/node/string counting ──


def _count_json_structure(obj: object) -> tuple[int, int, int, int]:
    """Count depth, total nodes, max single string chars, total string chars.

    Returns: (max_depth, total_nodes, max_string_chars, total_string_chars)
    """
    def _walk(o: object, depth: int) -> tuple[int, int, int, int]:
        nodes = 1
        max_str = 0
        total_str = 0
        max_d = depth

        if isinstance(o, str):
            l = len(o)
            max_str = l
            total_str = l
        elif isinstance(o, dict):
            for k, v in o.items():
                if isinstance(k, str):
                    l = len(k)
                    total_str += l
                    if l > max_str:
                        max_str = l
                child_d, child_n, child_max_s, child_total_s = _walk(v, depth + 1)
                nodes += child_n
                if child_d > max_d:
                    max_d = child_d
                total_str += child_total_s
                if child_max_s > max_str:
                    max_str = child_max_s
        elif isinstance(o, list):
            for item in o:
                child_d, child_n, child_max_s, child_total_s = _walk(item, depth + 1)
                nodes += child_n
                if child_d > max_d:
                    max_d = child_d
                total_str += child_total_s
                if child_max_s > max_str:
                    max_str = child_max_s

        return (max_d, nodes, max_str, total_str)

    return _walk(obj, 1)


# ── JSON parsing ──


def parse_json_to_text(file_path: Path) -> str:
    """Parse a JSON file and return a deterministic normalized string.

    Frozen contract:
    1. UTF-8 or UTF-8-SIG strict decode
    2. Reject NUL bytes
    3. parse_constant=_reject_non_finite (rejects NaN, Infinity, -Infinity)
    4. Validate depth ≤ MAX_BRIDGE_JSON_DEPTH
    5. Validate nodes ≤ MAX_BRIDGE_JSON_NODES
    6. Validate single string ≤ MAX_BRIDGE_JSON_STRING_CHARS
    7. Validate total strings ≤ MAX_BRIDGE_JSON_TOTAL_STRING_CHARS
    8. json.dumps(normalized, ensure_ascii=False, sort_keys=True,
                  allow_nan=False, indent=2)
    9. Validate rendered chars ≤ MAX_BRIDGE_JSON_RENDER_CHARS

    Returns:
        Normalized JSON string (deterministic sort_keys output).

    Raises:
        ValueError: Parse failure, size/limit exceeded, non-finite numbers.
    """
    file_size = file_path.stat().st_size
    if file_size > MAX_BRIDGE_PARSE_FILE_BYTES:
        raise ValueError("JSON file exceeds MAX_BRIDGE_PARSE_FILE_BYTES")

    raw = file_path.read_bytes()
    if b"\x00" in raw:
        raise ValueError("JSON contains NUL byte — rejected")

    # Decode UTF-8 or UTF-8-SIG
    try:
        if raw.startswith(b"\xef\xbb\xbf"):
            text = raw.decode("utf-8-sig")
        else:
            text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"JSON is not valid UTF-8: {e}")

    if not text.strip():
        raise ValueError("JSON file is empty")

    # Stage 1: Parse with parse_constant to reject NaN/Infinity/-Infinity
    try:
        parsed = _json_module.loads(
            text,
            parse_constant=_reject_non_finite,
        )
    except _json_module.JSONDecodeError as e:
        raise ValueError(f"JSON parse error: {e}")
    except ValueError as e:
        if "non-finite" in str(e):
            raise ValueError(f"JSON contains non-finite number: {e}")
        raise

    # Stage 2: Validate structural limits
    depth, nodes, max_single_str, total_str = _count_json_structure(parsed)

    if depth > MAX_BRIDGE_JSON_DEPTH:
        raise ValueError(
            f"JSON depth {depth} exceeds {MAX_BRIDGE_JSON_DEPTH}"
        )
    if nodes > MAX_BRIDGE_JSON_NODES:
        raise ValueError(
            f"JSON node count {nodes} exceeds {MAX_BRIDGE_JSON_NODES}"
        )
    if max_single_str > MAX_BRIDGE_JSON_STRING_CHARS:
        raise ValueError(
            f"JSON single string chars {max_single_str} exceeds "
            f"{MAX_BRIDGE_JSON_STRING_CHARS}"
        )
    if total_str > MAX_BRIDGE_JSON_TOTAL_STRING_CHARS:
        raise ValueError(
            f"JSON total string chars {total_str} exceeds "
            f"{MAX_BRIDGE_JSON_TOTAL_STRING_CHARS}"
        )

    # Stage 3: Deterministic normalization (allow_nan=False as second defense)
    try:
        normalized = _json_module.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            indent=2,
        )
    except ValueError as e:
        raise ValueError(f"JSON normalization failed: {e}")

    # Stage 4: Check rendered size
    if len(normalized) > MAX_BRIDGE_JSON_RENDER_CHARS:
        raise ValueError(
            f"JSON rendered chars {len(normalized)} exceeds "
            f"{MAX_BRIDGE_JSON_RENDER_CHARS}"
        )

    return normalized


# ── TXT parsing ──


def parse_txt_to_string(file_path: Path) -> str:
    """Parse a plain text file into a string.

    Requirements:
    - UTF-8 or UTF-8-SIG strict decode
    - Reject NUL bytes
    - No silent truncation
    - Characters ≤ MAX_BRIDGE_TXT_CHARS

    Returns:
        Decoded text string.
    """
    file_size = file_path.stat().st_size
    if file_size > MAX_BRIDGE_PARSE_FILE_BYTES:
        raise ValueError("TXT file exceeds MAX_BRIDGE_PARSE_FILE_BYTES")

    raw = file_path.read_bytes()
    if b"\x00" in raw:
        raise ValueError("TXT contains NUL byte — rejected")

    try:
        if raw.startswith(b"\xef\xbb\xbf"):
            text = raw.decode("utf-8-sig")
        else:
            text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"TXT is not valid UTF-8: {e}")

    if len(text) > MAX_BRIDGE_TXT_CHARS:
        raise ValueError(
            f"TXT character count {len(text)} exceeds {MAX_BRIDGE_TXT_CHARS}"
        )

    return text
