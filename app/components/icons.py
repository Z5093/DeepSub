"""矢量图标。

不依赖第三方图标字体或图片资源：全部用 QPainter 现画 1.6px 描边的线性图标，
因此在高 DPI 下始终锐利，且颜色可随状态变化。
"""

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PyQt5.QtWidgets import QPushButton, QSizePolicy, QWidget

from app.common.theme import UITheme as T


def _poly(p: QPainter, *points):
    p.drawPolyline(QPolygonF(list(points)))


def _rounded(p: QPainter, x, y, w, h, r):
    p.drawRoundedRect(QRectF(x, y, w, h), r, r)


# --- 每个图标都在 24x24 的坐标系里绘制 -------------------------------------
def _play(p):
    _rounded(p, 3.2, 4.2, 17.6, 15.6, 4.4)
    _poly(p, QPointF(10.0, 8.8), QPointF(15.6, 12.0), QPointF(10.0, 15.2), QPointF(10.0, 8.8))


def _waveform(p):
    bars = [(4.2, 9.5, 14.5), (7.6, 6.4, 17.6), (11.0, 3.6, 20.4), (14.4, 7.0, 17.0), (17.8, 10.0, 14.0),
            (21.0, 8.0, 16.0)]
    for x, y1, y2 in bars:
        p.drawLine(QPointF(x, y1), QPointF(x, y2))


def _download(p):
    p.drawLine(QPointF(12, 3.8), QPointF(12, 14.4))
    _poly(p, QPointF(7.8, 10.4), QPointF(12, 14.6), QPointF(16.2, 10.4))
    _poly(p, QPointF(4.6, 16.4), QPointF(4.6, 19.2), QPointF(19.4, 19.2), QPointF(19.4, 16.4))


def _chip(p):
    _rounded(p, 5.6, 5.6, 12.8, 12.8, 3.2)
    _rounded(p, 9.5, 9.5, 5.0, 5.0, 1.2)
    for v in (9.1, 14.9):
        p.drawLine(QPointF(v, 2.8), QPointF(v, 5.6))
        p.drawLine(QPointF(v, 18.4), QPointF(v, 21.2))
        p.drawLine(QPointF(2.8, v), QPointF(5.6, v))
        p.drawLine(QPointF(18.4, v), QPointF(21.2, v))


def _terminal(p):
    _rounded(p, 3.0, 4.6, 18.0, 14.8, 3.2)
    _poly(p, QPointF(7.0, 9.6), QPointF(10.0, 12.2), QPointF(7.0, 14.8))
    p.drawLine(QPointF(12.6, 14.8), QPointF(17.0, 14.8))


def _gear(p):
    """齿轮：外环 + 轮毂 + 8 颗与环相连的齿。齿必须贴着环，否则看着像太阳。"""
    import math
    p.drawEllipse(QPointF(12, 12), 6.1, 6.1)
    p.drawEllipse(QPointF(12, 12), 2.3, 2.3)
    for i in range(8):
        a = math.radians(i * 45)
        p.drawLine(
            QPointF(12 + 6.1 * math.cos(a), 12 + 6.1 * math.sin(a)),
            QPointF(12 + 8.2 * math.cos(a), 12 + 8.2 * math.sin(a)),
        )


def _layers(p):
    _poly(p, QPointF(12, 3.6), QPointF(20.4, 8.0), QPointF(12, 12.4), QPointF(3.6, 8.0), QPointF(12, 3.6))
    _poly(p, QPointF(3.6, 12.6), QPointF(12, 17.0), QPointF(20.4, 12.6))
    _poly(p, QPointF(3.6, 16.4), QPointF(12, 20.8), QPointF(20.4, 16.4))


def _folder(p):
    path = QPainterPath(QPointF(3.4, 7.2))
    path.lineTo(9.2, 7.2)
    path.lineTo(11.0, 9.4)
    path.lineTo(20.6, 9.4)
    path.lineTo(20.6, 18.6)
    path.lineTo(3.4, 18.6)
    path.closeSubpath()
    p.drawPath(path)


def _plus(p):
    p.drawLine(QPointF(12, 5.2), QPointF(12, 18.8))
    p.drawLine(QPointF(5.2, 12), QPointF(18.8, 12))


def _check(p):
    _poly(p, QPointF(5.2, 12.4), QPointF(10.0, 17.2), QPointF(18.8, 6.8))


def _x(p):
    p.drawLine(QPointF(6.4, 6.4), QPointF(17.6, 17.6))
    p.drawLine(QPointF(17.6, 6.4), QPointF(6.4, 17.6))


def _chevron_right(p):
    _poly(p, QPointF(9.6, 5.6), QPointF(16.0, 12.0), QPointF(9.6, 18.4))


def _chevron_down(p):
    _poly(p, QPointF(5.6, 9.6), QPointF(12.0, 16.0), QPointF(18.4, 9.6))


def _refresh(p):
    path = QPainterPath()
    path.arcMoveTo(QRectF(4.0, 4.0, 16.0, 16.0), 60)
    path.arcTo(QRectF(4.0, 4.0, 16.0, 16.0), 60, 280)
    p.drawPath(path)
    _poly(p, QPointF(15.4, 2.8), QPointF(16.6, 6.6), QPointF(12.8, 7.6))


def _trash(p):
    p.drawLine(QPointF(4.6, 7.0), QPointF(19.4, 7.0))
    _poly(p, QPointF(6.8, 7.0), QPointF(7.8, 20.0), QPointF(16.2, 20.0), QPointF(17.2, 7.0))
    _poly(p, QPointF(9.2, 7.0), QPointF(9.2, 4.2), QPointF(14.8, 4.2), QPointF(14.8, 7.0))


def _more(p):
    for x in (6.2, 12.0, 17.8):
        p.save()
        p.setBrush(p.pen().color())
        p.drawEllipse(QPointF(x, 12.0), 1.15, 1.15)
        p.restore()


def _search(p):
    p.drawEllipse(QPointF(10.6, 10.6), 5.6, 5.6)
    p.drawLine(QPointF(14.8, 14.8), QPointF(19.6, 19.6))


def _clock(p):
    p.drawEllipse(QPointF(12, 12), 8.0, 8.0)
    _poly(p, QPointF(12, 7.6), QPointF(12, 12.4), QPointF(15.4, 14.2))


def _stop(p):
    _rounded(p, 6.4, 6.4, 11.2, 11.2, 2.6)


_ICONS = {
    "play": _play,
    "waveform": _waveform,
    "download": _download,
    "chip": _chip,
    "terminal": _terminal,
    "gear": _gear,
    "layers": _layers,
    "folder": _folder,
    "plus": _plus,
    "check": _check,
    "x": _x,
    "chevron-right": _chevron_right,
    "chevron-down": _chevron_down,
    "refresh": _refresh,
    "trash": _trash,
    "more": _more,
    "search": _search,
    "clock": _clock,
    "stop": _stop,
}


def paint_icon(painter: QPainter, name: str, rect: QRectF, color, stroke: float = 1.65):
    fn = _ICONS.get(name)
    if fn is None:
        return
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.translate(rect.left(), rect.top())
    scale = min(rect.width(), rect.height()) / 24.0
    painter.scale(scale, scale)
    pen = QPen(QColor(color), stroke / max(scale, 0.01))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    fn(painter)
    painter.restore()


class Icon(QWidget):
    """固定尺寸的静态图标。"""

    def __init__(self, name: str, size: int = 18, color: str = T.TEXT_SECONDARY, parent=None):
        super().__init__(parent)
        self._name = name
        self._color = color
        self._stroke = 1.65
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_color(self, color: str):
        if color != self._color:
            self._color = color
            self.update()

    def set_name(self, name: str):
        self._name = name
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        paint_icon(p, self._name, QRectF(0, 0, self.width(), self.height()), self._color, self._stroke)
        p.end()


class IconButton(QPushButton):
    """只有图标的方形按钮（28x28），悬停显示浅底。"""

    def __init__(self, name: str, size: int = 28, color: str = T.TEXT_SECONDARY,
                 tooltip: str = "", parent=None):
        super().__init__(parent)
        self._name = name
        self._color = color
        self._icon_size = 16
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        if tooltip:
            self.setToolTip(tooltip)

    def set_name(self, name: str):
        self._name = name
        self.update()

    def set_color(self, color: str):
        self._color = color
        self.update()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self.isEnabled() and (self.underMouse() or self.isDown()):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.BTN_BG_PRESSED if self.isDown() else T.BTN_BG_HOVER))
            p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), T.R_SM, T.R_SM)
        color = self._color if self.isEnabled() else T.TEXT_TERTIARY
        s = self._icon_size
        r = QRectF((self.width() - s) / 2.0, (self.height() - s) / 2.0, s, s)
        paint_icon(p, self._name, r, color, 1.7)
        p.end()
