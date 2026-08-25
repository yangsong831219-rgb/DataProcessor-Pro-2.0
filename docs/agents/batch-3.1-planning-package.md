# Batch 3.1 — 范围冻结与规划合同校正

**日期**: 2026-07-22
**分支**: llama-cpp
**状态**：Batch 3.1 Run Status 与 Redaction 最终校正已完成，提交外部审核。
Batch 3.1.1 尚未批准实施。
未修改任何生产代码或测试代码。

---

## 1. 第三批后续批次冻结定义

外部审核已明确冻结以下批次边界。本规划包是这些定义的权威来源。

### 批次边界

```text
Batch 3.0 ── 已封板 (healthcheck only)
    │
Batch 3.1 ── 受限普通 run 的核心执行协议与生命周期
    │
Batch 3.2 ── Artifact 安全发布与消费
    │
Batch 3.3 ── Report bridge / adapters
```

### 关键澄清

1. **`RuntimeArtifact` 模型已存在不等于 artifact 功能已实现或已批准。**
   — 模型定义只是结构性预留，healthcheck 从不填充 artifacts 字段。
   — Artifact 的发布、复制、移动、消费一律属于 Batch 3.2，不在 3.1 范围内。

2. **`ui/skill_tab.py` 注释中的"后续批次"不等于 Batch 3.1 的冻结需求。**
   — 原始注释列出了三个领域：SkillRuntime、报告桥接、PPT Master。
   — 其中仅"受限 run 核心协议"进入 3.1；其余两项分别进入 3.2 和 3.3。

3. **Report bridge 不进入 Batch 3.1。**
   — `report_backend` skill_type 的 generate entrypoint 调用属于 3.3。

4. **Word/PPT Builder 不进入 Batch 3.1。**
   — Builder 内部实现修改不在任何已批准范围内。

5. **Artifact 文件发布不进入 Batch 3.1.1。**
   — Worker 可以在 workspace/output 内写入临时文件用于自身计算，但这些文件不得被发布、复制、移动或消费为正式 artifact。

6. **任何代码实施必须等待对应子批次规划通过。**

---

## 2. 当前 3.0 封板基线（不可回退）

以下基线来自 Batch 3.0.6 审核包，规划必须保护：

1. Runtime 当前只支持 `healthcheck`（`ALLOWED_OPERATIONS = frozenset({"healthcheck"})`）
2. 普通 `run` 当前仍禁用（UI 按钮 `setEnabled(False)`）
3. 技能模块只在独立 Worker 子进程加载（`subprocess.Popen`，`shell=False`）
4. 父进程不得 import 技能代码
5. 权限钩子必须在技能模块执行前安装
6. 文件访问继续使用显式允许根，不得恢复整体 `sys.path` 放行
7. 网络、子进程、`os.system`、ctypes/native load 默认拒绝
8. Dependencies 只检查，不安装
9. Runtime 不是完整 OS 沙箱，只是受限子进程和最佳努力限制
10. Registry 事务、取消、超时、崩溃处理和 UI 生命周期不得退化
11. 148 项 Runtime 基线测试必须保持有效
12. 67 项验收矩阵编号和语义不得重新定义

---

## 3. 现有调用链和架构的文字描述

### 3.1 healthcheck 完整调用链

```
UI 层:  AgentSkillWidget._on_start_healthcheck()
        → SkillRuntimeController.start_healthcheck(skill_id, version)
        → _RuntimeWorker.run() [QThread]

服务层: SkillRuntimeService.run_healthcheck()
        1. _run_preflight_gates() — 8 项预飞检查
           ├─ Gate 1: Skill exists in registry
           ├─ Gate 2: install_path within managed dir
           ├─ Gate 3: Registry consistency (FULL mode)
           ├─ Gate 4: Manifest has healthcheck entrypoint
           ├─ Gate 5: Dependencies satisfied (check only, no install)
           ├─ Gate 6: Capabilities compatibility check (not authorization)
           ├─ Gate 7: No forbidden capabilities
           └─ Gate 8: No unrecognized capabilities
        2. _build_request() → SkillRuntimeRequest
        3. create_workspace() → workspace dir
        4. write_request_atomic() → request.json
        5. _update_health_status("checking")
        6. _run_subprocess() → Popen → poll loop → timeout/cancel/terminate/kill
        7. read_response_atomic() → SkillRuntimeResponse
        8. _update_health_status(response.status)

Worker: runtime_worker.py [独立子进程]
        → 读取 request.json
        → 安装 audit hooks (permissions)
        → importlib 动态加载 skill 模块
        → 调用 healthcheck(HealthcheckContext)
        → 校验返回值 (healthy: bool)
        → 原子写入 result.json
```

### 3.2 request/response 数据流

```
SkillRuntimeRequest                  SkillRuntimeResponse
├── protocol_version: int            ├── protocol_version: int
├── task_id: str                     ├── task_id: str
├── operation: str ("healthcheck")   ├── operation: str
├── skill_id: str                    ├── success: bool
├── version: str                     ├── status: str (9 valid values)
├── installed_path: str              ├── message: str
├── entrypoint: str                  ├── started_at: ISO 8601
├── workspace_path: str              ├── finished_at: ISO 8601
├── timeout_seconds: float           ├── duration_ms: int
├── capabilities: tuple[str, ...]    ├── health: dict | None
├── environment: dict[str, str]      ├── artifacts: tuple[RuntimeArtifact, ...]
└── params: dict[str, object] | None ├── warnings: tuple[str, ...]
    (Batch 3.1.1 新增)               ├── error: RuntimeErrorInfo | None
                                     └── result: dict[str, object] | None
                                         (Batch 3.1.1 新增)
```

### 3.3 关键代码位置

| 组件 | 路径 | 关键行/函数 |
|------|------|------------|
| 操作门禁 | `dp_engine/skills/runtime_models.py:21` | `ALLOWED_OPERATIONS = frozenset({"healthcheck"})` |
| 大小限制常量 | `dp_engine/skills/runtime_models.py:46-51` | `MAX_STDOUT_BYTES`, `MAX_STDERR_BYTES`, `MAX_RESULT_JSON_BYTES`, `MAX_OUTPUT_TOTAL_BYTES`, `MAX_SINGLE_FILE_BYTES`, `MAX_FILE_COUNT` |
| 协议校验 | `dp_engine/skills/runtime_protocol.py:142-233` | `validate_request()` — operation NOT in ALLOWED_OPERATIONS → RuntimeProtocolError |
| 服务入口 | `dp_engine/skills/runtime_service.py:136` | `run_healthcheck()` — 唯一公开执行方法 |
| 服务 preflight | `dp_engine/skills/runtime_service.py:317` | `_run_preflight_gates()` — 抛异常，不 normalization |
| Worker 入口 | `dp_engine/skills/runtime_worker.py` | 仅支持 healthcheck 路径；main() 按 operation 分发 |
| 权限 | `dp_engine/skills/runtime_permissions.py` | `build_sanitized_env()` + audit hooks |
| 依赖 | `dp_engine/skills/runtime_dependencies.py` | `SkillDependencyChecker` — check only |
| 模型 | `dp_engine/skills/models.py:233` | `SkillManifest` — entrypoints dict, capabilities list |
| 入口点 | `dp_engine/skills/models.py:67` | `HEALTHCHECK_ENTRYPOINT_KEY = "healthcheck"` |
| UI controller | `ui/skill_runtime_controller.py` | `SkillRuntimeController` — 仅 `start_healthcheck()` |
| UI 页面 | `ui/skill_tab.py:398-401` | `run_btn.setEnabled(False)` + tooltip "SkillRuntime 将在后续批次实现" |

---

## 4. 冻结的 3.1、3.2、3.3 边界

### Batch 3.1: 受限普通 run 的核心执行协议与生命周期

**纳入**: operation="run" 的模型、协议、Worker、service、安全边界、非 UI 测试。

**排除**: UI 变更、artifact 发布、report bridge、capabilities 动态授权。

### Batch 3.2: Artifact 安全发布与消费

**纳入**: artifact schema、output 根、path resolve、symlink、数量/类型/大小限制、原子发布、取消和失败清理、生命周期和所有权。

**前提**: Batch 3.1 封板。

### Batch 3.3: Report bridge / adapters

**纳入**: report_backend 技能在报告工作台中的调用、artifact 到 Word/PPT 的适配。

**前提**: Batch 3.2 封板。

---

## 5. Batch 3.1.1 冻结范围

### 5.1 唯一目标

```text
在不扩大既有权限边界的前提下，
为独立 Worker 增加受限 operation="run" 核心执行协议，
支持 JSON-compatible 输入与结构化 JSON-compatible 结果。
```

### 5.2 允许纳入

| # | 项目 | 说明 |
|---|------|------|
| 1 | `operation="run"` 数据模型和协议校验 | `ALLOWED_OPERATIONS` 添加 `"run"`；协议校验接受 run |
| 2 | run entrypoint 定位、解析、存在性和 callable 校验 | 使用 manifest `entrypoints.run`，独立于 healthcheck |
| 3 | Worker 中执行 run entrypoint | 与 healthcheck 共享子进程架构 |
| 4 | JSON-compatible 输入参数 (`params`) | dict 形式，严格 JSON-compatible 校验 |
| 5 | JSON-compatible 返回值 | dict 形式，严格 JSON-compatible 校验 |
| 6 | result.json 的 schema、大小和原子写入 | 复用现有 `_atomic_write_json` 机制 |
| 7 | timeout、cancel、terminate/kill 状态机 | 复用现有 `_run_subprocess()` + `cancel()` |
| 8 | stdout/stderr 限制 | 复用现有 `MAX_STDOUT_BYTES` / `MAX_STDERR_BYTES` |
| 9 | dependencies 只检查、不安装 | 复用现有 `SkillDependencyChecker` |
| 10 | 父进程不 import 技能代码 | 不变 |
| 11 | 原有 `healthcheck` 行为和既有 Runtime 回归保持不变 | 148 项既有 Runtime 回归 + 2 项 installer symlink 回归必须零失败 |

### 5.3 明确排除

| # | 排除项 | 理由 |
|---|--------|------|
| 1 | UI 普通 run 按钮启用 | 属于 Batch 3.1.2 (Run UI Lifecycle) |
| 2 | UI 参数编辑器 | 属于 Batch 3.1.2 |
| 3 | artifact 文件发布、复制、移动或消费 | 属于 Batch 3.2 |
| 4 | report bridge | 属于 Batch 3.3 |
| 5 | report adapters | 属于 Batch 3.3 |
| 6 | Word Builder 修改 | 不在任何已批准范围 |
| 7 | PPT Builder 修改 | 不在任何已批准范围 |
| 8 | Chart 模块修改 | 不在任何已批准范围 |
| 9 | dependencies 自动安装 | 排除于 Batch 3.1.1。未来若要开放，必须另行规划和外部审核。 |
| 10 | 网络权限 | 排除于 Batch 3.1.1。未来若要开放，必须另行规划和外部审核。 |
| 11 | subprocess 或 `os.system` 权限 | 排除于 Batch 3.1.1。未来若要开放，必须另行规划和外部审核。 |
| 12 | ctypes/native load | 排除于 Batch 3.1.1。未来若要开放，必须另行规划和外部审核。 |
| 13 | 任意项目目录读取 | 排除于 Batch 3.1.1。未来若要开放，必须另行规划和外部审核。 |
| 14 | Registry 或 installed 目录写入 | 排除于 Batch 3.1.1。未来若要开放，必须另行规划和外部审核。 |
| 15 | capabilities 动态授予 | 排除于 Batch 3.1.1。未来若要开放，必须另行规划和外部审核。 |
| 16 | 多技能并行 | 排除于 Batch 3.1.1。未来若要开放，必须另行规划和外部审核。 |
| 17 | 长时后台任务 | 排除于 Batch 3.1.1。未来若要开放，必须另行规划和外部审核。 |
| 18 | 外部插件市场下载 | 排除于 Batch 3.1.1。未来若要开放，必须另行规划和外部审核。 |
| 19 | 完整 OS sandbox | 非本项目范围 |

---

## 6. Batch 3.1 子批次结构（冻结）

```text
Batch 3.0 (已封板)
    │
    ├── Batch 3.1.1: Run Core Protocol
    │   ├── 目标: operation="run" 在 Worker 中可执行，非 UI
    │   ├── 允许修改: runtime_models.py, runtime_protocol.py,
    │   │             runtime_service.py, runtime_worker.py,
    │   │             runtime_errors.py,
    │   │             测试文件 (L1/L2/L3, 非 UI)
    │   ├── 禁止修改: ui/skill_tab.py, main.py,
    │   │             core/report_engine.py, dp_engine/report_builder/,
    │   │             ui/report_workbench.py, ui/skill_runtime_controller.py,
    │   │             dp_engine/skills/runtime_permissions.py
    │   ├── 测试门槛: 80 新增 node PASS + 148 Runtime 回归 PASS + 2 installer symlink 回归 PASS
    │   ├── 审核包: docs/agents/batch-3.1.1-audit-package.md
    │   └── 可独立回滚
    │
    ├── Batch 3.1.2: Run UI Lifecycle
    │   ├── 目标: run 按钮、参数提交、取消、结果展示、Widget 生命周期
    │   ├── 允许修改: ui/skill_tab.py, ui/skill_runtime_controller.py
    │   ├── 禁止修改: runtime_models.py, runtime_protocol.py,
    │   │             runtime_service.py, runtime_worker.py (3.1.1 已封板),
    │   │             core/report_engine.py, dp_engine/report_builder/
    │   ├── 前提: 3.1.1 审核通过
    │   └── 可独立回滚
    │
    ├── Batch 3.2: Artifact Security
    │   └── 前提: 3.1 封板
    │
    └── Batch 3.3: Report Bridge
        └── 前提: 3.2 封板
```

---

## 7. 冻结权限决策

### 核心决策

```text
manifest capabilities 在 Batch 3.1.1 中只用于兼容性检查和拒绝，
不得用于扩大 Worker 权限。
```

### 详细规则

**run operation 的 capabilities 处理:**

1. Manifest 中声明的 `capabilities` 列表在 preflight gate 中被读取。
2. 任何在 `FORBIDDEN_HEALTHCHECK_CAPABILITIES` 中的 capability → `RuntimePermissionError`，拒绝执行。
3. 任何不在 `DEFAULT_HEALTHCHECK_CAPABILITIES` 中的 capability → `RuntimePermissionError`，拒绝执行。
4. **run 与 healthcheck 使用完全相同的 capabilities 集合。**
5. 不存在"run 专用 capability"——所有能力必须已在 healthcheck 允许集中。

### 保持不变的权限边界

| 边界 | 状态 | 说明 |
|------|------|------|
| network | 拒绝 | socket 创建/connect 均拒绝 |
| subprocess | 拒绝 | `Popen`/`run`/`call` 等均拒绝 |
| `os.system` | 拒绝 | shell 命令执行拒绝 |
| ctypes/native load | 拒绝 | `ctypes.CDLL`/`cffi` 等拒绝 |
| installed 目录写入 | 拒绝 | 技能不可修改已安装文件 |
| Registry 读取/写入 | 拒绝 | 技能不可访问 Registry |
| 其他技能目录 | 拒绝 | 仅可读自身安装目录 |
| project root / CWD | 拒绝 | 不因 run 而自动放行 |
| 文件写入 | 仅 workspace/output + temp | 不允许写 workspace 外 |
| 文件读取 | 仅当前封板的显式 roots | 不允许读外部文件 |
| symlink / `..` 逃逸 | 拒绝 | resolve + relative_to 检查 |
| 父进程环境 secret | 拒绝进入 Worker/env/log/result | `build_sanitized_env()` 不变 |
| 用户敏感 params | 允许传入 Worker，但 log/error/result 必须 redaction | per-task redaction values |

---

## 8. 冻结 run response 向后兼容模型

### 8.1 核心决策

```text
Batch 3.1.1 继续复用 SkillRuntimeResponse。
只以向后兼容方式增加 run 所需的 result 字段。
```

### 8.2 拟议模型

```python
@dataclass(frozen=True)
class SkillRuntimeResponse:
    protocol_version: int
    task_id: str
    operation: str
    success: bool
    status: str
    message: str
    started_at: str           # ISO 8601 UTC
    finished_at: str          # ISO 8601 UTC
    duration_ms: int
    health: dict[str, object] | None = None
    result: dict[str, object] | None = None       # Batch 3.1.1 新增
    artifacts: tuple[RuntimeArtifact, ...] = ()
    warnings: tuple[str, ...] = ()
    error: RuntimeErrorInfo | None = None
```

### 8.3 healthcheck 兼容合同

```text
health：继续使用现有值
result：None
artifacts：保持现有兼容行为（空 tuple）
其余字段：完全保持 3.0 行为
```

### 8.4 run 成功合同

```text
operation="run"
success=True
status="succeeded"
result=技能返回的业务 dict
health=None
artifacts=()
error=None
message、started_at、finished_at、duration_ms、warnings 正常保留
```

### 8.5 run 失败合同

```text
operation="run"
success=False
result=None
health=None
artifacts=()
error=经过 redaction 的 RuntimeErrorInfo
其余兼容字段正常保留
```

### 8.6 不可变规则

* `success` 由 Runtime 根据 status 推导；
* 技能不能控制 `success` 或 envelope status；
* 技能返回的 `ok`、`status`、`error` 等 key 只是 `result` 内普通业务数据；
* 不删除现有 `artifacts` 字段，只保证 run 时为空；
* 不删除 `message`、时间字段、duration、warnings；
* 不修改现有 healthcheck response schema；
* `read_response_atomic()` 必须继续兼容旧 healthcheck result.json（`from_dict` 对缺失 key 使用 `.get()`，天然兼容）。

### 8.7 新增 response 兼容测试

| 编号 | 语义 | 预期 |
|------|------|------|
| R-01 | healthcheck response roundtrip 在增加 result 字段后保持不变 | 旧格式 result.json 仍正确解析 |
| R-02 | run response 保留全部兼容字段 | protocol_version, task_id, operation, success, status, message, started_at, finished_at, duration_ms, artifacts, warnings, error 全部存在且类型正确 |
| R-03 | run response 的 artifacts 恒为空 tuple | artifacts=() |
| R-04 | run response 的 health 恒为 None | health=None |
| R-05 | success 与 status 映射由 Runtime 控制 | 技能返回 `{"status": "succeeded"}` 不改变 envelope status |

---

## 9. 冻结 Service API 和 Request 模型

### 9.1 Service API

```python
def run_skill(
    self,
    skill_id: str,
    version: str,
    params: dict[str, object],
    *,
    timeout_seconds: float | None = None,
) -> SkillRuntimeResponse:
    ...
```

* `skill_id`、`version`、`params`、`timeout_seconds` 是 service 调用参数。
* `params` 是用户可控业务数据。
* 其余所有字段（task_id、operation、installed_path、workspace_path、entrypoint、capabilities、environment）均由 Runtime 构建，不得由用户传入。

### 9.2 Request 模型向后兼容扩展

在现有 `SkillRuntimeRequest` 中向后兼容地增加：

```python
params: dict[str, object] | None = None
```

**healthcheck request 合同：**

```text
operation="healthcheck"
params=None
现有字段和序列化结果保持兼容
```

**run request 合同：**

```text
operation="run"
params=已经严格校验的用户业务参数
task_id、skill_id、version、installed_path、workspace_path、
entrypoint、timeout_seconds、capabilities、environment 均由 Runtime 生成
```

### 9.3 版本字段

继续使用现有：

```text
protocol_version
```

作为 wire protocol 版本（`RUNTIME_PROTOCOL_VERSION = 1`）。不得建立含义重叠的第二版本字段。删除单独的 `schema_version`。

### 9.4 三层边界

```text
service 调用参数：
skill_id、version、params、timeout_seconds

用户可控业务数据：
params

Runtime trusted fields：
task_id、operation、installed_path、workspace_path、entrypoint、
capabilities、environment 和其他内部字段
```

用户不能通过 `params` 覆盖 trusted fields。若 `params` 中出现 `task_id`、`operation` 等 key，它们仅作为普通业务 key 存在于 `params` dict 中，不覆盖 envelope 级同名字段。

### 9.5 新增 request 兼容测试

| 编号 | 语义 | 预期 |
|------|------|------|
| RQ-01 | healthcheck request 在新增 params 后 roundtrip 不变 | to_dict/from_dict 往返后所有字段一致 |
| RQ-02 | run request params roundtrip | params 正确序列化与反序列化 |
| RQ-03 | 用户 params 中出现 task_id/operation 等 key 不覆盖 envelope trusted fields | envelope task_id/operation 仍为 Runtime 值 |
| RQ-04 | service run_skill() 只接受 params 作为用户业务输入 | 调用者无法传入 task_id/operation/installed_path |

---

## 10. 冻结输入限制常量

### 10.1 既有常量（来自 `dp_engine/skills/runtime_models.py`）

```python
MAX_STDOUT_BYTES = 1 * 1024 * 1024        # 1 MB — 复用
MAX_STDERR_BYTES = 1 * 1024 * 1024        # 1 MB — 复用
MAX_RESULT_JSON_BYTES = 1 * 1024 * 1024   # 1 MB — 复用（response 端）
```

**注意**：当前代码中**不存在** `MAX_REQUEST_BYTES` 常量。既有 `write_request_atomic()` 不检查请求大小。

### 10.2 Batch 3.1.1 新增常量

| 常量 | 值 | 说明 |
|------|----|------|
| `MAX_REQUEST_BYTES` | `1 * 1024 * 1024` (1 MB) | 与 `MAX_RESULT_JSON_BYTES` 对齐，对称限制 |
| `MAX_JSON_NESTING_DEPTH` | `32` | 最大 JSON 嵌套深度 |
| `MAX_JSON_CONTAINER_ITEMS` | `10_000` | 最大数组/对象元素数 |
| `MAX_JSON_STRING_BYTES` | `1 * 1024 * 1024` (1 MB) | 单个 JSON 字符串最大 UTF-8 bytes |

所有大小按 UTF-8 bytes 计算，不得混用 Python 字符数。

---

## 11. 冻结错误分类

### 11.1 Preflight configuration failure

发生在 Worker 启动前。`_run_preflight_gates()` 当前**抛出 typed exception**（`SkillRuntimeError` 或其子类），不 normalization 为 `SkillRuntimeResponse`。

包括：

```text
技能不存在                     → SkillRuntimeError
安装路径非法                   → SkillRuntimeError
Registry 一致性失败            → SkillRuntimeError
run entrypoint 缺失           → SkillRuntimeError
entrypoint 路径非法            → SkillRuntimeError (validate_request 阶段)
entrypoint 文件不存在           → SkillRuntimeError
capability 不兼容              → RuntimePermissionError
依赖缺失                       → RuntimeDependencyError
依赖版本不满足                  → RuntimeDependencyError
请求在启动 Worker 前即校验失败    → RuntimeProtocolError
```

**决策**：`SkillRuntimeService.run_skill()` 对所有 preflight failure 原样抛出 typed exception，不 normalization 为 `SkillRuntimeResponse`。

适用范围：
```text
技能不存在
安装路径非法
Registry 一致性失败
run entrypoint 缺失
entrypoint 路径非法
entrypoint 文件不存在
capability 不兼容
依赖缺失
依赖版本不满足
```

共同语义：
```text
抛 typed exception
不返回 SkillRuntimeResponse
不产生 Runtime status
不启动 Worker
Popen 不调用
普通 run 对 Registry 零写入
```

异常类型按现有错误体系使用：
```text
普通配置错误：SkillRuntimeError 或现有更具体子类
权限/能力错误：RuntimePermissionError
依赖错误：RuntimeDependencyError
协议构建错误：RuntimeProtocolError
```

不得为同一场景同时规定"抛异常"和"返回 status"。

### 11.2 Dependency failure

依赖缺失和版本不满足属于 preflight configuration failure，由 `_run_preflight_gates()` 的 Gate 5 处理，抛出 `RuntimeDependencyError`（typed exception），不产生 `SkillRuntimeResponse`，不启动 Worker。

`dependency_missing` 可以作为既有 healthcheck 兼容状态保留在 `VALID_HEALTHCHECK_STATUSES` 中，但不是 Batch 3.1.1 `run_skill()` preflight 的返回 status。

缺少 run entrypoint **不得**映射成 `dependency_missing`。它属于 preflight configuration failure（`SkillRuntimeError`）。

### 11.3 Succeeded

Worker 成功写出合法 response，技能返回合法 JSON-compatible dict：

```text
技能返回合法 dict              → status="succeeded", success=True
业务 payload {"ok": false}     → status="succeeded", success=True
正常完成                       → status="succeeded", success=True
```

### 11.4 Failed

Worker 成功写出合法错误 response，但技能执行失败（非协议类、非权限类）：

```text
function 不存在               → status="failed", success=False
function 不可调用              → status="failed", success=False
技能入口抛出但被 Worker 捕获   → status="failed", success=False
其他受控 Worker 执行失败       → status="failed", success=False
```

Worker 已成功写出合法 `result.json` → `status="failed"`。

### 11.5 Protocol error

Worker 成功写出合法错误 response，但返回值违反协议：

```text
技能返回非 dict               → status="protocol_error", success=False
技能返回非 JSON-compatible     → status="protocol_error", success=False
NaN/Infinity                   → status="protocol_error", success=False
result 序列化超限              → status="protocol_error", success=False
Worker 写出 response schema 非法但仍能形成受控错误 response → status="protocol_error"
```

不得再写 `RuntimeProtocolError, status="failed"`。同一场景必须明确是 Worker 返回 `status="protocol_error"` 或父进程读取阶段抛 `RuntimeProtocolError` 不返回 response，二选一。

### 11.6 Permission denied（运行期）

Worker 已启动后，audit hook 拒绝以下操作。若 Worker 成功捕获并写出合法错误 response：

```text
socket 创建/connect            → status="permission_denied", success=False
subprocess / os.system         → status="permission_denied", success=False
ctypes / native load           → status="permission_denied", success=False
写 installed / workspace 外    → status="permission_denied", success=False
读取/修改 Registry             → status="permission_denied", success=False
symlink / .. 路径逃逸          → status="permission_denied", success=False
访问 project root/CWD/home     → status="permission_denied", success=False
```

error 中保留经过 redaction 的安全信息。

### 11.7 Timeout

```text
Worker 超时 → status="timeout", success=False
```

全文禁止 `status="timed_out"`。

### 11.8 Cancelled

```text
用户取消 → status="cancelled", success=False
```

### 11.9 Crashed

只用于 Worker 无法写出合法 response：

```text
Worker 非零异常退出且未形成合法 response → status="crashed", success=False
缺失 result.json                      → status="crashed", success=False
result.json 损坏到无法解析             → status="crashed", success=False
进程被外部异常终止                     → status="crashed", success=False
```

### 11.10 修正矩阵

| 原编号 | 原错误分类 | 修正后分类 |
|--------|----------|----------|
| 3.1.1-12 | `status="dependency_missing"` | preflight configuration failure（`SkillRuntimeError`，run entrypoint 缺失） |
| 3.1.1-14 | `status="crashed"` | `status="failed"`（handled failure：function 缺失但 Worker 写出合法错误 response） |
| 3.1.1-15 | `status="crashed"` | `status="failed"`（handled failure：function 不可调用但 Worker 写出合法错误 response） |
| 3.1.1-30 | `status="failed"`（非 dict 返回） | `status="protocol_error"`（协议失败，Worker 写出合法错误 response） |
| 3.1.1-31 | `status="failed"`（非 JSON 返回） | `status="protocol_error"`（协议失败，Worker 写出合法错误 response） |
| 3.1.1-32 | `status="failed"`（result 超限） | `status="protocol_error"`（协议失败，Worker 写出合法错误 response） |
| 3.1.1-39 | `status="timed_out"` | `status="timeout"`（全文统一，禁止 timed_out） |
| 3.1.1-45 至 56 | `RuntimeError`/`PermissionError` | `status="permission_denied"`（运行期 audit hook 拒绝，Worker 写出合法错误 response） |
| 3.1.1-67/68 | `status="dependency_missing"` | preflight configuration failure（`RuntimeDependencyError`，typed exception，不产生 response） |

---

## 12. Business failure 语义

### 12.1 核心定义

技能返回：

```json
{"ok": false}
```

时：

```text
Runtime status 仍为 "succeeded"
success=True
result={"ok": false}
```

因为这是合法业务 payload，不是 Runtime 执行失败。

### 12.2 术语

可称其为 **business-declared negative outcome**。不得列为新的 Runtime status。

### 12.3 Registry 不变性断言

```text
payload {"ok": false}
envelope status="succeeded"
Registry 完全不变（内存 + 磁盘 + 所有字段）
```

---

## 12-bis. Runtime Status 常量冻结（只读核对后决策）

以下内容来自 `dp_engine/skills/runtime_models.py:79-89` 的只读核对，不修改生产代码。

### 12-bis.1 现有 VALID_HEALTHCHECK_STATUSES（保持完全不变）

```python
VALID_HEALTHCHECK_STATUSES: frozenset[str] = frozenset({
    "healthy",
    "unhealthy",
    "not_supported",
    "dependency_missing",
    "permission_denied",
    "timeout",
    "cancelled",
    "crashed",
    "protocol_error",
})
```

### 12-bis.2 Batch 3.1.1 新增 VALID_RUN_STATUSES

```python
VALID_RUN_STATUSES: frozenset[str] = frozenset({
    "succeeded",
    "failed",
    "permission_denied",
    "timeout",
    "cancelled",
    "crashed",
    "protocol_error",
})
```

不得把 `VALID_RUN_STATUSES` 与 `VALID_HEALTHCHECK_STATUSES` 合并成一个无差别集合。

### 12-bis.3 协议按 operation 校验

```text
operation="healthcheck"
→ status 必须属于 VALID_HEALTHCHECK_STATUSES

operation="run"
→ status 必须属于 VALID_RUN_STATUSES
```

具体地：
```text
healthcheck 不接受 run-only status：succeeded、failed
run 不接受 healthcheck-only status：healthy、unhealthy、not_supported、dependency_missing
run 接受 shared status：permission_denied、timeout、cancelled、crashed、protocol_error
healthcheck 接受全部九个原有 status
```

### 12-bis.4 Run status 与 success 的完整映射

唯一映射，由 Runtime 生成，不接受技能返回值控制：

```text
run status="succeeded"        → success=True

run status 为以下任一值        → success=False：
  failed
  permission_denied
  timeout
  cancelled
  crashed
  protocol_error
```

技能返回 `{"ok": false}` 时：

```text
status="succeeded"
success=True
result={"ok": false}
```

因为这是合法业务 payload，不是 Runtime 执行失败。

### 12-bis.5 禁止新增的 status

```text
timed_out       — 使用 timeout
success         — 使用 succeeded
error           — 使用 failed
business-negative — 不是 Runtime status（是业务 payload 语义）
```

### 12-bis.6 禁止事项

```text
不新增同义状态（例如同时出现 timeout 与 timed_out）
typed preflight exception 不得伪装成 response status
"business-negative" 不是 Runtime status
```

---

## 13. Secret 的两种来源

### 13.1 父进程环境 secret

来源：`build_sanitized_env()` 已知的 forbidden patterns 和 denied keys。

已知 secret key pattern（来自 `runtime_permissions.py:58-60`）：

```python
_FORBIDDEN_ENV_PATTERNS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "KEY")
```

已知 denied exact keys（来自 `runtime_permissions.py:323-328`）：

```python
"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
"GITHUB_TOKEN", "AZURE_API_KEY",
"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"
```

规则：

```text
不得进入 Worker environment
不得进入 stdout/stderr
不得进入 result.json
不得进入 runtime.log
不得进入 error message
```

### 13.2 用户显式提供的敏感 params

用户可能通过 `params` 传入敏感键名，例如：

```text
password
token
api_key
secret
credential
```

Runtime 必须建立 per-task redaction values。

#### 13.2.1 冻结安全规则

1. 用户敏感 params 可以作为显式输入进入 Worker；
2. 原始值不得出现在 stdout；
3. 原始值不得出现在 stderr；
4. 原始值不得出现在 result.json；
5. 原始值不得出现在 runtime.log；
6. 原始值不得出现在 error message；
7. 输出中的敏感值替换为 `***REDACTED***`；
8. 最终用户可见或持久化输出 = `truncate(redact(raw_output))`。流式实现可采用等价算法，但必须保证：
   - secret 跨 chunk 时仍被识别；
   - secret 跨截断边界时仍不泄漏；
   - UTF-8 多字节字符不会造成部分匹配绕过；
9. 空字符串和 `None` 不得加入 redaction values；
10. 敏感键匹配大小写不敏感；
11. 至少识别以下敏感键：

```text
password
token
api_key
secret
credential
```

12. 必须递归扫描嵌套 dict/list 中的敏感键；
13. 普通非敏感值不得被修改。

#### 13.2.2 Redaction 责任位置

```text
Service 层：递归收集 per-task redaction values（从 params 中提取敏感键对应的值）
Worker 层：result/error 在序列化和写盘前 redaction
Service 层：stdout/stderr/runtime.log 在暴露或持久化前 redaction，然后截断
```

不得修改 `runtime_permissions.py`。

### 13.3 普通非敏感 params

普通业务 params 允许被技能作为业务结果返回，正常出现在 result.json 中。

### 13.4 测试必须分别覆盖

| 类别 | 测试目标 | node 数 |
|------|---------|---------|
| A: 父进程环境 secret 隔离 | Worker env、stdout/stderr、result.json、runtime.log、error message 均不含 parent env secret | 5 |
| B: 敏感-key params redaction | 用户传入 password/token 等 → stdout、stderr、result.json、error message、runtime.log 五个输出面均已 redact | 5 |
| C: 普通 params roundtrip | 非敏感 params 可在 result.json 中正常出现 | 1 |
| **L3 Secret 合计** | | **11** |

不得用一个模糊的"secret"测试同时代替三种语义。

---

## 14. Registry 不变性测试设计

### 14.1 核心决策

删除当前六个模糊 node（3.1.1-56 至 3.1.1-61），改为一个参数化测试族，每个终态产生独立 pytest node：

```text
tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[succeeded]

tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[business-negative]

tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[protocol-failed]

tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[dependency-preflight-error]

tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[timeout]

tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[cancelled]

tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[crashed]
```

### 14.2 每个 node 同时断言

```text
完整 Registry 内存快照不变
Registry 磁盘存在性不变
Registry 磁盘 bytes 不变
enabled 不变
active_version 不变
health_status 不变
health_message 不变
last_healthcheck_* 不变
其他 entry 字段不变
```

### 14.3 特殊 node 额外断言

**business-negative** 额外断言：

```text
payload {"ok": false}
envelope status="succeeded"
Registry 完全不变
```

**dependency-preflight-error** 额外断言：

```text
抛 RuntimeDependencyError（typed exception）
不产生 SkillRuntimeResponse
Worker/Popen 未启动
Registry 内存快照不变
Registry 磁盘存在性和 bytes 不变
不调用任何安装器
不 import 目标依赖
```

不得通过内部 for-loop 把七种终态隐藏在单个 pytest node 中。

---

## 15. 安全测试 fixture 语义

### 15.1 Socket 测试分离

不得用同一个不明确 fixture 同时证明 socket 创建拒绝和 socket connect 拒绝。

分别规划：

```text
create_skill_run_socket_create   → 覆盖 socket.__new__ + socket.bind 拒绝
create_skill_run_socket_connect  → 覆盖 socket.connect 拒绝
```

当前审计钩子（`runtime_permissions.py:430-437`）已区分 `socket.__new__`、`socket.bind`、`socket.connect` 三个事件。测试必须分别覆盖精确行为。

### 15.2 路径逃逸测试分离

symlink 和 `..` 路径逃逸应分别拥有可识别的断言。可以参数化，但必须产生独立 node ID：

```text
tests/test_runtime_l3_security_boundary.py::TestRunPathEscape::test_symlink_escape_rejected
tests/test_runtime_l3_security_boundary.py::TestRunPathEscape::test_dot_dot_escape_rejected
```

---

## 16. Installer symlink 强制回归

### 16.1 核心决策

```text
148 个既有 Runtime node
2 个 Batch 3.0.6 installer symlink 安全 node
合计 150 个强制既有回归 node
```

两个完整 node：

```text
tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry
tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction
```

### 16.2 强制要求

```text
2 passed
0 failed
0 skipped
0 deselected
```

不得再出现"可选 symlink""默认 209"等措辞。

### 16.3 最终计划执行总数

```text
80 新增 node + 150 个强制既有回归 node = 230
```

---

## 17. Batch 3.1.1 完整拟议验收矩阵

编号体系：`3.1.1-XX`，不得复用 3.0 的 1–67。
每项包含：编号、精确语义、层级、拟议完整 pytest node ID、预期断言、是否启动真实 Worker。

### 17.1 L1 — 模型与协议 (21 项)

| 编号 | 精确语义 | 层级 | 拟议完整 node ID | 预期断言 | 真实 Worker |
|------|---------|------|-----------------|---------|------------|
| 3.1.1-01 | healthcheck 与 run 均为合法 operation | L1 | `tests/test_runtime_l1_models.py::TestAllowedOperations::test_healthcheck_and_run_are_valid_operations` | 两者均通过 validate_request | 否 |
| 3.1.1-02 | 未知 operation 拒绝 | L1 | `tests/test_runtime_l1_models.py::TestAllowedOperations::test_unknown_operation_rejected` | `RuntimeProtocolError` | 否 |
| 3.1.1-03 | request 顶层未知字段拒绝 | L1 | `tests/test_runtime_l1_models.py::TestRequestValidation::test_unknown_top_level_field_rejected` | `RuntimeProtocolError` | 否 |
| 3.1.1-04 | params 非 dict 拒绝 | L1 | `tests/test_runtime_l1_models.py::TestRequestValidation::test_params_not_dict_rejected` | `RuntimeProtocolError` | 否 |
| 3.1.1-05 | params key 非字符串拒绝 | L1 | `tests/test_runtime_l1_models.py::TestRequestValidation::test_params_key_not_string_rejected` | `RuntimeProtocolError` | 否 |
| 3.1.1-06 | params 非 JSON-compatible 拒绝 | L1 | `tests/test_runtime_l1_models.py::TestRequestValidation::test_params_non_json_compatible_rejected` | `RuntimeProtocolError` | 否 |
| 3.1.1-07 | NaN/Infinity 拒绝 | L1 | `tests/test_runtime_l1_models.py::TestRequestValidation::test_nan_infinity_rejected` | `RuntimeProtocolError` | 否 |
| 3.1.1-08 | 输入嵌套深度超限（`MAX_JSON_NESTING_DEPTH=32`） | L1 | `tests/test_runtime_l1_models.py::TestInputLimits::test_nesting_depth_exceeded_rejected` | `RuntimeProtocolError` | 否 |
| 3.1.1-09 | 输入元素数量超限（`MAX_JSON_CONTAINER_ITEMS=10_000`） | L1 | `tests/test_runtime_l1_models.py::TestInputLimits::test_element_count_exceeded_rejected` | `RuntimeProtocolError` | 否 |
| 3.1.1-10 | 输入字符串长度超限（`MAX_JSON_STRING_BYTES`） | L1 | `tests/test_runtime_l1_models.py::TestInputLimits::test_string_length_exceeded_rejected` | `RuntimeProtocolError` | 否 |
| 3.1.1-11 | 输入序列化大小超限（`MAX_REQUEST_BYTES`） | L1 | `tests/test_runtime_l1_models.py::TestInputLimits::test_serialized_size_exceeded_rejected` | `RuntimeProtocolError` | 否 |
| 3.1.1-12 | healthcheck response roundtrip 在增加 result 字段后保持不变 | L1 | `tests/test_runtime_l1_models.py::TestResponseBackwardCompat::test_healthcheck_response_roundtrip_with_result_field` | 旧格式 result.json 仍正确解析，所有兼容字段不变 | 否 |
| 3.1.1-13 | run response 保留全部兼容字段 | L1 | `tests/test_runtime_l1_models.py::TestResponseBackwardCompat::test_run_response_retains_all_compat_fields` | protocol_version, task_id, operation, success, status, message, started_at, finished_at, duration_ms, artifacts, warnings, error 全部存在且类型正确 | 否 |
| 3.1.1-14 | run response 的 artifacts 恒为空 | L1 | `tests/test_runtime_l1_models.py::TestResponseBackwardCompat::test_run_response_artifacts_always_empty` | artifacts=() | 否 |
| 3.1.1-15 | run response 的 health 恒为 None | L1 | `tests/test_runtime_l1_models.py::TestResponseBackwardCompat::test_run_response_health_always_none` | health=None | 否 |
| 3.1.1-16 | 完整 VALID_RUN_STATUSES 与 success 映射 | L1 | `tests/test_runtime_l1_models.py::TestResponseBackwardCompat::test_success_derived_from_status_by_runtime` | succeeded→True；failed/permission_denied/timeout/cancelled/crashed/protocol_error→False；技能返回 `{"status":"succeeded"}` 不改变 envelope | 否 |
| 3.1.1-17 | operation-specific status 交叉校验 | L1 | `tests/test_runtime_l1_models.py::TestStatusCollections::test_operation_specific_status_validation` | healthcheck 拒绝 succeeded/failed；run 拒绝 healthy/unhealthy/not_supported/dependency_missing；run 接受 permission_denied/timeout/cancelled/crashed/protocol_error；healthcheck 接受全部九个原有 status | 否 |
| 3.1.1-18 | healthcheck request 在新增 params 后 roundtrip 不变 | L1 | `tests/test_runtime_l1_models.py::TestRequestBackwardCompat::test_healthcheck_request_roundtrip_with_params_field` | to_dict/from_dict 往返后所有字段一致 | 否 |
| 3.1.1-19 | run request params roundtrip | L1 | `tests/test_runtime_l1_models.py::TestRequestBackwardCompat::test_run_request_params_roundtrip` | params 正确序列化与反序列化 | 否 |
| 3.1.1-20 | 用户 params 不覆盖 envelope trusted fields | L1 | `tests/test_runtime_l1_models.py::TestRequestBackwardCompat::test_user_params_do_not_override_trusted_fields` | envelope task_id/operation 等仍为 Runtime 值 | 否 |
| 3.1.1-21 | service run_skill() 签名只接受 params 作为用户输入 | L1 | `tests/test_runtime_l1_models.py::TestRequestBackwardCompat::test_service_only_accepts_params_as_user_input` | 调用者无法传入 task_id/operation/installed_path | 否 |

### 17.2 L2 — Worker 执行 (17 项)

| 编号 | 精确语义 | 层级 | 拟议完整 node ID | 预期断言 | 真实 Worker |
|------|---------|------|-----------------|---------|------------|
| 3.1.1-22 | 合法 run | L2 | `tests/test_runtime_l2_subprocess.py::TestRunExecution::test_legal_run_succeeds` | status="succeeded", success=True, result 为合法 dict | 是 |
| 3.1.1-23 | run entrypoint 缺失 → preflight 拒绝 | L2 | `tests/test_runtime_l2_subprocess.py::TestRunEntrypoint::test_missing_run_entrypoint_rejected` | `SkillRuntimeError`（preflight gate，非 dependency_missing） | 否 |
| 3.1.1-24 | entrypoint 文件缺失 → preflight 拒绝 | L2 | `tests/test_runtime_l2_subprocess.py::TestRunEntrypoint::test_entrypoint_file_missing` | `SkillRuntimeError` | 否 |
| 3.1.1-25 | function 缺失 → handled failed | L2 | `tests/test_runtime_l2_subprocess.py::TestRunEntrypoint::test_function_missing` | status="failed", success=False（Worker 写出合法错误 response） | 是 |
| 3.1.1-26 | function 不可调用 → handled failed | L2 | `tests/test_runtime_l2_subprocess.py::TestRunEntrypoint::test_function_not_callable` | status="failed", success=False（Worker 写出合法错误 response） | 是 |
| 3.1.1-27 | 父进程不 import run 模块 | L2 | `tests/test_runtime_l2_subprocess.py::TestParentIsolation::test_parent_does_not_import_run_module` | 父进程 sys.modules 不含技能模块 | 是 |
| 3.1.1-28 | Worker PID 不同 | L2 | `tests/test_runtime_l2_subprocess.py::TestRunExecution::test_worker_pid_differs_from_parent` | Worker PID ≠ 父 PID | 是 |
| 3.1.1-29 | 权限钩子在模块执行前安装 | L2 | `tests/test_runtime_l2_subprocess.py::TestAuditHooks::test_hooks_installed_before_module_execution` | 钩子安装时间戳 < import 时间戳 | 是 |
| 3.1.1-30 | 正常 dict payload | L2 | `tests/test_runtime_l2_subprocess.py::TestRunResult::test_normal_dict_payload_accepted` | status="succeeded", success=True | 是 |
| 3.1.1-31 | 非 dict 返回 → 协议失败 | L2 | `tests/test_runtime_l2_subprocess.py::TestRunResult::test_non_dict_return_rejected` | status="protocol_error", success=False | 是 |
| 3.1.1-32 | 非 JSON-compatible 返回 → 协议失败 | L2 | `tests/test_runtime_l2_subprocess.py::TestRunResult::test_non_json_compatible_return_rejected` | status="protocol_error", success=False | 是 |
| 3.1.1-33 | result 超限 → 协议失败 | L2 | `tests/test_runtime_l2_subprocess.py::TestRunResult::test_result_size_exceeded_rejected` | status="protocol_error", success=False | 是 |
| 3.1.1-34 | 技能不能伪造 Runtime status | L2 | `tests/test_runtime_l2_subprocess.py::TestRunResult::test_skill_cannot_forge_runtime_status` | 技能返回 `{"status": "anything"}` 不影响 envelope status | 是 |
| 3.1.1-35 | 路径字符串只作为普通业务数据 | L2 | `tests/test_runtime_l2_subprocess.py::TestRunResult::test_path_string_treated_as_plain_data` | 不检查文件存在、不发布、不创建 RuntimeArtifact | 是 |
| 3.1.1-36 | result.json 原子写入 | L2 | `tests/test_runtime_l2_subprocess.py::TestRunResult::test_result_json_atomic_write` | 无 .tmp 残留，文件完整 | 是 |
| 3.1.1-37 | 缺失 result.json → crashed | L2 | `tests/test_runtime_l2_subprocess.py::TestRunCrash::test_missing_result_json` | status="crashed", success=False | 是 |
| 3.1.1-38 | task_id 不匹配 → 拒绝 | L2 | `tests/test_runtime_l2_subprocess.py::TestRunProtocol::test_task_id_mismatch_rejected` | `RuntimeProtocolError` | 是 |

### 17.3 L2 — 生命周期 (7 项)

| 编号 | 精确语义 | 层级 | 拟议完整 node ID | 预期断言 | 真实 Worker |
|------|---------|------|-----------------|---------|------------|
| 3.1.1-39 | 正常完成 | L2 | `tests/test_runtime_l2_subprocess.py::TestRunLifecycle::test_normal_completion` | status="succeeded", success=True, started_at/finished_at/duration_ms 有效 | 是 |
| 3.1.1-40 | timeout → terminate → kill | L2 | `tests/test_runtime_l2_subprocess.py::TestRunLifecycle::test_timeout` | status="timeout", success=False, 无残留 Worker | 是 |
| 3.1.1-41 | cancel | L2 | `tests/test_runtime_l2_subprocess.py::TestRunLifecycle::test_cancel` | status="cancelled", success=False | 是 |
| 3.1.1-42 | cancel 后无孤儿 Worker | L2 | `tests/test_runtime_l2_subprocess.py::TestRunLifecycle::test_no_orphan_worker_after_cancel` | 0 残留 Worker PID | 是 |
| 3.1.1-43 | crash (非零退出) | L2 | `tests/test_runtime_l2_subprocess.py::TestRunLifecycle::test_crash_nonzero_exit` | status="crashed", success=False, error.exit_code ≠ 0 | 是 |
| 3.1.1-44 | stdout 限制 | L2 | `tests/test_runtime_l2_subprocess.py::TestRunIOLimits::test_stdout_truncated` | stdout size ≤ `MAX_STDOUT_BYTES` | 是 |
| 3.1.1-45 | stderr 限制 | L2 | `tests/test_runtime_l2_subprocess.py::TestRunIOLimits::test_stderr_truncated` | stderr size ≤ `MAX_STDERR_BYTES` | 是 |

### 17.4 L3 — 安全边界 (12 项)

| 编号 | 精确语义 | 层级 | 拟议完整 node ID | 预期断言 | 真实 Worker |
|------|---------|------|-----------------|---------|------------|
| 3.1.1-46 | socket 创建拒绝 (run) | L3 | `tests/test_runtime_l3_security_boundary.py::TestRunNetworkBlocked::test_socket_create_rejected` | status="permission_denied", success=False (audit hook → Worker 写出合法错误 response) | 是 |
| 3.1.1-47 | socket connect 拒绝 (run) | L3 | `tests/test_runtime_l3_security_boundary.py::TestRunNetworkBlocked::test_socket_connect_rejected` | status="permission_denied", success=False | 是 |
| 3.1.1-48 | subprocess 拒绝 (run) | L3 | `tests/test_runtime_l3_security_boundary.py::TestRunSubprocessBlocked::test_subprocess_rejected` | status="permission_denied", success=False | 是 |
| 3.1.1-49 | `os.system` 拒绝 (run) | L3 | `tests/test_runtime_l3_security_boundary.py::TestRunSubprocessBlocked::test_os_system_rejected` | status="permission_denied", success=False | 是 |
| 3.1.1-50 | ctypes/native load 拒绝 (run) | L3 | `tests/test_runtime_l3_security_boundary.py::TestRunNativeBlocked::test_ctypes_rejected` | status="permission_denied", success=False | 是 |
| 3.1.1-51 | 写 installed 目录拒绝 (run) | L3 | `tests/test_runtime_l3_security_boundary.py::TestRunWriteBlocked::test_write_installed_rejected` | status="permission_denied", success=False | 是 |
| 3.1.1-52 | 写 workspace 外拒绝 (run) | L3 | `tests/test_runtime_l3_security_boundary.py::TestRunWriteBlocked::test_write_outside_workspace_rejected` | status="permission_denied", success=False | 是 |
| 3.1.1-53 | 读取 Registry 拒绝 (run) | L3 | `tests/test_runtime_l3_security_boundary.py::TestRunRegistryAccess::test_read_registry_rejected` | status="permission_denied", success=False | 是 |
| 3.1.1-54 | 修改 Registry 拒绝 (run) | L3 | `tests/test_runtime_l3_security_boundary.py::TestRunRegistryAccess::test_modify_registry_rejected` | status="permission_denied", success=False | 是 |
| 3.1.1-55 | symlink 逃逸拒绝 (run) | L3 | `tests/test_runtime_l3_security_boundary.py::TestRunPathEscape::test_symlink_escape_rejected` | status="permission_denied", success=False | 是 |
| 3.1.1-56 | `..` 路径逃逸拒绝 (run) | L3 | `tests/test_runtime_l3_security_boundary.py::TestRunPathEscape::test_dot_dot_escape_rejected` | status="permission_denied", success=False | 是 |
| 3.1.1-57 | project root、CWD、home 不自动放行 (run) | L3 | `tests/test_runtime_l3_security_boundary.py::TestRunPathEscape::test_project_root_cwd_home_not_auto_allowed` | status="permission_denied", success=False | 是 |

### 17.5 L3 — Secret 泄漏 (11 项)

| 编号 | 精确语义 | 层级 | 拟议完整 node ID | 预期断言 | 真实 Worker |
|------|---------|------|-----------------|---------|------------|
| 3.1.1-58 | 父进程环境 secret 不进入 Worker env (run) | L3 | `tests/test_runtime_l3_protocol_env.py::TestParentEnvSecretIsolation::test_secret_not_in_worker_env` | Worker os.environ 不含 parent env secret | 是 |
| 3.1.1-59 | 父进程环境 secret 不进入 stdout/stderr (run) | L3 | `tests/test_runtime_l3_protocol_env.py::TestParentEnvSecretIsolation::test_secret_not_in_stdout_stderr` | stdout+stderr 不含 parent env secret | 是 |
| 3.1.1-60 | 父进程环境 secret 不进入 result.json (run) | L3 | `tests/test_runtime_l3_protocol_env.py::TestParentEnvSecretIsolation::test_secret_not_in_result_json` | result.json 不含 parent env secret | 是 |
| 3.1.1-61 | 父进程环境 secret 不进入 runtime.log (run) | L3 | `tests/test_runtime_l3_protocol_env.py::TestParentEnvSecretIsolation::test_secret_not_in_runtime_log` | runtime.log 不含 parent env secret | 是 |
| 3.1.1-62 | 父进程环境 secret 不进入 error message (run) | L3 | `tests/test_runtime_l3_protocol_env.py::TestParentEnvSecretIsolation::test_secret_not_in_error_message` | error.message 不含 parent env secret | 是 |
| 3.1.1-63 | 用户敏感 params 在 stdout 中 redacted (run) | L3 | `tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_stdout` | 最终 stdout 不含完整 secret、不含截断产生的敏感片段、含 `***REDACTED***`、bytes ≤ `MAX_STDOUT_BYTES`、普通内容保持正确。覆盖：secret 位于大小限制边界附近、跨越截断边界、至少一个 UTF-8 多字节字符场景 | 是 |
| 3.1.1-64 | 用户敏感 params 在 stderr 中 redacted (run) | L3 | `tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_stderr` | 最终 stderr 不含完整 secret、不含截断产生的敏感片段、含 `***REDACTED***`、bytes ≤ `MAX_STDERR_BYTES`、普通内容保持正确。覆盖：secret 位于大小限制边界附近、跨越截断边界、至少一个 UTF-8 多字节字符场景 | 是 |
| 3.1.1-65 | 用户敏感-key params 在 result.json 中 redacted (run) | L3 | `tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_result_json` | result.json 中 password/token 等 value → `***REDACTED***` | 是 |
| 3.1.1-66 | 用户敏感-key params 在 error message 中 redacted (run) | L3 | `tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_error_message` | error.message 中敏感 value 已 redact | 是 |
| 3.1.1-67 | 用户敏感-key params 在 runtime.log 中 redacted (run) | L3 | `tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_runtime_log` | runtime.log 中敏感 value 已 redact | 是 |
| 3.1.1-68 | 普通非敏感 params 正常 roundtrip (run) | L3 | `tests/test_runtime_l3_protocol_env.py::TestNormalParamsRoundtrip::test_non_sensitive_params_roundtrip` | 普通 params 在 result.json 中正常出现 | 是 |

### 17.6 L3 — Dependencies (5 项)

| 编号 | 精确语义 | 层级 | 拟议完整 node ID | 预期断言 | 真实 Worker |
|------|---------|------|-----------------|---------|------------|
| 3.1.1-69 | 已满足依赖 → 通过 | L3 | `tests/test_runtime_l3_deps_registry.py::TestRunDependencies::test_satisfied_dependencies_pass` | preflight gate 5 通过 | 否 |
| 3.1.1-70 | 缺失依赖不启动 Worker (run) | L3 | `tests/test_runtime_l3_deps_registry.py::TestRunDependencies::test_missing_dependency_prevents_launch` | 抛 `RuntimeDependencyError`；Worker/Popen 未启动；不产生 `SkillRuntimeResponse`；Registry 内存快照不变；Registry 磁盘存在性和 bytes 不变；不调用任何安装器；不 import 目标依赖 | 否 |
| 3.1.1-71 | 版本不满足不启动 Worker (run) | L3 | `tests/test_runtime_l3_deps_registry.py::TestRunDependencies::test_version_mismatch_prevents_launch` | 抛 `RuntimeDependencyError`；Worker/Popen 未启动；不产生 `SkillRuntimeResponse`；Registry 内存快照不变；Registry 磁盘存在性和 bytes 不变；不调用任何安装器；不 import 目标依赖 | 否 |
| 3.1.1-72 | 不 import 目标依赖 (run) | L3 | `tests/test_runtime_l3_deps_registry.py::TestRunDependencies::test_does_not_import_target` | target 包不在 sys.modules | 否 |
| 3.1.1-73 | 不安装依赖 (run) | L3 | `tests/test_runtime_l3_deps_registry.py::TestRunDependencies::test_never_installs_dependencies` | install 方法未被调用 | 否 |

### 17.7 L3 — Registry 不变性 (7 项参数化 node)

全部来自单一参数化测试族：

```text
tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state
```

| 编号 | 参数 ID | 层级 | 拟议完整 node ID | 预期断言 | 真实 Worker |
|------|---------|------|-----------------|---------|------------|
| 3.1.1-74 | `succeeded` | L3 | `tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[succeeded]` | status="succeeded", success=True, 全部 Registry 字段不变 | 是 |
| 3.1.1-75 | `business-negative` | L3 | `tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[business-negative]` | payload `{"ok": false}`, envelope status="succeeded", success=True, Registry 完全不变 | 是 |
| 3.1.1-76 | `protocol-failed` | L3 | `tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[protocol-failed]` | status="protocol_error", success=False, 全部 Registry 字段不变 | 混合 |
| 3.1.1-77 | `dependency-preflight-error` | L3 | `tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[dependency-preflight-error]` | 抛 `RuntimeDependencyError`；不产生 response；Registry 完全不变 | 否 |
| 3.1.1-78 | `timeout` | L3 | `tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[timeout]` | status="timeout", success=False, 全部 Registry 字段不变 | 是 |
| 3.1.1-79 | `cancelled` | L3 | `tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[cancelled]` | status="cancelled", success=False, 全部 Registry 字段不变 | 是 |
| 3.1.1-80 | `crashed` | L3 | `tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[crashed]` | status="crashed", success=False, 全部 Registry 字段不变 | 是 |

### 17.8 回归门槛 (非新增 pytest node)

| 编号 | 精确语义 | 层级 | 拟议测试文件 | 说明 |
|------|---------|------|-------------|------|
| — | 既有 Runtime 回归全部保持通过 | 全部 | 6 个既有 Runtime 测试文件 | **回归门槛行 — 非新增 pytest node。** 六个 Runtime 回归文件: `tests/test_runtime_l1_models.py`, `tests/test_runtime_l2_subprocess.py`, `tests/test_runtime_l3_security_boundary.py`, `tests/test_runtime_l3_protocol_env.py`, `tests/test_runtime_l3_deps_registry.py`, `tests/test_runtime_ui_lifecycle.py`。148 个 Runtime 基线 node。0 failed, 0 skipped, 0 deselected。 |
| — | Installer symlink 强制回归 | 全部 | `tests/test_skill_package.py` | **回归门槛行 — 非新增 pytest node。** 两个 installer symlink 安全 node: `tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry`, `tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction`。0 failed, 0 skipped, 0 deselected。 |
| — | **合计** | | | 148 个 Runtime 基线 node + 2 个 installer symlink 安全 node = **150 个强制既有回归 node** |

---

## 18. 测试统计汇总

| 层级 | 文件 | 新增 node 数 |
|------|------|------------|
| L1 模型与协议 | `tests/test_runtime_l1_models.py` | 21 |
| L2 Worker 执行 | `tests/test_runtime_l2_subprocess.py` | 17 |
| L2 生命周期 | `tests/test_runtime_l2_subprocess.py` | 7 |
| L3 安全边界 | `tests/test_runtime_l3_security_boundary.py` | 12 |
| L3 Secret 泄漏 | `tests/test_runtime_l3_protocol_env.py` | 11 |
| L3 Dependencies | `tests/test_runtime_l3_deps_registry.py` | 5 |
| L3 Registry 不变性 | `tests/test_runtime_l3_deps_registry.py` | 7 |
| **新增小计** | | **80** |
| 强制既有回归 | 6 个既有 Runtime 测试文件 + `tests/test_skill_package.py` | 150 |
| **唯一计划执行总数** | | **80 + 150 = 230** |

### 18.1 L3 Secret 细分

| 类别 | node 数 |
|------|---------|
| A: 父进程环境 secret 隔离 | 5 |
| B: 用户敏感 params redaction | 5 (stdout, stderr, result.json, error message, runtime.log) |
| C: 普通 params roundtrip | 1 |
| **L3 Secret 合计** | **11** |

### 18.2 Batch 3.0.6 历史证据 node 说明

Batch 3.0.6 历史证据集共有 22 个 node：

- **20 个 Runtime node** — 已包含于现有 148 项 Runtime 全量回归中，不得重复计算；
- **2 个 installer symlink node** — 位于 `tests/test_skill_package.py`，独立于 148 Runtime 基线，现强制纳入。

150 个唯一既有回归 node = 148 Runtime + 2 installer symlink。

---

## 19. Batch 3.1.1 允许和禁止修改的文件

### 允许修改

```text
dp_engine/skills/runtime_models.py          — ALLOWED_OPERATIONS 添加 "run"
                                              — SkillRuntimeRequest 新增 params: dict | None
                                              — SkillRuntimeResponse 新增 result: dict | None
                                              — from_dict/to_dict 兼容处理
                                              — 新增 MAX_REQUEST_BYTES 常量
                                              — 新增 MAX_JSON_NESTING_DEPTH 常量
                                              — 新增 MAX_JSON_CONTAINER_ITEMS 常量
                                              — 新增 MAX_JSON_STRING_BYTES 常量
                                              — 新增 VALID_RUN_STATUSES 常量
dp_engine/skills/runtime_protocol.py         — operation 校验更新
                                              — 新增 params 校验规则（13 条）
                                              — 新增 validate_run_result()
                                              — 新增 response envelope 组装
                                              — 新增 sensitve-key redaction
                                              — write_request_atomic 新增 MAX_REQUEST_BYTES 检查
dp_engine/skills/runtime_service.py          — 新增 run_skill() 方法
                                              — 新增 run preflight 检查
                                              — 复用 _run_subprocess() / cancel()
                                              — run 路径零 Registry 写入
                                              — 新增 per-task redaction values
dp_engine/skills/runtime_worker.py           — main() 新增 "run" 分支
                                              — 新增 _execute_run() 函数
                                              — 复用权限钩子安装
                                              — 技能返回值 envelope 包装
                                              — result.json 写入前执行 redaction
dp_engine/skills/runtime_errors.py           — 可能需要新异常类型
                                              — (仅当 run 错误无法用现有异常表达)
tests/fixtures/runtime_fixtures.py           — 新增 run 相关 fixture
                                              — 新增 create_skill_run_socket_create fixture
                                              — 新增 create_skill_run_socket_connect fixture
                                              — 新增父进程环境 secret fixture
                                              — 新增用户敏感 params fixture
                                              — 新增普通 params roundtrip fixture
tests/test_runtime_l1_models.py              — 新增 L1 测试 (21 node: 含 operation-specific status 交叉校验)
tests/test_runtime_l2_subprocess.py          — 新增 L2 测试 (24 node: 17 执行 + 7 生命周期)
tests/test_runtime_l3_security_boundary.py   — 新增 L3 安全测试 (12 node)
tests/test_runtime_l3_protocol_env.py        — 新增 L3 secret 测试 (11 node: 5 父进程环境 + 5 用户敏感 params + 1 普通 roundtrip)
tests/test_runtime_l3_deps_registry.py       — 新增 L3 依赖/Registry 测试 (12 node: 5 依赖 + 7 Registry)
                                              — 新增测试合计: 80 node
```

### 禁止修改

```text
ui/skill_tab.py                              — UI 变更属于 Batch 3.1.2
ui/skill_runtime_controller.py               — UI 变更属于 Batch 3.1.2
main.py                                      — 主窗口变更属于 Batch 3.1.2
core/report_engine.py                        — 报告系统属于 Batch 3.3
dp_engine/report_builder/word_builder.py     — Builder 不在任何已批准范围
dp_engine/report_builder/ppt_builder.py      — Builder 不在任何已批准范围
ui/report_workbench.py                       — 报告 UI 属于 Batch 3.3
core/chart_bundle.py                         — 图表系统不在任何已批准范围
core/chart_registry.py                       — 图表系统不在任何已批准范围
core/chart_store.py                          — 图表系统不在任何已批准范围
dp_engine/skills/installer.py                — 安装器不在 3.1 范围
dp_engine/skills/registry.py                 — Registry 零写入决策，不修改
```

### 只读基线 / 禁止修改

```text
dp_engine/skills/runtime_permissions.py      — 当前标注"不变"，移至禁止修改。
                                               run 复用现有权限钩子，无需修改此文件。
                                               仅当后续测试证明 run 无法复用现有权限钩子时，
                                               才允许提交阻断说明，不得自行修改。
tests/test_runtime_ui_lifecycle.py           — 既有 Runtime 回归文件，不得新增测试或修改已有测试。
```

### 强制既有回归文件清单（148 个 Runtime 基线 node）

```text
tests/test_runtime_l1_models.py
tests/test_runtime_l2_subprocess.py
tests/test_runtime_l3_security_boundary.py
tests/test_runtime_l3_protocol_env.py
tests/test_runtime_l3_deps_registry.py
tests/test_runtime_ui_lifecycle.py
```

### Installer symlink 安全 node（2 个，位于 test_skill_package.py）

```text
tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry
tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction
```

---

## 20. 新增 fixture 需求

| fixture | 用途 | 对应测试 |
|---------|------|---------|
| `create_skill_with_run_entrypoint` | 合法 run entrypoint (`ok: true` 返回) | 3.1.1-22, 3.1.1-30 |
| `create_skill_without_run_entrypoint` | manifest 无 run entrypoint | 3.1.1-23 |
| `create_skill_with_run_entrypoint_missing_file` | entrypoint 路径指向不存在的文件 | 3.1.1-24 |
| `create_skill_with_missing_run_function` | entrypoint 文件存在但函数不存在 | 3.1.1-25 |
| `create_skill_with_non_callable_run` | entrypoint 指向不可调用对象 | 3.1.1-26 |
| `create_skill_run_returns_non_dict` | run() 返回 int | 3.1.1-31 |
| `create_skill_run_returns_non_json` | run() 返回含 Path 值的 dict | 3.1.1-32 |
| `create_skill_run_returns_path_string` | run() 返回 `{"path": "output/report.pdf"}` | 3.1.1-35 |
| `create_skill_run_forges_status` | run() 返回 `{"status": "anything"}` | 3.1.1-34 |
| `create_skill_run_writes_large_stdout` | run() 输出 >1MB stdout | 3.1.1-44 |
| `create_skill_run_writes_large_stderr` | run() 输出 >1MB stderr | 3.1.1-45 |
| `create_skill_run_hangs` | run() sleep 30s (超时测试) | 3.1.1-40 |
| `create_skill_run_exits_nonzero` | run() `sys.exit(1)` | 3.1.1-43 |
| `create_skill_run_no_result_json` | Worker crash 在写 result 前 | 3.1.1-37 |
| `create_skill_run_large_result` | run() 返回 >`MAX_RESULT_JSON_BYTES` result | 3.1.1-33 |
| `create_skill_run_socket_create` | run() 尝试 `socket.socket()` | 3.1.1-46 |
| `create_skill_run_socket_connect` | run() 尝试 `socket.create_connection()` | 3.1.1-47 |
| `create_skill_run_popen` | run() 尝试 `subprocess.Popen` | 3.1.1-48 |
| `create_skill_run_os_system` | run() 尝试 `os.system` | 3.1.1-49 |
| `create_skill_run_ctypes` | run() 尝试 `ctypes.CDLL` | 3.1.1-50 |
| `create_skill_run_write_installed` | run() 尝试写 installed 目录 | 3.1.1-51 |
| `create_skill_run_write_outside` | run() 尝试写 workspace 外 | 3.1.1-52 |
| `create_skill_run_read_registry` | run() 尝试读 Registry | 3.1.1-53 |
| `create_skill_run_modify_registry` | run() 尝试写 Registry | 3.1.1-54 |
| `create_skill_run_symlink_escape` | run() 尝试 symlink 逃逸 | 3.1.1-55 |
| `create_skill_run_dot_dot_escape` | run() 尝试 `..` 路径逃逸 | 3.1.1-56 |
| `create_skill_run_project_root_escape` | run() 尝试访问 project root/CWD/home | 3.1.1-57 |
| `create_skill_run_business_failure` | run() 返回 `ok: false` | 3.1.1-75 |
| `create_skill_run_task_id_mismatch` | Worker 返回不同 task_id | 3.1.1-38 |
| `create_skill_with_parent_env_secret` | 父进程环境中含 secret | 3.1.1-58 至 3.1.1-62 |
| `create_skill_run_with_sensitive_params` | 用户 params 含 password/token 等 | 3.1.1-63 至 3.1.1-67 |
| `create_skill_run_with_normal_params` | 用户 params 含普通业务数据 | 3.1.1-68 |

**合计: 32 个 fixture**

---

## 21. 安全与兼容风险

### 21.1 不扩大的边界 (确认)

- **文件读取根**: 不变 — 仅技能自身目录 + workspace
- **文件写入根**: 不变 — 仅 workspace/output + temp
- **网络**: 不变 — `FORBIDDEN_HEALTHCHECK_CAPABILITIES` 持续拒绝
- **子进程**: 不变 — `FORBIDDEN_HEALTHCHECK_CAPABILITIES` 持续拒绝
- **父进程加载技能**: 不变 — 仅 Worker 子进程 import
- **capabilities 授权**: 不变 — manifest capabilities 只用于兼容性检查
- **Registry 写入**: **零写入** — 所有 run 终态下 Registry 内存和磁盘完全不变
- **runtime_permissions.py**: 不变 — run 复用现有权限钩子

### 21.2 兼容性风险

| 风险 | 缓解措施 |
|------|---------|
| 已安装技能仅有 healthcheck 入口 | run preflight 检查 `entrypoints.run`，缺失则拒绝—不崩溃 |
| 既有 Runtime 回归破坏 | 150 强制既有回归 node 零失败（148 Runtime + 2 installer symlink） |
| 旧 Registry 文件 | 零 Registry 写入意味着零兼容性风险 |
| 技能返回 `ok`/`status` 等字段 | 作为普通业务数据保留，不影响 Runtime envelope |
| 旧 healthcheck result.json 与新响应模型 | `SkillRuntimeResponse.from_dict()` 对缺失 `result` key 使用 `.get()` → None，天然兼容 |
| 旧 request.json 与新请求模型 | `SkillRuntimeRequest` 新增 `params=None`，`to_dict()` 条件序列化 |

---

## 22. 回滚策略

### 22.1 完整回滚

1. 从 `ALLOWED_OPERATIONS` 移除 `"run"`（恢复为仅 `healthcheck`）
2. 移除 `SkillRuntimeService.run_skill()` 方法
3. Worker 的 run 分支保留但不可达（或移除）
4. 回退所有 80 个新增测试 node
5. 确认 150 强制既有回归 node 零失败

### 22.2 分阶段回滚

- **Gate 级**: 仅 `ALLOWED_OPERATIONS` 回滚 — 其他 run 代码保留但不可达
- **Service 级**: `run_skill()` 抛出 `NotImplementedError`
- **模型级**: `params`/`result` 字段保留但不被任何路径使用

### 22.3 不可回滚项

- 无。所有 3.1.1 新增能力通过 `ALLOWED_OPERATIONS` 门禁控制，移除即可回滚。

---

## 23. 全部节点 ID 汇总

### 新增节点 (80)

```
tests/test_runtime_l1_models.py::TestAllowedOperations::test_healthcheck_and_run_are_valid_operations
tests/test_runtime_l1_models.py::TestAllowedOperations::test_unknown_operation_rejected
tests/test_runtime_l1_models.py::TestRequestValidation::test_unknown_top_level_field_rejected
tests/test_runtime_l1_models.py::TestRequestValidation::test_params_not_dict_rejected
tests/test_runtime_l1_models.py::TestRequestValidation::test_params_key_not_string_rejected
tests/test_runtime_l1_models.py::TestRequestValidation::test_params_non_json_compatible_rejected
tests/test_runtime_l1_models.py::TestRequestValidation::test_nan_infinity_rejected
tests/test_runtime_l1_models.py::TestInputLimits::test_nesting_depth_exceeded_rejected
tests/test_runtime_l1_models.py::TestInputLimits::test_element_count_exceeded_rejected
tests/test_runtime_l1_models.py::TestInputLimits::test_string_length_exceeded_rejected
tests/test_runtime_l1_models.py::TestInputLimits::test_serialized_size_exceeded_rejected
tests/test_runtime_l1_models.py::TestResponseBackwardCompat::test_healthcheck_response_roundtrip_with_result_field
tests/test_runtime_l1_models.py::TestResponseBackwardCompat::test_run_response_retains_all_compat_fields
tests/test_runtime_l1_models.py::TestResponseBackwardCompat::test_run_response_artifacts_always_empty
tests/test_runtime_l1_models.py::TestResponseBackwardCompat::test_run_response_health_always_none
tests/test_runtime_l1_models.py::TestResponseBackwardCompat::test_success_derived_from_status_by_runtime
tests/test_runtime_l1_models.py::TestStatusCollections::test_operation_specific_status_validation
tests/test_runtime_l1_models.py::TestRequestBackwardCompat::test_healthcheck_request_roundtrip_with_params_field
tests/test_runtime_l1_models.py::TestRequestBackwardCompat::test_run_request_params_roundtrip
tests/test_runtime_l1_models.py::TestRequestBackwardCompat::test_user_params_do_not_override_trusted_fields
tests/test_runtime_l1_models.py::TestRequestBackwardCompat::test_service_only_accepts_params_as_user_input
tests/test_runtime_l2_subprocess.py::TestRunExecution::test_legal_run_succeeds
tests/test_runtime_l2_subprocess.py::TestRunEntrypoint::test_missing_run_entrypoint_rejected
tests/test_runtime_l2_subprocess.py::TestRunEntrypoint::test_entrypoint_file_missing
tests/test_runtime_l2_subprocess.py::TestRunEntrypoint::test_function_missing
tests/test_runtime_l2_subprocess.py::TestRunEntrypoint::test_function_not_callable
tests/test_runtime_l2_subprocess.py::TestParentIsolation::test_parent_does_not_import_run_module
tests/test_runtime_l2_subprocess.py::TestRunExecution::test_worker_pid_differs_from_parent
tests/test_runtime_l2_subprocess.py::TestAuditHooks::test_hooks_installed_before_module_execution
tests/test_runtime_l2_subprocess.py::TestRunResult::test_normal_dict_payload_accepted
tests/test_runtime_l2_subprocess.py::TestRunResult::test_non_dict_return_rejected
tests/test_runtime_l2_subprocess.py::TestRunResult::test_non_json_compatible_return_rejected
tests/test_runtime_l2_subprocess.py::TestRunResult::test_result_size_exceeded_rejected
tests/test_runtime_l2_subprocess.py::TestRunResult::test_skill_cannot_forge_runtime_status
tests/test_runtime_l2_subprocess.py::TestRunResult::test_path_string_treated_as_plain_data
tests/test_runtime_l2_subprocess.py::TestRunResult::test_result_json_atomic_write
tests/test_runtime_l2_subprocess.py::TestRunCrash::test_missing_result_json
tests/test_runtime_l2_subprocess.py::TestRunProtocol::test_task_id_mismatch_rejected
tests/test_runtime_l2_subprocess.py::TestRunLifecycle::test_normal_completion
tests/test_runtime_l2_subprocess.py::TestRunLifecycle::test_timeout
tests/test_runtime_l2_subprocess.py::TestRunLifecycle::test_cancel
tests/test_runtime_l2_subprocess.py::TestRunLifecycle::test_no_orphan_worker_after_cancel
tests/test_runtime_l2_subprocess.py::TestRunLifecycle::test_crash_nonzero_exit
tests/test_runtime_l2_subprocess.py::TestRunIOLimits::test_stdout_truncated
tests/test_runtime_l2_subprocess.py::TestRunIOLimits::test_stderr_truncated
tests/test_runtime_l3_security_boundary.py::TestRunNetworkBlocked::test_socket_create_rejected
tests/test_runtime_l3_security_boundary.py::TestRunNetworkBlocked::test_socket_connect_rejected
tests/test_runtime_l3_security_boundary.py::TestRunSubprocessBlocked::test_subprocess_rejected
tests/test_runtime_l3_security_boundary.py::TestRunSubprocessBlocked::test_os_system_rejected
tests/test_runtime_l3_security_boundary.py::TestRunNativeBlocked::test_ctypes_rejected
tests/test_runtime_l3_security_boundary.py::TestRunWriteBlocked::test_write_installed_rejected
tests/test_runtime_l3_security_boundary.py::TestRunWriteBlocked::test_write_outside_workspace_rejected
tests/test_runtime_l3_security_boundary.py::TestRunRegistryAccess::test_read_registry_rejected
tests/test_runtime_l3_security_boundary.py::TestRunRegistryAccess::test_modify_registry_rejected
tests/test_runtime_l3_security_boundary.py::TestRunPathEscape::test_symlink_escape_rejected
tests/test_runtime_l3_security_boundary.py::TestRunPathEscape::test_dot_dot_escape_rejected
tests/test_runtime_l3_security_boundary.py::TestRunPathEscape::test_project_root_cwd_home_not_auto_allowed
tests/test_runtime_l3_protocol_env.py::TestParentEnvSecretIsolation::test_secret_not_in_worker_env
tests/test_runtime_l3_protocol_env.py::TestParentEnvSecretIsolation::test_secret_not_in_stdout_stderr
tests/test_runtime_l3_protocol_env.py::TestParentEnvSecretIsolation::test_secret_not_in_result_json
tests/test_runtime_l3_protocol_env.py::TestParentEnvSecretIsolation::test_secret_not_in_runtime_log
tests/test_runtime_l3_protocol_env.py::TestParentEnvSecretIsolation::test_secret_not_in_error_message
tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_stdout
tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_stderr
tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_result_json
tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_error_message
tests/test_runtime_l3_protocol_env.py::TestSensitiveParamsRedaction::test_sensitive_params_redacted_in_runtime_log
tests/test_runtime_l3_protocol_env.py::TestNormalParamsRoundtrip::test_non_sensitive_params_roundtrip
tests/test_runtime_l3_deps_registry.py::TestRunDependencies::test_satisfied_dependencies_pass
tests/test_runtime_l3_deps_registry.py::TestRunDependencies::test_missing_dependency_prevents_launch
tests/test_runtime_l3_deps_registry.py::TestRunDependencies::test_version_mismatch_prevents_launch
tests/test_runtime_l3_deps_registry.py::TestRunDependencies::test_does_not_import_target
tests/test_runtime_l3_deps_registry.py::TestRunDependencies::test_never_installs_dependencies
tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[succeeded]
tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[business-negative]
tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[protocol-failed]
tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[dependency-preflight-error]
tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[timeout]
tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[cancelled]
tests/test_runtime_l3_deps_registry.py::TestRegistryImmutability::test_registry_unchanged_for_run_end_state[crashed]
```

### 强制既有回归 node (150)

**148 个 Runtime 基线 node** — 覆盖以下六个文件中的既有测试：

```text
tests/test_runtime_l1_models.py
tests/test_runtime_l2_subprocess.py
tests/test_runtime_l3_security_boundary.py
tests/test_runtime_l3_protocol_env.py
tests/test_runtime_l3_deps_registry.py
tests/test_runtime_ui_lifecycle.py
```

**2 个 installer symlink 安全 node**（独立于 148 Runtime 基线）:

```text
tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry
tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction
```

---

## 24. 最终声明

```text
Batch 3.1 Run Status 与 Redaction 最终校正已提交外部审核。
未开始 Batch 3.1.1 实施。
未修改任何生产代码或测试代码。
已停止，等待 Batch 3.1 规划最终外部审核。
```
