# Batch 3.0.6 — 审核包（整改后提交外部审核）

**日期**: 2026-07-22
**分支**: llama-cpp
**状态**: Batch 3.0.6 已完成整改，提交外部审核。
第三批 3.0 尚未正式封板。
在外部审核明确通过前，不进入 Batch 3.1。

---

## A. 正式状态

```text
状态：Batch 3.0.6 已完成整改，提交外部审核。
第三批 3.0 尚未正式封板。
在外部审核明确通过前，不进入 Batch 3.1。
```

禁止使用的旧措辞（审核包 v1 错误使用了以下措辞，已全部删除）：

```text
✅ 第三批 3.0 正式封板，可进入 3.1 规划。
结论: ✅ 第三批 3.0 正式封板，可进入 3.1 规划。
```

---

## B. 计数口径（已纠正）

### 旧审核包错误

| 项目 | 旧值（错误） | 新值（正确） | 说明 |
|------|-------------|-------------|------|
| 新增测试 node | 22 | **20** | 逐项 Git diff + 测试方法验证 |
| 整改证据 node | 未区分 | **22** | 20 新增 + 2 既有 (#38) |
| L3 security 新增 | 7 | **4** | write-output×2, registry-write, socket-connect |
| 新增 fixture | 12 | **10** | Batch 3.0.6 标记的 10 个 fixture 函数 |

### 逐项计数明细

**本批新增测试 node：20 个**

| # | 文件 | 类 | 测试方法 | 
|---|------|-----|---------|
| 1 | test_runtime_l2_subprocess.py | TestHealthcheckEntrypointErrors | test_healthcheck_entrypoint_file_missing |
| 2 | test_runtime_l2_subprocess.py | TestHealthcheckEntrypointErrors | test_healthcheck_function_missing |
| 3 | test_runtime_l2_subprocess.py | TestHealthcheckEntrypointErrors | test_healthcheck_function_not_callable |
| 4 | test_runtime_l2_subprocess.py | TestHealthcheckEntrypointErrors | test_healthcheck_return_value_not_dict |
| 5 | test_runtime_l2_subprocess.py | TestHealthcheckEntrypointErrors | test_healthcheck_healthy_field_not_bool |
| 6 | test_runtime_l3_security_boundary.py | TestSkillCanWriteRuntimeOutput | test_skill_can_write_runtime_output |
| 7 | test_runtime_l3_security_boundary.py | TestSkillCanWriteRuntimeOutput | test_skill_can_write_and_read_output_artifact |
| 8 | test_runtime_l3_security_boundary.py | TestSkillCannotModifyRegistry | test_skill_cannot_modify_registry |
| 9 | test_runtime_l3_security_boundary.py | TestMaliciousTopLevelSocketConnect | test_top_level_socket_connect_blocked |
| 10 | test_runtime_l3_protocol_env.py | TestSecretLeakageInWorker | test_secrets_not_in_result_json |
| 11 | test_runtime_l3_protocol_env.py | TestSecretLeakageInWorker | test_secrets_not_in_runtime_log |
| 12 | test_runtime_l3_deps_registry.py | TestDependencyVersionMismatch | test_dependency_version_mismatch_prevents_process_start |
| 13 | test_runtime_l3_deps_registry.py | TestDependencyCheckerNeverInstalls | test_dependency_checker_never_invokes_package_installer |
| 14 | test_runtime_l3_deps_registry.py | TestDependencyCheckerNeverInstalls | test_dependency_checker_uses_importlib_only |
| 15 | test_runtime_l3_deps_registry.py | TestHealthcheckUnhealthy | test_healthcheck_false_updates_unhealthy |
| 16 | test_runtime_l3_deps_registry.py | TestRegistrySaveFailureRestore | test_registry_save_failure_restores_health_snapshot |
| 17 | test_runtime_l3_deps_registry.py | TestHealthcheckDoesNotAutoEnable | test_successful_healthcheck_does_not_enable_disabled_skill |
| 18 | test_runtime_ui_lifecycle.py | TestRuntimeCloseNoDestroyedWarning | test_runtime_close_has_no_process_or_thread_destroyed_warning |
| 19 | test_skill_package.py | TestSafeCopyDirectory | test_safe_copy_directory_rejects_symlink_via_fake_entry |
| 20 | test_skill_package.py | TestInstaller | test_install_rejects_symlink_via_fake_entry_full_transaction |

**本轮整改完整证据 node：22 个**（20 新增 + 2 既有）

### #38 四个证据 node

| node ID | 来源 | 说明 |
|---------|------|------|
| test_secrets_not_in_worker_stdout_stderr | 既有 | 批次前已存在，继续作为证据 |
| test_secrets_not_in_result_json | **本批新增** | 独立验证 result.json 不含 secret |
| test_secrets_not_in_runtime_log | **本批新增** | 独立验证 runtime.log 不含 secret |
| test_secrets_not_in_error_messages | 既有 | 批次前已存在，继续作为证据 |

### 新增 fixture：10 个

全部位于 `tests/fixtures/runtime_fixtures.py`，标记为 Batch 3.0.6：

1. `create_skill_with_missing_entrypoint_file` — #10 入口文件缺失
2. `create_skill_with_missing_function` — #12 函数不存在
3. `create_skill_with_non_callable_entrypoint` — #13 不可调用
4. `create_skill_returning_non_dict` — #14 返回值非 dict
5. `create_skill_returning_non_bool_healthy` — #15 healthy 非 bool
6. `create_skill_returning_unhealthy` — #55 healthy=False
7. `create_skill_that_writes_to_output` — #23 写 output
8. `create_malicious_top_level_registry_write` — #28 写 Registry
9. `create_malicious_top_level_socket_connect` — #30 socket connect
10. `create_skill_that_reads_output_artifact` — #23 端到端闭环

---

## C. 修正后的 67 项覆盖矩阵

每项使用完整 pytest node ID，语义与验收场景一致。

### 1–7: 协议模型

| # | 验收场景 | pytest node ID | 文件 | 结果 |
|---|---------|---------------|------|------|
| 1 | 合法 request roundtrip | TestRuntimeRequest::test_valid_request_roundtrip | test_runtime_l1_models.py | PASS |
| 2 | 非法 protocol version | TestRuntimeRequest::test_invalid_protocol_version | test_runtime_l1_models.py | PASS |
| 3 | operation 非 healthcheck | TestRuntimeRequest::test_operation_not_healthcheck | test_runtime_l1_models.py | PASS |
| 4 | task_id 不匹配 | TestProtocolTaskIdValidation::test_response_task_id_mismatch | test_runtime_l3_protocol_env.py | PASS |
| 5 | result 超限 | TestAtomicIO::test_read_response_too_large | test_runtime_l1_models.py | PASS |
| 6 | artifact 越界 | TestArtifactPathValidation::test_artifact_dot_dot_rejected | test_runtime_l3_protocol_env.py | PASS |
| 7 | response schema 非法 | TestResultSchemaValidation::test_result_not_valid_json | test_runtime_l3_protocol_env.py | PASS |

### 8–17: Entrypoint

| # | 验收场景 | pytest node ID | 文件 | 结果 |
|---|---------|---------------|------|------|
| 8 | 合法 healthcheck | TestHealthcheckHappyPath::test_healthy_skill | test_runtime_l2_subprocess.py | PASS |
| 9 | 缺少 healthcheck | TestHealthcheckErrors::test_missing_healthcheck_entrypoint | test_runtime_l2_subprocess.py | PASS |
| 10 | entrypoint 文件不存在 | TestHealthcheckEntrypointErrors::test_healthcheck_entrypoint_file_missing | test_runtime_l2_subprocess.py | PASS |
| 11 | entrypoint 路径逃逸 | TestRuntimeRequest::test_entrypoint_path_escape | test_runtime_l1_models.py | PASS |
| 12 | function 不存在 | TestHealthcheckEntrypointErrors::test_healthcheck_function_missing | test_runtime_l2_subprocess.py | PASS |
| 13 | function 不可调用 | TestHealthcheckEntrypointErrors::test_healthcheck_function_not_callable | test_runtime_l2_subprocess.py | PASS |
| 14 | 返回值不是 dict | TestHealthcheckEntrypointErrors::test_healthcheck_return_value_not_dict | test_runtime_l2_subprocess.py | PASS |
| 15 | healthy 不是 bool | TestHealthcheckEntrypointErrors::test_healthcheck_healthy_field_not_bool | test_runtime_l2_subprocess.py | PASS |
| 16 | 返回值过大 | TestProtocolResultSizeLimit::test_result_too_large_rejected | test_runtime_l3_protocol_env.py | PASS |
| 17 | healthcheck 抛异常 | TestTimeoutCancelCrashReal::test_nonzero_worker_exit | test_runtime_l2_subprocess.py | PASS |

### 18–21: 父进程隔离

| # | 验收场景 | pytest node ID | 文件 | 结果 |
|---|---------|---------------|------|------|
| 18 | 父进程不加载技能 | TestParentIsolation::test_parent_does_not_import_skill | test_runtime_l2_subprocess.py | PASS |
| 19 | 技能仅在 Worker PID 加载 | TestHealthcheckHappyPath::test_healthcheck_pid_differs_from_parent | test_runtime_l2_subprocess.py | PASS |
| 20 | Worker PID 不同 | TestCoreRuntimeLifecycle::test_runtime_worker_process_pid_differs | test_runtime_ui_lifecycle.py | PASS |
| 21 | 顶层副作用不进入父进程 | TestAuditHookInstallOrder::test_permissions_installed_before_exec_module | test_runtime_l3_security_boundary.py | PASS |

### 22–28: 文件权限

| # | 验收场景 | pytest node ID | 文件 | 结果 |
|---|---------|---------------|------|------|
| 22 | 读取自身技能允许 | TestSkillCanReadOwnFiles::test_skill_can_read_own_files | test_runtime_l3_security_boundary.py | PASS |
| 23 | 写 output 允许 | TestSkillCanWriteRuntimeOutput::test_skill_can_write_runtime_output | test_runtime_l3_security_boundary.py | PASS |
| 24 | 写 installed 拒绝 | TestMaliciousTopLevelWrite::test_top_level_write_blocked_check_not_called | test_runtime_l3_security_boundary.py | PASS |
| 25 | 写 workspace 外拒绝 | TestMaliciousTopLevelWrite::test_top_level_write_check_never_called | test_runtime_l3_security_boundary.py | PASS |
| 26 | 读取 workspace 外敏感文件拒绝 | TestMaliciousTopLevelExternalRead::test_top_level_external_read_blocked | test_runtime_l3_security_boundary.py | PASS |
| 27 | symlink 逃逸拒绝 | TestReadSymlinkEscapeBlocked::test_read_symlink_escape_blocked | test_runtime_l3_security_boundary.py | PASS |
| 28 | 修改 Registry 拒绝 | TestSkillCannotModifyRegistry::test_skill_cannot_modify_registry | test_runtime_l3_security_boundary.py | PASS |

### 29–33: 网络和子进程

| # | 验收场景 | pytest node ID | 文件 | 结果 |
|---|---------|---------------|------|------|
| 29 | socket 创建拒绝 | TestMaliciousTopLevelSocket::test_top_level_socket_blocked | test_runtime_l3_security_boundary.py | PASS |
| 30 | socket connect 拒绝 | TestMaliciousTopLevelSocketConnect::test_top_level_socket_connect_blocked | test_runtime_l3_security_boundary.py | PASS |
| 31 | subprocess.Popen 拒绝 | TestMaliciousTopLevelPopen::test_top_level_popen_blocked | test_runtime_l3_security_boundary.py | PASS |
| 32 | os.system 拒绝 | TestMaliciousTopLevelOsSystem::test_top_level_os_system_blocked | test_runtime_l3_security_boundary.py | PASS |
| 33 | ctypes/native load 拒绝 | TestMaliciousTopLevelCtypes::test_top_level_ctypes_blocked | test_runtime_l3_security_boundary.py | PASS |

### 34–38: 环境

| # | 验收场景 | pytest node ID | 文件 | 结果 |
|---|---------|---------------|------|------|
| 34 | API Key 不进入 Worker | TestSecretFilteringEnv::test_all_secrets_removed_from_sanitized_env | test_runtime_l3_protocol_env.py | PASS |
| 35 | GITHUB_TOKEN 不进入 Worker | TestSecretFilteringEnv::test_secret_values_not_in_sanitized_env | test_runtime_l3_protocol_env.py | PASS |
| 36 | PYTHONPATH 被移除 | TestSanitizedEnv::test_pythonpath_removed | test_runtime_l1_models.py | PASS |
| 37 | 白名单变量保留 | TestSanitizedEnv::test_safe_vars_preserved | test_runtime_l1_models.py | PASS |
| 38 | 日志不包含 secret | **4 个独立 node**: TestSecretLeakageInWorker::test_secrets_not_in_worker_stdout_stderr, test_secrets_not_in_result_json (新增), test_secrets_not_in_runtime_log (新增), test_secrets_not_in_error_messages | test_runtime_l3_protocol_env.py | 4/4 PASS |

### 39–44: 依赖

| # | 验收场景 | pytest node ID | 文件 | 结果 |
|---|---------|---------------|------|------|
| 39 | 已满足依赖 | TestDependencyChecker::test_all_satisfied | test_runtime_l1_models.py | PASS |
| 40 | 缺失依赖 | TestDependencyGatingPopenNotCalled::test_dependency_missing_prevents_healthcheck | test_runtime_l3_deps_registry.py | PASS |
| 41 | 版本不满足 | TestDependencyVersionMismatch::test_dependency_version_mismatch_prevents_process_start | test_runtime_l3_deps_registry.py | PASS |
| 42 | 不 import 包 | TestDependencyGatingPopenNotCalled::test_dependency_check_does_not_import_target | test_runtime_l3_deps_registry.py | PASS |
| 43 | 不安装包 | TestDependencyCheckerNeverInstalls::test_dependency_checker_never_invokes_package_installer | test_runtime_l3_deps_registry.py | PASS |
| 44 | 依赖失败不启动进程 | TestDependencyGatingPopenNotCalled::test_dependency_missing_prevents_healthcheck | test_runtime_l3_deps_registry.py | PASS |

### 45–52: 超时、取消、崩溃和输出限制

| # | 验收场景 | pytest node ID | 文件 | 结果 |
|---|---------|---------------|------|------|
| 45 | 正常完成 | TestTimeoutCancelCrashReal::test_healthcheck_completes_normally | test_runtime_l2_subprocess.py | PASS |
| 46 | healthcheck 超时 | TestTimeoutCancelCrashReal::test_healthcheck_timeout | test_runtime_l2_subprocess.py | PASS |
| 47 | 用户取消 | TestTimeoutCancelCrashReal::test_user_cancel | test_runtime_l2_subprocess.py | PASS |
| 48 | terminate 后无孤儿进程 | TestTimeoutCancelCrashReal::test_cancel_leaves_no_worker_process | test_runtime_l2_subprocess.py | PASS |
| 49 | 缺失 result.json | TestTimeoutCancelCrashReal::test_missing_result_json | test_runtime_l2_subprocess.py | PASS |
| 50 | 子进程非零退出 | TestTimeoutCancelCrashReal::test_nonzero_worker_exit | test_runtime_l2_subprocess.py | PASS |
| 51 | stdout 限制 | TestTimeoutCancelCrashReal::test_stdout_truncated_at_limit | test_runtime_l2_subprocess.py | PASS |
| 52 | stderr 限制 | TestTimeoutCancelCrashReal::test_stderr_truncated_at_limit | test_runtime_l2_subprocess.py | PASS |

### 53–59: Registry

| # | 验收场景 | pytest node ID | 文件 | 结果 |
|---|---------|---------------|------|------|
| 53 | checking | TestRegistryHealthFlow::test_checking_then_healthy | test_runtime_l2_subprocess.py | PASS |
| 54 | healthy | TestRegistryHealthFlow::test_update_health_status_transaction | test_runtime_l2_subprocess.py | PASS |
| 55 | unhealthy | TestHealthcheckUnhealthy::test_healthcheck_false_updates_unhealthy | test_runtime_l3_deps_registry.py | PASS |
| 56 | dependency_missing | TestDependencyGatingPopenNotCalled::test_dependency_missing_prevents_healthcheck | test_runtime_l3_deps_registry.py | PASS |
| 57 | Registry save 失败恢复快照 | TestRegistrySaveFailureRestore::test_registry_save_failure_restores_health_snapshot | test_runtime_l3_deps_registry.py | PASS |
| 58 | active version 不变 | TestHealthcheckHappyPath::test_healthcheck_does_not_affect_active_version | test_runtime_l2_subprocess.py | PASS |
| 59 | 不自动 enabled | TestHealthcheckDoesNotAutoEnable::test_successful_healthcheck_does_not_enable_disabled_skill | test_runtime_l3_deps_registry.py | PASS |

### 60–67: UI 生命周期

| # | 验收场景 | pytest node ID | 文件 | 结果 |
|---|---------|---------------|------|------|
| 60 | 开始健康检查 | TestRuntimeUIHealthcheckLifecycle::test_runtime_ui_starts_healthcheck | test_runtime_ui_lifecycle.py | PASS |
| 61 | 取消健康检查 | TestRuntimeUIHealthcheckLifecycle::test_runtime_ui_cancels_healthcheck | test_runtime_ui_lifecycle.py | PASS |
| 62 | Widget 关闭取消或转交 | TestRuntimeUIHealthcheckLifecycle::test_runtime_widget_close_cancels_or_transfers_process | test_runtime_ui_lifecycle.py | PASS |
| 63 | MainWindow 关闭安全结束 | TestApplicationQuitWithRuntime::test_main_window_close_safely_finishes_process | test_runtime_ui_lifecycle.py | PASS |
| 64 | QApplication 退出前无 Runtime | TestApplicationQuitWithRuntime::test_about_to_quit_has_no_runtime_process | test_runtime_ui_lifecycle.py | PASS |
| 65 | 无 QProcess/线程销毁警告 | TestRuntimeCloseNoDestroyedWarning::test_runtime_close_has_no_process_or_thread_destroyed_warning | test_runtime_ui_lifecycle.py | PASS |
| 66 | 普通运行按钮禁用 | TestRuntimeUIHealthcheckLifecycle::test_normal_run_action_remains_disabled | test_runtime_ui_lifecycle.py | PASS |
| 67 | UI 不阻塞 | TestRuntimeUIHealthcheckLifecycle::test_runtime_healthcheck_does_not_block_ui_event_loop | test_runtime_ui_lifecycle.py | PASS |

---

## D. Symlink 验收

### D.1 测试设计

本轮有两个 symlink 测试 node，共同覆盖 10 项安全断言：

| # | 断言 | 测试 A (direct) | 测试 B (installer transaction) | 状态 |
|---|------|:-:|:-:|------|
| 1 | fake `is_symlink() == True` | ✅ | ✅ | PASS |
| 2 | 调用真实 safe-copy / installer | ✅ | ✅ | PASS |
| 3 | 抛安全异常 / install 失败 | ✅ | ✅ | PASS |
| 4 | 目标目录无半复制 | ✅ | ✅ | PASS |
| 5 | 源目录树和文件内容完全不变 | ✅ | ✅ | PASS |
| 6 | Registry 内存快照不变 | N/A | ✅ | PASS |
| 7 | Registry 磁盘文件不变 | N/A | ✅ | PASS |
| 8 | 无安装记录 | N/A | ✅ | PASS |
| 9 | 无 staging 残留 | N/A | ✅ | PASS |
| 10 | 无 skip / xfail | ✅ | ✅ | PASS |

### D.2 测试 A: 直接 safe-copy 路径

**node ID**: `tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry`

源码位置: `tests/test_skill_package.py:541–612`

断言实现：

1. **fake is_symlink() == True**: 内部类 `_FakeSymlinkEntry.is_symlink()` 确定性返回 `True`（line ~567）
2. **调用真实 safe_copy_directory()**: `safe_copy_directory(src, dest)` 直接调用（line ~596）
3. **抛安全异常**: `pytest.raises(SkillPackageSecurityError, match="symlink|Symlink")`（line ~596）
4. **目标无半复制**: 异常后显式断言 `if dest.exists(): remaining = list(dest.rglob("*")); assert len(remaining) == 0`（line ~603-606）
5. **源不变**: 对比 SHA256 哈希字典 `src_files_before == src_files_after`（line ~615-619）

### D.3 测试 B: 安装器事务路径（整改新增）

**node ID**: `tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction`

源码位置: `tests/test_skill_package.py:885–1005`

断言实现：

1. **fake is_symlink() == True**: 同测试 A 的 `_FakeSymlinkEntry` 内部类（line ~930-950）
2. **调用真实 installer**: `installer.install(request)` → `SkillInstaller._install_locked()` → `safe_copy_directory()`（line ~969）
3. **install 失败**: `assert result.success is False` + `assert "symlink" in result.message.lower()`（line ~975-979）
4. **目标无半复制**: 检查 `paths.installed_dir / "test-skill"` 不存在或为空（line ~982-987）
5. **源不变**: SHA256 哈希对比（line ~990-997）
6. **Registry 内存快照不变**: `reg_snapshot == reg_snapshot_after`（line ~1000-1004）
7. **Registry 磁盘文件不变**: `reg_disk_before == paths.registry_file.read_bytes()`（line ~1007-1015）
8. **无安装记录**: `registry.get("test-skill", "1.0.0") is None`（line ~1018-1020）
9. **无 staging 残留**: `staging_before == staging_after`（line ~1024-1031）
10. **无 skip/xfail**: 全文无 `pytest.skip`、`skipif`、`xfail` 调用

### D.4 旧 Windows 真实 symlink 测试（已存在，非本轮新增）

**node ID**: `tests/test_skill_package.py::TestSafeCopyDirectory::test_source_is_symlink_rejected`

该测试使用 `pytest.skip("Windows symlink detection requires dev mode; skipping")` 等平台条件跳过。
这是**既有测试**，属于旧代码，与本轮 20 个新增 node 无关。

---

## E. 原始命令证据

### E.1 22 个整改证据 node

**命令**:

```bash
python -m pytest \
  tests/test_runtime_l2_subprocess.py::TestHealthcheckEntrypointErrors::test_healthcheck_entrypoint_file_missing \
  tests/test_runtime_l2_subprocess.py::TestHealthcheckEntrypointErrors::test_healthcheck_function_missing \
  tests/test_runtime_l2_subprocess.py::TestHealthcheckEntrypointErrors::test_healthcheck_function_not_callable \
  tests/test_runtime_l2_subprocess.py::TestHealthcheckEntrypointErrors::test_healthcheck_return_value_not_dict \
  tests/test_runtime_l2_subprocess.py::TestHealthcheckEntrypointErrors::test_healthcheck_healthy_field_not_bool \
  tests/test_runtime_l3_security_boundary.py::TestSkillCanWriteRuntimeOutput::test_skill_can_write_runtime_output \
  tests/test_runtime_l3_security_boundary.py::TestSkillCanWriteRuntimeOutput::test_skill_can_write_and_read_output_artifact \
  tests/test_runtime_l3_security_boundary.py::TestSkillCannotModifyRegistry::test_skill_cannot_modify_registry \
  tests/test_runtime_l3_security_boundary.py::TestMaliciousTopLevelSocketConnect::test_top_level_socket_connect_blocked \
  tests/test_runtime_l3_protocol_env.py::TestSecretLeakageInWorker::test_secrets_not_in_worker_stdout_stderr \
  tests/test_runtime_l3_protocol_env.py::TestSecretLeakageInWorker::test_secrets_not_in_result_json \
  tests/test_runtime_l3_protocol_env.py::TestSecretLeakageInWorker::test_secrets_not_in_runtime_log \
  tests/test_runtime_l3_protocol_env.py::TestSecretLeakageInWorker::test_secrets_not_in_error_messages \
  tests/test_runtime_l3_deps_registry.py::TestDependencyVersionMismatch::test_dependency_version_mismatch_prevents_process_start \
  tests/test_runtime_l3_deps_registry.py::TestDependencyCheckerNeverInstalls::test_dependency_checker_never_invokes_package_installer \
  tests/test_runtime_l3_deps_registry.py::TestDependencyCheckerNeverInstalls::test_dependency_checker_uses_importlib_only \
  tests/test_runtime_l3_deps_registry.py::TestHealthcheckUnhealthy::test_healthcheck_false_updates_unhealthy \
  tests/test_runtime_l3_deps_registry.py::TestRegistrySaveFailureRestore::test_registry_save_failure_restores_health_snapshot \
  tests/test_runtime_l3_deps_registry.py::TestHealthcheckDoesNotAutoEnable::test_successful_healthcheck_does_not_enable_disabled_skill \
  tests/test_runtime_ui_lifecycle.py::TestRuntimeCloseNoDestroyedWarning::test_runtime_close_has_no_process_or_thread_destroyed_warning \
  tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry \
  tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction \
  -q --tb=long
```

**输出**:

```text
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-9.1.1, pluggy-1.6.0
rootdir: D:\桌面文件\软件项目_qt6
configfile: pytest.ini
plugins: anyio-4.13.0, langsmith-0.8.5
collected 22 items

tests\test_runtime_l2_subprocess.py .....                                [ 22%]
tests\test_runtime_l3_security_boundary.py ....                          [ 40%]
tests\test_runtime_l3_protocol_env.py ....                               [ 59%]
tests\test_runtime_l3_deps_registry.py ......                            [ 86%]
tests\test_runtime_ui_lifecycle.py .                                     [ 90%]
tests\test_skill_package.py ..                                           [100%]

============================= 22 passed in 5.83s ==============================
```

**Summary**: 22 collected, 22 passed, 0 failed, 0 skipped, 0 deselected, exit code 0

### E.2 Runtime 148 项全量回归

**命令**:

```bash
python -m pytest \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_l3_security_boundary.py \
  tests/test_runtime_l3_protocol_env.py \
  tests/test_runtime_l3_deps_registry.py \
  tests/test_runtime_ui_lifecycle.py \
  -q --tb=short
```

**六个文件**:

```text
tests/test_runtime_l1_models.py
tests/test_runtime_l2_subprocess.py
tests/test_runtime_l3_security_boundary.py
tests/test_runtime_l3_protocol_env.py
tests/test_runtime_l3_deps_registry.py
tests/test_runtime_ui_lifecycle.py
```

**输出**:

```text
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-9.1.1, pluggy-1.6.0
rootdir: D:\桌面文件\软件项目_qt6
configfile: pytest.ini
plugins: anyio-4.13.0, langsmith-0.8.5
collected 148 items

tests\test_runtime_l1_models.py .......................................  [ 26%]
tests\test_runtime_l2_subprocess.py ............................         [ 45%]
tests\test_runtime_l3_security_boundary.py ............................  [ 64%]
tests\test_runtime_l3_protocol_env.py ......................             [ 79%]
tests\test_runtime_l3_deps_registry.py .................                 [ 90%]
tests\test_runtime_ui_lifecycle.py ..............                        [100%]

============================ 148 passed in 21.77s =============================
```

**Summary**: 148 collected, 148 passed, 0 failed, 0 skipped, 0 deselected, exit code 0, 耗时 21.77s

**明确说明**: Runtime 148 项回归不包含 `tests/test_skill_package.py`。
旧 Windows 真实 symlink 集成测试仍可能因开发者模式限制 skipped，但本批平台无关安装器 symlink node 全部独立 PASS。

### E.3 Pyright

**命令**:

```bash
pyright tests/fixtures/runtime_fixtures.py \
       tests/test_runtime_l1_models.py \
       tests/test_runtime_l2_subprocess.py \
       tests/test_runtime_l3_security_boundary.py \
       tests/test_runtime_l3_protocol_env.py \
       tests/test_runtime_l3_deps_registry.py \
       tests/test_runtime_ui_lifecycle.py \
       tests/test_skill_package.py
```

**输出**:

```text
0 errors, 0 warnings, 0 informations
WARNING: there is a new pyright version available (v1.1.410 -> v1.1.411).
Please install the new version or set PYRIGHT_PYTHON_FORCE_VERSION to `latest`
```

**8 个文件**:

```text
tests/fixtures/runtime_fixtures.py      — 0 errors, 0 warnings
tests/test_runtime_l1_models.py         — 0 errors, 0 warnings
tests/test_runtime_l2_subprocess.py     — 0 errors, 0 warnings
tests/test_runtime_l3_security_boundary.py — 0 errors, 0 warnings
tests/test_runtime_l3_protocol_env.py   — 0 errors, 0 warnings
tests/test_runtime_l3_deps_registry.py  — 0 errors, 0 warnings
tests/test_runtime_ui_lifecycle.py      — 0 errors, 0 warnings
tests/test_skill_package.py             — 0 errors, 0 warnings
```

**Summary**: 0 errors, 0 warnings, exit code 0

### E.4 Compileall

**命令**:

```bash
python -m compileall -f \
  tests/fixtures/runtime_fixtures.py \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_l3_security_boundary.py \
  tests/test_runtime_l3_protocol_env.py \
  tests/test_runtime_l3_deps_registry.py \
  tests/test_runtime_ui_lifecycle.py \
  tests/test_skill_package.py
```

**文件范围**: 以上 8 个 .py 文件

**输出**:

```text
Compiling 'tests/fixtures/runtime_fixtures.py'...
Compiling 'tests/test_runtime_l1_models.py'...
Compiling 'tests/test_runtime_l2_subprocess.py'...
Compiling 'tests/test_runtime_l3_security_boundary.py'...
Compiling 'tests/test_runtime_l3_protocol_env.py'...
Compiling 'tests/test_runtime_l3_deps_registry.py'...
Compiling 'tests/test_runtime_ui_lifecycle.py'...
Compiling 'tests/test_skill_package.py'...
```

**结论**: 8/8 编译成功，0 syntax error，exit code 0

### E.5 禁止规避检查

**Runtime 测试文件 skip/xfail/skipif 检查**:

```bash
rg "(pytest\.skip|pytest\.mark\.skip|skipif|xfail|--deselect|-k not)" \
   tests/test_runtime_*.py
```

**结果**: No matches found — Runtime 本轮测试零 skip/xfail/skipif

**test_skill_package.py skip/xfail 检查**:

```bash
rg "(pytest\.skip|pytest\.mark\.skip|skipif|xfail)" tests/test_skill_package.py
```

**结果** (有匹配，但均为既有测试):

```text
515:    pytest.skip("Windows symlink detection requires dev mode; skipping")
518:    pytest.skip("symlink not supported")
528:    pytest.skip("symlink creation not permitted")
532:    pytest.skip("symlink creation succeeded but not detected as symlink")
839:    pytest.skip("Windows file locking prevents replace in this test env")
```

分析：

- 行 515/518/528/532: 属于 `test_source_is_symlink_rejected`（旧 Windows 真实 symlink 集成测试），与本轮 20 个新增 node **无关**
- 行 839: 属于 `test_same_version_replace`（既有测试），与本轮无关
- 本批新增的 2 个 symlink node (`test_safe_copy_directory_rejects_symlink_via_fake_entry` 和 `test_install_rejects_symlink_via_fake_entry_full_transaction`) 不含任何 skip/xfail/skipif

**检查 pytest 命令不含规避选项**:

```text
确认: 22 证据 node 命令不含 -k、--deselect、--ignore
确认: 148 Runtime 命令不含 -k、--deselect、--ignore
```

---

## F. 文件修改范围

### F.0 当前 Git 状态（原始输出）

```bash
git status --short
```

```text
 M CLAUDE.md
 M core/ai_client.py
 M core/chart_bundle.py
 M core/chart_registry.py
 M core/chart_store.py
 M core/report_engine.py
 M core/tools/calibration_chart_tool.py
 M dp_engine/agent_skill_hub.py
 D dp_engine/github_skill_loader.py
 M dp_engine/report_builder/ppt_builder.py
 M dp_engine/report_builder/word_builder.py
 M main.py
 M tests/test_ai_client_backend.py
 M tests/test_chart_bundle_from_providers.py
 M tests/test_chart_bundle_tables.py
 M tests/test_chart_registry_store.py
 M tests/test_diagnosis_save.py
 M tests/test_parse_enlight_sensors.py
 M tests/test_parse_validation.py
 M tests/test_phase_b_dialog.py
 M tests/test_ppt_builder_guard.py
 M tests/test_ppt_figure_injection.py
 M tests/test_report_diagnosis.py
 M tests/test_strain_sensor_list.py
 M ui/ai_diagnosis.py
 M ui/calibration_tab.py
 M ui/compare_tab.py
 M ui/report_workbench.py
 M ui/skill_tab.py
 M ui/widgets/chart_panel.py
 M utils/file_parser.py
 M utils/parse_validation.py
 M "软件功能与任务概览_2026-06-30.txt"
?? .codex/
?? AGENTS.md
?? core/report_figure_planner.py
?? docs/agents/
?? dp_engine/github_skill_source.py
?? dp_engine/skills/
?? readings_profiles/
?? tests/fixtures/
?? tests/test_compare_chart_capture.py
?? tests/test_ppt_builder_design.py
?? tests/test_report_figure_planning.py
?? tests/test_report_template_preparation.py
?? tests/test_report_template_validation.py
?? tests/test_runtime_l1_models.py
?? tests/test_runtime_l2_subprocess.py
?? tests/test_runtime_l3_deps_registry.py
?? tests/test_runtime_l3_protocol_env.py
?? tests/test_runtime_l3_security_boundary.py
?? tests/test_runtime_ui_lifecycle.py
?? tests/test_skill_batch2_3_threading.py
?? tests/test_skill_batch2_security.py
?? tests/test_skill_package.py
?? tests/test_skill_source_controller.py
?? tests/test_skills_models.py
?? tests/test_word_figure_injection.py
?? tests/test_word_report_regressions.py
?? tests/test_word_table_pagination.py
?? ui/skill_install_controller.py
?? ui/skill_runtime_controller.py
?? ui/skill_source_controller.py
?? utils/app_paths.py
?? utils/report_template_preparation.py
?? utils/report_template_validation.py
?? "报告/"
?? "软件功能与实现详解_2026-07-21.md"
?? "项目资料库/三组标定/"
?? "项目资料库/应变传感器标定/报告/"
?? "项目资料库/应变传感器标定/数据/"
```

### F.1 本轮审核整改修改

| 文件 | Git 状态 | 修改类型 | 说明 |
|------|---------|---------|------|
| `tests/test_skill_package.py` | `??` (untracked) | 增强 + 新增 | Git 状态：untracked（??）。本轮内容变化：在该既有 untracked 文件中增强 direct symlink 断言（目标空目录检查 + SHA256 哈希对比），并新增 installer transaction symlink 测试 `test_install_rejects_symlink_via_fake_entry_full_transaction`。此前批次已累计工作区变化（Batch 3.0.6 原始新增 `test_safe_copy_directory_rejects_symlink_via_fake_entry` 等）。 |
| `docs/agents/batch-3.0.6-audit-package.md` | `??` (untracked) | 重写 | 纠正计数口径、添加 Registry 断言、添加完整原始命令证据、修正正式状态措辞 |

### F.2 Batch 3.0.6 原始修改（此前已完成，非本轮修改）

| 文件 | 修改类型 | 说明 |
|------|---------|------|
| `tests/fixtures/runtime_fixtures.py` | 扩展 | 新增 10 个 fixture: entrypoint 错误 (5)、write-output (2)、registry-write、socket-connect、unhealthy |
| `tests/test_runtime_l2_subprocess.py` | 扩展 | 新增 TestHealthcheckEntrypointErrors (5 tests) |
| `tests/test_runtime_l3_security_boundary.py` | 扩展 | 新增 TestSkillCanWriteRuntimeOutput (2)、TestSkillCannotModifyRegistry、TestMaliciousTopLevelSocketConnect |
| `tests/test_runtime_l3_protocol_env.py` | 扩展 | 新增 test_secrets_not_in_result_json、test_secrets_not_in_runtime_log |
| `tests/test_runtime_l3_deps_registry.py` | 扩展 | 新增 TestDependencyVersionMismatch、TestDependencyCheckerNeverInstalls、TestHealthcheckUnhealthy、TestRegistrySaveFailureRestore、TestHealthcheckDoesNotAutoEnable |
| `tests/test_runtime_ui_lifecycle.py` | 扩展 | 新增 TestRuntimeCloseNoDestroyedWarning |
| `tests/test_skill_package.py` | 扩展 | 新增 test_safe_copy_directory_rejects_symlink_via_fake_entry（原始版本） |

### F.3 此前批次累计工作区修改

工作区内存量修改（git status tracked modified）属于此前批次，非本轮修改：

```text
CLAUDE.md, core/ai_client.py, core/chart_bundle.py, core/chart_registry.py,
core/chart_store.py, core/report_engine.py, core/tools/calibration_chart_tool.py,
dp_engine/agent_skill_hub.py, dp_engine/github_skill_loader.py (deleted),
dp_engine/report_builder/ppt_builder.py, dp_engine/report_builder/word_builder.py,
main.py, ui/ 系列, utils/ 系列, tests/ 系列 (tracked modified)
```

### F.4 未修改的生产代码

```text
Runtime 生产文件: 零修改
sysconfig 读取根: 零修改
测试仅限于测试文件和 fixture 文件
```

---

## G. 硬门槛检查

| # | 门槛 | 状态 | 证据 |
|---|------|------|------|
| 1 | 本批范围测试零失败 | ✅ | 22/22 证据 node + 148/148 Runtime 全量 passed |
| 2 | 核心生命周期测试零 skipped、零 deselected | ✅ | 0 skipped, 0 deselected |
| 3 | 本批修改代码 Pyright 零 error、零 warning | ✅ | 8 files, 0 errors, 0 warnings |
| 4 | Compileall 通过 | ✅ | 8/8 编译成功, 0 syntax error |
| 5 | Git diff 无无关修改和重复实现 | ✅ | 本轮仅修改 test_skill_package.py + audit-package.md |
| 6 | 67 项每行语义正确 | ✅ | 完整 node ID 映射 |
| 7 | 入口 10/12/13/14/15 真实专项测试 | ✅ | 5 tests, real subprocess |
| 8 | 写 output/改 registry/socket connect 真实专项 | ✅ | 4 new tests |
| 9 | secrets 4 个输出通道独立覆盖 | ✅ | 2 new + 2 existing |
| 10 | 版本门禁真实测试 | ✅ | monkeypatch + Popen spy |
| 11 | unhealthy ≠ crashed | ✅ | healthy=False 独立测试 |
| 12 | save 失败恢复快照 | ✅ | 真实 monkeypatch 注入 |
| 13 | enabled=False 正常路径 | ✅ | 非 rollback 路径 |
| 14 | #65 Runtime 专项 UI 测试 | ✅ | subprocess, <5s |
| 15 | symlink 10 项断言全部覆盖 | ✅ | 2 个测试 node: direct + installer transaction |
| 16 | 审核包计数口径正确 | ✅ | 20 新增, 22 证据, 10 fixture, 4 L3 security |

---

## H. 最终结论

```text
Batch 3.0.6 已完成整改，提交外部审核。
第三批 3.0 尚未正式封板。
在外部审核明确通过前，不进入 Batch 3.1。
```
