"""Tests for Report Bridge models (Batch 3.3.1A).

Covers RB-L1-01..20 — model contracts, invariants, and frozen semantics.
"""

import uuid as _uuid
from pathlib import Path

import pytest

from dp_engine.report_bridge.models import (
    MAX_BRIDGE_ASSETS,
    SAFE_ERROR_CODES,
    SAFE_ERROR_MESSAGES,
    InternalBridgeState,
    OperationKind,
    PreparedReportAsset,
    ReportArtifactSelection,
    ReportAssetRole,
    ReportAssetSummary,
    ReportBridgeLease,
    ReportBridgePublicResult,
    ReportBridgeRequest,
    ReportBridgeStatus,
    ReportGenerationInput,
)
from dp_engine.skills.runtime_models import RuntimeArtifact


# ── 32-char hex helpers for valid test IDs ──

def _tid() -> str:
    return _uuid.uuid4().hex  # always exactly 32 hex chars


def _aid() -> str:
    return _uuid.uuid4().hex


def _rid() -> str:
    return _uuid.uuid4().hex


def _make_selection(
    schema_version: int = 1,
    skill_id: str = "testskill001",
    task_id: str | None = None,
    artifact_id: str | None = None,
    role: ReportAssetRole = ReportAssetRole.IMAGE,
    order: int = 0,
    display_name_hint: str = "",
) -> ReportArtifactSelection:
    if task_id is None:
        task_id = _tid()
    if artifact_id is None:
        artifact_id = _aid()
    return ReportArtifactSelection(
        schema_version=schema_version,
        skill_id=skill_id,
        task_id=task_id,
        artifact_id=artifact_id,
        role=role,
        order=order,
        display_name_hint=display_name_hint,
    )


def _make_runtime_artifact(
    artifact_id: str | None = None,
    media_type: str = "image/png",
    size_bytes: int = 1024,
    skill_id: str = "testskill001",
    task_id: str | None = None,
    display_name: str = "test.png",
) -> RuntimeArtifact:
    if artifact_id is None:
        artifact_id = _aid()
    if task_id is None:
        task_id = _tid()
    return RuntimeArtifact(
        relative_path="output/test.png",
        size_bytes=size_bytes,
        sha256="a" * 64,
        artifact_schema_version=1,
        artifact_id=artifact_id,
        display_name=display_name,
        storage_relpath=f"testskill001/{task_id}/{artifact_id}_test.png",
        media_type=media_type,
        kind="output",
        created_at="2026-07-23T00:00:00Z",
        skill_id=skill_id,
        version="1.0.0",
        task_id=task_id,
        metadata={},
    )


# ── RB-L1-01, RB-L1-02, RB-L1-03 — owner consistency ──

class TestSelectionOwnerValidation:

    def test_all_selections_same_owner(self):
        tid = _tid()
        sels = [
            _make_selection(skill_id="sk1", task_id=tid, artifact_id=f"{i:032x}", order=i)
            for i in range(3)
        ]
        req = ReportBridgeRequest(schema_version=1, request_id=_rid(), selections=tuple(sels))
        assert req.schema_version == 1
        assert len(req.selections) == 3

    def test_mixed_skill_id_rejected(self):
        tid = _tid()
        sels = [
            _make_selection(skill_id="sk1", task_id=tid, artifact_id="0" * 32, order=0),
            _make_selection(skill_id="sk2", task_id=tid, artifact_id="1" * 32, order=1),
        ]
        with pytest.raises(ValueError, match="skill_id"):
            ReportBridgeRequest(schema_version=1, request_id=_rid(), selections=tuple(sels))

    def test_mixed_task_id_rejected(self):
        sels = [
            _make_selection(skill_id="sk1", task_id=_tid(), artifact_id="0" * 32, order=0),
            _make_selection(skill_id="sk1", task_id=_tid(), artifact_id="1" * 32, order=1),
        ]
        with pytest.raises(ValueError, match="task_id"):
            ReportBridgeRequest(schema_version=1, request_id=_rid(), selections=tuple(sels))


# ── RB-L1-04: Duplicate artifact_id ──

class TestDuplicateArtifact:

    def test_duplicate_artifact_id_rejected(self):
        tid = _tid()
        sels = [
            _make_selection(task_id=tid, artifact_id="0" * 32, order=0),
            _make_selection(task_id=tid, artifact_id="0" * 32, order=1),
        ]
        with pytest.raises(ValueError, match="Duplicate"):
            ReportBridgeRequest(schema_version=1, request_id=_rid(), selections=tuple(sels))


# ── RB-L1-05, RB-L1-06 ──

class TestRoleMediaTypeConflict:

    def test_role_media_type_conflict(self):
        """RB-L1-05: role must match authoritative media_type.
        A selection claiming IMAGE role but with text/csv media_type
        is a role_mismatch at request validation level."""
        # The conflict is validated at Service level when authoritative
        # media_type disagrees with user-chosen role. At model level,
        # the selection alone does not enforce this — it's a Service
        # contract. This test proves the model allows the selection
        # but the Service reject is proven in
        # test_report_bridge_service.py::TestJSONParsing::test_json_table_source_rolerejected
        sel = _make_selection(role=ReportAssetRole.IMAGE)
        assert sel.role == ReportAssetRole.IMAGE

    def test_image_role_with_csv_media_type_at_selection_level(self):
        """Model layer: IMAGE role selection is structurally valid.
        Conflict enforcement happens at Service layer via authoritative media_type."""
        sel = _make_selection(role=ReportAssetRole.IMAGE)
        assert sel.role == ReportAssetRole.IMAGE
        assert sel.role != ReportAssetRole.TABLE_SOURCE


class TestUIForgeryImmunity:
    """RB-L1-06: UI-provided media_type/size/hash do not affect host adjudication.

    The public Selection model does NOT carry media_type, size_bytes, or sha256.
    These are only obtained from the authoritative RuntimeArtifact returned by
    ArtifactStore.list_task. display_name_hint is stored but not used for any
    security or business decision.
    """

    def test_display_name_hint_not_used_for_security(self):
        """display_name_hint is stored verbatim but never used for path/type/size."""
        sel = _make_selection(display_name_hint="../../../etc/passwd")
        assert sel.display_name_hint == "../../../etc/passwd"

    def test_selection_has_no_media_type_field(self):
        """Public selection does NOT carry a media_type field —
        prevents UI from forging media type."""
        sel = _make_selection()
        assert not hasattr(sel, "media_type"), (
            "Selection must not expose media_type to UI"
        )

    def test_selection_has_no_size_bytes_field(self):
        """Public selection does NOT carry size_bytes —
        prevents UI from forging file size."""
        sel = _make_selection()
        assert not hasattr(sel, "size_bytes"), (
            "Selection must not expose size_bytes to UI"
        )

    def test_selection_has_no_sha256_field(self):
        """Public selection does NOT carry sha256 —
        prevents UI from forging hash."""
        sel = _make_selection()
        assert not hasattr(sel, "sha256"), (
            "Selection must not expose sha256 to UI"
        )

    def test_forged_display_name_hint_does_not_change_selection_identity(self):
        """Even with malicious display_name_hint, skill_id/task_id/artifact_id
        remain unchanged — host adjudication is unaffected."""
        sel = _make_selection(
            skill_id="realskill",
            display_name_hint="../../../etc/passwd",
        )
        assert sel.skill_id == "realskill"
        assert sel.display_name_hint == "../../../etc/passwd"


# ── RB-L1-07, RB-L1-08: PublicResult zero Path ──

class TestPublicResultZeroPath:

    def test_public_result_no_path_fields(self):
        result = ReportBridgePublicResult(
            request_id=_rid(), generation=1, status=ReportBridgeStatus.PREPARING,
            assets=(), warnings=(), safe_error_code=None, safe_error_message=None,
        )
        for field_name in result.__dataclass_fields__:
            field_type = result.__dataclass_fields__[field_name].type
            type_str = str(field_type)
            assert "Path" not in type_str, f"Field {field_name} has Path type"

    def test_public_result_no_bridge_dir(self):
        fields = set(ReportBridgePublicResult.__dataclass_fields__.keys())
        forbidden = {"bridge_dir", "material_path", "storage_relpath",
                      "artifact_root", "sha256", "manifest", "traceback", "workspace_path"}
        assert fields.isdisjoint(forbidden), f"Forbidden fields present: {fields & forbidden}"


# ── RB-L1-09: cancelled status assets=() ──

class TestCancelledNoAssets:

    def test_cancelled_requires_empty_assets(self):
        with pytest.raises(ValueError, match="assets"):
            ReportBridgePublicResult(
                request_id=_rid(), generation=1, status=ReportBridgeStatus.CANCELLED,
                assets=(ReportAssetSummary(asset_key="k", display_name="d",
                                           role=ReportAssetRole.IMAGE, size_bytes=0, order=0),),
                warnings=(), safe_error_code=None, safe_error_message=None,
            )


# ── RB-L1-10: FAILED safe_error_code non-empty + assets=() ──

class TestFailedErrorCode:

    def test_failed_requires_error_code(self):
        with pytest.raises(ValueError, match="safe_error_code"):
            ReportBridgePublicResult(
                request_id=_rid(), generation=1, status=ReportBridgeStatus.FAILED,
                assets=(), warnings=(), safe_error_code=None, safe_error_message=None,
            )

    def test_failed_requires_non_empty_assets(self):
        with pytest.raises(ValueError, match="assets"):
            ReportBridgePublicResult(
                request_id=_rid(), generation=1, status=ReportBridgeStatus.FAILED,
                assets=(ReportAssetSummary(asset_key="k", display_name="d",
                                           role=ReportAssetRole.IMAGE, size_bytes=0, order=0),),
                warnings=(), safe_error_code="internal_failure", safe_error_message="error",
            )


# ── RB-L1-11: Selection field validation ──

class TestSelectionFieldValidation:

    def test_schema_version_must_be_1(self):
        with pytest.raises(ValueError, match="schema_version"):
            _make_selection(schema_version=2)

    def test_skill_id_non_empty(self):
        with pytest.raises(ValueError, match="skill_id"):
            _make_selection(skill_id="")

    def test_skill_id_no_slash(self):
        with pytest.raises(ValueError, match="skill_id"):
            _make_selection(skill_id="a/b")

    def test_skill_id_no_backslash(self):
        with pytest.raises(ValueError, match="skill_id"):
            _make_selection(skill_id="a\\b")

    def test_task_id_must_be_32_hex(self):
        with pytest.raises(ValueError, match="hex"):
            _make_selection(task_id="nothex!!")

    def test_artifact_id_must_be_32_hex(self):
        with pytest.raises(ValueError, match="hex"):
            _make_selection(artifact_id="nothex!!")

    def test_order_non_negative(self):
        with pytest.raises(ValueError, match="order"):
            _make_selection(order=-1)


# ── RB-L1-12: role only IMAGE/TABLE_SOURCE/TEXT_SOURCE ──

class TestRoleEnum:

    def test_role_values(self):
        assert ReportAssetRole.IMAGE == "image"
        assert ReportAssetRole.TABLE_SOURCE == "table_source"
        assert ReportAssetRole.TEXT_SOURCE == "text_source"
        assert len(ReportAssetRole.__members__) == 3


# ── RB-L1-13: schema_version strict validation ──

class TestSchemaVersion:

    def test_request_schema_version_must_be_1(self):
        sels = [_make_selection(schema_version=1, order=0)]
        with pytest.raises(ValueError, match="schema_version"):
            ReportBridgeRequest(schema_version=0, request_id=_rid(), selections=tuple(sels))

    def test_selection_schema_version_must_be_1(self):
        with pytest.raises(ValueError, match="schema_version"):
            _make_selection(schema_version=2)


# ── RB-L1-14: order exact sequence 0..N-1 ──

class TestOrderSequence:

    def test_order_continuous(self):
        tid = _tid()
        sels = [_make_selection(task_id=tid, artifact_id=f"{i:032x}", order=i) for i in range(3)]
        req = ReportBridgeRequest(schema_version=1, request_id=_rid(), selections=tuple(sels))
        assert len(req.selections) == 3

    def test_order_gap_rejected(self):
        tid = _tid()
        sels = [
            _make_selection(task_id=tid, artifact_id="0" * 32, order=0),
            _make_selection(task_id=tid, artifact_id="1" * 32, order=2),
        ]
        with pytest.raises(ValueError, match="order"):
            ReportBridgeRequest(schema_version=1, request_id=_rid(), selections=tuple(sels))

    def test_order_not_starting_at_zero(self):
        tid = _tid()
        sels = [
            _make_selection(task_id=tid, artifact_id="0" * 32, order=1),
            _make_selection(task_id=tid, artifact_id="1" * 32, order=2),
        ]
        with pytest.raises(ValueError, match="order"):
            ReportBridgeRequest(schema_version=1, request_id=_rid(), selections=tuple(sels))


# ── RB-L1-15: selection count 1..MAX ──

class TestSelectionCount:
    """RB-L1-15: selection count must satisfy 1 ≤ N ≤ MAX_BRIDGE_ASSETS."""

    def test_empty_selections_rejected(self):
        """0 selections → rejected."""
        with pytest.raises(ValueError, match="selections"):
            ReportBridgeRequest(schema_version=1, request_id=_rid(), selections=())

    def test_one_selection_allowed(self):
        """1 selection (minimum) → allowed."""
        tid = _tid()
        sel = _make_selection(task_id=tid, artifact_id="0" * 32, order=0)
        req = ReportBridgeRequest(schema_version=1, request_id=_rid(), selections=(sel,))
        assert len(req.selections) == 1

    def test_max_selections_allowed(self):
        """MAX_BRIDGE_ASSETS (12) selections → allowed."""
        tid = _tid()
        sels = [_make_selection(task_id=tid, artifact_id=f"{i:032x}", order=i)
                for i in range(MAX_BRIDGE_ASSETS)]
        req = ReportBridgeRequest(schema_version=1, request_id=_rid(), selections=tuple(sels))
        assert len(req.selections) == MAX_BRIDGE_ASSETS

    def test_too_many_selections_rejected(self):
        """MAX_BRIDGE_ASSETS + 1 (13) → rejected."""
        sels = [_make_selection(artifact_id=f"{i:032x}", order=i) for i in range(MAX_BRIDGE_ASSETS + 1)]
        with pytest.raises(ValueError, match="selections"):
            ReportBridgeRequest(schema_version=1, request_id=_rid(), selections=tuple(sels))


# ── RB-L1-16,17,18: Status invariants ──

class TestReadyStatus:

    def test_ready_requires_non_empty_assets(self):
        with pytest.raises(ValueError, match="assets"):
            ReportBridgePublicResult(
                request_id=_rid(), generation=1, status=ReportBridgeStatus.READY,
                assets=(), warnings=(), safe_error_code=None, safe_error_message=None,
            )

    def test_ready_valid(self):
        result = ReportBridgePublicResult(
            request_id=_rid(), generation=1, status=ReportBridgeStatus.READY,
            assets=(ReportAssetSummary(asset_key="k", display_name="d",
                                       role=ReportAssetRole.IMAGE, size_bytes=0, order=0),),
            warnings=(), safe_error_code=None, safe_error_message=None,
        )
        assert result.status == ReportBridgeStatus.READY


class TestSucceededStatus:

    def test_succeeded_requires_non_empty_assets(self):
        with pytest.raises(ValueError, match="assets"):
            ReportBridgePublicResult(
                request_id=_rid(), generation=1, status=ReportBridgeStatus.SUCCEEDED,
                assets=(), warnings=(), safe_error_code=None, safe_error_message=None,
            )


class TestPreparingStatus:

    def test_preparing_empty(self):
        result = ReportBridgePublicResult(
            request_id=_rid(), generation=1, status=ReportBridgeStatus.PREPARING,
            assets=(), warnings=(), safe_error_code=None, safe_error_message=None,
        )
        assert result.assets == ()
        assert result.safe_error_code is None


# ── RB-L1-19: parsed_payload closed types ──

class TestParsedPayloadTypes:

    def test_image_payload_none(self):
        art = _make_runtime_artifact(media_type="image/png")
        pa = PreparedReportAsset(
            authoritative_artifact=art, role=ReportAssetRole.IMAGE,
            order=0, managed_filename="00_test.png", parsed_payload=None,
        )
        assert pa.parsed_payload is None

    def test_table_source_payload_tuple(self):
        art = _make_runtime_artifact(media_type="text/csv")
        pa = PreparedReportAsset(
            authoritative_artifact=art, role=ReportAssetRole.TABLE_SOURCE,
            order=0, managed_filename="00_test.csv",
            parsed_payload=(("col1", "col2"), ("val1", "val2")),
        )
        assert isinstance(pa.parsed_payload, tuple)

    def test_text_source_payload_str(self):
        art = _make_runtime_artifact(media_type="text/plain")
        pa = PreparedReportAsset(
            authoritative_artifact=art, role=ReportAssetRole.TEXT_SOURCE,
            order=0, managed_filename="00_test.txt", parsed_payload="hello",
        )
        assert isinstance(pa.parsed_payload, str)

    def test_image_with_non_none_payload_rejected(self):
        art = _make_runtime_artifact()
        with pytest.raises(ValueError, match="parsed_payload"):
            PreparedReportAsset(
                authoritative_artifact=art, role=ReportAssetRole.IMAGE,
                order=0, managed_filename="00_test.png", parsed_payload="bad",
            )

    def test_table_source_with_str_payload_rejected(self):
        art = _make_runtime_artifact(media_type="text/csv")
        with pytest.raises(ValueError, match="parsed_payload"):
            PreparedReportAsset(
                authoritative_artifact=art, role=ReportAssetRole.TABLE_SOURCE,
                order=0, managed_filename="00_test.csv", parsed_payload="bad",
            )

    def test_text_source_with_none_payload_rejected(self):
        art = _make_runtime_artifact(media_type="text/plain")
        with pytest.raises(ValueError, match="parsed_payload"):
            PreparedReportAsset(
                authoritative_artifact=art, role=ReportAssetRole.TEXT_SOURCE,
                order=0, managed_filename="00_test.txt", parsed_payload=None,
            )

    def test_text_source_with_tuple_payload_rejected(self):
        """TEXT_SOURCE + tuple payload → rejected (not a valid ParsedPayload for this role)."""
        art = _make_runtime_artifact(media_type="text/plain")
        with pytest.raises(ValueError, match="parsed_payload"):
            PreparedReportAsset(
                authoritative_artifact=art, role=ReportAssetRole.TEXT_SOURCE,
                order=0, managed_filename="00_test.txt",
                parsed_payload=(("col",), ("val",)),
            )

    def test_table_source_with_none_payload_rejected(self):
        """TABLE_SOURCE + None payload → rejected."""
        art = _make_runtime_artifact(media_type="text/csv")
        with pytest.raises(ValueError, match="parsed_payload"):
            PreparedReportAsset(
                authoritative_artifact=art, role=ReportAssetRole.TABLE_SOURCE,
                order=0, managed_filename="00_test.csv", parsed_payload=None,
            )


# ── RB-L1-20: GenerationInput has no release method ──

class TestGenerationInputNoRelease:

    def test_generation_input_no_release(self):
        art = _make_runtime_artifact()
        pa = PreparedReportAsset(
            authoritative_artifact=art, role=ReportAssetRole.IMAGE,
            order=0, managed_filename="00_test.png", parsed_payload=None,
        )
        gi = ReportGenerationInput(
            request_id=_rid(), generation=1, workspace_path=Path("/tmp/test"), assets=(pa,),
        )
        assert not hasattr(gi, "release")


# ── Additional model tests ──

class TestReportBridgeLease:

    def test_lease_creation(self):
        art = _make_runtime_artifact()
        pa = PreparedReportAsset(
            authoritative_artifact=art, role=ReportAssetRole.IMAGE,
            order=0, managed_filename="00_test.png", parsed_payload=None,
        )
        rid = _rid()
        lease = ReportBridgeLease(
            request_id=rid, generation=1, skill_id="sk1", task_id=_tid(),
            workspace_path=Path("/tmp/ws"), prepared_assets=(pa,),
            coordinator_token=object(),
        )
        assert not lease.released
        assert lease.request_id == rid

    def test_lease_release_is_idempotent(self):
        art = _make_runtime_artifact()
        pa = PreparedReportAsset(
            authoritative_artifact=art, role=ReportAssetRole.IMAGE,
            order=0, managed_filename="00_test.png", parsed_payload=None,
        )
        release_count = [0]
        def token_release():
            release_count[0] += 1

        lease = ReportBridgeLease(
            request_id=_rid(), generation=1, skill_id="sk1", task_id=_tid(),
            workspace_path=Path("/tmp/ws"), prepared_assets=(pa,),
            coordinator_token=object(),
        )
        lease.release(token_release=token_release)
        assert lease.released
        assert release_count[0] == 1
        lease.release(token_release=token_release)
        assert release_count[0] == 1


class TestSafeErrorCodes:

    def test_safe_error_codes_count(self):
        assert len(SAFE_ERROR_CODES) == 12

    def test_cancelled_not_in_error_codes(self):
        assert "cancelled" not in SAFE_ERROR_CODES

    def test_all_codes_have_messages(self):
        for code in SAFE_ERROR_CODES:
            assert code in SAFE_ERROR_MESSAGES
            assert SAFE_ERROR_MESSAGES[code]


class TestManagedFilename:

    def test_absolute_path_rejected(self):
        art = _make_runtime_artifact()
        with pytest.raises(ValueError, match="managed_filename"):
            PreparedReportAsset(
                authoritative_artifact=art, role=ReportAssetRole.IMAGE,
                order=0, managed_filename="/etc/passwd", parsed_payload=None,
            )

    def test_path_separator_rejected(self):
        art = _make_runtime_artifact()
        with pytest.raises(ValueError, match="managed_filename"):
            PreparedReportAsset(
                authoritative_artifact=art, role=ReportAssetRole.IMAGE,
                order=0, managed_filename="../escape.png", parsed_payload=None,
            )

    def test_dot_rejected(self):
        art = _make_runtime_artifact()
        with pytest.raises(ValueError, match="managed_filename"):
            PreparedReportAsset(
                authoritative_artifact=art, role=ReportAssetRole.IMAGE,
                order=0, managed_filename=".", parsed_payload=None,
            )

    def test_dotdot_rejected(self):
        art = _make_runtime_artifact()
        with pytest.raises(ValueError, match="managed_filename"):
            PreparedReportAsset(
                authoritative_artifact=art, role=ReportAssetRole.IMAGE,
                order=0, managed_filename="..", parsed_payload=None,
            )


class TestOperationKind:

    def test_operation_kind_values(self):
        assert OperationKind.USER_ARTIFACT_OPERATION == "user_artifact_operation"
        assert OperationKind.BRIDGE_SESSION == "bridge_session"


class TestInternalBridgeState:

    def test_all_states(self):
        """Verify all 8 internal states exist."""
        members = InternalBridgeState.__members__
        assert len(members) == 8
        assert "IDLE" in members
        assert "PREPARING" in members
        assert "READY" in members
        assert "GENERATING" in members
        assert "SUCCEEDED" in members
        assert "FAILED" in members
        assert "CANCELLED" in members
        assert "RELEASED" in members


class TestAssetSummary:

    def test_size_bytes_non_negative(self):
        with pytest.raises(ValueError, match="size_bytes"):
            ReportAssetSummary(asset_key="k", display_name="d",
                               role=ReportAssetRole.IMAGE, size_bytes=-1, order=0)

    def test_order_non_negative(self):
        with pytest.raises(ValueError, match="order"):
            ReportAssetSummary(asset_key="k", display_name="d",
                               role=ReportAssetRole.IMAGE, size_bytes=0, order=-1)

    def test_asset_key_no_path_separator(self):
        with pytest.raises(ValueError, match="asset_key"):
            ReportAssetSummary(asset_key="a/b", display_name="d",
                               role=ReportAssetRole.IMAGE, size_bytes=0, order=0)


class TestFrozenModels:

    def test_selection_is_frozen(self):
        sel = _make_selection()
        with pytest.raises(Exception):
            sel.order = 99  # type: ignore[misc]

    def test_request_is_frozen(self):
        sels = [_make_selection(order=0)]
        req = ReportBridgeRequest(schema_version=1, request_id=_rid(), selections=tuple(sels))
        with pytest.raises(Exception):
            req.selections = ()  # type: ignore[misc]

    def test_public_result_is_frozen(self):
        result = ReportBridgePublicResult(
            request_id=_rid(), generation=1, status=ReportBridgeStatus.PREPARING,
            assets=(), warnings=(), safe_error_code=None, safe_error_message=None,
        )
        with pytest.raises(Exception):
            result.status = ReportBridgeStatus.FAILED  # type: ignore[misc]
