"""知识库管理页面 — Wiki 页面浏览、编辑、搜索、沉浸阅读."""

from __future__ import annotations

import os
import threading

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QListWidget, QListWidgetItem, QStackedWidget,
    QSplitter, QTextEdit, QTextBrowser, QLineEdit, QDialog,
    QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QTextDocument, QTextCursor, QTextBlockFormat, QImage
from PyQt6.QtCore import QByteArray, QUrl

from dp_engine.wiki_system import WikiFileSystem


class WikiTabWidget(QWidget):
    """知识库管理页面 — 页面管理、编辑、搜索、全屏沉浸阅读."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.wiki_fs = WikiFileSystem()
        self.refresh_wiki_pages()
        self._init_wiki_embeddings()

    def _build_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # ── 根层切换栈：0=正常布局 / 1=全屏沉浸阅读 ──
        self.wiki_root_stack = QStackedWidget()

        # ═══════════════════════════════════════════════════════════
        # Page 0: 正常编辑布局
        # ═══════════════════════════════════════════════════════════
        normal_widget = QWidget()
        normal_layout = QVBoxLayout(normal_widget)
        normal_layout.setContentsMargins(16, 12, 16, 12)
        normal_layout.setSpacing(8)

        # ── 1. 顶部工具栏 ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(12)

        info_label = QLabel(
            '知识库管理 — 本地化 Markdown 文档系统：'
            '支持页面创建/编辑/搜索/删除，可上传外部文件，与 AI Agent 技能深度联动'
        )
        info_label.setStyleSheet('color: #555; font-size: 13px; padding: 6px 0;')
        toolbar.addWidget(info_label)
        toolbar.addStretch()

        btn_style_primary = """
            QPushButton {
                background: #1890ff; color: white; border: none; border-radius: 4px;
                padding: 7px 16px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background: #40a9ff; }
        """
        btn_style_secondary = """
            QPushButton {
                background: white; color: #555; border: 1px solid #d9d9d9; border-radius: 4px;
                padding: 7px 14px; font-size: 13px;
            }
            QPushButton:hover { color: #1890ff; border-color: #1890ff; }
        """
        btn_style_danger = """
            QPushButton {
                background: white; color: #ff4d4f; border: 1px solid #ffccc7; border-radius: 4px;
                padding: 7px 14px; font-size: 13px;
            }
            QPushButton:hover { color: white; background: #ff4d4f; border-color: #ff4d4f; }
        """

        new_page_btn = QPushButton("+ 新建")
        new_page_btn.setStyleSheet(btn_style_primary)
        new_page_btn.clicked.connect(self.on_wiki_new_page)
        toolbar.addWidget(new_page_btn)

        del_page_btn = QPushButton("删除")
        del_page_btn.setStyleSheet(btn_style_danger)
        del_page_btn.clicked.connect(self.on_wiki_delete_page)
        toolbar.addWidget(del_page_btn)

        upload_btn = QPushButton("上传文件")
        upload_btn.setStyleSheet(btn_style_secondary)
        upload_btn.clicked.connect(self.on_wiki_upload_file)
        toolbar.addWidget(upload_btn)

        help_btn = QPushButton("说明")
        help_btn.setStyleSheet(btn_style_secondary)
        help_btn.clicked.connect(self.show_wiki_help)
        toolbar.addWidget(help_btn)

        save_wiki_btn = QPushButton("保存页面")
        save_wiki_btn.setStyleSheet(btn_style_primary)
        save_wiki_btn.clicked.connect(self.on_wiki_save)
        toolbar.addWidget(save_wiki_btn)

        normal_layout.addLayout(toolbar)

        # ── 2. 中部导航条 ──
        navbar = QHBoxLayout()
        navbar.setSpacing(10)

        self.wiki_search_input = QLineEdit()
        self.wiki_search_input.setPlaceholderText("搜索关键词...")
        self.wiki_search_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #d9d9d9; border-radius: 4px;
                padding: 8px 12px; font-size: 13px; background: white;
            }
            QLineEdit:focus { border-color: #1890ff; }
        """)
        navbar.addWidget(self.wiki_search_input, stretch=1)

        wiki_search_btn = QPushButton("搜索")
        wiki_search_btn.setStyleSheet(btn_style_primary)
        wiki_search_btn.clicked.connect(self.on_wiki_search)
        navbar.addWidget(wiki_search_btn)

        navbar.addSpacing(16)
        sep = QLabel()
        sep.setFixedWidth(1)
        sep.setStyleSheet("background: #e8e8e8;")
        sep.setFixedHeight(28)
        navbar.addWidget(sep)
        navbar.addSpacing(16)

        title_label = QLabel("页面标题:")
        title_label.setStyleSheet("font-weight: bold; color: #333; font-size: 13px;")
        navbar.addWidget(title_label)

        self.wiki_title_input = QLineEdit()
        self.wiki_title_input.setPlaceholderText("输入页面标题...")
        self.wiki_title_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #d9d9d9; border-radius: 4px;
                padding: 8px 12px; font-size: 14px; font-weight: bold; background: white;
            }
            QLineEdit:focus { border-color: #1890ff; }
        """)
        self.wiki_title_input.textChanged.connect(self.on_wiki_title_changed)
        navbar.addWidget(self.wiki_title_input, stretch=2)

        normal_layout.addLayout(navbar)

        # ── 搜索结果列表 ──
        self.wiki_search_result = QListWidget()
        self.wiki_search_result.setMaximumHeight(100)
        self.wiki_search_result.setStyleSheet("""
            QListWidget {
                border: 1px solid #ffd666; border-radius: 4px;
                background: #fffbe6; margin: 0; padding: 4px;
            }
            QListWidget::item {
                padding: 6px 10px; color: #ad6800; border-bottom: 1px solid #fff1b8;
            }
            QListWidget::item:hover { background: #fff1b8; }
        """)
        self.wiki_search_result.itemClicked.connect(self.on_wiki_search_result_clicked)
        self.wiki_search_result.hide()
        normal_layout.addWidget(self.wiki_search_result)

        # ── 3. 底部沉浸式工作区 ──
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(6)
        splitter.setStyleSheet("""
            QSplitter::handle { background: #e8e8e8; border-radius: 2px; }
            QSplitter::handle:hover { background: #1890ff; }
        """)

        # 左侧：页面列表
        left_panel = QWidget()
        left_panel.setMinimumWidth(180)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(6)

        page_list_label = QLabel("知识库页面")
        page_list_label.setStyleSheet(
            "font-weight: bold; color: #333; font-size: 13px; padding: 2px 0;"
        )
        left_layout.addWidget(page_list_label)

        self.wiki_page_list = QListWidget()
        self.wiki_page_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #e0e0e0; border-radius: 4px;
                background: #fafafa; outline: none;
            }
            QListWidget::item {
                padding: 10px 14px; border-bottom: 1px solid #f0f0f0;
            }
            QListWidget::item:hover { background: #e6f4ff; }
            QListWidget::item:selected {
                background: #1890ff; color: white; border-radius: 2px;
            }
        """)
        self.wiki_page_list.itemClicked.connect(self.on_wiki_page_clicked)
        left_layout.addWidget(self.wiki_page_list, stretch=1)

        splitter.addWidget(left_panel)

        # 右侧：编辑器
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(8)

        editor_header = QHBoxLayout()
        editor_header.setSpacing(8)

        editor_label = QLabel("页面内容 (支持 Markdown)")
        editor_label.setStyleSheet("font-weight: bold; color: #333; font-size: 13px;")
        editor_header.addWidget(editor_label)
        editor_header.addStretch()

        self.wiki_fullscreen_btn = QPushButton("沉浸阅读")
        self.wiki_fullscreen_btn.setStyleSheet("""
            QPushButton {
                background: white; border: 1px solid #d9d9d9; border-radius: 4px;
                padding: 5px 14px; font-size: 12px; color: #555;
            }
            QPushButton:hover { color: #1890ff; border-color: #1890ff; }
        """)
        self.wiki_fullscreen_btn.clicked.connect(self._on_wiki_fullscreen_enter)
        editor_header.addWidget(self.wiki_fullscreen_btn)
        right_layout.addLayout(editor_header)

        self.wiki_content_stack = QStackedWidget()
        self.wiki_mode_btn = self.wiki_fullscreen_btn

        self.wiki_content = QTextEdit()
        self.wiki_content.setPlaceholderText(
            "# 一级标题\n## 二级标题\n- 列表项\n```python\n代码块\n```\n**粗体** *斜体*"
        )
        self.wiki_content.setStyleSheet("""
            QTextEdit {
                border: 1px solid #e0e0e0; border-radius: 4px;
                padding: 16px; font-family: 'Consolas', 'Microsoft YaHei', monospace;
                font-size: 13px; line-height: 1.7; background: white;
            }
            QTextEdit:focus { border-color: #1890ff; }
        """)
        right_layout.addWidget(self.wiki_content, stretch=1)

        splitter.addWidget(right_panel)
        splitter.setSizes([260, 840])

        normal_layout.addWidget(splitter, stretch=1)

        self.wiki_root_stack.addWidget(normal_widget)  # index 0

        # ═══════════════════════════════════════════════════════════
        # Page 1: 全屏沉浸式阅读
        # ═══════════════════════════════════════════════════════════
        fullscreen_widget = QWidget()
        fullscreen_layout = QVBoxLayout(fullscreen_widget)
        fullscreen_layout.setContentsMargins(0, 0, 0, 0)
        fullscreen_layout.setSpacing(0)

        fs_topbar = QHBoxLayout()
        fs_topbar.setContentsMargins(20, 12, 20, 12)
        fs_topbar.setSpacing(12)

        self.wiki_fs_title = QLabel("")
        self.wiki_fs_title.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #333;"
        )
        fs_topbar.addWidget(self.wiki_fs_title, stretch=1)

        exit_fs_btn = QPushButton("返回编辑")
        exit_fs_btn.setStyleSheet("""
            QPushButton {
                background: #1890ff; color: white; border: none; border-radius: 4px;
                padding: 7px 18px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background: #40a9ff; }
        """)
        exit_fs_btn.clicked.connect(self._on_wiki_fullscreen_exit)
        fs_topbar.addWidget(exit_fs_btn)

        fullscreen_layout.addLayout(fs_topbar)

        fs_sep = QLabel()
        fs_sep.setFixedHeight(1)
        fs_sep.setStyleSheet("background: #e8e8e8;")
        fullscreen_layout.addWidget(fs_sep)

        self.wiki_fullscreen_browser = QTextBrowser()
        self.wiki_fullscreen_browser.setOpenExternalLinks(True)
        self.wiki_fullscreen_browser.setStyleSheet("""
            QTextBrowser {
                border: none; padding: 0px 4% 24px; background: #fffdf7;
                font-size: 16px; line-height: 1.9;
            }
        """)
        self.wiki_fullscreen_browser.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.wiki_fullscreen_browser.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        fullscreen_layout.addWidget(self.wiki_fullscreen_browser, stretch=1)

        self.wiki_root_stack.addWidget(fullscreen_widget)  # index 1
        self.wiki_root_stack.setCurrentIndex(0)

        outer_layout.addWidget(self.wiki_root_stack)

    # ── Public API ──

    def refresh_wiki_pages(self):
        """刷新Wiki页面列表"""
        self.wiki_page_list.clear()
        pages = self.wiki_fs.list_pages()
        for line in pages.split('\n'):
            if line.startswith('## '):
                page_name = line[3:].strip()
                self.wiki_page_list.addItem(page_name)

    # ── Event handlers ──

    def on_wiki_new_page(self):
        """新建Wiki页面"""
        if self.wiki_root_stack.currentIndex() == 1:
            self.wiki_root_stack.setCurrentIndex(0)
        self.wiki_title_input.clear()
        self.wiki_content.clear()
        self.wiki_title_input.setFocus()

    def on_wiki_delete_page(self):
        """删除Wiki页面"""
        current_item = self.wiki_page_list.currentItem()
        if current_item:
            page_name = current_item.text()
            reply = QMessageBox.question(
                self, '确认', f'确定删除页面 "{page_name}"？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.wiki_fs.delete_wiki_page(page_name)
                self.refresh_wiki_pages()
                self.wiki_title_input.clear()
                self.wiki_content.clear()

    def on_wiki_save(self):
        """保存Wiki页面"""
        page_name = self.wiki_title_input.text().strip()
        if not page_name:
            QMessageBox.warning(self, '警告', '请输入页面标题')
            return
        content = self.wiki_content.toPlainText()
        result = self.wiki_fs.write_wiki_page(page_name, content)
        self.refresh_wiki_pages()
        QMessageBox.information(self, '成功', result)

    def on_wiki_search(self):
        """搜索Wiki页面"""
        keyword = self.wiki_search_input.text().strip()
        if not keyword:
            self.wiki_search_result.clear()
            self.wiki_search_result.hide()
            return
        self.wiki_search_result.clear()
        results = self.wiki_fs.search_pages(keyword)
        if results:
            for line in results.split('\n'):
                if line.strip():
                    self.wiki_search_result.addItem(line.strip())
            self.wiki_search_result.show()
        else:
            self.wiki_search_result.addItem(f'未找到匹配 "{keyword}" 的页面')
            self.wiki_search_result.show()

    def on_wiki_search_result_clicked(self, item):
        """点击搜索结果"""
        text = item.text()
        if text.startswith('未找到'):
            return
        if text.startswith('- '):
            text = text[2:]
        if ': ' in text:
            page_name = text.split(': ')[0]
        elif ':' in text:
            page_name = text.split(':')[0]
        else:
            page_name = text
        content = self.wiki_fs.read_wiki_page(page_name)
        if content and not content.startswith('Error:'):
            self.wiki_title_input.setText(page_name)
            self.wiki_content.setPlainText(content)

    def on_wiki_page_clicked(self, item):
        """点击Wiki页面时加载内容"""
        if self.wiki_root_stack.currentIndex() == 1:
            self.wiki_root_stack.setCurrentIndex(0)
        page_name = item.text()
        content = self.wiki_fs.read_wiki_page(page_name)
        if content and not content.startswith('Error:'):
            self.wiki_title_input.setText(page_name)
            self.wiki_content.setPlainText(content)
        else:
            self.wiki_title_input.setText(page_name)
            self.wiki_content.clear()

    def on_wiki_title_changed(self, text):
        """标题变化"""
        pass

    def on_wiki_upload_file(self):
        """上传文件到知识库"""
        last_dir = QSettings('DataProcessor', 'Pro').value('wiki_upload_last_dir', '')
        file_path, _ = QFileDialog.getOpenFileName(self, '选择文件', last_dir, 'All Files (*)')
        if file_path:
            QSettings('DataProcessor', 'Pro').setValue('wiki_upload_last_dir', os.path.dirname(file_path))
            result = self.wiki_fs.upload_file_to_wiki(file_path)
            self.refresh_wiki_pages()
            QMessageBox.information(self, '成功', result)

    def show_wiki_help(self):
        """显示知识库使用说明"""
        help_text = """# 知识库管理 — 使用说明

## 功能概述

知识库管理是本软件内置的本地化文档管理系统，专为科研和工程项目中的知识沉淀与快速检索而设计。与云端笔记工具不同，所有数据完全存储在本地文件系统，无需网络连接，保障数据安全。

核心定位：作为项目的"第二大脑"，将传感器配置经验、数据分析方法、调试记录、公式推导等零散知识系统化组织，形成可搜索、可复用的知识资产。

## 核心特点

1. **纯本地存储** — 所有页面以 Markdown 文件形式保存在 `wiki_vault/pages/` 目录下，索引导入 `wiki_vault/wiki_map.json`，可直接用任何文本编辑器打开
2. **沉浸式分割布局** — 左侧页面列表可拖拽调整宽度，右侧全功能 Markdown 编辑器，互不干扰
3. **一键沉浸阅读** — 点击"沉浸阅读"按钮进入全屏阅读模式，自动渲染 Markdown 为精美文章视图，右上角"返回编辑"退出
4. **顶部统一操作台** — 所有管理按钮（新建、删除、上传、说明、保存）集中在页面顶部工具栏，一目了然
5. **水平导航搜索条** — 搜索关键词和页面标题输入框位于同一水平线，查找与定义一次完成
6. **AI 技能联动** — 知识库页面可被 AI Agent 的 read_wiki_page / write_wiki_page / search_wiki_pages 等技能直接读写，实现 AI 辅助知识管理

## 页面管理操作

### 浏览与查看
- 左侧"知识库页面"列表显示所有已保存页面，点击任意页面名称即可加载
- 页面标题自动填入中部导航条的标题输入框，内容以 Markdown 原文显示在编辑区
- 点击"沉浸阅读"按钮 → 全屏渲染 Markdown 为美观的阅读视图（图片、表格、代码高亮）

### 新建页面
1. 点击顶部工具栏"+ 新建"按钮 → 标题和内容区清空
2. 在导航条的"页面标题"输入框中输入标题
3. 在编辑区编写 Markdown 内容，支持标题、列表、代码块、表格、粗体、斜体等
4. 点击顶部"保存页面"按钮 → 页面持久化到本地文件

### 编辑与更新
1. 从左侧列表选择已有页面 → 标题和内容自动加载
2. 直接修改标题或内容 → 点击"保存页面"
3. 系统自动覆盖原文件

### 删除页面
1. 选中左侧列表中目标页面
2. 点击顶部工具栏"删除"按钮 → 确认对话框
3. 删除操作同时移除本地 .md 文件和索引条目，不可恢复

### 关键词搜索
1. 在顶部导航条搜索框输入关键词
2. 点击"搜索"按钮 → 匹配结果在下拉列表中显示
3. 点击搜索结果项 → 自动跳转并加载对应页面内容

### 上传外部文件
1. 点击顶部"上传文件"按钮 → 系统文件对话框
2. 选择任意格式文件（PDF、Word、TXT、图片等）
3. 文件被复制到知识库并自动注册索引

## 存储结构

```
wiki_vault/
├── pages/              ← 所有 Markdown 页面文件 (.md)
│   ├── 传感器配置经验.md
│   ├── ENLIGHT数据格式说明.md
│   └── 调试记录_20260527.md
└── wiki_map.json       ← 页面索引（标题→文件映射）
```

## 与 AI Agent 的协作

知识库不仅是手动文档工具，更是 AI Agent 的长期记忆载体：
- **read_wiki_page** — Agent 读取指定页面内容作为推理上下文
- **write_wiki_page** — Agent 将分析结果、诊断结论自动写入知识库
- **list_wiki_pages** — Agent 获取所有页面列表
- **search_wiki_pages** — Agent 按关键词检索相关知识

典型场景：AI 诊断完成后自动将诊断报告写入知识库，下次遇到类似问题时 Agent 可检索历史经验作为参考。
"""
        help_dialog = QDialog(self)
        help_dialog.setWindowTitle('知识库管理 — 使用说明')
        help_dialog.resize(800, 700)

        layout = QVBoxLayout()
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(help_text)
        layout.addWidget(text_edit)

        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(help_dialog.close)
        layout.addWidget(close_btn)

        help_dialog.setLayout(layout)
        help_dialog.exec()

    def _on_wiki_fullscreen_enter(self):
        """进入全屏沉浸式阅读"""
        md_text = self.wiki_content.toPlainText()
        if not md_text.strip():
            QMessageBox.information(self, '提示', '请先编写或加载页面内容后再进入沉浸阅读')
            return
        html = self._markdown_to_html(md_text)
        import re as _re
        _wiki_base = str(self.wiki_fs.base_dir.resolve()).replace("\\", "/")
        _doc = QTextDocument()
        _img_index = 0

        def _replace_img_src(m):
            nonlocal _img_index
            rel = m.group(1)
            abs_path = _wiki_base + "/" + rel
            try:
                with open(abs_path, "rb") as _f:
                    _raw = _f.read()
                img = QImage.fromData(QByteArray(_raw))
                if img.isNull():
                    return m.group(0)
                res_name = f"wiki_img_{_img_index}"
                _img_index += 1
                _doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(res_name), img)
                return f'src="{res_name}"'
            except Exception:
                return m.group(0)

        html = _re.sub(
            r'src="(assets/[^"]+\.(?:jpg|jpeg|png|gif|webp|svg|bmp))"',
            _replace_img_src, html,
        )
        html = _re.sub(r'(<br\s*/?>\s*){3,}', '<br/>', html)
        html = _re.sub(r'<p>\s*</p>', '', html)
        _doc.setHtml(html)

        _cursor = QTextCursor(_doc)
        _cursor.movePosition(QTextCursor.MoveOperation.Start)
        _heading_margins = {1: (28, 10), 2: (24, 8), 3: (18, 6)}
        while True:
            _block = _cursor.block()
            _bfmt = _block.blockFormat()
            _level = _bfmt.headingLevel()
            if _level in _heading_margins:
                _bfmt.setTopMargin(_heading_margins[_level][0])
                _bfmt.setBottomMargin(_heading_margins[_level][1])
            else:
                _bfmt.setTopMargin(4)
                _bfmt.setBottomMargin(4)
            _cursor.setBlockFormat(_bfmt)
            if not _cursor.movePosition(QTextCursor.MoveOperation.NextBlock):
                break

        self.wiki_fullscreen_browser.setDocument(_doc)
        page_title = self.wiki_title_input.text().strip() or "知识库页面"
        self.wiki_fs_title.setText(page_title)
        self.wiki_root_stack.setCurrentIndex(1)

    def _on_wiki_fullscreen_exit(self):
        """退出全屏阅读，返回编辑布局"""
        self.wiki_root_stack.setCurrentIndex(0)

    # ── Background embedding init ──

    def _init_wiki_embeddings(self):
        """后台线程初始化语义搜索向量索引（静默失败不影响主流程）"""
        def _rebuild():
            try:
                self.wiki_fs._ensure_chroma()
                existing = self.wiki_fs._collection.count()
                page_count = len(self.wiki_fs._load_map().get("index", {}))
                if existing >= page_count and page_count > 0:
                    print(f"[Wiki] 向量索引已就绪: {existing} 条")
                    return
                print(f"[Wiki] 向量索引不完整 ({existing}/{page_count})，开始后台重建...")
                count = self.wiki_fs.rebuild_embeddings()
                print(f"[Wiki] 向量索引后台重建完成: {count} 页")
            except ImportError:
                pass
            except Exception as e:
                print(f"[Wiki] 向量索引初始化跳过: {e}")

        t = threading.Thread(target=_rebuild, daemon=True)
        t.start()

    # ── Markdown → HTML ──

    @staticmethod
    def _markdown_to_html(md_text: str) -> str:
        """将 Markdown 转换为 HTML 文章阅读视图。"""
        import re as _re

        body_text = md_text
        title = source_url = date_str = ""
        fm_match = _re.match(r'^---\s*\n(.*?)\n---\s*\n', md_text, _re.DOTALL)
        if fm_match:
            fm_content = fm_match.group(1)
            body_text = md_text[fm_match.end():]
            for line in fm_content.split("\n"):
                line = line.strip()
                if line.startswith("title:"):
                    title = line[6:].strip().strip('"').strip("'")
                elif line.startswith("source_url:"):
                    source_url = line[11:].strip().strip('"').strip("'")
                elif line.startswith("date:"):
                    date_str = line[5:].strip().strip('"').strip("'")
        body_text = _re.sub(r'^\s*---\s*\n.*?\n---\s*\n', '', body_text, flags=_re.DOTALL)

        body_text = _re.sub(r'\n{3,}', '\n\n', body_text)
        try:
            import markdown as md_lib
            html_body = md_lib.markdown(body_text, extensions=["tables"])
            html_body = _re.sub(
                r'<code>',
                '<span style="background:#f0f0f0;padding:2px 6px;border-radius:3px;'
                'font-family:Consolas,monospace;font-size:0.88em;color:#c7254e;">',
                html_body,
            )
            html_body = html_body.replace('</code>', '</span>')
            html_body = _re.sub(
                r'<pre>',
                '<div style="background:#1e1e1e;color:#d4d4d4;padding:16px 20px;'
                'border-radius:8px;font-size:13px;line-height:1.55;margin:14px 0;'
                'white-space:pre-wrap;font-family:Consolas,monospace;">',
                html_body,
            )
            html_body = html_body.replace('</pre>', '</div>')
            html_body = _re.sub(r'<p>\s*(<img[^>]*>)\s*</p>', r'\1', html_body)
        except ImportError:
            html_body = WikiTabWidget._basic_md_to_html(body_text)

        header_html = ""
        if title or date_str or source_url:
            header_parts = []
            if title:
                header_parts.append(f'<div class="article-title">{title}</div>')
            meta_items = []
            if date_str:
                meta_items.append(f'<span class="meta-date">📅 {date_str}</span>')
            if source_url:
                display_url = source_url[:80] + "..." if len(source_url) > 80 else source_url
                meta_items.append(
                    f'<span class="meta-source">🔗 <a href="{source_url}">原文链接</a></span>'
                )
            if meta_items:
                header_parts.append(
                    '<div class="article-meta">' + " &nbsp;·&nbsp; ".join(meta_items) + '</div>'
                )
            header_html = '<div class="article-header">' + "\n".join(header_parts) + '</div>'

        css = """
        <style>
            body { font-family: 'Microsoft YaHei', 'PingFang SC', 'Segoe UI', 'Noto Sans SC', sans-serif;
                   font-size: 16px; line-height: 1.6; color: #2c2c2c;
                   width: 100%; padding: 24px 6% 40px;
                   background: #fffdf7; }
            .article-header { background: linear-gradient(135deg, #f8fbff 0%, #eef5ff 100%);
                   border-left: 4px solid #1890ff; border-radius: 0 8px 8px 0;
                   padding: 20px 24px; margin-bottom: 32px; }
            .article-title { font-size: 1.7em; font-weight: bold; color: #111;
                   line-height: 1.4; margin-bottom: 8px; }
            .article-meta { font-size: 0.85em; color: #888; }
            .article-meta a { color: #1890ff; text-decoration: none; }
            .meta-date, .meta-source { display: inline-block; }
            h1 { font-size: 1.55em; margin: 28px 0 10px; padding-bottom: 8px;
                 border-bottom: 2px solid #1890ff; color: #111; line-height: 1.4; }
            h2 { font-size: 1.3em; margin: 24px 0 8px; padding-bottom: 6px;
                 border-bottom: 1px solid #e8e8e8; color: #1a1a1a; }
            h3 { font-size: 1.12em; margin: 18px 0 6px; color: #333; }
            p { margin: 4px 0; text-align: justify; }
            img { display: block; max-width: 100%; height: auto; margin: 0 auto;
                  border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
            blockquote { border-left: 4px solid #1890ff; margin: 16px 0; padding: 10px 18px;
                         background: #f0f5ff; color: #555; border-radius: 0 4px 4px 0;
                         font-style: italic; }
            code { background: #f0f0f0; padding: 2px 6px; border-radius: 3px;
                   font-family: 'Consolas', 'Courier New', 'Source Code Pro', monospace;
                   font-size: 0.88em; color: #c7254e; }
            pre { background: #1e1e1e; color: #d4d4d4; padding: 16px 20px;
                  border-radius: 8px; overflow-x: auto; font-size: 13px;
                  line-height: 1.55; margin: 14px 0; }
            pre code { background: none; padding: 0; color: inherit; font-size: inherit; }
            table { border-collapse: collapse; width: 100%; margin: 16px 0;
                    font-size: 0.95em; }
            th, td { border: 1px solid #e0e0e0; padding: 10px 14px; text-align: left; }
            th { background: #f7f7f7; font-weight: 600; color: #333; }
            tr:nth-child(even) td { background: #fafafa; }
            a { color: #1890ff; text-decoration: none; border-bottom: 1px dotted #b0d0ff; }
            a:hover { border-bottom-style: solid; }
            ul, ol { padding-left: 26px; margin: 10px 0; }
            li { margin: 5px 0; line-height: 1.7; }
            hr { border: none; border-top: 1px solid #e8e8e8; margin: 28px 0; }
            strong { color: #1a1a1a; }
            em { color: #555; }
        </style>
        """
        return f"<html><head><meta charset='utf-8'>{css}</head><body>{header_html}{html_body}</body></html>"

    @staticmethod
    def _basic_md_to_html(md_text: str) -> str:
        """内置轻量 Markdown→HTML 转换器（不依赖外部库）"""
        import re

        lines = md_text.split("\n")
        result = []
        in_code_block = False
        in_list = False
        list_type = None

        i = 0
        while i < len(lines):
            line = lines[i]

            if line.strip().startswith("```"):
                if in_code_block:
                    result.append("</code></pre>")
                    in_code_block = False
                else:
                    lang = line.strip()[3:].strip()
                    result.append(f'<pre><code class="{lang}">')
                    in_code_block = True
                i += 1
                continue

            if in_code_block:
                result.append(line)
                i += 1
                continue

            if not line.strip():
                if in_list:
                    result.append(f"</{list_type}>")
                    in_list = False
                    list_type = None
                result.append("")
                i += 1
                continue

            h_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if h_match:
                if in_list:
                    result.append(f"</{list_type}>")
                    in_list = False
                    list_type = None
                level = len(h_match.group(1))
                result.append(f"<h{level}>{h_match.group(2)}</h{level}>")
                i += 1
                continue

            ul_match = re.match(r"^[\-\*]\s+(.+)$", line)
            if ul_match:
                if not in_list:
                    result.append("<ul>")
                    in_list = True
                    list_type = "ul"
                elif list_type != "ul":
                    result.append(f"</{list_type}><ul>")
                    list_type = "ul"
                result.append(f"<li>{ul_match.group(1)}</li>")
                i += 1
                continue

            ol_match = re.match(r"^\d+\.\s+(.+)$", line)
            if ol_match:
                if not in_list:
                    result.append("<ol>")
                    in_list = True
                    list_type = "ol"
                elif list_type != "ol":
                    result.append(f"</{list_type}><ol>")
                    list_type = "ol"
                result.append(f"<li>{ol_match.group(1)}</li>")
                i += 1
                continue

            bq_match = re.match(r"^>\s?(.*)$", line)
            if bq_match:
                if in_list:
                    result.append(f"</{list_type}>")
                    in_list = False
                    list_type = None
                result.append(f"<blockquote>{bq_match.group(1)}</blockquote>")
                i += 1
                continue

            if re.match(r"^[\-\*_]{3,}$", line.strip()):
                if in_list:
                    result.append(f"</{list_type}>")
                    in_list = False
                    list_type = None
                result.append("<hr>")
                i += 1
                continue

            if in_list:
                result.append(f"</{list_type}>")
                in_list = False
                list_type = None

            processed = line
            processed = re.sub(r"\!\[([^\]]*)\]\(([^)]+)\)", r'<img src="\2" alt="\1">', processed)
            processed = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', processed)
            processed = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", processed)
            processed = re.sub(r"\*(.+?)\*", r"<em>\1</em>", processed)
            processed = re.sub(r"`([^`]+)`", r"<code>\1</code>", processed)

            result.append(f"<p>{processed}</p>")
            i += 1

        if in_code_block:
            result.append("</code></pre>")
        if in_list:
            result.append(f"</{list_type}>")

        return "\n".join(result)
