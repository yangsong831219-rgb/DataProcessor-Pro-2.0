"""Immutable domain models for Report Bridge (Batch 3.3.1A).

All public models are frozen dataclasses — they carry data only.
No QWidget, QObject, thread, process, or callable references allowed.

Internal types (ReportBridgeLease) are NOT in __all__ and must
never enter Qt signals or public API surfaces.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from dp_engine.skills.runtime_models import RuntimeArtifact


# ── Resource limits ──

MAX_BRIDGE_ASSETS: int = 12
MAX_BRIDGE_TOTAL_BYTES: int = 100 * 1024 * 1024       # 100 MiB
MAX_BRIDGE_PARSE_FILE_BYTES: int = 8 * 1024 * 1024    # 8 MiB
MAX_BRIDGE_IMAGE_BYTES: int = 20 * 1024 * 1024         # 20 MiB
MAX_BRIDGE_IMAGE_PIXELS: int = 40_000_000
MAX_BRIDGE_IMAGE_DIMENSION: int = 12_000
MAX_BRIDGE_CSV_ROWS: int = 5000
MAX_BRIDGE_CSV_COLS: int = 30
MAX_BRIDGE_CSV_CELL_CHARS: int = 8192
MAX_BRIDGE_CSV_TOTAL_CELLS: int = 150_000
MAX_BRIDGE_JSON_DEPTH: int = 8
MAX_BRIDGE_JSON_NODES: int = 5000
MAX_BRIDGE_JSON_STRING_CHARS: int = 50_000
MAX_BRIDGE_JSON_TOTAL_STRING_CHARS: int = 500_000
MAX_BRIDGE_JSON_RENDER_CHARS: int = 50_000
MAX_BRIDGE_TXT_CHARS: int = 50_000

# ── Frozen safe error codes ──

SAFE_ERROR_CODES: frozenset[str] = frozenset({
    "invalid_request",
    "owner_mismatch",
    "artifact_not_found",
    "artifact_integrity_failed",
    "unsupported_media_type",
    "role_mismatch",
    "asset_too_large",
    "asset_parse_failed",
    "workspace_security_failed",
    "workspace_io_failed",
    "operation_conflict",
    "internal_failure",
})

SAFE_ERROR_MESSAGES: dict[str, str] = {
    "invalid_request":         "请求参数验证失败，请检查输入。",
    "owner_mismatch":          "素材归属不匹配，无法用于报告生成。",
    "artifact_not_found":      "请求的素材不存在或已被移除。",
    "artifact_integrity_failed": "素材完整性验证失败，文件可能已损坏。",
    "unsupported_media_type":  "素材类型不在报告桥接支持范围内。",
    "role_mismatch":           "素材类型与选择的报告角色不兼容。",
    "asset_too_large":         "素材超过资源限制，无法处理。",
    "asset_parse_failed":      "素材内容解析失败，文件格式不正确。",
    "workspace_security_failed": "工作区安全验证失败。",
    "workspace_io_failed":     "工作区创建失败，存储空间可能不足。",
    "operation_conflict":      "当前有冲突的操作正在进行，请稍后重试。",
    "internal_failure":        "报告桥接内部错误，请稍后重试。",
}

# ── AUTHORITATIVE_MEDIA_TYPE_TO_ROLE ──

AUTHORITATIVE_MEDIA_TYPE_TO_ROLE: dict[str, str] = {
    "image/png":  "image",
    "image/jpeg": "image",
    "text/csv":   "table_source",
    "application/json": "text_source",
    "text/plain": "text_source",
}

# ── Role to extension mapping ──

ROLE_EXTENSION_MAP: dict[str, str] = {
    "image":        ".png",
    "table_source": ".csv",
    "text_source":  ".txt",
}

MEDIA_TYPE_EXTENSION_MAP: dict[str, str] = {
    "image/png":         ".png",
    "image/jpeg":        ".jpg",
    "text/csv":          ".csv",
    "application/json":  ".json",
    "text/plain":        ".txt",
}


# ── Enums ──


class ReportAssetRole(StrEnum):
    """素材在报告中的角色 — 决定 Builder 如何处理。"""
    IMAGE = "image"
    TABLE_SOURCE = "table_source"
    TEXT_SOURCE = "text_source"


class ReportBridgeStatus(StrEnum):
    """公开状态 — UI 可消费。"""
    PREPARING = "preparing"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"


class InternalBridgeState(StrEnum):
    """内部状态 — Controller 私有。UI 不得依赖 GENERATING 或 RELEASED。"""
    IDLE = "idle"
    PREPARING = "preparing"
    READY = "ready"
    GENERATING = "generating"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    RELEASED = "released"


class OperationKind(StrEnum):
    """协调器操作类型。"""
    USER_ARTIFACT_OPERATION = "user_artifact_operation"
    BRIDGE_SESSION = "bridge_session"


# ── Parsed payload types ──

# ((col1, col2, ...), (row1_val1, row1_val2, ...), ...)
ParsedTable = tuple[tuple[str, ...], ...]
# IMAGE → None, TABLE_SOURCE → ParsedTable, TEXT_SOURCE → str
ParsedPayload = None | ParsedTable | str


# ── Validation helpers ──

_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def _validate_hex32(value: str, field_name: str) -> None:
    """Validate a 32-character hex string."""
    if not _HEX32_RE.match(value):
        raise ValueError(
            f"{field_name} must be a 32-character hex string, got: {value!r}"
        )


def _validate_skill_id(value: str) -> None:
    """Validate skill_id: non-empty, no path separators."""
    if not value or not value.strip():
        raise ValueError("skill_id must be non-empty")
    if "/" in value or "\\" in value:
        raise ValueError(f"skill_id must not contain path separators: {value!r}")


def _validate_uuid4_hex(value: str) -> None:
    """Validate a uuid4 hex string (32 lowercase hex)."""
    _validate_hex32(value, "request_id/task_id")


# ── Public models ──


@dataclass(frozen=True, kw_only=True)
class ReportArtifactSelection:
    """用户在 UI 中将一个 Artifact 选入 Bridge 的不可变记录。"""

    schema_version: int
    skill_id: str
    task_id: str
    artifact_id: str
    role: ReportAssetRole
    order: int
    display_name_hint: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError(
                f"schema_version must be 1, got {self.schema_version}"
            )
        _validate_skill_id(self.skill_id)
        _validate_uuid4_hex(self.task_id)
        _validate_hex32(self.artifact_id, "artifact_id")
        if not isinstance(self.role, ReportAssetRole):
            raise ValueError(
                f"role must be a ReportAssetRole, got {type(self.role).__name__}"
            )
        if self.order < 0:
            raise ValueError(f"order must be >= 0, got {self.order}")


@dataclass(frozen=True, kw_only=True)
class ReportBridgeRequest:
    """Controller 接收的完整 Bridge 请求。"""

    schema_version: int
    request_id: str
    selections: tuple[ReportArtifactSelection, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError(
                f"schema_version must be 1, got {self.schema_version}"
            )
        _validate_uuid4_hex(self.request_id)
        n = len(self.selections)
        if n < 1 or n > MAX_BRIDGE_ASSETS:
            raise ValueError(
                f"selections count must be 1..{MAX_BRIDGE_ASSETS}, got {n}"
            )

        # All selections must share the same skill_id and task_id
        if n > 0:
            first_skill = self.selections[0].skill_id
            first_task = self.selections[0].task_id
            for sel in self.selections:
                if sel.skill_id != first_skill:
                    raise ValueError(
                        f"All selections must share the same skill_id: "
                        f"{sel.skill_id!r} != {first_skill!r}"
                    )
                if sel.task_id != first_task:
                    raise ValueError(
                        f"All selections must share the same task_id: "
                        f"{sel.task_id!r} != {first_task!r}"
                    )

        # artifact_id must not repeat
        seen_ids: set[str] = set()
        for sel in self.selections:
            if sel.artifact_id in seen_ids:
                raise ValueError(
                    f"Duplicate artifact_id: {sel.artifact_id!r}"
                )
            seen_ids.add(sel.artifact_id)

        # order must be exactly 0..N-1 (no gaps, no duplicates)
        orders = sorted(sel.order for sel in self.selections)
        expected = list(range(n))
        if orders != expected:
            raise ValueError(
                f"order must be exactly 0..{n - 1} without gaps, got: {orders}"
            )


@dataclass(frozen=True, kw_only=True)
class ReportAssetSummary:
    """UI 列表和移除操作使用的最小素材摘要 — 零 Path。"""

    asset_key: str
    display_name: str
    role: ReportAssetRole
    size_bytes: int
    order: int

    def __post_init__(self) -> None:
        if self.size_bytes < 0:
            raise ValueError(f"size_bytes must be >= 0, got {self.size_bytes}")
        if self.order < 0:
            raise ValueError(f"order must be >= 0, got {self.order}")
        # asset_key must not contain artifact_id format, path separators
        if "/" in self.asset_key or "\\" in self.asset_key:
            raise ValueError("asset_key must not contain path separators")


@dataclass(frozen=True, kw_only=True)
class ReportBridgePublicResult:
    """通过 Qt 信号发送给 UI 的唯一公开结果类型。"""

    request_id: str
    generation: int
    status: ReportBridgeStatus
    assets: tuple[ReportAssetSummary, ...]
    warnings: tuple[str, ...]
    safe_error_code: str | None
    safe_error_message: str | None

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ValueError(
                f"generation must be a positive integer, got {self.generation}"
            )

        # Enforce status invariants
        status = self.status
        if status == ReportBridgeStatus.PREPARING:
            if self.assets != ():
                raise ValueError("PREPARING status requires assets=()")
            if self.warnings != ():
                raise ValueError("PREPARING status requires warnings=()")
            if self.safe_error_code is not None:
                raise ValueError("PREPARING status requires safe_error_code=None")
            if self.safe_error_message is not None:
                raise ValueError("PREPARING status requires safe_error_message=None")

        elif status == ReportBridgeStatus.READY:
            if not self.assets:
                raise ValueError("READY status requires non-empty assets")
            if self.safe_error_code is not None:
                raise ValueError("READY status requires safe_error_code=None")
            if self.safe_error_message is not None:
                raise ValueError("READY status requires safe_error_message=None")

        elif status == ReportBridgeStatus.SUCCEEDED:
            if not self.assets:
                raise ValueError("SUCCEEDED status requires non-empty assets")
            if self.safe_error_code is not None:
                raise ValueError("SUCCEEDED status requires safe_error_code=None")
            if self.safe_error_message is not None:
                raise ValueError("SUCCEEDED status requires safe_error_message=None")

        elif status == ReportBridgeStatus.FAILED:
            if self.assets != ():
                raise ValueError("FAILED status requires assets=()")
            if self.warnings != ():
                raise ValueError("FAILED status requires warnings=()")
            if not self.safe_error_code:
                raise ValueError("FAILED status requires non-empty safe_error_code")
            if self.safe_error_code not in SAFE_ERROR_CODES:
                raise ValueError(
                    f"Unknown safe_error_code: {self.safe_error_code!r}"
                )
            if not self.safe_error_message:
                raise ValueError("FAILED status requires non-empty safe_error_message")

        elif status == ReportBridgeStatus.CANCELLED:
            if self.assets != ():
                raise ValueError("CANCELLED status requires assets=()")
            if self.warnings != ():
                raise ValueError("CANCELLED status requires warnings=()")
            if self.safe_error_code is not None:
                raise ValueError("CANCELLED status requires safe_error_code=None")
            if self.safe_error_message is not None:
                raise ValueError("CANCELLED status requires safe_error_message=None")


# ── Internal models ──


@dataclass(frozen=True, kw_only=True)
class PreparedReportAsset:
    """准备完成的单个素材 — 仅存在于 Controller 内部和 GenerationInput 中。"""

    authoritative_artifact: RuntimeArtifact
    role: ReportAssetRole
    order: int
    managed_filename: str
    parsed_payload: ParsedPayload

    def __post_init__(self) -> None:
        # managed_filename must be a single filename, no path separators,
        # no absolute path, not "." or ".."
        fn = self.managed_filename
        if not fn or fn != fn.strip():
            raise ValueError(f"managed_filename must be non-empty: {fn!r}")
        if fn in (".", ".."):
            raise ValueError(f"managed_filename must not be '.' or '..': {fn!r}")
        if "/" in fn or "\\" in fn:
            raise ValueError(f"managed_filename must not contain path separators: {fn!r}")
        if fn.startswith("/") or (len(fn) >= 2 and fn[1] == ":"):
            raise ValueError(f"managed_filename must not be absolute: {fn!r}")

        # Role ←→ payload type match
        if self.role == ReportAssetRole.IMAGE:
            if self.parsed_payload is not None:
                raise ValueError(
                    f"IMAGE role requires parsed_payload=None, got "
                    f"{type(self.parsed_payload).__name__}"
                )
        elif self.role == ReportAssetRole.TABLE_SOURCE:
            if not isinstance(self.parsed_payload, tuple):
                raise ValueError(
                    f"TABLE_SOURCE role requires parsed_payload=tuple, got "
                    f"{type(self.parsed_payload).__name__}"
                )
        elif self.role == ReportAssetRole.TEXT_SOURCE:
            if not isinstance(self.parsed_payload, str):
                raise ValueError(
                    f"TEXT_SOURCE role requires parsed_payload=str, got "
                    f"{type(self.parsed_payload).__name__}"
                )


@dataclass(frozen=True, kw_only=True)
class ReportGenerationInput:
    """Controller 通过 claim_ready_generation 交接的不可变句柄。

    这不是 Lease。Controller 保留真正的 Lease 所有权。
    此对象不含 release 方法，不能改变 Controller 状态。
    """

    request_id: str
    generation: int
    workspace_path: Path
    assets: tuple[PreparedReportAsset, ...]

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ValueError(
                f"generation must be a positive integer, got {self.generation}"
            )
        if not self.assets:
            raise ValueError("assets must be non-empty")


# ── Lease (always internal, never in Qt signals) ──


class ReportBridgeLease:
    """Controller 内部持有的 Lease — 绝对不在 Qt 信号中传输。"""

    def __init__(
        self,
        *,
        request_id: str,
        generation: int,
        skill_id: str,
        task_id: str,
        workspace_path: Path,
        prepared_assets: tuple[PreparedReportAsset, ...],
        coordinator_token: object,
    ) -> None:
        self._request_id = request_id
        self._generation = generation
        self._skill_id = skill_id
        self._task_id = task_id
        self._workspace_path = workspace_path
        self._prepared_assets = prepared_assets
        self._coordinator_token = coordinator_token
        self._released = False
        self._lock = threading.Lock()

    @property
    def request_id(self) -> str:
        return self._request_id

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def skill_id(self) -> str:
        return self._skill_id

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def workspace_path(self) -> Path:
        return self._workspace_path

    @property
    def prepared_assets(self) -> tuple[PreparedReportAsset, ...]:
        return self._prepared_assets

    @property
    def released(self) -> bool:
        with self._lock:
            return self._released

    def release(
        self,
        *,
        workspace_cleanup: object | None = None,
        token_release: object | None = None,
    ) -> None:
        """幂等释放 Lease。

        Args:
            workspace_cleanup: Callable[[Path], None] — 安全删除 workspace
            token_release: Callable[[], None] — 释放 Coordinator token
        """
        with self._lock:
            if self._released:
                return
            self._released = True

        # Step 1: try workspace cleanup (best-effort, does not block token release)
        if workspace_cleanup is not None:
            try:
                workspace_cleanup(self._workspace_path)  # type: ignore[call-arg]
            except Exception:
                # Cleanup failure is logged internally by workspace_cleanup;
                # must not prevent token release.
                pass

        # Step 2: release coordinator token (MUST happen regardless of cleanup)
        if token_release is not None:
            try:
                token_release()  # type: ignore[call-arg]
            except Exception:
                # Token release failure must not crash the controller.
                pass
