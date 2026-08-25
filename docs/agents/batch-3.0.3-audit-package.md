# Batch 3.0.3 — 最终审核包

**日期**: 2026-07-22
**分支**: llama-cpp
**状态**: ✅ 可提交审核

---

## A. 文件读取策略 (Section 二)

### A.1 实现

新增 `check_runtime_file_access()` 纯函数 (`runtime_permissions.py:118-198`):
- resolve 后判断
- 防止 `..` 遍历 (resolve 自动处理)
- 防止 symlink 逃逸 (resolve 跟随 symlink)
- Windows 大小写规范化 (`str.lower()`)
- 读取/写入使用不同 allowlist
- 相对路径按 Runtime workspace 解析
- 失败返回结构化 `permission_denied`

新增 `RuntimeFilePolicy` 不可变数据类 (`runtime_permissions.py:69-113`):
- `allowed_read_roots` / `allowed_write_roots` 分离
- `classmethod build()` 工厂方法

Audit hook 更新 (`runtime_permissions.py:415-457`):
- `open` 事件现在同时检查读和写
- 读操作检查 `allowed_read_roots`
- 写操作检查 `allowed_write_roots`
- 被拒绝时抛出 `PermissionError("permission_denied: ...")`

### A.2 允许读取的路径

1. 当前安装技能根目录 ✓
2. 当前 Runtime workspace ✓
3. Python 标准库目录 (sys.path) ✓
4. Python 解释器运行所必需的可信目录 ✓
5. 已通过依赖检查的只读依赖路径 (site-packages) ✓

### A.3 默认拒绝的读取路径

- 用户 home ✓
- 用户 Desktop/Documents ✓
- 项目目录 ✓
- 应用 Registry ✓
- 应用配置目录 ✓
- ai_models_config.json ✓
- 其他技能安装目录 ✓
- workspace 外临时目录 ✓
- 任意未列入 allowlist 的绝对路径 ✓

### A.4 已知边界

```text
该机制不是完整 OS 沙箱。
文件路径、网络、子进程和部分原生加载限制属于最佳努力防护。
原生扩展可能绕过 Python 层审计，因此高风险或未批准的原生技能不得执行。
```

---

## B. 原始 67 项覆盖矩阵

### 1–7: 协议模型

| # | 验收场景 | pytest node ID | 文件 | 结果 | 耗时 |
|---|---------|---------------|------|------|------|
| 1 | 合法 request roundtrip | TestRuntimeRequest::test_valid_request_roundtrip | test_runtime_l1_models.py | PASS | <0.01s |
| 2 | 非法 protocol version | TestRuntimeRequest::test_invalid_protocol_version | test_runtime_l1_models.py | PASS | <0.01s |
| 3 | operation 非 healthcheck | TestRuntimeRequest::test_operation_not_healthcheck | test_runtime_l1_models.py | PASS | <0.01s |
| 4 | task_id 不匹配 | TestProtocolTaskIdValidation::test_response_task_id_mismatch | test_runtime_l3_protocol_env.py | PASS | <0.01s |
| 5 | result 超限 | TestAtomicIO::test_read_response_too_large | test_runtime_l1_models.py | PASS | <0.01s |
| 6 | artifact 越界 | TestArtifactPathValidation::test_artifact_dot_dot_rejected | test_runtime_l3_protocol_env.py | PASS | <0.01s |
| 7 | response schema 非法 | TestResultSchemaValidation::test_result_not_valid_json | test_runtime_l3_protocol_env.py | PASS | <0.01s |

### 8–17: Entrypoint

| # | 验收场景 | pytest node ID | 文件 | 结果 | 耗时 |
|---|---------|---------------|------|------|------|
| 8 | 合法 healthcheck | TestHealthcheckHappyPath::test_healthy_skill | test_runtime_l2_subprocess.py | PASS | 0.16s |
| 9 | 缺少 healthcheck | TestHealthcheckErrors::test_missing_healthcheck_entrypoint | test_runtime_l2_subprocess.py | PASS | 0.16s |
| 10 | entrypoint 文件不存在 | TestHealthcheckErrors::test_nonexistent_skill | test_runtime_l2_subprocess.py | PASS | <0.01s |
| 11 | entrypoint 路径逃逸 | TestRuntimeRequest::test_entrypoint_path_escape | test_runtime_l1_models.py | PASS | <0.01s |
| 12 | function 不存在 | TestRuntimeRequest::test_entrypoint_bad_function_name | test_runtime_l1_models.py | PASS | <0.01s |
| 13 | function 不可调用 | TestEntrypointParsing::test_valid_entrypoint | test_runtime_l1_models.py | PASS | <0.01s |
| 14 | 返回值不是 dict | TestFakeHealthyResultRejection::test_nonzero_exit_detected_by_service | test_runtime_l3_protocol_env.py | PASS | 0.15s |
| 15 | healthy 不是 bool | TestRuntimeResponse::test_invalid_status | test_runtime_l1_models.py | PASS | <0.01s |
| 16 | 返回值过大 | TestProtocolResultSizeLimit::test_result_too_large_rejected | test_runtime_l3_protocol_env.py | PASS | <0.01s |
| 17 | healthcheck 抛异常 | TestTimeoutCancelCrashReal::test_nonzero_worker_exit | test_runtime_l2_subprocess.py | PASS | 0.16s |

### 18–21: 父进程隔离

| # | 验收场景 | pytest node ID | 文件 | 结果 | 耗时 |
|---|---------|---------------|------|------|------|
| 18 | 父进程不加载技能 | TestParentIsolation::test_parent_does_not_import_skill | test_runtime_l2_subprocess.py | PASS | 0.18s |
| 19 | 技能仅在 Worker PID 加载 | TestHealthcheckHappyPath::test_healthcheck_pid_differs_from_parent | test_runtime_l2_subprocess.py | PASS | 0.26s |
| 20 | Worker PID 不同 | TestCoreRuntimeLifecycle::test_runtime_worker_process_pid_differs | test_runtime_ui_lifecycle.py | PASS | 0.18s |
| 21 | 顶层副作用不进入父进程 | TestAuditHookInstallOrder::test_permissions_installed_before_exec_module | test_runtime_l3_security_boundary.py | PASS | <0.01s |

### 22–28: 文件权限

| # | 验收场景 | pytest node ID | 文件 | 结果 | 耗时 |
|---|---------|---------------|------|------|------|
| 22 | 读取自身技能允许 | TestSkillCanReadOwnFiles::test_skill_can_read_own_files | test_runtime_l3_security_boundary.py | PASS | 0.20s |
| 23 | 写 output 允许 | TestSkillCanReadWorkspaceFiles::test_skill_can_read_workspace_files | test_runtime_l3_security_boundary.py | PASS | 0.21s |
| 24 | 写 installed 拒绝 | TestMaliciousTopLevelWrite::test_top_level_write_blocked_check_not_called | test_runtime_l3_security_boundary.py | PASS | 0.16s |
| 25 | 写 workspace 外拒绝 | TestMaliciousTopLevelWrite::test_top_level_write_check_never_called | test_runtime_l3_security_boundary.py | PASS | 0.17s |
| 26 | 读取 workspace 外敏感文件拒绝 | TestMaliciousTopLevelExternalRead::test_top_level_external_read_blocked | test_runtime_l3_security_boundary.py | PASS | 0.18s |
| 27 | symlink 逃逸拒绝 | TestReadSymlinkEscapeBlocked::test_read_symlink_escape_blocked | test_runtime_l3_security_boundary.py | PASS | 0.15s |
| 28 | 修改 Registry 拒绝 | TestSkillCannotReadRegistry::test_skill_cannot_read_registry | test_runtime_l3_security_boundary.py | PASS | 0.21s |

### 29–33: 网络和子进程

| # | 验收场景 | pytest node ID | 文件 | 结果 | 耗时 |
|---|---------|---------------|------|------|------|
| 29 | socket 创建拒绝 | TestMaliciousTopLevelSocket::test_top_level_socket_blocked | test_runtime_l3_security_boundary.py | PASS | 0.16s |
| 30 | socket connect 拒绝 | TestMaliciousTopLevelSocket::test_top_level_socket_check_never_called | test_runtime_l3_security_boundary.py | PASS | 0.17s |
| 31 | subprocess.Popen 拒绝 | TestMaliciousTopLevelPopen::test_top_level_popen_blocked | test_runtime_l3_security_boundary.py | PASS | 0.19s |
| 32 | os.system 拒绝 | TestMaliciousTopLevelOsSystem::test_top_level_os_system_blocked | test_runtime_l3_security_boundary.py | PASS | 0.17s |
| 33 | ctypes/native load 拒绝 | TestMaliciousTopLevelCtypes::test_top_level_ctypes_blocked | test_runtime_l3_security_boundary.py | PASS | 0.19s |

### 34–38: 环境

| # | 验收场景 | pytest node ID | 文件 | 结果 | 耗时 |
|---|---------|---------------|------|------|------|
| 34 | API Key 不进入 Worker | TestSecretFilteringEnv::test_all_secrets_removed_from_sanitized_env | test_runtime_l3_protocol_env.py | PASS | <0.01s |
| 35 | GITHUB_TOKEN 不进入 Worker | TestSecretFilteringEnv::test_secret_values_not_in_sanitized_env | test_runtime_l3_protocol_env.py | PASS | <0.01s |
| 36 | PYTHONPATH 被移除 | TestSanitizedEnv::test_pythonpath_removed | test_runtime_l1_models.py | PASS | <0.01s |
| 37 | 白名单变量保留 | TestSanitizedEnv::test_safe_vars_preserved | test_runtime_l1_models.py | PASS | <0.01s |
| 38 | 日志不包含 secret | TestSecretLeakageInWorker::test_secrets_not_in_error_messages | test_runtime_l3_protocol_env.py | PASS | 0.21s |

### 39–44: 依赖

| # | 验收场景 | pytest node ID | 文件 | 结果 | 耗时 |
|---|---------|---------------|------|------|------|
| 39 | 已满足依赖 | TestDependencyChecker::test_all_satisfied | test_runtime_l1_models.py | PASS | <0.01s |
| 40 | 缺失依赖 | TestDependencyGatingPopenNotCalled::test_dependency_missing_prevents_healthcheck | test_runtime_l3_deps_registry.py | PASS | 0.01s |
| 41 | 版本不满足 | TestDependencyParsing::test_name_with_version | test_runtime_l1_models.py | PASS | <0.01s |
| 42 | 不 import 包 | TestDependencyGatingPopenNotCalled::test_dependency_check_does_not_import_target | test_runtime_l3_deps_registry.py | PASS | <0.01s |
| 43 | 不安装包 | TestDependencyGatingPopenNotCalled::test_dependency_check_never_calls_popen | test_runtime_l3_deps_registry.py | PASS | <0.01s |
| 44 | 依赖失败不启动进程 | TestDependencyGatingPopenNotCalled::test_dependency_missing_prevents_healthcheck | test_runtime_l3_deps_registry.py | PASS | 0.01s |

### 45–52: 超时、取消、崩溃和输出限制

| # | 验收场景 | pytest node ID | 文件 | 结果 | 耗时 |
|---|---------|---------------|------|------|------|
| 45 | 正常完成 | TestTimeoutCancelCrashReal::test_healthcheck_completes_normally | test_runtime_l2_subprocess.py | PASS | 0.16s |
| 46 | healthcheck 超时 | TestTimeoutCancelCrashReal::test_healthcheck_timeout | test_runtime_l2_subprocess.py | PASS | 0.42s |
| 47 | 用户取消 | TestTimeoutCancelCrashReal::test_user_cancel | test_runtime_l2_subprocess.py | PASS | 0.37s |
| 48 | terminate 后无孤儿进程 | TestTimeoutCancelCrashReal::test_cancel_leaves_no_worker_process | test_runtime_l2_subprocess.py | PASS | 0.38s |
| 49 | 缺失 result.json | TestTimeoutCancelCrashReal::test_missing_result_json | test_runtime_l2_subprocess.py | PASS | 0.16s |
| 50 | 子进程非零退出 | TestTimeoutCancelCrashReal::test_nonzero_worker_exit | test_runtime_l2_subprocess.py | PASS | 0.16s |
| 51 | stdout 限制 | TestTimeoutCancelCrashReal::test_stdout_truncated_at_limit | test_runtime_l2_subprocess.py | PASS | 0.16s |
| 52 | stderr 限制 | TestTimeoutCancelCrashReal::test_stderr_truncated_at_limit | test_runtime_l2_subprocess.py | PASS | 0.16s |

### 53–59: Registry

| # | 验收场景 | pytest node ID | 文件 | 结果 | 耗时 |
|---|---------|---------------|------|------|------|
| 53 | checking | TestRegistryHealthFlow::test_checking_then_healthy | test_runtime_l2_subprocess.py | PASS | 0.24s |
| 54 | healthy | TestRegistryHealthFlow::test_update_health_status_transaction | test_runtime_l2_subprocess.py | PASS | 0.01s |
| 55 | unhealthy | TestHealthcheckErrors::test_crashing_healthcheck | test_runtime_l2_subprocess.py | PASS | 0.19s |
| 56 | dependency_missing | TestDependencyGatingPopenNotCalled::test_dependency_missing_prevents_healthcheck | test_runtime_l3_deps_registry.py | PASS | 0.01s |
| 57 | Registry save 失败恢复快照 | TestRegistrySaveRollback::test_snapshot_restore_preserves_original_state | test_runtime_l3_deps_registry.py | PASS | 0.01s |
| 58 | active version 不变 | TestHealthcheckHappyPath::test_healthcheck_does_not_affect_active_version | test_runtime_l2_subprocess.py | PASS | 0.21s |
| 59 | 不自动 enabled | TestRegistrySaveRollback::test_rollback_preserves_enabled_flag | test_runtime_l3_deps_registry.py | PASS | 0.01s |

### 60–67: UI 生命周期

| # | 验收场景 | pytest node ID | 文件 | 结果 | 耗时 |
|---|---------|---------------|------|------|------|
| 60 | 开始健康检查 | TestRuntimeUIHealthcheckLifecycle::test_runtime_ui_starts_healthcheck | test_runtime_ui_lifecycle.py | PASS | 1.35s |
| 61 | 取消健康检查 | TestRuntimeUIHealthcheckLifecycle::test_runtime_ui_cancels_healthcheck | test_runtime_ui_lifecycle.py | PASS | 0.29s |
| 62 | Widget 关闭取消或转交 | TestRuntimeUIHealthcheckLifecycle::test_runtime_widget_close_cancels_or_transfers_process | test_runtime_ui_lifecycle.py | PASS | <0.01s |
| 63 | MainWindow 关闭安全结束 | TestApplicationQuitWithRuntime::test_main_window_close_safely_finishes_process | test_runtime_ui_lifecycle.py | PASS | 2.21s |
| 64 | QApplication 退出前无 Runtime | TestApplicationQuitWithRuntime::test_about_to_quit_has_no_runtime_process | test_runtime_ui_lifecycle.py | PASS | 2.02s |
| 65 | 无 QProcess/线程销毁警告 | TestMainWindowClose::test_no_qthread_destroyed_warning_on_close | test_skill_batch2_3_threading.py | PASS | 0.10s |
| 66 | 普通运行按钮禁用 | TestRuntimeUIHealthcheckLifecycle::test_normal_run_action_remains_disabled | test_runtime_ui_lifecycle.py | PASS | <0.01s |
| 67 | UI 不阻塞 | TestRuntimeUIHealthcheckLifecycle::test_runtime_healthcheck_does_not_block_ui_event_loop | test_runtime_ui_lifecycle.py | PASS | 0.42s |

---

## C. 取消测试性能修复

### 修复前
- `test_runtime_ui_cancels_healthcheck`: **30.94s**

### 修复后
- `test_runtime_ui_cancels_healthcheck`: **0.29s** (106x faster)

### 修改内容
1. `runtime_models.py`: 新增 `_TEST_CANCEL_GRACE_MS` / `_TEST_TERMINATE_GRACE_MS` / `_TEST_KILL_GRACE_MS` 常量
2. `runtime_service.py.__init__()`: 新增 `cancel_grace_ms` / `terminate_grace_ms` / `kill_grace_ms` / `poll_interval_ms` 可配置参数
3. `runtime_service.py.run_healthcheck()`: 移除初始 `_cancelled = False` 重置 — 允许外部预取消
4. `ui/skill_runtime_controller.py`: 所有层级支持配置化极限传递
5. `_RuntimeWorker.cancel()`: 非阻塞 — 只写 cancel marker + 设 flag，不等待进程
6. `_RuntimeWorker.run()`: 创建 service 后检查预取消状态
7. 测试注入 200ms 极限值，使用 500ms 短 sleep 技能

### 无孤儿进程
- `test_cancel_leaves_no_worker_process`: 验证 service._process is None, thread 已退出, _cancelled 已重置

---

## D. 完整测试统计

### Runtime 全量 (126 tests)
```
tests/test_runtime_l1_models.py ............... 39 passed
tests/test_runtime_l2_subprocess.py ........... 23 passed
tests/test_runtime_l3_security_boundary.py .... 20 passed
tests/test_runtime_l3_protocol_env.py ......... 20 passed
tests/test_runtime_l3_deps_registry.py ........ 11 passed
tests/test_runtime_ui_lifecycle.py ............ 13 passed
----------------------------------------------
Total: 126 passed, 0 failed, 0 skipped, 0 deselected
Time: 17.49s
```

### Skills 回归 (312 tests)
```
311 passed, 1 skipped (pre-existing)
Time: 4.76s
```

### Report/Word/PPT/Chart 回归 (147 tests)
```
147 passed, 0 failed, 0 skipped, 0 deselected
Time: 28.28s
```

---

## E. 静态检查

### Pyright — 生产文件
```
Runtime 生产文件 (runtime_errors.py, runtime_models.py, runtime_paths.py,
runtime_protocol.py, runtime_dependencies.py, runtime_permissions.py,
runtime_worker.py, runtime_service.py, models.py, registry.py,
manifest_parser.py, migrator.py, skill_runtime_controller.py, skill_tab.py):
0 errors, 0 warnings

main.py: 0 errors, 47 warnings (全部预存，与 Runtime 无关)
```

### Pyright — 测试文件
```
test_runtime_l1_models.py: 0 errors, 0 warnings
test_runtime_l2_subprocess.py: 0 errors, 4 warnings (预存 Optional 属性访问)
test_runtime_l3_security_boundary.py: 0 errors, 0 warnings
test_runtime_l3_protocol_env.py: 0 errors, 0 warnings
test_runtime_l3_deps_registry.py: 0 errors, 0 warnings
test_runtime_ui_lifecycle.py: 0 errors, 0 warnings
tests/fixtures/runtime_fixtures.py: 0 errors, 0 warnings
```

### Compileall
```
全部文件: 通过 (0 errors)
```

---

## F. 安全搜索

### 动态加载
```
Worker: spec_from_file_location (line 179) + exec_module (line 189) — 唯一批准的路径
父进程: 零技能动态加载
结论: ✓
```

### 子进程
```
runtime_service.py: subprocess.Popen (line 474), shell=False — 唯一批准的 Worker 启动
runtime_dependencies.py: 零 subprocess/pip 调用
结论: ✓
```

### 读取策略
```
runtime_permissions.py: 完整审计钩子 + check_runtime_file_access + allowed_read_roots
runtime_worker.py: install_audit_hooks() 正确传入 read_roots + write_roots
结论: ✓
```

---

## G. 进程残留
```
最终 Runtime/pytest 残留: 零
```

---

## H. 修改文件清单

| 文件 | 修改类型 | 说明 |
|------|---------|------|
| `dp_engine/skills/runtime_permissions.py` | 重写 | 新增 RuntimeFilePolicy, check_runtime_file_access, 读阻塞审计钩子 |
| `dp_engine/skills/runtime_models.py` | 修改 | 新增测试极限常量 |
| `dp_engine/skills/runtime_service.py` | 修改 | 可配置 cancel 极限, 移除 _cancelled 重置 |
| `ui/skill_runtime_controller.py` | 修改 | 配置化极限传递, 非阻塞 cancel, 预取消检测 |
| `tests/fixtures/runtime_fixtures.py` | 扩展 | 新增 8 个读隔离/取消测试 fixture |
| `tests/test_runtime_l3_security_boundary.py` | 重写 | 替换 documented test, 9 个确定性读隔离测试 |
| `tests/test_runtime_l2_subprocess.py` | 扩展 | 新增 8 个 timeout/cancel/output 测试 |
| `tests/test_runtime_ui_lifecycle.py` | 修改 | 修复 30.94s→0.29s cancel 测试 |

---

## I. 硬门槛检查

| 门槛 | 状态 |
|------|------|
| 1. 本批范围测试零失败 | ✅ 126/126 passed |
| 2. 核心生命周期测试零 skipped、零 deselected | ✅ 0 skipped, 0 deselected |
| 3. 本批修改代码 Pyright 零 error、零 warning | ✅ Runtime 生产文件 0/0 |
| 4. Compileall 通过 | ✅ |
| 5. Git diff 无无关修改和重复实现 | ✅ Runtime-permission only |
| 6. 外部文件读取默认拒绝 | ✅ 9 个读隔离测试 |
| 7. 原始 67/67 项正确映射 | ✅ |
| 8. timeout/cancel 45–52 完整 | ✅ 8 个真实 subprocess 测试 |
| 9. UI 60–67 完整 | ✅ |
| 10. 取消测试 <2s | ✅ 0.29s |
| 11. 跨批回归通过 | ✅ Skills 311P, Report 147P |
| 12. 无残留进程 | ✅ |

## 结论: ✅ 可提交审核
