# Batch 3.2 — Artifact 安全发布与消费规划及合同冻结（P0校正版）

**日期**: 2026-07-22
**分支**: llama-cpp
**状态**: 规划 P0 校正完成 — 提交外部审核
**批次类型**: 纯规划批次 — 未实施任何代码
**校正轮次**: Batch 3.2.0 一次性 P0 规划校正

---

## 1. 最小Markdown读取声明

```text
已读取并遵守 CLAUDE.md。
已读取当前 batch-3.2-planning-package.md。
采用最小Markdown上下文原则，未读取其他历史Markdown。
当前只执行 Batch 3.2.0 一次性 P0 规划校正，不实施代码。
```

实际读取的两个文件：

| 文件 | 行数 | 用途 |
|------|------|------|
| `CLAUDE.md` | 79 | 项目执行纪律 |
| `docs/agents/batch-3.2-planning-package.md` | 1271（校正前） | 待校正规划包 |

---

## 2. 冻结范围

### Batch 3.2 纳入

```text
artifact schema
output 源根
path resolve
symlink 及其他路径逃逸
数量、类型和大小限制
原子发布
取消和失败清理
生命周期
所有权
安全消费
ArtifactStore 主机侧消费 API
```

### Batch 3.2 明确排除（属于 Batch 3.3）

```text
report_backend generate 调用
report_workbench 集成
Word/PPT Builder
Artifact 到 Word/PPT 的适配
自动生成报告
自动解析业务 result 中的任意路径字符串
动态扩大 capabilities
网络、subprocess、os.system 或 ctypes 放行
依赖安装
云上传
外部插件下载
任意文件预览器
```

---

## 3. 当前代码现状盘点

### 3.1 实际只读检查的代码文件

| 文件 | 检查目的 |
|------|---------|
| `dp_engine/skills/runtime_models.py` | RuntimeArtifact 模型定义 |
| `dp_engine/skills/runtime_protocol.py` | response 验证逻辑（artifact 路径检查） |
| `dp_engine/skills/runtime_service.py` | workspace 创建、清理、response 读取 |
| `dp_engine/skills/runtime_worker.py` | Worker 侧 artifact 收集 |
| `dp_engine/skills/runtime_paths.py` | workspace 管理、artifact 迭代、清理 |
| `dp_engine/skills/runtime_permissions.py` | 文件写入允许根 |
| `dp_engine/skills/runtime_errors.py` | 错误类型体系 |
| `dp_engine/skills/models.py` | SkillManifest.artifact_types 字段 |
| `ui/skill_runtime_controller.py` | UI Controller API |
| `ui/skill_tab.py` | UI 结果展示 |
| `tests/test_runtime_l1_models.py` | L1 artifact 测试 |
| `tests/test_runtime_l3_protocol_env.py` | L3 artifact 路径安全测试 |
| `tests/test_runtime_l3_security_boundary.py` | L3 artifact 收集测试 |
| `tests/fixtures/runtime_fixtures.py` | artifact fixture |

使用定点搜索：

```bash
rg -n \
  "class RuntimeArtifact|def create_workspace|def cleanup_workspace|workspace_path|output_dir|artifacts|RuntimeArtifact" \
  dp_engine ui tests
```

### 3.2 RuntimeArtifact 当前字段

```python
# runtime_models.py:188-194
@dataclass(frozen=True)
class RuntimeArtifact:
    """A file produced by the skill operation in the output directory."""
    relative_path: str       # relative to workspace/output/
    size_bytes: int
    sha256: str | None = None
```

**现状**: 只有 3 个字段。无 `artifact_id`、无 `display_name`、无 `media_type`、无 `kind`、无 `created_at`、无 `metadata`。仅 `sha256` 可选——hash 未被强制执行。

### 3.3 to_dict / from_dict 行为

- `to_dict()`: 序列化为 `{"relative_path": ..., "size_bytes": ..., "sha256": ...}`
- `from_dict()`: 安全默认值反序列化（空 path、0 size、None sha256）
- 无反序列化验证（不检查 path 是否空、size 是否负）
- 未知字段被静默丢弃

### 3.4 当前 response 的 artifacts 验证逻辑

`runtime_protocol.py:493-514` 中的 `validate_response()` 执行：

1. `relative_path` 非空检查
2. 拒绝绝对路径
3. 拒绝含 `..` 的路径
4. resolve 后确认在 `workspace/output` 内

**缺失**: 不检查文件存在性、不检查文件类型（symlink/目录/特殊文件）、不检查文件大小、不检查 hash、不检查数量。

### 3.5 workspace 创建和清理位置

- **创建**: `create_workspace(task_id)` → `<skills_root>/runtime/<task_id>/` + `input/` `output/` `temp/`
- **清理（成功）**: `cleanup_workspace(ws, succeeded=True)` → `shutil.rmtree` 完整 workspace
- **清理（失败）**: `cleanup_workspace(ws, succeeded=False)` → 保留 workspace 用于诊断，仅移除 cancel marker

**关键发现**: 成功时 workspace 被完整删除。这意味着当前架构下，任何被 Worker 发现并写入 response 的 artifact 文件在 `cleanup_workspace` 之后**不再存在**。Artifact 的 `relative_path` 指向一个已删除的文件。

### 3.6 workspace/output 当前允许写入方式

`runtime_permissions.py:282-295` 中 `build_allowed_write_roots()`:
```python
return ((wp / "output"), (wp / "temp"))
```

技能可以直接写入 `workspace/output/`。audit hook 阻止写入 workspace 外部，但**不阻止**在 output 内创建 symlink（因为 symlink 创建走的是文件系统 API，audit hook 对 symlink 创建的拦截仅依赖 `open()` 路径检查）。

### 3.7 Service 何时读取 response、清理 workspace

`runtime_service.py` 中 `run_skill()` 的调用链：

```
Worker 完成 → read_response_atomic() → [验证 response] → cleanup_workspace(succeeded=True)
```

healthcheck 同理。**workspace 在 response 返回前即被删除**。

### 3.8 Worker 何时退出、如何处理 artifacts

**healthcheck** (`_execute_healthcheck`):
- 自动扫描 `workspace/output/` 下所有普通文件
- 对每个文件收集 `relative_path`、`size_bytes`、`sha256=None`
- 写入 response 的 `artifacts` 字段
- **这是既有的自动收集行为，Batch 3.2 保持不变**

**run** (`_execute_run`):
- **硬编码 `"artifacts": []`** — Run 操作从不产生 artifacts
- 技能在 output 中写入的任何文件都不被发现或发布
- **Batch 3.2.1 将改为通过 `declare_artifact()` 显式声明**

### 3.9 当前 UI 如何展示 run 结果

`ui/skill_tab.py:_on_run_result()`:
- 显示 status、success、duration_ms
- 显示 result dict（JSON 格式化）
- 显示 business-negative（ok=false）
- **不显示 artifacts**
- **不引用 `response.artifacts`**
- 没有 artifact 列表 widget

### 3.10 现有 artifact 相关测试验证了什么

| 测试 | 位置 | 验证内容 |
|------|------|---------|
| `test_artifact_escape` | L1 models | `..` 路径拒绝 |
| `test_roundtrip_via_dict` | L1 models | to_dict/from_dict 往返 |
| `test_artifact_absolute_path_rejected` | L3 protocol_env | 绝对路径拒绝 |
| `test_artifact_dot_dot_rejected` | L3 protocol_env | `..` 拒绝 |
| `test_artifact_symlink_escape_resolved` | L3 protocol_env | resolve 逃逸检测 |
| `test_worker_collects_output_artifacts` | L3 security_boundary | Worker 自动发现（healthcheck） |
| `test_skill_can_write_and_read_output_artifact` | L3 security_boundary | output 写入+读取 |
| `test_run_response_artifacts_always_empty` | L1 models | run 时 artifacts=()（Batch 3.2.1 将改变） |

**结论**: 现有测试覆盖了路径安全的基础面（绝对路径、`..`、resolve 逃逸），但都在 healthcheck 上下文。无发布、无持久化、无 UI 消费测试。

### 3.11 哪些已有逻辑只是结构性预留

| 组件 | 状态 |
|------|------|
| `RuntimeArtifact` 模型 | 结构性预留 — 仅 3 字段 |
| `SkillManifest.artifact_types` | 元数据字段 — Runtime 不使用 |
| `iter_workspace_artifacts()` | 辅助函数 — 仅 `os.scandir` 非递归 |
| `cleanup_expired_workspaces()` | 存在但从不自动调用 |
| Worker 自动发现 | healthcheck 有效，run 硬编码空 |
| `cleanup_workspace(succeeded=True)` | 删除 workspace — 零 artifact 持久化 |
| UI artifact 展示 | 不存在 |

---

## 4. 显式声明通道决策

### 4.1 评估的三种方案

| 方案 | 描述 | 安全性 | 技能复杂度 | 兼容性 |
|------|------|--------|-----------|--------|
| A. 从业务 result 保留 key 声明 | 技能在 result dict 中放置特殊 key | ❌ 需要扫描业务数据，破坏 Batch 3.1 冻结语义 | 低 | 差 |
| B. 独立 artifact manifest sidecar | 技能在 output 中写入 `artifact-manifest.json` | ⚠️ 需要额外文件系统操作，可能被伪造 | 中 | 中 |
| C. Worker context 中的专用 artifact API | 技能调用 `context.declare_artifact(path, name, kind)` | ✅ 主机权威，技能可控范围明确 | 低 | 好 |

### 4.2 最终选择：方案 C — Worker Context 专用 Artifact API

**理由**:

1. 主机（Worker/Runtime）保持对 `RuntimeArtifact` 字段的权威控制
2. 技能通过显式 API 调用声明，不可能"意外"发布
3. 不与业务 result 混合——result 中的 path 字符串仍是普通字符串
4. Worker 可以立即验证声明是否合法（文件存在、类型正确、路径在 output 内）

### 4.3 冻结 ArtifactRunContext — 向后兼容的 dict 子类

#### P0 合同

最终采用 `ArtifactRunContext`，它是普通 `dict` 的向后兼容子类：

```python
class ArtifactRunContext(dict[str, object]):
    """向后兼容的 dict 子类。技能通过 context["params"] 访问参数，
    通过 context.declare_artifact() 声明输出文件。
    """
    def declare_artifact(
        self,
        path: str,
        *,
        display_name: str | None = None,
        media_type: str | None = None,
        kind: str = "other",
        metadata: dict[str, object] | None = None,
    ) -> None:
        ...
```

#### 向后兼容保证

```text
isinstance(context, dict) == True
既有 context 键和值完全保留
既有 context["params"] 访问不变
json.dumps(context) 仍只序列化 dict 中的 JSON-compatible 内容
declare_artifact 不是 dict 中的 value
不得把 callable 或自定义对象插入 context 数据键
```

#### 兼容测试合同

```text
旧技能收到的 context 仍为 dict
旧技能不调用 declare_artifact 时行为完全不变
context["params"] 往返不变
JSON 序列化不包含方法或内部声明状态
```

#### declare_artifact() 参数合同

`declare_artifact()` 接受以下参数（全部由技能提供，其余字段由主机生成）：

| 参数 | 类型 | 必填 | 最大长度 | 说明 |
|------|------|------|---------|------|
| `path` | `str` | 是 | 255 chars | 相对于 workspace/output 的文件路径 |
| `display_name` | `str` | 否 | 128 chars | 人类可读名称，默认使用 `Path(relative_path).stem`；strip 后为空则使用原文件名；超长按 UTF-8 安全字符边界截断 |
| `media_type` | `str` | 否 | 64 chars | MIME 类型提示（hint），主机可覆盖；默认由主机根据扩展名和内容推断 |
| `kind` | `str` | 否 | 32 chars | 类别标签：`"chart"`, `"table"`, `"document"`, `"data"`, `"other"` |
| `metadata` | `dict` | 否 | 4096 bytes (JSON) | 业务元数据，必须是 JSON-compatible |

`declare_artifact()` 返回值：无（void）。重复调用同一 `path` 视为幂等，不产生重复声明。

#### 非法声明不可吞掉

任何一次非法 `declare_artifact()` 调用都必须将当前 context 标记为 **fatal declaration error**。

即使技能代码捕获了抛出的异常并继续返回合法业务 dict：

```text
Worker 仍必须在 run() 返回后检测 fatal flag
最终 status=protocol_error
result=None
artifacts=()
```

技能不得通过 `try/except` 吞掉非法声明并获得 `succeeded`。

合法重复声明同一路径仍保持幂等，不设置 fatal flag。

#### 声明何时被读取

Worker 在执行 `run()` 后收集所有声明，每次 `declare_artifact()` 调用立即：

1. 验证 path 是合法相对路径（无 `..`、无绝对路径）
2. lstat 确认文件存在且为 regular file（`S_ISREG`）
3. 拒绝 symlink（`S_ISLNK`）
4. 检查 link count = 1（拒绝 hardlink）
5. 检查文件大小在限制内
6. 检查扩展名在 allowlist 中
7. 计算 SHA256 hash
8. 记录声明（内存中排队）

`run()` 返回后，Worker 将已排队的声明构建为 `artifact_declarations` 列表。

#### 未声明文件如何处理

**不发布**。即使用户在 output 中写入了文件但没有调用 `declare_artifact()`，该文件不会被发布为 artifact。workspace 清理后文件消失。

#### 重复声明如何处理

同一 `path` 重复声明 → 幂等，仅保留第一次声明。后续调用不产生错误也不产生新条目。

#### 声明顺序是否保留

是。声明顺序 = artifact_declarations 列表顺序 = 最终 artifacts 列表顺序 = UI 展示顺序。

#### 声明中的未知字段如何处理

`declare_artifact()` 不接受未知关键字参数（`TypeError`）。metadata 中的未知 key 是合法业务数据，原样保留。

#### 技能可控字段 vs 主机权威字段边界

| 字段 | 来源 | 说明 |
|------|------|------|
| `path`（relative_path） | **技能声明** | 主机验证后使用 |
| `display_name` | **技能声明** | 主机可按规则截断超长值 |
| `media_type` | **技能声明（hint）** | 主机根据内容嗅探最终确定；hint 冲突则 protocol_error |
| `kind` | **技能声明** | 仅 allowlist 中的值 |
| `metadata` | **技能声明** | 主机验证 JSON-compatible + 大小限制 |
| `artifact_id` | **主机生成** | 技能不可控 |
| `size_bytes` | **主机权威** | 由 `lstat` 获取 |
| `sha256` | **主机权威** | 由主机计算 |
| `storage_relpath` | **主机权威** | 主机生成，相对于 artifact_root |
| `created_at` | **主机权威** | 主机时间戳 |
| `task_id` | **主机权威** | 关联的 task |
| `skill_id` | **主机权威** | 关联的技能 |
| `version` | **主机权威** | 技能版本 |

**技能不能伪造**: `size_bytes`、`sha256`、`storage_relpath`、`artifact_id`、`task_id`、`skill_id`、`created_at`。

---

## 5. 两阶段 Artifact 协议

### 5.1 阶段一：Worker 内部声明 (ArtifactDeclaration)

Worker 内部使用独立类型记录技能声明，**不是**最终 `RuntimeArtifact`：

```python
@dataclass
class ArtifactDeclaration:
    """Worker 内部声明记录——非最终 RuntimeArtifact。"""
    declared_path: str           # 技能声明的相对路径
    display_name: str            # 人类可读名称
    media_type_hint: str | None  # 技能提供的 MIME 提示（可为 None）
    kind: str                    # 类别标签
    metadata: dict[str, object]  # 业务元数据
    observed_size_bytes: int     # Worker lstat 观察到的大小
    observed_sha256: str         # Worker 计算的 SHA256
    observed_device: int         # st_dev
    observed_inode: int          # st_ino
    observed_mtime_ns: int       # st_mtime_ns
```

`ArtifactDeclaration` **不得包含**：

```text
artifact_id        — 主机 Service 生成
storage_relpath    — 主机 Service 生成
published 绝对路径  — 不在公开类型中暴露
skill_id           — 主机 Service 填充
version            — 主机 Service 填充
task_id            — 主机 Service 填充
created_at         — 主机 Service 填充
```

### 5.2 Worker wire 字段

Worker 的 `result.json` 使用独立、仅 Runtime 可生成的字段：

```json
{
  "artifact_declarations": []
}
```

冻结规则：

```text
artifact_declarations 只允许 operation="run"
默认缺失或空列表表示无声明
技能业务 result 不能控制该字段
Worker 负责写入
Service 负责读取和验证
最终返回给调用者的 SkillRuntimeResponse 不得暴露 artifact_declarations
```

`runtime_protocol.py` 必须加入 Batch 3.2.1 允许修改文件，因为需要验证该内部 wire 字段。

### 5.3 阶段二：最终 RuntimeArtifact（Service 发布后）

只有 Service 发布成功后才能创建 `RuntimeArtifact`。最终 `SkillRuntimeResponse.artifacts` 只包含主机发布完成的 Artifact。

### 5.4 最终 RuntimeArtifact Schema（P0 校正）

#### 字段保留决策

| 既有字段 | 决策 | 理由 |
|---------|------|------|
| `relative_path` | **保留名称** | healthcheck 及旧 response 仍使用此字段；不可重命名 |
| `size_bytes` | **保留** | 主机权威验证 |
| `sha256` | **保留，模型层保持 `str \| None`** | 兼容旧 healthcheck（sha256=None）；对已发布 Run Artifact 由 Service 强制为 64 位 hex |

#### 新增字段

| 字段 | 类型 | 必填 | 来源 | 说明 |
|------|------|------|------|------|
| `artifact_schema_version` | `int` | 是 | 主机 | 当前固定为 1；manifest 读取必须严格验证版本 |
| `artifact_id` | `str` | 是 | 主机 | 完整 `uuid4().hex`，32 个 hex 字符；不得截断为 16 字符 |
| `display_name` | `str` | 是 | 技能声明 | 人类可读名称 |
| `storage_relpath` | `str` | 是 | 主机 | 主机生成，始终相对于 Artifact 根；禁止绝对路径；UI 不得自行拼接或解析 |
| `media_type` | `str` | 是 | 主机推断 | 最终 MIME 类型 |
| `kind` | `str` | 是 | 技能声明 | 类别标签 |
| `metadata` | `dict[str, object]` | 是（可为空 dict） | 技能声明 | 业务元数据 |
| `created_at` | `str` | 是 | 主机 | ISO 8601 UTC |
| `skill_id` | `str` | 是 | 主机 | 所属技能 |
| `version` | `str` | 是 | 主机 | 技能版本 |
| `task_id` | `str` | 是 | 主机 | 所属 task |

#### 最终完整字段表

| # | 字段名 | 类型 | 必填 | 来源 | 序列化格式 | 最大长度 | 验证规则 |
|---|--------|------|------|------|-----------|---------|---------|
| 1 | `artifact_schema_version` | `int` | ✅ | 主机 | 整数 | — | 当前固定为 1 |
| 2 | `artifact_id` | `str` | ✅ | 主机 | 32 hex chars | 32 | `uuid4().hex`，完整 32 字符 |
| 3 | `display_name` | `str` | ✅ | 技能 | 纯文本 | 128 chars | 非空，strip 后非空 |
| 4 | `relative_path` | `str` | ✅ | 技能 | POSIX 相对路径 | 255 chars | 非空，无 `..`，无 `\`，非绝对 |
| 5 | `storage_relpath` | `str` | ✅ | 主机 | POSIX 相对路径 | — | 相对于 artifact_root；禁止绝对路径 |
| 6 | `media_type` | `str` | ✅ | 主机推断 | MIME type string | 64 chars | 格式 `type/subtype`；由主机内容嗅探确定 |
| 7 | `kind` | `str` | ✅ | 技能 | lowercase label | 32 chars | 仅 `chart`/`table`/`document`/`data`/`other` |
| 8 | `size_bytes` | `int` | ✅ | 主机 | 整数 | — | ≥0 |
| 9 | `sha256` | `str` 或 `None` | ✅(可 None) | 主机 | 64 hex chars 或 null | 64 | 模型层 `str \| None`；已发布 Run Artifact 强制 `[a-f0-9]{64}` |
| 10 | `created_at` | `str` | ✅ | 主机 | ISO 8601 UTC | 32 chars | 合法 ISO 8601 |
| 11 | `skill_id` | `str` | ✅ | 主机 | skill_id 格式 | 64 chars | 匹配 `_SKILL_ID_RE` |
| 12 | `version` | `str` | ✅ | 主机 | semver | 16 chars | 匹配 `_VERSION_RE` |
| 13 | `task_id` | `str` | ✅ | 主机 | hex | 64 chars | 匹配 `_TASK_ID_RE` |
| 14 | `metadata` | `dict` | ✅(可空) | 技能 | JSON object | 4096 bytes | JSON-compatible 递归检查 |

#### 关键语义冻结

```text
relative_path：
  技能声明的 workspace/output 相对路径；
  旧 healthcheck 和旧 response 继续兼容。

storage_relpath：
  主机生成，始终相对于 Artifact 根；
  禁止绝对路径；
  UI 不得自行拼接或解析。

sha256：
  数据模型层继续允许 None，以兼容旧 healthcheck；
  对已发布 Run Artifact 由 Service 强制为 64 位 hex。

artifact_id：
  使用完整 uuid4().hex，即 32 个 hex 字符；
  不得截断为 16 字符。

artifact_schema_version：
  当前固定为 1；
  manifest 读取必须严格验证版本。
```

#### 不在公开 RuntimeArtifact 中暴露的字段

```text
published_path（绝对路径）— 不得在公开 RuntimeArtifact 中暴露
```

Service、ArtifactStore 内部可以将 `artifact_root + storage_relpath` 解析为实际路径，但不得要求 UI 自行解析。

#### 序列化兼容规则

```text
旧格式 from_dict() 可以容忍缺失新增字段；
发布后的 Run Artifact 必须执行 operation-specific 严格验证；
manifest 未知顶层字段 → 严格拒绝；
公开 response 的未知新增字段 → 为兼容性可容忍。
```

#### 示例序列化

```json
{
  "artifact_schema_version": 1,
  "artifact_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
  "display_name": "标定曲线",
  "relative_path": "chart.png",
  "storage_relpath": "my-skill/abc123def456/a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6_chart.png",
  "media_type": "image/png",
  "kind": "chart",
  "size_bytes": 45231,
  "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "created_at": "2026-07-22T10:30:00.000000+00:00",
  "skill_id": "my-skill",
  "version": "1.0.0",
  "task_id": "abc123def456",
  "metadata": {"title": "校准曲线", "x_axis": "力值 (N)"}
}
```

### 5.5 冻结约束汇总

```text
artifacts 顺序: 声明顺序 = 列表顺序 = UI 展示顺序
artifact_id 唯一性: 同一 response 内唯一（主机保证）
display_name 默认规则: Path(relative_path).stem；strip 后为空则使用原文件名；超长按 UTF-8 安全字符边界截断到 128 字符
relative_path: 保持既有名称，兼容 healthcheck 和旧 response
旧 response 兼容: from_dict() 继续容忍缺失字段，新字段使用安全默认值
healthcheck: 保持既有自动收集 output 文件行为（见第 6 节）
技能不能伪造: size、hash、storage_relpath、artifact_id、skill_id、version、task_id、created_at
```

---

## 6. Healthcheck 兼容边界（P0 冻结）

### 6.1 核心决策

Batch 3.2 只为 `operation="run"` 增加显式声明和持久化发布。

**必须冻结**：

```text
healthcheck 现有 Artifact 行为保持原样
不迁移 healthcheck 到 declare_artifact
不删除 healthcheck 现有自动收集逻辑
不改变现有 healthcheck 相关测试语义
```

### 6.2 253 项基线兼容

当前 253 项基线中，healthcheck 会收集 output Artifact（自动扫描 `workspace/output/` 下普通文件，收集 `relative_path`、`size_bytes`、`sha256=None`）。**该行为继续保持，不得改变。**

### 6.3 明确禁止的写法

```text
不得再写: "healthcheck artifacts 始终为空"
不得再写: "healthcheck 不发布 artifact"
```

正确表述：

```text
healthcheck 保持既有自动收集 output Artifact 行为
healthcheck 不迁移到 declare_artifact API
healthcheck 的 sha256 继续允许 None
```

### 6.4 Run 的规则

```text
未调用 declare_artifact → artifacts=()
output 中未声明文件不发布
result 中的普通 path 字符串不发布
```

---

## 7. 源文件安全合同

### 7.1 唯一源根

Artifact 候选源文件只能来自：

```text
<workspace>/output/
```

### 7.2 精确验证顺序

```
技能调用 declare_artifact(path)
→ 拒绝绝对路径
→ 拒绝空路径
→ 规范化相对路径（PosixPath，拒绝 Windows 反斜杠在声明中）
→ 拼接 workspace/output + path
→ resolve(strict=False)
→ 确认 resolved 位于 workspace/output 内
→ lstat（非 follow）获取文件类型
→ 拒绝 S_ISLNK（symlink）
→ 拒绝 S_ISDIR（目录）
→ 拒绝 S_ISFIFO / S_ISSOCK / S_ISBLK / S_ISCHR（特殊文件）
→ 拒绝 st_nlink > 1（hardlink — 已有其他引用）
→ 确认 S_ISREG（普通文件）
→ 检查 st_size 在限制内
→ 检查扩展名/MIME 在 allowlist
→ 计算 SHA256（流式读取，限制读取字节数）
→ 排队声明
```

### 7.3 路径攻击测试矩阵

每个场景需要真实文件系统测试（不全部 mock）：

| # | 攻击场景 | 预期结果 | 测试方式 |
|---|---------|---------|---------|
| 1 | 绝对路径 `/etc/passwd` | 拒绝（绝对路径） | 真实文件系统 |
| 2 | `../../etc/passwd` | 拒绝（`..`） | 真实文件系统 |
| 3 | `a//b/../c` | 规范化后必须在 output 内 | 真实文件系统 |
| 4 | 空路径 `""` | 拒绝 | 纯函数 |
| 5 | 目录路径 `subdir/` | `lstat` → S_ISDIR → 拒绝 | 真实文件系统 |
| 6 | FIFO/device/socket | S_ISFIFO/S_ISSOCK → 拒绝 | POSIX 平台专项（仅在 POSIX 上定义并收集） |
| 7 | symlink 文件 | `lstat` → S_ISLNK → 拒绝 | 真实文件系统 |
| 8 | symlink 父目录 | resolve 在 output 内的 symlink 目标 → 仍需 lstat 确认 S_ISREG + nlink=1 | 真实文件系统 |
| 9 | Windows junction | `lstat` → 非 S_ISREG → 拒绝 | Windows 平台专项（仅在 Windows 上定义并收集） |
| 10 | Windows reparse point | `lstat` → 非 S_ISREG → 拒绝 | Windows 平台专项（仅在 Windows 上定义并收集） |
| 11 | 大小写路径差异 (Windows) | `os.path.normcase` 统一比较 | Windows 平台专项 |
| 12 | UNC 路径 `\\server\share\file` | 拒绝（绝对/非 output 内） | Windows 平台专项 |
| 13 | 盘符路径 `C:\temp\file` | 拒绝（绝对路径） | Windows 平台专项 |
| 14 | NTFS ADS `file.txt:hidden` | `lstat` → ADS 不可见或非 S_ISREG → 拒绝 | Windows 平台专项 |
| 15 | 不存在的文件 | `lstat` → FileNotFoundError → 拒绝 | 真实文件系统 |
| 16 | TOCTOU: 声明-复制间替换 | 使用安全文件句柄（`open` + `fstat` + 比较 dev/inode），不一致则拒绝 | 真实文件系统 |

### 7.4 Hardlink 决策

**决策: 拒绝 link count > 1**

文件 `st_nlink > 1` 意味着同一 inode 被多个目录条目引用。通过 hardlink 修改文件内容不会改变 artifact 的已发布字节，但存在以下风险：

- 技能通过 hardlink 引用 workspace 外的敏感文件（需要 inode 在同一文件系统）
- 发布后原始 hardlink 被修改，破坏了 artifact 的完整性假设

由于 `workspace/output` 的文件系统在主机控制下，且跨目录 hardlink 需要 CAP_SYS_ADMIN（Linux）或管理员权限（Windows），实际风险极小。但出于防御深度原则，统一拒绝 nlink > 1。

如果未来有合法需求（如去重存储），可以通过安全文件句柄复制并记录风险边界，但 Batch 3.2 不实现此路径。

### 7.5 runtime_permissions.py 不可修改声明

**本批不修改 `runtime_permissions.py`**。该文件的 audit hook 控制 Worker 子进程内的文件访问。Artifact 的 symlink/hardlink/文件类型检查发生在 Worker 进程内的 `declare_artifact()` API 中，使用 `lstat` 系统调用——不依赖 audit hook。这是独立的安全层。

---

## 8. 数量、名称、类型和大小限制

### 8.1 冻结常量

| 常量 | 值 | 理由 |
|------|----|------|
| `MAX_ARTIFACTS_PER_RUN` | `20` | 限制单次 run 的 artifact 数量。合理覆盖典型报告需求（若干图表 + 若干数据文件 + 若干文档），同时限制资源消耗 |
| `MAX_ARTIFACT_FILE_BYTES` | `50 * 1024 * 1024` (50 MB) | 单文件上限。覆盖高分辨率图表和大型数据导出，同时拒绝意外大文件 |
| `MAX_ARTIFACTS_TOTAL_BYTES` | `200 * 1024 * 1024` (200 MB) | 全部 artifact 总字节上限 |
| `MAX_DECLARE_MANIFEST_BYTES` | `128 * 1024` (128 KB) | 所有声明的序列化 JSON 总大小。20 个 artifact × ~6KB 元数据余量，确保合法最大声明不会必然冲突 |
| `MAX_DISPLAY_NAME_CHARS` | `128` | 人类可读名称最大字符数 |
| `MAX_DECLARED_PATH_CHARS` | `255` | 相对路径最大字符数（POSIX 单文件名 255） |
| `MAX_METADATA_JSON_BYTES` | `4096` (4 KB) | 每个 artifact 的 metadata dict 序列化后最大字节 |
| `MAX_METADATA_AGGREGATE_BYTES` | `102400` (100 KB) | 全部声明的 metadata 聚合上限 |

### 8.2 文件类型策略

**选择: 扩展名 allowlist + 内容嗅探（双重验证）**

1. **扩展名 allowlist 主检查**: 基于声明的 path 扩展名
2. **内容嗅探**: 主机根据文件内容精确判定最终 `media_type`
3. 技能提供的 `media_type` 仅为 **hint**，不是最终权威

**media_type 判定规则**：

```text
hint 缺失：
  主机根据扩展名和内容推断最终 media_type。

hint 与主机推断一致：
  接受 hint 对应的 media_type。

hint 与主机推断冲突：
  protocol_error。
```

### 8.3 允许的文件类型与精确嗅探

| 扩展名 | 最终 MIME | 内容嗅探规则 |
|--------|----------|------------|
| `.pdf` | application/pdf | 以 `%PDF-` 开头 |
| `.docx` | application/vnd.openxmlformats-officedocument.wordprocessingml.document | 有效 ZIP，包含 `[Content_Types].xml` 和 `word/` 目录 |
| `.pptx` | application/vnd.openxmlformats-officedocument.presentationml.presentation | 有效 ZIP，包含 `[Content_Types].xml` 和 `ppt/` 目录 |
| `.xlsx` | application/vnd.openxmlformats-officedocument.spreadsheetml.sheet | 有效 ZIP，包含 `[Content_Types].xml` 和 `xl/` 目录 |
| `.csv` | text/csv | 严格 UTF-8/UTF-8-SIG，拒绝 NUL 字节 |
| `.json` | application/json | 严格 UTF-8/UTF-8-SIG 且可被 `json` 解析 |
| `.txt` | text/plain | 严格 UTF-8/UTF-8-SIG，拒绝 NUL 字节 |
| `.png` | image/png | PNG magic bytes `\x89PNG\r\n\x1a\n` |
| `.jpg` / `.jpeg` | image/jpeg | JPEG SOI magic `\xFF\xD8\xFF` |
| `.zip` | application/zip | 有效 ZIP（不自动解压，不进一步验证内容） |

### 8.4 从 allowlist 显式移除

| 类型 | 移除理由 |
|------|---------|
| `.svg` | SVG 具有脚本、外部资源和活动内容风险；当前又没有安全渲染或净化器。列为未来 P2 |

### 8.5 明确拒绝的类型

| 类型 | 理由 |
|------|------|
| `.exe`, `.dll`, `.bat`, `.cmd`, `.ps1`, `.js`, `.py`, `.vbs`, `.msi` | 可执行/脚本 — 安全风险 |
| 无扩展名文件 | 无法验证类型 |
| 双扩展名（如 `report.pdf.exe`） | 伪装攻击 — 仅最后一个扩展名有效，`.exe` 被拒绝 |
| 大小写变体 | 扩展名匹配大小写不敏感 |

### 8.6 metadata 验证

metadata 复用现有 JSON 深度、容器数量、字符串长度和 finite-float 校验规则。额外限制：

```text
每项 metadata ≤ 4096 bytes（序列化 JSON）
全部声明 metadata 聚合 ≤ 100 KB
```

### 8.7 类型策略与 Report Bridge 的关系

**支持某文件类型 ≠ 允许自动导入 Word/PPT**。`.pdf`、`.docx`、`.pptx` 等类型在 artifact allowlist 中仅表示技能可以将其作为 artifact 发布。将它们导入 Word/PPT 报告是 Batch 3.3 的工作，不在 3.2 范围内。

---

## 9. Artifact 发布根与原子发布（P0 校正）

### 9.1 Artifact 发布根

```
<app-data>/skills/artifacts/
  <skill_id>/
    <task_id>/
      <artifact_id>_<safe_filename>
      manifest.json
```

示例:
```
C:/Users/<user>/AppData/Local/DataProcessorPro/skills/artifacts/
  my-calibration-skill/
    a1b2c3d4e5f6/
      7f8a9b0c1d2e3f4a_calibration_chart.png
      manifest.json
```

### 9.2 结构属性

- **主机所有**: 所有文件和目录由 Runtime Service 创建和管理
- **任务隔离**: 每个 task 有独立子目录（`<task_id>`）
- **技能隔离**: 每个技能有独立目录（`<skill_id>`）
- **不可覆盖其他任务**: task 目录名由主机生成，不同 task 不可能冲突
- **可追踪 owner**: 路径中包含 `skill_id` 和 `task_id`
- **可清理**: 按 task 或 skill 级别删除
- **不依赖 workspace 存活**: Artifact 发布后独立于 workspace 存在

### 9.3 原子发布流程（P0 校正）

```
1. 确认 skill 父目录安全（非 symlink/reparse）
   → 在 skill 目录内创建唯一临时目录:
     <artifact_root>/<skill_id>/.commit_<uuid>
   → 禁止先创建最终 task 目录

2. 对每个声明的 artifact:
   a. 安全打开源文件（open + fstat）
   b. 验证 fstat 结果与 lstat 声明一致（TOCTOU 防御: dev/inode/size 比较）
   c. 流式复制到临时目录中的目标文件:
      <temp_commit_dir>/<artifact_id>_<safe_filename>
   d. fsync 目标文件
   e. 对目标文件 lstat 确认 size
   f. 确认目标文件不是 symlink/reparse
   g. 如果已有 SHA256 → 对比；否则计算目标文件 SHA256
   h. 验证目标 resolve 仍在 artifact_root 内

3. 全部 artifact 复制成功后:
   a. 构建 manifest.json（包含所有 RuntimeArtifact 的完整信息 + commit_fingerprint）
   b. 写入临时 manifest.json
   c. fsync manifest.json
   d. 平台允许时 fsync 目录（Linux: os.fsync(os.open(dir)), Windows: 不适用）

4. 原子提交前检查:
   → 确认最终 task 目录不存在
   → 最终 task 目录存在 → 进入重试判断（见 9.4）
   → os.replace(<temp_commit_dir>, <final_task_dir>)
   → 整个 task 的发布集对消费者原子可见

5. 失败回滚:
   删除 <temp_commit_dir>
   response.artifacts = ()
```

### 9.4 同一 task 重试（P0 校正）

**不得**: 先删除旧 task 目录再覆盖。

```text
final_task_dir 不存在：
  正常提交。

final_task_dir 存在且 manifest 完整、task_id 匹配、commit_fingerprint 与本次完全一致：
  返回既有已提交 Artifact，视为幂等成功；
  不删除、不重写。

final_task_dir 存在但 manifest 损坏、owner 不匹配或 fingerprint 不同：
  fail closed；
  status=failed；
  不删除已有目录，不覆盖。
```

### 9.5 commit_fingerprint

`commit_fingerprint` 由以下稳定字段计算（声明顺序敏感）：

```text
声明顺序（列表索引）
每个声明的源文件 SHA256
每个声明的 display_name
每个声明的 kind
每个声明的 media_type_hint
每个声明的 metadata（序列化后）
```

同一任务相同输入产生相同 fingerprint → 幂等。任何差异产生不同 fingerprint → 拒绝覆盖。

### 9.6 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 复制还是移动 | **复制** | workspace 文件在 `cleanup_workspace` 时被删除，必须复制 |
| 为什么不直接暴露 workspace 文件 | workspace 在返回前被清理 | Artifact 必须持久化到独立根 |
| 目标已存在 | **根据指纹判断**（见 9.4） | 幂等返回或 fail closed |
| 同一 task 重试 | **幂等指纹匹配**（见 9.4） | 不删除已有数据 |
| 部分发布失败 | 全部回滚，不提交 | 删除临时目录，response.artifacts=() |
| 临时文件命名 | `.commit_<uuid4_hex>` | 不可预测，避免冲突 |
| 崩溃恢复 | ArtifactPublisher 初始化或每次 publish 前执行一次有界清理 | 孤儿 `.commit_*` 清理 |
| 不完整目录识别 | 无 `manifest.json` → 视为不完整 | 随孤儿清理处理 |

### 9.7 事务语义

**多 artifact 要么全部发布，要么全部不可见**。`os.replace()` 是原子操作（同文件系统内），整个 task 发布集一次性变为可见。不存在逐文件成功的半发布状态。

---

## 10. 目标 Artifact 根安全（P0 强化）

### 10.1 双重根验证

源根验证（`workspace/output/`）之外，**目标 Artifact 根也必须验证**：

每次发布、读取、导出、删除前必须验证：

```text
artifact_root 本身不是 symlink/reparse
skill 目录不是 symlink/reparse
task 目录不是 symlink/reparse
manifest 和 Artifact 文件不是 symlink/reparse
所有 resolve 结果仍位于 artifact_root 内
```

### 10.2 创建目录时的安全

```text
创建目录时采用主机生成名称（uuid-based）
父目录必须是已验证的安全目录
不得仅对 workspace/output 源文件执行安全检查
```

### 10.3 孤儿清理触发点

```text
ArtifactPublisher 初始化时执行一次有界清理
每次 publish 前执行一次有界清理
```

不得写"应用启动时扫描"却不批准任何启动集成文件。

---

## 11. 状态映射和事务边界

### 11.1 精确调用顺序

```
Worker 完成（子进程退出，result.json 已写入）
→ read_response_atomic() 读取并验证 Worker response
→ 提取 response 中的 artifact_declarations 列表（内部 wire 字段）
→ [新增] 检查 Worker 是否设置了 fatal declaration error flag
→ [新增] 如有 fatal flag → status=protocol_error, result=None, artifacts=()
→ [新增] Service 侧二次验证每个声明（防御深度）:
    - 重新 lstat 源文件
    - 重新检查路径在 workspace/output 内
    - 重新检查文件类型/大小/扩展名
    - 重新计算 SHA256 并与 Worker 报告的 SHA256 对比
→ [新增] 原子发布到 Artifact 根（含目标根安全验证）
→ [新增] 构建主机权威 RuntimeArtifact 列表
→ 最终 SkillRuntimeResponse（artifacts=主机权威列表）
→ cleanup_workspace(succeeded=True)
→ 返回 response 给调用者
```

### 11.2 状态映射表

| 场景 | Runtime status | success | artifacts | workspace 清理 | 发布状态 |
|------|---------------|---------|-----------|---------------|---------|
| 正常 succeeded，无 artifact | succeeded | True | `()` | 清理 | 无发布目录 |
| 正常 succeeded，artifact 全部发布成功 | succeeded | True | 主机权威列表 | 清理 | 已发布 |
| business-negative `{"ok": false}`，有 artifact | succeeded | True | 主机权威列表（如声明了） | 清理 | 已发布（如声明了） |
| business-negative `{"ok": false}`，无 artifact | succeeded | True | `()` | 清理 | 无 |
| 声明 schema 非法（如声明不存在的文件） | **protocol_error** | False | `()` | 保留（失败） | 不发布 |
| 源路径越界 | **protocol_error** | False | `()` | 保留 | 不发布 |
| symlink/reparse 拒绝（源或目标） | **protocol_error** | False | `()` | 保留 | 不发布 |
| 文件不存在（声明后到 lstat 间被删除） | **failed** | False | `()` | 保留 | 不发布 |
| 数量超限 | **protocol_error** | False | `()` | 保留 | 不发布 |
| 单文件超限 | **protocol_error** | False | `()` | 保留 | 不发布 |
| 总大小超限 | **protocol_error** | False | `()` | 保留 | 不发布 |
| 类型拒绝 | **protocol_error** | False | `()` | 保留 | 不发布 |
| media_type hint 冲突 | **protocol_error** | False | `()` | 保留 | 不发布 |
| SHA256 不匹配（Worker vs Service） | **protocol_error** | False | `()` | 保留 | 不发布 |
| 复制失败（磁盘满等） | **failed** | False | `()` | 保留 | 回滚临时目录 |
| 原子 rename 失败 | **failed** | False | `()` | 保留 | 回滚临时目录 |
| 目标 artifact_root symlink → 拒绝 | **protocol_error** | False | `()` | 保留 | 不发布 |
| 技能 try/except 吞掉非法声明 | **protocol_error** | False | `()` | 保留 | 不发布 |
| Worker timeout | timeout | False | `()` | 保留 | 不发布 |
| Worker crashed | crashed | False | `()` | 保留 | 不发布 |
| protocol_error（Worker response 非法） | protocol_error | False | `()` | 保留 | 不发布 |
| permission_denied（Worker 内 audit hook） | permission_denied | False | `()` | 保留 | 不发布 |

### 11.3 Business-negative 与 Artifact

**决策: business-negative 允许发布 artifact**

理由:

1. `{"ok": false}` 是业务语义，不是 Runtime 失败。业务操作可能产生诊断数据、部分结果或日志文件
2. 如果业务操作声明了 artifact（在发现 `ok=false` 前），这些文件已经是合法输出
3. 禁止 business-negative 发布 artifact 会在声明 API 中引入时序依赖（技能必须在返回前知道最终 ok 值）
4. 消费者可以自己决定是否信任 business-negative 的 artifact

**约束**: business-negative 时 artifact 与 succeeded 时遵循完全相同的安全验证规则。

### 11.4 Artifact 失败使用的状态

**决策: 使用现有 VALID_RUN_STATUSES 中的状态，不新增 `artifact_error`**

| Artifact 失败原因 | 映射状态 | 理由 |
|------------------|---------|------|
| 声明 schema/路径/类型/大小非法 | `protocol_error` | 技能违反了声明协议 — 这是协议失败 |
| 文件系统错误（复制失败、rename 失败） | `failed` | Runtime 执行失败 — 非技能可控 |
| SHA256 不匹配 | `protocol_error` | Worker 报告的数据与 Service 验证不一致 — 协议完整性失败 |
| 技能 try/except 吞掉非法声明 | `protocol_error` | Fatal declaration error — 不可吞掉的协议失败 |

---

## 12. 失败、取消与清理

### 12.1 清理对象

| 对象 | 成功时 | 失败/取消/crash 时 |
|------|--------|-------------------|
| workspace | 完整删除 | 保留用于诊断 |
| 发布临时目录 `.commit_*` | 已 rename 为目标目录 | 删除（回滚） |
| 发布临时文件 | 已 rename 为最终文件 | 随临时目录删除 |
| 未提交 manifest | 不存在（已提交） | 删除 |
| 已提交 Artifact 任务目录 | 保留 | 不创建 |
| 内存索引 | 不需要 | 不需要 |
| UI 引用 | 通过 response.artifacts | response.artifacts=() |

### 12.2 场景清理合同

#### 成功（有 artifact）

```
1. 所有 artifact 已复制到 artifact 根并原子提交
2. workspace 完整删除（shutil.rmtree）
3. 最终 published Artifact 保留在 artifact 根
4. response.artifacts = 主机权威列表
```

#### 成功（无 artifact）

```
1. workspace 完整删除
2. 不创建发布目录（无空 task 目录）
3. response.artifacts = ()
```

#### failed / permission_denied / timeout / cancelled / crashed / protocol_error

```
1. 不发布任何 Artifact
2. 删除全部发布临时状态（临时目录/文件）
3. workspace 保留用于诊断（cleanup_workspace(succeeded=False)）
4. response.artifacts = ()
```

### 12.3 取消提交点（P0 冻结）

唯一提交点为 `os.replace()` 成功。

```text
提交点之前观察到 cancel：
  回滚临时目录
  status=cancelled
  artifacts=()

提交点完成后才观察到 cancel：
  取消请求视为过晚
  保留提交
  保持原始成功状态 succeeded
  返回已发布 artifacts
```

**不得产生**:

```text
status=cancelled 且 artifacts 非空
```

### 12.4 business-negative 与取消

business-negative 仍属于 Runtime `succeeded`，允许正常提交 Artifact。

---

## 13. 主机侧 ArtifactStore 消费 API（P0 新增）

### 13.1 核心原则

UI 不得直接：

```text
拼接 artifact_root
解析 storage_relpath
lstat
hash
复制文件
删除 task 目录
调用 os.replace
```

### 13.2 ArtifactStore API

在 `dp_engine/skills/runtime_artifacts.py` 中冻结主机侧 API：

```python
class ArtifactStore:
    """主机侧 Artifact 消费 API。UI 只能通过此 API 访问已发布 Artifact。"""

    def list_task(self, task_id: str) -> tuple[RuntimeArtifact, ...]:
        """列出指定 task 的所有已发布 Artifact。"""
        ...

    def locate(self, artifact_id: str) -> None:
        """在文件管理器中定位指定 Artifact。
        内部执行: manifest 验证、owner 验证、storage_relpath 解析、
        Artifact 根约束、symlink/reparse 拒绝、文件存在性检查。
        """
        ...

    def export(
        self, artifact_id: str, target: Path, *, overwrite: bool = False
    ) -> None:
        """将 Artifact 安全复制到用户指定位置。
        内部执行: manifest 验证、owner 验证、storage_relpath 解析、
        Artifact 根约束、symlink/reparse 拒绝、size/hash 验证、
        目标路径逃逸拒绝、临时文件 + 原子 replace。
        """
        ...

    def delete_task(self, task_id: str) -> None:
        """安全删除指定 task 的全部 Artifact。
        内部执行: manifest 验证、owner 验证、任务目录定位、
        确认在 artifact_root 内、安全删除。
        """
        ...
```

### 13.3 每个 API 内部责任

```text
manifest 验证
owner 验证
storage_relpath 解析
Artifact 根约束
symlink/reparse 拒绝
文件存在性
size/hash 验证
安全复制或删除
目标路径逃逸拒绝
```

### 13.4 UI 责任

UI 只负责：

```text
展示 RuntimeArtifact 元数据（display_name、kind、size_bytes、media_type）
获取用户目标路径和覆盖确认
调用 ArtifactStore
显示成功或安全错误
```

UI 不得：

```text
根据 result 路径字符串自行构造 Artifact
直接读取 workspace/output
自行复制未验证的技能文件
拼接 artifact_root 路径
解析 storage_relpath
自行删除 task 目录
```

### 13.5 用户删除能力

用户删除能力必须纳入 Batch 3.2.2 并有安全测试。不得一处承诺删除、另一处没有实现或测试。

### 13.6 安全消费（Batch 3.2.2）

#### UI 最低能力

| 能力 | 描述 | 安全性 |
|------|------|--------|
| 列出本次 Run 发布的 Artifacts | 显示名称、类型、大小、状态 | 只读 |
| 在文件管理器中定位 | 调用 `ArtifactStore.locate(artifact_id)` | ArtifactStore 内部验证 |
| 导出/另存为 | 调用 `ArtifactStore.export(artifact_id, target, overwrite=True)` | ArtifactStore 内部验证 + 原子 replace |
| 删除 | 调用 `ArtifactStore.delete_task(task_id)` | ArtifactStore 内部验证 + 安全删除 |

#### 明确禁止

```text
自动执行 artifact 文件
自动用 shell 打开不受信类型
自动 import 到报告系统（Batch 3.3）
自动传入 Word/PPT Builder（Batch 3.3）
自动解压 zip
自动渲染 HTML/SVG 脚本内容
自动运行脚本或宏
预览 artifact 内容（推迟到 P2 — 仅提供"定位"和"另存为"）
```

#### "打开" 能力决策

**推迟到 P2**。Batch 3.2.2 只提供：

- **定位**: 打开文件管理器并选中文件
- **另存为**: 复制到用户选择的位置

不提供"打开"或"预览"。需要时由用户在文件管理器中自行打开。

---

## 14. 所有权、生命周期和留存

### 14.1 Artifact Owner

每个 artifact 记录：

```text
task_id
skill_id
skill_version (version)
operation (="run")
created_at
```

这些信息全部存储在 `manifest.json` 和 `RuntimeArtifact` 字段中。

### 14.2 持久化策略

**决策: 文件系统 manifest.json + 内存 response**

- **文件系统**: 每个 task 目录下有 `manifest.json`
- **不持久化到 Registry**: 不污染 Registry 健康状态和 run 状态
- **不建立独立 Artifact Registry**: Batch 3.2 不使用独立索引
- **内存**: response.artifacts 传递给 UI

### 14.3 生命周期规则

| 场景 | 处理 |
|------|------|
| Artifact 保存根 | `<app-data>/skills/artifacts/` |
| 默认留存周期 | 永久（Batch 3.2 不实现自动过期清理 — 列为 P2） |
| 用户删除 | 通过 `ArtifactStore.delete_task(task_id)` 删除 task 目录（Batch 3.2.2） |
| 技能卸载后 | Artifact 保留（文件是主机所有，不依赖技能） |
| 技能升级后 | Artifact 保留（历史 artifact 属于旧版本的 task） |
| 应用退出 | Artifact 保留（持久化在磁盘上） |
| 孤儿目录扫描 | ArtifactPublisher 初始化 + 每次 publish 前有界清理 |
| 同一 task 重复发布 | 指纹幂等匹配（见 9.4），不删除不回写 |
| Artifact metadata 损坏 | 读取时校验 manifest.json，损坏则标记为不可用 |
| 文件被外部修改 | 读取时可选验证 SHA256（ArtifactStore 导出时检查） |
| Hash 验证时机 | 发布时（必须）+ 消费时（ArtifactStore.export 时验证） |

### 14.4 并发、幂等和 TOCTOU

#### 并发场景

| 场景 | 处理 |
|------|------|
| 两个技能并发发布 | 不同 `<skill_id>` 目录 — 天然隔离 |
| 同一技能两个 task 并发 | 不同 `<task_id>` 目录 — 天然隔离 |
| 相同文件名 | 发布时使用 `<artifact_id>_<safe_filename>` — artifact_id 是 UUID，不可能冲突 |
| 相同 artifact_id | 主机生成 UUID — 不可能重复 |
| 同一 task 重复提交 | 指纹匹配 → 幂等返回；指纹不同 → fail closed |

#### TOCTOU 防御

```
1. declare_artifact() 时 lstat 源文件 → 记录 dev/inode/size/mtime
2. 发布复制时 open + fstat 源文件 → 比较 dev/inode/size
3. 不一致 → 拒绝发布，全部回滚
4. 流式复制时只读已打开的 fd — 源文件替换不影响 fd
5. 复制完成后重新计算 SHA256 → 与 Worker 报告的 SHA256 比较
6. 目标文件 lstat 确认非 symlink/reparse + resolve 在 artifact_root 内
```

#### 安全基元

| 基元 | 用途 |
|------|------|
| 唯一 task 目录 | 任务隔离 |
| 不可预测临时名 | 避免冲突 |
| 安全打开的文件句柄 | TOCTOU 防御 |
| 原子目录提交 | 全有或全无 |
| 主机生成 artifact_id | 不可伪造 |
| commit_fingerprint | 幂等检测 |

**禁止**: "先 exists 再 copy" 的非原子检查；"先删除旧目录再覆盖"。

---

## 15. 实施文件边界（P0 校正）

### 15.1 子批拆分

```text
Batch 3.2.0: 规划 P0 校正（本批）— 已完成
    │
Batch 3.2.1: Artifact Core / Publisher
    │  目标: ArtifactRunContext、ArtifactDeclaration、
    │        declare_artifact API、RuntimeArtifact 扩展、
    │        Artifact Publisher、原子发布、安全验证、
    │        ArtifactStore 基础实现
    │  审核包: docs/agents/batch-3.2.1-audit-package.md
    │  可独立审核和回滚
    │
Batch 3.2.2: Artifact UI Consumption
    │  目标: UI artifact 列表、定位、导出/另存为、删除、
    │        安全消费（通过 ArtifactStore API）
    │  前提: 3.2.1 审核通过
    │  审核包: docs/agents/batch-3.2.2-audit-package.md
    │  可独立审核和回滚
    │
Batch 3.2.3: 统一回归与封板
       目标: 完整回归运行、P0 清零、最终封板
       审核包: docs/agents/batch-3.2.3-audit-package.md
```

### 15.2 Batch 3.2.1 允许修改的文件

#### 允许修改的生产文件

```text
dp_engine/skills/runtime_models.py     — RuntimeArtifact 字段扩展
                                         — 新增 ArtifactDeclaration 模型
                                         — 新增 Artifact 限制常量
                                         — to_dict/from_dict 更新
                                         — 新增 ArtifactRunContext 类
dp_engine/skills/runtime_protocol.py   — 新增 artifact_declarations wire 字段验证
                                         — 新增 operation-specific response 验证
                                         — 新增最终 RuntimeArtifact 验证
                                         — 新增 fatal declaration error 检测
dp_engine/skills/runtime_worker.py     — _execute_run() 不再硬编码 artifacts=[]
                                         — 注入 ArtifactRunContext（dict 子类）
                                         — 新增 _validate_declared_artifact()
                                         — 新增 _build_artifact_declarations()
                                         — 新增 fatal declaration error flag 检测
dp_engine/skills/runtime_service.py    — run_skill() 中新增 Artifact Publisher 步骤
                                         — 新增 _publish_artifacts()
                                         — 新增 _verify_and_copy_artifact()
                                         — 新增 _commit_artifact_transaction()
                                         — 原子发布流程
                                         — Service 侧二次验证
                                         — 目标根安全验证
dp_engine/skills/runtime_paths.py      — 新增 get_artifact_root()
                                         — 新增 get_artifact_task_dir()
                                         — 新增 cleanup_orphan_commit_dirs()
                                         — 新增 build_safe_filename()
                                         — iter_workspace_artifacts() 保留但不用于发布
```

#### 允许新增的生产文件

```text
dp_engine/skills/runtime_artifacts.py  — ArtifactPublisher 类
                                         - publish_artifacts()
                                         - _verify_artifact_source()
                                         - _copy_to_temp()
                                         - _commit_transaction()
                                         - _rollback_transaction()
                                         - _build_manifest()
                                         - 安全验证函数（lstat、symlink、hardlink、TOCTOU）
                                         - 文件类型 allowlist 和嗅探
                                         - 目标根安全验证
                                       — ArtifactStore 类
                                         - list_task()
                                         - locate()
                                         - export()
                                         - delete_task()
                                         - 内部安全验证
```

#### 允许修改的测试文件

```text
tests/fixtures/runtime_fixtures.py         — 新增 artifact 相关 fixture
tests/test_runtime_l1_models.py           — 新增 L1 artifact 测试
tests/test_runtime_l2_subprocess.py        — 新增 L2 artifact 发布测试
tests/test_runtime_l3_security_boundary.py — 新增 L3 artifact 安全边界测试
```

#### 允许新增的测试文件

```text
tests/test_runtime_l3_artifact_security.py — L3 artifact 路径/文件类型/symlink/hardlink/TOCTOU 测试
tests/test_runtime_l2_artifact_publish.py  — L2 真实 Worker + 发布流程测试
tests/test_runtime_artifact_store.py       — ArtifactStore 安全消费测试（必要时新增）
```

### 15.3 Batch 3.2.1 绝对禁止修改的文件

```text
dp_engine/skills/runtime_permissions.py — 权限边界不可修改
dp_engine/skills/registry.py            — Registry 零写入
dp_engine/skills/installer.py           — 安装器不在 3.2 范围
ui/skill_tab.py                         — UI 属于 3.2.2
ui/skill_runtime_controller.py          — UI 属于 3.2.2（仅传递或调用 ArtifactStore）
core/report_engine.py                   — 属于 Batch 3.3
ui/report_workbench.py                  — 属于 Batch 3.3
dp_engine/report_builder/               — 属于 Batch 3.3
core/chart_bundle.py                    — 不在任何已批准范围
core/chart_registry.py                  — 不在任何已批准范围
core/chart_store.py                     — 不在任何已批准范围
```

### 15.4 Batch 3.2.2 允许修改的文件

```text
ui/skill_tab.py                      — 新增 artifact 列表区域
                                       — 新增 artifact 定位/导出/删除按钮
                                       — _on_run_result() 展示 artifacts
ui/skill_runtime_controller.py       — 仅传递 response.artifacts 或调用 ArtifactStore
                                       — 不承担路径安全验证
dp_engine/skills/runtime_artifacts.py — ArtifactStore 消费 API（如 3.2.1 未完成）
```

#### 允许新增的测试文件

```text
tests/test_runtime_ui_artifact.py      — UI artifact 消费测试
tests/test_runtime_artifact_store.py   — ArtifactStore 安全消费测试（如未在 3.2.1 新增）
```

### 15.5 重点保护基线

```text
253 项 Runtime 封板基线
2 项 installer symlink 哨兵
UI Run 生命周期（Batch 3.1.2）
Registry 健康状态不变性
runtime_permissions.py 拒绝边界
healthcheck 既有自动收集 output Artifact 行为
```

---

## 16. 测试矩阵（P0 校正）

### 16.1 L1: Schema 与纯函数 (Batch 3.2.1)

| 编号 | 语义 | 拟议 node ID | 真实 FS |
|------|------|-------------|---------|
| A-L1-01 | ArtifactRunContext 是 dict 子类 | `test_runtime_l1_models.py::TestArtifactContext::test_context_is_dict_subclass` | 否 |
| A-L1-02 | 既有 context["params"] 兼容 | `test_runtime_l1_models.py::TestArtifactContext::test_params_backward_compat` | 否 |
| A-L1-03 | 旧技能不声明 Artifact 时完全不变 | `test_runtime_l1_models.py::TestArtifactContext::test_no_declare_no_change` | 否 |
| A-L1-04 | ArtifactDeclaration 字段验证 | `test_runtime_l1_models.py::TestArtifactDeclaration::test_declaration_fields` | 否 |
| A-L1-05 | ArtifactDeclaration 不含主机字段 | `test_runtime_l1_models.py::TestArtifactDeclaration::test_no_host_fields` | 否 |
| A-L1-06 | 新 RuntimeArtifact roundtrip (to_dict/from_dict) | `test_runtime_l1_models.py::TestArtifactSchema::test_artifact_to_dict_from_dict_roundtrip` | 否 |
| A-L1-07 | from_dict 旧格式兼容（仅 3 字段） | `test_runtime_l1_models.py::TestArtifactSchema::test_old_format_backward_compat` | 否 |
| A-L1-08 | from_dict 未知字段不崩溃（public response） | `test_runtime_l1_models.py::TestArtifactSchema::test_unknown_fields_tolerated` | 否 |
| A-L1-09 | manifest 未知顶层字段严格拒绝 | `test_runtime_l1_models.py::TestArtifactSchema::test_manifest_unknown_fields_rejected` | 否 |
| A-L1-10 | declare_artifact 参数类型校验 | `test_runtime_l1_models.py::TestArtifactSchema::test_declare_params_type_validation` | 否 |
| A-L1-11 | display_name 默认规则（Path.stem） | `test_runtime_l1_models.py::TestArtifactSchema::test_display_name_default_from_stem` | 否 |
| A-L1-12 | display_name 超长截断（UTF-8 安全边界） | `test_runtime_l1_models.py::TestArtifactSchema::test_display_name_truncation` | 否 |
| A-L1-13 | declared_path 超长拒绝 | `test_runtime_l1_models.py::TestArtifactSchema::test_declared_path_too_long_rejected` | 否 |
| A-L1-14 | kind 非法值拒绝 | `test_runtime_l1_models.py::TestArtifactSchema::test_invalid_kind_rejected` | 否 |
| A-L1-15 | metadata 非 JSON-compatible 拒绝 | `test_runtime_l1_models.py::TestArtifactSchema::test_metadata_non_json_rejected` | 否 |
| A-L1-16 | metadata 单条超限拒绝 | `test_runtime_l1_models.py::TestArtifactSchema::test_metadata_size_exceeded_rejected` | 否 |
| A-L1-17 | metadata 聚合超限拒绝 | `test_runtime_l1_models.py::TestArtifactSchema::test_metadata_aggregate_exceeded_rejected` | 否 |
| A-L1-18 | 数量超限拒绝（>20） | `test_runtime_l1_models.py::TestArtifactLimits::test_max_count_exceeded` | 否 |
| A-L1-19 | 单文件大小超限拒绝 | `test_runtime_l1_models.py::TestArtifactLimits::test_max_file_size_exceeded` | 否 |
| A-L1-20 | 总大小超限拒绝 | `test_runtime_l1_models.py::TestArtifactLimits::test_max_total_size_exceeded` | 否 |
| A-L1-21 | 重复声明幂等 | `test_runtime_l1_models.py::TestArtifactSchema::test_duplicate_declare_idempotent` | 否 |
| A-L1-22 | artifact_id 唯一性（32 hex） | `test_runtime_l1_models.py::TestArtifactSchema::test_artifact_id_uniqueness` | 否 |
| A-L1-23 | artifact_schema_version = 1 | `test_runtime_l1_models.py::TestArtifactSchema::test_schema_version_is_1` | 否 |
| A-L1-24 | 非法声明即使被技能 catch 仍为 protocol_error | `test_runtime_l1_models.py::TestArtifactSchema::test_fatal_declaration_error_not_swallowable` | 否 |
| A-L1-25 | 内部 artifact_declarations 不暴露给 UI | `test_runtime_l1_models.py::TestArtifactSchema::test_declarations_not_in_public_response` | 否 |
| A-L1-26 | Worker 不能伪造最终 RuntimeArtifact 字段 | `test_runtime_l1_models.py::TestArtifactSchema::test_worker_cannot_forge_host_fields` | 否 |
| A-L1-27 | healthcheck 既有 Artifact 行为不变 | `test_runtime_l1_models.py::TestArtifactBackwardCompat::test_healthcheck_behavior_unchanged` | 否 |
| A-L1-28 | run response artifacts 始终为 tuple | `test_runtime_l1_models.py::TestArtifactSchema::test_artifacts_type_is_tuple` | 否 |

**预计 L1 新增: 28 node**（含 P0 校正新增的 ArtifactRunContext、ArtifactDeclaration、fatal error、healthcheck 兼容等语义）

### 16.2 L2: 真实 Worker 和发布 (Batch 3.2.1)

| 编号 | 语义 | 拟议 node ID | 真实 Worker |
|------|------|-------------|------------|
| A-L2-01 | 显式声明单个 artifact 并成功发布 | `test_runtime_l2_artifact_publish.py::TestArtifactPublish::test_single_artifact_published` | 是 |
| A-L2-02 | 显式声明多个 artifact | `test_runtime_l2_artifact_publish.py::TestArtifactPublish::test_multiple_artifacts_published` | 是 |
| A-L2-03 | 无声明 → artifacts=() | `test_runtime_l2_artifact_publish.py::TestArtifactPublish::test_no_declare_no_artifacts` | 是 |
| A-L2-04 | 写入文件但不声明 → 不发布 | `test_runtime_l2_artifact_publish.py::TestArtifactPublish::test_undeclared_file_not_published` | 是 |
| A-L2-05 | result 中 path 字符串不自动发布 | `test_runtime_l2_artifact_publish.py::TestArtifactPublish::test_result_path_string_not_published` | 是 |
| A-L2-06 | workspace 清理后 artifact 仍存在 | `test_runtime_l2_artifact_publish.py::TestArtifactPublish::test_artifact_survives_workspace_cleanup` | 是 |
| A-L2-07 | 主机字段正确生成 | `test_runtime_l2_artifact_publish.py::TestArtifactPublish::test_host_fields_generated` | 是 |
| A-L2-08 | business-negative 且有 artifact — 正常发布 | `test_runtime_l2_artifact_publish.py::TestArtifactPublish::test_business_negative_with_artifacts` | 是 |
| A-L2-09 | kind 和 display_name 传递正确 | `test_runtime_l2_artifact_publish.py::TestArtifactPublish::test_kind_and_display_name_preserved` | 是 |
| A-L2-10 | metadata 传递正确 | `test_runtime_l2_artifact_publish.py::TestArtifactPublish::test_metadata_preserved` | 是 |
| A-L2-11 | 声明顺序保留 | `test_runtime_l2_artifact_publish.py::TestArtifactPublish::test_declare_order_preserved` | 是 |
| A-L2-12 | 最终 task 目录不预创建 | `test_runtime_l2_artifact_publish.py::TestArtifactPublish::test_final_task_dir_not_precreated` | 是 |
| A-L2-13 | 相同 task 相同 fingerprint 幂等返回 | `test_runtime_l2_artifact_publish.py::TestArtifactPublish::test_same_fingerprint_idempotent` | 是 |
| A-L2-14 | 相同 task 不同 fingerprint 拒绝覆盖 | `test_runtime_l2_artifact_publish.py::TestArtifactPublish::test_different_fingerprint_rejected` | 是 |
| A-L2-15 | storage_relpath 由主机生成（非技能可控） | `test_runtime_l2_artifact_publish.py::TestArtifactPublish::test_storage_relpath_host_generated` | 是 |

**预计 L2 新增: 15 node**（含 P0 校正新增的原子提交、指纹幂等等语义）

### 16.3 L3: 安全边界 (Batch 3.2.1)

| 编号 | 语义 | 拟议 node ID | 真实 FS |
|------|------|-------------|---------|
| A-SEC-01 | 声明绝对路径 → protocol_error | `test_runtime_l3_artifact_security.py::TestArtifactPathSecurity::test_absolute_path_rejected` | 是 |
| A-SEC-02 | 声明 `..` 遍历 → protocol_error | `test_runtime_l3_artifact_security.py::TestArtifactPathSecurity::test_dot_dot_rejected` | 是 |
| A-SEC-03 | 声明 symlink 文件 → lstat 拒绝 (S_ISLNK) | `test_runtime_l3_artifact_security.py::TestArtifactPathSecurity::test_symlink_file_rejected` | 是 |
| A-SEC-04 | 声明 symlink 父目录 → resolve + lstat 拒绝 | `test_runtime_l3_artifact_security.py::TestArtifactPathSecurity::test_symlink_parent_dir_rejected` | 是 |
| A-SEC-05 | Windows junction → lstat 拒绝 | `test_runtime_l3_artifact_security.py::TestArtifactPathSecurity::test_junction_rejected` | Win 专项 |
| A-SEC-06 | hardlink (nlink > 1) → 拒绝 | `test_runtime_l3_artifact_security.py::TestArtifactPathSecurity::test_hardlink_rejected` | 是 |
| A-SEC-07 | 目录 → S_ISDIR 拒绝 | `test_runtime_l3_artifact_security.py::TestArtifactPathSecurity::test_directory_rejected` | 是 |
| A-SEC-08 | 特殊文件（FIFO/socket）→ 拒绝 | `test_runtime_l3_artifact_security.py::TestArtifactPathSecurity::test_special_file_rejected` | POSIX 专项 |
| A-SEC-09 | NTFS ADS → 拒绝 | `test_runtime_l3_artifact_security.py::TestArtifactPathSecurity::test_ads_rejected` | Win 专项 |
| A-SEC-10 | 不存在的文件 → 拒绝 | `test_runtime_l3_artifact_security.py::TestArtifactPathSecurity::test_nonexistent_file_rejected` | 是 |
| A-SEC-11 | 大小写路径差异 (Windows) → 正确检测 | `test_runtime_l3_artifact_security.py::TestArtifactPathSecurity::test_case_insensitive_path` | Win 专项 |
| A-SEC-12 | 数量超限 → protocol_error | `test_runtime_l3_artifact_security.py::TestArtifactLimits::test_count_exceeded_runtime` | 是 |
| A-SEC-13 | 单文件大小超限 → protocol_error | `test_runtime_l3_artifact_security.py::TestArtifactLimits::test_single_file_size_exceeded` | 是 |
| A-SEC-14 | 总大小超限 → protocol_error | `test_runtime_l3_artifact_security.py::TestArtifactLimits::test_total_size_exceeded` | 是 |
| A-SEC-15 | 类型拒绝（exe/dll/bat 等） | `test_runtime_l3_artifact_security.py::TestArtifactTypeSecurity::test_executable_rejected` | 是 |
| A-SEC-16 | 无扩展名文件 → 拒绝 | `test_runtime_l3_artifact_security.py::TestArtifactTypeSecurity::test_no_extension_rejected` | 是 |
| A-SEC-17 | 双扩展名（.pdf.exe）→ 拒绝 | `test_runtime_l3_artifact_security.py::TestArtifactTypeSecurity::test_double_extension_rejected` | 是 |
| A-SEC-18 | media_type hint 冲突 → protocol_error | `test_runtime_l3_artifact_security.py::TestArtifactTypeSecurity::test_media_type_hint_conflict_rejected` | 是 |
| A-SEC-19 | PDF 嗅探（%PDF- 开头） | `test_runtime_l3_artifact_security.py::TestArtifactTypeSecurity::test_pdf_content_sniffing` | 是 |
| A-SEC-20 | PNG 嗅探（magic bytes） | `test_runtime_l3_artifact_security.py::TestArtifactTypeSecurity::test_png_content_sniffing` | 是 |
| A-SEC-21 | JPEG 嗅探（SOI magic） | `test_runtime_l3_artifact_security.py::TestArtifactTypeSecurity::test_jpeg_content_sniffing` | 是 |
| A-SEC-22 | OOXML 嗅探 — docx（ZIP + [Content_Types].xml + word/） | `test_runtime_l3_artifact_security.py::TestArtifactTypeSecurity::test_docx_content_sniffing` | 是 |
| A-SEC-23 | OOXML 嗅探 — pptx（ZIP + [Content_Types].xml + ppt/） | `test_runtime_l3_artifact_security.py::TestArtifactTypeSecurity::test_pptx_content_sniffing` | 是 |
| A-SEC-24 | OOXML 嗅探 — xlsx（ZIP + [Content_Types].xml + xl/） | `test_runtime_l3_artifact_security.py::TestArtifactTypeSecurity::test_xlsx_content_sniffing` | 是 |
| A-SEC-25 | SVG 拒绝（不在 allowlist） | `test_runtime_l3_artifact_security.py::TestArtifactTypeSecurity::test_svg_rejected` | 是 |
| A-SEC-26 | TOCTOU: 声明后替换源文件 → 检测并拒绝 | `test_runtime_l3_artifact_security.py::TestArtifactTOCTOU::test_replace_after_declare_detected` | 是 |
| A-SEC-27 | TOCTOU: 声明后替换为 symlink → 检测并拒绝 | `test_runtime_l3_artifact_security.py::TestArtifactTOCTOU::test_replace_with_symlink_detected` | 是 |
| A-SEC-28 | 原子提交: 部分复制失败回滚 | `test_runtime_l3_artifact_security.py::TestArtifactTransaction::test_partial_copy_rollback` | 是 |
| A-SEC-29 | 原子提交: os.replace 全有或全无 | `test_runtime_l3_artifact_security.py::TestArtifactTransaction::test_atomic_commit_all_or_nothing` | 是 |
| A-SEC-30 | 提交前 cancel → 回滚，artifacts=() | `test_runtime_l3_artifact_security.py::TestArtifactTransaction::test_cancel_before_commit_rollback` | 是 |
| A-SEC-31 | 提交后 late cancel → 保持 succeeded + artifacts | `test_runtime_l3_artifact_security.py::TestArtifactTransaction::test_late_cancel_keeps_succeeded` | 是 |
| A-SEC-32 | 目标 artifact_root symlink → 拒绝 | `test_runtime_l3_artifact_security.py::TestArtifactTargetSecurity::test_target_root_symlink_rejected` | 是 |
| A-SEC-33 | skill 目录 symlink → 拒绝 | `test_runtime_l3_artifact_security.py::TestArtifactTargetSecurity::test_skill_dir_symlink_rejected` | 是 |
| A-SEC-34 | 目标文件 symlink 替换 → 拒绝 | `test_runtime_l3_artifact_security.py::TestArtifactTargetSecurity::test_target_file_symlink_rejected` | 是 |

**预计 L3 新增: 34 node**（含 P0 校正新增的目标根安全、内容嗅探、取消提交点、media_type hint 冲突等语义）

### 16.4 L3: ArtifactStore 安全消费 (Batch 3.2.1 / 3.2.2)

| 编号 | 语义 | 拟议 node ID | 真实 FS |
|------|------|-------------|---------|
| A-AS-01 | ArtifactStore.list_task 返回正确列表 | `tests/test_runtime_artifact_store.py::TestArtifactStore::test_list_task` | 是 |
| A-AS-02 | ArtifactStore.locate 验证 manifest | `tests/test_runtime_artifact_store.py::TestArtifactStore::test_locate_verifies_manifest` | 是 |
| A-AS-03 | ArtifactStore.export 安全复制 | `tests/test_runtime_artifact_store.py::TestArtifactStore::test_export_safe_copy` | 是 |
| A-AS-04 | ArtifactStore.export 路径逃逸拒绝 | `tests/test_runtime_artifact_store.py::TestArtifactStore::test_export_path_escape_rejected` | 是 |
| A-AS-05 | ArtifactStore.export symlink 目标替换拒绝 | `tests/test_runtime_artifact_store.py::TestArtifactStore::test_export_symlink_target_rejected` | 是 |
| A-AS-06 | ArtifactStore.delete_task 安全删除 | `tests/test_runtime_artifact_store.py::TestArtifactStore::test_delete_task_safe` | 是 |
| A-AS-07 | ArtifactStore.delete_task 路径逃逸拒绝 | `tests/test_runtime_artifact_store.py::TestArtifactStore::test_delete_task_escape_rejected` | 是 |
| A-AS-08 | ArtifactStore.export 覆盖确认 | `tests/test_runtime_artifact_store.py::TestArtifactStore::test_export_overwrite_confirm` | 是 |

**预计 ArtifactStore 新增: 8 node**

### 16.5 UI: Artifact 消费 (Batch 3.2.2)

| 编号 | 语义 | 拟议 node ID | 真实 FS |
|------|------|-------------|---------|
| A-UI-01 | response 有 artifact 时列表展示 | `test_runtime_ui_artifact.py::TestArtifactUI::test_artifact_list_displayed` | 是 |
| A-UI-02 | 无 artifact 时列表为空（不崩溃） | `test_runtime_ui_artifact.py::TestArtifactUI::test_no_artifact_empty_list` | 是 |
| A-UI-03 | 多个 artifact 正确排序 | `test_runtime_ui_artifact.py::TestArtifactUI::test_multiple_artifacts_order` | 是 |
| A-UI-04 | "在文件管理器中定位" 调用 ArtifactStore.locate | `test_runtime_ui_artifact.py::TestArtifactUI::test_locate_calls_artifact_store` | 是 |
| A-UI-05 | "另存为" 调用 ArtifactStore.export | `test_runtime_ui_artifact.py::TestArtifactUI::test_save_as_calls_artifact_store` | 是 |
| A-UI-06 | "另存为" 覆盖确认（UI 层） | `test_runtime_ui_artifact.py::TestArtifactUI::test_save_as_overwrite_confirm` | 是 |
| A-UI-07 | "删除" 调用 ArtifactStore.delete_task | `test_runtime_ui_artifact.py::TestArtifactUI::test_delete_calls_artifact_store` | 是 |
| A-UI-08 | 文件被外部删除后提示 | `test_runtime_ui_artifact.py::TestArtifactUI::test_missing_file_error` | 是 |
| A-UI-09 | 禁止自动执行验证 | `test_runtime_ui_artifact.py::TestArtifactUI::test_no_auto_execute` | 是 |
| A-UI-10 | Widget 销毁不崩溃（有 artifact 列表时） | `test_runtime_ui_artifact.py::TestArtifactUI::test_widget_destroy_with_artifacts` | 是 |

**预计 UI 新增: 10 node**

### 16.6 跨平台零 skip 策略

```text
Windows：
  收集并执行 junction/reparse/ADS 等 Windows 真实文件系统节点。
  不定义 POSIX 专项测试函数（FIFO/socket/symlink 特定语义）。

POSIX：
  收集并执行 FIFO/socket/symlink 等 POSIX 真实文件系统节点。
  不定义 Windows 专项测试函数（junction/ADS）。

不适用于当前平台的测试函数不定义、不收集；
不得定义后再 skip。
```

**审核包必须报告**：

```text
运行平台
实际收集的平台专项 node
未收集的其他平台节点（因为未定义，不是 skip）
至少存在一个当前平台真实特殊文件节点
```

真实 node 数以 `pytest --collect-only` 为准，不机械维持固定数量。

### 16.7 测试汇总

| 层级 | 测试文件 | 预计新增 node |
|------|---------|-------------|
| L1 Schema | `test_runtime_l1_models.py` | 28 |
| L2 Publish | `test_runtime_l2_artifact_publish.py` (新) | 15 |
| L3 Security | `test_runtime_l3_artifact_security.py` (新) | 34 |
| ArtifactStore | `test_runtime_artifact_store.py` (新) | 8 |
| UI | `test_runtime_ui_artifact.py` (新) | 10 |
| **新增合计** | | **约 95** |
| 强制回归 | 既有 Runtime 测试文件 + `test_skill_package.py` | 255 (253 Runtime + 2 installer symlink) |

**注意**: 以上为规划预估。真实数量以后续 `pytest --collect-only` 为准。平台专项节点数量因平台而异。

### 16.8 删除或改写的错误拟议测试

以下原先拟议的测试语义已校正或删除：

| 原拟议 | 问题 | 校正 |
|--------|------|------|
| healthcheck artifacts 始终为空 | 与既有基线冲突 | 改为: healthcheck 保持既有自动收集行为 |
| UI 自行验证 published_path | UI 不应直接验证路径 | 改为: UI 通过 ArtifactStore API 消费 |
| UI 自行复制 Artifact 文件 | UI 不应自行复制 | 改为: 调用 ArtifactStore.export() |
| 同一 task 先删除再覆盖 | 破坏幂等安全 | 改为: 指纹匹配幂等返回 / 指纹不同 fail closed |
| cancelled 状态携带已发布 artifacts | 取消合同禁止 | 改为: 提交前 cancel=空 / 提交后 late cancel=succeeded |
| declared_path 替代 relative_path | 破坏 healthcheck 兼容 | 改回: relative_path 保留 |
| published_path 在公开 RuntimeArtifact | 不应暴露绝对路径 | 改为: storage_relpath（相对路径） |
| artifact_id 16 字符 | UUID4 hex 是 32 字符 | 改为: 32 hex 字符 |

---

## 17. 回归策略

### 17.1 Batch 3.2.1 回归命令

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
  -q
```

### 17.2 Batch 3.2.2 回归命令

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

### 17.3 Batch 3.2.3 封板回归命令

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
  tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry \
  tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction \
  -q
```

### 17.4 回归门槛

```text
所有既存 253 项 Runtime 通过
所有新增 Artifact 测试通过
2 项 installer symlink 哨兵通过
零 skip / xfail / deselected / -k not
Pyright 零 error 零 warning (本批修改文件)
Compileall 通过
```

---

## 18. P0 / P1 / P2（P0 校正）

### P0

```text
P0-1: ArtifactRunContext 向后兼容 dict 子类
P0-2: 两阶段 Artifact 协议（ArtifactDeclaration vs RuntimeArtifact）
P0-3: healthcheck 兼容边界（保持既有自动收集行为）
P0-4: RuntimeArtifact Schema（relative_path 保留、storage_relpath 新增、published_path 移除、artifact_id=32 hex、sha256=str|None）
P0-5: 原子提交指纹幂等（不预创建目录、不删除覆盖、commit_fingerprint）
P0-6: 目标 Artifact 根安全（双重根验证、symlink/reparse 拒绝）
P0-7: ArtifactStore 主机侧消费 API
P0-8: 精确文件类型嗅探（移除 SVG、hint 冲突拒绝）
P0-9: 跨平台零 skip 测试策略
P0-10: 非法声明不可吞掉（fatal declaration error → protocol_error）
P0-11: 取消提交点（提交前回滚、提交后保持 succeeded）
P0-12: MAX_DECLARE_MANIFEST_BYTES = 128 KB
P0-13: display_name 默认规则（Path.stem）、metadata 聚合限制
```

### P1

```text
display_name 自动生成规则细化（多语言文件名处理）
metadata JSON Schema 深度校验细节
ArtifactStore 性能优化（批量导出、缓存）
```

### P2

```text
Artifact 自动过期清理
Artifact 搜索和过滤
Artifact 预览（内嵌查看器）
历史 artifact 管理 UI
Artifact 分享/导出向导
云同步
批量删除
SVG 支持（需安全渲染/净化器）
```

---

## 19. 规划包最终自检（P0 校正）

| # | 问题 | 答案 |
|---|------|------|
| 1 | ArtifactRunContext 如何保持 dict 兼容？ | `class ArtifactRunContext(dict[str, object])` — `isinstance(context, dict) == True`；`context["params"]` 不变；`json.dumps(context)` 只序列化 dict 内容 |
| 2 | Worker 中间声明字段是什么？ | `ArtifactDeclaration` 内部类型：declared_path, display_name, media_type_hint, kind, metadata, observed_size_bytes, observed_sha256, observed_device, observed_inode, observed_mtime_ns |
| 3 | 最终 RuntimeArtifact 何时产生？ | 仅在 Service 发布成功后；`SkillRuntimeResponse.artifacts` 只包含发布完成的 Artifact |
| 4 | 非法声明能否被技能吞掉？ | 不能。即使 `try/except` 捕获异常并返回合法业务 dict，Worker 仍检测 fatal flag → `status=protocol_error, result=None, artifacts=()` |
| 5 | healthcheck 现有行为是否保持？ | 是。保持既有自动收集 output 文件行为（relative_path, size_bytes, sha256=None）。不迁移到 declare_artifact |
| 6 | 最终目录是否在提交前存在？ | 否。先创建 `.commit_<uuid>` 临时目录，fsync 后 `os.replace()` 原子提交。禁止预创建最终目录 |
| 7 | 同一 task 重试是否会删除旧数据？ | 否。指纹匹配 → 幂等返回已有 Artifact；指纹不同 → fail closed；绝不先删除再覆盖 |
| 8 | 提交后 late cancel 的最终 status 是什么？ | `succeeded`（取消请求过晚，保留提交，返回已发布 artifacts） |
| 9 | 公开 Artifact 是否包含绝对路径？ | 否。`storage_relpath` 是相对路径；Service/ArtifactStore 内部解析，UI 不拼接 |
| 10 | 谁解析 storage_relpath？ | Service 和 ArtifactStore 内部解析为 `artifact_root + storage_relpath`；UI 不解析 |
| 11 | 谁执行导出和删除？ | `ArtifactStore.export()` 和 `ArtifactStore.delete_task()`；每个 API 内部负责全部安全验证 |
| 12 | Artifact 根自身被替换为 symlink 时如何拒绝？ | 每次发布、读取、导出、删除前 lstat 验证 artifact_root、skill 目录、task 目录、文件均非 symlink/reparse |
| 13 | 每种 allowlist 类型如何嗅探？ | PDF: %PDF- 开头；PNG: magic bytes；JPEG: SOI magic；OOXML: 有效 ZIP + 特定目录；JSON: UTF-8 + json 可解析；TXT/CSV: UTF-8 无 NUL；ZIP: 有效 ZIP |
| 14 | 平台专项测试如何实现 0 skipped？ | Windows 只定义 junction/reparse/ADS 测试；POSIX 只定义 FIFO/socket/symlink 测试；不定义不适用的测试函数；不 skip |
| 15 | 为何 SVG 被移除？ | SVG 具有脚本、外部资源和活动内容风险；当前无安全渲染或净化器；列为 P2 |
| 16 | artifact_declarations 对 UI 可见吗？ | 不可见。是 Worker-Service 内部 wire 字段；`SkillRuntimeResponse` 不暴露该字段 |
| 17 | commit_fingerprint 包含哪些字段？ | 声明顺序、源文件 SHA256、display_name、kind、media_type_hint、metadata（序列化后） |

---

## 20. 未实施声明

```text
Batch 3.2 规划 P0 定点校正已完成并提交外部审核。
未实施任何 Batch 3.2 代码或测试。
未开始 Batch 3.2.1。
未开始 Batch 3.3 Report bridge。
等待外部审核结论。
```

---

## 21. 规划包实物信息

| 属性 | 值 |
|------|-----|
| 绝对路径 | `D:\桌面文件\软件项目_qt6\docs\agents\batch-3.2-planning-package.md` |
| 仓库相对路径 | `docs/agents/batch-3.2-planning-package.md` |
| 校正前行数 | 1271 |
| 校正前行数 | 见 git diff --stat |
| 校正后大小 | 见 git diff --stat |
| 校正后 SHA256 | 见下方 Bash 输出 |

---

## 22. P0 校正逐项摘要

### P0-1: ArtifactRunContext 向后兼容 dict 子类

- 新增 `ArtifactRunContext(dict[str, object])` 类定义
- 保证 `isinstance(context, dict) == True`
- `context["params"]` 保持不变
- `json.dumps(context)` 只序列化 dict 数据
- `declare_artifact` 是方法而非 dict 键
- 新增兼容测试合同

### P0-2: 两阶段 Artifact 协议

- 新增 `ArtifactDeclaration` 内部类型（Worker 侧，9 个字段）
- 明确禁止字段：artifact_id, storage_relpath, published 绝对路径, skill_id, version, task_id, created_at
- 新增 `artifact_declarations` wire 字段（`result.json`）
- `runtime_protocol.py` 加入 Batch 3.2.1 允许修改
- `RuntimeArtifact` 仅在 Service 发布成功后创建
- `SkillRuntimeResponse.artifacts` 不含 `artifact_declarations`

### P0-3: Healthcheck 兼容边界

- 保留 healthcheck 既有自动收集 output 文件行为
- 不迁移到 declare_artifact
- 不改变 healthcheck 测试语义
- 删除"healthcheck artifacts 始终为空"的错误表述
- sha256 模型层保持 `str | None` 以兼容旧 healthcheck

### P0-4: Schema 校正

- `relative_path` 保留（不重命名为 declared_path）
- 新增 `storage_relpath`（主机生成，相对路径）
- 移除公开 `published_path`
- `artifact_id` = 32 hex chars（完整 uuid4().hex）
- 新增 `artifact_schema_version` = 1
- `sha256` 模型层 `str | None`，已发布 Run Artifact 强制 64 位 hex
- manifest 未知顶层字段严格拒绝

### P0-5: 原子提交校正

- 禁止预创建最终 task 目录
- 使用 `.commit_<uuid>` 临时目录 + `os.replace()`
- 新增 `commit_fingerprint`
- 指纹匹配 → 幂等返回，不删除不重写
- 指纹不同或 manifest 损坏 → fail closed
- 删除"先删除旧目录再覆盖"的错误策略

### P0-6: 目标根安全

- 新增双重根验证（源根 + 目标根）
- artifact_root、skill 目录、task 目录、文件均验证非 symlink/reparse
- 孤儿 `.commit_*` 清理触发点：ArtifactPublisher 初始化 + 每次 publish 前
- 创建目录时采用主机生成名称

### P0-7: ArtifactStore API

- 新增 `ArtifactStore` 类（list_task, locate, export, delete_task）
- UI 不得直接拼接路径、解析 storage_relpath、lstat、hash、复制、删除
- UI 只展示元数据 + 获取用户输入 + 调用 ArtifactStore
- 用户删除能力纳入 Batch 3.2.2 并有安全测试

### P0-8: 精确类型嗅探

- 技能 media_type 降级为 hint
- hint 缺失 → 主机推断；hint 一致 → 接受；hint 冲突 → protocol_error
- 新增每种 allowlist 类型的精确嗅探规则
- 从 allowlist 移除 `.svg`（安全风险，列为 P2）

### P0-9: 跨平台零 skip

- 不适用平台的测试不定义、不收集
- 不定义后再 skip
- Windows 专项：junction/reparse/ADS
- POSIX 专项：FIFO/socket/symlink
- 审核包必须报告平台、收集节点、未收集节点

### 统一其他校正

- `MAX_DECLARE_MANIFEST_BYTES` 调整为 128 KB
- `display_name` 默认规则固定为 `Path(relative_path).stem`
- metadata 聚合限制 100 KB
- 非法声明不可吞掉（fatal declaration error → protocol_error）
- 取消提交点明确（`os.replace` 成功为唯一提交点）
- `runtime_protocol.py` 加入 3.2.1 允许修改
- `runtime_artifacts.py` 新增 ArtifactStore
- 测试矩阵从 59 更新为约 95 node（含 P0 新增语义）

---

## 23. 最终声明

```text
Batch 3.2 规划 P0 定点校正已完成并提交外部审核。
未实施任何 Batch 3.2 代码或测试。
未开始 Batch 3.2.1。
未开始 Batch 3.3 Report bridge。
等待外部审核结论。
```
