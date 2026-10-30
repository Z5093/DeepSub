from __future__ import annotations
from PyQt5.QtCore import QThread, pyqtSignal
from typing import Optional
from app.core.subtitle.models import SubtitleDocument


class ASRThread(QThread):
    progress = pyqtSignal(int)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    log = pyqtSignal(str, str)

    def __init__(self, audio_path: str, language: str = "", model: str = "large-v3",
                 gpu_mode: bool = False,
                 one_word: int = 1, sentence: bool = True,
                 max_line_width: int = 30, max_line_count: int = 1,
                 max_comma: int = 20, max_comma_cent: int = 50,
                 parent=None):
        super().__init__(parent)
        self._audio_path = audio_path
        self._language = language
        self._model = model
        self._gpu_mode = gpu_mode
        self._one_word = one_word
        self._sentence = sentence
        self._max_line_width = max_line_width
        self._max_line_count = max_line_count
        self._max_comma = max_comma
        self._max_comma_cent = max_comma_cent
        self._cancelled = False
        self._asr = None

    def run(self):
        try:
            from app.core.asr.faster_whisper import FasterWhisperASR
            asr = FasterWhisperASR(gpu_mode=self._gpu_mode)
            self._asr = asr
            if not asr.is_available():
                self.failed.emit("FasterWhisper 程序未安装")
                return
            result = asr.run(
                audio_path=self._audio_path,
                language=self._language,
                model=self._model,
                one_word=self._one_word,
                sentence=self._sentence,
                max_line_width=self._max_line_width,
                max_line_count=self._max_line_count,
                max_comma=self._max_comma,
                max_comma_cent=self._max_comma_cent,
                progress_callback=self._on_progress,
                log_callback=self._on_log,
            )
            if self._cancelled:
                return
            self.completed.emit(result)
        except Exception as e:
            if not self._cancelled:
                self.failed.emit(str(e))

    def _on_progress(self, pct: float):
        if not self._cancelled:
            self.progress.emit(int(pct))

    def _on_log(self, message: str, level: str):
        if not self._cancelled:
            self.log.emit(message, level)

    def cancel(self):
        self._cancelled = True
        if self._asr:
            self._asr.cancel()
