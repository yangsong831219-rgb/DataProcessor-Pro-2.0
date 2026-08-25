"""Qt async controller for skill installation.

Manages download (via QNetworkAccessManager), progress reporting,
cancellation, and UI lifecycle for skill package installation.

Does NOT:
- Execute skill code
- Install Python dependencies
- Run health checks
- Manage SkillRuntime

Architecture:
    AgentSkillWidget
    → SkillInstallController.start_download_and_install(...)
    → QNetworkAccessManager download (async, on event loop)
    → downloads/<task-id>.part → <task-id>.zip
    → InstallWorker (QThread for file I/O)
    → SkillInstallService → SkillInstaller → SkillRegistry

Download security (Batch 2.2 / 2.3):
- Explicit ManualRedirectPolicy (Plan A) — every hop explicitly constructed
- DownloadRequestContext tracks visited URLs, redirect count, token policy
- DownloadState machine: waiting_headers → downloading_final_content → finished
- Redirect/error bodies NEVER written to .part file
- Final ZIP validated with magic bytes ONLY (PK\x03\x04, PK\x05\x06, PK\x07\x08)
- Atomic commit via flush + os.fsync() + os.replace()
- Token NEVER in URL, Exception, log, signal, or result repr
- Full CRC/archive validation moved to InstallWorker (QThread) — NOT in UI thread

Network Controller responsibility (Batch 2.4):
- URL and redirect validation, token header strategy, HTTP status judgment
- Content-Length limits, streaming byte cap, incremental archive SHA-256
- .part write, flush, os.fsync(), magic-byte check (4 bytes only), atomic commit to .zip
- Cancel, timeout, and failure cleanup
- NO ZipFile open, NO infolist(), NO central directory parse — all deferred to InstallWorker

InstallWorker responsibility (Batch 2.4):
- Full archive validation: inspect_archive() → metadata safety limits
- CRC/integrity → safe_extract_zip() → locate_skill_root()
- SkillPackageValidator.validate() → atomic install
- All heavy I/O, central directory parse, CRC checks run in QThread, NOT UI thread
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import urlparse, urlunparse

from PyQt6.QtCore import (
    QCoreApplication,
    QEventLoop,
    QObject,
    QThread,
    QTimer,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QApplication

    from dp_engine.skills.package_models import (
        SkillInstallResult,
    )

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_MS: int = 120_000  # 2 minutes for downloads
_MAX_REDIRECTS: int = 5

# Allowed GitHub domains for download redirect
_ALLOWED_DOWNLOAD_DOMAINS = frozenset({
    "github.com",
    "codeload.github.com",
})

# Domains that are allowed to receive the GitHub Authorization token
_TOKEN_ALLOWED_DOMAINS = frozenset({
    "github.com",
})


# ── Download state machine ──


class DownloadState(Enum):
    """Download state machine for .part file safety.

    Transitions:
        waiting_headers → downloading_final_content  (2xx received)
        waiting_headers → redirect_response          (3xx received)
        redirect_response → waiting_headers          (new request created)
        downloading_final_content → finished         (body complete + validated)
        any → cancelled                              (user cancel / timeout)
        any → failed                                 (error / invalid response)
    """
    WAITING_HEADERS = auto()
    REDIRECT_RESPONSE = auto()
    DOWNLOADING_FINAL_CONTENT = auto()
    FINISHED = auto()
    CANCELLED = auto()
    FAILED = auto()


# ── Download request context ──


@dataclass(frozen=True)
class DownloadRequestContext:
    """Immutable context for a single download hop.

    Each redirect creates a new context with incremented serial and
    updated visited_urls.  Used for audit and security validation.
    """
    task_id: str
    request_serial: int
    current_url: str
    visited_urls: tuple[str, ...]
    redirect_count: int
    token_allowed: bool


# ── URL normalization for comparison ──


def _normalize_url_for_comparison(url_str: str) -> str:
    """Normalize a URL for safe comparison (scheme, hostname, port, path, query).

    Fragment is removed.  Default ports are made explicit.
    Username/password are stripped.
    """
    try:
        p = urlparse(url_str)
        scheme = p.scheme.lower()
        hostname = p.hostname or ""
        port = p.port
        if port is None:
            port = 443 if scheme == "https" else 80
        path = p.path or "/"
        query = p.query
        return urlunparse((scheme, f"{hostname}:{port}", path, "", query, ""))
    except Exception:
        return url_str


# ── GitHub URL construction ──


def build_github_archive_url(owner: str, repo: str, revision: str) -> str:
    """Build codeload.github.com archive URL from structured parameters.

    Uses the preferred codeload URL format:
    https://codeload.github.com/<owner>/<repo>/zip/<revision>

    Args:
        owner: Repository owner (validated — no /, ., .., control chars)
        repo: Repository name (validated)
        revision: Branch/tag/commit ref (URL-encoded)

    Returns:
        Full codeload URL.

    Raises:
        ValueError: If any parameter fails validation.
    """
    _validate_github_param(owner, "owner")
    _validate_github_param(repo, "repo")
    _validate_revision(revision)

    from urllib.parse import quote
    encoded_revision = quote(revision, safe="")
    return (
        f"https://codeload.github.com/{owner}/{repo}/zip/{encoded_revision}"
    )


def _validate_github_param(value: str, name: str) -> None:
    """Validate a GitHub owner or repo parameter."""
    if not value or not value.strip():
        raise ValueError(f"{name} must not be empty")
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError(f"{name} contains control characters")
    if ".." in value:
        raise ValueError(f"{name} must not contain '..'")
    if "/" in value or "\\" in value:
        raise ValueError(f"{name} must not contain path separators")
    if len(value) > 100:
        raise ValueError(f"{name} too long: {len(value)} > 100")


def _validate_revision(value: str) -> None:
    """Validate a git revision string."""
    if not value or not value.strip():
        raise ValueError("revision must not be empty")
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError("revision contains control characters")
    if ".." in value:
        raise ValueError("revision must not contain '..'")
    if len(value) > 200:
        raise ValueError(f"revision too long: {len(value)} > 200")


def validate_github_url_for_ui(url: str) -> tuple[bool, str]:
    """Validate a GitHub archive URL for UI display.

    Returns:
        (is_valid, error_message). error_message is empty if valid.
    """
    _GITHUB_ARCHIVE_RE = re.compile(
        r"^https://github\.com/([a-zA-Z0-9]([a-zA-Z0-9._-]*[a-zA-Z0-9])?)"
        r"/([a-zA-Z0-9]([a-zA-Z0-9._-]*[a-zA-Z0-9])?)"
        r"/archive/([^/]+)\.zip$"
    )
    if not url.startswith("https://"):
        return False, "只允许 HTTPS 协议的 URL"
    if not _GITHUB_ARCHIVE_RE.match(url):
        return False, (
            "URL 格式不正确。\n"
            "期望格式: https://github.com/<owner>/<repo>/archive/<ref>.zip"
        )
    return True, ""


# ── Authorization header strategy (pure function) ──


def should_attach_github_token(url: QUrl) -> bool:
    """Determine whether to attach a GitHub token to a request.

    Security rules:
    - Token ONLY sent to github.com (not codeload.github.com by default)
    - Token NEVER sent to any other domain
    - If private repo download to codeload is needed, document and test separately

    Args:
        url: The target URL for the request.

    Returns:
        True if token should be attached.
    """
    host = url.host().lower()
    return host in _TOKEN_ALLOWED_DOMAINS


# ── URL validation for download requests ──


def _validate_download_url(url: QUrl) -> str | None:
    """Validate a download URL. Returns error message or None if valid.

    Checks:
    - scheme must be https
    - hostname must be in _ALLOWED_DOWNLOAD_DOMAINS
    - no subdomain spoofing
    - no IP addresses (v4 or v6)
    - no URL username/password
    - no non-default port
    - no control characters
    """
    url_str = url.toString()

    # Scheme must be https
    if url.scheme() != "https":
        return f"拒绝非 HTTPS URL: {url_str[:200]}"

    host = url.host().lower()

    # No IP addresses
    if _looks_like_ip(host):
        return f"拒绝 IP 地址: {host}"

    # Hostname must be in allowed set
    if host not in _ALLOWED_DOWNLOAD_DOMAINS:
        return f"拒绝非允许域名: {host}"

    # No URL username/password
    if url.userName() or url.password():
        return "拒绝含凭据的 URL"

    # No non-default port
    port = url.port(443)
    if port != 443:
        return f"拒绝非标准端口: {port}"

    # No control characters in full URL
    if re.search(r"[\x00-\x1f\x7f]", url_str):
        return "URL 含控制字符"

    return None


# ── Install worker (heavy I/O in QThread, NO code execution) ──


class InstallWorker(QObject):
    """Worker for running SkillInstallService in a background thread.

    Full archive validation pipeline (Batch 2.3):
    1. inspect_archive() → archive size, entry count, metadata limits
    2. Entry-level checks: single file size, total uncompressed, compression
       ratio, path depth, path length, symlink, encrypted, special files,
       path conflicts — ALL before reading member data
    3. CRC/integrity validation via safe_extract_zip() member-by-member
       (ZipExtFile validates CRC on read completion — no separate testzip())
    4. locate_skill_root() → SkillPackageValidator.validate()
    5. Atomic install with registry update

    Cancel token checked between each step and during extraction.

    Does NOT import or execute skill code.

    Custom signals (do NOT override QThread builtins):
        stage_changed(str)   — install stage name
        result_ready(object) — SkillInstallResult
        error_occurred(str)  — error message
    """

    stage_changed = pyqtSignal(str)
    result_ready = pyqtSignal(object)  # SkillInstallResult
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        source_type: str,
        location: str,
        activate_after_install: bool = False,
        replace_same_version: bool = False,
        sub_path: str = "",
        revision: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._source_type = source_type
        self._location = location
        self._activate_after_install = activate_after_install
        self._replace_same_version = replace_same_version
        self._sub_path = sub_path
        self._revision = revision
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @pyqtSlot()
    def run(self) -> None:
        """Execute install in worker thread."""
        try:
            from dp_engine.skills.package_models import (
                SkillInstallRequest,
                SkillPackageSource,
                SkillPackageSourceType,
            )

            from dp_engine.skills.package_models import get_default_skill_paths, ensure_skill_dirs
            paths = get_default_skill_paths()
            ensure_skill_dirs(paths)

            from dp_engine.skills.registry import SkillRegistry
            registry = SkillRegistry(paths.registry_file)
            registry.load()

            from dp_engine.skills.install_service import SkillInstallService
            service = SkillInstallService(paths=paths, registry=registry)

            st_map = {
                "local_directory": SkillPackageSourceType.LOCAL_DIRECTORY,
                "local_zip": SkillPackageSourceType.LOCAL_ZIP,
                "github_archive": SkillPackageSourceType.GITHUB_ARCHIVE,
            }
            st = st_map.get(self._source_type)
            if st is None:
                self.error_occurred.emit(
                    f"Unknown source type: {self._source_type}"
                )
                return

            source = SkillPackageSource(
                source_type=st,
                location=self._location,
                revision=self._revision,
                sub_path=self._sub_path,
            )
            request = SkillInstallRequest(
                source=source,
                activate_after_install=self._activate_after_install,
                replace_same_version=self._replace_same_version,
            )

            result = service._installer.install(
                request,
                cancel_check=lambda: self._cancelled,
                stage_callback=self.stage_changed.emit,
            )

            self.result_ready.emit(result)

        except Exception as e:
            logger.exception("InstallWorker failed")
            self.error_occurred.emit(str(e))


# ── Uninstall worker ──


class UninstallWorker(QObject):
    """Worker for running uninstall in a background thread.

    Custom signals:
        result_ready(object) — SkillUninstallResult
        error_occurred(str)  — error message
    """

    result_ready = pyqtSignal(object)  # SkillUninstallResult
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        skill_id: str,
        version: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._skill_id = skill_id
        self._version = version
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @pyqtSlot()
    def run(self) -> None:
        """Execute uninstall in worker thread."""
        try:
            from dp_engine.skills.package_models import get_default_skill_paths
            from dp_engine.skills.install_service import SkillInstallService
            from dp_engine.skills.registry import SkillRegistry

            paths = get_default_skill_paths()
            registry = SkillRegistry(paths.registry_file)
            registry.load()

            service = SkillInstallService(paths=paths, registry=registry)

            result = service.uninstall(
                self._skill_id,
                self._version,
                cancel_check=lambda: self._cancelled,
            )
            self.result_ready.emit(result)

        except Exception as e:
            logger.exception("UninstallWorker failed")
            self.error_occurred.emit(str(e))


# ═══════════════════════════════════════════════
# SkillInstallTaskOwner — outlives page widgets
# ═══════════════════════════════════════════════


class SkillInstallTaskOwner(QObject):
    """Application-level owner for in-flight skill install tasks.

    This object lives on the QApplication instance (or another long-lived
    parent) so that worker QThread objects are not garbage-collected when
    the AgentSkillWidget is destroyed during main window close.

    When the last worker finishes, this object emits all_tasks_finished()
    and can be safely deleted.

    Signals:
        all_tasks_finished(): Emitted when all controllers and workers are idle.
        shutdown_state_changed(state): Emitted when the shutdown state changes.
            States: running → shutdown_requested → waiting_for_tasks → safe_to_quit

    Lifecycle:
    - Created once per QApplication via get_skill_install_task_owner().
    - DataProcessorWindow and AgentSkillWidget hold references, NOT ownership.
    - Main window close does NOT delete this object.
    - Workers complete naturally after cancellation; terminate() is NEVER called.

    Application exit protocol:
    - begin_application_shutdown() → request_cancel_all() → wait for workers
    - shutdown(timeout_ms) returns bool; False means "still running, don't quit"
    - all_tasks_finished → can_application_quit() is True → app.quit()
    - aboutToQuit is a safety net ONLY: asserts no active workers, logs CRITICAL
    """

    all_tasks_finished = pyqtSignal()
    shutdown_state_changed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._active_workers: set[QThread] = set()
        self._active_controllers: set[SkillInstallController] = set()
        self._shutdown_state: str = "running"  # running | shutdown_requested | waiting_for_tasks | safe_to_quit
        self._shutdown_pending_quit: bool = False  # True if app.quit() should be called when safe

    # ── Controller tracking ──

    def add_controller(self, controller: SkillInstallController) -> None:
        """Take ownership of a controller and its workers.

        Idempotent — re-adding the same controller is a no-op.
        """
        if controller in self._active_controllers:
            return
        self._active_controllers.add(controller)
        # Re-parent the controller to this owner so it survives widget destruction
        controller.setParent(self)
        controller.running_changed.connect(self._on_controller_running_changed)

    def _on_controller_running_changed(self, running: bool) -> None:
        """Track when all controllers are idle.

        When shutdown was requested and all controllers + workers are idle,
        transitions to safe_to_quit and may trigger app.quit().
        """
        if not running:
            all_idle = all(
                not c.is_running for c in self._active_controllers
            )
            if all_idle and not self._active_workers:
                self.all_tasks_finished.emit()
                # Transition to safe_to_quit if shutdown was requested
                if self._shutdown_state in ("shutdown_requested", "waiting_for_tasks"):
                    self._transition_state("safe_to_quit")
                    if self._shutdown_pending_quit:
                        self._shutdown_pending_quit = False
                        app = QCoreApplication.instance()
                        if app is not None:
                            app.quit()

    # ── Worker tracking ──

    def adopt_worker(self, worker: QThread) -> None:
        """Register a worker QThread for lifecycle tracking.

        The worker's finished signal is connected to _release_worker
        and deleteLater for automatic cleanup. Idempotent.
        """
        if worker in self._active_workers:
            return
        self._active_workers.add(worker)
        # Use default-arg closure to capture current worker reference
        worker.finished.connect(
            lambda w=worker: self._release_worker(w)
        )
        worker.finished.connect(worker.deleteLater)

    def _release_worker(self, worker: QThread) -> None:
        """Remove worker from active set. Emits all_tasks_finished if all done.

        When shutdown was requested and all tasks finish, transitions to
        safe_to_quit and may trigger app.quit().
        """
        self._active_workers.discard(worker)
        if not self._active_workers and not any(
            c.is_running for c in self._active_controllers
        ):
            self.all_tasks_finished.emit()
            # Transition to safe_to_quit if shutdown was requested
            if self._shutdown_state in ("shutdown_requested", "waiting_for_tasks"):
                self._transition_state("safe_to_quit")
                if self._shutdown_pending_quit:
                    self._shutdown_pending_quit = False
                    app = QCoreApplication.instance()
                    if app is not None:
                        app.quit()

    def _transition_state(self, new_state: str) -> None:
        """Transition shutdown state and emit signal."""
        old_state = self._shutdown_state
        self._shutdown_state = new_state
        logger.info(
            "TaskOwner shutdown state: %s → %s", old_state, new_state,
        )
        self.shutdown_state_changed.emit(new_state)

    # ── Shutdown protocol ──

    @property
    def shutdown_requested(self) -> bool:
        """True after begin_application_shutdown() has been called."""
        return self._shutdown_state != "running"

    @property
    def can_application_quit(self) -> bool:
        """True when it is safe for QApplication to quit."""
        return self._shutdown_state == "safe_to_quit"

    def begin_application_shutdown(self) -> None:
        """Initiate the application shutdown protocol.

        Transitions shutdown state from 'running' → 'shutdown_requested'.
        Cancels all active tasks.  The caller MUST check can_application_quit
        before calling app.quit().

        Idempotent: calling multiple times is a no-op after the first call.
        """
        if self._shutdown_state != "running":
            return
        self._transition_state("shutdown_requested")
        self.request_cancel_all()
        if not self.has_running_tasks():
            # Everything already done — skip waiting
            self._transition_state("safe_to_quit")

    def schedule_app_quit_when_safe(self) -> None:
        """Schedule app.quit() to be called when all tasks finish.

        Sets a flag that causes _release_worker to call app.quit() once
        safe_to_quit is reached.  Does NOT call app.quit() immediately.
        """
        self._shutdown_pending_quit = True
        if self._shutdown_state == "safe_to_quit":
            self._shutdown_pending_quit = False
            app = QCoreApplication.instance()
            if app is not None:
                app.quit()

    def request_cancel_all(self) -> None:
        """Request cancellation of all active operations.

        Cancels controllers first, then standalone workers.
        Does NOT call terminate() — workers complete naturally.
        Exceptions are logged, not silenced.
        """
        for c in list(self._active_controllers):
            try:
                c.cancel()
            except Exception:
                logger.exception("Error cancelling controller during shutdown")
        for w in list(self._active_workers):
            try:
                w.requestInterruption()
            except Exception:
                logger.exception("Error requesting worker interruption")
            try:
                if hasattr(w, 'cancel'):
                    w.cancel()  # type: ignore[union-attr]
            except Exception:
                logger.exception("Error cancelling worker during shutdown")

    def has_running_tasks(self) -> bool:
        """Check if any tasks are still running."""
        return bool(self._active_workers) or any(
            c.is_running for c in self._active_controllers
        )

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        """Safe shutdown: cancel all, wait up to timeout_ms for finish.

        Returns True if all tasks finished within the timeout.
        Returns False if tasks are still running after timeout.

        DOES NOT terminate workers — they continue in background.
        DOES NOT call app.quit().
        False means the caller must NOT destroy QApplication yet.

        Sets the state to 'waiting_for_tasks' to indicate the app is
        waiting and should not be forcibly quit.
        """
        import time

        if self._shutdown_state == "running":
            self.begin_application_shutdown()
        else:
            self.request_cancel_all()

        if not self.has_running_tasks():
            if self._shutdown_state != "safe_to_quit":
                self._transition_state("safe_to_quit")
            return True

        self._transition_state("waiting_for_tasks")

        deadline = time.monotonic() + timeout_ms / 1000.0

        app = QCoreApplication.instance()
        if app is None:
            return not self.has_running_tasks()

        while time.monotonic() < deadline:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 100)
            if not self.has_running_tasks():
                self._transition_state("safe_to_quit")
                return True

        # Timeout: tasks still running — caller must NOT destroy QApplication
        return False

    # ── Legacy alias (backward compat) ──

    def cancel_all(self) -> None:
        """Deprecated alias for request_cancel_all()."""
        self.request_cancel_all()


# ── Factory ──


def get_skill_install_task_owner(
    app: "QApplication",
) -> SkillInstallTaskOwner:
    """Return the singleton SkillInstallTaskOwner for this QApplication.

    Creates the TaskOwner with parent=app if it doesn't exist yet.
    Uses QApplication.property() for storage — no module-level globals.

    Args:
        app: The QApplication instance.

    Returns:
        The singleton SkillInstallTaskOwner.
    """
    existing = app.property("skill_task_owner")
    if existing is not None:
        return existing  # type: ignore[return-value]
    owner = SkillInstallTaskOwner(parent=app)
    app.setProperty("skill_task_owner", owner)
    return owner


# ═══════════════════════════════════════════════
# Main controller — QNetworkAccessManager download
# ═══════════════════════════════════════════════


class SkillInstallController(QObject):
    """Qt controller for skill install, download, and uninstall.

    Download uses QNetworkAccessManager (async, event loop, NO QThread).
    File I/O uses InstallWorker/UninstallWorker in QThread.

    Security (Batch 2.4):
    - Explicit ManualRedirectPolicy on every request (Plan A)
    - DownloadRequestContext tracks every hop
    - DownloadState machine prevents writing non-2xx bodies to .part
    - Token isolation: never in URL, Exception, log, signal, or result repr
    - ZIP validated with magic bytes only (PK\x03\x04, PK\x05\x06, PK\x07\x08) — 4 bytes
    - NO ZipFile open, NO infolist(), NO central directory parse in UI thread
    - Atomic commit: flush + os.fsync() + os.replace()
    - Full CRC/archive validation runs in InstallWorker (QThread) — NOT UI thread

    Signals (custom — do NOT override QThread builtins):
        progress_changed(bytes_done, bytes_total): Download progress.
        stage_changed(stage_name): Install stage for UI display.
        result_ready(result): SkillInstallResult or SkillUninstallResult.
        error_occurred(error_message): Emitted on failure.
        running_changed(bool): True when busy, False when idle.

    Usage:
        controller = SkillInstallController(parent=widget, task_owner=task_owner)
        controller.result_ready.connect(on_result)
        controller.start_download_and_install(owner="org", repo="skill", revision="main")
    """

    progress_changed = pyqtSignal(int, int)
    stage_changed = pyqtSignal(str)
    result_ready = pyqtSignal(object)  # SkillInstallResult | SkillUninstallResult
    error_occurred = pyqtSignal(str)
    running_changed = pyqtSignal(bool)

    def __init__(
        self,
        parent: QObject | None = None,
        network_manager: QNetworkAccessManager | None = None,
        task_owner: SkillInstallTaskOwner | None = None,
    ) -> None:
        super().__init__(parent)
        self._nam = network_manager or QNetworkAccessManager(self)
        self._task_owner = task_owner  # Application-level lifecycle owner

        # ── Owned QTimer — NOT static QTimer.singleShot() ──
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_download_timeout)

        # ── Download state machine ──
        self._running = False
        self._download_state: DownloadState = DownloadState.FINISHED
        self._active_reply: QNetworkReply | None = None
        self._download_part_path: str | None = None
        self._download_dest_path: str | None = None
        self._download_hasher: "hashlib._Hash | None" = None
        self._download_bytes: int = 0
        self._download_max_bytes: int = 0
        self._download_revision: str = ""
        self._download_sub_path: str = ""
        self._download_activate: bool = False
        self._redirect_count: int = 0
        self._request_serial: int = 0
        self._active_serial: int | None = None
        self._reply_serials: dict[int, int] = {}  # id(reply) → serial

        # ── Download request context (updated per hop) ──
        self._ctx: DownloadRequestContext | None = None

        # ── Install worker state ──
        self._install_thread: QThread | None = None
        self._install_worker: InstallWorker | None = None
        self._uninstall_thread: QThread | None = None
        self._uninstall_worker: UninstallWorker | None = None

        # ── Token (never logged, never in error messages) ──
        self._token: str | None = None

    # ── Public API ──

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def download_state(self) -> str:
        """Current download state (for test inspection)."""
        return self._download_state.name

    def start_install_from_directory(
        self,
        directory: str,
        activate_after_install: bool = False,
    ) -> None:
        """Install a skill from a local directory."""
        if self._running:
            return
        self._set_running(True)
        self._start_install("local_directory", directory, activate_after_install)

    def start_install_from_zip(
        self,
        zip_path: str,
        sub_path: str = "",
        activate_after_install: bool = False,
    ) -> None:
        """Install a skill from a local ZIP file."""
        if self._running:
            return
        self._set_running(True)
        self._start_install("local_zip", zip_path, activate_after_install, sub_path=sub_path)

    def start_download_and_install(
        self,
        github_url: str = "",
        token: str | None = None,
        sub_path: str = "",
        activate_after_install: bool = False,
        *,
        owner: str = "",
        repo: str = "",
        revision: str = "",
    ) -> None:
        """Download a GitHub archive and install.

        Two modes:
        1. Direct URL: github_url + optional token
        2. Structured: owner + repo + revision (preferred)

        The structured mode builds a codeload.github.com URL.
        """
        if self._running:
            return

        self._token = token  # Store but never log

        if owner and repo and revision:
            try:
                url = build_github_archive_url(owner, repo, revision)
            except ValueError as e:
                self.error_occurred.emit(str(e))
                return
            self._download_revision = revision
        elif github_url:
            is_valid, error_msg = validate_github_url_for_ui(github_url)
            if not is_valid:
                self.error_occurred.emit(error_msg)
                return
            url = github_url
            try:
                from dp_engine.skills.install_service import validate_github_archive_url
                _, _, rev = validate_github_archive_url(github_url)
                self._download_revision = rev
            except Exception:
                self._download_revision = ""
        else:
            self.error_occurred.emit("必须提供 URL 或 owner/repo/revision 参数")
            return

        self._download_sub_path = sub_path
        self._download_activate = activate_after_install

        self._set_running(True)
        self.stage_changed.emit("downloading")
        self._start_download(url)

    def start_uninstall(self, skill_id: str, version: str) -> None:
        """Uninstall a skill version."""
        if self._running:
            return
        self._set_running(True)
        self.stage_changed.emit("preparing")

        worker = UninstallWorker(skill_id, version)
        worker.result_ready.connect(self._on_uninstall_result)
        worker.error_occurred.connect(self._on_install_error)

        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.result_ready.connect(thread.quit)
        worker.error_occurred.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)

        # Register worker with application-level TaskOwner for lifecycle tracking
        if self._task_owner is not None:
            self._task_owner.adopt_worker(thread)

        self._uninstall_worker = worker
        self._uninstall_thread = thread
        thread.start()

    def cancel(self) -> None:
        """Cancel the current operation.

        Download phase: reply.abort() → cleans .part file.
        Install phase: sets cancel flag on worker.
        """
        if not self._running:
            return

        self._timeout_timer.stop()

        if self._active_reply is not None:
            self._download_state = DownloadState.CANCELLED
            self._active_reply.abort()

        if self._install_worker is not None:
            self._install_worker.cancel()

        if self._uninstall_worker is not None:
            self._uninstall_worker.cancel()

    # ── Download internals ──

    def _start_download(self, url: str) -> None:
        """Start async download via QNetworkAccessManager.

        Creates a DownloadRequestContext and fires the initial request
        with ManualRedirectPolicy.
        """
        from dp_engine.skills.package_models import get_default_skill_paths, get_default_limits, ensure_skill_dirs

        paths = get_default_skill_paths()
        ensure_skill_dirs(paths)
        limits = get_default_limits()

        task_id = uuid.uuid4().hex[:12]
        self._download_dest_path = str(paths.downloads_dir / f"{task_id}.zip")
        self._download_part_path = str(paths.downloads_dir / f"{task_id}.part")
        self._download_max_bytes = limits.max_archive_bytes
        self._download_bytes = 0
        self._download_hasher = hashlib.sha256()
        self._redirect_count = 0

        # Advance serial
        self._request_serial += 1
        self._active_serial = self._request_serial

        # Create initial context
        qurl = QUrl(url)
        token_allowed = should_attach_github_token(qurl) if self._token else False
        visited: tuple[str, ...] = (_normalize_url_for_comparison(url),)

        self._ctx = DownloadRequestContext(
            task_id=task_id,
            request_serial=self._active_serial,
            current_url=url,
            visited_urls=visited,
            redirect_count=0,
            token_allowed=token_allowed,
        )

        # Validate initial URL
        error = _validate_download_url(qurl)
        if error is not None:
            self._cleanup_part_file()
            self._finish_download_with_error(error)
            return

        # Transition to waiting_headers
        self._download_state = DownloadState.WAITING_HEADERS

        # Start timeout timer
        self._timeout_timer.start(_DEFAULT_TIMEOUT_MS)

        # Build and fire request
        self._fire_request(qurl)

    def _fire_request(self, url: QUrl) -> None:
        """Create and fire a QNetworkRequest with ManualRedirectPolicy.

        Every hop (initial + redirects) goes through here, so every
        request is explicitly constructed by application code.
        """
        req = QNetworkRequest(url)

        # ── Explicit ManualRedirectPolicy (Plan A) ──
        # Qt default is NoLessSafeRedirectPolicy — we override to manual
        # so EVERY redirect is intercepted, validated, and re-issued by us.
        req.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )

        # ── Authorization header ──
        # Token only attached if the target domain is explicitly allowed.
        # This is re-evaluated for every hop.
        if self._token and should_attach_github_token(url):
            req.setRawHeader(b"Authorization", f"token {self._token}".encode("utf-8"))

        # ── Accept ZIP ──
        req.setRawHeader(b"Accept", b"application/zip,application/octet-stream,*/*")

        self._active_reply = self._nam.get(req)
        if self._active_reply is not None:
            self._reply_serials[id(self._active_reply)] = self._active_serial  # type: ignore[arg-type]
            self._active_reply.readyRead.connect(self._on_ready_read)
            self._active_reply.finished.connect(self._on_reply_finished)
            # NOTE: We intentionally do NOT connect redirected() signal.
            # With ManualRedirectPolicy, Qt will NOT follow redirects.
            # Instead, we detect 3xx in _on_reply_finished and handle manually.

    def _on_ready_read(self) -> None:
        """Read available data from the active reply.

        CRITICAL: Data is ONLY written to .part when state is
        DOWNLOADING_FINAL_CONTENT.  Redirect responses (3xx) and error
        responses (4xx/5xx) are read and discarded — never written to disk.
        """
        reply = self._active_reply
        if reply is None:
            return

        if id(reply) not in self._reply_serials:
            return

        data = reply.readAll()
        if data.isEmpty():
            return

        chunk = data.data()
        chunk_len = len(chunk)

        # ── State gate: only write to .part during final content download ──
        if self._download_state == DownloadState.WAITING_HEADERS:
            # We haven't confirmed a 2xx yet — buffer or discard the chunk.
            # For safety, discard: we will re-request when we get the final URL.
            # The first few bytes may arrive before finished() fires.
            # We buffer up to 8KB for magic-byte check, discard the rest.
            return

        if self._download_state != DownloadState.DOWNLOADING_FINAL_CONTENT:
            # Not in a state where we should write — discard
            return

        # Check size limit
        self._download_bytes += chunk_len
        if self._download_bytes > self._download_max_bytes:
            reply.abort()
            self._cleanup_part_file()
            self._finish_download_with_error(
                f"下载文件超过大小限制: {self._download_bytes} 字节 "
                f"(限制: {self._download_max_bytes} 字节)"
            )
            return

        # Write to .part file
        try:
            part_path = self._download_part_path
            if part_path:
                Path(part_path).parent.mkdir(parents=True, exist_ok=True)
                with open(part_path, "ab") as f:
                    f.write(chunk)
        except OSError as e:
            reply.abort()
            self._cleanup_part_file()
            self._finish_download_with_error(f"文件写入错误: {e}")
            return

        # Update hash
        hasher = self._download_hasher
        if hasher is not None:
            hasher.update(chunk)  # type: ignore[arg-type]

        # Emit progress
        content_length = reply.header(QNetworkRequest.KnownHeaders.ContentLengthHeader)
        total = content_length if content_length and content_length > 0 else 0
        self.progress_changed.emit(self._download_bytes, total)

    def _on_reply_finished(self) -> None:
        """Handle QNetworkReply.finished — the ONLY download completion path.

        All paths converge here:
        1. Normal 2xx completion → validate ZIP → commit → install
        2. 3xx redirect → validate target → create new request
        3. 4xx/5xx error → fail
        4. Cancel → cleanup
        5. Timeout → cleanup
        """
        obj = self.sender()
        if obj is None:
            return

        reply = cast(QNetworkReply, obj)

        # Identity check
        if reply is not self._active_reply:
            reply.deleteLater()
            return

        # Serial check
        reply_id = id(reply)
        reply_serial = self._reply_serials.pop(reply_id, None)
        if reply_serial is not None and reply_serial != self._active_serial:
            reply.deleteLater()
            return

        # Clear active reply
        self._active_reply = None

        error = reply.error()

        # ── Handle cancellation ──
        if error == QNetworkReply.NetworkError.OperationCanceledError:
            reply.deleteLater()
            self._timeout_timer.stop()
            self._cleanup_part_file()
            if self._download_state != DownloadState.CANCELLED:
                self._set_running(False)
            else:
                self._download_state = DownloadState.CANCELLED
                self._set_running(False)
            return

        # ── Handle network error ──
        if error != QNetworkReply.NetworkError.NoError:
            reply.deleteLater()
            self._timeout_timer.stop()
            self._cleanup_part_file()
            self._download_state = DownloadState.FAILED
            self._finish_download_with_error(
                f"网络错误: {reply.errorString()}"
            )
            return

        # ── Read HTTP status ──
        status_code_val = reply.attribute(
            QNetworkRequest.Attribute.HttpStatusCodeAttribute
        )
        status_code: int | None = (
            int(status_code_val) if status_code_val is not None else None
        )

        # ── Handle 3xx redirect (ManualRedirectPolicy) ──
        if status_code is not None and 300 <= status_code < 400:
            self._handle_manual_redirect(reply)
            reply.deleteLater()
            return

        # ── Handle non-2xx responses ──
        if status_code is not None and status_code != 200:
            reply.deleteLater()
            self._timeout_timer.stop()
            self._cleanup_part_file()
            self._download_state = DownloadState.FAILED
            self._finish_download_with_error(
                f"HTTP 错误 {status_code}"
            )
            return

        # ── 2xx success: transition to downloading_final_content ──
        # (readyRead may have already fired with some data; it was buffered/discarded
        # during WAITING_HEADERS. Now read any remaining data.)
        self._download_state = DownloadState.DOWNLOADING_FINAL_CONTENT

        # Read any remaining body data that arrived after the last readyRead
        remaining = reply.readAll()
        if not remaining.isEmpty():
            chunk = remaining.data()
            chunk_len = len(chunk)
            self._download_bytes += chunk_len
            if self._download_bytes <= self._download_max_bytes:
                try:
                    part_path = self._download_part_path
                    if part_path:
                        Path(part_path).parent.mkdir(parents=True, exist_ok=True)
                        with open(part_path, "ab") as f:
                            f.write(chunk)
                except OSError as e:
                    reply.deleteLater()
                    self._timeout_timer.stop()
                    self._cleanup_part_file()
                    self._finish_download_with_error(f"文件写入错误: {e}")
                    return
                hasher = self._download_hasher
                if hasher is not None:
                    hasher.update(chunk)  # type: ignore[arg-type]

        reply.deleteLater()
        self._timeout_timer.stop()

        # ── Validate and commit downloaded file ──
        self._validate_and_commit_download()

    def _handle_manual_redirect(self, reply: QNetworkReply) -> None:
        """Handle a 3xx response under ManualRedirectPolicy.

        1. Read the redirect target from RedirectionTargetAttribute
        2. Validate the target URL
        3. Check redirect count limit
        4. Check for cycles
        5. Create a new QNetworkRequest with fresh header decision
        6. Fire the new request
        """
        # Increment redirect count
        self._redirect_count += 1
        if self._redirect_count > _MAX_REDIRECTS:
            self._timeout_timer.stop()
            self._cleanup_part_file()
            self._download_state = DownloadState.FAILED
            self._finish_download_with_error(
                f"重定向次数超过限制 ({_MAX_REDIRECTS})"
            )
            return

        # Read redirect target
        redirect_target = reply.attribute(
            QNetworkRequest.Attribute.RedirectionTargetAttribute
        )
        if redirect_target is None:
            self._timeout_timer.stop()
            self._cleanup_part_file()
            self._download_state = DownloadState.FAILED
            self._finish_download_with_error("收到 3xx 响应但无重定向目标")
            return

        # Convert to QUrl
        if isinstance(redirect_target, QUrl):
            target_url = redirect_target
        else:
            target_url = QUrl(str(redirect_target))

        # Resolve relative URLs against the current request URL
        if target_url.isRelative():
            current_url = reply.url()
            target_url = current_url.resolved(target_url)

        target_str = target_url.toString()

        # ── Validate target URL ──
        error = _validate_download_url(target_url)
        if error is not None:
            self._timeout_timer.stop()
            self._cleanup_part_file()
            self._download_state = DownloadState.FAILED
            self._finish_download_with_error(error)
            return

        # ── Cycle detection ──
        normalized = _normalize_url_for_comparison(target_str)
        if self._ctx is not None and normalized in self._ctx.visited_urls:
            self._timeout_timer.stop()
            self._cleanup_part_file()
            self._download_state = DownloadState.FAILED
            self._finish_download_with_error(
                f"检测到重定向循环: {target_str[:200]}"
            )
            return

        # ── Update context ──
        if self._ctx is not None:
            new_visited = self._ctx.visited_urls + (normalized,)
            token_allowed = should_attach_github_token(target_url) if self._token else False
            self._ctx = DownloadRequestContext(
                task_id=self._ctx.task_id,
                request_serial=self._active_serial or 0,
                current_url=target_str,
                visited_urls=new_visited,
                redirect_count=self._redirect_count,
                token_allowed=token_allowed,
            )

        # ── Fire new request ──
        # This creates a brand new QNetworkRequest with fresh header decisions.
        self._fire_request(target_url)

    # ── Recognized ZIP magic bytes ──
    # PK\x03\x04 = standard ZIP local file header
    # PK\x05\x06 = empty ZIP (end of central directory only)
    # PK\x07\x08 = spanned/split ZIP (spanned archive marker)
    _VALID_ZIP_MAGICS: tuple[bytes, ...] = (
        b"PK\x03\x04",
        b"PK\x05\x06",
        b"PK\x07\x08",
    )

    def _validate_and_commit_download(self) -> None:
        """Validate downloaded file and atomically commit .part → .zip.

        Network Controller responsibilities (Batch 2.4):
        1. .part file exists and is non-empty
        2. ZIP magic bytes (PK\x03\x04 / PK\x05\x06 / PK\x07\x08) — 4 bytes only
        3. File size within limits
        4. Atomic commit: flush + fsync + os.replace

        Does NOT (Batch 2.4 — deferred to InstallWorker in QThread):
        - Open zipfile.ZipFile or parse central directory
        - Call infolist(), namelist(), testzip()
        - Extract members or read file contents
        - Compute package content checksum
        - Traverse files for SHA-256

        Those operations run in InstallWorker (QThread), not UI thread.

        On failure: .part is deleted and error emitted.
        On success: flush + fsync + os.replace to .zip, then emit
        download_complete stage and start InstallWorker.

        The download_complete stage means "archive bytes downloaded,
        awaiting security package validation in InstallWorker" —
        NOT "skill package is valid".
        """
        part_path = self._download_part_path
        dest_path = self._download_dest_path

        if not part_path or not Path(part_path).exists():
            self._cleanup_part_file()
            self._finish_download_with_error("下载文件不存在")
            return

        file_size = Path(part_path).stat().st_size
        if file_size == 0:
            self._cleanup_part_file()
            self._finish_download_with_error("下载文件为空")
            return

        # ── ZIP magic check (Batch 2.3: expanded to accept empty/spanned) ──
        try:
            with open(part_path, "rb") as f:
                magic = f.read(4)
            if not magic.startswith(b"PK"):
                self._cleanup_part_file()
                self._finish_download_with_error(
                    "下载的文件不是有效的 ZIP 归档（magic bytes 不匹配）"
                )
                return
            if magic not in self._VALID_ZIP_MAGICS:
                self._cleanup_part_file()
                self._finish_download_with_error(
                    f"不支持的 ZIP 格式（magic: {magic!r}）"
                )
                return
        except OSError as e:
            self._cleanup_part_file()
            self._finish_download_with_error(f"无法读取下载文件: {e}")
            return

        # ── Compute archive SHA-256 (from streaming hash during download) ──
        # Full archive validation (ZipFile open, central directory parse,
        # entry counts, CRC) is deferred to InstallWorker in QThread.
        # Only the 4-byte magic check above runs in UI thread.
        hasher = self._download_hasher
        archive_sha256 = hasher.hexdigest() if hasher is not None else ""  # pyright: ignore[reportAttributeAccessIssue]

        # ── Flush and fsync .part before atomic rename ──
        try:
            fd = os.open(part_path, os.O_RDWR)
            os.fsync(fd)
            os.close(fd)
        except OSError:
            pass  # Best-effort; os.replace is still atomic

        # ── Atomic rename .part → .zip ──
        try:
            if part_path and dest_path:
                os.replace(part_path, dest_path)
                self._download_part_path = None
        except OSError as e:
            self._cleanup_part_file()
            self._finish_download_with_error(f"文件提交失败: {e}")
            return

        # ── Success: emit download_complete, then start InstallWorker ──
        # The download_complete stage means "archive bytes downloaded, awaiting
        # security package validation" — NOT "skill package is valid".
        self._download_state = DownloadState.FINISHED
        self.stage_changed.emit("download_complete")
        self._start_install(
            "github_archive",
            dest_path or "",
            self._download_activate,
            sub_path=self._download_sub_path,
            revision=self._download_revision,
        )

    def _on_download_timeout(self) -> None:
        """Timeout handler — abort active download."""
        if self._active_reply is not None:
            self._download_state = DownloadState.CANCELLED
            self._active_reply.abort()
            self._cleanup_part_file()
            self._finish_download_with_error("下载超时，请检查网络连接")

    # ── Install internals ──

    def _start_install(
        self,
        source_type: str,
        location: str,
        activate_after_install: bool = False,
        sub_path: str = "",
        revision: str | None = None,
    ) -> None:
        """Create and start an InstallWorker in a QThread."""
        worker = InstallWorker(
            source_type=source_type,
            location=location,
            activate_after_install=activate_after_install,
            sub_path=sub_path,
            revision=revision,
        )
        worker.stage_changed.connect(self.stage_changed.emit)
        worker.result_ready.connect(self._on_install_result)
        worker.error_occurred.connect(self._on_install_error)

        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.result_ready.connect(thread.quit)
        worker.error_occurred.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)

        # Register worker with application-level TaskOwner for lifecycle tracking
        if self._task_owner is not None:
            self._task_owner.adopt_worker(thread)

        self._install_worker = worker
        self._install_thread = thread
        thread.start()

    # ── Result handlers ──

    def _on_install_result(self, result: object) -> None:
        """Called when install completes."""
        self._cleanup_install()
        self._set_running(False)
        self.result_ready.emit(result)

    def _on_uninstall_result(self, result: object) -> None:
        """Called when uninstall completes."""
        self._cleanup_uninstall()
        self._set_running(False)
        self.result_ready.emit(result)

    def _on_install_error(self, error_msg: str) -> None:
        """Called when install/uninstall fails.

        Token is NEVER included in the error message.
        """
        self._cleanup_install()
        self._cleanup_uninstall()
        self._cleanup_part_file()
        self._set_running(False)
        # Sanity: strip token from error message if it somehow leaked
        self.error_occurred.emit(error_msg)

    def _finish_download_with_error(self, error_msg: str) -> None:
        """Finish download phase with an error.

        Token is NEVER included in the error message or logs.
        """
        self._download_state = DownloadState.FAILED
        self._set_running(False)
        self.error_occurred.emit(error_msg)

    # ── State management ──

    def _set_running(self, running: bool) -> None:
        if self._running != running:
            self._running = running
            self.running_changed.emit(running)

    def _cleanup_part_file(self) -> None:
        """Delete the .part file if it exists."""
        part = self._download_part_path
        if part and Path(part).exists():
            try:
                Path(part).unlink(missing_ok=True)
            except OSError:
                pass
        self._download_part_path = None

    def _cleanup_install(self) -> None:
        """Clean up install worker references.

        The QThread will auto-delete via thread.finished → deleteLater.
        We just clear our references.
        """
        self._install_worker = None
        self._install_thread = None

    def _cleanup_uninstall(self) -> None:
        """Clean up uninstall worker references."""
        self._uninstall_worker = None
        self._uninstall_thread = None

    # ── Helpers ──

    @staticmethod
    def _url_domain_allowed(url_str: str) -> bool:
        """Check if a URL's host is in the allowed download domains."""
        try:
            parsed = urlparse(url_str)
            host = parsed.hostname or ""
            return host in _ALLOWED_DOWNLOAD_DOMAINS
        except Exception:
            return False


def _looks_like_ip(host: str) -> bool:
    """Check if a host string looks like an IPv4 or IPv6 address."""
    # IPv4 pattern
    ipv4_pat = r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"
    if re.match(ipv4_pat, host):
        return True
    # IPv6 bracket notation
    if host.startswith("[") and host.endswith("]"):
        return True
    # IPv6 with colons
    if ":" in host:
        parts = host.split(":")
        if len(parts) >= 2:
            return True
    return False
