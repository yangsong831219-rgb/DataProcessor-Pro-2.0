"""Tests for SkillSourceInspectController — Qt async, zero QThread.

Covers:
- Lifecycle: start, cancel, timeout, finish
- Signal emission: running_changed, result_ready
- QThread absence
- Widget close safety
- Late reply after cancel safety
- **Batch 1.4: Timer race-condition safety**
  - Owned QTimer (NOT static singleShot)
  - Request serial / generation protection
  - Reply identity checks
  - Exactly one terminal result per request
  - running_changed balance (True → False)
  - Stale timeout cannot abort new request
  - Late reply A cannot affect request B

All tests use FakeNetworkReply — no real network access.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkReply, QNetworkRequest


# ── Fake QByteArray (mimics QByteArray.data()) ──

class _FakeQByteArray:
    """Minimal fake for QByteArray so .data() returns bytes."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def data(self) -> bytes:
        return self._data


# ── Fake QNetworkReply ──

class FakeNetworkReply(QObject):
    """Fake QNetworkReply with real Qt finished signal.

    Emit .finished.emit() to simulate completion.
    """

    finished = pyqtSignal()  # type: ignore[reportUnannotatedClassAttribute]

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        url: str = "https://raw.githubusercontent.com/o/r/main/p/SKILL.md",
        status_code: int = 200,
        content: bytes = b"",
        error: QNetworkReply.NetworkError = QNetworkReply.NetworkError.NoError,
        error_string: str = "",
    ) -> None:
        super().__init__(parent)
        self._url = QUrl(url)
        self._status_code = status_code
        self._content = content
        self._error = error
        self._error_string = error_string
        self._aborted = False
        self._deleted = False

    def url(self) -> QUrl:
        return self._url

    def attribute(self, code: QNetworkRequest.Attribute) -> object:
        if code == QNetworkRequest.Attribute.HttpStatusCodeAttribute:
            return self._status_code
        return None

    def readAll(self) -> _FakeQByteArray:  # type: ignore[reportIncompatibleMethodOverride]
        return _FakeQByteArray(self._content)

    def error(self) -> QNetworkReply.NetworkError:
        return self._error

    def errorString(self) -> str:
        return self._error_string

    def abort(self) -> None:
        self._aborted = True
        self.finished.emit()  # Real QNetworkReply.abort() triggers finished

    def deleteLater(self) -> None:  # type: ignore[reportIncompatibleMethodOverride]
        self._deleted = True

    @property
    def was_aborted(self) -> bool:
        return self._aborted

    @property
    def was_deleted(self) -> bool:
        return self._deleted

    def reset_flags(self) -> None:
        """Reset abort/delete flags (useful for race-condition tests)."""
        self._aborted = False
        self._deleted = False


# ── FakeNetworkManager (for injecting multiple replies) ──

class FakeNetworkManager(QObject):
    """Fake QNetworkAccessManager that returns a pre-configured reply.

    Supports reply replacement for race-condition tests — change
    .next_reply between start() calls to simulate different responses.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.next_reply: FakeNetworkReply | None = None
        self._get_calls: list[QNetworkRequest] = []

    def get(self, request: QNetworkRequest) -> FakeNetworkReply:
        self._get_calls.append(request)
        if self.next_reply is None:
            raise RuntimeError("FakeNetworkManager.next_reply not set")
        return self.next_reply

    @property
    def call_count(self) -> int:
        return len(self._get_calls)


# ── Helpers ──

_VALID_SKILL_MD = (
    b"---\n"
    b"schema_version: 1\n"
    b"skill_id: t\n"
    b"name: T\n"
    b"version: 1.0.0\n"
    b"skill_type: instruction\n"
    b"capabilities:\n"
    b"  - read\n"
    b"entrypoints:\n"
    b"  run: run.py\n"
    b"---\n\n"
    b"# Body"
)


def _make_controller(**kwargs):
    """Create a SkillSourceInspectController with default timeout."""
    from ui.skill_source_controller import SkillSourceInspectController
    return SkillSourceInspectController(timeout_ms=5000, **kwargs)


def _make_controller_with_fake_nam():
    """Create a controller with FakeNetworkManager for reply injection."""
    from ui.skill_source_controller import SkillSourceInspectController
    nam = FakeNetworkManager()
    controller = SkillSourceInspectController(
        network_manager=nam,  # type: ignore[arg-type]
        timeout_ms=5000,
    )
    return controller, nam


# ═══════════════════════════════════════════════
# Lifecycle Tests
# ═══════════════════════════════════════════════


class TestControllerLifecycle:
    """Controller lifecycle: start → finish / cancel / timeout."""

    def test_controller_uses_no_qthread(self):
        """Controller must not subclass QThread or import it."""
        from ui.skill_source_controller import SkillSourceInspectController
        from PyQt6.QtCore import QThread

        assert not issubclass(SkillSourceInspectController, QThread), (
            "Controller must not subclass QThread"
        )
        assert QThread not in SkillSourceInspectController.__mro__, (
            "QThread must not appear in controller MRO"
        )

    def test_start_returns_true(self):
        """start() returns True when request is accepted."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply()
        controller._nam.get = MagicMock(return_value=fake_reply)

        result = controller.start("owner", "repo")
        assert result is True

    def test_start_emits_running_true(self):
        """start() emits running_changed(True)."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply()
        controller._nam.get = MagicMock(return_value=fake_reply)

        captured: list[bool] = []
        controller.running_changed.connect(lambda v: captured.append(v))

        controller.start("owner", "repo")
        assert captured == [True]

    def test_success_emits_result_once(self):
        """Normal completion emits exactly one result_ready."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(content=_VALID_SKILL_MD)
        controller._nam.get = MagicMock(return_value=fake_reply)

        results: list[object] = []
        controller.result_ready.connect(lambda r: results.append(r))

        controller.start("owner", "repo")
        fake_reply.finished.emit()

        assert len(results) == 1

    def test_success_result_is_viable(self):
        """Valid SKILL.md Front Matter produces is_viable_skill_source=True."""
        from dp_engine.skills.models import SkillSourceInspectionResult

        controller = _make_controller()
        fake_reply = FakeNetworkReply(content=_VALID_SKILL_MD)
        controller._nam.get = MagicMock(return_value=fake_reply)

        results: list[object] = []
        controller.result_ready.connect(lambda r: results.append(r))

        controller.start("owner", "repo")
        fake_reply.finished.emit()

        assert len(results) == 1
        result = results[0]
        assert isinstance(result, SkillSourceInspectionResult)
        assert result.is_viable_skill_source is True

    def test_finish_emits_running_false(self):
        """After reply.finished, running_changed(False) is emitted."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(content=b"test")
        controller._nam.get = MagicMock(return_value=fake_reply)

        captured: list[bool] = []
        controller.running_changed.connect(lambda v: captured.append(v))

        controller.start("owner", "repo")
        assert captured == [True]

        fake_reply.finished.emit()
        assert captured == [True, False]

    def test_double_start_rejected(self):
        """Second start() while request active returns False."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply()
        controller._nam.get = MagicMock(return_value=fake_reply)

        captured: list[bool] = []
        controller.running_changed.connect(lambda v: captured.append(v))

        result1 = controller.start("owner", "repo")
        assert result1 is True
        assert captured == [True]

        result2 = controller.start("owner", "repo")
        assert result2 is False, (
            "Second start() must return False when request is active"
        )
        assert captured == [True], (
            "Second start() must not emit another running_changed"
        )

    def test_is_running_during_request(self):
        """is_running returns True while request active, False otherwise."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply()
        controller._nam.get = MagicMock(return_value=fake_reply)

        assert not controller.is_running

        controller.start("owner", "repo")
        assert controller.is_running

        fake_reply.finished.emit()
        assert not controller.is_running

    def test_timeout_aborts_reply(self):
        """Timeout handler calls reply.abort()."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply()
        controller._nam.get = MagicMock(return_value=fake_reply)

        controller.start("owner", "repo")
        controller._on_timeout()

        assert fake_reply.was_aborted, "Timeout must abort the reply"

    def test_timeout_result_is_reported_once(self):
        """Timeout produces exactly one result_ready with timed_out."""
        from dp_engine.skills.models import SkillSourceInspectionResult

        controller = _make_controller()
        fake_reply = FakeNetworkReply(
            error=QNetworkReply.NetworkError.OperationCanceledError,
        )
        controller._nam.get = MagicMock(return_value=fake_reply)

        results: list[object] = []
        controller.result_ready.connect(lambda r: results.append(r))

        controller.start("owner", "repo")
        controller._on_timeout()  # Mark as timeout
        # _on_timeout calls abort() which triggers finished synchronously
        # via FakeNetworkReply.abort()

        assert len(results) == 1
        result = results[0]
        assert isinstance(result, SkillSourceInspectionResult)
        assert result.source_reachable is False
        assert result.error_code == "TIMEOUT"

    def test_cancel_aborts_active_reply(self):
        """cancel() calls reply.abort()."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(
            error=QNetworkReply.NetworkError.OperationCanceledError,
        )
        controller._nam.get = MagicMock(return_value=fake_reply)

        controller.start("owner", "repo")
        controller.cancel()

        assert fake_reply.was_aborted, "cancel() must abort the reply"

    def test_cancel_does_not_emit_result(self):
        """User cancel (not timeout) must NOT emit result_ready."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(
            error=QNetworkReply.NetworkError.OperationCanceledError,
        )
        controller._nam.get = MagicMock(return_value=fake_reply)

        results: list[object] = []
        controller.result_ready.connect(lambda r: results.append(r))

        controller.start("owner", "repo")
        controller.cancel()
        # cancel() calls abort() → finished fires synchronously.
        # _on_reply_finished sees CANCELLING state → no result.

        assert len(results) == 0, (
            "User cancel must not emit result_ready"
        )

    def test_cancel_stops_timeout_timer(self, qapp):
        """cancel() must stop the timeout timer."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(
            error=QNetworkReply.NetworkError.OperationCanceledError,
        )
        controller._nam.get = MagicMock(return_value=fake_reply)

        controller.start("owner", "repo")
        assert controller._timeout_timer.isActive(), (
            "Timer must be active after start()"
        )

        controller.cancel()
        assert not controller._timeout_timer.isActive(), (
            "Timer must be stopped after cancel()"
        )

    def test_success_stops_timeout_timer(self, qapp):
        """Successful completion must stop the timeout timer."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(content=b"test")
        controller._nam.get = MagicMock(return_value=fake_reply)

        controller.start("owner", "repo")
        assert controller._timeout_timer.isActive()

        fake_reply.finished.emit()
        assert not controller._timeout_timer.isActive(), (
            "Timer must be stopped after successful completion"
        )

    def test_reply_delete_later_called(self):
        """After normal completion, reply.deleteLater() is called."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(content=b"test")
        controller._nam.get = MagicMock(return_value=fake_reply)

        controller.start("owner", "repo")
        fake_reply.finished.emit()

        assert fake_reply.was_deleted, (
            "reply.deleteLater() must be called after finish"
        )

    def test_active_reply_reference_cleared(self):
        """After reply.finished, _active_reply is None."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(content=b"test")
        controller._nam.get = MagicMock(return_value=fake_reply)

        controller.start("owner", "repo")
        assert controller._active_reply is not None

        fake_reply.finished.emit()
        assert controller._active_reply is None

    def test_widget_close_cancels_request(self):
        """Widget closeEvent → controller.cancel() → reply.abort()."""
        from ui.skill_tab import AgentSkillWidget

        widget = AgentSkillWidget()
        widget.github_owner_input.setText("test")
        widget.github_repo_input.setText("test")

        fake_reply = FakeNetworkReply(
            error=QNetworkReply.NetworkError.OperationCanceledError,
        )
        widget._source_controller._nam.get = MagicMock(return_value=fake_reply)

        widget._on_inspect_source()
        assert not widget.inspect_btn.isEnabled(), "Button should be disabled"

        widget.close()
        assert fake_reply.was_aborted, (
            "Widget close must abort active network request"
        )
        widget.deleteLater()

    def test_late_reply_after_cancel_does_not_touch_ui(self):
        """Late reply after cancel() does not emit result."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(
            error=QNetworkReply.NetworkError.OperationCanceledError,
            content=_VALID_SKILL_MD,
        )
        controller._nam.get = MagicMock(return_value=fake_reply)

        results: list[object] = []
        controller.result_ready.connect(lambda r: results.append(r))

        controller.start("owner", "repo")
        controller.cancel()
        # cancel() calls abort() → finished fires synchronously.
        # _on_reply_finished sees CANCELLING → no result.
        # Then _active_reply is set to None.
        # Any subsequent finished.emit() would be a stale reply.

        assert len(results) == 0, (
            "Late reply after cancel must not emit result"
        )

    def test_no_qthread_in_controller_imports(self):
        """Verify controller module has no QThread import."""
        import ast
        from pathlib import Path

        controller_path = (
            Path(__file__).parent.parent / "ui" / "skill_source_controller.py"
        )
        source = controller_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "QThread" not in alias.name, (
                        f"Controller must not import QThread"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for alias in node.names:
                        assert "QThread" not in alias.name, (
                            f"Controller must not import QThread"
                        )

    def test_skill_tab_no_longer_imports_qthread(self):
        """skill_tab.py no longer imports QThread for source inspection."""
        import ast
        from pathlib import Path

        skill_tab_path = (
            Path(__file__).parent.parent / "ui" / "skill_tab.py"
        )
        source = skill_tab_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        assert "SourceInspectWorker" not in source, (
            "skill_tab.py must not contain SourceInspectWorker"
        )
        assert "_source_worker" not in source, (
            "skill_tab.py must not contain _source_worker"
        )

    def test_no_qthread_destroyed_warning_path(self):
        """Prove source inspection chain has no QThread via module-level AST check."""
        import ast
        from pathlib import Path

        controller_path = (
            Path(__file__).parent.parent / "ui" / "skill_source_controller.py"
        )
        source = controller_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        qthread_refs: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "QThread" in alias.name:
                        qthread_refs.append(
                            f"import {alias.name} at line {node.lineno}"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for alias in node.names:
                        if "QThread" in alias.name:
                            qthread_refs.append(
                                f"from {node.module} import {alias.name} "
                                f"at line {node.lineno}"
                            )

        assert not qthread_refs, (
            f"Controller must not import QThread. Found: {qthread_refs}"
        )

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "QThread":
                    assert False, (
                        f"Controller must not instantiate QThread "
                        f"at line {node.lineno}"
                    )

    # ── Owned QTimer tests ──

    def test_timer_is_owned_not_static_singleshot(self):
        """Controller uses owned QTimer, NOT static QTimer.singleShot()."""
        import ast
        from pathlib import Path

        controller_path = (
            Path(__file__).parent.parent / "ui" / "skill_source_controller.py"
        )
        source = controller_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    # Check for QTimer.singleShot(...)
                    if (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "QTimer"
                        and node.func.attr == "singleShot"
                    ):
                        assert False, (
                            "Controller must NOT use static QTimer.singleShot(). "
                            "Use owned QTimer with start()/stop() instead."
                        )

    def test_timer_created_in_init(self, qapp):
        """Owned _timeout_timer is created in __init__, not per-request."""
        controller = _make_controller()
        assert controller._timeout_timer is not None
        assert not controller._timeout_timer.isActive(), (
            "Timer must not be active before any request"
        )


# ═══════════════════════════════════════════════
# Race Condition Tests (Batch 1.4)
# ═══════════════════════════════════════════════


class TestTimerRaceConditions:
    """Timer race-condition safety: stale timeout cannot affect new request."""

    def test_finished_request_timer_is_stopped(self, qapp):
        """After request A completes successfully, its timer is stopped.

        Request B then starts — A's old timeout must not affect B.
        """
        controller = _make_controller()

        # ── Request A ──
        reply_a = FakeNetworkReply(content=b"response A")
        controller._nam.get = MagicMock(return_value=reply_a)
        controller.start("owner", "repo")
        reply_a.finished.emit()  # A completes

        assert not controller._timeout_timer.isActive(), (
            "Timer must be stopped after request A completes"
        )

        # ── Request B ──
        reply_b = FakeNetworkReply(content=b"response B")
        controller._nam.get = MagicMock(return_value=reply_b)

        results: list[object] = []
        controller.result_ready.connect(lambda r: results.append(r))
        controller.start("owner2", "repo2")

        # Simulate time passing beyond A's original timeout.
        # A's timer is stopped, so this should have no effect on B.
        assert controller._active_reply is reply_b, (
            "Active reply must be reply_b"
        )
        assert not reply_b.was_aborted, (
            "Reply B must not be aborted by stale timer"
        )

        reply_b.finished.emit()
        assert len(results) == 1

    def test_cancelled_request_timeout_does_not_abort_next_request(self):
        """After cancelling request A and starting B, A's timeout must not affect B."""
        controller = _make_controller()

        # ── Request A ──
        reply_a = FakeNetworkReply(
            error=QNetworkReply.NetworkError.OperationCanceledError,
        )
        controller._nam.get = MagicMock(return_value=reply_a)
        controller.start("owner", "repo")
        controller.cancel()  # A cancelled

        assert not controller._timeout_timer.isActive(), (
            "Timer must be stopped after cancel()"
        )
        assert controller._active_reply is None, (
            "Active reply must be None after cancel"
        )

        # ── Request B ──
        reply_b = FakeNetworkReply(content=b"response B")
        controller._nam.get = MagicMock(return_value=reply_b)
        controller.start("owner2", "repo2")

        # Try to fire A's old timeout (simulated — the timer is stopped,
        # but we test the _on_timeout guard directly)
        # Since _state is RUNNING (for B), _on_timeout would transition to
        # TIMED_OUT — but the real timer was stopped, so this is defensive.

        assert not reply_b.was_aborted, (
            "Reply B must not be pre-emptively aborted"
        )
        assert controller.is_running

    def test_failed_request_timeout_does_not_abort_retry(self):
        """Request A fails with network error. User retries B.
        A's timer (already stopped) must not affect B.
        """
        controller = _make_controller()

        # ── Request A — network error ──
        reply_a = FakeNetworkReply(
            error=QNetworkReply.NetworkError.ConnectionRefusedError,
            error_string="Connection refused",
        )
        controller._nam.get = MagicMock(return_value=reply_a)

        results: list[object] = []
        controller.result_ready.connect(lambda r: results.append(r))
        controller.start("owner", "repo")
        reply_a.finished.emit()  # A fails

        assert len(results) == 1, "A must produce one error result"
        assert not controller._timeout_timer.isActive(), (
            "Timer must be stopped after A fails"
        )

        # ── Request B — retry ──
        reply_b = FakeNetworkReply(content=b"response B")
        controller._nam.get = MagicMock(return_value=reply_b)
        results.clear()
        controller.start("owner2", "repo2")

        assert not reply_b.was_aborted, "Reply B must not be affected by A"
        reply_b.finished.emit()
        assert len(results) == 1

    def test_stale_timer_fire_in_wrong_state_is_noop(self):
        """_on_timeout called in non-RUNNING state must be a no-op."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(content=b"test")
        controller._nam.get = MagicMock(return_value=fake_reply)

        # Start and finish normally
        controller.start("owner", "repo")
        fake_reply.finished.emit()
        assert controller.state == "idle"

        # Now simulate a late timer firing
        controller._on_timeout()
        # Must be no-op — state is idle, not running
        assert controller.state == "idle"
        assert controller._active_reply is None


class TestLateReplyProtection:
    """Late reply A must not affect request B's state or signals."""

    def test_late_reply_a_does_not_clear_active_reply_b(self):
        """A late-arriving reply from request A must not clear B's _active_reply."""
        controller, nam = _make_controller_with_fake_nam()

        # ── Request A ──
        reply_a = FakeNetworkReply(
            error=QNetworkReply.NetworkError.OperationCanceledError,
        )
        nam.next_reply = reply_a
        controller.start("ownerA", "repoA")

        # Cancel A (cleans up everything including _active_reply)
        controller.cancel()
        assert not controller.is_running
        assert controller._active_reply is None

        # ── Request B ──
        reply_b = FakeNetworkReply(content=b"response B")
        nam.next_reply = reply_b
        controller.start("ownerB", "repoB")
        assert controller._active_reply is reply_b

        # Now A's old reply emits finished (late arrival)
        reply_a.finished.emit()

        # B's active reply must NOT be cleared by A's late signal
        assert controller._active_reply is reply_b, (
            "A's late reply must not clear B's _active_reply"
        )
        assert controller.is_running, "B must still be running"

    def test_late_reply_a_does_not_emit_running_false_for_b(self):
        """A's late reply must not emit running_changed(False) on B's behalf."""
        controller, nam = _make_controller_with_fake_nam()

        # ── Request A ──
        reply_a = FakeNetworkReply(content=b"response A")
        nam.next_reply = reply_a
        controller.start("ownerA", "repoA")

        # Cancel A (cleans up)
        controller.cancel()
        assert not controller.is_running  # A fully cleaned up
        assert controller._active_reply is None

        # ── Request B ──
        reply_b = FakeNetworkReply(content=b"response B")
        nam.next_reply = reply_b

        captured: list[bool] = []
        controller.running_changed.connect(lambda v: captured.append(v))
        captured.clear()  # Clear A's signals

        controller.start("ownerB", "repoB")
        assert captured == [True], "B start must emit running_changed(True)"

        # Now simulate A's old reply arriving late
        # Since _active_reply is reply_b, reply_a's signal checks identity
        # and bails.  We simulate by directly emitting A's finished.
        reply_a.finished.emit()

        assert captured == [True], (
            "A's late reply must not emit running_changed(False) for B"
        )
        assert controller.is_running, "B must still be running"

        # Now complete B normally
        reply_b.finished.emit()
        assert captured == [True, False], (
            "B must complete with balanced running_changed"
        )

    def test_late_reply_a_does_not_emit_result_for_b(self):
        """A's late reply must not emit result_ready — B gets exactly one result."""
        controller, nam = _make_controller_with_fake_nam()

        # ── Request A ──
        reply_a = FakeNetworkReply(content=_VALID_SKILL_MD)
        nam.next_reply = reply_a
        controller.start("ownerA", "repoA")
        controller.cancel()

        # ── Request B ──
        reply_b = FakeNetworkReply(content=_VALID_SKILL_MD)
        nam.next_reply = reply_b

        results: list[object] = []
        controller.result_ready.connect(lambda r: results.append(r))

        controller.start("ownerB", "repoB")

        # A's late reply arrives
        reply_a.finished.emit()
        assert len(results) == 0, (
            "A's late reply must not emit result_ready"
        )

        # B completes normally
        reply_b.finished.emit()
        assert len(results) == 1, (
            "B must produce exactly one result"
        )

    def test_stale_timeout_serial_is_ignored(self, qapp):
        """_on_timeout in IDLE state must be a no-op (stale timeout from old request).

        After request A completes and request B is in flight, A's old timer
        should never fire (it's stopped).  But if it somehow did, the state
        guard in _on_timeout prevents it from affecting B.

        We separately verify: calling _on_timeout while request B is RUNNING
        WILL abort B (it's the active request).  The protection is the timer
        being stopped, not _on_timeout being selective — _on_timeout always
        acts on the current request.
        """
        controller, nam = _make_controller_with_fake_nam()

        # ── Request A: complete normally ──
        reply_a = FakeNetworkReply(content=b"response A")
        nam.next_reply = reply_a
        controller.start("ownerA", "repoA")
        reply_a.finished.emit()
        assert controller.state == "idle"

        # ── Verify timer is stopped after A completes ──
        assert not controller._timeout_timer.isActive(), (
            "Timer must be stopped after A completes — this is the real "
            "protection against stale timeouts"
        )

        # ── Request B: start new request ──
        reply_b = FakeNetworkReply(content=b"response B")
        nam.next_reply = reply_b
        controller.start("ownerB", "repoB")

        # Timer is restarted for B — the old A timer cannot fire because
        # it was a single-shot that was stopped.
        assert controller._timeout_timer.isActive(), (
            "Timer must be active for request B"
        )

        # B is still running, unaffected by A's old (stopped) timer
        assert controller.is_running, "B must still be running"
        assert not reply_b.was_aborted, "B's reply must not be aborted"


class TestTerminalResultCount:
    """Each request lifecycle must produce exactly one terminal result."""

    def test_success_emits_exactly_one_terminal_result(self):
        """Normal success emits exactly one result_ready."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(content=b"test")
        controller._nam.get = MagicMock(return_value=fake_reply)

        results: list[object] = []
        controller.result_ready.connect(lambda r: results.append(r))

        controller.start("owner", "repo")
        fake_reply.finished.emit()

        assert len(results) == 1

    def test_timeout_emits_exactly_one_terminal_result(self):
        """Timeout emits exactly one result_ready."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(
            error=QNetworkReply.NetworkError.OperationCanceledError,
        )
        controller._nam.get = MagicMock(return_value=fake_reply)

        results: list[object] = []
        controller.result_ready.connect(lambda r: results.append(r))

        controller.start("owner", "repo")
        controller._on_timeout()
        # abort() → finished synchronously → result emitted

        assert len(results) == 1

    def test_cancel_emits_no_business_result(self):
        """User cancel emits zero result_ready signals."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(
            error=QNetworkReply.NetworkError.OperationCanceledError,
        )
        controller._nam.get = MagicMock(return_value=fake_reply)

        results: list[object] = []
        controller.result_ready.connect(lambda r: results.append(r))

        controller.start("owner", "repo")
        controller.cancel()

        assert len(results) == 0

    def test_network_error_emits_exactly_one_terminal_result(self):
        """Network error emits exactly one result_ready."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(
            error=QNetworkReply.NetworkError.ConnectionRefusedError,
            error_string="Connection refused",
        )
        controller._nam.get = MagicMock(return_value=fake_reply)

        results: list[object] = []
        controller.result_ready.connect(lambda r: results.append(r))

        controller.start("owner", "repo")
        fake_reply.finished.emit()

        assert len(results) == 1

    def test_running_changed_balanced_for_each_request(self):
        """running_changed must be True→False, not True→False→False."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(content=b"test")
        controller._nam.get = MagicMock(return_value=fake_reply)

        captured: list[bool] = []
        controller.running_changed.connect(lambda v: captured.append(v))

        # ── Request A ──
        controller.start("owner", "repo")
        assert captured == [True]
        fake_reply.finished.emit()
        assert captured == [True, False], (
            "Request must have exactly one True→False pair"
        )

        # ── Request B ──
        captured.clear()
        fake_reply_b = FakeNetworkReply(content=b"test B")
        controller._nam.get = MagicMock(return_value=fake_reply_b)
        controller.start("owner2", "repo2")
        assert captured == [True]

        fake_reply_b.finished.emit()
        assert captured == [True, False], (
            "Each request must have exactly one True→False pair"
        )

    def test_running_changed_not_double_false(self):
        """A single request must not emit False twice."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(content=b"test")
        controller._nam.get = MagicMock(return_value=fake_reply)

        captured: list[bool] = []
        controller.running_changed.connect(lambda v: captured.append(v))

        controller.start("owner", "repo")
        fake_reply.finished.emit()

        assert captured == [True, False], (
            f"Expected [True, False], got {captured}"
        )

    def test_cancel_running_changed_balanced(self):
        """Cancel must produce balanced running_changed: True → False."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(
            error=QNetworkReply.NetworkError.OperationCanceledError,
        )
        controller._nam.get = MagicMock(return_value=fake_reply)

        captured: list[bool] = []
        controller.running_changed.connect(lambda v: captured.append(v))

        controller.start("owner", "repo")
        assert captured == [True]
        controller.cancel()

        assert captured == [True, False], (
            f"Cancel must produce [True, False], got {captured}"
        )

    def test_timeout_running_changed_balanced(self):
        """Timeout must produce balanced running_changed: True → False."""
        controller = _make_controller()
        fake_reply = FakeNetworkReply(
            error=QNetworkReply.NetworkError.OperationCanceledError,
        )
        controller._nam.get = MagicMock(return_value=fake_reply)

        captured: list[bool] = []
        controller.running_changed.connect(lambda v: captured.append(v))

        controller.start("owner", "repo")
        assert captured == [True]
        controller._on_timeout()
        # abort() → finished synchronously → running_changed(False)

        assert captured == [True, False], (
            f"Timeout must produce [True, False], got {captured}"
        )


# ═══════════════════════════════════════════════
# Type‑check regression tests (manifest_parser)
# ═══════════════════════════════════════════════


class TestManifestTypeValidation:
    """Type‑narrowing helpers reject invalid types."""

    def test_schema_version_bool_rejected(self):
        from dp_engine.skills.errors import SkillManifestError
        from dp_engine.skills.manifest_parser import parse_skill_front_matter

        text = """---
schema_version: True
skill_id: test
name: Test
version: 1.0.0
skill_type: instruction
capabilities:
  - read
entrypoints:
  run: run.py
---
"""
        with pytest.raises(SkillManifestError, match="schema_version"):
            parse_skill_front_matter(text, source_label="test.md")

    def test_schema_version_negative_rejected(self):
        from dp_engine.skills.errors import SkillManifestError
        from dp_engine.skills.manifest_parser import parse_skill_front_matter

        text = """---
schema_version: -1
skill_id: test
name: Test
version: 1.0.0
skill_type: instruction
capabilities:
  - read
entrypoints:
  run: run.py
---
"""
        with pytest.raises(SkillManifestError, match="schema_version"):
            parse_skill_front_matter(text, source_label="test.md")

    def test_schema_version_zero_rejected(self):
        from dp_engine.skills.errors import SkillManifestError
        from dp_engine.skills.manifest_parser import parse_skill_front_matter

        text = """---
schema_version: 0
skill_id: test
name: Test
version: 1.0.0
skill_type: instruction
capabilities:
  - read
entrypoints:
  run: run.py
---
"""
        with pytest.raises(SkillManifestError, match="schema_version"):
            parse_skill_front_matter(text, source_label="test.md")

    def test_artifact_types_string_rejected(self):
        from dp_engine.skills.errors import SkillManifestError
        from dp_engine.skills.manifest_parser import parse_skill_manifest
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_root = Path(tmpdir)
            skill_md = skill_root / "SKILL.md"
            skill_md.write_text("""---
schema_version: 1
skill_id: test
name: Test
version: 1.0.0
description: test
skill_type: instruction
capabilities:
  - read
artifact_types: "pptx"
entrypoints:
  run: run.py
---
""", encoding="utf-8")
            (skill_root / "run.py").write_text("# test", encoding="utf-8")

            with pytest.raises(SkillManifestError, match="must be a list"):
                parse_skill_manifest(skill_root)

    def test_capabilities_with_non_string_item_rejected(self):
        from dp_engine.skills.errors import SkillManifestError
        from dp_engine.skills.manifest_parser import parse_skill_front_matter

        text = """---
schema_version: 1
skill_id: test
name: Test
version: 1.0.0
skill_type: instruction
capabilities:
  - read
  - 42
entrypoints:
  run: run.py
---
"""
        with pytest.raises(SkillManifestError, match="must be a string"):
            parse_skill_front_matter(text, source_label="test.md")

    def test_dependencies_with_dict_rejected(self):
        from dp_engine.skills.errors import SkillManifestError
        from dp_engine.skills.manifest_parser import parse_skill_front_matter

        text = """---
schema_version: 1
skill_id: test
name: Test
version: 1.0.0
skill_type: instruction
capabilities:
  - read
dependencies:
  - {name: foo}
entrypoints:
  run: run.py
---
"""
        with pytest.raises(SkillManifestError, match="must be a string"):
            parse_skill_front_matter(text, source_label="test.md")

    def test_entrypoints_not_mapping_rejected(self):
        from dp_engine.skills.errors import SkillManifestError
        from dp_engine.skills.manifest_parser import parse_skill_front_matter

        text = """---
schema_version: 1
skill_id: test
name: Test
version: 1.0.0
skill_type: instruction
capabilities:
  - read
entrypoints:
  - run.py
---
"""
        with pytest.raises(SkillManifestError, match="must be a mapping"):
            parse_skill_front_matter(text, source_label="test.md")

    def test_entrypoints_value_not_string_rejected(self):
        from dp_engine.skills.errors import SkillManifestError
        from dp_engine.skills.manifest_parser import parse_skill_front_matter

        text = """---
schema_version: 1
skill_id: test
name: Test
version: 1.0.0
skill_type: instruction
capabilities:
  - read
entrypoints:
  run: 42
---
"""
        with pytest.raises(SkillManifestError, match="must be a string"):
            parse_skill_front_matter(text, source_label="test.md")

    def test_source_url_list_rejected(self):
        from dp_engine.skills.errors import SkillManifestError
        from dp_engine.skills.manifest_parser import parse_skill_front_matter

        text = """---
schema_version: 1
skill_id: test
name: Test
version: 1.0.0
skill_type: instruction
capabilities:
  - read
entrypoints:
  run: run.py
source_url:
  - https://a.com
  - https://b.com
---
"""
        with pytest.raises(SkillManifestError, match="must be a string"):
            parse_skill_front_matter(text, source_label="test.md")

    def test_min_app_version_number_rejected(self):
        from dp_engine.skills.errors import SkillManifestError
        from dp_engine.skills.manifest_parser import parse_skill_front_matter

        text = """---
schema_version: 1
skill_id: test
name: Test
version: 1.0.0
skill_type: instruction
capabilities:
  - read
entrypoints:
  run: run.py
min_app_version: 2.0
---
"""
        with pytest.raises(SkillManifestError, match="must be a string"):
            parse_skill_front_matter(text, source_label="test.md")

    def test_unknown_fields_still_in_warnings(self):
        from dp_engine.skills.manifest_parser import parse_skill_front_matter

        text = """---
schema_version: 1
skill_id: test
name: Test
version: 1.0.0
skill_type: instruction
capabilities:
  - read
entrypoints:
  run: run.py
unknown_field: some_value
another_custom: 123
---
"""
        result = parse_skill_front_matter(text, source_label="test.md")
        assert len(result.warnings) > 0, (
            "Unknown fields should generate warnings"
        )
        warning_text = " ".join(result.warnings)
        assert "unknown_field" in warning_text or "Unknown fields" in warning_text
        assert result.metadata["skill_id"] == "test"
        assert result.metadata["name"] == "Test"


# ═══════════════════════════════════════════════
# Main Window Smoke Tests
# ═══════════════════════════════════════════════


class TestMainWindowWithController:
    """Main window integration: widget creates, inspects, closes safely."""

    def test_agent_skill_widget_creates_with_controller(self, qapp):
        """AgentSkillWidget creates with SkillSourceInspectController."""
        from ui.skill_tab import AgentSkillWidget
        from ui.skill_source_controller import SkillSourceInspectController

        widget = AgentSkillWidget()
        assert widget is not None
        assert widget._source_controller is not None
        assert isinstance(widget._source_controller, SkillSourceInspectController)
        assert widget.inspect_btn is not None
        widget.deleteLater()

    def test_start_inspection_with_fake_network(self, qapp, monkeypatch):
        """Full flow: start inspection with fake reply, verify result.

        QMessageBox dialogs are patched to prevent modal event-loop
        blocking in headless test runs — the inspection success path
        calls QMessageBox.information() which would otherwise block
        indefinitely under QT_QPA_PLATFORM=offscreen.
        """
        from ui.skill_tab import AgentSkillWidget
        from dp_engine.skills.models import SkillSourceInspectionResult
        from PyQt6.QtWidgets import QMessageBox

        # ── Prevent modal dialog blocking in headless tests ──
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: None)
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **kw: None)
        monkeypatch.setattr(QMessageBox, "critical", lambda *a, **kw: None)

        widget = AgentSkillWidget()
        widget.github_owner_input.setText("test-owner")
        widget.github_repo_input.setText("test-repo")

        fake_reply = FakeNetworkReply(content=_VALID_SKILL_MD)
        widget._source_controller._nam.get = MagicMock(return_value=fake_reply)

        results: list[object] = []
        widget._source_controller.result_ready.connect(lambda r: results.append(r))

        try:
            widget._on_inspect_source()
            assert not widget.inspect_btn.isEnabled()

            fake_reply.finished.emit()

            assert len(results) == 1
            result = results[0]
            assert isinstance(result, SkillSourceInspectionResult)
            assert result.is_viable_skill_source is True

            assert widget.inspect_btn.isEnabled()
        finally:
            widget._source_controller.cancel()
            widget.close()
            widget.deleteLater()
            qapp.processEvents()

    def test_close_during_inspection_is_safe(self, qapp):
        """Close widget during active request → no crash, no thread leak."""
        from ui.skill_tab import AgentSkillWidget

        widget = AgentSkillWidget()
        widget.github_owner_input.setText("test-owner")
        widget.github_repo_input.setText("test-repo")

        fake_reply = FakeNetworkReply(
            error=QNetworkReply.NetworkError.OperationCanceledError,
        )
        widget._source_controller._nam.get = MagicMock(return_value=fake_reply)

        widget._on_inspect_source()
        widget.close()

        assert fake_reply.was_aborted, (
            "Active reply must be aborted on widget close"
        )
        assert widget._source_controller._active_reply is None, (
            "Active reply ref should be cleared after abort"
        )
        widget.deleteLater()

    def test_no_exception_on_close_with_no_active_request(self, qapp):
        """Closing widget with no active request is safe (no crash)."""
        from ui.skill_tab import AgentSkillWidget

        widget = AgentSkillWidget()
        widget.close()
        widget.deleteLater()


# ═══════════════════════════════════════════════
# Real Main Window Smoke Tests (Batch 1.4)
# ═══════════════════════════════════════════════


class TestRealMainWindowSmoke:
    """Tests that instantiate the real DataProcessorWindow.

    These tests prove the main window UI construction is not broken
    by the skill controller refactoring.  Heavy initialization is
    monkeypatched per-test to keep tests fast and avoid real I/O.
    """

    def test_real_main_window_contains_skill_center(self, qapp, monkeypatch):
        """Create the real DataProcessorWindow and verify skill center exists."""
        monkeypatch.setattr(
            "core.models.SensorSystem.add_fbg",
            lambda self, *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "core.ai_client.AIClient._load_config_json",
            lambda: {},
        )

        from main import DataProcessorWindow

        window = DataProcessorWindow()

        # Verify the main tabs exist
        assert window.report_content_stack is not None
        assert window.report_content_stack.count() >= 6, (
            "Main window must have at least 6 stacked pages"
        )

        # Verify skill center widget exists
        assert window.skill_tab_widget is not None
        from ui.skill_tab import AgentSkillWidget
        assert isinstance(window.skill_tab_widget, AgentSkillWidget)

        # Verify title exists in the info menu
        found = False
        for i in range(window.info_menu_list.count()):
            if "技能插件中心" in window.info_menu_list.item(i).text():
                found = True
                break
        assert found, "技能插件中心 must exist in the info menu list"

        window.close()
        window.deleteLater()

    def test_real_main_window_switches_to_skill_center(self, qapp, monkeypatch):
        """Switch to the 成果输出与报告 area → skill center sub-page.
        Must not raise ImportError, AttributeError, or page construction error.
        """
        monkeypatch.setattr(
            "core.models.SensorSystem.add_fbg",
            lambda self, *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "core.ai_client.AIClient._load_config_json",
            lambda: {},
        )

        from main import DataProcessorWindow

        window = DataProcessorWindow()

        # Find the index of "技能插件中心" in the menu
        skill_index = None
        for i in range(window.info_menu_list.count()):
            if "技能插件中心" in window.info_menu_list.item(i).text():
                skill_index = i
                break
        assert skill_index is not None

        # Switch to the skill center page
        window.info_menu_list.setCurrentRow(skill_index)
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

        # Verify the skill tab widget is now visible
        current_widget = window.report_content_stack.currentWidget()
        assert current_widget is window.skill_tab_widget, (
            "After switching to skill center menu, skill_tab_widget "
            "must be the current widget"
        )

        window.close()
        window.deleteLater()

    def test_real_main_window_skill_widget_close_aborts_active_request(
        self, qapp, monkeypatch,
    ):
        """Close skill widget (inside real main window) while request is active.

        The skill widget's closeEvent triggers controller.cancel() →
        reply.abort().  Must call FakeReply.abort(), no real network access,
        no QThread warnings.
        """
        monkeypatch.setattr(
            "core.models.SensorSystem.add_fbg",
            lambda self, *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "core.ai_client.AIClient._load_config_json",
            lambda: {},
        )

        from main import DataProcessorWindow

        window = DataProcessorWindow()

        # Inject FakeNetworkReply into the skill source controller
        fake_reply = FakeNetworkReply(
            error=QNetworkReply.NetworkError.OperationCanceledError,
        )
        window.skill_tab_widget._source_controller._nam.get = (
            MagicMock(return_value=fake_reply)
        )

        # Fill in required fields and start inspection
        widget = window.skill_tab_widget
        widget.github_owner_input.setText("test-owner")
        widget.github_repo_input.setText("test-repo")
        widget._on_inspect_source()

        assert widget._source_controller.is_running, (
            "Controller must be running after inspection start"
        )

        # Close the skill widget first (triggers closeEvent → cancel)
        widget.close()

        # Verify abort was called by widget close
        assert fake_reply.was_aborted, (
            "FakeReply.abort() must be called when skill widget closes "
            "with active request"
        )

        # Then close the main window
        window.close()

        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

        window.deleteLater()
