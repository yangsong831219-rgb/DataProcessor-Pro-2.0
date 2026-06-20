"""ExternalDataProvider + 外部数据源分析线路 测试.

验证:
1. CSV/XLSX → 形状/列/统计/样例摘要正确
2. TXT/JSON/MD → 文本读取、截断
3. 坏文件/不支持类型 → 跳过不崩
4. 接入: 仅外部源(内部全空) → context 可构建
5. 按钮行为测试
"""

import os
import json
import tempfile
import pytest


# ── Fixtures ──


@pytest.fixture
def csv_file(tmp_path):
    """创建测试 CSV 文件."""
    f = tmp_path / "test.csv"
    f.write_text("col1,col2,col3\n1.0,2.0,3.0\n4.0,5.0,6.0\n7.0,8.0,9.0\n")
    return str(f)


@pytest.fixture
def xlsx_file(tmp_path):
    """创建测试 Excel 文件."""
    try:
        import openpyxl  # noqa
    except ImportError:
        pytest.skip("openpyxl 未安装")
    import pandas as pd
    f = tmp_path / "test.xlsx"
    df = pd.DataFrame({"A": [1, 2, 3], "B": [4.0, 5.0, 6.0]})
    df.to_excel(str(f), index=False)
    return str(f)


@pytest.fixture
def txt_file(tmp_path):
    """创建测试 TXT 文件."""
    f = tmp_path / "notes.txt"
    f.write_text("需求说明\n1. 分析传感器\n2. 检查异常\n")
    return str(f)


@pytest.fixture
def json_file(tmp_path):
    """创建测试 JSON 文件."""
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"sensor": "A1", "threshold": 0.5}))
    return str(f)


@pytest.fixture
def bad_file(tmp_path):
    """创建一个无法正常读取的损坏 CSV."""
    import struct
    f = tmp_path / "bad.bin"
    f.write_bytes(b'\x00\x01\x02' * 50)
    return str(f)


# ── Fixtures ──


@pytest.fixture(scope="session")
def ai_diag_widget(qapp):
    """构造 AiDiagnosisWidget 实例."""
    from ui.ai_diagnosis import AiDiagnosisWidget
    widget = AiDiagnosisWidget()
    yield widget
    try:
        widget.deleteLater()
    except Exception:
        pass


# ── Mock main window ──


class _MockMainWin:
    """模拟主窗口，仅暴露必要属性."""
    def __init__(self, external_files=None):
        self._external_files = external_files or []
        self.ai_diagnosis_widget = self


# ── Tests ──


class TestExternalDataProvider:
    """ExternalDataProvider 各文件类型处理."""

    def test_not_available_when_empty(self):
        from core.data_providers import ExternalDataProvider
        p = ExternalDataProvider()
        mw = _MockMainWin([])
        assert not p.is_available(mw)

    def test_available_when_files_loaded(self, csv_file):
        from core.data_providers import ExternalDataProvider
        p = ExternalDataProvider()
        mw = _MockMainWin([csv_file])
        assert p.is_available(mw)

    def test_csv_summary_has_shape_and_stats(self, csv_file):
        from core.data_providers import ExternalDataProvider
        p = ExternalDataProvider()
        mw = _MockMainWin([csv_file])
        summary = p.get_summary(mw, budget_chars=3000)
        assert "外部数据文件" in summary
        # CSV 3行3列
        assert "3 行" in summary
        assert "3 列" in summary or "col1" in summary
        # 统计
        assert "mean=" in summary or "mean" in summary
        # 前5行样例
        assert "1.0" in summary

    def test_xlsx_summary_has_shape(self, xlsx_file):
        from core.data_providers import ExternalDataProvider
        p = ExternalDataProvider()
        mw = _MockMainWin([xlsx_file])
        summary = p.get_summary(mw, budget_chars=3000)
        assert "外部数据文件" in summary
        assert "3" in summary  # rows or cols

    def test_txt_summary_has_content(self, txt_file):
        from core.data_providers import ExternalDataProvider
        p = ExternalDataProvider()
        mw = _MockMainWin([txt_file])
        summary = p.get_summary(mw, budget_chars=3000)
        assert "需求说明" in summary
        assert "文本" in summary or ".txt" in summary

    def test_json_summary(self, json_file):
        from core.data_providers import ExternalDataProvider
        p = ExternalDataProvider()
        mw = _MockMainWin([json_file])
        summary = p.get_summary(mw, budget_chars=3000)
        assert "sensor" in summary

    def test_bad_file_does_not_crash(self, bad_file):
        from core.data_providers import ExternalDataProvider
        p = ExternalDataProvider()
        mw = _MockMainWin([bad_file])
        summary = p.get_summary(mw, budget_chars=3000)
        # 不崩溃，且标注问题
        assert len(summary) > 0
        assert "外部数据文件" in summary

    def test_budget_truncation(self, csv_file):
        from core.data_providers import ExternalDataProvider
        p = ExternalDataProvider()
        mw = _MockMainWin([csv_file])
        # 极小 budget → 截断
        summary = p.get_summary(mw, budget_chars=150)
        assert len(summary) <= 200
        assert "截断" in summary or len(summary) < 200

    def test_multiple_files(self, csv_file, txt_file):
        from core.data_providers import ExternalDataProvider
        p = ExternalDataProvider()
        mw = _MockMainWin([csv_file, txt_file])
        summary = p.get_summary(mw, budget_chars=5000)
        # 两个文件都有
        assert "test.csv" in summary
        assert "notes.txt" in summary or "txt" in summary


class TestExternalDataAiDiagnosisIntegration:
    """外部源接入 AI 诊断流程."""

    def test_external_files_state_initialized(self, ai_diag_widget):
        """widget 初始化时 _external_files 为空列表."""
        assert hasattr(ai_diag_widget, '_external_files')
        assert ai_diag_widget._external_files == []

    def test_clear_files_resets_state(self, ai_diag_widget, csv_file):
        """清除后 _external_files 为空."""
        ai_diag_widget._external_files = [csv_file]
        ai_diag_widget._on_clear_external_files()
        assert ai_diag_widget._external_files == []

    def test_external_files_ui_label_updated(self, ai_diag_widget, csv_file, txt_file):
        """加载和清除后 UI 标签更新."""
        ai_diag_widget._external_files = [csv_file]
        ai_diag_widget._refresh_external_files_ui()
        assert "test.csv" in ai_diag_widget._external_files_label.text()

        ai_diag_widget._external_files = [csv_file, txt_file]
        ai_diag_widget._refresh_external_files_ui()
        assert "test.csv" in ai_diag_widget._external_files_label.text()
        assert "notes.txt" in ai_diag_widget._external_files_label.text()

        ai_diag_widget._external_files = []
        ai_diag_widget._refresh_external_files_ui()
        assert "未加载" in ai_diag_widget._external_files_label.text()


class TestProviderRegistered:
    """ExternalDataProvider 已注册到 get_all_providers."""

    def test_external_provider_in_registry(self):
        from core.data_providers import get_all_providers, ExternalDataProvider
        providers = get_all_providers()
        names = [p.display_name for p in providers]
        assert "外部数据文件" in names

    def test_external_provider_last_in_order(self):
        """外部数据源在列表最后."""
        from core.data_providers import get_all_providers, ExternalDataProvider
        providers = get_all_providers()
        assert isinstance(providers[-1], ExternalDataProvider)
