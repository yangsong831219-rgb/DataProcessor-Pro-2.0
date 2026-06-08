"""pytest 配置 — 共享 fixtures 与路径设置"""

import sys
import os
from pathlib import Path

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
