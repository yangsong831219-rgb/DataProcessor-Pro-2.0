"""技能插件中心 — 视觉样式常量 (Batch UX-1).

所有颜色、间距、圆角、字号等视觉参数集中定义在此。
"""

from __future__ import annotations


class SkillCenterStyle:
    """Namespace for skill center visual constants."""

    # ── Colors ──
    BG_PAGE = "#F5F7FA"
    BG_CARD = "#FFFFFF"
    BORDER = "#DDE3EA"
    PRIMARY = "#2563EB"
    PRIMARY_HOVER = "#1D4ED8"
    DANGER = "#DC2626"
    DANGER_HOVER = "#B91C1C"
    TEXT_PRIMARY = "#1F2937"
    TEXT_SECONDARY = "#64748B"
    TEXT_MUTED = "#9CA3AF"
    SUCCESS = "#16A34A"
    WARNING = "#D97706"
    INFO = "#2563EB"

    # ── Spacing (px) ──
    SPACE_XS = 4
    SPACE_SM = 8
    SPACE_MD = 12
    SPACE_LG = 16
    SPACE_XL = 24

    # ── Page margins ──
    PAGE_MARGIN = 16

    # ── Border radius ──
    RADIUS_SM = 4
    RADIUS_MD = 6
    RADIUS_LG = 8

    # ── Button heights ──
    BTN_HEIGHT = 32
    BTN_HEIGHT_LG = 36

    # ── Table row heights ──
    TABLE_ROW_HEIGHT = 34
    TABLE_ROW_HEIGHT_LG = 38

    # ── Font sizes ──
    FONT_XS = 11
    FONT_SM = 12
    FONT_MD = 13
    FONT_LG = 15
    FONT_XL = 18

    # ── Card style sheet ──
    CARD_STYLE = f"""
        background: {BG_CARD};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_MD}px;
    """

    # ── Primary button ──
    BTN_PRIMARY_STYLE = f"""
        QPushButton {{
            background-color: {PRIMARY};
            color: white;
            border: none;
            border-radius: {RADIUS_SM}px;
            padding: 6px 16px;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background-color: {PRIMARY_HOVER};
        }}
        QPushButton:disabled {{
            background-color: #93C5FD;
            color: #E0E7FF;
        }}
    """

    # ── Danger button ──
    BTN_DANGER_STYLE = f"""
        QPushButton {{
            color: {DANGER};
            border: 1px solid {DANGER};
            border-radius: {RADIUS_SM}px;
            padding: 6px 16px;
            background: transparent;
        }}
        QPushButton:hover {{
            background-color: #FEF2F2;
        }}
    """

    # ── Default/secondary button ──
    BTN_SECONDARY_STYLE = f"""
        QPushButton {{
            color: {TEXT_PRIMARY};
            border: 1px solid {BORDER};
            border-radius: {RADIUS_SM}px;
            padding: 6px 16px;
            background: {BG_CARD};
        }}
        QPushButton:hover {{
            background-color: {BG_PAGE};
            border-color: {TEXT_MUTED};
        }}
    """

    # ── Page background ──
    PAGE_STYLE = f"""
        QWidget#skillCenterPage {{
            background-color: {BG_PAGE};
        }}
    """

    # ── Left panel ──
    LEFT_PANEL_STYLE = f"""
        QWidget#skillNavPanel {{
            background-color: {BG_CARD};
            border: 1px solid {BORDER};
            border-radius: {RADIUS_MD}px;
        }}
    """

    # ── Right panel ──
    RIGHT_PANEL_STYLE = f"""
        QWidget#skillWorkArea {{
            background-color: transparent;
        }}
    """

    # ── Tab widget ──
    TAB_STYLE = f"""
        QTabWidget::pane {{
            border: 1px solid {BORDER};
            border-radius: {RADIUS_SM}px;
            background: {BG_CARD};
        }}
        QTabBar::tab {{
            padding: 8px 20px;
            margin-right: 2px;
            border: 1px solid transparent;
            border-bottom: none;
            color: {TEXT_SECONDARY};
        }}
        QTabBar::tab:selected {{
            color: {PRIMARY};
            border-bottom: 2px solid {PRIMARY};
            font-weight: 500;
        }}
        QTabBar::tab:hover {{
            color: {PRIMARY};
        }}
    """

    # ── Badge styles ──
    BADGE_ENABLED = f"""
        QLabel {{
            background: #D1FAE5;
            color: #065F46;
            border-radius: 10px;
            padding: 2px 10px;
            font-size: {FONT_XS}px;
            font-weight: 500;
        }}
    """

    BADGE_DISABLED = f"""
        QLabel {{
            background: #FEE2E2;
            color: #991B1B;
            border-radius: 10px;
            padding: 2px 10px;
            font-size: {FONT_XS}px;
            font-weight: 500;
        }}
    """

    # ── Empty state ──
    EMPTY_STATE_STYLE = f"""
        QLabel {{
            color: {TEXT_MUTED};
            font-size: {FONT_MD}px;
            padding: 48px;
        }}
    """

    # ── Subtitle ──
    SUBTITLE_STYLE = f"""
        QLabel {{
            color: {TEXT_SECONDARY};
            font-size: {FONT_SM}px;
            padding: 2px 0;
        }}
    """

    # ── Section label ──
    SECTION_LABEL_STYLE = f"""
        QLabel {{
            color: {TEXT_PRIMARY};
            font-size: {FONT_MD}px;
            font-weight: 600;
            padding: {SPACE_MD}px 0 {SPACE_SM}px 0;
        }}
    """


# Singleton
STYLE = SkillCenterStyle()
