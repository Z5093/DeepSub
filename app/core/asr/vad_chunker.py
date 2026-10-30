from __future__ import annotations

import logging
import os
import subprocess
from typing import Callable, Optional

from app.core.asr.chunker import AudioChunk, AudioChunker, SpeechSegment
from app.core.asr.vad import SileroVAD
from app.core.utils.ffmpeg_utils import get_ffmpeg_path, get_video_info

logger = logging.getLogger(__name__)


class VADChunker:
    """VAD 分块器：加载音频 → Silero VAD 检测语音段 → 打包成 AudioChunk。

    与 AudioChunker 同 chunk(audio_path) 接口，产出的 chunk.is_vad=True，
    segments 为多个去静音语音段。

    改进：
    - 合并间隔很近的语音段（保留中间无人声部分），避免语义割裂
    - 允许单段超出 30s 限制，避免在人声中间强制切割
    """

    MAX_PACKED_DURATION = 30.0      # 单 chunk 拼接后时长上限（软限制）
    MAX_SEGMENTS_PER_CHUNK = 32     # 段数上限（控制 ffmpeg filter_complex 命令行长度）
    MERGE_GAP_THRESHOLD = 1.5       # 间隔小于此值（秒）的语音段合并，保留中间无人声

    # 覆盖率下限：检出语音总时长占音频总时长的比例低于此值，判定 VAD 失效。
    # 典型正常语音 40%~80%。低于 3% 说明模型几乎没认出人声（模型接入错误/
    # 极端混音/纯音乐），继续按 VAD 分块会丢字幕，必须回退定长分块。
    MIN_COVERAGE = 0.03

    # RMS 归一化（可选，历史遗留）。
    # 曾用于"放大耳语让 silero 识别"，但后来定位到真正原因是 silero v5 少喂了
    # 64 样本上下文（见 vad.SileroVAD 类注释）——修好后归一化对检出率几乎无影响
    # （实测同一文件：开 62.0% vs 关 62.5% 覆盖）。保留开关以便对比，默认建议关闭：
    # 预加重 + tanh 软限幅会改变频谱形状，对 ASR 无益，且多花一次全量 numpy 运算。
    _NORM_TARGET_RMS = 0.1
    _NORM_MAX_GAIN = 30.0
    _NORM_PEAK_PERCENTILE = 99.0

    def __init__(
        self,
        vad: SileroVAD,
        log_callback: Optional[Callable[[str, str], None]] = None,
        merge_gap_threshold: float = MERGE_GAP_THRESHOLD,
        normalize_audio: bool = False,
    ) -> None:
        self._vad = vad
        self._log_callback = log_callback
        self._merge_gap_threshold = merge_gap_threshold
        self._normalize_audio = normalize_audio

    def _log(self, message: str, level: str = "info") -> None:
        if self._log_callback:
            self._log_callback(message, level)
        getattr(logger, level if level != "warning" else "warning")(message)

    def chunk(self, audio_path: str) -> list[AudioChunk]:
        if not os.path.isfile(audio_path):
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")

        samples = self._load_audio_pcm(audio_path)
        if samples is None or len(samples) == 0:
            self._log("音频加载失败，返回空分块", "warning")
            return []

        self._log(
            f"正在执行 VAD 语音检测（{len(samples) / SileroVAD.SAMPLE_RATE:.1f}s 音频"
            f"{', 启用响度归一化' if self._normalize_audio else ''}）...",
            "info",
        )
        segments = self._vad.detect(samples)
        total_duration = len(samples) / SileroVAD.SAMPLE_RATE

        if not segments:
            self._log("VAD 未检测到语音段", "warning")
            return []

        speech_total = sum(s.end - s.start for s in segments)
        self._log(
            f"VAD 检测到 {len(segments)} 段语音，总时长 {speech_total:.1f}s / {total_duration:.1f}s",
            "info",
        )

        # 合并间隔很近的语音段，保留中间无人声部分（避免语义割裂）
        segments = self._merge_close_segments(segments)
        if len(segments) > 0:
            merged_total = sum(s.end - s.start for s in segments)
            self._log(
                f"合并相近段后 {len(segments)} 段，总时长 {merged_total:.1f}s",
                "info",
            )

        chunks = self._pack_segments(segments)
        self._log(f"VAD 打包为 {len(chunks)} 块", "info")

        # 覆盖率守卫：VAD 只认出极少量语音时（例如 1315s 音频只检出 1.2s），
        # 继续按 VAD 分块会把 99% 的音频直接丢弃，且下游看到"有 chunk"不会报错，
        # 最终产出几乎空白字幕。此时回退定长分块，宁可多转静音也不漏内容。
        covered = sum(c.packed_duration() for c in chunks)
        coverage = covered / total_duration if total_duration > 0 else 0.0
        if coverage < self.MIN_COVERAGE:
            self._log(
                f"VAD 覆盖率仅 {coverage * 100:.2f}%（{covered:.1f}s / {total_duration:.1f}s），"
                f"低于 {self.MIN_COVERAGE * 100:.0f}% 判定 VAD 失效，回退定长分块",
                "warning",
            )
            fallback = AudioChunker(log_callback=self._log_callback)
            try:
                return fallback.chunk(audio_path)
            finally:
                fallback.cleanup()
        return chunks

    def _merge_close_segments(self, segments: list[SpeechSegment]) -> list[SpeechSegment]:
        """合并间隔小于阈值的相邻语音段，保留中间无人声部分。

        例如：[0-3s 语音] [3.2-5s 语音] 间隔 0.2s < 1.5s 阈值
        → 合并为 [0-5s]（保留 3-3.2s 无人声部分）

        合并受 MAX_PACKED_DURATION 约束：若合并后总时长将超过此值则停止合并，
        避免间隔密集的多段被级联成单个超长段，导致下游打包软限制失效
        （ASMR/耳语音频常出现数十段间隔 <1.5s，无上限合并会塌缩成单段）。
        """
        if len(segments) <= 1:
            return segments

        merged: list[SpeechSegment] = [segments[0]]
        for seg in segments[1:]:
            prev = merged[-1]
            gap = seg.start - prev.end
            if gap < self._merge_gap_threshold and seg.end - prev.start <= self.MAX_PACKED_DURATION:
                # 间隔很近且合并后不超打包上限，合并（保留中间无人声部分）
                merged[-1] = SpeechSegment(start=prev.start, end=seg.end)
            else:
                merged.append(seg)
        return merged

    def _load_audio_pcm(self, audio_path: str):
        """用 ffmpeg 提取 16k mono float32 PCM，返回 numpy array。

        归一化仅用于 VAD 检测阶段，后续 ASR 仍按时间段从原始音频切取，
        不受归一化副作用影响。
        """
        import numpy as np

        ffmpeg = get_ffmpeg_path()
        cmd = [
            ffmpeg,
            "-i", audio_path,
            "-vn",
            "-acodec", "pcm_f32le",
            "-ar", str(SileroVAD.SAMPLE_RATE),
            "-ac", "1",
            "-f", "f32le",
            "-y",
            "pipe:1",
        ]
        try:
            info = get_video_info(audio_path)
            audio_dur = float(info.get("duration", 60)) if info else 60.0
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=max(60, int(audio_dur * 2)),
            )
            if result.returncode != 0 or not result.stdout:
                if result.returncode != 0:
                    self._log(
                        f"ffmpeg 提取 PCM 失败 rc={result.returncode}: "
                        f"{result.stderr.decode('utf-8', errors='replace')[-500:]}",
                        "warning",
                    )
                return None
            samples = np.frombuffer(result.stdout, dtype=np.float32).copy()
            if len(samples) == 0:
                return samples
            rms = float(np.sqrt(np.mean(samples ** 2)))
            peak = float(np.max(np.abs(samples)))
            self._log(
                f"PCM 原始: 样本={len(samples)} RMS={rms:.4f} 峰值={peak:.4f}",
                "info",
            )
            if self._normalize_audio:
                samples = self._normalize_rms(np, samples)
            return samples
        except Exception as e:
            self._log(f"ffmpeg 提取 PCM 异常: {e}", "warning")
            return None

    def _normalize_rms(self, np, samples):
        """RMS 归一化：去 DC + 预加重高通 + RMS 增益 + tanh 软限幅。

        去 DC + 高通解决"仅放大音量模型仍不识别"：低频/直流分量占据 RMS 主体，
        RMS 增益后语音频段仍偏低，silero 概率趋零（实测前10窗口全 0.001）。
        tanh 软限幅替代硬 clip，避免方顶波形污染 LSTM 状态。
        """
        samples = samples.astype(np.float32)
        # 去 DC 偏置：录音直流分量使波形整体偏移一侧，silero 响应极低
        dc_offset = float(np.mean(samples))
        samples = samples - dc_offset
        # 预加重高通 y[n]=x[n]-α·x[n-1]（α=0.97, ~80Hz@16k）：衰减低频隆隆声
        emphasized = np.empty_like(samples, dtype=np.float32)
        emphasized[0] = samples[0]
        emphasized[1:] = samples[1:] - 0.97 * samples[:-1]
        samples = emphasized

        rms = float(np.sqrt(np.mean(samples ** 2)))
        if rms < 1e-6:
            self._log("RMS 过低，跳过归一化（疑似静音音频）", "warning")
            return samples
        gain = min(self._NORM_TARGET_RMS / rms, self._NORM_MAX_GAIN)
        samples = samples * gain
        # 99 分位作为缩放判据：避免少数突发尖峰过度限制 gain
        peak_p = float(np.percentile(np.abs(samples), self._NORM_PEAK_PERCENTILE))
        actual_gain = gain
        if peak_p > 0.99:
            scale = 0.99 / peak_p
            samples = samples * scale
            actual_gain = gain * scale
        # tanh 软限幅：对 |x|<0.5 几乎无影响（tanh(0.5)=0.46），仅平滑压缩
        # 超阈值尖峰，无方顶，不污染 silero LSTM 状态
        samples = np.tanh(samples)
        new_rms = float(np.sqrt(np.mean(samples ** 2)))
        peak_actual = float(np.max(np.abs(samples)))
        self._log(
            f"PCM 归一化: DC={dc_offset:.4f} gain={actual_gain:.2f}x "
            f"RMS={rms:.4f}→{new_rms:.4f} 峰值={peak_actual:.4f} 峰值99分位={peak_p:.4f}",
            "info",
        )
        return samples

    def _pack_segments(self, segments: list[SpeechSegment]) -> list[AudioChunk]:
        """贪心打包：累加段时长，达 MAX_PACKED_DURATION 或段数上限则封包。

        MAX_PACKED_DURATION 为软限制：当 current_segs 非空且加入下一段会超限时封包，
        但若 current_segs 为空（单段已超限），仍直接加入，允许单段超出 30s 避免语义割裂。
        """
        if not segments:
            return []

        chunks: list[AudioChunk] = []
        current_segs: list[SpeechSegment] = []
        current_packed = 0.0

        for seg in segments:
            seg_dur = seg.end - seg.start
            would_exceed_duration = current_packed + seg_dur > self.MAX_PACKED_DURATION
            would_exceed_count = len(current_segs) >= self.MAX_SEGMENTS_PER_CHUNK
            if current_segs and (would_exceed_duration or would_exceed_count):
                chunks.append(self._build_chunk(current_segs))
                current_segs = []
                current_packed = 0.0
            current_segs.append(seg)
            current_packed += seg_dur

        if current_segs:
            chunks.append(self._build_chunk(current_segs))
        return chunks

    @staticmethod
    def _build_chunk(segs: list[SpeechSegment]) -> AudioChunk:
        first = segs[0]
        last = segs[-1]
        return AudioChunk(
            offset=first.start,
            duration=last.end - first.start,
            segments=list(segs),
            is_vad=True,
        )

    def cleanup(self) -> None:
        self._vad.cleanup()
