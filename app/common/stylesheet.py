"""全局 QSS。

层次只靠三样东西：留白、1px 发丝线、字重。不使用渐变与彩色阴影。
"""

from app.common.theme import UITheme as T


APP_QSS = f"""
/* ============================ 基础 ============================ */
QWidget {{
    background: transparent;
    color: {T.TEXT_PRIMARY};
    font-family: {T.FONT_UI};
    font-size: {T.SIZE_BODY}px;
}}
QMainWindow, QDialog, QMessageBox {{
    background-color: {T.CANVAS};
}}
QWidget#PageRoot {{
    background-color: {T.CANVAS};
}}
QWidget#AppRoot, QStackedWidget#PageStack {{
    background-color: {T.CANVAS};
}}
QWidget#WizardHeader {{
    background-color: {T.CANVAS};
    border-bottom: 1px solid {T.HAIRLINE};
}}
QWidget#WizardFooter {{
    background-color: {T.CANVAS};
    border-top: 1px solid {T.HAIRLINE};
}}
QScrollArea, QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}

/* ============================ 文字层级 ============================ */
QLabel {{
    background: transparent;
    color: {T.TEXT_PRIMARY};
}}
QLabel#H1 {{
    font-family: {T.FONT_DISPLAY};
    font-size: {T.SIZE_DISPLAY}px;
    font-weight: 600;
    color: {T.TEXT_PRIMARY};
}}
QLabel#H2 {{
    font-family: {T.FONT_DISPLAY};
    font-size: {T.SIZE_TITLE}px;
    font-weight: 600;
    color: {T.TEXT_PRIMARY};
}}
QLabel#H3 {{
    font-family: {T.FONT_UI};
    font-size: {T.SIZE_HEADLINE}px;
    font-weight: 600;
    color: {T.TEXT_PRIMARY};
}}
QLabel#Body {{
    font-size: {T.SIZE_BODY}px;
    color: {T.TEXT_PRIMARY};
}}
QLabel#Muted {{
    font-size: {T.SIZE_SUB}px;
    color: {T.TEXT_SECONDARY};
}}
QLabel#Hint {{
    font-size: {T.SIZE_CAPTION}px;
    color: {T.TEXT_TERTIARY};
}}
QLabel#Mono {{
    font-family: {T.FONT_MONO};
    font-size: {T.SIZE_SUB}px;
    color: {T.TEXT_SECONDARY};
}}
QLabel#SectionLabel {{
    font-size: {T.SIZE_CAPTION}px;
    font-weight: 600;
    color: {T.TEXT_TERTIARY};
}}
QLabel#Link {{
    color: {T.TINT};
}}

/* ============================ 容器 ============================ */
QFrame#Card {{
    background-color: {T.SURFACE};
    border: 1px solid {T.HAIRLINE};
    border-radius: {T.R_XL}px;
}}
QFrame#CardMuted {{
    background-color: {T.SURFACE_MUTED};
    border: 1px solid {T.HAIRLINE_SOFT};
    border-radius: {T.R_MD}px;
}}
QFrame#Hairline {{
    background-color: {T.HAIRLINE};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}
QFrame#Sidebar {{
    background-color: {T.SIDEBAR};
    border: none;
    border-right: 1px solid {T.HAIRLINE_STRONG};
}}
QFrame#StatusBar {{
    background-color: {T.CANVAS};
    border: none;
    border-top: 1px solid {T.HAIRLINE};
}}

/* ============================ 按钮 ============================ */
QPushButton {{
    background-color: {T.BTN_BG};
    border: 1px solid {T.BTN_BORDER};
    border-radius: {T.R_MD}px;
    color: {T.TEXT_PRIMARY};
    font-family: {T.FONT_UI};
    font-size: {T.SIZE_BODY}px;
    font-weight: 500;
    padding: 5px 14px;
    min-height: 22px;
}}
QPushButton:hover {{
    background-color: {T.BTN_BG_HOVER};
    border-color: {T.FIELD_BORDER_HOVER};
}}
QPushButton:pressed {{
    background-color: {T.BTN_BG_PRESSED};
}}
QPushButton:disabled {{
    background-color: {T.FIELD_BG_DISABLED};
    border-color: {T.HAIRLINE};
    color: {T.TEXT_TERTIARY};
}}
QPushButton[variant="primary"] {{
    background-color: {T.TINT};
    border: 1px solid {T.TINT};
    color: {T.TEXT_ON_TINT};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{
    background-color: {T.TINT_HOVER};
    border-color: {T.TINT_HOVER};
}}
QPushButton[variant="primary"]:pressed {{
    background-color: {T.TINT_PRESSED};
    border-color: {T.TINT_PRESSED};
}}
QPushButton[variant="primary"]:disabled {{
    background-color: {T.TINT_SOFT};
    border-color: {T.TINT_BORDER};
    color: #6E6E73;
}}
QPushButton[variant="ghost"] {{
    background-color: transparent;
    border: 1px solid transparent;
    color: {T.TINT};
}}
QPushButton[variant="ghost"]:hover {{
    background-color: {T.TINT_SOFT};
}}
QPushButton[variant="danger"] {{
    background-color: transparent;
    border: 1px solid {T.HAIRLINE_STRONG};
    color: {T.LIVE};
}}
QPushButton[variant="danger"]:hover {{
    background-color: {T.LIVE_SOFT};
    border-color: #F3C2C6;
}}

/* ============================ 输入控件 ============================ */
QLineEdit, QPlainTextEdit, QTextEdit {{
    background-color: {T.FIELD_BG};
    border: 1px solid {T.FIELD_BORDER};
    border-radius: {T.R_MD}px;
    color: {T.TEXT_PRIMARY};
    font-family: {T.FONT_UI};
    font-size: {T.SIZE_BODY}px;
    padding: 5px 9px;
    min-height: 20px;
    selection-background-color: {T.SELECTION};
    selection-color: {T.TEXT_PRIMARY};
}}
QLineEdit:hover, QPlainTextEdit:hover {{
    border-color: {T.FIELD_BORDER_HOVER};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {T.TINT};
    background-color: {T.SURFACE};
}}
QLineEdit:disabled {{
    background-color: {T.FIELD_BG_DISABLED};
    color: {T.TEXT_TERTIARY};
    border-color: {T.HAIRLINE};
}}
QLineEdit[mono="true"] {{
    font-family: {T.FONT_MONO};
    font-size: {T.SIZE_SUB}px;
}}

QComboBox {{
    background-color: {T.FIELD_BG};
    border: 1px solid {T.FIELD_BORDER};
    border-radius: {T.R_MD}px;
    color: {T.TEXT_PRIMARY};
    font-size: {T.SIZE_BODY}px;
    padding: 5px 9px;
    min-height: 20px;
}}
QComboBox:hover {{
    border-color: {T.FIELD_BORDER_HOVER};
}}
QComboBox:focus, QComboBox:on {{
    border-color: {T.TINT};
}}
QComboBox:disabled {{
    background-color: {T.FIELD_BG_DISABLED};
    color: {T.TEXT_TERTIARY};
    border-color: {T.HAIRLINE};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}}
QComboBox QAbstractItemView {{
    background-color: {T.SURFACE};
    border: 1px solid {T.HAIRLINE_STRONG};
    border-radius: {T.R_MD}px;
    padding: 4px;
    color: {T.TEXT_PRIMARY};
    outline: none;
    selection-background-color: {T.TINT_SOFT};
    selection-color: {T.TINT};
}}
QComboBox QAbstractItemView::item {{
    padding: 5px 8px;
    border-radius: {T.R_SM}px;
    min-height: 22px;
}}

QSpinBox, QDoubleSpinBox {{
    background-color: {T.FIELD_BG};
    border: 1px solid {T.FIELD_BORDER};
    border-radius: {T.R_MD}px;
    color: {T.TEXT_PRIMARY};
    font-family: {T.FONT_MONO};
    font-size: {T.SIZE_SUB}px;
    padding: 5px 8px;
    min-height: 20px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {T.TINT};
}}
QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background-color: {T.FIELD_BG_DISABLED};
    color: {T.TEXT_TERTIARY};
    border-color: {T.HAIRLINE};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background: transparent;
    border: none;
    width: 16px;
}}

/* ============================ 滑块 ============================ */
QSlider::groove:horizontal {{
    background: {T.TRACK};
    height: 4px;
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {T.TINT};
    height: 4px;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {T.KNOB};
    border: 1px solid {T.FIELD_BORDER_HOVER};
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{
    border-color: {T.TINT};
}}

/* ============================ 进度 ============================ */
QProgressBar {{
    background-color: {T.TRACK};
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {T.TINT};
    border-radius: 3px;
}}

/* ============================ 滚动条 ============================ */
QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #C7C7CC;
    border-radius: 3px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{
    background: #AEAEB4;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #C7C7CC;
    border-radius: 3px;
    min-width: 32px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
    width: 0;
}}

/* ============================ 分段控件（Tab） ============================ */
QTabWidget {{
    background: transparent;
    border: none;
}}
QTabWidget::pane {{
    border: none;
    background: transparent;
    top: -1px;
}}
QTabBar {{
    background: #E8E8ED;
    border-radius: {T.R_LG}px;
    qproperty-drawBase: 0;
}}
QTabBar::tab {{
    background: transparent;
    border: none;
    border-radius: {T.R_SM}px;
    color: {T.TEXT_SECONDARY};
    font-size: {T.SIZE_BODY}px;
    font-weight: 500;
    padding: 5px 14px;
    margin: 3px;
    min-height: 20px;
}}
QTabBar::tab:hover:!selected {{
    color: {T.TEXT_PRIMARY};
}}
QTabBar::tab:selected {{
    background: {T.SURFACE};
    color: {T.TEXT_PRIMARY};
    font-weight: 600;
}}

/* ============================ 表格 ============================ */
QTableWidget, QTableView {{
    background-color: {T.SURFACE};
    alternate-background-color: {T.SURFACE_MUTED};
    border: 1px solid {T.HAIRLINE};
    border-radius: {T.R_MD}px;
    gridline-color: transparent;
    color: {T.TEXT_PRIMARY};
    font-size: {T.SIZE_BODY}px;
    selection-background-color: {T.TINT_SOFT};
    selection-color: {T.TEXT_PRIMARY};
    outline: none;
}}
QTableWidget::item {{
    padding: 6px 8px;
    border: none;
}}
QTableWidget::item:selected {{
    background-color: {T.TINT_SOFT};
    color: {T.TEXT_PRIMARY};
}}
QHeaderView {{
    background-color: transparent;
}}
QHeaderView::section {{
    background-color: {T.SURFACE_MUTED};
    border: none;
    border-bottom: 1px solid {T.HAIRLINE};
    padding: 6px 8px;
    color: {T.TEXT_SECONDARY};
    font-size: {T.SIZE_CAPTION}px;
    font-weight: 600;
}}
QTableCornerButton::section {{
    background-color: {T.SURFACE_MUTED};
    border: none;
}}

/* ============================ 列表 / 树 ============================ */
QListWidget, QListView, QTreeWidget, QTreeView {{
    background-color: {T.SURFACE};
    border: 1px solid {T.HAIRLINE};
    border-radius: {T.R_MD}px;
    color: {T.TEXT_PRIMARY};
    font-size: {T.SIZE_BODY}px;
    outline: none;
}}
QListWidget::item {{
    padding: 6px 8px;
    border-radius: {T.R_SM}px;
}}
QListWidget::item:selected {{
    background-color: {T.TINT_SOFT};
    color: {T.TINT};
}}

/* ============================ 其它 ============================ */
QSplitter::handle {{
    background: transparent;
}}
QSplitter::handle:horizontal {{
    width: 1px;
    background: {T.HAIRLINE};
}}
QSplitter::handle:horizontal:hover {{
    background: {T.HAIRLINE_STRONG};
}}
QSplitter::handle:vertical {{
    height: 1px;
    background: {T.HAIRLINE};
}}

QToolTip {{
    background-color: #2C2C2E;
    color: #F2F2F7;
    border: none;
    border-radius: {T.R_SM}px;
    padding: 5px 8px;
    font-size: {T.SIZE_CAPTION}px;
}}

QMenu {{
    background-color: {T.SURFACE};
    border: 1px solid {T.HAIRLINE_STRONG};
    border-radius: {T.R_MD}px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 20px;
    border-radius: {T.R_SM}px;
    color: {T.TEXT_PRIMARY};
}}
QMenu::item:selected {{
    background-color: {T.TINT_SOFT};
    color: {T.TINT};
}}
QMenu::separator {{
    height: 1px;
    background: {T.HAIRLINE};
    margin: 4px 8px;
}}

QGroupBox {{
    border: 1px solid {T.HAIRLINE};
    border-radius: {T.R_MD}px;
    margin-top: 14px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
    color: {T.TEXT_PRIMARY};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {T.TEXT_SECONDARY};
    font-size: {T.SIZE_CAPTION}px;
}}

QStatusBar {{
    background: {T.CANVAS};
    color: {T.TEXT_SECONDARY};
    border-top: 1px solid {T.HAIRLINE};
}}
"""
