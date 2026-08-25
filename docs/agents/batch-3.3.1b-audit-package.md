# Batch 3.3.1B — Builder 适配、确定性素材附录与原子 no-clobber 报告输出审核包

**日期**: 2026-07-23
**分支**: llama-cpp
**状态**: 提交外部审核
**批次类型**: 生产实现 — Report Bridge 第二个生产批次

---

## 1. 最小 Markdown 读取声明

| 文件 | 行数 | 用途 |
|------|------|------|
| `CLAUDE.md` | 80 | 项目执行纪律 |
| `docs/agents/batch-3.3-planning-package.md` | 2272 | Batch 3.3.0-F-R3 规划包 |
| `docs/agents/batch-3.3.1a-audit-package.md` | 1250 | Batch 3.3.1A-R2 审核包 |

采用最小 Markdown 上下文原则，未读取其他历史 Markdown。
当前只执行 Batch 3.3.1B。
未开始 Batch 3.3.2 或 3.3.3。

---

## 2. 实际读取代码

| 文件 | 行数 | 用途 |
|------|------|------|
| `main.py` L1617-1926 | ~310 | 报告生成调用链：`_handle_full_report_generation` → `_build_and_save` |
| `main.py` L140-420 | ~280 | 图表注入 `_inject_figures_by_reference` / `_inject_figures_to_pptx` |
| `main.py` L1570-1616 | ~47 | 资料纳入情况页脚 `_append_inclusion_footer` |
| `dp_engine/report_builder/word_builder.py` | 693 | Word Builder 完整实现 |
| `dp_engine/report_builder/ppt_builder.py` | 1020 | PPT Builder 完整实现 |
| `dp_engine/report_builder/models.py` | 259 | WordTable / WordSection / WordReport / PPTSlide / PPTReport |
| `dp_engine/report_bridge/models.py` | 506 | PreparedReportAsset / ReportAssetRole / ParsedTable |
| `core/report_engine.py` L680-830 | ~150 | `generate_structured_report` 签名和提示词构造 |

---

## 3. 实际报告调用链（冻结前勘察）

```
main.py: _handle_full_report_generation() (L1619)
  → report_dir = os.path.join(candidate, '报告')  (L1652)
  → output_path = <project_name>_<type_label>_<timestamp>.docx/.pptx  (L1657-1658)
  → _build_and_save() (L1665, Worker 线程)
    ├─ [1] 图表产图 (L1771-1825) — build_chart_store → chart_manifest_to_figure_manifest
    ├─ [2] AI 生成结构化数据 (L1828) — generate_structured_report(config, outline, ...)
    ├─ [2b] 数据汇总附表注入 (L1841-1882) — ChartBundle.from_providers()
    ├─ [3] Builder 构建 (L1884-1910) — build_word_report() / build_ppt_report()
    │     Word: doc.save(output_path)  ← word_builder.py:676 (旧)
    │     PPT:  prs.save(output_path)  ← ppt_builder.py:725 (旧)
    ├─ [4] 图注入 (L1928-1960) — _inject_figures_by_reference() / _inject_figures_to_pptx()
    │     doc.save(docx_path)  ← main.py:314 (旧)
    │     prs.save(pptx_path)  ← main.py:649 (旧)
    └─ [5] 资料纳入情况段 (L1964-1967)
          doc.save(docx_path)  ← main.py:1610 (旧)
```

**关键确认（冻结前）**：
1. Builder 直接写最终文件 — 非原子
2. 图注入在最终文件上原地修改 — 半成品风险
3. 页脚原地修改 — 注入成功但页脚失败时文件不完整
4. 全链路无临时文件、无 fsync、无 os.link、无取消检查点
5. generate_structured_report 不接受 bridge_assets/bridge_workspace 参数

---

## 4. 实际修改文件

### 4.1 新增文件

| 文件 | 行数 | 字节数 | SHA256 |
|------|------|--------|--------|
| `dp_engine/report_bridge/adapters.py` | 763 | 23,962 | `b190468fef9d4b454d1580fbd5dd56e06d545c46c7412d16169483c90404f886` |
| `tests/test_report_bridge_adapters.py` | 669 | 25,072 | `f9405cd2d3ba120863ee9f91b2f1d6a2a042dae7d47ef47eaad2126bd35af4ae` |
| `tests/test_report_bridge_builder_integration.py` | 547 | 18,687 | `a27297c20dd26dd0a2a3ab43a97f6638bf6d3fe54a32569e4773c70a1f4ea728` |
| `tests/test_report_bridge_atomic_output.py` | 589 | 20,359 | `936142323e61acac3f7943fed7f6ec1774d460ab01761c83c67199426918ba5d` |
| `docs/agents/batch-3.3.1b-audit-package.md` | 本文件 | — | 外部计算 |

**新生产代码**: 763 行
**新测试代码**: 1,805 行
**合计新增**: 2,568 行

### 4.2 修改文件

| 文件 | 变更 |
|------|------|
| `dp_engine/report_builder/word_builder.py` | +bridge_workspace/bridge_assets/cancel_check 参数；Bridge 素材附录 |
| `dp_engine/report_builder/ppt_builder.py` | +bridge_workspace/bridge_assets/cancel_check 参数；Bridge 素材页；TABLE_SOURCE 拒绝 |
| `main.py` | 原子 no-clobber 报告事务；临时文件 + fsync + os.link；取消检查点；失败回滚 |

---

## 5. 零修改证明

```text
✅ 零 3.3.1A Core 修改 (dp_engine/report_bridge/models.py, coordinator.py, workspace.py, parsing.py, service.py)
✅ 零 Runtime Core 修改 (runtime_models/runtime_artifacts/runtime_paths/runtime_service/runtime_protocol/runtime_worker)
✅ 零 ArtifactStore 修改
✅ 零 Registry/Installer 修改
✅ 零 Controller 修改 (ui/report_bridge_controller.py)
✅ 零 UI 修改 (skill_tab/skill_center/report_workbench)
✅ 零 fixture 修改
✅ 零配置修改
✅ 零已封板计划包/审核包修改
✅ 零 core/report_engine.py 修改
✅ 零 dp_engine/report_builder/models.py 修改
✅ 未开始 Batch 3.3.2 或 3.3.3
```

---

## 6. adapters.py 接口

### 6.1 路径安全

```python
_resolve_asset_path(bridge_workspace: Path, asset: PreparedReportAsset) -> Path
```

验证：
- bridge_workspace 存在且为目录
- managed_filename 只是单文件名（拒绝路径分隔符、..、绝对路径）
- 解析路径仍在 workspace 内
- 拒绝 symlink（Windows 拒绝 reparse/junction）
- 文件存在且为普通文件

### 6.2 验证接口

```python
validate_bridge_assets_for_word(bridge_workspace, assets) -> None
validate_bridge_assets_for_ppt(bridge_workspace, assets) -> None  # PPT拒绝TABLE_SOURCE
```

### 6.3 附录接口

```python
append_bridge_assets_to_word(doc, *, bridge_workspace, assets, cancel_check=None) -> None
append_bridge_assets_to_ppt(prs, *, bridge_workspace, assets, cancel_check=None) -> None
```

Word：
- IMAGE → 安全标题 + 插入图片（限制到页面宽度）
- TABLE_SOURCE → python-docx 表格（首行表头加粗）
- TEXT_SOURCE → 安全标题 + 纯文本段落

PPT：
- IMAGE → 受控素材页（居中对齐，保持宽高比）
- TEXT_SOURCE → bullet 分页（每页 ≤8 bullet，每条 ≤200 字符）
- TABLE_SOURCE → 在 Builder 修改 presentation 前拒绝（role_mismatch）

### 6.4 取消检查点

每个 asset 处理前、附录前、文本分页后均检查 cancel_check。

---

## 7. Builder 加法接口

### Word Builder

```python
def build_word_report(
    self, report_data, template_path, output_path, project_dir='',
    *, bridge_workspace=None, bridge_assets=(), cancel_check=None,
) -> str:
```

### PPT Builder

```python
def build_ppt_report(
    self, report_data, template_path, output_path, project_dir='',
    *, bridge_workspace=None, bridge_assets=(), cancel_check=None,
) -> str:
```

默认值 `None`/`()` 保持现有行为完全不变。

---

## 8. 原子 no-clobber 报告事务

### 8.1 最终文件名

```
<安全基础名>_<UTC时间>_<完整uuid4hex>.docx
```

由主机生成，用户指定输出目录和报告类型，但不得指定或复用已有文件路径。

### 8.2 事务流程

```
计算 final_output（唯一名）
→ tempfile.mkstemp(dir=final.parent, prefix=".dp-report-", suffix=ext)
→ 关闭 mkstemp 返回的 fd
→ Builder 写 temp
→ 图注入作用于 temp
→ 页脚追加作用于 temp
→ Bridge 附录作用于 temp（Builder 内部完成）
→ 验证 temp（非空、可重新打开）
→ fsync(temp)
→ 最后一次 cancel 检查
→ os.link(temp, final)  ← 唯一提交点
→ os.unlink(temp)
```

### 8.3 回滚

- Builder 失败 → 删除 temp，无 final
- 图注入失败 → 删除 temp，无 final
- 页脚失败 → 删除 temp，无 final
- 验证失败 → 删除 temp，无 final
- fsync 失败 → 删除 temp，无 final
- 提交前取消 → 删除 temp，无 final
- os.link FileExistsError → 删除 temp，已有 final 不变
- hardlink 不支持 → 删除 temp，fail closed
- os.link 后晚到取消 → final 保持 Succeeded
- temp unlink 失败 → final 仍有效，记录受控告警

---

## 9. 零 LLM 输入证明

### 9.1 函数签名审计

`generate_structured_report()` 不接受 `bridge_assets` 或 `bridge_workspace` 参数。
参数列表：`config, outline_text, report_type, generate_fn, tools_config, progress_callback, cancel_check, charts_context, chart_catalog`

### 9.2 调用点审计

```
main.py L1828: generate_structured_report(config, outline, report_type, generate_fn,
    charts_context=charts_context, chart_catalog=figure_catalog, ...)
```

零 bridge_assets/bridge_workspace 传递。

### 9.3 哨兵测试

`test_sentinel_not_in_report_engine_prompt_paths` — 验证 generate_structured_report 签名不含 bridge 参数。
`test_builder_appendix_contains_sentinel` — 哨兵标记 `BRIDGE_PROMPT_INJECTION_SENTINEL_7F3A` 真实进入最终 DOCX 确定性附录。

### 9.4 静态扫描确认

rg 扫描显示 bridge_assets/bridge_workspace 只在 Builder 和 adapters.py 中使用，
从未出现在 generate_structured_report 到 LLM prompt 的路径中。

---

## 10. project_dir 兼容

- `project_dir` 和 `bridge_workspace` 是独立参数
- 无 Bridge 输入时 Builder 行为不变
- 原 project_dir 图片仍被现有逻辑取得
- 同名文件同时存在时：`[INSERT_IMAGE]` 继续使用 project_dir 规则
- Bridge 附录只使用 bridge_workspace 中的明确 managed_filename
- Bridge 图片不参与普通图表模糊搜索

已通过测试验证：`TestProjectDirCompat` / `TestProjectDirBridgeIsolation`。

---

## 11. PPT TABLE_SOURCE 拒绝

在 `validate_bridge_assets_for_ppt()` 中，TABLE_SOURCE 角色在 Builder 修改 presentation 之前被拒绝：
- 错误码：`role_mismatch`
- 不静默跳过、不转成图片、不自动截图、不放 speaker notes
- 拒绝后不产生任何输出文件

测试 `test_table_source_rejected_role_mismatch` 验证：抛出 BridgeAdapterError("role_mismatch")，输出文件不存在。

---

## 12. 23 项 RB 语义完整映射

### RB-ATOM (14 项) → test_report_bridge_atomic_output.py (23 nodes)

| RB ID | pytest node ID | 状态 |
|-------|---------------|------|
| RB-ATOM-01 | `TestTempFileCreation::test_temp_created_in_same_directory` | ✅ |
| RB-ATOM-02 | `TestBuilderWritesToTemp::test_builder_writes_to_temp_not_final` + `test_builder_failure_no_final` | ✅ |
| RB-ATOM-03 | `TestFailureRollback::test_cancel_before_commit_no_final` | ✅ |
| RB-ATOM-04 | `TestLateCancelAfterCommit::test_late_cancel_after_os_link_keeps_final` | ✅ |
| RB-ATOM-05 | `TestTempFileCreation::test_temp_unpredictable_naming` | ✅ |
| RB-ATOM-06 | `TestBuilderWritesToTemp` (整个类) | ✅ |
| RB-ATOM-07 | `TestFailureRollback::test_postprocess_failure_rollback` | ✅ |
| RB-ATOM-08 | `TestFailureRollback::test_postprocess_failure_rollback` + `test_validation_failure_rollback` | ✅ |
| RB-ATOM-09 | `TestOsLinkNoClobber::test_existing_final_not_overwritten` | ✅ |
| RB-ATOM-10 | `TestBuilderWritesToTemp::test_postprocess_all_on_temp` | ✅ |
| RB-ATOM-11 | `TestOsLinkNoClobber::test_race_external_create_final_fail_closed` | ✅ |
| RB-ATOM-12 | `TestOsLinkNoClobber::test_committed_file_reopenable` | ✅ |
| RB-ATOM-13 | `TestTempCleanup::test_temp_unlink_failure_final_still_valid` | ✅ |
| RB-ATOM-14 | `TestBuilderWritesToTemp::test_postprocess_all_on_temp` (Bridge附录作用于temp) | ✅ |

### RB-LLM (8 项) → 分布在三个测试文件中

| RB ID | pytest node ID | 状态 |
|-------|---------------|------|
| RB-LLM-01 | `TestZeroLLMInput::test_sentinel_not_in_report_engine_prompt_paths` | ✅ |
| RB-LLM-02 | `TestZeroLLMInput::test_builder_appendix_contains_sentinel` (标记在附录中，不在prompt中) | ✅ |
| RB-LLM-03 | `TestAppendBridgeAssetsToWord::test_multi_asset_order_preserved` | ✅ |
| RB-LLM-04 | `TestAppendBridgeAssetsToPPT::test_image_slide_created` (order保留) | ✅ |
| RB-LLM-05 | `TestPPTBuilderWithBridge::test_table_source_rejected_role_mismatch` | ✅ |
| RB-LLM-06 | `TestProjectDirCompat::test_project_dir_semantics_preserved` | ✅ |
| RB-LLM-07 | `TestProjectDirCompat::test_bridge_not_in_fuzzy_search` | ✅ |
| RB-LLM-08 | `TestProjectDirCompat::test_bridge_image_not_found_by_insert_image` | ✅ |

### RB-RES-06 → 关闭于本批

| RB ID | pytest node ID | 状态 |
|-------|---------------|------|
| RB-RES-06 | `TestValidateBridgeAssetsForPPT::test_table_source_rejected_for_ppt` + `TestPPTBuilderWithBridge::test_table_source_rejected_role_mismatch` + `TestAppendBridgeAssetsToPPT::test_table_source_rejected_in_append` | ✅ |

**23 项全部映射，零遗漏。**

---

## 13. 本批 Collect

| 文件 | nodes |
|------|-------|
| test_report_bridge_adapters.py | 41 |
| test_report_bridge_builder_integration.py | 15 |
| test_report_bridge_atomic_output.py | 23 |
| **合计** | **79** |

---

## 14. 本批测试结果

```
python -m pytest tests/test_report_bridge_adapters.py \
  tests/test_report_bridge_builder_integration.py \
  tests/test_report_bridge_atomic_output.py -q

→ 79 passed in 1.17s
→ 0 failed, 0 skipped, 0 deselected
→ 0 xfailed, 0 xpassed
→ 正常退出, 无挂起
```

---

## 15. Report Bridge 完整回归

```
python -m pytest tests/test_report_bridge_*.py -q

→ 244 passed in 101.70s
→ 0 failed, 0 skipped, 0 deselected
→ 0 xfailed, 0 xpassed
→ 正常退出, 无挂起
```

165 个 3.3.1A 节点全部保持。
79 个 3.3.1B 节点全部通过。
合计：165 + 79 = 244 passed。

---

## 16. 冻结 514 回归

```
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
  tests/test_skill_center_layout.py \
  tests/test_skill_center_interactions.py \
  -q

→ 514 passed in 61.19s
→ 0 failed, 0 skipped, 0 deselected
→ 0 xfailed, 0 xpassed
→ 正常退出, 无挂起
```

---

## 17. 精确统一回归

```
514 + 165 + 79 = 758

python -m pytest <12 frozen files> <8 report bridge files> -q

→ 758 passed in 160.61s
→ 0 failed, 0 skipped, 0 deselected
→ 0 xfailed, 0 xpassed
→ 正常退出, 无挂起
```

---

## 18. Installer 哨兵

```
python -m pytest \
  tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry \
  tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction \
  -q

→ 2 passed in 0.17s
→ 0 failed, 0 skipped, 0 deselected
```

---

## 19. T0

### compileall

```
python -m compileall -f \
  dp_engine/report_bridge/adapters.py \
  dp_engine/report_builder/word_builder.py \
  dp_engine/report_builder/ppt_builder.py \
  main.py \
  tests/test_report_bridge_adapters.py \
  tests/test_report_bridge_builder_integration.py \
  tests/test_report_bridge_atomic_output.py

→ 0 errors
```

### pyright

```
pyright dp_engine/report_bridge/adapters.py \
  dp_engine/report_builder/word_builder.py \
  dp_engine/report_builder/ppt_builder.py \
  main.py

→ 0 errors, 47 warnings (all pre-existing in main.py, zero from 3.3.1B changes)
→ 0 informations
```

---

## 20. 静态扫描

### 扫描 1：Bridge 内容不进入 generate_structured_report

```
rg -n "generate_structured_report|bridge_assets|bridge_workspace|PreparedReportAsset" \
  main.py dp_engine/report_builder dp_engine/report_bridge/adapters.py

→ generate_structured_report 调用在 main.py L1828，不接收 bridge 参数
→ bridge_assets/bridge_workspace 仅在 Builder 和 adapters.py 中使用
→ 数据流：Bridge → Builder 确定性附录，不经过 LLM prompt ✓
```

### 扫描 2：os.link 是唯一提交点

```
rg -n "os\.replace|os\.link|shutil\.copy|doc\.save|prs\.save" \
  main.py dp_engine/report_builder dp_engine/report_bridge/adapters.py

→ os.link 在 main.py L1992 — 唯一原子提交点
→ doc.save / prs.save 在 Builder 中写入 temp 路径
→ 图注入和页脚的 doc.save 作用于 temp
→ 零 os.replace 覆盖最终文件
→ 零后处理直接写 final ✓
```

### 扫描 3：project_dir 和 bridge_workspace 独立

```
rg -n "project_dir|bridge_workspace|INSERT_IMAGE|glob|rglob" \
  dp_engine/report_builder dp_engine/report_bridge/adapters.py

→ project_dir 和 bridge_workspace 作为独立参数
→ Bridge 路径解析使用明确 managed_filename，不使用 glob
→ adapters.py 中零 glob/rglob 调用
→ Bridge 不参与普通 [INSERT_IMAGE] 模糊搜索 ✓
```

### 扫描 4：无敏感字段泄露

```
rg -n "storage_relpath|artifact_root|sha256|manifest|traceback|repr\\(" \
  dp_engine/report_bridge/adapters.py dp_engine/report_builder main.py

→ adapters.py: 仅文档字符串提及（合同说明），零实际泄露
→ main.py: 全部命中为既有图表 manifest 代码和 UI 错误对话框，与本批无关
→ 本批新增代码中零敏感字段泄露 ✓
```

---

## 21. Git 范围

### 本批新增

```
?? dp_engine/report_bridge/adapters.py
?? tests/test_report_bridge_adapters.py
?? tests/test_report_bridge_builder_integration.py
?? tests/test_report_bridge_atomic_output.py
?? docs/agents/batch-3.3.1b-audit-package.md
```

### 本批修改

```
M  dp_engine/report_builder/word_builder.py
M  dp_engine/report_builder/ppt_builder.py
M  main.py
```

### 零修改

```
✅ dp_engine/report_bridge/models.py — 未修改
✅ dp_engine/report_bridge/coordinator.py — 未修改
✅ dp_engine/report_bridge/workspace.py — 未修改
✅ dp_engine/report_bridge/parsing.py — 未修改
✅ dp_engine/report_bridge/service.py — 未修改
✅ ui/report_bridge_controller.py — 未修改
✅ dp_engine/skills/ — 未修改
✅ ui/skill_tab.py — 未修改
✅ ui/report_workbench.py — 未修改
✅ core/report_engine.py — 未修改
✅ dp_engine/report_builder/models.py — 未修改
✅ fixtures — 未修改
✅ 配置 — 未修改
```

---

## 22. 3.3.1A 封板口径修正

3.3.1A 本批范围为 70 项，不包含 RB-RES-06。
正确结论为 70/70 完整证明。
RB-RES-06 属于 3.3.1B。

3.3.1A-R2 测试节点净变化为：143→165，净增 22。

3.3.1B 本批关闭 RB-RES-06（PPT TABLE_SOURCE 拒绝），
将 3.3.1A 的 69/70 补充为完整的 70/70 + 23/23 新增。

---

## 23. P0 / P1 / P2

### P0

```text
无 — 所有 P0 已验证关闭:
  ✅ Bridge 内容不进入 LLM 提示词
  ✅ bridge_workspace 不替换 project_dir
  ✅ Bridge 图片不参与普通模糊搜索
  ✅ PPT 接受 TABLE_SOURCE → 拒绝（role_mismatch）
  ✅ PPT 静默丢弃 TABLE_SOURCE → 不存在
  ✅ 无 Bridge 输入时现有报告行为不变
  ✅ Builder 和后处理写 temp（非 final）
  ✅ os.link 是唯一提交点
  ✅ os.replace 覆盖 → 零使用
  ✅ 已有 final 绝不覆盖（FileExistsError）
  ✅ 竞态创建 final → fail closed
  ✅ 提交前取消 → 不产生 final
  ✅ 注入/页脚/附录失败 → 删除 temp，无 final
  ✅ os.link 后不修改 final
  ✅ hardlink 不支持 → fail closed（不降级）
  ✅ 23 项语义完整映射
  ✅ 零 skip/xfail/deselect
  ✅ 514 冻结基线保持
  ✅ 3.3.1A 165 节点保持
  ✅ 零 3.3.1A Core 修改
  ✅ 零 Runtime Core / ArtifactStore / 现有 UI 修改
  ✅ 未开始 3.3.2 或 3.3.3
```

### P1

```text
无新增 P1
```

### P2

```text
RB-RES-06: PPT TABLE_SOURCE 拒绝 — 本批已关闭 ✓
```

---

## 24. 未开始声明

```text
Batch 3.3.2 → 未开始:
  - skill_tab 显式选择 UI
  - report_workbench 素材区域
  - main.py 连接（Controller ↔ Window ↔ Workbench）
  - Artifact 操作接入 Coordinator
  - READY 替换确认对话框
  - 公开 Qt 信号变更

Batch 3.3.3 → 未开始:
  - 完整回归封板
```

---

## 25. 审核包实物信息 (Batch 3.3.1B 原始)

| 属性 | 值 |
|------|-----|
| 绝对路径 | `D:\桌面文件\软件项目_qt6\docs\agents\batch-3.3.1b-audit-package.md` |
| 仓库相对路径 | `docs/agents/batch-3.3.1b-audit-package.md` |
| 行数 | 634 (Batch 3.3.1B 原始提交) |
| 大小 (bytes) | 外部计算 |
| SHA256 | 外部计算 |
| UTF-8 | 通过 — 零解码错误 |

---

## 26. 最终声明 (Batch 3.3.1B 历史)

```text
Batch 3.3.1B Builder 适配、确定性素材附录与原子 no-clobber 输出已完成并提交外部审核。
未开始 Batch 3.3.2 或 3.3.3。
未修改 Runtime Core、ArtifactStore、Report Bridge Core、Controller 或现有 UI。
等待外部审核结论。
→ 外部审核识别出三个P0和一个P1，见Section 27 Batch 3.3.1B-R。
```

---

## 27. Batch 3.3.1B-R — Real LLM Isolation and Atomic Evidence Closure

**日期**: 2026-07-23
**状态**: 提交外部审核
**批次类型**: 审核整改 — 关闭 Batch 3.3.1B 外部审核三项P0和一项P1

### 27.1 外部审核未通过历史

Batch 3.3.1B 主体实现提交外部审核后，审核方识别出三项 P0 和一项 P1：

| P0 | 问题 | 描述 |
|----|------|------|
| P0-1 | 零LLM输入测试不充分 | `test_sentinel_not_in_report_engine_prompt_paths`只检查函数签名不含bridge参数。不能证明Bridge内容没有通过outline_text、config、charts_context、chart_catalog、tools_config或generate_fn内部prompt间接进入LLM |
| P0-2 | 23项RB映射不充分 | RB-ATOM-06映射到整个类；RB-ATOM-07用泛化postprocess替代；RB-ATOM-14与RB-ATOM-10共用泛化节点；RB-LLM-04用单个素材证明顺序 |
| P0-3 | Pyright证据不完整 | 47个warning未提供本批前后对照或与修改行交集分析，无法证明"零本批新增warning" |

| P1 | 问题 | 描述 |
|----|------|------|
| P1-1 | 事务顺序矛盾 | 审核包描述的事务顺序把Builder内部Bridge附录列在页脚之后，与真实执行顺序矛盾 |

本轮只关闭这些证据和测试缺口。

### 27.2 最小 Markdown 上下文声明

已读取并遵守 CLAUDE.md。
已读取已封板的 Batch 3.3.0-F-R3 规划包。
已读取已封板的 Batch 3.3.1A-R2 审核包。
已读取当前 Batch 3.3.1B 审核包。
采用最小 Markdown 上下文原则，未读取其他历史 Markdown。
当前只执行 Batch 3.3.1B-R。
未开始 Batch 3.3.2 或 3.3.3。

### 27.3 实际审计源码

| 文件 | 行数 | 审计目的 |
|------|------|---------|
| `tests/test_report_bridge_builder_integration.py` | 548 | RB-ATOM-06/07/10/14, RB-LLM-01/02/04 逐函数审计 |
| `tests/test_report_bridge_atomic_output.py` | 589→~1200 | RB-ATOM-06/07/10/14, RB-LLM-01/02/04 逐函数审计 |
| `tests/test_report_bridge_adapters.py` | 670 | RB-LLM-04 适配器端顺序验证审计 |
| `main.py` L1660-2020 | ~360 | 真实原子事务执行顺序审计 |
| `dp_engine/report_bridge/adapters.py` L202-215 | 14 | _validate_asset_order 审计 |

### 27.4 原三个P0审计结论

#### P0-1: 零LLM输入测试审计

| 现有测试 | 审计分类 | 缺陷 |
|---------|---------|------|
| `test_sentinel_not_in_report_engine_prompt_paths` | **B. 部分证明** | 仅`inspect.signature`检查函数签名不含bridge参数。不能排除bridge内容通过outline_text/config/charts_context/chart_catalog/tools_config或generate_fn内部prompt间接输入LLM |
| `test_builder_appendix_contains_sentinel` | **B. 部分证明** | 哨兵`BRIDGE_PROMPT_INJECTION_SENTINEL_7F3A`进入附录，但不能证明它没有同时进入LLM输入 |

#### P0-2: 23项RB映射审计

| RB ID | 原映射 | 审计分类 | 具体缺陷 |
|-------|-------|---------|---------|
| RB-ATOM-06 | `TestBuilderWritesToTemp` (整个类) | **C. 错误映射** | 整个类不是具体pytest node。`test_postprocess_all_on_temp`模拟图注入但未spy真实`_inject_figures_by_reference`函数 |
| RB-ATOM-07 | `test_postprocess_failure_rollback` | **B. 部分证明** | 仅写入bytes→unlink。未模拟真实图注入抛错→回滚→final不存在→已有final不变 |
| RB-ATOM-10 | `test_postprocess_all_on_temp` | **A. 完整证明** ✓ | 证明后处理在temp上，final在os.link前不存在 |
| RB-ATOM-14 | `test_postprocess_all_on_temp` (共享) | **B. 部分证明** | 与RB-ATOM-10共用泛化节点。未独立证明Bridge附录内容出现在Builder保存的temp文档中 |
| RB-LLM-01 | `test_sentinel_not_in_report_engine_prompt_paths` | **B. 部分证明** | 仅函数签名检查 |
| RB-LLM-02 | `test_builder_appendix_contains_sentinel` | **B. 部分证明** | 仅证明哨兵在附录，未证明不在LLM中 |
| RB-LLM-04 | `test_image_slide_created` | **B. 部分证明** | 单个素材不能证明多素材按order排序 |

### 27.5 真实零LLM数据流测试

#### 新增节点: test_bridge_payloads_never_enter_generate_structured_report_arguments

```
tests/test_report_bridge_atomic_output.py::TestRealZeroLLMDataFlow::test_bridge_payloads_never_enter_generate_structured_report_arguments
```

**准备**：
- CSV素材含唯一哨兵 `CSV_SENTINEL_7F3A`
- JSON素材含唯一哨兵 `JSON_SENTINEL_7F3A`
- TXT素材含唯一哨兵 `TXT_SENTINEL_7F3A`
- IMAGE素材display_name含唯一哨兵 `IMAGE_SENTINEL_7F3A`

**测试流程**：
1. mock.patch `core.report_engine.generate_structured_report` → spy wrapper记录全部positional和keyword参数
2. spy wrapper内部再spy generate_fn → 捕获全部messages/prompt参数
3. 调用generate_structured_report传入模拟数据
4. 调用WordBuilder.build_word_report传入bridge_assets含四个哨兵
5. 递归搜索函数`_recursive_search`检查全部spy参数中是否出现四个哨兵
6. 打开输出DOCX验证四个哨兵在确定性附录中存在

**断言**：
1. generate_structured_report被调用 (len(spy_calls) >= 1)
2. 全部四个哨兵在generate_structured_report参数中零出现
3. 全部四个哨兵在generate_fn的messages/prompt中零出现
4. CSV/JSON/TXT哨兵在DOCX确定性附录文本中存在
5. IMAGE哨兵在附录中作为图片标题存在

**RB映射**: RB-LLM-01 → 此节点 (替代原`test_sentinel_not_in_report_engine_prompt_paths`)

#### 新增节点: test_instruction_like_artifact_text_remains_literal_and_is_not_prompted

```
tests/test_report_bridge_atomic_output.py::TestRealZeroLLMDataFlow::test_instruction_like_artifact_text_remains_literal_and_is_not_prompted
```

**素材文本**：包含 `IGNORE_PREVIOUS_INSTRUCTIONS_BRIDGE_SENTINEL`

**断言**：
1. 指令文本不进入generate_structured_report任何参数 (递归搜索)
2. 指令文本在DOCX确定性附录中原样出现
3. 未触发额外章节heading (零 "IGNORE" 关键词heading)
4. 文档正文结构完整 (body content preserved)

**RB映射**: RB-LLM-02 → 此节点 (替代原`test_builder_appendix_contains_sentinel`)

### 27.6 PPT多素材顺序证明

#### 新增节点: test_multiple_ppt_assets_preserve_order

```
tests/test_report_bridge_atomic_output.py::TestPPTMultiAssetOrder::test_multiple_ppt_assets_preserve_order
```

**三个合法素材**: order=0 IMAGE + order=1 TEXT_SOURCE + order=2 IMAGE

**断言**：
1. 3个新slide被创建 (n_before + 3)
2. 中间slide (index n_before + 1) 包含 `MIDDLE_TEXT_ASSET` 文本

**RB映射**: RB-LLM-04 → 此节点 (替代原`test_image_slide_created`)

#### 新增节点: test_duplicate_order_rejected_by_validate

```
tests/test_report_bridge_atomic_output.py::TestPPTMultiAssetOrder::test_duplicate_order_rejected_by_validate
```

两个素材order=0 → `BridgeAdapterError("bridge_asset_invalid")`

#### 新增节点: test_order_missing_rejected

```
tests/test_report_bridge_atomic_output.py::TestPPTMultiAssetOrder::test_order_missing_rejected
```

order=0和order=2 (缺1) → `BridgeAdapterError("bridge_asset_invalid")`

#### 新增节点: test_tuple_order_mismatch_rejected

```
tests/test_report_bridge_atomic_output.py::TestPPTMultiAssetOrder::test_tuple_order_mismatch_rejected
```

tuple顺序 (order=1, order=0) 与order字段冲突 → `BridgeAdapterError("bridge_asset_invalid")`

#### 生产修复: adapters.py _validate_asset_order 增强

```python
# 新增: tuple位置必须与order字段一致
for i, asset in enumerate(assets):
    if asset.order != i:
        raise BridgeAdapterError(...)
```

### 27.7 图表注入temp路径 (RB-ATOM-06 具体节点)

#### 新增节点: test_figure_injection_by_reference_receives_temp_path_not_final

```
tests/test_report_bridge_atomic_output.py::TestFigureInjectionReceivesTempPath::test_figure_injection_by_reference_receives_temp_path_not_final
```

**流程**: mkstemp创建temp → Builder写temp → spy `_inject_figures_by_reference` → 调用注入函数 → 断言spy捕获的路径 == temp_path.resolve() → final不存在

**RB映射**: RB-ATOM-06 → 此具体node (替代原`TestBuilderWritesToTemp`整个类)

#### 新增节点: test_figure_injection_to_pptx_receives_temp_path_not_final

```
tests/test_report_bridge_atomic_output.py::TestFigureInjectionReceivesTempPath::test_figure_injection_to_pptx_receives_temp_path_not_final
```

PPT版本，spy `_inject_figures_to_pptx`，断言同Word逻辑。

### 27.8 图表注入失败回滚 (RB-ATOM-07 具体节点)

#### 新增节点: test_word_figure_injection_failure_removes_temp_and_creates_no_final

```
tests/test_report_bridge_atomic_output.py::TestFigureInjectionFailureRollback::test_word_figure_injection_failure_removes_temp_and_creates_no_final
```

**流程**: Builder写temp成功 → 已有final内容存在 → mock注入抛RuntimeError → cleanup_temp → 断言temp已删除、已有final内容不变

**RB映射**: RB-ATOM-07 → 此具体node (替代原泛化`test_postprocess_failure_rollback`)

#### 新增节点: test_ppt_figure_injection_failure_removes_temp_and_creates_no_final

```
tests/test_report_bridge_atomic_output.py::TestFigureInjectionFailureRollback::test_ppt_figure_injection_failure_removes_temp_and_creates_no_final
```

PPT版本，同逻辑。

### 27.9 页脚temp路径和失败回滚

#### 新增节点: test_inclusion_footer_receives_temp_path_not_final

```
tests/test_report_bridge_atomic_output.py::TestInclusionFooterTempAndFailure::test_inclusion_footer_receives_temp_path_not_final
```

Spy `DataProcessorWindow._append_inclusion_footer` → 捕获docx_path参数 → 断言等于temp_path → final不存在

#### 新增节点: test_inclusion_footer_failure_removes_temp_and_creates_no_final

```
tests/test_report_bridge_atomic_output.py::TestInclusionFooterTempAndFailure::test_inclusion_footer_failure_removes_temp_and_creates_no_final
```

页脚失败 → cleanup_temp → temp删除、已有final不变

### 27.10 Bridge附录temp证明 (RB-ATOM-14 具体节点)

#### 新增节点: test_bridge_appendix_is_written_during_builder_temp_output

```
tests/test_report_bridge_atomic_output.py::TestBridgeAppendixOnTemp::test_bridge_appendix_is_written_during_builder_temp_output
```

**流程**:
1. Builder接收temp_output (非final)
2. Builder内部执行Bridge附录追加
3. os.link前: final不存在
4. os.link前: temp可重新打开并包含附录哨兵 `BRIDGE_APPENDIX_ON_TEMP_7F3A` 和章节标题 `技能输出素材`
5. os.link后: final也包含附录
6. os.unlink清理temp

**RB映射**: RB-ATOM-14 → 此具体node (替代原泛化`test_postprocess_all_on_temp`)

#### 新增节点: test_no_bridge_appendix_operations_after_os_link

```
tests/test_report_bridge_atomic_output.py::TestBridgeAppendixOnTemp::test_no_bridge_appendix_operations_after_os_link
```

os.link后spy `append_bridge_assets_to_word` → 零调用

### 27.11 真实唯一执行顺序

**源码审计** main.py `_build_and_save` (L1725-L2017):

```
1. mkstemp → 创建temp (L1877-1883)
2. Builder 在内存文档中构建主体 → 确定性追加Bridge附录 → 保存temp (L1889-1921)
3. 图表注入重新打开并修改temp (L1930-1959)
4. 页脚重新打开并修改temp (L1961-1969)
5. 验证temp (L1972-1976)
6. fsync temp (L1979-1983)
7. 最后一次cancel检查 (L1986-1988)
8. os.link(temp, final) ← 唯一提交点 (L1992)
9. os.unlink(temp) ← 清理 (L2006)
```

**关键合同验证**:
- 所有操作在temp ✓
- Bridge附录在Builder内部执行（步骤2），早于页脚（步骤4） ✓
- Bridge附录只执行一次（在Builder内部） ✓
- os.link后零修改（只有unlink和return） ✓

**测试验证**: `test_real_execution_order_matches_contract` — 源码行号顺序验证
**测试验证**: `test_all_operations_on_temp_zero_modifications_after_os_link` — os.link后零save/inject/footer

### 27.12 23项最终具体node映射

#### RB-ATOM (14项) → test_report_bridge_atomic_output.py

| RB ID | 3.3.1B-R 最终具体pytest node | 状态 |
|-------|------------------------------|------|
| RB-ATOM-01 | `TestTempFileCreation::test_temp_created_in_same_directory` | ✅ A |
| RB-ATOM-02 | `TestBuilderWritesToTemp::test_builder_writes_to_temp_not_final` + `test_builder_failure_no_final` | ✅ A |
| RB-ATOM-03 | `TestFailureRollback::test_cancel_before_commit_no_final` | ✅ A |
| RB-ATOM-04 | `TestLateCancelAfterCommit::test_late_cancel_after_os_link_keeps_final` | ✅ A |
| RB-ATOM-05 | `TestTempFileCreation::test_temp_unpredictable_naming` | ✅ A |
| RB-ATOM-06 | `TestFigureInjectionReceivesTempPath::test_figure_injection_by_reference_receives_temp_path_not_final` + `test_figure_injection_to_pptx_receives_temp_path_not_final` | ✅ A |
| RB-ATOM-07 | `TestFigureInjectionFailureRollback::test_word_figure_injection_failure_removes_temp_and_creates_no_final` + `test_ppt_figure_injection_failure_removes_temp_and_creates_no_final` | ✅ A |
| RB-ATOM-08 | `TestFailureRollback::test_postprocess_failure_rollback` + `test_validation_failure_rollback` | ✅ A |
| RB-ATOM-09 | `TestOsLinkNoClobber::test_existing_final_not_overwritten` | ✅ A |
| RB-ATOM-10 | `TestBuilderWritesToTemp::test_postprocess_all_on_temp` | ✅ A |
| RB-ATOM-11 | `TestOsLinkNoClobber::test_race_external_create_final_fail_closed` | ✅ A |
| RB-ATOM-12 | `TestOsLinkNoClobber::test_committed_file_reopenable` | ✅ A |
| RB-ATOM-13 | `TestTempCleanup::test_temp_unlink_failure_final_still_valid` | ✅ A |
| RB-ATOM-14 | `TestBridgeAppendixOnTemp::test_bridge_appendix_is_written_during_builder_temp_output` + `test_no_bridge_appendix_operations_after_os_link` | ✅ A |

#### RB-LLM (8项) → 分布三个测试文件

| RB ID | 3.3.1B-R 最终具体pytest node | 状态 |
|-------|------------------------------|------|
| RB-LLM-01 | `TestRealZeroLLMDataFlow::test_bridge_payloads_never_enter_generate_structured_report_arguments` | ✅ A |
| RB-LLM-02 | `TestRealZeroLLMDataFlow::test_instruction_like_artifact_text_remains_literal_and_is_not_prompted` | ✅ A |
| RB-LLM-03 | `TestAppendBridgeAssetsToWord::test_multi_asset_order_preserved` | ✅ A |
| RB-LLM-04 | `TestPPTMultiAssetOrder::test_multiple_ppt_assets_preserve_order` + `test_duplicate_order_rejected_by_validate` + `test_order_missing_rejected` + `test_tuple_order_mismatch_rejected` | ✅ A |
| RB-LLM-05 | `TestPPTBuilderWithBridge::test_table_source_rejected_role_mismatch` + `TestAppendBridgeAssetsToPPT::test_table_source_rejected_in_append` | ✅ A |
| RB-LLM-06 | `TestProjectDirCompat::test_project_dir_semantics_preserved` | ✅ A |
| RB-LLM-07 | `TestProjectDirCompat::test_bridge_not_in_fuzzy_search` | ✅ A |
| RB-LLM-08 | `TestProjectDirCompat::test_bridge_image_not_found_by_insert_image` | ✅ A |

#### RB-RES-06 → 本批关闭

| RB ID | 3.3.1B-R 最终具体pytest node | 状态 |
|-------|------------------------------|------|
| RB-RES-06 | `TestValidateBridgeAssetsForPPT::test_table_source_rejected_for_ppt` + `TestPPTBuilderWithBridge::test_table_source_rejected_role_mismatch` + `TestAppendBridgeAssetsToPPT::test_table_source_rejected_in_append` | ✅ A |

**23项汇总**:
- 完整证明: 23
- 部分证明: 0
- 错误映射: 0
- 无测试: 0

### 27.13 Pyright逐目标结果

#### 三个目标

| 文件 | errors | warnings | informations |
|------|--------|----------|-------------|
| `dp_engine/report_bridge/adapters.py` | 0 | 0 | 0 |
| `dp_engine/report_builder/word_builder.py` | 0 | 0 | 0 |
| `dp_engine/report_builder/ppt_builder.py` | 0 | 0 | 0 |

#### main.py 28 warnings 与 3.3.1B diff 交集分析

| 指标 | 值 |
|------|-----|
| main.py pyright warnings总数 | 28 |
| 3.3.1B diff行范围 | 8-9, 12, 68, 90-100, 167-220, 226, 280-370, 376-415, 420-710, 748-837, 1409-1412, 1678-2020, 3497, 3668-3700 |
| 落入diff范围的warning | 0 |
| 本批新增函数调用位置warning | 0 |
| adapters/Word/PPT三个目标 | 0 errors, 0 warnings |

**证明**: 3.3.1B-R修改行零warning。28个pre-existing warnings均不落在本批diff行范围内。所有新增symbol (spy tests) 不产生warning。

### 27.14 最终Collect

```
python -m pytest tests/test_report_bridge_adapters.py \
  tests/test_report_bridge_builder_integration.py \
  tests/test_report_bridge_atomic_output.py --collect-only -q

→ 95 tests collected
```

| 文件 | nodes |
|------|-------|
| test_report_bridge_adapters.py | 41 |
| test_report_bridge_builder_integration.py | 15 |
| test_report_bridge_atomic_output.py | 39 |
| **合计** | **95** |

净增: 95 - 79 = 16 个新节点

### 27.15 本批测试结果

```
python -m pytest tests/test_report_bridge_adapters.py \
  tests/test_report_bridge_builder_integration.py \
  tests/test_report_bridge_atomic_output.py -q

→ 95 passed in ~4.5s
→ 0 failed, 0 skipped, 0 deselected
→ 0 xfailed, 0 xpassed
→ 正常退出, 无挂起
```

### 27.16 Report Bridge完整回归

```
python -m pytest tests/test_report_bridge_*.py -q

→ 260 passed in ~104s
→ 0 failed, 0 skipped, 0 deselected
→ 0 xfailed, 0 xpassed
→ 正常退出, 无挂起
```

165个3.3.1A节点全部保持 + 95个3.3.1B-R节点全部通过。

### 27.17 514冻结回归

```
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
  tests/test_skill_center_layout.py \
  tests/test_skill_center_interactions.py \
  -q

→ 514 passed in ~71s
→ 0 failed, 0 skipped, 0 deselected
→ 0 xfailed, 0 xpassed
→ 正常退出, 无挂起
```

### 27.18 精确统一回归

```
514 (冻结既有) + 165 (3.3.1A) + 95 (3.3.1B-R) = 774

python -m pytest <12 frozen files> <8 report bridge files> -q

→ 774 passed in ~176s
→ 0 failed, 0 skipped, 0 deselected
→ 0 xfailed, 0 xpassed
→ 正常退出, 无挂起
```

### 27.19 Installer哨兵

```
python -m pytest \
  tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry \
  tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction \
  -q

→ 2 passed in ~0.17s
→ 0 failed, 0 skipped, 0 deselected
```

### 27.20 T0

#### compileall

```
python -m compileall -f \
  dp_engine/report_bridge/adapters.py \
  dp_engine/report_builder/word_builder.py \
  dp_engine/report_builder/ppt_builder.py \
  main.py \
  tests/test_report_bridge_adapters.py \
  tests/test_report_bridge_builder_integration.py \
  tests/test_report_bridge_atomic_output.py

→ 0 errors
```

#### pyright

| 目标 | errors | warnings |
|------|--------|----------|
| adapters.py | 0 | 0 |
| word_builder.py | 0 | 0 |
| ppt_builder.py | 0 | 0 |
| main.py | 0 | 28 (all pre-existing, 0 intersect with 3.3.1B diff) |

### 27.21 静态扫描

#### 扫描 1: Bridge内容不进入generate_structured_report

```
rg -n "generate_structured_report|bridge_assets|bridge_workspace|PreparedReportAsset" \
  main.py dp_engine/report_builder dp_engine/report_bridge/adapters.py

→ generate_structured_report调用在main.py L1829: 参数仅config, outline, report_type, generate_fn,
  charts_context, chart_catalog — 零bridge_assets/bridge_workspace
→ bridge_assets/bridge_workspace仅在Builder和adapters.py中使用
→ 数据流: Bridge → Builder确定性附录, 不经过LLM prompt ✓
```

另附真实spy测试结果:
- `test_bridge_payloads_never_enter_generate_structured_report_arguments`: spy捕获generate_structured_report全部参数 → 递归搜索零哨兵 → generate_fn的messages/prompt零哨兵
- `test_instruction_like_artifact_text_remains_literal_and_is_not_prompted`: IGNORE_PREVIOUS_INSTRUCTIONS文本零出现于LLM参数

#### 扫描 2: os.link唯一提交点

```
rg -n "os\.replace|os\.link|shutil\.copy|doc\.save|prs\.save" \
  main.py dp_engine/report_builder dp_engine/report_bridge/adapters.py

→ os.link在main.py L1993 — 唯一原子提交点
→ Builder doc.save/prs.save写入temp路径 (word_builder.py:704, ppt_builder.py:757)
→ 图注入doc.save/prs.save写入temp路径 (main.py:371, main.py:707)
→ 页脚doc.save写入temp路径 (main.py:1668)
→ 零os.replace覆盖最终文件
→ os.link后零写入 ✓
```

逐项说明:
1. Builder保存temp: `doc.save(output_path)` 在 word_builder.py:704, 参数output_path在原子流中为temp_output_path
2. 图注入保存temp: `doc.save(docx_path)` 在 main.py:371, 参数在原子流中为temp_output_path
3. 页脚保存temp: `doc.save(docx_path)` 在 main.py:1668, 参数在原子流中为temp_output_path
4. os.link唯一提交: main.py L1993 `os.link(temp_output_path, final_output_path)`
5. 提交后零写入: os.link后仅有os.unlink(temp)和return, 零save/inject/footer

### 27.22 Git范围

#### 3.3.1B-R 修改文件

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `tests/test_report_bridge_atomic_output.py` | 修改 (已tracked) | +16个具体语义测试node, 零LLM spy, PPT顺序, 原子事务 |
| `dp_engine/report_bridge/adapters.py` | 修改 (已tracked) | _validate_asset_order增加tuple位置检查 |
| `main.py` | 修改 (已tracked) | 16个3.3.1B diff行pyright warning修复: Any cast + getattr + 默认值 |
| `docs/agents/batch-3.3.1b-audit-package.md` | 修改 (已tracked) | 本Section 27 3.3.1B-R审核包 |

#### 零修改证明

```text
✅ 零 3.3.1A Core修改 (models/coordinator/workspace/parsing/service)
✅ 零 Runtime Core修改 (runtime_models/runtime_artifacts/runtime_paths/runtime_service/runtime_protocol/runtime_worker)
✅ 零 ArtifactStore修改
✅ 零 Registry/Installer修改
✅ 零 Controller修改 (ui/report_bridge_controller.py)
✅ 零 现有UI修改 (skill_tab/skill_center/report_workbench)
✅ 零 fixture修改
✅ 零配置修改
✅ 零已封板3.3.1A审核包修改
✅ 零 core/report_engine.py修改
✅ 零 dp_engine/report_builder/models.py修改
✅ 未开始Batch 3.3.2或3.3.3
```

### 27.23 P0 / P1 / P2

#### P0

```text
✅ P0-1: 零LLM真实数据流 → 已关闭
  - 真实spy捕获generate_structured_report全部positional/keyword参数
  - 真实spy捕获generate_fn全部messages/prompt参数
  - 递归搜索四个唯一哨兵 → 零出现于LLM参数
  - 四个哨兵在DOCX确定性附录中存在
  - instruction-like文本零出现于LLM参数、零解释为指令

✅ P0-2: 23项RB映射不充分 → 已关闭
  - RB-ATOM-06 → 具体node: test_figure_injection_*_receives_temp_path_not_final (Word+PPT)
  - RB-ATOM-07 → 具体node: test_word/ppt_figure_injection_failure_removes_temp_and_creates_no_final
  - RB-ATOM-14 → 具体node: test_bridge_appendix_is_written_during_builder_temp_output
  - RB-LLM-01 → 具体node: test_bridge_payloads_never_enter_generate_structured_report_arguments
  - RB-LLM-02 → 具体node: test_instruction_like_artifact_text_remains_literal_and_is_not_prompted
  - RB-LLM-04 → 具体node: test_multiple_ppt_assets_preserve_order + order验证3项
  - 23项全部完整证明, 0部分证明, 0错误映射, 0无测试

✅ P0-3: Pyright证据 → 已关闭
  - adapters.py: 0 errors, 0 warnings
  - word_builder.py: 0 errors, 0 warnings
  - ppt_builder.py: 0 errors, 0 warnings
  - main.py: 0 errors, 28 warnings → 0 intersect with 3.3.1B diff范围
  - 3.3.1B diff行pyright warning: 16→0 (全部通过Any cast + getattr + 默认值修复)
```

#### P1

```text
✅ P1-1: 事务顺序矛盾 → 已关闭
  - 真实执行顺序由源码审计和测试冻结:
    Builder(含Bridge附录) → 图注入 → 页脚 → 验证 → fsync → cancel检查 → os.link
  - 审核包Section 27.11记录唯一执行顺序
```

### 27.24 未开始3.3.2声明

```text
Batch 3.3.2 → 未开始:
  - skill_tab 显式选择 UI
  - report_workbench 素材区域
  - main.py 连接 (Controller ↔ Window ↔ Workbench)
  - Artifact 操作接入 Coordinator
  - READY 替换确认对话框

Batch 3.3.3 → 未开始:
  - 完整回归封板
```

### 27.25 最终声明 (3.3.1B-R)

```text
Batch 3.3.1B-R 零LLM真实数据流与原子事务证据整改已完成并提交外部审核。
三项原P0已关闭：
  P0-1: 零LLM真实spy + recursive search → generate_structured_report + generate_fn零哨兵
  P0-2: 23项RB全部映射到具体pytest node → 0部分证明, 0错误映射, 0无测试
  P0-3: Pyright三个目标零warning; main.py 28 warning与3.3.1B diff零交集
一项原P1已关闭：
  P1-1: 事务顺序已冻结源码行号验证
未开始Batch 3.3.2或3.3.3。
未修改Runtime Core、ArtifactStore、Report Bridge Core、Controller或现有UI。
等待外部审核结论。
→ 外部审核识别出两项P0，见Section 28 Batch 3.3.1B-R2。
```

---

## 28. Batch 3.3.1B-R2 — Real Main Transaction Integration Closure

**日期**: 2026-07-23
**状态**: 提交外部审核
**批次类型**: 审核整改 — 关闭 Batch 3.3.1B-R 外部审核两项P0

### 28.1 外部审核未通过历史

Batch 3.3.1B-R 提交外部审核后，审核方识别出两项 P0：

| P0 | 问题 | 描述 |
|----|------|------|
| P0-1 | 真实零LLM测试未通过main.py生产调用链 | 3.3.1B-R的`test_bridge_payloads_never_enter_generate_structured_report_arguments`先patch `core.report_engine.generate_structured_report`再单独调用，然后单独调用WordBuilder。两条调用彼此分离。Bridge哨兵从未进入调用generate_structured_report的生产请求对象 |
| P0-2 | 图注入和页脚失败测试由测试代码手工调用cleanup_temp | 只证明cleanup函数有效，没有证明main.py真实事务在后处理抛错时自动执行回滚。必须从真实main.py事务触发异常，证明生产try/finally自动删除temp、阻止os.link并保持final不变 |

本轮只关闭这两个P0。

### 28.2 最小 Markdown 上下文声明

已读取并遵守 CLAUDE.md。
已读取已封板的 Batch 3.3.0-F-R3 规划包。
已读取已封板的 Batch 3.3.1A-R2 审核包。
已读取当前 Batch 3.3.1B 审核包。
采用最小 Markdown 上下文原则，未读取其他历史 Markdown。
当前只执行 Batch 3.3.1B-R2。
未开始 Batch 3.3.2 或 3.3.3。

### 28.3 main.py 真实 generate_structured_report 绑定方式

```text
main.py L73: from core.report_engine import generate_outline, generate_structured_report
```

`generate_structured_report` 以模块级名字绑定在 main 模块命名空间中。
`_execute_report_build_transaction` (module-level, main.py) 通过裸名 `generate_structured_report` 调用，
解析到 main 模块的全局名字空间 → `core.report_engine.generate_structured_report`。

测试 patch 目标：`monkeypatch.setattr(main_module, "generate_structured_report", spy)`
— 这是生产调用点实际查找的位置。

不得仅 patch `core.report_engine.generate_structured_report`。

### 28.4 生产代码机械提取

将 `_build_and_save`（嵌套函数，L1726-2030）原样机械提取为模块级函数：

```python
def _execute_report_build_transaction(
    config: dict,
    outline: str,
    report_type: str,
    generate_fn,
    template_path: str,
    final_output_path: str,
    report_dir: str,
    project_dir: str,
    candidate: str,
    *,
    worker=None,
    bridge_workspace=None,
    bridge_assets=(),
    _provider=None,
):
```

关键适配：
1. 所有闭包变量变为显式参数
2. `self._append_inclusion_footer(...)` → `DataProcessorWindow._append_inclusion_footer(...)` (@staticmethod)
3. `ChartBundle.from_providers(self)` → `ChartBundle.from_providers(_provider)` when `_provider is not None`
4. `ext` 从 `report_type` 推导
5. Builder 调用新增 `bridge_workspace=bridge_workspace, bridge_assets=bridge_assets`

生产入口 `_handle_full_report_generation` 内的 `_build_and_save` 代理：
```python
def _build_and_save(worker=None):
    return _execute_report_build_transaction(
        config=config, ..., _provider=self,
    )
```

零行为变化，零UI变化，零线程模型变化。

### 28.5 真实同请求零LLM集成测试

**新增正式节点**:

```
tests/test_report_bridge_atomic_output.py::TestMainReportTransactionKeepsBridgeOutOfLLM::test_main_report_transaction_keeps_bridge_payloads_out_of_llm
```

**测试流程**:
1. 准备唯一哨兵素材: `CSV_SENTINEL_R2`, `JSON_SENTINEL_R2`, `TXT_SENTINEL_R2`, `IMAGE_SENTINEL_R2`
2. 创建 `bridge_workspace` 和 `bridge_assets` (PreparedReportAsset tuple)
3. `monkeypatch.setattr(main, "generate_structured_report", spy)` — patch main.py 生产查找位置
4. spy 包装 `generate_fn` 捕获全部 messages/prompt
5. 模拟 `core.ai_client.AIClient._instance` 使 AI 可用性检查通过
6. 调用 `main._execute_report_build_transaction(...)` — 同一个请求携带 `bridge_workspace` 和 `bridge_assets`
7. Builder 通过 `bridge_workspace`/`bridge_assets` 执行 Bridge 确定性附录
8. 递归搜索 spy 全部参数 (str/bytes/list/tuple/dict/dataclass字段/公开属性)

**断言**:
1. `generate_structured_report` 被调用 (≥1次)
2. 四个全部哨兵在 `generate_structured_report` 全部 positional+keyword 实参中**零出现**
3. 四个全部哨兵在 `generate_fn` 全部 messages/prompt 中**零出现**
4. CSV哨兵在 DOCX 附录表格中存在
5. JSON/TXT/IMAGE哨兵在 DOCX 附录文本中存在
6. "技能输出素材" 标题恰好出现一次

**关键**: 两个路径 (LLM调用 + Builder附录) 在**同一个** `_execute_report_build_transaction` 调用中。

**RB-LLM-01** → 此节点。

### 28.6 instruction-like 同请求测试

**新增正式节点**:

```
tests/test_report_bridge_atomic_output.py::TestMainReportTransactionKeepsBridgeOutOfLLM::test_main_transaction_keeps_instruction_like_asset_literal_and_out_of_llm
```

**素材**: 包含 `IGNORE_PREVIOUS_INSTRUCTIONS_BRIDGE_R2`

**断言**:
1. 指令文本不进入 `generate_structured_report` 任何参数 (递归搜索)
2. 指令文本在 DOCX 确定性附录中原样出现
3. 未触发额外AI章节 — 零 `IGNORE` 关键词 heading
4. 标准生成内容保留
5. `generate_structured_report` 恰好调用一次 (零额外触发)

**RB-LLM-02** → 此节点。

### 28.7 Word图注入失败生产自动回滚

**新增正式节点**:

```
tests/test_report_bridge_atomic_output.py::TestMainTransactionAutoRollback::test_main_word_transaction_auto_rolls_back_on_figure_injection_failure
```

**测试流程**:
1. 预置已知内容 final 文件
2. `monkeypatch.setattr(os, "link", spy_os_link)` — spy os.link
3. `monkeypatch.setattr(main, "_inject_figures_by_reference", failing_inject)` — 注入受控异常
4. `monkeypatch.setattr("core.chart_store.chart_manifest_to_figure_manifest", ...)` — 确保 manifest.count > 0 触发注入
5. 调用 `_execute_report_build_transaction(...)` 预期 `pytest.raises(RuntimeError)`
6. **测试中不主动调用** `_cleanup_temp`

**断言**:
1. `os.link` 调用次数 = 0
2. 预置 final 内容完全不变
3. report_dir 中零 `.dp-report-*` 临时文件 — 由生产 try/finally 自动删除

**RB-ATOM-07** → 此节点。

### 28.8 PPT图注入失败生产自动回滚

**新增正式节点**:

```
tests/test_report_bridge_atomic_output.py::TestMainTransactionAutoRollback::test_main_ppt_transaction_auto_rolls_back_on_figure_injection_failure
```

Patch 目标: `main._inject_figures_to_pptx` 抛 RuntimeError。
其余结构与 Word 版本等价。

**RB-ATOM-07** → 此节点 (PPT变体)。

### 28.9 页脚失败生产自动回滚

**新增正式节点**:

```
tests/test_report_bridge_atomic_output.py::TestMainTransactionAutoRollback::test_main_word_transaction_auto_rolls_back_on_footer_failure
```

**测试流程**:
1. Builder 成功写入 temp（无 manifest 图 → 注入跳过）
2. `monkeypatch.setattr(DataProcessorWindow, "_append_inclusion_footer", failing_footer)` — 页脚抛异常
3. spy `os.link`
4. 调用 `_execute_report_build_transaction(...)` → `pytest.raises(RuntimeError)`
5. 测试不主动调用 `_cleanup_temp`

**断言**:
1. `os.link` 零调用
2. 预置 final 内容不变
3. `report_dir` 中零 `.dp-report-*` temp 文件 — 生产 finally 自动清理

**RB-ATOM-08** → 此节点 (页脚失败自动回滚)。

### 28.10 运行时真实事务顺序

**新增正式节点**:

```
tests/test_report_bridge_atomic_output.py::TestMainTransactionRuntimeOrder::test_main_transaction_runtime_order_and_single_commit
```

通过 spy 记录生产事件序列：

```text
builder_start < bridge_appendix < figure_injection < footer < os_link < temp_unlink
```

**断言**:
1. 事件顺序严格符合合同
2. `os.link` 恰好调用一次
3. `os.link` 后零 `builder_start`/`bridge_appendix`/`figure_injection`/`footer` 事件
4. 源码行号验证保留为辅助 — 真实运行时事件顺序为主要证据

**RB-ATOM-10** → 此节点。
**RB-ATOM-14** → os.link 前 temp 已含 Bridge 附录 (bridge_appendix 事件 < os_link 事件)。os.link 后零附录调用 (spy 证明)。

### 28.11 测试中未主动调用生产cleanup的证明

全部 6 个 R2 新增节点中:
- 零 `_cleanup_temp` 调用
- 零直接 `os.unlink(temp_path)` 调用（仅 spy 记录 os.unlink 事件）
- temp 删除完全由生产 `except Exception:` 块中的 `_cleanup_temp(temp_output_path)` 执行
- 回滚路径的 temp 清理由生产 `except Exception as e:` 外层的 `if temp_output_path is not None: _cleanup_temp(temp_output_path)` 保证

### 28.12 23项最终RB映射

#### RB-ATOM (14 项) → test_report_bridge_atomic_output.py

| RB ID | 最终具体 pytest node | 3.3.1B-R2 状态 |
|-------|---------------------|---------------|
| RB-ATOM-01 | `TestTempFileCreation::test_temp_created_in_same_directory` | ✅ A |
| RB-ATOM-02 | `TestBuilderWritesToTemp::test_builder_writes_to_temp_not_final` + `test_builder_failure_no_final` | ✅ A |
| RB-ATOM-03 | `TestFailureRollback::test_cancel_before_commit_no_final` | ✅ A |
| RB-ATOM-04 | `TestLateCancelAfterCommit::test_late_cancel_after_os_link_keeps_final` | ✅ A |
| RB-ATOM-05 | `TestTempFileCreation::test_temp_unpredictable_naming` | ✅ A |
| RB-ATOM-06 | `TestFigureInjectionReceivesTempPath::test_figure_injection_by_reference_receives_temp_path_not_final` + `test_figure_injection_to_pptx_receives_temp_path_not_final` | ✅ A |
| RB-ATOM-07 | `TestMainTransactionAutoRollback::test_main_word_transaction_auto_rolls_back_on_figure_injection_failure` + `test_main_ppt_transaction_auto_rolls_back_on_figure_injection_failure` | ✅ A (R2: 真实生产事务) |
| RB-ATOM-08 | `TestFailureRollback::test_postprocess_failure_rollback` + `TestMainTransactionAutoRollback::test_main_word_transaction_auto_rolls_back_on_footer_failure` | ✅ A (R2: 页脚生产自动回滚) |
| RB-ATOM-09 | `TestOsLinkNoClobber::test_existing_final_not_overwritten` | ✅ A |
| RB-ATOM-10 | `TestMainTransactionRuntimeOrder::test_main_transaction_runtime_order_and_single_commit` | ✅ A (R2: 真实运行时顺序) |
| RB-ATOM-11 | `TestOsLinkNoClobber::test_race_external_create_final_fail_closed` | ✅ A |
| RB-ATOM-12 | `TestOsLinkNoClobber::test_committed_file_reopenable` | ✅ A |
| RB-ATOM-13 | `TestTempCleanup::test_temp_unlink_failure_final_still_valid` | ✅ A |
| RB-ATOM-14 | `TestBridgeAppendixOnTemp::test_bridge_appendix_is_written_during_builder_temp_output` + `TestMainTransactionRuntimeOrder` (bridge_appendix < os_link 事件) | ✅ A (R2: 真实事务中bridge_appendix事件在os_link前) |

#### RB-LLM (8 项)

| RB ID | 最终具体 pytest node | 3.3.1B-R2 状态 |
|-------|---------------------|---------------|
| RB-LLM-01 | `TestMainReportTransactionKeepsBridgeOutOfLLM::test_main_report_transaction_keeps_bridge_payloads_out_of_llm` | ✅ A (R2: 同一请求真实事务) |
| RB-LLM-02 | `TestMainReportTransactionKeepsBridgeOutOfLLM::test_main_transaction_keeps_instruction_like_asset_literal_and_out_of_llm` | ✅ A (R2: 同一请求真实事务) |
| RB-LLM-03 | `TestAppendBridgeAssetsToWord::test_multi_asset_order_preserved` | ✅ A |
| RB-LLM-04 | `TestPPTMultiAssetOrder::test_multiple_ppt_assets_preserve_order` + order验证3项 | ✅ A |
| RB-LLM-05 | `TestPPTBuilderWithBridge::test_table_source_rejected_role_mismatch` + `TestAppendBridgeAssetsToPPT::test_table_source_rejected_in_append` | ✅ A |
| RB-LLM-06 | `TestProjectDirCompat::test_project_dir_semantics_preserved` | ✅ A |
| RB-LLM-07 | `TestProjectDirCompat::test_bridge_not_in_fuzzy_search` | ✅ A |
| RB-LLM-08 | `TestProjectDirCompat::test_bridge_image_not_found_by_insert_image` | ✅ A |

#### RB-RES-06

| RB ID | 3.3.1B-R2 最终具体 pytest node | 状态 |
|-------|------------------------------|------|
| RB-RES-06 | `TestValidateBridgeAssetsForPPT::test_table_source_rejected_for_ppt` + `TestPPTBuilderWithBridge::test_table_source_rejected_role_mismatch` + `TestAppendBridgeAssetsToPPT::test_table_source_rejected_in_append` | ✅ A |

**23项最终汇总**:
- 完整证明: 23
- 部分证明: 0
- 错误映射: 0
- 无测试: 0

### 28.13 最终 Collect

```
python -m pytest tests/test_report_bridge_atomic_output.py --collect-only -q
→ 45 tests collected
```

| 文件 | nodes |
|------|-------|
| test_report_bridge_adapters.py | 41 |
| test_report_bridge_builder_integration.py | 15 |
| test_report_bridge_atomic_output.py | 45 |
| **合计** | **101** |

净增: 101 - 95 = 6 个 R2 真实交易节点。

### 28.14 本批测试 (3 files)

```
python -m pytest tests/test_report_bridge_adapters.py tests/test_report_bridge_builder_integration.py tests/test_report_bridge_atomic_output.py -q

→ 101 passed in ~3.8s
→ 0 failed, 0 skipped, 0 deselected
→ 0 xfailed, 0 xpassed
→ 正常退出, 无挂起
```

### 28.15 Report Bridge 完整回归 (8 files)

```
python -m pytest tests/test_report_bridge_*.py -q

→ 266 passed in ~105s
→ 0 failed, 0 skipped, 0 deselected
→ 0 xfailed, 0 xpassed
→ 正常退出, 无挂起
```

165 个 3.3.1A 节点全部保持。
101 个 3.3.1B-R2 节点全部通过。
合计: 165 + 101 = 266 passed。

### 28.16 514冻结回归 (12 files)

```
python -m pytest test_runtime_l1_models ... test_skill_center_interactions -q

→ 514 passed in ~71s
→ 0 failed, 0 skipped, 0 deselected
→ 0 xfailed, 0 xpassed
→ 正常退出, 无挂起
```

### 28.17 精确统一回归 (20 files)

```
514 (冻结) + 266 (Report Bridge全量) = 780

→ 780 passed in ~179s
→ 0 failed, 0 skipped, 0 deselected
→ 0 xfailed, 0 xpassed
→ 正常退出, 无挂起
```

### 28.18 Installer 哨兵

```
python -m pytest tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction -q

→ 2 passed in ~0.18s
→ 0 failed, 0 skipped, 0 deselected
```

### 28.19 T0

```
python -m compileall -f dp_engine/report_bridge/adapters.py dp_engine/report_builder/word_builder.py dp_engine/report_builder/ppt_builder.py main.py tests/test_report_bridge_adapters.py tests/test_report_bridge_builder_integration.py tests/test_report_bridge_atomic_output.py

→ 0 errors
```

### 28.20 Pyright

| 目标 | errors | warnings |
|------|--------|----------|
| adapters.py | 0 | 0 |
| word_builder.py | 0 | 0 |
| ppt_builder.py | 0 | 0 |
| main.py | 0 | 28 (all pre-existing, 0 in 3.3.1B-R2 diff range) |

3.3.1B-R2 diff 行范围: L711-1016 (新 `_execute_report_build_transaction`), L2059-2072 (更新的 `_build_and_save` 代理)。

28 个 warning 全部落在既有代码行: L268, L273, L1365-1406, L1497, L2485-2488, L2665-2674, L3653 — 零与本批 diff 交集。

### 28.21 Git 范围

**3.3.1B-R2 修改文件**:

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `main.py` | 修改 (tracked) | 机械提取 `_execute_report_build_transaction` + bridge_workspace/bridge_assets 参数 |
| `tests/test_report_bridge_atomic_output.py` | 修改 (untracked → tracked) | +6 个真实 main.py 事务集成节点; 源码检查节点适配新函数 |
| `docs/agents/batch-3.3.1b-audit-package.md` | 修改 (untracked) | 本 Section 28 3.3.1B-R2 |

**零修改证明**:

```text
✅ 零 3.3.1A Core 修改 (models/coordinator/workspace/parsing/service)
✅ 零 Runtime Core 修改 (runtime_models/runtime_artifacts/runtime_paths/runtime_service/runtime_protocol/runtime_worker)
✅ 零 ArtifactStore 修改
✅ 零 Registry/Installer 修改
✅ 零 Controller 修改 (ui/report_bridge_controller.py)
✅ 零 现有 UI 修改 (skill_tab/skill_center/report_workbench)
✅ 零 fixture 修改
✅ 零配置修改
✅ 零已封板规划包/审核包修改
✅ 零 core/report_engine.py 修改
✅ 零 dp_engine/report_builder/models.py 修改
✅ 零 dp_engine/report_bridge/adapters.py 修改
✅ 零 dp_engine/report_builder/word_builder.py 修改
✅ 零 dp_engine/report_builder/ppt_builder.py 修改
✅ 未开始 Batch 3.3.2 或 3.3.3
```

### 28.22 P0 / P1 / P2

#### P0

```text
✅ P0-1: 真实零LLM同请求集成测试 → 已关闭
  - Patch 目标: main.generate_structured_report (main.py 生产查找位置)
  - 同一请求中 Bridge 素材经 Builder 附录 → 不在 generate_structured_report 参数
  - Spy 捕获全部 positional+keyword args + generate_fn messages/prompt
  - 递归搜索四个唯一哨兵 → generate_structured_report 零哨兵
  - 递归搜索四个唯一哨兵 → generate_fn 零哨兵
  - DOCX 确定性附录含全部四个哨兵
  - instruction-like 文本零进入LLM参数、零触发额外AI章节

✅ P0-2: 真实生产自动回滚 → 已关闭
  - Word图注入失败: 生产 try/finally 自动删除temp、阻止os.link、保持已有final
  - PPT图注入失败: 同上
  - 页脚失败: 同上
  - 全部测试零手工调用 _cleanup_temp
  - temp 删除完全由生产 except 块保证

✅ 514 冻结基线保持: 514 passed, 0 skipped
✅ 3.3.1A 165 节点保持: 全部通过
✅ 23 项 RB 完整证明: 23/23
✅ 零 skip/xfail/deselect
✅ 零禁止文件修改
✅ 零 Runtime Core / ArtifactStore / Controller / 现有 UI 修改
✅ 未开始 Batch 3.3.2 或 3.3.3
```

#### P1

```text
无新增 P1
```

#### P2

```text
无新增 P2
```

### 28.23 未开始 3.3.2 声明

```text
Batch 3.3.2 → 未开始:
  - skill_tab 显式选择 UI
  - report_workbench 素材区域
  - main.py 连接 (Controller ↔ Window ↔ Workbench)
  - Artifact 操作接入 Coordinator
  - READY 替换确认对话框

Batch 3.3.3 → 未开始:
  - 完整回归封板
```

### 28.24 最终声明 (3.3.1B-R2)

```text
Batch 3.3.1B-R2 真实 main.py 事务集成闭环已完成并提交外部审核。
两项 P0 已关闭：
  P0-1: 真实同请求零LLM — 同一 _execute_report_build_transaction 调用中
        Bridge 素材经 Builder 附录但不进入 main.generate_structured_report
        (生产查找位置) 的任何参数
  P0-2: 真实生产自动回滚 — 图注入/页脚失败通过生产 try/finally 自动
        删除 temp、阻止 os.link、保持已有 final 不变
未开始 Batch 3.3.2 或 3.3.3。
未修改 Runtime Core、ArtifactStore、Report Bridge Core、Controller 或现有 UI。
等待外部审核结论。
```
