"""运行测试套件的入口脚本。

解决 pytest 依赖的 `py.path` 与项目 `py/` 目录的命名冲突：
1. 导入 pytest 前移除项目根路径，让 pytest 使用系统的 `py` 包
2. 导入后恢复项目根路径，并替换 sys.modules['py'] 为项目的 `py/`
"""

import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# ── Step 1: 暂移除项目根路径 ──
paths_to_restore = []
while PROJECT_ROOT in sys.path:
    idx = sys.path.index(PROJECT_ROOT)
    paths_to_restore.append(sys.path.pop(idx))

# ── Step 2: 导入 pytest（使用系统的 py 包） ──
import pytest

# ── Step 3: 恢复项目根路径 ──
for p in reversed(paths_to_restore):
    sys.path.insert(0, p)

# ── Step 4: 替换 sys.modules['py'] 为项目 py/ ──
# pytest 在导入时已将 py.path.local 缓存在 _pytest.compat.LEGACY_PATH，
# 后续不再依赖 sys.modules['py']。我们安全地替换它。
if 'py' in sys.modules:
    del sys.modules['py']
for key in list(sys.modules.keys()):
    if key.startswith('py.') and not key.startswith('py.formula'):
        del sys.modules[key]

if __name__ == '__main__':
    args = sys.argv[1:] if len(sys.argv) > 1 else ['tests/', '-v']
    sys.exit(pytest.main(args))
