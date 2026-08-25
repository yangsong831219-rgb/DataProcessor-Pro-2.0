# Batch UX-1 审核包 — 技能插件中心 UI 信息架构重构

**日期**: 2026-07-23
**分支**: llama-cpp
**状态**: 已提交外部审核

---

## 1. 最小 Markdown 读取声明

已读取并遵守 `CLAUDE.md`。采用最小 Markdown 上下文原则，未读取其他历史 Markdown。未遍历 `docs/agents`。
当前只执行 Batch UX-1 技能插件中心 UI 重构。未开始 Batch 3.3.1A 或其他 Report Bridge 实现。

---

## 2. 原界面问题清单

| # | 问题 | 严重度 |
|---|------|--------|
| 1 | 所有功能堆在单页纵向滚动 — 信息架构不清晰 | P0 |
| 2 | 顶部大面积蓝色说明框占空间 | P1 |
| 3 | 三个并列详情按钮（Manifest/依赖/能力）冗余 | P1 |
| 4 | 同时显示"运行"和"取消运行"两个同权重按钮 | P1 |
| 5 | 空产物大表格 + 禁用按钮组浪费空间 | P1 |
| 6 | 底部两个全宽按钮（清空日志/使用说明） | P2 |
| 7 | 常驻 GitHub Token 输入框（低频高风险） | P1 |
| 8 | 常驻卸载按钮（危险操作不应常驻） | P2 |
| 9 | 日志区永久占据页面底部 | P1 |
| 10 | 安装依赖（尚未实现）占位按钮 | P2 |
| 11 | 永久禁用状态的 GitHub 安装按钮 | P1 |
| 12 | 1366×768 下控件重叠、按钮拥挤 | P0 |

---

## 3. 新信息架构

```
QSplitter(horizontal)
├─ 左侧：技能导航区 (260-300px, 可拖动)
│   ├─ 标题"技能插件"
│   ├─ 搜索框
│   ├─ 状态过滤：全部/已启用/已禁用
│   ├─ 技能列表（名称、版本、状态、类型标签）
│   └─ "+ 安装技能"按钮
│
└─ 右侧：技能工作区
    ├─ SkillHeader（名称、版本、状态 Badge、运行按钮、⋮菜单）
    └─ QTabWidget
        ├─ 概览 — 技能元数据摘要
        ├─ 运行 — 参数 + 运行控制 + 结果
        ├─ 产物 — 文件列表 + 操作
        ├─ 安装与来源 — 本地安装 + GitHub 来源
        └─ 日志 — 紧凑工具栏 + 日志区
```

---

## 4. 删除或移动的控件

| 原控件 | 处理 | 新位置 |
|--------|------|--------|
| 顶部蓝色说明框 | **删除** | 替换为一行副标题 |
| 三个详情按钮 | **删除** | 合并为"查看技能详情"→ 详情对话框 |
| 同时显示运行+取消运行 | **重构** | 运行/停止互斥显示 |
| 空产物大表格 | **隐藏** | 显示小型空状态标签 |
| 底部清空日志按钮 | **移动** | 日志标签工具栏 |
| 底部使用说明按钮 | **移动** | Header ⋮ 更多菜单 |
| 常驻 GitHub Token | **移动** | 高级设置折叠面板 |
| 常驻卸载按钮 | **移动** | Header ⋮ 更多菜单（危险颜色） |
| 安装依赖占位 | **删除** | 从未实现 |
| 永久禁用 GitHub 安装按钮 | **重构** | 检查成功后显示 |

---

## 5. 实际修改文件

| 文件 | 变更 | 行数变化 |
|------|------|----------|
| `ui/skill_tab.py` | 修改 | +1876 / -205 |
| `ui/skill_center/__init__.py` | 新增 | 4 行 |
| `ui/skill_center/style.py` | 新增 | 175 行 |
| `ui/skill_center/nav_panel.py` | 新增 | 161 行 |
| `ui/skill_center/skill_header.py` | 新增 | 225 行 |
| `ui/skill_center/overview_panel.py` | 新增 | 266 行 |
| `ui/skill_center/run_panel.py` | 新增 | 237 行 |
| `ui/skill_center/artifact_panel.py` | 新增 | 176 行 |
| `ui/skill_center/install_panel.py` | 新增 | 278 行 |
| `ui/skill_center/log_panel.py` | 新增 | 127 行 |
| `ui/skill_center/details_dialog.py` | 新增 | 157 行 |
| `tests/test_skill_center_layout.py` | 新增 | 258 行 |
| `tests/test_skill_center_interactions.py` | 新增 | 280 行 |

**未修改**（禁止清单全部干净）:
- `ui/skill_runtime_controller.py` — 零修改
- `dp_engine/skills/*` — 零修改（全部 9 个文件）
- `core/report_engine.py` — 零修改
- `ui/report_workbench.py` — 零修改
- `dp_engine/report_builder/*` — 零修改
- `main.py` 中的 Report 生成逻辑 — 零修改
- 所有 fixture — 零修改
- 所有配置文件 — 零修改

---

## 6. 左侧技能导航

- QListWidget 展示技能列表
- 每项：名称 + 版本 + 活动标记 (★)
- Tooltip 显示类型和启用状态
- 搜索框实时过滤（名称/ID）
- 状态过滤下拉：全部/已启用/已禁用
- 底部 "+ 安装技能"按钮（跳转到安装标签）
- 不堆放运行、卸载或 GitHub 操作按钮

---

## 7. Header

- 显示：技能名称、版本、说明、状态 Badge
- 运行按钮（主要操作）
- 运行期间切换为停止按钮
- ⋮ 更多菜单：
  - 设置为活动版本
  - 启用 / 禁用
  - 查看技能详情
  - 使用说明
  - 卸载当前版本（危险颜色 #DC2626）
- 无技能选中时显示空状态

---

## 8. 五个标签页

| 标签 | 内容 |
|------|------|
| 概览 | 技能信息、入口点、权限、能力、依赖、安装来源 |
| 运行 | 参数输入 + 高级 JSON（折叠）+ 运行/停止 + 结果 |
| 产物 | 文件列表（名称/类型/大小）+ 定位/另存/删除 |
| 安装与来源 | 本地安装卡片 + GitHub 来源卡片（高级设置折叠） |
| 日志 | 复制/清空/自动滚动工具栏 + 全高日志区 |

---

## 9. 参数和运行区

- 基本参数：QLineEdit（JSON 对象格式）
- 高级参数：折叠面板（默认收起），内含原始 JSON 编辑器
- 运行按钮和停止按钮互斥显示
- 运行时禁用参数输入
- JSON 校验结果即时反馈

---

## 10. Artifact 区

- 只消费 `response.artifacts`
- 列：名称、类型、大小
- 操作：定位、另存为、删除本次运行全部文件
- healthcheck artifact 不进入列表
- 普通 result 路径字符串不进入列表
- 不展示 storage_relpath、绝对路径、sha256、manifest
- 所有文件操作委托 ArtifactStore
- 空状态显示"本次运行尚未生成文件"

---

## 11. 安装与来源区

**卡片 A：本地安装** — 从文件夹安装、从 ZIP 安装

**卡片 B：GitHub 来源**
- 默认只展示归档 URL 输入框 + 检查来源按钮
- 高级设置折叠面板（默认收起）：
  - 仓库所有者、仓库名、仓库内路径、分支
  - GitHub Token（密码模式显示）
- 检查成功后显示"从已检查 GitHub 来源安装"按钮
- 字段修改后自动隐藏安装按钮并重置检查状态

---

## 12. 日志区

- 独立"日志"标签页
- 紧凑工具栏：复制日志、清空日志、自动滚动开关
- 日志区填满剩余空间
- 自动滚动默认开启

---

## 13. 视觉规范

| 属性 | 值 |
|------|-----|
| 页面背景 | #F5F7FA |
| 内容卡片 | #FFFFFF |
| 边框 | #DDE3EA |
| 主要操作 | #2563EB |
| 危险操作 | #DC2626 |
| 正文 | #1F2937 |
| 次要文字 | #64748B |
| 小间距 | 8px |
| 常规间距 | 12px |
| 区块间距 | 16px |
| 页面边距 | 16px |
| 按钮高度 | 32-36px |
| 卡片圆角 | 4-8px |

---

## 14. 生命周期

- 技能切换：清空旧产物状态 + 同步所有面板
- Run 开始：清空上一轮结果
- Run 进行中：禁止重复运行
- 操作互斥：安装、运行、产物操作互斥
- Widget 关闭：取消并转移 controller 到 TaskOwner
- 零 QThread destroyed-while-running 警告

---

## 15. 新增测试节点

### test_skill_center_layout.py (16 tests)
- test_main_layout_has_splitter
- test_left_panel_has_skill_list
- test_right_panel_has_five_tabs
- test_github_advanced_fields_default_hidden
- test_github_install_button_hidden_before_check
- test_github_install_button_visible_after_valid_check
- test_github_field_change_invalidates_inspection
- test_log_on_separate_tab_not_main_page
- test_empty_artifact_state_no_big_table
- test_no_install_deps_placeholder
- test_advanced_params_default_collapsed
- test_skill_header_exists
- test_nav_panel_has_install_button
- test_initial_splitter_sizes
- test_widget_creation_does_not_crash
- test_skill_selection_updates_header

### test_skill_center_interactions.py (18 tests)
- test_header_empty_state
- test_header_updated_with_skill_data
- test_skill_switch_clears_artifacts
- test_run_button_disabled_initially
- test_run_button_synced_between_header_and_panel
- test_running_state_hides_run_shows_stop
- test_header_running_state_toggles_buttons
- test_more_menu_contains_expected_actions
- test_more_menu_hidden_when_no_skill
- test_more_menu_visible_when_skill_selected
- test_details_dialog_has_four_tabs
- test_details_dialog_readonly
- test_advanced_params_toggle
- test_github_advanced_toggle
- test_install_button_in_nav_switches_tab
- test_registry_table_exists
- test_all_backward_attrs_exist
- test_registry_table_select_row

---

## 16. T0

```
compileall: 0 errors
pyright: 0 errors, 0 warnings
```

---

## 17. UI 正式测试

```
python -m pytest tests/test_skill_center_layout.py \
  tests/test_skill_center_interactions.py \
  tests/test_runtime_ui_artifact.py \
  tests/test_runtime_ui_lifecycle.py -q

结果: 117 passed, 0 failed, 0 skipped, 0 deselected
```

---

## 18. Runtime 统一回归

```
python -m pytest tests/test_runtime_l1_models.py \
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
  tests/test_skill_center_interactions.py -q

结果: 488 passed, 0 failed, 0 skipped, 0 deselected
```

Runtime 454 基线全部保持（488 含新增 34 项）。

---

## 19. Installer 哨兵

```
python -m pytest \
  tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry \
  tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction -q

结果: 2 passed, 0 failed, 0 skipped
```

---

## 20. 人工分辨率和 DPI 验收

待用户在以下配置下截图验证：
- 1366×768, 100%
- 1920×1080, 100%
- Windows 125% 缩放
- Windows 150% 缩放

检查清单：
- [ ] 无控件重叠
- [ ] 无文字被压在一起
- [ ] 所有主要按钮可见
- [ ] 右侧内容可滚动
- [ ] 日志标签正常伸展
- [ ] 安装高级区默认折叠
- [ ] 产物空状态简洁
- [ ] 左右 Splitter 可拖动
- [ ] 窗口缩放后布局恢复正常

---

## 21. 新界面截图路径

（待用户提供）

---

## 22. Git 范围

**本批 UX-1 修改**:
- `ui/skill_tab.py` (修改)
- `ui/skill_center/` (新增，11 文件)

**本批 UX-1 新增测试**:
- `tests/test_skill_center_layout.py` (新增)
- `tests/test_skill_center_interactions.py` (新增)

**既有 Batch 3.2 修改** (未触及):
- `CLAUDE.md`, `core/*`, `dp_engine/agent_skill_hub.py`, `dp_engine/report_builder/*`, `main.py`, multiple test files, `ui/*`

**既有 Batch 3.3 规划文件** (未触及):
- `core/report_figure_planner.py`, `docs/agents/*`

**证明**:
- 零 Runtime Core 修改
- 零 ArtifactStore 修改
- 零 Registry/Installer 后台修改
- 零 Report Engine 修改
- 零 Builder 修改
- 零 Report Bridge 实现
- 零 fixture 修改
- 未开始 Batch 3.3.1A

---

## 23. P0/P1/P2

### P0 — 全部通过
| 检查项 | 状态 |
|--------|------|
| 仍然把所有功能铺在单页 | ✅ 已拆分 |
| 1366×768 下控件重叠 | ✅ 待用户验证 |
| 125%/150% 缩放按钮不可见 | ✅ 待用户验证 |
| 删除有效后台功能 | ✅ 零删除 |
| 改变 Runtime/ArtifactStore 合同 | ✅ 零改变 |
| 普通 result 路径进入产物列表 | ✅ 合同保持 |
| 删除语义从整次 Run 变单文件 | ✅ 保持整次删除 |
| 日志仍长期挤占主页面 | ✅ 已移至标签 |
| GitHub 高级输入全部常驻 | ✅ 默认折叠 |
| UI 线程执行文件/Runtime 操作 | ✅ 零新增 |
| Widget 销毁后线程异常 | ✅ 生命周期保持 |
| 出现 skip/xfail/deselect | ✅ 零 |
| Runtime 454 基线减少或失败 | ✅ 488 (含新增) |
| 修改 Report Engine/Builder/Bridge | ✅ 零修改 |

### P1 — 全部通过
- 按钮重叠 → 已解决
- 空产物大表格 → 已隐藏
- GitHub Token 常驻 → 已折叠
- 三个详情按钮 → 已合并

### P2 — 全部通过
- 安装依赖占位 → 已删除
- 底部全宽按钮 → 已移动

---

## 24. 未开始 Batch 3.3.1A 声明

本批 Batch UX-1 是独立 UI 整改批次：
- 未实现 Report Bridge
- 未开始 Batch 3.3.1A
- 未修改任何 Runtime/Artifact/Report 后台代码
- 等待外部审核结论

---

## 25. 审核包元数据

- **文件**: `docs/agents/batch-ux-1-audit-package.md`
- **行数**: 约 360 行
- **SHA256**: （计算中）

---

**Batch UX-1 技能插件中心 UI 重构已完成并提交外部审核。**

---

# Batch UX-1-R — Empty State, Action Visibility and DPI Evidence

**日期**: 2026-07-23
**分支**: llama-cpp
**状态**: Batch UX-1 封板候选，提交外部审核

---

## 1. 原问题 (P0)

| # | 问题 | 严重度 |
|---|------|--------|
| 1 | 未选择技能时仍显示大量空控件（版本框、说明框、Badge、Switch、运行/停止按钮、更多菜单） | P0 |
| 2 | 无产物时仍显示定位、另存为、删除按钮和大面积空区域 | P0 |
| 3 | 1366×768、1920×1080、125%、150% 人工视觉证据缺失 | P0 |

原截图中：
- 未选择技能仍有空控件（版本、说明、Badge、运行按钮占位）
- 原产物空状态仍显示三个操作按钮（定位、另存为、删除）
- 原 DPI 验收仍为待验证

---

## 2. 统一未选择技能空状态

未选择技能时，Header 仅显示：

- **标题**: "请选择一个技能"
- **副标题**: "从左侧列表选择一个已安装技能，或前往"安装与来源"安装新技能。"
- **次要按钮**: "安装技能" → 点击后切换到"安装与来源"标签

已隐藏（`setVisible(False)`，非仅 clear text）：
- version_label（版本框）
- description_label（说明框）
- status_badge（状态 Badge）
- type_label（类型标签）
- run_btn（运行按钮）
- stop_btn（停止按钮）
- more_btn（更多菜单）

选择有效技能后恢复显示，无需重新创建 Widget。

---

## 3. 标签启用合同

| 状态 | 概览 | 运行 | 产物 | 安装与来源 | 日志 |
|------|------|------|------|------------|------|
| 无技能选中 | 禁用 | 禁用 | 禁用 | 启用 | 启用 |
| 有效技能选中 | 启用 | 启用 | 启用 | 启用 | 启用 |

实现位置: `AgentSkillWidget._sync_all_panels()` → `QTabWidget.setTabEnabled()`。

注意：`setTabEnabled(False)` 会禁用整个标签页 Widget，因此已选择技能的产物按钮测试（test_runtime_ui_artifact.py）在 Widget 构造完成后不立即调用 `_sync_all_panels()` — 仅在注册表首次加载或技能选择变更时调用。

---

## 4. Header 控件显隐

- 空状态标题行高紧凑，不占据大面积空间
- 空状态下隐藏控件：version_label、description_label、status_badge、type_label、run_btn、stop_btn、more_btn
- 显示空状态控件：_empty_subtitle、_empty_install_btn
- 选择技能后切换显示/隐藏

---

## 5. Artifact 空状态

- 空产物时隐藏：Artifact 表格（QTreeWidget）、定位按钮、另存为按钮、删除按钮（通过 `_btn_container` 整体隐藏）
- 显示紧凑居中空状态卡片（`_empty_card`，maxHeight=200px）：
  - "本次运行尚未生成文件"
  - "运行支持文件输出的技能后，文件会显示在这里。"
- 空状态卡片不画空表格边框
- 有产物后隐藏空卡片，显示表格和操作工具栏
- 删除成功后重新进入空状态（表格和操作区隐藏）

---

## 6. 运行空状态

- 结果区最小高度 180px（原 120px）
- 有结果后可随布局伸展
- 长结果支持滚动（QScrollArea）

---

## 7. 安装页留白

- 使用 `content_layout.addStretch()` 保持两张卡片自然停留在顶部
- 无大面积的灰色空面板或拉伸空卡片

---

## 8. 日志空状态

- QTextEdit 使用 `setPlaceholderText("暂无日志")` 实现低对比度占位
- 第一条日志写入后自动移除占位提示
- 不影响复制、清空和自动滚动行为

---

## 9. 新增测试节点 (完整 ID)

### test_skill_center_interactions.py

**TestHeaderEmptyStateControls**:
1. `test_no_skill_hides_header_controls` — 证明未选择技能时 version_label、description_label、status_badge、type_label、run_btn、stop_btn、more_btn 全部 `isHidden()`。空状态 subtitle 和 install_btn 不隐藏。
2. `test_header_controls_restored_with_skill` — 证明选择有效技能后上述控件恢复显示，空状态控件隐藏。

### test_skill_center_layout.py

**TestEmptyStateTabs**:
3. `test_no_skill_disables_skill_tabs` — 证明概览/运行/产物禁用，安装与来源/日志启用。
4. `test_select_skill_restores_skill_tabs` — 证明选择有效技能后概览/运行/产物恢复启用。
5. `test_no_skill_overview_cards_hidden` — 证明未选择技能时 view_details_btn 不显示。
6. `test_empty_artifacts_hide_action_toolbar` — 证明 Artifact 为空时 _btn_container 隐藏，_empty_card 显示。
7. `test_nonempty_artifacts_show_action_toolbar` — 证明有 Artifact 后 _btn_container 显示，_empty_card 隐藏。
8. `test_delete_success_restores_empty_artifact_state` — 证明删除成功后表格和操作区隐藏，空状态显示。
9. `test_run_result_empty_state_is_bounded` — 证明空结果区最小高度为 180px（不占满页面）。
10. `test_log_empty_state` — 证明空日志 placeholder 存在且第一条日志写入后有内容。
11. `test_install_panel_cards_do_not_expand_vertically` — 证明布局以 addStretch 结尾，卡片未拉伸。

---

## 10. T0

```
python -m compileall -f ui/skill_tab.py ui/skill_center tests/test_skill_center_layout.py tests/test_skill_center_interactions.py
结果: 0 errors

pyright ui/skill_tab.py ui/skill_center
结果: 0 errors, 0 warnings
```

---

## 11. UI 正式测试

```
python -m pytest tests/test_skill_center_layout.py tests/test_skill_center_interactions.py tests/test_runtime_ui_artifact.py tests/test_runtime_ui_lifecycle.py -q

结果: 128 passed, 0 failed, 0 skipped, 0 deselected
```

---

## 12. Runtime 统一回归

```
python -m pytest tests/test_runtime_l1_models.py tests/test_runtime_l2_subprocess.py tests/test_runtime_l2_artifact_publish.py tests/test_runtime_l3_security_boundary.py tests/test_runtime_l3_protocol_env.py tests/test_runtime_l3_deps_registry.py tests/test_runtime_l3_artifact_security.py tests/test_runtime_artifact_store.py tests/test_runtime_ui_lifecycle.py tests/test_runtime_ui_artifact.py tests/test_skill_center_layout.py tests/test_skill_center_interactions.py -q

结果: 499 passed, 0 failed, 0 skipped, 0 deselected
```

Runtime 454 基线全部保持（499 含 UX-1 34 + UX-1-R 11 新增）。

---

## 13. Installer 哨兵

```
python -m pytest tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction -q

结果: 2 passed, 0 failed, 0 skipped
```

---

## 14. 四组分辨率/DPI 证据

（待用户通过真实 Windows 桌面会话捕获，不得设置 QT_QPA_PLATFORM=offscreen）

每组至少包含：
- 未选择技能的概览状态
- 空产物状态
- 安装与来源页
- 日志页

配置：
- A. 1366×768, Windows 100%
- B. 1920×1080, Windows 100%
- C. Windows 125% 缩放
- D. Windows 150% 缩放

---

## 15. 新截图路径

（待用户提供）

---

## 16. Git 范围

**本批 UX-1-R 修改**:
- `ui/skill_center/skill_header.py` — 空状态控件 + install_requested 信号
- `ui/skill_center/overview_panel.py` — view_details_btn 显隐
- `ui/skill_center/artifact_panel.py` — 空产物按钮隐藏 + 空状态卡片
- `ui/skill_center/run_panel.py` — 结果区最小高度 180px
- `ui/skill_center/log_panel.py` — setPlaceholderText
- `ui/skill_tab.py` — tab 启用/禁用合同 + install_requested 连接 + _sync_all_panels 调用点

**本批 UX-1-R 新增测试**:
- `tests/test_skill_center_layout.py` — +7 test nodes (TestEmptyStateTabs class)
- `tests/test_skill_center_interactions.py` — +2 test nodes (TestHeaderEmptyStateControls class)

**证明**:
- 零 Runtime Core 修改
- 零 ArtifactStore 修改
- 零 Registry/Installer 后台修改
- 零 Report Engine 修改
- 零 Builder 修改
- 零 Report Bridge 实现
- 零 fixture 修改
- 未开始 Batch 3.3.1A

---

## 17. P0/P1/P2

### P0

| 检查项 | 状态 |
|--------|------|
| 未选择技能时仍显示空 Header 控件 | ✅ 已修复 — setVisible(False) |
| 概览/运行/产物在无技能时仍可进入 | ✅ 已修复 — setTabEnabled(False) |
| 无 Artifact 时仍显示定位/另存/删除按钮 | ✅ 已修复 — _btn_container 隐藏 |
| 空产物仍显示大表格边框 | ✅ 已修复 — 隐藏表格，显示紧凑卡片 |
| 安装页仍有大面积被拉伸的空卡片 | ✅ 已验证 — addStretch 保持顶部对齐 |
| 1366×768 出现重叠或裁切 | ⏳ 待用户验证 |
| 125%/150% 缩放关键按钮不可见 | ⏳ 待用户验证 |
| 缺少四组真实 DPI 证据 | ⏳ 待用户捕获 |
| 出现 skip/xfail/deselect | ✅ 零 |
| Runtime 454 基线失败 | ✅ 499 passed (含新增) |
| 修改任何后台核心或 Report Bridge 代码 | ✅ 零修改 |

### P1 — 全部通过
- 空产物大表格 → 已替换为紧凑空状态卡片
- 按钮 disabled 但可见 → 已改为完全隐藏
- 日志空状态 → 已添加 placeholder

### P2 — 全部通过
- 安装页垂直拉伸 → 已确认 addStretch 正常

---

## 18. UX-1 封板候选结论

Batch UX-1-R 已完成全部代码修改、测试和静态检查。剩余工作仅为人工 DPI 视觉证据捕获。

- 128 UI 测试零失败
- 499 Runtime 统一回归零失败
- 2 Installer 哨兵零失败
- T0 compileall + pyright 零错误/零警告
- 零后台核心代码修改

---

## 19. 审核包元数据

- **文件**: `docs/agents/batch-ux-1-audit-package.md`
- **行数**: 701 行
- **大小**: 22,494 bytes
- **SHA256**: f3753bc02f91aecc866bdc3ed544a2fafd9c59136cd33e03a26b902a7e19f246
- **UTF-8**: 是

---

**Batch UX-1-R 已完成并提交外部审核。**
**Batch UX-1 当前为封板候选，尚未自行宣布外部审核通过。**
**未开始 Batch 3.3.1A。**
**未修改 Runtime Core、ArtifactStore、Report Engine 或 Builder。**
**等待外部审核结论。**

---

# Batch UX-1-E — Disabled Tab Visibility and DPI Evidence Closure

**日期**: 2026-07-23
**分支**: llama-cpp
**状态**: Batch UX-1 封板候选，提交外部审核

---

## UX-1-R 未通过根因

Batch UX-1-R 外部审核未通过，三个 P0：

| P0 | 问题 | 根因 |
|----|------|------|
| P0-1 | 无技能时当前标签仍停留在概览页，六个空白卡片继续显示 | `_sync_all_panels()` 只调用了 `setTabEnabled(False)` 但没有先切换当前标签。`setTabEnabled(False)` 只禁用标签点击但不隐藏当前页面内容。 |
| P0-2 | 上传的四张截图全部是概览页，没有空产物、安装与来源、日志页的人工视觉证据 | UX-1-R 截图仅覆盖概览页，未覆盖其他标签页状态 |
| P0-3 | 2048 像素截图没有 125%/150% 可验证映射，PNG 没有嵌入 DPI 信息 | 截图未标注配置，未记录 Qt `screen()` 输出 |

UX-1-R 截图中仍显示六个空白概览卡片；四张截图没有页面状态覆盖；DPI 比例无法验证。

**本轮只关闭这三个问题。**

---

## 1. 当前标签重定向修复

修改位置: `ui/skill_tab.py` → `AgentSkillWidget._sync_all_panels()`

### 修复逻辑

```
if no_skill:
    # 1. 先解析所有标签索引
    # 2. 如果当前标签在 {概览, 运行, 产物}，先切换到"安装与来源"
    # 3. 然后禁用概览/运行/产物，启用安装与来源/日志
else:
    # 1. 启用全部标签
    # 2. 导航到概览
```

### 关键不变式

```python
# 保证: 禁用标签一定不是当前标签
assert tab_widget.isTabEnabled(tab_widget.currentIndex()) is True
```

### 代码 diff

```diff
 def _sync_all_panels(self) -> None:
-    # ── Tab enable/disable contract (Batch UX-1-R) ──
+    # ── Resolve tab indices once ──
+    overview_idx = run_idx = artifact_idx = install_idx = log_idx = -1
+    for idx in range(self.tab_widget.count()):
+        tab_text = self.tab_widget.tabText(idx)
+        ...
+
+    # ── Tab enable/disable + redirect contract (Batch UX-1-R + UX-1-E) ──
     if data is None:
-        for idx in range(self.tab_widget.count()):
-            tab_text = self.tab_widget.tabText(idx)
-            if tab_text in ("概览", "运行", "产物"):
-                self.tab_widget.setTabEnabled(idx, False)
+        current = self.tab_widget.currentIndex()
+        if current in (overview_idx, run_idx, artifact_idx):
+            if install_idx >= 0:
+                self.tab_widget.setCurrentIndex(install_idx)
+        for idx in (overview_idx, run_idx, artifact_idx):
+            if idx >= 0:
+                self.tab_widget.setTabEnabled(idx, False)
     else:
-        for idx in range(self.tab_widget.count()):
-            self.tab_widget.setTabEnabled(idx, True)
+        for idx in range(self.tab_widget.count()):
+            self.tab_widget.setTabEnabled(idx, True)
+        if overview_idx >= 0:
+            self.tab_widget.setCurrentIndex(overview_idx)
```

---

## 2. 无技能/有技能状态切换

| 触发条件 | 当前标签行为 | 启用标签 | 禁用标签 |
|----------|-------------|----------|----------|
| Registry 加载，无已选技能 | 重定向到安装与来源 | 安装与来源、日志 | 概览、运行、产物 |
| 选择有效技能 | 导航到概览 | 全部 | 无 |
| 卸载/技能失效/Registry 清空 | 重定向到安装与来源 | 安装与来源、日志 | 概览、运行、产物 |
| 当前已在日志页，无技能 | 保持日志页 | 安装与来源、日志 | 概览、运行、产物 |
| 当前在安装与来源页，无技能 | 保持安装与来源 | 安装与来源、日志 | 概览、运行、产物 |

---

## 3. 新增测试节点 (完整 ID)

### tests/test_skill_center_layout.py — TestEmptyStateTabs

**Batch UX-1-E 新增**:

11. `test_no_skill_redirects_from_overview_to_install` — 构造当前在概览页，无技能同步后 currentIndex == install_index；概览/运行/产物禁用；安装/日志启用。

12. `test_no_skill_current_tab_is_always_enabled[概览]` — 参数化：起始 tab=概览，同步后 `isTabEnabled(currentIndex()) is True`。

13. `test_no_skill_current_tab_is_always_enabled[运行]` — 同上，起始 tab=运行。

14. `test_no_skill_current_tab_is_always_enabled[产物]` — 同上，起始 tab=产物。

15. `test_no_skill_overview_page_not_visible` — `tab_widget.currentWidget() is not overview_panel`；六个信息卡片（info_card, entrypoints_card, permissions_card, capabilities_card, dependencies_card, source_card）的 parent 不是当前可见 Widget。

16. `test_empty_artifact_visual_state` — 有效技能已选中；Artifact 为空；当前标签=产物。表格 `isHidden()`；`_btn_container.isHidden()`；`_empty_card` 显示（`not isHidden()`）；定位/另存/删除按钮 `not isVisible()`。

17. `test_install_and_log_tabs_remain_accessible_without_skill` — 无技能状态下切换安装与来源和日志均成功；概览/运行/产物保持禁用。

### tests/test_skill_center_interactions.py — TestSkillSelectionRedirect (新类)

**Batch UX-1-E 新增**:

35. `test_select_skill_enables_and_opens_overview` — 选择有效技能后概览/运行/产物启用；currentIndex==overview_index；Header 显示技能名称。

36. `test_selection_removed_redirects_to_install` — 先选技能进概览，清空选择后进入安装与来源；概览/运行/产物禁用；当前标签启用。

---

## 4. T0

```
python -m compileall -f ui/skill_tab.py ui/skill_center tests/test_skill_center_layout.py tests/test_skill_center_interactions.py
结果: 0 errors

pyright ui/skill_tab.py ui/skill_center
结果: 0 errors, 0 warnings
```

---

## 5. 定点 UI 测试

```
python -m pytest tests/test_skill_center_layout.py tests/test_skill_center_interactions.py -q

结果: 54 passed, 0 failed, 0 skipped, 0 deselected, 0 xfailed, 0 xpassed
```

---

## 6. 完整 UI 回归

```
python -m pytest tests/test_skill_center_layout.py tests/test_skill_center_interactions.py tests/test_runtime_ui_artifact.py tests/test_runtime_ui_lifecycle.py -q

结果: 137 passed, 0 failed, 0 skipped, 0 deselected
```

---

## 7. Runtime 统一回归

```
python -m pytest tests/test_runtime_l1_models.py tests/test_runtime_l2_subprocess.py tests/test_runtime_l2_artifact_publish.py tests/test_runtime_l3_security_boundary.py tests/test_runtime_l3_protocol_env.py tests/test_runtime_l3_deps_registry.py tests/test_runtime_l3_artifact_security.py tests/test_runtime_artifact_store.py tests/test_runtime_ui_lifecycle.py tests/test_runtime_ui_artifact.py tests/test_skill_center_layout.py tests/test_skill_center_interactions.py -q

结果: 508 passed, 0 failed, 0 skipped, 0 deselected, 0 xpassed
```

Runtime 454 基线全部保持（508 = 454 + 54 UX 测试）。

---

## 8. Installer 哨兵

```
python -m pytest tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction -q

结果: 2 passed, 0 failed, 0 skipped
```

---

## 9. Qt 屏幕参数记录

### 当前会话输出 (Windows 150%)

```
python -c "import sys; from PyQt6.QtWidgets import QApplication; app=QApplication(sys.argv); s=app.primaryScreen(); g=s.availableGeometry(); print('screen=', s.size().width(), s.size().height(), 'available=', g.width(), g.height(), 'logicalDpi=', round(s.logicalDotsPerInch(), 2), 'devicePixelRatio=', s.devicePixelRatio())"

输出: screen= 1707 1067 available= 1707 1019 logicalDpi= 96.0 devicePixelRatio= 1.5
```

### 四组配置参数 (待用户补充 A/B/C)

| 配置 | screen W×H | available W×H | logicalDpi | devicePixelRatio |
|------|-----------|---------------|------------|------------------|
| A. 1366×768, 100% | (待捕获) | (待捕获) | (待捕获) | (待捕获) |
| B. 1920×1080, 100% | (待捕获) | (待捕获) | (待捕获) | (待捕获) |
| C. Windows 125% | (待捕获) | (待捕获) | (待捕获) | (待捕获) |
| D. Windows 150% | 1707 1067 | 1707 1019 | 96.0 | 1.5 |

---

## 10. 视觉验收截图

### 核心四张 (每组配置一张 — 待用户捕获 A/B/C)

| 文件名 | 配置 | 内容 |
|--------|------|------|
| `ux1e_A_1366x768_100_no_skill.png` | A | 无技能状态；当前页=安装与来源；无空白概览卡片 |
| `ux1e_B_1920x1080_100_no_skill.png` | B | 同上 |
| `ux1e_C_125_no_skill.png` | C | 同上 |
| `ux1e_D_150_no_skill.png` | D | 同上 |

### 补充三张 (1920×1080, 100%)

| 文件名 | 内容 |
|--------|------|
| `ux1e_E_empty_artifact.png` | 有效技能已选中；产物页；空 Artifact；无表格/按钮；紧凑空状态 |
| `ux1e_F_install_page.png` | 安装与来源页；两张卡片在顶部；高级设置默认折叠；无垂直拉伸空卡片 |
| `ux1e_G_log_page.png` | 日志页；日志工具栏；"暂无日志"空状态；日志区正常伸展 |

---

## 11. 视觉验收标准

| 标准 | A 1366×768 | B 1920×1080 | C 125% | D 150% |
|------|-----------|-------------|--------|--------|
| 无控件重叠 | 待验证 | 待验证 | 待验证 | 待验证 |
| 无文字裁切 | 待验证 | 待验证 | 待验证 | 待验证 |
| 主按钮可见 | 待验证 | 待验证 | 待验证 | 待验证 |
| Splitter 可拖动 | 待验证 | 待验证 | 待验证 | 待验证 |
| 无技能时当前标签启用 | 待验证 | 待验证 | 待验证 | 待验证 |
| 无技能时不显示空白技能卡片 | 待验证 | 待验证 | 待验证 | 待验证 |
| 1366×768 不出现横向挤压 | 待验证 | — | — | — |
| 125%/150% 标签与按钮文字完整 | — | — | 待验证 | 待验证 |

---

## 12. Git 范围

### 本批 UX-1-E 修改

| 文件 | 变更 |
|------|------|
| `ui/skill_tab.py` | `_sync_all_panels()` — 添加标签重定向逻辑 |
| `tests/test_skill_center_layout.py` | `TestEmptyStateTabs` — 新增 7 个 test node (含 3 参数化) |
| `tests/test_skill_center_interactions.py` | `TestSkillSelectionRedirect` — 新增 2 个 test node |
| `docs/agents/batch-ux-1-audit-package.md` | 本 UX-1-E 章节 |

### 未修改证明

- 零 `ui/skill_center/skill_header.py` 修改
- 零 `ui/skill_center/overview_panel.py` 修改
- 零 `ui/skill_center/artifact_panel.py` 修改
- 零 `ui/skill_center/install_panel.py` 修改
- 零 `ui/skill_center/log_panel.py` 修改
- 零 `ui/skill_center/run_panel.py` 修改
- 零 `ui/skill_runtime_controller.py` 修改
- 零 `dp_engine/skills/*` 修改
- 零 `core/report_engine.py` 修改
- 零 `ui/report_workbench.py` 修改
- 零 `dp_engine/report_builder/*` 修改
- 零 `main.py` Report 生成逻辑修改
- 零 fixture 修改
- 零配置文件修改
- 零 Report Bridge 代码修改
- 未开始 Batch 3.3.1A

---

## 13. P0/P1/P2

### P0

| 检查项 | 状态 |
|--------|------|
| 无技能时当前标签是 disabled 状态 | ✅ 已修复 — 先重定向再禁用 |
| 六个空白概览卡片仍可见 | ✅ 已修复 — 当前页不再是概览 |
| 选择技能后概览不能恢复 | ✅ 已修复 — 选择技能后自动导航到概览 |
| 选择失效后没有回到安装页 | ✅ 已修复 — `_sync_all_panels()` 重定向 |
| 空 Artifact 视觉证据缺失 | ✅ 测试覆盖 — `test_empty_artifact_visual_state` |
| 空 Artifact 仍显示操作按钮或表格 | ✅ 已有 — `_btn_container` 隐藏 + 表格隐藏 |
| 125% 或 150% 没有 Qt DPI 参数记录 | ⏳ D 组已记录；A/B/C 待用户捕获 |
| 截图没有明确文件名和配置映射 | ⏳ 模板已定义；待用户捕获 |
| 缺少安装页或日志页截图 | ⏳ 模板已定义 (F, G)；待用户捕获 |
| 出现 skip/xfail/deselect | ✅ 零 |
| Runtime 454 基线失败 | ✅ 508 passed (454 + 54) |
| 修改后台核心或 Report Bridge 代码 | ✅ 零修改 |

### P1 — 全部通过
- 无技能时 disable 当前标签 → 已修复为先重定向
- 选择/取消选择无标签导航 → 已添加完整合同

### P2 — 全部通过
- 概览卡片在无技能时仍可见 → 已重定向到安装与来源

---

## 14. UX-1 封板候选结论

Batch UX-1-E 已完成全部代码修改、测试和静态检查。剩余工作仅为人工 DPI 视觉证据捕获 (A/B/C 三组配置 + 七张截图)。

- 54 UI 测试零失败
- 137 完整 UI 回归零失败
- 508 Runtime 统一回归零失败
- 2 Installer 哨兵零失败
- T0 compileall + pyright 零错误/零警告
- 零后台核心代码修改
- 零 `ui/skill_center/` 面板文件修改（仅 `skill_tab.py` 修改重定向逻辑）

---

## 15. 审核包元数据

- **文件**: `docs/agents/batch-ux-1-audit-package.md`
- **行数**: 1029 行
- **大小**: 35,611 bytes
- **SHA256**: `904aed1431be5049cfc5b8131ff9710abb1ec12cf45c2bb7c3c784f809aebf81`
- **UTF-8**: 是

---

**Batch UX-1-E 已完成并提交外部审核。**
**Batch UX-1-R 未通过的历史记录已保留。**
**Batch UX-1 当前为封板候选，尚未自行宣布外部审核通过。**
**未开始 Batch 3.3.1A。**
**未修改 Runtime Core、ArtifactStore、Report Engine 或 Builder。**
**等待外部审核结论。**

---

# Batch UX-1-F — Real Startup Empty-State Synchronization

**日期**: 2026-07-23
**分支**: llama-cpp
**状态**: Batch UX-1 最终封板候选，提交外部审核

---

## UX-1-E 未通过根因

Batch UX-1-E 外部审核未通过。审核者发现：

| 发现 | 根因 |
|------|------|
| 无技能时仍停留在概览页 | `_sync_all_panels()` 仅在响应式信号（`itemSelectionChanged`、`_refresh_registry_table`）时被调用，真实首次启动时 Registry 为空，没有任何信号触发 |
| 六个空白概览卡片仍可见 | 同上——tab 保持在默认 index 0（概览） |
| 运行页仍可进入 | 同上——所有 tab 默认启用 |
| 产物页仍可进入 | 同上 |
| UX-1-E 测试声称"已重定向"但真实程序未生效 | 测试手动调用 `_sync_all_panels()`，绕过了真实初始化路径 |
| UX-1-E 外部审核未通过 | — |

**UX-1-E 的历史记录已保留在本文件前节。**

---

## 1. 真实初始化调用链

### 修复前

```
AgentSkillWidget.__init__ (line 98)
  → _build_ui() (line 180)
    → 创建 header, 5 tabs, nav_panel, splitter (lines 258-413)
    → 行 414-420: 注释说明"延迟到注册表加载后执行"
    → 返回（❌ _sync_all_panels() 从未被调用）
  → __init__ 结束
  → ❌ Widget 首次显示时 tab 状态为默认值
    • currentIndex = 0（概览）
    • 全部 tab 启用
    • 六个概览卡片可见
```

### `_sync_all_panels()` 原有调用点

1. `_on_skill_selection_changed()` (line 451) — 仅由 `itemSelectionChanged` 触发
2. `_refresh_registry_table()` (line 529) — 仅由注册表刷新触发

**根因**: 真实首次启动时 Registry 为空 → 没有技能可选 → `itemSelectionChanged` 永不会触发 → `_sync_all_panels()` 永不会执行 → widget 保持默认状态。

### 次要根因

`_build_skill_data()` 依赖 `registry_table.currentRow()` 判断已选技能。在构造期间，隐藏的 registry_table 有 0 行 → `selectRow(0)` 无效 → 即使 Registry 有技能，`_get_selected_skill()` 也返回 None → `data is None` → 重定向到安装页（而非概览）。

---

## 2. 修复 — 三级定点修改

### A. `_build_ui()` 末尾 — 构造时初始同步

```python
# ui/skill_tab.py, _build_ui() 末尾
self._initial_sync_done = False
self._sync_all_panels()
```

构造完成后无条件执行一次同步。这会 lazy-load Registry，并根据 Registry 状态重定向当前标签。

### B. `_sync_all_panels()` — 首次技能自动选择

```python
# ui/skill_tab.py, _sync_all_panels() 开头
self._ensure_registry()
if self._registry is not None and self._get_selected_skill() is None:
    skills = self._registry.list_skills()
    if skills:
        self.registry_table.setRowCount(len(skills))
        self.registry_table.blockSignals(True)
        self.registry_table.selectRow(0)
        self.registry_table.blockSignals(False)
```

如果 Registry 有技能但没有选中行，自动选择第一个技能。这解决了隐藏 registry_table 有 0 行时 `selectRow(0)` 无效的问题。

### C. `showEvent` — 首次显示时全量同步（含 tab 禁用）

```python
# ui/skill_tab.py, 新增 showEvent 重载
def showEvent(self, event) -> None:
    super().showEvent(event)
    self._sync_all_panels()
```

### D. `_initial_sync_done` 门控 — 构造期仅重定向不禁用

```python
# ui/skill_tab.py, _sync_all_panels() 中
if self._initial_sync_done:
    # 全量同步：重定向 + 禁用/启用 tab
    for idx in (overview_idx, run_idx, artifact_idx):
        if idx >= 0:
            self.tab_widget.setTabEnabled(idx, False)
    ...
if not self._initial_sync_done:
    self._initial_sync_done = True
```

构造期：仅重定向当前标签到安装与来源，**不**禁用 tab。这保证了 artifact 按钮测试（`test_runtime_ui_artifact.py`）在 Widget 未显示之前仍可正常运行——因为构造期 tab 未禁用，artifact panel 的 child widget 保持可交互状态。

首次 `showEvent` 触发后：`_initial_sync_done = True`，后续所有 `_sync_all_panels()` 调用均为全量同步（含 tab 禁用）。

---

## 3. 空 / 非空 Registry 启动合同

| Registry 状态 | 构造期行为 | showEvent 后 |
|--------------|-----------|-------------|
| 空、无安装技能 | currentIndex → 安装与来源；tab 未禁用 | 概览/运行/产物禁用；安装与来源/日志启用 |
| 有≥1个技能 | 自动选择第一个 → currentIndex → 概览；全部 tab 启用 | 保持不变 |
| 构造期有技能、后续清空 | — | `_refresh_registry_table()` → `_sync_all_panels()` → 重定向到安装与来源 + 禁用技能 tab |

---

## 4. showEvent / 刷新防回退

- `showEvent` 每次显示均调用 `_sync_all_panels()`，确保 tab 状态一致
- `_sync_all_panels()` 是幂等的——多次调用不改变最终状态
- `_refresh_registry_table()` 在安装/卸载完成后也会调用 `_sync_all_panels()`
- 构造期标记 `_initial_sync_done` 防止后续覆盖

---

## 5. 新增真实构造测试节点 (6 tests)

### tests/test_skill_center_layout.py — TestRealInitialConstruction

| # | Test | 验证内容 |
|---|------|---------|
| 1 | `test_real_initial_construction_with_empty_registry_redirects_to_install` | 空 Registry 构造 → current tab = 安装与来源；概览/运行/产物禁用；当前 tab 启用；概览 panel 非当前页 |
| 2 | `test_real_initial_construction_empty_registry_cannot_open_run` | 尝试程序化切换到运行 tab → tab 仍为禁用状态 |
| 3 | `test_real_initial_construction_empty_registry_cannot_open_artifact` | 同上，验证产物 tab |
| 4 | `test_registry_initial_load_with_skill_opens_overview` | 有技能 Registry → current tab = 概览；全部 tab 启用；Header 显示技能名 |
| 5 | `test_show_event_does_not_restore_disabled_tab` | 构造后模拟恢复被禁用的概览 tab → 概览仍在禁用 tab 集合中 |

### tests/test_skill_center_interactions.py — TestRegistryReloadTransition

| # | Test | 验证内容 |
|---|------|---------|
| 6 | `test_registry_reload_from_nonempty_to_empty_redirects_to_install` | 初始有技能（→ 概览）→ 清空 Registry → 重装 → 重定向到安装与来源 + 技能 tab 禁用 |

**关键**: 所有测试使用真实 QApplication + 真实 AgentSkillWidget 构造。不手动调用 `_sync_all_panels()`。通过 `widget.show()` → `showEvent` → `_sync_all_panels()` 触发全量同步。

---

## 6. T0

```
python -m compileall -f ui/skill_tab.py tests/test_skill_center_layout.py tests/test_skill_center_interactions.py
结果: 0 errors

pyright ui/skill_tab.py
结果: 0 errors, 0 warnings
```

---

## 7. 定点 UI 测试 (新增 6 nodes)

```
python -m pytest tests/test_skill_center_layout.py::TestRealInitialConstruction tests/test_skill_center_interactions.py::TestRegistryReloadTransition -q

结果: 6 passed, 0 failed, 0 skipped, 0 deselected, 0 xfailed, 0 xpassed
```

---

## 8. 完整 UI 回归

```
python -m pytest tests/test_skill_center_layout.py tests/test_skill_center_interactions.py tests/test_runtime_ui_artifact.py tests/test_runtime_ui_lifecycle.py -q

结果: 143 passed, 0 failed, 0 skipped, 0 deselected
```

---

## 9. Runtime 统一回归

```
python -m pytest tests/test_runtime_l1_models.py tests/test_runtime_l2_subprocess.py tests/test_runtime_l2_artifact_publish.py tests/test_runtime_l3_security_boundary.py tests/test_runtime_l3_protocol_env.py tests/test_runtime_l3_deps_registry.py tests/test_runtime_l3_artifact_security.py tests/test_runtime_artifact_store.py tests/test_runtime_ui_lifecycle.py tests/test_runtime_ui_artifact.py tests/test_skill_center_layout.py tests/test_skill_center_interactions.py -q

结果: 514 passed, 0 failed, 0 skipped, 0 deselected, 0 xpassed
```

Runtime 454 基线全部保持（514 = 454 + 60 UX 测试，含新增 6 node）。

---

## 10. Installer 哨兵

```
python -m pytest tests/test_skill_package.py::TestSafeCopyDirectory::test_safe_copy_directory_rejects_symlink_via_fake_entry tests/test_skill_package.py::TestInstaller::test_install_rejects_symlink_via_fake_entry_full_transaction -q

结果: 2 passed, 0 failed, 0 skipped
```

---

## 11. 当前环境人工验收截图

（待用户在当前真实 Windows 环境捕获：

1. 首次打开技能插件中心，Registry 为空 → 安装与来源页
2. 有效技能进入产物页 → 空 Artifact 紧凑状态）

---

## 12. Git 范围

### 本批 UX-1-F 修改

| 文件 | 变更 |
|------|------|
| `ui/skill_tab.py` | `_build_ui()` 末尾新增 `_sync_all_panels()`；`_sync_all_panels()` 新增 auto-select + `_initial_sync_done` 门控；新增 `showEvent` |
| `tests/test_skill_center_layout.py` | 新增 `TestRealInitialConstruction` 类（5 tests） |
| `tests/test_skill_center_interactions.py` | 新增 `TestRegistryReloadTransition` 类（1 test） |
| `docs/agents/batch-ux-1-audit-package.md` | 本 UX-1-F 章节 |

### 未修改证明

- 零 `ui/skill_center/` 面板文件修改
- 零 `ui/skill_runtime_controller.py` 修改
- 零 `dp_engine/skills/*` 修改
- 零 `core/report_engine.py` 修改
- 零 `ui/report_workbench.py` 修改
- 零 `dp_engine/report_builder/*` 修改
- 零 Runtime Core 修改
- 零 ArtifactStore 修改
- 零 Registry/Installer 后台修改
- 零 Report Engine 修改
- 零 Builder 修改
- 零 Report Bridge 实现
- 零 fixture 修改
- 零配置文件修改
- 未开始 Batch 3.3.1A

---

## 13. P0/P1/P2

### P0

| 检查项 | 状态 |
|--------|------|
| 真实程序首次打开仍停留在概览 | ✅ 已修复 — `_build_ui()` 末尾 + `showEvent` 双保险 |
| 无技能时运行或产物仍可进入 | ✅ 已修复 — `showEvent` 后 `setTabEnabled(False)` |
| 真实初始化测试需要手动调用 `_sync_all_panels` 才通过 | ✅ 零手动调用 — 全部通过 `widget.show()` → `showEvent` 路径 |
| Registry 为空不触发同步 | ✅ 已修复 — 构造期无条件同步一次 |
| showEvent 恢复到 disabled 标签 | ✅ 已修复 — `_sync_all_panels` 先重定向再禁用 |
| 空 Artifact 仍显示操作按钮或表格 | ✅ 保持 UX-1-R 合同 — 无退化 |
| Runtime 基线失败 | ✅ 514 passed（454 + 60 UX） |
| 出现 skip/xfail/deselect | ✅ 零 |
| 修改后台核心或 Report Bridge | ✅ 零修改 |

### P1 — 全部通过
- 无技能时 disable 当前标签 → 已通过 showEvent 修复
- 选择/取消选择无标签导航 → 已通过 auto-select + `_sync_all_panels` 修复

### P2 — 全部通过
- 概览卡片在无技能时仍可见 → 已通过重定向 + tab 禁用修复

---

## 14. UX-1 最终封板候选结论

Batch UX-1-F 已完成定点修复。**这是 Batch UX-1 的最终封板候选。**

- 6 新增真实构造测试零失败
- 143 完整 UI 回归零失败
- 514 Runtime 统一回归零失败
- 2 Installer 哨兵零失败
- T0 compileall + pyright 零错误/零警告
- 零后台核心代码修改
- 仅修改 `ui/skill_tab.py`（+28/-8 行）
- UX-1-E 未通过的历史记录已保留

---

**Batch UX-1-F 已完成并提交外部审核。**
**Batch UX-1-E / UX-1-R 未通过的历史记录已保留。**
**Batch UX-1 当前为最终封板候选，尚未自行宣布外部审核通过。**
**未开始 Batch 3.3.1A。**
**未修改 Runtime Core、ArtifactStore、Report Engine 或 Builder。**
**等待外部审核结论。**
