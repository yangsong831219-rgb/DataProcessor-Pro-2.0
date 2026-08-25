# Batch 3.5.1 — report_backend 清单语义与后端目录审核包

**日期**：2026-08-05  
**分支**：`llama-cpp`  
**HEAD**：`8a0105527857bbefd34426817ccc0cbbf23102b6`  
**审核结论**：PASS，可提交用户审核；未进入 Batch 3.5.2

> GitHub 插件和 `gh` 仍不可用。本批依据 `batch-3.5-planning-package.md` 的本地 Issue 草案执行，不声称已读取或修改远程 Issue。

---

## 1. 批次合同

单一目标：在不执行插件、不接报告主链的前提下，确定性判断一个已安装技能是否是可候选的 PPT/Word `report_backend`。

允许范围：

- `dp_engine/report_backend/` 新建纯模型与目录；
- `manifest_parser.py` 和 `package_validator.py` 的 report_backend 语义；
- report_backend fixture、专属测试和本审核包。

禁止范围：SkillRuntime Service/Worker/Protocol、`main.py`、Report Workbench、Word/PPT Builder、Report Bridge、用户 AppData、插件执行/安装、报告生成和 GitHub 远程写入。

---

## 2. Git 起点

```text
branch = llama-cpp
HEAD = 8a0105527857bbefd34426817ccc0cbbf23102b6
status --untracked-files=all = 440
tracked diff-name count = 50
cached count = 0
dp_engine/report_backend exists = False
```

仓库开始时已经高度脏化，且 `dp_engine/skills/models.py`、`manifest_parser.py`、`package_validator.py` 和相关 fixture 均已是未跟踪用户文件。因此标准 `git diff` 无法显示这些文件的增量。本批用起点/结束 SHA-256、精确路径范围和禁止文件哈希共同审核，未清理或暂存用户变化。

---

## 3. 实现结果

### 3.1 纯合同模型

`dp_engine/report_backend/models.py` 新增：

- 合同版本 `1`；
- 支持产物 `pptx/docx`；
- 模板模式 `none/normalized`；
- 最小权限 allowlist；
- `ReportBackendIssue`、`ReportBackendDescriptor`；
- `validate_report_backend_manifest()`。

稳定错误码覆盖：合同缺失/非法/不支持、生成入口缺失/非法、产物缺失/不支持、模板模式缺失/非法/不支持、能力越权和非 report_backend。

### 3.2 只读目录

`ReportBackendCatalog`：

- 只读取一次 Registry immutable snapshot；
- 忽略 instruction/executable；
- 分类 manifest、enabled、active、health、格式和模板模式；
- 保留不兼容原因，支持按格式/模板模式过滤；
- 不读取安装目录、不执行入口、不修改 Registry。

### 3.3 安装期 fail closed

`SkillPackageValidator` 现在：

- 对 `report_backend` 调用严格语义校验；
- 将 `module.py:function` 解析成 module 路径后检查文件；
- path-only `entrypoints.generate` 明确拒绝；
- 不把 `report_backend_contract/template_modes` 错报为未知字段；
- instruction/executable 的通用前向兼容行为不变。

### 3.4 Fixture

`valid_report_skill` 改成合同 v1 规范 fixture：安全能力、callable generate、PPTX、无模板/标准化模板。它仍不代表真实 PPT Master，也未执行。

---

## 4. 测试证据

### 4.1 Red 基线

```powershell
$env:QT_QPA_PLATFORM='offscreen'; C:\Python314\python.exe -m pytest tests\test_report_backend_catalog.py -q -p no:cacheprovider
```

```text
collected 27
27 failed
```

失败符合预期：新模块、安装期语义校验和 Catalog 均尚不存在，callable generate 被旧 Package Validator 误判。

### 4.2 首轮 Green 调试

同一命令首轮实现后：

```text
25 passed, 2 failed
```

两个失败来自测试对既有 Registry 的错误假设：首个版本会自动 active/enable。只修正测试，通过安装第二版本并切换 active 构造非 active 状态；没有为测试弱化生产实现。

### 4.3 专属最终结果

Catalog 改用单一 snapshot 后最后复核：

```text
collected 27
27 passed
0 failed
0 skipped
0 deselected
```

### 4.4 既有受影响回归

```powershell
$env:QT_QPA_PLATFORM='offscreen'; C:\Python314\python.exe -m pytest tests\test_skills_models.py tests\test_skill_package.py -q -p no:cacheprovider
```

```text
collected 139
139 passed
0 failed
0 skipped
0 deselected
```

该集合完整包含两项 Installer 安全哨兵：

- `TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry`
- `TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction`

唯一节点算术：27 新增 + 139 既有 = **166 passed**。red/green 调试重跑不重复计数；按规划未提前执行全仓测试。

---

## 5. 静态检查

### 5.1 Pyright

```powershell
C:\Python314\Scripts\pyright.exe dp_engine\report_backend\__init__.py dp_engine\report_backend\models.py dp_engine\report_backend\catalog.py dp_engine\skills\manifest_parser.py dp_engine\skills\package_validator.py tests\test_report_backend_catalog.py tests\fixtures\skills\valid_report_skill\workflows\generate.py
```

```text
0 errors, 0 warnings, 0 informations
```

Catalog snapshot 小改后又定点执行 Pyright，结果同为 0/0/0。

### 5.2 Compileall

```powershell
C:\Python314\python.exe -m compileall -q dp_engine\report_backend dp_engine\skills\manifest_parser.py dp_engine\skills\package_validator.py tests\test_report_backend_catalog.py tests\fixtures\skills\valid_report_skill\workflows\generate.py
```

正常退出，无输出；Catalog 小改后定点 compileall 同样通过。

---

## 6. 危险行为搜索

```powershell
rg -n "\beval\b|\bexec\b|os\.system|subprocess\.|Popen|shell\s*=\s*True|requests\.|urllib\.|socket\.|QThread|\.save\(|\.register\(|set_active_version\(|set_enabled\(" dp_engine\report_backend dp_engine\skills\manifest_parser.py dp_engine\skills\package_validator.py
```

结果：0 命中（`rg` exit 1）。新目录无执行、网络、shell、线程或 Registry 写入。新增测试搜索 `pytest.skip/xfail` 同为 0 命中。

---

## 7. 关键代码位置

| 合同 | 位置 |
|---|---|
| typed issue/descriptor | `dp_engine/report_backend/models.py:35,43` |
| manifest semantic validator | `dp_engine/report_backend/models.py:85` |
| read-only catalog | `dp_engine/report_backend/catalog.py:16` |
| list/filter APIs | `dp_engine/report_backend/catalog.py:22,94` |
| known extension keys | `dp_engine/skills/manifest_parser.py:56,322` |
| install-time semantic gate | `dp_engine/skills/package_validator.py:112` |
| callable path extraction | `dp_engine/skills/package_validator.py:129` |

---

## 8. 范围审核

新增路径：

```text
dp_engine/report_backend/__init__.py
dp_engine/report_backend/models.py
dp_engine/report_backend/catalog.py
tests/test_report_backend_catalog.py
docs/agents/batch-3.5.1-audit-package.md
```

修改的既有未跟踪路径：

```text
dp_engine/skills/manifest_parser.py
dp_engine/skills/package_validator.py
tests/fixtures/skills/valid_report_skill/SKILL.md
tests/fixtures/skills/valid_report_skill/workflows/generate.py
```

`dp_engine/skills/models.py` 未修改，SHA-256 保持：

```text
6A244D6BD67F90736F45E01A03D90693E3DEDE7C68DCAB5520A1B277DB4B98B2
```

以下禁止文件结束哈希与起点完全一致：

- `dp_engine/skills/registry.py`
- `dp_engine/skills/runtime_service.py`
- `dp_engine/skills/runtime_worker.py`
- `main.py`
- `ui/report_workbench.py`
- `dp_engine/report_builder/word_builder.py`
- `dp_engine/report_builder/ppt_builder.py`

cached count 始终为 0；未暂存、提交、还原或清理用户文件。

---

## 9. 门槛判定

| 门槛 | 结果 |
|---|---|
| 本批范围测试 | PASS — 27/27 |
| 受影响既有回归 | PASS — 139/139 |
| skip/deselect | PASS — 0/0 |
| Installer 安全哨兵 | PASS — 包含于 139/139 |
| Pyright | PASS — 0 errors / 0 warnings |
| Compileall | PASS |
| 危险行为搜索 | PASS — 0 命中 |
| 禁止范围哈希 | PASS |
| Git cached | PASS — 0 |

**最终结论：Batch 3.5.1 可提交用户审核。审核通过后方可进入 Batch 3.5.2；当前仍没有执行插件、安装 PPT Master 或让报告主链调用外部后端。**
