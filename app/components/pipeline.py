"""处理流程 —— 整个界面唯一的「记忆点」。

做成播出/剪辑软件里的时间码轨道：一条轨道线串起 7 个环节，
编号像 cue 表一样标在上方，正在执行的环节带一个红色播出指示（tally）。
其余部分刻意保持安静，整套 UI 只有这里允许出现红色。
"""

from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from app.common.theme import UITheme as T
from app.components.icons import paint_icon


STEP_ORDER = ["download", "audio", "asr", "split1", "translate", "split2", "export"]

STEP_LABELS = {
    "download": "下载",
    "audio": "音频",
    "asr": "识别",
    "split1": "分段",
    "translate": "翻译",
    "split2": "对齐",
    "export": "导出",
}

STEP_HINTS = {
    "download": "从链接抓取视频",
    "audio": "抽取 48k FLAC 音轨",
    "asr": "FasterWhisper 词级时间戳",
    "split1": "按语义切分为整句",
    "translate": "三步翻译 + 上下文窗口",
    "split2": "AI 断句并回填时间轴",
    "export": "输出 SRT / ASS / VTT",
}


class _Rail(QWidget):
    step_clicked = pyqtSignal(str)

    NODE_R = 8.5
    PAD_X = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self._states = {sid: "pending" for sid in STEP_ORDER}
        self._active = ""
        self.setFixedHeight(78)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self._hover = ""

    # -- 状态 ---------------------------------------------------------------
    def set_state(self, step_id: str, state: str):
        if step_id in self._states:
            self._states[step_id] = state
            self.update()

    def set_active(self, step_id: str):
        self._active = step_id
        idx = STEP_ORDER.index(step_id) if step_id in STEP_ORDER else -1
        for i, sid in enumerate(STEP_ORDER):
            if sid == step_id:
                self._states[sid] = "active"
            elif i < idx:
                if self._states[sid] != "error":
                    self._states[sid] = "completed"
            else:
                if self._states[sid] != "error":
                    self._states[sid] = "pending"
        self.update()

    def reset(self):
        self._states = {sid: "pending" for sid in STEP_ORDER}
        self._active = ""
        self.update()

    # -- 几何 ---------------------------------------------------------------
    def _node_x(self, index: int) -> float:
        n = len(STEP_ORDER)
        if n == 1:
            return self.width() / 2.0
        span = self.width() - self.PAD_X * 2
        return self.PAD_X + span * index / (n - 1)

    def _center_y(self) -> float:
        return 30.0

    # -- 交互 ---------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            for i, sid in enumerate(STEP_ORDER):
                if abs(event.pos().x() - self._node_x(i)) < 26:
                    self.step_clicked.emit(sid)
                    break
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        hover = ""
        for i, sid in enumerate(STEP_ORDER):
            if abs(event.pos().x() - self._node_x(i)) < 26:
                hover = sid
                break
        if hover != self._hover:
            self._hover = hover
            self.setToolTip(STEP_HINTS.get(hover, "") if hover else "")
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover = ""
        self.update()
        super().leaveEvent(event)

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        y = self._center_y()
        n = len(STEP_ORDER)
        active_idx = STEP_ORDER.index(self._active) if self._active in STEP_ORDER else -1

        # 轨道
        track_pen = QPen(QColor(T.TRACK), 2.0)
        track_pen.setCapStyle(Qt.RoundCap)
        p.setPen(track_pen)
        p.drawLine(QPointF(self._node_x(0), y), QPointF(self._node_x(n - 1), y))

        # 已完成部分用主色实线覆盖
        done_upto = -1
        for i, sid in enumerate(STEP_ORDER):
            if self._states[sid] in ("completed",) or (active_idx >= 0 and i < active_idx):
                done_upto = i
            elif self._states[sid] == "error":
                done_upto = max(done_upto, i - 1)
        if done_upto >= 0:
            done_pen = QPen(QColor(T.TINT), 2.0)
            done_pen.setCapStyle(Qt.RoundCap)
            p.setPen(done_pen)
            p.drawLine(QPointF(self._node_x(0), y), QPointF(self._node_x(done_upto), y))

        # 播出指示（仅在执行中）
        if active_idx >= 0 and self._states[self._active] == "active":
            ax = self._node_x(active_idx)
            head_pen = QPen(QColor(T.LIVE), 1.4)
            head_pen.setCapStyle(Qt.RoundCap)
            p.setPen(head_pen)
            p.drawLine(QPointF(ax, y - 26), QPointF(ax, y - 12))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.LIVE))
            path = QPainterPath(QPointF(ax - 3.6, y - 13.5))
            path.lineTo(ax + 3.6, y - 13.5)
            path.lineTo(ax, y - 9.0)
            path.closeSubpath()
            p.drawPath(path)

        # 节点
        label_font = QFont()
        label_font.setPixelSize(T.SIZE_SUB)
        num_font = QFont()
        num_font.setPixelSize(9)
        num_font.setFamily("Consolas")

        for i, sid in enumerate(STEP_ORDER):
            x = self._node_x(i)
            state = self._states[sid]
            hovered = sid == self._hover

            if state == "active":
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(236, 243, 253))
                p.drawEllipse(QPointF(x, y), self.NODE_R + 5, self.NODE_R + 5)
                p.setBrush(QColor(T.TINT))
                p.setPen(QPen(QColor(T.TINT), 1.0))
                p.drawEllipse(QPointF(x, y), self.NODE_R, self.NODE_R)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#FFFFFF"))
                p.drawEllipse(QPointF(x, y), 3.0, 3.0)
            elif state == "completed":
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T.TINT))
                p.drawEllipse(QPointF(x, y), self.NODE_R, self.NODE_R)
                paint_icon(p, "check", QRectF(x - 5, y - 5, 10, 10), "#FFFFFF", 2.4)
            elif state == "error":
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T.LIVE))
                p.drawEllipse(QPointF(x, y), self.NODE_R, self.NODE_R)
                paint_icon(p, "x", QRectF(x - 5, y - 5, 10, 10), "#FFFFFF", 2.4)
            else:
                p.setPen(QPen(QColor(T.FIELD_BORDER_HOVER if hovered else "#D2D2D9"), 1.4))
                p.setBrush(QColor("#FFFFFF"))
                p.drawEllipse(QPointF(x, y), self.NODE_R, self.NODE_R)

            # 编号（cue 序号）
            p.setFont(num_font)
            p.setPen(QColor(T.TEXT_TERTIARY if state == "pending" else T.TEXT_SECONDARY))
            p.drawText(
                QRectF(x - 16, y - 24, 32, 12),
                Qt.AlignCenter,
                f"{i + 1:02d}",
            )

            # 名称
            f = QFont(label_font)
            f.setWeight(QFont.DemiBold if state == "active" else QFont.Normal)
            p.setFont(f)
            color = {
                "active": T.TEXT_PRIMARY,
                "completed": T.TEXT_SECONDARY,
                "error": T.LIVE,
            }.get(state, T.TEXT_TERTIARY)
            p.setPen(QColor(color))
            p.drawText(QRectF(x - 34, y + 14, 68, 16), Qt.AlignCenter, STEP_LABELS[sid])

            if state == "completed":
                p.setFont(num_font)
                p.setPen(QColor(T.TEXT_TERTIARY))
                p.drawText(QRectF(x - 34, y + 29, 68, 12), Qt.AlignCenter, "完成")
            elif state == "error":
                p.setFont(num_font)
                p.setPen(QColor(T.LIVE))
                p.drawText(QRectF(x - 34, y + 29, 68, 12), Qt.AlignCenter, "中断")

        p.end()


class PipelineWidget(QFrame):
    step_clicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 10)
        root.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("处理流程")
        title.setObjectName("H3")
        header.addWidget(title)

        self._caption = QLabel("等待文件")
        self._caption.setStyleSheet(
            f"color: {T.TEXT_TERTIARY}; font-size: {T.SIZE_CAPTION}px; background: transparent;"
        )
        header.addWidget(self._caption, 0, Qt.AlignVCenter)
        header.addStretch(1)
        root.addLayout(header)

        self._rail = _Rail()
        self._rail.step_clicked.connect(self.step_clicked.emit)
        root.addWidget(self._rail)

    # -- 兼容原有调用 -------------------------------------------------------
    def set_current_step(self, step_id: str):
        self._rail.set_active(step_id)
        for sid in STEP_ORDER:
            if sid == step_id:
                self._caption.setText(f"正在执行 · {STEP_HINTS.get(sid, '')}")
                break

    def set_step_state(self, step_id: str, state: str):
        self._rail.set_state(step_id, state)
        if state == "completed" and step_id == STEP_ORDER[-1]:
            self._caption.setText("全部环节已完成")
        elif state == "error":
            self._caption.setText("执行中断，请查看日志")

    def reset(self):
        self._rail.reset()
        self._caption.setText("等待文件")

    def set_caption(self, text: str):
        self._caption.setText(text)
