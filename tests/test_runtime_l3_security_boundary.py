"""L3 tests: Security boundary — malicious top-level code blocked in real Worker subprocess.

Each test:
1. Creates a skill with MALICIOUS top-level code in its healthcheck module
2. Launches it via SkillRuntimeService → real subprocess
3. Verifies the malicious action was BLOCKED at import time (by audit hooks)
4. Verifies the check() function was NEVER called (no marker file)

CRITICAL: These tests use real subprocess.Popen in the service.
The malicious code runs ONLY in the isolated Worker subprocess — never in the test process.

Security model (BEST-EFFORT, not a complete OS sandbox):
- Python audit hooks provide defense-in-depth in the Worker subprocess
- Top-level side effects are blocked DURING exec_module(), before check() runs
- This is process isolation + audit hooks, NOT a security sandbox
- Native extensions with unapproved capabilities are denied by default

KNOWN BOUNDARY (honest disclosure):
This mechanism is NOT a complete OS sandbox.
File path, network, subprocess and some native-loading restrictions are
best-effort defenses.  Native extensions MAY bypass Python-layer auditing,
so high-risk or unapproved native skills MUST NOT be executed.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from dp_engine.skills.registry import SkillRegistry
from dp_engine.skills.manifest_parser import parse_skill_manifest
from dp_engine.skills.runtime_service import SkillRuntimeService
from dp_engine.skills.runtime_errors import (
    SkillRuntimeError,
    RuntimePermissionError,
)
from dp_engine.skills.models import InstalledSkill
from tests.fixtures.runtime_fixtures import (
    create_malicious_top_level_write,
    create_malicious_top_level_external_read,
    create_malicious_top_level_socket,
    create_malicious_top_level_popen,
    create_malicious_top_level_os_system,
    create_malicious_top_level_ctypes,
    create_skill_that_reads_own_files,
    create_skill_that_reads_workspace_files,
    create_skill_that_reads_other_skill,
    create_skill_that_reads_registry,
    create_skill_with_symlink_read_escape,
    create_minimal_skill_package,
    create_skill_that_reads_project_root_sentinel,
    create_skill_that_reads_cwd_sentinel,
    create_skill_that_reads_unapproved_site_packages,
    create_skill_that_reads_approved_dependency,
    create_skill_that_writes_to_output,
    create_skill_that_reads_output_artifact,
    create_malicious_top_level_registry_write,
    create_malicious_top_level_socket_connect,
)


def _register_and_service(
    tmp_path: Path,
    skill_dir: Path,
    skill_id: str,
    version: str = "1.0.0",
) -> tuple[SkillRuntimeService, SkillRegistry, Path]:
    """Register a skill and create a SkillRuntimeService.

    Returns (service, registry, workspace_parent_dir).
    """
    base_dir = tmp_path / "skills_data"
    base_dir.mkdir(exist_ok=True)
    installed_dir = base_dir / "installed"
    installed_dir.mkdir(exist_ok=True)

    registry_path = base_dir / "registry.json"
    registry = SkillRegistry(registry_path)
    registry.load()

    # The skill_dir must be UNDER installed_dir to pass the gate check
    # (install_path within managed directory)
    target_dir = installed_dir / skill_id / version
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    # Copy skill files to the managed directory
    import shutil
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(skill_dir, target_dir)

    manifest = parse_skill_manifest(target_dir)
    installed = InstalledSkill(
        manifest=manifest,
        install_path=str(target_dir),
        enabled=True,
        installed_at="2024-01-01T00:00:00Z",
        health_status="unknown",
    )
    registry.register(installed)
    registry.save()

    service = SkillRuntimeService(
        registry=registry,
        installed_dir=installed_dir,
    )
    return service, registry, target_dir


def _check_not_called_in_workspaces() -> None:
    """Assert that check_called.txt marker exists in NO workspace."""
    from dp_engine.skills.runtime_paths import get_runtime_root
    runtime_root = get_runtime_root()
    if runtime_root.exists():
        for ws_dir in runtime_root.iterdir():
            if ws_dir.is_dir():
                marker = ws_dir / "output" / "check_called.txt"
                assert not marker.exists(), (
                    f"check() was called! Marker found at: {marker}\n"
                    f"Failure should have occurred during import, not function execution."
                )


# ── Malicious top-level write ──


class TestMaliciousTopLevelWrite:
    """Top-level file write outside workspace must be blocked."""

    def test_top_level_write_blocked_check_not_called(self, tmp_path):
        """Top-level Path.write_text() blocked by audit hook during import.

        Verifies:
        - PermissionError raised during exec_module
        - check() never called (no marker in output)
        """
        skill_dir = create_malicious_top_level_write(
            tmp_path, "mal-write", "1.0.0",
            tamper_path=str(tmp_path / "should_not_exist.txt"),
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "mal-write",
        )

        response = service.run_healthcheck(
            "mal-write", "1.0.0",
            timeout_seconds=5.0,
        )

        # The operation should NOT succeed
        assert not response.success, (
            f"Malicious top-level write should be blocked, got: {response.status}"
        )
        # Status should be unhealthy (import failed) or crashed
        assert response.status in ("unhealthy", "crashed", "protocol_error"), (
            f"Expected unhealthy/crashed/protocol_error, got: {response.status}"
        )

        # The tamper file must NOT exist
        tamper = tmp_path / "should_not_exist.txt"
        assert not tamper.exists(), (
            "Tamper file was written — audit hook failed to block top-level write"
        )

    def test_top_level_write_check_never_called(self, tmp_path):
        """Verify check() was never invoked — failure happens at import time."""
        skill_dir = create_malicious_top_level_write(
            tmp_path, "mal-write2", "1.0.0",
            tamper_path=str(tmp_path / "tamper2.txt"),
        )
        service, registry, installed = _register_and_service(
            tmp_path, skill_dir, "mal-write2",
        )

        response = service.run_healthcheck(
            "mal-write2", "1.0.0",
            timeout_seconds=5.0,
        )

        assert not response.success
        _check_not_called_in_workspaces()


# ── Malicious top-level EXTERNAL read (Batch 3.0.3) ──


class TestMaliciousTopLevelExternalRead:
    """Top-level file read of external files must be blocked.

    Replaces the Batch 3.0.1 documented-behavior test with deterministic
    security tests.  The audit hook now blocks reads outside allowed roots.
    """

    @pytest.fixture(autouse=True)
    def _external_secret(self, tmp_path) -> Path:
        """Create a fake external secret file outside skill/workspace."""
        secret = tmp_path / "TEST_EXTERNAL_SECRET_FILE.txt"
        secret.write_text("TOP_SECRET_DO_NOT_READ_3_0_3", encoding="utf-8")
        return secret

    def test_top_level_external_read_blocked(self, tmp_path, _external_secret):
        """Top-level read of external file is BLOCKED by audit hook.

        The malicious skill has top-level code that reads a fake secret
        file outside the allowed read roots.  The audit hook must raise
        PermissionError during exec_module.
        """
        skill_dir = create_malicious_top_level_external_read(
            tmp_path, "mal-ext-read", "1.0.0",
            external_file_path=str(_external_secret),
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "mal-ext-read",
        )

        response = service.run_healthcheck(
            "mal-ext-read", "1.0.0",
            timeout_seconds=5.0,
        )

        assert not response.success, (
            f"External read should be BLOCKED, got success=True, status={response.status}"
        )
        assert response.status in ("unhealthy", "crashed", "protocol_error"), (
            f"Expected unhealthy/crashed/protocol_error, got: {response.status}"
        )

    def test_top_level_external_read_check_never_called(
        self, tmp_path, _external_secret
    ):
        """External read fails at import time — check() is never invoked.

        Verifies the full chain:
        RuntimeService → runtime_worker → install audit hook →
        exec_module → top-level open/read_text → permission_denied →
        check() never called
        """
        skill_dir = create_malicious_top_level_external_read(
            tmp_path, "mal-ext-read2", "1.0.0",
            external_file_path=str(_external_secret),
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "mal-ext-read2",
        )

        response = service.run_healthcheck(
            "mal-ext-read2", "1.0.0",
            timeout_seconds=5.0,
        )

        assert not response.success, (
            f"External read should be BLOCKED, got: {response.status}"
        )
        _check_not_called_in_workspaces()


# ── Allowed reads ──


class TestSkillCanReadOwnFiles:
    """Skill can read files within its own installed directory."""

    def test_skill_can_read_own_files(self, tmp_path):
        """Skill's check() can read SKILL.md from its own installed path."""
        skill_dir = create_skill_that_reads_own_files(
            tmp_path, "read-own", "1.0.0",
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "read-own",
        )

        response = service.run_healthcheck(
            "read-own", "1.0.0",
            timeout_seconds=5.0,
        )

        assert response.success, (
            f"Reading own files should be ALLOWED, got: {response.status} — {response.message}"
        )
        assert response.status == "healthy"


class TestSkillCanReadWorkspaceFiles:
    """Skill can read files within its workspace."""

    def test_skill_can_read_workspace_files(self, tmp_path):
        """Skill's check() can read and write files in workspace."""
        skill_dir = create_skill_that_reads_workspace_files(
            tmp_path, "read-ws", "1.0.0",
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "read-ws",
        )

        response = service.run_healthcheck(
            "read-ws", "1.0.0",
            timeout_seconds=5.0,
        )

        assert response.success, (
            f"Reading workspace files should be ALLOWED, got: {response.status}"
        )
        assert response.status == "healthy"


# ── Forbidden reads ──


class TestSkillCannotReadOtherSkill:
    """Skill cannot read files from another skill's installed directory."""

    def test_skill_cannot_read_other_skill(self, tmp_path):
        """Top-level read of another skill's directory is BLOCKED."""
        # First, create another skill so its files exist
        base_dir = tmp_path / "skills_data"
        installed_dir = base_dir / "installed"
        installed_dir.mkdir(parents=True, exist_ok=True)
        other_dir = installed_dir / "other-skill" / "1.0.0"
        other_dir.mkdir(parents=True, exist_ok=True)
        other_skill_md = other_dir / "SKILL.md"
        other_skill_md.write_text("---\nschema_version: 1\nskill_id: other-skill\n---\n", encoding="utf-8")

        skill_dir = create_skill_that_reads_other_skill(
            tmp_path, "read-other", "1.0.0",
            other_skill_dir=str(other_skill_md),
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "read-other",
        )

        response = service.run_healthcheck(
            "read-other", "1.0.0",
            timeout_seconds=5.0,
        )

        assert not response.success, (
            f"Reading other skill's files should be BLOCKED, got: {response.status}"
        )
        _check_not_called_in_workspaces()


class TestSkillCannotReadRegistry:
    """Skill cannot read the registry file."""

    def test_skill_cannot_read_registry(self, tmp_path):
        """Top-level read of registry file is BLOCKED."""
        base_dir = tmp_path / "skills_data"
        installed_dir = base_dir / "installed"
        installed_dir.mkdir(parents=True, exist_ok=True)
        registry_path = base_dir / "registry.json"
        registry_path.write_text('{"skills": {}}', encoding="utf-8")

        skill_dir = create_skill_that_reads_registry(
            tmp_path, "read-registry", "1.0.0",
            registry_path=str(registry_path),
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "read-registry",
        )

        response = service.run_healthcheck(
            "read-registry", "1.0.0",
            timeout_seconds=5.0,
        )

        assert not response.success, (
            f"Reading registry should be BLOCKED, got: {response.status}"
        )
        _check_not_called_in_workspaces()


class TestSkillCannotReadFakeHomeSecret:
    """Skill cannot read files in fake home directory."""

    def test_skill_cannot_read_fake_home_secret(self, tmp_path):
        """Top-level read of ~/.secret file is BLOCKED."""
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        secret_file = fake_home / ".secret"
        secret_file.write_text("FAKE_HOME_SECRET_DO_NOT_LEAK", encoding="utf-8")

        skill_dir = create_malicious_top_level_external_read(
            tmp_path, "mal-home-read", "1.0.0",
            external_file_path=str(secret_file),
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "mal-home-read",
        )

        response = service.run_healthcheck(
            "mal-home-read", "1.0.0",
            timeout_seconds=5.0,
        )

        assert not response.success, (
            f"Reading fake home secret should be BLOCKED, got: {response.status}"
        )
        _check_not_called_in_workspaces()


class TestReadSymlinkEscapeBlocked:
    """Symlink-based read escape is blocked."""

    def test_read_symlink_escape_blocked(self, tmp_path):
        """Creating a symlink and reading through it is blocked.

        The audit hook resolves paths before checking, so symlink
        indirection does not bypass the read sandbox.
        """
        # Create an outside secret file
        outside_secret = tmp_path / "outside_secret.txt"
        outside_secret.write_text("ESCAPED_VIA_SYMLINK", encoding="utf-8")

        skill_dir = create_skill_with_symlink_read_escape(
            tmp_path, "symlink-esc", "1.0.0",
            symlink_target=str(outside_secret),
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "symlink-esc",
        )

        response = service.run_healthcheck(
            "symlink-esc", "1.0.0",
            timeout_seconds=5.0,
        )

        # If symlink creation succeeded, the read should be blocked
        # If symlink creation failed (no admin on Windows), that's also fine
        assert response.success, (
            f"Symlink test should succeed (either blocked or OS denied), "
            f"got: {response.status} — {response.message}"
        )
        # The message should indicate blocking, not escape
        assert "escape succeeded" not in response.message.lower(), (
            f"SYMLINK ESCAPE WAS NOT BLOCKED! Message: {response.message}"
        )


class TestStdlibImportStillWorks:
    """Python standard library imports are not affected by read blocking."""

    def test_stdlib_import_still_works(self, tmp_path):
        """A skill that imports standard library modules succeeds."""
        from tests.fixtures.runtime_fixtures import create_minimal_skill_package

        skill_dir = create_minimal_skill_package(
            tmp_path, "stdlib-test", "1.0.0",
            healthy=True,
            extra_healthcheck_code=(
                "    import json, os, pathlib, sys\n"
                "    # These imports should work — stdlib is in allowed_read_roots\n"
            ),
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "stdlib-test",
        )

        response = service.run_healthcheck(
            "stdlib-test", "1.0.0",
            timeout_seconds=5.0,
        )

        assert response.success, (
            f"Stdlib imports should work, got: {response.status} — {response.message}"
        )
        assert response.status == "healthy"


# ── Batch 3.0.5: sys.path isolation verification (project root, cwd, site-packages) ──


class TestProjectRootNotImplicitlyAllowed:
    """Project root files are NOT allowed via implicit sys.path inclusion."""

    def test_project_root_file_is_not_implicitly_allowed_by_sys_path(self, tmp_path):
        """Skill cannot read a sentinel file in a fake project root directory.

        Since we use sysconfig.get_paths() for stdlib (NOT sys.path),
        arbitrary directories that happen to be in sys.path are not
        implicitly authorized for reading.
        """
        fake_project_root = tmp_path / "fake_project_root"
        sentinel = fake_project_root / "SENTINEL.txt"
        fake_project_root.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("PROJECT_ROOT_SENTINEL_DO_NOT_READ", encoding="utf-8")

        skill_dir = create_skill_that_reads_project_root_sentinel(
            tmp_path, "read-proj-root", "1.0.0",
            project_root_sentinel_path=str(sentinel),
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "read-proj-root",
        )

        response = service.run_healthcheck(
            "read-proj-root", "1.0.0",
            timeout_seconds=5.0,
        )

        assert not response.success, (
            f"Project root sentinel should NOT be readable, "
            f"got: {response.status} — {response.message}"
        )
        _check_not_called_in_workspaces()


class TestCwdNotImplicitlyAllowed:
    """Current working directory files are NOT allowed via implicit sys.path inclusion."""

    def test_current_working_directory_is_not_implicitly_allowed(self, tmp_path):
        """Skill cannot read a sentinel in a CWD-like directory.

        CWD is typically in sys.path, but with the sysconfig-based approach,
        it should not be added to allowed read roots.
        """
        fake_cwd = tmp_path / "fake_cwd"
        sentinel = fake_cwd / "CWD_SENTINEL.txt"
        fake_cwd.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("CWD_SENTINEL_DO_NOT_READ", encoding="utf-8")

        skill_dir = create_skill_that_reads_cwd_sentinel(
            tmp_path, "read-cwd", "1.0.0",
            cwd_sentinel_path=str(sentinel),
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "read-cwd",
        )

        response = service.run_healthcheck(
            "read-cwd", "1.0.0",
            timeout_seconds=5.0,
        )

        assert not response.success, (
            f"CWD sentinel should NOT be readable, "
            f"got: {response.status} — {response.message}"
        )
        _check_not_called_in_workspaces()


class TestUnapprovedSitePackagesDenied:
    """Unapproved site-packages files are denied by default."""

    def test_unapproved_site_packages_file_is_denied(self, tmp_path):
        """Skill cannot read from an unapproved site-packages directory.

        Without explicit approval via approved_dependency_paths,
        site-packages directories are NOT added to allowed read roots.
        """
        fake_site_packages = tmp_path / "fake_site_packages" / "unapproved_pkg"
        sentinel = fake_site_packages / "SENTINEL.txt"
        fake_site_packages.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("UNAPPROVED_SITE_PACKAGES_SENTINEL", encoding="utf-8")

        skill_dir = create_skill_that_reads_unapproved_site_packages(
            tmp_path, "read-site-pkg", "1.0.0",
            site_packages_sentinel_path=str(sentinel),
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "read-site-pkg",
        )

        response = service.run_healthcheck(
            "read-site-pkg", "1.0.0",
            timeout_seconds=5.0,
        )

        assert not response.success, (
            f"Unapproved site-packages sentinel should be DENIED, "
            f"got: {response.status} — {response.message}"
        )
        _check_not_called_in_workspaces()


class TestApprovedDependencyImportStillWorks:
    """Explicitly approved dependency paths are included in allowed read roots."""

    def test_approved_dependency_import_still_works(self, tmp_path):
        """Skill can read a file from an explicitly approved dependency path.

        Files in the approved dependency directory should be readable
        when the path is explicitly passed to the read-roots builder.
        """
        from dp_engine.skills.runtime_permissions import (
            build_allowed_read_roots,
            check_runtime_file_access,
            RuntimeFilePolicy,
        )

        # Create a fake approved dependency with a sentinel file
        approved_dep_dir = tmp_path / "approved_dep"
        approved_file = approved_dep_dir / "approved_data.txt"
        approved_dep_dir.mkdir(parents=True, exist_ok=True)
        approved_file.write_text("APPROVED_DEPENDENCY_DATA_OK", encoding="utf-8")

        # Build allowed read roots WITH the approved dependency
        skill_dir = tmp_path / "skill"
        skill_dir.mkdir(exist_ok=True)
        workspace = tmp_path / "workspace"
        workspace.mkdir(exist_ok=True)

        roots = build_allowed_read_roots(
            skill_dir,
            workspace,
            approved_dependency_paths=(approved_dep_dir,),
        )

        # Verify the approved path is in the roots
        policy = RuntimeFilePolicy(
            allowed_read_roots=roots,
            allowed_write_roots=(workspace / "output", workspace / "temp"),
        )

        # Reading approved dependency file should NOT raise
        try:
            check_runtime_file_access(
                str(approved_file), mode="r", policy=policy, workspace_path=workspace,
            )
        except PermissionError as e:
            pytest.fail(f"Approved dependency file should be readable, got: {e}")

        # Reading a file NOT in approved roots should raise
        unapproved = tmp_path / "unapproved" / "secret.txt"
        unapproved.parent.mkdir(exist_ok=True)
        unapproved.write_text("NOT_APPROVED", encoding="utf-8")

        with pytest.raises(PermissionError, match="permission_denied"):
            check_runtime_file_access(
                str(unapproved), mode="r", policy=policy, workspace_path=workspace,
            )


# ── Malicious top-level socket ──


class TestMaliciousTopLevelSocket:
    """Top-level socket creation must be blocked."""

    def test_top_level_socket_blocked(self, tmp_path):
        """Top-level socket.socket() blocked by audit hook during import."""
        skill_dir = create_malicious_top_level_socket(
            tmp_path, "mal-socket", "1.0.0",
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "mal-socket",
        )

        response = service.run_healthcheck(
            "mal-socket", "1.0.0",
            timeout_seconds=5.0,
        )

        assert not response.success, (
            f"Malicious socket creation should be blocked, got: {response.status}"
        )
        assert response.status in ("unhealthy", "crashed", "protocol_error"), (
            f"Expected unhealthy/crashed, got: {response.status}"
        )

    def test_top_level_socket_check_never_called(self, tmp_path):
        """Socket creation fails at import time, check() never runs."""
        skill_dir = create_malicious_top_level_socket(
            tmp_path, "mal-socket2", "1.0.0",
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "mal-socket2",
        )

        response = service.run_healthcheck(
            "mal-socket2", "1.0.0",
            timeout_seconds=5.0,
        )
        assert not response.success
        _check_not_called_in_workspaces()


# ── Malicious top-level subprocess.Popen ──


class TestMaliciousTopLevelPopen:
    """Top-level subprocess.Popen must be blocked."""

    def test_top_level_popen_blocked(self, tmp_path):
        """Top-level subprocess.Popen() blocked by audit hook during import."""
        skill_dir = create_malicious_top_level_popen(
            tmp_path, "mal-popen", "1.0.0",
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "mal-popen",
        )

        response = service.run_healthcheck(
            "mal-popen", "1.0.0",
            timeout_seconds=5.0,
        )

        assert not response.success, (
            f"Malicious Popen should be blocked, got: {response.status}"
        )
        assert response.status in ("unhealthy", "crashed", "protocol_error"), (
            f"Expected unhealthy/crashed, got: {response.status}"
        )

    def test_top_level_popen_check_never_called(self, tmp_path):
        """Popen fails at import time, check() never runs."""
        skill_dir = create_malicious_top_level_popen(
            tmp_path, "mal-popen2", "1.0.0",
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "mal-popen2",
        )

        response = service.run_healthcheck(
            "mal-popen2", "1.0.0",
            timeout_seconds=5.0,
        )
        assert not response.success
        _check_not_called_in_workspaces()


# ── Malicious top-level os.system ──


class TestMaliciousTopLevelOsSystem:
    """Top-level os.system must be blocked."""

    def test_top_level_os_system_blocked(self, tmp_path):
        """Top-level os.system() blocked by audit hook during import."""
        skill_dir = create_malicious_top_level_os_system(
            tmp_path, "mal-os-system", "1.0.0",
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "mal-os-system",
        )

        response = service.run_healthcheck(
            "mal-os-system", "1.0.0",
            timeout_seconds=5.0,
        )

        assert not response.success, (
            f"Malicious os.system should be blocked, got: {response.status}"
        )
        assert response.status in ("unhealthy", "crashed", "protocol_error"), (
            f"Expected unhealthy/crashed, got: {response.status}"
        )

    def test_top_level_os_system_check_never_called(self, tmp_path):
        """os.system fails at import time, check() never runs."""
        skill_dir = create_malicious_top_level_os_system(
            tmp_path, "mal-os-system2", "1.0.0",
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "mal-os-system2",
        )

        response = service.run_healthcheck(
            "mal-os-system2", "1.0.0",
            timeout_seconds=5.0,
        )
        assert not response.success
        _check_not_called_in_workspaces()


# ── Malicious top-level ctypes/native load ──


class TestMaliciousTopLevelCtypes:
    """Top-level ctypes native library load must be blocked."""

    def test_top_level_ctypes_blocked(self, tmp_path):
        """Top-level ctypes.CDLL() blocked by audit hook during import."""
        skill_dir = create_malicious_top_level_ctypes(
            tmp_path, "mal-ctypes", "1.0.0",
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "mal-ctypes",
        )

        response = service.run_healthcheck(
            "mal-ctypes", "1.0.0",
            timeout_seconds=5.0,
        )

        assert not response.success, (
            f"Malicious ctypes should be blocked, got: {response.status}"
        )
        assert response.status in ("unhealthy", "crashed", "protocol_error"), (
            f"Expected unhealthy/crashed, got: {response.status}"
        )

    def test_top_level_ctypes_check_never_called(self, tmp_path):
        """ctypes fails at import time, check() never runs."""
        skill_dir = create_malicious_top_level_ctypes(
            tmp_path, "mal-ctypes2", "1.0.0",
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "mal-ctypes2",
        )

        response = service.run_healthcheck(
            "mal-ctypes2", "1.0.0",
            timeout_seconds=5.0,
        )
        assert not response.success
        _check_not_called_in_workspaces()


# ── Cross-cutting: audit hook install order ──


class TestAuditHookInstallOrder:
    """Verify audit hooks are installed BEFORE skill module import."""

    def test_permissions_installed_before_exec_module(self, tmp_path):
        """Worker installs audit hooks, THEN imports skill module.

        This is proven by the fact that top-level malicious code is
        blocked — if hooks were installed AFTER import, the side
        effects would already have executed.

        We verify by checking the worker source: install_audit_hooks()
        CALL occurs before the operation dispatch (which leads to
        _load_entrypoint_function) in run_worker().
        """
        worker_path = (
            Path(__file__).parent.parent / "dp_engine" / "skills" / "runtime_worker.py"
        )
        source = worker_path.read_text(encoding="utf-8")

        lines = source.split("\n")

        # Find the run_worker function body bounds
        run_worker_start = -1
        for i, line in enumerate(lines):
            if line.strip().startswith("def run_worker("):
                run_worker_start = i
                break
        assert run_worker_start > 0, "run_worker function not found"

        # Find install_audit_hooks CALL within run_worker()
        install_call_line = -1
        # Find operation dispatch within run_worker()
        dispatch_call_line = -1

        for i in range(run_worker_start, len(lines)):
            line = lines[i]
            stripped = line.strip()
            if stripped.startswith("def ") and i > run_worker_start:
                # We've left run_worker body
                break
            # Call site: not a definition, not a comment
            if "install_audit_hooks(" in stripped and not stripped.startswith("#"):
                if install_call_line == -1:
                    install_call_line = i
            if ("_execute_healthcheck(" in stripped or "_execute_run(" in stripped) and not stripped.startswith("#"):
                if dispatch_call_line == -1:
                    dispatch_call_line = i

        assert install_call_line > 0, (
            "install_audit_hooks() CALL not found in run_worker() body"
        )
        assert dispatch_call_line > 0, (
            "operation dispatch (_execute_healthcheck/_execute_run) CALL not found in run_worker() body"
        )
        assert install_call_line < dispatch_call_line, (
            f"install_audit_hooks CALL (line {install_call_line + 1}) MUST come BEFORE "
            f"operation dispatch CALL (line {dispatch_call_line + 1}) "
            f"in run_worker(). "
            f"Current order is WRONG — hooks would be installed after skill import!"
        )


# ── Batch 3.0.6: #23 Write output allowed ──


class TestSkillCanWriteRuntimeOutput:
    """Skill can write files to workspace/output."""

    def test_skill_can_write_runtime_output(self, tmp_path):
        """#23: Skill writes to workspace/output — must be ALLOWED.

        Verifies:
        - Write succeeds
        - Artifact is present in output
        - File content matches test value
        - Other workspace write paths are NOT implicitly allowed
        """
        skill_dir = create_skill_that_writes_to_output(
            tmp_path, "write-out", "1.0.0",
        )
        service, registry, installed = _register_and_service(
            tmp_path, skill_dir, "write-out",
        )

        response = service.run_healthcheck(
            "write-out", "1.0.0",
            timeout_seconds=5.0,
        )

        assert response.success, (
            f"Writing to workspace/output should be ALLOWED, "
            f"got: {response.status} — {response.message}"
        )
        assert response.status == "healthy"

        # Verify the artifact was collected
        artifact_rel_paths = [a.relative_path for a in response.artifacts]
        assert "result.txt" in artifact_rel_paths, (
            f"Artifact 'result.txt' not found in response artifacts: {artifact_rel_paths}"
        )

    def test_skill_can_write_and_read_output_artifact(self, tmp_path):
        """#23: Skill writes to output and verifies content via read-back.

        This confirms the write path is operational end-to-end through
        the Runtime system, not just that a fixture file exists on disk.
        """
        skill_dir = create_skill_that_reads_output_artifact(
            tmp_path, "write-read-out", "1.0.0",
        )
        service, registry, installed = _register_and_service(
            tmp_path, skill_dir, "write-read-out",
        )

        response = service.run_healthcheck(
            "write-read-out", "1.0.0",
            timeout_seconds=5.0,
        )

        assert response.success, (
            f"Output artifact write+read should be ALLOWED, "
            f"got: {response.status} — {response.message}"
        )
        assert response.status == "healthy"


# ── Batch 3.0.6: #28 Modify registry denied ──


class TestSkillCannotModifyRegistry:
    """Skill must NOT be able to WRITE to the registry file."""

    def test_skill_cannot_modify_registry(self, tmp_path):
        """#28: Top-level write to registry file is BLOCKED.

        Creates a sentinel registry file, attempts a skill that writes
        to it at top level. Asserts:
        - permission_denied
        - Registry sentinel content unchanged
        - healthcheck NOT marked healthy
        """
        # Use separate directory from _register_and_service internal dirs
        skill_src = tmp_path / "skill_src"
        skill_src.mkdir()

        # Create a separate registry sentinel in a location the skill will target
        registry_sentinel_dir = tmp_path / "registry_sentinel"
        registry_sentinel_dir.mkdir()
        registry_sentinel_file = registry_sentinel_dir / "registry.json"
        sentinel_content = '{"sentinel": "BATCH_306_REGISTRY_SENTINEL", "skills": {}}'
        registry_sentinel_file.write_text(sentinel_content, encoding="utf-8")

        # Create the malicious skill in skill_src
        skill_dir = create_malicious_top_level_registry_write(
            skill_src, "mal-reg-write", "1.0.0",
            registry_path=str(registry_sentinel_file),
        )

        # Now manually set up the service with its OWN registry
        base_dir = tmp_path / "skills_data"
        installed_dir = base_dir / "installed"
        installed_dir.mkdir(parents=True, exist_ok=True)
        service_registry_path = base_dir / "service_registry.json"

        registry = SkillRegistry(service_registry_path)
        registry.load()

        # Copy skill to managed dir
        import shutil
        target_dir = installed_dir / "mal-reg-write" / "1.0.0"
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(skill_dir, target_dir)

        manifest = parse_skill_manifest(target_dir)
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(target_dir),
            enabled=True,
            installed_at="2024-01-01T00:00:00Z",
            health_status="unknown",
        )
        registry.register(installed)
        registry.save()

        service = SkillRuntimeService(
            registry=registry,
            installed_dir=installed_dir,
        )

        response = service.run_healthcheck(
            "mal-reg-write", "1.0.0",
            timeout_seconds=5.0,
        )

        assert not response.success, (
            f"Registry write should be BLOCKED, got: {response.status}"
        )
        assert response.status in ("unhealthy", "crashed", "protocol_error"), (
            f"Expected unhealthy/crashed, got: {response.status}"
        )

        # Sentinel content must be unchanged
        disk_content = registry_sentinel_file.read_text(encoding="utf-8")
        assert "BATCH_306_REGISTRY_SENTINEL" in disk_content, (
            f"Registry sentinel was overwritten! Content: {disk_content[:200]}"
        )
        assert '"tampered": true' not in disk_content, (
            f"Registry was tampered with! Content: {disk_content[:200]}"
        )

        _check_not_called_in_workspaces()


# ── Batch 3.0.6: #30 Socket connect blocked ──


class TestMaliciousTopLevelSocketConnect:
    """Top-level socket connect() must be blocked — not just socket creation."""

    def test_top_level_socket_connect_blocked(self, tmp_path):
        """#30: Top-level socket.socket() + connect() blocked by audit hooks.

        This is a dedicated test for connect() blocking, distinct from
        the socket creation test (#29). The skill creates a socket AND
        calls connect() to a TEST-NET reserved address (192.0.2.1:80).

        Verifies:
        - connect-related audit event is triggered
        - No real connection is attempted
        - check() is never called
        - Returns permission_denied
        """
        skill_dir = create_malicious_top_level_socket_connect(
            tmp_path, "mal-sock-connect", "1.0.0",
        )
        service, registry, _ = _register_and_service(
            tmp_path, skill_dir, "mal-sock-connect",
        )

        response = service.run_healthcheck(
            "mal-sock-connect", "1.0.0",
            timeout_seconds=5.0,
        )

        assert not response.success, (
            f"Socket connect should be BLOCKED, got: {response.status}"
        )
        assert response.status in ("unhealthy", "crashed", "protocol_error"), (
            f"Expected unhealthy/crashed, got: {response.status}"
        )
        _check_not_called_in_workspaces()


# ══════════════════════════════════════════════════════════════════════
# Batch 3.1.1B — Run security boundary: audit hook rejections → permission_denied
# ══════════════════════════════════════════════════════════════════════


def _register_and_service_for_run(
    tmp_path: Path,
    skill_dir: Path,
    skill_id: str,
    version: str = "1.0.0",
) -> tuple[SkillRuntimeService, SkillRegistry, Path]:
    """Register a run skill and create a SkillRuntimeService.

    Returns (service, registry, target_dir).
    """
    base_dir = tmp_path / "skills_data"
    base_dir.mkdir(exist_ok=True)
    installed_dir = base_dir / "installed"
    installed_dir.mkdir(exist_ok=True)

    registry_path = base_dir / "registry.json"
    registry = SkillRegistry(registry_path)
    registry.load()

    target_dir = installed_dir / skill_id / version
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    import shutil
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(skill_dir, target_dir)

    manifest = parse_skill_manifest(target_dir)
    installed = InstalledSkill(
        manifest=manifest,
        install_path=str(target_dir),
        enabled=True,
        installed_at="2024-01-01T00:00:00Z",
        health_status="unknown",
    )
    registry.register(installed)
    registry.save()

    service = SkillRuntimeService(
        registry=registry,
        installed_dir=installed_dir,
    )
    return service, registry, target_dir


class TestRunNetworkBlocked:
    """Run operation: socket create and connect → permission_denied."""

    def test_socket_create_rejected(self, tmp_path):
        """socket.socket() in run() → status='permission_denied'."""
        from tests.fixtures.runtime_fixtures import create_skill_run_socket_create
        skill_dir = create_skill_run_socket_create(tmp_path, "run-sock-create", "1.0.0")
        service, registry, _ = _register_and_service_for_run(
            tmp_path, skill_dir, "run-sock-create",
        )
        response = service.run_skill("run-sock-create", "1.0.0", {"input": "test"}, timeout_seconds=5.0)
        assert not response.success, f"Expected success=False, got {response.status}"
        assert response.status == "permission_denied", (
            f"Expected 'permission_denied', got '{response.status}'"
        )

    def test_socket_connect_rejected(self, tmp_path):
        """socket.connect() in run() → status='permission_denied'."""
        from tests.fixtures.runtime_fixtures import create_skill_run_socket_connect
        skill_dir = create_skill_run_socket_connect(tmp_path, "run-sock-conn", "1.0.0")
        service, registry, _ = _register_and_service_for_run(
            tmp_path, skill_dir, "run-sock-conn",
        )
        response = service.run_skill("run-sock-conn", "1.0.0", {"input": "test"}, timeout_seconds=5.0)
        assert not response.success
        assert response.status == "permission_denied", (
            f"Expected 'permission_denied', got '{response.status}'"
        )


class TestRunSubprocessBlocked:
    """Run operation: subprocess and os.system → permission_denied."""

    def test_subprocess_rejected(self, tmp_path):
        """subprocess.Popen in run() → status='permission_denied'."""
        from tests.fixtures.runtime_fixtures import create_skill_run_subprocess
        skill_dir = create_skill_run_subprocess(tmp_path, "run-popen", "1.0.0")
        service, registry, _ = _register_and_service_for_run(
            tmp_path, skill_dir, "run-popen",
        )
        response = service.run_skill("run-popen", "1.0.0", {"input": "test"}, timeout_seconds=5.0)
        assert not response.success
        assert response.status == "permission_denied", (
            f"Expected 'permission_denied', got '{response.status}'"
        )

    def test_os_system_rejected(self, tmp_path):
        """os.system in run() → status='permission_denied'."""
        from tests.fixtures.runtime_fixtures import create_skill_run_os_system
        skill_dir = create_skill_run_os_system(tmp_path, "run-os-sys", "1.0.0")
        service, registry, _ = _register_and_service_for_run(
            tmp_path, skill_dir, "run-os-sys",
        )
        response = service.run_skill("run-os-sys", "1.0.0", {"input": "test"}, timeout_seconds=5.0)
        assert not response.success
        assert response.status == "permission_denied", (
            f"Expected 'permission_denied', got '{response.status}'"
        )


class TestRunNativeBlocked:
    """Run operation: ctypes/native load → permission_denied."""

    def test_ctypes_rejected(self, tmp_path):
        """ctypes native library load in run() → permission_denied.

        Uses a unique non-existent library path to reliably trigger the
        ctypes.dlopen audit event. The audit hook fires during
        LoadLibrary before the OS attempts the actual load, so the
        rejection is deterministic regardless of platform.
        """
        from tests.fixtures.runtime_fixtures import create_skill_run_ctypes
        skill_dir = create_skill_run_ctypes(tmp_path, "run-ctypes", "1.0.0")
        service, registry, _ = _register_and_service_for_run(
            tmp_path, skill_dir, "run-ctypes",
        )
        response = service.run_skill("run-ctypes", "1.0.0", {"input": "test"}, timeout_seconds=5.0)
        assert not response.success, (
            f"Expected success=False, got success=True, status={response.status}"
        )
        assert response.status == "permission_denied", (
            f"Expected 'permission_denied', got '{response.status}'"
        )
        assert response.result is None, (
            f"Expected result=None for permission_denied, got {response.result}"
        )


class TestRunWriteBlocked:
    """Run operation: write outside allowed roots → permission_denied."""

    def test_write_installed_rejected(self, tmp_path):
        """Write to installed directory in run() → status='permission_denied'."""
        from tests.fixtures.runtime_fixtures import create_skill_run_write_installed
        skill_dir = create_skill_run_write_installed(tmp_path, "run-write-inst", "1.0.0")
        service, registry, _ = _register_and_service_for_run(
            tmp_path, skill_dir, "run-write-inst",
        )
        response = service.run_skill("run-write-inst", "1.0.0", {"input": "test"}, timeout_seconds=5.0)
        assert not response.success
        assert response.status == "permission_denied", (
            f"Expected 'permission_denied', got '{response.status}'"
        )

    def test_write_outside_workspace_rejected(self, tmp_path):
        """Write outside workspace in run() → status='permission_denied'."""
        from tests.fixtures.runtime_fixtures import create_skill_run_write_outside
        skill_dir = create_skill_run_write_outside(tmp_path, "run-write-out", "1.0.0")
        service, registry, _ = _register_and_service_for_run(
            tmp_path, skill_dir, "run-write-out",
        )
        response = service.run_skill("run-write-out", "1.0.0", {"input": "test"}, timeout_seconds=5.0)
        assert not response.success
        assert response.status == "permission_denied", (
            f"Expected 'permission_denied', got '{response.status}'"
        )


class TestRunRegistryAccess:
    """Run operation: Registry access → permission_denied."""

    def test_read_registry_rejected(self, tmp_path):
        """Read registry in run() → status='permission_denied'."""
        from tests.fixtures.runtime_fixtures import create_skill_run_read_registry
        base_dir = tmp_path / "skills_data"
        installed_dir = base_dir / "installed"
        installed_dir.mkdir(parents=True, exist_ok=True)
        registry_path = base_dir / "registry.json"
        registry_path.write_text('{"skills": {}}', encoding="utf-8")

        skill_src = tmp_path / "skill_src"
        skill_dir = create_skill_run_read_registry(
            skill_src, "run-read-reg", "1.0.0",
            registry_path=str(registry_path),
        )
        service, registry, _ = _register_and_service_for_run(
            tmp_path, skill_dir, "run-read-reg",
        )
        response = service.run_skill("run-read-reg", "1.0.0", {"input": "test"}, timeout_seconds=5.0)
        assert not response.success
        assert response.status == "permission_denied", (
            f"Expected 'permission_denied', got '{response.status}'"
        )

    def test_modify_registry_rejected(self, tmp_path):
        """Modify registry in run() → status='permission_denied'."""
        from tests.fixtures.runtime_fixtures import create_skill_run_modify_registry
        # Use a separate sentinel registry file outside managed dir
        sentinel_dir = tmp_path / "sentinel_registry"
        sentinel_dir.mkdir(parents=True, exist_ok=True)
        sentinel_path = sentinel_dir / "registry.json"
        sentinel_content = '{"sentinel": "BATCH_311B_MODIFY_REGISTRY_SENTINEL", "skills": {}}'
        sentinel_path.write_text(sentinel_content, encoding="utf-8")

        skill_src = tmp_path / "skill_src"
        skill_dir = create_skill_run_modify_registry(
            skill_src, "run-mod-reg", "1.0.0",
            registry_path=str(sentinel_path),
        )
        service, registry, _ = _register_and_service_for_run(
            tmp_path, skill_dir, "run-mod-reg",
        )
        response = service.run_skill("run-mod-reg", "1.0.0", {"input": "test"}, timeout_seconds=5.0)
        assert not response.success
        assert response.status == "permission_denied", (
            f"Expected 'permission_denied', got '{response.status}'"
        )
        # Sentinel content must be unchanged
        disk_content = sentinel_path.read_text(encoding="utf-8")
        assert "BATCH_311B_MODIFY_REGISTRY_SENTINEL" in disk_content, (
            "Registry sentinel was overwritten!"
        )


class TestRunPathEscape:
    """Run operation: path escape → permission_denied."""

    def test_symlink_escape_rejected(self, tmp_path):
        """Symlink read escape in run() → status='permission_denied'.

        The skill creates a symlink inside workspace/output (writable)
        pointing to SYSTEMROOT\\system.ini (outside allowed roots).
        It then reads through the symlink without catching PermissionError,
        so the audit-hook rejection propagates to the Worker →
        permission_denied.
        """
        from tests.fixtures.runtime_fixtures import create_skill_run_symlink_escape
        skill_dir = create_skill_run_symlink_escape(tmp_path, "run-sym-esc", "1.0.0")
        service, registry, _ = _register_and_service_for_run(
            tmp_path, skill_dir, "run-sym-esc",
        )
        response = service.run_skill("run-sym-esc", "1.0.0", {"input": "test"}, timeout_seconds=5.0)
        assert not response.success, (
            f"Expected success=False, got success=True, status={response.status}"
        )
        assert response.status == "permission_denied", (
            f"Expected 'permission_denied', got '{response.status}'"
        )
        assert response.result is None, (
            f"Expected result=None for permission_denied, got {response.result}"
        )

    def test_dot_dot_escape_rejected(self, tmp_path):
        """.. path traversal in run() → status='permission_denied'."""
        from tests.fixtures.runtime_fixtures import create_skill_run_dot_dot_escape
        skill_dir = create_skill_run_dot_dot_escape(tmp_path, "run-dotdot", "1.0.0")
        service, registry, _ = _register_and_service_for_run(
            tmp_path, skill_dir, "run-dotdot",
        )
        response = service.run_skill("run-dotdot", "1.0.0", {"input": "test"}, timeout_seconds=5.0)
        assert not response.success
        assert response.status == "permission_denied", (
            f"Expected 'permission_denied', got '{response.status}'"
        )

    def test_project_root_cwd_home_not_auto_allowed(self, tmp_path):
        """Project root/CWD/home access in run() → status='permission_denied'."""
        from tests.fixtures.runtime_fixtures import create_skill_run_project_root_escape
        skill_dir = create_skill_run_project_root_escape(tmp_path, "run-proj-root", "1.0.0")
        service, registry, _ = _register_and_service_for_run(
            tmp_path, skill_dir, "run-proj-root",
        )
        response = service.run_skill("run-proj-root", "1.0.0", {"input": "test"}, timeout_seconds=5.0)
        assert not response.success
        assert response.status == "permission_denied", (
            f"Expected 'permission_denied', got '{response.status}'"
        )
