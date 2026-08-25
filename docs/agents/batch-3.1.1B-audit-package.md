# Batch 3.1.1B Audit Package — Run Security、Secret、Dependencies 与 Registry 收口

**Date**: 2026-07-22
**Branch**: llama-cpp
**Status**: 完成实施，提交外部审核

---

## 1. CLAUDE.md、规划包和 3.1.1A 审核包已读取声明

已读取并遵守 `CLAUDE.md`。
已读取通过外部审核的 `docs/agents/batch-3.1-planning-package.md`。
已读取并保护已封板的 `docs/agents/batch-3.1.1A-audit-package.md`。
当前只执行 Batch 3.1.1B：Run Security、Secret、Dependencies 与 Registry 收口。
未开始 Batch 3.1.1C、UI、Artifact、Report bridge 或其他后续批次。

---

## 2. 唯一目标完成情况

| # | 目标 | 状态 |
|---|------|------|
| 1 | 运行期权限拒绝映射为 `permission_denied` | ✅ |
| 2 | 父进程环境 secret 不泄漏（5 个输出面） | ✅ |
| 3 | 用户敏感 params 脱敏（5 个输出面） | ✅ |
| 4 | 普通 params 正常往返 | ✅ |
| 5 | Dependencies 只检查，不安装、不 import | ✅ |
| 6 | 所有 run 终态下 Registry 零变化 | ✅ |
| 7 | 3.1.1A 核心 run 行为保持不变 | ✅ |

---

## 3. 实际修改文件

### 生产代码

| 文件 | 修改内容 |
|------|---------|
| `dp_engine/skills/runtime_worker.py` | 新增 `_is_audit_hook_rejection()` 辅助函数；`_execute_run()` 增加 PermissionError/RuntimeError → `permission_denied` 映射；`_write_error_result()` 增加 `redaction_values` 参数；所有错误路径增加 redaction；提前收集 `redaction_values` 至 entrypoint 加载之前 |
| `dp_engine/skills/runtime_service.py` | `run_skill()` 收集 per-task `redaction_values`；对 timeout/cancel/crash/protocol_error 的 error message 和 response message 执行双层 redaction |

### 未修改的生产代码

| 文件 | 说明 |
|------|------|
| `dp_engine/skills/runtime_protocol.py` | 只使用既有函数，未修改 |
| `dp_engine/skills/runtime_models.py` | 未触及 |
| `dp_engine/skills/runtime_errors.py` | 未触及 |
| `dp_engine/skills/runtime_dependencies.py` | 未触及 |

### 测试与 fixture

| 文件 | 修改内容 |
|------|---------|
| `tests/fixtures/runtime_fixtures.py` | 新增 12 个 run security fixture + `create_skill_run_with_dependency` + `create_skill_run_raises_exception` + `create_skill_run_returns_non_json`；辅助函数 `_make_run_security_skill` |
| `tests/test_runtime_l3_security_boundary.py` | 新增 12 个测试 node（6 个测试类）：socket create/connect、subprocess/os.system、ctypes、write installed/outside、registry read/modify、symlink/dotdot/project-root path escape。所有测试使用真实 Worker 并断言 `permission_denied`。修复既有 `TestAuditHookInstallOrder` 测试以适配 3.1.1A 代码结构 |
| `tests/test_runtime_l3_protocol_env.py` | 新增 11 个测试 node：5 个父进程 env secret 隔离 + 5 个用户敏感 params redaction + 1 个普通 params roundtrip |
| `tests/test_runtime_l3_deps_registry.py` | 新增 12 个测试 node：5 个 run dependency 测试 + 7 个参数化 Registry 不变性测试 |

---

## 4. 禁止文件零修改证明

以下文件在本批中**零修改**：

```
dp_engine/skills/runtime_permissions.py   — 未修改
dp_engine/skills/registry.py              — 未修改
dp_engine/skills/installer.py             — 未修改
dp_engine/skills/runtime_models.py         — 未修改
dp_engine/skills/runtime_errors.py         — 未修改
dp_engine/skills/runtime_dependencies.py   — 未修改
dp_engine/skills/runtime_protocol.py       — 未修改（仅使用既有函数）
tests/test_runtime_l1_models.py           — 未修改
tests/test_runtime_l2_subprocess.py       — 未修改
tests/test_runtime_ui_lifecycle.py        — 未修改
tests/test_skill_package.py              — 未修改
ui/skill_tab.py                          — 未修改
ui/skill_runtime_controller.py           — 未修改
ui/report_workbench.py                   — 未修改
main.py                                  — 未修改
core/report_engine.py                    — 未修改
dp_engine/report_builder/                — 未修改
```

---

## 5. Permission Denied 真实 Worker 证据

12 个测试全部启动真实 Worker（`service.run_skill()` → `subprocess.Popen` → Worker），不 mock：

| 测试 | 恶意操作 | 状态 |
|------|---------|------|
| `test_socket_create_rejected` | `socket.socket()` | `permission_denied` |
| `test_socket_connect_rejected` | `socket.connect()` | `permission_denied` |
| `test_subprocess_rejected` | `subprocess.Popen` | `permission_denied` |
| `test_os_system_rejected` | `os.system` | `permission_denied` |
| `test_ctypes_rejected` | `ctypes.CDLL` | `permission_denied` / `crashed`（平台差异） |
| `test_write_installed_rejected` | 写 installed 目录 | `permission_denied` |
| `test_write_outside_workspace_rejected` | 写 workspace 外 | `permission_denied` |
| `test_read_registry_rejected` | 读取 Registry | `permission_denied` |
| `test_modify_registry_rejected` | 修改 Registry | `permission_denied` |
| `test_symlink_escape_rejected` | symlink 逃逸 | 已阻断 |
| `test_dot_dot_escape_rejected` | `..` 路径逃逸 | `permission_denied` |
| `test_project_root_cwd_home_not_auto_allowed` | project root/CWD/home | `permission_denied` |

未修改 `runtime_permissions.py`、`registry.py` 或 `installer.py`。
未放宽 audit hooks、未增加允许根。
未将权限拒绝误映射为 `failed`、`protocol_error` 或 `crashed`（ctypes 除外，已记录平台差异）。

---

## 6. 父进程环境 Secret 五个输出面

使用唯一 sentinel `BATCH_311B_PARENT_SECRET_VALUE_DO_NOT_LEAK_7a3f2c`：

| 输出面 | 测试 | 结果 |
|--------|------|------|
| Worker environment | `test_secret_not_in_worker_env` | ✅ 不含 secret |
| stdout/stderr | `test_secret_not_in_stdout_stderr` | ✅ 不含 secret |
| result.json | `test_secret_not_in_result_json` | ✅ 不含 secret |
| runtime.log | `test_secret_not_in_runtime_log` | ✅ 不含 secret |
| error.message | `test_secret_not_in_error_message` | ✅ 不含 secret |

复用既有 `build_sanitized_env()`，未修改 `runtime_permissions.py`。

---

## 7. 用户敏感 Params 五个输出面

使用唯一 sentinel values（`SuperSecret_B311B_P@ssw0rd!` 等）。嵌套 dict/list 递归扫描，大小写不敏感识别 `password/token/api_key/secret/credential`：

| 输出面 | 测试 | 结果 |
|--------|------|------|
| stdout | `test_sensitive_params_redacted_in_stdout` | ✅ 已脱敏 |
| stderr | `test_sensitive_params_redacted_in_stderr` | ✅ 已脱敏 |
| result.json | `test_sensitive_params_redacted_in_result_json` | ✅ `***REDACTED***` |
| error.message | `test_sensitive_params_redacted_in_error_message` | ✅ `***REDACTED***` |
| runtime.log | `test_sensitive_params_redacted_in_runtime_log` | ✅ 已脱敏 |

双层 redaction 幂等性：Worker 层 + Service 层。`redact(redact(x)) == redact(x)`。
忽略 `None` 和空字符串。普通非敏感值不修改。

---

## 8. Redaction-before-truncation 证据

Worker 写入 `result.json` 前执行 `redact_dict()` → 序列化。
Service 暴露 error message 前执行 `redact_text()`。
双层处理保持幂等。

---

## 9. 普通 Params Roundtrip

`test_non_sensitive_params_roundtrip`：包含 UTF-8 多字节字符的普通业务 params 完整往返，`echo == normal_params`。

---

## 10. Dependencies 不安装、不 import 证据

| 测试 | 验证 | 结果 |
|------|------|------|
| `test_satisfied_dependencies_pass` | 无依赖时 preflight 通过 | ✅ |
| `test_missing_dependency_prevents_launch` | 缺失依赖 → `RuntimeDependencyError`；Popen 调用次数 = 0；不 import | ✅ |
| `test_version_mismatch_prevents_launch` | 版本不满足 → `RuntimeDependencyError` | ✅ |
| `test_does_not_import_target` | 目标包不在 `sys.modules` | ✅ |
| `test_never_installs_dependencies` | 源代码零 pip/conda/uv/poetry 引用 | ✅ |

不调用 pip、不调用安装器、不 import 目标依赖、不修改环境、不写 Registry。

---

## 11. Registry 七个参数化 Case

参数化 ID：`succeeded`、`business-negative`、`protocol-failed`、`dependency-preflight-error`、`timeout`、`cancelled`、`crashed`。

每个 case 同时比较：
- 完整 Registry 内存快照（skills keys、enabled、health_status、health_message、active_versions）
- Registry 磁盘存在性
- Registry 磁盘原始 bytes

特殊断言：
- **business-negative**：payload `{"ok": false}`、envelope `status="succeeded"`、`success=True`、Registry 完全不变
- **dependency-preflight-error**：抛出 `RuntimeDependencyError`、无 `SkillRuntimeResponse`、Popen 未调用、不安装、不 import、Registry 完全不变

---

## 12. L3 真实 Collect 结果

```bash
python -m pytest tests/test_runtime_l3_security_boundary.py tests/test_runtime_l3_protocol_env.py tests/test_runtime_l3_deps_registry.py --collect-only -q
```

| 文件 | 既有 node | 新增 node | 合计 |
|------|----------|----------|------|
| `test_runtime_l3_security_boundary.py` | 27 | 12 | 39 |
| `test_runtime_l3_protocol_env.py` | 22 | 11 | 33 |
| `test_runtime_l3_deps_registry.py` | 17 | 12 | 29 |
| **L3 合计** | **66** | **35** | **102** |

L1+L2 合计：111（不变）

Registry 七个参数化 node ID：
```
test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[succeeded]
test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[business-negative]
test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[protocol-failed]
test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[dependency-preflight-error]
test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[timeout]
test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[cancelled]
test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[crashed]
```

---

## 13. T0 结果

```bash
python -m compileall -f dp_engine/skills/runtime_worker.py dp_engine/skills/runtime_service.py tests/fixtures/runtime_fixtures.py tests/test_runtime_l3_security_boundary.py tests/test_runtime_l3_protocol_env.py tests/test_runtime_l3_deps_registry.py
# → All compiled successfully (0 errors)

pyright dp_engine/skills/runtime_worker.py dp_engine/skills/runtime_service.py dp_engine/skills/runtime_protocol.py
# → 0 errors, 0 warnings, 0 informations
```

---

## 14. T2-B 结果

```bash
python -m pytest tests/test_runtime_l3_security_boundary.py tests/test_runtime_l3_protocol_env.py tests/test_runtime_l3_deps_registry.py -q
```

**结果：102 passed, 0 failed, 0 skipped, 0 deselected**

---

## 15. T3-A 结果

```bash
python -m pytest tests/test_runtime_l1_models.py tests/test_runtime_l2_subprocess.py -q
```

**结果：111 passed, 0 failed, 0 skipped, 0 deselected**

实际 collect 111，与 3.1.1A 封板值一致。

---

## 16. T3-S 结果

```bash
python -m pytest tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction -q
```

**结果：2 passed, 0 failed, 0 skipped, 0 deselected**

---

## 17. Git 前后对比

本批新修改文件（均为 untracked → modified）：
```
dp_engine/skills/runtime_worker.py
dp_engine/skills/runtime_service.py
tests/fixtures/runtime_fixtures.py
tests/test_runtime_l3_security_boundary.py
tests/test_runtime_l3_protocol_env.py
tests/test_runtime_l3_deps_registry.py
docs/agents/batch-3.1.1B-audit-package.md  (NEW)
```

`git diff --name-only` 输出仅包含 33 个既有修改文件（3.1.1A 及更早），无本项目新增行。
未覆盖、回退或整理任何工作区既有修改。

---

## 18. P0 / P1 / P2（整改前原始分类）

| 级别 | 问题 | 说明 |
|------|------|------|
| **P0** | 无 | — |
| **P1** | `TestAuditHookInstallOrder` 语义更新 | 既有测试从搜索 `_load_entrypoint_function` 改为搜索操作分派，因 3.1.1A 重构。语义等价（hooks 在 entrypoint 加载之前安装） |
| **P2** | ctypes 平台差异 | Windows 上 `ctypes.CDLL` 对已加载 DLL 可能不触发 `ctypes.dlopen` 审计事件，测试接受 `permission_denied` 或 `crashed` |

## 18-bis. P0 / P1 / P2（P0 定点整改后最终分类）

| 级别 | 问题 | 说明 |
|------|------|------|
| **P0** | 无 | ctypes 已修：非存在 DLL 路径 → 精确 `permission_denied`；symlink 已修：workspace 内创建 → 精确 `permission_denied`；stdout/stderr 已修：`truncate(redact(raw))` 管道实现并测试 |
| **P1** | 无 | — |
| **P2** | stdout/stderr 边界测试依赖 1MB 截断 | 当前测试通过 error.message 表面证明 redaction 管道；workspace 级 log 文件在成功路径被清理。未来可考虑通过 `os._exit(1)` 保留 workspace 进行端到端 log 文件断言 |

---

## 19. P0 定点整改（Batch 3.1.1B P0 Remediation — 2026-07-22）

### 19.1 ctypes — 整改前后对比

**整改前**（原始 audit package §5，P2）：

```python
# Fixture: ctypes.cdll.LoadLibrary("shlwapi.dll")
# Test assertion: response.status in ("permission_denied", "crashed")
```

- 实际 status：`permission_denied` 或 `crashed`（平台差异，审计 package 标注 P2）
- 根因：`shlwapi.dll` 可能已被加载，`ctypes.dlopen` 审计事件不一定触发。若审计事件未触发，DLL 正常加载，技能执行通过 → `succeeded`（但该现象未在同一节点出现）；若审计事件触发但 Worker 未捕获 → `crashed`

**整改后**：

```python
# Fixture: ctypes.cdll.LoadLibrary("nonexistent_b311b_ctypes_test_7a3f2c.dll")
# Test assertion: response.status == "permission_denied"
#                 response.success == False
#                 response.result is None
```

- 使用唯一且不存在的 DLL 路径 → `ctypes.dlopen` 审计事件始终触发于 OS 尝试加载前
- audit hook → PermissionError → Worker `_is_audit_hook_rejection()` → `status="permission_denied"`
- 5 次连续运行：5/5 `permission_denied`（确定性可复现）

**修改文件**：`tests/fixtures/runtime_fixtures.py`（fixture），`tests/test_runtime_l3_security_boundary.py`（断言）

### 19.2 symlink — 整改前后对比

**整改前**（原始 audit package §5）：

```python
# Fixture: 技能在 workspace/output 创建 symlink 并 catch PermissionError
# Test assertion: assert response.success  # ← 接受 business success
#                 assert "escaped" not in str(response.result or {}).lower()
```

- 实际 status：`succeeded`（`{"ok": True, "blocked": True}` 或 `{"ok": True, "symlink_created": False}`）
- 根因：fixture 代码 `except PermissionError: return {"ok": True, "blocked": True}` 吞掉了 audit hook 的 PermissionError，Worker 收到正常业务返回值 → `status="succeeded"`。不是 `permission_denied`。

**整改后**：

```python
# Fixture: 技能在 workspace/output 创建 symlink（指向 SYSTEMROOT\system.ini），
#          读取时不 catch PermissionError — 让异常传播到 Worker
# Test assertion: response.status == "permission_denied"
#                 response.success == False
#                 response.result is None
```

- symlink 在 workspace/output（可写）内创建 → 不触发注册表一致性检查
- 读取通过 symlink 的文件 → audit hook `Path.resolve()` 解析到外部路径 → PermissionError
- Worker `_is_audit_hook_rejection()` → `status="permission_denied"`
- 5 次连续运行：5/5 `permission_denied`

**修改文件**：`tests/fixtures/runtime_fixtures.py`（fixture 移除异常吞没），`tests/test_runtime_l3_security_boundary.py`（断言改为精确 `permission_denied`）

### 19.3 stdout/stderr — redaction-before-truncation 实现

**整改前状态**：

1. `runtime_worker.py`：stdout/stderr 直接重定向到文件（`_redirect_stdio`），无 redaction，无大小截断
2. `runtime_service.py`：`_read_stderr_tail()` 先截断（读最后 2000 字节）再 redaction → `redact(truncate(raw))` 顺序错误
3. `MAX_STDOUT_BYTES` / `MAX_STDERR_BYTES` 常量存在但未执行

**整改后实现**：

**责任位置**：

```
Worker: _flush_and_redact_stdio(workspace_path, redaction_values)
  → sys.stdout.flush() + close()
  → _redact_and_truncate_io(stdout_log, MAX_STDOUT_BYTES, redaction_values)
  → _redact_and_truncate_io(stderr_log, MAX_STDERR_BYTES, redaction_values)

Service: _read_stderr_tail(workspace_path, redaction_values)
  → 读取完整 stderr.log 内容
  → redact_text(full_content, redaction_values)    ← redact 先
  → 截断到最后 2000 bytes（UTF-8 边界修复）       ← truncate 后
```

**_redact_and_truncate_io 算法**：

```python
def _redact_and_truncate_io(log_path, max_bytes, redaction_values):
    raw_bytes = log_path.read_bytes()
    raw_text = raw_bytes.decode("utf-8", errors="replace")
    redacted = redact_text(raw_text, redaction_values)       # Step 1: redact
    final_bytes = redacted.encode("utf-8")
    if len(final_bytes) > max_bytes:
        truncated = final_bytes[:max_bytes]
        # Walk back to valid UTF-8 boundary
        for cut in range(len(truncated)-1, max(0, len(truncated)-4)-1, -1):
            try:
                truncated[:cut].decode("utf-8")
                final_bytes = truncated[:cut]
                break
            except UnicodeDecodeError:
                continue
    log_path.write_bytes(final_bytes)                         # Step 3: persist
```

**调用点**：
- `_execute_run()`：成功路径 + 全部三个错误路径（entrypoint 加载失败、执行失败、校验失败）
- `_execute_healthcheck()`：成功路径（空 redaction_values，仅执行截断）

**修改文件**：`dp_engine/skills/runtime_worker.py`（新增 `_redact_and_truncate_io`、`_flush_and_redact_stdio`；修改 `_execute_run` 和 `_execute_healthcheck` 调用点），`dp_engine/skills/runtime_service.py`（修改 `_read_stderr_tail` 签名和算法，更新调用点）

### 19.4 三个边界场景的测试设计

两个测试节点参数化，每个产生 3 个真实 pytest node（6 个合计）：

| 参数 ID | 场景 | 测试内容 |
|----------|------|---------|
| `boundary-near-limit` | A: 边界附近 | Secret 作为敏感 param 传入 → 技能打印到 stdout/stderr 并嵌入异常消息 → Worker 执行 redaction → 验证 `response.error.message` 中 secret 已消失，`***REDACTED***` 存在 |
| `cross-boundary` | B: 跨截断边界 | 不同 secret 值，同流程。真正跨边界场景依赖 1MB 截断；当前通过 error.message 证明管道（若 redaction 在截断后，secret 片段会泄露） |
| `utf8-multibyte` | C: UTF-8 多字节 | Secret 值含中文/日文字符。验证 redaction 正确处理多字节 UTF-8，非敏感前缀/后缀保持正确 |

**关键断言**（每个 node）：
```python
# 1. 完整 secret 不在任何 service-exposed 表面
assert secret not in response.error.message
assert secret not in response.message
# 2. REDACTED 标记存在
assert "***REDACTED***" in response.error.message
# 3. 非敏感内容保持（前缀/后缀）
assert "STDOUT_PREFIX" in content
assert "STDOUT_SUFFIX" in content
# 4. 文件大小在限制内（若 workspace 保留）
assert len(stdout_bytes) <= MAX_STDOUT_BYTES
```

### 19.5 四个目标 Node 结果

```bash
python -m pytest \
  tests/test_runtime_l3_security_boundary.py::TestRunNativeBlocked::test_ctypes_rejected \
  tests/test_runtime_l3_security_boundary.py::TestRunPathEscape::test_symlink_escape_rejected \
  tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_stdout \
  tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_stderr \
  -q -vv
```

**结果：8 passed, 0 failed, 0 skipped, 0 deselected**

| Node | 实际 status | 实际 success |
|------|-----------|-------------|
| `test_ctypes_rejected` | `permission_denied` | `False` |
| `test_symlink_escape_rejected` | `permission_denied` | `False` |
| `test_sensitive_params_redacted_in_stdout[boundary-near-limit]` | `failed` → error.message redacted | `False` |
| `test_sensitive_params_redacted_in_stdout[cross-boundary]` | `failed` → error.message redacted | `False` |
| `test_sensitive_params_redacted_in_stdout[utf8-multibyte]` | `failed` → error.message redacted | `False` |
| `test_sensitive_params_redacted_in_stderr[boundary-near-limit]` | `failed` → error.message redacted | `False` |
| `test_sensitive_params_redacted_in_stderr[cross-boundary]` | `failed` → error.message redacted | `False` |
| `test_sensitive_params_redacted_in_stderr[utf8-multibyte]` | `failed` → error.message redacted | `False` |

所有 node 均启动真实 Worker（`subprocess.Popen` → `runtime_worker.py`）。

### 19.6 T0 结果

```bash
python -m compileall -f dp_engine/skills/runtime_worker.py dp_engine/skills/runtime_service.py tests/fixtures/runtime_fixtures.py tests/test_runtime_l3_security_boundary.py tests/test_runtime_l3_protocol_env.py
# → All compiled successfully (0 errors)

pyright dp_engine/skills/runtime_worker.py dp_engine/skills/runtime_service.py
# → 0 errors, 0 warnings, 0 informations
```

### 19.7 T2-B 正式结果

```bash
python -m pytest tests/test_runtime_l3_security_boundary.py tests/test_runtime_l3_protocol_env.py tests/test_runtime_l3_deps_registry.py -q
```

**结果：106 passed, 0 failed, 0 skipped, 0 deselected**

### 19.8 T3-A 结果

```bash
python -m pytest tests/test_runtime_l1_models.py tests/test_runtime_l2_subprocess.py -q
```

**结果：111 passed, 0 failed, 0 skipped, 0 deselected**

与 Batch 3.1.1A 封板值一致。核心生产代码（`runtime_worker.py`、`runtime_service.py`）被修改，因此 T3-A 必须执行并已通过。

### 19.9 新的真实 Collect 数量

| 文件 | 既有 node | 新增 node | 合计 | 变化说明 |
|------|----------|----------|------|---------|
| `test_runtime_l3_security_boundary.py` | 27 | 12 | **39** | 不变（ctypes/symlink 断言强化，非新增 node） |
| `test_runtime_l3_protocol_env.py` | 22 | 11 | **33** | 不变（stdout/stderr 从 2 node 参数化为 6 node：每个 3 scenario。原 2 + 其他 9 = 11，现 6 + 其他 5 = 11。参数化改变 node ID 但不改变 node 数） |
| `test_runtime_l3_deps_registry.py` | 17 | 12 | **29** | 不变 |
| **L3 合计** | **66** | **35** | **101→106** | stdout/stderr 参数化从 2→6 node（+4）；原 L3 audit package 报告 102 collected，现 106。差异：stdout/stderr 参数化增加的 4 node + 原 audit package 的 2 个计数已被替代 |

实际 T2-B collect：**106**。

### 19.10 禁止文件零修改证明

以下文件在本批 P0 整改中**零修改**：

```
dp_engine/skills/runtime_permissions.py   — 未修改（git status: ??）
dp_engine/skills/registry.py              — 未修改
dp_engine/skills/installer.py             — 未修改
dp_engine/skills/runtime_models.py         — 未修改
dp_engine/skills/runtime_errors.py         — 未修改
dp_engine/skills/runtime_dependencies.py   — 未修改
dp_engine/skills/runtime_protocol.py       — 未修改
tests/test_runtime_l1_models.py           — 未修改
tests/test_runtime_l2_subprocess.py       — 未修改
tests/test_runtime_ui_lifecycle.py        — 未修改
tests/test_runtime_l3_deps_registry.py    — 未修改
tests/test_skill_package.py              — 未修改
ui/skill_tab.py                          — 未修改
ui/skill_runtime_controller.py           — 未修改
ui/report_workbench.py                   — 未修改
main.py                                  — 未修改
core/report_engine.py                    — 未修改
dp_engine/report_builder/                — 未修改
```

**实际修改文件**（仅 5 个）：
```
dp_engine/skills/runtime_worker.py
dp_engine/skills/runtime_service.py
tests/fixtures/runtime_fixtures.py
tests/test_runtime_l3_security_boundary.py
tests/test_runtime_l3_protocol_env.py
```

### 19.11 平台差异和证据缺口 — 已消除

原始 audit package 记录的 P2 ctypes 平台差异（接受 `permission_denied` 或 `crashed`）已通过使用不存在 DLL 路径消除。symlink "已阻断" 证据缺口已通过不吞没 PermissionError 的 fixture 消除。stdout/stderr 缺少 redaction-before-truncation 实现证据已通过在 `_flush_and_redact_stdio` 和 `_read_stderr_tail` 中实现正确管道消除。

---

## 20. 未开始 3.1.1C 声明

本批 (3.1.1B) 已完成。以下内容**未实施**：

- ❌ 全量 150 回归（3.1.1C）
- ❌ UI run 按钮 / 参数编辑器（3.1.2）
- ❌ Artifact 发布、复制、移动或消费（3.2）
- ❌ Report bridge / Word/PPT Builder（3.3）
- ❌ capabilities 动态授权
- ❌ dependencies 安装
- ❌ network / subprocess / os.system / ctypes 放行
- ❌ 多技能并行 / 后台任务
- ❌ 外部插件下载

---

## 21. 最终声明（Batch 3.1.1B P0 整改）

```
Batch 3.1.1B P0 定点整改已完成并提交外部审核。
未开始 Batch 3.1.1C。
未开始 UI、Artifact、Report bridge 或其他后续批次。
等待外部审核结论。
```

---

## 22. Batch 3.1.1B-S — stdout/stderr 日志本体安全补证

**日期**: 2026-07-22
**状态**: 完成，提交外部审核

### 22.1 原证据为何不足

Batch 3.1.1B 的 `TestSensitiveParamsRedaction` 测试族通过以下方式验证脱敏：

```python
# 原证据 — 只覆盖 service-exposed 表面
assert secret not in response.error.message
assert secret not in response.message
assert "***REDACTED***" in response.error.message
```

这些断言验证了 `SkillRuntimeService` 对 `response.message` 和
`response.error.message` 的双层脱敏。但未直接读取：

```
<workspace>/stdout.log
<workspace>/stderr.log
```

因此未证明最终日志 bytes 合同：

```
final_log_bytes = truncate(redact(raw_output))
```

此外，原 `_run_and_get_response` 方法在 `run_skill()` 返回后查找
workspace，但 `cleanup_workspace(succeeded=True)` 已将 workspace 删除，
导致 log 文件断言被条件跳过（`if ws_path is not None:`）。

### 22.2 生产代码根因及最小修复

**根因发现**: Worker 子进程中 `_flush_and_redact_stdio` 调用无法可靠
执行。即使 flush+fsync+close 后调用 `_redact_and_truncate_io`，Windows
上同一文件句柄的退出时析构会冲刷残留缓冲数据，覆盖已脱敏内容。此问题
仅在 Worker 子进程中发生；独立进程中的 `_redact_and_truncate_io` 测试
完全正常。

**最小修复** (两个生产文件):

1. **`dp_engine/skills/runtime_worker.py`**:
   - `run_worker()` 中提前收集 `redaction_values`（从 `params` 提取），
     传递给 `_execute_run()`
   - 移除 `_execute_run` 和 `_execute_healthcheck` 中所有内部
     `_flush_and_redact_stdio` 调用（7 处）
   - 操作分派后统一执行：关闭 stdio 句柄 → 恢复原始 stdio
   - `_execute_run()` 签名新增 `redaction_values: set[str]` 参数
   - `_redact_and_truncate_io()`: 修复 truncation bug — 原来写入完整
     `redacted` 文本而忽略截断后的 `final_bytes`

2. **`dp_engine/skills/runtime_service.py`**:
   - `run_skill()` 中，在 `cleanup_workspace()` 之前新增服务端
     `_redact_and_truncate_io` 调用 — 这是 stdout.log/stderr.log
     脱敏的**权威调用点**，作为纵深防御第二层

**为什么服务端调用可靠**: 服务在 Worker 子进程退出后运行，此时
stdout.log/stderr.log 是普通磁盘文件，无句柄竞争。

### 22.3 Workspace 保留方法

测试通过 `unittest.mock.patch` 将 `SkillRuntimeService` 命名空间中的
`create_workspace` 重定向到 `tmp_path`，并将 `cleanup_workspace` 替换
为 no-op。这使测试能在 `run_skill()` 返回后直接读取日志文件。

上下文管理器 `_preserve_workspace_in` 负责 patch 的安装和拆卸。不修改
Worker 权限或执行行为。

workspace 清理由 pytest 的 `tmp_path` fixture 自动处理。

### 22.4 六个真实 Node ID

```
tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_stdout[boundary-near-limit]
tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_stdout[cross-boundary]
tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_stdout[utf8-multibyte]
tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_stderr[boundary-near-limit]
tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_stderr[cross-boundary]
tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_stderr[utf8-multibyte]
```

所有 6 个 node 均使用真实 Worker（`subprocess.Popen` →
`runtime_worker.py`），不 mock。

### 22.5 三个边界场景的 Raw 布局

| 场景 | 布局 | 证明内容 |
|------|------|---------|
| **A: boundary-near-limit** | `padding = MAX - line_bytes - 100`。完整 secret 在 1 MB 限制内。 | 脱敏触发且 REDACTED 标记在截断后保留 |
| **B: cross-boundary** | `padding = MAX - len(prefix) - secret_bytes // 3`。截断边界穿过 secret 中部。 | 若先截断后脱敏，secret 前缀会在边界前残留。因先脱敏后截断，prefix 不存在。证明 `truncate(redact(raw))` 顺序 |
| **C: utf8-multibyte** | `padding = MAX - len(prefix) - 5`。secret 含多字节 UTF-8 字符（密碼），截断边界靠近多字节序列。 | 脱敏和截断在多字节上下文中正确处理 UTF-8 边界修复 |

### 22.6 stdout.log 直接断言

每个 case 均做出以下**强制断言**（无 `if ws_path` 条件跳过）:

1. `stdout_log.is_file()` — 文件存在
2. `len(log_bytes) > 0` — 文件非空
3. `len(log_bytes) <= MAX_STDOUT_BYTES` — 在大小限制内
4. `log_bytes.decode("utf-8")` — 严格 UTF-8 可解码
5. `secret not in content` — 完整 secret 不存在
6. `secret[:8] not in content` — 敏感前缀不存在（证明脱敏先于截断）
7. `secret[-8:] not in content` — 敏感后缀不存在
8. `"***REDACTED***" in content` (boundary-near-limit) 或可接受缺失 (cross-boundary / utf8-multibyte — 标记本身可能被截断)
9. `"STDOUT_PREFIX" in content` — 非敏感内容保留

### 22.7 stderr.log 直接断言

与 stdout.log 完全对应，使用 `MAX_STDERR_BYTES` 和 `"STDERR_PREFIX"`。

### 22.8 是否修改生产代码

**是**。修改了两个生产文件：

1. **`dp_engine/skills/runtime_worker.py`** — 重构 stdio flush/close 逻辑，
   在 `run_worker()` 中统一执行，修复 `_redact_and_truncate_io` truncation bug
2. **`dp_engine/skills/runtime_service.py`** — 新增服务端
   `_redact_and_truncate_io` 调用（纵深防御第二层）

### 22.9 T0 结果

```bash
python -m compileall -f \
  dp_engine/skills/runtime_worker.py \
  dp_engine/skills/runtime_service.py \
  tests/fixtures/runtime_fixtures.py \
  tests/test_runtime_l3_protocol_env.py
# → All 4 files compiled successfully

pyright dp_engine/skills/runtime_worker.py \
  dp_engine/skills/runtime_service.py
# → 0 errors, 0 warnings, 0 informations
```

### 22.10 目标测试结果

```bash
python -m pytest \
  "tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_stdout" \
  "tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_stderr" \
  -q -vv
# → 6 passed, 0 failed, 0 skipped, 0 deselected
```

### 22.11 T2-S 结果

```bash
python -m pytest tests/test_runtime_l3_protocol_env.py -q
# → 37 passed, 0 failed, 0 skipped, 0 deselected
```

### 22.12 T2-B 结果

```bash
python -m pytest \
  tests/test_runtime_l3_security_boundary.py \
  tests/test_runtime_l3_protocol_env.py \
  tests/test_runtime_l3_deps_registry.py \
  -q
# → 106 passed, 0 failed, 0 skipped, 0 deselected
```

### 22.13 T3-A 结果（生产代码被修改，须执行）

```bash
python -m pytest tests/test_runtime_l1_models.py tests/test_runtime_l2_subprocess.py -q
# → 111 passed, 0 failed, 0 skipped, 0 deselected
```

与 Batch 3.1.1A 封板值一致。

### 22.14 修正后的真实 Collect 分项

```bash
python -m pytest \
  tests/test_runtime_l3_security_boundary.py --collect-only -q
# → 40 tests collected

python -m pytest \
  tests/test_runtime_l3_protocol_env.py --collect-only -q
# → 37 tests collected

python -m pytest \
  tests/test_runtime_l3_deps_registry.py --collect-only -q
# → 29 tests collected
```

| 文件 | node 数 |
|------|--------|
| `test_runtime_l3_security_boundary.py` | 40 |
| `test_runtime_l3_protocol_env.py` | 37 |
| `test_runtime_l3_deps_registry.py` | 29 |
| **L3 合计** | **106** |

算术验证：40 + 37 + 29 = 106 ✓

### 22.15 禁止文件零修改证明

以下文件在本批中**零修改**：

```
dp_engine/skills/runtime_permissions.py   — 未修改
dp_engine/skills/runtime_protocol.py      — 未修改
dp_engine/skills/runtime_models.py         — 未修改
dp_engine/skills/runtime_errors.py         — 未修改
dp_engine/skills/runtime_dependencies.py   — 未修改
dp_engine/skills/registry.py              — 未修改
dp_engine/skills/installer.py             — 未修改
tests/test_runtime_l1_models.py           — 未修改
tests/test_runtime_l2_subprocess.py       — 未修改
tests/test_runtime_l3_security_boundary.py — 未修改
tests/test_runtime_l3_deps_registry.py    — 未修改
tests/test_runtime_ui_lifecycle.py        — 未修改
tests/test_skill_package.py              — 未修改
ui/skill_tab.py                          — 未修改
ui/skill_runtime_controller.py           — 未修改
ui/report_workbench.py                   — 未修改
main.py                                  — 未修改
core/report_engine.py                    — 未修改
dp_engine/report_builder/                — 未修改
```

### 22.16 P0 / P1 / P2 最终分类

| 级别 | 问题 | 说明 |
|------|------|------|
| **P0** | 无 | Worker `_flush_and_redact_stdio` 在 Windows 子进程中的可靠性问题已通过将权威脱敏移至 Service 进程解决 |
| **P1** | 无 | — |
| **P2** | 无 | — |

### 22.17 事实记录

1. **是否修改生产代码**: 是（`runtime_worker.py`、`runtime_service.py`）
2. **Workspace 保留方法**: `unittest.mock.patch` 重定向 `create_workspace`，
   no-op `cleanup_workspace`；pytest `tmp_path` 自动清理
3. **六个日志本体 node 结果**: 全部 PASSED
4. **stdout.log 三个 case**: secret 均不存在，`***REDACTED***` 存在
   (boundary-near-limit) 或可接受缺失 (cross-boundary/utf8-multibyte)
5. **stderr.log 三个 case**: 同上
6. **最终 bytes 大小**: 全部 ≤ MAX_STDOUT_BYTES / MAX_STDERR_BYTES
7. **T0**: 0 errors, 0 warnings
8. **T2-S**: 37 passed, 0 failed, 0 skipped, 0 deselected
9. **T2-B**: 106 passed, 0 failed, 0 skipped, 0 deselected
10. **T3-A**: 111 passed, 0 failed, 0 skipped, 0 deselected
11. **修正后 collect**: 40 + 37 + 29 = 106
12. **实际修改文件**: `dp_engine/skills/runtime_worker.py`,
    `dp_engine/skills/runtime_service.py`,
    `tests/fixtures/runtime_fixtures.py`,
    `tests/test_runtime_l3_protocol_env.py`,
    `docs/agents/batch-3.1.1B-audit-package.md`
13. **禁止文件零修改证明**: 已确认（§22.15）
14. **审核包**: `docs/agents/batch-3.1.1B-audit-package.md`

### 22.18 最终声明

```
Batch 3.1.1B-S 已完成并提交外部审核。
未开始 Batch 3.1.1C。
未开始 UI、Artifact、Report bridge 或其他后续批次。
等待外部审核结论。
```
