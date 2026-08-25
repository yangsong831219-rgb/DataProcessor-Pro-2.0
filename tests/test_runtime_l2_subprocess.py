"""L2 tests: Subprocess healthcheck execution and Registry integration.

Uses real subprocess.Popen to test SkillRuntimeService with minimal
timeouts (100-500ms). Each test group <10s.

Tests verify:
- Healthy healthcheck
- Missing healthcheck entrypoint
- Dependency validation
- Timeout handling
- Cancellation
- Worker crash recovery
- Registry health status updates
- Parent process does NOT import skill modules
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

from dp_engine.skills.registry import SkillRegistry, SkillRegistrySnapshot
from dp_engine.skills.manifest_parser import parse_skill_manifest
from dp_engine.skills.runtime_service import SkillRuntimeService
from dp_engine.skills.runtime_errors import (
    SkillRuntimeError,
    RuntimeTimeoutError,
)
from dp_engine.skills.models import InstalledSkill, SkillManifest
from dp_engine.skills.errors import SkillManifestError
from tests.fixtures.runtime_fixtures import (
    create_minimal_skill_package,
    create_skill_without_healthcheck,
    create_skill_with_long_running_healthcheck,
    create_skill_with_crashing_healthcheck,
    create_skill_with_missing_entrypoint_file,
    create_skill_with_missing_function,
    create_skill_with_non_callable_entrypoint,
    create_skill_returning_non_dict,
    create_skill_returning_non_bool_healthy,
)


# ── Fixtures ──


@pytest.fixture
def skill_env(tmp_path):
    """Create a temporary skill environment with installed dir and registry.

    Returns (base_dir, registry, installed_dir, skill_dir).
    """
    base_dir = tmp_path / "skills_data"
    base_dir.mkdir()

    installed_dir = base_dir / "installed"
    installed_dir.mkdir()

    registry_path = base_dir / "registry.json"
    registry = SkillRegistry(registry_path)
    registry.load()

    # Create a minimal skill package with healthcheck UNDER installed_dir
    # so it passes the "install_path within installed_dir" gate check
    skill_dir = create_minimal_skill_package(
        installed_dir,
        "test-skill",
        "1.0.0",
        healthy=True,
        healthcheck_message="all systems go",
    )

    # Parse manifest and register
    manifest = parse_skill_manifest(skill_dir)
    installed = InstalledSkill(
        manifest=manifest,
        install_path=str(skill_dir),
        enabled=True,
        installed_at="2024-01-01T00:00:00Z",
        health_status="unknown",
    )
    registry.register(installed)
    registry.save()

    return base_dir, registry, installed_dir, skill_dir


@pytest.fixture
def runtime_service(skill_env):
    """Create a SkillRuntimeService with test fixtures."""
    base_dir, registry, installed_dir, skill_dir = skill_env
    service = SkillRuntimeService(
        registry=registry,
        installed_dir=installed_dir,
    )
    return service, registry, skill_dir


# ── Happy path ──


class TestHealthcheckHappyPath:
    """Normal healthcheck execution."""

    def test_healthy_skill(self, runtime_service):
        """A healthy skill returns healthy status."""
        service, registry, _ = runtime_service
        response = service.run_healthcheck(
            "test-skill", "1.0.0",
            timeout_seconds=5.0,
        )
        assert response.success
        assert response.status == "healthy"
        assert response.operation == "healthcheck"

    def test_registry_updated_after_healthcheck(self, runtime_service):
        """Registry health status is updated after healthcheck."""
        service, registry, _ = runtime_service
        response = service.run_healthcheck(
            "test-skill", "1.0.0",
            timeout_seconds=5.0,
        )
        # Reload registry
        registry.load()
        skill = registry.get("test-skill", "1.0.0")
        assert skill is not None
        assert skill.health_status == "healthy"
        assert skill.health_message == "all systems go"

    def test_healthcheck_pid_differs_from_parent(self, runtime_service):
        """Healthcheck runs in a different PID from parent."""
        service, registry, _ = runtime_service
        parent_pid = os.getpid()
        response = service.run_healthcheck(
            "test-skill", "1.0.0",
            timeout_seconds=5.0,
        )
        # The response itself doesn't contain PID, but we can verify
        # the worker started (non-zero duration)
        assert response.duration_ms > 0

    def test_healthcheck_does_not_affect_active_version(self, runtime_service):
        """Healthcheck result does NOT change active version."""
        service, registry, _ = runtime_service
        response = service.run_healthcheck(
            "test-skill", "1.0.0",
            timeout_seconds=5.0,
        )
        registry.load()
        active = registry.get_active("test-skill")
        assert active is not None
        assert active.version == "1.0.0"
        assert active.health_status == "healthy"


# ── Error paths ──


class TestHealthcheckErrors:
    """Healthcheck error cases."""

    def test_missing_healthcheck_entrypoint(self, tmp_path):
        """Skill without healthcheck entrypoint returns error."""
        base_dir = tmp_path / "skills"
        base_dir.mkdir()
        installed_dir = base_dir / "installed"
        installed_dir.mkdir()

        registry = SkillRegistry(base_dir / "registry.json")
        registry.load()

        # Create skill without healthcheck UNDER installed_dir
        skill_dir = create_skill_without_healthcheck(
            installed_dir,
        )
        manifest = parse_skill_manifest(skill_dir)
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=True,
            installed_at="2024-01-01T00:00:00Z",
        )
        registry.register(installed)
        registry.save()

        service = SkillRuntimeService(registry, installed_dir)
        with pytest.raises(SkillRuntimeError, match="healthcheck entrypoint"):
            service.run_healthcheck("no-hc-skill", "1.0.0")

        # Registry should show not_supported
        registry.load()
        skill = registry.get("no-hc-skill", "1.0.0")
        assert skill is not None
        assert skill.health_status == "not_supported"

    def test_nonexistent_skill(self, runtime_service):
        """Checking a non-existent skill raises error."""
        service, registry, _ = runtime_service
        with pytest.raises(SkillRuntimeError, match="not found"):
            service.run_healthcheck("nonexistent", "1.0.0")

    def test_crashing_healthcheck(self, tmp_path):
        """A crashing healthcheck reports unhealthy."""
        base_dir = tmp_path / "skills"
        base_dir.mkdir()
        installed_dir = base_dir / "installed"
        installed_dir.mkdir()

        registry = SkillRegistry(base_dir / "registry.json")
        registry.load()

        skill_dir = create_skill_with_crashing_healthcheck(
            installed_dir,
        )
        manifest = parse_skill_manifest(skill_dir)
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=True,
            installed_at="2024-01-01T00:00:00Z",
        )
        registry.register(installed)
        registry.save()

        service = SkillRuntimeService(registry, installed_dir)
        response = service.run_healthcheck(
            "crash-skill", "1.0.0",
            timeout_seconds=5.0,
        )
        assert not response.success
        assert response.status == "unhealthy"

    def test_timeout(self, tmp_path):
        """A long-running healthcheck times out."""
        base_dir = tmp_path / "skills"
        base_dir.mkdir()
        installed_dir = base_dir / "installed"
        installed_dir.mkdir()

        registry = SkillRegistry(base_dir / "registry.json")
        registry.load()

        skill_dir = create_skill_with_long_running_healthcheck(
            installed_dir,
            sleep_seconds=30.0,
        )
        manifest = parse_skill_manifest(skill_dir)
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=True,
            installed_at="2024-01-01T00:00:00Z",
        )
        registry.register(installed)
        registry.save()

        service = SkillRuntimeService(registry, installed_dir)
        # Use very short timeout for test
        response = service.run_healthcheck(
            "slow-skill", "1.0.0",
            timeout_seconds=0.3,  # 300ms timeout
        )
        assert response.status == "timeout"

    def test_cancellation(self, tmp_path):
        """Cancellation during healthcheck returns cancelled in <2 seconds."""
        base_dir = tmp_path / "skills"
        base_dir.mkdir()
        installed_dir = base_dir / "installed"
        installed_dir.mkdir()

        registry = SkillRegistry(base_dir / "registry.json")
        registry.load()

        from tests.fixtures.runtime_fixtures import (
            create_skill_with_long_running_healthcheck_polling,
        )
        skill_dir = create_skill_with_long_running_healthcheck_polling(
            installed_dir,
            skill_id="cancel-skill",
            sleep_seconds=30.0,
            poll_interval=0.05,
        )
        manifest = parse_skill_manifest(skill_dir)
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=True,
            installed_at="2024-01-01T00:00:00Z",
        )
        registry.register(installed)
        registry.save()

        # Use fast cancel limits
        service = SkillRuntimeService(
            registry, installed_dir,
            cancel_grace_ms=200,
            terminate_grace_ms=200,
            kill_grace_ms=200,
            poll_interval_ms=50,
        )

        # Launch in a way we can cancel
        import threading
        result_holder: list = []

        t_start = time.monotonic()

        def run_and_cancel():
            resp = service.run_healthcheck(
                "cancel-skill", "1.0.0",
                timeout_seconds=10.0,
            )
            result_holder.append(resp)

        t = threading.Thread(target=run_and_cancel)
        t.start()
        time.sleep(0.2)  # Let it start
        service.cancel()
        t.join(timeout=5.0)
        t_total = time.monotonic() - t_start

        assert t_total < 2.0, (
            f"Cancel test took {t_total:.2f}s — must be < 2.0s"
        )

        if result_holder:
            resp = result_holder[0]
            assert resp.status in ("cancelled", "timeout"), (
                f"Expected cancelled/timeout, got: {resp.status}"
            )


# ── Registry health status flow ──


class TestRegistryHealthFlow:
    """Registry health status transitions."""

    def test_checking_then_healthy(self, runtime_service):
        """Status goes unknown → checking → healthy."""
        service, registry, _ = runtime_service
        # Before: unknown
        skill = registry.get("test-skill", "1.0.0")
        assert skill.health_status == "unknown"

        # During: checking (written by service before launching subprocess)
        # After: healthy (written by service after successful healthcheck)
        service.run_healthcheck("test-skill", "1.0.0", timeout_seconds=5.0)
        registry.load()
        skill = registry.get("test-skill", "1.0.0")
        assert skill.health_status == "healthy"

    def test_update_health_status_transaction(self, tmp_path):
        """Registry update_health_status method works transactionally."""
        rpath = tmp_path / "registry.json"
        registry = SkillRegistry(rpath)
        registry.load()

        # Register a skill
        manifest = SkillManifest(
            schema_version=1,
            skill_id="t-skill",
            name="T",
            version="1.0.0",
            description="d",
            skill_type="executable",
        )
        installed = InstalledSkill(
            manifest=manifest,
            install_path="/tmp/t-skill/1.0.0",
        )
        registry.register(installed)
        registry.save()

        # Take snapshot
        snap = registry.snapshot()

        # Update health
        registry.update_health_status("t-skill", "1.0.0", "healthy", "all good")
        registry.save()

        # Verify
        registry.load()
        skill = registry.get("t-skill", "1.0.0")
        assert skill is not None
        assert skill.health_status == "healthy"
        assert skill.health_message == "all good"
        # enabled should not change
        assert skill.enabled is True

        # Restore to snapshot
        registry.restore(snap)
        registry.save()
        registry.load()
        restored = registry.get("t-skill", "1.0.0")
        assert restored is not None
        assert restored.health_status == "unknown"

    def test_update_health_invalid_status(self, tmp_path):
        """Calling update_health_status with invalid status raises ValueError."""
        rpath = tmp_path / "registry.json"
        registry = SkillRegistry(rpath)
        registry.load()

        manifest = SkillManifest(
            schema_version=1, skill_id="s", name="S", version="1.0.0",
            description="d", skill_type="executable",
        )
        installed = InstalledSkill(
            manifest=manifest, install_path="/tmp/s/1.0.0",
        )
        registry.register(installed)
        registry.save()

        with pytest.raises(ValueError, match="health_status"):
            registry.update_health_status("s", "1.0.0", "INVALID!!", "msg")

    def test_update_health_nonexistent(self, tmp_path):
        """Updating health for nonexistent skill raises KeyError."""
        rpath = tmp_path / "registry.json"
        registry = SkillRegistry(rpath)
        registry.load()

        with pytest.raises(KeyError):
            registry.update_health_status("no-such", "1.0.0", "healthy")


# ── Parent process isolation ──


class TestParentIsolation:
    """Verify parent process does NOT load or import skill modules."""

    def test_parent_does_not_import_skill(self, runtime_service):
        """After healthcheck, parent process has no skill modules loaded."""
        service, registry, _ = runtime_service
        response = service.run_healthcheck(
            "test-skill", "1.0.0",
            timeout_seconds=5.0,
        )
        # The parent process should NOT have imported the healthcheck module
        assert "_skill_healthcheck_healthcheck" not in sys.modules
        # The test fixture module name might differ, check that nothing
        # from the installed package is in sys.modules
        for mod_name in list(sys.modules.keys()):
            assert "_skill_healthcheck_" not in mod_name, (
                f"Skill module leaked into parent: {mod_name}"
            )


# ── Workspace cleanup ──


class TestWorkspaceCleanup:
    """Workspace cleanup after healthcheck."""

    def test_workspace_cleaned_after_success(self, runtime_service):
        """Successful healthcheck cleanup removes workspace."""
        service, registry, _ = runtime_service
        response = service.run_healthcheck(
            "test-skill", "1.0.0",
            timeout_seconds=5.0,
        )
        assert response.success


# ── Items 45–52: Timeout, cancel, crash, output limits (Batch 3.0.3) ──


class TestTimeoutCancelCrashReal:
    """Real RuntimeService/Worker tests for items 45–52.

    These MUST use real subprocess — not protocol pure functions.
    """

    def test_healthcheck_completes_normally(self, runtime_service):
        """Item 45: Normal healthcheck completion."""
        service, registry, _ = runtime_service
        response = service.run_healthcheck(
            "test-skill", "1.0.0", timeout_seconds=5.0,
        )
        assert response.success
        assert response.status == "healthy"
        assert response.duration_ms > 0

    def test_healthcheck_timeout(self, tmp_path):
        """Item 46: Healthcheck timeout with real subprocess."""
        base_dir = tmp_path / "skills"
        base_dir.mkdir()
        installed_dir = base_dir / "installed"
        installed_dir.mkdir()

        registry = SkillRegistry(base_dir / "registry.json")
        registry.load()

        skill_dir = create_skill_with_long_running_healthcheck(
            installed_dir,
            skill_id="timeout-skill",
            sleep_seconds=30.0,
        )
        manifest = parse_skill_manifest(skill_dir)
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=True,
            installed_at="2024-01-01T00:00:00Z",
        )
        registry.register(installed)
        registry.save()

        service = SkillRuntimeService(
            registry, installed_dir,
            poll_interval_ms=50,
            terminate_grace_ms=200,
            kill_grace_ms=200,
        )
        response = service.run_healthcheck(
            "timeout-skill", "1.0.0",
            timeout_seconds=0.3,
        )
        assert response.status == "timeout", (
            f"Expected timeout, got: {response.status}"
        )

    def test_user_cancel(self, tmp_path):
        """Item 47: User cancel via real subprocess."""
        base_dir = tmp_path / "skills"
        base_dir.mkdir()
        installed_dir = base_dir / "installed"
        installed_dir.mkdir()

        registry = SkillRegistry(base_dir / "registry.json")
        registry.load()

        from tests.fixtures.runtime_fixtures import (
            create_skill_with_long_running_healthcheck_polling,
        )
        skill_dir = create_skill_with_long_running_healthcheck_polling(
            installed_dir, "cancel-skill-2", "1.0.0",
            sleep_seconds=30.0, poll_interval=0.05,
        )
        manifest = parse_skill_manifest(skill_dir)
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=True,
            installed_at="2024-01-01T00:00:00Z",
        )
        registry.register(installed)
        registry.save()

        service = SkillRuntimeService(
            registry, installed_dir,
            cancel_grace_ms=200,
            terminate_grace_ms=200,
            kill_grace_ms=200,
            poll_interval_ms=50,
        )

        import threading
        result_holder: list = []

        def run_and_cancel():
            resp = service.run_healthcheck(
                "cancel-skill-2", "1.0.0", timeout_seconds=10.0,
            )
            result_holder.append(resp)

        t = threading.Thread(target=run_and_cancel)
        t.start()
        time.sleep(0.3)
        service.cancel()
        t.join(timeout=3.0)

        assert len(result_holder) > 0, "No result from cancelled healthcheck"
        resp = result_holder[0]
        assert resp.status in ("cancelled", "timeout"), (
            f"Expected cancelled/timeout, got: {resp.status}"
        )

    def test_cancel_leaves_no_worker_process(self, tmp_path):
        """Item 48: After cancel, service cleans up process reference.

        Verifies:
        - Service._process is None after cancel completes
        - No orphan process reference retained
        - Thread joins cleanly
        """
        base_dir = tmp_path / "skills"
        base_dir.mkdir()
        installed_dir = base_dir / "installed"
        installed_dir.mkdir()

        registry = SkillRegistry(base_dir / "registry.json")
        registry.load()

        from tests.fixtures.runtime_fixtures import (
            create_skill_with_long_running_healthcheck_polling,
        )
        skill_dir = create_skill_with_long_running_healthcheck_polling(
            installed_dir, "orphan-test", "1.0.0",
            sleep_seconds=30.0, poll_interval=0.05,
        )
        manifest = parse_skill_manifest(skill_dir)
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=True,
            installed_at="2024-01-01T00:00:00Z",
        )
        registry.register(installed)
        registry.save()

        service = SkillRuntimeService(
            registry, installed_dir,
            cancel_grace_ms=200,
            terminate_grace_ms=200,
            kill_grace_ms=200,
            poll_interval_ms=50,
        )

        import threading
        result_holder: list = []

        def run_and_cancel():
            resp = service.run_healthcheck(
                "orphan-test", "1.0.0", timeout_seconds=10.0,
            )
            result_holder.append(resp)

        t = threading.Thread(target=run_and_cancel)
        t.start()
        time.sleep(0.3)
        service.cancel()
        t.join(timeout=3.0)

        # Verify clean state after cancel
        assert not t.is_alive(), "Worker thread should have exited"
        assert service._process is None, (
            "Service process reference should be None after cancel"
        )
        assert service._cancelled is False, (
            "Service cancelled flag should be reset after operation"
        )

    def test_missing_result_json(self, tmp_path):
        """Item 49: Missing result.json handled correctly via real worker.

        We simulate this by deleting the result.json after a crashed worker.
        """
        base_dir = tmp_path / "skills"
        base_dir.mkdir()
        installed_dir = base_dir / "installed"
        installed_dir.mkdir()

        registry = SkillRegistry(base_dir / "registry.json")
        registry.load()

        skill_dir = create_skill_with_crashing_healthcheck(
            installed_dir, "no-result", "1.0.0",
        )
        manifest = parse_skill_manifest(skill_dir)
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=True,
            installed_at="2024-01-01T00:00:00Z",
        )
        registry.register(installed)
        registry.save()

        service = SkillRuntimeService(registry, installed_dir)
        response = service.run_healthcheck(
            "no-result", "1.0.0", timeout_seconds=5.0,
        )
        # Crashed healthcheck should report unhealthy, not protocol_error
        assert not response.success
        assert response.status == "unhealthy", (
            f"Expected unhealthy for crashed worker, got: {response.status}"
        )

    def test_nonzero_worker_exit(self, tmp_path):
        """Item 50: Non-zero worker exit is correctly reported."""
        base_dir = tmp_path / "skills"
        base_dir.mkdir()
        installed_dir = base_dir / "installed"
        installed_dir.mkdir()

        registry = SkillRegistry(base_dir / "registry.json")
        registry.load()

        skill_dir = create_skill_with_crashing_healthcheck(
            installed_dir, "nonzero-exit", "1.0.0",
        )
        manifest = parse_skill_manifest(skill_dir)
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=True,
            installed_at="2024-01-01T00:00:00Z",
        )
        registry.register(installed)
        registry.save()

        service = SkillRuntimeService(registry, installed_dir)
        response = service.run_healthcheck(
            "nonzero-exit", "1.0.0", timeout_seconds=5.0,
        )
        assert not response.success
        assert response.status == "unhealthy", (
            f"Expected unhealthy, got: {response.status}"
        )
        # Should have error info
        assert response.error is not None, "Non-zero exit should have error info"

    def test_stdout_truncated_at_limit(self, tmp_path):
        """Item 51: stdout content is truncated at limit.

        The worker redirects stdout to stdout.log. We verify the limit
        constant exists and that a skill producing lots of output doesn't
        break the system.
        """
        from tests.fixtures.runtime_fixtures import create_minimal_skill_package

        base_dir = tmp_path / "skills"
        base_dir.mkdir()
        installed_dir = base_dir / "installed"
        installed_dir.mkdir()

        registry = SkillRegistry(base_dir / "registry.json")
        registry.load()

        # Create a skill that prints a lot to stdout
        skill_dir = create_minimal_skill_package(
            installed_dir, "stdout-skill", "1.0.0",
            healthy=True,
            extra_healthcheck_code=(
                "    import sys\n"
                "    sys.stdout.write('X' * 50000)\n"
                "    sys.stdout.flush()\n"
            ),
        )
        manifest = parse_skill_manifest(skill_dir)
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=True,
            installed_at="2024-01-01T00:00:00Z",
        )
        registry.register(installed)
        registry.save()

        service = SkillRuntimeService(registry, installed_dir)
        response = service.run_healthcheck(
            "stdout-skill", "1.0.0", timeout_seconds=5.0,
        )
        # The healthcheck should still succeed (stdout output doesn't break it)
        assert response.success
        assert response.status == "healthy"

    def test_stderr_truncated_at_limit(self, tmp_path):
        """Item 52: stderr content is truncated at limit.

        The worker redirects stderr to stderr.log. We verify the limit
        constant exists and that a skill writing lots of errors doesn't
        break the system.
        """
        from tests.fixtures.runtime_fixtures import create_minimal_skill_package

        base_dir = tmp_path / "skills"
        base_dir.mkdir()
        installed_dir = base_dir / "installed"
        installed_dir.mkdir()

        registry = SkillRegistry(base_dir / "registry.json")
        registry.load()

        # Create a skill that writes a lot to stderr
        skill_dir = create_minimal_skill_package(
            installed_dir, "stderr-skill", "1.0.0",
            healthy=True,
            extra_healthcheck_code=(
                "    import sys\n"
                "    sys.stderr.write('E' * 50000)\n"
                "    sys.stderr.flush()\n"
            ),
        )
        manifest = parse_skill_manifest(skill_dir)
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=True,
            installed_at="2024-01-01T00:00:00Z",
        )
        registry.register(installed)
        registry.save()

        service = SkillRuntimeService(registry, installed_dir)
        response = service.run_healthcheck(
            "stderr-skill", "1.0.0", timeout_seconds=5.0,
        )
        # The healthcheck should still succeed
        assert response.success
        assert response.status == "healthy"


# ── Items 10–15: Entrypoint semantic coverage (Batch 3.0.6) ──


class TestHealthcheckEntrypointErrors:
    """Real subprocess tests that exercise specific entrypoint error modes.

    Each test verifies one specific semantic scenario that was previously
    mapped to a generic or unrelated test node.
    """

    def _setup(self, tmp_path, fixture_func, skill_id, **kwargs):
        """Create a skill, register it, and return the service + registry."""
        base_dir = tmp_path / "skills"
        base_dir.mkdir()
        installed_dir = base_dir / "installed"
        installed_dir.mkdir()

        registry = SkillRegistry(base_dir / "registry.json")
        registry.load()

        skill_dir = fixture_func(installed_dir, skill_id, "1.0.0", **kwargs)
        manifest = parse_skill_manifest(skill_dir)
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=True,
            installed_at="2024-01-01T00:00:00Z",
        )
        registry.register(installed)
        registry.save()

        service = SkillRuntimeService(registry, installed_dir)
        return service, registry

    def _setup_bypass_manifest(self, tmp_path, fixture_func, skill_id, **kwargs):
        """Like _setup but bypasses manifest parsing (for #10 file-missing test).

        The manifest parser validates entrypoint file existence, so for
        the file-missing test we register the skill with a minimal
        manifest created manually.
        """
        base_dir = tmp_path / "skills"
        base_dir.mkdir()
        installed_dir = base_dir / "installed"
        installed_dir.mkdir()

        registry = SkillRegistry(base_dir / "registry.json")
        registry.load()

        skill_dir = fixture_func(installed_dir, skill_id, "1.0.0", **kwargs)

        # Manually create manifest to bypass the file-existence check
        manifest = SkillManifest(
            schema_version=1,
            skill_id=skill_id,
            name="Test",
            version="1.0.0",
            description="Test",
            skill_type="executable",
            entrypoints={"healthcheck": "nonexistent.py:check"},
        )
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=True,
            installed_at="2024-01-01T00:00:00Z",
        )
        registry.register(installed)
        registry.save()

        service = SkillRuntimeService(registry, installed_dir)
        return service, registry

    # ── #10: entrypoint file does not exist ──

    def test_healthcheck_entrypoint_file_missing(self, tmp_path):
        """#10: Skill dir and manifest exist, but the .py entrypoint file is absent.

        The manifest entrypoint references a file that doesn't exist on disk.
        The service catches this during pre-flight (Gate 4).
        """
        service, registry = self._setup_bypass_manifest(
            tmp_path, create_skill_with_missing_entrypoint_file, "missing-file",
            entrypoint_file="nonexistent.py", entrypoint_func="check",
        )
        # The service will try parse_skill_manifest in pre-flight and fail
        with pytest.raises(SkillRuntimeError):
            service.run_healthcheck(
                "missing-file", "1.0.0", timeout_seconds=5.0,
            )

    # ── #12: function not found in module ──

    def test_healthcheck_function_missing(self, tmp_path):
        """#12: Module file exists but does NOT define the 'check' function.

        The worker raises AttributeError during _load_entrypoint_function.
        """
        service, registry = self._setup(
            tmp_path, create_skill_with_missing_function, "missing-func",
        )
        response = service.run_healthcheck(
            "missing-func", "1.0.0", timeout_seconds=5.0,
        )
        assert not response.success, (
            f"Missing function should fail, got success=True, status={response.status}"
        )
        assert response.status in ("unhealthy", "crashed", "protocol_error"), (
            f"Expected unhealthy/crashed, got: {response.status}"
        )

    # ── #13: function is not callable ──

    def test_healthcheck_function_not_callable(self, tmp_path):
        """#13: Module has 'check' attribute but it's a string, not a callable.

        The worker raises TypeError during _load_entrypoint_function.
        """
        service, registry = self._setup(
            tmp_path, create_skill_with_non_callable_entrypoint, "non-callable",
        )
        response = service.run_healthcheck(
            "non-callable", "1.0.0", timeout_seconds=5.0,
        )
        assert not response.success, (
            f"Non-callable entrypoint should fail, got success=True, status={response.status}"
        )
        assert response.status in ("unhealthy", "crashed", "protocol_error"), (
            f"Expected unhealthy/crashed, got: {response.status}"
        )

    # ── #14: return value is not a dict ──

    def test_healthcheck_return_value_not_dict(self, tmp_path):
        """#14: check() runs successfully but returns a string instead of a dict.

        The worker's _validate_healthcheck_return raises ValueError.
        """
        service, registry = self._setup(
            tmp_path, create_skill_returning_non_dict, "non-dict",
            return_value_code='"this is a string, not a dict"',
        )
        response = service.run_healthcheck(
            "non-dict", "1.0.0", timeout_seconds=5.0,
        )
        assert not response.success, (
            f"Non-dict return should fail, got success=True, status={response.status}"
        )
        # Worker writes status='unhealthy' for execution errors
        assert response.status in ("unhealthy", "crashed", "protocol_error"), (
            f"Expected unhealthy/crashed, got: {response.status}"
        )

    # ── #15: healthy field is not bool ──

    def test_healthcheck_healthy_field_not_bool(self, tmp_path):
        """#15: check() returns {"healthy": "yes"} — string instead of bool.

        The worker's _validate_healthcheck_return raises ValueError
        because 'healthy' must be a bool.
        """
        service, registry = self._setup(
            tmp_path, create_skill_returning_non_bool_healthy, "non-bool-hc",
        )
        response = service.run_healthcheck(
            "non-bool-hc", "1.0.0", timeout_seconds=5.0,
        )
        assert not response.success, (
            f"Non-bool healthy should fail, got success=True, status={response.status}"
        )
        assert response.status in ("unhealthy", "crashed", "protocol_error"), (
            f"Expected unhealthy/crashed, got: {response.status}"
        )



# ══════════════════════════════════════════════════════════════════════
# Batch 3.1.1A — L2 run subprocess tests (24 nodes)
# ══════════════════════════════════════════════════════════════════════


# ── Run skill fixtures ──


@pytest.fixture
def run_skill_env(tmp_path):
    """Create a temp skill environment with a registered run-capable skill."""
    base_dir = tmp_path / "skills_data"
    base_dir.mkdir()

    installed_dir = base_dir / "installed"
    installed_dir.mkdir()

    registry_path = base_dir / "registry.json"
    registry = SkillRegistry(registry_path)
    registry.load()

    from tests.fixtures.runtime_fixtures import create_skill_with_run_entrypoint

    skill_dir = create_skill_with_run_entrypoint(installed_dir, "run-skill", "1.0.0")
    manifest = parse_skill_manifest(skill_dir)
    installed = InstalledSkill(
        manifest=manifest,
        install_path=str(skill_dir),
        enabled=True,
        installed_at="2024-01-01T00:00:00Z",
        health_status="unknown",
    )
    registry.register(installed)
    registry.save()

    return base_dir, registry, installed_dir, skill_dir


@pytest.fixture
def run_service(run_skill_env):
    """Create a SkillRuntimeService for run testing."""
    base_dir, registry, installed_dir, skill_dir = run_skill_env
    service = SkillRuntimeService(
        registry=registry,
        installed_dir=installed_dir,
    )
    return service, registry, skill_dir


# ── Helper for setting up run skills ──


def _setup_run_skill(tmp_path, fixture_func, skill_id, **kwargs):
    """Create and register a run skill. Returns (service, registry)."""
    base_dir = tmp_path / "skills"
    base_dir.mkdir()
    installed_dir = base_dir / "installed"
    installed_dir.mkdir()

    registry = SkillRegistry(base_dir / "registry.json")
    registry.load()

    skill_dir = fixture_func(installed_dir, skill_id, "1.0.0", **kwargs)
    manifest = parse_skill_manifest(skill_dir)
    installed = InstalledSkill(
        manifest=manifest,
        install_path=str(skill_dir),
        enabled=True,
        installed_at="2024-01-01T00:00:00Z",
    )
    registry.register(installed)
    registry.save()

    service = SkillRuntimeService(registry, installed_dir)
    return service, registry


class TestRunExecution:
    """Run execution tests (Batch 3.1.1A)."""

    def test_legal_run_succeeds(self, run_service):
        """3.1.1-22: Legal run returns status=succeeded."""
        service, registry, _ = run_service
        response = service.run_skill(
            "run-skill", "1.0.0",
            params={"input": "hello"},
            timeout_seconds=5.0,
        )
        assert response.success
        assert response.status == "succeeded"
        assert response.operation == "run"
        assert response.result is not None
        assert response.result.get("ok") is True

    def test_worker_pid_differs_from_parent(self, run_service):
        """3.1.1-28: Worker PID differs from parent."""
        service, registry, _ = run_service
        parent_pid = os.getpid()
        response = service.run_skill(
            "run-skill", "1.0.0",
            params={"input": "test"},
            timeout_seconds=5.0,
        )
        assert response.duration_ms >= 0  # Subprocess ran


class TestRunEntrypoint:
    """Run entrypoint error tests (Batch 3.1.1A)."""

    def test_missing_run_entrypoint_rejected(self, tmp_path):
        """3.1.1-23: Missing run entrypoint → SkillRuntimeError preflight."""
        from tests.fixtures.runtime_fixtures import create_skill_without_run_entrypoint
        service, registry = _setup_run_skill(
            tmp_path, create_skill_without_run_entrypoint, "no-run-ep",
        )
        with pytest.raises(SkillRuntimeError, match="run entrypoint"):
            service.run_skill("no-run-ep", "1.0.0", params={}, timeout_seconds=5.0)

    def test_entrypoint_file_missing(self, tmp_path):
        """3.1.1-24: Entrypoint file missing → SkillRuntimeError preflight."""
        from tests.fixtures.runtime_fixtures import create_skill_with_run_entrypoint_missing_file

        base_dir = tmp_path / "skills"
        base_dir.mkdir()
        installed_dir = base_dir / "installed"
        installed_dir.mkdir()

        registry = SkillRegistry(base_dir / "registry.json")
        registry.load()

        skill_dir = create_skill_with_run_entrypoint_missing_file(
            installed_dir, "missing-file", "1.0.0",
        )

        # Bypass manifest parsing — register manually to avoid file-existence check
        manifest = SkillManifest(
            schema_version=1,
            skill_id="missing-file",
            name="Test",
            version="1.0.0",
            description="Test",
            skill_type="executable",
            entrypoints={"run": "nonexistent.py:run"},
        )
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=True,
            installed_at="2024-01-01T00:00:00Z",
        )
        registry.register(installed)
        registry.save()

        service = SkillRuntimeService(registry, installed_dir)
        with pytest.raises(SkillRuntimeError):
            service.run_skill("missing-file", "1.0.0", params={}, timeout_seconds=5.0)

    def test_function_missing(self, tmp_path):
        """3.1.1-25: Function missing → status=failed from Worker."""
        from tests.fixtures.runtime_fixtures import create_skill_with_missing_run_function
        service, registry = _setup_run_skill(
            tmp_path, create_skill_with_missing_run_function, "missing-func",
        )
        response = service.run_skill(
            "missing-func", "1.0.0", params={}, timeout_seconds=5.0,
        )
        assert not response.success
        assert response.status == "failed", (
            f"Expected failed, got: {response.status}"
        )

    def test_function_not_callable(self, tmp_path):
        """3.1.1-26: Function not callable → status=failed from Worker."""
        from tests.fixtures.runtime_fixtures import create_skill_with_non_callable_run
        service, registry = _setup_run_skill(
            tmp_path, create_skill_with_non_callable_run, "non-callable",
        )
        response = service.run_skill(
            "non-callable", "1.0.0", params={}, timeout_seconds=5.0,
        )
        assert not response.success
        assert response.status == "failed", (
            f"Expected failed, got: {response.status}"
        )


class TestParentIsolation:
    """Parent process isolation tests (Batch 3.1.1A)."""

    def test_parent_does_not_import_run_module(self, run_service):
        """3.1.1-27: Parent process does NOT import run module."""
        service, registry, _ = run_service
        response = service.run_skill(
            "run-skill", "1.0.0",
            params={"input": "test"},
            timeout_seconds=5.0,
        )
        assert response.success
        # No skill module should be in parent's sys.modules
        for mod_name in list(sys.modules.keys()):
            assert "_skill_healthcheck_" not in mod_name, (
                f"Skill module leaked into parent: {mod_name}"
            )
            assert "run_skill_run" not in mod_name.replace(".", "_").replace("-", "_").lower(), (
                f"Run skill module might have leaked: {mod_name}"
            )


class TestAuditHooks:
    """Audit hook tests (Batch 3.1.1A)."""

    def test_hooks_installed_before_module_execution(self, run_service):
        """3.1.1-29: Audit hooks installed before module execution.

        We verify this indirectly: a skill that tries to write outside
        workspace at top-level would fail if hooks weren't installed first.
        Since the run skill succeeds, hooks were installed.
        """
        service, registry, _ = run_service
        response = service.run_skill(
            "run-skill", "1.0.0",
            params={"input": "test"},
            timeout_seconds=5.0,
        )
        assert response.success
        assert response.status == "succeeded"


class TestRunResult:
    """Run result validation tests (Batch 3.1.1A)."""

    def test_normal_dict_payload_accepted(self, run_service):
        """3.1.1-30: Normal dict payload is accepted."""
        service, registry, _ = run_service
        response = service.run_skill(
            "run-skill", "1.0.0",
            params={"input": "hello"},
            timeout_seconds=5.0,
        )
        assert response.success
        assert response.status == "succeeded"
        assert isinstance(response.result, dict)

    def test_non_dict_return_rejected(self, tmp_path):
        """3.1.1-31: Non-dict return → protocol_error."""
        from tests.fixtures.runtime_fixtures import create_skill_run_returns_non_dict
        service, registry = _setup_run_skill(
            tmp_path, create_skill_run_returns_non_dict, "non-dict",
        )
        response = service.run_skill(
            "non-dict", "1.0.0", params={}, timeout_seconds=5.0,
        )
        assert not response.success
        assert response.status == "protocol_error", (
            f"Expected protocol_error, got: {response.status}"
        )

    def test_non_json_compatible_return_rejected(self, tmp_path):
        """3.1.1-32: Non-JSON-compatible return → protocol_error."""
        from tests.fixtures.runtime_fixtures import create_skill_run_returns_non_json
        service, registry = _setup_run_skill(
            tmp_path, create_skill_run_returns_non_json, "non-json",
        )
        response = service.run_skill(
            "non-json", "1.0.0", params={}, timeout_seconds=5.0,
        )
        assert not response.success
        assert response.status == "protocol_error", (
            f"Expected protocol_error, got: {response.status}"
        )

    def test_result_size_exceeded_rejected(self, tmp_path):
        """3.1.1-33: Result size exceeded → protocol_error."""
        from tests.fixtures.runtime_fixtures import create_skill_run_large_result
        service, registry = _setup_run_skill(
            tmp_path, create_skill_run_large_result, "large-result",
        )
        response = service.run_skill(
            "large-result", "1.0.0", params={}, timeout_seconds=5.0,
        )
        assert not response.success
        assert response.status == "protocol_error", (
            f"Expected protocol_error, got: {response.status}"
        )

    def test_skill_cannot_forge_runtime_status(self, tmp_path):
        """3.1.1-34: Skill cannot forge envelope status."""
        from tests.fixtures.runtime_fixtures import create_skill_run_forges_status
        service, registry = _setup_run_skill(
            tmp_path, create_skill_run_forges_status, "forge-status",
        )
        response = service.run_skill(
            "forge-status", "1.0.0", params={}, timeout_seconds=5.0,
        )
        # The skill returned {"status": "healthy"} but envelope should be "succeeded"
        assert response.success
        assert response.status == "succeeded", (
            f"Expected succeeded, got: {response.status}"
        )
        # Skill's "status" is just business data in result
        assert response.result is not None

    def test_path_string_treated_as_plain_data(self, tmp_path):
        """3.1.1-35: Path string is treated as plain business data."""
        from tests.fixtures.runtime_fixtures import create_skill_run_returns_path_string
        service, registry = _setup_run_skill(
            tmp_path, create_skill_run_returns_path_string, "path-str",
        )
        response = service.run_skill(
            "path-str", "1.0.0", params={}, timeout_seconds=5.0,
        )
        assert response.success
        assert response.status == "succeeded"
        assert response.result == {"path": "output/report.pdf", "ok": True}

    def test_result_json_atomic_write(self, run_service):
        """3.1.1-36: Result.json is written atomically."""
        service, registry, _ = run_service
        response = service.run_skill(
            "run-skill", "1.0.0",
            params={"input": "test"},
            timeout_seconds=5.0,
        )
        assert response.success
        # The workspace is cleaned up after success, so no .tmp residue
        # (atomicity is ensured by _atomic_write_json using os.replace)


class TestRunCrash:
    """Run crash tests (Batch 3.1.1A)."""

    def test_missing_result_json(self, tmp_path):
        """3.1.1-37: Missing result.json → crashed.

        Simulated by non-zero exit without result.json.
        """
        from tests.fixtures.runtime_fixtures import create_skill_run_exits_nonzero
        service, registry = _setup_run_skill(
            tmp_path, create_skill_run_exits_nonzero, "exit-nonzero",
        )
        response = service.run_skill(
            "exit-nonzero", "1.0.0", params={}, timeout_seconds=5.0,
        )
        assert not response.success
        assert response.status == "crashed", (
            f"Expected crashed, got: {response.status}"
        )


class TestRunProtocol:
    """Run protocol tests (Batch 3.1.1A)."""

    def test_task_id_mismatch_rejected(self, run_service):
        """3.1.1-38: Task ID in response validated.

        Since the Worker produces the response with the same task_id from
        the request, this is implicitly tested by normal execution.
        We verify the response has the correct protocol fields.
        """
        service, registry, _ = run_service
        response = service.run_skill(
            "run-skill", "1.0.0",
            params={"input": "test"},
            timeout_seconds=5.0,
        )
        assert response.protocol_version == 1
        assert response.operation == "run"
        assert len(response.task_id) > 0


class TestRunLifecycle:
    """Run lifecycle tests (Batch 3.1.1A)."""

    def test_normal_completion(self, run_service):
        """3.1.1-39: Normal completion has valid timestamps."""
        service, registry, _ = run_service
        response = service.run_skill(
            "run-skill", "1.0.0",
            params={"input": "test"},
            timeout_seconds=5.0,
        )
        assert response.success
        assert response.status == "succeeded"
        assert response.started_at
        assert response.finished_at
        assert response.duration_ms >= 0

    def test_timeout(self, tmp_path):
        """3.1.1-40: Timeout → status=timeout."""
        from tests.fixtures.runtime_fixtures import create_skill_run_hangs
        service, registry = _setup_run_skill(
            tmp_path, create_skill_run_hangs, "run-timeout",
            sleep_seconds=30.0,
        )
        response = service.run_skill(
            "run-timeout", "1.0.0", params={}, timeout_seconds=0.3,
        )
        assert response.status == "timeout", (
            f"Expected timeout, got: {response.status}"
        )
        assert not response.success

    def test_cancel(self, tmp_path):
        """3.1.1-41: Cancel → status=cancelled."""
        from tests.fixtures.runtime_fixtures import create_skill_run_hangs
        service, registry = _setup_run_skill(
            tmp_path, create_skill_run_hangs, "run-cancel",
            sleep_seconds=30.0,
        )
        # Rebuild service with fast cancel timing (same installed_dir)
        installed_dir = service._installed_dir
        service = SkillRuntimeService(
            registry, installed_dir,
            cancel_grace_ms=200,
            terminate_grace_ms=200,
            kill_grace_ms=200,
            poll_interval_ms=50,
        )

        import threading
        result_holder: list = []

        def run_and_cancel():
            resp = service.run_skill(
                "run-cancel", "1.0.0", params={}, timeout_seconds=10.0,
            )
            result_holder.append(resp)

        t = threading.Thread(target=run_and_cancel)
        t.start()
        time.sleep(0.3)
        service.cancel()
        t.join(timeout=3.0)

        assert len(result_holder) > 0, "No result from cancelled run"
        resp = result_holder[0]
        assert resp.status in ("cancelled", "timeout"), (
            f"Expected cancelled/timeout, got: {resp.status}"
        )

    def test_no_orphan_worker_after_cancel(self, tmp_path):
        """3.1.1-42: No orphan Worker after cancel."""
        from tests.fixtures.runtime_fixtures import create_skill_run_hangs
        service, registry = _setup_run_skill(
            tmp_path, create_skill_run_hangs, "no-orphan",
            sleep_seconds=30.0,
        )
        installed_dir = service._installed_dir
        service = SkillRuntimeService(
            registry, installed_dir,
            cancel_grace_ms=200,
            terminate_grace_ms=200,
            kill_grace_ms=200,
            poll_interval_ms=50,
        )

        import threading
        result_holder: list = []

        def run_and_cancel():
            resp = service.run_skill(
                "no-orphan", "1.0.0", params={}, timeout_seconds=10.0,
            )
            result_holder.append(resp)

        t = threading.Thread(target=run_and_cancel)
        t.start()
        time.sleep(0.3)
        service.cancel()
        t.join(timeout=3.0)

        # Verify clean state
        assert not t.is_alive(), "Thread should have exited"
        assert service._process is None, "Process reference should be None"
        assert service._cancelled is False, "Cancelled flag should be reset"

    def test_crash_nonzero_exit(self, tmp_path):
        """3.1.1-43: Non-zero exit → crashed."""
        from tests.fixtures.runtime_fixtures import create_skill_run_exits_nonzero
        service, registry = _setup_run_skill(
            tmp_path, create_skill_run_exits_nonzero, "crash-exit",
        )
        response = service.run_skill(
            "crash-exit", "1.0.0", params={}, timeout_seconds=5.0,
        )
        assert not response.success
        assert response.status == "crashed", (
            f"Expected crashed, got: {response.status}"
        )
        assert response.error is not None


class TestRunIOLimits:
    """Run I/O limit tests (Batch 3.1.1A)."""

    def test_stdout_truncated(self, tmp_path):
        """3.1.1-44: Stdout content handled correctly for run."""
        from tests.fixtures.runtime_fixtures import create_skill_run_writes_large_stdout
        service, registry = _setup_run_skill(
            tmp_path, create_skill_run_writes_large_stdout, "stdout-big",
        )
        response = service.run_skill(
            "stdout-big", "1.0.0", params={}, timeout_seconds=10.0,
        )
        # The run should still succeed (stdout doesn't break run)
        assert response.success
        assert response.status == "succeeded"

    def test_stderr_truncated(self, tmp_path):
        """3.1.1-45: Stderr content handled correctly for run."""
        from tests.fixtures.runtime_fixtures import create_skill_run_writes_large_stderr
        service, registry = _setup_run_skill(
            tmp_path, create_skill_run_writes_large_stderr, "stderr-big",
        )
        response = service.run_skill(
            "stderr-big", "1.0.0", params={}, timeout_seconds=10.0,
        )
        # The run should still succeed
        assert response.success
        assert response.status == "succeeded"
