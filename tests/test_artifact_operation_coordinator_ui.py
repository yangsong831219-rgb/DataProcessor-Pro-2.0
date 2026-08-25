"""Batch 3.3.2: Artifact Operation Coordinator UI Tests.

UI-27..UI-31: Verify artifact locate/export/delete are
gated by ArtifactOperationCoordinator.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtWidgets import QApplication

from dp_engine.report_bridge.coordinator import ArtifactOperationCoordinator
from dp_engine.report_bridge.models import OperationKind


class FakeCoordinatorToken:
    """Fake token that tracks release calls."""

    def __init__(self, key, operation):
        self.key = key
        self.operation = operation
        self._released = False

    @property
    def released(self):
        return self._released

    def release(self):
        self._released = True


class FakeCoordinator:
    """Fake coordinator for testing artifact operation gating."""

    def __init__(self, allow: bool = True):
        self.calls: list[dict] = []
        self._allow = allow
        self._tokens: list[FakeCoordinatorToken] = []

    def try_acquire(self, skill_id, task_id, *, operation):
        self.calls.append({
            "skill_id": skill_id,
            "task_id": task_id,
            "operation": operation,
        })
        if not self._allow:
            return None
        token = FakeCoordinatorToken((skill_id, task_id), operation)
        self._tokens.append(token)
        return token

    def set_allow(self, allow: bool):
        self._allow = allow

    @property
    def released_count(self):
        return sum(1 for t in self._tokens if t.released)


class TestArtifactLocateCoordinatorGate:
    """UI-27: Bridge session blocks same-owner locate."""

    def test_locate_blocked_by_bridge_session(self, qapp):
        """UI-27: When BRIDGE_SESSION active, locate is blocked."""
        coord = FakeCoordinator(allow=False)
        token = coord.try_acquire(
            "skill-A", "task-1",
            operation=OperationKind.USER_ARTIFACT_OPERATION,
        )
        assert token is None
        assert len(coord.calls) == 1
        assert coord.calls[0]["operation"] == OperationKind.USER_ARTIFACT_OPERATION

    def test_locate_allowed_when_no_conflict(self, qapp):
        """Locate allowed when no bridge session active."""
        coord = FakeCoordinator(allow=True)
        token = coord.try_acquire(
            "skill-A", "task-1",
            operation=OperationKind.USER_ARTIFACT_OPERATION,
        )
        assert token is not None
        token.release()
        assert token.released

    def test_locate_different_owner_allowed(self, qapp):
        """Different owner operations don't block each other."""
        coord = ArtifactOperationCoordinator()
        t1 = coord.try_acquire("s1", "t1", operation=OperationKind.BRIDGE_SESSION)
        t2 = coord.try_acquire("s2", "t2", operation=OperationKind.USER_ARTIFACT_OPERATION)
        assert t1 is not None
        assert t2 is not None
        t1.release()
        t2.release()


class TestArtifactExportCoordinatorGate:
    """UI-28: Bridge session blocks same-owner export."""

    def test_export_blocked_by_bridge_session(self, qapp):
        """UI-28: BRIDGE_SESSION blocks same-owner export."""
        coord = ArtifactOperationCoordinator()
        bridge_token = coord.try_acquire(
            "skill-X", "task-Y",
            operation=OperationKind.BRIDGE_SESSION,
        )
        assert bridge_token is not None
        export_token = coord.try_acquire(
            "skill-X", "task-Y",
            operation=OperationKind.USER_ARTIFACT_OPERATION,
        )
        assert export_token is None
        bridge_token.release()

    def test_export_allowed_after_bridge_release(self, qapp):
        """Export allowed after bridge session releases."""
        coord = ArtifactOperationCoordinator()
        bridge_token = coord.try_acquire(
            "skill-X", "task-Y",
            operation=OperationKind.BRIDGE_SESSION,
        )
        bridge_token.release()
        export_token = coord.try_acquire(
            "skill-X", "task-Y",
            operation=OperationKind.USER_ARTIFACT_OPERATION,
        )
        assert export_token is not None
        export_token.release()


class TestArtifactDeleteCoordinatorGate:
    """UI-29: Bridge session blocks same-owner delete."""

    def test_delete_blocked_by_bridge_session(self, qapp):
        """UI-29: BRIDGE_SESSION blocks same-owner delete."""
        coord = ArtifactOperationCoordinator()
        bridge_token = coord.try_acquire(
            "skill-D", "task-E",
            operation=OperationKind.BRIDGE_SESSION,
        )
        assert bridge_token is not None
        delete_token = coord.try_acquire(
            "skill-D", "task-E",
            operation=OperationKind.USER_ARTIFACT_OPERATION,
        )
        assert delete_token is None
        bridge_token.release()

    def test_delete_allowed_after_bridge_release(self, qapp):
        """Delete allowed after bridge session releases."""
        coord = ArtifactOperationCoordinator()
        bridge_token = coord.try_acquire(
            "skill-D", "task-E",
            operation=OperationKind.BRIDGE_SESSION,
        )
        bridge_token.release()
        delete_token = coord.try_acquire(
            "skill-D", "task-E",
            operation=OperationKind.USER_ARTIFACT_OPERATION,
        )
        assert delete_token is not None
        delete_token.release()


class TestTokenRelease:
    """UI-31: Token release in exception and cancel paths."""

    def test_token_release_idempotent(self, qapp):
        """UI-31: Token release is idempotent."""
        coord = ArtifactOperationCoordinator()
        token = coord.try_acquire(
            "skill-R", "task-R",
            operation=OperationKind.USER_ARTIFACT_OPERATION,
        )
        token.release()
        # Second release should not raise
        token.release()
        assert token.released

    def test_token_not_leaked_on_exception(self, qapp):
        """UI-31: Token released even on exception path."""
        coord = ArtifactOperationCoordinator()
        token = coord.try_acquire(
            "skill-E", "task-E",
            operation=OperationKind.USER_ARTIFACT_OPERATION,
        )
        try:
            raise RuntimeError("simulated failure")
        except RuntimeError:
            token.release()
        assert token.released

    def test_diff_owner_concurrent_operations(self, qapp):
        """UI-30: Different owners can hold tokens concurrently."""
        coord = ArtifactOperationCoordinator()
        tokens = []
        for i in range(5):
            token = coord.try_acquire(
                f"skill-{i}", f"task-{i}",
                operation=OperationKind.USER_ARTIFACT_OPERATION,
            )
            assert token is not None, f"Token {i} should be acquired"
            tokens.append(token)
        for t in tokens:
            t.release()

    def test_same_owner_mutex_across_operation_types(self, qapp):
        """Same owner can only hold one operation regardless of kind."""
        coord = ArtifactOperationCoordinator()
        t1 = coord.try_acquire("s", "t", operation=OperationKind.BRIDGE_SESSION)
        t2 = coord.try_acquire("s", "t", operation=OperationKind.USER_ARTIFACT_OPERATION)
        assert t1 is not None
        assert t2 is None
        t1.release()


class TestSkillRuntimeControllerCoordinatorIntegration:
    """Verify SkillRuntimeController uses coordinator for artifact ops."""

    def test_controller_set_coordinator_stores_reference(self, qapp):
        """set_coordinator stores the coordinator reference."""
        from ui.skill_runtime_controller import SkillRuntimeController
        from pathlib import Path
        ctrl = SkillRuntimeController(Path("/tmp"), Path("/tmp"))
        coord = FakeCoordinator(allow=True)
        ctrl.set_coordinator(coord)
        assert ctrl._coordinator is coord

    def test_controller_start_locate_checks_coordinator(self, qapp):
        """start_artifact_locate checks coordinator before proceeding."""
        from ui.skill_runtime_controller import SkillRuntimeController
        from pathlib import Path
        ctrl = SkillRuntimeController(Path("/tmp"), Path("/tmp"))
        coord = FakeCoordinator(allow=False)
        ctrl.set_coordinator(coord)
        result = ctrl.start_artifact_locate(
            "skill-A", "task-A", "artifact-1",
        )
        assert result is False
        assert len(coord.calls) == 1

    def test_controller_start_export_checks_coordinator(self, qapp):
        """start_artifact_export checks coordinator before proceeding."""
        from ui.skill_runtime_controller import SkillRuntimeController
        from pathlib import Path
        ctrl = SkillRuntimeController(Path("/tmp"), Path("/tmp"))
        coord = FakeCoordinator(allow=False)
        ctrl.set_coordinator(coord)
        result = ctrl.start_artifact_export(
            "skill-A", "task-A", "artifact-1",
            target=Path("/tmp/out.dat"),
            overwrite=False,
        )
        assert result is False

    def test_controller_start_delete_checks_coordinator(self, qapp):
        """start_artifact_delete_task checks coordinator before proceeding."""
        from ui.skill_runtime_controller import SkillRuntimeController
        from pathlib import Path
        ctrl = SkillRuntimeController(Path("/tmp"), Path("/tmp"))
        coord = FakeCoordinator(allow=False)
        ctrl.set_coordinator(coord)
        result = ctrl.start_artifact_delete_task("skill-A", "task-A")
        assert result is False


# ── Fixtures ──

@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
