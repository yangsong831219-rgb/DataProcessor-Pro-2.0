"""项目统计同步 + 标签去重 回归测试。

验证:
1. 标签无重复前缀 ("项目数: 项目数: 0" → "项目数: 0")
2. 项目计数 = 项目列表项目数
3. 文件夹/文件计数 = real os.walk 结果
4. clear_tree 归零
"""

import os
import tempfile
import pytest


# ── Fixture: 构造真实项目树 ──


@pytest.fixture
def sample_project_tree(tmp_path):
    """创建临时项目树: 2 文件夹 + 3 文件."""
    proj = tmp_path / "test_project"
    proj.mkdir()
    sub1 = proj / "docs"
    sub1.mkdir()
    sub2 = proj / "data"
    sub2.mkdir()
    (proj / "readme.md").write_text("# Project")
    (sub1 / "notes.txt").write_text("notes")
    (sub2 / "sensor.csv").write_text("col1,col2\n1,2")
    return str(proj)


# ── Tests ──


class TestProjectStats:
    """验证: load_project_tree 后统计正确，无重复前缀。"""

    def test_label_no_duplicate_prefix(self, qapp):
        """QLabel 文字不含重复的 '项目数:' 前缀。"""
        from ui.project_tab import ProjectManagerWidget
        w = ProjectManagerWidget()
        try:
            w.set_project_list(["p1", "p2"])
            # 标签现在只含数字
            assert w.project_stats_label.text() == "2"
            # QFormLayout 会在左列加 "项目数:"
        finally:
            w.deleteLater()

    def test_project_count_matches_list(self, qapp):
        """项目计数 = QListWidget 项目数."""
        from ui.project_tab import ProjectManagerWidget
        w = ProjectManagerWidget()
        try:
            w.set_project_list(["proj_a", "proj_b", "proj_c"])
            assert w.project_stats_label.text() == "3"
        finally:
            w.deleteLater()

    def test_tree_loading_syncs_stats(self, qapp, sample_project_tree):
        """加载真实项目树后 folders/files 不为 0."""
        from ui.project_tab import ProjectManagerWidget
        w = ProjectManagerWidget()
        try:
            w.set_project_list(["test_project"])
            w.load_project_tree(sample_project_tree)
            folders = int(w.project_folders_label.text())
            files = int(w.project_files_count_label.text())
            # 有 2 个真实子文件夹 + 根目录算 1 个文件夹项 → ≥2
            assert folders >= 2, f"子文件夹应为 ≥2, 实际 {folders}"
            assert files == 3, f"文件数应为 3, 实际 {files}"
        finally:
            w.deleteLater()

    def test_clear_tree_resets_stats(self, qapp, sample_project_tree):
        """clear_tree 后 folders/files 归零，但项目计数保留."""
        from ui.project_tab import ProjectManagerWidget
        w = ProjectManagerWidget()
        try:
            w.set_project_list(["test_project"])
            w.load_project_tree(sample_project_tree)
            w.clear_tree()
            assert w.project_folders_label.text() == "0"
            assert w.project_files_count_label.text() == "0"
            assert w.project_stats_label.text() == "1"  # 项目列表项保留
        finally:
            w.deleteLater()

    def test_stats_not_zero_when_empty(self, qapp):
        """初始状态 stats 为 0."""
        from ui.project_tab import ProjectManagerWidget
        w = ProjectManagerWidget()
        try:
            assert w.project_stats_label.text() == "0"
            assert w.project_folders_label.text() == "0"
            assert w.project_files_count_label.text() == "0"
        finally:
            w.deleteLater()

    def test_set_stats_method(self, qapp):
        """set_stats 直接调用正确更新."""
        from ui.project_tab import ProjectManagerWidget
        w = ProjectManagerWidget()
        try:
            w.set_stats(5, 12, 34)
            assert w.project_stats_label.text() == "5"
            assert w.project_folders_label.text() == "12"
            assert w.project_files_count_label.text() == "34"
        finally:
            w.deleteLater()
