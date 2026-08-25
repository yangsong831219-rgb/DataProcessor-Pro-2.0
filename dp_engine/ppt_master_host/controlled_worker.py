"""Isolated worker for one Host-authorized PPT Master command.

This file is launched with ``python -I -B``.  It uses only the standard
library, scrubs the inherited environment, installs deny-by-default audit
hooks, and only then executes the attested bundled script with ``runpy``.
"""

from __future__ import annotations

import json
import importlib.abc
import importlib.util
import os
import runpy
import subprocess
import sys
import traceback
from collections.abc import Sequence
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any


_MAX_REQUEST_BYTES = 256 * 1024
_SECRET_ENV_MARKERS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "API_KEY",
    "ACCESS_KEY",
    "PRIVATE_KEY",
)
_BLOCKED_EXACT_EVENTS = frozenset(
    {
        "ctypes.dlopen",
        "os.startfile",
        "os.system",
        "pty.spawn",
        "socket.__new__",
        "socket.bind",
        "socket.connect",
        "socket.getaddrinfo",
    }
)
_WRITE_SINGLE_PATH_EVENTS = frozenset(
    {
        "os.chmod",
        "os.mkdir",
        "os.remove",
        "os.rmdir",
        "os.truncate",
        "os.unlink",
        "os.utime",
    }
)
_READ_SINGLE_PATH_EVENTS = frozenset(
    {
        "os.chdir",
        "os.listdir",
        "os.scandir",
    }
)


def _normalized(path: str | bytes | os.PathLike[str]) -> str:
    value = os.fsdecode(path)
    resolved = os.path.realpath(os.path.abspath(value))
    return os.path.normcase(resolved)


def _is_within(path: str, roots: tuple[str, ...]) -> bool:
    for root in roots:
        try:
            if os.path.commonpath((path, root)) == root:
                return True
        except ValueError:
            continue
    return False


def _check_path(
    value: object,
    *,
    roots: tuple[str, ...],
    access: str,
) -> None:
    if isinstance(value, int) or not isinstance(value, (str, bytes, os.PathLike)):
        return
    path = _normalized(value)
    if not _is_within(path, roots):
        raise PermissionError(
            f"controlled_tool_denied: {access} path is outside allowed roots: {path}"
        )


def _check_read_path(
    value: object,
    *,
    allowed_roots: tuple[str, ...],
    restricted_roots: tuple[str, ...],
    approved_dependency_roots: tuple[str, ...],
) -> None:
    if isinstance(value, int) or not isinstance(value, (str, bytes, os.PathLike)):
        return
    path = _normalized(value)
    if _is_within(path, restricted_roots) and not _is_within(
        path,
        approved_dependency_roots,
    ):
        raise PermissionError(
            "controlled_tool_denied: read path belongs to an unattested "
            f"dependency: {path}"
        )
    if not _is_within(path, allowed_roots):
        raise PermissionError(
            f"controlled_tool_denied: read path is outside allowed roots: {path}"
        )


class _ApprovedDependencyFinder(importlib.abc.MetaPathFinder):
    """Resolve only attested top-level packages without scanning site-packages."""

    def __init__(
        self,
        *,
        approved_dependency_roots: tuple[str, ...],
        dependency_sys_paths: tuple[str, ...],
    ) -> None:
        packages: dict[str, Path] = {}
        sys_paths = tuple(
            Path(path).resolve(strict=False) for path in dependency_sys_paths
        )
        for raw_root in approved_dependency_roots:
            root = Path(raw_root).resolve(strict=False)
            if not root.is_dir() or not (root / "__init__.py").is_file():
                continue
            if any(
                _is_direct_child(root, sys_path)
                for sys_path in sys_paths
            ):
                packages[root.name] = root
        self._packages = packages

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        del path, target
        if "." in fullname:
            return None
        package = self._packages.get(fullname)
        if package is None:
            return None
        return importlib.util.spec_from_file_location(
            fullname,
            package / "__init__.py",
            submodule_search_locations=[str(package)],
        )


def _is_direct_child(path: Path, parent: Path) -> bool:
    try:
        relative = path.relative_to(parent)
    except ValueError:
        return False
    return len(relative.parts) == 1


def _open_is_write(args: tuple[object, ...]) -> bool:
    mode = args[1] if len(args) > 1 else "r"
    if isinstance(mode, str):
        return any(char in mode for char in "wax+")
    flags = args[2] if len(args) > 2 else 0
    if not isinstance(flags, int):
        return False
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
    return bool(flags & write_flags)


def _install_audit_hook(
    *,
    allowed_read_roots: tuple[str, ...],
    allowed_write_roots: tuple[str, ...],
    restricted_read_roots: tuple[str, ...],
    approved_dependency_roots: tuple[str, ...],
) -> None:
    read_roots = tuple(_normalized(path) for path in allowed_read_roots)
    write_roots = tuple(_normalized(path) for path in allowed_write_roots)
    restricted_roots = tuple(_normalized(path) for path in restricted_read_roots)
    dependency_roots = tuple(
        _normalized(path) for path in approved_dependency_roots
    )

    def audit(event: str, args: tuple[object, ...]) -> None:
        if (
            event in _BLOCKED_EXACT_EVENTS
            or event.startswith("subprocess.")
            or event.startswith("winreg.")
            or event.startswith("os.spawn")
        ):
            raise RuntimeError(f"controlled_tool_denied: blocked audit event {event}")

        if event == "open" and args:
            if _open_is_write(args):
                _check_path(args[0], roots=write_roots, access="write")
            else:
                _check_read_path(
                    args[0],
                    allowed_roots=read_roots,
                    restricted_roots=restricted_roots,
                    approved_dependency_roots=dependency_roots,
                )
            return

        if event in _READ_SINGLE_PATH_EVENTS and args and args[0] is not None:
            _check_read_path(
                args[0],
                allowed_roots=read_roots,
                restricted_roots=restricted_roots,
                approved_dependency_roots=dependency_roots,
            )
            return

        if event in _WRITE_SINGLE_PATH_EVENTS and args:
            _check_path(args[0], roots=write_roots, access="write")
            return

        if event in {"os.rename", "os.replace"} and len(args) >= 2:
            _check_path(args[0], roots=write_roots, access="write")
            _check_path(args[1], roots=write_roots, access="write")
            return

        if event in {"os.link", "os.symlink"} and len(args) >= 2:
            _check_path(args[0], roots=read_roots, access="read")
            _check_path(args[1], roots=write_roots, access="write")
            return

        if event == "shutil.copyfile" and len(args) >= 2:
            _check_path(args[0], roots=read_roots, access="read")
            _check_path(args[1], roots=write_roots, access="write")
            return

        if event in {"shutil.copymode", "shutil.copystat"} and len(args) >= 2:
            _check_path(args[0], roots=read_roots, access="read")
            _check_path(args[1], roots=write_roots, access="write")

    sys.addaudithook(audit)


def _scrub_environment() -> None:
    for name in tuple(os.environ):
        upper = name.upper()
        if any(marker in upper for marker in _SECRET_ENV_MARKERS):
            del os.environ[name]
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PYTHONUNBUFFERED"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8:replace"
    sys.dont_write_bytecode = True


def _read_request(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > _MAX_REQUEST_BYTES:
        raise ValueError("controlled worker request is missing or too large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("controlled worker request must be an object")
    return value


def _required_string(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"controlled worker field must be a string: {field}")
    return value


def _required_string_list(data: dict[str, Any], field: str) -> tuple[str, ...]:
    value = data.get(field)
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"controlled worker field must be a string list: {field}")
    return tuple(value)


def _required_string_matrix(
    data: dict[str, Any],
    field: str,
) -> tuple[tuple[str, ...], ...]:
    value = data.get(field)
    if not isinstance(value, list) or any(
        not isinstance(row, list)
        or not row
        or any(not isinstance(item, str) or not item for item in row)
        for row in value
    ):
        raise ValueError(f"controlled worker field must be a string matrix: {field}")
    return tuple(tuple(row) for row in value)


def _install_suppressed_subprocess_adapter(
    suppressed_argv: tuple[tuple[str, ...], ...],
) -> None:
    """Record and no-op exact Host-declared optional helper invocations."""
    suppressed = frozenset(suppressed_argv)
    original_run: Any = subprocess.run

    def controlled_run(
        *popenargs: Any,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]:
        raw_args = popenargs[0] if popenargs else kwargs.get("args")
        normalized: tuple[str, ...] | None = None
        if isinstance(raw_args, (list, tuple)) and all(
            isinstance(item, (str, bytes, os.PathLike)) for item in raw_args
        ):
            normalized = tuple(os.fsdecode(item) for item in raw_args)
        if normalized is not None and normalized in suppressed:
            text_mode = bool(
                kwargs.get("text")
                or kwargs.get("universal_newlines")
                or kwargs.get("encoding")
            )
            empty: str | bytes = "" if text_mode else b""
            print(
                "controlled_optional_subprocess_suppressed: "
                f"{normalized[0]}",
                file=sys.stderr,
            )
            return subprocess.CompletedProcess(normalized, 0, empty, empty)
        return original_run(*popenargs, **kwargs)

    subprocess.run = controlled_run


def main(argv: list[str] | None = None) -> int:
    selected = list(sys.argv[1:] if argv is None else argv)
    if len(selected) != 1:
        print("controlled worker requires exactly one request path", file=sys.stderr)
        return 64

    try:
        request_path = Path(selected[0]).resolve(strict=True)
        data = _read_request(request_path)
        tool_path = Path(_required_string(data, "tool_path")).resolve(strict=True)
        cwd = Path(_required_string(data, "cwd")).resolve(strict=True)
        tool_argv = _required_string_list(data, "tool_argv")
        read_roots = _required_string_list(data, "allowed_read_roots")
        write_roots = _required_string_list(data, "allowed_write_roots")
        restricted_roots = _required_string_list(data, "restricted_read_roots")
        dependency_roots = _required_string_list(
            data,
            "approved_dependency_roots",
        )
        dependency_sys_paths = _required_string_list(data, "dependency_sys_paths")
        suppressed_subprocess_argv = _required_string_matrix(
            data,
            "suppressed_subprocess_argv",
        )

        _scrub_environment()
        for dependency_path in reversed(dependency_sys_paths):
            if dependency_path not in sys.path:
                sys.path.insert(0, dependency_path)
        sys.meta_path.insert(
            0,
            _ApprovedDependencyFinder(
                approved_dependency_roots=dependency_roots,
                dependency_sys_paths=dependency_sys_paths,
            ),
        )
        tool_parent = str(tool_path.parent)
        if tool_parent not in sys.path:
            sys.path.insert(0, tool_parent)
        os.chdir(cwd)
        _install_suppressed_subprocess_adapter(suppressed_subprocess_argv)
        _install_audit_hook(
            allowed_read_roots=read_roots,
            allowed_write_roots=write_roots,
            restricted_read_roots=restricted_roots,
            approved_dependency_roots=dependency_roots,
        )
        sys.argv = [str(tool_path), *tool_argv]
        runpy.run_path(str(tool_path), run_name="__main__")
        return 0
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 1
    except BaseException:  # worker must turn every failure into an exit status
        traceback.print_exc()
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
