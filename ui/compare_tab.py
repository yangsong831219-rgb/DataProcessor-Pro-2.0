"""多源数据对比分析标签页 — 全局数据池 + 绝对时间插值对齐."""

from __future__ import annotations

import os
import re
import json
from typing import Optional

import pandas as pd
import numpy as np

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFileDialog, QMessageBox, QSplitter, QGroupBox, QFormLayout,
    QComboBox, QSpinBox, QListWidget, QListWidgetItem,
    QAbstractItemView, QTableWidget, QTableWidgetItem,
    QCheckBox, QScrollArea, QDialog, QHeaderView,
    QInputDialog, QMenu, QSizePolicy, QTextEdit,
)
from PyQt6.QtCore import Qt, QObject, QSettings

import matplotlib
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.backends.backend_qt import NavigationToolbar2QT

# ── Regex: matches '内容' "内容" ‘内容’ “内容” ──
_QUOTED_RE = re.compile(r"^[\'\"‘’“”](.*?)[\'\"‘’“”]$")

# ── Standard physical unit suffix mapping ──
_UNIT_MAP: dict[str, str] = {
    '应变': ' (με)',
    '温度': ' (℃)',
    '位移': ' (mm)',
    '挠度': ' (mm)',
    '拉力': ' (kN)',
    '应力': ' (MPa)',
    '压力': ' (MPa)',
    '频率': ' (Hz)',
}


# ══════════════════════════════════════════════════
#  FileConfig — per-file data source configuration
# ══════════════════════════════════════════════════

class FileConfig:
    """单个文件的数据源配置."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.raw_df: pd.DataFrame | None = None
        self.annotated_df: pd.DataFrame | None = None
        self.annotation_row_idx: int = 0
        self.time_col: str = ""
        self.data_cols: list[str] = []
        self.row_start: int = 0
        self.row_end: int = 0          # 0 = last data row
        self.time_offset: int = 0      # seconds compensation
        self._annotated_name_map: dict[str, str] = {}   # column → annotation content

    # ── helpers ──

    @property
    def display_name(self) -> str:
        return os.path.basename(self.filepath)

    def annotated_name(self, col: str) -> str:
        """Return the annotation text content for a data column, or column name as fallback."""
        return self._annotated_name_map.get(col, col)

    @property
    def data_row_count(self) -> int:
        """Number of data rows (rows after the annotation row)."""
        if self.annotated_df is None:
            return 0
        data_start = self.annotation_row_idx + 1
        return max(0, len(self.annotated_df) - data_start)

    def get_plot_data(self) -> pd.DataFrame:
        """Return cropped data (rows after annotation row, excluding annotation)."""
        if self.annotated_df is None:
            return pd.DataFrame()
        data = self.annotated_df.iloc[self.annotation_row_idx + 1:].copy()
        data.reset_index(drop=True, inplace=True)
        end = self.row_end if self.row_end > 0 else len(data)
        data = data.iloc[self.row_start:end]
        data.reset_index(drop=True, inplace=True)
        return data


# ══════════════════════════════════════════════════
#  CompareTabWidget
# ══════════════════════════════════════════════════

_PREVIEW_ROWS = 10
_ANN_CONTEXT_ROWS = 3

# ── Config persistence paths ──
_ANNOTATION_CACHE_PATH = os.path.join('config', 'annotation_cache.json')
_PROFILES_PATH = os.path.join('config', 'comparison_profiles.json')


class CompareTabWidget(QWidget):
    """多源数据对比分析标签页."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._files: list[FileConfig] = []
        self._data_col_checkboxes: list[QCheckBox] = []
        self._data_col_sources: list[tuple[FileConfig, str]] = []    # (fc, col) parallel to checkboxes
        self._shared_time_source: tuple[FileConfig, str] | None = None   # (fc, col_name)
        self._selected_data_items: set[tuple[FileConfig, str]] = set()   # {(fc, col_name), ...}
        self._fullscreen_dialog: QDialog | None = None
        self._last_comparison: dict | None = None  # 供 provider 读取的暂存结果
        self._build_ui()

    # ──────────────────────────────────────
    #  UI construction
    # ──────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)

        desc = QLabel(
            "加载多个数据文件，填写备注行标记时间列和数据列。\n"
            "公共时间列和数据列从所有文件中统一选择，系统自动执行绝对时间插值对齐。"
        )
        desc.setStyleSheet("color: #666; font-size: 12px; padding: 4px 0;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ======== LEFT (40%) ========
        left = QWidget()
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)

        # ── ① 数据源管理 ──
        src_group = QGroupBox("数据源管理")
        src_gl = QVBoxLayout()
        btn_row = QHBoxLayout()
        self.add_file_btn = QPushButton("+ 添加文件")
        self._add_file_menu = QMenu(self)
        self.add_file_btn.setMenu(self._add_file_menu)
        self.remove_file_btn = QPushButton("- 移除选中")
        self.remove_file_btn.setEnabled(False)
        btn_row.addWidget(self.add_file_btn)
        btn_row.addWidget(self.remove_file_btn)
        btn_row.addStretch()
        src_gl.addLayout(btn_row)
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        src_gl.addWidget(self.file_list)
        src_group.setLayout(src_gl)
        left_layout.addWidget(src_group)

        # ── ② 数据标注（选中文件） ──
        ann_group = QGroupBox("数据标注（选中文件）")
        ann_gl = QVBoxLayout()
        ann_gl.setContentsMargins(4, 4, 4, 4)
        self.annotation_table = QTableWidget()
        self.annotation_table.setMaximumHeight(200)
        self.annotation_table.setAlternatingRowColors(True)
        self.annotation_table.verticalHeader().setVisible(False)
        self.annotation_table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
        )
        ann_gl.addWidget(self.annotation_table)
        ann_hint = QLabel("黄色行为备注行（可编辑）— 填写 '时间戳' 标记时间列，'数据列名' 标记数据列")
        ann_hint.setStyleSheet("color: #999; font-size: 11px;")
        ann_hint.setWordWrap(True)
        ann_gl.addWidget(ann_hint)
        self.save_ann_btn = QPushButton("[保存] 当前文件标注")
        self.save_ann_btn.setEnabled(False)
        self.save_ann_btn.setStyleSheet("font-size: 11px; padding: 3px 8px;")
        ann_gl.addWidget(self.save_ann_btn)
        ann_group.setLayout(ann_gl)
        left_layout.addWidget(ann_group)

        # ── ③ 数据时程选择 ──
        range_group = QGroupBox("数据时程选择")
        rg_gl = QFormLayout()
        self.row_start_spin = QSpinBox()
        self.row_start_spin.setRange(0, 9_999_999)
        self.row_start_spin.setSuffix(" 行")
        self.row_end_spin = QSpinBox()
        self.row_end_spin.setRange(0, 9_999_999)
        self.row_end_spin.setSuffix(" 行（0=末尾）")
        self.row_end_spin.setSpecialValueText("末尾")
        self.time_offset_spin = QSpinBox()
        self.time_offset_spin.setRange(-86_400, 86_400)
        self.time_offset_spin.setSuffix(" 秒")
        self.time_offset_spin.setToolTip("对该文件时间列的整体偏移量（秒），用于对齐不同起始时间的文件")
        rg_gl.addRow("起始行:", self.row_start_spin)
        rg_gl.addRow("截止行:",  self.row_end_spin)
        rg_gl.addRow("时间补偿:", self.time_offset_spin)
        range_group.setLayout(rg_gl)
        left_layout.addWidget(range_group)

        # ── ④ 图表数据选择（全局） ──
        sel_group = QGroupBox("图表数据选择（全局）")
        sel_gl = QVBoxLayout()
        sel_gl.setContentsMargins(4, 4, 4, 4)

        sel_gl.addWidget(QLabel("公共时间列（单选）:"))
        self.time_col_combo = QComboBox()
        sel_gl.addWidget(self.time_col_combo)

        sel_gl.addWidget(QLabel("数据列（多选）:"))
        self.data_col_scroll = QScrollArea()
        self.data_col_scroll.setWidgetResizable(True)
        self.data_col_scroll.setMaximumHeight(120)
        self._data_col_container = QWidget()
        self._data_col_container_layout = QVBoxLayout()
        self._data_col_container_layout.setContentsMargins(0, 0, 0, 0)
        self._data_col_container_layout.setSpacing(2)
        self._data_col_container.setLayout(self._data_col_container_layout)
        self.data_col_scroll.setWidget(self._data_col_container)
        sel_gl.addWidget(self.data_col_scroll)

        sel_hint = QLabel("格式: [文件名] - 备注名（勾选的数据列将被插值对齐到公共时间列）")
        sel_hint.setStyleSheet("color: #999; font-size: 11px;")
        sel_hint.setWordWrap(True)
        sel_gl.addWidget(sel_hint)

        sel_group.setLayout(sel_gl)
        left_layout.addWidget(sel_group)

        # ── 执行 ──
        self.execute_btn = QPushButton("▶ 执行对比")
        self.execute_btn.setEnabled(False)
        self.execute_btn.setStyleSheet("""
            QPushButton {
                background-color: #4472C4; color: white; font-size: 14px;
                padding: 8px; border-radius: 4px; font-weight: bold;
            }
            QPushButton:hover { background-color: #3561A8; }
            QPushButton:disabled { background-color: #ccc; color: #888; }
        """)
        left_layout.addWidget(self.execute_btn)

        # ── 对比历史方案 ──
        self.profile_btn = QPushButton("[方案] 对比历史方案")
        self._profile_menu = QMenu(self)
        self.profile_btn.setMenu(self._profile_menu)
        self._profile_menu.addAction("保存对比方案", self._save_profile)
        self._profile_menu.addAction("读取对比方案", self._load_profile)
        self._profile_menu.addAction("删除对比方案", self._delete_profile)
        left_layout.addWidget(self.profile_btn)

        left_layout.addStretch()
        left.setLayout(left_layout)

        # ======== RIGHT (60%) ========
        right = QWidget()
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.fig = Figure(figsize=(8, 5), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.toolbar = NavigationToolbar2QT(self.canvas, right)

        toolbar_row = QHBoxLayout()
        toolbar_row.addWidget(self.toolbar)
        toolbar_row.addStretch()
        self.fullscreen_btn = QPushButton("⛶ 全屏")
        self.fullscreen_btn.setFixedWidth(60)
        toolbar_row.addWidget(self.fullscreen_btn)

        # Wrap toolbar + canvas in a container so we can move them to fullscreen dialog
        self._chart_container = QWidget()
        chart_layout = QVBoxLayout(self._chart_container)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.addLayout(toolbar_row)
        chart_layout.addWidget(self.canvas)
        right_layout.addWidget(self._chart_container)

        # ── 多源对比指标展示 ──
        self._comparison_result = QTextEdit()
        self._comparison_result.setReadOnly(True)
        self._comparison_result.setMaximumHeight(100)
        self._comparison_result.setMinimumHeight(60)
        self._comparison_result.setPlaceholderText("多源对比指标将在执行后显示...")
        self._comparison_result.setVisible(False)
        right_layout.addWidget(self._comparison_result)

        right.setLayout(right_layout)
        self._right_panel = right  # saved for fullscreen restore

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 40)
        splitter.setStretchFactor(1, 60)
        splitter.setSizes([400, 600])

        layout.addWidget(splitter)
        self.setLayout(layout)

        # Populate recent-dirs menu
        self._rebuild_recent_dirs_menu()

        # ── signals ──
        self.remove_file_btn.clicked.connect(self._on_remove_file)
        self.file_list.currentRowChanged.connect(self._on_file_selected)
        self.annotation_table.itemChanged.connect(self._on_annotation_cell_changed)
        self.row_start_spin.valueChanged.connect(self._on_range_changed)
        self.row_end_spin.valueChanged.connect(self._on_range_changed)
        self.time_offset_spin.valueChanged.connect(self._on_time_offset_changed)
        self.time_col_combo.currentIndexChanged.connect(self._on_time_col_changed)
        self.execute_btn.clicked.connect(self._align_and_plot)
        self.fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        self.save_ann_btn.clicked.connect(self._save_current_annotation)

    # ──────────────────────────────────────
    #  File management
    # ──────────────────────────────────────

    def _on_add_file(self, start_dir: str = ""):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择数据文件",
            start_dir, "数据文件 (*.csv *.txt *.xlsx *.xls);;所有文件 (*.*)",
        )
        existing_paths = {fc.filepath for fc in self._files}
        for path in paths:
            if path in existing_paths:
                continue
            cfg = FileConfig(path)
            try:
                cfg.raw_df = self._load_file(path)
            except Exception as e:
                QMessageBox.warning(
                    self, "加载失败",
                    f"无法加载文件:\n{path}\n\n{str(e)}",
                )
                continue
            cfg.annotated_df, cfg.annotation_row_idx = self._insert_annotation_row(cfg.raw_df)
            self._auto_annotate(cfg)
            self._parse_annotations(cfg)
            self._restore_annotation_cache(cfg)
            self._files.append(cfg)
            self.file_list.addItem(cfg.display_name)

        # Save recent dir from the first successfully loaded file
        if paths:
            self._save_recent_dir(paths[0])

        # Auto-select first file when list was empty
        if self.file_list.currentRow() < 0 and self._files:
            self.file_list.setCurrentRow(len(self._files) - 1)
        self._refresh_global_selection()
        self._update_execute_button()

    def _on_remove_file(self):
        row = self.file_list.currentRow()
        if row < 0 or row >= len(self._files):
            return

        removed_cfg = self._files[row]

        # Purge stale selections by FileConfig identity
        if self._shared_time_source is not None and self._shared_time_source[0] is removed_cfg:
            self._shared_time_source = None
        self._selected_data_items = {
            (fc, cn) for (fc, cn) in self._selected_data_items
            if fc is not removed_cfg
        }

        del self._files[row]
        self.file_list.takeItem(row)
        self._update_execute_button()
        if not self._files:
            self._clear_annotation_table()
            self._clear_config_panel()
        else:
            cur = self.file_list.currentRow()
            if 0 <= cur < len(self._files):
                self._on_file_selected(cur)
        self._refresh_global_selection()

    def _update_execute_button(self):
        has_time = self._shared_time_source is not None
        has_data = len(self._selected_data_items) > 0
        self.execute_btn.setEnabled(has_time and has_data)

    # ──────────────────────────────────────
    #  Recent directories menu (QSettings)
    # ──────────────────────────────────────

    def _rebuild_recent_dirs_menu(self):
        """Rebuild the add-file dropdown from QSettings-stored recent dirs."""
        self._add_file_menu.clear()
        settings = QSettings('DataProcessor', 'CompareTab')
        recent = settings.value('CompareTab_RecentDirs')

        if recent is None:
            recent_strs: list[str] = []
        elif isinstance(recent, str):
            recent_strs = [recent]
        elif isinstance(recent, list):
            recent_strs = [str(d) for d in recent if isinstance(d, str)]
        else:
            recent_strs = []

        for d in recent_strs:
            if not os.path.isdir(d):
                continue
            short = d  # keep readable on Windows
            if len(short) > 55:
                short = '...' + short[-52:]
            action = self._add_file_menu.addAction(f"[最近] {short}")
            action.setData(d)
            action.triggered.connect(
                lambda checked, path=d: self._on_add_file(path)
            )

        if recent_strs:
            self._add_file_menu.addSeparator()

        self._add_file_menu.addAction(
            "[浏览] 其他文件...", lambda: self._on_add_file("")
        )

    def _save_recent_dir(self, file_path: str):
        """Store the parent directory of a loaded file in QSettings (max 5)."""
        dir_path = os.path.dirname(file_path)
        if not dir_path:
            return

        settings = QSettings('DataProcessor', 'CompareTab')
        recent = settings.value('CompareTab_RecentDirs')
        if recent is None:
            recent_strs: list[str] = []
        elif isinstance(recent, str):
            recent_strs = [recent]
        elif isinstance(recent, list):
            recent_strs = [str(d) for d in recent if isinstance(d, str)]
        else:
            recent_strs = []

        # Remove if already present (will re-insert at front)
        recent_strs = [d for d in recent_strs if d != dir_path]
        recent_strs.insert(0, dir_path)
        recent_strs = recent_strs[:5]
        settings.setValue('CompareTab_RecentDirs', recent_strs)
        self._rebuild_recent_dirs_menu()

    # ──────────────────────────────────────
    #  File loading
    # ──────────────────────────────────────

    @staticmethod
    def _load_file(path: str) -> pd.DataFrame:
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.xlsx', '.xls'):
            df = pd.read_excel(path)
            if df is None or df.empty:
                raise ValueError("文件为空或无法解析")
            df.columns = [str(c).strip() for c in df.columns]
            return df

        from utils.file_parser import try_read_csv
        try:
            df, _ = try_read_csv(
                path, delimiter=None, skip_rows=0,
                encoding='utf-8', header='detect',
            )
        except Exception:
            df = None

        if df is None or df.shape[1] <= 1:
            df = CompareTabWidget._parse_enlight_like(path)
            if df is None or df.empty:
                raise ValueError(
                    f"无法解析文件:\n{path}\n请确认文件格式是否为标准 CSV/TXT/XLSX。"
                )

        df.columns = [str(c).strip() for c in df.columns]
        return df

    @staticmethod
    def _parse_enlight_like(path: str) -> pd.DataFrame | None:
        """薄壳委托 — 调用统一的 ENLIGHT/Hyperion 解析器"""
        try:
            from utils.file_parser import parse_enlight_file
            df, _annotation, _meta = parse_enlight_file(path)
            return df if df is not None and not df.empty else None
        except Exception:
            # 兼容旧行为: 解析失败返回 None 不抛异常
            return None

    # ──────────────────────────────────────
    #  Annotation row management
    # ──────────────────────────────────────

    @staticmethod
    def _row_has_timestamp(row: pd.Series) -> bool:
        for col in row.index:
            val = str(row[col]).strip()
            if not val or val in ('nan', 'NaT', '', 'None'):
                continue
            if re.match(r'^\d{2,4}[-/\.]\d{1,2}[-/\.]\d{1,2}', val):
                if not re.search(r'[a-zA-Z一-鿿]', val):
                    return True
        return False

    @staticmethod
    def _find_first_timestamp_row(df: pd.DataFrame) -> int:
        limit = min(len(df), 500)
        for idx in range(limit - 2):
            if all(
                CompareTabWidget._row_has_timestamp(df.iloc[r])
                for r in (idx, idx + 1, idx + 2)
            ):
                return idx
        return 0

    @staticmethod
    def _insert_annotation_row(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
        # 查重：扫描前 5 行是否已存在暗号标记，避免重复插入
        for i in range(min(5, len(df))):
            row_vals = [str(df.iloc[i, j]) for j in range(len(df.columns))]
            if any('时间戳' in v for v in row_vals):
                return df, i

        ann_idx = CompareTabWidget._find_first_timestamp_row(df)
        # 全局强转为 object，彻底杜绝浮点/字符串类型冲突
        df = df.astype(object)
        blank = pd.DataFrame(
            [[''] * len(df.columns)], columns=df.columns, dtype=object,
        )
        before = df.iloc[:ann_idx].copy() if ann_idx > 0 else pd.DataFrame(columns=df.columns)
        after = df.iloc[ann_idx:].copy()
        return pd.concat([before, blank, after], ignore_index=True), ann_idx

    @staticmethod
    def _auto_annotate(cfg: FileConfig) -> None:
        """Auto-fill time-column annotation if column name suggests it."""
        if cfg.annotated_df is None:
            return
        df = cfg.annotated_df
        ann_idx = cfg.annotation_row_idx
        for col in df.columns:
            col_lower = str(col).lower()
            if any(kw in col_lower for kw in ['时间', 'time', 'timestamp']):
                df.iloc[ann_idx, df.columns.get_loc(col)] = "'时间戳'"
                break

    @staticmethod
    def _parse_annotations(cfg: FileConfig) -> None:
        """Parse the annotation row to identify time and data columns.

        Also populates cfg._annotated_name_map with annotation content.
        """
        if cfg.annotated_df is None or cfg.annotated_df.empty:
            cfg.time_col = ""
            cfg.data_cols = []
            cfg._annotated_name_map = {}
            return
        ann_idx = cfg.annotation_row_idx
        row_ann = cfg.annotated_df.iloc[ann_idx]
        time_col = ""
        data_cols: list[str] = []
        name_map: dict[str, str] = {}
        for col in cfg.annotated_df.columns:
            val = str(row_ann.get(col, '')).strip()
            cleaned = val.strip("'\"‘’“”").strip()
            if '时间戳' in cleaned:
                time_col = str(col)
            elif _QUOTED_RE.match(val):
                m = _QUOTED_RE.match(val)
                custom_name = m.group(1).strip()
                data_cols.append(str(col))
                name_map[str(col)] = custom_name
        cfg.time_col = time_col
        cfg.data_cols = data_cols
        cfg._annotated_name_map = name_map

    # ──────────────────────────────────────
    #  File selection → Update UI
    # ──────────────────────────────────────

    def _on_file_selected(self, row: int):
        self.remove_file_btn.setEnabled(row >= 0 and row < len(self._files))
        self.save_ann_btn.setEnabled(row >= 0 and row < len(self._files))
        if row < 0 or row >= len(self._files):
            self._clear_annotation_table()
            self._clear_config_panel()
            return
        cfg = self._files[row]
        self._populate_annotation_table(cfg)
        self._populate_range_spins(cfg)

    def _clear_annotation_table(self):
        self.annotation_table.setRowCount(0)
        self.annotation_table.setColumnCount(0)

    def _clear_config_panel(self):
        self.row_start_spin.setValue(0)
        self.row_end_spin.setValue(0)
        self.time_offset_spin.setValue(0)
        self.annotation_table.setRowCount(0)
        self.annotation_table.setColumnCount(0)

    def _populate_annotation_table(self, cfg: FileConfig) -> None:
        self.annotation_table.blockSignals(True)

        df = cfg.annotated_df
        if df is None or df.empty:
            self.annotation_table.setRowCount(0)
            self.annotation_table.setColumnCount(0)
            self.annotation_table.blockSignals(False)
            return

        cols = [str(c) for c in df.columns]
        ann_idx = cfg.annotation_row_idx

        rows_above = min(ann_idx, _ANN_CONTEXT_ROWS)
        data_start = ann_idx + 1
        n_data = min(len(df) - data_start, _PREVIEW_ROWS)
        total_rows = rows_above + 1 + n_data

        self.annotation_table.setColumnCount(len(cols))
        self.annotation_table.setHorizontalHeaderLabels(cols)
        self.annotation_table.setRowCount(total_rows)

        # Rows above annotation (read-only)
        for i in range(rows_above):
            df_row = ann_idx - rows_above + i
            for j in range(len(cols)):
                try:
                    val = str(df.iloc[df_row, j])
                except Exception:
                    val = ""
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.annotation_table.setItem(i, j, item)

        # Annotation row (yellow, editable)
        ann_tbl_row = rows_above
        for j in range(len(cols)):
            try:
                val = str(df.iloc[ann_idx, j])
            except Exception:
                val = ""
            item = QTableWidgetItem(val)
            item.setBackground(Qt.GlobalColor.yellow)
            item.setToolTip("在此填写 '时间戳' 或 '数据列名'")
            self.annotation_table.setItem(ann_tbl_row, j, item)

        # Data rows below annotation (read-only)
        for i in range(n_data):
            df_row = data_start + i
            tbl_row = ann_tbl_row + 1 + i
            for j in range(len(cols)):
                try:
                    val = str(df.iloc[df_row, j])
                except Exception:
                    val = ""
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.annotation_table.setItem(tbl_row, j, item)

        self.annotation_table.resizeColumnsToContents()
        self.annotation_table.blockSignals(False)

    # ── Annotation cell edit → update cfg → refresh global pool ──

    def _on_annotation_cell_changed(self, item: QTableWidgetItem):
        row = self.file_list.currentRow()
        if row < 0 or row >= len(self._files):
            return
        cfg = self._files[row]
        if cfg.annotated_df is None:
            return

        ann_idx = cfg.annotation_row_idx
        rows_above = min(ann_idx, _ANN_CONTEXT_ROWS)
        ann_tbl_row = rows_above

        r = item.row()
        c = item.column()
        if r != ann_tbl_row:
            return
        if not (0 <= c < len(cfg.annotated_df.columns)):
            return

        cfg.annotated_df.iloc[ann_idx, c] = item.text()
        self._parse_annotations(cfg)

        # Remove stale selections from this file (annotation changed → column set may differ)
        self._selected_data_items = {
            (fc, cn) for (fc, cn) in self._selected_data_items
            if fc is not cfg
        }
        self._refresh_global_selection()
        self._update_execute_button()

    # ──────────────────────────────────────
    #  Range spinboxes + time offset
    # ──────────────────────────────────────

    def _populate_range_spins(self, cfg: FileConfig) -> None:
        self.row_start_spin.blockSignals(True)
        self.row_end_spin.blockSignals(True)
        self.time_offset_spin.blockSignals(True)
        n = cfg.data_row_count
        self.row_start_spin.setRange(0, max(0, n - 1))
        self.row_start_spin.setValue(min(cfg.row_start, max(0, n - 1)))
        self.row_end_spin.setRange(0, n)
        self.row_end_spin.setValue(cfg.row_end if cfg.row_end > 0 else 0)
        self.time_offset_spin.setValue(cfg.time_offset)
        self.row_start_spin.blockSignals(False)
        self.row_end_spin.blockSignals(False)
        self.time_offset_spin.blockSignals(False)

    def _on_range_changed(self):
        row = self.file_list.currentRow()
        if row < 0 or row >= len(self._files):
            return
        cfg = self._files[row]
        cfg.row_start = self.row_start_spin.value()
        cfg.row_end = self.row_end_spin.value()

    def _on_time_offset_changed(self, value: int):
        row = self.file_list.currentRow()
        if row < 0 or row >= len(self._files):
            return
        self._files[row].time_offset = value

    # ──────────────────────────────────────
    #  Global column selection (ALL files)
    # ──────────────────────────────────────

    @staticmethod
    def _get_annotated_data_cols(cfg: FileConfig) -> list[str]:
        """Return columns with quoted annotation marks, excluding the time column."""
        if cfg.annotated_df is None:
            return []
        ann_idx = cfg.annotation_row_idx
        ann_row = cfg.annotated_df.iloc[ann_idx]
        result: list[str] = []
        for col in cfg.annotated_df.columns:
            val = str(ann_row.get(col, '')).strip()
            if not val:
                continue
            cleaned = val.strip("'\"'\"'\"")
            if '时间戳' in cleaned:
                continue
            if _QUOTED_RE.match(val):
                result.append(str(col))
        return result

    @staticmethod
    def _get_annotated_time_cols(cfg: FileConfig) -> list[str]:
        """Return columns with '时间戳' annotation from a single file."""
        if cfg.annotated_df is None:
            return []
        ann_idx = cfg.annotation_row_idx
        ann_row = cfg.annotated_df.iloc[ann_idx]
        result: list[str] = []
        for col in cfg.annotated_df.columns:
            val = str(ann_row.get(col, '')).strip()
            cleaned = val.strip("'\"'\"'\"")
            if '时间戳' in cleaned:
                result.append(str(col))
        return result

    def _get_all_time_source_items(self) -> list[tuple[FileConfig, str, str]]:
        """Return [(fc, col_name, display_text)] for all files' time-annotated columns."""
        items: list[tuple[FileConfig, str, str]] = []
        for fc in self._files:
            for col in self._get_annotated_time_cols(fc):
                display = f"[{fc.display_name}] - {col}"
                items.append((fc, col, display))
        return items

    def _get_all_data_source_items(self) -> list[tuple[FileConfig, str, str, str]]:
        """Return [(fc, col_name, display_text, annotated_name)] for all files' data columns."""
        items: list[tuple[FileConfig, str, str, str]] = []
        for fc in self._files:
            for col in self._get_annotated_data_cols(fc):
                ann_name = fc.annotated_name(col)
                display = f"[{fc.display_name}] - {ann_name}"
                items.append((fc, col, display, ann_name))
        return items

    def _refresh_global_selection(self) -> None:
        """Rebuild time-col combo and data-col checkboxes from ALL files."""
        # ── Time column combo ──
        time_items = self._get_all_time_source_items()
        self.time_col_combo.blockSignals(True)
        self.time_col_combo.clear()
        self.time_col_combo.addItem("")   # index 0 = empty placeholder
        for fc, col, display in time_items:
            self.time_col_combo.addItem(display, userData=(fc, col))

        # Restore previous selection if still valid
        if self._shared_time_source is not None:
            matched = False
            for i in range(1, self.time_col_combo.count()):
                item_data = self.time_col_combo.itemData(i)
                if (isinstance(item_data, tuple) and len(item_data) == 2
                        and item_data[0] is self._shared_time_source[0]
                        and item_data[1] == self._shared_time_source[1]):
                    self.time_col_combo.setCurrentIndex(i)
                    matched = True
                    break
            if not matched:
                self._shared_time_source = None
                self.time_col_combo.setCurrentIndex(0)
        else:
            self.time_col_combo.setCurrentIndex(0)
        self.time_col_combo.blockSignals(False)

        # ── Data column checkboxes ──
        data_items = self._get_all_data_source_items()
        self._rebuild_data_col_checkboxes(data_items)

    def _rebuild_data_col_checkboxes(
        self, items: list[tuple[FileConfig, str, str, str]],
    ) -> None:
        """Clear and rebuild the global data-column checkbox list.

        items: [(fc, col_name, display_text, annotated_name), ...]
        """
        # Clear existing
        for cb in self._data_col_checkboxes:
            self._data_col_container_layout.removeWidget(cb)
            cb.deleteLater()
        self._data_col_checkboxes.clear()
        self._data_col_sources.clear()

        for fc, col_name, display_text, _ann in items:
            cb = QCheckBox(display_text)
            # Bind FileConfig reference as hidden property for reliable reverse lookup
            cb.setProperty('_fc_data', (fc, col_name))
            # Restore previously checked state
            cb.blockSignals(True)
            cb.setChecked((fc, col_name) in self._selected_data_items)
            cb.blockSignals(False)
            cb.toggled.connect(self._on_data_col_toggled)
            self._data_col_container_layout.addWidget(cb)
            self._data_col_checkboxes.append(cb)
            self._data_col_sources.append((fc, col_name))
        self._data_col_container_layout.addStretch()

    def _on_data_col_toggled(self, _checked: bool):
        """Sync checkbox states to _selected_data_items (by FileConfig identity)."""
        self._selected_data_items.clear()
        for cb in self._data_col_checkboxes:
            if cb.isChecked():
                fc_data = cb.property('_fc_data')
                if fc_data is not None and isinstance(fc_data, tuple) and len(fc_data) == 2:
                    self._selected_data_items.add((fc_data[0], fc_data[1]))
        self._update_execute_button()

    def _on_time_col_changed(self, _idx: int):
        idx = self.time_col_combo.currentIndex()
        if idx > 0:   # index 0 is the empty placeholder
            data = self.time_col_combo.itemData(idx)
            if data is not None and isinstance(data, tuple) and len(data) == 2:
                self._shared_time_source = (data[0], data[1])
            else:
                self._shared_time_source = None
        else:
            self._shared_time_source = None
        self._update_execute_button()

    # ──────────────────────────────────────
    #  Fullscreen (frameless, ESC to exit)
    # ──────────────────────────────────────

    def _toggle_fullscreen(self):
        if self._fullscreen_dialog is not None and self._fullscreen_dialog.isVisible():
            self._fullscreen_dialog.close()
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("多源对比图表 — 全屏")
        dialog.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        )
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)

        # Move existing toolbar + canvas into the dialog (chart gets all stretching space)
        self._chart_container.setParent(dialog)
        layout.addWidget(self._chart_container, stretch=1)

        # Close hint — fixed minimal height, no stretching
        hint = QLabel("按 ESC 键退出全屏")
        hint.setFixedHeight(30)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #aaa; font-size: 14px; padding: 4px; background: #222;")
        layout.addWidget(hint, stretch=0)

        self._fullscreen_dialog = dialog
        dialog.showFullScreen()

        # ── ESC key handling via event filter ──
        class _EscFilter(QObject):
            def __init__(self, dlg):
                super().__init__(dlg)
                self._dlg = dlg
            def eventFilter(self, obj, event):
                if event.type() == event.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                    self._dlg.accept()
                    return True
                return False

        self._fs_esc_filter = _EscFilter(dialog)
        dialog.installEventFilter(self._fs_esc_filter)

        dialog.finished.connect(lambda r: self._on_fullscreen_closed(dialog, layout))

    def _on_fullscreen_closed(self, dialog: QDialog, dialog_layout: QVBoxLayout):
        """Move chart_container back to the main layout."""
        dialog_layout.removeWidget(self._chart_container)
        self._chart_container.setParent(None)
        self._right_panel.layout().addWidget(self._chart_container)
        self.canvas.draw_idle()
        self._fullscreen_dialog = None

    # ──────────────────────────────────────
    #  Core alignment & plotting
    # ──────────────────────────────────────

    def _align_and_plot(self):
        """全局多源对比 — numpy.interp 绝对时间插值对齐绘图.

        流程:
          1. 公共时间列 → 基准 Unix 时间戳网格
          2. 遍历勾选数据列，提取各文件时间列（+时间补偿）→ Unix 时间戳
          3. 全局交集截断基准网格
          4. numpy.interp 一维线性插值到基准网格
          5. 图例只显示备注名，Y轴带物理单位，标题含前缀
        """
        # ── 1. Validate ──
        if self._shared_time_source is None:
            QMessageBox.warning(self, "未选择时间列", "请先选择公共时间列。")
            return
        if not self._selected_data_items:
            QMessageBox.warning(self, "未选择数据列", "请至少勾选一个数据列。")
            return

        base_fc, base_col_name = self._shared_time_source
        base_data = base_fc.get_plot_data()
        if base_data.empty or base_col_name not in base_data.columns:
            QMessageBox.warning(self, "错误", "公共时间列数据不可用。")
            return

        # ── 2. Build master Unix-timestamp grid from the shared time source ──
        base_raw = pd.to_datetime(base_data[base_col_name].astype(str), errors='coerce', format='mixed')
        base_valid = base_raw.dropna().sort_values()
        if base_valid.empty:
            QMessageBox.warning(self, "时间解析失败", "公共时间列无法解析为有效时间。")
            return
        # Convert to Unix-seconds (float64) for numpy.interp
        base_unix = base_valid.astype('int64') / 1e9  # nanoseconds → seconds

        # ── 3. Collect (src_ts, src_val, fc, dc) for every selected data column ──
        #     Iterate checkboxes in UI order — matches user's visual order
        bucket: list[tuple[np.ndarray, np.ndarray, FileConfig, str]] = []

        for cb in self._data_col_checkboxes:
            if not cb.isChecked():
                continue
            fc_data = cb.property('_fc_data')
            if fc_data is None or not isinstance(fc_data, tuple) or len(fc_data) != 2:
                continue
            fc, dc = fc_data

            file_time_col = fc.time_col
            if not file_time_col:
                continue

            data_df = fc.get_plot_data()
            if data_df.empty:
                continue
            if file_time_col not in data_df.columns or dc not in data_df.columns:
                continue

            # Parse time with offset → Unix seconds
            ft = pd.to_datetime(data_df[file_time_col].astype(str), errors='coerce', format='mixed')
            ft += pd.Timedelta(seconds=fc.time_offset)
            mask = ft.notna()
            if not mask.any():
                continue

            combined = pd.DataFrame({
                't': ft[mask],
                'v': pd.to_numeric(data_df.loc[mask, dc], errors='coerce'),
            }).dropna(subset=['v']).sort_values('t')
            if combined.empty:
                continue

            src_ts = combined['t'].astype('int64').values / 1e9  # Unix seconds
            src_val = combined['v'].values.astype(float)
            bucket.append((src_ts, src_val, fc, dc))

        if not bucket:
            QMessageBox.warning(self, "无数据", "没有可绘制的数据。")
            return

        # ── 4. Global intersection (all source timestamps + master) ──
        all_arrs = [b[0] for b in bucket] + [base_unix.values]
        global_start = max(arr.min() for arr in all_arrs)
        global_end = min(arr.max() for arr in all_arrs)
        if global_start >= global_end:
            QMessageBox.warning(self, "无重叠时间区间", "各文件的时间范围没有重叠区间。")
            return

        # ── 5. Filter master grid to the intersection interval ──
        master_mask = (base_unix.values >= global_start) & (base_unix.values <= global_end)
        master_grid = base_unix.values[master_mask]
        if len(master_grid) < 2:
            QMessageBox.warning(self, "交集不足", "公共时间列在交集区间内数据点不足。")
            return

        # ── 6. numpy.interp each series onto master_grid ──
        plot_bucket: list[tuple[np.ndarray, np.ndarray, str]] = []  # (x_datetime_64, y_values, label)

        prefixes: set[str] = set()
        for src_ts, src_val, fc, dc in bucket:
            interp_y = np.interp(master_grid, src_ts, src_val)
            label = fc.annotated_name(dc)   # Task 3: legend = annotated name ONLY
            x_dt = pd.to_datetime(master_grid, unit='s')
            plot_bucket.append((x_dt, interp_y, label))

            # Collect prefix for Y-axis / title
            if '-' in label:
                prefix = label.split('-', 1)[0].strip()
            else:
                prefix = label
            if prefix:
                prefixes.add(prefix)

        # ── 7. Smart Y-axis label with physical unit ──
        if len(prefixes) == 1:
            chosen_prefix = prefixes.pop()
        elif prefixes:
            chosen_prefix = "/".join(sorted(prefixes))
        else:
            chosen_prefix = "数值"

        unit_suffix = _UNIT_MAP.get(chosen_prefix, '')
        ylabel = f"{chosen_prefix}{unit_suffix}"
        title = f"{chosen_prefix}时程曲线"

        # ── 8. Plot ──
        self.fig.clear()
        ax = self.fig.add_subplot(111)

        for x_dt, y_vals, label in plot_bucket:
            ax.plot(x_dt, y_vals, label=label, linewidth=1.2)

        ax.set_xlabel('时间')
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        self.fig.autofmt_xdate(rotation=45, ha='right')
        ax.xaxis.set_major_locator(
            mdates.AutoDateLocator(minticks=5, maxticks=15)
        )
        if plot_bucket:
            ax.set_xlim(plot_bucket[0][0][0], plot_bucket[0][0][-1])
        self.fig.tight_layout()
        self.canvas.draw_idle()

        # ── 9. Compute pairwise comparison metrics ──
        self._compute_and_display_comparison(plot_bucket)

    # ──────────────────────────────────────
    #  Pairwise comparison (baselining + dropna + HTML dashboard)
    # ──────────────────────────────────────

    def _compute_and_display_comparison(
        self, plot_bucket: list[tuple],
    ) -> None:
        """对所有对齐序列执行成对对比分析。

        实现三条工程纪律：
          1. 窗口首点归零法（Baselining）
          2. 联合去空对齐（dropna how='any'）
          3. HTML 横向仪表盘呈报
        """
        if len(plot_bucket) < 2:
            self._last_comparison = None
            self._comparison_result.setVisible(False)
            return

        # ── 1. 构建 DataFrame，每列为一个对齐序列 ──
        df = pd.DataFrame({label: y for _, y, label in plot_bucket})

        # ── 2. 窗口首点归零 ──
        df_zeroed = df - df.iloc[0]

        # ── 3. 联合去空对齐 ──
        df_clean = df_zeroed.dropna(how='any')
        if df_clean.empty or len(df_clean) < 2:
            self._last_comparison = None
            self._comparison_result.setHtml(
                '<div style="color: #999; padding: 8px;">'
                '有效重叠数据不足，无法计算对比指标</div>'
            )
            self._comparison_result.setVisible(True)
            return

        cols = list(df_clean.columns)
        n = len(cols)

        # ── 4. 成对计算并构建 HTML ──
        html_rows: list[str] = []
        for i in range(n):
            for j in range(i + 1, n):
                c1, c2 = cols[i], cols[j]
                s1, s2 = df_clean[c1], df_clean[c2]
                diff = s1 - s2
                max_err = float(diff.abs().max())
                mae = float(diff.abs().mean())
                rmse = float(np.sqrt((diff ** 2).mean()))
                std1, std2 = float(s1.std()), float(s2.std())
                corr = float(s1.corr(s2)) if std1 > 0 and std2 > 0 else 0.0

                html_rows.append(
                    '<tr style="border-bottom:1px solid #eee;">'
                    f'<td style="padding:2px 8px;font-weight:bold;white-space:nowrap;">'
                    f'{c1} vs {c2}</td>'
                    f'<td style="padding:2px 8px;color:#d9363e;">{max_err:.4f}</td>'
                    f'<td style="padding:2px 8px;color:#1890ff;">{mae:.4f}</td>'
                    f'<td style="padding:2px 8px;color:#1890ff;">{rmse:.4f}</td>'
                    f'<td style="padding:2px 8px;color:#52c41a;">{corr:.4f}</td>'
                    '</tr>'
                )

        html = (
            '<table width="100%" cellpadding="0" cellspacing="0"'
            ' style="font-size:12px;border-collapse:collapse;">'
            '<tr style="background:#f5f5f5;font-weight:bold;">'
            '<td style="padding:3px 8px;">数据对比</td>'
            '<td style="padding:3px 8px;color:#d9363e;">最大误差</td>'
            '<td style="padding:3px 8px;color:#1890ff;">平均绝对误差</td>'
            '<td style="padding:3px 8px;color:#1890ff;">均方根误差</td>'
            '<td style="padding:3px 8px;color:#52c41a;">相关系数</td>'
            '</tr>'
            + ''.join(html_rows)
            + '</table>'
        )

        # ── 暂存结构化结果供 CompareProvider 读取 ──
        pair_results: list[dict] = []
        for i in range(n):
            for j in range(i + 1, n):
                c1, c2 = cols[i], cols[j]
                s1, s2 = df_clean[c1], df_clean[c2]
                diff = s1 - s2
                pair_results.append({
                    'device_a': str(c1), 'device_b': str(c2),
                    'max_error': float(diff.abs().max()),
                    'mae': float(diff.abs().mean()),
                    'rmse': float(np.sqrt((diff ** 2).mean())),
                    'corr': float(s1.corr(s2)) if float(s1.std()) > 0 and float(s2.std()) > 0 else 0.0,
                })

        # 每设备特征 (复用 compute_time_series_features)
        from core.data_providers import compute_time_series_features
        device_features: dict[str, dict] = {}
        for col_name in cols:
            device_features[str(col_name)] = compute_time_series_features(df_zeroed[col_name].dropna())

        # 报告图表必须复用用户在多源对比页实际看到的对齐时程。
        # 指标表使用首点归零值，图表则保留 UI 绘制的原序列，二者含义不同。
        time_index = pd.to_datetime(plot_bucket[0][0])
        elapsed_h = (
            np.asarray((time_index - time_index[0]).total_seconds(), dtype=float)
            / 3600.0
        )
        chart_sources = {
            str(label): np.asarray(values, dtype=float).tolist()
            for _, values, label in plot_bucket
        }

        self._last_comparison = {
            'pairs': pair_results,
            'device_features': device_features,
            'baseline_method': '窗口首点归零',
            'align_method': '绝对时间插值 (numpy.interp)',
            'n_points': int(len(df_clean)),
            'time_h': elapsed_h.tolist(),
            'sources': chart_sources,
        }

        self._comparison_result.setHtml(html)
        self._comparison_result.setVisible(True)

    # ──────────────────────────────────────
    #  Annotation cache (annotation_cache.json)
    # ──────────────────────────────────────

    def _save_current_annotation(self):
        """将标注行直接写入原数据文件。"""
        row = self.file_list.currentRow()
        if row < 0 or row >= len(self._files):
            QMessageBox.warning(self, "提示", "请先选中一个文件。")
            return
        cfg = self._files[row]
        if cfg.annotated_df is None or cfg.annotated_df.empty:
            QMessageBox.warning(self, "无数据", "当前文件无数据，无法保存。")
            return

        # 直接覆盖原文件
        output_path = cfg.filepath
        _, ext = os.path.splitext(cfg.filepath)
        ext_lower = ext.lower()

        try:
            if ext_lower in ('.xlsx', '.xls'):
                cfg.annotated_df.to_excel(output_path, index=False)
            else:
                # 文本文件：根据扩展名选择分隔符
                sep = '\t' if ext_lower == '.txt' else ','
                cfg.annotated_df.to_csv(
                    output_path, index=False, sep=sep,
                    encoding='utf-8-sig',
                )

            # 同时也更新 JSON 缓存，确保下次加载时自动恢复
            self._save_annotation_cache(cfg)

            QMessageBox.information(
                self, "保存成功",
                f"标注已写入文件：\n{os.path.basename(output_path)}",
            )
        except Exception as e:
            QMessageBox.warning(
                self, "保存失败",
                f"无法保存标注文件：\n{str(e)}",
            )

    def _save_annotation_cache(self, cfg: FileConfig) -> None:
        """Write annotation row to JSON by absolute filepath."""
        if cfg.annotated_df is None:
            return
        ann_idx = cfg.annotation_row_idx
        os.makedirs(os.path.dirname(_ANNOTATION_CACHE_PATH), exist_ok=True)

        cache: dict = {}
        if os.path.exists(_ANNOTATION_CACHE_PATH):
            try:
                with open(_ANNOTATION_CACHE_PATH, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
            except (json.JSONDecodeError, OSError):
                cache = {}

        file_cache: dict[str, str] = {}
        for col_idx in range(len(cfg.annotated_df.columns)):
            val = str(cfg.annotated_df.iloc[ann_idx, col_idx])
            if val.strip():
                file_cache[str(col_idx)] = val

        cache[cfg.filepath] = file_cache
        with open(_ANNOTATION_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

    def _restore_annotation_cache(self, cfg: FileConfig) -> None:
        """Restore annotation row from cache if previously saved for this filepath."""
        if not os.path.exists(_ANNOTATION_CACHE_PATH):
            return
        try:
            with open(_ANNOTATION_CACHE_PATH, 'r', encoding='utf-8') as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        file_cache = cache.get(cfg.filepath, {})
        if not file_cache or cfg.annotated_df is None:
            return

        ann_idx = cfg.annotation_row_idx
        for col_idx_str, annotation_text in file_cache.items():
            try:
                col_idx = int(col_idx_str)
                if 0 <= col_idx < len(cfg.annotated_df.columns):
                    cfg.annotated_df.iloc[ann_idx, col_idx] = annotation_text
            except (ValueError, TypeError):
                continue

        self._parse_annotations(cfg)

    # ──────────────────────────────────────
    #  Comparison profile management (comparison_profiles.json)
    # ──────────────────────────────────────

    def _build_profile_data(self) -> dict:
        """Serialize current module state into a profile dict."""
        shared_info = None
        if self._shared_time_source is not None:
            shared_info = {
                'filepath': self._shared_time_source[0].filepath,
                'col_name': self._shared_time_source[1],
            }

        selected_items = []
        for fc, cn in self._selected_data_items:
            selected_items.append({'filepath': fc.filepath, 'col_name': cn})

        files_list = []
        for fc in self._files:
            ann_map: dict[str, str] = {}
            if fc.annotated_df is not None:
                ann_idx = fc.annotation_row_idx
                for col_idx in range(len(fc.annotated_df.columns)):
                    val = str(fc.annotated_df.iloc[ann_idx, col_idx])
                    if val.strip():
                        ann_map[str(col_idx)] = val
            files_list.append({
                'path': fc.filepath,
                'row_start': fc.row_start,
                'row_end': fc.row_end,
                'time_offset': fc.time_offset,
                'annotations': ann_map,
            })

        return {
            'shared_time_source': shared_info,
            'selected_data_items': selected_items,
            'files': files_list,
        }

    def _apply_profile_data(self, data: dict) -> None:
        """Deserialize a profile dict and restore the module state."""
        files_data: list[dict] = data.get('files', [])
        if not files_data:
            QMessageBox.warning(self, "方案无效", "方案中不包含任何文件信息。")
            return

        # Track which files loaded successfully
        loaded_cfgs: list[FileConfig] = []
        for fd in files_data:
            path = fd.get('path', '')
            if not os.path.exists(path):
                QMessageBox.warning(
                    self, "文件不存在",
                    f"文件已不存在，将被跳过:\n{path}",
                )
                continue
            try:
                cfg = FileConfig(path)
                cfg.raw_df = self._load_file(path)
                cfg.annotated_df, cfg.annotation_row_idx = self._insert_annotation_row(cfg.raw_df)
                # Restore annotations from profile
                if cfg.annotated_df is not None:
                    ann_idx = cfg.annotation_row_idx
                    for col_str, ann_text in fd.get('annotations', {}).items():
                        try:
                            ci = int(col_str)
                            if 0 <= ci < len(cfg.annotated_df.columns):
                                cfg.annotated_df.iloc[ann_idx, ci] = ann_text
                        except (ValueError, TypeError):
                            continue
                self._parse_annotations(cfg)
                # Restore crop / offset
                cfg.row_start = fd.get('row_start', 0)
                cfg.row_end = fd.get('row_end', 0)
                cfg.time_offset = fd.get('time_offset', 0)
                loaded_cfgs.append(cfg)
            except Exception as e:
                QMessageBox.warning(
                    self, "加载失败",
                    f"无法加载文件:\n{path}\n\n{str(e)}",
                )

        if not loaded_cfgs:
            QMessageBox.warning(self, "加载失败", "所有文件均加载失败，无法恢复方案。")
            return

        # Replace current state
        old_files = self._files[:]
        self._files.clear()
        self.file_list.clear()
        for cfg in loaded_cfgs:
            self._files.append(cfg)
            self.file_list.addItem(cfg.display_name)

        # Disconnect old checkboxes
        for cb in self._data_col_checkboxes:
            self._data_col_container_layout.removeWidget(cb)
            cb.deleteLater()
        self._data_col_checkboxes.clear()
        self._data_col_sources.clear()
        self._selected_data_items.clear()

        # Restore selections
        shared_info = data.get('shared_time_source')
        if shared_info:
            fp = shared_info.get('filepath', '')
            cn = shared_info.get('col_name', '')
            for fc in self._files:
                if fc.filepath == fp and fc.time_col == cn:
                    self._shared_time_source = (fc, cn)
                    break
            else:
                self._shared_time_source = None
        else:
            self._shared_time_source = None

        selected_items_data: list[dict] = data.get('selected_data_items', [])
        for sd in selected_items_data:
            fp = sd.get('filepath', '')
            cn = sd.get('col_name', '')
            for fc in self._files:
                if fc.filepath == fp and cn in fc.data_cols:
                    self._selected_data_items.add((fc, cn))
                    break

        self._refresh_global_selection()
        self._update_execute_button()

        # Clear old file configs that aren't in use anymore
        for old in old_files:
            if old not in self._files:
                del old

        # Auto-trigger plot if we have both time and data columns
        if self._shared_time_source is not None and self._selected_data_items:
            self._align_and_plot()

    def _save_profile(self) -> None:
        """Save current module state as a named profile."""
        if not self._files:
            QMessageBox.warning(self, "无数据", "请先加载至少一个文件。")
            return

        name, ok = QInputDialog.getText(
            self, "保存对比方案", "请输入方案名称：",
        )
        if not ok or not name.strip():
            return
        name = name.strip()

        os.makedirs(os.path.dirname(_PROFILES_PATH), exist_ok=True)
        profiles: dict = {}
        if os.path.exists(_PROFILES_PATH):
            try:
                with open(_PROFILES_PATH, 'r', encoding='utf-8') as f:
                    profiles = json.load(f)
            except (json.JSONDecodeError, OSError):
                profiles = {}

        profiles[name] = self._build_profile_data()
        with open(_PROFILES_PATH, 'w', encoding='utf-8') as f:
            json.dump(profiles, f, ensure_ascii=False, indent=2)

        QMessageBox.information(self, "保存成功", f"对比方案已保存：{name}")

    def _load_profile(self) -> None:
        """Load a named profile and restore module state."""
        if not os.path.exists(_PROFILES_PATH):
            QMessageBox.warning(self, "无保存方案", "尚未保存任何对比方案。")
            return

        try:
            with open(_PROFILES_PATH, 'r', encoding='utf-8') as f:
                profiles = json.load(f)
        except (json.JSONDecodeError, OSError):
            QMessageBox.warning(self, "读取失败", "方案文件损坏，无法读取。")
            return

        if not profiles:
            QMessageBox.warning(self, "无保存方案", "尚未保存任何对比方案。")
            return

        names = list(profiles.keys())
        name, ok = QInputDialog.getItem(
            self, "读取对比方案", "请选择要读取的方案：", names, 0, False,
        )
        if not ok or not name:
            return

        data = profiles.get(name)
        if not data:
            QMessageBox.warning(self, "方案无效", f'方案「{name}」数据无效。')
            return

        self._apply_profile_data(data)

    def _delete_profile(self) -> None:
        """Delete a named profile."""
        if not os.path.exists(_PROFILES_PATH):
            QMessageBox.warning(self, "无保存方案", "尚未保存任何对比方案。")
            return

        try:
            with open(_PROFILES_PATH, 'r', encoding='utf-8') as f:
                profiles = json.load(f)
        except (json.JSONDecodeError, OSError):
            QMessageBox.warning(self, "读取失败", "方案文件损坏，无法读取。")
            return

        if not profiles:
            QMessageBox.warning(self, "无保存方案", "尚未保存任何对比方案。")
            return

        names = list(profiles.keys())
        name, ok = QInputDialog.getItem(
            self, "删除对比方案", "请选择要删除的方案：", names, 0, False,
        )
        if not ok or not name:
            return

        del profiles[name]
        with open(_PROFILES_PATH, 'w', encoding='utf-8') as f:
            json.dump(profiles, f, ensure_ascii=False, indent=2)

        QMessageBox.information(self, "删除成功", f"方案已删除：{name}")
