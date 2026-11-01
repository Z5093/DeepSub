import os

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from app.common.config import config_manager
from app.common.constants import (
    MODEL_SOURCE_HUGGINGFACE, MODEL_SOURCE_MODELSCOPE, WHISPER_MODELS,
)
from app.common.signal_bus import signal_bus
from app.common.logger import get_logger
from app.common.theme import UITheme as T
from app.components.buttons import Button
from app.components.panels import Card, StatTile
from app.components.widgets import CheckBox, DecimalField, Field, Form, Label, NumberField, Select
from app.core.asr.program_manager import FasterWhisperManager
from app.core.utils.gpu_utils import detect_cuda
from app.thread.model_download_thread import ModelDownloadThread

_logger = get_logger("asr.page")


def _short_reason(error, limit: int = 48) -> str:
    """把异常压成一行短提示，完整内容留给 tooltip 与日志。"""
    lines = [ln.strip() for ln in str(error).strip().splitlines() if ln.strip()]
    first = lines[0] if lines else "未知错误"
    return first if len(first) <= limit else first[:limit] + "…"


class _ProgramDownloadThread(QThread):
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


class _InitCheckThread(QThread):
    result_ready = pyqtSignal(dict, dict)

    def run(self):
        # 检测失败也要回一次结果，否则界面会一直停在「正在检测…」
        try:
            gpu_info = detect_cuda()
        except Exception:
            gpu_info = {"available": False, "reason": "smi_failed", "nvidia_devices": []}
        try:
            version_info = FasterWhisperManager().get_installed_version()
        except Exception:
            version_info = {
                "gpu_installed": False,
                "cpu_installed": False,
                "gpu_version": "",
                "cpu_version": "",
            }
        self.result_ready.emit(gpu_info, version_info)


class FasterWhisperPage(QWidget):
    _is_downloading = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self._manager = FasterWhisperManager()
        self._download_threads = {}
        self._program_download_thread = None
        self._init_check_thread = None
        self._setup_ui()
        self._connect_signals()
        self._init_config()
        self._refresh_model_status()
        self._start_async_check()

    # ------------------------------------------------------------------ UI
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 12)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        h1 = QLabel("语音识别")
        h1.setObjectName("H2")
        sub = QLabel("FasterWhisper 程序与模型管理；词级时间戳是后续断句与对齐的基础")
        sub.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; font-size: {T.SIZE_SUB}px; background: transparent;"
        )
        titles.addWidget(h1)
        titles.addWidget(sub)
        header.addLayout(titles)
        header.addStretch(1)
        self._detect_btn = Button("重新检测 GPU")
        header.addWidget(self._detect_btn, 0, Qt.AlignVCenter)
        root.addLayout(header)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self._card_cuda = StatTile("CUDA", "检测中")
        self._card_gpu_mem = StatTile("可用显存", "—", "MB")
        self._card_version = StatTile("程序版本", "未安装")
        self._card_status = StatTile("运行状态", "离线")
        for tile in (self._card_cuda, self._card_gpu_mem, self._card_version, self._card_status):
            stats.addWidget(tile)
        root.addLayout(stats)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._make_program_tab(), "程序管理")
        self._tabs.addTab(self._make_model_tab(), "模型管理")
        self._tabs.addTab(self._make_asr_config_tab(), "识别参数")
        root.addWidget(self._tabs, 1)

    def _make_program_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(14)

        install = Card("程序安装", "GPU 版本需要 NVIDIA 显卡与 CUDA 运行时；CPU 版本兼容性更好但速度慢")
        self._gpu_download_btn = Button("下载 GPU 版本", variant="primary")
        self._gpu_status_label = Label("", "muted")
        self._gpu_delete_btn = Button("删除", variant="danger")
        self._gpu_status_label.hide()
        self._gpu_delete_btn.hide()
        install.add_widget(self._make_program_row(
            "GPU 版本", self._gpu_download_btn, self._gpu_status_label, self._gpu_delete_btn,
        ))

        self._cpu_download_btn = Button("下载 CPU 版本")
        self._cpu_status_label = Label("", "muted")
        self._cpu_delete_btn = Button("删除", variant="danger")
        self._cpu_status_label.hide()
        self._cpu_delete_btn.hide()
        install.add_widget(self._make_program_row(
            "CPU 版本", self._cpu_download_btn, self._cpu_status_label, self._cpu_delete_btn,
        ))
        layout.addWidget(install)

        detect = Card("环境检测")
        self._gpu_status = Label("CUDA 状态：正在检测…", "muted")
        self._gpu_status.setWordWrap(True)
        detect.add_widget(self._gpu_status)
        layout.addWidget(detect)

        mode_card = Card("运行模式")
        self._mode_combo = Select()
        self._mode_combo.addItems(["自动检测", "GPU (CUDA)", "CPU"])
        mode_card.add_widget(Field("优先使用", self._mode_combo,
                                   hint="显存不足时会自动回退到 CPU"))
        layout.addWidget(mode_card)

        layout.addStretch(1)
        return widget

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

    def _make_model_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(14)

        card = Card("可用模型", "模型越大越准，也越吃内存；建议先装 small 或 medium 试跑")
        self._source_combo = Select()
        self._source_combo.addItems(["ModelScope（国内）", "HuggingFace（国际）"])
        self._source_combo.setFixedWidth(180)
        card.add_header_widget(Label("下载源", "hint"))
        card.add_header_widget(self._source_combo)

        self._model_table = QTableWidget()
        self._model_table.setColumnCount(5)
        self._model_table.setHorizontalHeaderLabels(["模型", "大小", "状态", "操作", "说明"])
        header = self._model_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self._model_table.setColumnWidth(0, 160)
        self._model_table.setColumnWidth(1, 90)
        self._model_table.setColumnWidth(2, 100)
        self._model_table.setColumnWidth(3, 110)
        self._model_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._model_table.setAlternatingRowColors(True)
        self._model_table.setShowGrid(False)
        self._model_table.verticalHeader().setVisible(False)
        self._model_table.verticalHeader().setDefaultSectionSize(38)

        self._model_table.setRowCount(len(WHISPER_MODELS))
        for row, (model_id, info) in enumerate(WHISPER_MODELS.items()):
            self._model_table.setItem(row, 0, QTableWidgetItem(info["label"]))
            self._model_table.setItem(row, 1, QTableWidgetItem(f"{info['size_mb']} MB"))
            self._model_table.setItem(row, 2, QTableWidgetItem("未安装"))
            self._model_table.setItem(row, 3, QTableWidgetItem(""))
            self._model_table.setItem(row, 4, QTableWidgetItem(self._model_note(model_id)))

        self._model_download_btns = {}
        for row, (model_id, info) in enumerate(WHISPER_MODELS.items()):
            btn = Button("下载")
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda checked=False, mid=model_id: self._on_model_download_clicked(mid))
            holder = QWidget()
            h = QHBoxLayout(holder)
            h.setContentsMargins(4, 2, 4, 2)
            h.addWidget(btn)
            self._model_table.setCellWidget(row, 3, holder)
            self._model_download_btns[model_id] = btn

        card.add_widget(self._model_table, 1)
        layout.addWidget(card, 1)
        return widget

    @staticmethod
    def _model_note(model_id: str) -> str:
        notes = {
            "tiny": "最快，适合先验证流程",
            "base": "速度与精度平衡的入门档",
            "small": "长视频的性价比之选",
            "medium": "高精度，需要较多内存",
            "large-v1": "旧版权重",
            "large-v2": "旧版权重",
            "large-v3": "最高精度",
            "large-v3-turbo": "高精度且更快，推荐",
        }
        return notes.get(model_id, "")

    def _make_asr_config_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(0)

        form = Form()

        section = form.add_section("识别参数", "控制输出字幕的断行方式")
        self._one_word_combo = Select()
        self._one_word_combo.addItems(["0 · 关闭", "1 · 单字时间戳", "2 · 单字 + 标点"])
        section.add_row("单字模式", self._one_word_combo)

        self._sentence_check = CheckBox("启用句子分段")
        section.add_row("句子分段", self._sentence_check)

        self._max_line_width_spin = NumberField()
        self._max_line_width_spin.setRange(1, 100)
        section.add_row("最大行宽", self._max_line_width_spin)

        self._max_line_count_spin = NumberField()
        self._max_line_count_spin.setRange(1, 5)
        section.add_row("最大行数", self._max_line_count_spin)

        self._max_comma_spin = NumberField()
        self._max_comma_spin.setRange(1, 100)
        section.add_row("逗号断行阈值", self._max_comma_spin)

        self._max_comma_cent_spin = NumberField()
        self._max_comma_cent_spin.setRange(1, 100)
        section.add_row("逗号居中阈值", self._max_comma_cent_spin)

        vad = form.add_section("VAD 语音检测", "先去静音再识别，可明显降低空白段的幻觉字幕")
        self._vad_enabled_check = CheckBox("启用 Silero VAD")
        vad.add_row("启用", self._vad_enabled_check)

        self._vad_preset_combo = Select()
        self._vad_preset_combo.addItems(["ASMR 耳语", "通用", "自定义"])
        vad.add_row("预设", self._vad_preset_combo)

        self._vad_threshold_spin = DecimalField()
        self._vad_threshold_spin.setRange(0.05, 0.95)
        self._vad_threshold_spin.setSingleStep(0.05)
        self._vad_threshold_spin.setDecimals(2)
        vad.add_row("语音概率阈值", self._vad_threshold_spin)

        self._vad_min_speech_spin = NumberField()
        self._vad_min_speech_spin.setRange(50, 2000)
        self._vad_min_speech_spin.setSingleStep(50)
        vad.add_row("最小语音时长", self._vad_min_speech_spin)

        self._vad_min_silence_spin = NumberField()
        self._vad_min_silence_spin.setRange(100, 5000)
        self._vad_min_silence_spin.setSingleStep(100)
        vad.add_row("最小静音时长", self._vad_min_silence_spin)

        self._vad_speech_pad_spin = NumberField()
        self._vad_speech_pad_spin.setRange(0, 1000)
        self._vad_speech_pad_spin.setSingleStep(50)
        vad.add_row("语音边界填充", self._vad_speech_pad_spin)

        layout.addWidget(form)
        return widget

    # -------------------------------------------------------------- 生命周期
    def showEvent(self, event):
        super().showEvent(event)
        self._start_async_check()

    def _connect_signals(self):
        self._gpu_download_btn.clicked.connect(lambda: self._download_program("gpu"))
        self._cpu_download_btn.clicked.connect(lambda: self._download_program("cpu"))
        self._gpu_delete_btn.clicked.connect(lambda: self._on_program_delete_clicked("gpu"))
        self._cpu_delete_btn.clicked.connect(lambda: self._on_program_delete_clicked("cpu"))
        self._detect_btn.clicked.connect(self._on_detect_gpu_clicked)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._source_combo.currentIndexChanged.connect(self._on_source_changed)
        signal_bus.gpu_detected.connect(self._on_gpu_detected)

        self._one_word_combo.currentIndexChanged.connect(lambda v: self._persist_asr("one_word", v))
        self._sentence_check.stateChanged.connect(
            lambda s: self._persist_asr("sentence", s == Qt.Checked))
        self._max_line_width_spin.valueChanged.connect(
            lambda v: self._persist_asr("max_line_width", v))
        self._max_line_count_spin.valueChanged.connect(
            lambda v: self._persist_asr("max_line_count", v))
        self._max_comma_spin.valueChanged.connect(lambda v: self._persist_asr("max_comma", v))
        self._max_comma_cent_spin.valueChanged.connect(
            lambda v: self._persist_asr("max_comma_cent", v))

        self._vad_enabled_check.stateChanged.connect(
            lambda s: self._persist_asr("vad_enabled", s == Qt.Checked))
        self._vad_preset_combo.currentIndexChanged.connect(self._on_vad_preset_changed)
        self._vad_threshold_spin.valueChanged.connect(
            lambda v: self._persist_asr("vad_threshold", float(v)))
        self._vad_min_speech_spin.valueChanged.connect(
            lambda v: self._persist_asr("vad_min_speech_duration_ms", int(v)))
        self._vad_min_silence_spin.valueChanged.connect(
            lambda v: self._persist_asr("vad_min_silence_duration_ms", int(v)))
        self._vad_speech_pad_spin.valueChanged.connect(
            lambda v: self._persist_asr("vad_speech_pad_ms", int(v)))

    def _init_config(self):
        asr_config = config_manager.get_section("asr")

        self._one_word_combo.setCurrentIndex(asr_config.get("one_word", 1))
        self._sentence_check.setChecked(asr_config.get("sentence", True))
        self._max_line_width_spin.setValue(asr_config.get("max_line_width", 30))
        self._max_line_count_spin.setValue(asr_config.get("max_line_count", 1))
        self._max_comma_spin.setValue(asr_config.get("max_comma", 20))
        self._max_comma_cent_spin.setValue(asr_config.get("max_comma_cent", 50))

        self._vad_enabled_check.setChecked(asr_config.get("vad_enabled", False))
        preset = asr_config.get("vad_preset", "asmr")
        preset_map = {"asmr": 0, "general": 1, "custom": 2}
        self._vad_preset_combo.setCurrentIndex(preset_map.get(preset, 0))
        self._vad_threshold_spin.setValue(float(asr_config.get("vad_threshold", 0.25)))
        self._vad_min_speech_spin.setValue(asr_config.get("vad_min_speech_duration_ms", 100))
        self._vad_min_silence_spin.setValue(asr_config.get("vad_min_silence_duration_ms", 500))
        self._vad_speech_pad_spin.setValue(asr_config.get("vad_speech_pad_ms", 300))
        self._apply_vad_preset(preset)

        gpu_mode = asr_config.get("gpu_mode", "auto")
        mode_map = {"auto": 0, "gpu": 1, "cpu": 2}
        self._mode_combo.setCurrentIndex(mode_map.get(gpu_mode, 0))
        model_source = asr_config.get("model_source", "modelscope")
        source_map = {"modelscope": 0, "huggingface": 1}
        self._source_combo.setCurrentIndex(source_map.get(model_source, 0))

    def _start_async_check(self):
        if self._init_check_thread and self._init_check_thread.isRunning():
            return
        self._init_check_thread = _InitCheckThread(self)
        self._init_check_thread.result_ready.connect(self._on_async_check_ready)
        self._init_check_thread.start()

    def _on_async_check_ready(self, gpu_info, version_info):
        signal_bus.gpu_detected.emit(gpu_info["available"], gpu_info)
        self._update_program_buttons(version_info)

    def _update_program_buttons(self, version_info):
        if version_info["gpu_installed"]:
            ver = version_info["gpu_version"]
            self._gpu_status_label.setText("已安装" + (f" · v{ver}" if ver else ""))
            self._gpu_status_label.show()
            self._gpu_download_btn.hide()
            self._gpu_delete_btn.show()
        else:
            self._gpu_status_label.hide()
            self._gpu_download_btn.show()
            self._gpu_download_btn.setEnabled(True)
            self._gpu_download_btn.setText("下载 GPU 版本")
            self._gpu_delete_btn.hide()

        if version_info["cpu_installed"]:
            ver = version_info["cpu_version"]
            self._cpu_status_label.setText("已安装" + (f" · v{ver}" if ver else ""))
            self._cpu_status_label.show()
            self._cpu_download_btn.hide()
            self._cpu_delete_btn.show()
        else:
            self._cpu_status_label.hide()
            self._cpu_download_btn.show()
            self._cpu_download_btn.setEnabled(True)
            self._cpu_download_btn.setText("下载 CPU 版本")
            self._cpu_delete_btn.hide()

        if version_info["gpu_installed"]:
            self._card_version.set_value(version_info["gpu_version"] or "已安装")
            self._card_version.set_status("success")
            self._card_status.set_value("就绪")
            self._card_status.set_status("normal")
        elif version_info["cpu_installed"]:
            self._card_version.set_value(version_info["cpu_version"] or "已安装")
            self._card_version.set_status("success")
            self._card_status.set_value("就绪")
            self._card_status.set_status("normal")
        else:
            self._card_version.set_value("未安装")
            self._card_version.set_status("error")

    def _on_detect_gpu_clicked(self):
        self._gpu_status.setText("CUDA 状态：正在检测…")
        self._start_async_check()

    def _on_gpu_detected(self, available, info):
        if available:
            self._card_cuda.set_value("可用")
            self._card_cuda.set_status("success")
            self._card_gpu_mem.set_value(str(info.get("vram_free_mb", 0)))
            self._gpu_status.setText(self._gpu_detail(info))
        else:
            self._card_cuda.set_value("不可用")
            self._card_cuda.set_status("error")
            self._card_gpu_mem.set_value("—")
            self._gpu_status.setText(self._gpu_failure_hint(info))

    @staticmethod
    def _gpu_detail(info) -> str:
        parts = [f"CUDA 可用 · {info.get('gpu_name') or '未知显卡'}"]
        cuda = info.get("cuda_version") or ""
        if cuda:
            parts.append(f"CUDA {cuda}")
        driver = info.get("driver_version") or ""
        if driver:
            parts.append(f"驱动 {driver}")
        free = info.get("vram_free_mb", 0)
        total = info.get("vram_total_mb", 0)
        if total:
            parts.append(f"显存 {free}/{total} MB")
        count = int(info.get("gpu_count", 1) or 1)
        if count > 1:
            parts.append(f"共 {count} 张显卡")
        return " · ".join(parts)

    @staticmethod
    def _gpu_failure_hint(info) -> str:
        reason = info.get("reason", "")
        devices = [d for d in (info.get("nvidia_devices") or []) if d]
        name = devices[0] if devices else (info.get("gpu_name") or "")

        if reason == "smi_not_found":
            if name:
                return (f"检测到 {name}，但找不到 nvidia-smi；"
                        "请更新或重装 NVIDIA 驱动后点「重新检测 GPU」（当前使用 CPU 版本）")
            return "未找到 nvidia-smi，请确认已正确安装 NVIDIA 显卡驱动（当前使用 CPU 版本）"
        if reason == "smi_failed":
            prefix = f"检测到 {name}，" if name else ""
            return (f"{prefix}nvidia-smi 读取失败，通常与驱动异常或版本过旧有关；"
                    "请更新 NVIDIA 驱动后点「重新检测 GPU」（当前使用 CPU 版本）")
        if reason == "no_gpu":
            return "nvidia-smi 未列出可用的 NVIDIA 显卡，当前使用 CPU 版本运行"
        if name:
            return f"未检测到可用的 CUDA 环境（{name}），当前使用 CPU 版本运行"
        return "未检测到 NVIDIA 显卡，当前使用 CPU 版本运行"

    def _download_program(self, version):
        if self._program_download_thread and self._program_download_thread.isRunning():
            return
        if FasterWhisperPage._is_downloading:
            return
        FasterWhisperPage._is_downloading = True
        btn = self._gpu_download_btn if version == "gpu" else self._cpu_download_btn
        btn.setEnabled(False)
        btn.setText("下载中…")
        self._program_download_thread = _ProgramDownloadThread(version, self._manager, self)
        self._program_download_thread.progress.connect(self._on_program_download_progress)
        self._program_download_thread.completed.connect(
            lambda: self._on_program_download_completed(version))
        self._program_download_thread.failed.connect(
            lambda e: self._on_program_download_failed(version, e))
        self._program_download_thread.start()

    def _on_program_download_progress(self, pct):
        self._card_status.set_value(f"下载 {pct}%")

    def _on_program_download_completed(self, version):
        FasterWhisperPage._is_downloading = False
        if version == "gpu":
            self._gpu_download_btn.hide()
            self._gpu_status_label.setText("已安装")
            self._gpu_status_label.show()
            self._gpu_delete_btn.show()
        else:
            self._cpu_download_btn.hide()
            self._cpu_status_label.setText("已安装")
            self._cpu_status_label.show()
            self._cpu_delete_btn.show()
        self._card_status.set_value("就绪")
        self._card_status.set_status("normal")
        self._start_async_check()

    def _on_program_download_failed(self, version, error):
        FasterWhisperPage._is_downloading = False
        btn = self._gpu_download_btn if version == "gpu" else self._cpu_download_btn
        btn.setEnabled(True)
        btn.setText("下载 GPU 版本" if version == "gpu" else "下载 CPU 版本")
        self._card_status.set_value("下载失败")
        self._card_status.set_status("error")

        _logger.error("识别程序下载失败 (%s): %s", version, error)
        status_label = self._gpu_status_label if version == "gpu" else self._cpu_status_label
        status_label.setText(f"下载失败：{_short_reason(error)}")
        status_label.setToolTip(f"{error}\n\n完整记录见「设置 → 性能 → 诊断」里的日志文件")
        status_label.show()

    def _on_program_delete_clicked(self, version):
        label = "GPU 版本" if version == "gpu" else "CPU 版本"
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除已安装的 {label} 吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._manager.remove_program(version)
        if version == "gpu":
            self._gpu_status_label.hide()
            self._gpu_delete_btn.hide()
            self._gpu_download_btn.show()
            self._gpu_download_btn.setEnabled(True)
            self._gpu_download_btn.setText("下载 GPU 版本")
        else:
            self._cpu_status_label.hide()
            self._cpu_delete_btn.hide()
            self._cpu_download_btn.show()
            self._cpu_download_btn.setEnabled(True)
            self._cpu_download_btn.setText("下载 CPU 版本")
        self._start_async_check()

    def _on_mode_changed(self, index):
        mode_map = {0: "auto", 1: "gpu", 2: "cpu"}
        self._persist_asr("gpu_mode", mode_map.get(index, "auto"))

    def _on_source_changed(self, index):
        source_map = {0: MODEL_SOURCE_MODELSCOPE, 1: MODEL_SOURCE_HUGGINGFACE}
        self._persist_asr("model_source", source_map.get(index, MODEL_SOURCE_MODELSCOPE))

    _VAD_PRESETS = {
        # threshold 0.18：耳语音量低，配合响度归一化仍需低于通用阈值才能检出
        # min_speech 300：过滤敲击/啪嗒等短促触发音（多 <300ms）
        # min_silence 700：归一化后触发音可能短暂触发 VAD，延长静音判定避免把
        #                   连续耳语中夹杂的短脉冲误判为停顿而切段
        # pad 400：耳语起止边界较模糊，多留余量避免切字
        "asmr": {"threshold": 0.18, "min_speech": 300, "min_silence": 700, "pad": 400},
        "general": {"threshold": 0.5, "min_speech": 250, "min_silence": 2000, "pad": 300},
    }

    def _on_vad_preset_changed(self, index: int):
        preset_map = {0: "asmr", 1: "general", 2: "custom"}
        preset = preset_map.get(index, "custom")
        self._persist_asr("vad_preset", preset)
        self._apply_vad_preset(preset)

    def _apply_vad_preset(self, preset: str):
        is_custom = preset == "custom"
        self._vad_threshold_spin.setEnabled(is_custom)
        self._vad_min_speech_spin.setEnabled(is_custom)
        self._vad_min_silence_spin.setEnabled(is_custom)
        self._vad_speech_pad_spin.setEnabled(is_custom)
        if not is_custom and preset in self._VAD_PRESETS:
            p = self._VAD_PRESETS[preset]
            self._vad_threshold_spin.setValue(p["threshold"])
            self._vad_min_speech_spin.setValue(p["min_speech"])
            self._vad_min_silence_spin.setValue(p["min_silence"])
            self._vad_speech_pad_spin.setValue(p["pad"])

    def _persist_asr(self, key, value):
        config_manager.set("asr", key, value)
        config_manager.save()

    def _on_model_download_clicked(self, model_id):
        if FasterWhisperPage._is_downloading:
            return
        if model_id in self._download_threads and self._download_threads[model_id].isRunning():
            return
        FasterWhisperPage._is_downloading = True
        source_map = {0: MODEL_SOURCE_MODELSCOPE, 1: MODEL_SOURCE_HUGGINGFACE}
        source = source_map.get(self._source_combo.currentIndex(), MODEL_SOURCE_MODELSCOPE)
        thread = ModelDownloadThread(model_id, source, parent=self)
        thread.progress.connect(self._on_model_download_progress)
        thread.completed.connect(self._on_model_download_completed)
        thread.failed.connect(self._on_model_download_failed)
        self._download_threads[model_id] = thread
        thread.start()
        signal_bus.model_download_started.emit(model_id)
        for mid, btn in self._model_download_btns.items():
            btn.setEnabled(False)
        if model_id in self._model_download_btns:
            self._model_download_btns[model_id].setText("下载中…")

    def _on_model_download_progress(self, model_id, pct):
        for row, (mid, info) in enumerate(WHISPER_MODELS.items()):
            if mid == model_id:
                item = self._model_table.item(row, 2)
                if item:
                    item.setText(f"{pct}%")
                break

    def _on_model_download_completed(self, model_id):
        FasterWhisperPage._is_downloading = False
        for row, (mid, info) in enumerate(WHISPER_MODELS.items()):
            if mid == model_id:
                item = self._model_table.item(row, 2)
                if item:
                    item.setText("已安装")
                if model_id in self._model_download_btns:
                    self._model_download_btns[model_id].setEnabled(False)
                    self._model_download_btns[model_id].setText("已安装")
                break
        for mid, btn in self._model_download_btns.items():
            if mid != model_id:
                model_path = os.path.join(self._get_models_dir(), mid)
                is_installed = os.path.exists(os.path.join(model_path, "model.bin"))
                btn.setEnabled(not is_installed)
                btn.setText("已安装" if is_installed else "下载")
        signal_bus.model_download_completed.emit(model_id)
        signal_bus.model_installed.emit(model_id)

    def _on_model_download_failed(self, model_id, error):
        FasterWhisperPage._is_downloading = False
        _logger.error("模型下载失败 (%s): %s", model_id, error)
        for row, (mid, info) in enumerate(WHISPER_MODELS.items()):
            if mid == model_id:
                item = self._model_table.item(row, 2)
                if item:
                    item.setText("下载失败")
                    item.setToolTip(f"{error}\n\n完整记录见「设置 → 性能 → 诊断」里的日志文件")
                if model_id in self._model_download_btns:
                    self._model_download_btns[model_id].setEnabled(True)
                    self._model_download_btns[model_id].setText("重试")
                break
        for mid, btn in self._model_download_btns.items():
            if mid != model_id:
                model_path = os.path.join(self._get_models_dir(), mid)
                is_installed = os.path.exists(os.path.join(model_path, "model.bin"))
                btn.setEnabled(not is_installed)
                btn.setText("已安装" if is_installed else "下载")
        signal_bus.model_download_failed.emit(model_id, error)

    def _get_models_dir(self):
        from app.core.utils.file_utils import get_whisper_models_dir
        return get_whisper_models_dir()

    def _refresh_model_status(self):
        model_dir = self._get_models_dir()
        for row, (model_id, info) in enumerate(WHISPER_MODELS.items()):
            model_path = os.path.join(model_dir, model_id)
            is_installed = os.path.exists(os.path.join(model_path, "model.bin"))
            status_item = self._model_table.item(row, 2)
            if status_item:
                status_item.setText("已安装" if is_installed else "未安装")
            if model_id in self._model_download_btns:
                if is_installed:
                    self._model_download_btns[model_id].setEnabled(False)
                    self._model_download_btns[model_id].setText("已安装")
                else:
                    self._model_download_btns[model_id].setEnabled(True)
                    self._model_download_btns[model_id].setText("下载")
