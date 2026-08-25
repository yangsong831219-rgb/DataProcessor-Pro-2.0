# Batch 3.5.2 — 隔离报告生成协议与安全发布审核包

**日期**：2026-08-05  
**分支**：`llama-cpp`  
**HEAD**：`8a0105527857bbefd34426817ccc0cbbf23102b6`  
**审核结论**：PASS，可提交用户审核；尚未进入 Batch 3.5.3

> GitHub 插件和 `gh` 仍不可用。本批依据 `batch-3.5-planning-package.md` 的本地 Issue 草案执行，不声称读取、创建或更新了远程 Issue。

---

## 1. 批次合同

单一目标：实现 `generate_report` 的 headless Runtime 闭环，完成安全 staging、隔离 Worker 调用、单一报告 Artifact 发布与 Host 复核；不接 UI 和主报告生成链。

允许范围：

- Runtime models/protocol/service/worker/artifacts 的最小扩展；
- 新增 `dp_engine/report_backend/job.py`；
- `dp_engine/report_backend/__init__.py` 导出新合同；
- 新增专属测试和本审核包。

禁止范围：

- `main.py`；
- `ui/report_workbench.py`；
- Report Bridge；
- Word/PPT Builder；
- Registry 语义或写入行为；
- 安装或伪造真实 PPT Master/Word 插件；
- 报告主链、最终文件提交和 UI 选择。

---

## 2. Git 起点

```text
branch = llama-cpp
HEAD = 8a0105527857bbefd34426817ccc0cbbf23102b6
status --untracked-files=all = 445
tracked diff-name count = 50
cached count = 0
dp_engine/report_backend/job.py exists = False
tests/test_report_backend_runtime.py exists = False
```

工作树在本批开始前已高度脏化，Runtime、report_backend 和测试文件大多是既有未跟踪用户资产，普通 `git diff` 无法显示其增量。本批使用起点/结束哈希、精确路径、测试和禁止文件哈希审核；未清理、还原、暂存或提交任何用户变化。

---

## 3. 实现结果

### 3.1 专用运行时操作

- `ALLOWED_OPERATIONS` 新增 `generate_report`；
- `SkillRuntimeService.run_report_backend()` 只读取 `entrypoints.generate`；
- Worker 将 `generate_report` 分派到经过验证的 Artifact 上下文；
- 普通 `run_skill()` 保留原入口、状态、参数和零 Registry 写入语义；
- 取消、超时、崩溃、越权、协议错误和发布错误复用 Runtime 状态集合，并为报告路径增加安全阶段码。

### 3.2 `ReportBackendJobV1`

Host 端作业合同包含：

- `schema_version = 1`；
- `ppt|word` 与 `pptx|docx` 一致性；
- 结构化报告快照；
- 可选的标准化模板；
- 带 host ID、SHA-256、媒体类型、语义标签和目标位置的图片资产；
- required/supplementary 图表集合；
- inclusion summary；
- 固定 `output/report.pptx|docx`。

staging 只把经过批准的源文件复制进托管工作区。传给插件的 JSON/请求仅包含相对路径，不包含源绝对路径、密钥、环境变量或命令。输入拒绝 symlink/reparse、hardlink、非普通文件、超限文件、伪造 PNG/JPEG、损坏或类型不符的模板 OOXML，以及敏感/绝对路径字段。

### 3.3 输出 fail closed

成功必须同时满足：

1. Worker 状态为 `succeeded`；
2. 工作区只有固定的 `report.*`；
3. 结果字段精确为 provider provenance、合同版本、图表放置集合和 warnings；
4. provider ID/version 与实际选择一致；
5. required 图表全部覆盖，无重复、未知或交叉 ID；
6. 只声明一个 `kind=document` Artifact；
7. 声明路径、扩展名、MIME 和请求格式一致；
8. ArtifactPublisher 实际嗅探到匹配的 DOCX/PPTX OOXML；
9. 发布后 owner、task、hash、size 再验证。

任一条件失败均不返回 Artifact；若失败发生在发布后，Host 安全删除该 task 的已发布目录。

### 3.4 日志与错误最小化

- 报告后端崩溃响应不回传原始 stderr；
- 插件执行异常不回传包含用户正文的异常字符串或 traceback；
- 失败工作区中的插件 stdout/stderr 被固定安全标记覆盖；
- result 不接受合同外字段，防止把任意用户正文带回主进程；
- 普通 `run` 的既有错误和脱敏行为保持不变。

### 3.5 OOXML MIME 修正

旧 Runtime 将 MIME 限制为 64 字符，但标准 PPTX/DOCX MIME 超出该值，导致已声明支持的 OOXML 无法合法发布。本批将统一上限调整为 128 字符，仍保持有界，并由既有 Artifact 安全回归覆盖。

---

## 4. 测试证据

### 4.1 Red 基线

```powershell
C:\Python314\python.exe -m pytest tests/test_report_backend_runtime.py -q
```

```text
collected 18
18 failed
```

失败符合预期：`job.py`、`generate_report` 协议和 Service 公共入口尚不存在。

### 4.2 Green 调试记录

首轮实现：

```text
10 passed, 8 failed
```

八项失败均指向同一旧缺陷：64 字符 MIME 上限拒绝标准 PPTX/DOCX MIME。统一修正后，专属集合先达到 18/18；补充缺失输出、崩溃和越权用例后达到 21/21。

后续阶段码定点测试曾出现 `8 passed, 1 failed`：测试把硬崩溃错误地期望为 `backend_execute`，实现正确返回 `execute`。只修正测试期望，没有弱化生产合同。

过程说明：一次非验收的 `-k crashing` 定点命令产生 `1 passed, 1 deselected`。该命令未作为门禁证据；随后执行完整专属集合和全部最终受影响节点，最终验收命令没有 deselect、skip 或 xfail。

### 4.3 专属最终结果与最终普通 run 哨兵

```powershell
C:\Python314\python.exe -m pytest tests/test_report_backend_runtime.py tests/test_runtime_l3_protocol_env.py::TestParentEnvSecretIsolation::test_secret_not_in_error_message tests/test_runtime_l3_security_boundary.py::TestRunSubprocessBlocked::test_subprocess_rejected -q
```

```text
collected 24
24 passed
0 failed
0 skipped
0 deselected
```

其中专属集合为 **22/22**；两个普通 `run` 哨兵验证异常脱敏和越权状态没有因 Worker 的报告专用分支而改变。

### 4.4 既有 Runtime 精确回归

```powershell
C:\Python314\python.exe -m pytest tests/test_runtime_l1_models.py tests/test_runtime_l2_subprocess.py tests/test_runtime_l2_artifact_publish.py tests/test_runtime_l3_protocol_env.py tests/test_runtime_l3_artifact_security.py tests/test_runtime_l3_security_boundary.py tests/test_runtime_l3_deps_registry.py tests/test_runtime_artifact_store.py tests/test_report_backend_catalog.py -q
```

```text
collected 398
398 passed
0 failed
0 skipped
0 deselected
```

该集合完整覆盖模型/协议、真实子进程、普通 run、Artifact 发布与存储、环境脱敏、路径/内容/TOCTOU 安全、权限、依赖、Registry 和 Batch 3.5.1 Catalog。

唯一测试算术：22 个新增专属节点 + 398 个既有节点 = **420 个唯一通过节点**。最后两个普通 run 哨兵属于上述 398 的子集，只是对最终 Worker 小改的定点复核，不重复计数。按 Batch 3.5 计划未提前执行全仓测试。

---

## 5. 静态检查

### 5.1 Pyright

```powershell
C:\Python314\Scripts\pyright.exe dp_engine/report_backend/__init__.py dp_engine/report_backend/job.py dp_engine/skills/runtime_models.py dp_engine/skills/runtime_protocol.py dp_engine/skills/runtime_worker.py dp_engine/skills/runtime_artifacts.py dp_engine/skills/runtime_service.py tests/test_report_backend_runtime.py
```

```text
0 errors, 0 warnings, 0 informations
```

### 5.2 Compileall

```powershell
C:\Python314\python.exe -m compileall -q dp_engine/report_backend dp_engine/skills/runtime_models.py dp_engine/skills/runtime_protocol.py dp_engine/skills/runtime_worker.py dp_engine/skills/runtime_artifacts.py dp_engine/skills/runtime_service.py tests/test_report_backend_runtime.py
```

正常退出，无输出。

### 5.3 危险行为与测试标记搜索

新增测试中的 `pytest.skip/xfail`：0 命中。生产增量中的 `shell=True`、`os.system`、`eval/exec`、requests/urllib/socket：0 个可执行命中；唯一 `shell=True` 文本是既有 Service 注释 `NO shell=True`。Runtime 仍只使用参数数组和 `shell=False` 启动受控 Worker。

---

## 6. 安全用例覆盖

| 类别 | 结果 |
|---|---|
| 相对路径 staging + SHA-256 | PASS |
| 敏感字段/绝对路径拒绝 | PASS |
| 固定 generate_report 请求白名单 | PASS |
| 真 PPTX 与真 DOCX 发布 | PASS |
| 假 OOXML/错 MIME/错扩展名 | PASS |
| 缺失、多余、错 kind、错路径文档 | PASS |
| required 图表覆盖 | PASS |
| disabled/unhealthy/格式/模板门禁 | PASS |
| timeout/cancel/crash | PASS |
| 项目外读取越权 | PASS |
| Registry 零写入 | PASS |
| 错误/日志不含报告正文 | PASS |

---

## 7. 范围与哈希审核

本批生产/测试路径：

```text
dp_engine/report_backend/__init__.py
dp_engine/report_backend/job.py                  # 新增
dp_engine/skills/runtime_models.py
dp_engine/skills/runtime_protocol.py
dp_engine/skills/runtime_worker.py
dp_engine/skills/runtime_artifacts.py
dp_engine/skills/runtime_service.py
tests/test_report_backend_runtime.py             # 新增
docs/agents/batch-3.5.2-audit-package.md         # 新增
```

代码完成、写审计包前：

```text
status --untracked-files=all = 447
tracked diff-name count = 50
cached count = 0
```

相对起点 445 项只增加 `job.py` 与专属测试；本审核包写入后预期为 448。既有 tracked diff-name 保持 50，cached 保持 0。

最终关键哈希：

```text
94C3D14F230EF542D3E5D4536A353A3876AC8F97E2680637610E407FD13A3A1A  runtime_models.py
A471E000131BE90652FB062B617C9ED3F6A98D41C25186A9879C0D51BF6313FB  runtime_protocol.py
87491D46BED60E936F94B3DFC10E710C40ACE7289CEC6195AECECF10F4574FB6  runtime_worker.py
A9F5B1E2E25B27736537DBF7251D5DBD179674F4C72B2863DE6F66128C2C5ED4  runtime_artifacts.py
B389807BC4BF230999428D594736C01AB96C30438679C8D5FF9BABF37F590B21  runtime_service.py
99EFA3C721A9282541ED2E888BECD58FCC0576A1C4090EDF4FBC35D3DA747CA7  report_backend/__init__.py
C7A0EC51F476097520BECFCDFC773109F343C49B6B78FD3B8C8998B79918C85B  report_backend/job.py
808047EA4704E0C17F4BECFC9699B743B9DAC899DDB8B765D696900F90EFEDF4  test_report_backend_runtime.py
```

以下禁止文件结束哈希与 Batch 3.5.2 起点完全一致：

```text
5083FF64EE74B8022606E2DDC4F412E60179A6A9A94232179861E3AB241EB15B  main.py
FF857B9F6CC71A260B43D54A47085C91639533BAD55106280C827E3AFAD052B2  ui/report_workbench.py
186CFEA92F8599EF2A54F2EC1624E70B15B90107E84BDCEEAD0F59C3866F7CF1  report_bridge/service.py
F5932EF813F54B52C93E449FD4A673A313519AF9474B5DC572D121D90C171931  word_builder.py
135FBC4E2255FAC2F497AA6E96F2E2728099386A5A9C20632BE501D8CE29F190  ppt_builder.py
814720EE2FC46C84A3F254BACC0D63B74A6C5DCDFBAF860B2EDAA659BB9620E9  registry.py
```

---

## 8. 门槛判定

| 门槛 | 结果 |
|---|---|
| generate_report 专属集合 | PASS — 22/22 |
| 既有 Runtime 精确回归 | PASS — 398/398 |
| 最终受影响集合 | PASS — 24/24，0 skipped/deselected |
| 普通 run 行为 | PASS |
| cancel/timeout/crash/越权 | PASS |
| 假 OOXML 与输出合同 | PASS |
| Pyright | PASS — 0 errors / 0 warnings |
| Compileall | PASS |
| 禁止范围哈希 | PASS |
| Git cached | PASS — 0 |

**最终结论：Batch 3.5.2 通过自审，可提交用户审核。审核通过后可进入 Batch 3.5.3（Provider 调度与内置路径等价性）；当前仍未接入报告工作台、未改变内置 Word/PPT Builder、未安装或调用真实 PPT Master。**
