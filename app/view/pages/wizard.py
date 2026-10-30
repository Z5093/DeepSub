"""新手引导。

首次启动自动弹出；配置存在之后，也可以在「设置 → 新手引导」重新打开，
用来重新检查运行环境、补下模型、调整文件落盘位置。

每一步只做一件事：环境自检 → 识别模型 → 文件位置 → 翻译服务 → 完成。
"""

import os

from PyQt5.QtCore import QRectF, Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar,
    QStackedWidget, QVBoxLayout, QWidget,
)

from app.common.config import config_manager
from app.common.constants import (
    MODEL_SOURCE_HUGGINGFACE, MODEL_SOURCE_MODELSCOPE, WHISPER_MODELS,
)
from app.common.signal_bus import signal_bus
from app.common.theme import UITheme as T
from app.components.buttons import Button, Switch
from app.components.icons import Icon
from app.components.panels import Badge, Card, hairline
from app.components.widgets import CheckBox, Field, Form, Label, Select, TextField
from app.core.asr.program_manager import FasterWhisperManager
from app.thread.model_download_thread import ModelDownloadThread


class _WizardProgramDownloadThread(QThread):
    progress = pyqtSignal(int)
    completed = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, version, manager, parent=None):
        super().__init__(parent)
        self._version = version
        self._manager = manager

    def run(self):
        try:
            result = self._manager.download_program(
                self._version,
                progress_callback=lambda pct: self.progress.emit(int(pct)),
            )
            if result:
                self.completed.emit()
            else:
                self.failed.emit("下载失败")
        except Exception as e:
            self.failed.emit(str(e))


class _StepDots(QWidget):
    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self._count = count
        self._current = 0
        self.setFixedSize(count * 16, 8)

    def set_current(self, index: int):
        self._current = index
        self.update()

    def paintEvent(self, event):
        from PyQt5.QtGui import QColor, QPainter

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        for i in range(self._count):
            p.setBrush(QColor(T.TINT if i == self._current else "#D2D2D9"))
            p.drawEllipse(QRectF(i * 16, 2.5, 6, 6))
        p.end()


class _ToolRow(QWidget):
    """一行工具自检结果：名称 + 状态徽标 + 位置说明。"""

    def __init__(self, title: str, icon_name: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        lay.addWidget(Icon(icon_name, 16, T.TEXT_SECONDARY))

        name = Label(title, "body")
        name.setFixedWidth(84)
        lay.addWidget(name)

        self._badge = Badge("检测中", "neutral")
        lay.addWidget(self._badge)

        self._detail = Label("", "hint")
        lay.addWidget(self._detail, 1)

    def set_result(self, ok: bool, detail: str):
        self._badge.set_tone("success" if ok else "warning", "已就绪" if ok else "未找到")
        self._detail.setText(detail)
        self.setToolTip(detail)


class WizardPageBase(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        pass

    def get_title(self) -> str:
        return ""

    def load(self):
        """从配置回填界面（重新打开引导时不清空已有设置）。"""

    def save(self):
        """把界面值写回配置。"""

    def on_enter(self):
        """切换到本页时调用（只做刷新，不改用户输入）。"""


class WelcomePage(WizardPageBase):
    def get_title(self):
        return "欢迎"

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 28)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignCenter)

        title = QLabel("DeepSub")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color: {T.TEXT_PRIMARY}; font-family: {T.FONT_DISPLAY}; font-size: 38px;"
            " font-weight: 600; background: transparent;"
        )
        layout.addWidget(title)

        subtitle = QLabel("把视频变成准确的双语字幕，全程在本机完成")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; font-size: {T.SIZE_HEADLINE}px; background: transparent;"
        )
        layout.addWidget(subtitle)

        layout.addSpacing(18)

        steps = [
            ("1", "检查运行环境", "ffmpeg、yt-dlp 随程序附带；识别程序按显卡情况选 GPU 或 CPU。"),
            ("2", "准备识别模型", "模型越大越准，也越吃内存；可以先装 small 试跑，之后随时再补。"),
            ("3", "确定文件位置", "下载的视频放哪、生成的字幕放哪，一页就能设好。"),
        ]
        for index, name, desc in steps:
            card = Card()
            card.setFixedWidth(470)
            card.setMinimumHeight(72)
            row = QHBoxLayout()
            row.setSpacing(12)
            num = QLabel(index)
            num.setFixedSize(22, 22)
            num.setAlignment(Qt.AlignCenter)
            num.setStyleSheet(
                f"color: {T.TINT}; background: {T.TINT_SOFT}; border-radius: 11px;"
                f" font-size: {T.SIZE_CAPTION}px; font-weight: 600;"
            )
            row.addWidget(num, 0, Qt.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(Label(name, "body"))
            d = Label(desc, "muted")
            d.setWordWrap(True)
            text.addWidget(d)
            row.addLayout(text, 1)
            card.add_layout(row)
            layout.addWidget(card, 0, Qt.AlignHCenter)

        layout.addSpacing(6)
        note = Label("这些内容之后都能在「设置 → 新手引导」重新打开。", "hint")
        note.setAlignment(Qt.AlignCenter)
        layout.addWidget(note)


class EnvPage(WizardPageBase):
    """运行环境 + 识别程序安装。"""

    def get_title(self):
        return "运行环境"

    def _setup_ui(self):
        self._manager = FasterWhisperManager()
        self._program_download_thread = None
        self._checked = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 8, 32, 8)
        layout.setSpacing(0)

        form = Form()

        tools = Card("随程序附带的工具", "不需要单独安装；缺失时下载与音频提取会失败")
        self._row_ffmpeg = _ToolRow("ffmpeg", "layers")
        self._row_ffprobe = _ToolRow("ffprobe", "search")
        self._row_ytdlp = _ToolRow("yt-dlp", "download")
        for row in (self._row_ffmpeg, self._row_ffprobe, self._row_ytdlp):
            tools.add_widget(row)
        self._recheck_btn = Button("重新检测", variant="ghost")
        self._recheck_btn.clicked.connect(self._refresh_tools)
        tools.add_header_widget(self._recheck_btn)
        form.add_widget(tools)

        program = Card("识别程序", "FasterWhisper 是识别引擎本体；GPU 版本需要 NVIDIA 显卡")
        self._gpu_download_btn = Button("下载 GPU 版本", variant="primary")
        self._gpu_download_btn.clicked.connect(lambda: self._download_program("gpu"))
        self._gpu_status_label = Label("", "muted")
        self._gpu_status_label.hide()
        self._gpu_delete_btn = Button("删除", variant="danger")
        self._gpu_delete_btn.hide()
        self._gpu_delete_btn.clicked.connect(lambda: self._on_program_delete("gpu"))
        program.add_widget(self._make_program_row(
            "GPU 版本", self._gpu_download_btn, self._gpu_status_label, self._gpu_delete_btn))

        self._cpu_download_btn = Button("下载 CPU 版本")
        self._cpu_download_btn.clicked.connect(lambda: self._download_program("cpu"))
        self._cpu_status_label = Label("", "muted")
        self._cpu_status_label.hide()
        self._cpu_delete_btn = Button("删除", variant="danger")
        self._cpu_delete_btn.hide()
        self._cpu_delete_btn.clicked.connect(lambda: self._on_program_delete("cpu"))
        program.add_widget(self._make_program_row(
            "CPU 版本", self._cpu_download_btn, self._cpu_status_label, self._cpu_delete_btn))

        prog_row = QHBoxLayout()
        prog_row.setSpacing(10)
        self._program_progress = QProgressBar()
        self._program_progress.setRange(0, 100)
        self._program_progress.setValue(0)
        self._program_progress.setTextVisible(False)
        self._program_progress.setFixedHeight(6)
        self._program_progress.hide()
        prog_row.addWidget(self._program_progress, 1)
        self._program_status_label = Label("", "hint")
        self._program_status_label.hide()
        prog_row.addWidget(self._program_status_label)
        program.add_layout(prog_row)
        form.add_widget(program)

        mode = Card("运行方式")
        self._asr_mode_combo = Select()
        self._asr_mode_combo.addItems(["自动检测", "GPU 模式", "CPU 模式"])
        mode.add_widget(Field("优先使用", self._asr_mode_combo, hint="显存不足时自动回退", label_width=132))
        self._asr_source_combo = Select()
        self._asr_source_combo.addItems(["ModelScope", "HuggingFace"])
        mode.add_widget(Field("程序下载源", self._asr_source_combo, hint="国内建议用 ModelScope",
                              label_width=132))
        form.add_widget(mode)

        layout.addWidget(form)

    def _make_program_row(self, title: str, button: Button, status: Label, delete_btn: Button) -> QWidget:
        controls = QWidget()
        lay = QHBoxLayout(controls)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(button)
        lay.addWidget(status, 1)
        lay.addWidget(delete_btn)
        lay.addStretch(0)
        return Field(title, controls, label_width=96)

    # -- 自检 ---------------------------------------------------------------
    def on_enter(self):
        if not self._checked:
            self._checked = True
            self._refresh_tools()
        self._refresh_program_status()

    @staticmethod
    def _short(path: str) -> str:
        from app.core.utils.file_utils import get_project_root

        try:
            if path.lower().startswith(get_project_root().lower()):
                return os.path.relpath(path, get_project_root())
        except Exception:
            pass
        return path

    def _refresh_tools(self):
        from app.core.utils.ffmpeg_utils import get_ffmpeg_path, get_ffprobe_path
        from app.core.utils.ytdlp_utils import get_ytdlp_path, get_ytdlp_version

        path = get_ffmpeg_path()
        self._row_ffmpeg.set_result(os.path.isfile(path), self._short(path))

        path = get_ffprobe_path()
        self._row_ffprobe.set_result(os.path.isfile(path), self._short(path))

        path = get_ytdlp_path()
        if os.path.isfile(path):
            try:
                version = get_ytdlp_version(path)
            except Exception:
                version = ""
            self._row_ytdlp.set_result(True, f"v{version}" if version else self._short(path))
        else:
            self._row_ytdlp.set_result(False, "下载功能不可用")

    def _refresh_program_status(self):
        info = self._manager.get_installed_version()
        self._apply_program_state("gpu", info["gpu_installed"], info["gpu_version"])
        self._apply_program_state("cpu", info["cpu_installed"], info["cpu_version"])

    def _apply_program_state(self, version: str, installed: bool, ver: str):
        if version == "gpu":
            btn, status, delete = self._gpu_download_btn, self._gpu_status_label, self._gpu_delete_btn
            default_text = "下载 GPU 版本"
        else:
            btn, status, delete = self._cpu_download_btn, self._cpu_status_label, self._cpu_delete_btn
            default_text = "下载 CPU 版本"

        if installed:
            ver = (ver or "").strip()
            if ver and not any(ch.isalnum() for ch in ver):
                ver = ""
            status.setText("已安装" + (f" · v{ver}" if ver else ""))
            status.show()
            btn.hide()
            delete.show()
        else:
            status.hide()
            btn.show()
            btn.setEnabled(True)
            btn.setText(default_text)
            delete.hide()

    # -- 下载 ---------------------------------------------------------------
    def _download_program(self, version):
        if self._program_download_thread and self._program_download_thread.isRunning():
            return
        btn = self._gpu_download_btn if version == "gpu" else self._cpu_download_btn
        btn.setEnabled(False)
        btn.setText("下载中…")
        self._program_progress.setValue(0)
        self._program_progress.show()
        self._program_status_label.show()
        self._program_status_label.setText("连接中…")
        self._program_download_thread = _WizardProgramDownloadThread(version, self._manager, self)
        self._program_download_thread.progress.connect(self._on_program_progress)
        self._program_download_thread.completed.connect(lambda: self._on_program_completed(version))
        self._program_download_thread.failed.connect(self._on_program_failed)
        self._program_download_thread.start()

    def _on_program_progress(self, pct):
        self._program_progress.setValue(pct)
        self._program_status_label.setText(f"{pct}%")

    def _on_program_completed(self, version):
        self._program_progress.setValue(100)
        self._program_progress.hide()
        self._program_status_label.hide()
        self._refresh_program_status()

    def _on_program_failed(self, error):
        self._program_progress.setValue(0)
        self._program_progress.hide()
        self._program_status_label.show()
        self._program_status_label.setText("下载失败，可稍后在设置里重试")
        self._gpu_download_btn.setEnabled(True)
        self._gpu_download_btn.setText("下载 GPU 版本")
        self._cpu_download_btn.setEnabled(True)
        self._cpu_download_btn.setText("下载 CPU 版本")

    def _on_program_delete(self, version: str):
        self._manager.remove_program(version)
        self._refresh_program_status()

    # -- 配置 ---------------------------------------------------------------
    def load(self):
        cfg = config_manager.get_section("asr")
        mode_map = {"auto": 0, "gpu": 1, "cpu": 2}
        self._asr_mode_combo.setCurrentIndex(mode_map.get(cfg.get("gpu_mode", "auto"), 0))
        source_map = {"modelscope": 0, "huggingface": 1}
        self._asr_source_combo.setCurrentIndex(source_map.get(cfg.get("model_source", "modelscope"), 0))

    def save(self):
        mode_map = {0: "auto", 1: "gpu", 2: "cpu"}
        config_manager.set("asr", "gpu_mode", mode_map.get(self._asr_mode_combo.currentIndex(), "auto"))
        source_map = {0: "modelscope", 1: "huggingface"}
        config_manager.set("asr", "model_source",
                           source_map.get(self._asr_source_combo.currentIndex(), "modelscope"))


class ModelPage(WizardPageBase):
    def get_title(self):
        return "识别模型"

    def _setup_ui(self):
        self._model_download_thread = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 8, 32, 8)
        layout.setSpacing(0)

        form = Form()

        card = Card("Whisper 模型", "同一次识别只用一个；模型文件保存在 models/faster-whisper/ 下")
        self._model_combo = Select()
        for model_id, info in WHISPER_MODELS.items():
            self._model_combo.addItem(f"{info['label']} · {info['size_mb']} MB · {self._note(model_id)}",
                                      model_id)
        self._model_combo.setMinimumWidth(360)
        self._model_combo.currentIndexChanged.connect(lambda _: self._refresh_status())
        card.add_widget(Field("选择模型", self._model_combo))

        self._source_combo = Select()
        self._source_combo.addItems(["ModelScope（国内）", "HuggingFace（国际）"])
        card.add_widget(Field("下载源", self._source_combo))

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._model_download_btn = Button("下载模型", variant="primary")
        self._model_download_btn.clicked.connect(self._download_model)
        btn_row.addWidget(self._model_download_btn)
        self._status_label = Label("未下载", "hint")
        btn_row.addWidget(self._status_label, 1)
        card.add_layout(btn_row)

        self._model_progress = QProgressBar()
        self._model_progress.setRange(0, 100)
        self._model_progress.setValue(0)
        self._model_progress.setTextVisible(False)
        self._model_progress.setFixedHeight(6)
        self._model_progress.hide()
        card.add_widget(self._model_progress)
        form.add_widget(card)

        tip = Card("选哪个")
        tips = Label(
            "small · 488 MB　先用它跑通流程，长视频也扛得住\n"
            "medium · 1.5 GB　精度更好，需要 4 GB 以上空闲内存\n"
            "large-v3-turbo · 1.7 GB　精度接近 large，速度更快，推荐\n"
            "large-v3 · 3.1 GB　最高精度；8 GB 内存的机器很难跑动",
            "muted",
        )
        tips.setWordWrap(True)
        tip.add_widget(tips)
        form.add_widget(tip)

        layout.addWidget(form)

    @staticmethod
    def _note(model_id: str) -> str:
        return {
            "tiny": "最快，验证流程用",
            "base": "速度与精度折中",
            "small": "推荐入门",
            "medium": "高精度",
            "large-v1": "旧版",
            "large-v2": "旧版",
            "large-v3": "最高精度",
            "large-v3-turbo": "推荐",
        }.get(model_id, "")

    def on_enter(self):
        self._refresh_status()

    def _installed(self, model_id: str) -> bool:
        from app.core.utils.file_utils import get_whisper_models_dir

        return os.path.exists(os.path.join(get_whisper_models_dir(), model_id, "model.bin"))

    def _refresh_status(self):
        model_id = self._model_combo.currentData()
        if not model_id:
            return
        if self._installed(model_id):
            self._status_label.setText("已安装，可以直接使用")
            self._model_progress.hide()
            self._model_download_btn.hide()
        else:
            self._status_label.setText("未下载")
            self._model_progress.hide()
            self._model_download_btn.show()
            self._model_download_btn.setEnabled(True)
            self._model_download_btn.setText("下载模型")

    def _download_model(self):
        if self._model_download_thread and self._model_download_thread.isRunning():
            return
        model_id = self._model_combo.currentData()
        if not model_id:
            return
        source_map = {0: MODEL_SOURCE_MODELSCOPE, 1: MODEL_SOURCE_HUGGINGFACE}
        source = source_map.get(self._source_combo.currentIndex(), MODEL_SOURCE_MODELSCOPE)
        self._model_download_btn.setEnabled(False)
        self._model_download_btn.setText("下载中…")
        self._model_progress.setValue(0)
        self._model_progress.show()
        self._status_label.setText("连接中…")
        self._model_download_thread = ModelDownloadThread(model_id, source, parent=self)
        self._model_download_thread.progress.connect(self._on_model_progress)
        self._model_download_thread.completed.connect(self._on_model_completed)
        self._model_download_thread.failed.connect(self._on_model_failed)
        self._model_download_thread.start()

    def _on_model_progress(self, model_id, pct):
        self._model_progress.setValue(pct)
        self._status_label.setText(f"{pct}%")

    def _on_model_completed(self, model_id):
        self._model_progress.setValue(100)
        self._model_progress.hide()
        self._status_label.setText("已安装，可以直接使用")
        self._model_download_btn.hide()
        signal_bus.model_download_completed.emit(model_id)
        signal_bus.model_installed.emit(model_id)

    def _on_model_failed(self, model_id, error):
        self._model_progress.setValue(0)
        self._model_progress.hide()
        self._status_label.setText("下载失败，可稍后重试")
        self._model_download_btn.setEnabled(True)
        self._model_download_btn.setText("重试")
        signal_bus.model_download_failed.emit(model_id, error)

    def load(self):
        cfg = config_manager.get_section("asr")
        idx = self._model_combo.findData(cfg.get("model", "large-v3"))
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        source_map = {"modelscope": 0, "huggingface": 1}
        self._source_combo.setCurrentIndex(source_map.get(cfg.get("model_source", "modelscope"), 0))

    def save(self):
        model_id = self._model_combo.currentData()
        if model_id:
            config_manager.set("asr", "model", model_id)


class FilesPage(WizardPageBase):
    def get_title(self):
        return "文件位置"

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 8, 32, 8)
        layout.setSpacing(0)

        form = Form()

        download_card = Card("下载的视频放在哪", "留空则使用程序目录下的 downloads/")
        row = QHBoxLayout()
        row.setSpacing(8)
        self._download_path_input = TextField("默认：程序目录 / downloads")
        self._download_path_input.textChanged.connect(self._update_example)
        row.addWidget(self._download_path_input, 1)
        browse = Button("浏览…")
        browse.clicked.connect(self._on_browse)
        row.addWidget(browse)
        download_card.add_layout(row)
        form.add_widget(download_card)

        output_card = Card("生成的字幕放在哪", "会在视频所在文件夹里建同名子目录，原文件不动")
        self._output_subdir_input = TextField("DeepSub")
        self._output_subdir_input.textChanged.connect(self._update_example)
        output_card.add_widget(Field("输出子目录", self._output_subdir_input))

        output_card.add_widget(Label("输出格式（可多选）", "section"))
        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(20)
        self._srt_check = CheckBox("SRT")
        self._srt_check.setChecked(True)
        self._ass_check = CheckBox("ASS")
        self._vtt_check = CheckBox("VTT")
        for cb in (self._srt_check, self._ass_check, self._vtt_check):
            cb.toggled.connect(self._update_example)
            fmt_row.addWidget(cb)
        fmt_row.addStretch(1)
        output_card.add_layout(fmt_row)

        output_card.add_widget(hairline())
        self._example = Label("", "mono")
        self._example.setWordWrap(True)
        output_card.add_widget(self._example)
        form.add_widget(output_card)

        note = Card("说明")
        for line in (
            "中间结果（01_原始字幕.json 等）也放在同一个子目录，方便回看每一步。",
            "字幕按语言代码命名：影片.zh-CN.srt、影片.en-US_zh-CN.srt。",
        ):
            lb = Label("· " + line, "muted")
            lb.setWordWrap(True)
            note.add_widget(lb)
        form.add_widget(note)

        layout.addWidget(form)

    def _on_browse(self):
        from app.core.utils.file_utils import get_downloads_root

        path = QFileDialog.getExistingDirectory(self, "选择下载根目录", get_downloads_root())
        if path:
            self._download_path_input.setText(path)

    def _update_example(self):
        from app.core.utils.file_utils import get_downloads_root

        root = self._download_path_input.text().strip() or get_downloads_root()
        subdir = self._output_subdir_input.text().strip() or "DeepSub"
        fmt = "ass" if (self._ass_check.isChecked() and not self._srt_check.isChecked()) else "srt"
        self._example.setText(
            f"视频 → {os.path.join(root, '影片.mp4')}\n"
            f"字幕 → {os.path.join('…', '影片', subdir, '影片.zh-CN.' + fmt)}"
        )

    def load(self):
        self._download_path_input.setText(config_manager.get("general", "download_path", ""))
        self._output_subdir_input.setText(config_manager.get("general", "output_subdir", "DeepSub"))
        formats = config_manager.get("general", "output_formats", ["srt"])
        self._srt_check.setChecked("srt" in formats)
        self._ass_check.setChecked("ass" in formats)
        self._vtt_check.setChecked("vtt" in formats)
        self._update_example()

    def save(self):
        config_manager.set("general", "download_path", self._download_path_input.text().strip())
        config_manager.set("general", "output_subdir",
                           self._output_subdir_input.text().strip() or "DeepSub")
        formats = []
        if self._srt_check.isChecked():
            formats.append("srt")
        if self._ass_check.isChecked():
            formats.append("ass")
        if self._vtt_check.isChecked():
            formats.append("vtt")
        config_manager.set("general", "output_formats", formats or ["srt"])


class TranslatePage(WizardPageBase):
    def get_title(self):
        return "翻译服务"

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 8, 32, 8)
        layout.setSpacing(0)

        form = Form()

        toggle = Card("是否需要翻译")
        self._enable_check = Switch("生成译文（关闭则只输出原文字幕）")
        self._enable_check.setChecked(True)
        self._enable_check.toggled.connect(self._on_enable_toggled)
        toggle.add_widget(self._enable_check)
        form.add_widget(toggle)

        api = Card("翻译服务", "支持任意 OpenAI 兼容接口：DeepSeek、通义千问、本地 vLLM 等")
        self._api_url_input = TextField("https://api.deepseek.com/v1", mono=True)
        api.add_widget(Field("接口地址", self._api_url_input))
        self._api_key_input = TextField("sk-…", mono=True)
        self._api_key_input.setEchoMode(TextField.Password)
        api.add_widget(Field("密钥", self._api_key_input))
        self._model_input = TextField("deepseek-chat", mono=True)
        api.add_widget(Field("模型", self._model_input))
        form.add_widget(api)
        self._api_card = api

        param = Card("翻译参数", "串行 + 上下文窗口通常能换来最稳的表达")
        self._concurrency_combo = Select()
        self._concurrency_combo.addItems(["1", "2", "3", "5", "10"])
        param.add_widget(Field("并发度", self._concurrency_combo, hint="大于 1 时关闭窗口",
                               label_width=132))

        self._window_combo = Select()
        self._window_combo.addItems(["1", "3", "5", "10"])
        param.add_widget(Field("窗口条数", self._window_combo))

        self._context_combo = Select()
        self._context_combo.addItems(["0", "1", "2", "3", "5"])
        param.add_widget(Field("上下文条数", self._context_combo))
        form.add_widget(param)
        self._param_card = param

        hint = Label("想完全离线翻译，可以在「翻译引擎」页面配置 llama.cpp，之后再切过去。", "hint")
        hint.setWordWrap(True)
        form.add_widget(hint)

        layout.addWidget(form)

    def _on_enable_toggled(self, checked: bool):
        self._api_card.setEnabled(checked)
        self._param_card.setEnabled(checked)

    def load(self):
        cfg = config_manager.get_section("translate")
        self._enable_check.setChecked(cfg.get("enable_translation", True))
        self._on_enable_toggled(self._enable_check.isChecked())
        self._api_url_input.setText(cfg.get("api_base_url", ""))
        self._api_key_input.setText(cfg.get("api_key", ""))
        self._model_input.setText(cfg.get("model", ""))
        self._concurrency_combo.setCurrentText(str(cfg.get("concurrency", 1)))
        self._window_combo.setCurrentText(str(cfg.get("context_window_size", 3)))
        self._context_combo.setCurrentText(str(cfg.get("context_size", 1)))

    def save(self):
        config_manager.set("translate", "enable_translation", self._enable_check.isChecked())
        config_manager.set("translate", "api_base_url", self._api_url_input.text().strip())
        config_manager.set("translate", "api_key", self._api_key_input.text().strip())
        config_manager.set("translate", "model", self._model_input.text().strip())
        for key, combo, default in (
            ("concurrency", self._concurrency_combo, 1),
            ("context_window_size", self._window_combo, 3),
            ("context_size", self._context_combo, 1),
        ):
            try:
                config_manager.set("translate", key, int(combo.currentText()))
            except ValueError:
                config_manager.set("translate", key, default)


class CompletePage(WizardPageBase):
    """收尾页：只把这次定下来的几项摆出来，供用户确认。"""

    ROWS = [
        ("mode", "运行方式"),
        ("model", "识别模型"),
        ("subdir", "输出子目录"),
        ("format", "输出格式"),
        ("translate", "翻译"),
    ]

    def get_title(self):
        return "完成"

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 28, 40, 28)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignCenter)

        badge = QFrame()
        badge.setFixedSize(56, 56)
        badge.setStyleSheet(f"background-color: {T.SUCCESS_SOFT}; border-radius: 28px;")
        inner = QVBoxLayout(badge)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.addWidget(Icon("check", 26, T.SUCCESS), 0, Qt.AlignCenter)
        layout.addWidget(badge, 0, Qt.AlignHCenter)

        title = QLabel("可以开始了")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color: {T.TEXT_PRIMARY}; font-family: {T.FONT_DISPLAY}; font-size: {T.SIZE_TITLE}px;"
            " font-weight: 600; background: transparent;"
        )
        layout.addWidget(title)

        desc = QLabel("把视频拖进工作台，识别 → 分段 → 翻译 → 对齐 → 导出 会依次执行。")
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; font-size: {T.SIZE_BODY}px; background: transparent;"
        )
        layout.addWidget(desc)

        # 摘要卡片一次建好，切换回来只改数值，避免反复重建导致的布局高度问题
        self._summary = Card("本次设置")
        self._summary.setFixedWidth(520)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        grid.setColumnMinimumWidth(0, 92)
        grid.setColumnStretch(1, 1)

        self._values = {}
        for row, (key, name) in enumerate(self.ROWS):
            grid.addWidget(Label(name, "muted"), row, 0, Qt.AlignLeft | Qt.AlignVCenter)
            value = Label("—", "body")
            grid.addWidget(value, row, 1)
            self._values[key] = value
        self._summary.add_layout(grid)

        wrapper = QHBoxLayout()
        wrapper.addStretch(1)
        wrapper.addWidget(self._summary)
        wrapper.addStretch(1)
        layout.addLayout(wrapper)

    def on_enter(self):
        gen = config_manager.get_section("general")
        asr = config_manager.get_section("asr")
        tr = config_manager.get_section("translate")

        mode_map = {"auto": "自动检测（优先 GPU）", "gpu": "GPU (CUDA)", "cpu": "CPU"}
        model_id = asr.get("model", "")
        model = WHISPER_MODELS.get(model_id, {}).get("label", model_id or "—")
        formats = " / ".join(f.upper() for f in gen.get("output_formats", ["srt"])) or "SRT"

        self._values["mode"].setText(mode_map.get(asr.get("gpu_mode", "auto"), "自动检测（优先 GPU）"))
        self._values["model"].setText(model)
        self._values["subdir"].setText(gen.get("output_subdir", "DeepSub"))
        self._values["format"].setText(formats)
        self._values["translate"].setText("已开启" if tr.get("enable_translation", True) else "已关闭")


class SetupWizard(QFrame):
    finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_page = 0
        self._setup_ui()
        self._load_all()
        self._on_page_changed(0)

    def _setup_ui(self):
        self.setStyleSheet(f"SetupWizard {{ background-color: {T.CANVAS}; border: none; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._pages = [
            WelcomePage(),
            EnvPage(),
            ModelPage(),
            FilesPage(),
            TranslatePage(),
            CompletePage(),
        ]

        header = QWidget()
        header.setObjectName("WizardHeader")
        header.setFixedHeight(52)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 0, 20, 0)
        header_layout.setSpacing(10)

        title = QLabel("新手引导")
        title.setStyleSheet(
            f"color: {T.TEXT_PRIMARY}; font-size: {T.SIZE_BODY}px; font-weight: 600;"
            " background: transparent;"
        )
        header_layout.addWidget(title)

        self._page_title = QLabel("")
        self._page_title.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; font-size: {T.SIZE_CAPTION}px; background: transparent;"
        )
        header_layout.addWidget(self._page_title)
        header_layout.addStretch(1)

        self._dots = _StepDots(len(self._pages))
        header_layout.addWidget(self._dots)

        self._step_label = QLabel(f"第 1 步，共 {len(self._pages)} 步")
        self._step_label.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; font-size: {T.SIZE_CAPTION}px; background: transparent;"
        )
        header_layout.addWidget(self._step_label)
        layout.addWidget(header)

        self._stack = QStackedWidget()
        for page in self._pages:
            self._stack.addWidget(page)
        self._stack.currentChanged.connect(self._on_page_changed)
        layout.addWidget(self._stack, 1)

        footer = QWidget()
        footer.setObjectName("WizardFooter")
        footer.setFixedHeight(56)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 8, 20, 8)
        footer_layout.setSpacing(8)

        self._skip_btn = Button("跳过", variant="ghost")
        self._skip_btn.setToolTip("保存当前设置并关闭；之后可在「设置 → 新手引导」重新打开")
        self._skip_btn.clicked.connect(self._on_finish)
        footer_layout.addWidget(self._skip_btn)

        self._back_btn = Button("上一步")
        self._back_btn.clicked.connect(self._go_back)
        self._back_btn.setVisible(False)
        footer_layout.addWidget(self._back_btn)
        footer_layout.addStretch(1)

        self._next_btn = Button("继续", variant="primary")
        self._next_btn.clicked.connect(self._go_next)
        footer_layout.addWidget(self._next_btn)
        layout.addWidget(footer)

    # -- 配置 ---------------------------------------------------------------
    def _load_all(self):
        for page in self._pages:
            try:
                page.load()
            except Exception:
                pass

    def _save_all(self):
        for page in self._pages:
            try:
                page.save()
            except Exception:
                pass
        config_manager.apply()

    # -- 导航 ---------------------------------------------------------------
    def _go_next(self):
        if self._current_page < len(self._pages) - 1:
            self._current_page += 1
            self._stack.setCurrentIndex(self._current_page)
        else:
            self._on_finish()

    def _go_back(self):
        if self._current_page > 0:
            self._current_page -= 1
            self._stack.setCurrentIndex(self._current_page)

    def _on_finish(self):
        self._save_all()
        signal_bus.wizard_completed.emit()
        self.finished.emit()

    def _update_nav(self):
        total = len(self._pages)
        self._back_btn.setVisible(self._current_page > 0)
        self._skip_btn.setVisible(self._current_page < total - 1)
        self._step_label.setText(f"第 {self._current_page + 1} 步，共 {total} 步")
        self._dots.set_current(self._current_page)
        self._page_title.setText(f"· {self._pages[self._current_page].get_title()}")
        self._next_btn.setText("开始使用" if self._current_page == total - 1 else "继续")

    def _on_page_changed(self, index):
        self._update_nav()
        try:
            self._pages[index].on_enter()
        except Exception:
            pass


class GuideDialog(QDialog):
    """从设置里重新打开引导时用的模态窗口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DeepSub — 新手引导")
        self.setModal(True)
        self.resize(900, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._wizard = SetupWizard(self)
        self._wizard.finished.connect(self.accept)
        layout.addWidget(self._wizard)
