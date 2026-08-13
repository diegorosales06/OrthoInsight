"""Design tokens + global stylesheet for the OrthoInsight dashboard.

Palette: white + baby blue. All UI colors are defined here so components
never hardcode hex values. Plot curve colors live in `constants.py`.
"""

# ---- Surface + text ----
SURFACE          = "#FFFFFF"   # window / plot background
SURFACE_ALT      = "#F3F8FC"   # panels, group boxes, alternating rows
SURFACE_HOVER    = "#E4F0FA"   # hover fill
SURFACE_SUNKEN   = "#EAF3FB"   # sunken / pressed fill

ON_SURFACE       = "#1F2A3A"   # primary text (dark slate — 12:1 on white)
ON_SURFACE_MUTED = "#5C6B7A"   # secondary text / captions
ON_SURFACE_SUBTLE = "#8896A6"  # disabled / tertiary

# ---- Blue interactive family ----
PRIMARY          = "#055CA3"   # dark blue per user spec
PRIMARY_HOVER    = "#044D8A"
PRIMARY_PRESSED  = "#034173"
PRIMARY_LIGHT    = "#C5DCF0"   # tint for backgrounds / selection
PRIMARY_TINT     = "#E4EFF9"   # very light tint for hover surfaces
ON_PRIMARY       = "#FFFFFF"

# ---- Outline / dividers ----
OUTLINE          = "#D6E3EE"
OUTLINE_STRONG   = "#B7CCDE"

# ---- Semantic accents (chosen to harmonize with baby blue) ----
ACCENT_SUCCESS   = "#3E9E7A"   # muted green — recording live
ACCENT_DANGER    = "#D9634B"   # warm coral — stop/error
ACCENT_DANGER_HOVER = "#C5553F"
ACCENT_WARNING   = "#D89A3E"   # amber — debug mode indicator
ACCENT_WARNING_HOVER = "#BF8830"

# ---- Typography scale (points; Minor Third ratio, base 10 for dense UI) ----
FONT_FAMILY      = ""  # empty = system default (Segoe UI / SF / Roboto)
FONT_CAPTION     = 9
FONT_BODY        = 10
FONT_CONTROL     = 11
FONT_READOUT     = 13
FONT_SECTION     = 14
FONT_TITLE       = 18

# ---- pyqtgraph plot theming ----
PLOT_BG          = SURFACE
PLOT_FG          = ON_SURFACE
PLOT_GRID_ALPHA  = 0.15
PLOT_AXIS_COLOR  = "#8896A6"
PLOT_LINE_WIDTH  = 2


def apply(app):
    """Apply the global stylesheet + default font to the QApplication."""
    from PyQt6.QtGui import QFont
    font = QFont()
    font.setPointSize(FONT_BODY)
    app.setFont(font)
    app.setStyleSheet(stylesheet())


def stylesheet() -> str:
    return f"""
    /* ---- base ---- */
    QMainWindow, QWidget {{
        background-color: {SURFACE};
        color: {ON_SURFACE};
    }}
    QLabel {{
        color: {ON_SURFACE};
        background: transparent;
    }}
    QLabel[muted="true"] {{ color: {ON_SURFACE_MUTED}; }}
    QLabel[subtle="true"] {{ color: {ON_SURFACE_SUBTLE}; }}
    QLabel[role="title"] {{
        color: {ON_SURFACE};
        font-size: {FONT_TITLE}pt;
        font-weight: 600;
        padding-bottom: 2px;
    }}
    QLabel[role="section"] {{
        color: {ON_SURFACE};
        font-size: {FONT_SECTION}pt;
        font-weight: 600;
    }}
    QLabel[role="subtitle"] {{
        color: {ON_SURFACE_MUTED};
        font-size: {FONT_BODY}pt;
    }}

    /* ---- tabs ---- */
    QTabWidget::pane {{
        border: 1px solid {OUTLINE};
        border-radius: 10px;
        background: {SURFACE};
        top: -1px;
    }}
    QTabBar {{
        qproperty-drawBase: 0;
        background: transparent;
    }}
    QTabBar::tab {{
        background: {SURFACE_ALT};
        color: {ON_SURFACE_MUTED};
        padding: 9px 20px;
        margin-right: 3px;
        border: 1px solid {OUTLINE};
        border-bottom: none;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
        font-size: {FONT_CONTROL}pt;
        min-width: 80px;
    }}
    QTabBar::tab:selected {{
        background: {SURFACE};
        color: {PRIMARY_PRESSED};
        font-weight: 600;
        border-color: {OUTLINE};
    }}
    QTabBar::tab:hover:!selected {{
        background: {SURFACE_HOVER};
        color: {ON_SURFACE};
    }}

    /* ---- dropdown menu (cell selector on the Cell Graphs tab) ---- */
    QMenu {{
        background-color: {SURFACE};
        border: 1px solid {OUTLINE_STRONG};
        border-radius: 8px;
        padding: 4px;
    }}
    QMenu::item {{
        background: transparent;
        color: {ON_SURFACE};
        padding: 7px 26px 7px 22px;
        border-radius: 5px;
        font-size: {FONT_CONTROL}pt;
    }}
    QMenu::item:selected {{
        background-color: {PRIMARY_LIGHT};
        color: {PRIMARY_PRESSED};
    }}
    QMenu::item:checked {{
        font-weight: 600;
        color: {PRIMARY_PRESSED};
    }}
    QMenu::indicator {{
        width: 12px;
        height: 12px;
        left: 6px;
    }}
    QMenu::separator {{
        height: 1px;
        background: {OUTLINE};
        margin: 4px 8px;
    }}

    /* ---- default buttons (secondary/ghost) ---- */
    QPushButton {{
        background-color: {SURFACE};
        color: {ON_SURFACE};
        border: 1px solid {OUTLINE_STRONG};
        border-radius: 6px;
        padding: 7px 16px;
        font-size: {FONT_CONTROL}pt;
    }}
    QPushButton:hover {{
        background-color: {SURFACE_HOVER};
        border-color: {PRIMARY};
    }}
    QPushButton:pressed {{
        background-color: {PRIMARY_LIGHT};
        border-color: {PRIMARY_HOVER};
    }}
    QPushButton:disabled {{
        background-color: {SURFACE_ALT};
        color: {ON_SURFACE_SUBTLE};
        border-color: {OUTLINE};
    }}
    QPushButton:checked {{
        background-color: {PRIMARY_LIGHT};
        border-color: {PRIMARY};
        color: {PRIMARY_PRESSED};
    }}

    /* ---- primary button ---- */
    QPushButton[variant="primary"] {{
        background-color: {PRIMARY};
        color: {ON_PRIMARY};
        border: 1px solid {PRIMARY};
        font-weight: 600;
    }}
    QPushButton[variant="primary"]:hover {{
        background-color: {PRIMARY_HOVER};
        border-color: {PRIMARY_HOVER};
    }}
    QPushButton[variant="primary"]:pressed {{
        background-color: {PRIMARY_PRESSED};
        border-color: {PRIMARY_PRESSED};
    }}
    QPushButton[variant="primary"]:disabled {{
        background-color: {PRIMARY_LIGHT};
        border-color: {PRIMARY_LIGHT};
        color: {SURFACE};
    }}

    /* ---- danger (recording / stop) ---- */
    QPushButton[variant="danger"] {{
        background-color: {ACCENT_DANGER};
        color: white;
        border: 1px solid {ACCENT_DANGER};
        font-weight: 600;
    }}
    QPushButton[variant="danger"]:hover {{
        background-color: {ACCENT_DANGER_HOVER};
        border-color: {ACCENT_DANGER_HOVER};
    }}

    /* ---- accent (debug mode) ---- */
    QPushButton[variant="accent"] {{
        background-color: {ACCENT_WARNING};
        color: white;
        border: 1px solid {ACCENT_WARNING};
        font-weight: 600;
    }}
    QPushButton[variant="accent"]:hover {{
        background-color: {ACCENT_WARNING_HOVER};
        border-color: {ACCENT_WARNING_HOVER};
    }}

    /* ---- inputs ---- */
    QSpinBox, QDoubleSpinBox, QComboBox {{
        background-color: {SURFACE};
        color: {ON_SURFACE};
        border: 1px solid {OUTLINE_STRONG};
        border-radius: 6px;
        padding: 5px 8px;
        font-size: {FONT_CONTROL}pt;
        min-height: 22px;
        selection-background-color: {PRIMARY_LIGHT};
        selection-color: {ON_SURFACE};
    }}
    QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {PRIMARY};
    }}
    QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
        background-color: {SURFACE_ALT};
        color: {ON_SURFACE_SUBTLE};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 22px;
        border: none;
    }}
    QComboBox QAbstractItemView {{
        background-color: {SURFACE};
        color: {ON_SURFACE};
        border: 1px solid {OUTLINE_STRONG};
        selection-background-color: {PRIMARY_LIGHT};
        selection-color: {ON_SURFACE};
        outline: none;
    }}
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        background: {SURFACE_ALT};
        border: none;
        width: 18px;
    }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover,
    QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
        background: {SURFACE_HOVER};
    }}

    /* ---- tables ---- */
    QTableWidget {{
        background-color: {SURFACE};
        alternate-background-color: {SURFACE_ALT};
        gridline-color: {OUTLINE};
        selection-background-color: {PRIMARY_LIGHT};
        selection-color: {ON_SURFACE};
        border: 1px solid {OUTLINE};
        border-radius: 8px;
        font-size: {FONT_BODY}pt;
    }}
    QTableWidget::item {{ padding: 4px 6px; }}
    QTableWidget::item:selected {{ background-color: {PRIMARY_LIGHT}; color: {ON_SURFACE}; }}

    QHeaderView::section {{
        background-color: {SURFACE_ALT};
        color: {ON_SURFACE};
        padding: 8px 12px;
        border: none;
        border-bottom: 1px solid {OUTLINE_STRONG};
        border-right: 1px solid {OUTLINE};
        font-weight: 600;
        font-size: {FONT_BODY}pt;
    }}
    QHeaderView::section:last {{ border-right: none; }}
    QTableCornerButton::section {{
        background-color: {SURFACE_ALT};
        border: none;
        border-bottom: 1px solid {OUTLINE_STRONG};
    }}

    /* ---- group box ---- */
    QGroupBox {{
        background-color: {SURFACE_ALT};
        border: 1px solid {OUTLINE};
        border-radius: 10px;
        margin-top: 16px;
        padding: 18px 16px 12px 16px;
        font-size: {FONT_CONTROL}pt;
        font-weight: 600;
        color: {ON_SURFACE};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        padding: 0 8px;
        left: 14px;
        color: {PRIMARY_PRESSED};
    }}

    /* ---- scrollbars ---- */
    QScrollBar:vertical {{
        background: transparent;
        width: 12px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {OUTLINE_STRONG};
        border-radius: 5px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {PRIMARY}; }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 12px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {OUTLINE_STRONG};
        border-radius: 5px;
        min-width: 30px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {PRIMARY}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ background: none; border: none; height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

    /* ---- tooltip ---- */
    QToolTip {{
        background-color: {ON_SURFACE};
        color: white;
        border: none;
        padding: 6px 10px;
        border-radius: 4px;
        font-size: {FONT_BODY}pt;
    }}

    /* ---- message boxes ---- */
    QMessageBox {{ background-color: {SURFACE}; }}
    QMessageBox QLabel {{ color: {ON_SURFACE}; }}
    """
