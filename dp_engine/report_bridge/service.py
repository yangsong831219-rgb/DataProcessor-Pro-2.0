"""ReportBridgeService — prepare_assets with all-or-nothing semantics.

Batch 3.3.1A: Creates a safe workspace, exports artifacts via ArtifactStore,
validates media types and resource limits, parses content, and produces
a ReportBridgeLease on success.

All IO and parsing runs on the calling thread (must be a Worker thread).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from dp_engine.report_bridge.coordinator import (
    ArtifactOperationCoordinator,
    OperationKind,
)
from dp_engine.report_bridge.models import (
    AUTHORITATIVE_MEDIA_TYPE_TO_ROLE,
    MAX_BRIDGE_ASSETS,
    MAX_BRIDGE_TOTAL_BYTES,
    MEDIA_TYPE_EXTENSION_MAP,
    ROLE_EXTENSION_MAP,
    ReportBridgeLease,
    ReportBridgeRequest,
    ReportBridgeStatus,
    PreparedReportAsset,
    ReportArtifactSelection,
    ReportAssetRole,
    SAFE_ERROR_CODES,
    SAFE_ERROR_MESSAGES,
    ParsedPayload,
)
from dp_engine.report_bridge.parsing import (
    parse_csv_to_table,
    parse_json_to_text,
    parse_txt_to_string,
    validate_image_dimensions,
)
from dp_engine.report_bridge.workspace import (
    create_report_bridge_workspace,
    safe_release_report_bridge_workspace,
)
from dp_engine.skills.runtime_models import RuntimeArtifact

logger = logging.getLogger(__name__)


class _PrepareError(Exception):
    """Internal exception carrying safe error codes for prepare_assets."""

    def __init__(self, error_code: str) -> None:
        super().__init__(SAFE_ERROR_MESSAGES.get(error_code, error_code))
        self.error_code = error_code


def _check_cancel(cancel_event: threading.Event | None) -> None:
    """Raise if cancel is requested. Does not carry an error code
    because cancellation is not an error — it produces CANCELLED status."""
    if cancel_event is not None and cancel_event.is_set():
        raise _PrepareError("__cancelled__")  # sentinel


class ReportBridgeService:
    """Service that prepares bridge artifacts for report generation.

    Usage (from Worker thread only):
        service = ReportBridgeService(artifact_store, coordinator)
        lease = service.prepare_assets(request, generation, cancel_event)
    """

    def __init__(
        self,
        artifact_store: Any,
        coordinator: ArtifactOperationCoordinator,
    ) -> None:
        self._artifact_store = artifact_store
        self._coordinator = coordinator

    def prepare_assets(
        self,
        request: ReportBridgeRequest,
        generation: int,
        cancel_event: threading.Event | None = None,
    ) -> ReportBridgeLease:
        """Prepare all assets for a bridge request (all-or-nothing).

        Processing order:
        1. Validate request
        2. Acquire BRIDGE_SESSION token
        3. Create safe workspace
        4. list_task → match artifacts by order
        5. Validate media_type ↔ role
        6. Check resource limits
        7. Generate managed_filename
        8. ArtifactStore.export (overwrite=False)
        9. Check cancel
        10. Parse/validate content
        11. Construct PreparedReportAsset tuple
        12. Construct Lease → transfer token ownership

        On any failure: cleanup workspace, release token, no Lease produced.

        Raises:
            _PrepareError: with safe error_code on failure.
            FileExistsError: workspace already exists.
        """
        request_id = request.request_id
        selections = request.selections
        skill_id = selections[0].skill_id
        task_id = selections[0].task_id

        # Step 1: Validate request (already validated by model __post_init__,
        # but re-validate to be safe)
        if request.schema_version != 1:
            raise _PrepareError("invalid_request")

        # Step 2: Check cancel before acquiring token
        _check_cancel(cancel_event)

        # Step 3: Acquire BRIDGE_SESSION token
        token = self._coordinator.try_acquire(
            skill_id,
            task_id,
            operation=OperationKind.BRIDGE_SESSION,
        )
        if token is None:
            raise _PrepareError("operation_conflict")

        workspace_path: Path | None = None
        lease_returned = False  # Track success vs failure for finally cleanup
        try:
            # Step 4: Check cancel
            _check_cancel(cancel_event)

            # Step 5: Create workspace
            try:
                workspace_path = create_report_bridge_workspace(request_id)
            except FileExistsError:
                raise _PrepareError("workspace_io_failed")
            except (ValueError, RuntimeError) as e:
                logger.warning("Workspace creation failed: %s", e)
                raise _PrepareError("workspace_security_failed")
            except OSError as e:
                logger.warning("Workspace IO error: %s", e)
                raise _PrepareError("workspace_io_failed")

            try:
                # Step 6: Check cancel after workspace creation
                _check_cancel(cancel_event)

                # Step 7: List task artifacts
                try:
                    artifacts = self._artifact_store.list_task(skill_id, task_id)
                except Exception:
                    logger.exception("list_task failed")
                    raise _PrepareError("internal_failure")

                # Build artifact_id → RuntimeArtifact map
                artifact_map: dict[str, RuntimeArtifact] = {}
                for art in artifacts:
                    artifact_map[art.artifact_id] = art

                # Step 8: Match selections by order
                prepared_list: list[PreparedReportAsset] = []
                total_bytes = 0

                for i, sel in enumerate(selections):
                    # Check cancel before each artifact
                    _check_cancel(cancel_event)

                    # Find artifact
                    art = artifact_map.get(sel.artifact_id)
                    if art is None:
                        raise _PrepareError("artifact_not_found")

                    # Verify owner (must match skill_id/task_id from selection)
                    if art.skill_id != skill_id or art.task_id != task_id:
                        raise _PrepareError("owner_mismatch")

                    # Verify media_type is supported
                    role_str = AUTHORITATIVE_MEDIA_TYPE_TO_ROLE.get(art.media_type)
                    if role_str is None:
                        raise _PrepareError("unsupported_media_type")

                    # Verify role matches
                    if role_str != sel.role.value:
                        raise _PrepareError("role_mismatch")

                    # Check individual file size
                    if art.size_bytes < 0:
                        raise _PrepareError("artifact_integrity_failed")

                    # Check total bytes
                    total_bytes += art.size_bytes
                    if total_bytes > MAX_BRIDGE_TOTAL_BYTES:
                        raise _PrepareError("asset_too_large")

                    # Generate managed filename
                    ext = MEDIA_TYPE_EXTENSION_MAP.get(art.media_type)
                    if ext is None:
                        # Fallback: use role extension
                        ext = ROLE_EXTENSION_MAP.get(sel.role.value, ".bin")
                    managed_filename = f"{i:02d}_{sel.artifact_id}{ext}"
                    managed_target = workspace_path / managed_filename

                    # Export artifact
                    try:
                        self._artifact_store.export(
                            skill_id,
                            task_id,
                            sel.artifact_id,
                            managed_target,
                            overwrite=False,
                        )
                    except FileExistsError:
                        raise _PrepareError("workspace_io_failed")
                    except RuntimeError as e:
                        msg = str(e).lower()
                        if "hash" in msg:
                            raise _PrepareError("artifact_integrity_failed")
                        raise _PrepareError("internal_failure")
                    except OSError:
                        raise _PrepareError("workspace_io_failed")

                    # Check cancel after export
                    _check_cancel(cancel_event)

                    # Parse/validate content
                    parsed_payload: ParsedPayload = None
                    try:
                        if sel.role == ReportAssetRole.IMAGE:
                            validate_image_dimensions(managed_target)
                            parsed_payload = None
                        elif sel.role == ReportAssetRole.TABLE_SOURCE:
                            parsed_payload = parse_csv_to_table(managed_target)
                        elif sel.role == ReportAssetRole.TEXT_SOURCE:
                            if art.media_type == "application/json":
                                parsed_payload = parse_json_to_text(managed_target)
                            else:
                                parsed_payload = parse_txt_to_string(managed_target)
                    except ValueError as e:
                        msg = str(e).lower()
                        if any(kw in msg for kw in ("exceed", "pixel", "dimension",
                                                      "bomb", "chars", "cell",
                                                      "row", "column", "depth",
                                                      "node", "string", "rendered")):
                            raise _PrepareError("asset_too_large")
                        raise _PrepareError("asset_parse_failed")

                    # Build PreparedReportAsset
                    prepared = PreparedReportAsset(
                        authoritative_artifact=art,
                        role=sel.role,
                        order=sel.order,
                        managed_filename=managed_filename,
                        parsed_payload=parsed_payload,
                    )
                    prepared_list.append(prepared)

                # Step 9: Check cancel before constructing Lease
                _check_cancel(cancel_event)

                # Step 10: Construct Lease (transfers token ownership)
                lease = ReportBridgeLease(
                    request_id=request_id,
                    generation=generation,
                    skill_id=skill_id,
                    task_id=task_id,
                    workspace_path=workspace_path,
                    prepared_assets=tuple(prepared_list),
                    coordinator_token=token,
                )
                # Transfer token ownership — token is now owned by Lease
                token = None
                lease_returned = True

                return lease

            except _PrepareError:
                raise
            except Exception:
                logger.exception("Unexpected error in prepare_assets")
                raise _PrepareError("internal_failure")

        finally:
            # If we still hold the token (failure before Lease creation),
            # release it
            if token is not None:
                try:
                    token.release()
                except Exception:
                    pass

            # Only clean workspace on failure — on success the Lease owns it
            if not lease_returned and workspace_path is not None:
                try:
                    safe_release_report_bridge_workspace(request_id, workspace_path)
                except Exception:
                    pass
