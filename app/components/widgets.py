"""表单与输入控件。

所有控件都保持 28px 高、8px 圆角、1px 发丝边框；行内标签统一 13px，
说明文字 11px —— 与 Apple HIG 的「控件与文字比例」保持一致。
"""

from PyQt5.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QScrollArea, QSizePolicy, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from app.common.theme import UITheme as T
from app.components.icons import paint_icon


class Label(QLabel):
    KINDS = {
        "title": "H1",
        "heading": "H2",
        "subheading": "H3",
        "body": "Body",
        "muted": "Muted",
        "hint": "Hint",
        "mono": "Mono",
        "section": "SectionLabel",
    }

    def __init__(self, text: str = "", kind: str = "body", parent=None):
        super().__init__(text, parent)
        self._kind = kind
        self.setObjectName(self.KINDS.get(kind, "Body"))

    def set_kind(self, kind: str):
        self._kind = kind
        self.setObjectName(self.KINDS.get(kind, "Body"))
        self.style().unpolish(self)
        self.style().polish(self)


class TextField(QLineEdit):
    def __init__(self, placeholder: str = "", mono: bool = False, parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setMinimumHeight(28)
        self.setProperty("mono", "true" if mono else "false")
        self.setClearButtonEnabled(False)

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(s.width(), 28)


class Select(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(28)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        # 未获得焦点时不响应滚轮，避免误改参数
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NumberField(QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(28)
        self.setButtonSymbols(QSpinBox.NoButtons)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class DecimalField(QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(28)
        self.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class Slider(QSlider):
    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setFixedHeight(22)


class CheckBox(QCheckBox):
    BOX = 16

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(24)

    def sizeHint(self):
        w = self.BOX
        if self.text():
            w += 8 + self.fontMetrics().width(self.text())
        return QSize(w + 2, 24)

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        y = (self.height() - self.BOX) / 2.0
        box = QRectF(0.5, y + 0.5, self.BOX - 1, self.BOX - 1)

        if self.isChecked():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.TINT if self.isEnabled() else T.TINT_BORDER))
            p.drawRoundedRect(box, T.R_XS, T.R_XS)
            paint_icon(p, "check", QRectF(box.x() + 2.4, box.y() + 2.4, 10, 10), "#FFFFFF", 2.6)
        else:
            p.setPen(QPen(QColor(T.FIELD_BORDER if self.isEnabled() else T.HAIRLINE_STRONG), 1.2))
            p.setBrush(QColor(T.FIELD_BG if self.isEnabled() else T.FIELD_BG_DISABLED))
            p.drawRoundedRect(box, T.R_XS, T.R_XS)

        if self.text():
            p.setPen(QColor(T.TEXT_PRIMARY if self.isEnabled() else T.TEXT_TERTIARY))
            f = QFont(self.font())
            f.setPixelSize(T.SIZE_BODY)
            p.setFont(f)
            p.drawText(
                QRectF(self.BOX + 8, 0, self.width() - self.BOX - 8, self.height()),
                Qt.AlignLeft | Qt.AlignVCenter, self.text(),
            )
        p.end()


class Field(QWidget):
    """一行标签 + 控件（+ 可选说明）。

    短控件（下拉框、数字框、勾选框）保持自然宽度并左对齐，
    只有多行文本类控件才会铺满整行 —— 这样表单不会显得松散。
    """

    SHORT_CONTROLS = (QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox)

    def __init__(self, text: str, widget: QWidget, hint: str = "", label_width: int = 118, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(1)
        left.setContentsMargins(0, 0, 0, 0)
        lb = Label(text, "body")
        left.addWidget(lb)
        if hint:
            h = Label(hint, "hint")
            h.setWordWrap(True)
            left.addWidget(h)
        holder = QWidget()
        holder.setLayout(left)
        holder.setFixedWidth(label_width)
        lay.addWidget(holder)

        if isinstance(widget, self.SHORT_CONTROLS):
            if widget.minimumWidth() < 180:
                widget.setMinimumWidth(180)
            wrap = QWidget()
            wl = QHBoxLayout(wrap)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(0)
            wl.addWidget(widget)
            wl.addStretch(1)
            lay.addWidget(wrap, 1)
        else:
            lay.addWidget(widget, 1)


class Section(QFrame):
    """卡片内的一个分组：标题 + 内容。"""

    def __init__(self, title: str = "", hint: str = "", parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(10)
        if title:
            head = QVBoxLayout()
            head.setSpacing(2)
            head.setContentsMargins(0, 0, 0, 0)
            head.addWidget(Label(title, "section"))
            if hint:
                h = Label(hint, "hint")
                h.setWordWrap(True)
                head.addWidget(h)
            self._root.addLayout(head)
        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(10)
        self._root.addLayout(self._body)

    def body(self) -> QVBoxLayout:
        return self._body

    def add_widget(self, widget: QWidget, stretch: int = 0):
        self._body.addWidget(widget, stretch)

    def add_layout(self, layout, stretch: int = 0):
        self._body.addLayout(layout, stretch)

    def add_row(self, text: str, widget: QWidget, hint: str = "", label_width: int = 118):
        self._body.addWidget(Field(text, widget, hint, label_width))
        return widget

    def add_hairline(self):
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background-color: {T.HAIRLINE_SOFT}; border: none;")
        self._body.addWidget(line)


class Form(QScrollArea):
    """可滚动的表单：一串 Section，自动留白。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._container.setObjectName("FormBody")
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)
        self._layout.addStretch(1)
        self.setWidget(self._container)

    def add_section(self, title: str, hint: str = "") -> Section:
        section = Section(title, hint)
        self._layout.insertWidget(self._layout.count() - 1, section)
        return section

    def add_widget(self, widget: QWidget):
        self._layout.insertWidget(self._layout.count() - 1, widget)


class DropZone(QFrame):
    """虚线投放区：点击选择文件，或把文件拖进来。"""

    clicked = pyqtSignal()
    files_dropped = pyqtSignal(list)

    def __init__(self, title: str = "拖入视频或字幕文件", subtitle: str = "", parent=None):
        super().__init__(parent)
        self._title = title
        self._subtitle = subtitle
        self._active = False
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(132)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignCenter)

        from app.components.icons import Icon
        self._icon = Icon("download", 22, T.TEXT_TERTIARY)
        lay.addWidget(self._icon, 0, Qt.AlignHCenter)

        self._title_label = QLabel(title)
        self._title_label.setStyleSheet(
            f"color: {T.TEXT_PRIMARY}; font-size: {T.SIZE_BODY}px; font-weight: 600;"
            " background: transparent;"
        )
        self._title_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._title_label)

        self._subtitle_label = QLabel(subtitle)
        self._subtitle_label.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; font-size: {T.SIZE_SUB}px; background: transparent;"
        )
        self._subtitle_label.setAlignment(Qt.AlignCenter)
        self._subtitle_label.setWordWrap(True)
        lay.addWidget(self._subtitle_label)

    def set_texts(self, title: str, subtitle: str = ""):
        self._title_label.setText(title)
        self._subtitle_label.setText(subtitle)

    def set_active(self, active: bool):
        self._active = active
        self._icon.set_color(T.TINT if active else T.TEXT_TERTIARY)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.set_active(True)

    def dragLeaveEvent(self, event):
        self.set_active(False)

    def dropEvent(self, event):
        self.set_active(False)
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.75, 0.75, self.width() - 1.5, self.height() - 1.5)
        p.setBrush(QColor(T.TINT_SOFT if self._active else T.SURFACE_MUTED))
        pen = QPen(QColor(T.TINT if self._active else "#D2D2D9"), 1.4)
        pen.setStyle(Qt.DashLine)
        pen.setDashPattern([4, 3])
        p.setPen(pen)
        p.drawRoundedRect(rect, T.R_LG, T.R_LG)
        p.end()
