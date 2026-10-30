from __future__ import annotations
from PyQt5.QtCore import QThread, pyqtSignal
from typing import Optional
from app.core.subtitle.models import SubtitleDocument


class TranslateThread(QThread):
    progress = pyqtSignal(int, int)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    log = pyqtSignal(str, str)

    def __init__(self, document: SubtitleDocument, source_lang: str, target_lang: str,
                 service: str = "openai_compat",
                 api_base_url: str = "", api_key: str = "", model: str = "gpt-4o-mini",
                 llamacpp_config: Optional[dict] = None,
                 template_name: str = "auto", glossary_name: str = "",
                 concurrency: int = 1, window_size: int = 3, context_size: int = 1,
                 video_style: str = "", auto_detect_style: bool = True,
                 timeout: float = 900.0, max_retries: int = 10,
                 retry_delay: float = 30.0,
                 parent=None):
        super().__init__(parent)
        self._document = document
        self._source_lang = source_lang
        self._target_lang = target_lang
        self._service = service
        self._api_base_url = api_base_url
        self._api_key = api_key
        self._model = model
        self._llamacpp_config = llamacpp_config or {}
        self._template_name = template_name
        self._glossary_name = glossary_name
        self._concurrency = concurrency
        self._window_size = window_size
        self._context_size = context_size
        self._video_style = video_style
        self._auto_detect_style = auto_detect_style
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._cancelled = False

    def run(self):
        try:
            from app.core.translator.prompt_template import PromptTemplateManager
            from app.core.translator.glossary import GlossaryManager
            from app.core.translator.sliding_window import SlidingWindowTranslator

            if self._service == "llamacpp":
                from app.core.llamacpp.translator import LlamaCppTranslator
                translator = LlamaCppTranslator(self._llamacpp_config)
            else:
                from app.core.translator.openai_compat import OpenAICompatTranslator
                translator = OpenAICompatTranslator(
                    api_base_url=self._api_base_url,
                    api_key=self._api_key,
                    model=self._model,
                    timeout=self._timeout,
                    max_retries=self._max_retries,
                    retry_delay=self._retry_delay,
                )

            template_mgr = PromptTemplateManager()
            glossary_mgr = GlossaryManager()

            window_translator = SlidingWindowTranslator(
                translator=translator,
                template_manager=template_mgr,
                glossary_manager=glossary_mgr,
                window_size=self._window_size,
                context_size=self._context_size,
                concurrency=self._concurrency,
                video_style=self._video_style,
                auto_detect_style=self._auto_detect_style,
            )

            result = window_translator.translate_document(
                document=self._document,
                source_lang=self._source_lang,
                target_lang=self._target_lang,
                template_name=self._template_name,
                glossary_name=self._glossary_name,
                progress_callback=self._on_progress
            )

            # 与流水线保持一致：修复空译文/未翻译/明显扩写的条目
            try:
                window_translator.repair_document(
                    result, self._source_lang, self._target_lang,
                    log_callback=lambda msg, level: self.log.emit(msg, level),
                )
            except Exception as exc:  # noqa: BLE001 - 修复失败不影响主流程
                self.log.emit(f"译文质量修复失败: {exc}", "warning")

            if self._cancelled:
                return
            self.completed.emit(result)
        except Exception as e:
            if not self._cancelled:
                self.failed.emit(str(e))

    def _on_progress(self, current: int, total: int):
        if not self._cancelled:
            self.progress.emit(current, total)

    def cancel(self):
        self._cancelled = True
