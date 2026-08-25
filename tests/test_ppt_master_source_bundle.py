from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from dp_engine.ppt_master_host.source_bundle import (
    PPT_MASTER_2_7_0_CONTRACT,
    PptMasterSourceQualificationError,
    qualify_ppt_master_source_archive,
)


def _metadata(*, version: str = "2.7.0") -> dict[str, object]:
    return {
        "name": "ppt-master",
        "metadata": {"version": version},
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


def _write_source_archive(
    path: Path,
    *,
    version: str = "2.7.0",
    omit: str = "",
    special_path: str = "",
) -> str:
    root = PPT_MASTER_2_7_0_CONTRACT.root_prefix
    contents = {
        relative_path: b"fixture\n"
        for relative_path in PPT_MASTER_2_7_0_CONTRACT.critical_relative_paths
    }
    contents[".claude-plugin/marketplace.json"] = json.dumps(
        _metadata(version=version)
    ).encode("utf-8")
    contents["skills/.claude-plugin/plugin.json"] = json.dumps(
        _plugin_manifest()
    ).encode("utf-8")
    if omit:
        del contents[omit]

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path, payload in contents.items():
            archive.writestr(root + relative_path, payload)
        if special_path:
            info = zipfile.ZipInfo(root + special_path)
            info.create_system = 3
            info.external_attr = (stat.S_IFIFO | 0o644) << 16
            archive.writestr(info, b"")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture_contract(archive_sha256: str):
    return replace(
        PPT_MASTER_2_7_0_CONTRACT,
        archive_sha256=archive_sha256,
    )


def test_qualifies_matching_read_only_source_bundle(tmp_path: Path) -> None:
    archive_path = tmp_path / "ppt-master.zip"
    digest = _write_source_archive(archive_path)

    bundle = qualify_ppt_master_source_archive(
        archive_path,
        contract=_fixture_contract(digest),
    )

    assert bundle.archive_path == archive_path.resolve()
    assert bundle.version == "2.7.0"
    assert bundle.archive_sha256 == digest
    assert bundle.entry_count == len(
        PPT_MASTER_2_7_0_CONTRACT.critical_relative_paths
    )
    assert not (tmp_path / "ppt-master-main").exists()


def test_rejects_unreviewed_archive_checksum(tmp_path: Path) -> None:
    archive_path = tmp_path / "ppt-master.zip"
    _write_source_archive(archive_path)

    with pytest.raises(PptMasterSourceQualificationError) as caught:
        qualify_ppt_master_source_archive(
            archive_path,
            contract=PPT_MASTER_2_7_0_CONTRACT,
        )

    assert caught.value.code == "checksum_mismatch"


def test_rejects_release_identity_mismatch(tmp_path: Path) -> None:
    archive_path = tmp_path / "ppt-master.zip"
    digest = _write_source_archive(archive_path, version="2.6.0")

    with pytest.raises(PptMasterSourceQualificationError) as caught:
        qualify_ppt_master_source_archive(
            archive_path,
            contract=_fixture_contract(digest),
        )

    assert caught.value.code == "identity_mismatch"


def test_rejects_missing_controlled_tool(tmp_path: Path) -> None:
    archive_path = tmp_path / "ppt-master.zip"
    missing = "skills/ppt-master/scripts/svg_to_pptx.py"
    digest = _write_source_archive(archive_path, omit=missing)

    with pytest.raises(PptMasterSourceQualificationError) as caught:
        qualify_ppt_master_source_archive(
            archive_path,
            contract=_fixture_contract(digest),
        )

    assert caught.value.code == "critical_files_missing"
    assert missing in str(caught.value)


def test_rejects_special_zip_entry(tmp_path: Path) -> None:
    archive_path = tmp_path / "ppt-master.zip"
    digest = _write_source_archive(archive_path, special_path="unsafe.pipe")

    with pytest.raises(PptMasterSourceQualificationError) as caught:
        qualify_ppt_master_source_archive(
            archive_path,
            contract=_fixture_contract(digest),
        )

    assert caught.value.code == "archive_safety"
