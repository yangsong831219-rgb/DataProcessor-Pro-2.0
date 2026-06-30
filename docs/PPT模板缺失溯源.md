# PPT 模板缺失溯源 — Package not found at ''

**日期**: 2026-06-25  
**分支**: llama-cpp  
**状态**: 只记现状，不给方案

**真机现象**: 报告类型选"PPT 演示汇报"、模板文件未选 → 大纲 OK → "基于大纲生成完整报告" → `报告生成失败: Package not found at ''`

---

## 1. 错误来源 — `Presentation('')` 空路径抛错

**调用链**：
```
main.py:1313  builder.build_ppt_report(report, template_path, output_path, project_dir=project_dir)
  → ppt_builder.py:274  prs = Presentation(template_path)
```

**`ppt_builder.py:274`** — 无条件传 `template_path`，**无空值 guard**：

```python
def build_ppt_report(
    self,
    report_data: PPTReport,
    template_path: str,       # ← 当值为 '' 时
    output_path: str,
    project_dir: str = '',
) -> str:
    ...
    prs = Presentation(template_path)  # ← Presentation('') → FileNotFoundError → "Package not found at ''"
```

`python-pptx` 的 `Presentation(path)` 必须传入已存在的 .pptx 文件路径。空字符串 `''` 不是有效路径 → `Package not found at ''`。

---

## 2. 模板路径取值链

**UI 层** — `report_workbench.py:110`：

```python
self._template_file_path: str = ''   # 初始值: 空字符串
```

**config 传递** — `report_workbench.py:131`：

```python
'template_file': self._template_file_path,   # 未选模板 → ''（空字符串，不是 None）
```

**消费端** — `main.py:1204`：

```python
template_path = config.get('template_file', '')   # 未选 → ''（default ' ' 无效——键存在值为 ''）
```

> `config.get('template_file', '')` 的 default `''` 在键**缺失**时生效。但 `get_config()` 始终写入该键（值为 `self._template_file_path`），键始终存在 → `config.get(...)` 永远返回 `''`（未选时），default 永不触发。即 `template_path` 在未选时 **必然是 `''`**，不可能是 `None`。

**传给 PPT 构建器** — `main.py:1313`：

```python
builder.build_ppt_report(report, template_path, output_path, project_dir=project_dir)
```

---

## 3. Word vs PPT 差异 — 为什么 Word 未选模板能工作

| 维度 | Word 路径 | PPT 路径 |
|------|----------|----------|
| 构建器入口 | `WordBuilder.build_word_report()` (`word_builder.py:559`) | `PPTBuilder.build_ppt_report()` (`ppt_builder.py:253`) |
| 模板处理 | **line 567**: `doc = Document(template_path) if template_path else Document()` | **line 274**: `prs = Presentation(template_path)` |
| 空字符串行为 | `''` → `falsy` → `Document()` 走默认空白文档 | `''` → 直接传给 `Presentation('')` → **抛错** |
| 有 guard？ | ✅ ternary: `if template_path else ...` | ❌ 无 guard |

**另注**: `PPTBuilder.build()` (`ppt_builder.py:54-57`)**有**正确的 guard：

```python
if report.template_path and Path(report.template_path).exists():
    prs = Presentation(report.template_path)
else:
    prs = Presentation()       # 默认空白模板
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
```

但 `main.py:1313` **不走** `build()`，走的是 `build_ppt_report()`。`build_ppt_report()` 是后来加的（支持模板 Slide 0 标题修改），写的时候漏了空模板 fallback。

---

## 4. `Presentation()` 无参默认模板可行性

**python-pptx 行为**：`Presentation()` 无参时使用内置空白模板（默认 4:3 版式）。

**改成 fallback 的可行方案**：在 `ppt_builder.py:274` 加 guard：

```python
# 与 build() 的 guard (line 54-57) 同构
if template_path and Path(template_path).exists():
    prs = Presentation(template_path)
else:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
```

**差异**：无模板 fallback 会：
- 失去标题页模板（用户需自行设计标题样式）
- `_update_title_slide` 在 0-slide prs 上调用 → `len(prs.slides) == 0` → 直接 return，不崩 (`ppt_builder.py:297`)
- `_add_content_slide` 需要无模板时找到合适版式 → 需确认 `_get_content_layout` 在 default blank 模板上的行为（可能返回 blank layout → 内容正常渲染）

**评估**：技术上可行，但需验证空白模板下 `_add_content_slide` 的版式选择逻辑。

---

## 5. fail-loud 落点评估

| 落点 | 位置 | 机制 | 推荐 |
|------|------|------|------|
| **(a) UI 按钮校验** | `report_workbench.py` — PPT 被选时加提示 | PPT 模式 + 未选模板 → 弹框或灰按钮 | ⭐⭐ — 提前告知但 UX 差（用户可能故意不用模板） |
| **(b) 构建器入口 guard** | `ppt_builder.py:274` | `if not template_path → Presentation()` 用默认 | ⭐⭐⭐ — 最治本、与 `build()` 同构、零 UI 改动 |
| **(c) main.py 校验** | `main.py:1204-1208` | `if report_type == 'ppt' and not template_path: raise RuntimeError` | ⭐⭐ — 也行但 PPT 模式完全可无模板生成 |

**最佳落点 (b)**：与 `PPTBuilder.build()` (line 54-57) 保持同构——`template_path` 为空或不存在时 `Presentation()` 无参默认。Word 路径 `build_word_report` 也是这个策略（`if template_path else Document()`）。最简、最一致。
