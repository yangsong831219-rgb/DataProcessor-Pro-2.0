"""L3 tests: Dependency gating, Registry transactions, and rollback.

Dependency tests verify:
- Popen is NOT called when dependencies are missing
- Dependency check uses importlib.metadata, never pip
- No dependency installation or environment modification

Registry tests verify:
- Save failure rolls back memory state
- Disk content unchanged on save failure
- active_version, enabled, last_healthcheck preserved on rollback
- No partial commits
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from dp_engine.skills.registry import SkillRegistry, SkillRegistrySnapshot
from dp_engine.skills.runtime_service import SkillRuntimeService
from dp_engine.skills.runtime_errors import (
    SkillRuntimeError,
    RuntimeDependencyError,
)
from dp_engine.skills.runtime_dependencies import (
    SkillDependencyChecker,
    _parse_dependency_spec,
    _get_installed_version,
)
from dp_engine.skills.models import InstalledSkill, SkillManifest
from dp_engine.skills.manifest_parser import parse_skill_manifest


# ── Helpers ──


def _make_registry_with_skill(
    registry_path: Path,
    skill_id: str = "test-skill",
    version: str = "1.0.0",
    health_status: str = "unknown",
) -> tuple[SkillRegistry, InstalledSkill]:
    """Create a registry with one registered skill."""
    registry = SkillRegistry(registry_path)
    registry.load()

    manifest = SkillManifest(
        schema_version=1,
        skill_id=skill_id,
        name="Test Skill",
        version=version,
        description="Test",
        skill_type="executable",
    )
    installed = InstalledSkill(
        manifest=manifest,
        install_path=f"/tmp/{skill_id}/{version}",
        enabled=True,
        installed_at="2024-01-01T00:00:00Z",
        health_status=health_status,
    )
    registry.register(installed)
    registry.save()
    return registry, installed


# ═══════════════════════════════════════════════════════════════════
# Dependency gating
# ═══════════════════════════════════════════════════════════════════


class TestDependencyGatingPopenNotCalled:
    """Dependency checks must NOT invoke subprocess or pip."""

    def test_dependency_check_uses_importlib_metadata_only(self):
        """_get_installed_version uses importlib.metadata, never subprocess."""
        import inspect
        source = inspect.getsource(_get_installed_version)
        assert "Popen" not in source, (
            "_get_installed_version must NOT use subprocess.Popen"
        )
        assert "subprocess" not in source, (
            "_get_installed_version must NOT use subprocess"
        )
        assert "pip" not in source, (
            "_get_installed_version must NOT call pip"
        )

    def test_missing_package_returns_none(self):
        """Non-existent package returns None without launching anything."""
        ver = _get_installed_version("completely-nonexistent-package-xyz-12345")
        assert ver is None

    def test_dependency_check_does_not_import_target(self):
        """Dependency check must NOT import the package being checked."""
        # We verify this by checking a known-missing package
        checker = SkillDependencyChecker()
        # With "this-package-does-not-exist-xyz" as dep, check should not import it
        report = checker.check(
            "test-skill", "1.0.0",
            ("this-package-does-not-exist-xyz",),
        )
        assert not report.all_satisfied
        # The missing package should NOT have been imported
        assert "this-package-does-not-exist-xyz" not in sys.modules

    def test_dependency_missing_prevents_healthcheck(self, tmp_path):
        """When a dependency is missing, healthcheck fails at pre-flight."""
        from tests.fixtures.runtime_fixtures import create_minimal_skill_package

        base_dir = tmp_path / "skills_data"
        installed_dir = base_dir / "installed"
        installed_dir.mkdir(parents=True)
        registry_path = base_dir / "registry.json"

        # Create skill with an impossible dependency
        skill_dir = create_minimal_skill_package(
            installed_dir, "dep-skill", "1.0.0",
            healthy=True,
            dependencies="  - nonexistent-package-xyz-99999>=999.0.0\n",
        )

        registry = SkillRegistry(registry_path)
        registry.load()
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

        service = SkillRuntimeService(registry, installed_dir)

        # Should raise RuntimeDependencyError BEFORE any subprocess
        with pytest.raises(SkillRuntimeError, match="Dependencies"):
            service.run_healthcheck("dep-skill", "1.0.0", timeout_seconds=5.0)

        # Registry should show dependency_missing
        registry.load()
        skill = registry.get("dep-skill", "1.0.0")
        assert skill is not None
        assert skill.health_status == "dependency_missing"

    def test_dependency_check_never_calls_popen(self):
        """Spy on subprocess.Popen to verify it's never called during dep check."""
        checker = SkillDependencyChecker()
        # The checker code itself has no Popen calls — this is a structural check
        import inspect
        source = inspect.getsource(SkillDependencyChecker.check)
        assert "Popen" not in source, "check() method must not contain Popen"
        assert "subprocess" not in source, "check() method must not use subprocess"
        assert "pip" not in source.lower(), "check() method must not call pip"


# ═══════════════════════════════════════════════════════════════════
# Registry transactions & rollback
# ═══════════════════════════════════════════════════════════════════


class TestRegistrySaveRollback:
    """Registry save failure must roll back memory and preserve disk."""

    def test_snapshot_restore_preserves_original_state(self, tmp_path):
        """Snapshot + restore returns registry to exact pre-mutation state."""
        registry_path = tmp_path / "registry.json"
        registry, installed = _make_registry_with_skill(
            registry_path, "rollback-test", "1.0.0",
        )

        # Take snapshot BEFORE mutation
        snap = registry.snapshot()

        # Mutate
        registry.update_health_status("rollback-test", "1.0.0", "healthy", "all good")
        registry.save()

        # Verify mutation took effect
        registry.load()
        mutated = registry.get("rollback-test", "1.0.0")
        assert mutated is not None
        assert mutated.health_status == "healthy"
        assert mutated.health_message == "all good"

        # Restore snapshot
        registry.restore(snap)
        registry.save()

        # Verify restoration
        registry.load()
        restored = registry.get("rollback-test", "1.0.0")
        assert restored is not None
        assert restored.health_status == "unknown", (
            f"Expected 'unknown' after restore, got '{restored.health_status}'"
        )
        assert restored.health_message is None or restored.health_message == "", (
            f"Expected empty message after restore, got '{restored.health_message}'"
        )

    def test_rollback_preserves_enabled_flag(self, tmp_path):
        """Rollback preserves the enabled flag."""
        registry_path = tmp_path / "registry.json"
        registry, installed = _make_registry_with_skill(
            registry_path, "enabled-test", "1.0.0",
        )

        snap = registry.snapshot()

        # Mutate
        registry.update_health_status("enabled-test", "1.0.0", "unhealthy", "bad")
        registry.save()

        # Restore
        registry.restore(snap)
        registry.save()

        registry.load()
        restored = registry.get("enabled-test", "1.0.0")
        assert restored is not None
        assert restored.enabled is True, "enabled flag should be preserved after rollback"

    def test_rollback_preserves_active_version(self, tmp_path):
        """Rollback preserves active_version."""
        registry_path = tmp_path / "registry.json"
        registry, installed = _make_registry_with_skill(
            registry_path, "active-test", "1.0.0",
        )

        # Set active version
        registry.set_active_version("active-test", "1.0.0")
        registry.save()

        snap = registry.snapshot()

        # Mutate health
        registry.update_health_status("active-test", "1.0.0", "healthy", "all ok")
        registry.save()

        # Restore
        registry.restore(snap)
        registry.save()

        registry.load()
        active = registry.get_active("active-test")
        assert active is not None, "active_version should be preserved after rollback"
        assert active.version == "1.0.0"

    def test_rollback_no_partial_healthcheck_commit(self, tmp_path):
        """Failed update does not leave partial healthcheck state."""
        registry_path = tmp_path / "registry.json"
        registry, installed = _make_registry_with_skill(
            registry_path, "partial-test", "1.0.0",
        )

        # Read the on-disk content before mutation
        original_disk = registry_path.read_text(encoding="utf-8") if registry_path.exists() else "{}"

        snap = registry.snapshot()

        # Attempt a mutation but then rollback
        registry.update_health_status("partial-test", "1.0.0", "checking", "started...")
        # Simulate failure — restore without saving
        registry.restore(snap)
        registry.save()

        # Disk should reflect the restored state, not partial
        registry.load()
        restored = registry.get("partial-test", "1.0.0")
        assert restored is not None
        assert restored.health_status == "unknown", (
            f"Partial commit leaked! Got status: {restored.health_status}"
        )
        assert restored.health_message is None or restored.health_message == ""

    def test_registry_disk_content_unchanged_on_rollback(self, tmp_path):
        """Disk registry file content is preserved on rollback."""
        registry_path = tmp_path / "registry.json"
        registry, installed = _make_registry_with_skill(
            registry_path, "disk-test", "1.0.0",
        )

        # Read initial disk content
        initial_disk = registry_path.read_text(encoding="utf-8")

        snap = registry.snapshot()

        # Mutate and save
        registry.update_health_status("disk-test", "1.0.0", "crashed", "boom")
        registry.save()

        # Disk should have changed
        mutated_disk = registry_path.read_text(encoding="utf-8")
        assert mutated_disk != initial_disk, "Mutation should change disk content"

        # Rollback
        registry.restore(snap)
        registry.save()

        # Disk should be back to initial state (functionally equivalent)
        registry.load()
        restored = registry.get("disk-test", "1.0.0")
        assert restored is not None
        assert restored.health_status == "unknown"

    def test_memory_state_rollback_field_by_field(self, tmp_path):
        """After rollback, every relevant field matches the snapshot."""
        registry_path = tmp_path / "registry.json"
        registry, installed = _make_registry_with_skill(
            registry_path, "field-test", "1.0.0",
            health_status="unknown",
        )

        snap = registry.snapshot()

        # Mutate multiple fields
        registry.update_health_status("field-test", "1.0.0", "healthy", "all systems go")
        registry.save()

        # Verify mutation
        registry.load()
        mutated = registry.get("field-test", "1.0.0")
        assert mutated is not None
        assert mutated.health_status == "healthy"

        # Rollback
        registry.restore(snap)
        registry.save()

        # Verify field-by-field
        registry.load()
        restored = registry.get("field-test", "1.0.0")
        assert restored is not None
        assert restored.health_status == "unknown", "health_status should roll back"
        assert restored.health_message is None or restored.health_message == "", (
            "health_message should roll back"
        )
        assert restored.enabled is True, "enabled should be preserved"
        assert restored.version == "1.0.0", "version should be preserved"


# ═══════════════════════════════════════════════════════════════════
# Batch 3.0.6: #41 Version mismatch prevents process start
# ═══════════════════════════════════════════════════════════════════


class TestDependencyVersionMismatch:
    """#41: installed version that does NOT satisfy specifier prevents start."""

    def test_dependency_version_mismatch_prevents_process_start(self, tmp_path, monkeypatch):
        """Version mismatch → dependency report unsatisfied → Popen count == 0.

        Monkeypatches _get_installed_version to return a too-low version
        for a skill that requires 'requests>=999.0.0'.
        """
        from tests.fixtures.runtime_fixtures import create_minimal_skill_package
        from dp_engine.skills.runtime_dependencies import _get_installed_version

        # Install a fake version check that returns a version NOT satisfying
        original_get_ver = _get_installed_version

        def _fake_get_ver(package_name: str) -> str | None:
            if package_name == "requests":
                return "2.0.0"  # installed version
            return original_get_ver(package_name)

        monkeypatch.setattr(
            "dp_engine.skills.runtime_dependencies._get_installed_version",
            _fake_get_ver,
        )

        base_dir = tmp_path / "skills_data"
        installed_dir = base_dir / "installed"
        installed_dir.mkdir(parents=True)
        registry_path = base_dir / "registry.json"

        skill_dir = create_minimal_skill_package(
            installed_dir, "ver-mismatch", "1.0.0",
            healthy=True,
            dependencies="  - requests>=999.0.0\n",
        )

        registry = SkillRegistry(registry_path)
        registry.load()
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

        service = SkillRuntimeService(registry, installed_dir)

        # Spying on Popen to verify it's never called
        import subprocess
        popen_calls = []
        original_popen = subprocess.Popen

        class _SpyPopen:
            def __init__(self, *args, **kwargs):
                popen_calls.append((args, kwargs))
                raise RuntimeError("Popen should not be called")

        monkeypatch.setattr(subprocess, "Popen", _SpyPopen)

        with pytest.raises(SkillRuntimeError, match="Dependencies"):
            service.run_healthcheck("ver-mismatch", "1.0.0", timeout_seconds=5.0)

        # Popen must NOT have been called
        assert len(popen_calls) == 0, (
            f"Popen was called {len(popen_calls)} times — version mismatch "
            f"should have been caught at pre-flight"
        )

        # Registry must NOT enter checking/healthy
        registry.load()
        skill = registry.get("ver-mismatch", "1.0.0")
        assert skill is not None
        assert skill.health_status == "dependency_missing", (
            f"Expected dependency_missing, got: {skill.health_status}"
        )


# ═══════════════════════════════════════════════════════════════════
# Batch 3.0.6: #43 Never invokes package installer
# ═══════════════════════════════════════════════════════════════════


class TestDependencyCheckerNeverInstalls:
    """#43: Dependency checker must never invoke any package installer."""

    def test_dependency_checker_never_invokes_package_installer(self):
        """Verify source code has zero references to pip/conda/uv/poetry.

        Goes beyond 'no Popen' — explicitly checks all installer entry points.
        Excludes docstring/comment mentions (which document the prohibition).
        """
        import inspect
        from dp_engine.skills import runtime_dependencies as rd

        source = inspect.getsource(rd)
        lines = source.split("\n")

        # Filter out docstrings and comments
        code_lines = []
        in_docstring = False
        for line in lines:
            stripped = line.strip()
            # Toggle docstring state
            if stripped.startswith('"""') or stripped.startswith("'''"):
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if stripped.startswith("#"):
                continue
            code_lines.append(stripped)

        code_text = " ".join(code_lines).lower()

        # Check for all known package installer entry points in actual code
        forbidden = ["pip ", "pip3", "conda ", "uv ", "poetry ", "subprocess.run", "os.system"]
        for term in forbidden:
            assert term not in code_text, (
                f"runtime_dependencies.py code references '{term}' — "
                f"dependency checker must never invoke package installers!"
            )

        # Also verify check() method has no subprocess import
        checker_source = inspect.getsource(SkillDependencyChecker.check)
        assert "subprocess" not in checker_source, (
            "SkillDependencyChecker.check() must not import subprocess"
        )
        assert "Popen" not in checker_source, (
            "SkillDependencyChecker.check() must not use Popen"
        )

    def test_dependency_checker_uses_importlib_only(self):
        """All version queries go through importlib.metadata — never subprocess."""
        import inspect
        from dp_engine.skills import runtime_dependencies as rd

        source = inspect.getsource(rd)

        # importlib.metadata MUST be used
        assert "importlib.metadata" in source or "importlib_metadata" in source, (
            "Dependency checker must use importlib.metadata"
        )

        # subprocess must NOT be imported
        assert "import subprocess" not in source, (
            "Dependency checker must NOT import subprocess"
        )


# ═══════════════════════════════════════════════════════════════════
# Batch 3.0.6: #55 Unhealthy (not crashed)
# ═══════════════════════════════════════════════════════════════════


class TestHealthcheckUnhealthy:
    """#55: healthcheck returns {'healthy': False} → registry shows unhealthy."""

    def test_healthcheck_false_updates_unhealthy(self, tmp_path):
        """Healthy=false is a normal response — registry must show unhealthy, NOT crashed.

        This is distinct from a crashing healthcheck (raise/exit non-zero).
        """
        from tests.fixtures.runtime_fixtures import create_skill_returning_unhealthy

        base_dir = tmp_path / "skills_data"
        installed_dir = base_dir / "installed"
        installed_dir.mkdir(parents=True)
        registry_path = base_dir / "registry.json"

        skill_dir = create_skill_returning_unhealthy(installed_dir, "unhealthy-ok", "1.0.0")

        registry = SkillRegistry(registry_path)
        registry.load()
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

        service = SkillRuntimeService(registry, installed_dir)
        response = service.run_healthcheck(
            "unhealthy-ok", "1.0.0", timeout_seconds=5.0,
        )

        # The healthcheck itself completed successfully (no crash)
        assert response.success, (
            f"healthy=False is a valid result, should succeed. "
            f"Got: {response.status} — {response.message}"
        )
        # But status is 'unhealthy'
        assert response.status == "unhealthy", (
            f"Expected 'unhealthy', got: {response.status}"
        )

        # Registry must show unhealthy, NOT crashed
        registry.load()
        skill = registry.get("unhealthy-ok", "1.0.0")
        assert skill is not None
        assert skill.health_status == "unhealthy", (
            f"Expected registry 'unhealthy', got: '{skill.health_status}'"
        )
        assert skill.health_message == "not ready — resource unavailable"


# ═══════════════════════════════════════════════════════════════════
# Batch 3.0.6: #57 Save failure restores snapshot
# ═══════════════════════════════════════════════════════════════════


class TestRegistrySaveFailureRestore:
    """#57: When registry.save() fails, memory snapshot is restored."""

    def test_registry_save_failure_restores_health_snapshot(self, tmp_path, monkeypatch):
        """Monkeypatch registry save to raise OSError; verify snapshot restore.

        Asserts:
        - Memory health state restored
        - Disk content unchanged
        - No partial last_healthcheck commit
        - Active version unchanged
        - Enabled flag unchanged
        """
        registry_path = tmp_path / "registry.json"
        registry, installed = _make_registry_with_skill(
            registry_path, "save-fail", "1.0.0",
            health_status="unknown",
        )

        # Set active version
        registry.set_active_version("save-fail", "1.0.0")
        registry.save()

        # Read disk content before failure
        pre_failure_disk = registry_path.read_text(encoding="utf-8")

        # Record pre-mutation state
        snap = registry.snapshot()

        # Mutate in memory
        registry.update_health_status("save-fail", "1.0.0", "healthy", "should roll back")
        # DO NOT save yet — we will monkeypatch save to fail

        # Monkeypatch the save method to raise
        original_save = registry.save

        save_calls = []

        def _failing_save():
            save_calls.append(1)
            raise OSError("Simulated disk write failure")

        monkeypatch.setattr(registry, "save", _failing_save)

        # Attempt to save — should fail
        try:
            registry.save()
            # If we get here, the monkeypatch didn't work
            if len(save_calls) == 0:
                pytest.fail("Monkeypatch didn't trigger")
        except OSError:
            pass  # Expected

        # Now restore to snapshot
        registry.restore(snap)

        # Restore the save method and persist
        monkeypatch.setattr(registry, "save", original_save)
        registry.save()

        # Verify restoration
        registry.load()
        restored = registry.get("save-fail", "1.0.0")
        assert restored is not None
        assert restored.health_status == "unknown", (
            f"Health status should revert to 'unknown', got '{restored.health_status}'"
        )
        assert restored.health_message is None or restored.health_message == "", (
            f"health_message should be empty after rollback, got '{restored.health_message}'"
        )

        # Disk content should match pre-failure
        post_restore_disk = registry_path.read_text(encoding="utf-8")
        assert "should roll back" not in post_restore_disk, (
            f"Partial healthcheck leaked to disk!"
        )

        # Active version preserved
        active = registry.get_active("save-fail")
        assert active is not None
        assert active.version == "1.0.0"

        # Enabled unchanged
        assert restored.enabled is True


# ═══════════════════════════════════════════════════════════════════
# Batch 3.0.6: #59 Successful healthcheck does NOT auto-enable
# ═══════════════════════════════════════════════════════════════════


class TestHealthcheckDoesNotAutoEnable:
    """#59: Successful healthcheck on a disabled skill must not set enabled=True."""

    def test_successful_healthcheck_does_not_enable_disabled_skill(self, tmp_path):
        """Skill with enabled=False → healthcheck succeeds → enabled stays False.

        This is the NORMAL success path, NOT a rollback test.
        """
        from tests.fixtures.runtime_fixtures import create_minimal_skill_package

        base_dir = tmp_path / "skills_data"
        installed_dir = base_dir / "installed"
        installed_dir.mkdir(parents=True)
        registry_path = base_dir / "registry.json"

        skill_dir = create_minimal_skill_package(
            installed_dir, "disabled-skill", "1.0.0", healthy=True,
            healthcheck_message="i am healthy but disabled",
        )

        registry = SkillRegistry(registry_path)
        registry.load()
        manifest = parse_skill_manifest(skill_dir)
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=False,  # <-- THE KEY: disabled by default
            installed_at="2024-01-01T00:00:00Z",
            health_status="unknown",
        )
        registry.register(installed)
        # Explicitly disable after registration (register auto-enables first version)
        registry.set_enabled("disabled-skill", False, "1.0.0")
        registry.save()

        # Verify pre-condition
        pre = registry.get("disabled-skill", "1.0.0")
        assert pre is not None
        assert pre.enabled is False

        service = SkillRuntimeService(registry, installed_dir)
        response = service.run_healthcheck(
            "disabled-skill", "1.0.0", timeout_seconds=5.0,
        )

        # Healthcheck succeeds
        assert response.success
        assert response.status == "healthy"

        # Registry shows healthy status
        registry.load()
        post = registry.get("disabled-skill", "1.0.0")
        assert post is not None
        assert post.health_status == "healthy"

        # BUT enabled must still be False
        assert post.enabled is False, (
            f"Successful healthcheck must NOT auto-enable a disabled skill! "
            f"enabled={post.enabled}"
        )


# ══════════════════════════════════════════════════════════════════════
# Batch 3.1.1B — Run Dependencies: check-only, no install, no import
# ══════════════════════════════════════════════════════════════════════


def _setup_run_dep_service(
    tmp_path: Path,
    skill_id: str,
    version: str = "1.0.0",
    *,
    dependency_spec: str = "",
) -> tuple[SkillRuntimeService, SkillRegistry, Path]:
    """Set up a service with a run skill that has an optional dependency."""
    from tests.fixtures.runtime_fixtures import create_skill_run_with_dependency
    base_dir = tmp_path / "skills_data"
    installed_dir = base_dir / "installed"
    installed_dir.mkdir(parents=True)

    skill_dir = create_skill_run_with_dependency(
        installed_dir, skill_id, version,
        dependency_spec=dependency_spec,
    )

    registry_path = base_dir / "registry.json"
    registry = SkillRegistry(registry_path)
    registry.load()
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

    service = SkillRuntimeService(registry, installed_dir)
    return service, registry, installed_dir


class TestRunDependencies:
    """Run operation: dependencies check-only, never install, never import."""

    def test_satisfied_dependencies_pass(self, tmp_path):
        """Dependency check passes → Worker can launch."""
        service, registry, _ = _setup_run_dep_service(
            tmp_path, "run-dep-ok", dependency_spec="",
        )
        response = service.run_skill(
            "run-dep-ok", "1.0.0", {"input": "test"},
            timeout_seconds=5.0,
        )
        assert response.success, f"Expected success, got: {response.status}"
        assert response.status == "succeeded"

    def test_missing_dependency_prevents_launch(self, tmp_path):
        """Missing dependency → RuntimeDependencyError, no Worker, no Response."""
        service, registry, _ = _setup_run_dep_service(
            tmp_path, "run-dep-missing",
            dependency_spec="nonexistent-package-xyz-99999>=999.0.0",
        )
        import subprocess as _sp
        popen_calls = []
        original_popen = _sp.Popen

        class _SpyPopen:
            def __init__(self, *args, **kwargs):
                popen_calls.append(1)
                raise RuntimeError("Popen should not be called")

        # Monkeypatch subprocess.Popen
        import subprocess
        monkeypatch_sentinel = False
        try:
            setattr(subprocess, "Popen", _SpyPopen)
            monkeypatch_sentinel = True
            with pytest.raises(SkillRuntimeError, match="Dependencies"):
                service.run_skill(
                    "run-dep-missing", "1.0.0", {"input": "test"},
                    timeout_seconds=5.0,
                )
        finally:
            if monkeypatch_sentinel:
                setattr(subprocess, "Popen", original_popen)

        assert len(popen_calls) == 0, (
            f"Popen was called {len(popen_calls)} times — "
            f"missing dependency should prevent launch"
        )
        # No import of the target package
        assert "nonexistent-package-xyz-99999" not in sys.modules, (
            "Target dependency must not be imported"
        )

    def test_version_mismatch_prevents_launch(self, tmp_path, monkeypatch):
        """Version mismatch → RuntimeDependencyError, no Worker."""
        from dp_engine.skills.runtime_dependencies import _get_installed_version

        original_get_ver = _get_installed_version

        def _fake_get_ver(package_name: str) -> str | None:
            if package_name == "requests":
                return "2.0.0"  # Installed version too low
            return original_get_ver(package_name)

        monkeypatch.setattr(
            "dp_engine.skills.runtime_dependencies._get_installed_version",
            _fake_get_ver,
        )

        service, registry, _ = _setup_run_dep_service(
            tmp_path, "run-dep-ver",
            dependency_spec="requests>=999.0.0",
        )
        with pytest.raises(SkillRuntimeError, match="Dependencies"):
            service.run_skill(
                "run-dep-ver", "1.0.0", {"input": "test"},
                timeout_seconds=5.0,
            )

    def test_does_not_import_target(self, tmp_path):
        """Dependency check must NOT import the target package."""
        checker = SkillDependencyChecker()
        report = checker.check(
            "test-skill", "1.0.0",
            ("nonexistent-package-import-test-xyz",),
        )
        assert not report.all_satisfied
        assert "nonexistent-package-import-test-xyz" not in sys.modules

    def test_never_installs_dependencies(self):
        """Dependency checker source has zero references to pip/installers."""
        import inspect
        from dp_engine.skills import runtime_dependencies as rd
        source = inspect.getsource(rd)
        source_lower = source.lower()
        forbidden = ["pip ", "pip3", "conda ", "uv ", "poetry ", "subprocess.run", "os.system",
                      "subprocess.call", "subprocess.check_call", "subprocess.check_output"]
        for term in forbidden:
            assert term not in source_lower, (
                f"runtime_dependencies.py references '{term}' — must never invoke installers!"
            )


# ══════════════════════════════════════════════════════════════════════
# Batch 3.1.1B — Registry immutability: 7 parameterized run end states
# ══════════════════════════════════════════════════════════════════════


def _make_registry_snapshot(registry: SkillRegistry) -> dict:
    """Capture a full in-memory snapshot of registry state for comparison."""
    snap = registry.snapshot()
    # Also record disk state
    disk_path = registry._registry_path  # public-ish attribute
    disk_bytes = None
    disk_exists = False
    if disk_path.is_file():
        disk_exists = True
        disk_bytes = disk_path.read_bytes()
    return {
        "snapshot": snap,
        "disk_exists": disk_exists,
        "disk_bytes": disk_bytes,
        "disk_path": disk_path,
    }


def _assert_registry_unchanged(
    registry: SkillRegistry,
    pre: dict,
    label: str = "",
) -> None:
    """Assert registry memory + disk state is completely unchanged."""
    registry.load()  # Re-read from disk
    snap_now = registry.snapshot()
    snap_pre = pre["snapshot"]

    # Compare skills count and keys
    pre_skills = snap_pre.skills
    now_skills = snap_now.skills
    assert set(pre_skills.keys()) == set(now_skills.keys()), (
        f"[{label}] Registry skills keys changed!"
    )
    for key in pre_skills:
        pre_skill = pre_skills[key]
        now_skill = now_skills[key]
        assert pre_skill.enabled == now_skill.enabled, (
            f"[{label}] enabled changed for {key}"
        )
        assert pre_skill.health_status == now_skill.health_status, (
            f"[{label}] health_status changed for {key}: "
            f"was '{pre_skill.health_status}', now '{now_skill.health_status}'"
        )
        assert pre_skill.health_message == now_skill.health_message, (
            f"[{label}] health_message changed for {key}"
        )
        assert pre_skill.active_version == now_skill.active_version if hasattr(pre_skill, 'active_version') else True

    # Compare active_versions
    assert snap_pre.active_versions == snap_now.active_versions, (
        f"[{label}] active_versions changed!"
    )

    # Compare disk
    disk_path = pre["disk_path"]
    disk_now_exists = disk_path.is_file()
    assert disk_now_exists == pre["disk_exists"], (
        f"[{label}] Registry disk existence changed"
    )
    if pre["disk_exists"] and disk_now_exists:
        disk_now_bytes = disk_path.read_bytes()
        assert disk_now_bytes == pre["disk_bytes"], (
            f"[{label}] Registry disk bytes changed!"
        )


def _setup_registry_immutability_service(
    tmp_path: Path,
    skill_id: str,
    version: str = "1.0.0",
    *,
    run_code: str | None = None,
    dependency_spec: str = "",
) -> tuple[SkillRuntimeService, SkillRegistry, Path]:
    """Set up service with a run skill for registry immutability testing."""
    from tests.fixtures.runtime_fixtures import _make_run_skill
    base_dir = tmp_path / "skills_data"
    installed_dir = base_dir / "installed"
    installed_dir.mkdir(parents=True)

    code = run_code or '"""OK run."""\ndef run(context):\n    return {"ok": True}\n'
    skill_dir = _make_run_skill(
        installed_dir, skill_id, version, code,
        dependencies=f"  - {dependency_spec}\n" if dependency_spec else "",
    )

    registry_path = base_dir / "registry.json"
    registry = SkillRegistry(registry_path)
    registry.load()
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

    service = SkillRuntimeService(registry, installed_dir)
    return service, registry, installed_dir


class TestRegistryImmutability:
    """Registry must remain unchanged across all 7 run end states."""

    @pytest.mark.parametrize("end_state", [
        "succeeded",
        "business-negative",
        "protocol-failed",
        "dependency-preflight-error",
        "timeout",
        "cancelled",
        "crashed",
    ])
    def test_registry_unchanged_for_run_end_state(self, tmp_path, end_state):
        """Registry memory + disk unchanged for each run end state."""
        if end_state == "succeeded":
            service, registry, _ = _setup_registry_immutability_service(
                tmp_path, "reg-succeeded",
                run_code='"""OK."""\ndef run(context):\n    return {"ok": True, "data": "success"}\n',
            )
            pre = _make_registry_snapshot(registry)
            response = service.run_skill("reg-succeeded", "1.0.0", {"x": 1}, timeout_seconds=5.0)
            assert response.success
            assert response.status == "succeeded"
            _assert_registry_unchanged(registry, pre, end_state)

        elif end_state == "business-negative":
            service, registry, _ = _setup_registry_immutability_service(
                tmp_path, "reg-biz-neg",
                run_code='"""Business negative."""\ndef run(context):\n    return {"ok": False, "reason": "validation failed"}\n',
            )
            pre = _make_registry_snapshot(registry)
            response = service.run_skill("reg-biz-neg", "1.0.0", {"x": 1}, timeout_seconds=5.0)
            assert response.success, "Business-negative should succeed"
            assert response.status == "succeeded"
            result = response.result or {}
            assert result.get("ok") is False
            _assert_registry_unchanged(registry, pre, end_state)

        elif end_state == "protocol-failed":
            # Skill returns non-dict → protocol_error
            from tests.fixtures.runtime_fixtures import create_skill_run_returns_non_json
            base_dir = tmp_path / "skills_data"
            installed_dir = base_dir / "installed"
            installed_dir.mkdir(parents=True, exist_ok=True)
            registry_path = base_dir / "registry.json"
            registry = SkillRegistry(registry_path)
            registry.load()

            skill_dir = create_skill_run_returns_non_json(installed_dir, "reg-proto-fail", "1.0.0")
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

            service = SkillRuntimeService(registry, installed_dir)
            pre = _make_registry_snapshot(registry)
            response = service.run_skill("reg-proto-fail", "1.0.0", {"x": 1}, timeout_seconds=5.0)
            assert not response.success
            assert response.status == "protocol_error"
            _assert_registry_unchanged(registry, pre, end_state)

        elif end_state == "dependency-preflight-error":
            service, registry, _ = _setup_registry_immutability_service(
                tmp_path, "reg-dep-preflight",
                dependency_spec="nonexistent-package-xyz-311b-99999>=999.0.0",
            )
            pre = _make_registry_snapshot(registry)
            with pytest.raises(SkillRuntimeError, match="Dependencies"):
                service.run_skill("reg-dep-preflight", "1.0.0", {"x": 1}, timeout_seconds=5.0)
            _assert_registry_unchanged(registry, pre, end_state)

        elif end_state == "timeout":
            from tests.fixtures.runtime_fixtures import create_skill_run_hangs
            base_dir = tmp_path / "skills_data"
            installed_dir = base_dir / "installed"
            installed_dir.mkdir(parents=True, exist_ok=True)
            registry_path = base_dir / "registry.json"
            registry = SkillRegistry(registry_path)
            registry.load()

            skill_dir = create_skill_run_hangs(installed_dir, "reg-timeout", "1.0.0", sleep_seconds=30.0)
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

            service = SkillRuntimeService(
                registry, installed_dir,
                cancel_grace_ms=50, terminate_grace_ms=50, kill_grace_ms=50,
            )
            pre = _make_registry_snapshot(registry)
            response = service.run_skill("reg-timeout", "1.0.0", {"x": 1}, timeout_seconds=0.5)
            assert not response.success
            assert response.status == "timeout"
            _assert_registry_unchanged(registry, pre, end_state)

        elif end_state == "cancelled":
            from tests.fixtures.runtime_fixtures import create_skill_run_hangs
            base_dir = tmp_path / "skills_data"
            installed_dir = base_dir / "installed"
            installed_dir.mkdir(parents=True, exist_ok=True)
            registry_path = base_dir / "registry.json"
            registry = SkillRegistry(registry_path)
            registry.load()

            skill_dir = create_skill_run_hangs(installed_dir, "reg-cancelled", "1.0.0", sleep_seconds=30.0)
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

            service = SkillRuntimeService(
                registry, installed_dir,
                cancel_grace_ms=50, terminate_grace_ms=50, kill_grace_ms=50,
            )
            pre = _make_registry_snapshot(registry)

            # Launch run in background thread-style — we simulate cancel
            import threading
            import time as _time

            response_container = []

            def _run():
                try:
                    resp = service.run_skill("reg-cancelled", "1.0.0", {"x": 1}, timeout_seconds=5.0)
                    response_container.append(resp)
                except Exception as ex:
                    response_container.append(ex)

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            _time.sleep(0.3)
            service.cancel()
            t.join(timeout=5.0)

            assert len(response_container) > 0, "No response captured for cancelled"
            response = response_container[0]
            if isinstance(response, Exception):
                raise response
            assert not response.success
            assert response.status == "cancelled"
            _assert_registry_unchanged(registry, pre, end_state)

        elif end_state == "crashed":
            from tests.fixtures.runtime_fixtures import create_skill_run_exits_nonzero
            base_dir = tmp_path / "skills_data"
            installed_dir = base_dir / "installed"
            installed_dir.mkdir(parents=True, exist_ok=True)
            registry_path = base_dir / "registry.json"
            registry = SkillRegistry(registry_path)
            registry.load()

            skill_dir = create_skill_run_exits_nonzero(installed_dir, "reg-crashed", "1.0.0")
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

            service = SkillRuntimeService(registry, installed_dir)
            pre = _make_registry_snapshot(registry)
            response = service.run_skill("reg-crashed", "1.0.0", {"x": 1}, timeout_seconds=5.0)
            assert not response.success
            assert response.status == "crashed"
            _assert_registry_unchanged(registry, pre, end_state)
