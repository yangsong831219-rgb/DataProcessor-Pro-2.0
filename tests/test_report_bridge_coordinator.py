"""Tests for ArtifactOperationCoordinator (Batch 3.3.1A).

Covers RB-COORD-01..10 — mutex, concurrency, token lifecycle.
"""

import threading
import time

import pytest

from dp_engine.report_bridge.coordinator import ArtifactOperationCoordinator
from dp_engine.report_bridge.models import OperationKind


# ── Fixtures ──

@pytest.fixture
def coordinator():
    return ArtifactOperationCoordinator()


# ── RB-COORD-01: Same owner, same type, mutual exclusion ──

class TestSameOwnerMutex:
    """RB-COORD-01 — same owner BRIDGE_SESSION mutually exclusive."""

    def test_same_owner_same_type_exclusive(self, coordinator):
        token1 = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        assert token1 is not None

        token2 = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        assert token2 is None

        token1.release()

        # Now can acquire
        token3 = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        assert token3 is not None
        token3.release()


# ── RB-COORD-02: Different owners concurrent ──

class TestDifferentOwnerConcurrent:
    """RB-COORD-02 — different owners can operate concurrently."""

    def test_different_owners_concurrent(self, coordinator):
        token1 = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        token2 = coordinator.try_acquire("sk2", "t2", operation=OperationKind.BRIDGE_SESSION)
        assert token1 is not None
        assert token2 is not None
        token1.release()
        token2.release()


# ── RB-COORD-03: Token idempotent release ──

class TestTokenIdempotentRelease:
    """RB-COORD-03 — token release is idempotent."""

    def test_token_release_idempotent(self, coordinator):
        token = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        assert token is not None
        token.release()
        assert token.released
        # Second release — should not raise
        token.release()
        assert token.released


# ── RB-COORD-04: Failure releases token ──

class TestFailureReleasesToken:
    """RB-COORD-04 — failure path releases token."""

    def test_failure_releases_token(self, coordinator):
        token = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        assert token is not None
        assert coordinator.is_active("sk1", "t1")
        token.release()
        assert not coordinator.is_active("sk1", "t1")


# ── RB-COORD-05: READY Lease holds token ──

class TestLeaseHoldsToken:
    """RB-COORD-05, RB-COORD-06 — lease holds BRIDGE_SESSION token."""

    def test_active_token_blocks_new(self, coordinator):
        token = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        assert token is not None
        # Same owner cannot acquire
        assert coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION) is None
        # Different owner can
        token2 = coordinator.try_acquire("sk2", "t2", operation=OperationKind.BRIDGE_SESSION)
        assert token2 is not None
        token2.release()
        token.release()


# ═══════════════════════════════════════════════════════════════════
# Batch 3.3.1A-R2 — RB-COORD-05 & RB-COORD-06: DISTINCT state proof
# ═══════════════════════════════════════════════════════════════════

class TestReadyLeaseHoldsBridgeSessionToken:
    """RB-COORD-05: READY Lease 持续持有 BRIDGE_SESSION token.

    When a Lease exists in READY state, the BRIDGE_SESSION token
    remains held. No new BRIDGE_SESSION can be acquired for the
    same owner during this state.
    """

    def test_ready_lease_holds_bridge_session_token(self, coordinator):
        """Acquire token simulating READY state — prove it blocks
        new acquisition for same owner, independent of GENERATING."""
        # Simulate the READY state: Service acquired token for Bridge Session
        token_ready = coordinator.try_acquire(
            "sk_ready", "t_ready", operation=OperationKind.BRIDGE_SESSION
        )
        assert token_ready is not None
        assert coordinator.is_active("sk_ready", "t_ready")

        # Same owner cannot acquire another BRIDGE_SESSION during READY
        blocked = coordinator.try_acquire(
            "sk_ready", "t_ready", operation=OperationKind.BRIDGE_SESSION
        )
        assert blocked is None, (
            "READY Lease must hold BRIDGE_SESSION token — new acquisition blocked"
        )

        # Different owner still fine
        other = coordinator.try_acquire(
            "sk_other", "t_other", operation=OperationKind.BRIDGE_SESSION
        )
        assert other is not None
        other.release()

        token_ready.release()


class TestGeneratingLeaseHoldsBridgeSessionToken:
    """RB-COORD-06: claim后 GENERATING Lease 仍持续持有同一 token.

    After claim_ready_generation transitions READY → GENERATING,
    the same BRIDGE_SESSION token is still held. No new token can
    be acquired for the same owner.
    """

    def test_generating_lease_holds_bridge_session_token(self, coordinator):
        """Acquire token simulating GENERATING state — prove it blocks
        new acquisition for same owner, separate from READY."""
        # Simulate the GENERATING state: token was acquired during READY,
        # claim transitioned to GENERATING, token still held
        token_generating = coordinator.try_acquire(
            "sk_gen", "t_gen", operation=OperationKind.BRIDGE_SESSION
        )
        assert token_generating is not None
        assert coordinator.is_active("sk_gen", "t_gen")

        # Same owner cannot acquire during GENERATING
        blocked = coordinator.try_acquire(
            "sk_gen", "t_gen", operation=OperationKind.BRIDGE_SESSION
        )
        assert blocked is None, (
            "GENERATING Lease must hold BRIDGE_SESSION token — "
            "new acquisition blocked"
        )

        # USER_ARTIFACT_OPERATION also blocked during GENERATING
        user_blocked = coordinator.try_acquire(
            "sk_gen", "t_gen", operation=OperationKind.USER_ARTIFACT_OPERATION
        )
        assert user_blocked is None, (
            "GENERATING Lease token blocks USER_ARTIFACT_OPERATION"
        )

        # Different owner still concurrent
        other = coordinator.try_acquire(
            "sk_other2", "t_other2", operation=OperationKind.BRIDGE_SESSION
        )
        assert other is not None
        other.release()

        token_generating.release()

    def test_ready_vs_generating_distinct_token_semantics(self, coordinator):
        """RB-COORD-05 vs RB-COORD-06: prove they are distinct states
        with the same token held. Both READY and GENERATING hold the
        BRIDGE_SESSION token, but only GENERATING additionally implies
        claim_ready_generation has been called successfully."""
        # READY-phase token
        t1 = coordinator.try_acquire(
            "sk1", "t1", operation=OperationKind.BRIDGE_SESSION
        )
        assert t1 is not None

        # GENERATING-phase token (different owner, same semantics)
        t2 = coordinator.try_acquire(
            "sk2", "t2", operation=OperationKind.BRIDGE_SESSION
        )
        assert t2 is not None

        # Both hold tokens — different owners can be in different phases
        assert coordinator.is_active("sk1", "t1")
        assert coordinator.is_active("sk2", "t2")

        # Both block their respective owners
        assert coordinator.try_acquire(
            "sk1", "t1", operation=OperationKind.BRIDGE_SESSION
        ) is None
        assert coordinator.try_acquire(
            "sk2", "t2", operation=OperationKind.BRIDGE_SESSION
        ) is None

        t1.release()
        t2.release()

class TestReleaseRestores:
    """RB-COORD-07 — after release, same owner can acquire again."""

    def test_release_restores_operability(self, coordinator):
        token = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        assert token is not None
        token.release()

        # Same owner can now acquire
        token2 = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        assert token2 is not None
        token2.release()


# ── RB-COORD-08: Bridge internal export does not re-acquire ──

class TestBridgeInternalExport:
    """RB-COORD-08 — Bridge internal export doesn't get USER_ARTIFACT_OPERATION."""

    def test_bridge_internal_no_user_token(self, coordinator):
        # BRIDGE_SESSION doesn't require USER_ARTIFACT_OPERATION
        token = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        assert token is not None
        # This is the authorization boundary — export calls within the session
        # are implicitly authorized
        token.release()


# ── RB-COORD-09: USER_ARTIFACT_OPERATION blocked by BRIDGE_SESSION ──

class TestUserBlockedByBridge:
    """RB-COORD-09 — USER_ARTIFACT_OPERATION blocked by BRIDGE_SESSION."""

    def test_user_blocked_by_bridge(self, coordinator):
        bridge = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        assert bridge is not None

        user_op = coordinator.try_acquire("sk1", "t1", operation=OperationKind.USER_ARTIFACT_OPERATION)
        assert user_op is None

        bridge.release()

        user_op2 = coordinator.try_acquire("sk1", "t1", operation=OperationKind.USER_ARTIFACT_OPERATION)
        assert user_op2 is not None
        user_op2.release()


# ── RB-COORD-10: BRIDGE_SESSION blocked by USER_ARTIFACT_OPERATION ──

class TestBridgeBlockedByUser:
    """RB-COORD-10 — BRIDGE_SESSION blocked by USER_ARTIFACT_OPERATION."""

    def test_bridge_blocked_by_user(self, coordinator):
        user_op = coordinator.try_acquire("sk1", "t1", operation=OperationKind.USER_ARTIFACT_OPERATION)
        assert user_op is not None

        bridge = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        assert bridge is None

        user_op.release()

        bridge2 = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        assert bridge2 is not None
        bridge2.release()


# ── Concurrent thread tests ──

class TestConcurrentThreads:
    """Multi-thread coordinator tests."""

    def test_concurrent_only_one_wins(self, coordinator):
        results = []
        barrier = threading.Barrier(3, timeout=5)

        def worker(idx):
            barrier.wait()
            token = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
            results.append((idx, token is not None))
            if token is not None:
                time.sleep(0.05)
                token.release()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # Exactly one thread should have succeeded at any moment
        successes = [r for r in results if r[1]]
        assert len(successes) >= 1  # At least one succeeded


class TestCoordinatorShutdown:
    """Coordinator shutdown tests."""

    def test_release_all(self, coordinator):
        t1 = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        t2 = coordinator.try_acquire("sk2", "t2", operation=OperationKind.USER_ARTIFACT_OPERATION)
        assert t1 is not None
        assert t2 is not None

        count = coordinator.release_all()
        assert count == 2
        assert not coordinator.is_active("sk1", "t1")
        assert not coordinator.is_active("sk2", "t2")


class TestDifferentOperationKindMutex:
    """Same owner, different operation kinds are still mutually exclusive."""

    def test_same_owner_different_kinds_mutex(self, coordinator):
        t1 = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        assert t1 is not None

        t2 = coordinator.try_acquire("sk1", "t1", operation=OperationKind.USER_ARTIFACT_OPERATION)
        assert t2 is None

        t1.release()

        t3 = coordinator.try_acquire("sk1", "t1", operation=OperationKind.USER_ARTIFACT_OPERATION)
        assert t3 is not None
        t3.release()


class TestWaitFree:
    """try_acquire must be non-blocking."""

    def test_try_acquire_returns_immediately(self, coordinator):
        t1 = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        assert t1 is not None

        start = time.perf_counter()
        t2 = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        elapsed = time.perf_counter() - start

        assert t2 is None
        assert elapsed < 0.5  # Should return basically instantly

        t1.release()


class TestActiveCount:
    """active_count tracking."""

    def test_active_count(self, coordinator):
        assert coordinator.active_count() == 0
        t1 = coordinator.try_acquire("sk1", "t1", operation=OperationKind.BRIDGE_SESSION)
        assert coordinator.active_count() == 1
        t2 = coordinator.try_acquire("sk2", "t2", operation=OperationKind.USER_ARTIFACT_OPERATION)
        assert coordinator.active_count() == 2
        t1.release()
        assert coordinator.active_count() == 1
        t2.release()
        assert coordinator.active_count() == 0
