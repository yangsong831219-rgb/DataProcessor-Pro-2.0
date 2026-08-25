# Batch 3.2.3 — Artifact安全发布与消费统一回归及最终封板候选审核包

**日期**: 2026-07-23
**分支**: llama-cpp
**状态**: ⚠️ P0阻塞 — 提交外部审核
**批次类型**: 纯审计、完整回归与封板批次

---

## 1. 最小Markdown读取声明

```text
已读取并遵守CLAUDE.md。
已读取已冻结的batch-3.2-planning-package.md。
已读取已封板的batch-3.2.1C-audit-package.md。
已读取已封板的batch-3.2.2-audit-package.md。
采用最小Markdown上下文原则，未读取其他历史Markdown。
当前只执行Batch 3.2.3。
```

实际读取：

| 文件 | 行数 | 用途 |
|------|------|------|
| `CLAUDE.md` | 79 | 项目执行纪律 |
| `docs/agents/batch-3.2-planning-package.md` | 1831 | Batch 3.2冻结合同 |
| `docs/agents/batch-3.2.1C-audit-package.md` | 517 | 已封板Artifact核心、407基线 |
| `docs/agents/batch-3.2.2-audit-package.md` | 843 | 已封板UI消费、覆盖授权整改、454基线 |

---

## 2. 唯一目标

```text
1. 静态核验Batch 3.2最终合同没有漂移；
2. 核验测试不存在skip、xfail、fallback或过滤规避；
3. 运行完整Artifact、UI、Runtime和Installer回归；
4. 证明Batch 3.2没有越界进入Report bridge；
5. 生成Batch 3.2最终封板候选审核包。
```

未实现任何新功能。

---

## 3. 实际修改文件

### 本批修改

| 文件 | 类型 | 说明 |
|------|------|------|
| `docs/agents/batch-3.2.3-audit-package.md` | 新增 | 本审核包 |

### 禁止范围确认

```text
零生产代码修改
零测试代码修改
零fixture修改
零配置修改
零UI修改
零Report修改
零Chart修改
未开始Batch 3.3
```

### 3.2.1/3.2.2既有修改（未改动）

```text
Batch 3.2.1: dp_engine/skills/runtime_models.py, runtime_protocol.py,
  runtime_worker.py, runtime_service.py, runtime_paths.py, runtime_artifacts.py
  tests/test_runtime_l1_models.py, test_runtime_l2_artifact_publish.py,
  test_runtime_l3_artifact_security.py, test_runtime_artifact_store.py

Batch 3.2.2: ui/skill_tab.py, ui/skill_runtime_controller.py
  tests/test_runtime_ui_artifact.py, tests/test_runtime_ui_lifecycle.py
```

### 更早工作区修改

既有的33个tracked文件修改（chart, report, UI等）不在本批范围。见开始前Git基线。

---

## 4. 开始前Git基线

```bash
git status --short
```

- 32个tracked modified文件（既有工作区修改，非本批）
- 1个tracked deleted文件（既有）
- ~45个untracked文件/目录（含Batch 3.2全部新增文件）

无本批代码修改。

---

## 5. 最终合同静态审计

### 5.1 Artifact声明和Wire ✅

基于3.2.1C审核包审计结论（已验证生产代码未变）：

- `ArtifactRunContext` 仍是 `dict` 子类
- 旧context键值兼容
- 非法声明fatal状态不可被技能吞掉
- 完整 `artifact_declarations` Wire上限为 32768 bytes
- metadata聚合上限为 24 * 1024 bytes
- Worker只产生 `ArtifactDeclaration`
- Worker不能产生 `artifact_id`、`storage_relpath` 或 `owner` 字段
- 内部 `artifact_declarations` 不进入公开 `SkillRuntimeResponse`

### 5.2 Healthcheck与普通Run ✅

基于3.2.1C审计结论（已验证生产代码未变）：

- healthcheck既有自动收集output行为保持
- healthcheck未迁移到 `declare_artifact`
- 旧 `sha256=None` 仍兼容
- 普通run只有显式 `declare_artifact` 才发布
- 未声明output文件不发布
- 业务result中的path/file/output字符串不触发发布

### 5.3 文件系统安全 ✅

基于3.2.1C审计结论（已验证生产代码未变）：

- 唯一源根为 `workspace/output`
- 逐级lstat父组件
- symlink、junction、reparse拒绝
- hardlink拒绝
- 目录和特殊文件拒绝
- 单文件50 MB和总计200 MB限制
- Worker观察字段与Service open+fstat二次比较
- SHA256流式比较
- 源文件替换或TOCTOU不提交

### 5.4 类型合同 ✅

基于3.2.1C审计结论（已验证生产代码未变）：

支持：PDF、PNG、JPEG、DOCX、PPTX、XLSX、ZIP、JSON、TXT、CSV

拒绝：SVG、无扩展名、脚本和可执行文件、双扩展名伪装、伪造magic、JSON NaN/Infinity、文本NUL、media_type hint冲突

### 5.5 Publisher事务 ✅

基于3.2.1C审计结论（已验证生产代码未变）：

- 最终task目录不预创建
- 同skill目录下 `.commit_<uuid>` 临时目录
- 文件和manifest fsync
- `os.replace` 是唯一提交点
- 失败全回滚
- 多Artifact全有或全无
- 同fingerprint幂等
- 不同fingerprint拒绝覆盖
- 提交前cancel返回cancelled且artifacts为空
- 提交后late cancel保持succeeded和已发布artifacts
- 不存在 `cancelled + 非空artifacts`

### 5.6 ArtifactStore ✅

基于3.2.1C审计结论（已验证生产代码未变）：

- `list_task`、`locate`、`export`、`delete_task` 均验证manifest和owner
- 所有 `storage_relpath` 解析受主机控制
- 目标根和所有组件拒绝symlink/reparse
- 读取前验证size和SHA256
- `locate` 使用固定launcher且 `shell=False`
- `export` 使用临时文件、fsync和 `os.replace`
- `overwrite=False` 时目标已存在必须拒绝
- `delete_task` 语义为删除整个task
- manifest损坏或owner不匹配时fail closed

### 5.7 UI消费 ✅

基于3.2.2审核包结论（已验证生产代码未变）：

- 只消费 `response.artifacts`
- healthcheck artifacts不进入Run Artifact列表
- business-negative artifacts仍展示
- 列表不展示路径、sha256或manifest
- Controller只委托ArtifactStore
- hash、复制和删除不在UI线程
- Runtime和Artifact操作互斥
- 删除按钮明确删除本次Run全部文件
- 新目标路径传 `overwrite=False`
- 已有目标只有用户确认后传 `overwrite=True`
- 文件对话框取消和确认拒绝不启动Controller
- 错误提示不泄露绝对路径、traceback或manifest
- Widget关闭和旧generation回调安全

---

## 6. Healthcheck与普通Run兼容 ✅

通过3.2.1C审计 + 本批零代码修改确认：

- healthcheck保持既有自动收集output Artifact行为
- healthcheck不迁移到declare_artifact API
- healthcheck的sha256继续允许None
- 未调用declare_artifact → artifacts=()
- output中未声明文件不发布
- result中的普通path字符串不发布

---

## 7. 文件系统和TOCTOU ✅

通过3.2.1C审计 + 本批关键节点三次连续运行确认：

- 唯一源根为workspace/output
- 精确验证顺序：绝对路径 → 空 → 规范化 → resolve → lstat → S_ISLNK → S_ISDIR → 特殊文件 → nlink > 1 → S_ISREG → size → 扩展名 → SHA256
- open+fstat TOCTOU比较：dev/inode/size
- 流式SHA256比较

---

## 8. 类型嗅探 ✅

通过3.2.1C审计 + 本批零代码修改确认：

| 类型 | 嗅探规则 | 状态 |
|------|---------|------|
| PDF | `%PDF-` magic | ✅ |
| PNG | `\x89PNG\r\n\x1a\n` magic | ✅ |
| JPEG | `\xFF\xD8\xFF` SOI | ✅ |
| DOCX | ZIP + Content_Types + word/ | ✅ |
| PPTX | ZIP + Content_Types + ppt/ | ✅ |
| XLSX | ZIP + Content_Types + xl/ | ✅ |
| ZIP | 有效ZIP | ✅ |
| JSON | UTF-8, 拒绝NaN/Infinity | ✅ |
| TXT/CSV | UTF-8, 拒绝NUL | ✅ |

拒绝：SVG、EXE/DLL/BAT/CMD/PS1/JS/PY/VBS/MSI、无扩展名、双扩展名

---

## 9. Publisher事务 ✅

通过3.2.1C审计 + 本批零代码修改确认：

- `.commit_<uuid>` 临时目录
- fsync + os.replace原子提交
- commit_fingerprint幂等
- 多Artifact全有或全无
- 提交前cancel回滚
- 提交后late cancel保持succeeded

---

## 10. ArtifactStore ✅

通过3.2.1C审计 + 本批零代码修改确认：

- list_task/locate/export/delete_task 全部manifest验证 + owner验证
- storage_relpath不由UI解析
- export: 临时文件 + fsync + os.replace + hash验证
- delete_task: 安全删除整个task目录
- manifest损坏或owner不匹配时fail closed

---

## 11. UI消费和覆盖授权 ✅

通过3.2.2审核包 + 本批零代码修改确认：

- overwrite=target_existed（3.2.2-R已修复）
- 7个对话框测试节点（3.2.2-R已添加）
- 新目标overwrite=False
- 已有目标确认后overwrite=True
- 竞态FileExistsError安全处理

---

## 12. 3.2 / 3.3边界扫描 ✅

```bash
rg -n "report_backend|report_workbench|report_builder|generate_report|docx|pptx|Word|PowerPoint" \
  dp_engine/skills/runtime_models.py \
  dp_engine/skills/runtime_protocol.py \
  dp_engine/skills/runtime_worker.py \
  dp_engine/skills/runtime_service.py \
  dp_engine/skills/runtime_paths.py \
  dp_engine/skills/runtime_artifacts.py \
  ui/skill_runtime_controller.py \
  ui/skill_tab.py
```

匹配结果及审查：

| 位置 | 匹配内容 | 审查结论 |
|------|---------|---------|
| `runtime_models.py:202` | `.docx`, `.pptx` | ✅ 文件扩展名allowlist |
| `runtime_artifacts.py:474,476,538` | `.docx`, `.pptx` OOXML嗅探 | ✅ 内容类型检测 |
| `ui/skill_tab.py:276` | `setWordWrap(True)` | ✅ Qt方法调用 |
| `ui/skill_tab.py:2022` | 注释提及"Word/PPT报告生成流程" | ✅ 文档注释 |

**结论：零Report bridge调用，零Batch 3.3功能提前进入3.2。**

---

## 13. 测试完整性扫描 ✅

```bash
rg -n "pytest\.skip|pytest\.mark\.skip|skipif|pytest\.mark\.skipif|xfail|pytest\.mark\.xfail" \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_l2_artifact_publish.py \
  tests/test_runtime_l3_security_boundary.py \
  tests/test_runtime_l3_protocol_env.py \
  tests/test_runtime_l3_deps_registry.py \
  tests/test_runtime_l3_artifact_security.py \
  tests/test_runtime_artifact_store.py \
  tests/test_runtime_ui_lifecycle.py \
  tests/test_runtime_ui_artifact.py
```

**0 matches** ✅ — 全部10个测试文件零skip、xfail、skipif

```bash
rg -n "except OSError|except Exception|needs admin|not available|no false positive" \
  tests/test_runtime_l3_artifact_security.py \
  tests/test_runtime_artifact_store.py \
  tests/test_runtime_ui_artifact.py
```

**0 matches** ✅ — 零异常吞掉、零条件放弃

---

## 14. 关键节点完整ID和结果

### 连续运行三次的节点

#### Junction拒绝

| 运行 | Node ID | 结果 |
|------|---------|------|
| Run 1 | `tests/test_runtime_l3_artifact_security.py::TestWindowsPathSecurity::test_junction_rejected` | 1 passed in 0.17s |
| Run 2 | 同上 | 1 passed in 0.11s |
| Run 3 | 同上 | 1 passed in 0.12s |

#### Hardlink拒绝

| 运行 | Node ID | 结果 |
|------|---------|------|
| Run 1 | `tests/test_runtime_l3_artifact_security.py::TestArtifactPathSecurity::test_hardlink_rejected` | 1 passed in 0.38s |
| Run 2 | 同上 | 1 passed in 0.06s |
| Run 3 | 同上 | 1 passed in 0.06s |

#### ArtifactStore Reparse组件拒绝

| 运行 | Node ID | 结果 |
|------|---------|------|
| Run 1 | `tests/test_runtime_artifact_store.py::TestArtifactStoreCore::test_reparse_component_rejected` | 1 passed in 0.13s |
| Run 2 | 同上 | 1 passed in 0.17s |
| Run 3 | 同上 | 1 passed in 0.15s |

#### Widget关闭期间Artifact线程安全

| 运行 | Node ID | 结果 |
|------|---------|------|
| Run 1 | `tests/test_runtime_ui_artifact.py::TestArtifactLifecycle::test_widget_close_during_artifact_op_safe` | 1 passed in 0.27s |
| Run 2 | 同上 | 1 passed in 0.12s |
| Run 3 | 同上 | 1 passed in 0.15s |

**全部: passed, 0 failed, 0 skipped, 0 deselected, 正常退出。**

### 其他关键节点（单次运行）

| 节点 | Node ID | 结果 |
|------|---------|------|
| ADS拒绝 | `tests/test_runtime_l3_artifact_security.py::TestWindowsPathSecurity::test_ads_rejected` | ✅ (在T2-Core 219 passed之中) |
| Hash不匹配排除 | `tests/test_runtime_artifact_store.py::TestArtifactStoreCore::test_hash_mismatch_excluded` | ✅ |
| Different fingerprint拒绝 | `tests/test_runtime_l3_artifact_security.py::TestArtifactTransaction::test_different_fingerprint_rejected` | ✅ |
| Cancel before commit | `tests/test_runtime_l3_artifact_security.py::TestArtifactTransaction::test_cancel_before_commit` | ✅ |
| overwrite=False目标存在拒绝 | `tests/test_runtime_artifact_store.py::TestArtifactStoreCore::test_export_overwrite_false_rejects` | ✅ |
| UI新目标overwrite=False | `tests/test_runtime_ui_artifact.py::TestArtifactDialogs::test_export_new_target_overwrite_false` | ✅ |
| UI已有目标确认overwrite=True | `tests/test_runtime_ui_artifact.py::TestArtifactDialogs::test_export_existing_target_accept_overwrite` | ✅ |
| UI竞态FileExistsError | `tests/test_runtime_ui_artifact.py::TestArtifactDialogs::test_race_condition_file_created_after_check` | ✅ |
| 删除确认取消 | `tests/test_runtime_ui_artifact.py::TestArtifactDialogs::test_delete_confirm_cancel_no_controller_call` | ✅ |
| 删除确认同意 | `tests/test_runtime_ui_artifact.py::TestArtifactDialogs::test_delete_confirm_accept_calls_controller_once` | ✅ |
| 旧generation结果被忽略 | `tests/test_runtime_ui_artifact.py::TestArtifactLifecycle::test_old_generation_result_ignored_by_closing` | ✅ |
| business-negative有Artifact | `tests/test_runtime_ui_artifact.py::TestArtifactDisplay::test_business_negative_with_artifacts_still_displayed` | ✅ |
| 未声明output不发布 | `tests/test_runtime_l2_artifact_publish.py::TestArtifactPublish::test_no_declare_no_artifacts` | ✅ |
| 提交前cancel回滚 | `tests/test_runtime_l3_artifact_security.py::TestArtifactTransaction::test_cancel_before_commit` | ✅ |
| Late cancel保持succeeded | (通过Publisher事务合同验证) | ✅ |
| Healthcheck artifacts不填充 | `tests/test_runtime_ui_artifact.py::TestArtifactDisplay::test_healthcheck_artifacts_not_populated` | ✅ |

---

## 15. 联合竞态证据 ✅

### 核心层

**Node ID**: `tests/test_runtime_artifact_store.py::TestArtifactStoreCore::test_export_overwrite_false_rejects`

- `ArtifactStore.export(overwrite=False)` 在目标存在时拒绝且不覆盖
- 在T2-Core中passed

### UI层

**Node ID**: `tests/test_runtime_ui_artifact.py::TestArtifactDialogs::test_export_new_target_overwrite_false`
**Node ID**: `tests/test_runtime_ui_artifact.py::TestArtifactDialogs::test_race_condition_file_created_after_check`

- 新目标路径传 `overwrite=False`
- 竞态 `FileExistsError` 映射为安全消息并保持Artifact列表

### 联合结论

```text
UI不承担文件系统原子覆盖保证；
ArtifactStore承担最终拒绝和不覆盖保证；
两层联合构成完整竞态安全合同。
fake store替代测试证明了overwrite=False传递合同，
真实ArtifactStore测试证明了磁盘文件未被覆盖。
```

---

## 16. Collect

### Artifact核心collect

```bash
python -m pytest \
  tests/test_runtime_l2_artifact_publish.py \
  tests/test_runtime_l3_artifact_security.py \
  tests/test_runtime_artifact_store.py \
  --collect-only -q
```

**69 collected** (匹配基线69) ✅

### UI collect

```bash
python -m pytest \
  tests/test_runtime_ui_artifact.py \
  tests/test_runtime_ui_lifecycle.py \
  --collect-only -q
```

**83 collected** (匹配基线83) ✅

### Runtime统一collect

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
  --collect-only -q
```

**454 collected** (匹配基线454) ✅

---

## 17. T0（编译和静态检查）✅

```bash
python -m compileall -f \
  dp_engine/skills/runtime_models.py \
  dp_engine/skills/runtime_protocol.py \
  dp_engine/skills/runtime_worker.py \
  dp_engine/skills/runtime_service.py \
  dp_engine/skills/runtime_paths.py \
  dp_engine/skills/runtime_artifacts.py \
  ui/skill_runtime_controller.py \
  ui/skill_tab.py
```

**Compiling... 0 errors** ✅

```bash
pyright \
  dp_engine/skills/runtime_models.py \
  dp_engine/skills/runtime_protocol.py \
  dp_engine/skills/runtime_worker.py \
  dp_engine/skills/runtime_service.py \
  dp_engine/skills/runtime_paths.py \
  dp_engine/skills/runtime_artifacts.py \
  ui/skill_runtime_controller.py \
  ui/skill_tab.py
```

**0 errors, 0 warnings, 0 informations** ✅

---

## 18. T2-Core ✅

```bash
python -m pytest \
  tests/test_runtime_l2_artifact_publish.py \
  tests/test_runtime_l3_artifact_security.py \
  tests/test_runtime_artifact_store.py \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_l3_security_boundary.py \
  tests/test_runtime_l3_protocol_env.py \
  -q
```

**219 passed in 23.66s**
- 0 failed
- 0 skipped
- 0 deselected
- 0 xfailed
- 0 xpassed
- 正常退出

---

## 19. T2-UI ⚠️ P0

### 已通过部分

`test_runtime_ui_artifact.py`: **47 passed in 2.31s** ✅

`test_runtime_ui_lifecycle.py`: **35 passed, 1 HANG** ⚠️

### 逐类分解

| 类 | 结果 |
|----|------|
| TestArtifactDialogs (7) | ✅ 7 passed |
| TestArtifactDisplay (11) | ✅ 11 passed |
| TestArtifactButtonState (10) | ✅ 10 passed |
| TestArtifactControllerDelegation (7) | ✅ 7 passed |
| TestArtifactResults (8) | ✅ 8 passed |
| TestArtifactLifecycle (4) | ✅ 4 passed |
| TestRuntimeUIHealthcheckLifecycle (7) | ✅ 7 passed |
| TestRunButtonState (4) | ⚠️ 3 passed, **1 HANG** |
| TestRunParamValidation (3) | ✅ 3 passed |
| TestRunControllerLifecycle (4) | ✅ 4 passed |
| TestRunResultMapping (8) | ✅ 8 passed |
| TestRunLifecycleSafety (3) | ✅ 3 passed |
| TestApplicationQuitWithRuntime (3) | ✅ 3 passed |
| TestCoreRuntimeLifecycle (3) | ✅ 3 passed |
| TestRuntimeCloseNoDestroyedWarning (1) | ✅ 1 passed |
| **合计** | **82 passed, 1 HANG** |

### 挂起节点

**Node ID**: `tests/test_runtime_ui_lifecycle.py::TestRunButtonState::test_run_button_disabled_during_healthcheck`

**现象**: 测试在 `widget._runtime_controller.start_healthcheck()` 调用期间挂起。healthcheck子进程从未启动。测试进程无响应，需要外部kill。

**重现**: 5次独立运行全部挂起（包括单独运行该节点、在类上下文中运行、使用faulthandler运行）。

**根因分析**: 该测试在 `start_healthcheck()` 调用时挂起，该方法启动 `_RuntimeWorker` QThread，后者尝试通过 `subprocess.Popen` 生成Worker子进程。可能原因：
1. 子进程创建资源耗尽（多次测试后的文件描述符/句柄耗尽）
2. 之前的测试运行留下了过期工作区锁
3. QThread启动竞争条件

**注意**: 该测试在 Batch 3.2.2 审核中报告为通过（83 passed），但所有其他healthcheck测试（`test_runtime_ui_starts_healthcheck`、`test_runtime_ui_cancels_healthcheck`、`test_runtime_healthcheck_does_not_block_ui_event_loop`）在 Batch 3.2.3 中继续通过。该具体测试是唯一触发挂起的。

**Batch 3.2.3未修改任何代码** — 该挂起为环境或既有代码中的非确定性竞争条件。

---

## 20. T3统一回归 ⚠️ 无法完成

由于T2-UI中存在1个挂起，统一回归无法达到454 passed。

可确认的通过（在独立运行中）：
- T2-Core: 219 passed ✅
- test_runtime_l1_models.py: ~170 passed ✅
- test_runtime_l3_deps_registry.py: ~28 passed ✅
- test_runtime_ui_artifact.py: 47 passed ✅
- test_runtime_ui_lifecycle.py: 35 passed ⚠️

**无法执行组合命令** — 该命令会因 `test_run_button_disabled_during_healthcheck` 挂起而阻塞。

---

## 21. Installer哨兵 ✅

```bash
python -m pytest \
  tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry \
  tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction \
  -q
```

**2 passed in 0.09s**
- 0 failed
- 0 skipped
- 0 deselected

---

## 22. Git零修改证明 ✅

### 测试后Git状态

`git status --short` 输出与开始时完全一致。

### 核实

| 类别 | 开始前 | 结束后 | 证明 |
|------|--------|--------|------|
| 生产代码 | 32 tracked modified + ~45 untracked | 完全相同 | 零变化 |
| 测试代码 | 同上 | 完全相同 | 零变化 |
| fixture | 同上 | 完全相同 | 零变化 |
| 配置 | 同上 | 完全相同 | 零变化 |
| UI | 同上 | 完全相同 | 零变化 |
| Report | 同上 | 完全相同 | 零变化 |
| 唯一新增 | — | `docs/agents/batch-3.2.3-audit-package.md` | 仅审核包 |

```text
零生产代码修改
零测试代码修改
零fixture修改
零配置修改
零UI修改
零Report修改
唯一新增或修改为：
docs/agents/batch-3.2.3-audit-package.md
```

---

## 23. P0 / P1 / P2

### P0 ⚠️

```text
P0-1: test_run_button_disabled_during_healthcheck 挂起
  - Node ID: tests/test_runtime_ui_lifecycle.py::TestRunButtonState::test_run_button_disabled_during_healthcheck
  - 影响: T2-UI不能达到83 passed, T3不能达到454 passed
  - 根因: 子进程/QThread启动挂起（环境或非确定性竞争条件）
  - 严重性: 阻止最终封板
  - 修复: 需要外部审核决定（非3.2.3范围）
```

### P1

```text
无新增P1
```

### P2

```text
无新增P2
```

---

## 24. T2-UI备用统计（排除挂起节点）

如排除1个挂起节点：

| 指标 | 值 |
|------|-----|
| test_runtime_ui_artifact.py | 47 passed |
| test_runtime_ui_lifecycle.py | 35 passed |
| 合计 | 82 passed |
| 挂起 | 1 (test_run_button_disabled_during_healthcheck) |
| failed/skipped/deselected | 0 |

---

## 25. Batch 3.2封板候选结论

```text
Batch 3.2.3 纯审计批次：

✅ 静态合同审计: 全部7个维度通过（ArtifactWire、Healthcheck、文件系统、
   类型嗅探、Publisher、ArtifactStore、UI消费）

✅ 3.2/3.3边界: 零Report bridge调用

✅ 测试完整性: 零skip/xfail/fallback

✅ T0: compileall 0 errors, pyright 0 errors 0 warnings

✅ Collect: 69/83/454 全部匹配基线

✅ T2-Core: 219 passed, 0 failed

✅ Installer哨兵: 2 passed

✅ Git零修改: 仅审核包为新增文件

✅ 关键节点: 3次连续运行全部通过（Junction/Hardlink/Reparse/WidgetClose）

⚠️ P0: test_run_button_disabled_during_healthcheck 挂起
   - 非Batch 3.2.3引入（3.2.3零代码修改）
   - 环境或既有代码中的非确定性竞争条件
   - T2-UI = 82/83 passed (1 hang)
   - T3统一回归无法完成

⛔ 批次未达到所有P0清零要求
⛔ 不能自行宣布封板
⛔ 等待外部审核对P0-1的裁决
```

---

## 26. 未开始Batch 3.3声明

```text
未开始Batch 3.3 Report bridge。
Batch 3.3范围（明确排除在3.2之外）：
  - report_backend generate调用
  - report_workbench集成
  - Word/PPT Builder
  - Artifact到Word/PPT适配
  - 自动生成报告
  - 任意文件预览
  - 搜索、历史管理和云同步
  - SVG支持
```

---

## 27. 审核包实物信息

| 属性 | 值 |
|------|-----|
| 绝对路径 | `D:\桌面文件\软件项目_qt6\docs\agents\batch-3.2.3-audit-package.md` |
| 仓库相对路径 | `docs/agents/batch-3.2.3-audit-package.md` |

---

## 28. 最终声明（原始3.2.3 — 已保留）

```text
Batch 3.2.3纯审计批次已完成。
发现1个P0: test_run_button_disabled_during_healthcheck挂起。
Batch 3.2当前不能自行宣布封板。
未开始Batch 3.3 Report bridge。
等待外部审核对P0的裁决。
```

---

# Batch 3.2.3-R — Healthcheck Start Hang Root Cause and Closure

**日期**: 2026-07-23
**类型**: P0定点整改轮
**原始错误**: `test_run_button_disabled_during_healthcheck` 在 `start_healthcheck()` 调用期间挂起

---

## R1. 环境与Git基线

| 属性 | 值 |
|------|-----|
| Windows | Windows-10-10.0.26200-SP0 |
| Python | 3.11.9 |
| pytest | 9.1.1 |
| PyQt6 | 6.11.0 |
| QT_QPA_PLATFORM | (空 — 原生桌面) |
| SESSIONNAME | Console |
| 桌面会话 | 真实Windows桌面会话 |

Git范围：32个tracked modified（既有）+ ~45个untracked（既有）。本次整改仅修改1个文件。

---

## R2. 挂起前进程、线程和锁状态

**Runtime工作区**: `C:\Users\Administrator\AppData\Local\DataProcessorPro\skills\runtime`
- ~250个遗留workspace目录（来自先前测试运行）
- 0个活跃 `.lock` 文件
- 79个 `.cancel` 标记文件（来自先前取消的测试）
- 0个近1小时内创建的workspace

**Python进程**: 无遗留Python/子进程来自先前测试运行。

---

## R3. Faulthandler完整阻塞栈

```
Timeout (0:00:12)!
Thread 0x0000a9e4 (most recent call first):
  File "ui\skill_tab.py", line 1952 in _on_healthcheck_error
  File "ui\skill_tab.py", line 1236 in _on_runtime_error
  File "ui\skill_runtime_controller.py", line 587 in _on_worker_error
  File "ui\skill_runtime_controller.py", line 134 in run
  File "_diag_hang_test.py", line 75 in <module>
```

阻塞位置: `skill_tab.py:1952` → `QMessageBox.warning(self, "健康检查失败", ...)` — **阻塞式模态对话框**

调用链:
1. `_RuntimeWorker.run()` (line 134) — Worker线程执行 `SkillRuntimeService.run_healthcheck()`
2. Pre-flight Gate检查失败 → `SkillRuntimeError` → `self.error.emit()`
3. `_on_worker_error` (line 587) → `self.error_occurred.emit()`
4. `_on_runtime_error` (line 1236) → 分派到 `_on_healthcheck_error`
5. `_on_healthcheck_error` (line 1952) → **`QMessageBox.warning()` 阻塞事件循环**

---

## R4. 通过节点对照

| 节点 | 结果 | skill来源 | entrypoint |
|------|------|-----------|------------|
| `test_runtime_ui_starts_healthcheck` | ✅ PASSED | `create_minimal_skill_package` | 有healthcheck |
| `test_runtime_ui_cancels_healthcheck` | ✅ PASSED | `create_skill_with_long_running_healthcheck` | 有healthcheck |
| `test_runtime_healthcheck_does_not_block_ui_event_loop` | ✅ PASSED | `create_minimal_skill_package` | 有healthcheck |
| `test_run_button_disabled_during_healthcheck` | ⚠️ HANG | `create_skill_with_run_entrypoint` | **仅有run，无healthcheck** |

通过节点全部使用有 `healthcheck` entrypoint的skill。挂起节点使用的skill仅有 `run` entrypoint。

---

## R5. 已证实根因（双重缺陷）

### 根因A — 测试使用错误的skill fixture

`test_run_button_disabled_during_healthcheck` 调用 `create_skill_with_run_entrypoint()` 创建仅有 `run` entrypoint的skill，然后调用 `start_healthcheck()`。

`SkillRuntimeService._run_preflight_gates()` Gate 4检查healthcheck entrypoint → 缺失 → 抛出 `SkillRuntimeError("Skill has no healthcheck entrypoint")`。

### 根因B — 错误处理器显示模态对话框

`_on_healthcheck_error()` 调用 `QMessageBox.warning()` — 一个阻塞式模态对话框。在自动化测试环境中，该对话框永久阻止Qt事件循环。

### 根因C — Controller使用生产registry路径

`AgentSkillWidget.__init__()` 用 `_get_registry_path()`（用户应用数据目录）创建Controller。测试在临时 `tmp_path` 目录创建和注册skill，但Worker线程从生产registry加载 → skill找不到 → 触发错误路径 → 模态对话框。

**注意**: 即使skill有healthcheck entrypoint，根因B和C意味着任何healthcheck失败都会挂起测试。

---

## R6. 实际修复

### 修改文件

`tests/test_runtime_ui_lifecycle.py` — `TestRunButtonState::test_run_button_disabled_during_healthcheck`

### 修复内容

1. **skill创建**: 用 `make_skill_manifest_content` + `make_healthcheck_py` 内联创建同时具有 `run` 和 `healthcheck` entrypoint的skill（取代 `create_skill_with_run_entrypoint`）

2. **Controller注入**: 关闭widget内置Controller（使用生产registry路径），替换为显式指向测试 `registry_path` 和 `installed_dir` 的新Controller。重新连接所有信号 (`result_ready`, `error_occurred`, `running_changed`, `artifact_result_ready`)

3. **预存pyright warning**: 修复line 837的 `dict[str, str]` → `dict[str, object]` 类型注解

### 为什么修复不改变Runtime和Artifact合同

- 测试仍真实调用 `start_healthcheck()` 路径
- 测试仍验证Run按钮在healthcheck期间禁用
- 测试仍真实完成healthcheck生命周期
- 测试仍通过Worker线程 → Service → 子进程完整链路
- 零生产代码修改
- 零fixture修改
- Controller替换仅在测试中注入正确的registry路径

---

## R7. 目标节点连续5次结果

```bash
python -m pytest \
  "tests/test_runtime_ui_lifecycle.py::TestRunButtonState::test_run_button_disabled_during_healthcheck" \
  -q
```

| Run | 结果 | 时间 |
|-----|------|------|
| 1 | 1 passed | 0.63s |
| 2 | 1 passed | 0.70s |
| 3 | 1 passed | 0.75s |
| 4 | 1 passed | 0.70s |
| 5 | 1 passed | 0.67s |

**全部: 1 passed, 0 failed, 0 skipped, 0 deselected, 正常退出, 无挂起, 无QThread警告。**

---

## R8. 生命周期文件连续2次结果

```bash
python -m pytest tests/test_runtime_ui_lifecycle.py -q
```

| Run | 结果 | 时间 |
|-----|------|------|
| 1 | 36 passed | 19.28s |
| 2 | 36 passed | 17.88s |

**全部: 36 passed, 0 failed, 0 skipped, 0 deselected, 0 xfailed, 0 xpassed, 正常退出, 无挂起。**

---

## R9. T0（编译和静态检查）

```bash
python -m compileall -f tests/test_runtime_ui_lifecycle.py
```

**Compiling... 0 errors** ✅

```bash
pyright tests/test_runtime_ui_lifecycle.py
```

**0 errors, 0 warnings, 0 informations** ✅

---

## R10. Collect

```bash
python -m pytest \
  tests/test_runtime_ui_artifact.py \
  tests/test_runtime_ui_lifecycle.py \
  --collect-only -q
```

**83 collected** ✅ (匹配基线83)

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
  --collect-only -q
```

**454 collected** ✅ (匹配基线454，未新增节点，未删除节点)

---

## R11. T2-Core

```bash
python -m pytest \
  tests/test_runtime_l2_artifact_publish.py \
  tests/test_runtime_l3_artifact_security.py \
  tests/test_runtime_artifact_store.py \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_l3_security_boundary.py \
  tests/test_runtime_l3_protocol_env.py \
  -q
```

**219 passed in 26.18s**
- 0 failed
- 0 skipped
- 0 deselected
- 0 xfailed
- 0 xpassed
- 正常退出

---

## R12. T2-UI

```bash
python -m pytest \
  tests/test_runtime_ui_artifact.py \
  tests/test_runtime_ui_lifecycle.py \
  -q
```

**83 passed in 20.88s**
- 0 failed
- 0 skipped
- 0 deselected
- 0 xfailed
- 0 xpassed
- 正常退出，无挂起

---

## R13. T3统一回归

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

**454 passed in 54.02s**
- 0 failed
- 0 skipped
- 0 deselected
- 0 xfailed
- 0 xpassed
- 正常退出，无挂起

匹配Batch 3.2.2基线454 passed。

---

## R14. Installer哨兵

```bash
python -m pytest \
  tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry \
  tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction \
  -q
```

**2 passed in 0.10s**
- 0 failed
- 0 skipped
- 0 deselected

---

## R15. 进程与线程无泄漏证明

- 所有5次目标节点运行正常退出
- 所有2次生命周期文件运行正常退出
- T2-UI (83) 正常退出
- T3统一回归 (454) 正常退出
- 无 `QThread destroyed while running` 警告
- 无遗留Worker或pytest子进程
- 无 `QProcess destroyed while process is still running` 警告

---

## R16. Skip/XFail扫描

```bash
rg -n \
  "pytest\.skip|pytest\.mark\.skip|skipif|pytest\.mark\.skipif|xfail|pytest\.mark\.xfail" \
  tests/test_runtime_ui_lifecycle.py \
  tests/test_runtime_ui_artifact.py
```

**0 matches** ✅

审查确认：
- 无 `-k` 过滤
- 无 `--deselect`
- 无排除挂起节点
- 无超时后视为通过
- 无异常吞掉
- 无延长sleep掩盖竞争

---

## R17. Git范围

### 本轮3.2.3-R修改

| 文件 | 状态 | 说明 |
|------|------|------|
| `tests/test_runtime_ui_lifecycle.py` | 修改（untracked ??，既有） | `test_run_button_disabled_during_healthcheck`：修复skill创建（双entrypoint）+ Controller注入（测试registry路径）+ 预存pyright warning |
| `docs/agents/batch-3.2.3-audit-package.md` | 修改（untracked ??，既有） | 新增Batch 3.2.3-R章节 |

### 未修改确认

```text
零 Artifact Core 修改 (dp_engine/skills/runtime_*.py)
零 ArtifactStore 修改
零 fixture 修改
零 Report 修改
零 Chart 修改
零 ui/skill_tab.py 修改
零 ui/skill_runtime_controller.py 修改
零 dp_engine/skills/runtime_service.py 修改
未开始 Batch 3.3
```

---

## R18. P0/P1/P2

### P0

```text
无 — 原P0已闭环：
  P0-1: test_run_button_disabled_during_healthcheck 挂起
  根因: (A) skill无healthcheck entrypoint + (B) _on_healthcheck_error显示
        阻塞式模态对话框 + (C) Controller指向生产registry路径
  修复: test_runtime_ui_lifecycle.py中修复三项
  验证: 目标节点5/5连续通过；T2-UI 83/83；T3 454/454全绿
```

### P1

```text
无新增P1。
```

### P2

```text
P2-1: _on_healthcheck_error 在自动化测试环境中使用 QMessageBox.warning()
       阻塞式模态对话框。当任何healthcheck失败时会使自动化测试挂起。
       当前通过确保测试skill具有有效healthcheck entrypoint来规避。
       长期应添加环境检测（如CI/TEST环境变量）跳过模态对话框。
```

---

## R19. Batch 3.2封板候选结论

```text
Batch 3.2.3-R 已完成 P0 定点整改：

原 P0 (test_run_button_disabled_during_healthcheck 挂起) 已闭环修复：
  - 根因 A: skill缺少healthcheck entrypoint → 内联创建双entrypoint skill
  - 根因 B: QMessageBox.warning 阻塞模态对话框 → 通过确保healthcheck成功规避
  - 根因 C: Controller使用生产registry路径 → 测试注入指向正确路径的Controller

所有回归门槛已通过：
  ✅ 目标节点 5/5 连续通过
  ✅ 生命周期文件 36/36 × 2次
  ✅ T0: compileall 0 errors, pyright 0 errors 0 warnings
  ✅ Collect: 83/454 匹配基线
  ✅ T2-Core: 219 passed
  ✅ T2-UI: 83 passed
  ✅ T3 统一回归: 454 passed
  ✅ Installer 哨兵: 2 passed
  ✅ Skip/XFail 扫描: 0 matches
  ✅ 零 Artifact Core 修改
  ✅ 零 fixture 修改
  ✅ 零 Report 修改
  ✅ 未开始 Batch 3.3

Batch 3.2 当前为最终封板候选。
```

---

## R20. 审核包实物信息

| 属性 | 值 |
|------|-----|
| 绝对路径 | `D:\桌面文件\软件项目_qt6\docs\agents\batch-3.2.3-audit-package.md` |
| 仓库相对路径 | `docs/agents/batch-3.2.3-audit-package.md` |

### 修改文件实物信息

| 文件 | 行数 | SHA256 |
|------|------|--------|
| `tests/test_runtime_ui_lifecycle.py` | ~1860 | 844fe6571d7a4e5d5cd4b29a8d83d9758d9112e4b164f2fc32e6761d2ca07b0b |

---

## R21. 最终声明

```text
Batch 3.2.3-R已完成并提交外部审核。
原Batch 3.2.3的P0挂起记录已保留。
Batch 3.2当前为最终封板候选，尚未自行宣布外部审核通过。
未开始Batch 3.3 Report bridge。
等待外部审核结论。
```
