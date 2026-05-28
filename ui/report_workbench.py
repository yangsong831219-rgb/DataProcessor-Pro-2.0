"""统一报告生成工作台 — Word/PPT 合并界面.

布局: 左侧配置面板 + 右侧 QSplitter (大纲编辑器 / 执行日志)
流程: 生成大纲 → 用户确认修改 → 分步扩写 → 渲染输出
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTextEdit, QGroupBox, QRadioButton, QButtonGroup, QFileDialog,
    QSplitter, QDialog, QTextBrowser, QMessageBox, QComboBox,
    QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from py.report_builder.flow_controller import ReportFlowController
from py.report_builder.models import ReportOutline, GenerationEvent


STYLE_CARD = """
    QGroupBox {
        border: 1px solid #e0e0e0; border-radius: 8px;
        font-weight: bold; padding: 14px; padding-top: 24px;
        background: white; margin-top: 8px;
    }
    QGroupBox::title {
        subcontrol-origin: margin; left: 12px; padding: 0 6px;
        color: #333;
    }
"""

STYLE_BTN_PRIMARY = """
    QPushButton {
        background: #1890ff; color: white; border: none; border-radius: 4px;
        padding: 9px 20px; font-size: 13px; font-weight: bold;
    }
    QPushButton:hover { background: #40a9ff; }
    QPushButton:disabled { background: #d9d9d9; color: #999; }
"""

STYLE_BTN_ACCENT = """
    QPushButton {
        background: #52c41a; color: white; border: none; border-radius: 4px;
        padding: 9px 20px; font-size: 13px; font-weight: bold;
    }
    QPushButton:hover { background: #73d13d; }
    QPushButton:disabled { background: #d9d9d9; color: #999; }
"""

STYLE_BTN_SECONDARY = """
    QPushButton {
        background: white; color: #555; border: 1px solid #d9d9d9; border-radius: 4px;
        padding: 8px 16px; font-size: 13px;
    }
    QPushButton:hover { color: #1890ff; border-color: #1890ff; }
"""

STYLE_SEGMENT_SELECTED = """
    QRadioButton {
        background: #1890ff; color: white; border: none; border-radius: 4px;
        padding: 8px 18px; font-size: 13px; font-weight: bold;
    }
"""

STYLE_SEGMENT_NORMAL = """
    QRadioButton {
        background: #f0f0f0; color: #555; border: 1px solid #d9d9d9; border-radius: 4px;
        padding: 8px 18px; font-size: 13px;
    }
    QRadioButton:hover { border-color: #1890ff; color: #1890ff; }
"""

HELP_TEXT = """# 报告生成工作台 — 使用说明

## 功能概述

报告生成工作台将 Word 深度报告和 PPT 汇报演示整合到统一界面，
基于 **"大纲确认 → 分步扩写"** 的人机协作模式，确保生成质量可控。

## 操作流程

### 第一步：配置
1. 选择**项目资料文件** (可选) — 提供数据上下文
2. 选择**需求文件** (可选) — 描述报告要覆盖的内容
3. 选择**模板文件** (可选) — .docx 或 .pptx 模板
4. 切换报告类型 — [Word 深度报告] 或 [PPT 汇报演示]

### 第二步：生成大纲
点击 **[生成并预览大纲]** → AI 用约 10 秒生成章节大纲 → 显示在右侧编辑器

### 第三步：确认与修改
- 在右侧编辑器中**直接修改**大纲文本
- 增删章节、调整标题、修改论点
- 确认无误后点击 **[基于大纲生成完整报告]**

### 第四步：生成
AI 逐章扩写，日志终端实时显示进度。完成后自动保存文件。

## Word vs PPT 的区别

| | Word | PPT |
|---|---|---|
| 内容风格 | 长文本深度论述 | 极简 bullet 要点 |
| 每节要点数 | 不限 | ≤4 条 |
| 数据细节 | 正文表格 | Speaker Notes |
| 图表标签 | [INSERT_IMAGE: xxx.png] | 同 (每页最多1张) |
| 适用场景 | 技术报告、分析文档 | 领导汇报、项目答辩 |

## 图表标签语法

在需求文件或项目资料中使用：
`[INSERT_IMAGE: 文件名.png]`
系统自动从项目资料目录搜索并插入图片。
"""


class ReportWorkbenchWidget(QWidget):
    """统一报告生成工作台."""

    # 信号: 通知主窗口保存文件
    report_generated = pyqtSignal(bytes, str)  # (file_bytes, suggested_filename)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.controller = ReportFlowController()
        self._outline: ReportOutline | None = None
        self._generate_fn = None

        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        # ═══════════════════════════════════════════════
        # 左侧: 配置面板
        # ═══════════════════════════════════════════════
        left_panel = QWidget()
        left_panel.setFixedWidth(300)
        left_panel.setStyleSheet("background: #fafbfc; border-right: 1px solid #e8e8e8;")
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)

        # ── 标题 ──
        header = QLabel("报告生成工作台")
        header.setStyleSheet("font-size: 17px; font-weight: bold; color: #333; padding: 4px 0;")
        left_layout.addWidget(header)

        # ── 报告类型 ──
        type_group = QGroupBox("报告类型")
        type_group.setStyleSheet(STYLE_CARD)
        type_layout = QVBoxLayout()

        self.type_group = QButtonGroup(self)
        self.word_radio = QRadioButton("Word 深度报告")
        self.ppt_radio = QRadioButton("PPT 汇报演示")
        self.type_group.addButton(self.word_radio, 0)
        self.type_group.addButton(self.ppt_radio, 1)
        self.word_radio.setChecked(True)

        type_layout.addWidget(self.word_radio)
        type_layout.addWidget(self.ppt_radio)
        type_group.setLayout(type_layout)
        left_layout.addWidget(type_group)

        # ── 项目资料 ──
        file_group = QGroupBox("输入文件")
        file_group.setStyleSheet(STYLE_CARD)
        file_layout = QVBoxLayout()
        file_layout.setSpacing(6)

        self.project_file_label = QLabel("项目资料: 未选择")
        self.project_file_label.setStyleSheet("color: #888; font-size: 12px;")
        self.project_file_label.setWordWrap(True)
        file_layout.addWidget(self.project_file_label)

        proj_btn = QPushButton("选择项目资料...")
        proj_btn.setStyleSheet(STYLE_BTN_SECONDARY)
        proj_btn.clicked.connect(self._select_project_file)
        file_layout.addWidget(proj_btn)

        self.req_file_label = QLabel("需求文件: 未选择")
        self.req_file_label.setStyleSheet("color: #888; font-size: 12px;")
        self.req_file_label.setWordWrap(True)
        file_layout.addWidget(self.req_file_label)

        req_btn = QPushButton("选择需求文件...")
        req_btn.setStyleSheet(STYLE_BTN_SECONDARY)
        req_btn.clicked.connect(self._select_req_file)
        file_layout.addWidget(req_btn)

        self.template_file_label = QLabel("模板文件: 未选择")
        self.template_file_label.setStyleSheet("color: #888; font-size: 12px;")
        self.template_file_label.setWordWrap(True)
        file_layout.addWidget(self.template_file_label)

        tmpl_btn = QPushButton("选择模板文件...")
        tmpl_btn.setStyleSheet(STYLE_BTN_SECONDARY)
        tmpl_btn.clicked.connect(self._select_template_file)
        file_layout.addWidget(tmpl_btn)

        file_group.setLayout(file_layout)
        left_layout.addWidget(file_group)

        # ── 操作按钮 ──
        btn_group = QGroupBox("操作")
        btn_group.setStyleSheet(STYLE_CARD)
        btn_layout = QVBoxLayout()
        btn_layout.setSpacing(8)

        self.outline_btn = QPushButton("生成并预览大纲")
        self.outline_btn.setStyleSheet(STYLE_BTN_PRIMARY)
        self.outline_btn.clicked.connect(self._on_generate_outline)
        btn_layout.addWidget(self.outline_btn)

        self.full_report_btn = QPushButton("基于大纲生成完整报告")
        self.full_report_btn.setStyleSheet(STYLE_BTN_ACCENT)
        self.full_report_btn.setEnabled(False)
        self.full_report_btn.clicked.connect(self._on_generate_full_report)
        btn_layout.addWidget(self.full_report_btn)

        self.help_btn = QPushButton("使用说明")
        self.help_btn.setStyleSheet(STYLE_BTN_SECONDARY)
        self.help_btn.clicked.connect(self._show_help)
        btn_layout.addWidget(self.help_btn)

        btn_group.setLayout(btn_layout)
        left_layout.addWidget(btn_group)

        left_layout.addStretch()
        left_panel.setLayout(left_layout)

        # ═══════════════════════════════════════════════
        # 右侧: 工作区 (Splitter)
        # ═══════════════════════════════════════════════
        right_splitter = QSplitter(Qt.Orientation.Vertical)

        # 上半: 大纲编辑器
        editor_group = QGroupBox("报告大纲 (可编辑)")
        editor_group.setStyleSheet(STYLE_CARD)
        editor_layout = QVBoxLayout()

        self.outline_editor = QTextEdit()
        self.outline_editor.setPlaceholderText(
            "点击「生成并预览大纲」后，AI 生成的大纲将显示在此处。\n"
            "你可以直接编辑、增删章节、修改标题和论点。"
        )
        self.outline_editor.setFont(QFont("Microsoft YaHei", 11))
        editor_layout.addWidget(self.outline_editor)

        editor_group.setLayout(editor_layout)
        right_splitter.addWidget(editor_group)

        # 下半: 执行日志
        log_group = QGroupBox("AI 执行日志")
        log_group.setStyleSheet(STYLE_CARD)
        log_layout = QVBoxLayout()

        self.log_terminal = QTextEdit()
        self.log_terminal.setReadOnly(True)
        self.log_terminal.setFont(QFont("Consolas", 10))
        self.log_terminal.setMaximumHeight(160)
        self.log_terminal.setStyleSheet(
            "background: #1e1e1e; color: #d4d4d4; border: 1px solid #333; border-radius: 4px;"
        )
        log_layout.addWidget(self.log_terminal)

        log_group.setLayout(log_layout)
        right_splitter.addWidget(log_group)

        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 1)

        layout.addWidget(left_panel)
        layout.addWidget(right_splitter, 1)

        self.setLayout(layout)

    # ═══════════════════════════════════════════════
    # LLM 回调获取
    # ═══════════════════════════════════════════════

    def _get_generate_fn(self):
        """从主窗口获取 LLM 生成回调."""
        if self._generate_fn:
            return self._generate_fn
        if self.parent_window and hasattr(self.parent_window, 'ollama_client'):
            client = self.parent_window.ollama_client
            if client and client.is_available():
                return client.generate
        return None

    # ═══════════════════════════════════════════════
    # 文件选择
    # ═══════════════════════════════════════════════

    def _select_project_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择项目资料", "",
                                               "All Files (*.*);;CSV (*.csv);;Excel (*.xlsx);;Text (*.txt *.md)")
        if path:
            self.project_file_label.setText(f"项目资料: {path}")
            self.project_file_label.setStyleSheet("color: #333; font-size: 12px;")

    def _select_req_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择需求文件", "",
                                               "Text Files (*.txt *.md);;All Files (*.*)")
        if path:
            self.req_file_label.setText(f"需求文件: {path}")
            self.req_file_label.setStyleSheet("color: #333; font-size: 12px;")

    def _select_template_file(self):
        if self.ppt_radio.isChecked():
            filt = "PowerPoint (*.pptx);;All Files (*.*)"
        else:
            filt = "Word (*.docx *.doc);;All Files (*.*)"
        path, _ = QFileDialog.getOpenFileName(self, "选择模板文件", "", filt)
        if path:
            self.template_file_label.setText(f"模板: {path}")
            self.template_file_label.setStyleSheet("color: #333; font-size: 12px;")

    # ═══════════════════════════════════════════════
    # 生成大纲
    # ═══════════════════════════════════════════════

    def _on_generate_outline(self):
        generate_fn = self._get_generate_fn()
        if not generate_fn:
            QMessageBox.warning(self, "AI 不可用",
                                "无法连接到 AI 模型。请检查 Ollama 服务是否运行，或在设置中配置模型。")
            return

        report_type = "ppt" if self.ppt_radio.isChecked() else "word"
        requirements = self._gather_requirements()

        self._log("── 正在生成大纲... ──")
        self.outline_btn.setEnabled(False)
        self.outline_btn.setText("正在生成大纲...")

        try:
            self._outline, raw = self.controller.generate_outline(
                generate_fn, requirements, report_type,
            )
            self.outline_editor.setPlainText(self._outline.to_markdown())
            self.full_report_btn.setEnabled(True)
            self._log(f"大纲生成完成: {len(self._outline.sections)} 个章节")
        except Exception as e:
            self._log(f"错误: {e}")
            QMessageBox.critical(self, "生成失败", str(e))
        finally:
            self.outline_btn.setEnabled(True)
            self.outline_btn.setText("生成并预览大纲")

    # ═══════════════════════════════════════════════
    # 生成完整报告
    # ═══════════════════════════════════════════════

    def _on_generate_full_report(self):
        generate_fn = self._get_generate_fn()
        if not generate_fn:
            QMessageBox.warning(self, "AI 不可用", "无法连接到 AI 模型。")
            return

        if not self._outline:
            QMessageBox.warning(self, "无大纲", "请先生成大纲。")
            return

        # 解析用户编辑后的大纲
        edited_text = self.outline_editor.toPlainText().strip()
        if edited_text:
            self._outline = self._parse_edited_outline(edited_text, self._outline.report_type)

        report_type = self._outline.report_type
        requirements = self._gather_requirements()

        self._log("── 开始分步扩写报告... ──")
        self.full_report_btn.setEnabled(False)
        self.outline_btn.setEnabled(False)

        def progress_callback(event: GenerationEvent):
            self._log(f"[{event.stage}] {event.message}")

        try:
            doc_bytes = self.controller.run_full_flow(
                generate_fn=generate_fn,
                requirements=requirements,
                report_type=report_type,
                author="DataProcessor Pro",
                project_context=self._gather_project_context(),
                progress_callback=progress_callback,
            )

            ext = ".pptx" if report_type == "ppt" else ".docx"
            default_name = f"report_{self._outline.title[:30] or 'output'}{ext}"
            default_name = default_name.replace("/", "_").replace("\\", "_")

            path, _ = QFileDialog.getSaveFileName(
                self, "保存报告", default_name,
                "PowerPoint (*.pptx)" if report_type == "ppt" else "Word (*.docx)",
            )
            if path:
                with open(path, "wb") as f:
                    f.write(doc_bytes)
                self._log(f"报告已保存: {path}")
                self.report_generated.emit(doc_bytes, default_name)
            else:
                self._log("保存已取消")

        except Exception as e:
            self._log(f"错误: {e}")
            import traceback
            self._log(traceback.format_exc())
        finally:
            self.full_report_btn.setEnabled(True)
            self.outline_btn.setEnabled(True)

    # ═══════════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════════

    def _gather_requirements(self) -> str:
        parts = []
        req_text = self.req_file_label.text()
        if "需求文件:" in req_text and "未选择" not in req_text:
            path = req_text.replace("需求文件: ", "").strip()
            try:
                with open(path, "r", encoding="utf-8") as f:
                    parts.append(f.read())
            except Exception:
                pass
        if not parts:
            parts.append("请根据项目资料生成一份全面的技术报告。")
        return "\n\n".join(parts)

    def _gather_project_context(self) -> str:
        parts = []
        proj_text = self.project_file_label.text()
        if "项目资料:" in proj_text and "未选择" not in proj_text:
            path = proj_text.replace("项目资料: ", "").strip()
            try:
                with open(path, "r", encoding="utf-8") as f:
                    parts.append(f.read()[:8000])
            except Exception:
                pass
        return "\n\n".join(parts)

    def _parse_edited_outline(self, text: str, report_type: str) -> ReportOutline:
        """解析用户在编辑器中修改后的大纲文本."""
        sections = []
        title = ""
        current_section = None

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("# "):
                title = line[2:].strip()
            elif line.startswith("## "):
                if current_section:
                    sections.append(current_section)
                current_section = OutlineSection(heading=line[3:].strip(), key_points=[])
            elif line.startswith("- ") and current_section:
                current_section.key_points.append(line[2:].strip())
            elif current_section:
                current_section.key_points.append(line)

        if current_section:
            sections.append(current_section)

        return ReportOutline(
            title=title or "Report",
            report_type=report_type,
            sections=sections,
        )

    def _log(self, msg: str):
        self.log_terminal.append(msg)

    def _show_help(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("报告生成工作台 — 使用说明")
        dlg.setMinimumSize(650, 550)
        layout = QVBoxLayout()

        browser = QTextBrowser()
        browser.setMarkdown(HELP_TEXT)
        browser.setStyleSheet("font-size: 13px; padding: 10px;")
        layout.addWidget(browser)

        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(STYLE_BTN_SECONDARY)
        close_btn.clicked.connect(dlg.close)
        layout.addWidget(close_btn)

        dlg.setLayout(layout)
        dlg.exec()
