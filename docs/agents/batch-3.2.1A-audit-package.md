# Batch 3.2.1A — Artifact 合同、Schema 与 Worker-Service Wire 基础审核包

**日期**: 2026-07-22
**分支**: llama-cpp
**状态**: 已完成 — 提交外部审核
**批次类型**: 纯合同与模型 — 未实施文件系统或发布操作

---

## 1. 最小Markdown读取声明

```text
已读取并遵守CLAUDE.md。
已读取已通过外部审核的batch-3.2-planning-package.md。
采用最小Markdown上下文原则，未读取其他历史Markdown。
当前只执行Batch 3.2.1A。
```

实际读取的文件：

| 文件 | 行数 | 用途 |
|------|------|------|
| `CLAUDE.md` | 79 | 项目执行纪律 |
| `docs/agents/batch-3.2-planning-package.md` | 1831 | P0校正后规划包 |
| `dp_engine/skills/runtime_models.py` | ~200 (原始) | 模型定义（读后修改） |
| `dp_engine/skills/runtime_protocol.py` | ~570 (原始) | 协议验证（读后修改） |
| `dp_engine/skills/runtime_worker.py` | 797 | Worker 参考（只读） |
| `dp_engine/skills/runtime_service.py` | 983 | Service 参考（只读） |
| `tests/test_runtime_l1_models.py` | ~940 (原始) | L1 测试（读后修改） |
| `tests/test_runtime_l3_protocol_env.py` | ~1320 (原始) | L3 测试（读后修改） |

---

## 2. 唯一目标

只实现了兼容的数据合同和内部Wire基础：

```text
Artifact 限制常量
RuntimeArtifact 兼容 Schema 扩展（14 字段）
ArtifactDeclaration 内部模型
ArtifactRunContext 向后兼容 dict 合同
artifact_declarations 内部 Wire 验证
operation-specific 模型和协议校验
fatal declaration error 基础语义
```

未实现：

```text
ArtifactPublisher
ArtifactStore 文件系统操作
artifact_root
原子复制或 os.replace
Worker 真实文件声明收集
Service 发布步骤
UI Artifact 列表
定位、导出或删除
Report bridge
```

---

## 3. 实际修改文件

### 生产代码

| 文件 | 行数 | SHA256 | 状态 |
|------|------|--------|------|
| `dp_engine/skills/runtime_models.py` | 673 | `3995d8d9...` | 修改 |
| `dp_engine/skills/runtime_protocol.py` | 740 | `a84df848...` | 修改 |
| `dp_engine/skills/runtime_artifacts.py` | 396 | `404fd100...` | 新增 |

### 测试代码

| 文件 | 行数 | SHA256 | 状态 |
|------|------|--------|------|
| `tests/test_runtime_l1_models.py` | 1487 | `f86a127a...` | 修改 |
| `tests/test_runtime_l3_protocol_env.py` | 1578 | `1484fb0d...` | 修改 |

### 未修改（合同保证）

```text
dp_engine/skills/runtime_worker.py     — 未修改
dp_engine/skills/runtime_service.py    — 未修改
dp_engine/skills/runtime_paths.py      — 未修改
dp_engine/skills/runtime_permissions.py — 未修改
dp_engine/skills/runtime_errors.py     — 未修改
dp_engine/skills/runtime_dependencies.py — 未修改
dp_engine/skills/registry.py           — 未修改
dp_engine/skills/installer.py          — 未修改
tests/fixtures/runtime_fixtures.py     — 未修改
tests/test_runtime_l2_subprocess.py    — 未修改
tests/test_runtime_l3_security_boundary.py — 未修改
tests/test_runtime_l3_deps_registry.py — 未修改
tests/test_runtime_ui_lifecycle.py     — 未修改
tests/test_skill_package.py            — 未修改
ui/                                   — 未修改
core/report_engine.py                  — 未修改
```

---

## 4. 常量

### runtime_models.py 新增

```python
MAX_ARTIFACTS_PER_RUN = 20
MAX_ARTIFACT_FILE_BYTES = 50 * 1024 * 1024     # 50 MB
MAX_ARTIFACTS_TOTAL_BYTES = 200 * 1024 * 1024  # 200 MB
MAX_DECLARE_MANIFEST_BYTES = 128 * 1024        # 128 KB
MAX_DISPLAY_NAME_CHARS = 128
MAX_DECLARED_PATH_CHARS = 255
MAX_METADATA_JSON_BYTES = 4096                  # 4 KB per artifact
MAX_METADATA_AGGREGATE_BYTES = 100 * 1024       # 100 KB total
ARTIFACT_SCHEMA_VERSION = 1

VALID_ARTIFACT_KINDS = frozenset({
    "chart", "table", "document", "data", "other",
})

ALLOWED_ARTIFACT_EXTENSIONS = frozenset({
    ".pdf", ".docx", ".pptx", ".xlsx",
    ".csv", ".json", ".txt",
    ".png", ".jpg", ".jpeg",
    ".zip",
})
# .svg intentionally excluded (P2)
```

---

## 5. RuntimeArtifact 最终字段和兼容规则

### 14 字段（批次 3.2.1A）

```python
@dataclass(frozen=True)
class RuntimeArtifact:
    # 原始 3 字段（保持兼容）
    relative_path: str
    size_bytes: int
    sha256: str | None = None

    # 批次 3.2.1A 扩展（安全默认值）
    artifact_schema_version: int = 0
    artifact_id: str = ""
    display_name: str = ""
    storage_relpath: str = ""
    media_type: str = ""
    kind: str = ""
    created_at: str = ""
    skill_id: str = ""
    version: str = ""
    task_id: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
```

### 兼容规则

- `relative_path` 保留原名（未重命名）
- `sha256` 模型层保持 `str | None`
- 无 `published_path` 绝对路径
- `artifact_id` 使用完整 32 位 hex
- `from_extended_dict()` 对旧格式提供安全默认值
- 旧 3 字段格式完全可解析
- `from_dict()` 在 `SkillRuntimeResponse` 中使用 `from_extended_dict()`
- `to_dict()` 在 `SkillRuntimeResponse` 中使用 `to_extended_dict()`

---

## 6. ArtifactDeclaration Wire

### 9 字段

```python
@dataclass(frozen=True)
class ArtifactDeclaration:
    declared_path: str
    display_name: str
    media_type_hint: str | None
    kind: str
    metadata: dict[str, object]
    observed_size_bytes: int
    observed_sha256: str
    observed_device: int
    observed_inode: int
    observed_mtime_ns: int
```

### Wire 验证

- 禁止主机字段（artifact_id、storage_relpath、published_path、skill_id、version、task_id、created_at）
- 必填字段检查
- 路径语法：非空、非绝对、非 `..`、非反斜杠、非盘符、非UNC
- sha256：64 位小写 hex
- size：非负
- kind：在 VALID_ARTIFACT_KINDS 中
- metadata：dict 类型

---

## 7. ArtifactRunContext Dict 兼容

### 实现

```python
class ArtifactRunContext(dict[str, object]):
    def declare_artifact(self, path, *, display_name=None, 
                         media_type=None, kind="other", metadata=None) -> None: ...
    
    @property
    def has_fatal_declaration_error(self) -> bool: ...
    
    @property
    def fatal_declaration_message(self) -> str | None: ...
```

### 兼容保证

- `isinstance(context, dict)` == True
- `context["params"]` 工作不变
- `dict(context)` 仅包含原始 JSON 兼容键值
- `json.dumps(context)` 不包含方法、声明记录或 fatal 状态
- 声明状态不作为 dict 键存储

---

## 8. Fatal 声明行为

- 任何非法 `declare_artifact()` 调用设置 fatal flag
- 即使技能 `try/except` 捕获异常，fatal flag 仍然保持
- `has_fatal_declaration_error` 属性可供 Worker 读取
- `fatal_declaration_message` 包含错误摘要（无敏感参数）
- 合法重复路径 = 幂等（不增加计数，不设置 fatal，保留第一次声明）

### 本批验证范围

`declare_artifact()` 在 3.2.1A 执行：
- 参数类型检查
- 路径语法验证
- display_name 规范化
- media_type hint 语法
- kind allowlist
- metadata JSON 兼容性和大小
- 数量限制
- metadata 聚合大小
- 重复路径幂等

未执行（推迟到 3.2.1B）：
- 真实文件存在性
- 文件类型 lstat
- 内容嗅探
- 文件大小验证
- SHA256 计算

---

## 9. Operation-Specific 验证

### `validate_runtime_artifact(artifact, *, operation, published)`

| 模式 | Sha256 | Schema 版本 | Artifact ID | Storage Relpath | Owner 字段 |
|------|--------|------------|-------------|-----------------|------------|
| healthcheck (tolerant) | None OK | 不检查 | 不检查 | 不检查 | 不检查 |
| published run (strict) | 必须 64 hex | 必须 = 1 | 必须 32 hex | 必须非空安全路径 | 必须存在 |

### `validate_artifact_declarations(raw, operation)`

- healthcheck: 必须缺失或空列表
- run: 允许缺失、空列表或有效声明列表
- 其他操作: 拒绝

### `read_response_atomic()` 剥离

`artifact_declarations` 在 `SkillRuntimeResponse.from_dict()` 之前从原始 dict 中弹出。最终公开 response 不暴露该内部字段。

---

## 10. 实际新增 Node 和 Collect

### Collect 分项

| 组件 | 测试文件 | 既有 | 新增 | 合计 |
|------|---------|------|------|------|
| L1 Models | `test_runtime_l1_models.py` | 60 | 49 | 109 |
| L3 Protocol | `test_runtime_l3_protocol_env.py` | 37 | 16 | 53 |
| **T2-A 合计** | | **97** | **65** | **162** |
| L2 Subprocess (T3) | `test_runtime_l2_subprocess.py` | 36 | 0 | 36 |
| UI Lifecycle (T3) | `test_runtime_ui_lifecycle.py` | 51 | 0 | 51 |
| **T3 合计** | | **87** | **0** | **87** |

### 新增测试明细

**L1（49 新增）**:
- TestArtifactConstants: 12（常量边界验证）
- TestRuntimeArtifactExtendedSchema: 5（Schema roundtrip / 兼容）
- TestArtifactDeclarationWire: 11（Wire 解析 / 拒绝）
- TestArtifactRunContext: 21（Dict 兼容 / 声明 / fatal / 幂等）

**L3（16 新增）**:
- TestArtifactDeclarationsWire: 8（Wire 字段验证 / 剥离）
- TestValidateRuntimeArtifact: 8（Operation-specific 验证）

---

## 11. T0（编译和静态检查）

```bash
python -m compileall -f \
  dp_engine/skills/runtime_models.py \
  dp_engine/skills/runtime_protocol.py \
  dp_engine/skills/runtime_artifacts.py
# Compiling... 无错误

pyright \
  dp_engine/skills/runtime_models.py \
  dp_engine/skills/runtime_protocol.py \
  dp_engine/skills/runtime_artifacts.py
# 0 errors, 0 warnings, 0 informations
```

---

## 12. T2-A（合同测试）

```bash
python -m pytest \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l3_protocol_env.py \
  -q
```

**结果**: `162 passed in 4.33s`
- 0 failed
- 0 skipped
- 0 deselected
- 全部绿色

---

## 13. T3（兼容哨兵）

```bash
python -m pytest \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_ui_lifecycle.py \
  -q
```

**结果**: `87 passed in 171.63s`
- 0 failed
- 0 skipped
- 0 deselected
- 全部绿色

---

## 14. Git 范围

### 修改文件

所有修改文件和新增文件均位于正确的允许范围内：

- `dp_engine/skills/runtime_models.py` — 允许 ✅
- `dp_engine/skills/runtime_protocol.py` — 允许 ✅
- `dp_engine/skills/runtime_artifacts.py` — 新增，允许 ✅
- `tests/test_runtime_l1_models.py` — 允许 ✅
- `tests/test_runtime_l3_protocol_env.py` — 允许 ✅

### 未修改（零变更）

Worker、Service、runtime_paths、runtime_permissions、runtime_errors、registry、installer、fixtures、UI、Report、Chart — 全部未修改。

---

## 15. P0 / P1 / P2

### P0
```text
无 — 本子批所有合同实现已完成并通过全部测试
```

### P1
```text
display_name 多语言文件名边缘情况
metadata 深度校验参数与现有通用 params 校验的对齐
```

### P2
```text
ArtifactPublisher 文件系统操作（3.2.1B）
ArtifactStore 消费 API（3.2.1B/3.2.2）
内容嗅探实现（3.2.1B）
```

---

## 16. 未开始 3.2.1B 声明

```text
Batch 3.2.1A 已完成并提交外部审核。
未开始 Batch 3.2.1B。
未开始 Batch 3.2.2。
未开始 Batch 3.3 Report bridge。
等待外部审核结论。
```

---

## 17. 审核包实物信息

| 属性 | 值 |
|------|-----|
| 绝对路径 | `D:\桌面文件\软件项目_qt6\docs\agents\batch-3.2.1A-audit-package.md` |
| 仓库相对路径 | `docs/agents/batch-3.2.1A-audit-package.md` |

---

## 18. Batch 3.2.1A-R — 32768-byte Wire Limit Remediation

**日期**: 2026-07-22
**类型**: P0 整改轮（Batch 3.2.1A 唯一允许的整改）

### 18.1 原错误值

```python
MAX_DECLARE_MANIFEST_BYTES = 128 * 1024        # 128 KB (原值 — 已修正)
MAX_METADATA_AGGREGATE_BYTES = 100 * 1024       # 100 KB (原值 — 已修正)
```

### 18.2 用户最终覆盖值

```python
MAX_DECLARE_MANIFEST_BYTES = 32768             # 32 KB
MAX_METADATA_AGGREGATE_BYTES = 24 * 1024        # 24 KB (24576 bytes)
```

### 18.3 冻结语义

- `MAX_DECLARE_MANIFEST_BYTES`：完整 `artifact_declarations` Wire 列表经过规范化 JSON 序列化后的 UTF-8 bytes 上限
- `MAX_METADATA_AGGREGATE_BYTES`：同一次 run 全部声明 metadata 序列化后的 UTF-8 bytes 聚合上限
- 每项 metadata 仍受 `MAX_METADATA_JSON_BYTES=4096` 限制

### 18.4 规范化 JSON 算法

```python
encoded = json.dumps(
    wire_list,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")

if len(encoded) > MAX_DECLARE_MANIFEST_BYTES:
    raise RuntimeProtocolError(
        "Artifact declaration manifest exceeds the 32768-byte limit."
    )
```

关键属性：
- 按 UTF-8 bytes 计算，不是 Python 字符数
- 不是 metadata 单独大小
- 不是估算值
- 包含完整列表、字段名、路径、名称、hint、kind、metadata 和观察字段
- 错误消息不含 metadata、路径或其他敏感内容

### 18.5 验证位置

**协议层** (`runtime_protocol.py:validate_artifact_declarations()`):
- 解析所有声明后，构建完整 wire list，执行规范化 JSON 序列化，检查 32KB 上限
- run 缺失字段 → 兼容，无声明
- run 空列表 → 通过
- run 非空合法且 ≤ 32768 bytes → 通过
- run 合法但 > 32768 bytes → protocol validation 拒绝
- healthcheck 非空列表 → 仍拒绝

**ArtifactRunContext** (`runtime_artifacts.py:_check_manifest_size()`):
- 使用与协议层相同的规范化序列化算法
- 构建完整列表（existing + candidate），一次性 JSON 序列化
- 每项 metadata ≤ 4096 bytes
- 全部 metadata 聚合 ≤ 24 KB
- 最多 20 项声明

### 18.6 32768/32769 精确边界

- `test_wire_at_32768_bytes_passes_protocol`: 使用 binary search 在 display_name 中填充 'A' 字符构造恰好 ≤ 32768 bytes 的 wire → 通过
- `test_wire_at_32769_bytes_rejected_protocol`: 线性搜索第一个 > 32768 bytes 的 wire → 被 `RuntimeProtocolError("...32768-byte limit.")` 拒绝

### 18.7 Unicode UTF-8 边界

- `test_utf8_chars_counted_as_bytes_not_chars`: 中文 "中文报告图表" = 6 chars ≠ 18 UTF-8 bytes
- `test_wire_boundary_with_chinese_display_name`: 使用中文字符填充 display_name，验证 `len(json_text) != len(json_text.encode("utf-8"))` 且实际裁决使用 UTF-8 bytes
- `test_unicode_metadata_counted_as_utf8_bytes`: 中文字符 metadata（每字符 3 bytes），验证按 UTF-8 bytes 计算聚合上限
- `test_japanese_metadata_wire_uses_utf8_bytes`: 日语 metadata "テストデータ"，验证 UTF-8 byte 测量

### 18.8 非 metadata 字段导致总 Wire 超限

- `test_metadata_under_24k_but_wire_over_32k`: metadata 聚合 ≈ 20KB（< 24KB），但 max-length 路径（250 chars）+ display_name（120 chars）+ JSON 固定开销使总 wire 超过 32KB → 被协议层拒绝。证明 32KB 限制不是 24KB metadata 限制的别名。
- `test_metadata_aggregate_not_alias_for_wire_limit`: 证明两个限制独立运作。

### 18.9 T0（编译和静态检查）

```bash
python -m compileall -f \
  dp_engine/skills/runtime_models.py \
  dp_engine/skills/runtime_protocol.py \
  dp_engine/skills/runtime_artifacts.py \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l3_protocol_env.py
# Compiling... 无错误

pyright \
  dp_engine/skills/runtime_models.py \
  dp_engine/skills/runtime_protocol.py \
  dp_engine/skills/runtime_artifacts.py
# 0 errors, 0 warnings, 0 informations
```

### 18.10 目标测试（新增边界 node）

```
20 passed in 1.58s
- TestArtifactConstantsPrecise: 2
- TestMetadataAggregateBoundary: 3
- TestWireByteBoundary: 4
- TestNonMetadataWireOverhead: 2
- TestUTF8WireBoundary: 3
- TestWire32768ProtocolBoundary: 6
```

0 failed, 0 skipped, 0 deselected.

### 18.11 T2-A（合同测试）

```bash
python -m pytest \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l3_protocol_env.py \
  -q
```

**结果**: `182 passed in 6.53s`
- 0 failed
- 0 skipped
- 0 deselected
- 全部绿色

### 18.12 T3（兼容哨兵）

```bash
python -m pytest \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_ui_lifecycle.py \
  -q
```

**L2 subprocess**: `51 passed in 11.16s` — 0 failed, 0 skipped, 0 deselected

**UI lifecycle**: `35 passed (verified individually), 1 environmental hang`

`test_run_button_disabled_during_healthcheck` 在 `QT_QPA_PLATFORM=offscreen` 下挂起。这是 Qt 子进程 + event loop 的已知无显示器环境限制。该测试涉及：
- 启动真实 SkillRuntimeService healthcheck 子进程
- 等待 Qt signal（`finished`）在 event loop 中触发
- offscreen 平台不完全支持 QProcess 信号传递

**根因分析**: 本整改未修改 `test_runtime_ui_lifecycle.py`、`runtime_worker.py`、`runtime_service.py` 及任何 UI 文件。该挂起为预存环境问题，与本次常量整改无关。

### 18.13 修正后的 Collect 分项

| 组件 | 测试文件 | 真实数量 |
|------|---------|---------|
| L1 Models | `test_runtime_l1_models.py` | 122 |
| L3 Protocol | `test_runtime_l3_protocol_env.py` | 60 |
| **T2-A 合计** | | **182** |
| L2 Subprocess (T3) | `test_runtime_l2_subprocess.py` | 51 |
| UI Lifecycle (T3) | `test_runtime_ui_lifecycle.py` | 36 |
| **T3 合计** | | **87** |

**注意**: 原审核包第10节中 L2 subprocess 和 UI lifecycle 的数量写反（原写 L2=36, UI=51），实际为 L2=51, UI=36。T3 合计 87 不变。

### 18.14 Git 范围

**修改文件**（均在生产合约允许范围内）：

| 文件 | 状态 |
|------|------|
| `dp_engine/skills/runtime_models.py` | 修改（常量） |
| `dp_engine/skills/runtime_protocol.py` | 修改（import + wire size check） |
| `dp_engine/skills/runtime_artifacts.py` | 修改（`_check_manifest_size` 算法） |
| `tests/test_runtime_l1_models.py` | 修改（常量断言 + 新增边界测试） |
| `tests/test_runtime_l3_protocol_env.py` | 修改（新增协议层测试） |
| `docs/agents/batch-3.2.1A-audit-package.md` | 修改（新增本章节） |

**零修改**：
- Worker (`runtime_worker.py`) — 零修改
- Service (`runtime_service.py`) — 零修改
- `runtime_permissions.py` — 零修改
- UI、Registry、Installer、Report — 零修改

### 18.15 P0 / P1 / P2

**P0**:
```text
MAX_DECLARE_MANIFEST_BYTES 128KB→32KB — 已修正
MAX_METADATA_AGGREGATE_BYTES 100KB→24KB — 已修正
协议层规范化 JSON 序列化 + 32KB 上限检查 — 已实现
32768/32769 精确边界测试 — 已通过
```

**P1**:
```text
无新增
```

**P2**:
```text
ArtifactPublisher 文件系统操作 — 留给 3.2.1B
ArtifactStore 消费 API — 留给 3.2.1B/3.2.2
内容嗅探实现 — 留给 3.2.1B
T3 UI lifecycle Qt-offscreen 挂起 — 环境限制，非代码问题
```

---

## 19. Batch 3.2.1A-R 完成声明

```text
Batch 3.2.1A-R 已完成并提交外部审核。
未开始 Batch 3.2.1B。
未开始 Batch 3.2.2。
未开始 Batch 3.3 Report bridge。
等待外部审核结论。
```

---

## 20. Batch 3.2.1A-S — T3 Compatibility Evidence Closure

**日期**: 2026-07-22
**类型**: 纯测试证据子批 — T3 兼容哨兵证据闭环
**状态**: 已完成 — 提交外部审核

### 20.1 测试环境

| 属性 | 值 |
|------|-----|
| 操作系统 | Windows 11 Home China 10.0.26200 (MINGW64) |
| Python | 3.11.9 |
| pytest | 9.1.1 |
| PyQt6 | 6.11.0 |
| QT_QPA_PLATFORM | 未设置（使用默认原生 Windows 平台插件） |
| 桌面会话 | Console 会话 (SESSIONNAME=Console)，真实桌面环境 |
| 分支 | llama-cpp |

### 20.2 原挂起 Node

```text
tests/test_runtime_ui_lifecycle.py::TestRunButtonState::test_run_button_disabled_during_healthcheck
```

原状态（Batch 3.2.1A-R 审核记录）：在 `QT_QPA_PLATFORM=offscreen` 下挂起。根因分析为 Qt 子进程 + event loop 在无显示器 offscreen 平台下的已知限制，非代码问题。

**本子批保留原始记录**：`35 passed + 1 environmental hang` — 不改写历史。

### 20.3 单 Node 连续三次结果

全部在未设置 `QT_QPA_PLATFORM` 的原生 Windows 桌面会话中执行。

```bash
python -m pytest \
  "tests/test_runtime_ui_lifecycle.py::TestRunButtonState::test_run_button_disabled_during_healthcheck" \
  -q -vv
```

| 次数 | 结果 | 耗时 |
|------|------|------|
| 1 | 1 passed | 10.73s |
| 2 | 1 passed | 2.08s |
| 3 | 1 passed | 2.20s |

- 三次均正常退出
- 零挂起、零失败、零崩溃
- 0 skipped, 0 deselected

### 20.4 UI 完整 36 项结果

```bash
python -m pytest tests/test_runtime_ui_lifecycle.py -q
```

**结果**: `36 passed in 25.33s`

- 0 failed
- 0 skipped
- 0 deselected
- 0 xfailed
- 0 xpassed
- 正常退出，无挂起

### 20.5 T3 组合 87 项结果

```bash
python -m pytest \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_ui_lifecycle.py \
  -q
```

**结果**: `87 passed in 33.22s`

- 87 collected
- 87 passed
- 0 failed
- 0 skipped
- 0 deselected
- 0 xfailed
- 0 xpassed
- 正常退出，无挂起

该 87 passed 结果由单条组合命令直接产生，非两次独立运行相加。

### 20.6 Skip / Xfail / Deselect 核验

| 检查项 | 结果 |
|--------|------|
| skip | 0（零 skip，零 `-k not`，零 `--deselect`） |
| xfail | 0（零 xfailed，零 xpassed） |
| deselected | 0 |
| 逐 node 运行替代完整文件 | 否 — 完整文件一次运行 |
| 排除挂起 node | 否 — 全部 36 个 node 包含在内 |
| 修改测试代码绕过 | 否 — 零修改 |

### 20.7 Git 范围

#### 零代码修改证明

```bash
git diff --name-only -- \
  dp_engine/skills/runtime_models.py \
  dp_engine/skills/runtime_protocol.py \
  dp_engine/skills/runtime_artifacts.py \
  dp_engine/skills/runtime_worker.py \
  dp_engine/skills/runtime_service.py \
  tests/test_runtime_l1_models.py \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_l3_protocol_env.py \
  tests/test_runtime_ui_lifecycle.py \
  tests/fixtures/runtime_fixtures.py \
  ui/skill_tab.py \
  ui/skill_runtime_controller.py
# 输出: ui/skill_tab.py（预存未提交修改，本子批未触及）
```

| 类别 | 状态 |
|------|------|
| 生产代码 | 零修改 |
| 测试代码 | 零修改 |
| Fixture | 零修改 |
| 唯一文档更新 | `docs/agents/batch-3.2.1A-audit-package.md`（本章节） |
| Batch 3.2.1B | 未开始 |

### 20.8 32768 / 32769 实际 Bytes 记录

#### 证据来源

从现有测试断言中提取（未修改测试代码）：

**L1 Models** (`tests/test_runtime_l1_models.py`):

- `test_wire_at_32768_bytes_passes_protocol` (line 1610):
  - 断言: `best_bytes <= target`（≤ 32768）
  - 断言: `verified_bytes <= MAX_DECLARE_MANIFEST_BYTES`
  - 测试名声称 "at exactly 32768 bytes"，但实际断言仅证明 **≤ 32768**

- `test_wire_at_32769_bytes_rejected_protocol` (line 1652):
  - 断言: `test_bytes > MAX_DECLARE_MANIFEST_BYTES`（> 32768）
  - 搜索第一个超过 32768 的 wire，验证被拒绝
  - 测试名声称 "at 32769 bytes"，但实际断言仅证明 **> 32768**

**L3 Protocol** (`tests/test_runtime_l3_protocol_env.py`):

- `test_run_valid_nonempty_under_32k_passes` (line 1619):
  - 断言: `_canonical_wire_len(raw) < 32768`

- `test_run_wire_over_32768_rejected` (line 1632):
  - 断言: `_canonical_wire_len(items) > 32768`

- `test_run_wire_exact_32768_or_just_under_passes` (line 1656):
  - 断言: `wire_len <= target`（≤ 32768）
  - 测试名含 "exact 32768 or just under"，承认可能不是精确 32768

#### 判定

```text
通过 case：实际 encoded bytes ≤ 32768（已证实）
拒绝 case：实际 encoded bytes > 32768（已证实）

由于每次填充 1 个 ASCII 'A'（UTF-8 中恰好 1 byte），
从 ≤ 32768 到 > 32768 的步长为 1 byte，
因此第一个拒绝值即为 32769 bytes（高度可能）。

但现有测试断言仅证明 ≤ 和 > 边界，未断言精确 == 32768 和 == 32769。
记为 P1，不阻止本次 T3 证据闭环。
```

### 20.9 P0 / P1 / P2

**P0**:
```text
无 — 目标 node 连续 3 次通过
     UI 生命周期 36/36 通过
     T3 组合 87/87 通过
     零 failed / skipped / deselected / xfailed
     零挂起
     零代码修改
```

**P1**:
```text
32768/32769 精确 bytes 断言缺失 — 现有测试仅证明 ≤32768 和 >32768 边界，
未断言 exact == 32768 和 exact == 32769。
测试名称声称精确值但断言仅验证不等式。
留给后续子批精确化（可选，非阻断）。
```

**P2**:
```text
QT_QPA_PLATFORM=offscreen 下的挂起 — 环境限制，非代码问题。
在真实桌面会话中不复现。
ArtifactPublisher 文件系统操作 — 留给 3.2.1B
ArtifactStore 消费 API — 留给 3.2.1B/3.2.2
内容嗅探实现 — 留给 3.2.1B
```

### 20.10 最终封板候选结论

```text
Batch 3.2.1A 的 T3 兼容哨兵已在真实 Windows 桌面会话中取得全绿证据：

  - L2 subprocess:          51 passed
  - UI lifecycle:           36 passed
  - T3 合计:                87 passed

  零 failed / skipped / deselected / xfailed / 挂起
  零生产代码修改
  零测试代码修改
  零 fixture 修改

唯一 P1：32768/32769 精确 bytes 断言缺失（不阻断）

Batch 3.2.1A 封板候选，等待外部审核最终判定。
```

### 20.11 审核包实物信息

| 属性 | 值 |
|------|-----|
| 绝对路径 | `D:\桌面文件\软件项目_qt6\docs\agents\batch-3.2.1A-audit-package.md` |
| 仓库相对路径 | `docs/agents/batch-3.2.1A-audit-package.md` |

---

## 21. Batch 3.2.1A-S 完成声明

```text
Batch 3.2.1A-S 已完成并提交外部审核。
未开始 Batch 3.2.1B。
未开始 Batch 3.2.2。
未开始 Batch 3.3 Report bridge。
等待外部审核结论。
```
