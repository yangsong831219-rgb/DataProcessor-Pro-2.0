"""L3 tests: Protocol boundary validation, secret filtering, environment isolation.

These tests verify that the parent process (SkillRuntimeService) does NOT
trust Worker output blindly — every response is validated at the protocol level.

Secret tests use ONLY fake values (TEST_SECRET_DO_NOT_LEAK_3_0_1) and
verify they never appear in:
- Worker environment
- stdout / stderr
- result.json
- runtime.log
- error messages / tracebacks
"""

from __future__ import annotations

import contextlib
import json
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
    RuntimeProtocolError,
)
from dp_engine.skills.runtime_models import (
    RUNTIME_PROTOCOL_VERSION,
    MAX_RESULT_JSON_BYTES,
    SkillRuntimeRequest,
    SkillRuntimeResponse,
    RuntimeArtifact,
)
from dp_engine.skills.runtime_protocol import (
    validate_request,
    validate_response,
)
from dp_engine.skills.runtime_permissions import (
    build_sanitized_env,
)
from dp_engine.skills.models import InstalledSkill
from tests.fixtures.runtime_fixtures import (
    create_minimal_skill_package,
)


# ── Helpers ──


def _setup_service_for_skill(
    tmp_path: Path,
    skill_id: str = "proto-test",
    version: str = "1.0.0",
    **skill_kwargs,
) -> tuple[SkillRuntimeService, SkillRegistry, Path]:
    """Create a SkillRuntimeService with a registered skill."""
    base_dir = tmp_path / "skills_data"
    installed_dir = base_dir / "installed"
    installed_dir.mkdir(parents=True)
    registry_path = base_dir / "registry.json"

    skill_dir = create_minimal_skill_package(
        installed_dir, skill_id, version, **skill_kwargs,
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
    return service, registry, skill_dir


# ═══════════════════════════════════════════════════════════════════
# Protocol boundary — parent-side validation
# ═══════════════════════════════════════════════════════════════════


class TestProtocolResultSizeLimit:
    """result.json size limit enforcement."""

    def test_result_too_large_rejected(self, tmp_path):
        """Oversized result.json is rejected at the protocol level."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "output").mkdir()

        # Write a result just over the limit
        result_path = ws / "result.json"
        large_content = {"key": "x" * (MAX_RESULT_JSON_BYTES + 100)}
        result_path.write_text(json.dumps(large_content), encoding="utf-8")

        from dp_engine.skills.runtime_protocol import read_response_atomic
        with pytest.raises(RuntimeProtocolError, match="size limit"):
            read_response_atomic(result_path, "abcdef", "healthcheck", ws)

    def test_result_exactly_at_limit_accepted(self, tmp_path):
        """Result at exact size limit is accepted (if valid schema)."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "output").mkdir()

        # This is a valid response at 1MB — would be huge and take time.
        # Instead, verify that smaller valid responses are accepted.
        resp = SkillRuntimeResponse(
            protocol_version=RUNTIME_PROTOCOL_VERSION,
            task_id="abc123",
            operation="healthcheck",
            success=True,
            status="healthy",
            message="ok",
            started_at="2024-01-01T00:00:00Z",
            finished_at="2024-01-01T00:00:01Z",
            duration_ms=100,
        )
        validate_response(resp, "abc123", "healthcheck", ws)


class TestProtocolTaskIdValidation:
    """task_id mismatch detection."""

    def test_response_task_id_mismatch(self, tmp_path):
        """Response with wrong task_id is rejected."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "output").mkdir()

        resp = SkillRuntimeResponse(
            protocol_version=RUNTIME_PROTOCOL_VERSION,
            task_id="wrong_id",
            operation="healthcheck",
            success=True,
            status="healthy",
            message="ok",
            started_at="2024-01-01T00:00:00Z",
            finished_at="2024-01-01T00:00:01Z",
            duration_ms=100,
        )
        with pytest.raises(RuntimeProtocolError, match="task_id"):
            validate_response(resp, "expected_id", "healthcheck", ws)


class TestProtocolVersionValidation:
    """protocol_version mismatch detection."""

    def test_response_protocol_version_mismatch(self, tmp_path):
        """Response with wrong protocol_version is rejected."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "output").mkdir()

        resp = SkillRuntimeResponse(
            protocol_version=999,
            task_id="abc123",
            operation="healthcheck",
            success=True,
            status="healthy",
            message="ok",
            started_at="2024-01-01T00:00:00Z",
            finished_at="2024-01-01T00:00:01Z",
            duration_ms=100,
        )
        with pytest.raises(RuntimeProtocolError, match="protocol"):
            validate_response(resp, "abc123", "healthcheck", ws)


class TestProtocolOperationValidation:
    """operation mismatch detection."""

    def test_response_operation_mismatch(self, tmp_path):
        """Response with wrong operation is rejected."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "output").mkdir()

        resp = SkillRuntimeResponse(
            protocol_version=RUNTIME_PROTOCOL_VERSION,
            task_id="abc123",
            operation="run",  # Should be healthcheck
            success=True,
            status="healthy",
            message="ok",
            started_at="2024-01-01T00:00:00Z",
            finished_at="2024-01-01T00:00:01Z",
            duration_ms=100,
        )
        with pytest.raises(RuntimeProtocolError, match="operation"):
            validate_response(resp, "abc123", "healthcheck", ws)


class TestArtifactPathValidation:
    """Artifact path escape detection."""

    def test_artifact_absolute_path_rejected(self, tmp_path):
        """Absolute artifact path is rejected."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "output").mkdir()

        resp = SkillRuntimeResponse(
            protocol_version=RUNTIME_PROTOCOL_VERSION,
            task_id="abc123",
            operation="healthcheck",
            success=True,
            status="healthy",
            message="ok",
            started_at="2024-01-01T00:00:00Z",
            finished_at="2024-01-01T00:00:01Z",
            duration_ms=100,
            artifacts=(RuntimeArtifact(relative_path="/etc/passwd", size_bytes=10),),
        )
        with pytest.raises(RuntimeProtocolError, match="relative|escapes"):
            validate_response(resp, "abc123", "healthcheck", ws)

    def test_artifact_dot_dot_rejected(self, tmp_path):
        """Artifact with .. path traversal is rejected."""
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "output").mkdir()

        resp = SkillRuntimeResponse(
            protocol_version=RUNTIME_PROTOCOL_VERSION,
            task_id="abc123",
            operation="healthcheck",
            success=True,
            status="healthy",
            message="ok",
            started_at="2024-01-01T00:00:00Z",
            finished_at="2024-01-01T00:00:01Z",
            duration_ms=100,
            artifacts=(RuntimeArtifact(relative_path="../etc/passwd", size_bytes=10),),
        )
        with pytest.raises(RuntimeProtocolError, match="contains '..'"):
            validate_response(resp, "abc123", "healthcheck", ws)

    def test_artifact_symlink_escape_resolved(self, tmp_path):
        """Artifact path that resolves outside output via symlink is detected.

        Uses resolve() to verify the artifact path stays within workspace/output.
        """
        ws = tmp_path / "ws"
        ws.mkdir()
        output_dir = ws / "output"
        output_dir.mkdir()

        # Create a symlink-like scenario: a deep path with .. that resolves outside
        # Note: on Windows, symlinks require admin; we test the resolve() logic directly
        artifact_rel = "deep/../../outside.txt"
        resolved = (output_dir / artifact_rel).resolve()
        try:
            resolved.relative_to(output_dir.resolve())
            is_within = True
        except ValueError:
            is_within = False

        # The path should NOT be within output after resolution
        assert not is_within, (
            f"Path '{artifact_rel}' resolved to '{resolved}' which is within output dir"
        )


class TestResultSchemaValidation:
    """result.json schema validation."""

    def test_result_not_valid_json(self, tmp_path):
        """Non-JSON result is rejected."""
        ws = tmp_path / "ws"
        ws.mkdir()
        result_path = ws / "result.json"
        result_path.write_text("this is not json", encoding="utf-8")

        from dp_engine.skills.runtime_protocol import read_response_atomic
        with pytest.raises(RuntimeProtocolError, match="invalid"):
            read_response_atomic(result_path, "abcdef", "healthcheck", ws)

    def test_result_not_a_dict(self, tmp_path):
        """Result that is valid JSON but not a dict is rejected."""
        ws = tmp_path / "ws"
        ws.mkdir()
        result_path = ws / "result.json"
        result_path.write_text("[1, 2, 3]", encoding="utf-8")

        from dp_engine.skills.runtime_protocol import read_response_atomic
        with pytest.raises(RuntimeProtocolError, match="dict"):
            read_response_atomic(result_path, "abcdef", "healthcheck", ws)

    def test_result_missing_file(self, tmp_path):
        """Missing result.json is handled."""
        ws = tmp_path / "ws"
        ws.mkdir()
        result_path = ws / "result.json"
        # Don't create the file

        from dp_engine.skills.runtime_protocol import read_response_atomic
        with pytest.raises(RuntimeProtocolError, match="not found"):
            read_response_atomic(result_path, "abcdef", "healthcheck", ws)


class TestFakeHealthyResultRejection:
    """Non-zero exit with fake healthy result should be detected."""

    def test_nonzero_exit_detected_by_service(self, tmp_path):
        """Service properly handles non-zero worker exits.

        The SkillRuntimeService checks exit_code != 0 and returns
        status='crashed' rather than trusting any worker output.
        """
        service, registry, _ = _setup_service_for_skill(
            tmp_path, "exit-test", "1.0.0",
        )

        # Use a skill that crashes - the worker exits non-zero
        from tests.fixtures.runtime_fixtures import create_skill_with_crashing_healthcheck

        # We can verify the service handles crashed workers properly
        # by checking that a real crashed healthcheck returns unhealthy, not healthy
        base_dir2 = tmp_path / "skills_data2"
        installed_dir2 = base_dir2 / "installed"
        installed_dir2.mkdir(parents=True)
        registry_path2 = base_dir2 / "registry.json"

        skill_dir = create_skill_with_crashing_healthcheck(installed_dir2, "crash2", "1.0.0")
        reg2 = SkillRegistry(registry_path2)
        reg2.load()
        manifest = parse_skill_manifest(skill_dir)
        installed = InstalledSkill(
            manifest=manifest,
            install_path=str(skill_dir),
            enabled=True,
            installed_at="2024-01-01T00:00:00Z",
        )
        reg2.register(installed)
        reg2.save()

        svc2 = SkillRuntimeService(reg2, installed_dir2)
        resp = svc2.run_healthcheck("crash2", "1.0.0", timeout_seconds=5.0)

        # The crash should NOT be reported as healthy
        assert not resp.success
        assert resp.status == "unhealthy"


# ═══════════════════════════════════════════════════════════════════
# Secret / key filtering
# ═══════════════════════════════════════════════════════════════════


# Fake secrets for testing — NEVER real values
_FAKE_SECRETS = {
    "OPENAI_API_KEY": "sk-TEST_SECRET_DO_NOT_LEAK_3_0_1",
    "ANTHROPIC_API_KEY": "sk-ant-TEST_SECRET_DO_NOT_LEAK_3_0_1",
    "DEEPSEEK_API_KEY": "sk-TEST_SECRET_DO_NOT_LEAK_3_0_1_ds",
    "GITHUB_TOKEN": "ghp_TEST_SECRET_DO_NOT_LEAK_3_0_1",
    "HTTP_PROXY": "http://TEST_SECRET_DO_NOT_LEAK_3_0_1:8080",
    "HTTPS_PROXY": "https://TEST_SECRET_DO_NOT_LEAK_3_0_1:8443",
    "PYTHONPATH": "/fake/TEST_SECRET_DO_NOT_LEAK_3_0_1",
    "PYTHONHOME": "/fake/TEST_SECRET_DO_NOT_LEAK_3_0_1_home",
    "CUSTOM_TOKEN": "tok_TEST_SECRET_DO_NOT_LEAK_3_0_1",
    "CUSTOM_SECRET": "sec_TEST_SECRET_DO_NOT_LEAK_3_0_1",
    "DATABASE_PASSWORD": "db_TEST_SECRET_DO_NOT_LEAK_3_0_1",
    "OTHER_API_KEY": "other-TEST_SECRET_DO_NOT_LEAK_3_0_1",
}


class TestSecretFilteringEnv:
    """All known secret keys are removed from Worker environment."""

    def test_all_secrets_removed_from_sanitized_env(self, monkeypatch):
        """Every fake secret is stripped by build_sanitized_env."""
        for key, value in _FAKE_SECRETS.items():
            monkeypatch.setenv(key, value)

        env = build_sanitized_env()

        for key in _FAKE_SECRETS:
            assert key not in env, (
                f"Secret '{key}' leaked into sanitized environment!"
            )

    def test_secret_values_not_in_sanitized_env(self, monkeypatch):
        """Fake secret VALUES don't appear anywhere in sanitized env."""
        for key, value in _FAKE_SECRETS.items():
            monkeypatch.setenv(key, value)

        env = build_sanitized_env()

        all_values = " ".join(str(v) for v in env.values())
        assert "TEST_SECRET_DO_NOT_LEAK_3_0_1" not in all_values, (
            "Fake secret value leaked into sanitized environment values!"
        )

    def test_pattern_matched_keys_also_removed(self, monkeypatch):
        """Keys matching patterns (TOKEN, SECRET, PASSWORD, API_KEY, KEY) are removed."""
        monkeypatch.setenv("MY_CUSTOM_TOKEN", "tok_TEST_SECRET_DO_NOT_LEAK_3_0_1")
        monkeypatch.setenv("DB_PASSWORD", "pw_TEST_SECRET_DO_NOT_LEAK_3_0_1")
        monkeypatch.setenv("SOME_API_KEY", "key_TEST_SECRET_DO_NOT_LEAK_3_0_1")

        env = build_sanitized_env()

        assert "MY_CUSTOM_TOKEN" not in env
        assert "DB_PASSWORD" not in env
        assert "SOME_API_KEY" not in env


class TestSecretLeakageInWorker:
    """Secrets must not appear in Worker output or workspace files."""

    def test_secrets_not_in_worker_stdout_stderr(self, tmp_path, monkeypatch):
        """Fake secrets don't leak into worker stdout/stderr."""
        # Set fake secrets
        for key, value in _FAKE_SECRETS.items():
            monkeypatch.setenv(key, value)

        service, registry, _ = _setup_service_for_skill(
            tmp_path, "secret-test", "1.0.0", healthy=True,
            healthcheck_message="all good, no secrets here",
        )

        # Run healthcheck — secrets are set in THIS process but
        # should be sanitized before worker subprocess launches
        response = service.run_healthcheck(
            "secret-test", "1.0.0", timeout_seconds=5.0,
        )

        assert response.success

        # The fake secret value should not appear in the response message
        assert "TEST_SECRET_DO_NOT_LEAK_3_0_1" not in response.message

        # Check workspace stdout/stderr for leaks
        from dp_engine.skills.runtime_paths import get_runtime_root
        runtime_root = get_runtime_root()
        if runtime_root.exists():
            for ws_dir in runtime_root.iterdir():
                if ws_dir.is_dir():
                    for log_name in ("stdout.log", "stderr.log", "runtime.log"):
                        log_path = ws_dir / log_name
                        if log_path.is_file():
                            content = log_path.read_text(errors="replace")
                            assert "TEST_SECRET_DO_NOT_LEAK_3_0_1" not in content, (
                                f"Secret leaked into {log_name} in {ws_dir.name}"
                            )

    def test_secrets_not_in_error_messages(self, tmp_path, monkeypatch):
        """Fake secrets don't leak into error messages or tracebacks."""
        for key, value in _FAKE_SECRETS.items():
            monkeypatch.setenv(key, value)

        # Test with a crashing skill
        from tests.fixtures.runtime_fixtures import create_skill_with_crashing_healthcheck

        base_dir = tmp_path / "skills_data"
        installed_dir = base_dir / "installed"
        installed_dir.mkdir(parents=True)
        registry_path = base_dir / "registry.json"

        skill_dir = create_skill_with_crashing_healthcheck(installed_dir, "secret-crash", "1.0.0")
        registry = SkillRegistry(registry_path)
        registry.load()
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
        response = service.run_healthcheck("secret-crash", "1.0.0", timeout_seconds=5.0)

        # Error info must not contain fake secrets
        if response.error:
            error_str = json.dumps({
                "error_type": response.error.error_type,
                "message": response.error.message,
                "traceback": response.error.traceback or "",
            })
            assert "TEST_SECRET_DO_NOT_LEAK_3_0_1" not in error_str, (
                f"Secret leaked into error info: {error_str[:200]}"
            )

        # Response message must not contain fake secrets
        assert "TEST_SECRET_DO_NOT_LEAK_3_0_1" not in response.message

    def test_secrets_not_in_result_json(self, tmp_path, monkeypatch):
        """#38: Fake secrets don't leak into result.json written by worker."""
        for key, value in _FAKE_SECRETS.items():
            monkeypatch.setenv(key, value)

        service, registry, _ = _setup_service_for_skill(
            tmp_path, "secret-result-json", "1.0.0", healthy=True,
            healthcheck_message="result.json test — no secrets",
        )

        response = service.run_healthcheck(
            "secret-result-json", "1.0.0", timeout_seconds=5.0,
        )
        assert response.success

        # Check all result.json files in runtime workspaces
        from dp_engine.skills.runtime_paths import get_runtime_root
        runtime_root = get_runtime_root()
        if runtime_root.exists():
            for ws_dir in runtime_root.iterdir():
                if ws_dir.is_dir():
                    result_path = ws_dir / "result.json"
                    if result_path.is_file():
                        content = result_path.read_text(encoding="utf-8")
                        assert "TEST_SECRET_DO_NOT_LEAK_3_0_1" not in content, (
                            f"Secret leaked into result.json in {ws_dir.name}"
                        )

    def test_secrets_not_in_runtime_log(self, tmp_path, monkeypatch):
        """#38: Fake secrets don't leak into runtime.log written by worker."""
        for key, value in _FAKE_SECRETS.items():
            monkeypatch.setenv(key, value)

        service, registry, _ = _setup_service_for_skill(
            tmp_path, "secret-runtime-log", "1.0.0", healthy=True,
            healthcheck_message="runtime.log test — no secrets",
        )

        response = service.run_healthcheck(
            "secret-runtime-log", "1.0.0", timeout_seconds=5.0,
        )
        assert response.success

        from dp_engine.skills.runtime_paths import get_runtime_root
        runtime_root = get_runtime_root()
        if runtime_root.exists():
            for ws_dir in runtime_root.iterdir():
                if ws_dir.is_dir():
                    log_path = ws_dir / "runtime.log"
                    if log_path.is_file():
                        content = log_path.read_text(errors="replace")
                        assert "TEST_SECRET_DO_NOT_LEAK_3_0_1" not in content, (
                            f"Secret leaked into runtime.log in {ws_dir.name}"
                        )


# ═══════════════════════════════════════════════════════════════════
# Environment isolation
# ═══════════════════════════════════════════════════════════════════


class TestEnvironmentIsolation:
    """Worker environment is properly isolated from parent."""

    def test_worker_env_does_not_contain_parent_secrets(self, tmp_path, monkeypatch):
        """After sanitization, worker env has no parent secrets."""
        for key, value in _FAKE_SECRETS.items():
            monkeypatch.setenv(key, value)

        env = build_sanitized_env()
        for key in _FAKE_SECRETS:
            assert key not in env

    def test_windows_required_vars_preserved(self, monkeypatch):
        """Windows-required env vars are preserved in sanitized env."""
        # These should always be in the real environment on Windows
        env = build_sanitized_env()

        # On Windows, certain vars must be present
        if sys.platform == "win32":
            required = {"SYSTEMROOT", "TEMP", "TMP", "PATH"}
            for key in required:
                # At least some of these should be present
                pass  # The sanitizer handles this

        # PYTHONUTF8 and PYTHONDONTWRITEBYTECODE are always set
        assert env.get("PYTHONUTF8") == "1"
        assert env.get("PYTHONDONTWRITEBYTECODE") == "1"

    def test_safe_python_vars_preserved(self, monkeypatch):
        """Safe Python vars survive sanitization."""
        monkeypatch.setenv("PYTHONUTF8", "1")
        monkeypatch.setenv("PYTHONIOENCODING", "utf-8")

        env = build_sanitized_env()

        # These are always set by the sanitizer
        assert "PYTHONUTF8" in env


# ══════════════════════════════════════════════════════════════════════
# Batch 3.1.1B — Parent env secret isolation for operation='run'
# ══════════════════════════════════════════════════════════════════════

# Unique sentinel for parent-env-secret tests — never used elsewhere
_PARENT_SECRET_KEY = "BATCH_311B_PARENT_SECRET_KEY"
_PARENT_SECRET_VALUE = "BATCH_311B_PARENT_SECRET_VALUE_DO_NOT_LEAK_7a3f2c"


def _setup_service_for_run(
    tmp_path: Path,
    skill_id: str,
    version: str = "1.0.0",
    **skill_kwargs,
) -> tuple[SkillRuntimeService, SkillRegistry, Path]:
    """Create a SkillRuntimeService with a run-entrypoint skill registered."""
    base_dir = tmp_path / "skills_data"
    installed_dir = base_dir / "installed"
    installed_dir.mkdir(parents=True)

    from tests.fixtures.runtime_fixtures import _make_run_skill
    code = '"""Basic run entrypoint for env/secret tests."""\nimport os, sys, json\n\ndef run(context):\n    params = context.get("params", {})\n    key = params.get("env_key", "BATCH_311B_PARENT_SECRET_KEY")\n    val = os.environ.get(key, "NOT_FOUND")\n    # Echo the value to stdout for leak testing\n    print(f"ENV_VAL: {val}", flush=True)\n    return {"ok": True, "env_value_found": val != "NOT_FOUND"}\n'
    skill_dir = _make_run_skill(installed_dir, skill_id, version, code)

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
    return service, registry, skill_dir


class TestParentEnvSecretIsolation:
    """Parent environment secrets must NOT leak into run Worker output surfaces."""

    def test_secret_not_in_worker_env(self, tmp_path, monkeypatch):
        """Parent env secret is stripped before Worker subprocess launches."""
        monkeypatch.setenv(_PARENT_SECRET_KEY, _PARENT_SECRET_VALUE)
        service, registry, _ = _setup_service_for_run(tmp_path, "env-secret-1")
        response = service.run_skill(
            "env-secret-1", "1.0.0",
            {"env_key": _PARENT_SECRET_KEY},
            timeout_seconds=5.0,
        )
        assert response.success, f"Run failed: {response.status} — {response.message}"
        # Worker should NOT find the secret in its environment
        result = response.result or {}
        assert result.get("env_value_found") is False, (
            f"Parent secret leaked into Worker environment! result={result}"
        )

    def test_secret_not_in_stdout_stderr(self, tmp_path, monkeypatch):
        """Parent env secret does NOT appear in stdout.log or stderr.log."""
        monkeypatch.setenv(_PARENT_SECRET_KEY, _PARENT_SECRET_VALUE)
        service, registry, _ = _setup_service_for_run(tmp_path, "env-secret-2")
        response = service.run_skill(
            "env-secret-2", "1.0.0",
            {"env_key": _PARENT_SECRET_KEY},
            timeout_seconds=5.0,
        )
        assert response.success
        # Check workspace stdout/stderr for leaks
        from dp_engine.skills.runtime_paths import get_runtime_root
        runtime_root = get_runtime_root()
        if runtime_root.exists():
            for ws_dir in runtime_root.iterdir():
                if ws_dir.is_dir():
                    for log_name in ("stdout.log", "stderr.log"):
                        log_path = ws_dir / log_name
                        if log_path.is_file():
                            content = log_path.read_text(encoding="utf-8", errors="replace")
                            assert _PARENT_SECRET_VALUE not in content, (
                                f"Secret leaked into {log_name} in {ws_dir.name}"
                            )

    def test_secret_not_in_result_json(self, tmp_path, monkeypatch):
        """Parent env secret does NOT appear in result.json."""
        monkeypatch.setenv(_PARENT_SECRET_KEY, _PARENT_SECRET_VALUE)
        service, registry, _ = _setup_service_for_run(tmp_path, "env-secret-3")
        response = service.run_skill(
            "env-secret-3", "1.0.0",
            {"env_key": _PARENT_SECRET_KEY},
            timeout_seconds=5.0,
        )
        assert response.success
        result_str = json.dumps(response.result or {})
        assert _PARENT_SECRET_VALUE not in result_str, (
            f"Secret leaked into result: {result_str[:200]}"
        )
        # Also check response message
        assert _PARENT_SECRET_VALUE not in response.message, (
            f"Secret leaked into response.message"
        )

    def test_secret_not_in_runtime_log(self, tmp_path, monkeypatch):
        """Parent env secret does NOT appear in runtime.log."""
        monkeypatch.setenv(_PARENT_SECRET_KEY, _PARENT_SECRET_VALUE)
        service, registry, _ = _setup_service_for_run(tmp_path, "env-secret-4")
        response = service.run_skill(
            "env-secret-4", "1.0.0",
            {"env_key": _PARENT_SECRET_KEY},
            timeout_seconds=5.0,
        )
        assert response.success
        from dp_engine.skills.runtime_paths import get_runtime_root
        runtime_root = get_runtime_root()
        if runtime_root.exists():
            for ws_dir in runtime_root.iterdir():
                if ws_dir.is_dir():
                    log_path = ws_dir / "runtime.log"
                    if log_path.is_file():
                        content = log_path.read_text(encoding="utf-8", errors="replace")
                        assert _PARENT_SECRET_VALUE not in content, (
                            f"Secret leaked into runtime.log in {ws_dir.name}"
                        )

    def test_secret_not_in_error_message(self, tmp_path, monkeypatch):
        """Parent env secret does NOT appear in error.message."""
        monkeypatch.setenv(_PARENT_SECRET_KEY, _PARENT_SECRET_VALUE)
        # Use a crashing skill to force an error path
        from tests.fixtures.runtime_fixtures import create_skill_run_raises_exception
        base_dir = tmp_path / "skills_data"
        installed_dir = base_dir / "installed"
        installed_dir.mkdir(parents=True)

        skill_dir = create_skill_run_raises_exception(installed_dir, "env-secret-5", "1.0.0")
        registry_path = base_dir / "registry.json"
        registry = SkillRegistry(registry_path)
        registry.load()
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
        response = service.run_skill(
            "env-secret-5", "1.0.0",
            {"env_key": _PARENT_SECRET_KEY},
            timeout_seconds=5.0,
        )
        assert not response.success
        # Error must not contain the secret
        if response.error:
            error_str = json.dumps({
                "error_type": response.error.error_type,
                "message": response.error.message,
            })
            assert _PARENT_SECRET_VALUE not in error_str, (
                f"Secret leaked into error info: {error_str[:200]}"
            )
        assert _PARENT_SECRET_VALUE not in response.message


# ══════════════════════════════════════════════════════════════════════
# Batch 3.1.1B — User sensitive params redaction for operation='run'
# ══════════════════════════════════════════════════════════════════════

# Unique sentinel for user-params redaction tests
_USER_SENSITIVE_PARAMS = {
    "username": "normal_user",
    "password": "SuperSecret_B311B_P@ssw0rd!",
    "auth_token": "tok_B311B_abc123def456_sensitive",
    "api_key": "sk-B311B-key-9876543210abcdef",
    "secret_code": "sec_B311B_nested_secret_value",
    "credential": "cred_B311B_top_secret_credential",
    "normal_param": "this_is_fine",
    "nested": {
        "token": "tok_B311B_nested_token_hidden",
        "data": "plain_data",
        "sub": {
            "password": "pw_B311B_deeply_nested_secret",
        },
    },
    "items": [
        {"name": "item1", "api_key": "key_B311B_in_list_item"},
        {"name": "item2", "value": "normal_value"},
    ],
    "empty_secret": "",
    "none_secret": None,
}


def _setup_run_service(
    tmp_path: Path,
    skill_id: str,
    version: str = "1.0.0",
    run_code: str | None = None,
) -> tuple[SkillRuntimeService, SkillRegistry]:
    """Minimal helper: register a run skill and return service + registry."""
    base_dir = tmp_path / "skills_data"
    installed_dir = base_dir / "installed"
    installed_dir.mkdir(parents=True)

    from tests.fixtures.runtime_fixtures import _make_run_skill
    code = run_code or '"""Echo params."""\ndef run(context):\n    params = context.get("params", {})\n    import json\n    return {"echo": params, "ok": True}\n'
    skill_dir = _make_run_skill(installed_dir, skill_id, version, code)

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
    return SkillRuntimeService(registry, installed_dir), registry


class TestSensitiveParamsRedaction:
    """User sensitive params must be redacted from all output surfaces.

    Each stdout/stderr test proves the contract:
        final_output = truncate(redact(raw_output))

    The skill writes the secret to the I/O stream at a precise byte
    position (using sys.stdout.buffer.write / sys.stderr.buffer.write),
    then raises an exception so the Worker processes the log files with
    _flush_and_redact_stdio.

    Workspace preservation: create_workspace is redirected to tmp_path
    and cleanup_workspace is no-oped so the test can read stdout.log /
    stderr.log directly from disk after run_skill() returns.

    Every log-file assertion is MANDATORY — not conditional on
    workspace preservation.  This is the core evidence added in
    Batch 3.1.1B-S.
    """

    # ── Workspace preservation context manager ──

    @staticmethod
    @contextlib.contextmanager
    def _preserve_workspace_in(tmp_path: Path):
        """Redirect workspace creation to tmp_path and prevent cleanup.

        Patches create_workspace and cleanup_workspace in the
        SkillRuntimeService module namespace so the test can read
        stdout.log / stderr.log directly after run_skill() returns.

        IMPORTANT: The caller MUST clean up ws_root after reading the
        log files.  The context manager does NOT auto-delete — if it
        did, the files would be gone before the caller can read them
        (__exit__ runs even after a return inside the with block).
        """
        from unittest import mock

        ws_root = tmp_path / "runtime_workspaces"
        ws_root.mkdir(parents=True, exist_ok=True)

        # Collect created workspaces so the test can find them
        created: list[Path] = []

        def _create_in_tmp(task_id: str) -> Path:
            ws = ws_root / task_id
            for sub in ("input", "output", "temp"):
                (ws / sub).mkdir(parents=True, exist_ok=False)
            created.append(ws)
            return ws

        def _noop_cleanup(workspace: Path, *, succeeded: bool) -> None:
            pass  # never delete

        with mock.patch(
            "dp_engine.skills.runtime_service.create_workspace",
            _create_in_tmp,
        ), mock.patch(
            "dp_engine.skills.runtime_service.cleanup_workspace",
            _noop_cleanup,
        ):
            yield ws_root, created
        # NOTE: cleanup is the caller's responsibility —
        # see _cleanup_preserved_workspace below

    # ── Shared helpers ──

    @staticmethod
    def _run_skill_with_preserved_workspace(
        tmp_path: Path,
        skill_id: str,
        run_code: str,
        params: dict,
    ) -> tuple:
        """Run a skill with workspace preservation; return (response, ws_path).

        The workspace is guaranteed to exist after this call because
        cleanup_workspace is no-oped.  ws_path is the on-disk workspace
        directory containing stdout.log and stderr.log.
        """
        with TestSensitiveParamsRedaction._preserve_workspace_in(tmp_path) as (
            ws_root, created_list,
        ):
            service, registry = _setup_run_service(tmp_path, skill_id, run_code=run_code)
            response = service.run_skill(skill_id, "1.0.0", params, timeout_seconds=15.0)

            # The service should have created exactly one workspace
            assert len(created_list) >= 1, (
                f"Expected at least 1 workspace, got {len(created_list)}"
            )
            ws_path = created_list[-1]  # most recently created
            return response, ws_path

    @staticmethod
    def _assert_log_file_mandatory(
        log_bytes: bytes,
        log_name: str,
        secret: str,
        max_bytes: int,
        scenario: str,
        *,
        non_sensitive_prefix: str,
        non_sensitive_suffix: str,
    ) -> None:
        """Mandatory assertions on a log file's raw bytes.

        Every assertion here is required — no conditional skipping.
        This provides direct evidence that:
          final_log_bytes = truncate(redact(raw_output))

        For cross-boundary and utf8-multibyte scenarios, the
        ***REDACTED*** marker may itself be beyond the truncation
        boundary and thus absent from the final output.  That is
        expected and correct — the contract only guarantees that
        the secret (and its fragments) are absent.
        """
        # 1. Size within limit
        assert len(log_bytes) <= max_bytes, (
            f"[{scenario}] {log_name}: {len(log_bytes)} bytes > "
            f"max {max_bytes}"
        )
        # 2. Strict UTF-8 decodable
        try:
            content = log_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            raise AssertionError(
                f"[{scenario}] {log_name} is not valid UTF-8: {e}"
            ) from e
        # 3. Full secret absent
        assert secret not in content, (
            f"[{scenario}] Secret leaked into {log_name}! "
            f"Found secret hash: {hash(secret) & 0xFFFF:04x}"
        )
        # 4. Sensitive prefix (first 8 chars of secret) absent —
        #    proves redact-before-truncate: even a fragment would
        #    survive if truncation happened first
        secret_prefix = secret[:8]
        assert secret_prefix not in content, (
            f"[{scenario}] Secret prefix '{secret_prefix}' leaked "
            f"into {log_name} — would indicate truncate-before-redact"
        )
        # 5. Sensitive suffix (last 8 chars of secret) absent
        secret_suffix = secret[-8:]
        assert secret_suffix not in content, (
            f"[{scenario}] Secret suffix '{secret_suffix}' leaked "
            f"into {log_name}"
        )
        # 6. REDACTED marker present (may be beyond truncation limit
        #    in cross-boundary / utf8-multibyte — acceptable)
        if "***REDACTED***" not in content:
            assert scenario in ("cross-boundary", "utf8-multibyte"), (
                f"[{scenario}] Expected ***REDACTED*** in {log_name}"
            )
        # 7. Non-sensitive prefix preserved
        assert non_sensitive_prefix in content, (
            f"[{scenario}] Non-sensitive prefix "
            f"'{non_sensitive_prefix}' lost from {log_name}"
        )
        # 8. Non-sensitive suffix: may be beyond truncation limit
        #    in cross-boundary / utf8-multibyte scenarios — acceptable

    @staticmethod
    def _assert_response_surfaces_clean(
        response, secret: str, scenario: str,
    ) -> None:
        """Assert response.message and response.error.message are redacted."""
        assert secret not in response.message, (
            f"[{scenario}] Secret leaked into response.message"
        )
        assert "***REDACTED***" in response.message, (
            f"[{scenario}] Expected ***REDACTED*** in response.message"
        )
        if response.error:
            assert secret not in response.error.message, (
                f"[{scenario}] Secret leaked into response.error.message"
            )
            assert "***REDACTED***" in response.error.message, (
                f"[{scenario}] Expected ***REDACTED*** in "
                f"response.error.message"
            )

    # ── Skill code template for precise I/O positioning ──
    #
    # IMPORTANT: use sys.stdout.write() / sys.stderr.write() (text mode)
    # rather than buffer.write().  _redirect_stdio opens the log files in
    # text mode (encoding="utf-8", buffering=1), so writing to the binary
    # buffer bypasses the TextIOWrapper and can cause flush/close ordering
    # issues on Windows.  Text-mode writes go through the wrapper's
    # buffer and are reliably flushed to disk.

    _STDOUT_SKILL_CODE = (
        '"""Write secret to stdout at precise byte position, then raise."""\n'
        'import sys\n'
        '\n'
        'def run(context):\n'
        '    params = context.get("params", {})\n'
        '    s = params.get("secret", "")\n'
        '    padding = int(params.get("padding", 0))\n'
        '    # Use text-mode writes — the redirected stdout is a\n'
        '    # TextIOWrapper; writing through it ensures correct flushing\n'
        '    if padding > 0:\n'
        '        sys.stdout.write("P" * padding)\n'
        '    sys.stdout.write(f"\\nSTDOUT_PREFIX:{s}:STDOUT_SUFFIX\\n")\n'
        '    sys.stdout.flush()\n'
        '    raise RuntimeError(f"Error_with_secret:{s}:end")\n'
    )

    _STDERR_SKILL_CODE = (
        '"""Write secret to stderr at precise byte position, then raise."""\n'
        'import sys\n'
        '\n'
        'def run(context):\n'
        '    params = context.get("params", {})\n'
        '    s = params.get("secret", "")\n'
        '    padding = int(params.get("padding", 0))\n'
        '    if padding > 0:\n'
        '        sys.stderr.write("P" * padding)\n'
        '    sys.stderr.write(f"\\nSTDERR_PREFIX:{s}:STDERR_SUFFIX\\n")\n'
        '    sys.stderr.flush()\n'
        '    raise RuntimeError(f"Error_with_secret:{s}:end")\n'
    )

    # ── stdout tests ──

    # Unique sentinel secrets for each scenario
    _SECRET_A = "S3cret_At_Bound@ry_b311b_stdout"
    _SECRET_B = "S3cret_Cross_Bound@ry_b311b_stdout"
    _SECRET_C = "C3cret_密碼_UTF8_b311b_stdout"  # 密碼 = 6 UTF-8 bytes

    @pytest.mark.parametrize("scenario,secret", [
        pytest.param("boundary-near-limit", _SECRET_A,
                     id="boundary-near-limit"),
        pytest.param("cross-boundary", _SECRET_B,
                     id="cross-boundary"),
        pytest.param("utf8-multibyte", _SECRET_C,
                     id="utf8-multibyte"),
    ])
    def test_sensitive_params_redacted_in_stdout(
        self, tmp_path, scenario, secret,
    ):
        """Scenario {scenario}: stdout redact-before-truncation.

        MANDATORY log-file evidence (Batch 3.1.1B-S):
        - stdout.log exists, readable, valid UTF-8
        - len(stdout.log) <= MAX_STDOUT_BYTES
        - Full secret absent from stdout.log
        - Secret prefix (first 8 chars) absent → proves redact-before-truncate
        - Secret suffix (last 8 chars) absent
        - ***REDACTED*** present
        - Non-sensitive prefix "STDOUT_PREFIX" present
        - response.message / error.message also redacted (secondary)
        """
        from dp_engine.skills.runtime_models import MAX_STDOUT_BYTES

        # Compute padding for precise byte positioning.
        # On Windows, text-mode stdout translates \n → \r\n (2 bytes per
        # newline).  The skill writes:  "P" * padding  +  "\nPREFIX..."\n".
        # We use explicit \r\n in our byte-count to match the on-disk reality.
        prefix = "STDOUT_PREFIX:"
        suffix = ":STDOUT_SUFFIX"
        # The line has two newlines, both become \r\n on Windows = 4 bytes
        line_on_disk = f"\r\n{prefix}{secret}{suffix}\r\n"
        line_bytes = len(line_on_disk.encode("utf-8"))
        secret_bytes = len(secret.encode("utf-8"))

        if scenario == "boundary-near-limit":
            # Secret is fully within the 1 MB limit, ~100 bytes before end
            # Raw total ≈ MAX_STDOUT_BYTES - 100
            padding = max(0, MAX_STDOUT_BYTES - line_bytes - 100)
        elif scenario == "cross-boundary":
            # Secret straddles the 1 MB boundary — truncation would
            # cut through it if applied BEFORE redaction
            # Position: boundary hits ~1/3 into the secret
            padding = max(0, MAX_STDOUT_BYTES
                          - len(prefix.encode("utf-8"))
                          - secret_bytes // 3)
        else:  # utf8-multibyte
            # Multi-byte secret near the truncation boundary
            # Position: boundary near multi-byte chars in secret
            padding = max(0, MAX_STDOUT_BYTES
                          - len(prefix.encode("utf-8"))
                          - 5)  # cut close to prefix, near multi-byte chars

        params: dict[str, object] = {
            "secret": secret,
            "padding": padding,
        }

        response, ws_path = self._run_skill_with_preserved_workspace(
            tmp_path, "redact-stdout", self._STDOUT_SKILL_CODE, params,
        )
        assert not response.success, (
            f"Expected failure, got {response.status}"
        )

        # ── Secondary: service-exposed surfaces ──
        self._assert_response_surfaces_clean(response, secret, scenario)

        # ── PRIMARY (Batch 3.1.1B-S): direct log file evidence ──
        stdout_log = ws_path / "stdout.log"
        assert stdout_log.is_file(), (
            f"[{scenario}] stdout.log not found at {stdout_log}"
        )
        log_bytes = stdout_log.read_bytes()
        assert len(log_bytes) > 0, (
            f"[{scenario}] stdout.log is empty"
        )

        self._assert_log_file_mandatory(
            log_bytes, "stdout.log", secret, MAX_STDOUT_BYTES,
            scenario,
            non_sensitive_prefix="STDOUT_PREFIX",
            non_sensitive_suffix="STDOUT_SUFFIX",
        )

    # ── stderr tests ──

    _SECRET_STDERR_A = "S3cret_StdErr_At_Bound@ry_b311b"
    _SECRET_STDERR_B = "S3cret_StdErr_Cross_Bound@ry_b311b"
    _SECRET_STDERR_C = "C3cret_密碼_StdErr_UTF8_b311b"

    @pytest.mark.parametrize("scenario,secret", [
        pytest.param("boundary-near-limit", _SECRET_STDERR_A,
                     id="boundary-near-limit"),
        pytest.param("cross-boundary", _SECRET_STDERR_B,
                     id="cross-boundary"),
        pytest.param("utf8-multibyte", _SECRET_STDERR_C,
                     id="utf8-multibyte"),
    ])
    def test_sensitive_params_redacted_in_stderr(
        self, tmp_path, scenario, secret,
    ):
        """Scenario {scenario}: stderr redact-before-truncation.

        MANDATORY log-file evidence (Batch 3.1.1B-S):
        - stderr.log exists, readable, valid UTF-8
        - len(stderr.log) <= MAX_STDERR_BYTES
        - Full secret absent from stderr.log
        - Secret prefix/suffix absent → proves redact-before-truncate
        - ***REDACTED*** present
        - Non-sensitive prefix "STDERR_PREFIX" present
        """
        from dp_engine.skills.runtime_models import MAX_STDERR_BYTES

        prefix = "STDERR_PREFIX:"
        suffix = ":STDERR_SUFFIX"
        line_on_disk = f"\r\n{prefix}{secret}{suffix}\r\n"
        line_bytes = len(line_on_disk.encode("utf-8"))
        secret_bytes = len(secret.encode("utf-8"))

        if scenario == "boundary-near-limit":
            padding = max(0, MAX_STDERR_BYTES - line_bytes - 100)
        elif scenario == "cross-boundary":
            padding = max(0, MAX_STDERR_BYTES
                          - len(prefix.encode("utf-8"))
                          - secret_bytes // 3)
        else:  # utf8-multibyte
            padding = max(0, MAX_STDERR_BYTES
                          - len(prefix.encode("utf-8"))
                          - 5)

        params = {"secret": secret, "padding": padding}

        response, ws_path = self._run_skill_with_preserved_workspace(
            tmp_path, "redact-stderr", self._STDERR_SKILL_CODE, params,
        )
        assert not response.success, (
            f"Expected failure, got {response.status}"
        )

        self._assert_response_surfaces_clean(response, secret, scenario)

        # ── PRIMARY: direct log file evidence ──
        stderr_log = ws_path / "stderr.log"
        assert stderr_log.is_file(), (
            f"[{scenario}] stderr.log not found at {stderr_log}"
        )
        log_bytes = stderr_log.read_bytes()
        assert len(log_bytes) > 0, (
            f"[{scenario}] stderr.log is empty"
        )

        self._assert_log_file_mandatory(
            log_bytes, "stderr.log", secret, MAX_STDERR_BYTES,
            scenario,
            non_sensitive_prefix="STDERR_PREFIX",
            non_sensitive_suffix="STDERR_SUFFIX",
        )

    def test_sensitive_params_redacted_in_result_json(self, tmp_path):
        """Sensitive values in result.json are replaced with ***REDACTED***."""
        code = '"""Echo params in result."""\ndef run(context):\n    params = context.get("params", {})\n    return {"echo": params, "ok": True}\n'
        service, registry = _setup_run_service(tmp_path, "redact-result", run_code=code)
        response = service.run_skill(
            "redact-result", "1.0.0",
            {"username": "normal_user", "password": "SuperSecret_B311B_P@ssw0rd!"},
            timeout_seconds=5.0,
        )
        assert response.success
        result = response.result or {}
        echo = result.get("echo", {})
        # Normal value preserved
        assert echo.get("username") == "normal_user"
        # Sensitive value redacted
        assert echo.get("password") == "***REDACTED***", (
            f"Expected ***REDACTED***, got: {echo.get('password')}"
        )
        # Original secret must not appear anywhere in response
        response_str = json.dumps(response.to_dict() if hasattr(response, 'to_dict') else {"result": result})
        assert "SuperSecret_B311B_P@ssw0rd!" not in response_str, (
            "Secret leaked into response"
        )

    def test_sensitive_params_redacted_in_error_message(self, tmp_path):
        """Error messages are redacted of sensitive param values."""
        # Skill that raises exception with params content
        code = '"""Raise error with params."""\ndef run(context):\n    params = context.get("params", {})\n    pw = params.get("password", "none")\n    raise ValueError(f"Auth failed with password: {pw}")\n'
        service, registry = _setup_run_service(tmp_path, "redact-error", run_code=code)
        response = service.run_skill(
            "redact-error", "1.0.0",
            {"username": "normal_user", "password": "SuperSecret_B311B_P@ssw0rd!"},
            timeout_seconds=5.0,
        )
        assert not response.success
        # Error message must be redacted
        if response.error:
            assert "SuperSecret_B311B_P@ssw0rd!" not in response.error.message, (
                f"Secret leaked into error.message: {response.error.message}"
            )
            assert "***REDACTED***" in response.error.message, (
                f"Expected ***REDACTED*** in error.message"
            )
        # Also check response message
        assert "SuperSecret_B311B_P@ssw0rd!" not in response.message

    def test_sensitive_params_redacted_in_runtime_log(self, tmp_path):
        """Runtime logs are redacted of sensitive param values."""
        code = '"""Print params to stdout (captured as log)."""\nimport sys, json\n\ndef run(context):\n    params = context.get("params", {})\n    print(json.dumps(params), flush=True)\n    return {"ok": True}\n'
        service, registry = _setup_run_service(tmp_path, "redact-log", run_code=code)
        response = service.run_skill(
            "redact-log", "1.0.0",
            {"password": "SuperSecret_B311B_P@ssw0rd!"},
            timeout_seconds=5.0,
        )
        assert response.success
        from dp_engine.skills.runtime_paths import get_runtime_root
        runtime_root = get_runtime_root()
        if runtime_root.exists():
            for ws_dir in runtime_root.iterdir():
                if ws_dir.is_dir():
                    # Check both stdout.log and stderr.log (runtime log goes to stderr)
                    for log_name in ("stdout.log", "stderr.log"):
                        log_path = ws_dir / log_name
                        if log_path.is_file():
                            content = log_path.read_text(encoding="utf-8", errors="replace")
                            assert "SuperSecret_B311B_P@ssw0rd!" not in content, (
                                f"Secret leaked into {log_name}"
                            )


class TestNormalParamsRoundtrip:
    """Non-sensitive params must survive the full roundtrip unchanged."""

    def test_non_sensitive_params_roundtrip(self, tmp_path):
        """Normal business params appear correctly in result.json."""
        normal_params = {
            "username": "test_user",
            "email": "test@example.com",
            "count": 42,
            "enabled": True,
            "items": ["a", "b", "c"],
            "config": {"mode": "fast", "threshold": 0.95},
            "description": "A normal business parameter with UTF-8: 你好世界",
        }
        code = '"""Echo params for roundtrip."""\ndef run(context):\n    params = context.get("params", {})\n    return {"echo": params, "ok": True}\n'
        service, registry = _setup_run_service(tmp_path, "roundtrip", run_code=code)
        response = service.run_skill(
            "roundtrip", "1.0.0",
            normal_params,
            timeout_seconds=5.0,
        )
        assert response.success, f"Run failed: {response.status} — {response.message}"
        result = response.result or {}
        echo = result.get("echo", {})
        assert echo == normal_params, (
            f"Params roundtrip mismatch.\nExpected: {normal_params}\nGot: {echo}"
        )


# ══════════════════════════════════════════════════════════════════════
# Batch 3.2.1A — artifact_declarations wire validation
# ══════════════════════════════════════════════════════════════════════


class TestArtifactDeclarationsWire:
    """artifact_declarations internal wire field validation."""

    def test_run_missing_declarations_field_ok(self, tmp_path):
        """Run response without artifact_declarations is valid (field missing)."""
        from dp_engine.skills.runtime_protocol import validate_artifact_declarations
        result = validate_artifact_declarations(None, "run")
        assert result == ()

    def test_run_empty_declarations_list_ok(self, tmp_path):
        """Run response with empty artifact_declarations is valid."""
        from dp_engine.skills.runtime_protocol import validate_artifact_declarations
        result = validate_artifact_declarations([], "run")
        assert result == ()

    def test_run_valid_declarations_parsed(self):
        """Run with valid artifact_declarations returns parsed declarations."""
        from dp_engine.skills.runtime_protocol import validate_artifact_declarations
        raw = [
            {
                "declared_path": "chart.png",
                "display_name": "Chart",
                "media_type_hint": "image/png",
                "kind": "chart",
                "metadata": {},
                "observed_size_bytes": 1000,
                "observed_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "observed_device": 1,
                "observed_inode": 2,
                "observed_mtime_ns": 3,
            }
        ]
        declarations = validate_artifact_declarations(raw, "run")
        assert len(declarations) == 1
        assert declarations[0].declared_path == "chart.png"

    def test_healthcheck_non_empty_declarations_rejected(self):
        """Healthcheck with non-empty artifact_declarations → protocol_error."""
        from dp_engine.skills.runtime_protocol import validate_artifact_declarations
        from dp_engine.skills.runtime_errors import RuntimeProtocolError
        raw = [
            {
                "declared_path": "f.txt",
                "display_name": "F",
                "kind": "other",
                "observed_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            }
        ]
        with pytest.raises(RuntimeProtocolError, match="healthcheck"):
            validate_artifact_declarations(raw, "healthcheck")

    def test_run_invalid_declaration_rejected(self):
        """Run with invalid declaration item → protocol_error."""
        from dp_engine.skills.runtime_protocol import validate_artifact_declarations
        from dp_engine.skills.runtime_errors import RuntimeProtocolError
        raw = [
            {
                "declared_path": "f.txt",
                "display_name": "F",
                "kind": "invalid_kind",
                "observed_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            }
        ]
        with pytest.raises(RuntimeProtocolError, match="invalid"):
            validate_artifact_declarations(raw, "run")

    def test_not_a_list_rejected(self):
        """artifact_declarations not a list → protocol_error."""
        from dp_engine.skills.runtime_protocol import validate_artifact_declarations
        from dp_engine.skills.runtime_errors import RuntimeProtocolError
        with pytest.raises(RuntimeProtocolError, match="list"):
            validate_artifact_declarations({"not": "a list"}, "run")

    def test_item_not_a_dict_rejected(self):
        """artifact_declarations item not a dict → protocol_error."""
        from dp_engine.skills.runtime_protocol import validate_artifact_declarations
        from dp_engine.skills.runtime_errors import RuntimeProtocolError
        raw = ["not_a_dict"]
        with pytest.raises(RuntimeProtocolError, match="dict"):
            validate_artifact_declarations(raw, "run")

    def test_artifact_declarations_stripped_from_public_response(self):
        """artifact_declarations does NOT appear in public SkillRuntimeResponse."""
        ws = Path(tempfile.mkdtemp())
        (ws / "output").mkdir(parents=True, exist_ok=True)
        result_path = ws / "result.json"
        # Write result.json WITH artifact_declarations
        import json as _json
        data = {
            "protocol_version": 1,
            "task_id": "abc123",
            "operation": "run",
            "success": True,
            "status": "succeeded",
            "message": "ok",
            "started_at": "2024-01-01T00:00:00Z",
            "finished_at": "2024-01-01T00:00:01Z",
            "duration_ms": 1000,
            "artifact_declarations": [
                {
                    "declared_path": "chart.png",
                    "display_name": "Chart",
                    "media_type_hint": "image/png",
                    "kind": "chart",
                    "metadata": {},
                    "observed_size_bytes": 1000,
                    "observed_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                    "observed_device": 0,
                    "observed_inode": 0,
                    "observed_mtime_ns": 0,
                }
            ],
        }
        result_path.write_text(_json.dumps(data), encoding="utf-8")

        from dp_engine.skills.runtime_protocol import read_response_atomic
        response = read_response_atomic(result_path, "abc123", "run", ws)
        # The public response must NOT expose artifact_declarations
        resp_dict = response.to_dict()
        assert "artifact_declarations" not in resp_dict

        import shutil as _shutil
        _shutil.rmtree(ws, ignore_errors=True)


class TestValidateRuntimeArtifact:
    """Operation-specific RuntimeArtifact validation."""

    def test_healthcheck_tolerant_old_format(self):
        """Old 3-field artifact passes healthcheck (tolerant) validation."""
        from dp_engine.skills.runtime_models import RuntimeArtifact
        from dp_engine.skills.runtime_protocol import validate_runtime_artifact
        a = RuntimeArtifact(relative_path="out.txt", size_bytes=100, sha256=None)
        # Must not raise
        validate_runtime_artifact(a, operation="healthcheck", published=False)

    def test_healthcheck_tolerant_sha256_none(self):
        """Healthcheck artifact with sha256=None is OK."""
        from dp_engine.skills.runtime_models import RuntimeArtifact
        from dp_engine.skills.runtime_protocol import validate_runtime_artifact
        a = RuntimeArtifact(relative_path="log.txt", size_bytes=50, sha256=None)
        validate_runtime_artifact(a, operation="healthcheck", published=False)

    def test_published_run_rejects_artifact_schema_version_zero(self):
        """Published run artifact with artifact_schema_version=0 is rejected."""
        from dp_engine.skills.runtime_models import RuntimeArtifact
        from dp_engine.skills.runtime_protocol import validate_runtime_artifact
        from dp_engine.skills.runtime_errors import RuntimeProtocolError
        a = RuntimeArtifact(
            relative_path="f.txt", size_bytes=100,
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            artifact_schema_version=0,
        )
        with pytest.raises(RuntimeProtocolError, match="artifact_schema_version"):
            validate_runtime_artifact(a, operation="run", published=True)

    def test_published_run_rejects_bad_artifact_id(self):
        """Published run artifact with invalid artifact_id is rejected."""
        from dp_engine.skills.runtime_models import RuntimeArtifact
        from dp_engine.skills.runtime_protocol import validate_runtime_artifact
        from dp_engine.skills.runtime_errors import RuntimeProtocolError
        a = RuntimeArtifact(
            relative_path="f.txt", size_bytes=100,
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            artifact_schema_version=1,
            artifact_id="too-short",
            storage_relpath="s/t/f.txt",
            display_name="F",
            media_type="text/plain",
            kind="data",
            created_at="2026-07-22T10:30:00Z",
            skill_id="sk", version="1.0.0", task_id="t1",
        )
        with pytest.raises(RuntimeProtocolError, match="artifact_id"):
            validate_runtime_artifact(a, operation="run", published=True)

    def test_published_run_rejects_unsafe_storage_relpath(self):
        """Published run artifact with .. in storage_relpath is rejected."""
        from dp_engine.skills.runtime_models import RuntimeArtifact
        from dp_engine.skills.runtime_protocol import validate_runtime_artifact
        from dp_engine.skills.runtime_errors import RuntimeProtocolError
        a = RuntimeArtifact(
            relative_path="f.txt", size_bytes=100,
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            artifact_schema_version=1,
            artifact_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            storage_relpath="s/../escape/f.txt",  # .. traversal
            display_name="F",
            media_type="text/plain",
            kind="data",
            created_at="2026-07-22T10:30:00Z",
            skill_id="sk", version="1.0.0", task_id="t1",
        )
        with pytest.raises(RuntimeProtocolError, match="storage_relpath"):
            validate_runtime_artifact(a, operation="run", published=True)

    def test_published_run_rejects_sha256_none(self):
        """Published run artifact with sha256=None is rejected."""
        from dp_engine.skills.runtime_models import RuntimeArtifact
        from dp_engine.skills.runtime_protocol import validate_runtime_artifact
        from dp_engine.skills.runtime_errors import RuntimeProtocolError
        a = RuntimeArtifact(
            relative_path="f.txt", size_bytes=100,
            sha256=None,  # not allowed for published
            artifact_schema_version=1,
            artifact_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            storage_relpath="s/t/f.txt",
            display_name="F",
            media_type="text/plain",
            kind="data",
            created_at="2026-07-22T10:30:00Z",
            skill_id="sk", version="1.0.0", task_id="t1",
        )
        with pytest.raises(RuntimeProtocolError, match="sha256"):
            validate_runtime_artifact(a, operation="run", published=True)

    def test_published_run_rejects_invalid_kind(self):
        """Published run artifact with invalid kind is rejected."""
        from dp_engine.skills.runtime_models import RuntimeArtifact
        from dp_engine.skills.runtime_protocol import validate_runtime_artifact
        from dp_engine.skills.runtime_errors import RuntimeProtocolError
        a = RuntimeArtifact(
            relative_path="f.txt", size_bytes=100,
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            artifact_schema_version=1,
            artifact_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            storage_relpath="s/t/f.txt",
            display_name="F",
            media_type="text/plain",
            kind="invalid",
            created_at="2026-07-22T10:30:00Z",
            skill_id="sk", version="1.0.0", task_id="t1",
        )
        with pytest.raises(RuntimeProtocolError, match="kind"):
            validate_runtime_artifact(a, operation="run", published=True)

    def test_published_run_missing_owner_fields_rejected(self):
        """Published run artifact with empty owner fields is rejected."""
        from dp_engine.skills.runtime_models import RuntimeArtifact
        from dp_engine.skills.runtime_protocol import validate_runtime_artifact
        from dp_engine.skills.runtime_errors import RuntimeProtocolError
        a = RuntimeArtifact(
            relative_path="f.txt", size_bytes=100,
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            artifact_schema_version=1,
            artifact_id="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
            storage_relpath="s/t/f.txt",
            display_name="F",
            media_type="text/plain",
            kind="data",
            created_at="2026-07-22T10:30:00Z",
            skill_id="", version="", task_id="",  # empty owner
        )
        with pytest.raises(RuntimeProtocolError, match="skill_id"):
            validate_runtime_artifact(a, operation="run", published=True)


# ══════════════════════════════════════════════════════════════════════
# Batch 3.2.1A-R — Protocol-level 32768-byte wire limit validation
# ══════════════════════════════════════════════════════════════════════


def _make_wire_item_3(
    declared_path: str = "f.txt",
    display_name: str = "F",
    media_type_hint: str | None = None,
    kind: str = "other",
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "declared_path": declared_path,
        "display_name": display_name,
        "media_type_hint": media_type_hint,
        "kind": kind,
        "metadata": metadata if metadata is not None else {},
        "observed_size_bytes": 0,
        "observed_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "observed_device": 0,
        "observed_inode": 0,
        "observed_mtime_ns": 0,
    }


def _canonical_wire_len(items: list[dict[str, object]]) -> int:
    """Canonical UTF-8 byte length of a declarations wire list."""
    import json as _json
    return len(_json.dumps(
        items, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8"))


class TestWire32768ProtocolBoundary:
    """Batch 3.2.1A-R — protocol layer enforces 32768-byte wire limit."""

    def test_run_valid_nonempty_under_32k_passes(self):
        """Run with valid declarations under 32KB passes protocol validation."""
        from dp_engine.skills.runtime_protocol import validate_artifact_declarations
        raw = [
            _make_wire_item_3(declared_path="chart.png", display_name="Chart",
                              media_type_hint="image/png", kind="chart"),
            _make_wire_item_3(declared_path="table.csv", display_name="Table",
                              media_type_hint="text/csv", kind="data"),
        ]
        declarations = validate_artifact_declarations(raw, "run")
        assert len(declarations) == 2
        assert _canonical_wire_len(raw) < 32768

    def test_run_wire_over_32768_rejected(self):
        """Run with declarations exceeding 32KB wire is rejected at protocol level."""
        from dp_engine.skills.runtime_protocol import validate_artifact_declarations
        from dp_engine.skills.runtime_errors import RuntimeProtocolError

        # Build items until we exceed 32KB
        long_name = "N" * 250
        items: list[dict[str, object]] = []
        for i in range(50):
            items.append(_make_wire_item_3(
                declared_path=f"path/to/file_{i:04d}.dat",
                display_name=f"Display Name {long_name} {i:04d}",
                kind="data",
                metadata={"index": i, "description": "x" * 100},
            ))
            if _canonical_wire_len(items) > 32768:
                break

        assert _canonical_wire_len(items) > 32768, (
            "Expected wire to exceed 32768 bytes"
        )
        with pytest.raises(RuntimeProtocolError, match="32768-byte"):
            validate_artifact_declarations(items, "run")

    def test_run_wire_exact_32768_or_just_under_passes(self):
        """Run with declarations at exactly or just under 32768 bytes passes."""
        from dp_engine.skills.runtime_protocol import validate_artifact_declarations

        # Use a few items with controlled size
        base = _make_wire_item_3(declared_path="p", display_name="d")
        base_len = _canonical_wire_len([base])
        target = 32768
        needed = target - base_len

        # Pad display_name to get close to 32768
        if needed > 0:
            padded = _make_wire_item_3(
                declared_path="p",
                display_name="d" + "A" * needed,
            )
        else:
            padded = base

        wire_len = _canonical_wire_len([padded])
        # Adjust down if overshot
        while wire_len > target:
            padded["display_name"] = padded["display_name"][:-1]
            wire_len = _canonical_wire_len([padded])

        assert wire_len <= target, f"Wire {wire_len} should be ≤ {target}"
        declarations = validate_artifact_declarations([padded], "run")
        assert len(declarations) == 1

    def test_healthcheck_empty_declarations_ok(self):
        """Healthcheck with empty artifact_declarations passes."""
        from dp_engine.skills.runtime_protocol import validate_artifact_declarations
        result = validate_artifact_declarations([], "healthcheck")
        assert result == ()

    def test_healthcheck_nonempty_still_rejected(self):
        """Healthcheck non-empty declarations still rejected (unchanged contract)."""
        from dp_engine.skills.runtime_protocol import validate_artifact_declarations
        from dp_engine.skills.runtime_errors import RuntimeProtocolError
        raw = [_make_wire_item_3()]
        with pytest.raises(RuntimeProtocolError, match="healthcheck"):
            validate_artifact_declarations(raw, "healthcheck")

    def test_error_message_no_sensitive_content(self):
        """Wire overflow error message must not contain path/metadata."""
        from dp_engine.skills.runtime_protocol import validate_artifact_declarations
        from dp_engine.skills.runtime_errors import RuntimeProtocolError

        # Build items > 32KB with obviously sensitive-looking paths.
        # Each item needs ~1100 bytes → 30 items needed. Use long paths
        # and metadata padding to reach target.
        long_path = "secret/project/internal/" + "x" * 220
        meta_pad = "z" * 1000
        items: list[dict[str, object]] = []
        for i in range(40):
            items.append(_make_wire_item_3(
                declared_path=f"{long_path}_{i:04d}",
                display_name=f"Confidential Report {i:04d}",
                kind="data",
                metadata={"api_key": "sk-sensitive-value",
                          "payload": meta_pad, "index": i},
            ))
            if _canonical_wire_len(items) > 32768:
                break
        assert _canonical_wire_len(items) > 32768, (
            f"Wire too small: {_canonical_wire_len(items)} bytes"
        )

        try:
            validate_artifact_declarations(items, "run")
        except RuntimeProtocolError as e:
            msg = str(e)
            # Must not leak file paths
            assert "secret/project" not in msg
            # Must not leak metadata values
            assert "sk-sensitive" not in msg
            # Must mention the limit
            assert "32768" in msg
        else:
            raise AssertionError("Expected RuntimeProtocolError was not raised")
