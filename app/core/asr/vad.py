from __future__ import annotations

import logging
import os
from typing import Callable, Optional

from app.core.asr.chunker import SpeechSegment

logger = logging.getLogger(__name__)

_GITHUB_MIRRORS = [
    "",
    "https://ghfast.top/",
    "https://gh-proxy.com/",
    "https://mirror.ghproxy.com/",
    "https://ghps.cc/",
]

_MODEL_URL_PATH = "https://github.com/snakers4/silero-vad/raw/refs/heads/master/src/silero_vad/data/silero_vad.onnx"
_MIN_MODEL_SIZE = 1_500_000


def _download_with_httpx(
    url: str,
    dest_path: str,
    progress_callback: Optional[Callable[[float], None]] = None,
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> bool:
    def _log(message: str, level: str = "warning") -> None:
        if log_callback:
            log_callback(message, level)
        getattr(logger, level if level != "warning" else "warning")(message)

    try:
        import httpx

        headers = {"User-Agent": "DeepSub/0.1.0"}
        with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=180.0) as response:
            if response.status_code >= 400:
                _log(f"下载失败 HTTP {response.status_code}: {url}", "warning")
                return False
            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0
            with open(dest_path, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size > 0:
                        progress_callback(min(downloaded / total_size * 100, 100.0))
        if not os.path.isfile(dest_path) or os.path.getsize(dest_path) < _MIN_MODEL_SIZE:
            if os.path.isfile(dest_path):
                os.remove(dest_path)
            _log(f"下载内容过小或为空: {url}", "warning")
            return False
        return True
    except Exception as e:
        _log(f"下载异常 ({type(e).__name__}: {e}): {url}", "warning")
        return False


class SileroVAD:
    """Silero VAD onnx 推理引擎。

    流式推理：按 WINDOW_SIZE 窗口分帧，每帧送 onnx 模型得到语音概率，
    维护 LSTM 状态。后处理合并连续语音帧为 SpeechSegment。

    重要（v5 模型接入）：silero v5 的 onnx 输入是「64 样本历史上下文 + 512 样本
    当前窗口」共 576 样本。只喂 512 会让模型输出的概率整体塌陷到 0.001 量级
    （实测同一段 90s 清晰人声：喂 512 时 max=0.003，喂 576 时 max=1.000、
    mean=0.536）。官方 OnnxWrapper 用 self._context 实现滚动上下文。
    """

    SAMPLE_RATE = 16000
    WINDOW_SIZE = 512  # 32ms @ 16k，silero v5 标准
    CONTEXT_SIZE = 64  # v5 每个窗口前置的历史上下文样本数（16k）


    def __init__(
        self,
        model_dir: str,
        threshold: float = 0.25,
        min_speech_duration_ms: int = 100,
        min_silence_duration_ms: int = 500,
        speech_pad_ms: int = 300,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._model_dir = model_dir
        self._model_path = os.path.join(model_dir, "silero_vad.onnx")
        self.threshold = threshold
        self.min_speech_samples = self.SAMPLE_RATE * min_speech_duration_ms / 1000.0
        self.min_silence_samples = self.SAMPLE_RATE * min_silence_duration_ms / 1000.0
        self.speech_pad_samples = self.SAMPLE_RATE * speech_pad_ms / 1000.0
        self._log_callback = log_callback
        self._session = None
        self._input_names: list[str] = []
        self._output_names: list[str] = []

    def _log(self, message: str, level: str = "info") -> None:
        if self._log_callback:
            self._log_callback(message, level)
        getattr(logger, level if level != "warning" else "warning")(message)

    def is_model_available(self) -> bool:
        return os.path.isfile(self._model_path) and os.path.getsize(self._model_path) >= _MIN_MODEL_SIZE

    def ensure_model(self, progress_callback: Optional[Callable[[float], None]] = None) -> bool:
        if self.is_model_available():
            return True
        os.makedirs(self._model_dir, exist_ok=True)
        tmp_path = self._model_path + ".tmp"
        for mirror in _GITHUB_MIRRORS:
            url = mirror + _MODEL_URL_PATH if mirror else _MODEL_URL_PATH
            self._log(f"正在下载 Silero VAD 模型: {url}", "info")
            if _download_with_httpx(url, tmp_path, progress_callback, self._log_callback):
                try:
                    os.replace(tmp_path, self._model_path)
                    return True
                except OSError:
                    if os.path.isfile(tmp_path):
                        os.remove(tmp_path)
                    continue
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        self._log("Silero VAD 模型下载失败", "warning")
        return False

    def _load_session(self) -> None:
        if self._session is not None:
            return
        import onnxruntime as ort
        import numpy as np

        self._session = ort.InferenceSession(
            self._model_path,
            providers=["CPUExecutionProvider"],
        )
        self._input_names = [i.name for i in self._session.get_inputs()]
        self._output_names = [o.name for o in self._session.get_outputs()]
        inputs = ", ".join(f"{i.name}{list(i.shape)}" for i in self._session.get_inputs())
        outputs = ", ".join(f"{o.name}{list(o.shape)}" for o in self._session.get_outputs())
        file_size = os.path.getsize(self._model_path)
        self._log(
            f"Silero VAD 模型加载({file_size}字节): 输入[{inputs}] 输出[{outputs}]",
            "info",
        )

    def _init_state(self, np):
        """根据输入节点名初始化 LSTM 状态（兼容 v4 h/c 和 v5 state）。"""
        if "state" in self._input_names:
            return np.zeros((2, 1, 128), dtype=np.float32)
        h = np.zeros((2, 1, 64), dtype=np.float32)
        c = np.zeros((2, 1, 64), dtype=np.float32)
        return (h, c)

    def _run_window(self, np, window: np.ndarray, state, context):
        """对单窗口推理，返回 (prob, new_state, new_context)。"""
        sr = np.array(self.SAMPLE_RATE, dtype=np.int64)
        if "state" in self._input_names:
            # v5：输入必须拼接 64 样本历史上下文，否则概率整体趋零
            inp = np.concatenate([context, window.reshape(1, -1)], axis=1)
            feeds = {"input": inp.astype(np.float32), "state": state, "sr": sr}
            outs = self._session.run(self._output_names, feeds)
            prob = float(outs[0].item() if outs[0].size == 1 else outs[0][0, 0])
            new_context = inp[:, -self.CONTEXT_SIZE:].copy()
            return prob, outs[1], new_context
        h, c = state
        feeds = {"input": window.reshape(1, -1), "h": h, "c": c, "sr": sr}
        outs = self._session.run(self._output_names, feeds)
        prob = float(outs[0].item() if outs[0].size == 1 else outs[0][0, 0])
        out_map = dict(zip(self._output_names, outs))
        new_h = out_map.get("hn", outs[1])
        new_c = out_map.get("cn", outs[2] if len(outs) > 2 else outs[1])
        return prob, (new_h, new_c), context

    def detect(self, audio_samples) -> list[SpeechSegment]:
        """对 16k mono float32 numpy 数组做 VAD，返回 SpeechSegment 列表。

        段已应用 speech_pad 并裁剪到 [0, 总时长]。
        """
        import numpy as np

        if audio_samples is None or len(audio_samples) < self.WINDOW_SIZE:
            return []

        self._load_session()
        if self._session is None:
            self._log("VAD 模型不可用，跳过检测", "warning")
            return []

        total_samples = len(audio_samples)
        num_windows = total_samples // self.WINDOW_SIZE
        state = self._init_state(np)
        context = np.zeros((1, self.CONTEXT_SIZE), dtype=np.float32)

        speech_flags: list[bool] = []
        probs: list[float] = []
        # 周期性重置 LSTM 状态与上下文：silero 状态跨窗口累积，被 clip 到 1.0 的尖峰
        # （敲击/喷麦）会饱和隐藏状态，导致后续所有窗口概率趋零（实测 p99=0.002）。
        # 每 ~30s 重置一次，限制污染传播范围。
        _STATE_RESET_WINDOWS = 938  # 30s @ 16kHz / 512 samples
        for i in range(num_windows):
            if i > 0 and i % _STATE_RESET_WINDOWS == 0:
                state = self._init_state(np)
                context = np.zeros((1, self.CONTEXT_SIZE), dtype=np.float32)
            start = i * self.WINDOW_SIZE
            window = audio_samples[start:start + self.WINDOW_SIZE].astype(np.float32)
            prob, state, context = self._run_window(np, window, state, context)
            probs.append(prob)
            speech_flags.append(prob >= self.threshold)

        if probs:
            max_prob = max(probs)
            mean_prob = sum(probs) / len(probs)
            above = sum(1 for p in probs if p >= self.threshold)
            probs_arr = np.array(probs)
            p90 = float(np.percentile(probs_arr, 90))
            p99 = float(np.percentile(probs_arr, 99))
            self._log(
                f"VAD 概率统计: max={max_prob:.3f} mean={mean_prob:.3f} "
                f"p90={p90:.3f} p99={p99:.3f} 阈值={self.threshold:.2f} "
                f"超阈值窗口={above}/{len(probs)}",
                "info",
            )
            # p99 异常低时记录前10窗口概率，判断是模型故障还是状态污染
            # 前10窗口用初始零状态推理，若也接近0则模型本身有问题
            if p99 < 0.05:
                head = " ".join(f"{p:.3f}" for p in probs[:10])
                self._log(
                    f"VAD p99={p99:.3f} 异常低，前10窗口概率(初始状态): {head}",
                    "warning",
                )

        segments = self._flags_to_segments(speech_flags, total_samples)

        # 多级阈值回退：标准阈值无段时逐步降低。silero 对耳语/ASMR 响应偏低，
        # 且高概率窗口可能太分散无法成段（min_speech_duration 过滤掉碎片）。
        # 从 max_prob*0.5 逐步降到 0.03，取首个能产出段的阈值。
        if not segments and probs:
            max_prob = max(probs)
            for adaptive in (max_prob * 0.5, max_prob * 0.3, 0.05, 0.03):
                if adaptive >= self.threshold or adaptive < 0.02:
                    continue
                above = sum(1 for p in probs if p >= adaptive)
                if above < 3:
                    continue
                self._log(
                    f"VAD 阈值回退: {self.threshold:.2f}→{adaptive:.3f} "
                    f"(max_prob={max_prob:.3f}, 超阈值窗口={above}/{len(probs)})",
                    "info",
                )
                speech_flags = [p >= adaptive for p in probs]
                segments = self._flags_to_segments(speech_flags, total_samples)
                if segments:
                    break

        # 能量回退：silero 概率普遍趋零（p99<0.02）时，耳语超出模型识别范围。
        # 即使 silero 回退产出少量段（如 2.6s/1640s），能量回退通常覆盖更全——
        # 取覆盖时长更长者。
        if probs and p99 < 0.02:
            energy_segments = self._energy_fallback(np, audio_samples, num_windows, total_samples)
            if energy_segments:
                energy_total = sum(s.end - s.start for s in energy_segments)
                silero_total = sum(s.end - s.start for s in segments) if segments else 0.0
                if energy_total > silero_total:
                    self._log(
                        f"能量回退覆盖更全: {energy_total:.1f}s > silero {silero_total:.1f}s，采用能量分段",
                        "info",
                    )
                    segments = energy_segments

        return segments

    def _flags_to_segments(
        self, speech_flags: list[bool], total_samples: int
    ) -> list[SpeechSegment]:
        """把逐窗口语音标记合并为 SpeechSegment，应用 min_silence/min_speech/pad。"""
        if not speech_flags:
            return []

        raw_segments: list[tuple[int, int]] = []  # [start_window_idx, end_window_idx] 含 end
        in_speech = False
        seg_start = 0
        silence_run = 0

        for i, flag in enumerate(speech_flags):
            if flag:
                if not in_speech:
                    in_speech = True
                    seg_start = i
                silence_run = 0
            else:
                if in_speech:
                    silence_run += self.WINDOW_SIZE
                    if silence_run >= self.min_silence_samples or i == len(speech_flags) - 1:
                        raw_segments.append((seg_start, i - (silence_run // self.WINDOW_SIZE) if silence_run >= self.min_silence_samples else i))
                        in_speech = False
                        silence_run = 0

        if in_speech:
            raw_segments.append((seg_start, len(speech_flags) - 1))

        result: list[SpeechSegment] = []
        for start_idx, end_idx in raw_segments:
            start_sample = start_idx * self.WINDOW_SIZE
            end_sample = (end_idx + 1) * self.WINDOW_SIZE
            if end_sample - start_sample < self.min_speech_samples:
                continue
            start_sample = max(0, int(start_sample - self.speech_pad_samples))
            end_sample = min(total_samples, int(end_sample + self.speech_pad_samples))
            result.append(
                SpeechSegment(
                    start=start_sample / self.SAMPLE_RATE,
                    end=end_sample / self.SAMPLE_RATE,
                )
            )
        return result

    def _energy_fallback(
        self, np, audio_samples, num_windows: int, total_samples: int
    ) -> list[SpeechSegment]:
        """能量回退 VAD：silero 无法识别（耳语/气声 p99<0.02）时，用短时能量分段。

        按窗口 RMS 能量的 30 分位作阈值：低于此为静音，高于为语音。
        30 分位假设至少 30% 窗口是静音——对多数音频成立。偏激进：
        宁可多包含静音也不漏语音，后续 ASR 能处理段内静音。
        """
        windowed = audio_samples[:num_windows * self.WINDOW_SIZE].reshape(
            num_windows, self.WINDOW_SIZE
        )
        energies = np.sqrt(np.mean(windowed ** 2, axis=1))
        threshold_e = float(np.percentile(energies, 30))
        if threshold_e < 1e-6:
            self._log("能量回退: 30分位能量趋零，音频疑似全静音", "warning")
            return []
        speech_flags = (energies >= threshold_e).tolist()
        above = sum(speech_flags)
        self._log(
            f"VAD 能量回退: 30分位={threshold_e:.5f} 均值={float(np.mean(energies)):.5f} "
            f"超阈值窗口={above}/{num_windows}",
            "info",
        )
        return self._flags_to_segments(speech_flags, total_samples)

    def cleanup(self) -> None:
        self._session = None
