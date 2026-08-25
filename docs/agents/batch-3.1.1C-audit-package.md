# Batch 3.1.1C Audit Package — 统一回归、最终审计与封板候选

**Date**: 2026-07-22
**Branch**: llama-cpp
**Status**: ✅ 通过 — 3.1.1C-R 已完成 UI 禁用测试合同校正，回归全绿

---

## 1. 最小上下文读取声明

已读取并遵守 `CLAUDE.md`。
已读取当前权威文件 `docs/agents/batch-3.1.1B-audit-package.md`。
采用最小上下文读取原则，未读取无关历史 Markdown。
当前只执行 Batch 3.1.1C。

## 2. 唯一目标

| # | 目标 | 状态 |
|---|------|------|
| 1 | 核验当前 Runtime 测试的真实 collect 数 | ✅ 231 |
| 2 | 执行一次完整 Runtime 强制回归 | ✅ 231/231（3.1.1C-R 校正后） |
| 3 | 执行两个 installer symlink 安全哨兵 | ✅ 2/2 |
| 4 | 核验无 skipped、xfail、deselected 或规避参数 | ✅ 零规避 |
| 5 | 核验生产代码和测试范围未越界 | ✅ 未越界 |
| 6 | 创建最终封板审核包 | ✅ 本文档 |
| 7 | 提交外部审核 | ✅ |

## 3. 本批实际修改文件

本批为零修改批次（证据批次）。唯一新增文件：

```
docs/agents/batch-3.1.1C-audit-package.md  (NEW — 本审核包)
```

生产代码和测试文件**零修改**。Git `diff --name-only` 和 `status --short` 与本批开始前完全一致。

## 4. 前置基线（第三节）

```bash
git status --short → 33 modified (tracked) + Runtime files (untracked ??)
git diff --name-only → 33 files (same as 3.1.1A/B baseline)
git diff --stat → 33 files, +5998/-1039
```

Runtime 测试文件、fixtures、生产代码均为 `??`（untracked），符合 3.1.1A/B 状态。

## 5. T0 结果

### compileall

```bash
python -m compileall -f \
  dp_engine/skills/runtime_models.py \
  dp_engine/skills/runtime_protocol.py \
  dp_engine/skills/runtime_service.py \
  dp_engine/skills/runtime_worker.py \
  dp_engine/skills/runtime_errors.py \
  dp_engine/skills/runtime_permissions.py \
  dp_engine/skills/runtime_dependencies.py
```

**结果：All 7 files compiled successfully (0 errors)**

### pyright

```bash
pyright \
  dp_engine/skills/runtime_models.py \
  dp_engine/skills/runtime_protocol.py \
  dp_engine/skills/runtime_service.py \
  dp_engine/skills/runtime_worker.py \
  dp_engine/skills/runtime_errors.py \
  dp_engine/skills/runtime_permissions.py \
  dp_engine/skills/runtime_dependencies.py
```

**结果：0 errors, 0 warnings, 0 informations**

### T0 门槛判定

| 门槛 | 要求 | 实际 | 通过 |
|------|------|------|------|
| compileall | 0 errors | 0 errors | ✅ |
| pyright | 0 errors, 0 warnings | 0 errors, 0 warnings | ✅ |

## 6. T1 — 六个 Runtime 文件分项 Collect

### 分项

```bash
python -m pytest tests/test_runtime_l1_models.py --collect-only -q
# → 60 tests collected

python -m pytest tests/test_runtime_l2_subprocess.py --collect-only -q
# → 51 tests collected

python -m pytest tests/test_runtime_l3_security_boundary.py --collect-only -q
# → 40 tests collected

python -m pytest tests/test_runtime_l3_protocol_env.py --collect-only -q
# → 37 tests collected

python -m pytest tests/test_runtime_l3_deps_registry.py --collect-only -q
# → 29 tests collected

python -m pytest tests/test_runtime_ui_lifecycle.py --collect-only -q
# → 14 tests collected
```

### 分项汇总

| 文件 | Node 数 |
|------|--------|
| `test_runtime_l1_models.py` | 60 |
| `test_runtime_l2_subprocess.py` | 51 |
| `test_runtime_l3_security_boundary.py` | 40 |
| `test_runtime_l3_protocol_env.py` | 37 |
| `test_runtime_l3_deps_registry.py` | 29 |
| `test_runtime_ui_lifecycle.py` | 14 |
| **分项之和** | **231** |

### 统一 Collect

```bash
python -m pytest \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_l3_security_boundary.py \
  tests/test_runtime_l3_protocol_env.py \
  tests/test_runtime_l3_deps_registry.py \
  tests/test_runtime_ui_lifecycle.py \
  --collect-only -q
# → 231 tests collected
```

**分项之和 = 统一 collect = 231。✅**

### 与 3.1.1B 审核包 L3 分项对比

| 文件 | 3.1.1B-S 报告 | 3.1.1C 实测 | 一致 |
|------|-------------|-----------|------|
| `test_runtime_l3_security_boundary.py` | 40 | 40 | ✅ |
| `test_runtime_l3_protocol_env.py` | 37 | 37 | ✅ |
| `test_runtime_l3_deps_registry.py` | 29 | 29 | ✅ |
| **L3 合计** | **106** | **106** | ✅ |

L1 (60) + L2 (51) = 111，与 3.1.1A 封板值一致。

### Installer Symlink 两个 Node

```bash
python -m pytest \
  tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry \
  tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction \
  --collect-only -q
# → 2 tests collected
```

两个 node 确认存在（不计入 Runtime 231）。

## 7. skip/xfail 规避扫描（第六节）

```bash
rg -n \
  "pytest\.skip|pytest\.mark\.skip|skipif|xfail|pytest\.mark\.xfail" \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_l3_security_boundary.py \
  tests/test_runtime_l3_protocol_env.py \
  tests/test_runtime_l3_deps_registry.py \
  tests/test_runtime_ui_lifecycle.py
```

**结果：零匹配。** 六个 Runtime 测试文件中无任何 skip、xfail、skipif 标记。

## 8. 关键安全 Node 清单核验（第九节）

以下全部在 T1 collect 输出中确认真实 node 存在：

| 类别 | 测试类/函数 | 文件 | 存在 |
|------|-----------|------|------|
| 父进程不 import 技能模块 | `TestParentIsolation::test_parent_does_not_import_run_module` | L2 | ✅ |
| audit hooks 先于技能模块加载 | `TestAuditHookInstallOrder::test_permissions_installed_before_exec_module` | L3 sec | ✅ |
| audit hooks（L2 视角） | `TestAuditHooks::test_hooks_installed_before_module_execution` | L2 | ✅ |
| socket create 拒绝 | `TestRunNetworkBlocked::test_socket_create_rejected` | L3 sec | ✅ |
| socket connect 拒绝 | `TestRunNetworkBlocked::test_socket_connect_rejected` | L3 sec | ✅ |
| subprocess 拒绝 | `TestRunSubprocessBlocked::test_subprocess_rejected` | L3 sec | ✅ |
| os.system 拒绝 | `TestRunSubprocessBlocked::test_os_system_rejected` | L3 sec | ✅ |
| ctypes 拒绝 | `TestRunNativeBlocked::test_ctypes_rejected` | L3 sec | ✅ |
| installed 目录写入拒绝 | `TestRunWriteBlocked::test_write_installed_rejected` | L3 sec | ✅ |
| workspace 外写入拒绝 | `TestRunWriteBlocked::test_write_outside_workspace_rejected` | L3 sec | ✅ |
| Registry 读取拒绝 | `TestRunRegistryAccess::test_read_registry_rejected` | L3 sec | ✅ |
| Registry 修改拒绝 | `TestRunRegistryAccess::test_modify_registry_rejected` | L3 sec | ✅ |
| symlink 逃逸拒绝 | `TestRunPathEscape::test_symlink_escape_rejected` | L3 sec | ✅ |
| .. 逃逸拒绝 | `TestRunPathEscape::test_dot_dot_escape_rejected` | L3 sec | ✅ |
| project root/CWD/home 拒绝 | `TestRunPathEscape::test_project_root_cwd_home_not_auto_allowed` | L3 sec | ✅ |
| 父进程环境 secret 五个输出面 | `TestParentEnvSecretIsolation` (5 tests) | L3 env | ✅ |
| 用户敏感 params 五个输出面 | `TestSensitiveParamsRedaction` (9 tests) | L3 env | ✅ |
| stdout.log 三个边界 case | `test_sensitive_params_redacted_in_stdout[boundary-near-limit/cross-boundary/utf8-multibyte]` | L3 env | ✅ |
| stderr.log 三个边界 case | `test_sensitive_params_redacted_in_stderr[boundary-near-limit/cross-boundary/utf8-multibyte]` | L3 env | ✅ |
| dependencies 不安装、不 import | `TestRunDependencies` (5 tests) | L3 deps | ✅ |
| Registry 七个终态不变性 | `TestRegistryImmutability` (7 parameterized) | L3 deps | ✅ |
| timeout/cancel 后无孤儿 Worker | `TestRunLifecycle::test_no_orphan_worker_after_cancel` + `TestTimeoutCancelCrashReal::test_cancel_leaves_no_worker_process` | L2 | ✅ |
| healthcheck 向后兼容 | `TestResponseBackwardCompat` (5) + `TestRequestBackwardCompat` (4) | L1 | ✅ |
| run status 映射 | Runtime status → L2 多类覆盖 | L2 | ✅ |

**关键安全类别全部覆盖，无缺失。**

## 9. T2 — 完整 Runtime 正式回归

### 命令

```bash
python -m pytest \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_l3_security_boundary.py \
  tests/test_runtime_l3_protocol_env.py \
  tests/test_runtime_l3_deps_registry.py \
  tests/test_runtime_ui_lifecycle.py \
  -q
```

无 `-k`、`--maxfail`、`--deselect`、`--ignore`。

### 结果（3.1.1C 初次执行 — 发现历史既有测试失效）

```
collected 231 items

... (230 passed) ...

================================== FAILURES ===================================
__ TestRuntimeUIHealthcheckLifecycle.test_normal_run_action_remains_disabled __

self = <tests.test_runtime_ui_lifecycle.TestRuntimeUIHealthcheckLifecycle object at 0x...>
qapp = <PyQt6.QtWidgets.QApplication object at 0x...>

    def test_normal_run_action_remains_disabled(self, qapp):
        """Verify that 'run' operation is not available (only healthcheck)."""
        from dp_engine.skills.runtime_models import ALLOWED_OPERATIONS

        # In Batch 3.0, only healthcheck is allowed
>       assert "run" not in ALLOWED_OPERATIONS, (
            "'run' operation should NOT be in ALLOWED_OPERATIONS for Batch 3.0"
        )
E       AssertionError: 'run' operation should NOT be in ALLOWED_OPERATIONS for Batch 3.0
E       assert 'run' not in frozenset({'healthcheck', 'run'})

tests\test_runtime_ui_lifecycle.py:383: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_runtime_ui_lifecycle.py::TestRuntimeUIHealthcheckLifecycle::test_normal_run_action_remains_disabled
======================= 1 failed, 230 passed in 41.20s ========================
```

### 统计（初次）

| 指标 | 值 |
|------|-----|
| Collected | 231 |
| Passed | 230 |
| Failed | 1 |
| Skipped | 0 |
| Deselected | 0 |
| Xfailed | 0 |
| Xpassed | 0 |

### 根因分析

（保留原始分析，详见 §9.1）

### 3.1.1C-R 校正后正式回归结果

```bash
python -m pytest \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_l3_security_boundary.py \
  tests/test_runtime_l3_protocol_env.py \
  tests/test_runtime_l3_deps_registry.py \
  tests/test_runtime_ui_lifecycle.py \
  -q
```

```
collected 231 items
...................... (all passed) ......................
======================= 231 passed in 39.82s ========================
```

### 统计（3.1.1C-R 校正后）

| 指标 | 值 |
|------|-----|
| Collected | 231 |
| Passed | 231 |
| Failed | 0 |
| Skipped | 0 |
| Deselected | 0 |
| Xfailed | 0 |
| Xpassed | 0 |

### T2 门槛判定（3.1.1C-R 校正后）

| 门槛 | 要求 | 实际 | 通过 |
|------|------|------|------|
| 全部 collected Runtime node passed | 231/231 | 231/231 | ✅ |
| 0 failed | 0 | 0 | ✅ |
| 0 skipped | 0 | 0 | ✅ |
| 0 deselected | 0 | 0 | ✅ |
| 0 xfailed | 0 | 0 | ✅ |
| 0 xpassed | 0 | 0 | ✅ |

## 10. T3 — Installer Symlink 安全哨兵

### 命令

```bash
python -m pytest \
  tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry \
  tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction \
  -q
```

### 结果

```
collected 2 items
tests\test_skill_package.py ..                               [100%]
======================= 2 passed in 0.33s ========================
```

### 门槛

| 门槛 | 要求 | 实际 | 通过 |
|------|------|------|------|
| 2 passed | 2 | 2 | ✅ |
| 0 failed | 0 | 0 | ✅ |
| 0 skipped | 0 | 0 | ✅ |
| 0 deselected | 0 | 0 | ✅ |

## 11. Git 范围核验

### 3.1.1C 零修改范围

| 检查项 | 开始前 | 结束后 | 一致 |
|--------|--------|--------|------|
| `git diff --name-only` | 33 files | 33 files | ✅ |
| `git diff --stat` | +5998/-1039 | +5998/-1039 | ✅ |
| `git status --short` tracked modified | 33 `M` | 33 `M` | ✅ |
| Runtime 生产文件 | `??` untracked | `??` untracked | ✅ |
| Runtime 测试文件 | `??` untracked | `??` untracked | ✅ |

**结论：3.1.1C 未意外改变任何生产代码或测试文件。**

### 3.1.1C-R 修改范围

本批（3.1.1C-R）仅修改：

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `tests/test_runtime_ui_lifecycle.py` | 测试校正 | 仅 `test_normal_run_action_remains_disabled` |
| `docs/agents/batch-3.1.1C-audit-package.md` | 审核包更新 | 新增 3.1.1C-R 章节，更新统计数据 |

**零生产代码修改。零 fixture 修改。零其他测试文件修改。**

## 12. P0 / P1 / P2 分类

### P0（3.1.1C-R 校正后：零项）

**3.1.1C 原始 P0-1 已在 3.1.1C-R 中解决。**

| # | 问题 | 状态 |
|---|------|------|
| ~~P0-1~~ | ~~T2 正式回归 1 failed~~ | ✅ 3.1.1C-R 校正完成 |

### P1

无。

### P2

| # | 问题 | 说明 |
|---|------|------|
| P2-1 | UI Run 入口完整实施 | 计划在 Batch 3.1.2 中实施，当前仅保持禁用状态。 |
| P2-2 | UI lifecycle 测试可进一步增强 | 当前 14 node 全部通过。3.1.2 实现 Run UI 后需新增对应测试。 |

## 13. Batch 3.1.1 封板候选结论

**当前状态：✅ 可封板。**

3.1.1C-R 校正完成，所有门槛通过：

| 检查项 | 结果 |
|--------|------|
| T0 compileall | 0 errors ✅ |
| T0 pyright | 0 errors, 0 warnings ✅ |
| T1 collect | 231 ✅ |
| T2 Runtime 回归 | 231/231 ✅ |
| T3 installer 哨兵 | 2/2 ✅ |
| skip/xfail 规避 | 零 ✅ |
| 关键安全 node | 全覆盖 ✅ |
| Git 范围 | 仅测试+审核包 ✅ |
| 生产代码修改 | 零 ✅ |

## 14. 未开始 3.1.2 声明

以下内容**未实施**：

- ❌ UI run 按钮 / 参数编辑器（3.1.2）
- ❌ Artifact 发布、复制、移动或消费（3.2）
- ❌ Report bridge / Word/PPT Builder（3.3）
- ❌ capabilities 动态授权
- ❌ dependencies 安装
- ❌ network / subprocess / os.system / ctypes 放行
- ❌ 多技能并行 / 后台任务
- ❌ 外部插件下载

## 15. 审核包文件实物信息

| 属性 | 值 |
|------|-----|
| 绝对路径 | `D:\桌面文件\软件项目_qt6\docs\agents\batch-3.1.1C-audit-package.md` |
| 仓库相对路径 | `docs/agents/batch-3.1.1C-audit-package.md` |
| 行数 | 630 |
| 文件大小 | 20,229 bytes (19.8 KB) |
| SHA256 | `090a9297ea3f95bba4942d0623c66b251bdf03ab64674b362af1f9ddb49e86e8` |
| UTF-8 读取 | OK |

## 16. 最终声明

```
Batch 3.1.1C + 3.1.1C-R 已完成并提交外部审核。
231项 Runtime 正式回归全绿。
2项 installer symlink 安全哨兵全绿。
零 P0。
Batch 3.1.1 当前为封板候选，尚未自行宣布外部审核通过。
未开始 Batch 3.1.2。
未开始 Artifact、Report bridge 或其他后续批次。
等待外部审核结论。
```

---

## 17. Batch 3.1.1C-R — UI 禁用测试合同校正

**执行日期**: 2026-07-22
**分支**: llama-cpp
**类型**: 测试合同校正（非新功能批次）

### 17.1 原失败 Node

```
tests/test_runtime_ui_lifecycle.py::TestRuntimeUIHealthcheckLifecycle::test_normal_run_action_remains_disabled
```

### 17.2 原错误合同

```python
assert "run" not in ALLOWED_OPERATIONS  # Batch 3.0 合同
assert len(ALLOWED_OPERATIONS) == 1     # 仅 healthcheck
```

该断言在 Batch 3.0 中正确，但 Batch 3.1.1A 已将 `"run"` 加入 `ALLOWED_OPERATIONS`，
核心 Runtime 已正式支持 `run` 操作。测试合同未同步更新。

### 17.3 新两层合同

| 层 | 合同 | 验证方式 |
|----|------|---------|
| **Runtime 核心** | `"run" in ALLOWED_OPERATIONS` | 直接导入 `runtime_models.ALLOWED_OPERATIONS` 断言 |
| **UI 禁用** | Run 按钮 `isEnabled() is False` | 创建真实 `AgentSkillWidget`，在 widget 树中定位 Run 按钮并断言禁用状态 |

### 17.4 测试函数最小修改

**修改前**（仅检查 ALLOWED_OPERATIONS）：
```python
def test_normal_run_action_remains_disabled(self, qapp):
    from dp_engine.skills.runtime_models import ALLOWED_OPERATIONS
    assert "run" not in ALLOWED_OPERATIONS, ...
    assert "healthcheck" in ALLOWED_OPERATIONS
    assert len(ALLOWED_OPERATIONS) == 1, ...
```

**修改后**（双层合同）：
```python
def test_normal_run_action_remains_disabled(self, qapp):
    from dp_engine.skills.runtime_models import ALLOWED_OPERATIONS
    from ui.skill_install_controller import SkillInstallTaskOwner
    from ui.skill_tab import AgentSkillWidget
    from PyQt6.QtWidgets import QPushButton

    # Contract 1: Runtime core supports 'run' (Batch 3.1.1A)
    assert "run" in ALLOWED_OPERATIONS, ...
    assert "healthcheck" in ALLOWED_OPERATIONS

    # Contract 2: UI Run entry remains disabled (Batch 3.1.2 not started)
    task_owner = SkillInstallTaskOwner(parent=qapp)
    widget = AgentSkillWidget(task_owner=task_owner)
    run_buttons = [btn for btn in widget.findChildren(QPushButton)
                   if "加载并运行" in btn.text()]
    assert len(run_buttons) == 1, ...
    run_btn = run_buttons[0]
    assert not run_btn.isEnabled(), ...
    widget.deleteLater()
```

### 17.5 UI 真实 disabled 断言

```python
assert not run_btn.isEnabled(), (
    "UI Run button must remain disabled until Batch 3.1.2"
)
```

- 按钮文字：`"加载并运行 (尚未实现 SkillRuntime)"`
- 按钮状态：`setEnabled(False)`（在 `_build_install_section` 中设置）
- 按钮 tooltip：`"SkillRuntime 将在后续批次实现"`
- 通过 `findChildren(QPushButton)` 在真实 widget 树中定位，非 mock

### 17.6 是否修改生产代码

**零生产代码修改。** 仅修改 `tests/test_runtime_ui_lifecycle.py` 的测试函数体。

### 17.7 T0 结果

```bash
python -m compileall -f tests/test_runtime_ui_lifecycle.py
# → Compiling 'tests/test_runtime_ui_lifecycle.py'... (0 errors)
```

**0 errors ✅**

### 17.8 单节点结果

```bash
python -m pytest "tests/test_runtime_ui_lifecycle.py::TestRuntimeUIHealthcheckLifecycle::test_normal_run_action_remains_disabled" -q -vv
# → 1 passed in 0.44s
```

**1 passed, 0 failed, 0 skipped, 0 deselected ✅**

### 17.9 UI 生命周期文件结果

```bash
python -m pytest tests/test_runtime_ui_lifecycle.py -q
# → 14 passed in 13.71s
```

**14 passed, 0 failed, 0 skipped, 0 deselected ✅**

### 17.10 231 项 Runtime 完整回归

```bash
python -m pytest \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_l3_security_boundary.py \
  tests/test_runtime_l3_protocol_env.py \
  tests/test_runtime_l3_deps_registry.py \
  tests/test_runtime_ui_lifecycle.py \
  -q
# → 231 passed in 39.82s
```

| 指标 | 值 |
|------|-----|
| Collected | 231 |
| Passed | 231 |
| Failed | 0 |
| Skipped | 0 |
| Deselected | 0 |
| Xfailed | 0 |
| Xpassed | 0 |

**231 passed, 0 failed, 0 skipped, 0 deselected ✅**

### 17.11 2 项 installer 哨兵

```bash
python -m pytest \
  tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry \
  tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction \
  -q
# → 2 passed in 0.33s
```

**2 passed, 0 failed, 0 skipped, 0 deselected ✅**

### 17.12 skip/xfail 扫描

```bash
rg -n "pytest\.skip|pytest\.mark\.skip|skipif|xfail|pytest\.mark\.xfail" \
  tests/test_runtime_ui_lifecycle.py
# → 零匹配
```

**零规避 ✅**

### 17.13 Git 范围

| 文件 | 变更类型 |
|------|---------|
| `tests/test_runtime_ui_lifecycle.py` | 仅修改 `test_normal_run_action_remains_disabled` 函数体 |
| `docs/agents/batch-3.1.1C-audit-package.md` | 新增 §17 + 更新统计数据 |

- 33 个 tracked modified 文件无变化 ✅
- 零生产代码修改 ✅
- 零 fixture 修改 ✅

### 17.14 P0 / P1 / P2

| 级别 | 内容 |
|------|------|
| **P0** | 零项 — 3.1.1C 的 P0-1 已在 3.1.1C-R 中解决 |
| **P1** | 零项 |
| **P2** | UI Run 入口完整实施（计划 3.1.2）；UI lifecycle 测试可进一步增强 |

### 17.15 Batch 3.1.1 封板候选结论

✅ **可封板。** 所有门槛通过：

- 231 项 Runtime 正式回归全绿
- 2 项 installer symlink 安全哨兵全绿
- 零 skip/xfail 规避
- 零生产代码修改
- 关键安全 node 全覆盖
- Git 范围严格限定
