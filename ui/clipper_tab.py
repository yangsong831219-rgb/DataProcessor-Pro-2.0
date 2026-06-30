"""网络信息剪藏页面 — URL 抓取、图片本地化、一键入库."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QCheckBox, QTextEdit, QLineEdit, QFileDialog,
    QMessageBox, QApplication,
)
from PyQt6.QtCore import pyqtSignal


class WebClipperWidget(QWidget):
    """网络剪藏页面 — URL 抓取、图片本地化、视频下载、一键入库."""

    cookie_changed = pyqtSignal(str)          # cookie value
    wiki_page_saved = pyqtSignal()             # new wiki page created

    def __init__(self, parent=None):
        super().__init__(parent)
        self._clipped_title = ''
        self._clipped_md = ''
        self._clipped_url = ''
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        header = QLabel('网络信息剪藏')
        header.setStyleSheet(
            'font-size: 18px; font-weight: bold; color: #1890ff; padding: 4px 0;'
        )
        layout.addWidget(header)

        desc = QLabel(
            '支持微信公众号、学术期刊、Bilibili、YouTube 等平台的'
            '图文抓取与多媒体下载，一键存入本地知识库'
        )
        desc.setWordWrap(True)
        desc.setStyleSheet('color: #666; font-size: 13px; padding-bottom: 6px;')
        layout.addWidget(desc)

        # ── URL 输入区 ──
        url_group = QGroupBox('目标链接')
        url_group.setStyleSheet("""
            QGroupBox { border: 1px solid #e0e0e0; border-radius: 8px;
                font-weight: bold; color: #333; padding: 14px; padding-top: 24px; background: white; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
        """)
        url_layout = QVBoxLayout(url_group)
        url_layout.setSpacing(8)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel('URL:'))
        self.clip_url_input = QLineEdit()
        self.clip_url_input.setPlaceholderText(
            '粘贴微信公众号 / B站 / YouTube / 学术期刊 链接...'
        )
        self.clip_url_input.setStyleSheet("""
            QLineEdit { border: 1px solid #d9d9d9; border-radius: 4px;
                padding: 10px 12px; font-size: 13px; }
            QLineEdit:focus { border-color: #1890ff; }
        """)
        self.clip_url_input.textChanged.connect(self._on_clip_url_changed)
        url_row.addWidget(self.clip_url_input, stretch=1)

        paste_btn = QPushButton('粘贴')
        paste_btn.setStyleSheet("""
            QPushButton { background: #f0f0f0; border: 1px solid #d9d9d9;
                border-radius: 4px; padding: 10px 16px; }
            QPushButton:hover { background: #e0e0e0; }
        """)
        paste_btn.clicked.connect(self._on_clip_paste)
        url_row.addWidget(paste_btn)
        url_layout.addLayout(url_row)

        self.clip_platform_label = QLabel('')
        self.clip_platform_label.setStyleSheet('color: #999; font-size: 12px;')
        url_layout.addWidget(self.clip_platform_label)

        self.clip_cookie_hint = QLabel(
            '知乎等平台需要登录 Cookie 才能抓取，'
            '请在下方"高级选项"中粘贴浏览器 Cookie'
        )
        self.clip_cookie_hint.setStyleSheet(
            'color: #fa8c16; font-size: 12px; padding: 4px 0;'
        )
        self.clip_cookie_hint.setWordWrap(True)
        self.clip_cookie_hint.hide()
        url_layout.addWidget(self.clip_cookie_hint)

        layout.addWidget(url_group)

        # ── 高级选项 (Cookie) ──
        self.clip_advanced_group = QGroupBox('高级选项 (Cookie 认证)')
        self.clip_advanced_group.setStyleSheet("""
            QGroupBox { border: 1px solid #e0e0e0; border-radius: 6px;
                font-weight: bold; color: #555; padding: 10px; padding-top: 20px;
                background: #fafafa; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
        """)
        advanced_inner = QVBoxLayout(self.clip_advanced_group)
        advanced_inner.setSpacing(6)

        cookie_hint = QLabel(
            '粘贴浏览器 Cookie 字符串'
            '（从开发者工具 → Network → 请求头 → Cookie 复制）'
        )
        cookie_hint.setStyleSheet('color: #999; font-size: 11px;')
        cookie_hint.setWordWrap(True)
        advanced_inner.addWidget(cookie_hint)

        self.clip_cookie_input = QTextEdit()
        self.clip_cookie_input.setPlaceholderText(
            '在此粘贴 Cookie...（仅需要登录的网站填写，普通网页留空即可）'
        )
        self.clip_cookie_input.setMaximumHeight(60)
        self.clip_cookie_input.setStyleSheet("""
            QTextEdit { border: 1px solid #d9d9d9; border-radius: 4px;
                padding: 6px 10px; font-size: 12px; background: white;
                font-family: 'Consolas', monospace; }
            QTextEdit:focus { border-color: #1890ff; }
        """)
        advanced_inner.addWidget(self.clip_cookie_input)
        self.clip_cookie_input.textChanged.connect(self._on_cookie_changed)

        self.clip_advanced_group.setVisible(False)
        layout.addWidget(self.clip_advanced_group)

        # ── 展开/折叠高级选项按钮 ──
        cookie_row = QHBoxLayout()
        cookie_row.setSpacing(8)
        self.clip_toggle_advanced_btn = QPushButton('高级选项 ▸')
        self.clip_toggle_advanced_btn.setStyleSheet("""
            QPushButton { background: none; border: none; color: #999; font-size: 12px; padding: 2px 0; }
            QPushButton:hover { color: #1890ff; }
        """)
        self.clip_toggle_advanced_btn.clicked.connect(self._on_toggle_advanced)
        cookie_row.addWidget(self.clip_toggle_advanced_btn)
        cookie_row.addStretch()
        layout.addLayout(cookie_row)

        # ── 抓取选项 ──
        options_row = QHBoxLayout()
        options_row.setSpacing(16)

        self.clip_download_images_cb = QCheckBox('下载图片到本地')
        self.clip_download_images_cb.setChecked(True)
        self.clip_download_images_cb.setStyleSheet('font-size: 13px;')
        options_row.addWidget(self.clip_download_images_cb)

        self.clip_download_video_cb = QCheckBox('下载视频')
        self.clip_download_video_cb.setStyleSheet('font-size: 13px;')
        self.clip_download_video_cb.setEnabled(False)
        options_row.addWidget(self.clip_download_video_cb)

        options_row.addStretch()
        layout.addLayout(options_row)

        # ── 操作按钮 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self.clip_fetch_btn = QPushButton('开始抓取')
        self.clip_fetch_btn.setStyleSheet("""
            QPushButton { background-color: #1890ff; color: white; border: none;
                border-radius: 6px; padding: 12px 32px; font-size: 14px; font-weight: bold; }
            QPushButton:hover { background-color: #40a9ff; }
            QPushButton:disabled { background-color: #d9d9d9; color: #999; }
        """)
        self.clip_fetch_btn.clicked.connect(self._on_clip_fetch)
        btn_row.addWidget(self.clip_fetch_btn)

        self.clip_save_btn = QPushButton('保存到知识库')
        self.clip_save_btn.setStyleSheet("""
            QPushButton { background-color: #52c41a; color: white; border: none;
                border-radius: 6px; padding: 12px 28px; font-size: 14px; font-weight: bold; }
            QPushButton:hover { background-color: #73d13d; }
            QPushButton:disabled { background-color: #d9d9d9; color: #999; }
        """)
        self.clip_save_btn.clicked.connect(self._on_clip_save)
        self.clip_save_btn.setEnabled(False)
        btn_row.addWidget(self.clip_save_btn)

        self.clip_clear_btn = QPushButton('清空')
        self.clip_clear_btn.setStyleSheet("""
            QPushButton { background: #f0f0f0; border: 1px solid #d9d9d9;
                border-radius: 6px; padding: 12px 20px; font-size: 13px; }
            QPushButton:hover { background: #e0e0e0; }
        """)
        self.clip_clear_btn.clicked.connect(self._on_clip_clear)
        btn_row.addWidget(self.clip_clear_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── 状态栏 ──
        self.clip_status_label = QLabel('就绪 — 粘贴链接后点击"开始抓取"')
        self.clip_status_label.setStyleSheet(
            'color: #999; font-size: 12px; padding: 6px 12px;'
            ' background: #fafafa; border-radius: 4px;'
        )
        layout.addWidget(self.clip_status_label)

        # ── 预览区 ──
        preview_group = QGroupBox('内容预览')
        preview_group.setStyleSheet("""
            QGroupBox { border: 1px solid #e0e0e0; border-radius: 8px;
                font-weight: bold; color: #333; padding: 12px; padding-top: 24px; background: white; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
        """)
        preview_layout = QVBoxLayout(preview_group)

        self.clip_preview_info = QLabel('')
        self.clip_preview_info.setStyleSheet(
            'color: #1890ff; font-size: 12px; padding: 4px 0;'
        )
        self.clip_preview_info.setWordWrap(True)
        preview_layout.addWidget(self.clip_preview_info)

        self.clip_preview_text = QTextEdit()
        self.clip_preview_text.setReadOnly(True)
        self.clip_preview_text.setPlaceholderText('抓取的内容将在此预览...')
        self.clip_preview_text.setStyleSheet("""
            QTextEdit { border: 1px solid #e0e0e0; border-radius: 4px;
                padding: 12px; font-size: 13px; line-height: 1.6; background: #fafafa; }
        """)
        preview_layout.addWidget(self.clip_preview_text, stretch=1)

        layout.addWidget(preview_group, stretch=1)

    # ── Public API ──

    def set_cookie(self, cookie: str):
        """设置初始 Cookie 值（由主窗口在加载设置后调用）."""
        if cookie:
            self.clip_cookie_input.setPlainText(cookie)

    # ── Event handlers ──

    def _on_clip_url_changed(self, text: str):
        text_lower = text.lower()
        if 'mp.weixin.qq.com' in text_lower:
            self.clip_platform_label.setText('已识别: 微信公众号')
            self.clip_platform_label.setStyleSheet('color: #52c41a; font-size: 12px;')
            self.clip_download_video_cb.setEnabled(False)
            self.clip_download_video_cb.setChecked(False)
            self.clip_cookie_hint.hide()
        elif 'zhihu.com' in text_lower:
            self.clip_platform_label.setText('已识别: 知乎 — 需粘贴 Cookie')
            self.clip_platform_label.setStyleSheet('color: #fa8c16; font-size: 12px;')
            self.clip_download_video_cb.setEnabled(False)
            self.clip_download_video_cb.setChecked(False)
            self.clip_cookie_hint.show()
            if not self.clip_advanced_group.isVisible():
                self._on_toggle_advanced()
        elif 'bilibili.com' in text_lower:
            self.clip_platform_label.setText('已识别: Bilibili')
            self.clip_platform_label.setStyleSheet('color: #fa8c16; font-size: 12px;')
            self.clip_download_video_cb.setEnabled(True)
            self.clip_cookie_hint.hide()
        elif 'youtube.com' in text_lower or 'youtu.be' in text_lower:
            self.clip_platform_label.setText('已识别: YouTube')
            self.clip_platform_label.setStyleSheet('color: #ff4d4f; font-size: 12px;')
            self.clip_download_video_cb.setEnabled(True)
            self.clip_cookie_hint.hide()
        elif 'arxiv.org' in text_lower or 'doi.org' in text_lower or 'scholar' in text_lower:
            self.clip_platform_label.setText('已识别: 学术期刊')
            self.clip_platform_label.setStyleSheet('color: #722ed1; font-size: 12px;')
            self.clip_download_video_cb.setEnabled(False)
            self.clip_download_video_cb.setChecked(False)
            self.clip_cookie_hint.hide()
        elif not text.strip():
            self.clip_platform_label.setText('')
            self.clip_download_video_cb.setEnabled(False)
            self.clip_download_video_cb.setChecked(False)
            self.clip_cookie_hint.hide()
        else:
            self.clip_platform_label.setText('已识别: 通用网页')
            self.clip_platform_label.setStyleSheet('color: #999; font-size: 12px;')
            self.clip_download_video_cb.setEnabled(False)
            self.clip_download_video_cb.setChecked(False)
            self.clip_cookie_hint.hide()

    def _on_cookie_changed(self):
        cookies = self.clip_cookie_input.toPlainText().strip()
        self.cookie_changed.emit(cookies)

    def _on_toggle_advanced(self):
        visible = not self.clip_advanced_group.isVisible()
        self.clip_advanced_group.setVisible(visible)
        self.clip_toggle_advanced_btn.setText(
            '高级选项 ▾' if visible else '高级选项 ▸'
        )

    def _on_clip_paste(self):
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        if text:
            self.clip_url_input.setText(text.strip())

    def _on_clip_fetch(self):
        url = self.clip_url_input.text().strip()
        if not url:
            QMessageBox.warning(self, '提示', '请输入目标 URL')
            return

        self.clip_fetch_btn.setEnabled(False)
        self.clip_status_label.setText('正在抓取网页内容...')
        self.clip_status_label.setStyleSheet(
            'color: #faad14; font-size: 12px; padding: 6px 12px;'
            ' background: #fffbe6; border-radius: 4px;'
        )
        QApplication.processEvents()

        try:
            from dp_engine.web_clipper import WebClipper

            clipper = WebClipper()
            cookies = self.clip_cookie_input.toPlainText().strip()
            title, md_body = clipper.fetch_article(url, cookies=cookies)
            self._clipped_title = title
            self._clipped_md = md_body
            self._clipped_url = url

            self.clip_preview_info.setText(f'标题: {title}\n来源: {url}')
            preview_display = md_body[:5000] + ('...' if len(md_body) > 5000 else '')
            self.clip_preview_text.setPlainText(preview_display)

            self.clip_save_btn.setEnabled(True)
            self.clip_status_label.setText(f'抓取成功 — 正文 {len(md_body)} 字符')
            self.clip_status_label.setStyleSheet(
                'color: #52c41a; font-size: 12px; padding: 6px 12px;'
                ' background: #f6ffed; border-radius: 4px;'
            )
        except Exception as e:
            import traceback
            self.clip_status_label.setText(f'抓取失败: {str(e)}')
            self.clip_status_label.setStyleSheet(
                'color: #ff4d4f; font-size: 12px; padding: 6px 12px;'
                ' background: #fff1f0; border-radius: 4px;'
            )
            QMessageBox.critical(self, '抓取失败', f'{str(e)}\n\n{traceback.format_exc()}')
        finally:
            self.clip_fetch_btn.setEnabled(True)

    def _on_clip_save(self):
        if not self._clipped_md:
            QMessageBox.warning(self, '提示', '请先抓取网页内容')
            return

        self.clip_save_btn.setEnabled(False)
        self.clip_status_label.setText('正在下载图片并保存到知识库...')
        self.clip_status_label.setStyleSheet(
            'color: #faad14; font-size: 12px; padding: 6px 12px;'
            ' background: #fffbe6; border-radius: 4px;'
        )
        QApplication.processEvents()

        try:
            from dp_engine.web_clipper import WebClipper

            clipper = WebClipper()
            md_content = self._clipped_md

            if self.clip_download_images_cb.isChecked():
                cookies = self.clip_cookie_input.toPlainText().strip()
                md_content = clipper.download_and_replace_images(
                    md_content, cookies=cookies, referer=self._clipped_url
                )

            if self.clip_download_video_cb.isChecked():
                video_path = clipper.download_video(self._clipped_url)
                if video_path:
                    md_content += f'\n\n> 视频已下载: {video_path}\n'

            saved_path = clipper.save_to_wiki(
                self._clipped_url, self._clipped_title, md_content
            )

            self.clip_preview_text.setPlainText(
                md_content[:5000] + ('...' if len(md_content) > 5000 else '')
            )
            self._clipped_md = md_content

            self.wiki_page_saved.emit()

            self.clip_status_label.setText(f'已保存 → {saved_path}')
            self.clip_status_label.setStyleSheet(
                'color: #52c41a; font-size: 12px; padding: 6px 12px;'
                ' background: #f6ffed; border-radius: 4px;'
            )
        except Exception as e:
            import traceback
            self.clip_status_label.setText(f'保存失败: {str(e)}')
            self.clip_status_label.setStyleSheet(
                'color: #ff4d4f; font-size: 12px; padding: 6px 12px;'
                ' background: #fff1f0; border-radius: 4px;'
            )
            QMessageBox.critical(self, '保存失败', f'{str(e)}\n\n{traceback.format_exc()}')
        finally:
            self.clip_save_btn.setEnabled(True)

    def _on_clip_clear(self):
        self.clip_url_input.clear()
        self.clip_preview_text.clear()
        self.clip_preview_info.clear()
        self.clip_platform_label.clear()
        self.clip_status_label.setText('就绪 — 粘贴链接后点击"开始抓取"')
        self.clip_status_label.setStyleSheet(
            'color: #999; font-size: 12px; padding: 6px 12px;'
            ' background: #fafafa; border-radius: 4px;'
        )
        self.clip_save_btn.setEnabled(False)
        self._clipped_title = ''
        self._clipped_md = ''
        self._clipped_url = ''
