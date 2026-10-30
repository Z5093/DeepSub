from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.core.subtitle.models import SubtitleDocument


class ASRBase(ABC):
    @abstractmethod
    def run(self, audio_path: str, language: str = "", model: str = "large-v3", **kwargs) -> SubtitleDocument:
        pass

    @abstractmethod
    def is_available(self) -> bool:
        pass

    @abstractmethod
    def get_version(self) -> str:
        pass

    def cancel(self):
        pass
