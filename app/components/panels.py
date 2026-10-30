"""容器类组件：卡片、数据块、日志、细进度条、状态徽标。

统一规则：
- 卡片内边距 16，卡片之间 12；标题用 15px/600，说明用 12px secondary。
- 数字不抢戏：24px/600，单位 12px tertiary。
"""

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QTextCursor
from PyQt5.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

from app.common.theme import UITheme as T


def hairline() -> QFrame:
    line = QFrame()
    line.setObjectName("Hairline")
    line.setFixedHeight(1)
    line.setStyleSheet(f"background-color: {T.HAIRLINE}; border: none;")
    return line


def label(text: str = "", kind: str = "Body") -> QLabel:
    lb = QLabel(text)
    lb.setObjectName(kind)
    return lb


class Card(QFrame):
    """白底、1px 发丝边框、14 圆角的容器。"""

    def __init__(self, title: str = "", subtitle: str = "", parent=None, padding: int = 16):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(padding, padding, padding, padding)
        self._root.setSpacing(12)

        self._header = QHBoxLayout()
        self._header.setSpacing(8)
        self._header_text = QVBoxLayout()
        self._header_text.setSpacing(2)

        self.title_label = None
        self.subtitle_label = None
        if title:
            self.title_label = label(title, "H3")
            self._header_text.addWidget(self.title_label)
        if subtitle:
            self.subtitle_label = label(subtitle, "Muted")
            self.subtitle_label.setWordWrap(True)
            self._header_text.addWidget(self.subtitle_label)
        if title or subtitle:
            self._header.addLayout(self._header_text, 1)
            self._root.addLayout(self._header)

        self._body = QVBoxLayout()
        self._body.setSpacing(10)
        self._root.addLayout(self._body)

    def add_header_widget(self, widget: QWidget):
        self._header.addWidget(widget, 0, Qt.AlignRight | Qt.AlignVCenter)

    def body(self) -> QVBoxLayout:
        return self._body

    def add_widget(self, widget: QWidget, stretch: int = 0):
        self._body.addWidget(widget, stretch)

    def add_layout(self, layout, stretch: int = 0):
        self._body.addLayout(layout, stretch)

    def add_row(self, text: str, widget: QWidget, hint: str = "", label_width: int = 112):
        row = QHBoxLayout()
        row.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(1)
        lb = label(text, "Body")
        left.addWidget(lb)
        if hint:
            h = label(hint, "Hint")
            h.setWordWrap(True)
            left.addWidget(h)
        holder = QWidget()
        holder.setLayout(left)
        holder.setFixedWidth(label_width)
        row.addWidget(holder)
        row.addWidget(widget, 1)
        self._body.addLayout(row)
        return row

    def add_full_row(self, widget: QWidget):
        self._body.addWidget(widget)


class StatTile(QFrame):
    """一个数字 + 一句说明。用于状态、CUDA、模型数量等。"""

    TONES = {
        "normal": T.TEXT_PRIMARY,
        "success": T.SUCCESS,
        "warning": T.WARNING,
        "error": T.LIVE,
        "dim": T.TEXT_TERTIARY,
        "accent": T.TINT,
    }

    def __init__(self, title: str, value: str = "—", unit: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._unit = unit
        self._tone = "normal"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 13, 16, 13)
        lay.setSpacing(4)

        self._raw = value
        self._title = label(title, "SectionLabel")
        lay.addWidget(self._title)

        value_row = QHBoxLayout()
        value_row.setSpacing(4)
        value_row.setContentsMargins(0, 0, 0, 0)
        self._value = QLabel(value)
        self._value.setStyleSheet(
            f"font-family: {T.FONT_DISPLAY}; font-size: 24px; font-weight: 600;"
            f" color: {T.TEXT_PRIMARY}; background: transparent;"
        )
        value_row.addWidget(self._value, 0, Qt.AlignBottom)
        if unit:
            self._unit_label = QLabel(unit)
            self._unit_label.setStyleSheet(
                f"font-size: {T.SIZE_CAPTION}px; color: {T.TEXT_TERTIARY};"
                " background: transparent; padding-bottom: 4px;"
            )
            value_row.addWidget(self._unit_label, 0, Qt.AlignBottom)
        value_row.addStretch(1)
        lay.addLayout(value_row)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_value(self, value: str):
        self._raw = value
        self._refresh_elide()

    def _refresh_elide(self):
        """长文件名不撑破卡片：超出宽度时省略，完整内容放进 tooltip。"""
        fm = self._value.fontMetrics()
        available = max(60, self.width() - 34)
        if self._unit:
            available -= 28
        text = fm.elidedText(self._raw, Qt.ElideRight, available)
        self._value.setText(text)
        self._value.setToolTip(self._raw if text != self._raw else "")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_elide()

    def set_status(self, tone: str):
        self._tone = tone
        color = self.TONES.get(tone, T.TEXT_PRIMARY)
        self._value.setStyleSheet(
            f"font-family: {T.FONT_DISPLAY}; font-size: 24px; font-weight: 600;"
            f" color: {color}; background: transparent;"
        )

    def set_title(self, text: str):
        self._title.setText(text)


class Badge(QLabel):
    """状态小胶囊：一个圆点 + 一句话。"""

    TONES = {
        "neutral": (T.TEXT_SECONDARY, "#E8E8ED"),
        "live": (T.LIVE, T.LIVE_SOFT),
        "success": (T.SUCCESS, T.SUCCESS_SOFT),
        "warning": (T.WARNING, T.WARNING_SOFT),
        "accent": (T.TINT, T.TINT_SOFT),
    }

    def __init__(self, text: str = "", tone: str = "neutral", parent=None):
        super().__init__(text, parent)
        self._tone = tone
        self.setAlignment(Qt.AlignCenter)
        self._apply()

    def _apply(self):
        fg, bg = self.TONES.get(self._tone, self.TONES["neutral"])
        self.setStyleSheet(
            f"color: {fg}; background-color: {bg}; border-radius: 9px;"
            f" padding: 2px 9px; font-size: {T.SIZE_CAPTION}px; font-weight: 600;"
        )

    def set_tone(self, tone: str, text: str = None):
        self._tone = tone
        if text is not None:
            self.setText(text)
        self._apply()


class Meter(QFrame):
    """6px 细进度条。0-100。"""

    def __init__(self, value: int = 0, parent=None):
        super().__init__(parent)
        self._value = max(0, min(100, value))
        self._tone = T.TINT
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet("background: transparent; border: none;")

    def set_value(self, value: int):
        self._value = max(0, min(100, int(value)))
        self.update()

    def value(self) -> int:
        return self._value

    def set_tone(self, color: str):
        self._tone = color
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.TRACK))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 3, 3)
        w = self.width() * self._value / 100.0
        if w > 0:
            p.setBrush(QColor(self._tone))
            p.drawRoundedRect(QRectF(0, 0, max(w, 6.0), self.height()), 3, 3)
        p.end()


class MeterRow(QWidget):
    """标签 + 细进度条 + 百分比，用于显存/磁盘这类占用显示。"""

    def __init__(self, title: str = "", value: int = 0, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self._title = QLabel(title)
        self._title.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; font-size: {T.SIZE_SUB}px; background: transparent;"
        )
        self._title.setFixedWidth(96)
        lay.addWidget(self._title)
        self._meter = Meter(value)
        lay.addWidget(self._meter, 1)
        self._pct = QLabel(f"{value}%")
        self._pct.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; font-family: {T.FONT_MONO};"
            f" font-size: {T.SIZE_CAPTION}px; background: transparent;"
        )
        self._pct.setFixedWidth(38)
        self._pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self._pct)

    def set_value(self, value: int):
        self._meter.set_value(value)
        self._pct.setText(f"{value}%")


class LogView(QFrame):
    """运行日志：浅色底 + 等宽字体，按级别着色，不带装饰性扫描线。"""

    COLORS = {
        "info": "#3C3C43",
        "success": T.SUCCESS,
        "warning": T.WARNING,
        "error": T.LIVE,
        "dim": T.TEXT_TERTIARY,
    }

    def __init__(self, title: str = "运行日志", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._max_lines = 800
        self._lines = []
        self._autoscroll = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setFixedHeight(36)
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(16, 0, 12, 0)
        hlay.setSpacing(8)

        self._title = QLabel(title)
        self._title.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; font-size: {T.SIZE_CAPTION}px;"
            " font-weight: 600; background: transparent;"
        )
        hlay.addWidget(self._title)
        hlay.addStretch(1)

        self._count = QLabel("0")
        self._count.setStyleSheet(
            f"color: {T.TEXT_TERTIARY}; font-family: {T.FONT_MONO};"
            f" font-size: {T.SIZE_CAPTION}px; background: transparent;"
        )
        hlay.addWidget(self._count)

        self._clear_btn = QPushButton("清空")
        self._clear_btn.setProperty("variant", "ghost")
        self._clear_btn.setCursor(Qt.PointingHandCursor)
        self._clear_btn.setFixedHeight(22)
        self._clear_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {T.TINT};"
            f" font-size: {T.SIZE_CAPTION}px; padding: 0 6px; }}"
            f"QPushButton:hover {{ background: {T.TINT_SOFT}; border-radius: 4px; }}"
        )
        self._clear_btn.clicked.connect(self.clear)
        hlay.addWidget(self._clear_btn)

        root.addWidget(header)
        root.addWidget(hairline())

        self._content = QTextEdit()
        self._content.setReadOnly(True)
        self._content.setFrameShape(QFrame.NoFrame)
        self._content.setLineWrapMode(QTextEdit.WidgetWidth)
        self._content.setStyleSheet(
            f"QTextEdit {{ background: transparent; border: none; padding: 10px 14px;"
            f" color: {T.TEXT_PRIMARY}; font-family: {T.FONT_MONO};"
            f" font-size: {T.SIZE_SUB}px; selection-background-color: {T.SELECTION}; }}"
        )
        root.addWidget(self._content, 1)

    def append_line(self, text: str, level: str = "info"):
        color = self.COLORS.get(level, self.COLORS["info"])
        self._lines.append((text, color))
        if len(self._lines) > self._max_lines:
            self._lines = self._lines[-self._max_lines:]
        escaped = (
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        self._content.append(
            f'<span style="color:{color}; white-space:pre-wrap;">{escaped}</span>'
        )
        self._count.setText(f"{len(self._lines)} 行")
        if self._autoscroll:
            self._content.moveCursor(QTextCursor.End)

    def clear(self):
        self._lines.clear()
        self._content.clear()
        self._count.setText("0 行")

    def set_blink_active(self, active: bool):
        """保留旧接口：新设计不使用闪烁光标。"""
        return


class SectionLabel(QLabel):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setObjectName("SectionLabel")
