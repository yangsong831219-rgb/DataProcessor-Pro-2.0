from __future__ import annotations

import hashlib
import json
import os
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from dp_engine.ppt_master_host import bundle_store as bundle_store_module
from dp_engine.ppt_master_host.bundle_store import (
    PptMasterBundleInstallCancelled,
    PptMasterBundleStore,
    PptMasterBundleStoreError,
    PptMasterBundleStorePaths,
)
from dp_engine.ppt_master_host.source_bundle import PPT_MASTER_2_7_0_CONTRACT


def _marketplace() -> dict[str, object]:
    return {
        "name": "ppt-master",
        "metadata": {"version": "2.7.0"},
        "plugins": [
            {
                "name": "ppt-master",
                "source": {
                    "url": "https://github.com/hugohe3/ppt-master.git",
                },
                "license": "MIT",
            }
        ],
    }


def _plugin_manifest() -> dict[str, object]:
    return {
        "name": "ppt-master",
        "license": "MIT",
        "repository": "https://github.com/hugohe3/ppt-master",
        "skills": "./",
    }


def _write_source_archive(path: Path) -> str:
    root = PPT_MASTER_2_7_0_CONTRACT.root_prefix
    contents = {
        relative_path: f"fixture:{relative_path}\n".encode("utf-8")
        for relative_path in PPT_MASTER_2_7_0_CONTRACT.critical_relative_paths
    }
    contents[".claude-plugin/marketplace.json"] = json.dumps(
        _marketplace()
    ).encode("utf-8")
    contents["skills/.claude-plugin/plugin.json"] = json.dumps(
        _plugin_manifest()
    ).encode("utf-8")
    contents["skills/ppt-master/templates/icons/kept.svg"] = b"<svg/>"
    contents[
        "skills/ppt-master/references/ai-image-comparison/ignored.png"
    ] = b"ignored-demo"
    contents["examples/ignored.txt"] = b"ignored-repository-example"

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path, payload in contents.items():
            archive.writestr(root + relative_path, payload)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_store(tmp_path: Path) -> tuple[PptMasterBundleStore, Path]:
    archive_path = tmp_path / "ppt-master.zip"
    digest = _write_source_archive(archive_path)
    contract = replace(PPT_MASTER_2_7_0_CONTRACT, archive_sha256=digest)
    store = PptMasterBundleStore(
        PptMasterBundleStorePaths(tmp_path / "toolchains" / "ppt-master"),
        source_contract=contract,
        expected_tree_sha256=None,
    )
    return store, archive_path


def test_selectively_installs_without_skill_registry_or_execution(
    tmp_path: Path,
) -> None:
    store, archive_path = _make_store(tmp_path)

    installed = store.install_archive(archive_path)

    assert installed.created is True
    assert (installed.install_path / "LICENSE").is_file()
    assert (
        installed.install_path
        / "skills/ppt-master/templates/icons/kept.svg"
    ).is_file()
    assert not (
        installed.install_path
        / "skills/ppt-master/references/ai-image-comparison/ignored.png"
    ).exists()
    assert not (installed.install_path / "examples/ignored.txt").exists()
    assert (installed.install_path / ".source-bundle.json").is_file()
    assert not (store.paths.root / "registry.json").exists()


def test_intact_reinstall_is_idempotent(tmp_path: Path) -> None:
    store, archive_path = _make_store(tmp_path)
    first = store.install_archive(archive_path)
    attestation = first.install_path / ".source-bundle.json"
    original_mtime = attestation.stat().st_mtime_ns

    second = store.install_archive(archive_path)

    assert second.created is False
    assert second.install_path == first.install_path
    assert attestation.stat().st_mtime_ns == original_mtime
    assert list(store.paths.staging_dir.iterdir()) == []


def test_cancellation_rolls_back_current_transaction(tmp_path: Path) -> None:
    store, archive_path = _make_store(tmp_path)
    calls = 0

    def cancel_during_extraction() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 3

    with pytest.raises(PptMasterBundleInstallCancelled):
        store.install_archive(
            archive_path,
            cancel_check=cancel_during_extraction,
        )

    assert list(store.paths.staging_dir.iterdir()) == []
    assert not any(store.paths.installed_dir.rglob(".source-bundle.json"))


def test_wrong_pinned_tree_rolls_back_without_commit(tmp_path: Path) -> None:
    store, archive_path = _make_store(tmp_path)
    contract = replace(
        PPT_MASTER_2_7_0_CONTRACT,
        archive_sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
    )
    mismatched = PptMasterBundleStore(
        store.paths,
        source_contract=contract,
        expected_tree_sha256="0" * 64,
    )

    with pytest.raises(PptMasterBundleStoreError) as caught:
        mismatched.install_archive(archive_path)

    assert caught.value.code == "tree_digest_mismatch"
    assert list(store.paths.staging_dir.iterdir()) == []
    assert not any(store.paths.installed_dir.rglob(".source-bundle.json"))


def test_tampered_existing_install_is_not_overwritten(tmp_path: Path) -> None:
    store, archive_path = _make_store(tmp_path)
    installed = store.install_archive(archive_path)
    license_path = installed.install_path / "LICENSE"
    license_path.write_text("tampered", encoding="utf-8")

    with pytest.raises(PptMasterBundleStoreError) as caught:
        store.install_archive(archive_path)

    assert caught.value.code == "install_conflict"
    assert license_path.read_text(encoding="utf-8") == "tampered"
    assert list(store.paths.staging_dir.iterdir()) == []


def test_atomic_commit_failure_cleans_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, archive_path = _make_store(tmp_path)
    real_replace = os.replace

    def fail_final_commit(source: str, destination: str) -> None:
        if "installed" in Path(destination).parts:
            raise OSError("simulated commit failure")
        real_replace(source, destination)

    monkeypatch.setattr(bundle_store_module.os, "replace", fail_final_commit)

    with pytest.raises(PptMasterBundleStoreError) as caught:
        store.install_archive(archive_path)

    assert caught.value.code == "commit_failed"
    assert list(store.paths.staging_dir.iterdir()) == []
    assert not any(store.paths.installed_dir.rglob(".source-bundle.json"))
