from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from app.core.asr.base import ASRBase
from app.core.asr.chunker import AudioChunk
from app.core.subtitle.models import SubtitleDocument, SubtitleItem, SubtitleMetadata, SubtitleWord
from app.core.utils.gpu_utils import detect_cuda, get_gpu_vram
from app.core.utils.file_utils import get_whisper_models_dir, get_fw_gpu_dir, get_fw_cpu_exe
from app.core.utils.ffmpeg_utils import get_ffmpeg_path
from app.core.utils.cache_utils import generate_asr_cache_key, get_cached_asr_result, save_asr_cache

_GPU_EXE_NAME = "faster-whisper-xxl.exe"

_CPU_MODEL_ALIASES: dict[str, str] = {}

_EXE_MODEL_PREFIX = "faster-whisper-"
_EXE_DISTIL_PREFIX = "faster-distil-whisper-"

_WHISPER_LANG_TO_BCP47: dict[str, str] = {
    "english": "en-US", "en": "en-US",
    "chinese": "zh-CN", "zh": "zh-CN",
    "japanese": "ja-JP", "ja": "ja-JP",
    "korean": "ko-KR", "ko": "ko-KR",
    "french": "fr-FR", "fr": "fr-FR",
    "german": "de-DE", "de": "de-DE",
    "spanish": "es-ES", "es": "es-ES",
    "russian": "ru-RU", "ru": "ru-RU",
    "portuguese": "pt-BR", "pt": "pt-BR",
    "italian": "it-IT", "it": "it-IT",
    "arabic": "ar-SA", "ar": "ar-SA",
    "thai": "th-TH", "th": "th-TH",
    "vietnamese": "vi-VN", "vi": "vi-VN",
}


def _normalize_whisper_lang(raw: str) -> str:
    if not raw:
        return ""
    return _WHISPER_LANG_TO_BCP47.get(raw.strip().lower(), "")


class FasterWhisperASR(ASRBase):
    def __init__(self, gpu_mode: bool = False) -> None:
        self._gpu_mode = gpu_mode if gpu_mode else self._detect_gpu_mode()
        self._exe_path = self._resolve_exe()
        self._program_name = os.path.basename(self._exe_path)
        self._process: Optional[subprocess.Popen] = None

    def _detect_gpu_mode(self) -> bool:
        info = detect_cuda()
        if info["available"]:
            vram = int(info["vram_free_mb"])
            return vram >= 2048
        return False

    def _resolve_exe(self) -> str:
        if self._gpu_mode:
            gpu_dir = get_fw_gpu_dir()
            direct = os.path.join(gpu_dir, _GPU_EXE_NAME)
            if os.path.isfile(direct):
                return direct
            if os.path.isdir(gpu_dir):
                for name in os.listdir(gpu_dir):
                    sub = os.path.join(gpu_dir, name, _GPU_EXE_NAME)
                    if os.path.isfile(sub):
                        return sub

        xxl_in_path = shutil.which("faster-whisper-xxl")
        if xxl_in_path:
            return xxl_in_path

        cpu_path = get_fw_cpu_exe()
        if os.path.isfile(cpu_path):
            return cpu_path

        fw_in_path = shutil.which("faster-whisper")
        if fw_in_path:
            return fw_in_path

        if self._gpu_mode:
            return os.path.join(get_fw_gpu_dir(), _GPU_EXE_NAME)
        return cpu_path

    def _is_xxl(self) -> bool:
        return "xxl" in self._program_name.lower()

    def _resolve_model_name(self, model: str) -> str:
        if not self._is_xxl() and model in _CPU_MODEL_ALIASES:
            return _CPU_MODEL_ALIASES[model]
        return model

    def _ensure_model_alias(self, model_dir: str, original: str, alias: str) -> None:
        original_path = os.path.join(model_dir, original)
        alias_path = os.path.join(model_dir, alias)
        if os.path.isdir(alias_path):
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["cmd", "/c", "mklink", "/J", alias_path, original_path],
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                )
            else:
                os.symlink(original_path, alias_path, target_is_directory=True)
        except (subprocess.CalledProcessError, OSError):
            shutil.copytree(original_path, alias_path)

    def _exe_model_dir_name(self, model_name: str) -> str:
        if model_name.startswith("distil-"):
            return _EXE_DISTIL_PREFIX + model_name[len("distil-"):]
        return _EXE_MODEL_PREFIX + model_name

    def _ensure_exe_model_available(self, resolved_model: str, model_dir: str, original_model: str = "") -> None:
        if self._is_xxl():
            return

        exe_dir = os.path.dirname(self._exe_path)
        exe_models_dir = os.path.join(exe_dir, "_models")
        os.makedirs(exe_models_dir, exist_ok=True)

        exe_dir_name = self._exe_model_dir_name(resolved_model)
        exe_model_path = os.path.join(exe_models_dir, exe_dir_name)

        if os.path.isdir(exe_model_path):
            return

        source_path = os.path.join(model_dir, resolved_model)
        if not os.path.isdir(source_path) and original_model:
            source_path = os.path.join(model_dir, original_model)
        if not os.path.isdir(source_path):
            return

        try:
            if os.name == "nt":
                subprocess.run(
                    ["cmd", "/c", "mklink", "/J", exe_model_path, source_path],
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                )
            else:
                os.symlink(source_path, exe_model_path, target_is_directory=True)
        except (subprocess.CalledProcessError, OSError):
            shutil.copytree(source_path, exe_model_path)

    def _get_model_n_mels(self, model_name: str, model_dir: str) -> int | None:
        import json

        candidates = []
        if not self._is_xxl():
            exe_dir = os.path.dirname(self._exe_path)
            exe_dir_name = self._exe_model_dir_name(model_name)
            candidates.append(os.path.join(exe_dir, "_models", exe_dir_name, "config.json"))
        if model_dir:
            candidates.append(os.path.join(str(model_dir), model_name, "config.json"))

        for cfg_path in candidates:
            if os.path.isfile(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    return cfg.get("n_mels")
                except (json.JSONDecodeError, OSError):
                    continue
        return None

    def _validate_model_files(self, resolved_model: str, model_dir: str) -> None:
        import json

        model_dirs = []
        if not self._is_xxl():
            exe_dir = os.path.dirname(self._exe_path)
            exe_dir_name = self._exe_model_dir_name(resolved_model)
            model_dirs.append(os.path.join(exe_dir, "_models", exe_dir_name))
        if model_dir:
            model_dirs.append(os.path.join(str(model_dir), resolved_model))

        for mdir in model_dirs:
            vocab_json = os.path.join(mdir, "vocabulary.json")
            if not os.path.isfile(vocab_json):
                continue
            try:
                with open(vocab_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    raise RuntimeError(
                        f"模型文件损坏: {vocab_json}\n"
                        f"该文件应为 JSON 数组，但当前内容不是数组。\n"
                        f"这通常是因为模型下载时网络错误导致文件内容为服务端错误响应。\n\n"
                        f"建议: 在设置中删除并重新下载模型 '{resolved_model}'"
                    )
            except json.JSONDecodeError:
                raise RuntimeError(
                    f"模型文件损坏: {vocab_json}\n"
                    f"该文件不是有效的 JSON 格式。\n"
                    f"这通常是因为模型下载时网络错误导致文件内容不完整。\n\n"
                    f"建议: 在设置中删除并重新下载模型 '{resolved_model}'"
                )

    def _check_model_exe_compatibility(self, model: str, resolved_model: str, model_dir: str) -> None:
        if self._is_xxl():
            return

        n_mels = self._get_model_n_mels(resolved_model, str(model_dir))
        if n_mels != 128:
            return

        _CPU_NATIVE_128_MELS = {"large-v3"}
        if resolved_model in _CPU_NATIVE_128_MELS:
            return

        version = self.get_version()
        raise RuntimeError(
            f"模型 '{model}' 需要 128 个 mel 频率通道 (n_mels=128)，"
            f"但当前 FasterWhisper CPU 可执行文件 (版本 {version}) "
            f"未内置对该模型的识别，会默认使用 80 个通道，导致特征维度不匹配。\n\n"
            f"建议:\n"
            f"  1. 改用 'large-v3' 模型 (CPU 可执行文件原生支持)\n"
            f"  2. 切换到 GPU 模式 (如可用)\n"
            f"  3. 更新 FasterWhisper 可执行文件到支持该模型的较新版本"
        )

    def is_available(self) -> bool:
        if os.path.isfile(self._exe_path):
            return True
        return shutil.which(self._exe_path) is not None

    def get_version(self) -> str:
        if not self.is_available():
            return ""
        try:
            result = subprocess.run(
                [self._exe_path, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            output = (result.stdout or "").strip()
            if not output:
                output = (result.stderr or "").strip()
            match = re.search(r"[\d.]+", output)
            return match.group(0) if match else output
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    def _build_command(self, audio_path: str, **kwargs) -> list[str]:
        cmd = [self._exe_path]
        log_callback = kwargs.get("_log_callback")

        model = kwargs.get("model", "large-v3")
        resolved_model = self._resolve_model_name(model)

        model_dir = kwargs.get("model_dir", get_whisper_models_dir())

        if not self._is_xxl():
            if model_dir:
                self._ensure_exe_model_available(resolved_model, str(model_dir), model)
            cmd.extend(["-m", resolved_model])
        else:
            use_local_path = False
            if model_dir:
                resolved_model_path = os.path.join(str(model_dir), resolved_model)
                if os.path.isdir(resolved_model_path):
                    cmd.extend(["-m", resolved_model_path])
                    use_local_path = True
                else:
                    original_model_path = os.path.join(str(model_dir), model)
                    if os.path.isdir(original_model_path):
                        cmd.extend(["-m", original_model_path])
                        use_local_path = True

            if not use_local_path:
                cmd.extend(["-m", resolved_model])
                if model_dir:
                    cmd.extend(["--model_dir", str(model_dir)])

        cmd.extend(["--print_progress"])

        cmd.append(str(audio_path))

        language = kwargs.get("language", "")
        if language:
            cmd.extend(["-l", language])

        device = "cuda" if self._gpu_mode else "cpu"
        cmd.extend(["-d", device])

        # 优先使用 JSON 输出格式以获取词级时间戳
        output_format = kwargs.get("output_format", "json")
        cmd.extend(["--output_format", output_format])

        output_dir = kwargs.get("output_dir", "")
        if output_dir:
            cmd.extend(["-o", str(output_dir)])
        else:
            cmd.extend(["-o", "source"])

        vad_filter = kwargs.get("vad_filter", False)
        if vad_filter:
            cmd.extend(["--vad_filter", "true"])
            vad_threshold = kwargs.get("vad_threshold", 0.5)
            cmd.extend(["--vad_threshold", f"{vad_threshold:.2f}"])
        else:
            cmd.extend(["--vad_filter", "false"])

        one_word = kwargs.get("one_word", 1)
        sentence = kwargs.get("sentence", True)

        if output_format == "json":
            # JSON 输出：使用句子模式获取更好的分段和词级时间戳
            if sentence:
                cmd.extend([
                    "--sentence",
                    "--max_line_width", str(kwargs.get("max_line_width", 80)),
                    "--max_line_count", str(kwargs.get("max_line_count", 1)),
                    "--max_comma", str(kwargs.get("max_comma", 20)),
                    "--max_comma_cent", str(kwargs.get("max_comma_cent", 50)),
                ])
        elif one_word in (1, 2):
            cmd.extend(["--one_word", str(one_word)])
        elif sentence:
            cmd.extend([
                "--sentence",
                "--max_line_width", str(kwargs.get("max_line_width", 30)),
                "--max_line_count", str(kwargs.get("max_line_count", 1)),
                "--max_comma", str(kwargs.get("max_comma", 20)),
                "--max_comma_cent", str(kwargs.get("max_comma_cent", 50)),
            ])

        cmd.append("--beep_off")

        return cmd

    def _parse_srt(self, srt_text: str, audio_path: str, model: str, language: str, audio_offset: float = 0.0) -> SubtitleDocument:
        subtitles: list[SubtitleItem] = []

        blocks = re.split(r"\n\s*\n", srt_text.strip())
        idx = 0
        for block in blocks:
            lines = block.strip().split("\n")
            if len(lines) < 3:
                continue

            text_lines = lines[2:]
            text = " ".join(line.strip() for line in text_lines).strip()

            if not text:
                continue

            if (text.startswith("【") or text.startswith("[")
                    or text.startswith("(") or text.startswith("（")):
                continue

            time_match = re.match(
                r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})",
                lines[1].strip(),
            )
            if not time_match:
                continue

            start = self._srt_time_to_seconds(time_match, 1) + audio_offset
            end = self._srt_time_to_seconds(time_match, 5) + audio_offset

            idx += 1
            subtitles.append(
                SubtitleItem(
                    index=idx,
                    start=start,
                    end=end,
                    text=text,
                    words=[],
                    confidence=0.0,
                )
            )

        metadata = SubtitleMetadata(
            source_file=audio_path,
            source_lang=language,
            target_lang="",
            asr_model=model,
            source_step="01_asr",
        )

        return SubtitleDocument(metadata=metadata, subtitles=subtitles)

    @staticmethod
    def _srt_time_to_seconds(match: re.Match, group_offset: int) -> float:
        h = int(match.group(group_offset))
        m = int(match.group(group_offset + 1))
        s = int(match.group(group_offset + 2))
        ms = int(match.group(group_offset + 3))
        return h * 3600 + m * 60 + s + ms / 1000.0

    def _parse_json_file(self, json_path: str, audio_path: str, model: str, language: str, audio_offset: float = 0.0) -> Optional[SubtitleDocument]:
        """解析 faster-whisper JSON 输出文件，提取词级时间戳。"""
        import json as _json

        with open(json_path, "r", encoding="utf-8", errors="replace") as f:
            raw = _json.load(f)

        # 兼容两种 JSON 格式：
        # 1. 旧格式：顶层为列表 [...]
        # 2. 新格式（r192.3+）：顶层为字典 {"segments": [...], "language": "...", "text": "..."}
        detected_lang = ""
        if isinstance(raw, dict):
            detected_lang = _normalize_whisper_lang(raw.get("language", ""))
            data = raw.get("segments", [])
        else:
            data = raw

        if not isinstance(data, list) or not data:
            return None

        subtitles: list[SubtitleItem] = []
        for idx, segment in enumerate(data, start=1):
            words: list[SubtitleWord] = []
            for w in segment.get("words", []):
                words.append(
                    SubtitleWord(
                        word=w.get("word", ""),
                        start=w.get("start", 0.0) + audio_offset,
                        end=w.get("end", 0.0) + audio_offset,
                        probability=w.get("probability", 0.0),
                    )
                )

            confidence = 0.0
            if words:
                confidence = sum(w.probability for w in words) / len(words)

            text = segment.get("text", "").strip()
            if not text:
                continue

            if (text.startswith("【") or text.startswith("[")
                    or text.startswith("(") or text.startswith("（")):
                continue

            subtitles.append(
                SubtitleItem(
                    index=idx,
                    start=segment.get("start", 0.0) + audio_offset,
                    end=segment.get("end", 0.0) + audio_offset,
                    text=text,
                    words=words,
                    confidence=confidence,
                )
            )

        if not subtitles:
            return None

        metadata = SubtitleMetadata(
            source_file=audio_path,
            source_lang=language or detected_lang,
            target_lang="",
            asr_model=model,
            source_step="01_asr",
        )

        return SubtitleDocument(metadata=metadata, subtitles=subtitles)

    def _parse_output(
        self,
        raw_output: str,
        audio_path: str,
        model: str,
        language: str,
        audio_offset: float = 0.0,
    ) -> SubtitleDocument:
        import json

        json_match = re.search(r"\[.*\]", raw_output, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(0))
                if isinstance(parsed, list) and parsed:
                    subtitles: list[SubtitleItem] = []
                    for idx, segment in enumerate(parsed, start=1):
                        words: list[SubtitleWord] = []
                        for w in segment.get("words", []):
                            words.append(
                                SubtitleWord(
                                    word=w.get("word", ""),
                                    start=w.get("start", 0.0) + audio_offset,
                                    end=w.get("end", 0.0) + audio_offset,
                                    probability=w.get("probability", 0.0),
                                )
                            )

                        confidence = 0.0
                        if words:
                            confidence = sum(w.probability for w in words) / len(words)

                        text = segment.get("text", "").strip()
                        if (text.startswith("【") or text.startswith("[")
                                or text.startswith("(") or text.startswith("（")):
                            continue

                        subtitles.append(
                            SubtitleItem(
                                index=idx,
                                start=segment.get("start", 0.0) + audio_offset,
                                end=segment.get("end", 0.0) + audio_offset,
                                text=text,
                                words=words,
                                confidence=confidence,
                            )
                        )

                    metadata = SubtitleMetadata(
                        source_file=audio_path,
                        source_lang=language,
                        target_lang="",
                        asr_model=model,
                        source_step="01_asr",
                    )
                    return SubtitleDocument(metadata=metadata, subtitles=subtitles)
            except (json.JSONDecodeError, KeyError):
                pass

        return self._parse_srt(raw_output, audio_path, model, language, audio_offset)

    def cancel(self):
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()

    def run_chunks(
        self,
        chunks: list[AudioChunk],
        original_audio_path: str,
        language: str = "",
        model: str = "large-v3",
        one_word: int = 1,
        sentence: bool = True,
        max_line_width: int = 80,
        max_line_count: int = 1,
        max_comma: int = 20,
        max_comma_cent: int = 50,
        progress_callback: Optional[Callable[[float], None]] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ) -> SubtitleDocument:
        """逐块转录分块音频，合并结果并对重叠区去重。

        流式去重：维护 finalized（已确定保留）和 pending（当前块字幕，待与下一块去重）。
        相邻块的重叠区内，前块尾部与后块头部通过文本匹配合并或按中点分割。
        """
        if not chunks:
            raise ValueError("音频分块列表为空")

        finalized: list[SubtitleItem] = []
        pending: list[SubtitleItem] = []
        total_chunks = len(chunks)
        max_retries = 10
        detected_lang = ""

        for i, chunk in enumerate(chunks):
            if log_callback:
                dur_str = f"拼接 {chunk.packed_duration():.1f}s" if chunk.is_vad else f"时长 {chunk.duration:.1f}s"
                log_callback(f"转录语块 {i + 1}/{total_chunks} (偏移 {chunk.offset:.1f}s, {dur_str})", "info")

            def chunk_progress(pct: float, _idx=i, _total=total_chunks) -> None:
                if progress_callback:
                    base = (_idx / _total) * 100.0
                    chunk_pct = (pct / 100.0) * (100.0 / _total)
                    progress_callback(min(base + chunk_pct, 100.0))

            chunk_audio_path = self._extract_chunk_audio(original_audio_path, chunk, i)
            if chunk_audio_path is None:
                if log_callback:
                    log_callback(f"语块 {i + 1} 音频提取失败，跳过", "warning")
                finalized.extend(pending)
                pending = []
                continue

            try:
                chunk_max_retries = 3 if (chunk.packed_duration() if chunk.is_vad else chunk.duration) < 3.0 else max_retries

                doc = None
                for attempt in range(1, chunk_max_retries + 1):
                    try:
                        doc = self.run(
                            chunk_audio_path,
                            language=language,
                            model=model,
                            vad_filter=False,
                            one_word=one_word,
                            sentence=sentence,
                            max_line_width=max_line_width,
                            max_line_count=max_line_count,
                            max_comma=max_comma,
                            max_comma_cent=max_comma_cent,
                            progress_callback=chunk_progress,
                            log_callback=log_callback,
                            use_cache=False,
                            audio_offset=0.0 if chunk.is_vad else chunk.offset,
                        )
                        break
                    except Exception as e:
                        if attempt < chunk_max_retries:
                            if log_callback:
                                log_callback(f"语块 {i + 1} 转录失败 (第 {attempt} 次)，1 秒后重试: {e}", "warning")
                            time.sleep(1)
                        else:
                            if log_callback:
                                log_callback(f"语块 {i + 1} 转录失败，已重试 {chunk_max_retries} 次，跳过该语块: {e}", "warning")

                if doc is None:
                    finalized.extend(pending)
                    pending = []
                    continue

                if not language and not detected_lang and doc.metadata.source_lang:
                    detected_lang = doc.metadata.source_lang

                chunk_subs = doc.subtitles

                if chunk.is_vad:
                    for sub in chunk_subs:
                        sub.start = chunk.relative_to_absolute(sub.start)
                        sub.end = chunk.relative_to_absolute(sub.end)
                        for w in sub.words:
                            w.start = chunk.relative_to_absolute(w.start)
                            w.end = chunk.relative_to_absolute(w.end)

                if i == 0:
                    pending = chunk_subs
                    continue

                if chunk.is_vad:
                    # VAD 精确分割无重叠，直接收集，跳过重叠去重
                    finalized.extend(pending)
                    pending = chunk_subs
                    continue

                # 计算与上一块的重叠区
                prev_chunk = chunks[i - 1]
                overlap_start = chunk.offset
                overlap_end = min(prev_chunk.offset + prev_chunk.duration, chunk.offset + chunk.duration)

                if overlap_end > overlap_start and pending and chunk_subs:
                    pending_before = [s for s in pending if s.end <= overlap_start]
                    pending_in_overlap = [s for s in pending if s.end > overlap_start]
                    curr_in_overlap = [s for s in chunk_subs if s.start < overlap_end]
                    curr_after = [s for s in chunk_subs if s.start >= overlap_end]

                    if pending_in_overlap and curr_in_overlap:
                        merged, kept_prev, kept_curr = self._merge_overlap_region(
                            pending_in_overlap, curr_in_overlap, overlap_start, overlap_end
                        )
                        finalized.extend(pending_before)
                        finalized.extend(kept_prev)
                        finalized.extend(merged)
                        pending = kept_curr + curr_after
                    else:
                        finalized.extend(pending_before)
                        finalized.extend(pending_in_overlap)
                        pending = list(chunk_subs)
                else:
                    finalized.extend(pending)
                    pending = list(chunk_subs)
            finally:
                try:
                    os.remove(chunk_audio_path)
                except OSError:
                    pass

        finalized.extend(pending)

        # 语义合并：修复去重后可能产生的不完整句
        finalized = self._merge_incomplete_after_dedup(finalized)

        # 时间戳后处理（修复倒退、零时长、异常跨度等）
        finalized = self._postprocess_timestamps(finalized)

        for idx, item in enumerate(finalized, start=1):
            item.index = idx

        metadata = SubtitleMetadata(
            source_file=original_audio_path,
            source_lang=language or detected_lang,
            target_lang="",
            asr_model=model,
            source_step="01_asr",
        )

        if progress_callback:
            progress_callback(100.0)

        return SubtitleDocument(metadata=metadata, subtitles=finalized)

    @staticmethod
    def _merge_overlap_region(
        prev_subs: list[SubtitleItem],
        curr_subs: list[SubtitleItem],
        overlap_start: float,
        overlap_end: float,
    ) -> tuple[list[SubtitleItem], list[SubtitleItem], list[SubtitleItem]]:
        """在重叠区 [overlap_start, overlap_end] 内去重。

        - 文本相似度 ≥ 0.5 的配对合并为一条（取更长文本，时间戳取并集）
        - 未匹配的按重叠区中点分割：前块保留中点前，后块从中点开始

        返回 (merged, kept_prev, kept_curr)。
        """
        if not prev_subs or not curr_subs:
            return [], list(prev_subs), list(curr_subs)

        mid = (overlap_start + overlap_end) / 2.0

        merged: list[SubtitleItem] = []
        used_curr: set[int] = set()
        unmatched_prev: list[SubtitleItem] = []

        for p in prev_subs:
            best_idx = -1
            best_score = 0.0
            for j, c in enumerate(curr_subs):
                if j in used_curr:
                    continue
                if c.start >= p.end or c.end <= p.start:
                    continue
                score = FasterWhisperASR._text_similarity(p.text, c.text)
                if score > best_score:
                    best_score = score
                    best_idx = j

            if best_idx >= 0 and best_score >= 0.5:
                c = curr_subs[best_idx]
                used_curr.add(best_idx)
                text = p.text if len(p.text) >= len(c.text) else c.text
                words = list(p.words) if len(p.words) >= len(c.words) else list(c.words)
                merged.append(SubtitleItem(
                    index=0,
                    start=min(p.start, c.start),
                    end=max(p.end, c.end),
                    text=text,
                    words=words,
                    confidence=max(p.confidence, c.confidence),
                ))
            else:
                unmatched_prev.append(p)

        unmatched_curr = [c for j, c in enumerate(curr_subs) if j not in used_curr]

        kept_prev: list[SubtitleItem] = []
        for p in unmatched_prev:
            if p.end <= mid:
                kept_prev.append(p)
            elif p.start >= mid:
                continue
            else:
                truncated = FasterWhisperASR._truncate_at_words(p, mid, keep_before=True)
                if truncated is not None:
                    kept_prev.append(truncated)

        kept_curr: list[SubtitleItem] = []
        for c in unmatched_curr:
            if c.start >= mid:
                kept_curr.append(c)
            elif c.end <= mid:
                continue
            else:
                truncated = FasterWhisperASR._truncate_at_words(c, mid, keep_before=False)
                if truncated is not None:
                    kept_curr.append(truncated)

        return merged, kept_prev, kept_curr

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        """基于词集合的重叠比例（取较小集合为基准）。"""
        tokens_a = set(re.findall(r'\w+', a.lower()))
        tokens_b = set(re.findall(r'\w+', b.lower()))
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        base = min(len(tokens_a), len(tokens_b))
        return len(intersection) / base if base > 0 else 0.0

    @staticmethod
    def _truncate_at_words(item: SubtitleItem, split_time: float, keep_before: bool) -> Optional[SubtitleItem]:
        """按词级时间戳截断字幕。

        keep_before=True: 保留 start < split_time 的词（前半段）
        keep_before=False: 保留 start >= split_time 的词（后半段）
        无 words 时仅调整时间戳，文本保留原文。无词可保留时返回 None。
        """
        if not item.words:
            new_start = item.start if keep_before else split_time
            new_end = split_time if keep_before else item.end
            return SubtitleItem(
                index=0, start=new_start, end=new_end,
                text=item.text, words=[], confidence=item.confidence,
            )

        if keep_before:
            kept = [w for w in item.words if w.start < split_time]
            if not kept:
                return None
            text = " ".join(w.word for w in kept)
            return SubtitleItem(
                index=0, start=item.start, end=kept[-1].end,
                text=text, words=kept, confidence=item.confidence,
            )
        else:
            kept = [w for w in item.words if w.start >= split_time]
            if not kept:
                return None
            text = " ".join(w.word for w in kept)
            return SubtitleItem(
                index=0, start=kept[0].start, end=item.end,
                text=text, words=kept, confidence=item.confidence,
            )

    @staticmethod
    def _merge_incomplete_after_dedup(items: list[SubtitleItem]) -> list[SubtitleItem]:
        """去重后的语义合并：合并不完整句和短片段，保证上下文完整。

        规则：
        1. 间隔超过 3s 不合并
        2. 合并后时长超过 10s 不合并
        3. 前条不以句末标点结尾且后条不以大写开头 → 合并
        4. 后条时长 < 1s 且不以大写开头 → 合并到前条
        """
        if not items:
            return items

        sorted_items = sorted(items, key=lambda s: s.start)
        merged: list[SubtitleItem] = [sorted_items[0]]

        MAX_GAP = 3.0
        MAX_MERGED_DURATION = 10.0

        for item in sorted_items[1:]:
            prev = merged[-1]
            prev_text = prev.text.rstrip()
            curr_text = item.text.strip()

            if not curr_text:
                merged.append(item)
                continue

            gap = item.start - prev.end
            if gap > MAX_GAP:
                merged.append(item)
                continue

            if (item.end - prev.start) > MAX_MERGED_DURATION:
                merged.append(item)
                continue

            prev_ends_sentence = bool(prev_text) and prev_text[-1] in '.!?。！？'
            curr_starts_upper = curr_text[0].isupper() if curr_text else False

            should_merge = False
            if not prev_ends_sentence and not curr_starts_upper:
                should_merge = True

            if (item.end - item.start) < 1.0 and not curr_starts_upper:
                should_merge = True

            if should_merge:
                new_text = (prev_text + " " + curr_text).strip()
                new_words = list(prev.words) + list(item.words)
                merged[-1] = SubtitleItem(
                    index=prev.index,
                    start=prev.start,
                    end=item.end,
                    text=new_text,
                    words=new_words,
                    confidence=prev.confidence,
                )
            else:
                merged.append(item)

        return merged

    @staticmethod
    def _extract_chunk_audio(audio_path: str, chunk: AudioChunk, chunk_index: int) -> Optional[str]:
        """使用 ffmpeg 从原始音频提取片段到临时 wav。

        单段（定长或单段 VAD）：用 -ss -t 切片。
        多段 VAD：用 filter_complex atrim+concat 拼接去静音；段数 >32 回退 concat demuxer。
        """
        import subprocess as sp

        tmp_dir = os.path.join(tempfile.gettempdir(), "deepsub_asr_chunks")
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_path = os.path.join(tmp_dir, f"chunk_{chunk_index:04d}.wav")

        ffmpeg = get_ffmpeg_path()

        if chunk.is_vad and len(chunk.segments) > 1:
            return FasterWhisperASR._extract_concat_audio(
                ffmpeg, audio_path, chunk.segments, tmp_path, chunk_index, tmp_dir
            )

        cmd = [
            ffmpeg,
            "-i", audio_path,
            "-ss", f"{chunk.offset:.3f}",
            "-t", f"{chunk.duration:.3f}",
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            "-y",
            tmp_path,
        ]

        try:
            result = sp.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
            )
            if result.returncode != 0:
                return None
            if not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) < 44:
                return None
            return tmp_path
        except Exception:
            return None

    @staticmethod
    def _extract_concat_audio(
        ffmpeg: str,
        audio_path: str,
        segments: list,
        tmp_path: str,
        chunk_index: int,
        tmp_dir: str,
    ) -> Optional[str]:
        """多段拼接：filter_complex atrim+concat；段数过多回退 concat demuxer。"""
        import subprocess as sp

        if len(segments) <= 32:
            filters = []
            for i, seg in enumerate(segments):
                filters.append(
                    f"[0:a]atrim=start={seg.start:.3f}:end={seg.end:.3f},asetpts=PTS-STARTPTS[a{i}]"
                )
            concat_inputs = "".join(f"[a{i}]" for i in range(len(segments)))
            filters.append(f"{concat_inputs}concat=n={len(segments)}:v=0:a=1[out]")
            filter_complex = ";".join(filters)
            cmd = [
                ffmpeg,
                "-i", audio_path,
                "-filter_complex", filter_complex,
                "-map", "[out]",
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                "-y",
                tmp_path,
            ]
            try:
                result = sp.run(
                    cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
                )
                if result.returncode == 0 and os.path.isfile(tmp_path) and os.path.getsize(tmp_path) >= 44:
                    return tmp_path
            except Exception:
                pass

        seg_paths: list[str] = []
        list_path = os.path.join(tmp_dir, f"chunk_{chunk_index:04d}_concat.txt")
        try:
            for i, seg in enumerate(segments):
                seg_path = os.path.join(tmp_dir, f"chunk_{chunk_index:04d}_seg{i:03d}.wav")
                cmd = [
                    ffmpeg,
                    "-i", audio_path,
                    "-ss", f"{seg.start:.3f}",
                    "-t", f"{seg.end - seg.start:.3f}",
                    "-vn",
                    "-acodec", "pcm_s16le",
                    "-ar", "16000",
                    "-ac", "1",
                    "-y",
                    seg_path,
                ]
                r = sp.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
                if r.returncode != 0 or not os.path.isfile(seg_path):
                    return None
                seg_paths.append(seg_path)

            with open(list_path, "w", encoding="utf-8") as f:
                for p in seg_paths:
                    f.write(f"file '{p}'\n")

            cmd = [
                ffmpeg,
                "-f", "concat",
                "-safe", "0",
                "-i", list_path,
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                "-y",
                tmp_path,
            ]
            result = sp.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            if result.returncode == 0 and os.path.isfile(tmp_path) and os.path.getsize(tmp_path) >= 44:
                return tmp_path
            return None
        finally:
            for p in seg_paths:
                try:
                    os.remove(p)
                except OSError:
                    pass
            try:
                os.remove(list_path)
            except OSError:
                pass

    @staticmethod
    def _clean_hallucination(text: str) -> str:
        """清洗 ASR 幻觉文本：去除连续重复词。

        检测模式如 "Your Your energy energy tanks tanks."，
        将重复词合并为单个词。
        """
        # 匹配连续重复的词（不区分大小写），如 "Your Your" 或 "energy energy"
        cleaned = re.sub(
            r'\b(\w+)\s+\1\b',
            r'\1',
            text,
            flags=re.IGNORECASE,
        )
        # 多轮清理，处理 "A A B B" → "A B"（第一轮只处理 "A A" → "A", "B B" → "B"）
        prev = ""
        while cleaned != prev:
            prev = cleaned
            cleaned = re.sub(
                r'\b(\w+)\s+\1\b',
                r'\1',
                cleaned,
                flags=re.IGNORECASE,
            )
        return cleaned

    @staticmethod
    def _postprocess_timestamps(subtitles: list[SubtitleItem], max_duration: float = 30.0) -> list[SubtitleItem]:
        """修复时间戳异常。

        处理：
        1. 清洗 ASR 幻觉文本（连续重复词）
        2. 按 start 时间排序
        3. 修复时间戳倒退（start < 前一条 end）
        4. 修复零时长（end <= start）
        5. 修复异常跨度（单条 > max_duration）
        6. 修复时间戳压缩（字符/秒比异常高）
        7. 合并极短碎片（时长 < 0.3s 且文本不完整）
        """
        if not subtitles:
            return subtitles

        # 1. 清洗 ASR 幻觉文本
        for item in subtitles:
            cleaned = FasterWhisperASR._clean_hallucination(item.text)
            if cleaned != item.text:
                # 同步清洗 words 中的重复词
                if item.words:
                    cleaned_words = FasterWhisperASR._clean_hallucination(
                        " ".join(w.word for w in item.words)
                    )
                    # 如果 words 文本也被清洗，标记 words 需要重建
                    original_words_text = " ".join(w.word for w in item.words)
                    if cleaned_words != original_words_text:
                        item.words = []  # 清洗后 words 无法精确对应，清空让后续重新对齐
                item.text = cleaned

        # 2. 按 start 时间排序
        subtitles.sort(key=lambda s: s.start)

        # 3 & 4. 修复倒退和零时长
        for i, item in enumerate(subtitles):
            if item.end <= item.start:
                item.end = item.start + 0.1

            if i > 0:
                prev = subtitles[i - 1]
                if item.start < prev.end:
                    # 时间戳倒退，调整 start 到前一条的 end
                    item.start = prev.end
                    if item.end <= item.start:
                        item.end = item.start + 0.1

        # 5. 修复异常跨度（放在词级对齐之后，见 7.7）

        # 6. 修复时间戳压缩（字符/秒比异常高，疑似幻觉残留）
        # 正常英语语速约 15 字符/秒，超过 40 字符/秒视为异常
        MAX_CHARS_PER_SECOND = 40.0
        for i, item in enumerate(subtitles):
            duration = item.end - item.start
            if duration <= 0:
                continue
            text = item.text.strip()
            chars_per_second = len(text) / duration
            if chars_per_second > MAX_CHARS_PER_SECOND and len(text.split()) > 3:
                # 基于前后正常条目的语速估算合理时长
                estimated_duration = len(text) / 15.0  # 按 15 字符/秒估算
                # 用前后条目估算更合理的语速
                neighbors = []
                for j in [i - 1, i + 1]:
                    if 0 <= j < len(subtitles):
                        nd = subtitles[j].end - subtitles[j].start
                        if nd > 0:
                            ntext = subtitles[j].text.strip()
                            n_cps = len(ntext) / nd
                            # 只使用正常语速的邻居作为参考
                            if n_cps < MAX_CHARS_PER_SECOND:
                                neighbors.append(n_cps)
                if neighbors:
                    avg_speed = sum(neighbors) / len(neighbors)
                    if avg_speed > 0:
                        estimated_duration = len(text) / avg_speed
                # 限制扩展范围：不超过估算时长的上限
                max_extend = min(estimated_duration, duration * 10)
                item.end = item.start + max(max_extend, duration)

        # 6.5 修复扩展后可能产生的时间戳重叠
        for i, item in enumerate(subtitles):
            if i > 0:
                prev = subtitles[i - 1]
                if item.start < prev.end:
                    item.start = prev.end
                    if item.end <= item.start:
                        item.end = item.start + 0.1

        # 7. 合并极短碎片
        merged: list[SubtitleItem] = []
        for item in subtitles:
            duration = item.end - item.start
            text = item.text.strip()

            # 判断是否为不完整文本（不以标点结尾，且很短）
            is_incomplete = (
                duration < 1.0 and
                text and
                text[-1] not in '.!?。！？' and
                len(text.split()) <= 5
            )

            # 以标点结尾但时长极短且文本是前一条尾部子串的碎片
            is_short_punctuated = (
                duration < 0.5 and
                text and
                merged and
                text[-1] in '.!?。！？' and
                len(text.split()) <= 5
            )
            if is_short_punctuated and merged:
                prev_text_clean = re.sub(r'[^\w]', '', merged[-1].text.lower())
                item_text_clean = re.sub(r'[^\w]', '', text.lower())
                if item_text_clean and item_text_clean in prev_text_clean:
                    is_short_punctuated = False  # 是前一条的子串重复，不合并

            if (is_incomplete or is_short_punctuated) and merged:
                # 合并到前一条
                prev = merged[-1]
                prev.end = item.end
                prev.text = prev.text.rstrip() + " " + text
                # 合并 words
                if hasattr(prev, 'words') and hasattr(item, 'words'):
                    prev.words.extend(item.words)
            else:
                merged.append(item)

        # 7.5 词级时间戳对齐：FasterWhisper 的 segment.start/end 是 VAD/解码
        #     窗口边界，常比首词实际发音时刻早数秒（长静音片段差 5 秒以上很
        #     常见），导致字幕远早于声音出现、并在静音里停留几十秒。词级时间
        #     戳是逐词对齐结果，精度远高于 segment 边界，因此只要词时间戳与
        #     所在 segment 自洽，就直接以首词/末词时间戳为准。
        #     仅收紧（start 后移、end 前移），不放宽，避免把误差放大。
        for item in merged:
            if not item.words:
                continue
            # 过滤无效词时间戳（start<0 或 end<=start 视为无效）
            valid_words = [w for w in item.words if w.start >= 0 and w.end > w.start]
            if not valid_words:
                continue
            first_start = min(w.start for w in valid_words)
            last_end = max(w.end for w in valid_words)

            # 自洽性校验：词时间戳必须与 segment 区间有明显交集，
            # 否则视为词时间戳异常，保持 segment 原值不动。
            WORD_TOLERANCE = 6.0
            if not (item.start - WORD_TOLERANCE <= first_start <= item.end):
                continue
            if not (item.start <= last_end <= item.end + WORD_TOLERANCE):
                continue
            if last_end - first_start <= 0:
                continue

            if first_start > item.start:
                item.start = first_start
            if last_end < item.end:
                item.end = last_end
            if item.end <= item.start:
                item.end = item.start + 0.1

        # 7.6 无词级时间戳的条目：只有很粗的 segment 边界，可能横跨十几秒
        #     静音。经验上 segment 的 start 比 end 更不可靠（start 常早数秒），
        #     因此按全片平均语速估出文本所需时长后，把条目"贴"到 segment 末尾，
        #     而不是让它从很早的位置一直停留到说话结束。
        chars_per_second = FasterWhisperASR._estimate_chars_per_second(merged)
        for item in merged:
            if item.words:
                continue
            text = re.sub(r"\s+", "", item.text)
            duration = item.end - item.start
            if not text or duration <= 0:
                continue
            expected = len(text) / chars_per_second if chars_per_second > 0 else duration
            allowed = min(max(expected * 1.8 + 1.0, 1.5), 14.0)
            if duration > allowed:
                item.start = max(0.0, item.end - allowed)

        # 7.7 兜底：任何情况下单条字幕都不该超过 max_duration，
        #     否则一条字幕会横跨整段静音（截断时间戳但保留文本）。
        for item in merged:
            duration = item.end - item.start
            if duration > max_duration:
                item.end = item.start + max_duration

        # 8. 字幕时间 padding：为每条字幕向前后扩展一小段时间，
        #    确保字幕显示比说话前后略长，避免字幕出现晚于说话、消失早于说话结束
        SUBTITLE_LEAD_PADDING = 0.15   # 字幕提前显示时间（秒）
        SUBTITLE_TRAIL_PADDING = 0.25  # 字幕延后消失时间（秒）
        MIN_GAP = 0.02                 # 相邻字幕之间的最小间隔（秒）
        for i, item in enumerate(merged):
            # 向前扩展 start：可以提前 padding，但不能越过上一条的结束时间。
            # 注意这里必须取"更晚的那个"（max），早期版本误用 min 把每条字幕
            # 的 start 都吸附到前一条的 end 上，导致字幕整体提前 1~5 秒出现。
            new_start = max(0.0, item.start - SUBTITLE_LEAD_PADDING)
            if i > 0:
                new_start = max(new_start, merged[i - 1].end + MIN_GAP)

            # 向后扩展 end，但不能越过下一条的 start
            new_end = item.end + SUBTITLE_TRAIL_PADDING
            if i < len(merged) - 1:
                new_end = min(new_end, merged[i + 1].start - MIN_GAP)
                new_end = max(new_end, item.start + 0.1)  # 保证时长为正

            item.start = new_start
            item.end = new_end

        # 8.5 收尾：padding 是按"旧的下一条 start"算的，可能又压出 0.1s 级的
        #     重叠，这里再兜一次，确保相邻字幕严格不重叠。
        for i in range(len(merged) - 1):
            curr = merged[i]
            nxt = merged[i + 1]
            if curr.end > nxt.start - MIN_GAP:
                curr.end = nxt.start - MIN_GAP
            if curr.end <= curr.start:
                curr.end = curr.start + 0.1
                if curr.end > nxt.start:
                    nxt.start = curr.end

        return merged

    @staticmethod
    def _estimate_chars_per_second(subtitles: list[SubtitleItem]) -> float:
        """用全片"有效字符数 / 词级时长"的中位数估算语速（字符/秒）。"""
        ratios: list[float] = []
        for item in subtitles:
            valid = [w for w in item.words if w.end > w.start]
            if not valid:
                continue
            span = max(w.end for w in valid) - min(w.start for w in valid)
            text = re.sub(r"\s+", "", item.text)
            if span >= 0.5 and len(text) >= 4:
                ratios.append(len(text) / span)
        if not ratios:
            return 4.0
        ratios.sort()
        return ratios[len(ratios) // 2]

    def run(
        self,
        audio_path: str,
        language: str = "",
        model: str = "large-v3",
        vad_filter: bool = False,
        vad_threshold: float = 0.5,
        one_word: int = 1,
        sentence: bool = True,
        max_line_width: int = 80,
        max_line_count: int = 1,
        max_comma: int = 20,
        max_comma_cent: int = 50,
        progress_callback: Optional[Callable[[float], None]] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
        use_cache: bool = True,
        audio_offset: float = 0.0,
    ) -> SubtitleDocument:
        if not self.is_available():
            raise FileNotFoundError(f"FasterWhisper executable not found: {self._exe_path}")

        if not os.path.isfile(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        model_dir = get_whisper_models_dir()
        resolved_model = self._resolve_model_name(model)
        if model_dir:
            model_path = os.path.join(str(model_dir), model)
            resolved_model_path = os.path.join(str(model_dir), resolved_model)
            if not os.path.isdir(model_path) and not os.path.isdir(resolved_model_path):
                installed = [
                    d for d in os.listdir(str(model_dir))
                    if os.path.isdir(os.path.join(str(model_dir), d))
                ] if os.path.isdir(str(model_dir)) else []
                hint = f" Available models: {installed}" if installed else ""
                raise FileNotFoundError(
                    f"Model '{model}' not found in {model_dir}.{hint}"
                )
            if resolved_model != model and not os.path.isdir(resolved_model_path) and os.path.isdir(model_path):
                self._ensure_model_alias(str(model_dir), model, resolved_model)

        self._validate_model_files(resolved_model, model_dir)
        self._check_model_exe_compatibility(model, resolved_model, model_dir)

        kwargs = {
            "model": model,
            "language": language,
            "vad_filter": vad_filter,
            "vad_threshold": vad_threshold,
            "one_word": one_word,
            "sentence": sentence,
            "max_line_width": max_line_width,
            "max_line_count": max_line_count,
            "max_comma": max_comma,
            "max_comma_cent": max_comma_cent,
            "_log_callback": log_callback,
        }

        temp_dir = Path(tempfile.gettempdir()) / "deepsub_asr"
        os.makedirs(temp_dir, exist_ok=True)

        with tempfile.TemporaryDirectory(dir=temp_dir) as temp_path:
            wav_path = os.path.join(temp_path, "audio.wav")

            shutil.copy2(audio_path, wav_path)

            kwargs["output_dir"] = temp_path
            cmd = self._build_command(wav_path, **kwargs)

            if use_cache:
                cache_key = generate_asr_cache_key(audio_path, cmd)
                cached = get_cached_asr_result(cache_key)
                if cached is not None:
                    if progress_callback:
                        progress_callback(100.0)
                    return SubtitleDocument.from_dict(cached)

            creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creation_flags,
            )

            progress_pattern = re.compile(r"(\d+(?:\.\d+)?)\s*%")
            output_lines: list[str] = []
            _vad_keywords = ("vad", "silero", "voice activity", "speech detection")

            while self._process.poll() is None:
                line = self._process.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                output_lines.append(line)

                if log_callback:
                    line_lower = line.lower()
                    if any(kw in line_lower for kw in _vad_keywords):
                        if "error" in line_lower or "fail" in line_lower or "warn" in line_lower:
                            log_callback(f"VAD: {line}", "error")
                        elif "load" in line_lower or "init" in line_lower or "start" in line_lower:
                            log_callback(f"VAD: {line}", "info")

                if progress_callback:
                    match = progress_pattern.search(line)
                    if match:
                        try:
                            pct = float(match.group(1))
                            mapped = int(5 + (min(pct, 100.0) * 0.9))
                            progress_callback(mapped)
                        except (ValueError, TypeError):
                            pass

                    if "Subtitles are written to" in line:
                        progress_callback(100.0)

            remaining = self._process.stdout.read()
            if remaining:
                for rl in remaining.strip().splitlines():
                    stripped = rl.strip()
                    if stripped:
                        output_lines.append(stripped)

            return_code = self._process.wait()
            self._process = None

            # 优先尝试读取 JSON 输出（含词级时间戳）
            output_json = os.path.join(temp_path, "audio.json")
            if not os.path.isfile(output_json):
                for fname in os.listdir(temp_path):
                    if fname.lower().endswith(".json"):
                        output_json = os.path.join(temp_path, fname)
                        break

            if os.path.isfile(output_json):
                try:
                    doc = self._parse_json_file(output_json, audio_path, model, language, audio_offset)
                    if doc and doc.subtitles:
                        if progress_callback:
                            progress_callback(100.0)
                        if use_cache:
                            save_asr_cache(cache_key, doc.to_dict(), audio_path)
                        return doc
                    else:
                        if log_callback:
                            log_callback(f"JSON 输出文件已找到但解析结果为空: {output_json}", "warning")
                except Exception as e:
                    if log_callback:
                        log_callback(f"JSON 输出文件解析失败: {e}", "warning")

            # 回退：读取 SRT 输出
            output_srt = os.path.join(temp_path, "audio.srt")

            # Fallback: search for any .srt file in the output directory
            if not os.path.isfile(output_srt):
                for fname in os.listdir(temp_path):
                    if fname.lower().endswith(".srt"):
                        output_srt = os.path.join(temp_path, fname)
                        break

            if os.path.isfile(output_srt):
                srt_text = ""
                with open(output_srt, "r", encoding="utf-8", errors="replace") as f:
                    srt_text = f.read()

                if srt_text.strip():
                    if progress_callback:
                        progress_callback(100.0)
                    doc = self._parse_srt(srt_text, audio_path, model, language, audio_offset)
                    if use_cache:
                        save_asr_cache(cache_key, doc.to_dict(), audio_path)
                    return doc

            tail = "\n".join(output_lines[-20:]) if output_lines else "(no output)"

            # 列出输出目录中的文件，辅助诊断
            dir_contents = []
            try:
                dir_contents = os.listdir(temp_path)
            except OSError:
                pass
            dir_info = f"Output directory contents: {dir_contents}" if dir_contents else "Output directory is empty or inaccessible"

            if "Invalid input features shape" in tail:
                n_mels = self._get_model_n_mels(resolved_model, str(model_dir))
                n_mels_hint = f" 模型需要 {n_mels} 个 mel 通道。" if n_mels else ""
                raise RuntimeError(
                    f"FasterWhisper CPU 可执行文件不支持模型 '{model}'。"
                    f"{n_mels_hint}\n"
                    f"可执行文件默认使用 80 个 mel 频率通道，与模型要求不匹配。\n\n"
                    f"建议:\n"
                    f"  1. 改用 'large-v3' 模型\n"
                    f"  2. 切换到 GPU 模式\n"
                    f"  3. 更新 FasterWhisper 可执行文件\n\n"
                    f"原始错误:\n{tail}"
                )

            raise RuntimeError(
                f"FasterWhisper did not produce output (exit code {return_code}).\n"
                f"{dir_info}\n"
                f"Command: {' '.join(cmd)}\n"
                f"Process output (last 20 lines):\n{tail}"
            )

