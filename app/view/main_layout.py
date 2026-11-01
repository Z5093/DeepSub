"""窗口骨架：侧边栏、页面标题区、底部状态条。

骨架不抢内容：侧边栏只有一层浅灰，页面标题只有标题 + 一句说明，
其余全部让给内容本身（Apple HIG 的 deference 原则）。
"""

from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from app.common.constants import (
    APP_VERSION,
    PAGE_DOWNLOAD, PAGE_FASTERWHISPER, PAGE_LLAMACPP, PAGE_SETTINGS, PAGE_WORKSPACE,
)
from app.common.theme import UITheme as T
from app.components.icons import paint_icon


class SidebarItem(QPushButton):
    HEIGHT = 32

    def __init__(self, page_id: str, icon: str, label: str, parent=None):
        super().__init__(parent)
        self._page_id = page_id
        self._icon_name = icon
        self._label = label
        self._active = False
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(label)

    @property
    def page_id(self):
        return self._page_id

    def set_active(self, active: bool):
        self._active = active
        self.setChecked(active)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)

        if self._active:
            p.setPen(QPen(QColor(T.HAIRLINE), 1.0))
            p.setBrush(QColor(T.SURFACE))
            p.drawRoundedRect(rect, T.R_MD, T.R_MD)
        elif self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 110))
            p.drawRoundedRect(rect, T.R_MD, T.R_MD)

        icon_color = T.TINT if self._active else T.TEXT_SECONDARY
        paint_icon(p, self._icon_name, QRectF(10, (self.height() - 17) / 2.0, 17, 17), icon_color, 1.65)

        f = QFont(self.font())
        f.setPixelSize(T.SIZE_BODY)
        f.setWeight(QFont.DemiBold if self._active else QFont.Normal)
        p.setFont(f)
        p.setPen(QColor(T.TEXT_PRIMARY if self._active else T.TEXT_SECONDARY))
        p.drawText(
            QRectF(35, 0, self.width() - 41, self.height()),
            Qt.AlignLeft | Qt.AlignVCenter, self._label,
        )
        p.end()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


class _Wordmark(QWidget):
    """品牌标识：一条音频波形 + 文字。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(46)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(T.TINT), 2.0)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        heights = [8, 15, 22, 13, 18, 9]
        x0, y0 = 4.0, self.height() / 2.0 + 2
        for i, h in enumerate(heights):
            x = x0 + i * 4.6
            p.drawLine(QPointF(x, y0 - h / 2.0), QPointF(x, y0 + h / 2.0))

        f = QFont(self.font())
        f.setPixelSize(16)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(T.TEXT_PRIMARY))
        p.drawText(QRectF(39, 0, self.width() - 39, self.height()), Qt.AlignLeft | Qt.AlignVCenter, "DeepSub")
        p.end()


class Sidebar(QFrame):
    page_selected = pyqtSignal(str)

    GROUPS = [
        ("工作流", [
            (PAGE_WORKSPACE, "play", "工作台"),
            (PAGE_DOWNLOAD, "download", "视频下载"),
        ]),
        ("引擎", [
            (PAGE_FASTERWHISPER, "waveform", "语音识别"),
            (PAGE_LLAMACPP, "terminal", "翻译引擎"),
        ]),
        ("系统", [
            (PAGE_SETTINGS, "gear", "设置"),
        ]),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self._items = {}
        self._current = PAGE_WORKSPACE
        self.setFixedWidth(216)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(0)

        layout.addWidget(_Wordmark())
        layout.addSpacing(10)

        for group_name, items in self.GROUPS:
            header = QLabel(group_name)
            header.setStyleSheet(
                f"color: {T.TEXT_TERTIARY}; font-size: {T.SIZE_CAPTION}px;"
                " font-weight: 600; background: transparent; padding: 10px 10px 6px 10px;"
            )
            layout.addWidget(header)

            for page_id, icon, label in items:
                item = SidebarItem(page_id, icon, label)
                item.clicked.connect(lambda checked=False, pid=page_id: self._on_clicked(pid))
                self._items[page_id] = item
                layout.addWidget(item)
                layout.addSpacing(2)

        layout.addStretch(1)

        self._state_dot = QLabel("●")
        self._state_text = QLabel("就绪")
        foot = QHBoxLayout()
        foot.setContentsMargins(10, 6, 10, 6)
        foot.setSpacing(6)
        self._state_dot.setStyleSheet(
            f"color: {T.SUCCESS}; font-size: 9px; background: transparent;"
        )
        self._state_text.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; font-size: {T.SIZE_CAPTION}px; background: transparent;"
        )
        foot.addWidget(self._state_dot)
        foot.addWidget(self._state_text)
        foot.addStretch(1)
        layout.addLayout(foot)

        # 版本号统一取 APP_VERSION，别在界面里写死
        version = QLabel(f"v{APP_VERSION}")
        version.setStyleSheet(
            f"color: {T.TEXT_TERTIARY}; font-family: {T.FONT_MONO}; font-size: 10px;"
            " background: transparent; padding-left: 10px;"
        )
        layout.addWidget(version)

        self._items[PAGE_WORKSPACE].set_active(True)

    def _on_clicked(self, page_id: str):
        if page_id == self._current:
            return
        self._items[self._current].set_active(False)
        self._current = page_id
        self._items[page_id].set_active(True)
        self.page_selected.emit(page_id)

    def set_current_page(self, page_id: str):
        if page_id == self._current or page_id not in self._items:
            return
        self._items[self._current].set_active(False)
        self._current = page_id
        self._items[page_id].set_active(True)

    def set_state(self, text: str, tone: str = "success"):
        colors = {"success": T.SUCCESS, "warning": T.WARNING, "error": T.LIVE, "idle": T.TEXT_TERTIARY}
        self._state_dot.setStyleSheet(
            f"color: {colors.get(tone, T.SUCCESS)}; font-size: 9px; background: transparent;"
        )
        self._state_text.setText(text)


class PageHeader(QWidget):
    """页面标题区：标题 + 一句说明 + 右侧操作。"""

    def __init__(self, title: str = "", subtitle: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self._title = QLabel(title)
        self._title.setObjectName("H2")
        self._subtitle = QLabel(subtitle)
        self._subtitle.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; font-size: {T.SIZE_SUB}px; background: transparent;"
        )
        text_col.addWidget(self._title)
        if subtitle:
            text_col.addWidget(self._subtitle)
        lay.addLayout(text_col)
        lay.addStretch(1)

        self._actions = QHBoxLayout()
        self._actions.setSpacing(8)
        lay.addLayout(self._actions)

    def add_action(self, widget: QWidget):
        self._actions.addWidget(widget)

    def set_title(self, title: str, subtitle: str = ""):
        self._title.setText(title)
        self._subtitle.setText(subtitle)


class StatusBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatusBar")
        self.setFixedHeight(28)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(10)

        self._left = QLabel("就绪")
        self._left.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; font-size: {T.SIZE_CAPTION}px; background: transparent;"
        )
        lay.addWidget(self._left)
        lay.addStretch(1)

        self._right = QLabel("DeepSub")
        self._right.setStyleSheet(
            f"color: {T.TEXT_TERTIARY}; font-size: {T.SIZE_CAPTION}px; background: transparent;"
        )
        lay.addWidget(self._right)

    def set_status(self, left: str = None, right: str = None):
        if left is not None:
            self._left.setText(left)
        if right is not None:
            self._right.setText(right)
