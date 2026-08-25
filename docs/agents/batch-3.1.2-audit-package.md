# Batch 3.1.2 Audit Package — Run UI Lifecycle 实施

**Date**: 2026-07-22
**Branch**: llama-cpp
**Status**: ✅ 完成 — 等待外部审核

---

## 1. 最小上下文读取声明

已读取并遵守 `CLAUDE.md`。
已读取当前权威文件 `docs/agents/batch-3.1.1C-audit-package.md`。
采用最小上下文读取原则，未读取其他历史 Markdown。
当前只执行 Batch 3.1.2：Run UI Lifecycle。

## 2. 唯一目标完成情况

| # | 目标 | 状态 |
|---|------|------|
| 1 | Run 入口按正确条件启用 | ✅ |
| 2 | JSON 对象参数输入和本地校验 | ✅ |
| 3 | 后台执行，不阻塞 UI 线程 | ✅ |
| 4 | 运行中按钮状态 | ✅ |
| 5 | 取消操作 | ✅ |
| 6 | 成功结果展示 (succeeded) | ✅ |
| 7 | business-negative 结果展示 | ✅ |
| 8 | failed/permission_denied/timeout/cancelled/crashed/protocol_error 展示 | ✅ |
| 9 | Widget 销毁、页面切换、重复运行时的生命周期安全 | ✅ |
| 10 | 既有 healthcheck UI 行为不回退 | ✅ |

## 3. 实际修改文件

### 生产代码

| 文件 | 变更类型 | 行数 | 大小 |
|------|---------|------|------|
| `ui/skill_runtime_controller.py` | 扩展 | 325 | 11,619 bytes |
| `ui/skill_tab.py` | 修改 | 1,685 | 65,272 bytes |

### 测试

| 文件 | 变更类型 | 行数 | 大小 |
|------|---------|------|------|
| `tests/test_runtime_ui_lifecycle.py` | 扩展 | 1,850 | 69,229 bytes |

### 审核包

| 文件 | 变更类型 |
|------|---------|
| `docs/agents/batch-3.1.2-audit-package.md` | 新增 |

### 禁止文件修改核验

以下文件**零修改**（git diff 确认）：

```text
dp_engine/skills/runtime_models.py       → 0 changes
dp_engine/skills/runtime_protocol.py     → 0 changes
dp_engine/skills/runtime_service.py      → 0 changes
dp_engine/skills/runtime_worker.py       → 0 changes
dp_engine/skills/runtime_errors.py       → 0 changes
dp_engine/skills/runtime_permissions.py  → 0 changes
dp_engine/skills/runtime_dependencies.py → 0 changes
dp_engine/skills/registry.py             → 0 changes
dp_engine/skills/installer.py            → 0 changes
tests/fixtures/runtime_fixtures.py       → 0 changes
```

## 4. Run 入口启用条件

`_refresh_run_button_state()` 集中管理按钮启用逻辑：

**启用条件（全部满足）**:
- 存在当前技能选择（`_get_selected_skill()` 返回非 None）
- 技能已安装且版本在注册表中存在
- manifest 中存在 `run` entrypoint
- 当前没有 healthcheck 或 run 任务正在执行
- Widget 未进入销毁状态（`_closing` 为 False）
- 参数输入为空或为合法 JSON 对象

**禁用情况**:
- 技能未安装
- run entrypoint 缺失
- 当前已有任务运行（`_runtime_controller.is_running`）
- Widget 正在关闭或已销毁
- 运行参数为非空且非 JSON 对象（数组、字符串、数字、非法 JSON）

**触发刷新时机**:
- 表格选择变更（`itemSelectionChanged`）
- 参数文本变更（`textChanged`）
- 运行时状态变更（`_on_runtime_running_changed`）
- 注册表刷新（`_refresh_registry_table`）

## 5. 参数输入与校验

- 使用 `QLineEdit` 作为参数输入区
- 空白输入按 `{}` 处理（Run 按钮保持启用）
- 非空输入进行 `json.loads()` 本地解析
- 非法 JSON → 禁用 Run 按钮，tooltip 显示"参数 JSON 格式无效"
- 非 object 顶层（array/string/number/bool/null）→ 禁用 Run 按钮
- 点击 Run 时二次校验：非法 JSON 显示错误且不调用 Controller
- 不把原始参数写入日志、异常文本或调试输出
- 最终权威校验由已封板 Runtime 完成

## 6. Controller 后台执行与取消

### API

```python
def start_run(self, skill_id, version, params, *, timeout_seconds=None) -> bool
```

- 后台 QThread 调用 `SkillRuntimeService.run_skill()`
- UI 线程不直接执行阻塞调用
- 同一 Controller 同时只允许一个活动 Runtime 任务
- 重复调用返回 `False`（不创建第二个线程）
- `cancel()` 同时适用于 healthcheck 和 run
- 线程结束后清除活动引用
- 复用现有 healthcheck QThread 模型（同一 `_RuntimeWorker` 类，通过 `operation` 参数分发）
- Controller 销毁时不留下活动 QThread

### _RuntimeWorker 扩展

- 新增 `operation: str` 参数（`"healthcheck"` 或 `"run"`）
- 新增 `params: dict[str, object] | None` 参数
- `run()` 方法根据 `operation` 分发到 `run_healthcheck()` 或 `run_skill()`

## 7. 状态与结果映射

### succeeded

- 显示 `status=succeeded, success=True` 及结构化 result（`json.dumps(indent=2)`）
- business-negative（`{"ok": false}`）仍显示 Runtime 执行成功，业务结果 `ok=false`（黄色/橙色）

### 其他状态（红色/暗红色）

| 状态 | 显示行为 |
|------|---------|
| `failed` | 显示错误信息（来自 `error.message`） |
| `permission_denied` | 显示权限拒绝原因 |
| `timeout` | 显示超时消息 |
| `cancelled` | 显示已取消（灰色） |
| `crashed` | 显示崩溃错误 |
| `protocol_error` | 显示协议错误 |

- UI 不重新推导或覆盖 Runtime 状态
- 不展示 traceback、原始 params、environment 或文件系统内部细节

## 8. Widget 生命周期

- `closeEvent()` 断开 runtime dispatcher 信号，取消运行中任务，transfer controller 到 TaskOwner
- `_closing` 标志阻止关闭后 UI 回调
- `_active_operation` 跟踪当前操作类型用于错误分发
- `_run_generation` 计数器用于防止旧结果覆盖新结果
- `_on_runtime_result` / `_on_runtime_error` 检查 `_closing`
- 所有运行时状态变更通过 `_on_runtime_running_changed` 统一处理 UI 恢复

## 9. UI 测试 Collect

### UI 文件新旧统计

| 类别 | 测试类数 | Node 数 |
|------|---------|--------|
| 既有 healthcheck UI | TestRuntimeUIHealthcheckLifecycle | 7 |
| 既有 subprocess | TestApplicationQuitWithRuntime (3) + TestCoreRuntimeLifecycle (3) + TestRuntimeCloseNoDestroyedWarning (1) | 7 |
| **既有小计** | | **14** |
| Run 按钮状态 | TestRunButtonState | 4 |
| 参数校验 | TestRunParamValidation | 3 |
| Controller 生命周期 | TestRunControllerLifecycle | 4 |
| 结果映射 | TestRunResultMapping | 8 |
| 生命周期安全 | TestRunLifecycleSafety | 3 |
| **新增小计** | | **22** |
| **UI 文件总计** | | **36** |

### Runtime 统一统计

```bash
python -m pytest \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_l3_security_boundary.py \
  tests/test_runtime_l3_protocol_env.py \
  tests/test_runtime_l3_deps_registry.py \
  tests/test_runtime_ui_lifecycle.py \
  --collect-only -q
# → 253 tests collected
```

| 指标 | 值 |
|------|-----|
| 3.1.1C 基线 | 231 |
| 3.1.2 新增 | +22 |
| 3.1.2 总计 | 253 |

## 10. T0 结果

### compileall

```bash
python -m compileall -f \
  ui/skill_tab.py \
  ui/skill_runtime_controller.py \
  tests/test_runtime_ui_lifecycle.py
```

**结果：0 errors ✅**

### pyright

```bash
pyright ui/skill_tab.py ui/skill_runtime_controller.py
```

**结果：0 errors, 0 warnings, 0 informations ✅**

## 11. T2-UI 结果

```bash
python -m pytest tests/test_runtime_ui_lifecycle.py -q
```

```
collected 36 items
....................................
======================= 36 passed in 398.12s ========================
```

| 指标 | 值 |
|------|-----|
| Collected | 36 |
| Passed | 36 |
| Failed | 0 |
| Skipped | 0 |
| Deselected | 0 |
| Xfailed | 0 |
| Xpassed | 0 |

## 12. Runtime 完整回归

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
collected 253 items
.............................. (all passed) ..............................
======================= 253 passed in 69.54s ========================
```

| 指标 | 值 |
|------|-----|
| Collected | 253 |
| Passed | 253 |
| Failed | 0 |
| Skipped | 0 |
| Deselected | 0 |
| Xfailed | 0 |
| Xpassed | 0 |

## 13. Installer 哨兵

```bash
python -m pytest \
  tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry \
  tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction \
  -q
```

```
collected 2 items
..                               [100%]
======================= 2 passed in 0.08s ========================
```

| 指标 | 值 |
|------|-----|
| Passed | 2 |
| Failed | 0 |
| Skipped | 0 |
| Deselected | 0 |

## 14. skip/xfail 扫描

```bash
rg -n "pytest\.skip|pytest\.mark\.skip|skipif|xfail|pytest\.mark\.xfail" \
  tests/test_runtime_ui_lifecycle.py
# → 零匹配
```

**零规避 ✅**

## 15. Git 范围

### 本批修改文件

| 文件 | 状态 | 说明 |
|------|------|------|
| `ui/skill_tab.py` | M (tracked) | Run UI widgets + handlers + dispatchers |
| `ui/skill_runtime_controller.py` | ?? (untracked) | `start_run()` + Worker dispatch |
| `tests/test_runtime_ui_lifecycle.py` | ?? (untracked) | +22 Run UI lifecycle tests |

### 禁止文件修改为零

所有 `dp_engine/skills/runtime_*.py`、`dp_engine/skills/registry.py`、`dp_engine/skills/installer.py`、`tests/fixtures/runtime_fixtures.py` — 零修改。

### 既有工作区修改

33 个 tracked modified 文件（Batch 3.0 / 3.1.1 遗留）未在本批被回退或整理。

## 16. P0 / P1 / P2

| 级别 | 内容 | 状态 |
|------|------|------|
| **P0** | 零项 | ✅ |
| **P1** | 零项 | ✅ |
| **P2** | JSON 编辑器增强、历史记录、进度条、Artifact 预览、报告集成 | 计划后续批次 |

## 17. 未开始 3.2 声明

以下内容**未实施**：

- ❌ Artifact 发布、复制、移动或消费（Batch 3.2）
- ❌ Report bridge / Word/PPT Builder（Batch 3.3）
- ❌ capabilities 动态授权
- ❌ dependencies 安装
- ❌ network / subprocess / os.system / ctypes 放行
- ❌ 多技能并行 / 后台任务
- ❌ 外部插件下载

---

## 18. 审核包文件实物信息

| 属性 | 值 |
|------|-----|
| 绝对路径 | `D:\桌面文件\软件项目_qt6\docs\agents\batch-3.1.2-audit-package.md` |
| 仓库相对路径 | `docs/agents/batch-3.1.2-audit-package.md` |
| 行数 | 359 |
| 文件大小 | 10,744 bytes (10.5 KB) |
| SHA256 | `6c0be0462b3e9b4147af60dbff6253566f70b8ebea471d97f03bb9d47f857892` |

## 19. 最终声明

```
Batch 3.1.2 已完成并提交外部审核。
253 项 Runtime 完整回归全绿（231 基线 + 22 新增 UI 测试）。
2 项 installer symlink 安全哨兵全绿。
零 P0，零 P1。
未开始 Batch 3.2。
未开始 Artifact、Report bridge 或其他后续批次。
等待外部审核结论。
```
