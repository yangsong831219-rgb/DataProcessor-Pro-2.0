"""report_workbench 未加载诊断硬拦 — 入口守卫 smoke test。

覆盖:
- _on_outline_requested: _diagnosis_record=None → 不发射 outline_requested 信号
- _on_outline_requested: _diagnosis_record 非 None → 正常发射
- _on_full_report_requested: _diagnosis_record=None → 不发射 full_report_requested 信号
- _on_full_report_requested: _diagnosis_record 非 None → 正常发射
- set_outline_text: 无诊断 → full_report_btn 保持 disabled
- set_outline_text: 有诊断 → full_report_btn unlocked
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QApplication

from ui.report_workbench import ReportWorkbenchWidget

_qapp = None


@pytest.fixture(scope="module")
def qapp():
    global _qapp
    if _qapp is None:
        _qapp = QApplication.instance() or QApplication(sys.argv)
    return _qapp


@pytest.fixture(autouse=True)
def _mock_qmessagebox():
    """全局 mock QMessageBox 防止测试阻塞"""
    from unittest import mock as _umock
    with _umock.patch("PyQt6.QtWidgets.QMessageBox.warning"):
        with _umock.patch("PyQt6.QtWidgets.QMessageBox.information"):
            with _umock.patch("PyQt6.QtWidgets.QMessageBox.critical"):
                yield


@pytest.fixture
def widget(qapp):
    """未加载诊断的干净 widget (预设 project_files 非空, 通过关联资料守卫)"""
    from PyQt6.QtWidgets import QListWidgetItem
    w = ReportWorkbenchWidget()
    w._req_file_path = ""
    # ★ 新守卫只判 project_files — 需加一项才能放行
    w._proj_file_list.addItem(QListWidgetItem("/fake/project/数据/data.csv"))
    # 替换 signal emit 为 mock 以便断言
    w.outline_requested = MagicMock()
    w.full_report_requested = MagicMock()
    return w


class TestGuardNoDiagnosis:
    """_diagnosis_record=None 时，两个入口均中止。"""

    def test_outline_btn_blocked_when_no_diagnosis(self, widget):
        widget._diagnosis_record = None
        widget._on_outline_requested()
        widget.outline_requested.emit.assert_not_called()

    def test_full_report_btn_blocked_when_no_diagnosis(self, widget):
        widget._diagnosis_record = None
        widget._on_full_report_requested()
        widget.full_report_requested.emit.assert_not_called()

    def test_full_report_btn_stays_disabled_after_set_outline_text(self, widget):
        widget._diagnosis_record = None
        widget.set_outline_text("# 测试大纲\n## 传感器分析")
        assert not widget.full_report_btn.isEnabled(), (
            "无诊断时 full_report_btn 应保持 disabled")


class TestGuardWithDiagnosis:
    """_diagnosis_record 非 None 时，两个入口正常放行。"""

    def test_outline_btn_allowed_with_diagnosis(self, widget):
        widget._req_file_path = "/fake/project/report.docx"  # bypass project-files guard
        widget._diagnosis_record = {"schema_version": "1.2", "kb_hits": []}
        widget._on_outline_requested()
        widget.outline_requested.emit.assert_called_once()

    def test_full_report_btn_allowed_with_diagnosis(self, widget):
        widget._req_file_path = "/fake/project/report.docx"
        widget._diagnosis_record = {"schema_version": "1.2", "kb_hits": []}
        widget._on_full_report_requested()
        widget.full_report_requested.emit.assert_called_once()

    def test_full_report_btn_unlocked_after_set_outline_text_with_diagnosis(self, widget):
        widget._diagnosis_record = {"schema_version": "1.2", "kb_hits": []}
        widget.set_outline_text("# 测试大纲\n## 传感器分析")
        assert widget.full_report_btn.isEnabled(), (
            "有诊断时 full_report_btn 应被 unlock")


class TestEdgeCases:
    """边界用例。"""

    def test_diagnosis_is_empty_dict_still_valid(self, widget):
        """空 dict 也是已加载（虽然数据少），应放行。"""
        widget._req_file_path = "/fake/project/report.docx"
        widget._diagnosis_record = {}
        widget._on_outline_requested()
        widget.outline_requested.emit.assert_called_once()

    def test_none_to_loaded_transition(self, widget):
        """先无诊断→被拦，再加载→放行。"""
        widget._req_file_path = "/fake/project/report.docx"
        widget._diagnosis_record = None
        widget._on_outline_requested()
        widget.outline_requested.emit.assert_not_called()

        widget._diagnosis_record = {"kb_hits": [{"id": "R1"}]}
        widget._on_outline_requested()
        widget.outline_requested.emit.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: 关联资料为空 → 三入口硬拦
# ═══════════════════════════════════════════════════════════════════════


class TestGuardNoProjectFiles:
    """关联资料为空时，三个入口中止 + 弹 QMessageBox。"""

    @pytest.fixture
    def w(self, qapp):
        w = ReportWorkbenchWidget()
        w.outline_requested = MagicMock()
        w.full_report_requested = MagicMock()
        w.load_diagnosis_requested = MagicMock()
        w._diagnosis_record = {"schema_version": "1.2", "kb_hits": []}
        w._req_file_path = ""
        return w

    def test_outline_blocked_when_no_project_files(self, w):
        """关联资料为空 → _on_outline_requested 中止"""
        from unittest import mock as _umock
        with _umock.patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn:
            w._on_outline_requested()
        mock_warn.assert_called_once()
        assert "项目关联资料" in str(mock_warn.call_args[0])
        w.outline_requested.emit.assert_not_called()

    def test_full_report_blocked_when_no_project_files(self, w):
        """关联资料为空 → _on_full_report_requested 中止"""
        from unittest import mock as _umock
        with _umock.patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn:
            w._on_full_report_requested()
        mock_warn.assert_called_once()
        assert "项目关联资料" in str(mock_warn.call_args[0])
        w.full_report_requested.emit.assert_not_called()

    def test_load_diagnosis_blocked_when_no_project_files(self, w):
        """关联资料为空 → _on_load_diagnosis 中止"""
        from unittest import mock as _umock
        with _umock.patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn:
            w._on_load_diagnosis()
        mock_warn.assert_called_once()
        assert "项目关联资料" in str(mock_warn.call_args[0])
        w.load_diagnosis_requested.emit.assert_not_called()

    def test_with_project_files_allowed(self, w):
        """有 project_files → _on_outline_requested 放行"""
        from PyQt6.QtWidgets import QListWidgetItem
        w._proj_file_list.addItem(QListWidgetItem("/some/project/数据/data.csv"))
        w._on_outline_requested()
        w.outline_requested.emit.assert_called_once()

    def test_with_project_list_files_allowed(self, w):
        """有 project_files → _on_load_diagnosis 放行"""
        from unittest import mock as _umock
        from PyQt6.QtWidgets import QListWidgetItem
        w._proj_file_list.addItem(QListWidgetItem("/some/project/数据/data.csv"))
        with _umock.patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn:
            w._on_load_diagnosis()
        mock_warn.assert_not_called()
        w.load_diagnosis_requested.emit.assert_called_once()

    def test_zombie_req_file_does_not_bypass_guard(self, w):
        """_req_file_path 有僵尸值、project_files 空 → 仍拦 (三入口)"""
        from unittest import mock as _umock
        w._req_file_path = "/old/project/报告/report.docx"  # 僵尸值

        # 加载入口
        with _umock.patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn:
            w._on_load_diagnosis()
        mock_warn.assert_called_once()
        assert "项目关联资料" in str(mock_warn.call_args[0])
        w.load_diagnosis_requested.emit.assert_not_called()

        # 大纲入口
        w.outline_requested.reset_mock()
        with _umock.patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn2:
            w._on_outline_requested()
        mock_warn2.assert_called_once()
        assert "项目关联资料" in str(mock_warn2.call_args[0])
        w.outline_requested.emit.assert_not_called()

        # 完整报告入口
        w.full_report_requested.reset_mock()
        with _umock.patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn3:
            w._on_full_report_requested()
        mock_warn3.assert_called_once()
        assert "项目关联资料" in str(mock_warn3.call_args[0])
        w.full_report_requested.emit.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# Phase 3: 删除/清除功能 — 需求文件/模板文件/关联资料
# ═══════════════════════════════════════════════════════════════════════


class TestRemoveProjectFiles:
    """关联资料列表 — 选中删除 + get_config 同步"""

    def test_remove_selected_items_leaves_remaining(self, qapp):
        """加3项 → 选中2项移除 → 剩1项, get_config 同步"""
        w = ReportWorkbenchWidget()
        from PyQt6.QtWidgets import QListWidgetItem
        w._proj_file_list.addItem(QListWidgetItem("/proj/data/file1.csv"))
        w._proj_file_list.addItem(QListWidgetItem("/proj/data/file2.csv"))
        w._proj_file_list.addItem(QListWidgetItem("/proj/data/file3.csv"))

        w._proj_file_list.item(0).setSelected(True)
        w._proj_file_list.item(1).setSelected(True)
        w._on_remove_project_files()

        assert w._proj_file_list.count() == 1
        assert w._proj_file_list.item(0).text() == "/proj/data/file3.csv"
        paths = w._get_project_file_paths()
        assert len(paths) == 1
        assert paths[0] == "/proj/data/file3.csv"

    def test_remove_non_contiguous_selection(self, qapp):
        """选中不连续项(第1和第3) → 移除 → 剩余项正确无索引错位"""
        w = ReportWorkbenchWidget()
        from PyQt6.QtWidgets import QListWidgetItem
        w._proj_file_list.addItem(QListWidgetItem("A"))
        w._proj_file_list.addItem(QListWidgetItem("B"))
        w._proj_file_list.addItem(QListWidgetItem("C"))
        w._proj_file_list.addItem(QListWidgetItem("D"))

        w._proj_file_list.item(0).setSelected(True)  # A
        w._proj_file_list.item(2).setSelected(True)  # C
        w._on_remove_project_files()

        assert w._proj_file_list.count() == 2
        assert w._proj_file_list.item(0).text() == "B"
        assert w._proj_file_list.item(1).text() == "D"

    def test_remove_when_none_selected_no_crash(self, qapp):
        """无选中 → 不操作，不崩"""
        w = ReportWorkbenchWidget()
        from PyQt6.QtWidgets import QListWidgetItem
        w._proj_file_list.addItem(QListWidgetItem("A"))
        w._on_remove_project_files()
        assert w._proj_file_list.count() == 1

    def test_remove_all_leaves_empty_triggers_guard(self, qapp):
        """全移除 → 列表空 → get_config project_files 空 → 门禁联动"""
        w = ReportWorkbenchWidget()
        from PyQt6.QtWidgets import QListWidgetItem
        w._proj_file_list.addItem(QListWidgetItem("A"))
        w._proj_file_list.item(0).setSelected(True)
        w._on_remove_project_files()

        assert w._proj_file_list.count() == 0
        assert w._get_project_file_paths() == []
        from unittest import mock as _umock
        with _umock.patch("PyQt6.QtWidgets.QMessageBox.warning") as mw:
            w._on_load_diagnosis()
        mw.assert_called_once()


class TestClearReqFile:
    """清除需求文件 → _req_file_path 清空 + get_config 同步"""

    def test_clear_req_file(self, qapp):
        w = ReportWorkbenchWidget()
        w._req_file_path = "/some/req/file.txt"
        w._req_file_label.setText("需求文件: /some/req/file.txt")
        w._on_clear_req_file()
        assert w._req_file_path == ''
        assert w._req_file_label.text() == '需求文件: 未选择'
        assert w.get_config()['req_file'] == ''


class TestClearTemplateFile:
    """清除模板文件 → _template_file_path 清空 + get_config 同步"""

    def test_clear_template_file(self, qapp):
        w = ReportWorkbenchWidget()
        w._template_file_path = "/some/tmpl.docx"
        w._tmpl_file_label.setText("模板: /some/tmpl.docx")
        w._on_clear_template_file()
        assert w._template_file_path == ''
        assert w._tmpl_file_label.text() == '模板文件: 未选择'
        assert w.get_config()['template_file'] == ''
