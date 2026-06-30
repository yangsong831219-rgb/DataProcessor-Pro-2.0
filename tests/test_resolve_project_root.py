"""_resolve_project_root_from_files 纯函数测试 — 方案B: 项目资料库锚点

覆盖场景表全部:
- 正常单项目 → 正确根
- 深层子目录 → 正确上推到项目
- 文件不在资料库下 → ValueError "不属于任何项目"
- 跨项目 → ValueError "跨多个项目"
- 路径含白名单词 → 不受影响
- 跨盘符 → 报跨项目不崩
- 关联资料空 → "请先添加项目关联资料"
"""

from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import _resolve_project_root_from_files


# ── helpers ──

def _mk(p: str, *sub: str) -> str:
    """创建目录树，返回最终路径。"""
    parent = p
    for s in sub:
        parent = os.path.join(parent, s)
    os.makedirs(parent, exist_ok=True)
    return parent


def _mkfile(p: str, *sub_name: str) -> str:
    """创建目录 + 文件，返回文件路径。"""
    d = os.path.join(p, *sub_name[:-1])
    os.makedirs(d, exist_ok=True)
    f = os.path.join(d, sub_name[-1])
    with open(f, "w") as fh:
        fh.write("test")
    return f


class TestSingleProject:
    """正常单项目 → 正确项目根"""

    def test_file_in_data_subdir(self, tmp_path):
        lib = _mk(tmp_path, "项目资料库")
        _mkfile(lib, "三组标定", "数据", "data.csv")
        root = _resolve_project_root_from_files(
            [os.path.join(lib, "三组标定", "数据", "data.csv")],
            library_root=lib,
        )
        assert os.path.normpath(root) == os.path.normpath(os.path.join(lib, "三组标定"))

    def test_file_in_report_subdir(self, tmp_path):
        lib = _mk(tmp_path, "项目资料库")
        _mkfile(lib, "三组标定", "报告", "report.docx")
        root = _resolve_project_root_from_files(
            [os.path.join(lib, "三组标定", "报告", "report.docx")],
            library_root=lib,
        )
        assert os.path.normpath(root) == os.path.normpath(os.path.join(lib, "三组标定"))

    def test_file_at_project_root(self, tmp_path):
        lib = _mk(tmp_path, "项目资料库")
        _mkfile(lib, "三组标定", "项目说明.txt")
        root = _resolve_project_root_from_files(
            [os.path.join(lib, "三组标定", "项目说明.txt")],
            library_root=lib,
        )
        assert os.path.normpath(root) == os.path.normpath(os.path.join(lib, "三组标定"))

    def test_deep_subdir_still_resolves(self, tmp_path):
        """三组标定/图片/子目录/chart.png → 推到三组标定 (方案B关键优势)"""
        lib = _mk(tmp_path, "项目资料库")
        _mkfile(lib, "三组标定", "图片", "子目录", "chart.png")
        root = _resolve_project_root_from_files(
            [os.path.join(lib, "三组标定", "图片", "子目录", "chart.png")],
            library_root=lib,
        )
        assert os.path.normpath(root) == os.path.normpath(os.path.join(lib, "三组标定"))

    def test_path_with_whitelist_word_unaffected(self, tmp_path):
        """三组标定/报告/数据/x.docx — 报告和数据都是白名单词, 方案B不受影响"""
        lib = _mk(tmp_path, "项目资料库")
        _mkfile(lib, "三组标定", "报告", "数据", "x.docx")
        root = _resolve_project_root_from_files(
            [os.path.join(lib, "三组标定", "报告", "数据", "x.docx")],
            library_root=lib,
        )
        assert os.path.normpath(root) == os.path.normpath(os.path.join(lib, "三组标定"))


class TestMultiFileSameProject:
    """多文件同项目 → 返回统一根"""

    def test_two_files_same_project(self, tmp_path):
        lib = _mk(tmp_path, "项目资料库")
        f1 = _mkfile(lib, "三组标定", "数据", "a.csv")
        f2 = _mkfile(lib, "三组标定", "报告", "b.docx")
        root = _resolve_project_root_from_files([f1, f2], library_root=lib)
        assert os.path.normpath(root) == os.path.normpath(os.path.join(lib, "三组标定"))


class TestFileNotUnderLibrary:
    """文件不在项目资料库下 → ValueError"""

    def test_desktop_file_raises(self, tmp_path):
        lib = _mk(tmp_path, "项目资料库")
        f = _mkfile(tmp_path, "桌面文件.xlsx")
        with pytest.raises(ValueError, match="不属于任何项目"):
            _resolve_project_root_from_files([f], library_root=lib)

    def test_sibling_dir_raises(self, tmp_path):
        lib = _mk(tmp_path, "项目资料库")
        nonlib = _mk(tmp_path, "随便放")
        f = _mkfile(nonlib, "test.csv")
        with pytest.raises(ValueError, match="不属于任何项目"):
            _resolve_project_root_from_files([f], library_root=lib)


class TestCrossProject:
    """跨项目 → ValueError"""

    def test_two_projects_raises(self, tmp_path):
        lib = _mk(tmp_path, "项目资料库")
        f1 = _mkfile(lib, "项目A", "数据", "a.csv")
        f2 = _mkfile(lib, "项目B", "数据", "b.csv")
        with pytest.raises(ValueError, match="跨多个项目"):
            _resolve_project_root_from_files([f1, f2], library_root=lib)

    def test_three_projects_raises(self, tmp_path):
        lib = _mk(tmp_path, "项目资料库")
        files = [
            _mkfile(lib, "A", "数据", "a.csv"),
            _mkfile(lib, "B", "数据", "b.csv"),
            _mkfile(lib, "C", "数据", "c.csv"),
        ]
        with pytest.raises(ValueError, match="跨多个项目"):
            _resolve_project_root_from_files(files, library_root=lib)


class TestEmptyFiles:
    """空列表 → ValueError"""

    def test_empty_list_raises(self):
        with pytest.raises(ValueError, match="请先添加项目关联资料"):
            _resolve_project_root_from_files([])


class TestCrossDrive:
    """跨盘符 → 报跨项目 (逐文件判断, 不依赖 commonpath)"""

    def test_cross_drive_raises(self, tmp_path):
        lib = _mk(tmp_path, "项目资料库")
        f1 = _mkfile(lib, "项目A", "数据", "a.csv")
        # 模拟另一盘符下的文件
        f2 = "D:\\other_lib\\项目B\\数据\\b.csv" if os.name == "nt" \
            else "/mnt/other/项目B/数据/b.csv"
        with pytest.raises(ValueError, match="不属于任何项目"):
            _resolve_project_root_from_files([f1, f2], library_root=lib)
