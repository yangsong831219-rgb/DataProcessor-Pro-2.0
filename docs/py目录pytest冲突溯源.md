# py/ 目录撞 PyPI py 包 — pytest 冲突溯源

**日期**: 2026-06-25  
**分支**: llama-cpp  
**状态**: 只调研，不改。两方案可行性评估如下。

---

## 1. 冲突的确切机制

### 完整报错

```
Error: AttributeError: module 'py' has no attribute 'path'

Traceback:
  File "pytest\__init__.py", line 8, in <module>
    from _pytest._code import ExceptionInfo
  File "_pytest\_code\__init__.py", line 5, in <module>
    from .code import Code
  File "_pytest\_code\code.py", line 41, in <module>
    from _pytest._io import TerminalWriter
  File "_pytest\_io\__init__.py", line 3, in <module>
    from .terminalwriter import get_terminal_width
  File "_pytest\_io\terminalwriter.py", line 19, in <module>
    from ..compat import assert_never
  File "_pytest\compat.py", line 32, in <module>
    LEGACY_PATH = py.path. local      # ← pytest 内部代码
                  ^^^^^^^
AttributeError: module 'py' has no attribute 'path'
```

### 冲突机制

**触发链**：

```
pytest.ini:5  pythonpath = .
  → 项目根目录 D:\...\软件项目_qt6 注入 sys.path[0]

pytest 启动 → _pytest.compat.py:32
  → import py      # 期望加载 PyPI 的 py 包（含 py.path.local 子模块）
  → 实际加载: D:\...\软件项目_qt6\py\__init__.py   ← 空文件，无 .path 子模块
  → AttributeError
```

**验证** — 在项目根目录下 `import py`：

```
import sys; sys.path.insert(0, '.')
import py
print(py.__file__)           # → ...软件项目_qt6\py\__init__.py  ← 项目 py/
print(hasattr(py, 'path'))   # → False
```

> **不是** pytest 找到项目 py/ 后收集不成功；**pytest 根本启动不了**——它在 `import pytest` 阶段就崩溃了。

### 为什么 `run_tests.py` 能绕过

`run_tests.py:2-37` 实施了一个临时救火方案：

```python
# Step 1: 暂移除项目根路径
while PROJECT_ROOT in sys.path:
    idx = sys.path.index(PROJECT_ROOT)
    paths_to_restore.append(sys.path.pop(idx))

# Step 2: 导入 pytest（使用系统的 py 包）
import pytest

# Step 3: 恢复项目根路径
for p in reversed(paths_to_restore):
    sys.path.insert(0, p)

# Step 4: 替换 sys.modules['py'] 为项目 py/
del sys.modules['py']
```

### 为什么 `python -m pytest` 直接调用仍崩

`tests/conftest.py:10-13` 重新把项目根注入 `sys.path[0]`：

```python
_project_root = Path(__file__).parent.parent
_str_root = str(_project_root)
if _str_root not in sys.path:
    sys.path.insert(0, _str_root)
```

即使绕过启动时冲突，conftest 被加载后项目根重回 `sys.path[0]`，任何后续对 `py` 的 import（如其他测试文件中的 `from py.calibration...`）都会遮蔽 PyPI 的 py。

**但这只会影响项目自有测试中的 `from py.X` import（此处是预期行为）——真正的问题只是 pytest 自身在启动时需要 PyPI 的 `py` 包。** `run_tests.py` 的救火脚本证实了这个冲突确由 `sys.path` 顺序引发。

---

## 2. import 现状盘点（方案A 爆炸半径）

### `import py` / `from py.` 出现点统计

**总行数**: 130 处  
**文件数**: ~35 个文件（不含 venv）

**按目录分布**：

| 目录 | 文件数 | 行数 (约) |
|------|--------|----------|
| `py/` 内部（自引用） | 5 | 5 |
| `ui/` | 6 | ~30 |
| `tests/` | ~20 | ~80 |
| `core/` | 2 | 2 |
| `main.py` | 1 | 7 |
| `test_clipper.py` (根目录) | 1 | 1 |

**引用模式**（全部是子模块导入，无裸 `import py`）：

```python
from py.calibration.temperature_calibration import ...  # 最常见
from py.calibration.strain_calibration import ...
from py.calibration.project_config import ...
from py.wiki_system import WikiFileSystem
from py.multi_agent import ...
from py.report_builder.ppt_builder import PPTBuilder
from py.report_builder.models import ...
from py.analyzer import ...
from py.formula import ...
from py.web_clipper import ...
```

> **精确匹配模式**: `from py\.`（总是 `from py.SUBMODULE import X`）。如果改目录名为 `engine`，这些全部需要写为 `from engine.SUBMODULE import X`。

### 字符串引用 `py/...`（改名易漏）

| 引用类型 | 位置 | 内容 |
|---------|------|------|
| CLAUDE.md | lines 73-74,101,110,125,127,136 | 文档中的 `py/calibration/` `py/formula.py` 等路径引用（约 8 处） |
| docstring | `tests/test_readings_profile.py:1` | `"""py/calibration/readings_profile.py 合成数据测试"""` |
| docstring | `py/calibration/project_config.py:3` | `取代旧 CalibrationProfile (py/calibration/profile.py)` |
| pyproject.toml | line 2 | `include = ["py", "ui", "utils", "core", "tests"]` |
| pytest.ini | line 5 | `pythonpath = .`（间接引用——`pythonpath` 把根目录送上 sys.path，`py/` 被子目录发现机制 shadow） |
| .gitignore | 无 `py/` 专属项 | 仅 `*.pyc` 等通配 |

### 无引用区域

以下位置**不含** `py/` 特定引用：
- `.gitignore` — 无 `py/` 条目
- `setup.cfg` — 不存在
- `setup.py` — 不存在
- IDE 配置 (`.vscode/` `.idea/`) — 不存在
- CI 配置 (`.github/`) — 不存在
- 打包配置 (`MANIFEST.in`) — 不存在

---

## 3. 现有 pytest 配置

### `pytest.ini` — 项目根目录

```ini
[pytest]
testpaths = tests
python_files = test_*.py
norecursedirs = .git .claude __pycache__ wiki_vault output_reports project_files *.egg-info
pythonpath = .                          # ← ★ 根因: 把项目根送上 sys.path
addopts = --strict-markers -v
```

### `pyproject.toml` — `[tool.pyright]` 段

```toml
[tool.pyright]
include = ["py", "ui", "utils", "core", "tests"]   # ← references py/
exclude = ["venv", ".venv", "**/__pycache__", "tests/golden"]
```

无 `[tool.pytest]` 段。

### `tests/conftest.py`

```python
# line 10-13 (冲突加剧)
_project_root = Path(__file__).parent.parent
if _str_root not in sys.path:
    sys.path.insert(0, _str_root)       # ← re-inject project root at 0
```

---

## 4. 方案B 可行性 — 配置绕过

### B1. pytest.ini `pythonpath` 去掉 `.`

**做法**: 将 `pythonpath = .` 改为空或移除该行。

**效果**: pytest 启动时项目根不在 sys.path → PyPI `py` 包正常加载。

**后果**: 所有测试中的 `from py.calibration...` import 失败 — 因为 `py/` 目录不在 sys.path 上，`py` 解析为 PyPI 的 py 包（没有 `.calibration` 子模块）。

**可行吗**: ❌ 需要项目 py/ 变成可见的同时不被 pytest 内部 import py 遮蔽 — 两者互相矛盾。不能单靠 `pythonpath` 配置解决。

### B2. conftest.py sys.path 操作

**做法**: 在 `tests/conftest.py` 的 `pytest_configure()` 中操作 sys.path：

```python
def pytest_configure(config):
    # 1. 将 py/ 的子模块路径注入 sys.modules
    # 2. 不碰 sys.path[0] 的根目录
```

**可行吗**: ⚠️ 部分可行但脆弱 — 
- conftest.py 本身被 pytest 加载时，pytest 已经启动完成（PyPI py 已用过），此时操作 sys.path 安全
- 但每个测试文件 import `from py.X` 时，Python import 系统会优先找 sys.path 中的 `py/` 目录 → 若根目录在 sys.path 上 → 再次遮蔽 PyPI py → 后续若有库延迟 import py → 崩
- `run_tests.py` 绕过的本质是 "启动前移除、启动后恢复"，conftest 无法在启动前执行

**可行吗**: ❌ conftest 在 pytest 启动**后**才加载，无法阻止启动时的冲突

### B3. `py/__init__.py` 改名或删掉

**做法**: 删除或重命名 `py/__init__.py`，让 `py/` 不再是 Python package。

**效果**: `import py` 不再解析为项目 py/ → PyPI py 包正常 → pytest 启动正常。`from py.calibration import X` 也不再工作（py/ 不是 package 了）→ 全部 130 处 import 崩。

**可行吗**: ❌ 等同于方案A（改目录名）的需求，但没有改目录名干净

### B4. pytest `norecursedirs`

**做法**: 把 `py` 加入 `norecursedirs`：

**效果**: 只控制 test discovery 的递归扫描范围——不影响 sys.path / import 解析。pytest 内部代码 `import py` 走 sys.path → 根目录在 sys.path → py/ 先被找到。`norecursedirs` 不治这个。

**可行吗**: ❌ 不解决 sys.path 遮蔽

### B5. PYTHONDONTWRITEBYTECODE 式环境变量

python 无内置机制让"同名字的本地目录优先于 site-packages 的某些库、但劣后于另一些"。sys.path 是一维列表。

**可行吗**: ❌ 不存在这种粒度

### 方案B 结论

**无纯配置绕过方案。** 冲突本质是 Python 的 import 机制决定的 — `sys.path` 是一维有序列表，同名 `py/` 目录只要在 sys.path 上，就会优先于 site-packages 的 `py` 包被解析。pytest 内部需要 PyPI py 包启动、项目自己的 130 处 import 需要项目 py/ — 两者同名冲突，一维 sys.path 无解。

---

## 5. 方案A 可行性 — 改目录名

### 改动清单

**Python import** — 130 行 × ~35 文件（见第 2 节清单）— `from py.X` → `from engine.X`（或新名），全部模式化批量替换。

**字符串引用**（6 处）：

| 文件 | 行 | 内容 |
|------|----|------|
| `CLAUDE.md` | 73-74,101,110,125,127,136 | 8 处 `py/` 路径引用 |
| `tests/test_readings_profile.py` | 1 | docstring `py/calibration/...` |
| `py/calibration/project_config.py` | 3 | docstring `py/calibration/...` |
| `pyproject.toml` | 2 | `include = ["py", ...]` |

**配置文件**：

| 文件 | 改动 |
|------|------|
| `pyproject.toml` | `include` 数组改 `"py"` → `"engine"` |
| `pytest.ini` | `pythonpath = .` 保留（不动 — 仍在项目根启动即可） |
| `run_tests.py` | 检查 `sys.modules['py']` 替换逻辑是否需调整（新目录名不再叫 py → 冲突消失 → 可删除整个救火脚本） |

**无改动区**：

| 项 | 状态 |
|----|------|
| `.gitignore` | 无需改动（无 `py/` 专属项） |
| 打包配置 (`setup.py`/`pyproject.toml` [build]段) | 不存在，无需改 |
| IDE 配置 | 不存在，无需改 |
| CI 配置 | 不存在，无需改 |

### 改动量评估

| 维度 | 量 |
|------|----|
| Python import 行 | **130 行 × 35 文件** |
| 文档 Markdown 路径引用 | ~8 处 |
| 配置文件 | 2 处 (`pyproject.toml` + `run_tests.py` ) |
| 回归风险 | 高 — 几乎所有测试和 UI 文件都有 `from py.` import |
| 副作用 | `run_tests.py` 救火脚本可删除（正面）；`docs/` 名考古路径引用需全量更新 |
| 回滚复杂度 | 低 — `git mv py/ engine/` + 批量 `sed`，`git revert` 即可 |

### 推荐新名

`engine/` — 短、与内容匹配（公式引擎、标定引擎、报告引擎、多智能体引擎），与现用 `core/`（AI客户端、数据providers）对等。

### 实施成本

- `git mv py/ engine/` — 保留 git 历史
- 一次批量 sed：`s/from py\./from engine./g; s/import py$/import engine/g` — 130 行
- `pyproject.toml` 改 1 行
- CLAUDE.md 改 ~8 行
- `run_tests.py` 删 ~20 行救火脚本
- 全量 pyright + pytest + 真机回归

**估计改动量**: 约 170 行变更，零逻辑变更（纯重命名）。

### 风险

低 — `py/` 不是公开 API、没有外部消费者、没有打包发布。`git mv` 保历史。唯一风险是遗漏某处 `"py/"` 字符串引用（非 import），如 docstring / 注释 / 文档。

---

## 结论

| 方案 | 可行性 | 改动量 | 推荐 |
|------|--------|--------|------|
| A: `py/` → `engine/` | ✅ | 170 行，零逻辑变更 | **唯一根治方案** |
| B1-B5: 配置绕过 | ❌ | 无法绕过 Python import 机制 | —

**冲突本质**：Python `sys.path` 是一维有序列表，同名目录在同一列表上必然遮蔽。pytest 需要 PyPI 的 `py` 包启动、项目 130 处 import 需要项目 `py/` → 无配置方案可正面解决。改目录名是唯一出路。
