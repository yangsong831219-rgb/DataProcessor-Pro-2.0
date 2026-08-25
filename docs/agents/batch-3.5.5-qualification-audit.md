# Batch 3.5.5 — PPT Master 2.7.0 候选包资格审计

审计日期：2026-08-06  
分支：`llama-cpp`  
HEAD：`8a0105527857bbefd34426817ccc0cbbf23102b6`  
结论：**INCOMPATIBLE_FOR_DIRECT_INSTALL；未安装、未登记、未调用，不得宣称 PPT Master 已接入。**

## 1. 冻结范围

本次唯一目标是判断用户提供的真实候选包能否作为 DataProcessor Pro 的 `report_backend` 安装并参与 PPT 报告生成。

允许：只读 ZIP 审计、版本/许可证/依赖/入口核对、使用现有安装器和 Manifest 解析器做兼容性测试、记录结论。

禁止：放宽 ZIP 安全阈值；授予 `network`、`subprocess`、`shell`、Node、COM、项目任意读写或环境秘密权限；修改 Runtime、Builder、Report Bridge 或模板标准化器；把重新实现的排版器冒充 PPT Master；向用户 AppData 或技能注册表写入不兼容包。

## 2. 冻结候选物

| 项目 | 值 |
|---|---|
| 文件 | `D:\桌面文件\ppt-master-main.zip` |
| 文件大小 | 632,802,950 bytes（约 603.49 MiB） |
| SHA-256 | `AC2599B467FFF4166EA2C34B62D877B12683391FEB95DE7A8FFCC7892EFFD7AF` |
| 包内声明版本 | `2.7.0` |
| 包内声明仓库 | `https://github.com/hugohe3/ppt-master` |
| 包内许可证 | MIT，Copyright Hugo He 2025–2026 |
| 交付形态 | GitHub `main` 源码仓库快照；无 Git commit 元数据，故以完整归档哈希锁定 |

## 3. ZIP 只读安全审计

中央目录检查结果：

- 13,746 个条目；总解压大小 705,145,737 bytes；压缩数据 629,352,130 bytes；总体压缩比约 1.12。
- 最大单文件 36,755,135 bytes。
- 路径穿越/绝对路径 0；加密条目 0；符号链接 0；重复路径 0。
- 源归档本身未出现 ZIP slip、加密、symlink 或异常压缩比信号。

这不等于可安装。DataProcessor 默认安装边界是归档最多 200 MiB、条目最多 10,000；候选包同时超过两项。使用生产 `inspect_archive()` 的实测首个拒绝为：

```text
SkillPackageLimitError:
Archive file too large: 632802950 bytes (limit: 209715200 bytes)
```

不得为了该源码仓库快照修改全局限制。

## 4. Manifest 兼容性测试

候选包提供的是 Claude 插件/技能元数据：

- `.claude-plugin/marketplace.json`
- `skills/.claude-plugin/plugin.json`
- `skills/ppt-master/SKILL.md`

其中 `SKILL.md` Front Matter 只有 `name` 和 `description`。将原文直接交给 DataProcessor 的生产 `parse_skill_front_matter()`，实测拒绝为：

```text
SkillManifestError:
SKILL.md missing required fields: skill_id, entrypoints, skill_type,
schema_version, capabilities, version.
```

候选包也没有声明下列 DataProcessor 专用合同：

- `skill_type: report_backend`
- `entrypoints.generate: relative.py:generate`
- `report_backend_contract: 1`
- `artifact_types: [pptx]`
- `template_modes: [none, normalized]`
- 最小权限集合 `read_skill_files/read_runtime_workspace/write_runtime_workspace`

因此即使忽略归档大小，安装器仍必须在 Manifest 门禁拒绝。

## 5. 可调用性审计

对 `skills/ppt-master` 下 196 个 Python 文件进行只读扫描，没有发现以下任何 DataProcessor 报告后端入口或协议标志：

- `def generate(context)`
- `ArtifactRunContext`
- `report_job.json`

上游 `workflows/routing.md` 把新建报告路由到 `Generate PPTX`。其 `generate-pptx.md` 明确规定：

- 当前主智能体逐页手写完整 SVG；不得使用 Python、Node 或 shell 批量生成页面。
- 需要三阶段用户确认和本地 Confirm UI。
- 执行期必须启动长期运行的本地 SVG Preview 服务。
- 最终再依次运行项目管理、质量检查、SVG finalize 和 SVG-to-PPTX 脚本。

所以该项目是“给智能体执行的交互式工作流 + 工具链”，不是“接收结构化报告 Job 并返回一个 PPTX 的无头服务/API”。仅把脚本复制进包并编写一个薄 `generate()` 外壳，无法实现其规定的 Strategist、确认、逐页设计与 Executor 流程，也不能证明报告使用了 PPT Master 的专业设计能力。

## 6. 权限与依赖边界

候选技能的 `requirements.txt` 包含 `python-pptx`、PyMuPDF、Pillow、NumPy、Flask、requests、`google-genai` 等依赖，工作流还包含本地服务、可选联网研究/素材获取和多个命令行步骤。

DataProcessor 首版 `report_backend` 刻意只允许：

- 读取技能自身文件；
- 读取本次托管 Runtime 工作区；
- 写入本次托管 Runtime 工作区。

它禁止网络、shell、任意子进程、Node/COM、项目目录任意访问和环境秘密。候选工作流的交互与执行模型不在这个最小权限合同内。扩大权限属于新的安全架构批次，不能作为 3.5.5 的安装兼容补丁。

## 7. 安装/调用判定

| 门禁 | 结果 | 判定 |
|---|---|---|
| 来源、版本、许可证可审计 | 有包内证据，归档哈希已锁定 | PASS（但无 commit 元数据） |
| ZIP 安装限制 | 603.49 MiB、13,746 条目 | FAIL |
| DataProcessor Manifest | 缺少 6 个必填字段及 report_backend 扩展 | FAIL |
| 无头生成入口 | 无 `generate(context)` | FAIL |
| 最小权限运行 | 交互 UI/工具链与当前 Runtime 合同不匹配 | FAIL |
| 无模板/标准化模板声明 | 无 DataProcessor `template_modes` | FAIL |
| 可执行健康检查 | 无兼容 healthcheck 入口 | FAIL |

最终判定：**该 ZIP 不能由当前技能插件中心安全、真实地安装成 PPT 报告后端。拒绝是正确行为，不是插件中心故障。**

## 8. 本次状态变化

- 未解压候选仓库到项目或用户目录。
- 未修改全局包限制、Manifest 规则、Runtime 权限、依赖或报告主链。
- 未写入用户 AppData、技能注册表或后端选择配置。
- 未生成测试 PPTX，未冒充安装/调用成功。
- 本次没有生产代码改动，因此没有重复执行既有 660 节点回归，也没有提前执行最终全量回归。

## 9. 可行的下一条开发路径

若仍要在 DataProcessor 内获得 PPT Master 级别的深度处理，需要另立“Host Agent Orchestrator”安全架构批次，而不是把当前 ZIP 当作 `report_backend`：

1. Host 负责模型调用、三阶段确认、会话状态和逐页设计；插件 Runtime 不接触模型密钥。
2. 用单独受控 Runner 明确允许的 Python 工具、长期本地预览服务、超时/取消/资源上限和审计日志；仍不默认开放网络、shell、Node 或 COM。
3. 以归档哈希固定上游 2.7.0 工具链，制作最小再分发包，只纳入当前路由真正需要且许可证允许的文件；不能包含示例仓库和 11,760 个图标的全量负担。
4. DataProcessor 的结构化报告、语义图表清单和标准化模板需转换成 PPT Master 的 `design_spec.md/spec_lock.md/SVG` 生命周期。
5. 只有无模板与标准化模板真实数据 E2E、逐页渲染目视审核、输出结构/图表覆盖/模板继承/原子提交及一次最终全量回归全部通过，才能标记可用。

该路径是一个新的产品能力和安全边界，不能在 Batch 3.5.5 内以“最小兼容适配包”名义偷渡。
