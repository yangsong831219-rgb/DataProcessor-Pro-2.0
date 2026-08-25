"""ArtifactRunContext, ArtifactPublisher, and ArtifactStore (Batch 3.2.1).

This module provides:
- ArtifactRunContext: backward-compatible dict subclass for skill artifact
  declarations.
- ArtifactPublisher: atomic filesystem publishing with content sniffing,
  security verification, and TOCTOU defense.
- ArtifactStore: host-side safe consumption API (list, locate, export, delete).
"""

from __future__ import annotations

import hashlib
import json as _json_module
import logging
import os
import shutil
import stat as _stat
import subprocess
import tempfile
import uuid as _uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dp_engine.skills.runtime_models import (
    ALLOWED_ARTIFACT_EXTENSIONS,
    ARTIFACT_SCHEMA_VERSION,
    MAX_ARTIFACTS_PER_RUN,
    MAX_ARTIFACT_FILE_BYTES,
    MAX_ARTIFACTS_TOTAL_BYTES,
    MAX_DECLARE_MANIFEST_BYTES,
    MAX_DISPLAY_NAME_CHARS,
    MAX_DECLARED_PATH_CHARS,
    MAX_METADATA_JSON_BYTES,
    MAX_METADATA_AGGREGATE_BYTES,
    MAX_MEDIA_TYPE_CHARS,
    VALID_ARTIFACT_KINDS,
    GENERATE_REPORT_OPERATION,
    ArtifactDeclaration,
    RuntimeArtifact,
    _validate_declared_path_syntax,
)
from dp_engine.skills.runtime_paths import (
    _is_symlink_or_reparse,
    build_safe_filename,
    cleanup_orphan_commit_dirs,
    get_artifact_root,
    get_artifact_skill_dir,
    get_artifact_task_dir,
    safe_create_artifact_dir,
    verify_artifact_root_safety,
    verify_path_not_symlink_or_reparse,
    verify_path_within_root,
)

logger = logging.getLogger(__name__)


# ── Internal pending declaration (NOT a wire type) ──


@dataclass(frozen=True)
class _PendingArtifactDeclaration:
    """Normalised, validated declaration stored in ArtifactRunContext.

    This is purely internal — it is NOT serialised to result.json.
    The Worker converts these to ArtifactDeclaration wire dicts after
    observing the real filesystem (3.2.1B).
    """

    declared_path: str
    display_name: str
    media_type_hint: str | None
    kind: str
    metadata: dict[str, object] = field(default_factory=dict)


# ── display_name helpers ──


def _normalize_display_name(
    declared_path: str,
    display_name: str | None,
) -> str:
    """Normalize a display_name per the frozen contract.

    1. If None → Path(declared_path).stem
    2. If stem.strip() == "" → use original filename
    3. Final strip must be non-empty
    4. Truncate to 128 chars on a UTF-8 safe code-point boundary
    """
    if display_name is None:
        display_name = Path(declared_path).stem
    name = display_name.strip()
    if not name:
        name = Path(declared_path).name.strip()
    if not name:
        name = declared_path.strip()
    if not name:
        raise ValueError("display_name must be non-empty after normalization")

    # Truncate to MAX_DISPLAY_NAME_CHARS on UTF-8 code-point boundary
    if len(name) > MAX_DISPLAY_NAME_CHARS:
        # Walk backwards from the limit to find a safe truncation point
        truncated = name[:MAX_DISPLAY_NAME_CHARS]
        # Ensure we don't cut in the middle of a multi-byte sequence
        # by encoding and decoding back
        name = truncated
        while True:
            try:
                name.encode("utf-8")
                break
            except UnicodeEncodeError:
                name = name[:-1]
                if not name:
                    name = truncated[0]  # should never happen with ASCII-safe limits
                    break

    return name


def _validate_media_type_hint(hint: str | None) -> None:
    """Validate media_type hint syntax (Batch 3.2.1A — no content sniffing yet).

    Raises ValueError on violation.
    """
    if hint is None:
        return
    if not isinstance(hint, str) or not hint:
        raise ValueError("media_type must be a non-empty string or None")
    if len(hint) > MAX_MEDIA_TYPE_CHARS:
        raise ValueError(
            f"media_type exceeds {MAX_MEDIA_TYPE_CHARS} chars: {len(hint)}"
        )
    # Must roughly match type/subtype
    parts = hint.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"media_type must be 'type/subtype' format, got: {hint!r}"
        )


# ── ArtifactRunContext ──


class ArtifactRunContext(dict[str, object]):
    """Backward-compatible dict subclass with declare_artifact().

    Skills receive this as their ``context`` parameter.
    ``context["params"]`` continues to work.
    ``json.dumps(context)`` serialises only the dict payload, not the
    internal declaration state.

    Any illegal declare_artifact() call sets a fatal-declaration-error
    flag that the Worker MUST check after run() returns.  The flag cannot
    be cleared by the skill.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._declarations: list[_PendingArtifactDeclaration] = []
        self._has_fatal_error: bool = False
        self._fatal_message: str = ""
        self._aggregate_metadata_bytes: int = 0

    # ── Public API ──

    def declare_artifact(
        self,
        path: str,
        *,
        display_name: str | None = None,
        media_type: str | None = None,
        kind: str = "other",
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Declare an output file as an artifact.

        In Batch 3.2.1A this performs pure-validation only (no filesystem access).
        File existence, type, content sniffing, and hashing are deferred to 3.2.1B.

        Raises ValueError on violations (sets fatal flag).  Even if the skill
        catches this exception, the fatal flag persists and the Worker will
        detect it after run() returns.
        """
        try:
            self._declare_impl(path, display_name, media_type, kind, metadata)
        except Exception:
            self._has_fatal_error = True
            if not self._fatal_message:
                import traceback
                self._fatal_message = traceback.format_exc().split("\n")[-2]
            raise

    @property
    def has_fatal_declaration_error(self) -> bool:
        """True if any declare_artifact() call was illegal.

        The Worker MUST check this after run() returns.  True → the
        final status is protocol_error regardless of the business result.
        """
        return self._has_fatal_error

    @property
    def fatal_declaration_message(self) -> str | None:
        """Human-readable description of the first fatal declaration error, or None."""
        return self._fatal_message or None

    # ── Internal (for Worker use only — 3.2.1B) ──

    def _get_pending_declarations(self) -> tuple[_PendingArtifactDeclaration, ...]:
        """Return the ordered list of pending declarations (Worker use only)."""
        return tuple(self._declarations)

    # ── Implementation ──

    def _declare_impl(
        self,
        path: str,
        display_name: str | None,
        media_type: str | None,
        kind: str,
        metadata: dict[str, object] | None,
    ) -> None:
        """Pure-validation implementation — no filesystem access in 3.2.1A."""
        # ── 1. Path validation ──
        if not isinstance(path, str):
            raise TypeError(f"path must be str, got {type(path).__name__}")

        _validate_declared_path_syntax(path)

        if len(path) > MAX_DECLARED_PATH_CHARS:
            raise ValueError(
                f"path exceeds {MAX_DECLARED_PATH_CHARS} chars: {len(path)}"
            )

        # ── 2. display_name ──
        if display_name is not None:
            if not isinstance(display_name, str):
                raise TypeError(
                    f"display_name must be str or None, got {type(display_name).__name__}"
                )
        normalized_name = _normalize_display_name(path, display_name)

        # ── 3. media_type hint ──
        _validate_media_type_hint(media_type)

        # ── 4. kind ──
        if not isinstance(kind, str) or kind not in VALID_ARTIFACT_KINDS:
            raise ValueError(
                f"kind must be one of {sorted(VALID_ARTIFACT_KINDS)}, got {kind!r}"
            )

        # ── 5. metadata ──
        meta: dict[str, object] = dict(metadata) if metadata is not None else {}
        self._validate_and_measure_metadata(meta)

        # ── 6. Duplicate check (path idempotency) ──
        existing = self._find_declaration(path)
        if existing is not None:
            # Same path → idempotent, keep first, ignore second (no fatal)
            return

        # ── 7. Count check ──
        if len(self._declarations) >= MAX_ARTIFACTS_PER_RUN:
            raise ValueError(
                f"Maximum {MAX_ARTIFACTS_PER_RUN} artifacts per run exceeded"
            )

        # ── 8. Manifest size check ──
        self._check_manifest_size(path, normalized_name, media_type, kind, meta)

        # ── 9. Queue declaration ──
        self._declarations.append(_PendingArtifactDeclaration(
            declared_path=path,
            display_name=normalized_name,
            media_type_hint=media_type,
            kind=kind,
            metadata=meta,
        ))

    def _find_declaration(self, path: str) -> int | None:
        """Return index of existing declaration for path, or None."""
        for i, decl in enumerate(self._declarations):
            if decl.declared_path == path:
                return i
        return None

    def _validate_and_measure_metadata(self, metadata: dict[str, object]) -> None:
        """Validate metadata JSON-compatibility and measure serialized size.

        Raises ValueError on violation.
        """
        import json

        # Check JSON-compatible recursively
        _check_metadata_json_compatible(metadata)

        try:
            serialized = json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, default=str)
        except (ValueError, TypeError) as e:
            raise ValueError(f"metadata not JSON-serializable: {e}") from e

        item_bytes = len(serialized.encode("utf-8"))
        if item_bytes > MAX_METADATA_JSON_BYTES:
            raise ValueError(
                f"metadata JSON exceeds {MAX_METADATA_JSON_BYTES} bytes: "
                f"{item_bytes} bytes"
            )

        # Aggregate check
        new_aggregate = self._aggregate_metadata_bytes + item_bytes
        if new_aggregate > MAX_METADATA_AGGREGATE_BYTES:
            raise ValueError(
                f"Aggregate metadata exceeds {MAX_METADATA_AGGREGATE_BYTES} bytes"
            )

        self._aggregate_metadata_bytes = new_aggregate

    def _check_manifest_size(
        self,
        path: str,
        display_name: str,
        media_type: str | None,
        kind: str,
        metadata: dict[str, object],
    ) -> None:
        """Check whether adding this declaration would exceed the manifest wire limit.

        Uses canonical JSON serialization of the full declarations list
        (existing + candidate), matching the protocol-level check in
        validate_artifact_declarations().
        """
        import json

        def _wire_item(declared_path: str, dn: str, mt: str | None,
                       k: str, md: dict[str, object]) -> dict[str, object]:
            return {
                "declared_path": declared_path,
                "display_name": dn,
                "media_type_hint": mt,
                "kind": k,
                "metadata": md,
                "observed_size_bytes": 0,
                "observed_sha256": "",
                "observed_device": 0,
                "observed_inode": 0,
                "observed_mtime_ns": 0,
            }

        wire_list: list[dict[str, object]] = [
            _wire_item(d.declared_path, d.display_name, d.media_type_hint,
                       d.kind, d.metadata)
            for d in self._declarations
        ]
        wire_list.append(_wire_item(path, display_name, media_type, kind, metadata))

        encoded = json.dumps(
            wire_list,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

        if len(encoded) > MAX_DECLARE_MANIFEST_BYTES:
            raise ValueError(
                "Artifact declaration manifest exceeds the 32768-byte limit."
            )


# ── Metadata JSON-compatible recursive check ──

_MAX_META_DEPTH = 16
_MAX_META_CONTAINER = 100
_MAX_META_STRING_BYTES = 4096


def _check_metadata_json_compatible(
    value: object,
    *,
    depth: int = 0,
) -> None:
    """Recursively check that metadata is JSON-compatible within limits.

    Reuses the spirit of validate_params but with tighter limits for metadata.
    Raises ValueError on any violation.
    """
    if depth > _MAX_META_DEPTH:
        raise ValueError(f"metadata nesting depth exceeded: {depth} > {_MAX_META_DEPTH}")

    if value is None:
        return
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        import math
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"metadata contains NaN/Infinity: {value}")
        return
    if isinstance(value, str):
        if len(value.encode("utf-8")) > _MAX_META_STRING_BYTES:
            raise ValueError(
                f"metadata string exceeds {_MAX_META_STRING_BYTES} bytes"
            )
        return
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_META_CONTAINER:
            raise ValueError(
                f"metadata container exceeds {_MAX_META_CONTAINER} items"
            )
        for i, item in enumerate(value):
            _check_metadata_json_compatible(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > _MAX_META_CONTAINER:
            raise ValueError(
                f"metadata dict exceeds {_MAX_META_CONTAINER} keys"
            )
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(f"metadata key must be string, got {type(k).__name__}")
            _check_metadata_json_compatible(v, depth=depth + 1)
        return
    raise ValueError(
        f"metadata contains non-JSON-compatible type: {type(value).__name__}"
    )


# ══════════════════════════════════════════════════════════════════════
# Batch 3.2.1B — Content sniffing
# ══════════════════════════════════════════════════════════════════════


def _sniff_media_type(file_path: Path, hint: str | None) -> str:
    """Determine media_type from file content and extension.

    The skill's hint is consulted but content analysis is authoritative.
    If hint conflicts with analysis, ValueError is raised.

    Returns the determined MIME type string.
    """
    ext = file_path.suffix.lower()

    # Check extension allowlist first
    if ext not in ALLOWED_ARTIFACT_EXTENSIONS:
        raise ValueError(f"File extension not allowed: {ext!r}")

    # Check for double extensions (e.g., report.pdf.exe)
    stem = file_path.name.lower()
    # Count dots after stripping the final extension
    name_no_ext = stem[: -len(ext)] if stem.endswith(ext) else stem
    if "." in name_no_ext:
        # Check if the inner extension is a rejected type
        inner_parts = name_no_ext.split(".")
        for inner_ext in reversed(inner_parts):
            check = f".{inner_ext}"
            if check in _REJECTED_EXTENSIONS:
                raise ValueError(
                    f"File has rejected inner extension: {check!r} in {file_path.name!r}"
                )

    # Content sniffing
    detected: str | None = None

    if ext == ".pdf":
        detected = _sniff_pdf(file_path)
    elif ext in (".png",):
        detected = _sniff_png(file_path)
    elif ext in (".jpg", ".jpeg"):
        detected = _sniff_jpeg(file_path)
    elif ext == ".docx":
        detected = _sniff_ooxml(file_path, "word")
    elif ext == ".pptx":
        detected = _sniff_ooxml(file_path, "ppt")
    elif ext == ".xlsx":
        detected = _sniff_ooxml(file_path, "xl")
    elif ext == ".zip":
        detected = _sniff_zip(file_path)
    elif ext == ".json":
        detected = _sniff_json(file_path)
    elif ext in (".csv", ".txt"):
        detected = _sniff_text(file_path, ext)

    if detected is None:
        raise ValueError(f"Cannot determine media type for: {file_path.name}")

    # Check hint consistency
    if hint is not None and hint != detected:
        raise ValueError(
            f"media_type hint {hint!r} conflicts with content analysis {detected!r}"
        )

    return detected


# Extensions that are explicitly rejected
_REJECTED_EXTENSIONS: frozenset[str] = frozenset({
    ".svg", ".exe", ".dll", ".bat", ".cmd", ".ps1",
    ".js", ".py", ".vbs", ".msi",
})

_PDF_MAGIC = b"%PDF-"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


def _sniff_pdf(path: Path) -> str:
    """Check if file starts with PDF magic bytes."""
    with open(path, "rb") as f:
        header = f.read(8)
    if not header.startswith(_PDF_MAGIC):
        raise ValueError("File does not start with PDF magic bytes")
    return "application/pdf"


def _sniff_png(path: Path) -> str:
    """Check if file starts with PNG magic bytes."""
    with open(path, "rb") as f:
        header = f.read(8)
    if header != _PNG_MAGIC:
        raise ValueError("File does not have PNG magic bytes")
    return "image/png"


def _sniff_jpeg(path: Path) -> str:
    """Check if file starts with JPEG SOI magic."""
    with open(path, "rb") as f:
        header = f.read(3)
    if header != _JPEG_MAGIC:
        raise ValueError("File does not start with JPEG SOI magic")
    return "image/jpeg"


def _sniff_ooxml(path: Path, required_dir: str) -> str:
    """Check OOXML file (docx/pptx/xlsx): valid ZIP with expected content."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            # Must contain [Content_Types].xml
            if "[Content_Types].xml" not in names:
                raise ValueError(
                    f"OOXML file missing [Content_Types].xml"
                )
            # Must contain at least one entry in the required directory
            prefix = f"{required_dir}/"
            if not any(n.startswith(prefix) for n in names):
                raise ValueError(
                    f"OOXML file missing {required_dir}/ directory"
                )
    except zipfile.BadZipFile as e:
        raise ValueError(f"Not a valid ZIP file: {e}") from e

    mime_map = {
        "word": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "ppt": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "xl": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    return mime_map[required_dir]


def _sniff_zip(path: Path) -> str:
    """Check if file is a valid ZIP archive."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            # Basic integrity: can list contents
            _ = zf.namelist()
    except zipfile.BadZipFile as e:
        raise ValueError(f"Not a valid ZIP file: {e}") from e
    return "application/zip"


def _sniff_json(path: Path) -> str:
    """Check if file is valid UTF-8 JSON (no NaN/Infinity)."""
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ValueError(f"Cannot read file: {e}") from e
    # Handle UTF-8 BOM
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"File is not valid UTF-8: {e}") from e
    try:
        parsed = _json_module.loads(text)
    except _json_module.JSONDecodeError as e:
        raise ValueError(f"File is not valid JSON: {e}") from e
    # Check for NaN/Infinity in parsed data
    _check_no_nan_inf(parsed)
    return "application/json"


def _sniff_text(path: Path, ext: str) -> str:
    """Check if file is valid UTF-8 text without NUL bytes."""
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ValueError(f"Cannot read file: {e}") from e
    # Handle UTF-8 BOM
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    if b"\x00" in raw:
        raise ValueError("File contains NUL bytes")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"File is not valid UTF-8: {e}") from e
    if ext == ".csv":
        return "text/csv"
    return "text/plain"


def _check_no_nan_inf(value: object) -> None:
    """Recursively check JSON value for NaN or Infinity."""
    import math
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"JSON contains NaN or Infinity: {value}")
    elif isinstance(value, dict):
        for v in value.values():
            _check_no_nan_inf(v)
    elif isinstance(value, list):
        for item in value:
            _check_no_nan_inf(item)


# ══════════════════════════════════════════════════════════════════════
# Batch 3.2.1B — ArtifactPublisher
# ══════════════════════════════════════════════════════════════════════


class ArtifactPublisher:
    """Host-side atomic artifact publisher.

    Verifies source files, copies them safely into the artifact_root,
    and commits via atomic os.replace.  Supports fingerprint-based
    idempotency and cancel-checked transactions.
    """

    def __init__(self, *, _artifact_root: Path | None = None) -> None:
        self._artifact_root = _artifact_root or get_artifact_root()

    def publish_artifacts(
        self,
        declarations: tuple[ArtifactDeclaration, ...],
        *,
        workspace_output: Path,
        skill_id: str,
        version: str,
        task_id: str,
        operation: str = "run",
        check_cancelled: Callable[[], bool] | None = None,
    ) -> tuple[RuntimeArtifact, ...]:
        """Publish artifacts from workspace/output to persistent storage.

        Args:
            declarations: Validated ArtifactDeclaration from Worker.
            workspace_output: The workspace/output directory.
            skill_id: Skill identifier.
            version: Skill version.
            task_id: Task identifier.
            check_cancelled: Optional callable() -> bool for cancel polling.

        Returns:
            Tuple of published RuntimeArtifact.

        Raises:
            RuntimeError: On security violations or filesystem failures.
        """
        if not declarations:
            return ()
        if operation not in {"run", GENERATE_REPORT_OPERATION}:
            raise ValueError(f"Unsupported artifact operation: {operation}")

        # ── Pre-publish safety checks ──
        self._verify_target_root_safety(skill_id)

        # ── Clean orphan commit dirs ──
        skill_dir = self._artifact_root / skill_id
        if skill_dir.exists():
            cleanup_orphan_commit_dirs(skill_dir, max_entries=50)

        # ── Check for existing task directory ──
        final_task_dir = skill_dir / task_id
        if final_task_dir.exists():
            return self._handle_existing_task_dir(
                final_task_dir, declarations, skill_id, version, task_id,
            )

        # ── Service-side secondary verification ──
        self._service_verify_declarations(declarations, workspace_output)

        # ── Build commit fingerprint ──
        fingerprint = self._compute_fingerprint(declarations)

        # ── Create temp commit directory ──
        commit_dir = skill_dir / f".commit_{_uuid.uuid4().hex}"
        try:
            # Verify artifact root safety first
            verify_artifact_root_safety(self._artifact_root)
            skill_dir.mkdir(parents=True, exist_ok=True)
            # Verify skill dir is not symlink after creation
            verify_path_not_symlink_or_reparse(skill_dir)
            commit_dir.mkdir(parents=False, exist_ok=False)
        except (OSError, RuntimeError) as e:
            raise RuntimeError(f"Failed to create commit directory: {e}") from e

        try:
            # ── Copy artifacts to temp directory ──
            published_artifacts: list[RuntimeArtifact] = []
            total_size = 0
            created_at = datetime.now(timezone.utc).isoformat()

            for decl in declarations:
                # Cancel check
                if check_cancelled is not None and check_cancelled():
                    self._rollback(commit_dir)
                    raise _CancelBeforeCommitError("Cancel before commit")

                artifact_id = _uuid.uuid4().hex
                safe_name = build_safe_filename(artifact_id, decl.declared_path)
                src_path = workspace_output / decl.declared_path

                # TOCTOU-safe copy
                dest_path = commit_dir / safe_name
                copied_sha256 = self._copy_and_verify(
                    src_path, dest_path, decl,
                )
                dest_size = dest_path.stat().st_size
                total_size += dest_size

                if total_size > MAX_ARTIFACTS_TOTAL_BYTES:
                    self._rollback(commit_dir)
                    raise RuntimeError(
                        f"Total artifact size exceeds {MAX_ARTIFACTS_TOTAL_BYTES} bytes"
                    )

                # Determine final media_type
                media_type = _sniff_media_type(dest_path, decl.media_type_hint)

                storage_relpath = (
                    f"{skill_id}/{task_id}/{safe_name}"
                ).replace("\\", "/")

                published = RuntimeArtifact(
                    relative_path=decl.declared_path,
                    size_bytes=dest_size,
                    sha256=copied_sha256,
                    artifact_schema_version=ARTIFACT_SCHEMA_VERSION,
                    artifact_id=artifact_id,
                    display_name=decl.display_name,
                    storage_relpath=storage_relpath,
                    media_type=media_type,
                    kind=decl.kind,
                    created_at=created_at,
                    skill_id=skill_id,
                    version=version,
                    task_id=task_id,
                    metadata=dict(decl.metadata),
                )
                published_artifacts.append(published)

            # ── Write manifest ──
            manifest = self._build_manifest(
                task_id, skill_id, version, operation,
                created_at, fingerprint, published_artifacts,
            )
            manifest_path = commit_dir / "manifest.json"
            manifest_path.write_text(
                _json_module.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # fsync manifest file (open for reading so the fd is valid on Windows)
            try:
                mfd = os.open(str(manifest_path), os.O_RDWR)
                os.fsync(mfd)
                os.close(mfd)
            except OSError:
                pass  # fsync is best-effort; atomic rename provides durability

            # ── Cancel check before commit ──
            if check_cancelled is not None and check_cancelled():
                self._rollback(commit_dir)
                raise _CancelBeforeCommitError("Cancel before commit")

            # ── Atomic commit ──
            # Verify final task dir doesn't exist (double-check)
            if final_task_dir.exists():
                self._rollback(commit_dir)
                return self._handle_existing_task_dir(
                    final_task_dir, declarations, skill_id, version, task_id,
                )

            os.replace(str(commit_dir), str(final_task_dir))

            return tuple(published_artifacts)

        except _CancelBeforeCommitError:
            raise
        except Exception:
            self._rollback(commit_dir)
            raise

    def _service_verify_declarations(
        self,
        declarations: tuple[ArtifactDeclaration, ...],
        workspace_output: Path,
    ) -> None:
        """Service-side secondary verification of each declaration.

        Re-checks: path safety, file existence, type, symlink, hardlink,
        size, and SHA256.
        """
        for i, decl in enumerate(declarations):
            src_path = workspace_output / decl.declared_path
            # Verify path is within workspace/output
            verify_path_within_root(src_path, workspace_output)
            # Verify each parent component
            _verify_source_parents(src_path, workspace_output)
            # lstat the source file
            try:
                st = src_path.lstat()
            except OSError as e:
                raise RuntimeError(
                    f"Service verify: cannot stat source file [{i}]: {e}"
                ) from e
            if _stat.S_ISLNK(st.st_mode):
                raise RuntimeError(
                    f"Service verify: source file [{i}] is a symlink"
                )
            if hasattr(st, "st_file_attributes"):
                if st.st_file_attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
                    raise RuntimeError(
                        f"Service verify: source file [{i}] is a reparse point"
                    )
            if not _stat.S_ISREG(st.st_mode):
                raise RuntimeError(
                    f"Service verify: source file [{i}] is not a regular file"
                )
            if st.st_nlink > 1:
                raise RuntimeError(
                    f"Service verify: source file [{i}] has hardlinks (nlink={st.st_nlink})"
                )
            if st.st_size > MAX_ARTIFACT_FILE_BYTES:
                raise RuntimeError(
                    f"Service verify: source file [{i}] exceeds max size"
                )
            # Compare with worker observations
            if st.st_dev != decl.observed_device:
                raise RuntimeError(
                    f"Service verify: source file [{i}] device changed "
                    f"({st.st_dev} != {decl.observed_device})"
                )
            if st.st_ino != decl.observed_inode:
                raise RuntimeError(
                    f"Service verify: source file [{i}] inode changed "
                    f"({st.st_ino} != {decl.observed_inode})"
                )
            if st.st_size != decl.observed_size_bytes:
                raise RuntimeError(
                    f"Service verify: source file [{i}] size changed "
                    f"({st.st_size} != {decl.observed_size_bytes})"
                )

    def _verify_target_root_safety(self, skill_id: str) -> None:
        """Verify the artifact root and skill directory are not symlinks."""
        verify_artifact_root_safety(self._artifact_root)
        skill_dir = self._artifact_root / skill_id
        if skill_dir.exists():
            verify_path_not_symlink_or_reparse(skill_dir)

    def _copy_and_verify(
        self,
        src: Path,
        dest: Path,
        decl: ArtifactDeclaration,
    ) -> str:
        """TOCTOU-safe copy with SHA256 computation.

        Returns the SHA256 hex digest.
        """
        sha256 = hashlib.sha256()
        # Open source safely
        try:
            with open(src, "rb") as src_f:
                # fstat source
                src_st = os.fstat(src_f.fileno())
                # Compare dev/inode with observed values
                if src_st.st_dev != decl.observed_device:
                    raise RuntimeError(
                        f"TOCTOU: source device changed during copy"
                    )
                if src_st.st_ino != decl.observed_inode:
                    raise RuntimeError(
                        f"TOCTOU: source inode changed during copy"
                    )
                if src_st.st_size != decl.observed_size_bytes:
                    raise RuntimeError(
                        f"TOCTOU: source size changed during copy"
                    )
                # Stream copy
                with open(dest, "wb") as dest_f:
                    while True:
                        chunk = src_f.read(65536)  # 64 KB chunks
                        if not chunk:
                            break
                        sha256.update(chunk)
                        dest_f.write(chunk)
                    dest_f.flush()
                    os.fsync(dest_f.fileno())
        except OSError as e:
            raise RuntimeError(f"Copy failed: {e}") from e

        # Verify destination
        dest_st = dest.lstat()
        if _is_symlink_or_reparse(dest):
            raise RuntimeError("Destination file is symlink/reparse after copy")
        if dest_st.st_size != decl.observed_size_bytes:
            raise RuntimeError(
                f"Destination size mismatch: {dest_st.st_size} != {decl.observed_size_bytes}"
            )
        # Verify destination is within artifact root
        verify_path_within_root(dest, self._artifact_root)

        computed_hash = sha256.hexdigest()
        if computed_hash != decl.observed_sha256:
            raise RuntimeError(
                f"SHA256 mismatch: computed {computed_hash[:16]}... != "
                f"observed {decl.observed_sha256[:16]}..."
            )
        return computed_hash

    def _compute_fingerprint(
        self,
        declarations: tuple[ArtifactDeclaration, ...],
    ) -> str:
        """Compute commit_fingerprint from declarations.

        Stable: same declarations → same fingerprint.
        """
        parts: list[str] = []
        for i, decl in enumerate(declarations):
            parts.append(str(i))
            parts.append(decl.observed_sha256)
            parts.append(decl.display_name)
            parts.append(decl.kind)
            parts.append(decl.media_type_hint or "")
            # Canonical metadata JSON
            meta = _json_module.dumps(
                decl.metadata, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            )
            parts.append(meta)
            parts.append(decl.declared_path)
        fp_input = "|".join(parts)
        return hashlib.sha256(fp_input.encode("utf-8")).hexdigest()

    def _build_manifest(
        self,
        task_id: str,
        skill_id: str,
        version: str,
        operation: str,
        created_at: str,
        fingerprint: str,
        artifacts: list[RuntimeArtifact],
    ) -> dict[str, object]:
        """Build manifest.json dict."""
        return {
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            "task_id": task_id,
            "skill_id": skill_id,
            "version": version,
            "operation": operation,
            "created_at": created_at,
            "commit_fingerprint": fingerprint,
            "artifacts": [a.to_extended_dict() for a in artifacts],
        }

    def _handle_existing_task_dir(
        self,
        task_dir: Path,
        declarations: tuple[ArtifactDeclaration, ...],
        skill_id: str,
        version: str,
        task_id: str,
    ) -> tuple[RuntimeArtifact, ...]:
        """Handle case where the final task directory already exists."""
        manifest_path = task_dir / "manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError(
                f"Task directory exists but no manifest: {task_dir}"
            )
        try:
            manifest = _read_manifest_safe(manifest_path)
        except RuntimeError:
            raise RuntimeError(
                f"Existing task directory has corrupt manifest: {task_dir}"
            )

        # Verify owner
        if manifest.get("task_id") != task_id:
            raise RuntimeError("Existing manifest has different task_id")
        if manifest.get("skill_id") != skill_id:
            raise RuntimeError("Existing manifest has different skill_id")

        # Compare fingerprints
        new_fp = self._compute_fingerprint(declarations)
        existing_fp = manifest.get("commit_fingerprint", "")
        if new_fp != existing_fp:
            raise RuntimeError(
                "Existing task directory has different fingerprint — "
                "refusing to overwrite"
            )

        # Idempotent: same fingerprint → return existing artifacts
        artifacts_raw = manifest.get("artifacts", [])
        if not isinstance(artifacts_raw, list):
            raise RuntimeError("Existing manifest has invalid artifacts")
        existing_artifacts: list[RuntimeArtifact] = []
        for a in artifacts_raw:
            if isinstance(a, dict):
                existing_artifacts.append(RuntimeArtifact.from_extended_dict(a))
        return tuple(existing_artifacts)

    def _rollback(self, commit_dir: Path) -> None:
        """Delete a temporary commit directory and all contents."""
        try:
            if commit_dir.exists():
                shutil.rmtree(str(commit_dir), ignore_errors=False)
        except OSError as e:
            logger.warning("Failed to rollback commit dir %s: %s", commit_dir, e)


class _CancelBeforeCommitError(Exception):
    """Internal: cancel detected before commit. Not a protocol error."""
    pass


# ══════════════════════════════════════════════════════════════════════
# Batch 3.2.1B — ArtifactStore
# ══════════════════════════════════════════════════════════════════════


def _read_manifest_safe(manifest_path: Path) -> dict[str, object]:
    """Read and validate a manifest.json file.

    Raises RuntimeError on any security or integrity violation.
    """
    if not manifest_path.is_file():
        raise RuntimeError(f"Manifest not found: {manifest_path}")

    # Verify manifest itself is not symlink/reparse
    if _is_symlink_or_reparse(manifest_path):
        raise RuntimeError(f"Manifest is symlink/reparse: {manifest_path}")

    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"Cannot read manifest: {e}") from e

    try:
        data = _json_module.loads(raw)
    except _json_module.JSONDecodeError as e:
        raise RuntimeError(f"Manifest JSON invalid: {e}") from e

    if not isinstance(data, dict):
        raise RuntimeError("Manifest root must be a dict")

    # Strict schema version check
    schema_ver = data.get("artifact_schema_version")
    if schema_ver != ARTIFACT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Manifest schema version {schema_ver!r} != {ARTIFACT_SCHEMA_VERSION}"
        )

    # Reject unknown top-level fields
    _KNOWN_MANIFEST_FIELDS = frozenset({
        "artifact_schema_version", "task_id", "skill_id", "version",
        "operation", "created_at", "commit_fingerprint", "artifacts",
    })
    for key in data:
        if key not in _KNOWN_MANIFEST_FIELDS:
            raise RuntimeError(f"Unknown manifest field: {key!r}")

    # Required fields
    for required in ("task_id", "skill_id", "commit_fingerprint", "artifacts"):
        if required not in data:
            raise RuntimeError(f"Manifest missing required field: {required!r}")

    if not isinstance(data["artifacts"], list):
        raise RuntimeError("Manifest artifacts must be a list")

    return data


def _resolve_artifact_path(
    skill_id: str,
    task_id: str,
    storage_relpath: str,
    artifact_root: Path,
) -> Path:
    """Safely resolve storage_relpath within artifact root.

    Raises RuntimeError on path escape or symlink/reparse.
    """
    verify_artifact_root_safety(artifact_root)

    # storage_relpath must be relative
    sp = Path(storage_relpath)
    if sp.is_absolute():
        raise RuntimeError(f"storage_relpath is absolute: {storage_relpath!r}")
    if ".." in sp.parts:
        raise RuntimeError(f"storage_relpath contains '..' : {storage_relpath!r}")

    # Expected prefix
    expected_prefix = f"{skill_id}/{task_id}/"
    normalized = storage_relpath.replace("\\", "/")
    if not normalized.startswith(expected_prefix):
        raise RuntimeError(
            f"storage_relpath does not start with expected prefix: "
            f"{normalized!r} vs {expected_prefix!r}"
        )

    resolved = (artifact_root / storage_relpath).resolve()
    try:
        resolved.relative_to(artifact_root.resolve())
    except ValueError:
        raise RuntimeError(
            f"storage_relpath resolves outside artifact_root: {resolved}"
        )

    return resolved


def _verify_artifact_file(path: Path, expected_size: int, expected_sha256: str | None) -> None:
    """Verify an artifact file exists, is regular, not symlink, and matches hash."""
    if not path.is_file():
        raise RuntimeError(f"Artifact file not found: {path}")
    if _is_symlink_or_reparse(path):
        raise RuntimeError(f"Artifact file is symlink/reparse: {path}")
    try:
        st = path.lstat()
    except OSError as e:
        raise RuntimeError(f"Cannot stat artifact file: {e}") from e
    if not _stat.S_ISREG(st.st_mode):
        raise RuntimeError(f"Artifact file is not a regular file: {path}")
    if st.st_size != expected_size:
        raise RuntimeError(
            f"Artifact size mismatch: {st.st_size} != {expected_size}"
        )
    if expected_sha256 is not None:
        actual = _compute_sha256(path)
        if actual != expected_sha256:
            raise RuntimeError(
                f"Artifact SHA256 mismatch: {actual[:16]}... != {expected_sha256[:16]}..."
            )


def _compute_sha256(path: Path) -> str:
    """Compute SHA256 of a file."""
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def _verify_source_parents(path: Path, root: Path) -> None:
    """Verify each parent component from root to path is not symlink/reparse.

    Walks from the output dir down, checking each component.
    """
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        rel = resolved.relative_to(root_resolved)
    except ValueError:
        raise RuntimeError(f"Path not within root: {path}")
    parts = rel.parts
    current = root_resolved
    for part in parts[:-1]:  # skip the final filename
        current = current / part
        if not current.exists():
            break
        if _is_symlink_or_reparse(current):
            raise RuntimeError(f"Parent component is symlink/reparse: {current}")


class ArtifactStore:
    """Host-side safe artifact consumption API.

    UI must use this API instead of directly accessing artifact files.
    Every operation validates manifest, owner, storage_relpath safety,
    symlink/reparse rejection, and file integrity.
    """

    def __init__(self, *, _artifact_root: Path | None = None) -> None:
        self._artifact_root = _artifact_root or get_artifact_root()

    def list_task(
        self,
        skill_id: str,
        task_id: str,
    ) -> tuple[RuntimeArtifact, ...]:
        """List all published artifacts for a task.

        Validates manifest, owner, and artifact integrity.
        """
        task_dir = self._artifact_root / skill_id / task_id
        if not task_dir.is_dir():
            return ()

        # Verify task dir safety
        verify_artifact_root_safety(self._artifact_root)
        skill_dir = self._artifact_root / skill_id
        verify_path_not_symlink_or_reparse(skill_dir)
        verify_path_not_symlink_or_reparse(task_dir)

        # Verify task dir is within artifact root
        verify_path_within_root(task_dir, self._artifact_root)

        manifest_path = task_dir / "manifest.json"
        manifest = _read_manifest_safe(manifest_path)

        # Verify owner
        if manifest.get("skill_id") != skill_id:
            raise RuntimeError("Manifest skill_id mismatch")
        if manifest.get("task_id") != task_id:
            raise RuntimeError("Manifest task_id mismatch")

        artifacts_raw = manifest.get("artifacts", [])
        if not isinstance(artifacts_raw, list):
            raise RuntimeError("Manifest artifacts is not a list")

        result: list[RuntimeArtifact] = []
        for a_dict in artifacts_raw:
            if not isinstance(a_dict, dict):
                continue
            art = RuntimeArtifact.from_extended_dict(a_dict)
            # Verify file integrity
            try:
                artifact_path = _resolve_artifact_path(
                    skill_id, task_id, art.storage_relpath, self._artifact_root,
                )
                _verify_artifact_file(artifact_path, art.size_bytes, art.sha256)
            except RuntimeError:
                continue  # Skip corrupted artifacts
            result.append(art)

        return tuple(result)

    def locate(
        self,
        skill_id: str,
        task_id: str,
        artifact_id: str,
    ) -> None:
        """Locate an artifact in the platform file manager.

        Does NOT open or execute the file.
        """
        artifact = self._find_artifact(skill_id, task_id, artifact_id)
        artifact_path = _resolve_artifact_path(
            skill_id, task_id, artifact.storage_relpath, self._artifact_root,
        )
        _verify_artifact_file(artifact_path, artifact.size_bytes, artifact.sha256)

        # Platform-specific file manager launch
        if os.name == "nt":
            # Windows: use explorer /select
            subprocess.run(
                ["explorer", "/select,", str(artifact_path)],
                shell=False,
                check=False,
            )
        else:
            # Assume freedesktop-compatible
            subprocess.run(
                ["xdg-open", str(artifact_path.parent)],
                shell=False,
                check=False,
            )

    def export(
        self,
        skill_id: str,
        task_id: str,
        artifact_id: str,
        target: Path,
        *,
        overwrite: bool = False,
    ) -> None:
        """Safely export an artifact to a user-chosen location.

        Uses atomic write (tmp file + fsync + os.replace) at the target.
        """
        artifact = self._find_artifact(skill_id, task_id, artifact_id)
        artifact_path = _resolve_artifact_path(
            skill_id, task_id, artifact.storage_relpath, self._artifact_root,
        )
        _verify_artifact_file(artifact_path, artifact.size_bytes, artifact.sha256)

        # Check target symlink BEFORE resolving (resolve follows symlinks on Windows)
        if target.exists() or target.is_symlink():
            if _is_symlink_or_reparse(target):
                raise RuntimeError(f"Target is symlink/reparse: {target}")

        target = target.resolve()

        # Verify target parent
        target_parent = target.parent
        if not target_parent.exists():
            raise RuntimeError(f"Target parent directory does not exist: {target_parent}")
        if not target_parent.is_dir():
            raise RuntimeError(f"Target parent is not a directory: {target_parent}")

        # Reject if target itself is symlink/reparse (post-resolve check too)
        if target.exists():
            if _is_symlink_or_reparse(target):
                raise RuntimeError(f"Target is symlink/reparse: {target}")
            if not overwrite:
                raise FileExistsError(
                    f"Target exists and overwrite=False: {target}"
                )

        # Atomic copy: temp file in target parent, then replace
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            dir=str(target_parent),
            prefix=".artifact_export_",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_path_str)
        try:
            # Stream copy with SHA256
            sha = hashlib.sha256()
            with open(artifact_path, "rb") as src:
                with os.fdopen(tmp_fd, "wb") as dst:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        sha.update(chunk)
                        dst.write(chunk)
                    dst.flush()
                    os.fsync(dst.fileno())
            tmp_fd = -1

            computed_hash = sha.hexdigest()
            if artifact.sha256 is not None and computed_hash != artifact.sha256:
                tmp_path.unlink(missing_ok=True)
                raise RuntimeError("Export hash mismatch")

            # Verify tmp file size
            if tmp_path.stat().st_size != artifact.size_bytes:
                tmp_path.unlink(missing_ok=True)
                raise RuntimeError("Export size mismatch")

            # Atomic replace
            os.replace(str(tmp_path), str(target))
        except Exception:
            if tmp_fd >= 0:
                try:
                    os.close(tmp_fd)
                except OSError:
                    pass
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def delete_task(
        self,
        skill_id: str,
        task_id: str,
    ) -> None:
        """Safely delete a task's artifact directory.

        Validates manifest, owner, and path containment before deletion.
        Only deletes if all artifact paths in the manifest are within the
        task directory.
        """
        task_dir = self._artifact_root / skill_id / task_id
        if not task_dir.exists():
            return

        # Verify safety
        verify_artifact_root_safety(self._artifact_root)
        skill_dir = self._artifact_root / skill_id
        verify_path_not_symlink_or_reparse(skill_dir)
        verify_path_not_symlink_or_reparse(task_dir)
        verify_path_within_root(task_dir, self._artifact_root)

        manifest_path = task_dir / "manifest.json"
        manifest = _read_manifest_safe(manifest_path)

        # Verify owner
        if manifest.get("skill_id") != skill_id:
            raise RuntimeError(
                "Cannot delete: manifest skill_id does not match"
            )
        if manifest.get("task_id") != task_id:
            raise RuntimeError(
                "Cannot delete: manifest task_id does not match"
            )

        # Verify every artifact path in manifest is within the task dir
        artifacts_raw = manifest.get("artifacts", [])
        if isinstance(artifacts_raw, list):
            for a_dict in artifacts_raw:
                if not isinstance(a_dict, dict):
                    continue
                storage_relpath = a_dict.get("storage_relpath", "")
                if not storage_relpath:
                    continue
                ap = (self._artifact_root / str(storage_relpath)).resolve()
                try:
                    ap.relative_to(task_dir.resolve())
                except ValueError:
                    raise RuntimeError(
                        f"Artifact path escapes task dir: {storage_relpath}"
                    )

        # Safe deletion
        shutil.rmtree(str(task_dir), ignore_errors=False)

    def _find_artifact(
        self,
        skill_id: str,
        task_id: str,
        artifact_id: str,
    ) -> RuntimeArtifact:
        """Find a specific artifact by ID within a task."""
        artifacts = self.list_task(skill_id, task_id)
        for art in artifacts:
            if art.artifact_id == artifact_id:
                return art
        raise RuntimeError(
            f"Artifact {artifact_id!r} not found in task {task_id!r} "
            f"for skill {skill_id!r}"
        )
