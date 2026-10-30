from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable

from app.core.utils.ffmpeg_utils import get_video_info

logger = logging.getLogger(__name__)


@dataclass
class SpeechSegment:
    """VAD 检测出的一段语音区间（原始音频绝对时间，秒）。"""

    start: float
    end: float


@dataclass
class AudioChunk:
    """音频分块。

    定长模式（is_vad=False）：segments 默认为单段 [SpeechSegment(offset, offset+duration)]，
    relative_to_absolute 退化为 offset + t，与历史行为一致。
    VAD 模式（is_vad=True）：segments 为多个去静音语音段，relative_to_absolute 按
    拼接音频相对时间映射回原始音频绝对时间。
    """

    offset: float        # 在原始音频中的起始时间（秒），等于 segments[0].start
    duration: float      # 原始音频中的跨度（秒），等于 segments[-1].end - segments[0].start
    segments: list[SpeechSegment] = field(default_factory=list)
    is_vad: bool = False

    def __post_init__(self) -> None:
        if not self.segments:
            self.segments = [SpeechSegment(start=self.offset, end=self.offset + self.duration)]

    def packed_duration(self) -> float:
        """拼接后音频时长（去静音后），用于 VAD 模式。"""
        return sum(s.end - s.start for s in self.segments)

    def relative_to_absolute(self, t: float) -> float:
        """把拼接音频内的相对时间 t 映射回原始音频绝对时间。

        单段时退化为 offset + t。多段时逐段累加，t 落在第 k 段内返回
        seg_k.start + (t - 前 k-1 段时长之和)。
        """
        if t <= 0.0:
            return self.segments[0].start
        acc = 0.0
        for seg in self.segments:
            seg_dur = seg.end - seg.start
            if t <= acc + seg_dur:
                return seg.start + (t - acc)
            acc += seg_dur
        return self.segments[-1].end


class AudioChunker:
    """音频定长分块器：按固定时长切分音频，无重叠。

    规则：
    - 音频总时长 ≤ MAX_DURATION_NO_SPLIT（60s）：不切分，整段作为一个语块直接转录
    - 音频总时长 > 60s：每块最大 CHUNK_DURATION（30s），步长 = CHUNK_DURATION，无重叠
    """

    MAX_DURATION_NO_SPLIT = 60.0
    CHUNK_DURATION = 30.0

    def __init__(self, log_callback: Optional[Callable[[str, str], None]] = None) -> None:
        self._log_callback = log_callback

    def _log(self, message: str, level: str = "info") -> None:
        if self._log_callback:
            self._log_callback(message, level)

    def chunk(self, audio_path: str) -> list[AudioChunk]:
        """对音频做定长切分（无重叠），返回分块列表。"""
        if not os.path.isfile(audio_path):
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")

        duration = self._get_duration(audio_path)
        if duration <= 0:
            self._log("音频时长无法获取，回退到整文件模式", "warning")
            return [AudioChunk(offset=0.0, duration=0.0)]

        if duration <= self.MAX_DURATION_NO_SPLIT:
            self._log(
                f"音频时长 {duration:.1f}s ≤ {self.MAX_DURATION_NO_SPLIT}s，不切分直接转录",
                "info",
            )
            return [AudioChunk(offset=0.0, duration=duration)]

        chunks = self._split_fixed(duration)
        self._log(
            f"音频时长 {duration:.1f}s，切分为 {len(chunks)} 块"
            f"（每块≤{self.CHUNK_DURATION}s，无重叠）",
            "info",
        )
        return chunks

    def _get_duration(self, audio_path: str) -> float:
        info = get_video_info(audio_path)
        if info is not None:
            return float(info.get("duration", 0.0))
        return 0.0

    def _split_fixed(self, duration: float) -> list[AudioChunk]:
        """定长切分（无重叠）。

        步长 = CHUNK_DURATION = 30s，每块时长 = CHUNK_DURATION（最后一块到音频结尾）。
        """
        step = self.CHUNK_DURATION
        chunks: list[AudioChunk] = []
        offset = 0.0

        while offset < duration:
            end = min(offset + self.CHUNK_DURATION, duration)
            chunks.append(AudioChunk(offset=offset, duration=end - offset))
            if end >= duration:
                break
            offset += step

        return chunks

    def cleanup(self) -> None:
        """清理资源（保留接口兼容）。"""
        pass
