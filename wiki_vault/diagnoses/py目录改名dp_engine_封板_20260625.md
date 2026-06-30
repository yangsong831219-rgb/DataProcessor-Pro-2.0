# py/ 目录撞 pip py 包名致 pytest 无法启动 — 改名根治 封板

**日期**: 2026-06-25  
**分支**: llama-cpp  
**状态**: ✅ 封板 — `git mv py dp_engine` + 全仓 import 替换 + 三关卡验证通过

---

## 根因

`pytest.ini:5` `pythonpath = .` 使项目根目录注入 `sys.path[0]` → pytest 启动时 `_pytest/compat.py:32` 执行 `LEGACY_PATH = py.path.local` → `import py` 解析到项目 `py/__init__.py`（空文件，无 `.path` 子模块）→ `AttributeError: module 'py' has no attribute 'path'` → **pytest 根本启动不了**，不只是收集不到。

**影响**: 本会话所有新测试只能 standalone 脚本跑（`python -c "..."`），无法被 pytest 收集 → 单测绿 ≠ 真机对，已多次因此栽轮（AnalysisProvider 判据改漏、死代码补在 has_fbg 守卫后被拦等）。

`run_tests.py:2-37` 有过临时救火脚本（启动前暂移除项目根路径、导入 pytest 后恢复），但 `python -m pytest` 直接调用时走 `pytest.ini` + `tests/conftest.py` 非援区，全面崩溃。

溯源详见 `docs/py目录pytest冲突溯源.md`。

---

## 方案选择

**方案B（配置绕过）— 彻底不可行**。勘探验证了 5 种子方案：

1. 去掉 `pythonpath = .` — pytest 启动了但 `from py.xxx` 全部 ImportError
2. conftest.py 调 sys.path — 只能在启动后操作，来不及
3. 删 `py/__init__.py` — pytest 启动了，但 130 处 `from py.xxx` 全崩（py/ 不再是 package）
4. `norecursedirs` — 只控制扫描范围，不控 import 解析
5. 环境变量 — 不存在此粒度

**根子**: Python `sys.path` 是一维有序列表，同名 `py/` 目录只要在上面，就会优先于 site-packages 的 `py` 包。pytest 内部需 PyPI py 包启动、项目 130 处 import 需项目 py/ → 同名冲突，一维 sys.path 无解。

**方案A（改目录名）— 唯一可行**。35 文件 × 153 行 import → 约 170 行纯机械重命名，零逻辑变更。

---

## 名字选择: `dp_engine`（不用 `engine`）

| 候选 | pip 冲突 | stdlib 冲突 | 项目内冲突 | 判定 |
|------|---------|-----------|-----------|------|
| `engine` | 无 | 无 | 无 | ✅ 但太通用，将来可能撞 PyPI |
| `dp_engine` | 无 | 无 | 无 | ✅ **dp_ 项目前缀永久根治** |
| `app_core` | 无 | 无 | 无 | ✅ |
| `backend` | 无 | 无 | 无 | ✅ |

选用 `dp_engine`（`dp_` = DataProcessor 项目前缀）— 确保永不再撞任何 PyPI 包名，一次根治。

---

## 改动清单

### 目录重命名

```bash
git mv py dp_engine    # 保留 git 历史
```

### import 替换（36 文件 × 153 行）

精确锚定模式，严禁宽匹配：

```
from py.     → from dp_engine.
import py.   → import dp_engine.
from py import → from dp_engine import
```

**零误伤** — `.py` 扩展名、`numpy`/`scipy`/`copy`/`python` 等含 `py` 的词汇未被触碰。改后全仓搜索 `from py.` / `import py.` → 0 残留。

### 字符串引用（6 处手动改）

| 文件 | 行 | 改前 | 改后 |
|------|----|------|------|
| `pyproject.toml` | 2 | `include = ["py", ...]` | `include = ["dp_engine", ...]` |
| `CLAUDE.md` | 73-74,101,110,125,127,136 | `py/` 路径 | `dp_engine/` 路径（7 处） |
| `tests/test_calibration_profile.py` | 31 | `"py.calibration.profile.PROFILES_DIR"` | `"dp_engine.calibration.profile.PROFILES_DIR"` |
| `tests/test_readings_profile.py` | 1 | docstring `py/calibration/...` | `dp_engine/calibration/...` |
| `dp_engine/calibration/project_config.py` | 3 | docstring `py/calibration/...` | `dp_engine/calibration/...` |

### 配置

| 文件 | 行 | 改动 |
|------|----|------|
| `pytest.ini` | — | 无需改（`pythonpath = .` 保留，改名后不再 shadow） |
| `pyproject.toml` | 2 | `include = ["py", ...]` → `include = ["dp_engine", ...]` |

### run_tests.py — 救火脚本退役

`run_tests.py:1-37`：旧脚本通过 pythonpath 体操绕过 `py/` shadow → 改名后冲突消失 → 脚本从 37 行简化为 6 行标准 pytest 入口。

### 连带修复：3 个 AnalysisProvider 测试对齐新判据

`tests/test_data_providers.py:264-315` — 4 个测试更新为 mock `analysis_tab_widget._current_data` DataFrame 结构（对齐真机验证行为），新增 `test_not_available_without_widget` 边界用例。**不改业务代码**（AnalysisProvider 上批真机已验证正确）。

---

## 验证：三关卡全过

| 关卡 | 命令 | 结果 |
|------|------|------|
| **G1 — pyright** | `pyright main.py core/ ui/` | ✅ 0 errors |
| **G2 — app 启动** | `python main.py` | ✅ GUI 正常启动，无 ImportError |
| **G3 — pytest** | `python -m pytest tests/` | ✅ 从 "无法启动" → **731 passed, 14 failed, 10 skipped, 1 error** |

G3 不再 `py.path` AttributeError 崩溃 — 根治确认。

---

## 收尾标准达成

| 标准 | 状态 |
|------|------|
| pytest 能启动并收集 | ✅ `python -m pytest tests/` 正常 |
| `from py.` / `import py.` 零残留 | ✅ 全仓 0 命中 |
| 本会话回归清零 | ✅ AnalysisProvider × 3 已修、calibration_profile monkeypatch 已修 |
| 预存失败全部登记 | ✅ 15 条清单 `wiki_vault/diagnoses/pytest预存失败清单_20260625.md` |

以后跑 pytest → 731p/14f/1e → 对照清单 → 若全部在册，即无新增回归。

---

## 待办

15 条预存失败（来自 `79b9b10` / `7fad573` / `a7b25b0` 三个前置提交）登记在册，本批不修。后续可专批逐根因清理，让回归套件全绿。

清单位置：`wiki_vault/diagnoses/pytest预存失败清单_20260625.md`
