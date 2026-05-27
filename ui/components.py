"""DataProcessor Pro UI 组件工厂

纯函数式工厂方法，消除重复的 QPushButton、QVBoxLayout 等样板代码。
每个函数返回 PyQt6 控件或 (容器, 布局) 元组，调用方自行组装。

设计原则 (Matt Pocock 风格):
- 纯函数：同样的输入 → 同样的控件，无副作用
- 单一职责：每个函数只创建一种控件
- 组合优于继承：小型工厂函数组合构建复杂 UI
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtGui import QFont


# ============ 类型别名 ============

Variant = str  # 'primary' | 'secondary' | 'danger' | 'success' | 'link'

LabeledInput = Tuple[QHBoxLayout, QLineEdit]
LabeledSpinBox = Tuple[QHBoxLayout, QDoubleSpinBox]
LabeledIntSpinBox = Tuple[QHBoxLayout, QSpinBox]
FormGroupResult = Tuple[QGroupBox, QVBoxLayout]


# ============ 样式常量 ============

_default_button_style = """
    QPushButton {
        padding: 6px 16px;
        border-radius: 4px;
        font-size: 13px;
    }
"""

_variant_styles: dict[Variant, str] = {
    'primary': """
        QPushButton {
            background-color: #1890ff;
            color: white;
            border: 1px solid #1890ff;
        }
        QPushButton:hover {
            background-color: #40a9ff;
        }
        QPushButton:pressed {
            background-color: #096dd9;
        }
    """,
    'secondary': """
        QPushButton {
            background-color: #ffffff;
            color: #333;
            border: 1px solid #d9d9d9;
        }
        QPushButton:hover {
            color: #1890ff;
            border-color: #1890ff;
        }
    """,
    'danger': """
        QPushButton {
            background-color: #ff4d4f;
            color: white;
            border: 1px solid #ff4d4f;
        }
        QPushButton:hover {
            background-color: #ff7875;
        }
    """,
    'success': """
        QPushButton {
            background-color: #52c41a;
            color: white;
            border: 1px solid #52c41a;
        }
        QPushButton:hover {
            background-color: #73d13d;
        }
    """,
    'link': """
        QPushButton {
            background: transparent;
            color: #1890ff;
            border: none;
            padding: 4px 8px;
        }
        QPushButton:hover {
            color: #40a9ff;
            text-decoration: underline;
        }
    """,
}


# ============ 按钮工厂 ============

def create_button(
    text: str,
    on_click: Optional[Callable[[], None]] = None,
    variant: Variant = 'primary',
    enabled: bool = True,
    tooltip: str = '',
) -> QPushButton:
    """创建带统一样式的按钮

    Args:
        text: 按钮文本
        on_click: 点击回调
        variant: 'primary'(蓝), 'secondary'(白框), 'danger'(红), 'success'(绿), 'link'(文字链接)
        enabled: 是否启用
        tooltip: 鼠标悬停提示
    """
    btn = QPushButton(text)
    btn.setEnabled(enabled)
    if tooltip:
        btn.setToolTip(tooltip)

    style = _default_button_style + _variant_styles.get(variant, '')
    btn.setStyleSheet(style)

    if on_click is not None:
        btn.clicked.connect(on_click)

    return btn


def create_button_row(
    buttons: List[Tuple[str, Optional[Callable[[], None]], Variant]],
) -> QHBoxLayout:
    """创建按钮栏 (统一间距的水平布局)

    Args:
        buttons: [(text, on_click, variant), ...]

    Example:
        create_button_row([
            ('保存', self.save, 'primary'),
            ('取消', self.close, 'secondary'),
        ])
    """
    layout = QHBoxLayout()
    layout.setSpacing(8)
    for text, on_click, variant in buttons:
        layout.addWidget(create_button(text, on_click, variant))
    layout.addStretch()
    return layout


# ============ 标签工厂 ============

def create_section_header(text: str, level: int = 1) -> QLabel:
    """创建章节标题标签

    Args:
        text: 标题文本
        level: 1=大标题 2=中标题 3=小标题
    """
    sizes = {1: 16, 2: 14, 3: 12}
    weights = {1: QFont.Weight.Bold, 2: QFont.Weight.DemiBold, 3: QFont.Weight.Medium}

    label = QLabel(text)
    font = label.font()
    font.setPointSize(sizes.get(level, 13))
    font.setWeight(weights.get(level, QFont.Weight.Normal))
    label.setFont(font)
    return label


def create_info_label(text: str) -> QLabel:
    """创建灰色信息提示标签"""
    label = QLabel(text)
    label.setStyleSheet("color: #888; font-size: 12px; padding: 4px 0;")
    return label


def create_label(text: str, bold: bool = False) -> QLabel:
    """创建普通表单标签"""
    label = QLabel(text)
    if bold:
        font = label.font()
        font.setWeight(QFont.Weight.Bold)
        label.setFont(font)
    return label


# ============ 输入控件工厂 ============

def create_labeled_input(
    label: str,
    default: str = '',
    placeholder: str = '',
) -> LabeledInput:
    """创建 [标签: QLineEdit] 的水平布局

    Returns:
        (QHBoxLayout, QLineEdit) — 调用方可将 layout 插入父布局
    """
    layout = QHBoxLayout()
    layout.addWidget(create_label(label))
    line_edit = QLineEdit()
    line_edit.setText(default)
    if placeholder:
        line_edit.setPlaceholderText(placeholder)
    layout.addWidget(line_edit)
    return (layout, line_edit)


def create_labeled_spinbox(
    label: str,
    minimum: float = -1e9,
    maximum: float = 1e9,
    default: float = 0.0,
    decimals: int = 4,
    step: float = 0.01,
    prefix: str = '',
    suffix: str = '',
) -> LabeledSpinBox:
    """创建 [标签: QDoubleSpinBox] 的水平布局"""
    layout = QHBoxLayout()
    layout.addWidget(create_label(label))
    spinbox = QDoubleSpinBox()
    spinbox.setRange(minimum, maximum)
    spinbox.setValue(default)
    spinbox.setDecimals(decimals)
    spinbox.setSingleStep(step)
    if prefix:
        spinbox.setPrefix(prefix)
    if suffix:
        spinbox.setSuffix(suffix)
    layout.addWidget(spinbox)
    return (layout, spinbox)


def create_labeled_int_spinbox(
    label: str,
    minimum: int = 0,
    maximum: int = 999999,
    default: int = 0,
    prefix: str = '',
    suffix: str = '',
) -> LabeledIntSpinBox:
    """创建 [标签: QSpinBox] 的水平布局"""
    layout = QHBoxLayout()
    layout.addWidget(create_label(label))
    spinbox = QSpinBox()
    spinbox.setRange(minimum, maximum)
    spinbox.setValue(default)
    if prefix:
        spinbox.setPrefix(prefix)
    if suffix:
        spinbox.setSuffix(suffix)
    layout.addWidget(spinbox)
    return (layout, spinbox)


def create_labeled_combo(
    label: str,
    items: List[str],
    current_index: int = 0,
) -> Tuple[QHBoxLayout, QComboBox]:
    """创建 [标签: QComboBox] 的水平布局"""
    layout = QHBoxLayout()
    layout.addWidget(create_label(label))
    combo = QComboBox()
    combo.addItems(items)
    if 0 <= current_index < len(items):
        combo.setCurrentIndex(current_index)
    layout.addWidget(combo)
    return (layout, combo)


# ============ 容器工厂 ============

def create_form_group(title: str) -> FormGroupResult:
    """创建 QGroupBox 表单容器

    Returns:
        (QGroupBox, QVBoxLayout) — 直接往 layout 里加控件即可

    Example:
        group, layout = create_form_group('数据源设置')
        layout.addWidget(...)
        parent_layout.addWidget(group)
    """
    group = QGroupBox(title)
    layout = QVBoxLayout()
    group.setLayout(layout)
    return (group, layout)


def create_hbox(*widgets: QWidget) -> QHBoxLayout:
    """创建水平布局并添加控件，末尾自动加 stretch"""
    layout = QHBoxLayout()
    for w in widgets:
        layout.addWidget(w)
    layout.addStretch()
    return layout


def create_vbox(*widgets: QWidget) -> QVBoxLayout:
    """创建垂直布局并添加控件"""
    layout = QVBoxLayout()
    for w in widgets:
        layout.addWidget(w)
    return layout


# ============ 装饰控件 ============

def create_separator() -> QFrame:
    """创建水平分割线"""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


def create_checkbox(
    text: str,
    checked: bool = False,
    on_toggle: Optional[Callable[[bool], None]] = None,
) -> QCheckBox:
    """创建复选框"""
    cb = QCheckBox(text)
    cb.setChecked(checked)
    if on_toggle is not None:
        cb.toggled.connect(on_toggle)
    return cb


# ============ 复合控件 ============

def create_file_picker(
    label: str,
    dialog_filter: str = '所有文件 (*.*)',
    on_file_selected: Optional[Callable[[str], None]] = None,
) -> Tuple[QHBoxLayout, QLineEdit, QPushButton]:
    """创建 [标签: QLineEdit + 浏览按钮] 文件选择器

    Returns:
        (layout, line_edit, browse_btn) — 调用方连接 browse_btn.clicked 打开 QFileDialog
    """
    layout = QHBoxLayout()
    layout.addWidget(create_label(label))
    line_edit = QLineEdit()
    line_edit.setReadOnly(True)
    line_edit.setPlaceholderText('未选择文件...')
    layout.addWidget(line_edit)

    def _browse() -> None:
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(None, '选择文件', '', dialog_filter)
        if path:
            line_edit.setText(path)
            if on_file_selected:
                on_file_selected(path)

    browse_btn = create_button('浏览...', _browse, 'secondary')
    layout.addWidget(browse_btn)
    return (layout, line_edit, browse_btn)
