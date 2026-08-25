# Batch 3.3 — Report Bridge架构勘察、合同设计与冻结规划包

**日期**: 2026-07-23
**分支**: llama-cpp
**状态**: 纯规划批次 — Batch 3.3.0-F-R3 JSON非有限数字拒绝合同修正
**批次类型**: 纯规划 — 未实施任何代码

---

## 0. 历史摘要

### 0.1 Batch 3.3.0（原始勘察）

原 Batch 3.3.0 规划包完成 Report Bridge 架构勘察，识别出当前调用链中 Builder 直接写最终文件、无取消检查点、无 Artifact 通道等结构性问题。

### 0.2 Batch 3.3.0-R（外部审核整改）

原规划包提交外部审核后，审核方提出 7 项 P0 整改要求。3.3.0-R 为纯规划整改轮，零代码修改。

| P0 | 问题 | 整改结论 |
|----|------|---------|
| P0-1: export_to_bridge_dir 缺 skill_id/task_id | 规划新增 API 签名违反 Batch 3.2 owner 合同 | **废弃该 API**。Bridge 使用现有 `ArtifactStore.export(skill_id, task_id, artifact_id, target, *, overwrite=False)` |
| P0-2: 内部 Path 进入 UI 结果 | ReportBridgeResult 含 bridge_dir，ReportInputAsset 含 material_path | **拆分 UI 公共模型与内部 Lease 模型**。UI 结果零 Path |
| P0-3: 新 Bridge 清理 vs 生成中保护冲突 | "新请求清理上次目录"与"生成中允许新 Bridge"冲突 | **冻结 Lease 互斥合同** |
| P0-4: 报告输出非原子 | doc.save/prs.save 直接写最终文件 | **原子输出合同**：临时文件→fsync→cancel 检查→os.replace |
| P0-5: 数据模型伪代码缺陷 | dataclass 字段顺序、cancelled+assets 冲突 | **冻结不可变合同模型** |
| P0-6: 后台线程模型未冻结 | 未明确工作线程划分 | **冻结 Worker 线程合同**：UI 线程零 IO |
| P0-7: 未完整读取 ArtifactStore | 只读了文件头部 | **本轮完整读取**：runtime_artifacts.py (1447 行)全量 |

### 0.3 Batch 3.3.0-S（最终合同闭环）

3.3.0-R 关闭了 7 项 P0 的主体问题，但外部审核识别出仍存在 **5 项实施级冲突**。3.3.0-S 将这些合同同步写入正文。

### 0.4 Batch 3.3.0-F（历史 — 可实施合同定稿，已被 F-R → F-R2 取代）

外部审核对 3.3.0-S 进行终审，识别出 **6 项实施阻断**：

```text
1. 最终正文只列模型名称，没有完整字段和构造不变量；
2. "解析失败全量FAILED"与"解析失败返回warning"冲突；
3. claim_ready_lease返回完整Lease，破坏Controller唯一所有权；
4. Coordinator没有区分Bridge内部export与用户Artifact操作；
5. os.replace会覆盖已有final，与no-clobber合同冲突；
6. 素材如何进入报告内容、是否进入LLM、如何兼容现有project_dir尚未冻结。
```

本轮 3.3.0-F 只关闭以上问题，不再扩大功能范围。

**状态：已完成。** 全部 6 项实施阻断已在正文中冻结等价合同。规划包从 1699 行（3.3.0-S）更新至 1947 行（3.3.0-F 定稿）。

### 0.5 Batch 3.3.0-F-R（历史 — 已被 F-R2 取代）

> **历史，已废弃，无实施效力。** 以下内容仅保留用于审计追溯。
> 当前有效合同见 Section 0.6 Batch 3.3.0-F-R2。

外部审核对 3.3.0-F 定稿进行终审，识别出 **8 项语义一致性缺陷**：

```text
1. 全文无 "Batch 3.3.0-F-R" 章节 — 整改轮次不可追溯；
2. JSON 类型矩阵仍写 "TABLE_SOURCE 或 TEXT_SOURCE，由结构规则决定" — 角色应由用户选择的 role 决定，非系统自动推断；
3. RB-L2-06 仍要求 JSON 解析为 ParsedTable — 忽略 JSON 可为 TEXT_SOURCE role；
4. safe_error_code 仍包含 cancelled — 取消是状态 (CANCELLED)，不是错误码；
5. 全文仍写 13 项 safe_error_code — 移除 cancelled 后应为 12 项；
6. Lease.release 缺少 workspace 清理失败时 finally 释放 token 的合同 — 清理失败不得阻止 token 释放；
7. 测试矩阵仍写 7 组 62 项 — 实际为 8 组 94 项唯一 RB-* ID；
8. 元数据仍是 1947 行、65407 bytes 和旧 SHA256 — 与实际文件状态不符。
```

F-R 整改关闭了以上 8 项，但外部审核终审发现 F-R 仍未解决核心 JSON 合同歧义问题：JSON 仍保留双角色（TABLE_SOURCE + TEXT_SOURCE），RB-L2-06b 仍存在，类型矩阵仍允许 JSON→TABLE_SOURCE，Lease 清理日志仍允许 workspace_path。这些问题在 F-R2 中彻底关闭。

### 0.6 Batch 3.3.0-F-R2（本轮 — Report Bridge 最终合同归位）

外部审核对 3.3.0-F-R 进行终审，识别出 **7 项必须机械整改的合同归位缺陷**：

```text
1. 没有 "Batch 3.3.0-F-R2" 章节 — 整改轮次不可追溯；
2. RB-L2-06 仍是 JSON(TABLE_SOURCE)→ParsedTable — JSON 应为唯一 TEXT_SOURCE；
3. RB-L2-06b 仍存在 — JSON 双角色未消除；
4. 类型矩阵仍允许 JSON→TABLE_SOURCE — 终局合同必须唯一；
5. Lease 清理失败日志仍允许记录 workspace_path — 必须脱敏为受控错误码；
6. 测试矩阵仍为 8 组 94 项 — 删除 RB-L2-06b 后应为 8 组 93 项；
7. 文件内 SHA256 与实际上传文件不一致 — 编辑后重算。
```

本轮 3.3.0-F-R2 只关闭以上 7 项机械整改，不再扩大功能范围，不修改任何生产代码、测试代码、fixture、配置或 UI。

**F-R2 整改范围**：
- 唯一允许修改文件：`docs/agents/batch-3.3-planning-package.md`
- 零生产代码修改
- 零测试代码修改
- 零 fixture 修改
- 零配置修改
- 零 UI 修改
- 未开始 Batch 3.3.1A

### 0.7 Batch 3.3.0-F-R3（本轮 — JSON 非有限数字拒绝合同机械修正）

外部审核对 F-R2 进行终审，识别出 **1 项 API 合同错误**：

```text
当前规划包 JSON 解析合同写成：
  json.loads() 解析；
  拒绝 NaN、Infinity 和 -Infinity（allow_nan=False）

这是无效的 Python API 合同。
json.loads() 不接受 allow_nan 参数。
allow_nan=False 是 json.dumps() 的有效参数。

正确做法：使用 parse_constant 回调在解析阶段拒绝非有限数字。
```

本轮 3.3.0-F-R3 只修正此 API 合同错误及关联测试语义，不再扩大功能范围。

**F-R3 整改范围**：
- 唯一允许修改文件：`docs/agents/batch-3.3-planning-package.md`
- 零生产代码修改
- 零测试代码修改
- 零 fixture 修改
- 零配置修改
- 零 UI 修改
- 未开始 Batch 3.3.1A

---

## 0-F. Batch 3.3.0-F — 最终决策清单

1. **完整模型合同** — 所有 dataclass 字段、类型、构造不变量完整冻结
2. **公开状态与内部状态分离** — ReportBridgeStatus (UI) vs InternalBridgeState (Controller)
3. **claim_ready_generation 返回 GenerationInput，不返回 Lease** — Controller 保留唯一 Lease 所有权
4. **Coordinator 区分 BRIDGE_SESSION 与 USER_ARTIFACT_OPERATION** — Bridge 内部 export 使用 Session token
5. **解析失败 → 全量 FAILED** — 删除 warning 路径，统一 FAILED 语义
6. **原子 no-clobber 提交** — os.link 替代 os.replace，已有 final 绝不覆盖
7. **素材确定性附录 + 零 LLM 输入** — Bridge 内容不进入 generate_structured_report() 提示词
8. **project_dir 兼容** — bridge_workspace 独立参数，现有搜索顺序不变
9. **错误分类** — 12 项 safe_error_code，UI 只接收 safe_error_code + safe_error_message
10. **Lease 始终由 Controller 内部持有** — ReportWorker 不持有 Lease，不调用 release

---

## 1. 最小 Markdown 读取声明

```text
已读取并遵守 CLAUDE.md。
已读取当前 batch-3.3-planning-package.md（3.3.0-F-R 版 → F-R2 整改基线）。
已读取已封板的 batch-3.2.3-audit-package.md（1179 行）。
采用最小 Markdown 上下文原则，未读取其他历史 Markdown。
当前只执行 Batch 3.3.0-F-R3 JSON非有限数字拒绝合同机械修正。
未开始 Batch 3.3.1A。
```

### 1.1 规划 Markdown 读取

| 文件 | 行数 | 用途 |
|------|------|------|
| `CLAUDE.md` | 80 | 项目执行纪律 |
| `docs/agents/batch-3.3-planning-package.md` | 1699 | 3.3.0-S → 3.3.0-F 整改基线 |
| `docs/agents/batch-3.2.3-audit-package.md` | 1179 | Batch 3.2 最终封板候选、454 项回归基线 |

### 1.2 定点复核代码读取（3.3.0-F 新增）

| 文件 | 行数 | 复核目的 |
|------|------|---------|
| `main.py` (L1617-1926) | 310 | 报告生成调用链：`_build_and_save` → Builder → 图注入 → 页脚追加 |
| `dp_engine/report_builder/word_builder.py` (L649-693) | 45 | `build_word_report`：`doc.save(output_path)` 直接写最终文件 |
| `dp_engine/report_builder/ppt_builder.py` (L676-725) | 50 | `build_ppt_report`：`prs.save(output_path)` 直接写最终文件 |
| `dp_engine/report_builder/models.py` | 259 | WordTable / WordSection / WordReport / PPTSlide / PPTReport |
| `dp_engine/skills/runtime_artifacts.py` (L1194-1447) | 254 | `ArtifactStore.list_task` + `export` 完整签名和安全语义 |
| `ui/report_workbench.py` (L1-50) | 50 | 报告入口确认：纯信号驱动 Dumb Component，无 Bridge |

**合计：6 个文件，约 968 行定点复核。**

---

## 2. 定点复核代码与测试文件

| 文件 | 行数 | 复核目的 |
|------|------|---------|
| `main.py` (L165-650, L1572-1941) | ~850 | 完整报告生成调用链：`_build_and_save` → Builder → 图注入 → 页脚追加 |
| `dp_engine/report_builder/word_builder.py` (L649-693) | 45 | `build_word_report`：doc.save(output_path) 直接写最终文件 |
| `dp_engine/report_builder/ppt_builder.py` (L676-726) | 51 | `build_ppt_report`：prs.save(output_path) 直接写最终文件 |
| `dp_engine/skills/runtime_artifacts.py` (L1194-1447) | 254 | `ArtifactStore.export` 完整签名和安全语义 |

**合计：4 个文件，约 1200 行定点复核。**

### 2.1 真实调用链确认

```
main.py: _handle_full_report_generation() (L1617)
  → _build_and_save() (L1663, Worker 线程)
    ├─ [1] 图表产图 (L1706-1761)
    ├─ [2] AI 生成结构化数据 (L1764-1776)
    ├─ [2b] 数据汇总附表注入 (L1778-1809)
    ├─ [3] Builder 构建 (L1811-1837)
    │     Word: builder.build_word_report() → doc.save(output_path)  ← word_builder.py:676
    │     PPT:  builder.build_ppt_report()  → prs.save(output_path)  ← ppt_builder.py:725
    ├─ [4] 图注入 (L1840-1863) — 重新打开 output_path，修改后原地保存
    │     Word: _inject_figures_by_reference(output_path, ...)  → doc.save(docx_path)  ← main.py:314
    │     PPT:  _inject_figures_to_pptx(output_path, ...)       → prs.save(pptx_path)  ← main.py:649
    └─ [5] 资料纳入情况段 (L1866-1868) — 重新打开，追加后原地保存
          _append_inclusion_footer(output_path, ...) → doc.save(docx_path)  ← main.py:1610
```

**关键确认**：
1. Builder 的 `doc.save(output_path)` 和 `prs.save(output_path)` 直接写最终文件 — 非原子
2. 图注入在最终文件上原地修改 — 若 Builder 成功但注入失败，半成品文件已存在
3. 资料纳入情况段同样原地修改 — 注入成功但页脚失败，文件不完整
4. 全链路无临时文件、无 fsync、无 os.replace、无取消检查点
5. Report Engine 当前输入无 Artifact 通道

### 2.2 ArtifactStore.export 确认

```python
# runtime_artifacts.py:1289-1375
def export(
    self,
    skill_id: str,
    task_id: str,
    artifact_id: str,
    target: Path,
    *,
    overwrite: bool = False,
) -> None:
```

安全语义：manifest 验证 → owner 检查 → symlink/reparse 拒绝 → 原子导出 (mkstemp + fsync + os.replace + hash 验证)

Bridge 必须使用此 API，不得绕过。

---

## 3. Git 基线

```
git status --short:
  M  CLAUDE.md 等 32 tracked modified (既有工作区修改，非本批)
  D  dp_engine/github_skill_loader.py (既有)
  ?? docs/agents/ 等 ~45 untracked (含 Batch 3.2 全部新增)
```

### 本批修改范围

```text
零生产代码修改
零测试代码修改
零 fixture 修改
零配置修改
零 UI 修改
唯一允许修改文件: docs/agents/batch-3.3-planning-package.md
```

---

## 4. 冻结完整模型合同

以下所有模型定义具有实施合同效力。命名可按项目风格调整，但字段、类型和边界必须等价。

### 4.1 枚举

```python
from enum import StrEnum


class ReportAssetRole(StrEnum):
    """素材在报告中的角色 — 决定 Builder 如何处理。"""
    IMAGE = "image"
    TABLE_SOURCE = "table_source"
    TEXT_SOURCE = "text_source"


class ReportBridgeStatus(StrEnum):
    """公开状态 — UI 可消费。"""
    PREPARING = "preparing"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"


class InternalBridgeState(StrEnum):
    """内部状态 — Controller 私有。UI 不得依赖 GENERATING 或 RELEASED。"""
    IDLE = "idle"
    PREPARING = "preparing"
    READY = "ready"
    GENERATING = "generating"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    RELEASED = "released"


class OperationKind(StrEnum):
    """协调器操作类型。"""
    USER_ARTIFACT_OPERATION = "user_artifact_operation"   # 用户触发的定位/另存为/删除
    BRIDGE_SESSION = "bridge_session"                      # Bridge 准备 + Report 生成
```

**公开状态和内部状态必须分离。** UI 只能消费 `ReportBridgeStatus`。`GENERATING` 和 `RELEASED` 仅在 Controller 内部存在，不进入任何 Qt 信号。

### 4.2 选择模型

```python
from dataclasses import dataclass, field


@dataclass(frozen=True, kw_only=True)
class ReportArtifactSelection:
    """用户在 UI 中将一个 Artifact 选入 Bridge 的不可变记录。"""
    schema_version: int          # 必须等于 1
    skill_id: str                # 冻结格式：非空，无路径分隔符
    task_id: str                 # 冻结格式：32 位 hex 或 uuid4 hex
    artifact_id: str             # 冻结格式：32 位 hex 或 uuid4 hex
    role: ReportAssetRole
    order: int                   # 从 0 开始连续，决定报告中出现顺序
    display_name_hint: str = ""  # 仅供展示，不参与路径、类型、大小或安全裁决
```

**构造不变量**：
```text
schema_version 必须等于 1；
skill_id 非空，不含 "/" 或 "\"；
task_id 为 32 位 hex 字符串；
artifact_id 为 32 位 hex 字符串；
order 必须从 0 开始连续（验证时检查恰好为 0..N-1）；
display_name_hint 仅供展示，不用于任何安全或业务裁决。
```

### 4.3 请求模型

```python
@dataclass(frozen=True, kw_only=True)
class ReportBridgeRequest:
    """Controller 接收的完整 Bridge 请求。"""
    schema_version: int
    request_id: str                    # uuid4().hex，由 Controller 生成
    selections: tuple[ReportArtifactSelection, ...]
```

**构造不变量**：
```text
request_id 由 Controller 在收到请求时生成，为 uuid4().hex (32 位)；
generation 不由 UI 传入，由 Controller 内部单调递增管理；
selections 数量：1 ≤ len ≤ MAX_BRIDGE_ASSETS (12)；
所有 selection 必须属于同一个 skill_id 和 task_id；
artifact_id 不得重复；
order 必须恰好为 0..N-1（无缺失、无重复）。
```

### 4.4 公开摘要

```python
@dataclass(frozen=True, kw_only=True)
class ReportAssetSummary:
    """UI 列表和移除操作使用的最小素材摘要 — 零 Path。"""
    asset_key: str           # 主机生成的不透明标识，不含路径、hash 或 artifact_id 原文
    display_name: str        # 展示名称
    role: ReportAssetRole
    size_bytes: int
    order: int
```

**构造不变量**：
```text
asset_key 是主机生成的无路径不透明标识；
不得包含 artifact_id 原文、路径、hash、storage_relpath 或任何可反推路径的信息；
用于 Workbench 列表和移除操作，不用于安全裁决。
```

### 4.5 公开结果

```python
@dataclass(frozen=True, kw_only=True)
class ReportBridgePublicResult:
    """通过 Qt 信号发送给 UI 的唯一公开结果类型。"""
    request_id: str
    generation: int
    status: ReportBridgeStatus
    assets: tuple[ReportAssetSummary, ...]
    warnings: tuple[str, ...]
    safe_error_code: str | None
    safe_error_message: str | None
```

**构造不变量 — 按状态强制**：

| 状态 | assets | safe_error_code | safe_error_message |
|------|--------|-----------------|-------------------|
| PREPARING | () | None | None |
| READY | 非空 tuple | None | None |
| SUCCEEDED | 非空 tuple | None | None |
| FAILED | () | 非空 str | 非空 str |
| CANCELLED | () | None | None |

**绝对禁止出现在任何状态的字段**：
```text
Path
bridge_dir
material_path
storage_relpath
artifact_root
sha256
manifest
异常 repr
traceback
绝对路径字符串
```

### 4.6 内部素材

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dp_engine.skills.runtime_models import RuntimeArtifact


# 封闭的解析载体类型
ParsedTable = tuple[tuple[str, ...], ...]       # ((col1, col2, ...), (row1_val1, row1_val2, ...), ...)
ParsedPayload = None | ParsedTable | str         # IMAGE→None, TABLE_SOURCE→ParsedTable, TEXT_SOURCE→str


@dataclass(frozen=True, kw_only=True)
class PreparedReportAsset:
    """准备完成的单个素材 — 仅存在于 Controller 内部和 GenerationInput 中。"""
    authoritative_artifact: "RuntimeArtifact"   # ArtifactStore 返回的权威对象
    role: ReportAssetRole
    order: int
    managed_filename: str                       # 主机生成的文件名，仅在 bridge_workspace 内解析
    parsed_payload: ParsedPayload               # 类型封闭：IMAGE→None, TABLE_SOURCE→ParsedTable, TEXT_SOURCE→str
```

**parsed_payload 封闭类型冻结**：
```text
IMAGE         → None（图片不解码为像素数组）
TABLE_SOURCE  → tuple[tuple[str, ...], ...]（首行为表头，后续为数据行）
TEXT_SOURCE   → str（完整文本内容）
```

不得使用任意可变 `dict | list | object` 作为 parsed_payload 的最终冻结合同。

### 4.7 内部生成句柄

```python
from pathlib import Path


@dataclass(frozen=True, kw_only=True)
class ReportGenerationInput:
    """Controller 通过 claim_ready_generation 交接给 DataProcessorWindow 的不可变句柄。
    
    这不是 Lease。Controller 保留真正的 Lease 所有权。
    此对象不含 release 方法，不能改变 Controller 状态。
    """
    request_id: str
    generation: int
    workspace_path: Path
    assets: tuple[PreparedReportAsset, ...]
```

**合同**：
```text
只在 DataProcessorWindow 和 ReportWorker 内部传递；
不进入 Qt 公开信号（信号只发送 ReportBridgePublicResult）；
不存入 ReportWorkbenchWidget；
没有 release 方法；
不能改变 Controller 状态；
generation 由 Controller 单调递增分配，claim 时原子验证。
```

### 4.8 Lease（始终内部持有）

```text
ReportBridgeLease 始终由 ReportBridgeController 内部持有。

Lease 包含：
  - request_id
  - generation
  - owner skill_id / task_id
  - workspace_path
  - prepared assets (tuple[PreparedReportAsset, ...])
  - coordinator token (BRIDGE_SESSION)
  - released 标志（bool）

release() 只能由 Controller 内部的 discard_ready_generation / finish_generation / 
关闭路径调用。release() 执行（finally 等价语义）：

  1. 尝试安全删除 workspace（验证路径后再删除）；
  2. 若 workspace 清理失败（权限不足、进程占用、IO 错误等）：
     a. 仅记录受控错误码和非敏感状态（禁止记录 workspace_path、绝对路径、用户目录、
        应用数据目录、原始异常消息、repr(exception)、traceback、manifest、storage_relpath）；
     b. 将残留目录登记到有界孤儿清理队列（受 24h 时效和 100 上限约束）；
     c. **不因清理失败而阻止后续步骤**；
  3. 释放 Coordinator token — **必须置于 finally 等价路径中执行**，
     不受 workspace 清理成败影响；
  4. 设置 released = True — Lease 最终标记 released；
  5. Controller 状态转换到 RELEASED。

release() 必须幂等（重复调用不重复释放 token，不重复删除 workspace，
不重复登记孤儿目录）。
```

---

## 5. Coordinator 与内部 export 合同

### 5.1 新增文件

在 3.3.1A 允许新增：

```text
dp_engine/report_bridge/coordinator.py
```

### 5.2 冻结类

```python
class ArtifactOperationCoordinator:
    """应用级单例，按 (skill_id, task_id) 跟踪操作互斥。"""
    ...
```

### 5.3 Key 和操作类型

Key 必须是：

```text
(skill_id, task_id)
```

操作类型：

```python
class OperationKind(StrEnum):
    USER_ARTIFACT_OPERATION = "user_artifact_operation"   # 用户触发的定位/另存为/删除
    BRIDGE_SESSION = "bridge_session"                      # Bridge 准备 + Report 生成
```

### 5.4 互斥规则

```text
同一 owner (skill_id, task_id) 同时只能存在一个 USER_ARTIFACT_OPERATION
或一个 BRIDGE_SESSION。

不同 owner 可并发。
```

具体：
- BRIDGE_SESSION 期间 → 同 owner 的 USER_ARTIFACT_OPERATION 被拒绝
- USER_ARTIFACT_OPERATION 期间 → 同 owner 的 BRIDGE_SESSION 被拒绝
- 不同 (skill_id, task_id) 可并发各自的操作

### 5.5 Bridge 内部 export 合同（关键修正）

```text
BRIDGE_SESSION token 持有期间：

ReportBridgeService 调用 ArtifactStore.list_task / ArtifactStore.export
属于当前 Bridge Session 的内部授权步骤。

内部调用不再次获取 USER_ARTIFACT_OPERATION token；
不得因自身 Session token 而被拒绝。

Coordinator 检查逻辑：
  if operation == BRIDGE_SESSION:
      检查同 owner 是否已有 BRIDGE_SESSION 或 USER_ARTIFACT_OPERATION
  elif operation == USER_ARTIFACT_OPERATION:
      检查同 owner 是否已有 BRIDGE_SESSION 或 USER_ARTIFACT_OPERATION
  
  Bridge 内部 export 不经过 Coordinator 检查（直接放行）。
```

### 5.6 3.3.2 接入规则

```text
用户定位、另存为和删除开始前：
  必须获取 USER_ARTIFACT_OPERATION token；
  
  同 owner 存在 BRIDGE_SESSION 时 → 启动失败，返回 safe_error_code="operation_conflict"；
  
  操作终态（成功/失败/取消）→ finally 释放 token。

不同 owner 可并发。
```

### 5.7 协调器范围声明

明确协调器是应用级调用协调，不修改 ArtifactStore 本身，也不能声称阻止仓库外部的任意直接调用。

### 5.8 协调器 API

```python
token = coordinator.try_acquire(
    skill_id,
    task_id,
    operation=OperationKind.BRIDGE_SESSION,
)
```

要求：

```text
非阻塞获取；
获取失败返回明确结果（如 None），不在 UI 线程等待；
token 只能释放一次（幂等 release）；
异常和取消路径必须释放 token；
应用退出可以安全释放剩余 token；
不得使用全局无 key 布尔值。
```

---

## 6. 冻结私有交接接口（修正 — GenerationInput 非 Lease）

### 6.1 公共 Qt 信号约束

公共 Qt 信号只能发送：

```text
ReportBridgePublicResult
```

不得发送：

```text
ReportBridgeLease
ReportGenerationInput
Path
workspace_path
PreparedReportAsset
任何含绝对路径或内部状态的对象
```

### 6.2 Controller 内部 Lease 持有

`ReportBridgeController` 必须在内部保存唯一活动 Lease。

### 6.3 冻结私有应用层接口（修正后）

```python
def claim_ready_generation(
    self,
    request_id: str,
    generation: int,
) -> ReportGenerationInput | None:
    """
    原子验证 request_id 与 generation；
    要求当前状态 READY；
    状态 READY → GENERATING；
    同一请求只成功一次；
    Controller 仍保留真正 Lease；
    返回 None 表示 claim 失败（状态不对、generation 过期等）。
    """
```

```python
def discard_ready_generation(
    self,
    request_id: str,
    generation: int,
) -> bool:
    """
    用户放弃 READY 素材。
    Controller 内部释放 Lease（清理 workspace + 释放 token）。
    返回 True 表示成功。
    """
```

```python
def finish_generation(
    self,
    request_id: str,
    generation: int,
    *,
    status: ReportBridgeStatus,
) -> bool:
    """
    生成终态。
    验证当前状态为 GENERATING 且 generation 匹配。
    Controller 内部释放 Lease。
    
    status 必须是 SUCCEEDED、FAILED 或 CANCELLED。
    返回 True 表示成功。
    """
```

### 6.4 禁止（修正后）

```text
把 ReportBridgeLease 返回给 DataProcessorWindow；
把 ReportGenerationInput 塞入 Qt 公开信号；
让 ReportWorker 直接调用 lease.release()；
让 ReportWorker 改变 Controller 状态；
公开信号携带 ReportGenerationInput；
两个 ReportWorker 取得同一 GenerationInput；
旧 generation claim 新 GenerationInput。
```

### 6.5 冻结 Owner

```text
Bridge Worker 在准备阶段创建 Lease；
ReportBridgeController 接管 Lease；
ReportWorkbenchWidget 永远不持有 Lease；
DataProcessorWindow 只能通过 Controller 的私有 claim_ready_generation 接口取得 GenerationInput；
ReportWorker 在 GENERATING 期间持有 GenerationInput 引用（不是 Lease）；
终态通过 finish_generation 释放。
```

---

## 7. 状态机归属

### 7.1 单一 Owner

```text
ReportBridgeController 是单一状态机 owner。
```

### 7.2 内部状态转换

```text
IDLE → PREPARING
PREPARING → READY
PREPARING → FAILED
PREPARING → CANCELLED
READY → GENERATING           (claim_ready_generation)
READY → RELEASED              (discard_ready_generation 或超时)
GENERATING → SUCCEEDED        (finish_generation)
GENERATING → FAILED           (finish_generation)
GENERATING → CANCELLED        (finish_generation)
SUCCEEDED / FAILED / CANCELLED → RELEASED  (自动，release 后)
```

### 7.3 禁止转换

```text
终态 (SUCCEEDED/FAILED/CANCELLED/RELEASED) 不允许重新进入 READY 或 GENERATING。
同一 Lease 不能从 READY → GENERATING 两次。
RELEASED 不能再进入任何状态。
```

### 7.4 公开结果暴露

公开结果只允许暴露：

```text
PREPARING
READY
FAILED
CANCELLED
SUCCEEDED
```

`GENERATING` 和 `RELEASED` 仅存在于 Controller 内部。当状态为 GENERATING 时，对外暴露的 status 仍为 READY（或更新为上一次已知公开状态）。

---

## 8. Workspace 安全合同

### 8.1 冻结根目录

```text
<app-data>/skills/report_bridge/
```

由 `utils/app_paths.py` 提供基础路径。

### 8.2 受控函数

```python
def get_report_bridge_root() -> Path:
    """返回经验证的 report_bridge 根目录。"""

def create_report_bridge_workspace(request_id: str) -> Path:
    """为 request_id 创建 workspace 目录。"""

def safe_release_report_bridge_workspace(request_id: str, workspace_path: Path) -> None:
    """安全删除指定 workspace。"""

def cleanup_orphan_report_bridge_workspaces() -> None:
    """启动时清理孤儿 workspace。"""
```

### 8.3 必须

```text
逐级 lstat 验证 root 及父组件；
Windows 拒绝 reparse/junction；
拒绝 symlink；
request_id 必须为完整 32 位 uuid4 hex；
mkdir(exist_ok=False) — 不同 request 绝不复用目录；
创建后重新 lstat 验证；
创建失败必须 fail closed。
```

### 8.4 目标文件命名

目标文件名必须由主机生成，不得使用 `display_name_hint`：

```text
<两位 order>_<32 位 artifact_id>.<主机权威扩展名>
```

例如：

```text
00_4fa3...e91c.png
01_19bd...44aa.csv
```

不得把以下内容用于路径：

```text
display_name
display_name_hint
UI 提供的文件名
storage_relpath
relative_path
```

### 8.5 每次导出必须

```python
ArtifactStore.export(
    skill_id,
    task_id,
    artifact_id,
    managed_target,
    overwrite=False,
)
```

---

## 9. Workspace 清理合同

### 9.1 lease.release() 必须

```text
验证 workspace 位于 report_bridge_root 直接子目录；
验证目录名匹配 32 位 hex request_id；
拒绝跟随 symlink/reparse；
只删除当前 Lease 目录；
不得删除 root 或其他 request 目录。
```

### 9.2 孤儿清理

```text
只扫描 root 直接子目录；
只处理 32 位 hex 目录；
拒绝跟随 symlink/reparse；
只清理超过 24 小时的目录；
单次最多 100 个；
任何可疑对象 fail closed 并保留。
```

### 9.3 禁止

```text
不得依赖 __del__ 作为主要清理机制。
不得在 __del__ 中释放协调 token。
```

---

## 10. prepare_assets 全有或全无 + 解析失败唯一语义

### 10.1 解析失败唯一语义（关键修正）

**删除测试矩阵中的「解析失败返回 warning」。**

统一冻结：

```text
CSV/JSON/TXT 解析失败：
  → 整个 prepare_assets FAILED
  → PublicResult.assets = ()
  → safe_error_code = "asset_parse_failed"
  → 不产生 Lease
  → 清理整个 workspace
  → 释放 Coordinator token
```

`warnings` 只允许表达不影响素材完整性和数量的提示。

**首版冻结**：

```text
prepare_assets 成功时 warnings = ()。
```

不得用 warning 实现部分成功。

### 10.2 首版冻结

```text
所有用户选择的素材必须全部准备成功，
否则整个请求 FAILED。
```

### 10.3 流程

```text
验证 selection 集合
→ 获取协调 token
→ 创建 workspace
→ 按 order 逐项取得主机权威 RuntimeArtifact
→ 校验 role 与权威 media_type
→ 校验资源限制
→ ArtifactStore.export
→ 解析或验证
→ 全部成功后构建 Lease
```

### 10.4 任何一项发生以下情况

```text
Artifact 不存在
owner 不匹配
role 冲突
不支持类型
大小超限
导出失败
hash 验证失败
解析失败
取消
```

必须：

```text
清理整个 workspace；
释放 token；
不产生 Lease；
PublicResult.assets = ()；
不得保留前面已成功的部分素材。
```

### 10.5 首版不支持

```text
部分成功
跳过损坏素材后继续
解析失败降级为附件
READY 但缺少用户选中的部分素材
```

### 10.6 JSON 确定性规范化冻结合同（F-R2 新增 → F-R3 修正）

RB-L2-06 唯一冻结流程：

```text
1. UTF-8 或 UTF-8-SIG 严格解码；
2. 拒绝 NUL 字节；
3. 定义非有限数字拒绝函数（解析阶段入口）：
   def _reject_non_finite(value: str) -> NoReturn:
       raise ValueError("non-finite JSON number")
4. json.loads(decoded_text, parse_constant=_reject_non_finite)
   parse_constant 在解析阶段拦截：
   - NaN
   - Infinity
   - -Infinity
   统一拒绝为 ValueError。
   不得使用 json.loads(..., allow_nan=...) —
   json.loads() 不接受 allow_nan 参数。
   不得解析后字符串搜索 NaN；
   不得静默转换成 None；
   不得接受 Python float('nan') 或 float('inf')。
5. 校验深度 ≤ MAX_BRIDGE_JSON_DEPTH (8)；
6. 校验节点数 ≤ MAX_BRIDGE_JSON_NODES (5000)；
7. 校验单字符串 ≤ MAX_BRIDGE_JSON_STRING_CHARS (50,000)；
8. 校验总字符串字符数 ≤ MAX_BRIDGE_JSON_TOTAL_STRING_CHARS (500,000)；
9. 确定性规范化输出（序列化阶段 allow_nan=False 双重防御）：
   json.dumps(
       parsed,
       ensure_ascii=False,
       sort_keys=True,
       allow_nan=False,
       indent=2,
   )
10. 冻结渲染上限：
    MAX_BRIDGE_JSON_RENDER_CHARS = 50_000
11. 规范化字符串长度超过 MAX_BRIDGE_JSON_RENDER_CHARS：
    → safe_error_code="asset_too_large"
12. 成功：
    role = TEXT_SOURCE
    parsed_payload = str（规范化 JSON 字符串）
```

**合同含义**：
- `parse_constant` 负责**解析阶段**拒绝 — 第一时间拦截非有限数字；
- `json.dumps(..., allow_nan=False)` 负责**序列化阶段**防御 — 确保规范化输出不含非有限数字；
- 二者都必须存在，缺一不可。

**JSON 不再支持 TABLE_SOURCE 角色。** 用户选择 JSON + TABLE_SOURCE → safe_error_code="role_mismatch"。

---

## 11. 主机权威 Artifact 取得流程

### 11.1 Service 必须使用

```python
artifacts = artifact_store.list_task(skill_id, task_id)
```

### 11.2 然后

```text
按 artifact_id 精确匹配；
必须且只能匹配一个；
使用返回的 RuntimeArtifact 作为权威数据；
display_name_hint 只用于准备前 UI 提示，不参与裁决；
权威 media_type 决定 role 兼容性；
权威 size_bytes 决定资源限制；
随后 export 再次执行 ArtifactStore 安全验证。
```

### 11.3 不得信任 UI 提供的

```text
media_type
size_bytes
sha256
扩展名
路径
```

---

## 12. 资源限制补全

### 12.1 保留现有

| 常量 | 值 |
|------|-----|
| `MAX_BRIDGE_ASSETS` | 12 |
| `MAX_BRIDGE_IMAGE_BYTES` | 20 MiB |
| `MAX_BRIDGE_CSV_ROWS` | 5000 |
| `MAX_BRIDGE_CSV_COLS` | 30 |
| `MAX_BRIDGE_JSON_DEPTH` | 8 |
| `MAX_BRIDGE_JSON_NODES` | 5000 |
| `MAX_BRIDGE_TXT_CHARS` | 50000 |
| `MAX_BRIDGE_TOTAL_BYTES` | 100 MiB |

### 12.2 新增并冻结

| 常量 | 值 | 理由 |
|------|-----|------|
| `MAX_BRIDGE_PARSE_FILE_BYTES` | 8 MiB | CSV/JSON/TXT 解析前总字节上限 |
| `MAX_BRIDGE_IMAGE_PIXELS` | 40,000,000 | 单图片像素总数 (width × height) |
| `MAX_BRIDGE_IMAGE_DIMENSION` | 12,000 | 单边最大像素 |
| `MAX_BRIDGE_CSV_CELL_CHARS` | 8192 | 单 cell 字符串最大字符数 |
| `MAX_BRIDGE_CSV_TOTAL_CELLS` | 150,000 | CSV 总 cell 数 (rows × cols) |
| `MAX_BRIDGE_JSON_STRING_CHARS` | 50,000 | 单 JSON 字符串最大字符数 |
| `MAX_BRIDGE_JSON_TOTAL_STRING_CHARS` | 500,000 | JSON 全部字符串总字符数 |
| `MAX_BRIDGE_JSON_RENDER_CHARS` | 50,000 | JSON 规范化后字符串总字符数上限（F-R2 新增） |

### 12.3 各类型具体限制

**PNG/JPEG**：
```text
在 Worker 线程中安全读取头部和尺寸；
验证 width、height 均 > 0；
任一维度不得超过 MAX_BRIDGE_IMAGE_DIMENSION；
width × height 不得超过 MAX_BRIDGE_IMAGE_PIXELS；
Pillow decompression-bomb warning/error 必须转为拒绝；
不得完整解码后才检查像素限制。
```

**CSV**：
```text
文件字节数 ≤ MAX_BRIDGE_PARSE_FILE_BYTES；
UTF-8 或 UTF-8-SIG 严格解码；
拒绝 NUL；
限制 rows、cols、cell chars、total cells；
设置受控 csv.field_size_limit；
不得无界累积。
```

**JSON**：
```text
文件字节数 ≤ MAX_BRIDGE_PARSE_FILE_BYTES；
UTF-8 或 UTF-8-SIG 严格解码；
拒绝 NUL；
拒绝 NaN、Infinity 和 -Infinity（parse_constant 解析阶段拒绝）；
限制 depth、nodes、单字符串和总字符串字符数；
读取超过字节上限时先拒绝，不得完整载入。
```

**TXT**：
```text
文件字节数 ≤ MAX_BRIDGE_PARSE_FILE_BYTES；
UTF-8 或 UTF-8-SIG 严格解码；
拒绝 NUL；
字符数 ≤ MAX_BRIDGE_TXT_CHARS；
不得静默截断。
```

---

## 13. 原子 no-clobber 报告提交（关键修正）

### 13.1 当前问题确认

```
Builder:  doc.save(output_path)     ← word_builder.py:676  直接写最终文件
Builder:  prs.save(output_path)     ← ppt_builder.py:725   直接写最终文件
图注入:   doc.save(docx_path)       ← main.py:314           原地修改最终文件
图注入:   prs.save(pptx_path)       ← main.py:649           原地修改最终文件
页脚:     doc.save(docx_path)       ← main.py:1610          原地修改最终文件
```

**如果 Builder 成功但注入失败 → 半成品已存在于最终路径。**

### 13.2 原子 no-clobber 提交（修正后）

**不得继续使用会覆盖目标的 `os.replace(temp, final)` 作为唯一提交方案。**

#### 13.2.1 最终输出命名

```text
<原安全基础名>_<UTC时间>_<完整uuid4hex>.docx
或
<原安全基础名>_<UTC时间>_<完整uuid4hex>.pptx
```

最终文件名由主机生成，不允许用户指定已有目标路径。

#### 13.2.2 冻结完整事务

```text
计算 final_output（主机生成唯一名）
→ 在 final 同目录创建不可预测 temp_output (mkstemp)
→ Builder 写 temp_output (doc.save(temp) / prs.save(temp))
→ 对 temp_output 执行：
   _inject_figures_by_reference() 或 _inject_figures_to_pptx()
→ _append_inclusion_footer() 作用于 temp_output
→ Bridge 素材附录作用于 temp_output
→ 对所有其他既有后处理作用于 temp_output
→ 验证最终临时文件可打开、非空且格式合理
→ fsync temp_output
→ 最后一次 cancel 检查
→ os.link(temp_output, final_output)    ← 原子 no-clobber 唯一提交点
→ os.unlink(temp_output)                ← 清理 temp（失败仅记录告警）
```

#### 13.2.3 推荐冻结实现

```python
os.link(temp_output, final_output)   # final 存在时抛出 FileExistsError，不修改已有文件
os.unlink(temp_output)               # 清理 temp
```

#### 13.2.4 合同

```text
temp 和 final 位于同一文件系统；
final 存在时 os.link 必须失败且不修改已有文件；
link 成功是唯一提交点；
link 后取消视为晚到取消并保持 succeeded；
unlink temp 失败时 final 仍为有效提交，记录受控清理告警；
不支持原子 hardlink 提交的文件系统必须 fail closed，
不得降级为覆盖式 os.replace。
```

#### 13.2.5 冻结回滚

```text
图表注入失败 → 删除 temp，final 不变；
后处理失败 → 删除 temp，final 不变；
Builder 失败 → 删除 temp，final 不变；
提交前取消 → 删除 temp，final 不变；
os.link 后晚到取消 → 保持 succeeded（final 已提交）。
```

#### 13.2.6 不得

```text
os.link 后再修改最终 DOCX/PPTX；
直接 doc.save(final_output)；
直接 prs.save(final_output)；
注入失败但保留无图报告；
后处理失败但保留半成品；
os.replace 覆盖已有 final；
静默覆盖用户现有文件。
```

#### 13.2.7 Builder 取消检查点

Builder 必须在以下节点检查取消标志：
- 每节开始前
- 每页/每段落后
- 图注入前
- 页脚追加前
- Bridge 素材附录前
- os.link 前（最后一次检查）

---

## 14. 首版素材进入报告的确定性合同（新增）

### 14.1 零 LLM 输入原则

**首版不得把 CSV、JSON、TXT 或图片描述直接加入 LLM 提示词。**

冻结：

```text
Report Engine 现有 AI 生成输入和提示词保持不变；
Bridge 内容不进入 generate_structured_report() 提示词；
避免不可信 Artifact 内容形成提示注入。
```

### 14.2 确定性附录

素材由主机确定性附加到结构化报告末尾。

#### Word

在现有报告末尾增加固定章节：

```text
"技能输出素材"
```

按 `order` 顺序：

```text
IMAGE：
  - 插入图片（使用 PreparedReportAsset.managed_filename）
  - 添加安全的 display_name 作为图片标题
  - 不将图片 base64 编码写入 XML

TABLE_SOURCE：
  - 使用 python-docx 插入 WordTable
  - 首行为表头（加粗）
  - 不得超过冻结行列限制（MAX_BRIDGE_CSV_ROWS × MAX_BRIDGE_CSV_COLS）
  - 超出限制在 prepare_assets 阶段已拒绝

TEXT_SOURCE：
  - 插入标题（display_name）和文本段落
  - 超出 MAX_BRIDGE_TXT_CHARS 在 prepare_assets 阶段已拒绝
```

#### PPT

在现有 PPT 末尾增加素材页：

```text
IMAGE：
  - 每个图片生成一个或多个受控素材页
  - 图片适配幻灯片尺寸（保留宽高比，居中）
  - 添加安全的 display_name 作为图片标题

TEXT_SOURCE：
  - 按字符和 bullet 限制生成受控文本页
  - 每页不超过 8 个 bullet，每个 bullet 不超过 200 字符
  - 超出部分自动分页

TABLE_SOURCE：
  - 在进入 Builder 前明确拒绝
  - safe_error_code = "role_mismatch"
  - 不在 PPT 中静默丢弃或自动截图
```

### 14.3 禁止

```text
静默丢弃素材；
将 Artifact 内容加入 LLM prompt；
从 Artifact 文本解释新的系统指令；
自动生成截图；
把巨型文本全部塞入单页；
把 CSV 表格截图后嵌入 PPT。
```

---

## 15. 保持现有 project_dir 兼容（新增）

### 15.1 现有语义不变

现有 `project_dir` 继续服务于原有图表、模板和项目素材，不得被 Bridge workspace 替换。

### 15.2 加法接口

3.3.1B 采用加法接口：

```python
# Word
build_word_report(
    ...,
    project_dir=existing_project_dir,            # 现有语义不变
    bridge_workspace=optional_bridge_workspace,   # 新增独立参数
    bridge_assets=prepared_assets,                # 新增独立参数
)

# PPT
build_ppt_report(
    ...,
    project_dir=existing_project_dir,
    bridge_workspace=optional_bridge_workspace,
    bridge_assets=prepared_assets,
)
```

### 15.3 冻结

```text
project_dir 语义不变 — 服务于原有图表、模板和项目素材；
bridge_workspace 是独立内部参数 — 不替换 project_dir；
Bridge managed_filename 仅在 bridge_workspace 内解析；
现有图片和图表搜索顺序不变；
Bridge 图片不得通过普通 [INSERT_IMAGE] 模糊搜索取得；
优先通过明确的 PreparedReportAsset 引用定位 Bridge 素材，
而不是把两个目录混成无序搜索列表。
```

---

## 16. Word 与 PPT 角色能力

### 16.1 冻结

**Word**：
```text
IMAGE → 图片 + 安全标题
TABLE_SOURCE → WordTable（首行表头加粗）
TEXT_SOURCE → 标题 + 文本段落
```

**PPT**：
```text
IMAGE → 受控素材页（适配尺寸，居中）
TEXT_SOURCE → bullet 文本页（每页 ≤ 8 bullet，每个 ≤ 200 字符，自动分页）
TABLE_SOURCE → 首版明确拒绝（safe_error_code="role_mismatch"）
```

### 16.2 不得

因为 PPT 没有表格能力而：
```text
静默丢弃 TABLE_SOURCE；
自动截图 CSV；
把完整巨型表格塞入 speaker notes。
```

### 16.3 UI 强制

UI 在选择 PPT 报告类型时必须禁用或拒绝 TABLE_SOURCE；Service 仍以主机权威 media_type 验证基础 role 合法性。

---

## 17. 错误分类（新增）

### 17.1 安全错误码

至少冻结以下安全码：

| safe_error_code | 含义 |
|----------------|------|
| `invalid_request` | 请求模型验证失败（schema_version、selection 集合不合法等） |
| `owner_mismatch` | selection 中的 skill_id/task_id 与 Artifact 实际 owner 不匹配 |
| `artifact_not_found` | artifact_id 在 list_task 结果中不存在 |
| `artifact_integrity_failed` | export 后 hash 验证失败 |
| `unsupported_media_type` | Artifact 类型不在 Bridge 支持范围内 |
| `role_mismatch` | 用户选择 role 与主机权威 media_type 不兼容（含 PPT TABLE_SOURCE） |
| `asset_too_large` | 超过资源限制（像素、行数、字符数等） |
| `asset_parse_failed` | CSV/JSON/TXT 解析失败（唯一解析失败语义） |
| `workspace_security_failed` | workspace 创建或验证安全失败 |
| `workspace_io_failed` | workspace 创建 IO 错误 |
| `operation_conflict` | 同 owner 已有操作正在进行 |
| `internal_failure` | 其他内部错误 |

### 17.2 UI 接收合同

UI 只接收：

```text
safe_error_code: str | None
safe_error_message: str | None
```

不得接收：

```text
原始异常文本
traceback
绝对路径
内部状态描述
```

---

## 18. 修订子批次

### Batch 3.3.1A — 纯合同、Service、Lease、Coordinator

允许新增：

```text
dp_engine/report_bridge/__init__.py
dp_engine/report_bridge/models.py
dp_engine/report_bridge/service.py
dp_engine/report_bridge/workspace.py
dp_engine/report_bridge/parsing.py
dp_engine/report_bridge/coordinator.py
ui/report_bridge_controller.py

tests/test_report_bridge_models.py
tests/test_report_bridge_service.py
tests/test_report_bridge_security.py
tests/test_report_bridge_controller.py
tests/test_report_bridge_coordinator.py
```

本批必须完成：

```text
全部精确模型（含完整字段和构造不变量）：
  ReportAssetRole, ReportBridgeStatus, InternalBridgeState, OperationKind,
  ReportArtifactSelection, ReportBridgeRequest, ReportAssetSummary,
  ReportBridgePublicResult, PreparedReportAsset, ParsedPayload,
  ReportGenerationInput, ReportBridgeLease

状态机（ReportBridgeController 为唯一 owner，含内部/公开状态分离）

Workspace（创建/验证/Lease/清理/孤儿清理）

Coordinator（try_acquire/release, 按 (skill_id, task_id) 跟踪,
          BRIDGE_SESSION 与 USER_ARTIFACT_OPERATION 互斥,
          Bridge 内部 export 不重复获取 USER_ARTIFACT_OPERATION）

Service（prepare_assets 全有或全无, ArtifactStore.export 调用,
         资源限制检查, 主机权威 Artifact 获取,
         解析失败 → FAILED，不产生 warning 部分成功）

受限解析（CSV/JSON/TXT/Pillow 头部）

后台 Controller（QThread/Worker, generation 跟踪, 取消令牌,
               安全的 Qt 信号, 关闭和线程生命周期）

私有 claim_ready_generation / discard_ready_generation / finish_generation 接口
（claim 返回 GenerationInput，不返回 Lease）

错误分类（12 项 safe_error_code）
```

默认禁止修改任何 Batch 3.2 生产文件、Report Engine、Builder、main.py 和现有 UI。

### Batch 3.3.1B — Builder 适配、确定性附录与原子输出

允许新增：

```text
dp_engine/report_bridge/adapters.py
```

允许修改：

```text
dp_engine/report_builder/word_builder.py  — bridge_workspace + bridge_assets 参数、素材附录
dp_engine/report_builder/ppt_builder.py   — 同，PPT TABLE_SOURCE 拒绝
main.py                                   — _build_and_save 原子 no-clobber 输出适配
```

本批必须完成：

```text
确定性 Word/PPT 素材附录适配（"技能输出素材"章节/素材页）
Bridge 内容不进入 LLM prompt（Report Engine 现有提示词不变）
project_dir 保持兼容（bridge_workspace 独立参数）
Bridge 图片不得通过 [INSERT_IMAGE] 模糊搜索取得
PPT TABLE_SOURCE 在 Builder 前明确拒绝
Builder 取消检查点（每节/每页/图注入/附录/os.link 前）
完整 temp 输出 (mkstemp)
所有图表注入和后处理作用于 temp
Bridge 素材附录作用于 temp
fsync temp
os.link 唯一 no-clobber 提交（替代 os.replace）
已有 final 绝不覆盖
外部竞态创建 final → fail closed
失败/取消回滚（删除 temp，final 不变）
link 后晚到取消保持 succeeded
temp 清理失败不损坏 final
```

除非实际审计证明需要，否则不修改 `core/report_engine.py` 的 LLM 提示构造。

### Batch 3.3.2 — UI 和应用级协调

只负责：

```text
skill_tab 显式选择（Bridge 素材多选 UI）
report_workbench 无路径摘要（ReportAssetSummary 列表）
main.py 连接（Controller ↔ Window ↔ Workbench）
将既有 Artifact 操作（定位/另存为/删除）接入 3.3.1A 已实现的 Coordinator
  - 操作前获取 USER_ARTIFACT_OPERATION token
  - 被 BRIDGE_SESSION 阻止时 safe_error_code="operation_conflict"
READY 替换确认对话框
取消按钮
生命周期管理
```

不得首次实现 Coordinator 或 Lease 状态机。

### Batch 3.3.3 — 完整回归封板

纯审计与测试，不增加功能。

---

## 19. 测试矩阵（补强）

### 19.1 L1: 模型合同

| 编号 | 语义 |
|------|------|
| RB-L1-01 | 所有 selection 同 owner 验证 (skill_id + task_id) |
| RB-L1-02 | 混合 skill_id 拒绝 |
| RB-L1-03 | 混合 task_id 拒绝 |
| RB-L1-04 | 重复 artifact_id 拒绝 |
| RB-L1-05 | role/media_type 冲突拒绝 |
| RB-L1-06 | UI 伪造 media_type/size/hash 不影响主机裁决 |
| RB-L1-07 | ReportBridgePublicResult 零 Path 字段 |
| RB-L1-08 | ReportBridgePublicResult 零 bridge_dir/material_path/storage_relpath |
| RB-L1-09 | cancelled 状态不能携带 assets (assets=()) |
| RB-L1-10 | failed 状态 safe_error_code 非空 + assets=() |
| RB-L1-11 | ReportArtifactSelection 字段验证 (schema_version/skill_id/task_id/artifact_id) |
| RB-L1-12 | role 仅允许 IMAGE/TABLE_SOURCE/TEXT_SOURCE |
| RB-L1-13 | schema_version 严格验证（必须等于 1） |
| RB-L1-14 | order 必须从 0 连续（0..N-1） |
| RB-L1-15 | selection 数量 1 ≤ N ≤ MAX_BRIDGE_ASSETS |
| RB-L1-16 | READY 状态 assets 非空 + safe_error_code=None |
| RB-L1-17 | SUCCEEDED 状态 assets 非空 + safe_error_code=None |
| RB-L1-18 | PREPARING 状态 assets=() + safe_error_code=None |
| RB-L1-19 | parsed_payload 封闭类型：IMAGE→None, TABLE_SOURCE→tuple, TEXT_SOURCE→str |
| RB-L1-20 | ReportGenerationInput 不含 release 方法 |

### 19.2 L2: Service 集成

| 编号 | 语义 |
|------|------|
| RB-L2-01 | ArtifactStore.export 调用携带完整 owner |
| RB-L2-02 | overwrite 固定 False |
| RB-L2-03 | prepare_assets 单 PNG 成功 |
| RB-L2-04 | prepare_assets 多素材顺序保持 |
| RB-L2-05 | prepare_assets CSV 解析为 ParsedTable |
| RB-L2-06 | prepare_assets JSON 严格解析并确定性规范化为 TEXT_SOURCE 字符串（含 parse_constant 拒绝 NaN/Infinity/-Infinity；含 json.dumps allow_nan=False 双重防御） |
| RB-L2-07 | prepare_assets TXT 读取为 str |
| RB-L2-08 | prepare_assets 不支持类型拒绝 |
| RB-L2-09 | prepare_assets 数量超限拒绝 |
| RB-L2-10 | 新请求不能清理 GENERATING Lease |
| RB-L2-11 | READY Lease 显式替换确认 |
| RB-L2-12 | CSV/JSON 解析失败 → 全量 FAILED（非 warning 降级） |
| RB-L2-13 | 多素材第 N 个失败全量回滚 (assets=()) |
| RB-L2-14 | 解析失败不产生部分 READY |
| RB-L2-15 | prepare_assets 成功时 warnings=() |

### 19.3 L3: 安全边界

| 编号 | 语义 |
|------|------|
| RB-SEC-01 | bridge workspace 非 symlink/reparse |
| RB-SEC-02 | 源 artifact 被替换→hash 不匹配→export 拒绝 |
| RB-SEC-03 | Worker 线程执行复制和解析 |
| RB-SEC-04 | UI 线程不执行解析 |
| RB-SEC-05 | 路径遍历拒绝（文件名含 ../） |
| RB-SEC-06 | 绝对路径不泄露到 UI |
| RB-SEC-07 | storage_relpath 不泄露到 UI |
| RB-SEC-08 | bridge_dir 不泄露到 UI |
| RB-SEC-09 | workspace root symlink/reparse 拒绝 |
| RB-SEC-10 | request 目录 reparse 拒绝 |
| RB-SEC-11 | 主机生成文件名不使用 display_name |
| RB-SEC-12 | 孤儿清理仅直接子目录且有界 |

### 19.4 原子 no-clobber 输出

| 编号 | 语义 |
|------|------|
| RB-ATOM-01 | 报告写临时文件 (mkstemp) |
| RB-ATOM-02 | Builder 失败不产生最终文件 |
| RB-ATOM-03 | 提交前取消不产生最终文件 |
| RB-ATOM-04 | os.link 后晚到取消保持 succeeded |
| RB-ATOM-05 | 临时文件不可预测命名 |
| RB-ATOM-06 | 图表注入作用于 temp 文件 |
| RB-ATOM-07 | 图表注入失败不产生 final |
| RB-ATOM-08 | Builder 成功但后处理失败回滚（删除 temp） |
| RB-ATOM-09 | 已有 final 绝不被覆盖（os.link → FileExistsError） |
| RB-ATOM-10 | 后处理全部发生在 temp 路径 |
| RB-ATOM-11 | 提交前外部创建 final → os.link 失败 → fail closed |
| RB-ATOM-12 | os.link 提交后 final 完整可打开 |
| RB-ATOM-13 | temp unlink 失败不损坏 final（final 仍为有效提交） |
| RB-ATOM-14 | Bridge 素材附录作用于 temp |

### 19.5 Coordinator

| 编号 | 语义 |
|------|------|
| RB-COORD-01 | Coordinator 同 owner BRIDGE_SESSION 互斥 |
| RB-COORD-02 | Coordinator 不同 owner 可并发 |
| RB-COORD-03 | token 重复 release 幂等 |
| RB-COORD-04 | 准备失败释放 BRIDGE_SESSION token |
| RB-COORD-05 | READY Lease 持续持有 BRIDGE_SESSION token |
| RB-COORD-06 | GENERATING Lease 持续持有 BRIDGE_SESSION token |
| RB-COORD-07 | release 后同 owner Artifact 操作恢复 |
| RB-COORD-08 | Bridge 内部 export 不重复获取 USER_ARTIFACT_OPERATION token |
| RB-COORD-09 | USER_ARTIFACT_OPERATION 被 BRIDGE_SESSION 阻止 |
| RB-COORD-10 | BRIDGE_SESSION 被 USER_ARTIFACT_OPERATION 阻止 |

### 19.6 Lease 交接

| 编号 | 语义 |
|------|------|
| RB-LEASE-01 | 公共信号不含 Lease 或 Path |
| RB-LEASE-02 | claim_ready_generation 返回 GenerationInput（非 Lease） |
| RB-LEASE-03 | claim_ready_generation 同一请求仅成功一次 |
| RB-LEASE-04 | 旧 generation 不能 claim |
| RB-LEASE-05 | discard_ready_generation 释放 workspace 和 token |
| RB-LEASE-06 | finish_generation 释放 workspace 和 token |
| RB-LEASE-07 | ReportWorker 不能调用 release（无 release 方法） |
| RB-LEASE-08 | GenerationInput 不暴露 release 方法 |

### 19.7 资源限制

| 编号 | 语义 |
|------|------|
| RB-RES-01 | 图片像素炸弹拒绝 |
| RB-RES-02 | CSV 巨型 cell 拒绝 |
| RB-RES-03 | JSON 非有限数字拒绝（NaN/Infinity/-Infinity → parse_constant 拦截 → asset_parse_failed）；JSON 巨型字符串拒绝 |
| RB-RES-04 | TXT 超字节限制拒绝 |
| RB-RES-05 | 严格 UTF-8 和 NUL 拒绝 |
| RB-RES-06 | PPT TABLE_SOURCE 明确拒绝 |

### 19.8 LLM 隔离与素材附录

| 编号 | 语义 |
|------|------|
| RB-LLM-01 | Bridge 内容不进入 generate_structured_report() 提示词 |
| RB-LLM-02 | Artifact 文本不解释为系统指令 |
| RB-LLM-03 | Word 素材附录按 order 顺序 |
| RB-LLM-04 | PPT 素材页按 order 顺序 |
| RB-LLM-05 | PPT TABLE_SOURCE 在 Builder 前拒绝 |
| RB-LLM-06 | project_dir 现有搜索行为不变 |
| RB-LLM-07 | bridge_workspace 不参与普通图表模糊搜索 |
| RB-LLM-08 | Bridge 图片不得通过 [INSERT_IMAGE] 取得 |

### 19.9 回归基线

```text
Runtime 基线: 454 passed
Installer 哨兵: 2 passed
0 failed
0 skipped
0 deselected
0 xfailed
0 xpassed
```

---

## 20. P0 / P1 / P2

### P0（本轮必须关闭 — 含 3.3.0-S 遗留 + 3.3.0-F 新增）

```text
P0-01: 绕过 ArtifactStore 读取文件 — 冻结: Bridge 只使用 ArtifactStore.export
P0-02: 路径或 manifest 泄露到 UI — 冻结: ReportBridgePublicResult 零 Path
P0-03: 普通 result 路径字符串自动进入报告 — 冻结: 只有显式选择才进入 Bridge
P0-04: 自动打开或执行 Artifact — 冻结: Bridge 只导出到受控 workspace
P0-05: ZIP 自动解压 — 冻结: 首版拒绝 ZIP
P0-06: 宏或脚本执行 — 冻结: parsing.py 仅纯数据解析
P0-07: 来源 task 删除与 Bridge 读取竞态 — 冻结: ArtifactOperationCoordinator
P0-08: 临时目录逃逸 — 冻结: workspace.py 路径安全检查
P0-09: 报告事务非原子 — 冻结: 临时文件 + 全部后处理 + fsync + os.link
P0-10: 取消语义冲突 (cancelled + 非空 assets) — 冻结: 状态机强制不变量
P0-11: Builder 失败留下半成品文件 — 冻结: 临时输出，失败回滚
P0-12: UI 线程执行大文件解析 — 冻结: Worker 线程合同
P0-13: 破坏 Runtime 454 基线 — 冻结: 每批回归
P0-14: 修改冻结的 RuntimeArtifact Schema — 冻结: 只读
P0-15: 修改冻结的 ArtifactDeclaration Wire — 冻结: 只读
P0-16: Batch 3.2 行为回退 — 冻结: 每批回归
P0-17: artifact_root 或 storage_relpath 泄露 — 冻结: Bridge 不拼接这些路径
P0-18: Bridge 临时目录路径泄露到 UI — 冻结: 零 Path 公开结果
P0-19: 错误消息包含绝对路径或 traceback — 冻结: safe_error 分类
P0-20: 零 skip/xfail/deselected — 冻结: 每批严格执行

P0-21: Coordinator 推迟到 3.3.2 — 冻结: 3.3.1A 实现 Coordinator
P0-22: 成功 Lease 未持有协调 token — 冻结: Lease 持有 token 直到 release
P0-23: Lease 通过公共信号进入 UI — 冻结: 私有 claim/discard/finish 接口
P0-24: claim 返回完整 Lease — 冻结: claim_ready_generation 返回 GenerationInput
P0-25: 同一 Lease 可被重复生成 — 冻结: claim 仅成功一次
P0-26: 图表注入发生在 os.link 之后 — 冻结: 全部后处理作用于 temp
P0-27: 后处理失败仍留下 final — 冻结: 失败删除 temp，final 不变
P0-28: 图片无像素限制 — 冻结: MAX_BRIDGE_IMAGE_PIXELS + MAX_BRIDGE_IMAGE_DIMENSION
P0-29: CSV/JSON 存在无界字符串 — 冻结: cell chars + string chars 上限
P0-30: 多素材允许静默部分成功 — 冻结: 全有或全无
P0-31: workspace 文件名使用 display_name — 冻结: 主机权威命名
P0-32: 孤儿清理无边界 — 冻结: 仅直接子目录、24h、100 上限
P0-33: PPT TABLE_SOURCE 行为未定义 — 冻结: 首版明确拒绝

P0-34: 模型字段未完整冻结 — 冻结: 所有 dataclass 完整字段和构造不变量
P0-35: PublicResult 允许非法状态组合 — 冻结: 状态 × assets × error_code 强制表
P0-36: Bridge 内部 export 与协调器语义不明 — 冻结: BRIDGE_SESSION 内部放行
P0-37: 解析失败仍可能 READY 或 warning — 冻结: 解析失败 = 全量 FAILED
P0-38: Artifact 文本可进入 LLM 提示词 — 冻结: 零 LLM 输入合同
P0-39: project_dir 可能被 Bridge workspace 替换 — 冻结: 加法接口，独立参数
P0-40: 最终提交仍可能覆盖已有 final — 冻结: os.link no-clobber
P0-41: 最终路径冲突策略仍是二选一 — 冻结: 主机生成唯一文件名
P0-42: 错误结果可包含原始异常 — 冻结: safe_error_code + safe_error_message 唯一通道
```

### P1

```text
P1-01: 命名风格统一
P1-02: 非关键重复测试
P1-03: 性能优化（大 CSV 解析、批量复制）
P1-04: 错误文案优化
P1-05: 布局美化
P1-06: 文档和 help 文本更新
P1-07: PPT 表格能力（当前 ppt_builder 不存在）
P1-08: Builder 进度粒度细化
P1-09: report_bridge 临时目录孤儿清理优化
```

### P2

```text
P2-01: Artifact 历史浏览和搜索
P2-02: 跨会话 Artifact 管理
P2-03: 云同步
P2-04: 高级预览
P2-05: SVG 安全支持（需安全渲染/净化器）
P2-06: 大型数据集流式分析
P2-07: 宏支持
P2-08: 第三方插件转换器
P2-09: OLE 嵌入（替代超链接引用）
P2-10: Artifact 自动过期清理
P2-11: Artifact 分享/导出向导
P2-12: 附件超链接支持 ← 从原版 3.3.1B 移入 P2
```

---

## 21. P0 判定自检

以下任一仍存在则规划不通过：

```text
✅ 模型字段仍未完整冻结 — 不存在：Section 4 完整定义所有 dataclass
✅ PublicResult 允许非法状态组合 — 不存在：4.5 节强制状态 × assets × error_code 表
✅ claim 仍返回完整 Lease — 不存在：claim_ready_generation 返回 GenerationInput
✅ Bridge 内部 export 与协调器语义不明 — 不存在：Section 5 冻结 BRIDGE_SESSION 内部放行
✅ 解析失败仍可能 READY 或 warning 继续 — 不存在：Section 10 统一 FAILED 语义
✅ Artifact 文本可进入 LLM 提示词 — 不存在：Section 14 零 LLM 输入合同
✅ project_dir 可能被 Bridge workspace 替换 — 不存在：Section 15 加法接口
✅ 最终提交仍可能覆盖已有 final — 不存在：Section 13 os.link no-clobber
✅ 最终路径冲突策略仍是二选一 — 不存在：主机生成唯一文件名
✅ 错误结果可包含原始异常 — 不存在：Section 17 safe_error_code 唯一通道
```

---

## 22. 推荐实施顺序

```text
1. 外部审核批准 Batch 3.3.0-F 规划包（本轮）
2. 开始 Batch 3.3.1A — 纯合同、Service、Lease、Coordinator
   - 模型定义 (dp_engine/report_bridge/models.py) — 含全部字段
   - Coordinator (dp_engine/report_bridge/coordinator.py) — BRIDGE_SESSION + USER_ARTIFACT_OPERATION
   - ReportBridgeService.prepare_assets() (使用现有 ArtifactStore.export)
   - ReportBridgeController (QThread/Worker, 私有 claim_ready_generation/discard/finish)
   - workspace.py (Lease 管理)
   - parsing.py (CSV/JSON/TXT/Pillow 头部)
   - 错误分类 (12 项 safe_error_code)
   - L1 + L2 + L3 + Coordinator + Lease 交接测试
   - 审核→封板
3. 开始 Batch 3.3.1B — Builder 适配、确定性附录与原子输出
   - dp_engine/report_bridge/adapters.py (新增)
   - bridge_workspace + bridge_assets 参数 (Word/PPT Builder)
   - 确定性素材附录（"技能输出素材"章节/素材页）
   - 零 LLM 输入合同验证
   - project_dir 兼容（搜索顺序不变）
   - Builder 取消检查点
   - 临时输出 + 全部后处理 + os.link 原子 no-clobber 提交
   - 图表注入 + Bridge 附录作用于 temp
   - 失败与取消回滚
   - PPT TABLE_SOURCE 拒绝
   - 原子输出测试 (RB-ATOM-01..14)
   - 审核→封板
4. 开始 Batch 3.3.2 — UI 集成
   - skill_tab 发送按钮
   - report_workbench 素材区域（ReportAssetSummary 列表）
   - 将既有 Artifact 操作接入已实现的 Coordinator
   - main.py 连接
   - UI 测试
   - 审核→封板
5. 开始 Batch 3.3.3 — 完整回归与封板
   - 全量测试
   - 静态审计
   - P0 清零
   - 最终封板
```

---

## 23. 规划包实物信息

| 属性 | 值 |
|------|-----|
| 绝对路径 | `D:\桌面文件\软件项目_qt6\docs\agents\batch-3.3-planning-package.md` |
| 仓库相对路径 | `docs/agents/batch-3.3-planning-package.md` |
| 行数 | 1947（定稿后实际值，见 Section 26） |
| 大小 | 65,407 bytes（定稿后实际值，见 Section 26） |
| SHA256 | `754bf9e23fb107b33bba46d4d76639a27b126997fd0b5e81bc23c5544c6cb406` |
| UTF-8 | 通过 — 零解码错误 |

---

## 24. 开放问题

| # | 问题 | 当前决策 |
|---|------|---------|
| 1 | ArtifactStore 是否需要新 API | 否 — 使用现有 export() |
| 2 | main.py 的 _ReportWorker 是否重构 | 3.3.1B 中最小修改 |
| 3 | report_engine 是否接受 bridge_assets 参数 | 否 — Bridge 内容不进入 LLM prompt；素材由 Builder 确定性附加 |
| 4 | Builder 的 project_dir 参数是否复用 | 是 — 作为独立 bridge_workspace 参数新增 |
| 5 | Bridge 结果是否跨 skill 切换保留 | 是 — 独立副本 |
| 6 | 原子输出适配是否在 3.3.1B 完成 | 是 |
| 7 | Coordinator 在哪个批次实现 | 3.3.1A（非 3.3.2） |
| 8 | os.link 跨平台兼容 | Windows NTFS/hardlink 支持；不支持的文件系统 fail closed |

---

## 25. Batch 3.3.0-F 最终输出（历史 — 3.3.0-F 定稿时写入）

### 25.1 实际读取 Markdown

| 文件 | 行数 | 用途 |
|------|------|------|
| `CLAUDE.md` | 80 | 项目执行纪律 |
| `docs/agents/batch-3.3-planning-package.md` | 1699 | 3.3.0-S 规划基线 |
| `docs/agents/batch-3.2.3-audit-package.md` | 1179 | Batch 3.2 封板候选审计包 |

### 25.2 实际复核代码

| 文件 | 行数 | 关键确认 |
|------|------|---------|
| `main.py` L1617-1926 | 310 | `doc.save(output_path)` + `prs.save(output_path)` 直接写最终文件；图注入和页脚原地修改；零临时文件、零 fsync、零取消检查点 |
| `word_builder.py` L649-693 | 45 | `build_word_report(…, project_dir='')` → `doc.save(output_path)` |
| `ppt_builder.py` L676-725 | 50 | `build_ppt_report(…, project_dir='')` → `prs.save(output_path)` |
| `models.py` | 259 | WordTable / WordSection / WordReport / PPTSlide / PPTReport — 无 Bridge 字段 |
| `runtime_artifacts.py` L1194-1447 | 254 | `list_task(skill_id, task_id)` → `tuple[RuntimeArtifact, ...]`；`export(skill_id, task_id, artifact_id, target, *, overwrite=False)` — 原子导出 |
| `report_workbench.py` L1-50 | 50 | 纯信号驱动 Dumb Component — 无 Artifact 通道 |
| **合计** | **968** | |

### 25.3 本轮 P0 逐项关闭

| P0 | 问题 | 状态 |
|----|------|------|
| P0-34 | 模型字段未完整冻结 | ✅ 关闭 — Section 4 完整定义 12 个 dataclass/enum，含全部字段、类型和构造不变量 |
| P0-35 | PublicResult 允许非法状态组合 | ✅ 关闭 — 4.5 节强制状态 × assets × error_code 表 |
| P0-36 | Bridge 内部 export 与协调器语义不明 | ✅ 关闭 — Section 5 冻结 BRIDGE_SESSION 内部放行规则 |
| P0-37 | 解析失败仍可能 READY 或 warning | ✅ 关闭 — Section 10 统一 FAILED 语义，删除所有 warning 降级路径 |
| P0-38 | Artifact 文本可进入 LLM 提示词 | ✅ 关闭 — Section 14 零 LLM 输入合同 |
| P0-39 | project_dir 可能被 Bridge workspace 替换 | ✅ 关闭 — Section 15 加法接口，bridge_workspace 独立参数 |
| P0-40 | 最终提交仍可能覆盖已有 final | ✅ 关闭 — Section 13 os.link no-clobber |
| P0-41 | 最终路径冲突策略仍是二选一 | ✅ 关闭 — 主机生成唯一文件名（UTC 时间 + uuid4 hex） |
| P0-42 | 错误结果可包含原始异常 | ✅ 关闭 — Section 17 safe_error_code + safe_error_message 唯一通道 |

全部 42 项 P0 关闭。零遗留。

### 25.4 完整模型合同

冻结 12 个类型定义（Section 4）：

```text
枚举 (4):
  ReportAssetRole          — IMAGE / TABLE_SOURCE / TEXT_SOURCE
  ReportBridgeStatus       — PREPARING / READY / FAILED / CANCELLED / SUCCEEDED
  InternalBridgeState      — IDLE / PREPARING / READY / GENERATING / FAILED / CANCELLED / SUCCEEDED / RELEASED
  OperationKind            — USER_ARTIFACT_OPERATION / BRIDGE_SESSION

选择与请求 (2):
  ReportArtifactSelection  — frozen, kw_only, 7 字段, 7 条构造不变量
  ReportBridgeRequest      — frozen, kw_only, 3 字段, 6 条构造不变量

公开模型 (2):
  ReportAssetSummary       — frozen, kw_only, 5 字段, 3 条构造不变量
  ReportBridgePublicResult — frozen, kw_only, 7 字段, 按状态强制 5 种合法组合

内部模型 (3):
  PreparedReportAsset      — frozen, kw_only, 5 字段, parsed_payload 封闭类型
  ReportGenerationInput    — frozen, kw_only, 4 字段, 不含 release, 不进入 Qt 信号
  ReportBridgeLease        — Controller 内部持有, 7 字段, 幂等 release
```

### 25.5 PublicResult 不变量

| 状态 | assets | safe_error_code | safe_error_message |
|------|--------|-----------------|-------------------|
| PREPARING | () | None | None |
| READY | 非空 tuple | None | None |
| SUCCEEDED | 非空 tuple | None | None |
| FAILED | () | 非空 str | 非空 str |
| CANCELLED | () | None | None |

绝对禁止：Path / bridge_dir / material_path / storage_relpath / artifact_root / sha256 / manifest / 异常 repr / traceback / 绝对路径字符串

### 25.6 GenerationInput 与 Lease 所有权

- `claim_ready_generation(request_id, generation)` → `ReportGenerationInput | None`
- GenerationInput 不含 release 方法，不能改变 Controller 状态
- Controller 始终持有真实 Lease（唯一 owner）
- ReportWorker 只持有 GenerationInput 引用，不持有 Lease
- 公共 Qt 信号只发送 ReportBridgePublicResult
- 禁止：返回 Lease 给调用方、Worker 调用 release、信号携带 Path

### 25.7 Coordinator 内部 export 合同

- BRIDGE_SESSION token 持有期间，Bridge 内部 `ArtifactStore.list_task` 和 `export` 调用属于 Session 授权步骤
- Bridge 内部 export 不经过 Coordinator 检查（直接放行），不重复获取 USER_ARTIFACT_OPERATION token
- 同一 (skill_id, task_id) 的 BRIDGE_SESSION 与 USER_ARTIFACT_OPERATION 互斥
- 不同 owner 可并发

### 25.8 解析失败唯一语义

- CSV/JSON/TXT 解析失败 → 整个 prepare_assets FAILED
- PublicResult.assets = ()
- safe_error_code = "asset_parse_failed"
- 不产生 Lease，清理整个 workspace，释放 Coordinator token
- 首版不支持部分成功、跳过失败素材、解析失败降级附件

### 25.9 类型矩阵（F-R2 终局合同 — JSON 唯一角色 TEXT_SOURCE）

| 扩展名 | role | parsed_payload | 备注 |
|--------|------|---------------|------|
| PNG | IMAGE | None | Pillow 头部验证尺寸 |
| JPEG | IMAGE | None | 同上 |
| CSV | TABLE_SOURCE | ParsedTable | UTF-8, 行列限制 |
| JSON | TEXT_SOURCE | str | 严格解析后确定性规范化为 TEXT_SOURCE 字符串（json.dumps sort_keys） |
| TXT | TEXT_SOURCE | str | UTF-8, 字符限制 |

首版拒绝：PDF / DOCX / PPTX / XLSX / ZIP / SVG

**JSON 选择 TABLE_SOURCE 时**：safe_error_code="role_mismatch"（JSON 不再支持 TABLE_SOURCE 角色）

> 历史（已废弃，无实施效力）：3.3.0-F 和 3.3.0-F-R 曾允许 JSON 双角色
> （TABLE_SOURCE→ParsedTable 与 TEXT_SOURCE→str 由用户选择）。
> F-R2 终局合同冻结 JSON 唯一角色为 TEXT_SOURCE。

### 25.10 零 LLM 输入合同

- PNG/JPEG/CSV/JSON/TXT 内容不进入 `generate_structured_report()` 的 LLM 提示词
- 现有 AI 报告生成提示词和输入合同保持不变
- Bridge 素材只通过确定性 Builder 适配进入报告
- 动机：Artifact 内容是不可信输入，防止提示注入

### 25.11 Word/PPT 确定性素材合同

- **Word**：报告末尾新增"技能输出素材"章节，按 order 顺序，IMAGE → 图片+标题、TABLE_SOURCE → WordTable（首行加粗）、TEXT_SOURCE → 标题+文本段落
- **PPT**：报告末尾追加素材页，IMAGE → 受控图片页（适配尺寸居中）、TEXT_SOURCE → bullet 文本页（每页≤8 bullet, ≤200 字符/条, 自动分页）、TABLE_SOURCE → 首版明确拒绝 (safe_error_code="role_mismatch")
- 禁止：静默丢弃、自动截图 CSV、素材加入 LLM prompt

### 25.12 project_dir 兼容

- 现有 `project_dir` 语义不变（图表搜索、模板素材、项目文件解析）
- `bridge_workspace` 作为独立参数新增（不替换 project_dir）
- Bridge managed_filename 仅在 bridge_workspace 内解析
- Bridge 图片不参与普通 `[INSERT_IMAGE]` 模糊搜索

### 25.13 原子 no-clobber 提交

冻结方案：`os.link(temp_output, final_output)` + `os.unlink(temp_output)`

- 最终文件名：`<安全基础名>_<UTC时间>_<完整uuid4hex>.docx/pptx`（主机生成）
- 完整事务：mkstemp → Builder 写 → 图注入 → 页脚 → Bridge 附录 → fsync → cancel 检查 → os.link → unlink temp
- final 已存在时 os.link 抛出 FileExistsError，不修改已有文件
- link 成功是唯一提交点
- link 后晚到取消保持 SUCCEEDED
- temp unlink 失败仅记录告警，不损坏 final
- 不支持硬链接的文件系统 fail closed

### 25.14 后处理事务

全部后处理（图注入、页脚追加、Bridge 素材附录）必须作用于 temp_output，在 os.link 提交前完成。任何一步失败 → 删除 temp，final 不变。

### 25.15 错误分类

12 项 safe_error_code：invalid_request / owner_mismatch / artifact_not_found / artifact_integrity_failed / unsupported_media_type / role_mismatch / asset_too_large / asset_parse_failed / workspace_security_failed / workspace_io_failed / operation_conflict / internal_failure

UI 只接收 safe_error_code + safe_error_message。

### 25.16 修订子批范围

| 子批 | 范围 | 状态 |
|------|------|------|
| 3.3.0 | 原始勘察 | 历史 |
| 3.3.0-R | 外部审核整改 (7 P0) | 历史 |
| 3.3.0-S | 最终合同闭环 (5 实施冲突) | 历史 |
| 3.3.0-F | 可实施合同定稿 | 历史 — 已被 F-R 取代 |
| 3.3.0-F-R | 语义一致性整改 (8 项缺陷) | 历史 — 已被 F-R2 取代 |
| 3.3.0-F-R2 | 最终合同归位 | 历史 — 已被 F-R3 取代 |
| **3.3.0-F-R3** | **JSON 非有限数字拒绝合同修正** | **当前** |
| 3.3.0-F-R3 规划 Readiness Review | 外部最终审核 | 待进行 |
| 3.3.1A | 模型 · 状态机 · Coordinator · Workspace · Lease · 解析 · Controller | 待 3.3.0-F-R3 审核通过后开始 |
| 3.3.1B | Builder 适配 · 确定性附录 · 原子输出 | 待 3.3.1A 封板后开始 |
| 3.3.2 | UI 集成 · Artifact 操作协调接入 | 待 3.3.1B 封板后开始 |
| 3.3.3 | 完整回归封板 | 待 3.3.2 封板后开始 |

### 25.17 补强测试矩阵

8 组，93 项唯一 RB-* 规划语义（非 pytest collected 节点）：
- L1 模型合同：20 项 (RB-L1-01..20)
- L2 Service 集成：15 项 (RB-L2-01..15)
- L3 安全边界：12 项 (RB-SEC-01..12)
- 原子 no-clobber 输出：14 项 (RB-ATOM-01..14)
- Coordinator：10 项 (RB-COORD-01..10)
- Lease 交接：8 项 (RB-LEASE-01..08)
- 资源限制：6 项 (RB-RES-01..06)
- LLM 隔离与素材附录：8 项 (RB-LLM-01..08)

### 25.18 Runtime 和 Installer 基线

```text
Runtime 基线: 454 passed
Installer 哨兵: 2 passed
0 failed
0 skipped
0 deselected
0 xfailed
0 xpassed
```

### 25.19 Git 范围

- 零生产代码修改
- 零测试代码修改
- 零 fixture 修改
- 零配置修改
- 零 UI 修改
- 唯一允许修改文件：`docs/agents/batch-3.3-planning-package.md`

### 25.20 P0 / P1 / P2

- **P0**：42 项 — 全部关闭（含 3.3.0-S 遗留 33 项 + 3.3.0-F 新增 9 项）
- **P1**：9 项 — 非阻塞
- **P2**：12 项 — 未来增强

### 25.21 规划包实物信息

见 Section 26（定稿后写入）。

### 25.22 最终声明（3.3.0-F 历史 — 已废弃，无实施效力）

```text
Batch 3.3.0-F 可实施合同定稿已完成并提交外部审核。
未开始 Batch 3.3.1A。
未修改任何生产代码、测试代码、fixture、配置或 UI。
等待外部审核结论。
```

---

## 26. 历史实物统计（3.3.0-F / 3.3.0-F-R 存档 — 已废弃，无实施效力）

（定稿后由自动化统计填入 — 见下方 Bash 命令输出）

### 26.1 文件元数据

| 属性 | 值 |
|------|-----|
| 绝对路径 | `D:\桌面文件\软件项目_qt6\docs\agents\batch-3.3-planning-package.md` |
| 仓库相对路径 | `docs/agents/batch-3.3-planning-package.md` |
| 编码 | UTF-8 |
| 行尾 | LF（Unix 风格，部分 Git-tracked 文件为 CRLF） |

### 26.2 统计数据

| 指标 | 值 |
|------|-----|
| 行数 | 1947 |
| 大小 (bytes) | 65,407 |
| SHA256 | `754bf9e23fb107b33bba46d4d76639a27b126997fd0b5e81bc23c5544c6cb406` |
| UTF-8 有效性 | 通过 — 零解码错误 |

---

## 27. Batch 3.3.0-F-R2 — 最终合同归位完成报告

### 27.1 F-R2 整改范围

```text
零生产代码修改
零测试代码修改
零 fixture 修改
零配置修改
零 UI 修改
唯一允许修改文件: docs/agents/batch-3.3-planning-package.md
未开始 Batch 3.3.1A
```

### 27.2 F-R2 关闭的 7 项合同归位缺陷

| # | 缺陷 | F-R2 整改 |
|---|------|---------|
| 1 | 无 "Batch 3.3.0-F-R2" 章节 | 新增 Section 0.6 — Batch 3.3.0-F-R2 轮次定义；Section 0.5 标记为历史 |
| 2 | RB-L2-06 仍是 JSON(TABLE_SOURCE)→ParsedTable | RB-L2-06 唯一改为：JSON 严格解析并确定性规范化为 TEXT_SOURCE 字符串 |
| 3 | RB-L2-06b 仍存在 | 删除 RB-L2-06b；L2 从 16 项减至 15 项 |
| 4 | 类型矩阵仍允许 JSON→TABLE_SOURCE | 终局合同冻结：唯一 JSON→TEXT_SOURCE；JSON+TABLE_SOURCE→role_mismatch |
| 5 | Lease 清理失败日志仍允许 workspace_path | 改为仅记录受控错误码和非敏感状态；禁止 8 类敏感字段 |
| 6 | 测试矩阵 8 组 94 项 | 更正为 8 组 93 项（删除 RB-L2-06b） |
| 7 | 文件内 SHA256 与实际不一致 | Section 27.3 编辑后重算 SHA256 |

### 27.3 F-R2 自查验证

**自查命令 1** — 正式有效合同零匹配 JSON TABLE_SOURCE：

```text
rg -n "JSON.*TABLE_SOURCE|TABLE_SOURCE.*JSON|JSON.*ParsedTable|ParsedTable.*JSON|RB-L2-06b" \
  docs/agents/batch-3.3-planning-package.md
→ 正式有效合同零匹配。
  历史引用已标注"已废弃，无实施效力"。
```

**自查命令 2** — workspace_path 告警零匹配：

```text
rg -n "workspace_path.*告警|告警.*workspace_path|含 workspace_path|含workspace_path" \
  docs/agents/batch-3.3-planning-package.md
→ 必须零匹配。
```

**自查命令 3** — F-R2 章节必须命中：

```text
rg -n "Batch 3\.3\.0-F-R2|Final Contract Restoration" \
  docs/agents/batch-3.3-planning-package.md
→ 必须命中 Section 0.6、Section 25.16、Section 27。
```

### 27.4 冻结合同确认

```text
✅ JSON 唯一角色 TEXT_SOURCE — 类型矩阵唯一：
   PNG → IMAGE, JPEG → IMAGE, CSV → TABLE_SOURCE, JSON → TEXT_SOURCE, TXT → TEXT_SOURCE
✅ 删除所有有效合同中的 JSON→TABLE_SOURCE、JSON→ParsedTable、JSON 双角色、RB-L2-06b
✅ JSON 选择 TABLE_SOURCE → safe_error_code="role_mismatch"
✅ RB-L2-06 唯一改为：严格解析并确定性规范化为 TEXT_SOURCE 字符串
✅ MAX_BRIDGE_JSON_RENDER_CHARS = 50_000
✅ 超限返回 safe_error_code="asset_too_large"
✅ workspace 清理失败仅记录受控错误码和非敏感状态
✅ 禁止记录 workspace_path、绝对路径、用户目录、应用数据目录、
   原始异常消息、repr(exception)、traceback、manifest、storage_relpath
✅ workspace 清理失败仍释放 Coordinator token
✅ token 在 finally 等价路径释放
✅ Lease 最终 released=True
✅ Controller 最终进入 RELEASED
✅ 残留目录进入有界孤儿清理
✅ 测试矩阵：8 组 93 项（非 94 项）
```

### 27.5 F-R2 最终实物统计

| 属性 | 值 |
|------|-----|
| 绝对路径 | `D:\桌面文件\软件项目_qt6\docs\agents\batch-3.3-planning-package.md` |
| 仓库相对路径 | `docs/agents/batch-3.3-planning-package.md` |
| 行数 | 2139 |
| 大小 (bytes) | 74,426 |
| SHA256 | `76a7c471d1692ba69bf23deaf9f9db0ae46caf6e1e7bb5a7bd0059d301d27767` |
| UTF-8 有效性 | 通过 — 零解码错误 |

### 27.6 最终声明（F-R2 历史 — 已被 F-R3 取代）

```text
Batch 3.3.0-F-R2最终合同归位已完成并提交外部审核。
未开始Batch 3.3.1A。
未修改任何生产代码、测试代码、fixture、配置或UI。
等待外部审核结论。
```

---

## 28. Batch 3.3.0-F-R3 — JSON 非有限数字拒绝合同修正完成报告

### 28.1 F-R3 修正内容

**原错误**：规划包 JSON 解析合同写成 `json.loads() 解析；拒绝 NaN、Infinity 和 -Infinity（allow_nan=False）`。`json.loads()` 不接受 `allow_nan` 参数 — 这是无效的 Python API 合同。

**F-R3 修正**：使用 `parse_constant` 回调在解析阶段拒绝非有限数字：

```python
def _reject_non_finite(value: str) -> NoReturn:
    raise ValueError("non-finite JSON number")

parsed = json.loads(
    decoded_text,
    parse_constant=_reject_non_finite,
)
```

**合同含义**：
- `parse_constant` 负责**解析阶段**拒绝 — 第一时间拦截 NaN/Infinity/-Infinity；
- `json.dumps(..., allow_nan=False)` 负责**序列化阶段**防御；
- 二者都必须存在，缺一不可。

### 28.2 测试矩阵同步

RB-L2-06 和 RB-RES-03 已同步更新，覆盖：
- JSON NaN → parse_constant 拦截 → asset_parse_failed
- JSON Infinity → parse_constant 拦截 → asset_parse_failed
- JSON -Infinity → parse_constant 拦截 → asset_parse_failed
- parse_constant 拒绝路径
- json.dumps allow_nan=False 规范化不包含非有限数字

作为既有节点子断言，不新增 RB-* ID。测试矩阵保持 8 组 93 项唯一 RB-* 规划语义。

### 28.3 全文一致性扫描

**扫描 1** — `json.loads.*allow_nan` 零匹配：
```text
rg -n "json\.loads.*allow_nan|allow_nan.*json\.loads" \
  docs/agents/batch-3.3-planning-package.md
→ 零匹配 ✅
```

**扫描 2** — `parse_constant` 和 `allow_nan=False` 能明确证明合同：
```text
rg -n "parse_constant|allow_nan=False" \
  docs/agents/batch-3.3-planning-package.md
→ parse_constant 解析阶段拒绝 ✅
→ allow_nan=False 序列化阶段防御 ✅
```

**扫描 3** — 其余合同不变：
```text
✅ RB-L2-06b 不存在于有效测试矩阵
✅ JSON 有效角色仍只有 TEXT_SOURCE
✅ 测试矩阵仍为 8 组 93 项
```

### 28.4 Git 范围

```text
唯一修改: docs/agents/batch-3.3-planning-package.md
零生产代码修改
零测试代码修改
零 fixture 修改
零配置修改
零 UI 修改
未开始 Batch 3.3.1A
```

### 28.5 P0 / P1 / P2

- **P0**：42 项 — 全部关闭（F-R3 不新增 P0）
- **P1**：9 项 — 非阻塞
- **P2**：12 项 — 未来增强

### 28.6 最终声明

```text
Batch 3.3.0-F-R3 JSON非有限数字合同修正已完成并提交外部审核。
未开始Batch 3.3.1A。
未修改任何生产代码、测试代码、fixture、配置或UI。
等待外部审核结论。
```
