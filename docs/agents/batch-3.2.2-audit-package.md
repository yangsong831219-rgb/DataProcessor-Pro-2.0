# Batch 3.2.2 — Artifact UI安全消费审核包

**日期**: 2026-07-22
**分支**: llama-cpp
**状态**: 已完成 — 提交外部审核
**批次类型**: UI安全消费实现

---

## 1. 最小Markdown读取声明

```text
已读取并遵守CLAUDE.md。
已读取已冻结的batch-3.2-planning-package.md。
已读取已封板的batch-3.2.1C-audit-package.md。
采用最小Markdown上下文原则，未读取其他历史Markdown。
当前只执行Batch 3.2.2。
```

实际读取：

| 文件 | 行数 | 用途 |
|------|------|------|
| `CLAUDE.md` | 79 | 项目执行纪律 |
| `docs/agents/batch-3.2-planning-package.md` | 1831 | Batch 3.2冻结合同 |
| `docs/agents/batch-3.2.1C-audit-package.md` | 517 | 已封板Artifact核心、407回归基线 |

---

## 2. 唯一目标

完成UI对已发布`RuntimeArtifact`的安全展示与消费：

```text
SkillRuntimeResponse.artifacts
→ AgentSkillWidget保存本次Run的主机权威Artifact元数据
→ UI Artifact列表（QTreeWidget）
→ 用户选择Artifact
→ SkillRuntimeController后台调用ArtifactStore
→ 定位 / 另存为 / 删除本次任务全部Artifact
→ UI显示安全结果
```

---

## 3. 实际修改文件

### 生产代码

| 文件 | 类型 | 说明 |
|------|------|------|
| `ui/skill_runtime_controller.py` | 修改（untracked既有） | 新增`_ArtifactWorker`、`start_artifact_locate/export/delete_task`、`artifact_result_ready`信号、ArtifactStore注入 |
| `ui/skill_tab.py` | 修改（tracked） | 新增Artifact列表UI、按钮、状态标签、owner跟踪、lifecycle安全 |

### 新增测试

| 文件 | 类型 | 行数 | SHA256 |
|------|------|------|--------|
| `tests/test_runtime_ui_artifact.py` | 新增（untracked） | 1157 | e0010a67f8b8974f99322d05c638996a8704c6d2ae69797babc305b0d5690009 |

### 禁止范围确认

```text
零Artifact核心生产代码修改（dp_engine/skills/runtime_*.py）
零fixture修改
零Report修改
零Chart修改
零配置文件修改
未开始3.2.3
未开始3.3
```

---

## 4. Artifact列表UI

在Run结果展示区域下方增加Artifact区域：

```text
── 运行产物 (N) ──
[QTreeWidget: 名称 | 类型 | 大小]
[定位] [另存为...] [删除全部]
[状态标签]
```

展示字段（主机权威）：
- `display_name` — 名称列
- `kind` 或 `media_type` — 类型列
- `size_bytes` — 大小列（自动格式化为B/KB/MB）

内部存储（QTreeWidgetItem UserRole data）：
- `artifact_id`
- `skill_id`
- `task_id`

不得展示：`storage_relpath`、`relative_path`、绝对路径、`sha256`、`manifest`内部字段。

### 列表行为

- `status=="succeeded"` + artifacts非空 → 按response.artifacts顺序展示
- `status=="succeeded"` + artifacts为空 → "本次运行未生成文件"
- `business-negative (ok=false)` + artifacts非空 → 正常展示
- 非succeeded状态 → 清除列表
- 普通result中的path字符串 → 不进入Artifact列表
- healthcheck artifacts → 不填入Run Artifact列表

---

## 5. Owner和当前Run状态

```python
_artifact_skill_id: str | None  # 当前artifact owner skill
_artifact_task_id: str | None   # 当前artifact owner task
```

冻结规则：
- 技能选择变化 → `_clear_artifact_state()`
- 新Run开始 → `_clear_artifact_state()`
- Run完成（succeeded + 有artifact）→ 设置owner + 填充列表
- Healthcheck → 不影响Run Artifact列表

---

## 6. 按钮启用条件

| 按钮 | 启用条件 |
|------|---------|
| 定位 | Widget未关闭 ∧ 无Runtime任务 ∧ 无Artifact操作 ∧ 选中有效Artifact（含非空artifact_id/skill_id/task_id） |
| 另存为 | 同定位 |
| 删除全部 | Widget未关闭 ∧ 无Runtime任务 ∧ 无Artifact操作 ∧ 列表非空 ∧ owner（skill_id/task_id）有效 |

任何Artifact操作进行中：
- Run按钮禁用
- Healthcheck按钮禁用
- 定位/另存为/删除全部禁用
- Cancel按钮禁用
- 参数编辑设为readOnly

---

## 7. Controller后台委托

`SkillRuntimeController`新增：

```python
# 信号
artifact_result_ready = pyqtSignal(str, bool, str, int)
# operation, success, safe_message, generation

# API
start_artifact_locate(skill_id, task_id, artifact_id) -> bool
start_artifact_export(skill_id, task_id, artifact_id, target, *, overwrite) -> bool
start_artifact_delete_task(skill_id, task_id) -> bool
```

实现特性：
- 后台`QThread`执行（`_ArtifactWorker`）
- 所有操作委托给`ArtifactStore`（可通过构造注入fake store用于测试）
- Runtime操作和Artifact操作共享`_running`互斥状态
- 重复启动返回`False`
- `finished/error`后清理`_artifact_worker`和`_artifact_thread`引用
- `close()`方法安全清理两个线程
- `is_artifact_running`属性用于查询artifact操作状态
- Generation token（`_artifact_generation`）用于生命周期追踪

---

## 8. 定位

- UI按钮 → `controller.start_artifact_locate()` → worker线程 → `ArtifactStore.locate()`
- UI不得调用`os.startfile`、shell命令、读取文件内容
- 成功提示："已在文件管理器中定位文件"
- 失败提示："无法定位该文件。文件可能已被移动、删除或安全校验失败。"
- 不显示绝对路径、traceback、manifest内容

---

## 9. 另存为

流程：
1. `QFileDialog.getSaveFileName()` — 用户选择目标路径
2. 目标已存在 → `QMessageBox.question` 覆盖确认
3. 用户取消/拒绝覆盖 → 不调用Controller
4. 调用 `controller.start_artifact_export(skill_id, task_id, artifact_id, target=target_path, overwrite=True)`
5. Worker线程 → `ArtifactStore.export()` — 完整安全验证、流式复制、hash校验、原子replace
6. 成功："文件已成功导出"
7. 失败：保留Artifact列表，显示安全错误

UI不自行复制文件、创建临时文件、os.replace、校验hash。

---

## 10. 删除任务

- 确认框："将删除本次运行生成的全部文件，此操作不可撤销。"
- 用户取消 → 不调用Controller
- 调用 `controller.start_artifact_delete_task(skill_id, task_id)`
- Worker线程 → `ArtifactStore.delete_task()` — manifest验证、owner验证、path containment、安全rmtree
- 成功 → 清空列表、清空owner和选择状态
- 失败 → 列表保持不变

语义明确：删除本次运行的全部Artifact，不是单文件删除。

---

## 11. 安全错误映射

`_ArtifactWorker.run()`中的错误映射：

| 异常类型 | 安全消息 |
|---------|---------|
| `FileNotFoundError` | "无法定位该文件。文件可能已被移动、删除或安全校验失败。" |
| `FileExistsError`（export overwrite=False） | "目标文件已存在，操作已取消" |
| `PermissionError` | "权限不足，无法完成操作" |
| `OSError` | "文件操作失败，请检查磁盘空间和目标路径" |
| `RuntimeError` with "hash"/"size" | "文件已损坏或校验不匹配，无法完成操作" |
| `RuntimeError` with "owner"/"skill_id"/"task_id" | "文件归属验证失败，无法完成操作" |
| `RuntimeError` with "symlink"/"reparse"/"escape" | "路径安全校验失败，无法完成操作" |
| `RuntimeError` with "not found" | "无法定位该文件..." |
| 其他 `RuntimeError` / `Exception` | "操作无法完成，请稍后重试" |

所有错误消息不包含：绝对路径、traceback、manifest原文、storage_relpath、SHA256。

---

## 12. 生命周期

防护措施：
- `_closing`标志：Widget销毁后回调不更新UI
- Signal disconnect：closeEvent中断开`artifact_result_ready`
- Controller `close()`：清理artifact线程和worker
- `start_artifact_*`检查`is_running`和`is_artifact_running`
- 失败后按钮通过`_refresh_artifact_buttons()`恢复
- Owner跟踪：新Run/技能切换清除旧状态
- Generation token在Controller中追踪每次操作
- Controller注册到TaskOwner：Widget关闭后线程生命周期安全

---

## 13. 实际新增node及collect

### test_runtime_ui_artifact.py: 40 nodes

| 类 | 节点数 | 覆盖范围 |
|----|--------|---------|
| TestArtifactDisplay | 11 | 展示：0/n个artifact、顺序保持、business-negative、failed/cancelled/protocol_error清除、healthcheck不填充、result路径不展示、技能切换清除、新Run清除 |
| TestArtifactButtonState | 10 | 按钮：无选择禁用、有选择启用、空列表删除禁用、非空列表删除启用、Runtime运行中禁用、Artifact操作中禁用、操作完成恢复、Widget关闭禁用、无owner删除禁用 |
| TestArtifactControllerDelegation | 7 | Controller：locate/export/delete传递owner、重复启动拒绝、后台线程、线程清理、互斥 |
| TestArtifactResults | 8 | 结果/错误：locate成功、export成功、delete成功清空、delete失败保留、文件缺失、hash不匹配、owner不匹配、路径安全（均验证无绝对路径/traceback） |
| TestArtifactLifecycle | 4 | 生命周期：Widget销毁安全、关闭后结果忽略、失败后按钮恢复、新Run不被旧结果覆盖 |

### test_runtime_ui_lifecycle.py: 36 nodes（无变化）

### 合计

| 指标 | 值 |
|------|-----|
| test_runtime_ui_artifact.py | 40 nodes |
| test_runtime_ui_lifecycle.py | 36 nodes |
| UI合计 | 76 nodes |
| Runtime统一总数 | 447 nodes |
| 新增UI唯一node | 40 nodes |

---

## 14. T0（编译和静态检查）

```bash
python -m compileall -f \
  ui/skill_tab.py \
  ui/skill_runtime_controller.py \
  tests/test_runtime_ui_artifact.py \
  tests/test_runtime_ui_lifecycle.py
```

**Compiling... 0 errors**

```bash
pyright \
  ui/skill_tab.py \
  ui/skill_runtime_controller.py
```

**0 errors, 0 warnings, 0 informations**

---

## 15. T2-UI（Artifact + Lifecycle正式测试）

```bash
python -m pytest \
  tests/test_runtime_ui_artifact.py \
  tests/test_runtime_ui_lifecycle.py \
  -q
```

**76 passed in 25.71s**
- 0 failed
- 0 skipped
- 0 deselected
- 0 xfailed
- 0 xpassed
- 正常退出，无挂起

---

## 16. Runtime统一回归

```bash
python -m pytest \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_l2_artifact_publish.py \
  tests/test_runtime_l3_security_boundary.py \
  tests/test_runtime_l3_protocol_env.py \
  tests/test_runtime_l3_deps_registry.py \
  tests/test_runtime_l3_artifact_security.py \
  tests/test_runtime_artifact_store.py \
  tests/test_runtime_ui_lifecycle.py \
  tests/test_runtime_ui_artifact.py \
  -q
```

**447 passed in 55.62s**
- 0 failed
- 0 skipped
- 0 deselected
- 0 xfailed
- 0 xpassed
- 正常退出，无挂起

407项基线 + 40项新增UI = 447项全部通过。

---

## 17. Installer哨兵

```bash
python -m pytest \
  tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry \
  tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction \
  -q
```

**2 passed in 0.07s**
- 0 failed
- 0 skipped
- 0 deselected

---

## 18. Skip/XFail扫描

```bash
rg -n \
  "pytest\.skip|pytest\.mark\.skip|skipif|pytest\.mark\.skipif|xfail|pytest\.mark\.xfail" \
  tests/test_runtime_ui_artifact.py \
  tests/test_runtime_ui_lifecycle.py
```

**0 matches** ✅

---

## 19. Git范围

### 本批3.2.2修改

| 文件 | 状态 | 说明 |
|------|------|------|
| `ui/skill_tab.py` | 修改（tracked M） | 新增Artifact UI区域、按钮、handler、lifecycle |
| `ui/skill_runtime_controller.py` | 修改（untracked ??，既有） | 新增`_ArtifactWorker`、artifact操作方法、信号 |
| `tests/test_runtime_ui_artifact.py` | 新增（untracked ??） | 40项UI artifact消费测试 |
| `docs/agents/batch-3.2.2-audit-package.md` | 新增（untracked ??） | 本审核包 |

### 3.2.1既有修改（未改动）

```text
dp_engine/skills/runtime_models.py, runtime_protocol.py,
runtime_worker.py, runtime_service.py, runtime_paths.py,
runtime_artifacts.py
tests/test_runtime_l1_models.py, test_runtime_l2_*.py,
test_runtime_l3_*.py, test_runtime_artifact_store.py,
test_runtime_ui_lifecycle.py
```

### 更早工作区修改

既有的33个tracked文件修改（chart, report, UI, etc.）不在本批范围。

### 证明

- **仅修改获批文件** ✅
- **零Artifact核心生产代码修改** ✅
- **零fixture修改** ✅
- **零Report修改** ✅
- **未开始3.2.3** ✅
- **未开始3.3** ✅

---

## 20. P0/P1/P2

### P0

```text
无 — 所有测试全绿，静态检查零warning，零核心代码修改。
```

### P1

```text
无新增P1。
```

### P2

```text
无新增P2。Artifact历史管理、搜索过滤、内容预览等推迟到后续版本。
```

---

## 21. 未开始3.2.3和3.3声明

```text
Batch 3.2.2已完成并提交外部审核。
未开始Batch 3.2.3。
未开始Batch 3.3 Report bridge。
等待外部审核结论。
```

---

## 22. 审核包实物信息

| 属性 | 值 |
|------|-----|
| 绝对路径 | `D:\桌面文件\软件项目_qt6\docs\agents\batch-3.2.2-audit-package.md` |
| 仓库相对路径 | `docs/agents/batch-3.2.2-audit-package.md` |
| 行数 | 441 |
| 大小 | ~14 KB |
| SHA256 | 16544dadb824c3b82b9fa9560a73152706284cb2776d7acea1b08100c4a72c23 |
| UTF-8 | ✅ |

---

# Batch 3.2.2-R — Export Overwrite Authorization and Dialog Evidence

**日期**: 2026-07-22
**类型**: P0定点整改轮
**原始错误**: 原实现始终传 `overwrite=True`；原审核包未提供确认对话框节点证据。

---

## R1. 外部审核结论（触发本轮）

```text
P0-1：
UI无论目标文件是否存在，都向ArtifactStore传递overwrite=True。
目标原本不存在时，若导出前出现同名文件，可能在用户未确认时覆盖。

P0-2：
审核包没有提供文件对话框、覆盖确认和删除确认的正式测试节点证据。
```

---

## R2. 原错误流程

### P0-1 原实现

```python
# ui/skill_tab.py _on_artifact_export (原)
target_path = Path(target)
if target_path.exists():
    reply = QMessageBox.question(...)
    if reply != QMessageBox.StandardButton.Yes:
        return

# BUG: always True, even when target was new
started = controller.start_artifact_export(
    ..., target=target_path, overwrite=True,
)
```

当目标在确认时不存在 → 无覆盖对话框 → 传给 ArtifactStore `overwrite=True`。
如果导出前出现同名文件，ArtifactStore 可能在用户未确认的情况下覆盖它。

### P0-2 原审核包缺陷

原审核包只有 TestArtifactControllerDelegation 中的 `test_export_passes_owner_target_overwrite_to_store`，
该测试硬编码 `overwrite=True` 调用 Controller，不经过 UI 对话框路径。
以下语义没有正式测试节点：

- QFileDialog 取消
- 新目标路径 overwrite=False
- 已存在目标拒绝覆盖
- 已存在目标同意覆盖 overwrite=True
- 竞态创建 FileExistsError
- 删除确认取消
- 删除确认同意

---

## R3. 新覆盖布尔值计算

### 冻结流程

```python
target_path = Path(target)

# Freeze whether the target existed at confirmation time
target_existed = target_path.exists()

if target_existed:
    reply = QMessageBox.question(
        self, "确认覆盖",
        f"文件 '{target_path.name}' 已存在，是否覆盖？",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if reply != QMessageBox.StandardButton.Yes:
        return  # User declined overwrite

# overwrite is True only when the target existed at confirmation
# time AND the user explicitly agreed to overwrite
overwrite = target_existed

started = controller.start_artifact_export(
    skill_id, task_id, artifact_id,
    target=target_path,
    overwrite=overwrite,
)
```

### 语义验证

| 场景 | target_existed | 用户操作 | overwrite | Controller调用 |
|------|---------------|---------|-----------|---------------|
| 新目标路径 | False | (无对话框) | False | 是 |
| 已存在+拒绝 | True | No | — | 否 |
| 已存在+同意 | True | Yes | True | 是 |
| 文件对话框取消 | — | 取消 | — | 否 |

---

## R4. 文件对话框取消

测试节点: `test_file_dialog_cancel_no_controller_call`

```text
QFileDialog.getSaveFileName → ("", "")
→ Controller export 调用次数为 0
→ Artifact 列表保持 (topLevelItemCount == 1)
→ _artifact_op_active 为 False
```

---

## R5. 新目标路径 overwrite=False

测试节点: `test_export_new_target_overwrite_false`

```text
目标文件不存在 (tmp_path / "nonexistent_subdir" / "exported.png")
→ 不弹覆盖确认 (QMessageBox.question 调用 0 次)
→ start_artifact_export(..., overwrite=False)
→ FakeArtifactStore 记录 overwrite=False (直接断言布尔值)
```

---

## R6. 已存在目标拒绝覆盖

测试节点: `test_export_existing_target_decline_overwrite`

```text
目标文件存在 (tmp_path / "existing.png")
→ 弹出覆盖确认
→ 用户选择 No (QMessageBox.StandardButton.No)
→ Controller 调用次数为 0
→ 原文件内容不变 ("original content")
```

---

## R7. 已存在目标同意覆盖

测试节点: `test_export_existing_target_accept_overwrite`

```text
目标文件存在 (tmp_path / "existing_overwrite.png")
→ 用户明确选择 Yes (QMessageBox.StandardButton.Yes)
→ start_artifact_export(..., overwrite=True)
→ FakeArtifactStore 记录 overwrite=True (直接断言布尔值)
```

---

## R8. 竞态创建安全结果

测试节点: `test_race_condition_file_created_after_check`

```text
UI 检查时目标不存在 → overwrite=False
FakeArtifactStore._fail_with = FileExistsError
→ Worker 映射 FileExistsError → "目标文件已存在，操作已取消"
→ 错误消息不包含绝对路径
→ Artifact 列表保持 (topLevelItemCount == 1)
→ FakeArtifactStore 记录 overwrite=False
```

---

## R9. 删除确认取消

测试节点: `test_delete_confirm_cancel_no_controller_call`

```text
用户选择 No
→ start_artifact_delete_task 调用次数为 0
→ Artifact 列表保持
→ Owner (skill_id/task_id) 保持
```

---

## R10. 删除确认同意

测试节点: `test_delete_confirm_accept_calls_controller_once`

```text
用户明确选择 Yes
→ start_artifact_delete_task(skill_id, task_id) 只调用一次
→ 参数 skill_id="test-skill", task_id="task-001"
→ 操作成功
→ 列表清空 (topLevelItemCount == 0)
```

---

## R11. 完整 Node ID

### 新增 TestArtifactDialogs 类 (7 nodes)

| # | Node ID | 覆盖 |
|---|---------|------|
| 1 | `test_file_dialog_cancel_no_controller_call` | 8.1 文件对话框取消 |
| 2 | `test_export_new_target_overwrite_false` | 8.2 新目标 overwrite=False |
| 3 | `test_export_existing_target_decline_overwrite` | 8.3 已存在拒绝覆盖 |
| 4 | `test_export_existing_target_accept_overwrite` | 8.4 已存在同意覆盖 |
| 5 | `test_race_condition_file_created_after_check` | 8.5 竞态创建 |
| 6 | `test_delete_confirm_cancel_no_controller_call` | 8.6 删除确认取消 |
| 7 | `test_delete_confirm_accept_calls_controller_once` | 8.7 删除确认同意 |

### Node 合计

| 指标 | Batch 3.2.2 | Batch 3.2.2-R | 变化 |
|------|------------|---------------|------|
| test_runtime_ui_artifact.py | 40 | 47 | +7 |
| test_runtime_ui_lifecycle.py | 36 | 36 | 0 |
| UI 合计 | 76 | 83 | +7 |
| Runtime 统一总数 | 447 | 454 | +7 |

---

## R12. T0（编译和静态检查）

```bash
python -m compileall -f ui/skill_tab.py tests/test_runtime_ui_artifact.py
```

**Compiling... 0 errors**

```bash
pyright ui/skill_tab.py ui/skill_runtime_controller.py
```

**0 errors, 0 warnings, 0 informations**

---

## R13. 对话框目标测试

```bash
python -m pytest tests/test_runtime_ui_artifact.py::TestArtifactDialogs -q
```

**7 passed in 0.94s**
- 0 failed
- 0 skipped
- 0 deselected
- 0 xfailed
- 0 xpassed

---

## R14. T2-UI（Artifact + Lifecycle 正式测试）

```bash
python -m pytest tests/test_runtime_ui_artifact.py tests/test_runtime_ui_lifecycle.py -q
```

**83 passed in 347.97s**
- 0 failed
- 0 skipped
- 0 deselected
- 0 xfailed
- 0 xpassed
- 正常退出，无挂起

真实 Windows 桌面会话执行（无 QT_QPA_PLATFORM=offscreen）。

---

## R15. Runtime 统一回归

```bash
python -m pytest \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_l2_artifact_publish.py \
  tests/test_runtime_l3_security_boundary.py \
  tests/test_runtime_l3_protocol_env.py \
  tests/test_runtime_l3_deps_registry.py \
  tests/test_runtime_l3_artifact_security.py \
  tests/test_runtime_artifact_store.py \
  tests/test_runtime_ui_lifecycle.py \
  tests/test_runtime_ui_artifact.py \
  -q
```

**454 passed in 48.06s**
- 0 failed
- 0 skipped
- 0 deselected
- 0 xfailed
- 0 xpassed
- 正常退出，无挂起

447 项基线 + 7 项新增对话框 = 454 项全部通过。

---

## R16. Installer 哨兵

```bash
python -m pytest \
  tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry \
  tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction \
  -q
```

**2 passed in 0.08s**
- 0 failed
- 0 skipped
- 0 deselected

---

## R17. Skip/XFail 扫描

```bash
rg -n \
  "pytest\.skip|pytest\.mark\.skip|skipif|pytest\.mark\.skipif|xfail|pytest\.mark\.xfail" \
  tests/test_runtime_ui_artifact.py \
  tests/test_runtime_ui_lifecycle.py
```

**0 matches** ✅

---

## R18. Git 范围

### 本轮 3.2.2-R 修改

| 文件 | 状态 | 说明 |
|------|------|------|
| `ui/skill_tab.py` | 修改（tracked M） | `_on_artifact_export`: overwrite=target_existed |
| `tests/test_runtime_ui_artifact.py` | 修改（untracked ?? 既有） | 新增 TestArtifactDialogs 类 (7 nodes) |
| `docs/agents/batch-3.2.2-audit-package.md` | 修改（untracked ?? 既有） | 新增 R-round 章节 |

### 未修改确认

```text
ui/skill_runtime_controller.py — 零修改 (git diff 无输出)
零 Artifact 核心生产代码修改
零 fixture 修改
零 Report 修改
未开始 3.2.3
未开始 3.3
```

### 文件实物信息

| 文件 | 行数 | SHA256 |
|------|------|--------|
| `ui/skill_tab.py` | 2054 | 41a5a5a601983c7bd10ea86d590f380f8ac49223d16da31450949f1cc5224293 |
| `ui/skill_runtime_controller.py` | 640 | e4395cbd04a268f8843cdb39f831af95de04ddd3224192e6ec1ccb0ce0d8b08f |
| `tests/test_runtime_ui_artifact.py` | 1464 | 04d7392977d5bdeeab36185ac2883a85c090f7a2963afdb1c08ed9c75c5e8ab9 |

---

## R19. P0/P1/P2

### P0

```text
无 — 两个原始 P0 已修复：
  P0-1: overwrite 合同已修正，覆盖确认后 overwrite=target_existed
  P0-2: 7 个正式 dialog 测试节点已添加
```

### P1

```text
无新增 P1。
```

### P2

```text
无新增 P2。
```

---

## R20. Batch 3.2.2 封板候选结论

```text
原 P0-1 和 P0-2 已闭环修复：
  - 新目标路径 now overwrite=False (曾始终 True)
  - 7 个 dialog confirmation 正式测试节点已添加
  - T0, T2-UI, Runtime 统一回归, Installer 哨兵全部零失败
  - 零 Artifact 核心代码修改

Batch 3.2.2-R 已完成并提交外部审核。
未开始 Batch 3.2.3。
未开始 Batch 3.3 Report bridge。
等待外部审核结论。
```
