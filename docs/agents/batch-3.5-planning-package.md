# Batch 3.5 — 专业 Word/PPT 报告后端接入规划包

**日期**：2026-08-05  
**分支**：`llama-cpp`  
**当前子批次**：Batch 3.5.0  
**状态**：纯规划与合同冻结；尚未授权修改生产代码  
**优先目标**：让技能插件中心安装的专业报告后端能够被报告生成主链真实调用，并且可证明“确实使用了所选后端”

> 本文件是 Batch 3.5 的本地范围冻结提案。GitHub Issues 当前不可读取：本机无 `gh`，GitHub 插件不可用，仓库网页也未能访问。因此本文不声称读取、创建或更新了任何远程 Issue。远程恢复后，应将 Section 14 的 Issue 草案同步到 GitHub，再开始生产代码子批次。

---

## 0. Batch 3.5.0 本批合同

### 0.1 单一目标

在不修改报告生成主链的前提下，完成 `report_backend` 的现状审计，冻结专业 PPT/Word 后端的接口、安全边界、模板行为、失败语义、测试序列和子批次拆分。

### 0.2 允许修改范围

- 新增本文件：`docs/agents/batch-3.5-planning-package.md`

### 0.3 禁止修改范围

- `main.py`
- `ui/` 全部生产代码
- `dp_engine/skills/` 全部生产代码
- `dp_engine/report_bridge/` 全部
- `dp_engine/report_builder/` 全部
- 任何测试、配置、依赖、注册表和用户 AppData
- 安装 PPT Master 或任意 Word/PPT 技能
- 创建、修改或声称修改 GitHub Issues

### 0.4 验收

1. 本文件明确回答“能否安装”“能否调用”“如何接入”“模板是否自动兼容”“Word 是否值得同时支持”。
2. 子批次均有单一目标、允许/禁止范围、输入/输出合同、测试和停止条件。
3. 不把测试夹具 `ppt-master` 误写成真实可用插件。
4. Git 最终范围只有本文件这一项新增规划文件；既有脏工作树内容保持不变。

---

## 1. 起点与已验证基线

### 1.1 Git 起点

```text
branch = llama-cpp
HEAD   = 8a0105527857bbefd34426817ccc0cbbf23102b6
Batch 3.5.0 start status entries = 439
Batch 3.5.0 start diff-name entries = 50
Batch 3.5.0 start cached entries = 0
```

工作树在本批开始前已经高度脏化，全部既有变化视为用户资产。本批不得清理、还原、暂存或重写这些变化。

### 1.2 最近一次测试基线

沿用刚完成的 Batch 3.4.3 证据，不重复执行全量测试：

```text
collection = 2430
full run   = 2429 passed, 1 transient failure
targeted retry of the only failure = passed
aggregate closure = 2430 / 2430
```

唯一瞬时失败为 `tests/test_runtime_l2_subprocess.py::TestHealthcheckErrors::test_cancellation`，同一节点随后的定点复核通过。Batch 3.5.0 不修改 Python，因此不得用重复全量测试制造新的“绿灯”证据。

### 1.3 环境限制

- 项目 `venv` 指向已不存在的 Python 3.11.9；当前可用解释器为 `C:\Python314\python.exe`。
- 全量测试包含子进程和临时目录安全边界，后续执行时可能需要沙箱授权。
- `tests/test_phase_b_guard_regression.py` 是带模块顶层 Qt 交互的历史脚本，标准 headless collection 会被首个模态框阻塞；后续只允许使用已验证的精确 collection hook，不允许 deselect/skip/xfail。

---

## 2. 只读调查结论

### 2.1 安装能力：有，但只是包级安装

现有技能清单模型接受三种类型：`instruction`、`executable`、`report_backend`。安装器会解析清单、校验包、复制到托管目录并写入注册表；技能中心也会展示 `skill_type` 和 `artifact_types`。

因此，**格式正确的 `report_backend` 包可以通过技能中心安装和登记**。

但当前仅存在测试夹具：

```text
tests/fixtures/skills/valid_report_skill/SKILL.md
skill_id: ppt-master
skill_type: report_backend
artifact_types: [pptx]
entrypoints.generate: workflows/generate.py
source_url: https://github.com/example/ppt-master
```

其中 URL 是 `example`，夹具只证明清单/安装解析，不是实际 PPT Master 产品，也不证明能生成 PPT。

本机应用技能注册表当前不存在，等价于没有任何真实技能安装记录；本批没有创建它。

### 2.2 调用能力：当前不具备

当前 SkillRuntime 的事实：

- `ALLOWED_OPERATIONS = {"healthcheck", "run"}`；
- `run_skill()` 强制读取 `entrypoints.run`；
- Worker 只分派 `healthcheck` 和 `run`；
- `entrypoints.generate` 不会被调用；
- `run` 只授予 `read_skill_files`、`read_runtime_workspace`、`write_runtime_workspace`；
- `network`、`subprocess`、`shell`、项目目录读写和环境秘密均被禁止。

所以当前真实结论是：

> `report_backend` 可以“安装并显示”，但不能被报告生成主链调用。即使安装一个只声明 `generate` 的 PPT Master，当前也不会参与报告生成。

### 2.3 Artifact 能力：输出格式基础已经存在

Runtime Artifact 已允许 `.pptx` 和 `.docx`，并会校验：

- 扩展名白名单；
- OOXML ZIP 结构及 `ppt/` 或 `word/` 目录；
- 单文件 50 MB、单次总计 200 MB；
- 哈希、大小、普通文件、非符号链接和托管路径；
- 原子发布与安全导出。

这部分可复用，但“允许发布文档”仍不等于“主报告采用该文档”。

### 2.4 Report Bridge 不是报告后端执行器

Report Bridge 仅接收并准备 `image/table_source/text_source`，支持 PNG/JPEG/CSV/JSON/TXT，然后由内置 Builder 确定性地追加到 Word/PPT。它：

- 不调用技能；
- 不生成最终 `.docx/.pptx`；
- 不做 LLM 排版；
- 当前 PPT 适配器会把素材页追加到末尾，这正是用户已指出的质量问题之一。

因此 Batch 3.5 **不得把 Report Bridge 政名或硬改成报告后端执行器**。它继续作为素材入口，专业后端接入必须建立独立服务。

### 2.5 当前报告主链

生产路径为：

```text
Report Workbench 配置
  -> main.py 生成结构化 WordReport / PPTReport
  -> 直接实例化 WordBuilder / PPTBuilder
  -> 图表引用注入
  -> 资料纳入段
  -> OOXML 校验 + fsync
  -> os.link 原子 no-clobber 提交
```

主链已经具备模板预检、临时文件、取消检查、OOXML 校验和原子提交。专业后端必须插入“结构化报告 -> 临时 OOXML”这一渲染位点，不能绕过最终校验与原子提交。

### 2.6 模板能力已存在，不应重复开发

工作台和 `utils/report_template_preparation.py` 已同时支持 `.docx` 与 `.pptx`：检测、拒绝、缓存和标准化均已实现。Batch 3.5 不再开发另一套模板检测器。

需要新增的是后端声明：它是否支持无模板、标准化模板，以及是否真的遵循模板的母版/主题/样式。

### 2.7 Codex 技能与应用技能不可混同

Codex 环境中的 Presentations/Documents 技能可帮助开发者制作和审核文件，但不会自动出现在 DataProcessor Pro 的技能插件中心，也不能被桌面软件运行时直接调用。若要在软件内使用，仍需封装成符合本规划 `report_backend` 合同的可安装包。

---

## 3. 架构决策

### 3.1 采用单一多格式 Provider 架构

推荐建立一个报告渲染 Provider 层，而不是分别把 PPT Master 逻辑塞进 `PPTBuilder`，再为 Word 重做一次：

```text
结构化报告 + 已规划图表 + 标准化模板
                  |
          ReportRenderProvider
            /             \
 BuiltinReportProvider   SkillReportBackendProvider
   (现有 Builder)          (安装的 report_backend)
            \             /
        输出校验 + 原子提交
```

核心接口同时支持 `pptx` 和 `docx`，但实施顺序先 PPT、后 Word。这样 Word 支持的边际成本低，且不迫使一个 PPT 专用插件假装会处理 Word。

### 3.2 内置 Provider 是兼容基线

`BuiltinReportProvider` 只包装现有 `WordBuilder/PPTBuilder` 调用，不改变其渲染行为。未选择外部后端时，现有报告生成结果和原子提交语义必须保持一致。

### 3.3 外部 Provider 必须显式选择

首版 UI 仅提供：

- `内置报告引擎（默认）`
- 已安装、启用、active、健康且兼容当前格式/模板模式的专业后端

首版不提供“自动选择”。用户显式选择专业后端后，如果预检或生成失败：

- 不得静默回退到内置引擎；
- 不得创建最终报告；
- 必须显示后端 ID、版本、失败阶段和安全化错误；
- 用户可自行改回内置引擎后重新生成。

这保证界面不会声称使用了 PPT Master，实际却生成了内置 PPT。

### 3.4 不允许插件直接读取项目任意路径

Host 创建单次托管工作区，只复制本次生成所需的安全输入：

```text
workspace/
  input/
    report_job.json
    template.pptx | template.docx      # 可选，且必须已标准化
    assets/<host-id>.<safe-ext>
  output/
    report.pptx | report.docx
```

插件只拿相对路径和工作区能力，不获取：

- 原始项目根路径；
- 用户任意文件路径；
- API Key、模型配置或完整环境变量；
- 网络、shell 或任意子进程能力。

如果真实 PPT Master 是 Node/CLI 工具，必须进入独立安全评审批次；不能因为“专业”就自动授予 `subprocess/shell/network`。

---

## 4. 冻结的数据合同

### 4.1 Manifest 语义

`skill_type: report_backend` 必须额外满足：

1. `entrypoints.generate` 存在且指向包内 Python 文件/函数；
2. `artifact_types` 至少包含 `pptx` 或 `docx`，不得包含未知报告格式；
3. 由未知 Front Matter 字段保存到 `SkillManifest.extensions` 的 `report_backend_contract` 精确为 `1`；
4. 同样保存到 `SkillManifest.extensions` 的 `template_modes` 是 `none`、`normalized` 的非空子集；
5. 不满足者在安装预检阶段明确拒绝，不能等到生成时才报“无入口”。

`generate` 使用 Runtime 已定义的可调用入口格式 `relative/module.py:function_name`。现有 Package Validator 仍把整个入口字符串当文件路径检查，Batch 3.5.1 必须只对需要可调用语义的入口解析 module 部分再检查文件；路径穿越和包外解析仍 fail closed。

示例：

```yaml
skill_type: report_backend
artifact_types: [pptx]
capabilities:
  - read_skill_files
  - read_runtime_workspace
  - write_runtime_workspace
entrypoints:
  healthcheck: workflows/health.py:check
  generate: workflows/generate.py:generate
report_backend_contract: 1
template_modes: [none, normalized]
```

### 4.2 `ReportBackendJobV1`

`input/report_job.json` 必须包含：

- `schema_version = 1`
- `report_type = ppt | word`
- `output_artifact_type = pptx | docx`
- `report`：完整的 `PPTReport` 或 `WordReport` 结构化内容
- `template`：空值或标准化模板的相对路径及 SHA-256
- `assets`：host ID、相对路径、SHA-256、媒体类型、语义标签、目标章节/幻灯片
- `required_figure_ids`：必须进入正文的图表集合
- `supplementary_figure_ids`：允许进入附录的集合
- `inclusion_summary`：资料纳入说明
- `expected_output`：固定为 `output/report.pptx` 或 `output/report.docx`

禁止字段：原始绝对路径、密钥、模型凭据、任意命令字符串、任意环境变量。

### 4.3 运行时操作

新增专用操作 `generate_report`，而不是把 `generate` 偷塞进普通 `run`：

- Service 公共入口：`run_report_backend(...)`
- Manifest 入口：`entrypoints.generate`
- Worker 分派：`generate_report`
- 状态：复用经过验证的 run 状态集合，并在 detail 中增加安全化阶段码
- 取消、超时、日志截断、参数脱敏、工作区清理、Artifact 发布继续复用现有 Runtime 机制

普通 `run_skill()` 行为不得变化。

### 4.4 输出合同

成功必须同时满足：

1. 返回成功状态；
2. 声明且仅声明一个 `kind=document` 的主报告 Artifact；
3. 扩展名和实际 OOXML 内容与请求格式一致；
4. 主报告位于固定 `output/report.*`；
5. 返回 `provider_id/provider_version/contract_version`；
6. 返回 `placed_figure_ids`、`supplementary_figure_ids` 和警告；
7. `placed_figure_ids` 覆盖全部 `required_figure_ids`，无重复、无未知 ID。

Host 再验证 Artifact 所有者、哈希、大小和 OOXML 结构，然后安全复制到现有 `.dp-report-*` 临时文件。现有 fsync、最终取消检查与 `os.link` no-clobber 提交不得绕过。

---

## 5. 图表与内容放置合同

### 5.1 语义位置优先

报告后端必须使用 Host 已生成的图表目录和语义规划，不得自行扫描 `charts/` 猜测文件。以 manifest/figure catalog 中明确标记为“本报告必需”的集合为准，不能拿目录 PNG 数量代替合同数量。

### 5.2 PPT

- 每个 required figure 必须出现在语义匹配的正文页；
- 不得把所有图片统一作为末尾附件；
- 同一图不得因 Builder 后处理再次注入；
- 只有 `supplementary_figure_ids` 可进入附录；
- 图表页需保留结论标题、解释要点和来源/图注；
- 阶段 B 原始与补偿后两版诊断四联图必须作为两个独立图表 ID 跟踪。

### 5.3 Word

- 图片紧跟首次解释它的章节/段落锚点；
- 图注与图片保持相邻，图号稳定；
- 表格进入对应章节，不得统一堆到文末；
- 只有明确标记为附录的素材可进入附录；
- unresolved `[INSERT_IMAGE: ...]` 为硬失败。

---

## 6. 模板行为

### 6.1 任意模板的自动路径

用户上传模板后仍走既有流程：

```text
扩展名识别 -> 结构检测 -> 自动标准化 -> 缓存 -> Provider 能力匹配
```

无需人工逐个评估模板：

- 检测/标准化失败：立即提示模板不可用；
- 后端未声明 `normalized`：该后端在当前选择中不可用，并解释原因；
- 后端声明支持：自动传入标准化模板，生成后再执行结构和视觉验收；
- 生成后发现母版/样式不符合合同：本次失败，不提交最终报告，并将该后端标记为兼容性失败，而不是悄悄换样式。

### 6.2 “按模板形式生成”的可测含义

PPT 模板模式至少验证：

- 幻灯片尺寸保持一致；
- 母版/版式引用有效；
- 主题字体与主题色可解析；
- 标题/正文占位符或标准化语义布局被使用；
- 不插入模板样例业务内容；
- 无模板时使用后端自身声明的稳定主题。

Word 模板模式至少验证：

- 页面尺寸、页边距、页眉页脚保留；
- Normal/Heading/Caption 等标准样式被报告内容使用；
- 模板样例正文已清理；
- 分节与分页合法；
- 无模板时使用后端自身声明的稳定样式集。

---

## 7. PPT Master 可行性判定

### 7.1 当前结论

**架构上可行，当前产品上尚不可用。**

原因不是安装按钮，而是缺少：真实插件包、专用生成运行时、主链 Provider 调度、模板能力声明、输出验证和可观测性。

### 7.2 真实插件兼容门槛

在拿到 PPT Master 的官方仓库/安装包后，只做一次兼容性审查：

1. 来源、版本、许可证与再分发限制；
2. 是否提供可调用入口，而非仅供人阅读的提示词文档；
3. Python 进程内可否运行；
4. 是否强制 Node、PowerPoint COM、LibreOffice、shell、网络或外部模型；
5. 是否支持接收既有结构化内容、图片和标准化模板；
6. 是否能输出本地 `.pptx`；
7. 是否能返回图表放置清单；
8. 是否能在无秘密、无项目任意读写条件下工作。

若它只是 Codex/Claude 的说明型 skill，而没有稳定的程序入口，则不能直接装入应用运行时。可选方案是为其工作流编写一个受控 adapter 包，但 adapter 仍必须通过上述合同和测试。

### 7.3 禁止假设

- 不假设名为 PPT Master 的第三方项目一定存在或可再分发；
- 不假设测试夹具就是该产品；
- 不假设安装成功等于 healthcheck 成功；
- 不假设 healthcheck 成功等于报告主链已调用；
- 不因插件要求而默认开放网络、shell 或环境密钥。

---

## 8. Word 专业美化支持的性价比

结论：**高，纳入同一 Batch 3.5 架构，但在 PPT 纵向闭环之后实现。**

理由：

- Provider、工作区、Artifact、选择 UI、失败语义和原子提交约 80% 可复用；
- Word 只需新增 `docx` 能力、Word 结构/视觉验证及真实候选后端；
- 不需要建立另一套“Word 技能中心”；
- 当前没有已确认可在应用内运行的“Word Master”候选，不能先安装再找用途。

一个 PPT-only 后端不会因公共架构支持 Word 而被强迫处理 Word；UI 只在格式匹配时列出它。

---

## 9. 子批次实施序列

### Batch 3.5.1 — Manifest 语义与后端目录

**单一目标**：让系统能准确判断一个已安装技能是否是“可候选”的 PPT/Word 报告后端，但不执行它。

**允许修改**：

- `dp_engine/skills/models.py`
- `dp_engine/skills/manifest_parser.py`
- `dp_engine/skills/package_validator.py`
- 新增 `dp_engine/report_backend/models.py`
- 新增 `dp_engine/report_backend/catalog.py`
- 对应新测试与 report_backend fixtures

**禁止修改**：Runtime Service/Worker、`main.py`、Report Workbench、Report Bridge、Word/PPT Builder。

**输出**：格式、合同版本、模板模式和兼容状态均可确定性查询；旧 instruction/executable 技能行为不变。

**停止条件**：专属测试、Installer 哨兵、Pyright、compileall、Git 范围审核通过。

### Batch 3.5.2 — 隔离生成协议与安全发布

**单一目标**：实现 `generate_report` 的 headless 运行时闭环，尚不接 UI/主报告。

**允许修改**：Runtime models/protocol/service/worker/artifacts 的最小必要文件、新增 `dp_engine/report_backend/job.py` 和专属测试夹具。

**禁止修改**：`main.py`、Report Workbench、Report Bridge、Word/PPT Builder。

**输出**：安全工作区 staging -> 插件 generate -> 单一文档 Artifact -> Host 验证/消费；取消、超时、崩溃、越权和伪造 OOXML 均 fail closed。

**停止条件**：新协议与 L1/L2/L3 Runtime 安全回归通过，普通 `run` 行为零回归，Pyright/compileall/Git 范围通过。

### Batch 3.5.3 — Provider 调度与内置兼容路径

**单一目标**：将报告渲染位点改为 Provider 调度，先接通 Builtin Provider，并证明输出行为等价。

**允许修改**：新增 provider/orchestrator 文件，`main.py` 的最小渲染分派位点，以及专属测试。

**禁止修改**：现有 Builder 内部排版、Report Bridge 合同、模板标准化算法、SkillRuntime 安全策略。

**输出**：默认仍走内置 Provider；现有临时文件、图表注入、资料纳入、校验和原子提交保持；Provider provenance 可记录。

**停止条件**：内置路径结构等价测试、原子/no-clobber/cancel 回归及相关报告测试通过。

### Batch 3.5.4 — 工作台后端选择与专业后端接通

**单一目标**：用户可在报告工作台选择一个已兼容的后端，并由主链真实调用它。

**允许修改**：Report Workbench 的最小选择 UI、主链调用、后台 Worker/controller、新专属测试。

**禁止修改**：Builder 内部排版、Report Bridge 既有协议、安装器安全边界。

**输出**：按格式/模板模式过滤后端；选择被写入 config；生成成功弹窗和日志显示实际 provider ID/version；失败不静默回退。

**停止条件**：UI 状态机、成功/失败/取消、无后端、禁用/非 active/不健康/格式不匹配测试全部通过。

### Batch 3.5.5 — 真实 PPT Master 兼容适配与视觉验收

**前置条件**：必须提供真实、可审查的 PPT Master 官方仓库或安装包；未满足则本批保持 blocked，不以测试夹具代替。

**单一目标**：只适配该确定版本，完成无模板与标准化 PPT 模板两条真实生成路径。

**输出**：安装、healthcheck、选择、生成、图表语义放置、模板遵循、渲染 QA、原子提交全闭环。

**停止条件**：一次端到端真实数据验收 + 一次最终全量回归；外部审核通过后才可封板。

### Batch 3.5.6 — Word 专业后端纵向闭环

**前置条件**：存在满足合同的真实 Word 后端候选；没有候选时不编造插件。

**单一目标**：复用公共 Provider/Runtime，只补齐 docx 适配、UI 能力匹配和 Word 视觉验收。

**停止条件**：无模板/标准化模板两条路径、图表/表格语义位置、分页/样式/渲染 QA、一次 Word 闭环最终全量回归全部通过。

---

## 10. 测试与审核合同

### 10.1 不重复测试规则

- 每个子批次先运行新增/直接相关测试；
- 修改 Runtime 时运行 Runtime 精确回归；修改报告主链时运行报告精确回归；
- PPT 纵向闭环的全量测试只在 Batch 3.5.5 封板前执行一次；若之后代码未变，不重复；
- 如果可选的 Batch 3.5.6 随后修改了 Word 生产代码，则在 Word 闭环封板前再执行一次新的全量测试；同一闭环内不重复；
- 若全量仅出现单一疑似瞬时失败，只定点复核该节点，并记录“全量结果 + 定点结果”，不重跑全仓；
- 禁止 skip、xfail、deselect 或缩小 collection 伪造绿灯；
- 每一批必须先过 Pyright（修改文件）与 compileall。

### 10.2 结构测试

PPTX：

- OOXML 可打开，slide/master/layout/theme 关系合法；
- 页数、标题、speaker notes、图表 ID 覆盖符合 job；
- 无未解析锚点；
- 图片不越界、不畸变、无意外重复；
- 模板样例内容不存在；
- provider provenance 与实际选择一致。

DOCX：

- OOXML 可打开，段落/表格/图片数量符合 job；
- Heading/Normal/Caption 样式可用；
- 图与图注相邻，表与表题相邻；
- 无未解析锚点、模板样例内容或空白附件章；
- provider provenance 与实际选择一致。

### 10.3 安全测试

- 包外入口、路径穿越、symlink/reparse、绝对路径、硬链接替换；
- 伪造扩展名、损坏 ZIP、docx/pptx 类型互换；
- 输出缺失、多主文档、未知 Artifact、超限文件；
- 项目目录读写、注册表读写、环境秘密、网络、shell、subprocess 越权；
- 取消/超时/进程崩溃后无最终文件，工作区按策略清理；
- 日志和错误不泄露密钥、原始绝对项目路径或用户资料正文。

### 10.4 视觉验收

专属 fixture 应覆盖：封面、目录/章节、正文、表格、单图、双图、长标题、长要点、中文/英文、无模板、标准化模板。

生成后只做一次完整渲染审核：

1. LibreOffice headless 转 PDF；
2. PDF 渲染全部页面/幻灯片为 PNG；
3. 自动检查空白页、越界、重叠、最小字号、图片纵横比和内容覆盖；
4. 主代理目视审核全部渲染页；
5. 任一页不合格则修复并只重测受影响的专属用例，最终封板再执行一次全量集合。

PPT 的最低专业门槛：统一网格与边距、清晰层级、正文不溢出、图表与结论同页、无“全部附件置后”、模板主题可辨认。  
Word 的最低专业门槛：标题层级一致、正文可读、图表在论述处、图表题连续、表格不越页宽、无孤立标题和无意义空白页。

### 10.5 真实数据验收

只在真实后端接通后执行一次，使用一份固定诊断记录及其冻结 chart manifest，不直接以 `charts/` 目录文件数作期望值。验收输出必须给出：

- manifest 中 required/supplementary/omitted 数量；
- 报告实际嵌入图表 ID；
- 原始/补偿后阶段 B 四联图的独立覆盖；
- 多源对比图覆盖；
- 无模板与标准化模板结果；
- 实际 provider ID/version；
- 结构、视觉和原子提交结论。

---

## 11. 风险与止损

| 风险 | 处置 |
|---|---|
| “PPT Master”只有提示词，没有程序入口 | 判 incompatible；不得伪装接入 |
| 插件必须调用网络/外部模型 | 首版拒绝；另立凭据与网络安全设计批次 |
| 插件必须 shell/Node/COM | 首版拒绝；单独评审最小受控 runner |
| 插件产出的内容缺图或全在附件 | 图表覆盖合同失败，不提交 |
| 插件不遵循模板 | 模板结构/视觉验收失败，不提交 |
| 外部后端失败后用户仍拿到报告 | 禁止静默 fallback；保持零 final |
| 专业后端破坏现有用户 | Builtin Provider 默认且必须保持等价 |
| 把 Report Bridge 再次当附件追加器 | 专业 Provider 直接消费语义资产计划，不二次追加 |
| 单个 PPTX 超过当前 50 MB Artifact 限制 | 兼容探针先测；不得未经安全评审直接放宽 |

---

## 12. 明确 Out of Scope

- 本批安装任何第三方插件；
- 动态把已安装技能自动注入 AI Agent；
- 允许插件直接调用 DeepSeek/Qwen 或读取模型密钥；
- 允许任意网络、shell、PowerShell、Node、COM 或 Office 自动化；
- 重写 WordBuilder/PPTBuilder 的现有排版；
- 修改 Report Bridge 已冻结合同；
- 重新开发 Word/PPT 模板检测与标准化器；
- 对未知 PPT Master 版本做猜测式适配；
- 创建在线插件市场或远程下载服务。

---

## 13. 通过/失败判定

Batch 3.5 最终只有同时满足以下条件才可宣称完成：

1. 至少一个真实 PPT report_backend 从技能中心安装；
2. healthcheck 和专用 generate_report 均通过；
3. 报告工作台可明确选择它；
4. 生成日志和报告 provenance 证明实际使用它；
5. required 图表全部在语义位置，非统一附件；
6. 无模板与标准化模板两条路径通过；
7. 结构、安全、视觉、真实数据和最终全量回归通过；
8. 没有静默 fallback、skip/xfail/deselect 或秘密泄漏；
9. 外部审核通过。

若真实插件包仍未提供，最多只能封板到 Batch 3.5.4 的通用基础设施，**不得宣称 PPT Master 已可用**。

---

## 14. GitHub Issues 草案（尚未同步）

远程恢复后建议创建以下纵向 Issue，并使用仓库默认五阶段标签：

1. `Batch 3.5.1 — Freeze report_backend manifest semantics and catalog`
2. `Batch 3.5.2 — Add isolated generate_report runtime contract`
3. `Batch 3.5.3 — Introduce report render providers with builtin parity`
4. `Batch 3.5.4 — Add explicit report backend selection and provenance`
5. `Batch 3.5.5 — Qualify and integrate real PPT Master package`
6. `Batch 3.5.6 — Qualify and integrate a Word report backend`

每个 Issue 应引用本文件对应子批次，并保留“一个 Issue 只实现一个子批次”的边界。同步前不得把这些草案当作已经存在的 GitHub Issues。

---

## 15. Batch 3.5.0 决策摘要

1. 技能中心的包安装链可继续使用；问题在“生成运行时 + 主链 Provider 调度”。
2. 采用一个多格式 Provider 架构，PPT 优先、Word 随后，性价比高。
3. 外部后端只能读托管工作区，输出必须经过 Artifact 与现有原子提交。
4. 显式选择、失败关闭、零静默回退，是证明专业后端真实参与的关键。
5. 既有 Word/PPT 模板检测与标准化保留；通过后端能力声明和生成后 QA 实现任意模板自动判定。
6. 真实 PPT Master 未提供前不安装、不假装兼容；测试夹具不是产品。
7. Batch 3.5.0 通过后，下一步应是 Batch 3.5.1，而不是直接修改 PPT 排版或安装未知插件。

---

## 16. Batch 3.5.0 自审记录

**自审结论**：PASS；纯规划目标已完成，可提交用户审核。

- 文档必需章节、关键结论、代码围栏和 Tab 检查通过；
- Git HEAD/branch 未变化；
- 起点 `--untracked-files=all` 状态 439 项，结束 440 项，增量恰为本规划文件；
- tracked diff-name 仍为 50，cached 仍为 0；
- `models.py`、Runtime Worker、Report Bridge Service、Report Workbench、Word/PPT Builder、Skill Tab 的 SHA-256 与本批起点一致；
- 未修改 Python、测试、配置、依赖、注册表或用户 AppData；
- 按“不重复测试”规则未重跑 Batch 3.4.3 全量集合；本批仅执行文档合同断言和 Git/哈希范围审计。

Batch 3.5.1 的生产代码开始条件仍为：本规划获用户确认，并在 GitHub 入口恢复后把 Section 14 的草案同步为真实 Issue；同步前只能继续本地只读分析，不能声称已有远程任务。
