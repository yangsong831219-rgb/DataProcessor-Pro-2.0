# Batch 3.5.3 — Provider 调度与内置兼容路径审核包

**日期**：2026-08-06  
**分支**：`llama-cpp`  
**HEAD**：`8a0105527857bbefd34426817ccc0cbbf23102b6`  
**审核结论**：PASS，可提交用户审核；尚未进入 Batch 3.5.4

> GitHub 插件和 `gh` 仍不可用。本批依据 `batch-3.5-planning-package.md` 已确认的本地 Batch 3.5.3 合同执行，不声称读取、创建或更新了远程 Issue。

---

## 1. 批次合同

单一目标：把报告主链中“结构化报告数据 → 临时 OOXML”的渲染位点改为 Provider 调度，默认接通 Builtin Provider，并证明输出结构和外层事务语义等价。

允许范围：

- 新增 `dp_engine/report_provider/` 的合同、内置 Adapter 和 Orchestrator；
- `main.py` 的最小渲染分派位点；
- Provider 专属测试；
- 将既有原子事务结构测试从“直接 Builder 调用”升级为“Provider 调度”同强度断言；
- 本审核包。

禁止范围：

- Word/PPT Builder 内部排版；
- Report Bridge 合同；
- Word/PPT 模板检测与标准化算法；
- SkillRuntime、Registry 和 Batch 3.5.2 报告后端安全合同；
- Report Workbench 后端选择 UI；
- 安装、模拟或调用真实 PPT Master/Word 插件。

---

## 2. 架构审查与调用面

仓库没有 `CONTEXT.md`、`CONTEXT-MAP.md` 或 `docs/adr/`。Provider 候选已由冻结的 Batch 3.5 计划和用户“进入 Batch 3.5.3”的指令选定，因此没有重复发起候选选择。

本批采用一个局部且有深度的 Seam：

```text
结构化报告
  -> 同目录 mkstemp
  -> ReportRenderOrchestrator
  -> BuiltinReportRenderProvider
  -> 既有 WordBuilder/PPTBuilder
  -> 图表注入
  -> 资料纳入 footer
  -> OOXML reopen 校验
  -> fsync
  -> 最后取消门禁
  -> os.link no-clobber commit
  -> unlink temp
```

Provider 只拥有临时 OOXML 渲染；Host 继续拥有后处理、验证和提交。这样未来外部 Provider 可替换渲染 Implementation，而不会把原子提交或 Bridge 生命周期移入插件。

代码图工具在当前环境不可用，且没有修改既有函数签名。使用 `rg` 枚举调用点：`_execute_report_build_transaction()` 只有 `main.py` 的生产调用和原子事务测试调用；直接 `build_word_report()`/`build_ppt_report()` 的生产渲染调用已只存在于 Builtin Adapter，`main.py` 不再直接调用 Builder。

---

## 3. 实现结果

### 3.1 稳定 Interface

`dp_engine/report_provider/models.py` 新增：

- `ReportRenderRequest`：格式、结构化报告、标准化模板路径、临时输出路径、项目图表根、Bridge 资产和取消回调；
- `ReportRenderResult`：临时输出路径、warnings、缺图和 Provider provenance；
- `ReportProviderProvenance`：非空 provider ID/version 和可记录字典形式；
- `ReportRenderProvider` Protocol。

请求只接受 `word|ppt`，输出路径不能为空。

### 3.2 Builtin Adapter

`BuiltinReportRenderProvider` 精确保留旧分派：

- Word：`WordReport.from_dict()` → `WordBuilder.build_word_report()`；
- PPT：`PPTReport.from_dict()` → `PPTBuilder.build_ppt_report()`；
- 原模板路径、项目目录、Bridge workspace/assets 和取消回调原样传递；
- PPT warnings 和 Word missing images 继续返回主链；
- provenance 固定为 `builtin.report_builder@1.0`。

Builder 文件本身零修改。

### 3.3 Orchestrator

`ReportRenderOrchestrator` 默认只注册 Builtin Provider；未知 provider fail closed。执行后校验：

1. 返回 provenance 必须等于执行前声明；
2. 返回输出路径解析后必须等于 Host 指定的临时路径。

该门面为 Batch 3.5.4 的显式后端选择保留扩展位点，但本批不接 UI、不注册外部 Provider、不静默 fallback。

### 3.4 主链最小改造

`main.py` 仅把原直接 Builder 分支替换为 `ReportRenderRequest` + `ReportRenderOrchestrator.render()`：

- 默认仍是 Builtin Provider；
- provenance 写入事务结果的 `provider_provenance`；
- 图表注入、资料纳入、临时文件校验、`fsync`、最后取消检查、`os.link` no-clobber 和临时文件清理顺序不变；
- Provider 失败或 provenance/path 不一致时，既有外层回滚删除临时文件，不创建或覆盖 final。

---

## 4. 测试证据

### 4.1 Red 基线

```powershell
C:\Python314\python.exe -m pytest tests/test_report_render_provider.py -q
```

```text
collected 0 items / 1 error
ModuleNotFoundError: No module named 'dp_engine.report_provider'
```

失败符合预期：Provider 包尚不存在。

### 4.2 Green 调试

首轮实现收集 8 项，结果为 `7 passed, 1 failed`。唯一失败是专属夹具把缺图文件名直接写入 `image_anchors`，而冻结合同要求 `[INSERT_IMAGE: filename]`。只修正夹具格式，没有修改或弱化 Builder 行为。

### 4.3 Provider 专属最终结果

```powershell
C:\Python314\python.exe -m pytest tests/test_report_render_provider.py -q
```

```text
collected 8
8 passed
0 failed
0 skipped
0 xfailed
0 deselected
```

覆盖：Word/PPT OOXML 主体结构与直接 Builder 等价、缺图传播、两种格式取消传播、默认 Builtin、未知 Provider、provenance 冒充 fail closed，以及 `main.py` 不再直接调用 Builder。

### 4.4 报告主链精确回归

一次性选择 25 个受影响的 `test_report_*`、`test_word_*`、`test_ppt_*` 文件；排除已单独完成的 Provider 专属集合和未受影响的 Batch 3.5.2 `test_report_backend_*` Runtime 集合。没有使用 `-k`、skip、xfail 或 deselect。

```text
collected 586
586 passed
0 failed
0 skipped
0 xfailed
0 deselected
1 existing RankWarning from numpy.polyfit
```

覆盖 Report Bridge adapter/service/controller/coordinator/安全/UI/真实主窗口集成、原子/no-clobber/cancel、Builder Bridge 附录、图表 manifest/规划/注入、资料完整性、模板准备/校验、Word/PPT Builder 与回归。

唯一测试算术：8 个 Provider 专属节点 + 586 个既有报告节点 = **594 个唯一通过节点**。按不重复测试合同，没有提前执行 Batch 3.5.5 的全仓最终回归。

---

## 5. 静态检查

### 5.1 Pyright

```powershell
C:\Python314\Scripts\pyright.exe dp_engine/report_provider tests/test_report_render_provider.py
```

```text
0 errors, 0 warnings, 0 informations
```

`main.py --outputjson`：0 errors、28 个历史 warnings；本批修改行与 warnings 交集为 0。  
`tests/test_report_bridge_atomic_output.py`：0 errors、14 个历史 warnings；本批修改行与 warnings 交集为 0。

没有通过修改无关代码或添加 `type: ignore` 来隐藏历史诊断。

### 5.2 Compileall

```powershell
C:\Python314\python.exe -m compileall -q main.py dp_engine/report_provider tests/test_report_render_provider.py tests/test_report_bridge_atomic_output.py
```

正常退出，无输出。

### 5.3 标记、秘密和格式搜索

- Provider 专属测试中的 `skip/xfail/deselect`：0 命中；
- Provider 生产代码和专属测试中的 API key/password/private key/配置密钥文件名：0 命中；
- 新增 Provider 代码和专属测试的行尾空白：0 命中；
- 未读取或输出 `ai_models_config.json`。

---

## 6. 范围与哈希审核

Batch 起点：

```text
status --untracked-files=all = 448
tracked diff-name count = 50
cached count = 0
main.py = 5083FF64EE74B8022606E2DDC4F412E60179A6A9A94232179861E3AB241EB15B
test_report_bridge_atomic_output.py = A4BDF10137C96AD9A25D80CD4053F266838C01599394766D19BEDE6EBEA28B96
dp_engine/report_provider/ exists = False
tests/test_report_render_provider.py exists = False
```

本批路径：

```text
main.py                                             # 最小 Provider 调度改造
dp_engine/report_provider/__init__.py               # 新增
dp_engine/report_provider/models.py                 # 新增
dp_engine/report_provider/builtin.py                # 新增
dp_engine/report_provider/orchestrator.py           # 新增
tests/test_report_render_provider.py                # 新增
tests/test_report_bridge_atomic_output.py            # 等强度结构断言升级
docs/agents/batch-3.5.3-audit-package.md              # 新增
```

代码完成、写审核包前：

```text
status --untracked-files=all = 453
tracked diff-name count = 50
cached count = 0
```

相对起点只新增 4 个 Provider 文件和 1 个专属测试状态项；`main.py` 与原子测试在起点已分别是修改/未跟踪状态。本审核包写入后预期为 454；tracked diff-name 保持 50，cached 保持 0。

最终代码哈希：

```text
5830B2535F766BBE1FD199AA0CEBA19547EDE2AA16B2E612E503EC22BD03950F  main.py
4FCFBD6252622C41A3593A10803E7D7323EC2B1B34D9F64802B3533A92F76136  report_provider/__init__.py
0ED477D10B433568FCE1E61AFD786B1247948A426DEEF2CF22FB3E4355A4E905  report_provider/models.py
A358C6C47356BCC889746516C21650D71F7D523358FED57F1BA15FA5274E34E8  report_provider/builtin.py
625FE2DEE9247DD6D279E464BF024A9301745154ECF82A2B8ECFDFAB26C3B50E  report_provider/orchestrator.py
45E1D72087FB763724EA6BBCCAB98D5577CA8BF9A09B6FCB40852824545CB662  test_report_render_provider.py
81630FE6DADD1822754433E7DECFC2E17AF6956A17890A62917580E551D2A79E  test_report_bridge_atomic_output.py
```

禁止文件结束哈希与 Batch 起点一致：

```text
FF857B9F6CC71A260B43D54A47085C91639533BAD55106280C827E3AFAD052B2  ui/report_workbench.py
186CFEA92F8599EF2A54F2EC1624E70B15B90107E84BDCEEAD0F59C3866F7CF1  report_bridge/service.py
F5932EF813F54B52C93E449FD4A673A313519AF9474B5DC572D121D90C171931  word_builder.py
135FBC4E2255FAC2F497AA6E96F2E2728099386A5A9C20632BE501D8CE29F190  ppt_builder.py
79A834BBE623D179F5C32A0206C063D8AEA7C12DBD5E9C5E954A4E31AE22F5FD  report_template_preparation.py
814720EE2FC46C84A3F254BACC0D63B74A6C5DCDFBAF860B2EDAA659BB9620E9  registry.py
B389807BC4BF230999428D594736C01AB96C30438679C8D5FF9BABF37F590B21  runtime_service.py
```

Batch 3.5.2 Runtime/report_backend 关键文件哈希也全部保持不变。

---

## 7. 门槛判定

| 门槛 | 结果 |
|---|---|
| 默认 Builtin Provider | PASS |
| Word/PPT 主体结构等价 | PASS — 2/2 |
| Provider provenance 可记录且防冒充 | PASS |
| 取消传播 | PASS — Word/PPT |
| 原子/no-clobber/cancel 精确回归 | PASS |
| 报告主链相关回归 | PASS — 586/586 |
| 唯一通过节点 | 594 |
| Pyright 新代码 | PASS — 0 errors / 0 warnings |
| Pyright 历史文件修改行交集 | PASS — 0 |
| Compileall | PASS |
| 禁止范围哈希 | PASS |
| Git cached | PASS — 0 |

**最终结论：Batch 3.5.3 通过自审，可提交用户审核。当前默认报告行为仍为 Builtin Provider；尚未增加工作台后端选择，尚未把 Batch 3.5.2 外部 report_backend 接入主链，也未安装或调用真实 PPT Master。审核通过后才可进入 Batch 3.5.4。**
