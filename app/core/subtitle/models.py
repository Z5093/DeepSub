from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Any, Optional


@dataclass
class SubtitleWord:
    word: str
    start: float
    end: float
    probability: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SubtitleWord:
        return cls(
            word=data.get("word", ""),
            start=data.get("start", 0.0),
            end=data.get("end", 0.0),
            probability=data.get("probability", 0.0),
        )


@dataclass
class SubtitleItem:
    index: int
    start: float
    end: float
    text: str
    translation: str = ""
    words: List[SubtitleWord] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "index": self.index,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "translation": self.translation,
            "words": [w.to_dict() for w in self.words],
            "confidence": self.confidence,
        }
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SubtitleItem:
        words_data = data.get("words", [])
        words = [SubtitleWord.from_dict(w) for w in words_data]
        return cls(
            index=data.get("index", 0),
            start=data.get("start", 0.0),
            end=data.get("end", 0.0),
            text=data.get("text", ""),
            translation=data.get("translation", ""),
            words=words,
            confidence=data.get("confidence", 0.0),
        )


@dataclass
class SubtitleMetadata:
    source_file: str
    source_lang: str
    target_lang: str
    asr_model: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    source_step: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SubtitleMetadata:
        return cls(
            source_file=data.get("source_file", ""),
            source_lang=data.get("source_lang", ""),
            target_lang=data.get("target_lang", ""),
            asr_model=data.get("asr_model", ""),
            created_at=data.get("created_at", datetime.now().isoformat(timespec="seconds")),
            source_step=data.get("source_step", ""),
        )


@dataclass
class SubtitleDocument:
    metadata: SubtitleMetadata
    subtitles: List[SubtitleItem] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "subtitles": [s.to_dict() for s in self.subtitles],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SubtitleDocument:
        metadata = SubtitleMetadata.from_dict(data.get("metadata", {}))
        subtitles_data = data.get("subtitles", [])
        subtitles = [SubtitleItem.from_dict(s) for s in subtitles_data]
        return cls(metadata=metadata, subtitles=subtitles)

    def save(self, file_path: str) -> None:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, file_path: str) -> SubtitleDocument:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> SubtitleDocument:
        data = json.loads(json_str)
        return cls.from_dict(data)


@dataclass
class ProcessingState:
    video_file: str
    current_step: str = ""
    completed_steps: List[str] = field(default_factory=list)
    step_timestamps: Dict[str, Dict[str, str]] = field(default_factory=dict)
    parameters: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "video_file": self.video_file,
            "current_step": self.current_step,
            "completed_steps": self.completed_steps,
            "step_timestamps": self.step_timestamps,
            "parameters": self.parameters,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ProcessingState:
        return cls(
            video_file=data.get("video_file", ""),
            current_step=data.get("current_step", ""),
            completed_steps=data.get("completed_steps", []),
            step_timestamps=data.get("step_timestamps", {}),
            parameters=data.get("parameters", {}),
        )

    def save(self, file_path: str) -> None:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, file_path: str) -> ProcessingState:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
