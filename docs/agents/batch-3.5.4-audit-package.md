# Batch 3.5.4 审核包 — 工作台后端选择与专业后端接通

审核日期：2026-08-06  
分支：`llama-cpp`  
HEAD：`8a0105527857bbefd34426817ccc0cbbf23102b6`  
结论：**PASS，可进入 Batch 3.5.5 的前置条件检查；未安装、未调用、未验收真实 PPT Master。**

## 1. 冻结合同与范围

单一目标：用户可在报告工作台明确选择一个当前兼容的报告后端，生成主链真实调用该后端，并显示实际 Provider 身份。

本批允许修改：Report Workbench 最小选择 UI、主报告调用链、专业 Provider Adapter、后台取消协调、专属测试及审核文档。

本批禁止修改：Word/PPT Builder 内部排版、Report Bridge 生产协议、Skill Runtime Service/Worker、安全发布策略、技能安装器和模板标准化算法。

## 2. 架构审核

- `ReportRenderProvider` 是稳定 **Interface**；Builtin Builder 与隔离 Skill Runtime 是两条真实 **Implementation**。
- `BuiltinReportRenderProvider` 和 `SkillReportRenderProvider` 是同一渲染 **Seam** 上的两个 **Adapter**。
- `ReportProviderController` 是负责 fresh-registry 查询、兼容过滤和 fail-closed 实例化的 **Module**。UI 不读取注册表，Runtime 不依赖 QWidget。
- 外部 Adapter 接收结构化报告、标准化模板、Host 批准的语义图片和最小纳入摘要；插件看不到项目根目录、注册表、环境秘密或最终提交路径。
- 外部 Provider 返回 `requires_host_postprocessing=False`，避免 Host 再次注图/追加附件；Builtin 仍保持原有后处理，旧输出路径不变。
- Orchestrator 每次只注册用户明确选择的 Provider，结构上不存在失败后改走 Builtin 的隐式 fallback。
- 该设计增加 **Depth**，同时保持选择状态、执行身份和错误阶段的 **Locality**；复用既有 Runtime/Artifact 安全边界形成 **Leverage**，没有扩张 Builder 或 Bridge 接口。

## 3. 用户可见行为

- 工作台新增“报告生成后端”下拉框。
- 后端按 `docx/pptx` 和 `none/normalized` 模板模式过滤。
- 默认选择 `builtin.report_builder@1.0`；兼容专业后端显示名称与 `provider_id@version`。
- 报告类型、模板状态或技能注册表变化时自动刷新。
- 已明确选择的外部后端若随后被禁用、切换为非 active、不健康、卸载或变得不兼容，原选择保留为“当前不可用”并阻止生成；不会静默切回 Builtin。
- `config.report_provider` 固化本次选择。
- 成功弹窗、状态栏和日志显示实际 `provider_id@version`。
- 外部失败显示所选 ID/version、`status` 和失败 `stage`；不暴露插件 traceback 或项目正文。
- 取消同时到达外部 Runtime 和 ReportWorker；窗口关闭也会取消活动 Provider。每次生成结束后断开临时取消闭包，避免多次生成累积连接。

## 4. 数据与原子提交

- Host 将 FigureManifest 图表及已批准的 Bridge 图片转换为 Provider-neutral `ReportRenderAsset`。
- 当前通用专业后端合同把全部报告清单图片列为 `required_figure_ids`，由 Runtime 强制验证精确覆盖；`supplementary_figure_ids` 当前为空。
- 每项资源只携带安全 ID、媒体类型、语义标签和目标章节；绝对路径只存在于 Host 输入模型，staging 后仅向插件提供相对路径。
- 专业后端只发布一个经内容嗅探和合同验证的 OOXML Artifact；`ArtifactStore.export(overwrite=True)` 原子替换 Host 预创建的同目录临时文件。
- 外层仍执行 OOXML 打开验证、fsync、最终 cancel 检查、`os.link` no-clobber 提交和临时文件清理。

## 5. 测试结果

测试没有使用 skip、xfail、deselect 或弱化断言。

### 5.1 Batch 3.5.4 专属节点

当前 `tests/test_report_provider_selection.py` 共 **17 个唯一节点**，全部通过。覆盖：

- 默认 Builtin、显式外部选择、config 固化；
- 格式/模板模式刷新、无兼容专业后端、运行中锁定；
- stale 选择保留并阻止生成；
- 禁用、非 active、不健康、格式不匹配过滤与 fail-closed；
- Adapter 成功、失败阶段、取消、Artifact 消费与 provenance；
- 外部 Provider 不再触发 Host 二次注图/附件追加；
- 真实 `DataProcessorWindow()` 注册表刷新；
- 成功弹窗、状态栏和日志的实际 Provider 身份；
- Controller -> Skill Adapter -> 隔离 Runtime 子进程 -> ArtifactStore -> PPTX 的真实纵向测试。

纵向测试使用仓库内临时兼容夹具，不冒充真实 PPT Master。

### 5.2 直接相关回归

一次直接集合收集 118 项并通过；扣除当时包含的 14 个专属节点，既有 Provider/Backend Runtime/Catalog/Workbench/模板/UI 回归为 **104 个唯一节点**。

结果：`118 passed`，0 failed，0 skipped，0 xfailed，0 deselected。

### 5.3 报告主链精确回归

未重复执行上一节文件，运行其余 22 个受影响的 Word/PPT、Report Bridge、图表、模板、原子提交和真实主窗口文件：

```text
collected 539
539 passed
0 failed
0 skipped
0 xfailed
0 deselected
1 existing RankWarning from numpy.polyfit
```

最后一次取消处理器/窗口关闭修复后，只定点复核 6 个受影响节点：`6 passed`；没有重跑 539 项集合。

唯一节点算术：17 + 104 + 539 = **660 个唯一通过节点**。

未提前运行 Batch 3.5.5 的全仓最终回归或真实数据视觉验收。

## 6. 静态与安全检查

- `pyright dp_engine/report_provider tests/test_report_provider_selection.py`：0 errors，0 warnings。
- `pyright main.py ui/report_workbench.py`：0 errors；30 个历史 warnings，均位于本批未修改语句，本批新增/修改语句交集为 0。
- `compileall`：正常退出，无输出。
- `git diff --check`：通过，仅显示既有 LF/CRLF 提示。
- 新增生产代码/专属测试中的 skip/xfail/deselect：0 命中。
- API key/password/private key/`ai_models_config.json` 等秘密模式：0 命中；未读取或输出配置密钥文件。
- `rg` 调用者审核确认 `_execute_report_build_transaction` 仅有主窗口和已知测试调用；环境没有 CodeGraph 工具，因此按仓库规则使用 `rg` fallback 并记录。

## 7. Git 范围与哈希

批次起点：status 454、tracked diff-name 50、cached 0。  
批次终点：status 457、tracked diff-name 50、cached 0。  
新增三项由 `skill_backend.py`、专属测试和本审核包解释；未暂存、未提交用户既有改动。

关键起止哈希：

```text
main.py
  start 5830B2535F766BBE1FD199AA0CEBA19547EDE2AA16B2E612E503EC22BD03950F
  end   8AC687998E49802C18DC33E44559E98EAEED4E37E8BBE310281CA5CC950D7550
ui/report_workbench.py
  start FF857B9F6CC71A260B43D54A47085C91639533BAD55106280C827E3AFAD052B2
  end   B358A139B15EB6AACE320D9BEAFF22A48294A432E7A57F8FDDA89742348C2EF2
dp_engine/report_provider/builtin.py
  unchanged A358C6C47356BCC889746516C21650D71F7D523358FED57F1BA15FA5274E34E8
dp_engine/report_provider/orchestrator.py
  unchanged 625FE2DEE9247DD6D279E464BF024A9301745154ECF82A2B8ECFDFAB26C3B50E
dp_engine/report_backend/catalog.py
  unchanged B6A066276E83172165A759E87DEA9A5F8A835005B8CFA6AF80B100C765EFA23E
dp_engine/report_backend/job.py
  unchanged C7A0EC51F476097520BECFCDFC773109F343C49B6B78FD3B8C8998B79918C85B
dp_engine/skills/runtime_service.py
  unchanged B389807BC4BF230999428D594736C01AB96C30438679C8D5FF9BABF37F590B21
dp_engine/skills/registry.py
  unchanged 814720EE2FC46C84A3F254BACC0D63B74A6C5DCDFBAF860B2EDAA659BB9620E9
dp_engine/report_bridge/service.py
  unchanged 186CFEA92F8599EF2A54F2EC1624E70B15B90107E84BDCEEAD0F59C3866F7CF1
dp_engine/report_builder/word_builder.py
  unchanged F5932EF813F54B52C93E449FD4A673A313519AF9474B5DC572D121D90C171931
dp_engine/report_builder/ppt_builder.py
  unchanged 135FBC4E2255FAC2F497AA6E96F2E2728099386A5A9C20632BE501D8CE29F190
utils/report_template_preparation.py
  unchanged 79A834BBE623D179F5C32A0206C063D8AEA7C12DBD5E9C5E954A4E31AE22F5FD
tests/test_report_render_provider.py
  unchanged 45E1D72087FB763724EA6BBCCAB98D5577CA8BF9A09B6FCB40852824545CB662
```

## 8. 封板判断与下一步

Batch 3.5.4 的 UI 状态机、兼容过滤、显式选择、专业 Adapter、隔离执行、失败不回退、取消、provenance 和主链原子提交均已通过审核。

**Batch 3.5.5 仍有硬前置条件：必须获得真实、可审查的 PPT Master 官方仓库或安装包及确定版本。** 在此前不得声称 PPT Master 已安装、已被软件调用或报告视觉质量已达标；通用兼容夹具不能替代真实插件、固定诊断记录和渲染目视验收。
