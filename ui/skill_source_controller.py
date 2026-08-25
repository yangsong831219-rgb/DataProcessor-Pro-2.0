"""Qt async controller for GitHub skill source inspection.

Replaces the QThread + requests.get() approach with Qt-native
QNetworkAccessManager.  No background threads, no terminate(), no wait().

Architecture:
    AgentSkillWidget
    → SkillSourceInspectController.start(...)
    → QNetworkAccessManager.get()
    → QNetworkReply.finished
    → classify_skill_source_response()  (pure, in dp_engine/github_skill_source.py)
    → result_ready(SkillSourceInspectionResult)

Race-condition safety (batch 1.4):
    - Owned QTimer (NOT static QTimer.singleShot) — cancellable per-request.
    - Request serial / generation counter — stale callbacks from request A
      cannot modify state or emit signals for request B.
    - Reply identity check — every handler verifies `reply is self._active_reply`.
    - State machine: idle → running → (success|timeout|cancelling).
    - Exactly one terminal result per request (success, timeout, or error).
      Cancel emits no business result.

Usage:
    controller = SkillSourceInspectController(parent=widget)
    controller.result_ready.connect(on_result)
    controller.running_changed.connect(on_running_changed)
    controller.start(owner="...", repo="...", path="skills/", branch="main")

    # On widget close:
    controller.cancel()  # aborts active QNetworkReply, no threads left behind
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, cast

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

if TYPE_CHECKING:
    from dp_engine.skills.models import SkillSourceInspectionResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_MS: int = 15_000


class _ControllerState(Enum):
    """Internal state machine for the controller.

    Transitions:
        idle → running  (start)
        running → idle  (success / network error)
        running → timed_out (timeout → abort → idle)
        running → cancelling (cancel) → idle
    """

    IDLE = "idle"
    RUNNING = "running"
    CANCELLING = "cancelling"
    TIMED_OUT = "timed_out"


class SkillSourceInspectController(QObject):
    """Async skill source inspection controller — zero QThread.

    Manages a single active QNetworkReply at a time.  Supports:
    - Single-request guard (reject double-start, return False).
    - Finite timeout via OWNED QTimer (NOT static singleShot).
    - Request serial/generation protection against stale callbacks.
    - Reply identity checks in all handlers.
    - Cancel via reply.abort().
    - Widget-close-safe: cancel() leaves no background threads.
    - Dependency injection for testing (QNetworkAccessManager, timeout).

    Signals:
        result_ready(SkillSourceInspectionResult): Emitted ONCE per request
            on success, timeout, or network error.  NOT emitted on cancel.
        running_changed(bool): True when request starts, False when it ends.
            Balanced: exactly one True → False pair per request lifecycle.
    """

    result_ready = pyqtSignal(object)  # SkillSourceInspectionResult
    running_changed = pyqtSignal(bool)

    def __init__(
        self,
        parent: QObject | None = None,
        network_manager: QNetworkAccessManager | None = None,
        timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    ) -> None:
        """Initialize the controller.

        Args:
            parent: Qt parent object (lifetime management).
            network_manager: QNetworkAccessManager instance.  If None, a new
                             one is created with this controller as parent.
                             Inject a fake for testing.
            timeout_ms: Request timeout in milliseconds (default 15_000).
        """
        super().__init__(parent)
        self._nam = network_manager or QNetworkAccessManager(self)
        self._timeout_ms = timeout_ms

        # ── Owned QTimer — NOT static QTimer.singleShot() ──
        # Created once, started per request, stopped on all completion paths.
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)

        # ── State ──
        self._state: _ControllerState = _ControllerState.IDLE
        self._active_reply: QNetworkReply | None = None

        # ── Request serial / generation counter ──
        # Incremented on each start().  All async callbacks verify against
        # _active_serial to prevent stale callback A from affecting request B.
        self._request_serial: int = 0
        self._active_serial: int | None = None

        # ── Reply → serial mapping for identity verification ──
        # Since Qt signal slots don't carry serial context, we track which
        # serial each reply belongs to.
        self._reply_serials: dict[int, int] = {}  # id(reply) → serial

    # ── Public API ──

    @property
    def is_running(self) -> bool:
        """True when a request is active (running, cancelling, or timed_out)."""
        return self._state != _ControllerState.IDLE

    @property
    def state(self) -> str:
        """Current controller state (string, for test inspection)."""
        return self._state.value

    def start(
        self,
        owner: str,
        repo: str,
        path: str = "skills/",
        branch: str = "main",
        token: str | None = None,
    ) -> bool:
        """Start a skill source inspection request.

        Only one request is allowed at a time.

        Args:
            owner: GitHub repository owner.
            repo: GitHub repository name.
            path: Path within the repo to the skill directory.
            branch: Git branch name.
            token: Optional GitHub personal access token.

        Returns:
            True if the request was started, False if a request is already
            active (caller should not retry until running_changed(False)).
        """
        if self.is_running:
            logger.debug(
                "SkillSourceInspectController: request already active "
                "(state=%s), ignoring start()",
                self._state.value,
            )
            return False

        # ── Build URL (pure function, no I/O) ──
        from dp_engine.github_skill_source import build_skill_md_url

        url = build_skill_md_url(owner, repo, path, branch)

        # ── Prepare request ──
        req = QNetworkRequest(QUrl(url))
        req.setTransferTimeout(self._timeout_ms)
        if token:
            # Token only goes in the request header, never logged
            req.setRawHeader(
                b"Authorization",
                f"token {token}".encode("utf-8"),
            )

        # ── Advance serial ──
        self._request_serial += 1
        serial = self._request_serial
        self._active_serial = serial

        # ── Transition to running ──
        self._state = _ControllerState.RUNNING

        # ── Start owned timeout timer ──
        self._timeout_timer.start(self._timeout_ms)

        # ── Emit running state (exactly once per request) ──
        self.running_changed.emit(True)

        # ── Fire request ──
        self._active_reply = self._nam.get(req)
        if self._active_reply is not None:
            self._reply_serials[id(self._active_reply)] = serial
            self._active_reply.finished.connect(self._on_reply_finished)

        return True

    def cancel(self) -> None:
        """Cancel the active request (if any).

        Calls reply.abort() and cleans up internal state.
        Safe to call multiple times or when idle.
        Does NOT emit result_ready — the caller is closing.

        State transition: running → cancelling → (reply abort) → idle.
        """
        if self._state == _ControllerState.IDLE:
            return

        # ── Stop timeout timer ──
        self._timeout_timer.stop()

        # ── Mark as cancelling ──
        prev_state = self._state
        self._state = _ControllerState.CANCELLING

        if self._active_reply is not None:
            # Save reply ref before abort — _on_reply_finished fires
            # synchronously and must see CANCELLING state to suppress
            # result emission.
            self._active_reply.abort()
            # _on_reply_finished returned early (due to CANCELLING guard).
            # If we transitioned from RUNNING (not already TIMED_OUT),
            # emit running_changed here since it wasn't emitted there.
            if prev_state == _ControllerState.RUNNING:
                self.running_changed.emit(False)
            # Clean up reply tracking
            self._active_reply = None
            self._active_serial = None

    # ── Internal slots ──

    def _on_timeout(self) -> None:
        """Timeout handler: mark timed_out and abort the CURRENT reply only.

        Guarded by _active_serial — if the timer fires for a stale request,
        it is silently ignored (the timer is stopped in all normal completion
        paths, so this should only fire for the active request).
        """
        # ── Guard: only handle timeout for the active request ──
        if self._state != _ControllerState.RUNNING:
            # Timer fired after we already transitioned (cancel, finish, etc.).
            # This is a defensive no-op — the timer should have been stopped.
            logger.debug(
                "_on_timeout called in state %s — ignoring stale timeout",
                self._state.value,
            )
            return

        # ── Transition to timed_out ──
        self._state = _ControllerState.TIMED_OUT

        # ── Abort only the current reply ──
        if self._active_reply is not None:
            self._active_reply.abort()
            # _on_reply_finished will fire when abort completes.
            # It sees TIMED_OUT state and produces a timeout result.

    def _on_reply_finished(self) -> None:
        """Handle QNetworkReply.finished — the ONLY completion path.

        All three paths converge here:
        1. Normal completion (success or HTTP error)
        2. Timeout → abort() → finished
        3. User cancel → abort() → finished

        Guarded by reply identity check and request serial:
        - Stale reply A cannot modify state for request B.
        - Stale reply A cannot emit signals for request B.
        """
        obj = self.sender()
        if obj is None:
            return

        # sender() returns QObject; cast to QNetworkReply.
        # Test fakes may not be true QNetworkReply subclasses — the
        # identity check (reply is self._active_reply) handles both.
        reply = cast(QNetworkReply, obj)

        # ── Reply identity check ──
        # If this reply is not the active one, it's a stale late arrival.
        # Delete it and bail out — do NOT touch current request state.
        if reply is not self._active_reply:
            logger.debug(
                "_on_reply_finished: stale reply (not _active_reply), "
                "deleting and ignoring"
            )
            reply.deleteLater()
            return

        # ── Serial verification ──
        reply_id = id(reply)
        reply_serial = self._reply_serials.pop(reply_id, None)
        if reply_serial is not None and reply_serial != self._active_serial:
            logger.debug(
                "_on_reply_finished: reply serial %s != active serial %s, "
                "stale callback ignored",
                reply_serial,
                self._active_serial,
            )
            reply.deleteLater()
            return

        # ── Clear active reply BEFORE processing ──
        # This prevents re-entry and ensures identity check works for
        # any synchronous signal cascades.
        self._active_reply = None

        # ── Stop timeout timer ──
        self._timeout_timer.stop()

        # ── Remember state before clearing ──
        current_state = self._state

        # ── Guard: user-initiated cancel ──
        if current_state == _ControllerState.CANCELLING:
            reply.deleteLater()
            self._state = _ControllerState.IDLE
            self._active_serial = None
            # running_changed(False) was already emitted in cancel()
            return

        # ── Read response data ──
        timed_out = current_state == _ControllerState.TIMED_OUT
        error = reply.error()
        url_str = reply.url().toString()

        status_code_val = reply.attribute(
            QNetworkRequest.Attribute.HttpStatusCodeAttribute
        )
        status_code: int | None = (
            int(status_code_val) if status_code_val is not None else None
        )
        raw_bytes: bytes = reply.readAll().data()
        content = raw_bytes.decode("utf-8", errors="replace")

        # ── Handle cancellation (OperationCanceledError) ──
        if error == QNetworkReply.NetworkError.OperationCanceledError:
            reply.deleteLater()

            if timed_out:
                # Timeout-triggered abort → report as timeout result
                from dp_engine.github_skill_source import (
                    classify_skill_source_response,
                )

                result = classify_skill_source_response(
                    source_url=url_str,
                    status_code=None,
                    content="",
                    timed_out=True,
                )
                self._state = _ControllerState.IDLE
                self._active_serial = None
                self.running_changed.emit(False)
                self.result_ready.emit(result)
            else:
                # This path should not be reachable — if we got here with
                # OperationCanceledError but not timed_out and not cancelling,
                # something unexpected happened.  Treat as generic cancel.
                logger.warning(
                    "_on_reply_finished: OperationCanceledError in state %s "
                    "(expected TIMED_OUT or CANCELLING)",
                    current_state.value,
                )
                self._state = _ControllerState.IDLE
                self._active_serial = None
                self.running_changed.emit(False)
            return

        # ── Normal completion (success or HTTP error) ──
        network_error_str: str | None = (
            reply.errorString()
            if error != QNetworkReply.NetworkError.NoError
            else None
        )

        reply.deleteLater()

        # ── Classify via pure function (no further I/O) ──
        from dp_engine.github_skill_source import classify_skill_source_response

        result = classify_skill_source_response(
            source_url=url_str,
            status_code=status_code,
            content=content,
            network_error=network_error_str,
            timed_out=timed_out,
        )

        self._state = _ControllerState.IDLE
        self._active_serial = None
        self.running_changed.emit(False)
        self.result_ready.emit(result)
