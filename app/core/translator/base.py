from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Callable

from app.core.subtitle.models import SubtitleDocument


class TranslatorBase(ABC):
    @abstractmethod
    def translate(self, text: str, source_lang: str, target_lang: str,
                  context: str = "", glossary: str = "") -> str:
        pass

    @abstractmethod
    def is_available(self) -> bool:
        pass
