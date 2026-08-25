# Batch 3.2.1C — Artifact Core 合同审计、统一回归与封板候选审核包

**日期**: 2026-07-22
**分支**: llama-cpp
**状态**: 已完成 — 提交外部审核
**批次类型**: 合同审计 + 统一回归 + 封板候选

---

## 1. 最小Markdown读取声明

```text
已读取并遵守CLAUDE.md。
已读取已冻结的batch-3.2-planning-package.md。
已读取已封板的batch-3.2.1B-audit-package.md。
采用最小Markdown上下文原则，未读取其他历史Markdown。
当前只执行Batch 3.2.1C。
```

实际读取：

| 文件 | 行数 | 用途 |
|------|------|------|
| `CLAUDE.md` | 79 | 项目执行纪律 |
| `docs/agents/batch-3.2-planning-package.md` | 1831 | Batch 3.2冻结合同 |
| `docs/agents/batch-3.2.1B-audit-package.md` | 977 | A/B实现、历史整改、407基线 |

---

## 2. 唯一目标

完成三项：

1. 对Batch 3.2.1最终生产代码和测试合同进行静态审计
2. 运行完整Artifact核心、Runtime和Installer回归
3. 生成Batch 3.2.1最终封板候选审核包

未实现任何新功能。

---

## 3. 实际修改文件

### 3.2.1C 本批P1校正（仅测试代码，共2处）

| 文件 | 修改 | 说明 |
|------|------|------|
| `tests/test_runtime_l3_artifact_security.py` | 重写第434-593行 | `TestWindowsPathSecurity` 增加 `if os.name == "nt":` 模块级条件定义 |
| `tests/test_runtime_artifact_store.py` | 第175行 | `test_hash_mismatch_skipped` → `test_hash_mismatch_excluded` |

### 3.2.1B 既有修改（未改动）

```text
dp_engine/skills/runtime_paths.py       — 3.2.1B
dp_engine/skills/runtime_artifacts.py   — 3.2.1B
dp_engine/skills/runtime_worker.py      — 3.2.1B
dp_engine/skills/runtime_protocol.py    — 3.2.1A → 3.2.1B
dp_engine/skills/runtime_service.py     — 3.2.1B
tests/test_runtime_l2_artifact_publish.py  — 3.2.1B 新增
tests/test_runtime_l3_artifact_security.py — 3.2.1B 新增 (3.2.1C 校正)
tests/test_runtime_artifact_store.py       — 3.2.1B 新增 (3.2.1C 校正)
```

### 3.2.1A 已封包修改（未改动）

```text
dp_engine/skills/runtime_models.py       — 3.2.1A
tests/test_runtime_l1_models.py          — 3.2.1A
tests/test_runtime_l3_protocol_env.py    — 3.2.1A
```

### 禁止范围确认

```text
零生产代码修改
零fixture修改
零UI修改
零Report修改
零配置文件修改
未开始3.2.2
未开始3.3
```

---

## 4. 静态合同审计结论

### 4.1 Context与Wire ✅

- `ArtifactRunContext` 仍是 `dict` 子类（`runtime_artifacts.py:145`）
- 旧context键和值不变：`context["params"]`、`context["skill_id"]` 等
- `has_fatal_declaration_error` 不可被技能吞掉（`runtime_artifacts.py:185-192`）
- Worker检测fatal flag后返回 `protocol_error`（`runtime_worker.py:569-585`）
- Worker只生成 `ArtifactDeclaration`，不含主机字段（`from_wire_dict` 拒绝 artifact_id/storage_relpath 等）
- `artifact_declarations` 不进入公开 `SkillRuntimeResponse`（`runtime_protocol.py:765-766`）
- Wire上限 32768 bytes（`runtime_models.py:190`）
- metadata聚合上限 24 * 1024 bytes（`runtime_models.py:194`）

### 4.2 Healthcheck兼容 ✅

- `_execute_healthcheck` 保持既有自动收集output文件行为（`runtime_worker.py:438-468`）
- 未迁移到 `declare_artifact`
- 旧 `sha256=None` 兼容（`runtime_worker.py:451`）
- 普通run未声明文件不发布（`runtime_worker.py:744-745`）
- 业务result中的路径字符串不自动发布

### 4.3 源路径安全 ✅

- output唯一源根（`runtime_worker.py:588-593`）
- 每级父组件lstat（`runtime_worker.py:925-956`）
- symlink拒绝（`S_ISLNK`，line 615-618）
- Windows reparse point拒绝（`FILE_ATTRIBUTE_REPARSE_POINT`，line 621-626）
- hardlink `st_nlink > 1` 拒绝（line 641-645）
- 目录和特殊文件拒绝
- 单文件及总大小限制
- open+fstat TOCTOU比较（`runtime_artifacts.py:886-901`）
- 流式SHA256比较（`runtime_artifacts.py:926-932`）

### 4.4 类型嗅探 ✅

| 类型 | 嗅探规则 | 位置 |
|------|---------|------|
| PDF | `%PDF-` magic | `runtime_artifacts.py:510-516` |
| PNG | `\x89PNG\r\n\x1a\n` magic | `runtime_artifacts.py:519-525` |
| JPEG | `\xFF\xD8\xFF` SOI | `runtime_artifacts.py:528-534` |
| DOCX | ZIP + `[Content_Types].xml` + `word/` | `runtime_artifacts.py:537-561` |
| PPTX | ZIP + `[Content_Types].xml` + `ppt/` | 同上 |
| XLSX | ZIP + `[Content_Types].xml` + `xl/` | 同上 |
| ZIP | 有效ZIP | `runtime_artifacts.py:564-572` |
| JSON | UTF-8/UTF-8-SIG，拒绝NaN/Infinity | `runtime_artifacts.py:575-594` |
| TXT/CSV | UTF-8/UTF-8-SIG，拒绝NUL | `runtime_artifacts.py:597-614` |

拒绝：SVG（`_REJECTED_EXTENSIONS`）、EXE/DLL/BAT/CMD/PS1/JS/PY/VBS/MSI、无扩展名、双扩展名伪装。

hint冲突 → `protocol_error`（`runtime_artifacts.py:491-494`）。

JSON拒绝NaN和Infinity（`runtime_artifacts.py:617-628`）。

### 4.5 Publisher事务 ✅

- 最终task目录不预创建（`runtime_artifacts.py:698-705`）
- `.commit_<uuid>` 临时目录（line 698）
- 文件和manifest均fsync（line 770-780）
- `os.replace` 唯一提交点（line 795）
- commit_fingerprint幂等（line 1009-1025）
- 不同fingerprint拒绝覆盖（line 1011-1015）
- 多Artifact全有或全无
- 失败rollback清理临时目录（`_rollback`，line 1027-1033）
- 提交前cancel回滚（`_CancelBeforeCommitError`，line 717-719, 783-785）
- 提交后late cancel保持succeeded（`runtime_service.py:448-452`）

### 4.6 目标根与ArtifactStore ✅

- artifact_root路径安全检查（`verify_artifact_root_safety`）
- skill目录非symlink验证（`verify_path_not_symlink_or_reparse`）
- ArtifactStore通过manifest验证定位（`list_task`）
- storage_relpath不由UI解析
- list_task验证manifest、owner、文件完整性
- export验证size/hash并原子提交
- delete_task验证manifest/owner/path逃逸
- manifest损坏或owner不匹配时fail closed
- locate使用固定launcher、shell=False

---

## 5. 平台条件定义 ✅

```python
# test_runtime_l3_artifact_security.py

# POSIX-only（Windows不定义、不收集）
if os.name != "nt":
    class TestPosixPathSecurity: ...  # line 363-365

# Windows-only（POSIX不定义、不收集）— 3.2.1C校正
if os.name == "nt":
    class TestWindowsPathSecurity: ...  # line 437-439
```

**3.2.1C校正前**：`TestWindowsPathSecurity` 在模块级无条件定义，仅靠内部 `assert os.name == "nt"` 守卫。

**3.2.1C校正后**：`if os.name == "nt":` 模块级条件定义。Windows不定义/不收集POSIX节点，POSIX不定义/不收集Windows节点。

---

## 6. Hash不匹配测试语义 ✅

节点ID：`TestArtifactStoreCore::test_hash_mismatch_excluded`（3.2.1C重命名自 `test_hash_mismatch_skipped`）

实际断言：
1. 发布合法artifact（`data.json`，content=`{"result": 42}`）
2. 破坏文件内容（写入 `b"corrupted content"`）
3. `store.list_task("hash1", "t1")` 返回 `len(result) == 0`

语义：**hash不匹配时list_task以fail-closed方式排除损坏Artifact**。不是skip——是被排除。

原始名称 `test_hash_mismatch_skipped` 易误解为"test was skipped"。校正名称 `test_hash_mismatch_excluded` 准确反映语义。

---

## 7. Skip/XFail/Fallback扫描 ✅

```bash
rg -n "pytest\.skip|pytest\.mark\.skip|skipif|pytest\.mark\.skipif|xfail|pytest\.mark\.xfail" \
  tests/test_runtime_l2_artifact_publish.py \
  tests/test_runtime_l3_artifact_security.py \
  tests/test_runtime_artifact_store.py
```

| 文件 | 匹配数 |
|------|--------|
| `test_runtime_l2_artifact_publish.py` | 0 |
| `test_runtime_l3_artifact_security.py` | 0 |
| `test_runtime_artifact_store.py` | 0 |
| **合计** | **0** ✅ |

```bash
rg -n "except OSError|except Exception" \
  tests/test_runtime_l3_artifact_security.py \
  tests/test_runtime_artifact_store.py
```

| 文件 | 匹配数 |
|------|--------|
| `test_runtime_l3_artifact_security.py` | 0 |
| `test_runtime_artifact_store.py` | 0 |
| **合计** | **0** ✅ |

人工确认：无攻击对象创建失败后改测普通文件的分支。无mock伪造symlink/junction/reparse/hardlink。

---

## 8. 关键安全节点结果

### Junction（3次）

| 运行 | 结果 |
|------|------|
| Run 1 | 1 passed in 0.44s |
| Run 2 | 1 passed in 0.53s |
| Run 3 | 1 passed in 0.13s |

### Hardlink（3次）

| 运行 | 结果 |
|------|------|
| Run 1 | 1 passed in 0.09s |
| Run 2 | 1 passed in 0.08s |
| Run 3 | 1 passed in 0.07s |

### ArtifactStore Reparse（3次）

| 运行 | 结果 |
|------|------|
| Run 1 | 1 passed in 0.14s |
| Run 2 | 1 passed in 0.11s |
| Run 3 | 1 passed in 0.13s |

### 其他关键节点

| 节点 | 结果 |
|------|------|
| ADS拒绝 | 1 passed in 0.32s |
| Hash不匹配排除 | 1 passed in 0.39s |
| Different fingerprint拒绝 (2 files) | 2 passed in 0.08s |
| Cancel before commit回滚 | 1 passed in 0.06s |

**所有关键节点：passed, 0 failed, 0 skipped, 0 deselected。正常退出。**

---

## 9. Collect

### Artifact核心collect

```bash
python -m pytest \
  tests/test_runtime_l2_artifact_publish.py \
  tests/test_runtime_l3_artifact_security.py \
  tests/test_runtime_artifact_store.py \
  --collect-only -q
```

**69 collected**（匹配3.2.1B基线）

### Runtime统一collect

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
  --collect-only -q
```

**407 collected**（匹配3.2.1B基线）

### 节点数变化解释

总数无变化（69 + 338 = 407）。`TestWindowsPathSecurity` 在Windows上仍然定义和收集（3 node），`TestPosixPathSecurity` 不收集（`if os.name != "nt"`）。

---

## 10. T0（编译和静态检查）

```bash
python -m compileall -f \
  dp_engine/skills/runtime_models.py \
  dp_engine/skills/runtime_protocol.py \
  dp_engine/skills/runtime_worker.py \
  dp_engine/skills/runtime_service.py \
  dp_engine/skills/runtime_paths.py \
  dp_engine/skills/runtime_artifacts.py
```

**Compiling... 0 errors**

```bash
pyright \
  dp_engine/skills/runtime_models.py \
  dp_engine/skills/runtime_protocol.py \
  dp_engine/skills/runtime_worker.py \
  dp_engine/skills/runtime_service.py \
  dp_engine/skills/runtime_paths.py \
  dp_engine/skills/runtime_artifacts.py
```

**0 errors, 0 warnings, 0 informations**

---

## 11. T2-C（Artifact核心正式测试）

```bash
python -m pytest \
  tests/test_runtime_l2_artifact_publish.py \
  tests/test_runtime_l3_artifact_security.py \
  tests/test_runtime_artifact_store.py \
  tests/test_runtime_l2_subprocess.py \
  tests/test_runtime_l3_security_boundary.py \
  tests/test_runtime_l3_protocol_env.py \
  -q
```

**219 passed in 33.16s**
- 0 failed
- 0 skipped
- 0 deselected
- 0 xfailed
- 0 xpassed
- 正常退出

---

## 12. T3-C（Runtime统一回归）

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

**407 passed in 139.92s (0:02:19)**
- 0 failed
- 0 skipped
- 0 deselected
- 0 xfailed
- 0 xpassed
- 正常退出，无挂起

---

## 13. Installer哨兵

```bash
python -m pytest \
  tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry \
  tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction \
  -q
```

**2 passed in 0.08s**
- 0 failed
- 0 skipped
- 0 deselected

---

## 14. Git范围

### 3.2.1C本批修改

| 文件 | 类型 | 说明 |
|------|------|------|
| `tests/test_runtime_l3_artifact_security.py` | 修改 | `TestWindowsPathSecurity` 增加 `if os.name == "nt":` 模块级保护 |
| `tests/test_runtime_artifact_store.py` | 修改 | `test_hash_mismatch_skipped` → `test_hash_mismatch_excluded` |
| `docs/agents/batch-3.2.1C-audit-package.md` | 新增 | 本审核包 |

### 3.2.1B既有无改动

```text
dp_engine/skills/runtime_worker.py, runtime_service.py,
runtime_paths.py, runtime_artifacts.py, runtime_protocol.py
tests/test_runtime_l2_artifact_publish.py,
tests/test_runtime_l3_artifact_security.py (3.2.1B base),
tests/test_runtime_artifact_store.py (3.2.1B base)
```

### 3.2.1A已封板无改动

```text
dp_engine/skills/runtime_models.py
tests/test_runtime_l1_models.py
tests/test_runtime_l3_protocol_env.py
```

### 更早工作区修改

既有的33个tracked文件修改（chart, report, UI, etc.）不在本批范围。

### 证明

- **零生产代码修改**
- **零fixture修改**
- **零UI修改**
- **零Report修改**
- **未开始3.2.2**
- **未开始3.3**

---

## 15. P0/P1/P2

### P0

```text
无 — 所有合同审计通过，所有测试全绿，无生产代码修改。
```

### P1（3.2.1C已处理）

```text
P1-1: TestWindowsPathSecurity平台条件定义 — 3.2.1C已校正为模块级 if os.name == "nt": 保护
P1-2: test_hash_mismatch测试名称 — 3.2.1C已重命名为 test_hash_mismatch_excluded
```

### P2

```text
无新增
```

---

## 16. Batch 3.2.1封板候选结论

```text
Batch 3.2.1A + 3.2.1A-R + 3.2.1A-S + 3.2.1B + 3.2.1B-R + 3.2.1B-E + 3.2.1C 联合封板候选：

- ArtifactRunContext (dict子类) + declare_artifact API 完整
- ArtifactDeclaration (Worker内部) → RuntimeArtifact (Service权威) 两阶段协议完整
- Healthcheck既有自动收集行为保持不变
- Worker文件系统观察（lstat、symlink、reparse、hardlink、SHA256）完整
- Service侧二次验证 + TOCTOU防御完整
- ArtifactPublisher原子发布（.commit_<uuid> + os.replace + fingerprint幂等）完整
- ArtifactStore安全消费API（list_task/locate/export/delete_task）完整
- 类型嗅探：PDF/PNG/JPEG/DOCX/PPTX/XLSX/ZIP/JSON/TXT/CSV
- 明确拒绝：SVG/EXE/DLL/BAT/CMD/PS1/JS/PY/VBS/MSI/无扩展名/双扩展名
- Windows真实Junction/Hardlink创建+拒绝
- ArtifactStore真实reparse路径链替换+拒绝
- NTFS ADS真实创建+路径语法拒绝
- POSIX symlink/fifo条件定义（Windows不收集）
- TestWindowsPathSecurity模块级平台条件定义（3.2.1C校正）
- test_hash_mismatch_excluded语义准确（3.2.1C校正）
- 全部skip/xfail已消除（在允许修改范围内）
- 407项Runtime统一回归全绿
- 219项Artifact核心测试全绿
- 2项Installer哨兵通过
- compileall 0 errors
- pyright 0 errors, 0 warnings, 0 informations
- 零生产代码修改（3.2.1C本批）
```

---

## 17. 审核包实物信息

| 属性 | 值 |
|------|-----|
| 绝对路径 | `D:\桌面文件\软件项目_qt6\docs\agents\batch-3.2.1C-audit-package.md` |
| 仓库相对路径 | `docs/agents/batch-3.2.1C-audit-package.md` |

---

## 18. 最终声明

```text
Batch 3.2.1C已完成并提交外部审核。
Batch 3.2.1当前为封板候选，尚未自行宣布外部审核通过。
未开始Batch 3.2.2。
未开始Batch 3.3 Report bridge。
等待外部审核结论。
```
