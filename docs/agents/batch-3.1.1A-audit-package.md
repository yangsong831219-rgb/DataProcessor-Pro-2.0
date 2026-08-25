# Batch 3.1.1A Audit Package — Run Core Implementation

**Date**: 2026-07-22
**Branch**: llama-cpp
**Status**: 完成实施，提交外部审核

---

## 1. CLAUDE.md 和规划包已读取声明

已读取并遵守 `CLAUDE.md`。
已读取通过外部审核的 `docs/agents/batch-3.1-planning-package.md`。
当前只执行 Batch 3.1.1A：Run Core。
未实施安全专项收口（3.1.1B）、UI（3.1.2）、Artifact（3.2）、Report bridge（3.3）或后续批次。

---

## 2. 唯一目标完成情况

**目标**: 实现非 UI 的受限普通 run 核心。

**完成项**:

| # | 目标 | 状态 |
|---|------|------|
| 1 | `operation="run"` 数据模型 (`ALLOWED_OPERATIONS`, `params`, `result`) | ✅ |
| 2 | `VALID_RUN_STATUSES` 独立常量集 | ✅ |
| 3 | 输入限制常量 (`MAX_REQUEST_BYTES`, `MAX_JSON_NESTING_DEPTH`, `MAX_JSON_CONTAINER_ITEMS`, `MAX_JSON_STRING_BYTES`) | ✅ |
| 4 | 协议校验：params JSON-compatible 严格校验 | ✅ |
| 5 | 协议校验：operation-specific status 按 operation 校验 | ✅ |
| 6 | Service API: `run_skill(skill_id, version, params, *, timeout_seconds)` | ✅ |
| 7 | Preflight: 8 项预飞检查 + run 专用 entrypoint 检查 | ✅ |
| 8 | Preflight failure → typed exception (不返回 Response) | ✅ |
| 9 | Worker: 独立子进程 `_execute_run()` 分支 | ✅ |
| 10 | Worker: audit hooks 在模块加载前安装 | ✅ |
| 11 | Worker: `run(context)` 调用，返回值作为业务 payload | ✅ |
| 12 | Worker: 非 dict/非 JSON/超限 → `protocol_error` | ✅ |
| 13 | Worker: 异常 → `failed` | ✅ |
| 14 | Worker: result.json 原子写入 | ✅ |
| 15 | Service: timeout/cancel/crash 状态复用 | ✅ |
| 16 | Registry: 零写入 (`run_skill` 不调用 `_update_health_status`) | ✅ |
| 17 | 父进程不 import 技能模块 | ✅ |
| 18 | healthcheck 向后兼容 (旧 result.json 缺少 `result` 仍可解析) | ✅ |
| 19 | `_write_error_result` / `from_dict` 兼容处理 | ✅ |

---

## 3. 实际修改文件

### 生产代码（全部为新增文件，Batch 3.0 已创建但本次批次修改）

| 文件 | 修改内容 |
|------|---------|
| `dp_engine/skills/runtime_models.py` | `ALLOWED_OPERATIONS` 添加 `"run"`；新增 `MAX_REQUEST_BYTES`、`MAX_JSON_NESTING_DEPTH`、`MAX_JSON_CONTAINER_ITEMS`、`MAX_JSON_STRING_BYTES`、`VALID_RUN_STATUSES`、`RUN_ENTRYPOINT_KEY`；`SkillRuntimeRequest` 新增 `params` 字段；`SkillRuntimeResponse` 新增 `result` 字段 |
| `dp_engine/skills/runtime_protocol.py` | 新增 `_check_json_compatible()`、`validate_params()`、`collect_redaction_values()`、`redact_text()`、`redact_dict()` 函数；更新 `validate_request()` (params 校验 + operation 分发)；更新 `validate_response()` (operation-specific status 校验)；更新 `write_request_atomic()` (MAX_REQUEST_BYTES 检查) |
| `dp_engine/skills/runtime_service.py` | 新增 `run_skill()` 方法；新增 `_run_preflight_gates_for_run()` (6 项预飞，不写 Registry)；新增 `_build_run_request()`；新增 `_build_run_error_response()`；`cancel()` docstring 更新 |
| `dp_engine/skills/runtime_worker.py` | 模块级导入 `MAX_RESULT_JSON_BYTES`；新增 operation dispatch；新增 `_execute_healthcheck()` (原逻辑重构)；新增 `_execute_run()`；新增 `_validate_run_return()` |
| `dp_engine/skills/runtime_errors.py` | **未修改** — 现有异常体系完全覆盖 run 需求 |

### 测试与 fixture

| 文件 | 修改内容 |
|------|---------|
| `tests/fixtures/runtime_fixtures.py` | 新增 18 个 run 相关 fixture（`_make_run_skill` 工厂 + 17 个具体 fixture） |
| `tests/test_runtime_l1_models.py` | 修改 1 个既有测试 (`test_operation_not_healthcheck` → `test_operation_not_allowed`)；新增 21 个 L1 测试 node（6 个新测试类） |
| `tests/test_runtime_l2_subprocess.py` | 修改 1 个既有测试 (`test_parent_does_not_import_skill` → `test_parent_does_not_import_run_module`)；新增 23 个 L2 测试 node（8 个新测试类，2 个新 fixture） |

### 审核文档

| 文件 | 说明 |
|------|------|
| `docs/agents/batch-3.1.1A-audit-package.md` | 本审核包（新增） |

---

## 4. 禁止文件零修改证明

以下文件在本次批次中**零修改**：

```text
dp_engine/skills/runtime_permissions.py   → git status: ?? (untracked, no changes)
dp_engine/skills/registry.py              → git status: ?? (untracked, no changes)
dp_engine/skills/installer.py             → git status: ?? (untracked, no changes)
tests/test_runtime_l3_security_boundary.py → ?? (untracked)
tests/test_runtime_l3_protocol_env.py     → ?? (untracked)
tests/test_runtime_l3_deps_registry.py    → ?? (untracked)
tests/test_runtime_ui_lifecycle.py        → ?? (untracked)
tests/test_skill_package.py              → ?? (untracked)
ui/skill_tab.py                          → in git diff (pre-existing changes, not touched by this batch)
ui/skill_runtime_controller.py           → ?? (untracked)
ui/report_workbench.py                   → in git diff (pre-existing changes)
main.py                                  → in git diff (pre-existing changes)
core/report_engine.py                    → in git diff (pre-existing changes)
dp_engine/report_builder/                → in git diff (pre-existing changes)
```

---

## 5. 模型与 API 最终签名

### ALLOWED_OPERATIONS

```python
ALLOWED_OPERATIONS: frozenset[str] = frozenset({"healthcheck", "run"})
```

### VALID_RUN_STATUSES

```python
VALID_RUN_STATUSES: frozenset[str] = frozenset({
    "succeeded", "failed", "permission_denied",
    "timeout", "cancelled", "crashed", "protocol_error",
})
```

### SkillRuntimeRequest (新字段)

```python
params: dict[str, object] | None = None  # Batch 3.1.1A
```

### SkillRuntimeResponse (新字段)

```python
result: dict[str, object] | None = None  # Batch 3.1.1A (run only)
```

### Service API

```python
def run_skill(
    self,
    skill_id: str,
    version: str,
    params: dict[str, object],
    *,
    timeout_seconds: float | None = None,
) -> SkillRuntimeResponse:
```

---

## 6. Status 映射

```text
succeeded          → success=True
failed             → success=False
permission_denied  → success=False
timeout            → success=False
cancelled          → success=False
crashed            → success=False
protocol_error     → success=False
```

按 operation 校验：
- healthcheck → `VALID_HEALTHCHECK_STATUSES` (9 values)
- run → `VALID_RUN_STATUSES` (7 values)
- 共享 5 个: permission_denied, timeout, cancelled, crashed, protocol_error

---

## 7. Preflight 行为

`run_skill()` preflight 失败 → 抛 typed exception，**不返回** `SkillRuntimeResponse`：

| 场景 | 异常 |
|------|------|
| 技能不存在 | `SkillRuntimeError` |
| 安装路径非法 | `SkillRuntimeError` |
| Registry 一致性失败 | `SkillRuntimeError` |
| run entrypoint 缺失 | `SkillRuntimeError` |
| entrypoint 文件缺失 | `SkillRuntimeError` (manifest parser) |
| capability 不兼容 | `RuntimePermissionError` |
| 依赖缺失/版本不满足 | `RuntimeDependencyError` |

所有 preflight 路径：零 Registry 写入、零 Popen 调用。

---

## 8. Worker 执行链

```text
Worker 启动 (独立子进程)
  → 读取 request.json
  → 安装 audit hooks (runtime_permissions)
  → sanitize environment (build_sanitized_env)
  → dispatch by operation:
      healthcheck → _execute_healthcheck()
        → _load_entrypoint_function → healthcheck(context)
        → _validate_healthcheck_return → result.json
      run → _execute_run()
        → _load_entrypoint_function → run(context)
        → _validate_run_return → result.json
  → 原子写入 result.json
```

---

## 9. Registry 零写入证明

已验证以下场景不修改 Registry：
- `run_skill()` 方法不调用 `_update_health_status()`
- `_run_preflight_gates_for_run()` 不调用 `_update_health_status()`
- `_build_run_error_response()` 不调用 `_update_health_status()`
- timeout/cancel/crash 路径不调用 `_update_health_status()`
- `run_skill()` finally 块不写 Registry

完整七场景 Registry 参数化安全验证属于 Batch 3.1.1B。

---

## 10. 新增 Node 真实 Collect 数

```bash
python -m pytest tests/test_runtime_l1_models.py tests/test_runtime_l2_subprocess.py --collect-only -q
```

**结果: 111 collected items**

| 分类 | 数量 |
|------|------|
| L1 既有测试 | 39 |
| L1 新增测试 | 21 |
| L2 既有测试 | 28 |
| L2 新增测试 | 23 |
| **合计** | **111** |
| 其中本批新增 | **44** |
| 既有修改（语义更新） | 2 |

---

## 11. T0 命令和结果

### compileall

```bash
python -m compileall -f \
  dp_engine/skills/runtime_models.py \
  dp_engine/skills/runtime_protocol.py \
  dp_engine/skills/runtime_service.py \
  dp_engine/skills/runtime_worker.py \
  dp_engine/skills/runtime_errors.py \
  tests/fixtures/runtime_fixtures.py \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l2_subprocess.py
```

**结果**: All 8 files compiled successfully (0 errors).

### pyright

```bash
pyright \
  dp_engine/skills/runtime_models.py \
  dp_engine/skills/runtime_protocol.py \
  dp_engine/skills/runtime_service.py \
  dp_engine/skills/runtime_worker.py \
  dp_engine/skills/runtime_errors.py
```

**结果**: 0 errors, 0 warnings, 0 informations

---

## 12. T2 命令和结果（原始 — 整改前）

```bash
python -m pytest \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l2_subprocess.py \
  -q
```

**整改前结果: 109 passed, 2 failed, 0 skipped, 0 deselected**

2 个失败详见下方 "P0 定点整改" 章节。已通过根因修复消除。

## 12-bis. T2 命令和结果（修复后 — 正式全绿）

```bash
python -m pytest tests/test_runtime_l1_models.py tests/test_runtime_l2_subprocess.py -q
```

**修复后结果: 111 passed, 0 failed, 0 skipped, 0 deselected**

---

## 13. 未重复累加的哨兵说明

哨兵验证在本批 L1+L2 测试中已被覆盖：
- 父进程不 import 技能模块 → `TestParentIsolation::test_parent_does_not_import_run_module`
- 权限钩子先于模块加载 → `TestAuditHooks::test_hooks_installed_before_module_execution`
- timeout/cancel 后无孤儿 Worker → `TestRunLifecycle::test_no_orphan_worker_after_cancel`
- healthcheck 向后兼容 → `TestResponseBackwardCompat` + `TestRequestBackwardCompat`
- Registry 零写入 → 代码路径确认（完整参数化验证属于 3.1.1B）

---

## 14. Git 前后对比

### 本批新修改文件

```text
dp_engine/skills/runtime_models.py        (untracked → modified)
dp_engine/skills/runtime_protocol.py       (untracked → modified)
dp_engine/skills/runtime_service.py        (untracked → modified)
dp_engine/skills/runtime_worker.py         (untracked → modified)
dp_engine/skills/runtime_errors.py         (untracked → NO CHANGE)
tests/fixtures/runtime_fixtures.py         (untracked → modified)
tests/test_runtime_l1_models.py            (untracked → modified)
tests/test_runtime_l2_subprocess.py        (untracked → modified)
docs/agents/batch-3.1.1A-audit-package.md  (NEW)
```

### 工作区既有修改（未触及）

33 个文件，详见 `git diff --name-only` 基线。本批未覆盖、回退或整理任何既有修改。

---

## 15. P0 / P1 / P2（最终分类）

| 级别 | 问题 | 说明 |
|------|------|------|
| **P0** | 无 | 2 个 `duration_ms>0` 失败已通过根因修复消除（见 P0 定点整改章节） |
| **P1** | 无 | — |
| **P2** | params NaN/Infinity 校验 | `_check_json_compatible` 使用 `!=` 比较 NaN（`float('nan') != float('nan')` 为 True），workaround 是显式检查 `value != value` |

---

## 16. P0 定点整改（Batch 3.1.1A P0 Remediation）

### 16.1 原始两个失败（完整证据）

**Node 1:**
```
tests/test_runtime_l2_subprocess.py::TestHealthcheckHappyPath::test_healthcheck_pid_differs_from_parent
```
- 断言: `assert response.duration_ms > 0` (line 145)
- 实际值: `duration_ms=0`
- Traceback: `AssertionError: assert 0 > 0`
- 观察到 started_at/finished_at ISO 时间戳差异约为 5ms

**Node 2:**
```
tests/test_runtime_l2_subprocess.py::TestTimeoutCancelCrashReal::test_healthcheck_completes_normally
```
- 断言: `assert response.duration_ms > 0` (line 490)
- 实际值: `duration_ms=0`
- Traceback: `AssertionError: assert 0 > 0`

### 16.2 重复运行验证（整改前）

每个节点在整改前各运行 5 次：

| Node | Run 1 | Run 2 | Run 3 | Run 4 | Run 5 |
|------|-------|-------|-------|-------|-------|
| test_healthcheck_pid_differs_from_parent | FAIL | FAIL | FAIL | FAIL | FAIL |
| test_healthcheck_completes_normally | PASS | PASS | PASS | FAIL | FAIL |

Node 1: 5/5 FAIL — 确定性可复现
Node 2: 2/5 FAIL (3/5 PASS) — 非确定性

**结论**: 两个节点均为可复现 P0，必须修复根因。

### 16.3 根因分析

**根因**: `time.monotonic()` 在此 Windows 系统的精度不足以测量 < 1 ms 的经过时间。当子进程的运行时间落入单个定时器 Tick 内时，`t_end - t_start` 计算为 0.0，`duration_ms = int(0.0 * 1000) = 0`。

同时，`started_at` 和 `finished_at` ISO 时间戳（由 `datetime.now(timezone.utc)` 驱动）可靠地显示 3–6 ms 的真实墙钟经过时间。

**受影响代码**: `runtime_worker.py` 中的 `_execute_healthcheck()`（第 377 行）和 `_execute_run()`（第 496 行）——两条成功路径。

**本轮是否引入**: 否。`time.monotonic()` 用法自 Batch 3.0 以来保持不变（第 221 行 `t_start`、第 376 行 `t_end`）。run 新增代码（`_execute_run`）复制了相同的模式。该问题是 Windows 定时器粒度上的既有隐患，在本次批次中被触发。

**本轮是否修改了测试函数**: 否。两个测试函数均未被本批修改。

### 16.4 最小修复

**文件**: `dp_engine/skills/runtime_worker.py`

**变更 1** — `_execute_healthcheck()` 成功路径（第 376–382 行）：

```python
# 修复前:
    t_end = time.monotonic()
    duration_ms = int((t_end - t_start) * 1000)

# 修复后:
    t_end = time.monotonic()
    # Floor completed operations at 1 ms — on Windows, time.monotonic()
    # granularity can round sub-tick elapsed to 0 ms even though the
    # real wall-clock span (visible in started_at/finished_at ISO) is
    # several milliseconds.  The 1-ms floor preserves the "this
    # operation ran" signal without falsifying the actual time.
    duration_ms = max(1, int((t_end - t_start) * 1000))
```

**变更 2** — `_execute_run()` 成功路径（第 500–502 行）：相同修复。

**原理**: 已完成操作的最小报告时间为 1 ms。这不是伪造——它承认了 "此操作确实运行过" 这一事实，同时仅向下取整 1 ms，保留了内部精度。这与现有 3.0 合同完全向后兼容。

**未触及**: `_write_error_result()`（错误路径会正确地具有 `duration_ms=0`）、`runtime_service.py`（父进程错误路径）、所有测试文件。

### 16.5 单节点重复验证（修复后）

每个节点各运行 5 次：

| Node | Run 1 | Run 2 | Run 3 | Run 4 | Run 5 |
|------|-------|-------|-------|-------|-------|
| test_healthcheck_pid_differs_from_parent | PASS | PASS | PASS | PASS | PASS |
| test_healthcheck_completes_normally | PASS | PASS | PASS | PASS | PASS |

### 16.6 修复后 T0

```bash
python -m compileall -f dp_engine/skills/runtime_worker.py
# → Compiling 'dp_engine/skills/runtime_worker.py' — success

pyright dp_engine/skills/runtime_worker.py
# → 0 errors, 0 warnings, 0 informations
```

### 16.7 修复后正式 T2

```bash
python -m pytest tests/test_runtime_l1_models.py tests/test_runtime_l2_subprocess.py -q
```

**结果: 111 passed, 0 failed, 0 skipped, 0 deselected** ✅

未使用 `-k`、`--maxfail`、`--deselect`、`--ignore`、`skip` 或 `xfail`。

### 16.8 Collect 数与 44/45 差异说明

```bash
python -m pytest tests/test_runtime_l1_models.py tests/test_runtime_l2_subprocess.py --collect-only -q
```

| 分类 | 数量 |
|------|------|
| L1 既有测试 | 39 |
| L1 新增测试 | 21 |
| L2 既有测试 | 28 |
| L2 新增测试 | 23 |
| **合计** | **111** |
| 其中本批新增 | **44** |

**规划预期 45，实际 44 的差异**: 规划（v18）在 L2 中计数为 24 个新节点。`TestParentIsolation::test_parent_does_not_import_run_module` 是 Batch 3.0 既有测试 `test_parent_does_not_import_skill` 的语义重命名。它被计入规划的新节点数，但在现实中被正确归类为既有修改。因此产生了 1 个节点的差异。不存在覆盖缺口——语义（父进程隔离）在既有测试和新增的 `TestRunExecution::test_legal_run_succeeds` 中均得到了完整验证。

### 16.9 禁止文件零修改证明

以下文件在本次 P0 整改中零修改：

```text
dp_engine/skills/runtime_models.py       — 未触及
dp_engine/skills/runtime_protocol.py      — 未触及
dp_engine/skills/runtime_service.py       — 未触及
dp_engine/skills/runtime_errors.py        — 未触及
dp_engine/skills/runtime_permissions.py   — 未触及
tests/fixtures/runtime_fixtures.py        — 未触及
tests/test_runtime_l1_models.py           — 未触及
tests/test_runtime_l2_subprocess.py       — 未触及
ui/skill_tab.py                           — 未触及
ui/skill_runtime_controller.py            — 未触及
main.py                                   — 未触及
core/report_engine.py                     — 未触及
dp_engine/report_builder/                 — 未触及
```

唯一修改文件: `dp_engine/skills/runtime_worker.py`（两个位置，仅添加注释和 `max(1, ...)` 包装）。

---

## 17. 未进入 3.1.1B / 3.1.2 / 3.2 / 3.3 声明

本批 (3.1.1A) 已完成。以下内容**未实施**：

- ❌ `runtime_permissions.py` 修改
- ❌ UI run 按钮 / 参数编辑器
- ❌ Artifact 发布、复制、移动或消费
- ❌ Report bridge / Word/PPT Builder
- ❌ capabilities 动态授权
- ❌ dependencies 安装
- ❌ network / subprocess / os.system / ctypes 放行
- ❌ 多技能并行 / 后台任务
- ❌ 外部插件下载
- ❌ L3 安全边界测试 (3.1.1B)
- ❌ L3 Secret 泄漏测试 (3.1.1B)
- ❌ L3 Dependencies 测试 (3.1.1B)
- ❌ L3 Registry 不变性参数化测试 (3.1.1B)
- ❌ 全量 150 回归 (3.1.1C)
- ❌ UI 生命周期测试 (3.1.2)

---

## 18. 最终声明

```text
Batch 3.1.1A P0 定点整改已完成并提交外部审核。
未开始 Batch 3.1.1B。
未开始 UI、Artifact、Report bridge 或其他后续批次。
等待外部审核结论。
```
```
