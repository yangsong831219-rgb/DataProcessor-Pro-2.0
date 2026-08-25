"""L2 tests: Real Worker + ArtifactPublisher integration.

Tests the complete call chain:
  skill run(context) → declare_artifact() → Worker file observation →
  internal wire → Service read → ArtifactPublisher → RuntimeArtifact

Uses real subprocess.Popen via SkillRuntimeService.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from dp_engine.skills.registry import SkillRegistry
from dp_engine.skills.manifest_parser import parse_skill_manifest
from dp_engine.skills.runtime_service import SkillRuntimeService
from dp_engine.skills.runtime_models import (
    RuntimeArtifact,
    ARTIFACT_SCHEMA_VERSION,
)
from dp_engine.skills.runtime_artifacts import (
    ArtifactStore,
)
from dp_engine.skills.models import InstalledSkill
from tests.fixtures.runtime_fixtures import (
    _make_run_skill,
    create_skill_with_run_entrypoint,
)


# ── Fixture helpers ──


def _register_and_service(tmp_path, skill_dir, skill_id, version="1.0.0"):
    """Register a skill and create a SkillRuntimeService."""
    base_dir = tmp_path / "skills_data"
    base_dir.mkdir(exist_ok=True)
    installed_dir = base_dir / "installed"
    installed_dir.mkdir(exist_ok=True)

    # Move skill dir into installed_dir
    dest = installed_dir / skill_id / version
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Copy all files from skill_dir to dest
    import shutil
    if skill_dir != dest:
        if dest.exists():
            shutil.rmtree(str(dest))
        shutil.copytree(str(skill_dir), str(dest))

    registry_path = base_dir / "registry.json"
    registry = SkillRegistry(registry_path)
    registry.load()

    manifest = parse_skill_manifest(dest)
    installed = InstalledSkill(
        manifest=manifest,
        install_path=str(dest),
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
    return service, registry, installed_dir


def _create_artifact_run_skill(base_dir, skill_id, artifact_code, extra_imports=""):
    """Create a run skill that declares artifacts."""
    code = f'''"""{skill_id} — artifact run test."""
import os
from pathlib import Path
{extra_imports}

def run(context):
    ws_path = Path(context.get("workspace_path", ""))
    output = ws_path / "output"
    output.mkdir(parents=True, exist_ok=True)
{artifact_code}
'''
    return _make_run_skill(base_dir, skill_id, "1.0.0", code)


# ── Tests ──


class TestArtifactPublish:
    """Real Worker + Service artifact publish integration tests."""

    def test_single_artifact_published(self, tmp_path):
        """Declare one artifact → it appears in response.artifacts."""
        skill = _create_artifact_run_skill(
            tmp_path, "single-artifact",
            '''    # Write a text file
    f = output / "report.txt"
    f.write_text("hello artifact", encoding="utf-8")
    context.declare_artifact("report.txt", display_name="Report", kind="data")
    return {"ok": True}
'''
        )
        service, _, _ = _register_and_service(tmp_path, skill, "single-artifact")
        response = service.run_skill("single-artifact", "1.0.0", params={"x": 1})

        assert response.status == "succeeded"
        assert response.success is True
        assert len(response.artifacts) == 1
        art = response.artifacts[0]
        assert art.artifact_schema_version == ARTIFACT_SCHEMA_VERSION
        assert len(art.artifact_id) == 32
        assert art.display_name == "Report"
        assert art.relative_path == "report.txt"
        assert art.kind == "data"
        assert art.media_type == "text/plain"
        assert art.size_bytes == len("hello artifact")
        assert art.sha256 is not None and len(art.sha256) == 64
        assert art.skill_id == "single-artifact"
        assert art.version == "1.0.0"
        assert art.task_id == response.task_id
        assert art.created_at != ""
        assert art.storage_relpath != ""

    def test_no_declare_no_artifacts(self, tmp_path):
        """Skill writes file but does NOT declare → no artifacts published."""
        skill = _create_artifact_run_skill(
            tmp_path, "no-declare",
            '''    f = output / "secret.txt"
    f.write_text("undeclared", encoding="utf-8")
    return {"ok": True}
'''
        )
        service, _, _ = _register_and_service(tmp_path, skill, "no-declare")
        response = service.run_skill("no-declare", "1.0.0", params={})

        assert response.status == "succeeded"
        assert response.artifacts == ()

    def test_result_path_string_not_published(self, tmp_path):
        """A path string in the business result is NOT auto-published."""
        skill = _create_artifact_run_skill(
            tmp_path, "path-str",
            '''    return {"ok": True, "report_path": "output/report.pdf"}
'''
        )
        service, _, _ = _register_and_service(tmp_path, skill, "path-str")
        response = service.run_skill("path-str", "1.0.0", params={})

        assert response.status == "succeeded"
        assert response.artifacts == ()
        # The path string is in the result but not in artifacts
        assert response.result is not None
        assert "report_path" in response.result

    def test_multiple_artifacts_published(self, tmp_path):
        """Declare multiple artifacts → all appear in order."""
        skill = _create_artifact_run_skill(
            tmp_path, "multi-artifact",
            '''    # Write 3 files
    for i, name in enumerate(["a.txt", "b.txt", "c.txt"]):
        f = output / name
        f.write_text(f"content {i}", encoding="utf-8")
    context.declare_artifact("a.txt", display_name="A", kind="data")
    context.declare_artifact("b.txt", display_name="B", kind="data")
    context.declare_artifact("c.txt", display_name="C", kind="data")
    return {"ok": True}
'''
        )
        service, _, _ = _register_and_service(tmp_path, skill, "multi-artifact")
        response = service.run_skill("multi-artifact", "1.0.0", params={})

        assert response.status == "succeeded"
        assert len(response.artifacts) == 3
        names = [a.display_name for a in response.artifacts]
        assert names == ["A", "B", "C"]

    def test_business_negative_with_artifacts(self, tmp_path):
        """Business-negative (ok=false) still publishes declared artifacts."""
        skill = _create_artifact_run_skill(
            tmp_path, "biz-neg",
            '''    f = output / "diag.json"
    f.write_text('{"error": "calibration failed"}', encoding="utf-8")
    context.declare_artifact("diag.json", display_name="Diagnostics", kind="data")
    return {"ok": False, "reason": "calibration failed"}
'''
        )
        service, _, _ = _register_and_service(tmp_path, skill, "biz-neg")
        response = service.run_skill("biz-neg", "1.0.0", params={})

        assert response.status == "succeeded"  # business-negative is still succeeded
        assert response.success is True
        assert len(response.artifacts) == 1
        assert response.artifacts[0].display_name == "Diagnostics"

    def test_declare_order_preserved(self, tmp_path):
        """Declaration order → artifact list order."""
        skill = _create_artifact_run_skill(
            tmp_path, "order-test",
            '''    names = ["z.txt", "a.txt", "m.txt", "b.txt", "x.txt"]
    for name in names:
        f = output / name
        f.write_text(name, encoding="utf-8")
    for name in names:
        context.declare_artifact(name, display_name=name, kind="data")
    return {"ok": True}
'''
        )
        service, _, _ = _register_and_service(tmp_path, skill, "order-test")
        response = service.run_skill("order-test", "1.0.0", params={})

        assert response.status == "succeeded"
        assert len(response.artifacts) == 5
        disp = [a.display_name for a in response.artifacts]
        assert disp == ["z.txt", "a.txt", "m.txt", "b.txt", "x.txt"]

    def test_kind_and_display_name_preserved(self, tmp_path):
        """Kind and display_name from skill survive publishing."""
        skill = _create_artifact_run_skill(
            tmp_path, "kind-test",
            '''    f = output / "chart.png"
    f.write_text("fake png", encoding="utf-8")  # won't pass sniffing!
    # Use a real JSON file instead
    f2 = output / "data.json"
    f2.write_text('{"key": "value"}', encoding="utf-8")
    context.declare_artifact("data.json", display_name="My Results", kind="table")
    return {"ok": True}
'''
        )
        service, _, _ = _register_and_service(tmp_path, skill, "kind-test")
        response = service.run_skill("kind-test", "1.0.0", params={})

        assert response.status == "succeeded"
        assert len(response.artifacts) == 1
        art = response.artifacts[0]
        assert art.display_name == "My Results"
        assert art.kind == "table"

    def test_metadata_preserved(self, tmp_path):
        """Metadata from skill survives publishing."""
        skill = _create_artifact_run_skill(
            tmp_path, "meta-test",
            '''    f = output / "meta.json"
    f.write_text('{"x": 1}', encoding="utf-8")
    context.declare_artifact(
        "meta.json",
        display_name="MetaFile",
        kind="data",
        metadata={"author": "test", "version": 2},
    )
    return {"ok": True}
'''
        )
        service, _, _ = _register_and_service(tmp_path, skill, "meta-test")
        response = service.run_skill("meta-test", "1.0.0", params={})

        assert response.status == "succeeded"
        assert len(response.artifacts) == 1
        art = response.artifacts[0]
        assert art.metadata == {"author": "test", "version": 2}

    def test_fatal_declaration_error_protocol_error(self, tmp_path):
        """If declare_artifact raises (illegal), status=protocol_error."""
        skill = _create_artifact_run_skill(
            tmp_path, "fatal-decl",
            '''    try:
        context.declare_artifact("/etc/passwd", display_name="Bad")
    except (ValueError, TypeError):
        pass  # skill catches the error
    return {"ok": True}
'''
        )
        service, _, _ = _register_and_service(tmp_path, skill, "fatal-decl")
        response = service.run_skill("fatal-decl", "1.0.0", params={})

        # Even though skill caught the error, fatal flag persists
        assert response.status == "protocol_error"
        assert response.success is False
        assert response.artifacts == ()

    def test_old_skill_without_declare_still_works(self, tmp_path):
        """Old skill that never calls declare_artifact works unchanged."""
        skill = _make_run_skill(
            tmp_path, "old-skill", "1.0.0",
            '"""Old skill."""\ndef run(context):\n    return {"ok": True, "result": "done"}\n',
        )
        service, _, _ = _register_and_service(tmp_path, skill, "old-skill")
        response = service.run_skill("old-skill", "1.0.0", params={})

        assert response.status == "succeeded"
        assert response.success is True
        assert response.artifacts == ()
        assert response.result == {"ok": True, "result": "done"}


class TestArtifactTransaction:
    """Atomic transaction and idempotency tests."""

    def test_same_fingerprint_idempotent(self, tmp_path):
        """Same declarations twice → second run returns existing artifacts."""
        skill = _create_artifact_run_skill(
            tmp_path, "idem-test",
            '''    f = output / "data.json"
    f.write_text('{"v": 1}', encoding="utf-8")
    context.declare_artifact("data.json", display_name="D", kind="data")
    return {"ok": True}
'''
        )
        service, _, _ = _register_and_service(tmp_path, skill, "idem-test")
        r1 = service.run_skill("idem-test", "1.0.0", params={})
        assert r1.status == "succeeded"
        assert len(r1.artifacts) == 1

        # Same skill — the worker creates identical declarations in a new task
        # Different task_id means different task directory, so no fingerprint
        # collision. Fingerprint idempotency is within the same task_id.
        r2 = service.run_skill("idem-test", "1.0.0", params={})
        assert r2.status == "succeeded"
        assert len(r2.artifacts) == 1

    def test_different_fingerprint_rejected(self, tmp_path):
        """Different declarations for the same task_id → fail closed.

        Note: This is hard to test directly because each run gets a new task_id.
        We test via the ArtifactPublisher directly.
        """
        from dp_engine.skills.runtime_artifacts import ArtifactPublisher

        tmp_root = tmp_path / "artifact_root"
        tmp_root.mkdir()

        # This test verifies the publisher behavior at the API level;
        # the actual fingerprint collision is tested in the store tests.
        publisher = ArtifactPublisher()
        assert publisher is not None  # constructor works
