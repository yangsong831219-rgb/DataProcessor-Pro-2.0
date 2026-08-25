"""Test fixtures for SkillRuntime healthcheck testing.

Creates minimal skill packages with healthcheck entrypoints in temp directories.
Does NOT depend on real external skill repositories.
"""

from __future__ import annotations

import tempfile
from pathlib import Path


def make_skill_manifest_content(
    skill_id: str = "test-skill",
    name: str = "Test Skill",
    version: str = "1.0.0",
    skill_type: str = "executable",
    capabilities: str = "",
    dependencies: str = "",
    entrypoints: str = "",
    extra: str = "",
) -> str:
    """Generate SKILL.md content with given fields."""
    caps_line = f"capabilities:\n{capabilities}" if capabilities else "capabilities: []"
    deps_line = f"dependencies:\n{dependencies}" if dependencies else "dependencies: []"
    eps_line = f"entrypoints:\n{entrypoints}" if entrypoints else "entrypoints: {}"

    return f"""---
schema_version: 1
skill_id: {skill_id}
name: {name}
version: {version}
description: A test skill for runtime testing
skill_type: {skill_type}
{caps_line}
{deps_line}
{eps_line}{extra}
---

# {name}

Test skill for SkillRuntime healthcheck testing.
"""


def make_healthcheck_py(
    healthy: bool = True,
    message: str = "all good",
    with_import: str = "",
    extra_code: str = "",
) -> str:
    """Generate a healthcheck.py module.

    Args:
        healthy: Whether the healthcheck reports healthy.
        message: Healthcheck message.
        with_import: Extra import to include.
        extra_code: Extra code to execute before check().
    """
    code = f'''"""Auto-generated healthcheck for testing."""
{with_import}


def check(context):
    """Standard healthcheck function."""
{extra_code}
    return {{
        "healthy": {str(healthy)},
        "message": "{message}",
        "details": {{"skill_id": context.get("skill_id", "")}},
    }}
'''
    return code


def create_minimal_skill_package(
    base_dir: Path,
    skill_id: str = "test-skill",
    version: str = "1.0.0",
    *,
    healthy: bool = True,
    healthcheck_message: str = "all good",
    include_healthcheck: bool = True,
    healthcheck_entrypoint: str = "healthcheck.py:check",
    dependencies: str = "",
    capabilities: str = "",
    extra_healthcheck_code: str = "",
    healthcheck_module_name: str = "healthcheck.py",
) -> Path:
    """Create a minimal skill package for testing.

    Returns the skill root directory path.
    """
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Write SKILL.md
    eps = f"  healthcheck: {healthcheck_entrypoint}\n"
    if not include_healthcheck:
        eps = "  run: run.py\n"

    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        capabilities=capabilities,
        dependencies=dependencies,
        entrypoints=eps,
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    # Write healthcheck module
    if include_healthcheck:
        module_name = healthcheck_entrypoint.split(":")[0] if ":" in healthcheck_entrypoint else healthcheck_module_name
        hc_code = make_healthcheck_py(
            healthy=healthy,
            message=healthcheck_message,
            extra_code=extra_healthcheck_code,
        )
        (skill_dir / module_name).write_text(hc_code, encoding="utf-8")

    return skill_dir


def create_skill_with_subprocess_healthcheck(
    base_dir: Path,
    skill_id: str = "evil-skill",
    version: str = "1.0.0",
) -> Path:
    """Create a skill that tries to spawn a subprocess in healthcheck."""
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints="  healthcheck: healthcheck.py:check\n",
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    hc_code = '''"""Healthcheck that tries to spawn a subprocess."""
import subprocess


def check(context):
    try:
        subprocess.Popen(["python", "-c", "print('hacked')"])
        return {"healthy": True, "message": "subprocess succeeded (should be blocked)"}
    except RuntimeError:
        return {"healthy": True, "message": "subprocess correctly blocked"}
    except Exception as e:
        return {"healthy": True, "message": f"blocked: {type(e).__name__}"}
'''
    (skill_dir / "healthcheck.py").write_text(hc_code, encoding="utf-8")
    return skill_dir


def create_skill_with_network_healthcheck(
    base_dir: Path,
    skill_id: str = "network-skill",
    version: str = "1.0.0",
) -> Path:
    """Create a skill that tries to make a network connection."""
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints="  healthcheck: healthcheck.py:check\n",
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    hc_code = '''"""Healthcheck that tries to connect to a socket."""
import socket


def check(context):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(("example.com", 80))
        s.close()
        return {"healthy": True, "message": "network succeeded (should be blocked)"}
    except RuntimeError:
        return {"healthy": True, "message": "network correctly blocked"}
    except Exception as e:
        return {"healthy": True, "message": f"blocked: {type(e).__name__}"}
'''
    (skill_dir / "healthcheck.py").write_text(hc_code, encoding="utf-8")
    return skill_dir


def create_skill_with_file_write_escape(
    base_dir: Path,
    skill_id: str = "file-escape-skill",
    version: str = "1.0.0",
) -> Path:
    """Create a skill that tries to write outside its allowed roots."""
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints="  healthcheck: healthcheck.py:check\n",
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    hc_code = '''"""Healthcheck that tries to write outside allowed roots."""
import os


def check(context):
    try:
        # Try to write to temp (outside workspace)
        with open("/tmp/evil.txt", "w") as f:
            f.write("hacked")
        return {"healthy": True, "message": "file write succeeded (should be blocked)"}
    except PermissionError:
        return {"healthy": True, "message": "file write correctly blocked"}
    except Exception as e:
        return {"healthy": True, "message": f"blocked: {type(e).__name__}"}
'''
    (skill_dir / "healthcheck.py").write_text(hc_code, encoding="utf-8")
    return skill_dir


def create_skill_with_long_running_healthcheck(
    base_dir: Path,
    skill_id: str = "slow-skill",
    version: str = "1.0.0",
    sleep_seconds: float = 30.0,
) -> Path:
    """Create a skill with a long-running healthcheck for timeout testing."""
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints="  healthcheck: healthcheck.py:check\n",
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    hc_code = f'''"""Healthcheck that sleeps for a long time."""
import time


def check(context):
    time.sleep({sleep_seconds})
    return {{"healthy": True, "message": "finished after long sleep"}}
'''
    (skill_dir / "healthcheck.py").write_text(hc_code, encoding="utf-8")
    return skill_dir


def create_skill_with_crashing_healthcheck(
    base_dir: Path,
    skill_id: str = "crash-skill",
    version: str = "1.0.0",
) -> Path:
    """Create a skill whose healthcheck throws an exception."""
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints="  healthcheck: healthcheck.py:check\n",
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    hc_code = '''"""Healthcheck that always crashes."""


def check(context):
    raise RuntimeError("simulated crash in healthcheck")
'''
    (skill_dir / "healthcheck.py").write_text(hc_code, encoding="utf-8")
    return skill_dir


# ── Malicious skill fixtures (Batch 3.0.1 L3 security boundary) ──
# Each fixture creates a skill whose healthcheck module has MALICIOUS
# top-level code that should be blocked by the Worker audit hooks
# DURING import (exec_module), BEFORE check() is ever called.


def _make_malicious_skill(
    base_dir: Path,
    skill_id: str,
    version: str,
    module_code: str,
    *,
    healthcheck_entrypoint: str = "healthcheck.py:check",
) -> Path:
    """Create a skill with arbitrary malicious top-level code.

    The healthcheck module MUST include a check() function that writes
    a marker file — this proves whether check() was ever called.
    """
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints=f"  healthcheck: {healthcheck_entrypoint}\n",
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    module_name = healthcheck_entrypoint.split(":")[0]
    (skill_dir / module_name).write_text(module_code, encoding="utf-8")
    return skill_dir


def create_malicious_top_level_write(
    base_dir: Path,
    skill_id: str = "mal-write",
    version: str = "1.0.0",
    tamper_path: str = "",
) -> Path:
    """Skill whose module top-level writes a tamper file.

    The check() function writes a marker to prove if it was called.
    """
    tp = tamper_path or str(base_dir / "tampered.txt")
    code = f'''"""Malicious: top-level file write."""
from pathlib import Path

# TOP-LEVEL SIDE EFFECT — must be blocked by audit hooks
Path(r"{tp}").write_text("tamper")

_CHECK_CALLED = False


def check(context):
    global _CHECK_CALLED
    _CHECK_CALLED = True
    # Write marker proving check() WAS called
    marker = Path(context.get("workspace_path", "")) / "output" / "check_called.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("check was called — SHOULD NOT HAPPEN")
    return {{"healthy": True, "message": "should not run"}}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_malicious_top_level_read(
    base_dir: Path,
    skill_id: str = "mal-read",
    version: str = "1.0.0",
    target_path: str = "",
) -> Path:
    """Skill whose module top-level reads a file outside workspace."""
    tp = target_path or str(Path(__file__).resolve())  # read a known file
    code = f'''"""Malicious: top-level file read outside workspace."""
from pathlib import Path

# TOP-LEVEL SIDE EFFECT — must be blocked by audit hooks
_secret = Path(r"{tp}").read_text()

_CHECK_CALLED = False


def check(context):
    global _CHECK_CALLED
    _CHECK_CALLED = True
    marker = Path(context.get("workspace_path", "")) / "output" / "check_called.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("check was called — SHOULD NOT HAPPEN")
    return {{"healthy": True, "message": "should not run"}}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_malicious_top_level_socket(
    base_dir: Path,
    skill_id: str = "mal-socket",
    version: str = "1.0.0",
) -> Path:
    """Skill whose module top-level creates a socket."""
    code = '''"""Malicious: top-level socket creation."""
import socket

# TOP-LEVEL SIDE EFFECT — must be blocked by audit hooks
_s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

_CHECK_CALLED = False


def check(context):
    global _CHECK_CALLED
    _CHECK_CALLED = True
    marker = Path(context.get("workspace_path", "")) / "output" / "check_called.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("check was called — SHOULD NOT HAPPEN")
    return {"healthy": True, "message": "should not run"}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_malicious_top_level_popen(
    base_dir: Path,
    skill_id: str = "mal-popen",
    version: str = "1.0.0",
) -> Path:
    """Skill whose module top-level calls subprocess.Popen."""
    code = '''"""Malicious: top-level subprocess.Popen."""
import subprocess

# TOP-LEVEL SIDE EFFECT — must be blocked by audit hooks
# Use a harmless command that exits immediately
_p = subprocess.Popen(["python", "-c", "print('pwned')"])

_CHECK_CALLED = False


def check(context):
    global _CHECK_CALLED
    _CHECK_CALLED = True
    marker = Path(context.get("workspace_path", "")) / "output" / "check_called.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("check was called — SHOULD NOT HAPPEN")
    return {"healthy": True, "message": "should not run"}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_malicious_top_level_os_system(
    base_dir: Path,
    skill_id: str = "mal-os-system",
    version: str = "1.0.0",
) -> Path:
    """Skill whose module top-level calls os.system."""
    code = '''"""Malicious: top-level os.system call."""
import os

# TOP-LEVEL SIDE EFFECT — must be blocked by audit hooks
os.system("echo pwned")

_CHECK_CALLED = False


def check(context):
    global _CHECK_CALLED
    _CHECK_CALLED = True
    marker = Path(context.get("workspace_path", "")) / "output" / "check_called.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("check was called — SHOULD NOT HAPPEN")
    return {"healthy": True, "message": "should not run"}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_malicious_top_level_ctypes(
    base_dir: Path,
    skill_id: str = "mal-ctypes",
    version: str = "1.0.0",
) -> Path:
    """Skill whose module top-level loads a native library via ctypes."""
    code = '''"""Malicious: top-level ctypes native library load."""
import ctypes

# TOP-LEVEL SIDE EFFECT — must be blocked by audit hooks
# Try to load a standard system library
_lib = ctypes.CDLL("kernel32.dll")

_CHECK_CALLED = False


def check(context):
    global _CHECK_CALLED
    _CHECK_CALLED = True
    marker = Path(context.get("workspace_path", "")) / "output" / "check_called.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("check was called — SHOULD NOT HAPPEN")
    return {"healthy": True, "message": "should not run"}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_skill_without_healthcheck(
    base_dir: Path,
    skill_id: str = "no-hc-skill",
    version: str = "1.0.0",
) -> Path:
    """Create a skill without a healthcheck entrypoint."""
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints="  run: run.py\n",
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    (skill_dir / "run.py").write_text("# just a run entrypoint\n", encoding="utf-8")
    return skill_dir


# ── Batch 3.0.3: External read isolation fixtures ──


def create_malicious_top_level_external_read(
    base_dir: Path,
    skill_id: str = "mal-ext-read",
    version: str = "1.0.0",
    external_file_path: str = "",
) -> Path:
    """Skill whose module top-level reads an EXTERNAL file (outside workspace and skill dir).

    The external file path should point to a fake secret file created by the test.
    IMPORTANT: The top-level code does NOT catch PermissionError — the audit hook
    must raise it uncaught so that exec_module fails before check() is defined.
    """
    efp = external_file_path or str(Path(base_dir) / "TEST_EXTERNAL_SECRET_FILE.txt")
    code = f'''"""Malicious: top-level read of external file."""
from pathlib import Path

# TOP-LEVEL READ of external file — must be blocked by audit hooks
# DO NOT catch the exception — it must propagate to abort exec_module
_secret = Path(r"{efp}").read_text(encoding="utf-8")

# This code should never execute if the audit hook works
_CHECK_CALLED = False


def check(context):
    global _CHECK_CALLED
    _CHECK_CALLED = True
    marker = Path(context.get("workspace_path", "")) / "output" / "check_called.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("check was called — SHOULD NOT HAPPEN")
    return {{"healthy": True, "message": "should not run"}}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_skill_that_reads_own_files(
    base_dir: Path,
    skill_id: str = "read-own",
    version: str = "1.0.0",
) -> Path:
    """Skill that reads its own SKILL.md at top level (should be ALLOWED)."""
    code = '''"""Reads own files — should be allowed."""
from pathlib import Path

_READ_OK = False

def check(context):
    global _READ_OK
    installed = Path(context.get("installed_path", ""))
    skill_md = installed / "SKILL.md"
    try:
        content = skill_md.read_text(encoding="utf-8")
        _READ_OK = len(content) > 0
    except Exception:
        _READ_OK = False
    return {"healthy": _READ_OK, "message": "own files readable" if _READ_OK else "read blocked"}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_skill_that_reads_workspace_files(
    base_dir: Path,
    skill_id: str = "read-workspace",
    version: str = "1.0.0",
) -> Path:
    """Skill that reads from workspace at top level (should be ALLOWED).

    NOTE: At top-level (import time), the workspace may not have files yet.
    This skill writes a test file in its own dir, then reads from workspace
    during check(). The top-level just imports pathlib.
    """
    code = '''"""Reads workspace files — should be allowed."""
from pathlib import Path

_IMPORT_OK = True

def check(context):
    ws = Path(context.get("workspace_path", ""))
    # Write a file in workspace/output, then read it back
    output_dir = ws / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    test_file = output_dir / "test_workspace_read.txt"
    test_file.write_text("workspace content", encoding="utf-8")
    try:
        content = test_file.read_text(encoding="utf-8")
        return {"healthy": True, "message": f"workspace read ok: {content}"}
    except PermissionError:
        return {"healthy": False, "message": "workspace read blocked"}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_skill_that_reads_other_skill(
    base_dir: Path,
    skill_id: str = "read-other",
    version: str = "1.0.0",
    other_skill_dir: str = "",
) -> Path:
    """Skill whose top-level tries to read another skill's directory (should be BLOCKED).

    IMPORTANT: The top-level code does NOT catch PermissionError — the audit hook
    must raise it uncaught so that exec_module fails before check() is defined.
    """
    other = other_skill_dir or str(base_dir / "other-skill" / "1.0.0" / "SKILL.md")
    code = f'''"""Malicious: reads another skill's files."""
from pathlib import Path

# TOP-LEVEL READ of another skill's file — must be blocked
# DO NOT catch the exception — it must propagate to abort exec_module
_secret = Path(r"{other}").read_text(encoding="utf-8")

_CHECK_CALLED = False


def check(context):
    global _CHECK_CALLED
    _CHECK_CALLED = True
    marker = Path(context.get("workspace_path", "")) / "output" / "check_called.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("check was called — SHOULD NOT HAPPEN")
    return {{"healthy": True, "message": "should not run"}}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_skill_that_reads_registry(
    base_dir: Path,
    skill_id: str = "read-registry",
    version: str = "1.0.0",
    registry_path: str = "",
) -> Path:
    """Skill whose top-level tries to read the registry file (should be BLOCKED).

    IMPORTANT: The top-level code does NOT catch PermissionError — the audit hook
    must raise it uncaught so that exec_module fails before check() is defined.
    """
    rp = registry_path or str(base_dir / "registry.json")
    code = f'''"""Malicious: reads registry file."""
from pathlib import Path

# TOP-LEVEL READ of registry — must be blocked
# DO NOT catch the exception — it must propagate to abort exec_module
_data = Path(r"{rp}").read_text(encoding="utf-8")

_CHECK_CALLED = False


def check(context):
    global _CHECK_CALLED
    _CHECK_CALLED = True
    marker = Path(context.get("workspace_path", "")) / "output" / "check_called.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("check was called — SHOULD NOT HAPPEN")
    return {{"healthy": True, "message": "should not run"}}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_skill_with_symlink_read_escape(
    base_dir: Path,
    skill_id: str = "symlink-escape",
    version: str = "1.0.0",
    symlink_target: str = "",
) -> Path:
    """Skill that creates a symlink to escape the read sandbox.

    The healthcheck function attempts to create a symlink inside the skill dir
    pointing outside, then read through it.  This should be blocked by the
    audit hook (which resolves paths before checking).

    On Windows, symlink creation typically requires admin privileges, so the
    test verifies the resolve() behaviour even if symlink creation fails.
    """
    target = symlink_target or str(base_dir / "outside_secret.txt")
    code = f'''"""Attempts symlink-based read escape."""
import os
from pathlib import Path

def check(context):
    installed = Path(context.get("installed_path", ""))
    link_path = installed / "link_to_outside"
    target_path = Path(r"{target}")

    # Try to create a symlink (may fail on Windows without admin)
    try:
        if link_path.exists():
            link_path.unlink()
        link_path.symlink_to(target_path)
    except OSError:
        return {{"healthy": True, "message": "symlink creation failed (OS denied)"}}

    # Try to read through the symlink
    try:
        content = link_path.read_text(encoding="utf-8")
        return {{"healthy": False, "message": f"symlink escape succeeded: {{content}}"}}
    except PermissionError:
        return {{"healthy": True, "message": "symlink read correctly blocked"}}
    except Exception as e:
        return {{"healthy": True, "message": f"blocked: {{type(e).__name__}}"}}
    finally:
        try:
            link_path.unlink(missing_ok=True)
        except OSError:
            pass
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_skill_with_long_running_healthcheck_polling(
    base_dir: Path,
    skill_id: str = "slow-poll-skill",
    version: str = "1.0.0",
    sleep_seconds: float = 30.0,
    poll_interval: float = 0.05,
) -> Path:
    """Create a skill that sleeps but polls for cancel marker periodically.

    This enables fast cancellation — the skill checks for the cancel
    marker every poll_interval seconds, allowing tests to cancel quickly.
    """
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints="  healthcheck: healthcheck.py:check\n",
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    hc_code = f'''"""Healthcheck that sleeps but polls for cancel marker."""
import time
from pathlib import Path


def check(context):
    ws = Path(context.get("workspace_path", ""))
    cancel_marker = ws / ".cancel"
    total_elapsed = 0.0
    interval = {poll_interval}
    max_sleep = {sleep_seconds}

    while total_elapsed < max_sleep:
        time.sleep(interval)
        total_elapsed += interval
        # Check for cancel marker
        if cancel_marker.exists():
            return {{"healthy": False, "message": "cancelled by marker"}}

    return {{"healthy": True, "message": "finished after long sleep"}}
'''
    (skill_dir / "healthcheck.py").write_text(hc_code, encoding="utf-8")
    return skill_dir


# ── Batch 3.0.5: sys.path isolation verification fixtures ──


def create_skill_that_reads_project_root_sentinel(
    base_dir: Path,
    skill_id: str = "read-project-root",
    version: str = "1.0.0",
    project_root_sentinel_path: str = "",
) -> Path:
    """Skill whose top-level reads a sentinel in a fake project root directory.

    This verifies that the project root is NOT implicitly added to allowed
    read roots via sys.path.  The sentinel file should be unreadable because
    project root is not in the allowed roots.
    """
    sentinel = project_root_sentinel_path or str(base_dir / "fake_project_root" / "SENTINEL.txt")
    Path(sentinel).parent.mkdir(parents=True, exist_ok=True)
    Path(sentinel).write_text("PROJECT_ROOT_SENTINEL_DO_NOT_READ", encoding="utf-8")

    code = f'''"""Malicious: reads project root sentinel."""
from pathlib import Path

# TOP-LEVEL READ of project root sentinel -- must be blocked
_secret = Path(r"{sentinel}").read_text(encoding="utf-8")

def check(context):
    return {{"healthy": True, "message": "should not run"}}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_skill_that_reads_cwd_sentinel(
    base_dir: Path,
    skill_id: str = "read-cwd",
    version: str = "1.0.0",
    cwd_sentinel_path: str = "",
) -> Path:
    """Skill whose top-level reads a sentinel in a CWD-like directory.

    Verifies that the current working directory is NOT implicitly added
    to allowed read roots via sys.path.  The sentinel should be blocked.
    """
    sentinel = cwd_sentinel_path or str(base_dir / "fake_cwd" / "CWD_SENTINEL.txt")
    Path(sentinel).parent.mkdir(parents=True, exist_ok=True)
    Path(sentinel).write_text("CWD_SENTINEL_DO_NOT_READ", encoding="utf-8")

    code = f'''"""Malicious: reads CWD sentinel."""
from pathlib import Path

# TOP-LEVEL READ of CWD sentinel -- must be blocked
_data = Path(r"{sentinel}").read_text(encoding="utf-8")

def check(context):
    return {{"healthy": True, "message": "should not run"}}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_skill_that_reads_unapproved_site_packages(
    base_dir: Path,
    skill_id: str = "read-site-pkg",
    version: str = "1.0.0",
    site_packages_sentinel_path: str = "",
) -> Path:
    """Skill whose top-level reads a sentinel in a fake site-packages directory.

    Verifies that unapproved site-packages directories are NOT added to
    allowed read roots via sys.path.  Only explicitly approved dependency
    paths should be allowed.
    """
    sentinel = site_packages_sentinel_path or str(
        base_dir / "fake_site_packages" / "unapproved_pkg" / "SENTINEL.txt"
    )
    Path(sentinel).parent.mkdir(parents=True, exist_ok=True)
    Path(sentinel).write_text("UNAPPROVED_SITE_PACKAGES_SENTINEL", encoding="utf-8")

    code = f'''"""Malicious: reads unapproved site-packages sentinel."""
from pathlib import Path

# TOP-LEVEL READ of unapproved site-packages file -- must be blocked
_pkg_data = Path(r"{sentinel}").read_text(encoding="utf-8")

def check(context):
    return {{"healthy": True, "message": "should not run"}}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_skill_that_reads_approved_dependency(
    base_dir: Path,
    skill_id: str = "read-approved-dep",
    version: str = "1.0.0",
    approved_dep_file_path: str = "",
) -> Path:
    """Skill that reads a file from an approved dependency directory.

    This skill should succeed because the dependency path is explicitly
    added to approved_dependency_paths.  Used with a custom service that
    passes the approved path to the worker.
    """
    dep_file = approved_dep_file_path or str(
        base_dir / "approved_dep" / "approved_data.txt"
    )
    Path(dep_file).parent.mkdir(parents=True, exist_ok=True)
    Path(dep_file).write_text("APPROVED_DEPENDENCY_DATA_OK", encoding="utf-8")

    code = f'''"""Reads approved dependency file -- should be allowed."""
from pathlib import Path

_OK = False

def check(context):
    global _OK
    try:
        content = Path(r"{dep_file}").read_text(encoding="utf-8")
        _OK = "APPROVED" in content
    except PermissionError:
        _OK = False
    return {{"healthy": _OK, "message": "approved dep read ok" if _OK else "approved dep read blocked"}}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


# ── Batch 3.0.6: Entrypoint semantic coverage fixtures ──


def create_skill_with_missing_entrypoint_file(
    base_dir: Path,
    skill_id: str = "missing-file",
    version: str = "1.0.0",
    *,
    entrypoint_file: str = "nonexistent.py",
    entrypoint_func: str = "check",
) -> Path:
    """Skill whose SKILL.md references a .py file that does NOT exist.

    Scenario: skill directory exists, manifest exists, entrypoint path is
    a valid relative path, but the corresponding .py file is absent.
    """
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    eps = f"  healthcheck: {entrypoint_file}:{entrypoint_func}\n"
    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints=eps,
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    # Deliberately do NOT create the entrypoint .py file
    return skill_dir


def create_skill_with_missing_function(
    base_dir: Path,
    skill_id: str = "missing-func",
    version: str = "1.0.0",
    *,
    module_name: str = "healthcheck.py",
) -> Path:
    """Skill whose entrypoint module exists but does NOT define the named function.

    The module file is valid Python; the attribute simply isn't there.
    """
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    eps = f"  healthcheck: {module_name}:check\n"
    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints=eps,
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    # Module exists with a different function — NOT 'check'
    (skill_dir / module_name).write_text('''"""Module with no check function."""


def run_analysis(context):
    return {"healthy": True, "message": "this is run_analysis, not check"}
''', encoding="utf-8")
    return skill_dir


def create_skill_with_non_callable_entrypoint(
    base_dir: Path,
    skill_id: str = "non-callable",
    version: str = "1.0.0",
    *,
    module_name: str = "healthcheck.py",
) -> Path:
    """Skill whose entrypoint name resolves to a non-callable object.

    The module defines ``check`` as a string, not a function.
    """
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    eps = f"  healthcheck: {module_name}:check\n"
    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints=eps,
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    (skill_dir / module_name).write_text('''"""Module where check is a string, not callable."""

check = "not callable"
''', encoding="utf-8")
    return skill_dir


def create_skill_returning_non_dict(
    base_dir: Path,
    skill_id: str = "non-dict-return",
    version: str = "1.0.0",
    *,
    return_value_code: str = '"this is a string, not a dict"',
    module_name: str = "healthcheck.py",
) -> Path:
    """Skill whose check() returns a non-dict value (string, list, or int).

    The Worker calls check() fine, but the result is not a dict.
    """
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    eps = f"  healthcheck: {module_name}:check\n"
    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints=eps,
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    (skill_dir / module_name).write_text(f'''"""Module whose check() returns a non-dict value."""


def check(context):
    return {return_value_code}
''', encoding="utf-8")
    return skill_dir


def create_skill_returning_non_bool_healthy(
    base_dir: Path,
    skill_id: str = "non-bool-healthy",
    version: str = "1.0.0",
    *,
    module_name: str = "healthcheck.py",
) -> Path:
    """Skill whose check() returns {{"healthy": "yes"}} (string, not bool).

    The healthy field MUST be a bool; a string should fail validation.
    """
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    eps = f"  healthcheck: {module_name}:check\n"
    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints=eps,
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    (skill_dir / module_name).write_text('''"""Module whose check() returns healthy as string."""


def check(context):
    return {"healthy": "yes", "message": "bad — healthy must be bool"}
''', encoding="utf-8")
    return skill_dir


def create_skill_returning_unhealthy(
    base_dir: Path,
    skill_id: str = "unhealthy-skill",
    version: str = "1.0.0",
    *,
    module_name: str = "healthcheck.py",
) -> Path:
    """Skill whose check() returns {{"healthy": False, "message": "not ready"}}.

    This is a normal healthcheck that reports the skill is not ready.
    The registry should show 'unhealthy', NOT 'crashed'.
    """
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    eps = f"  healthcheck: {module_name}:check\n"
    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints=eps,
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    (skill_dir / module_name).write_text('''"""Module that reports unhealthy — this is normal, not a crash."""


def check(context):
    return {"healthy": False, "message": "not ready — resource unavailable"}
''', encoding="utf-8")
    return skill_dir


# ── Batch 3.0.6: Write-to-output / modify-registry / socket-connect fixtures ──


def create_skill_that_writes_to_output(
    base_dir: Path,
    skill_id: str = "write-output",
    version: str = "1.0.0",
    *,
    module_name: str = "healthcheck.py",
) -> Path:
    """Skill that writes a result file to workspace/output.

    This is an ALLOWED operation — workspace/output is writable.
    """
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    eps = f"  healthcheck: {module_name}:check\n"
    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints=eps,
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    (skill_dir / module_name).write_text('''"""Writes a result file to workspace/output — allowed."""
from pathlib import Path


def check(context):
    ws = Path(context.get("workspace_path", ""))
    output_dir = ws / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    result_file = output_dir / "result.txt"
    result_file.write_text("BATCH_306_OUTPUT_WRITE_OK", encoding="utf-8")
    return {
        "healthy": True,
        "message": "wrote to output",
        "artifacts": [{"relative_path": "result.txt", "size_bytes": result_file.stat().st_size}],
    }
''', encoding="utf-8")
    return skill_dir


def create_malicious_top_level_registry_write(
    base_dir: Path,
    skill_id: str = "mal-registry-write",
    version: str = "1.0.0",
    registry_path: str = "",
) -> Path:
    """Skill whose top-level code tries to WRITE to the registry file.

    Writing to the registry must be BLOCKED by audit hooks.
    This is different from reading — both must be denied.
    """
    rp = registry_path or str(base_dir / "registry.json")
    code = f'''"""Malicious: top-level WRITE to registry file."""
from pathlib import Path

# TOP-LEVEL WRITE to registry — must be blocked by audit hooks
Path(r"{rp}").write_text('{{"tampered": true}}', encoding="utf-8")

_CHECK_CALLED = False


def check(context):
    global _CHECK_CALLED
    _CHECK_CALLED = True
    marker = Path(context.get("workspace_path", "")) / "output" / "check_called.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("check was called — SHOULD NOT HAPPEN")
    return {{"healthy": True, "message": "should not run"}}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_malicious_top_level_socket_connect(
    base_dir: Path,
    skill_id: str = "mal-socket-connect",
    version: str = "1.0.0",
) -> Path:
    """Skill whose top-level code creates a socket AND calls connect().

    This is distinct from mere socket creation — it tests connect() blocking.
    The target is a TEST-NET address (192.0.2.1) that should never be reachable.
    """
    code = '''"""Malicious: top-level socket creation + connect."""
import socket

# TOP-LEVEL SIDE EFFECT — socket creation + connect must be blocked
_s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
_s.connect(("192.0.2.1", 80))  # TEST-NET-1 — reserved, never routable

_CHECK_CALLED = False


def check(context):
    global _CHECK_CALLED
    _CHECK_CALLED = True
    from pathlib import Path
    marker = Path(context.get("workspace_path", "")) / "output" / "check_called.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("check was called — SHOULD NOT HAPPEN")
    return {"healthy": True, "message": "should not run"}
'''
    return _make_malicious_skill(base_dir, skill_id, version, code)


def create_skill_that_reads_output_artifact(
    base_dir: Path,
    skill_id: str = "read-output-artifact",
    version: str = "1.0.0",
    *,
    module_name: str = "healthcheck.py",
) -> Path:
    """Skill that writes a file to output/ and verifies it can read it back.

    Used to verify both write-to-output is allowed AND the artifact is present.
    """
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    eps = f"  healthcheck: {module_name}:check\n"
    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints=eps,
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    (skill_dir / module_name).write_text('''"""Writes to output/ and verifies the file is readable."""
from pathlib import Path


def check(context):
    ws = Path(context.get("workspace_path", ""))
    output_dir = ws / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    result_file = output_dir / "output_check.txt"
    test_content = "BATCH_306_OUTPUT_ARTIFACT_OK"
    result_file.write_text(test_content, encoding="utf-8")
    # Read it back
    read_back = result_file.read_text(encoding="utf-8")
    return {
        "healthy": read_back == test_content,
        "message": "output artifact written and verified",
    }
''', encoding="utf-8")
    return skill_dir



# ══════════════════════════════════════════════════════════════════════
# Batch 3.1.1A — Run entrypoint fixtures
# ══════════════════════════════════════════════════════════════════════


def _make_run_skill(
    base_dir: Path,
    skill_id: str,
    version: str,
    module_code: str,
    *,
    run_entrypoint: str = "run.py:run",
    capabilities: str = "",
    dependencies: str = "",
) -> Path:
    """Create a skill with a run entrypoint and given module code."""
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    eps = f"  run: {run_entrypoint}\n"
    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        capabilities=capabilities,
        dependencies=dependencies,
        entrypoints=eps,
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    module_name = run_entrypoint.split(":")[0]
    (skill_dir / module_name).write_text(module_code, encoding="utf-8")
    return skill_dir


def create_skill_with_run_entrypoint(
    base_dir: Path,
    skill_id: str = "run-skill",
    version: str = "1.0.0",
) -> Path:
    """Create a skill with a legal run entrypoint."""
    code = '"""Legal run entrypoint."""\ndef run(context):\n    params = context.get("params", {})\n    return {"ok": True, "message": f"processed: {params}"}\n'
    return _make_run_skill(base_dir, skill_id, version, code)


def create_skill_without_run_entrypoint(
    base_dir: Path,
    skill_id: str = "no-run-ep",
    version: str = "1.0.0",
) -> Path:
    """Create a skill WITHOUT a run entrypoint (healthcheck only)."""
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints="  healthcheck: healthcheck.py:check\n",
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    (skill_dir / "healthcheck.py").write_text(
        make_healthcheck_py(healthy=True), encoding="utf-8"
    )
    return skill_dir


def create_skill_with_run_entrypoint_missing_file(
    base_dir: Path,
    skill_id: str = "missing-run-file",
    version: str = "1.0.0",
) -> Path:
    """Skill whose manifest says run entrypoint but the .py file is missing."""
    skill_dir = base_dir / skill_id / version
    skill_dir.mkdir(parents=True, exist_ok=True)

    eps = "  run: nonexistent.py:run\n"
    content = make_skill_manifest_content(
        skill_id=skill_id,
        version=version,
        skill_type="executable",
        entrypoints=eps,
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


def create_skill_with_missing_run_function(
    base_dir: Path,
    skill_id: str = "missing-run-func",
    version: str = "1.0.0",
) -> Path:
    """Skill whose run module exists but does NOT define 'run' function."""
    code = '"""Module with NO run function."""\ndef other_function(context):\n    return {"ok": True}\n'
    return _make_run_skill(base_dir, skill_id, version, code, run_entrypoint="run.py:run")


def create_skill_with_non_callable_run(
    base_dir: Path,
    skill_id: str = "non-callable-run",
    version: str = "1.0.0",
) -> Path:
    """Skill whose 'run' attribute is a string, not callable."""
    code = '"""Module where run is a string."""\nrun = "not callable"\n'
    return _make_run_skill(base_dir, skill_id, version, code, run_entrypoint="run.py:run")


def create_skill_run_returns_non_dict(
    base_dir: Path,
    skill_id: str = "run-non-dict",
    version: str = "1.0.0",
) -> Path:
    """Skill whose run() returns an int instead of a dict."""
    code = '"""run() returns non-dict."""\ndef run(context):\n    return 42\n'
    return _make_run_skill(base_dir, skill_id, version, code)


def create_skill_run_returns_non_json(
    base_dir: Path,
    skill_id: str = "run-non-json",
    version: str = "1.0.0",
) -> Path:
    """Skill whose run() returns a dict with a Path (non-JSON-serializable)."""
    code = '"""run() returns non-JSON value."""\nfrom pathlib import Path\n\ndef run(context):\n    return {"path": Path("/tmp/evil")}\n'
    return _make_run_skill(base_dir, skill_id, version, code)


def create_skill_run_returns_path_string(
    base_dir: Path,
    skill_id: str = "run-path-str",
    version: str = "1.0.0",
) -> Path:
    """Skill whose run() returns a path-like string."""
    code = '"""run() returns a path-like string."""\ndef run(context):\n    return {"path": "output/report.pdf", "ok": True}\n'
    return _make_run_skill(base_dir, skill_id, version, code)


def create_skill_run_forges_status(
    base_dir: Path,
    skill_id: str = "run-forge-status",
    version: str = "1.0.0",
) -> Path:
    """Skill whose run() tries to forge envelope status."""
    code = '"""run() tries to forge envelope status."""\ndef run(context):\n    return {"status": "healthy", "ok": "forged"}\n'
    return _make_run_skill(base_dir, skill_id, version, code)


def create_skill_run_writes_large_stdout(
    base_dir: Path,
    skill_id: str = "run-large-stdout",
    version: str = "1.0.0",
) -> Path:
    """Skill whose run() writes >1MB to stdout."""
    code = '"""run() writes lots of stdout."""\nimport sys\n\ndef run(context):\n    sys.stdout.write("X" * 2000000)\n    sys.stdout.flush()\n    return {"ok": True}\n'
    return _make_run_skill(base_dir, skill_id, version, code)


def create_skill_run_writes_large_stderr(
    base_dir: Path,
    skill_id: str = "run-large-stderr",
    version: str = "1.0.0",
) -> Path:
    """Skill whose run() writes lots to stderr."""
    code = '"""run() writes lots of stderr."""\nimport sys\n\ndef run(context):\n    sys.stderr.write("E" * 2000000)\n    sys.stderr.flush()\n    return {"ok": True}\n'
    return _make_run_skill(base_dir, skill_id, version, code)


def create_skill_run_hangs(
    base_dir: Path,
    skill_id: str = "run-hangs",
    version: str = "1.0.0",
    sleep_seconds: float = 30.0,
) -> Path:
    """Skill whose run() sleeps for a long time (timeout testing)."""
    code = '"""run() hangs."""\nimport time\n\ndef run(context):\n    time.sleep(%s)\n    return {"ok": True}\n' % sleep_seconds
    return _make_run_skill(base_dir, skill_id, version, code)


def create_skill_run_exits_nonzero(
    base_dir: Path,
    skill_id: str = "run-exit-nonzero",
    version: str = "1.0.0",
) -> Path:
    """Skill whose run() calls sys.exit(1)."""
    code = '"""run() exits with non-zero."""\nimport sys\n\ndef run(context):\n    sys.exit(1)\n'
    return _make_run_skill(base_dir, skill_id, version, code)


def create_skill_run_large_result(
    base_dir: Path,
    skill_id: str = "run-large-result",
    version: str = "1.0.0",
) -> Path:
    """Skill whose run() returns a result > MAX_RESULT_JSON_BYTES."""
    code = '"""run() returns oversized result."""\ndef run(context):\n    return {"data": "X" * 2000000}\n'
    return _make_run_skill(base_dir, skill_id, version, code)


def create_skill_run_business_failure(
    base_dir: Path,
    skill_id: str = "run-biz-fail",
    version: str = "1.0.0",
) -> Path:
    """Skill whose run() returns {"ok": false} — business negative outcome."""
    code = '"""run() returns business failure."""\ndef run(context):\n    return {"ok": False, "reason": "validation failed", "errors": ["field1"]}\n'
    return _make_run_skill(base_dir, skill_id, version, code)


def create_skill_run_with_sensitive_params(
    base_dir: Path,
    skill_id: str = "run-sensitive",
    version: str = "1.0.0",
) -> Path:
    """Skill whose run() echoes params (for secret redaction testing)."""
    code = '"""run() echoes params."""\ndef run(context):\n    params = context.get("params", {})\n    return {"echo": params, "ok": True}\n'
    return _make_run_skill(base_dir, skill_id, version, code)


# ══════════════════════════════════════════════════════════════════════
# Batch 3.1.1B — Run security boundary fixtures
# ══════════════════════════════════════════════════════════════════════


def _make_run_security_skill(
    base_dir: Path,
    skill_id: str,
    version: str,
    run_body_code: str,
    run_imports: str = "",
) -> Path:
    """Create a run skill whose run() function body attempts a restricted operation."""
    code = f'"""{skill_id} — run security test."""\n{run_imports}\n\ndef run(context):\n{run_body_code}\n    return {{"ok": True}}\n'
    return _make_run_skill(base_dir, skill_id, version, code)


def create_skill_run_socket_create(
    base_dir: Path,
    skill_id: str = "run-sock-create",
    version: str = "1.0.0",
) -> Path:
    """Run skill that tries socket.socket() inside run()."""
    return _make_run_security_skill(
        base_dir, skill_id, version,
        run_imports="import socket\n",
        run_body_code="    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n",
    )


def create_skill_run_socket_connect(
    base_dir: Path,
    skill_id: str = "run-sock-connect",
    version: str = "1.0.0",
) -> Path:
    """Run skill that tries socket.socket() + connect() inside run()."""
    return _make_run_security_skill(
        base_dir, skill_id, version,
        run_imports="import socket\n",
        run_body_code='    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n    s.connect(("192.0.2.1", 80))\n',
    )


def create_skill_run_subprocess(
    base_dir: Path,
    skill_id: str = "run-subprocess",
    version: str = "1.0.0",
) -> Path:
    """Run skill that tries subprocess.Popen inside run()."""
    return _make_run_security_skill(
        base_dir, skill_id, version,
        run_imports="import subprocess\n",
        run_body_code='    subprocess.Popen(["python", "-c", "print(1)"])\n',
    )


def create_skill_run_os_system(
    base_dir: Path,
    skill_id: str = "run-os-system",
    version: str = "1.0.0",
) -> Path:
    """Run skill that tries os.system inside run()."""
    return _make_run_security_skill(
        base_dir, skill_id, version,
        run_imports="import os\n",
        run_body_code='    os.system("echo test")\n',
    )


def create_skill_run_ctypes(
    base_dir: Path,
    skill_id: str = "run-ctypes",
    version: str = "1.0.0",
) -> Path:
    """Run skill that tries ctypes library load inside run().

    Uses a unique non-existent library path so that the ctypes.dlopen
    audit event fires reliably before the OS attempts the actual load.
    A library that is already loaded (e.g. kernel32.dll) may bypass the
    audit event on some platforms.
    """
    return _make_run_security_skill(
        base_dir, skill_id, version,
        run_imports="import ctypes\n",
        run_body_code='    ctypes.cdll.LoadLibrary("nonexistent_b311b_ctypes_test_7a3f2c.dll")\n',
    )


def create_skill_run_write_installed(
    base_dir: Path,
    skill_id: str = "run-write-installed",
    version: str = "1.0.0",
) -> Path:
    """Run skill that tries to write to its installed directory."""
    return _make_run_security_skill(
        base_dir, skill_id, version,
        run_imports="from pathlib import Path\n",
        run_body_code='    p = Path(context.get("installed_path", "")) / "tampered.txt"\n    p.write_text("bad")\n',
    )


def create_skill_run_write_outside(
    base_dir: Path,
    skill_id: str = "run-write-outside",
    version: str = "1.0.0",
) -> Path:
    """Run skill that tries to write outside workspace (to temp dir)."""
    return _make_run_security_skill(
        base_dir, skill_id, version,
        run_imports="import tempfile\nfrom pathlib import Path\n",
        run_body_code='    p = Path(tempfile.gettempdir()) / "run_escape_test.txt"\n    p.write_text("bad")\n',
    )


def create_skill_run_read_registry(
    base_dir: Path,
    skill_id: str = "run-read-registry",
    version: str = "1.0.0",
    *,
    registry_path: str = "",
) -> Path:
    """Run skill that tries to read the registry file."""
    rp = registry_path or "C:/nonexistent_path/registry.json"
    return _make_run_security_skill(
        base_dir, skill_id, version,
        run_imports="from pathlib import Path\n",
        run_body_code=f'    Path(r"{rp}").read_text()\n',
    )


def create_skill_run_modify_registry(
    base_dir: Path,
    skill_id: str = "run-modify-registry",
    version: str = "1.0.0",
    *,
    registry_path: str = "",
) -> Path:
    """Run skill that tries to write to the registry file."""
    rp = registry_path or "C:/nonexistent_path/registry.json"
    return _make_run_security_skill(
        base_dir, skill_id, version,
        run_imports="from pathlib import Path\n",
        run_body_code=f'    Path(r"{rp}").write_text(\'{{"tampered": true}}\')\n',
    )


def create_skill_run_symlink_escape(
    base_dir: Path,
    skill_id: str = "run-symlink-escape",
    version: str = "1.0.0",
) -> Path:
    """Run skill that creates a symlink in workspace/output, then reads through it.

    The symlink is created inside workspace/output (writable), pointing to a
    file outside allowed roots.  The read triggers the audit hook's path
    resolution → PermissionError → Worker → permission_denied.

    The skill does NOT catch PermissionError — it propagates to the Worker.
    """
    code = '''"""run() creates symlink in workspace/output, reads through it."""
import os
from pathlib import Path

def run(context):
    ws = Path(context.get("workspace_path", ""))
    out = ws / "output"
    out.mkdir(parents=True, exist_ok=True)
    link = out / "link_to_outside"
    # Use SYSTEMROOT\system.ini as the target — definitely outside allowed roots
    system_root = os.environ.get("SYSTEMROOT", r"C:\\Windows")
    target = Path(system_root) / "system.ini"
    # Create symlink (may fail on locked-down Windows — let OSError propagate)
    if link.exists():
        link.unlink()
    link.symlink_to(target)
    # Read through the symlink — audit hook must block this
    # Do NOT catch PermissionError — let it propagate to the Worker
    content = link.read_text(errors="replace")
    return {"ok": True, "escaped": True, "content": content}
'''
    return _make_run_skill(base_dir, skill_id, version, code)


def create_skill_run_dot_dot_escape(
    base_dir: Path,
    skill_id: str = "run-dotdot-escape",
    version: str = "1.0.0",
) -> Path:
    """Run skill that tries to read via .. path traversal outside workspace."""
    return _make_run_security_skill(
        base_dir, skill_id, version,
        run_imports="from pathlib import Path\n",
        run_body_code='    p = Path(context.get("workspace_path", "")) / "../../../etc/passwd"\n    p.read_text()\n',
    )


def create_skill_run_project_root_escape(
    base_dir: Path,
    skill_id: str = "run-proj-root-escape",
    version: str = "1.0.0",
) -> Path:
    """Run skill that tries to read from parent directory (project root / CWD)."""
    return _make_run_security_skill(
        base_dir, skill_id, version,
        run_imports="from pathlib import Path\n",
        run_body_code='    p = Path(context.get("installed_path", "")).parent.parent.parent / "secret.txt"\n    p.read_text()\n',
    )


# ══════════════════════════════════════════════════════════════════════
# Batch 3.1.1B — Registry / dependency / timeout / cancel fixtures
# ══════════════════════════════════════════════════════════════════════


def create_skill_run_with_dependency(
    base_dir: Path,
    skill_id: str = "run-with-dep",
    version: str = "1.0.0",
    *,
    dependency_spec: str = "",
) -> Path:
    """Run skill with a specific dependency requirement."""
    return _make_run_skill(
        base_dir, skill_id, version,
        module_code='"""run with dependency."""\ndef run(context):\n    return {"ok": True}\n',
        dependencies=f"  - {dependency_spec}\n" if dependency_spec else "",
    )


def create_skill_run_raises_exception(
    base_dir: Path,
    skill_id: str = "run-raises",
    version: str = "1.0.0",
) -> Path:
    """Run skill whose run() raises an unhandled exception → status='failed'."""
    code = '"""run() raises exception."""\ndef run(context):\n    raise ValueError("simulated run error")\n'
    return _make_run_skill(base_dir, skill_id, version, code)


def create_skill_run_returns_non_json(
    base_dir: Path,
    skill_id: str = "run-non-json-v2",
    version: str = "1.0.0",
) -> Path:
    """Run skill whose run() returns non-JSON-compatible value (NaN)."""
    code = '"""run() returns NaN."""\ndef run(context):\n    return {"value": float("nan")}\n'
    return _make_run_skill(base_dir, skill_id, version, code)
