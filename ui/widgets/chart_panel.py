"""可复用 matplotlib 图表面板 — 统一工具栏 + 全屏 + 图表设置

每个 ChartPanel 封装:
  - matplotlib Figure + FigureCanvasQTAgg
  - 工具栏: mpl 原生 zoom/pan/home + 图表设置 + 全屏 + 保存
  - 可全屏弹窗 (深拷贝 Figure，零 reparent，零 Figure 共享)
  - 图表设置对话框 (标题/轴范围/标签/图例/线宽/网格/字号)
"""

from __future__ import annotations

from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QDialog, QLabel,
    QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox,
    QLineEdit, QFileDialog, QPushButton,
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal

# ★ 字体优先级 — Microsoft YaHei 覆盖 CJK + 上标² + Unicode 数学符号
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

from ui.components import create_button


class _EscFilter(QObject):
    """ESC 键事件过滤器 — 全屏对话框: 一次 ESC 直接关闭 (非两步)"""
    def __init__(self, dialog, parent=None):
        super().__init__(parent)
        self._dlg = dialog

    def eventFilter(self, obj, event):
        if event.type() == event.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            # 全屏 frameless 对话框只做全屏一件事，ESC 直接关闭
            obj.accept()
            return True
        return False


class ChartSettingsDialog(QDialog):
    """图表设置对话框 — 打开即显示，不做绘制；应用后仅 draw_idle"""

    def __init__(self, fig: Figure, parent=None):
        super().__init__(parent)
        self._fig = fig
        self.setWindowTitle("图表设置")
        self._build_ui()
        self._read_current_state()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.title_edit = QLineEdit(); layout.addLayout(self._row("标题:", self.title_edit))

        self.xmin_spin = QDoubleSpinBox(); self.xmin_spin.setRange(-1e9, 1e9); self.xmin_spin.setDecimals(2)
        self.xmax_spin = QDoubleSpinBox(); self.xmax_spin.setRange(-1e9, 1e9); self.xmax_spin.setDecimals(2)
        self.xlabel_edit = QLineEdit()
        layout.addLayout(self._row("X 范围:", self.xmin_spin, QLabel("~"), self.xmax_spin))
        layout.addLayout(self._row("X 标签:", self.xlabel_edit))

        self.ymin_spin = QDoubleSpinBox(); self.ymin_spin.setRange(-1e9, 1e9); self.ymin_spin.setDecimals(2)
        self.ymax_spin = QDoubleSpinBox(); self.ymax_spin.setRange(-1e9, 1e9); self.ymax_spin.setDecimals(2)
        self.ylabel_edit = QLineEdit()
        layout.addLayout(self._row("Y 范围:", self.ymin_spin, QLabel("~"), self.ymax_spin))
        layout.addLayout(self._row("Y 标签:", self.ylabel_edit))

        self.legend_cb = QCheckBox("显示图例"); self.legend_cb.setChecked(True)
        self.legend_pos = QComboBox()
        self.legend_pos.addItems(["best","upper right","upper left","lower left","lower right",
                                   "right","center left","center right",
                                   "lower center","upper center","center"])
        layout.addWidget(self.legend_cb)
        layout.addLayout(self._row("图例位置:", self.legend_pos))

        self.linewidth_spin = QSpinBox(); self.linewidth_spin.setRange(1, 10); self.linewidth_spin.setValue(2)
        self.grid_cb = QCheckBox("显示网格"); self.grid_cb.setChecked(True)
        self.fontsize_spin = QSpinBox(); self.fontsize_spin.setRange(6, 24); self.fontsize_spin.setValue(10)
        row = QHBoxLayout()
        row.addWidget(QLabel("线宽:")); row.addWidget(self.linewidth_spin)
        row.addWidget(self.grid_cb)
        row.addWidget(QLabel("字号:")); row.addWidget(self.fontsize_spin)
        row.addStretch(); layout.addLayout(row)

        btn_row = QHBoxLayout()
        btn_row.addWidget(create_button("应用", self._apply, "primary"))
        btn_row.addWidget(create_button("确定", self.accept, "primary"))
        btn_row.addWidget(create_button("取消", self.reject, "secondary"))
        layout.addLayout(btn_row)

    def _row(self, label, *widgets):
        row = QHBoxLayout(); row.addWidget(QLabel(label))
        for w in widgets: row.addWidget(w)
        row.addStretch(); return row

    def _read_current_state(self):
        ax = self._fig.axes[0] if self._fig.axes else None
        if ax is None: return
        self.title_edit.setText(ax.get_title())
        xl = ax.get_xlim(); self.xmin_spin.setValue(xl[0]); self.xmax_spin.setValue(xl[1])
        yl = ax.get_ylim(); self.ymin_spin.setValue(yl[0]); self.ymax_spin.setValue(yl[1])
        self.xlabel_edit.setText(ax.get_xlabel()); self.ylabel_edit.setText(ax.get_ylabel())
        leg = ax.get_legend()
        self.legend_cb.setChecked(leg is not None and leg.get_visible())
        if ax.lines: self.linewidth_spin.setValue(int(ax.lines[0].get_linewidth()))
        self.grid_cb.setChecked(ax.xaxis.get_gridlines()[0].get_visible() if ax.xaxis.get_gridlines() else True)
        self.fontsize_spin.setValue(int(ax.title.get_fontsize() if ax.title else 10))

    def _apply(self):
        for ax in self._fig.axes:
            t = self.title_edit.text().strip()
            if t: ax.set_title(t)
            ax.set_xlim(self.xmin_spin.value(), self.xmax_spin.value())
            ax.set_ylim(self.ymin_spin.value(), self.ymax_spin.value())
            xl = self.xlabel_edit.text().strip()
            if xl: ax.set_xlabel(xl)
            yl = self.ylabel_edit.text().strip()
            if yl: ax.set_ylabel(yl)
            leg = ax.get_legend()
            if leg: leg.set_visible(self.legend_cb.isChecked())
            elif self.legend_cb.isChecked(): ax.legend()
            for line in ax.lines: line.set_linewidth(self.linewidth_spin.value())
            ax.grid(self.grid_cb.isChecked(), alpha=0.3)
            fs = self.fontsize_spin.value()
            for item in ([ax.title, ax.xaxis.label, ax.yaxis.label] +
                         list(ax.get_xticklabels()) + list(ax.get_yticklabels())):
                if item: item.set_fontsize(fs)


# ═══════════════════════════════════════════════════════════════════════
# ChartPanel — 深拷贝 Figure 全屏 + 轻量增量刷新
# ═══════════════════════════════════════════════════════════════════════

class ChartPanel(QWidget):
    """可复用 matplotlib 图表面板"""

    fullscreen_closed = pyqtSignal()
    draw_count = 0  # 类变量: 累计 draw 次数 (性能分析用)

    def __init__(self, figsize=(8,4), dpi=100, show_fs_btn=True, parent=None):
        super().__init__(parent)
        self._figsize = figsize; self._dpi = dpi
        self._fig = Figure(figsize=figsize, dpi=dpi)
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._show_fs_btn = show_fs_btn
        self._fullscreen_dialog = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)

        from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
        self._mpl_toolbar = NavigationToolbar2QT(self._canvas, self)
        for action in self._mpl_toolbar.actions():
            if action.text() in ("Subplots","Customize","Save"):
                self._mpl_toolbar.removeAction(action)
        toolbar = QHBoxLayout(); toolbar.setSpacing(4)
        toolbar.addWidget(self._mpl_toolbar)
        toolbar.addSpacing(8)

        self.settings_btn = create_button("⚙ 图表设置", self._open_settings, "secondary")
        toolbar.addWidget(self.settings_btn)
        if self._show_fs_btn:
            self.fs_btn = create_button("⛶ 全屏", self._enter_fullscreen, "secondary")
            toolbar.addWidget(self.fs_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self._canvas.setMinimumHeight(250)
        layout.addWidget(self._canvas)

    # ── 公开 API ──

    def get_figure(self) -> Figure: return self._fig

    def add_subplot(self, *args, **kwargs): return self._fig.add_subplot(*args, **kwargs)

    def clear(self): self._fig.clear()

    def draw(self):
        """刷新画布 (仅 draw，不调 tight_layout 避免 8 子图阻塞)"""
        self._canvas.draw()
        ChartPanel.draw_count += 1

    def draw_with_layout(self):
        """首次/字号/布局变化后调用：tight_layout + draw"""
        self._fig.tight_layout()
        self._canvas.draw()
        ChartPanel.draw_count += 1

    # ── 工具栏 ──

    def _open_settings(self):
        dlg = ChartSettingsDialog(self._fig, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._canvas.draw_idle()  # 非阻塞

    def _enter_fullscreen(self):
        """全屏 — 深拷贝 Figure 到新窗口 (零 reparent，零 Figure 共享)"""
        if self._fullscreen_dialog is not None:
            return

        dlg = QDialog(self.window(), Qt.WindowType.FramelessWindowHint)
        dlg.setStyleSheet("background-color: #1a1a1a;")
        self._fullscreen_dialog = dlg
        layout = QVBoxLayout(dlg); layout.setContentsMargins(0, 0, 0, 0)

        # ★ 深拷贝 Figure: 避免 reparent 死锁 + 禁止 Figure 多 Canvas 共享
        import io, pickle
        try:
            buf = io.BytesIO()
            pickle.dump(self._fig, buf)
            buf.seek(0)
            new_fig = pickle.load(buf)
        except Exception:
            new_fig = None  # fallback to PNG bitmap below

        if new_fig is not None:
            new_canvas = FigureCanvasQTAgg(new_fig)
            layout.addWidget(new_canvas)
        else:
            # 极低概率 fallback: 渲染为高分辨率 PNG 位图
            buf = io.BytesIO()
            self._fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
            buf.seek(0)
            from PyQt6.QtGui import QPixmap
            pixmap = QPixmap()
            pixmap.loadFromData(buf.read())
            label = QLabel()
            label.setPixmap(pixmap)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)

        hint = QLabel("按 ESC 退出全屏")
        hint.setFixedHeight(28); hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #888; font-size: 13px; background: #222;")
        layout.addWidget(hint)

        esc = _EscFilter(dlg, dlg); dlg.installEventFilter(esc)

        def _on_closed():
            self._fullscreen_dialog = None
            self.fullscreen_closed.emit()
            self._canvas.draw_idle()  # 强制重绘，清除全屏窗口覆盖区域的渲染残影

        dlg.finished.connect(_on_closed)
        dlg.showFullScreen()

    def _save_png(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存图表", "chart.png",
                                               "PNG图片 (*.png);;所有文件 (*.*)")
        if path:
            self._fig.savefig(path, dpi=150, bbox_inches="tight")
