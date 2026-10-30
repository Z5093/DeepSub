import os

from PyQt5.QtCore import Qt, QThread, QElapsedTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from app.common.constants import BCP47_LANGUAGES, SUPPORTED_SUBTITLE_FORMATS, SUPPORTED_VIDEO_FORMATS, WHISPER_MODELS
from app.common.signal_bus import signal_bus
from app.common.theme import UITheme as T
from app.components.buttons import Button, Switch
from app.components.panels import Badge, Card, LogView, StatTile, hairline
from app.components.pipeline import PipelineWidget
from app.components.widgets import (
    DropZone, Field, Label, Select, TextField,
)
from app.core.asr.audio_extractor import AudioExtractor
from app.core.pipeline import PipelineOrchestrator
from app.core.subtitle.converter import SubtitleConverter

_STEP_DISPLAY_NAMES = {
    "download": "视频下载",
    "audio": "音频提取",
    "asr": "语音识别",
    "split1": "语义分段",
    "translate": "翻译",
    "split2": "智能分割",
    "export": "字幕生成",
}

_STEP_PROGRESS_WEIGHTS = {
    "audio": 10,
    "asr": 30,
    "split1": 10,
    "translate": 30,
    "split2": 10,
    "export": 10,
}


class _PipelineWorker(QThread):
    finished = pyqtSignal(bool)

    def __init__(self, orchestrator, from_step="", parent=None):
        super().__init__(parent)
        self._orchestrator = orchestrator
        self._from_step = from_step

    def run(self):
        result = self._orchestrator.start(from_step=self._from_step)
        self.finished.emit(result)


class WorkspacePage(QWidget):
    file_selected = pyqtSignal(str)
    start_processing = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self._orchestrator = None
        self._worker = None
        self._video_file_path = ""
        self._subtitle_doc = None
        self._processing_state = "IDLE"
        self._current_progress = 0
        self._completed_steps = set()
        self._elapsed_timer = QElapsedTimer()
        self._setup_ui()
        self._connect_signals()

    # ------------------------------------------------------------------ UI
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 12)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        h1 = QLabel("工作台")
        h1.setObjectName("H2")
        sub = QLabel("本地跑完从识别到成片的全部环节，文件不会离开这台电脑")
        sub.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; font-size: {T.SIZE_SUB}px; background: transparent;"
        )
        titles.addWidget(h1)
        titles.addWidget(sub)
        header.addLayout(titles)
        header.addStretch(1)
        self._state_badge = Badge("空闲", "neutral")
        header.addWidget(self._state_badge, 0, Qt.AlignVCenter)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(16)

        # ---------------- 左侧主区 ----------------
        left = QVBoxLayout()
        left.setSpacing(14)

        input_card = Card()
        self._drop_zone = DropZone(
            "拖入视频或字幕文件",
            "支持 mp4 / mkv / mov 视频，与 srt / ass / vtt 字幕",
        )
        self._drop_zone.clicked.connect(self._on_browse)
        self._drop_zone.files_dropped.connect(self._on_files_dropped)
        input_card.add_widget(self._drop_zone)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self._file_path = TextField("尚未选择文件")
        path_row.addWidget(self._file_path, 1)

        self._browse_btn = Button("选择…")
        self._browse_btn.clicked.connect(self._on_browse)
        path_row.addWidget(self._browse_btn)

        self._start_btn = Button("开始处理", variant="primary")
        self._start_btn.clicked.connect(self._on_start_processing)
        path_row.addWidget(self._start_btn)

        self._pause_btn = Switch("暂停")
        self._pause_btn.clicked.connect(self._on_toggle_pause)
        self._pause_btn.setVisible(False)
        path_row.addWidget(self._pause_btn)
        input_card.add_layout(path_row)
        left.addWidget(input_card)

        self._pipeline = PipelineWidget()
        left.addWidget(self._pipeline)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self._card_status = StatTile("状态", "空闲")
        self._card_step = StatTile("当前环节", "—")
        self._card_progress = StatTile("总进度", "0", "%")
        self._card_eta = StatTile("预计剩余", "—")
        for tile in (self._card_status, self._card_step, self._card_progress, self._card_eta):
            stats.addWidget(tile)
        left.addLayout(stats)

        info_card = Card("视频信息")
        self._video_info_label = Label("尚未加载视频文件", "muted")
        self._video_info_label.setWordWrap(True)
        self._video_info_label.setMinimumHeight(44)
        self._video_info_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        info_card.add_widget(self._video_info_label, 1)
        self._info_hint = Label("选择文件后会显示时长、分辨率、编码与字幕条数", "hint")
        self._info_hint.setWordWrap(True)
        info_card.add_widget(self._info_hint)
        left.addWidget(info_card, 1)

        left.addStretch(1)
        body.addLayout(left, 3)

        # ---------------- 右侧检查器 ----------------
        right = QVBoxLayout()
        right.setSpacing(14)

        params = Card("本次参数")
        from app.common.config import config_manager

        self._source_lang_combo = self._make_lang_combo(
            config_manager.get("general", "source_language", "en-US"), allow_auto=True,
        )
        params.add_widget(Field("源语言", self._source_lang_combo))

        self._target_lang_combo = self._make_lang_combo(
            config_manager.get("general", "target_language", "zh-CN"),
        )
        params.add_widget(Field("目标语言", self._target_lang_combo))

        self._model_combo = self._make_model_combo()
        params.add_widget(Field("识别模型", self._model_combo))

        self._enable_translation_check = Switch()
        self._enable_translation_check.setChecked(
            config_manager.get("translate", "enable_translation", True)
        )
        params.add_widget(Field("翻译", self._enable_translation_check, hint="关闭后只输出原文字幕"))

        params.add_widget(hairline())
        hint = Label("参数会在开始处理时写入任务，不影响正在运行的任务。", "hint")
        hint.setWordWrap(True)
        params.add_widget(hint)
        right.addWidget(params)

        self._terminal = LogView("运行日志")
        right.addWidget(self._terminal, 1)

        body.addLayout(right, 2)
        root.addLayout(body, 1)

        self._log_welcome()

    def _make_lang_combo(self, default="en-US", allow_auto=False):
        combo = Select()
        if allow_auto:
            combo.addItem("自动识别", "auto")
        for code, name in BCP47_LANGUAGES.items():
            combo.addItem(f"{name} · {code}", code)
        idx = combo.findData(default)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        return combo

    def _make_model_combo(self):
        from app.common.config import config_manager

        combo = Select()
        for model_id, info in WHISPER_MODELS.items():
            combo.addItem(f"{info['label']} · {info['size_mb']} MB", model_id)
        default_model = config_manager.get("asr", "model", "large-v3")
        idx = combo.findData(default_model)
        if idx < 0:
            idx = combo.findData("large-v3")
        if idx >= 0:
            combo.setCurrentIndex(idx)
        return combo

    # -------------------------------------------------------------- 交互
    def _on_browse(self):
        video_ext = " ".join(SUPPORTED_VIDEO_FORMATS)
        sub_ext = " ".join(SUPPORTED_SUBTITLE_FORMATS)
        all_ext = f"{video_ext} {sub_ext}"
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择文件", "", f"所有支持格式 ({all_ext});;视频 ({video_ext});;字幕 ({sub_ext})"
        )
        if file_path:
            self._file_path.setText(file_path)
            self.file_selected.emit(file_path)
            self._handle_file_selected(file_path)

    def _on_files_dropped(self, paths):
        if not paths:
            return
        path = paths[0]
        self._file_path.setText(path)
        self.file_selected.emit(path)
        self._handle_file_selected(path)

    def _log_welcome(self):
        self._terminal.append_line("系统就绪，等待文件输入", "success")
        self._terminal.append_line("处理顺序：识别 → 分段 → 翻译 → 对齐 → 导出", "dim")

    def get_pipeline(self) -> PipelineWidget:
        return self._pipeline

    def get_terminal(self) -> LogView:
        return self._terminal

    def _connect_signals(self):
        signal_bus.pipeline_step_changed.connect(self._on_pipeline_step_changed)
        signal_bus.pipeline_step_completed.connect(self._on_pipeline_step_completed)
        signal_bus.pipeline_step_error.connect(self._on_pipeline_step_error)
        signal_bus.pipeline_started.connect(self._on_pipeline_started)
        signal_bus.pipeline_completed.connect(self._on_pipeline_completed)
        signal_bus.pipeline_paused.connect(self._on_pipeline_paused)
        signal_bus.pipeline_stopped.connect(self._on_pipeline_stopped)
        signal_bus.log_message.connect(self._on_log_message)
        signal_bus.asr_progress.connect(self._on_asr_progress)
        signal_bus.translate_progress.connect(self._on_translate_progress)

        self._source_lang_combo.currentIndexChanged.connect(self._on_source_lang_changed)
        self._target_lang_combo.currentIndexChanged.connect(self._on_target_lang_changed)
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._enable_translation_check.toggled.connect(self._on_enable_translation_toggled)
        self._drop_zone.files_dropped.connect(self._on_files_dropped)

    def _on_source_lang_changed(self, _index):
        from app.common.config import config_manager
        code = self._source_lang_combo.currentData() or "en-US"
        config_manager.set("general", "source_language", code)
        config_manager.save()

    def _on_target_lang_changed(self, _index):
        from app.common.config import config_manager
        code = self._target_lang_combo.currentData() or "zh-CN"
        config_manager.set("general", "target_language", code)
        config_manager.save()

    def _on_model_changed(self, _index):
        from app.common.config import config_manager
        model_id = self._model_combo.currentData() or "large-v3"
        config_manager.set("asr", "model", model_id)
        config_manager.save()

    def _on_enable_translation_toggled(self, checked):
        from app.common.config import config_manager
        config_manager.set("translate", "enable_translation", checked)
        config_manager.save()

    def _handle_file_selected(self, file_path):
        if self._processing_state == "PROCESSING" and self._orchestrator:
            self._orchestrator.cancel()
            if self._worker:
                self._worker.wait(3000)

        self._pipeline.reset()
        self._completed_steps = set()
        self._processing_state = "IDLE"
        self._current_progress = 0
        self._card_step.set_value("—")
        self._card_eta.set_value("—")
        self._card_eta.set_status("dim")
        self._update_cards()

        ext = os.path.splitext(file_path)[1].lower()
        subtitle_exts = {".srt", ".vtt", ".ass", ".ssa"}

        if ext in subtitle_exts:
            self._handle_subtitle_file(file_path)
        else:
            self._handle_video_file(file_path)

    def _handle_video_file(self, file_path):
        self._video_file_path = file_path
        self._subtitle_doc = None
        self._video_info_label.set_kind("mono")
        self._video_info_label.setText("正在读取视频信息…")

        extractor = AudioExtractor()
        info = extractor.get_video_info(file_path)

        if info:
            self._info_hint.setVisible(False)
            self._video_info_label.setText(extractor.format_info_display(info))
            self._terminal.append_line(f"视频已加载：{os.path.basename(file_path)}", "success")
        else:
            self._video_info_label.setText("无法读取视频信息")
            self._terminal.append_line(f"无法读取视频信息：{os.path.basename(file_path)}", "error")

    def _handle_subtitle_file(self, file_path):
        self._video_file_path = ""
        try:
            doc = SubtitleConverter.from_file(file_path)
            self._subtitle_doc = doc
            self._info_hint.setVisible(False)
            count = len(doc.subtitles)
            self._video_info_label.set_kind("mono")
            self._video_info_label.setText(
                f"文件  {os.path.basename(file_path)}\n"
                f"类型  字幕文件\n"
                f"条数  {count}"
            )
            self._terminal.append_line(
                f"字幕已导入：{os.path.basename(file_path)}（{count} 条）", "success"
            )
        except Exception as e:
            self._subtitle_doc = None
            self._video_info_label.setText(f"字幕导入失败：{e}")
            self._terminal.append_line(f"字幕导入失败：{e}", "error")

    def _on_start_processing(self):
        if self._processing_state == "PROCESSING":
            return

        if not self._video_file_path and not self._subtitle_doc:
            self._terminal.append_line("请先选择视频或字幕文件", "error")
            return

        self.start_processing.emit()

        if self._video_file_path:
            self._start_video_pipeline()
        elif self._subtitle_doc:
            self._start_subtitle_pipeline()

    def _on_toggle_pause(self, checked):
        if not self._orchestrator:
            return
        if checked:
            self._orchestrator.pause()
        else:
            self._orchestrator.resume()
            paused_step = self._orchestrator.get_paused_step()
            if paused_step and self._worker is None:
                self._processing_state = "PROCESSING"
                self._update_cards()
                self._worker = _PipelineWorker(self._orchestrator)
                self._worker.finished.connect(self._on_pipeline_finished)
                self._worker.start()

    def _start_video_pipeline(self):
        self._orchestrator = PipelineOrchestrator()
        self._orchestrator.set_video(self._video_file_path)
        self._apply_params_to_orchestrator()

        self._processing_state = "PROCESSING"
        self._current_progress = 0
        self._completed_steps = set()
        self._update_cards()
        self._start_btn.setEnabled(False)
        self._pause_btn.setVisible(True)
        self._pause_btn.setChecked(False)
        self._elapsed_timer.start()

        self._worker = _PipelineWorker(self._orchestrator)
        self._worker.finished.connect(self._on_pipeline_finished)
        self._worker.start()

    def _apply_params_to_orchestrator(self):
        self._orchestrator.set_params(
            source_lang=self._source_lang_combo.currentData() or "en-US",
            target_lang=self._target_lang_combo.currentData() or "zh-CN",
            asr_model=self._model_combo.currentData() or "large-v3",
            enable_translation=self._enable_translation_check.isChecked(),
        )

    def _start_subtitle_pipeline(self):
        from app.core.utils.file_utils import ensure_output_dir, get_step_output_path

        file_path = self._file_path.text()
        ensure_output_dir(file_path)
        asr_path = get_step_output_path(file_path, "asr")
        self._subtitle_doc.save(asr_path)

        self._video_file_path = file_path

        self._orchestrator = PipelineOrchestrator()
        self._orchestrator.set_video(self._video_file_path)
        self._apply_params_to_orchestrator()

        if self._orchestrator._state:
            self._orchestrator._state.completed_steps = ["audio", "asr"]
            self._orchestrator._save_state()

        self._processing_state = "PROCESSING"
        self._current_progress = 0
        self._completed_steps = set()
        self._update_cards()
        self._start_btn.setEnabled(False)
        self._pause_btn.setVisible(True)
        self._pause_btn.setChecked(False)
        self._elapsed_timer.start()

        self._worker = _PipelineWorker(self._orchestrator, from_step="split1")
        self._worker.finished.connect(self._on_pipeline_finished)
        self._worker.start()

    def _on_pipeline_step_changed(self, step_id):
        self._pipeline.set_current_step(step_id)
        step_name = _STEP_DISPLAY_NAMES.get(step_id, step_id)
        self._card_step.set_value(step_name)
        self._terminal.append_line(f"开始：{step_name}", "info")
        self._current_progress = sum(_STEP_PROGRESS_WEIGHTS.get(s, 0) for s in self._completed_steps)
        self._update_cards()

    def _on_pipeline_step_completed(self, step_id):
        self._pipeline.set_step_state(step_id, "completed")
        self._completed_steps.add(step_id)
        step_name = _STEP_DISPLAY_NAMES.get(step_id, step_id)
        self._terminal.append_line(f"完成：{step_name}", "success")
        self._current_progress = sum(_STEP_PROGRESS_WEIGHTS.get(s, 0) for s in self._completed_steps)
        self._update_cards()

    def _on_pipeline_step_error(self, step_id, error_msg):
        self._pipeline.set_step_state(step_id, "error")
        self._processing_state = "ERROR"
        self._update_cards()
        self._start_btn.setEnabled(True)
        step_name = _STEP_DISPLAY_NAMES.get(step_id, step_id)
        self._terminal.append_line(f"{step_name}失败：{error_msg}", "error")

    def _on_pipeline_started(self):
        self._processing_state = "PROCESSING"
        self._current_progress = 0
        self._completed_steps = set()
        self._update_cards()
        self._start_btn.setEnabled(False)
        self._pause_btn.setVisible(True)
        self._pause_btn.setChecked(False)
        self._terminal.append_line("处理已开始", "success")

    def _on_pipeline_completed(self):
        self._processing_state = "COMPLETED"
        self._current_progress = 100
        self._update_cards()
        self._start_btn.setEnabled(True)
        self._pause_btn.setVisible(False)
        self._terminal.append_line("全部环节已完成", "success")

    def _on_pipeline_paused(self):
        self._processing_state = "PAUSED"
        self._update_cards()
        self._start_btn.setEnabled(True)
        self._terminal.append_line("已暂停，继续时从当前环节重新开始", "warning")

    def _on_pipeline_stopped(self):
        self._processing_state = "IDLE"
        self._update_cards()
        self._start_btn.setEnabled(True)
        self._pause_btn.setVisible(False)
        self._terminal.append_line("处理已停止", "warning")

    def _on_pipeline_finished(self, success):
        self._worker = None
        if not success and self._processing_state == "PROCESSING":
            self._processing_state = "ERROR"
            self._update_cards()
            self._terminal.append_line("处理启动失败", "error")
        if self._processing_state != "PAUSED":
            self._pause_btn.setVisible(False)
        self._start_btn.setEnabled(True)

    def _on_log_message(self, message, level):
        self._terminal.append_line(message, level)

    def _on_asr_progress(self, pct):
        base = sum(_STEP_PROGRESS_WEIGHTS.get(s, 0) for s in self._completed_steps)
        step_weight = _STEP_PROGRESS_WEIGHTS.get("asr", 30)
        self._current_progress = base + int(pct / 100 * step_weight)
        self._update_cards()

    def _on_translate_progress(self, current, total):
        base = sum(_STEP_PROGRESS_WEIGHTS.get(s, 0) for s in self._completed_steps)
        step_weight = _STEP_PROGRESS_WEIGHTS.get("translate", 30)
        pct = current / total if total > 0 else 0
        self._current_progress = base + int(pct * step_weight)
        self._update_cards()

    def _update_cards(self):
        state_map = {
            "IDLE": ("空闲", "neutral", "dim"),
            "PROCESSING": ("处理中", "accent", "normal"),
            "PAUSED": ("已暂停", "warning", "warning"),
            "ERROR": ("出错", "live", "error"),
            "COMPLETED": ("已完成", "success", "success"),
        }
        text, badge_tone, tile_tone = state_map.get(self._processing_state, state_map["IDLE"])
        self._card_status.set_value(text)
        self._card_status.set_status(tile_tone)
        self._state_badge.set_tone(badge_tone, text)

        if self._processing_state == "COMPLETED":
            self._card_progress.set_value("100")
            self._card_progress.set_status("success")
        elif self._processing_state == "PROCESSING":
            self._card_progress.set_value(str(self._current_progress))
            self._card_progress.set_status("normal")
        elif self._processing_state == "ERROR":
            self._card_progress.set_status("error")
        elif self._processing_state == "IDLE":
            self._card_progress.set_value("0")
            self._card_progress.set_status("dim")

        if self._processing_state == "PROCESSING" and self._elapsed_timer.isValid():
            elapsed_ms = self._elapsed_timer.elapsed()
            if self._current_progress > 0:
                remaining_ms = int(elapsed_ms / self._current_progress * (100 - self._current_progress))
                remaining_sec = remaining_ms // 1000
                if remaining_sec >= 60:
                    self._card_eta.set_value(f"{remaining_sec // 60}m {remaining_sec % 60}s")
                else:
                    self._card_eta.set_value(f"{remaining_sec}s")
                self._card_eta.set_status("normal")
            else:
                self._card_eta.set_value("—")
                self._card_eta.set_status("dim")
        elif self._processing_state == "COMPLETED":
            self._card_eta.set_value("0s")
            self._card_eta.set_status("success")
        else:
            self._card_eta.set_value("—")
            self._card_eta.set_status("dim")
