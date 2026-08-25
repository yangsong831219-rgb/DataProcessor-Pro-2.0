"""ArtifactOperationCoordinator — thread-safe mutex for artifact operations.

Batch 3.3.1A: Application-level singleton that prevents concurrent
operations on the same (skill_id, task_id) owner.

Key: (skill_id, task_id)
Operations: USER_ARTIFACT_OPERATION, BRIDGE_SESSION
Rule: same owner can only have one operation at a time,
      regardless of operation kind. Different owners can run concurrently.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from dp_engine.report_bridge.models import OperationKind

logger = logging.getLogger(__name__)


class _CoordinatorToken:
    """Opaque token returned by try_acquire. Release is idempotent."""

    def __init__(
        self,
        key: tuple[str, str],
        operation: OperationKind,
        on_release: Callable[[tuple[str, str], OperationKind], None],
    ) -> None:
        self._key = key
        self._operation = operation
        self._on_release = on_release
        self._released = False
        self._lock = threading.Lock()

    @property
    def key(self) -> tuple[str, str]:
        return self._key

    @property
    def operation(self) -> OperationKind:
        return self._operation

    @property
    def released(self) -> bool:
        with self._lock:
            return self._released

    def release(self) -> None:
        """Idempotent release. Safe to call multiple times."""
        with self._lock:
            if self._released:
                return
            self._released = True
        try:
            self._on_release(self._key, self._operation)
        except Exception:
            logger.exception("Coordinator token release callback failed")


class ArtifactOperationCoordinator:
    """Application-level singleton coordinating artifact operations.

    Tracks active operations per (skill_id, task_id) and enforces
    mutual exclusion. Non-blocking — try_acquire returns None on conflict.

    This coordinator does NOT modify ArtifactStore or prevent direct
    file access outside the application. It is a cooperative lock.

    Bridge-internal export calls (within BRIDGE_SESSION) do NOT pass
    through this coordinator — they are authorized implicitly by the
    session token.
    """

    def __init__(self) -> None:
        # Map: (skill_id, task_id) → (OperationKind, token_id)
        self._active: dict[tuple[str, str], tuple[OperationKind, int]] = {}
        self._lock = threading.Lock()
        self._token_counter = 0

    def try_acquire(
        self,
        skill_id: str,
        task_id: str,
        *,
        operation: OperationKind,
    ) -> _CoordinatorToken | None:
        """Non-blocking attempt to acquire an operation token.

        Returns None if the same owner already has an active operation
        (of any kind). Returns a token on success.

        The token must be released via token.release() when done.
        Release is idempotent.
        """
        key = (skill_id, task_id)
        with self._lock:
            if key in self._active:
                existing_op, _ = self._active[key]
                logger.debug(
                    "Coordinator: conflict on %s — %s blocks %s",
                    key, existing_op.value, operation.value,
                )
                return None

            self._token_counter += 1
            token_id = self._token_counter
            self._active[key] = (operation, token_id)
            logger.debug(
                "Coordinator: acquired %s for %s (token %d)",
                operation.value, key, token_id,
            )

        return _CoordinatorToken(key, operation, self._release)

    def _release(self, key: tuple[str, str], operation: OperationKind) -> None:
        """Internal release callback — idempotent by design."""
        with self._lock:
            if key in self._active:
                _, token_id = self._active[key]
                del self._active[key]
                logger.debug(
                    "Coordinator: released %s for %s (token %d)",
                    operation.value, key, token_id,
                )
            else:
                logger.debug(
                    "Coordinator: release called but no active token for %s", key
                )

    def active_count(self) -> int:
        """Return number of currently active operations (for testing)."""
        with self._lock:
            return len(self._active)

    def is_active(self, skill_id: str, task_id: str) -> bool:
        """Check if an operation is active for the given owner."""
        with self._lock:
            return (skill_id, task_id) in self._active

    def release_all(self) -> int:
        """Release all active tokens (for shutdown). Returns count released."""
        with self._lock:
            count = len(self._active)
            self._active.clear()
            logger.info("Coordinator: released all %d active tokens", count)
            return count
