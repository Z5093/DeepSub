"""按钮与开关。

- Button：三种语义（primary / secondary / danger）+ ghost，高度 28，圆角 8。
- Switch：自绘的 38x22 开关，带 140ms 缓动，符合「控件应能自我解释」的要求。
"""

from PyQt5.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, pyqtProperty
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import QCheckBox, QPushButton, QSizePolicy

from app.common.theme import UITheme as T


class Button(QPushButton):
    def __init__(self, text: str = "", variant: str = "secondary", parent=None):
        super().__init__(text, parent)
        self.setProperty("variant", variant)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.setMinimumHeight(28)

    def set_variant(self, variant: str):
        self.setProperty("variant", variant)
        self.style().unpolish(self)
        self.style().polish(self)


class Switch(QCheckBox):
    """38x22 的拨动开关，右侧可带说明文字。"""

    TRACK_W = 38
    TRACK_H = 22

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setChecked(False)
        self._offset = 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.setMinimumHeight(26)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.toggled.connect(self._animate)

    def _animate(self, checked: bool):
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def get_offset(self) -> float:
        return self._offset

    def set_offset(self, value: float):
        self._offset = float(value)
        self.update()

    offset = pyqtProperty(float, fget=get_offset, fset=set_offset)

    def sizeHint(self):
        from PyQt5.QtCore import QSize
        w = self.TRACK_W
        if self.text():
            w += 9 + self.fontMetrics().width(self.text())
        return QSize(w, 26)

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        y = (self.height() - self.TRACK_H) / 2.0
        track = QRectF(0, y, self.TRACK_W, self.TRACK_H)

        off = T.TINT if self.isChecked() else "#D8D8DE"
        if not self.isEnabled():
            off = T.TINT_SOFT if self.isChecked() else "#E8E8EC"
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(off))
        p.drawRoundedRect(track, self.TRACK_H / 2.0, self.TRACK_H / 2.0)

        knob = self.TRACK_H - 4
        travel = self.TRACK_W - knob - 4
        x = 2 + travel * self._offset
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, y + 2, knob, knob))

        if self.text():
            p.setPen(QPen(QColor(T.TEXT_PRIMARY if self.isEnabled() else T.TEXT_TERTIARY)))
            f = QFont(self.font())
            f.setPixelSize(T.SIZE_BODY)
            p.setFont(f)
            p.drawText(
                QRectF(self.TRACK_W + 9, 0, self.width() - self.TRACK_W - 9, self.height()),
                Qt.AlignLeft | Qt.AlignVCenter, self.text(),
            )
        p.end()
