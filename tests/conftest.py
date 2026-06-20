"""pytest 配置 — 共享 fixtures 与路径设置"""

import sys
import os
from pathlib import Path

import pytest

# 确保项目根目录在 sys.path 中（用于测试文件中的绝对导入）
_project_root = Path(__file__).parent.parent
_str_root = str(_project_root)
if _str_root not in sys.path:
    sys.path.insert(0, _str_root)


def pytest_configure(config):
    """pytest 启动配置"""
    config.addinivalue_line(
        "markers",
        "golden: marks tests that validate against golden benchmark data",
    )


# ═══════════════════════════════════════════════════════════════════
# PyQt6 共享 fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication — 整个测试会话共享，避免重复创建。

    适用于 ReportWorker error-chain 等需要 Qt 事件循环但不需要
    独立窗口的测试。"""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app
    # session 级不销毁，让 Qt 自然退出
