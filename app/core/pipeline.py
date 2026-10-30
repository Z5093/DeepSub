from __future__ import annotations

import gc
import os
import json
from typing import Optional, Callable
from datetime import datetime

from app.core.subtitle.models import SubtitleDocument, ProcessingState, SubtitleMetadata
from app.core.utils.file_utils import (
    get_output_dir,
    ensure_output_dir,
    get_audio_output_path,
    get_step_output_path,
    get_state_file_path,
    get_video_stem,
)
from app.common.signal_bus import signal_bus
from app.common.constants import (
    PROCESSING_STEPS,
    STEP_FILE_MAP,
    STATE_FILE,
    AUDIO_SUFFIX,
    OUTPUT_SUBDIR,
    BILINGUAL_LAYOUT_STACKED,
    DEFAULT_API_TIMEOUT_MS,
)
from app.common.config import config_manager
from app.core.utils.language import (
    is_auto_language,
    normalize_language,
    resolve_segmentation_language,
)


_PIPELINE_STEPS = [s for s in PROCESSING_STEPS if s != "download"]

_STEP_METHOD_MAP: dict[str, str] = {
    "audio": "_step_audio",
    "asr": "_step_asr",
    "split1": "_step_split1",
    "translate": "_step_translate",
    "split2": "_step_split2",
    "export": "_step_export",
}

_PREVIOUS_STEP_MAP: dict[str, Optional[str]] = {
    "audio": None,
    "asr": "audio",
    "split1": "asr",
    "translate": "split1",
    "split2": "translate",
    "export": "split2",
}


def _api_timeout_seconds(translate_config: dict) -> float:
    """远程 API 的请求超时（秒）。

    translate.timeout 以毫秒存储（与 llamacpp.timeout 单位一致），默认 15 分钟；
    本地 llama.cpp 的超时走 llamacpp.timeout（默认 30 分钟），两者互不影响。
    """
    try:
        timeout_ms = float(translate_config.get("timeout", DEFAULT_API_TIMEOUT_MS))
    except (TypeError, ValueError):
        timeout_ms = float(DEFAULT_API_TIMEOUT_MS)
    if timeout_ms <= 0:
        timeout_ms = float(DEFAULT_API_TIMEOUT_MS)
    return timeout_ms / 1000.0


class PipelineOrchestrator:
    def __init__(self):
        self._video_path: str = ""
        self._output_dir: str = ""
        self._state: Optional[ProcessingState] = None
        self._current_step: str = ""
        self._paused: bool = False
        self._cancelled: bool = False
        self._running: bool = False
        self._paused_step: str = ""  # 暂停时正在执行的步骤，恢复时从此步骤重新开始
        self._source_lang: str = "en-US"
        self._target_lang: str = "zh-CN"
        self._asr_model: str = "large-v3"
        self._gpu_mode: bool = False
        self._log_callback: Optional[Callable[[str, str], None]] = None
        self._progress_callback: Optional[Callable[[int], None]] = None
        self._enable_translation: bool = True

    def set_video(self, video_path: str):
        self._video_path = video_path
        self._output_dir = ensure_output_dir(video_path)
        self._init_state()

    def set_params(self, source_lang: str = "en-US", target_lang: str = "zh-CN",
                   asr_model: str = "large-v3", gpu_mode: bool = False,
                   enable_translation: bool = True):
        self._source_lang = source_lang
        self._target_lang = target_lang
        self._asr_model = asr_model
        self._gpu_mode = gpu_mode
        self._enable_translation = enable_translation

    def set_callbacks(self, log_callback: Optional[Callable] = None,
                      progress_callback: Optional[Callable] = None):
        self._log_callback = log_callback
        self._progress_callback = progress_callback

    def start(self, from_step: str = "") -> bool:
        if self._running:
            self._log("流水线已在运行中", "warning")
            return False

        if not self._video_path:
            self._log("未设置视频文件", "error")
            return False

        if not os.path.isfile(self._video_path):
            self._log(f"视频文件不存在: {self._video_path}", "error")
            return False

        self._running = True
        self._paused = False
        self._cancelled = False

        if from_step:
            start_step = from_step
        elif self._paused_step:
            # 从暂停的步骤重新开始
            start_step = self._paused_step
            self._paused_step = ""
        elif self._state and self._state.completed_steps:
            start_step = self._find_next_step()
        else:
            start_step = _PIPELINE_STEPS[0]

        if not start_step:
            self._log("所有步骤已完成", "info")
            signal_bus.pipeline_completed.emit()
            self._running = False
            return True

        signal_bus.pipeline_started.emit()
        self._log(f"开始处理: {os.path.basename(self._video_path)}", "info")

        step_index = _PIPELINE_STEPS.index(start_step) if start_step in _PIPELINE_STEPS else 0

        for i in range(step_index, len(_PIPELINE_STEPS)):
            if self._cancelled:
                self._log("处理已取消", "warning")
                break

            if self._paused:
                # 记录暂停时正在执行的步骤，恢复时从此步骤重新开始
                self._paused_step = _PIPELINE_STEPS[i]
                self._log(f"处理已暂停，恢复时将从步骤 {self._paused_step} 重新开始", "warning")
                signal_bus.pipeline_paused.emit()
                break

            step_id = _PIPELINE_STEPS[i]

            if step_id in (self._state.completed_steps if self._state else []):
                continue

            success = self._run_step(step_id)

            if not success:
                self._log(f"步骤 {step_id} 失败，流水线停止", "error")
                break

            if self._state:
                self._state.completed_steps.append(step_id)
                now = datetime.now().isoformat(timespec="seconds")
                self._state.step_timestamps[step_id] = {
                    "completed_at": now,
                }
                self._state.current_step = step_id
                self._save_state()

            signal_bus.pipeline_step_completed.emit(step_id)

        if not self._cancelled and not self._paused:
            if self._state and set(self._state.completed_steps) >= set(_PIPELINE_STEPS):
                signal_bus.pipeline_completed.emit()
                self._log("所有步骤已完成", "info")
                # 任务完成后清理后台资源
                self.cleanup()

        self._running = False
        self._current_step = ""
        return True

    def pause(self):
        self._paused = True

    def resume(self):
        """恢复暂停的流水线，从暂停时的步骤重新开始。"""
        if not self._paused_step:
            # 没有暂停步骤，正常恢复
            self._paused = False
            signal_bus.pipeline_resumed.emit()
            return

        self._paused = False
        signal_bus.pipeline_resumed.emit()

    def get_paused_step(self) -> str:
        """获取暂停时的步骤名称。"""
        return self._paused_step

    def cancel(self):
        self._cancelled = True
        self.cleanup()
        signal_bus.pipeline_stopped.emit()

    def cleanup(self):
        """清理后台资源：停止 llama.cpp server、释放内存。"""
        self._stop_llamacpp_server()
        gc.collect()
        self._log("后台资源已清理", "dim")

    def _stop_llamacpp_server(self):
        """停止 llama.cpp server 进程（如果正在运行）。"""
        try:
            from app.core.llamacpp.server import LlamaCppServer
            translate_config = config_manager.get_section("translate")
            service = translate_config.get("service", "openai_compat")
            if service == "llamacpp":
                llamacpp_config = config_manager.get_section("llamacpp")
                server = LlamaCppServer(llamacpp_config)
                if server.is_running:
                    self._log("正在停止 llama.cpp server...", "info")
                    server.stop()
                    self._log("llama.cpp server 已停止", "info")
                    signal_bus.llamacpp_server_stopped.emit()
        except Exception as e:
            self._log(f"停止 llama.cpp server 时出错: {e}", "warning")

    def _prepare_llamacpp(self) -> bool:
        """为 llama.cpp 翻译准备环境：清理后台内存，启动 server 并等待就绪。

        在 AI 风格识别或翻译步骤前调用。
        返回 True 表示 server 已就绪，False 表示启动失败。
        """
        # 先清理后台内存
        self._log("清理后台内存以为 llama.cpp 腾出资源...", "info")
        gc.collect()

        llamacpp_config = config_manager.get_section("llamacpp")
        if not llamacpp_config.get("model_path"):
            self._log("llama.cpp 模型文件路径未配置", "error")
            return False

        from app.core.llamacpp.server import LlamaCppServer
        server = LlamaCppServer(llamacpp_config)

        if server.is_running:
            self._log("llama.cpp server 已在运行", "info")
            return True

        self._log("正在启动 llama.cpp server...", "info")
        signal_bus.llamacpp_server_started.emit()

        if not server.start():
            detail = server.last_error or "llama.cpp server 启动失败"
            self._log(f"llama.cpp server 启动失败: {detail}", "error")
            signal_bus.llamacpp_server_error.emit(detail)
            return False

        self._log("llama.cpp server 已就绪", "info")
        return True

    def get_state(self) -> Optional[ProcessingState]:
        return self._state

    def get_current_step(self) -> str:
        return self._current_step

    def recover_state(self, video_path: str) -> Optional[ProcessingState]:
        state_file = get_state_file_path(video_path)
        if not os.path.isfile(state_file):
            return None

        try:
            state = ProcessingState.load(state_file)
            self._video_path = video_path
            self._output_dir = get_output_dir(video_path)
            self._state = state

            if state.parameters:
                self._source_lang = state.parameters.get("source_lang", self._source_lang)
                self._target_lang = state.parameters.get("target_lang", self._target_lang)
                self._asr_model = state.parameters.get("asr_model", self._asr_model)
                self._gpu_mode = state.parameters.get("gpu_mode", self._gpu_mode)
                self._enable_translation = state.parameters.get("enable_translation", self._enable_translation)

            return state
        except (json.JSONDecodeError, IOError, KeyError):
            return None

    def _init_state(self):
        state_file = get_state_file_path(self._video_path)
        if os.path.isfile(state_file):
            try:
                self._state = ProcessingState.load(state_file)
                return
            except (json.JSONDecodeError, IOError):
                pass

        self._state = ProcessingState(
            video_file=self._video_path,
            current_step="",
            completed_steps=[],
            step_timestamps={},
            parameters={
                "source_lang": self._source_lang,
                "target_lang": self._target_lang,
                "asr_model": self._asr_model,
                "gpu_mode": self._gpu_mode,
                "enable_translation": self._enable_translation,
            },
        )
        self._save_state()

    def _save_state(self):
        if self._state is None:
            return

        state_file = get_state_file_path(self._video_path)
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        self._state.save(state_file)

    def _run_step(self, step_id: str) -> bool:
        self._current_step = step_id
        if self._state:
            self._state.current_step = step_id
            self._save_state()

        signal_bus.pipeline_step_changed.emit(step_id)
        self._log(f"开始步骤: {step_id}", "info")

        prev_step = _PREVIOUS_STEP_MAP.get(step_id)
        if prev_step is not None:
            if prev_step not in (self._state.completed_steps if self._state else []):
                self._log(f"前置步骤 {prev_step} 尚未完成", "error")
                signal_bus.pipeline_step_error.emit(step_id, f"前置步骤 {prev_step} 尚未完成")
                return False

        method_name = _STEP_METHOD_MAP.get(step_id)
        if method_name is None:
            self._log(f"未知步骤: {step_id}", "error")
            signal_bus.pipeline_step_error.emit(step_id, f"未知步骤: {step_id}")
            return False

        method = getattr(self, method_name, None)
        if method is None:
            self._log(f"步骤方法未实现: {method_name}", "error")
            signal_bus.pipeline_step_error.emit(step_id, f"步骤方法未实现: {method_name}")
            return False

        try:
            result = method()
            if not result:
                return False
        except Exception as e:
            error_msg = str(e)
            self._log(f"步骤 {step_id} 异常: {error_msg}", "error")
            signal_bus.pipeline_step_error.emit(step_id, error_msg)
            return False

        return True

    def _step_audio(self) -> bool:
        from app.core.asr.audio_extractor import AudioExtractor

        if not os.path.isfile(self._video_path):
            self._log(f"视频文件不存在: {self._video_path}", "error")
            signal_bus.pipeline_step_error.emit("audio", "视频文件不存在")
            return False

        audio_path = get_audio_output_path(self._video_path)
        signal_bus.audio_extract_started.emit(self._video_path)
        self._log("正在提取音频...", "info")

        extractor = AudioExtractor()
        success, result = extractor.extract(self._video_path, audio_path)

        if not success:
            self._log(f"音频提取失败: {result}", "error")
            signal_bus.audio_extract_failed.emit(self._video_path, result)
            signal_bus.pipeline_step_error.emit("audio", result)
            return False

        signal_bus.audio_extract_completed.emit(audio_path)
        self._log(f"音频提取完成: {os.path.basename(audio_path)}", "info")
        return True

    def _step_asr(self) -> bool:
        from app.core.asr.faster_whisper import FasterWhisperASR
        from app.core.asr.chunker import AudioChunker

        audio_path = get_audio_output_path(self._video_path)
        if not os.path.isfile(audio_path):
            self._log(f"音频文件不存在: {audio_path}", "error")
            signal_bus.pipeline_step_error.emit("asr", "音频文件不存在")
            return False

        signal_bus.asr_started.emit()
        self._log("正在进行语音识别...", "info")

        asr = FasterWhisperASR(gpu_mode=self._gpu_mode)
        if not asr.is_available():
            msg = "FasterWhisper 程序未安装"
            self._log(msg, "error")
            signal_bus.asr_failed.emit(msg)
            signal_bus.pipeline_step_error.emit("asr", msg)
            return False

        is_auto_lang = is_auto_language(self._source_lang)
        if is_auto_lang:
            language = ""
            self._log("源语言: 自动识别", "info")
        else:
            language = normalize_language(self._source_lang)

        asr_config = config_manager.get_section("asr")

        def on_progress(pct: float) -> None:
            signal_bus.asr_progress.emit(int(pct))
            if self._progress_callback:
                self._progress_callback(int(pct))

        try:
            chunker = None
            if asr_config.get("vad_enabled", False):
                chunker = self._make_vad_chunker(asr_config)
            if chunker is None:
                chunker = AudioChunker(log_callback=self._log)
            try:
                chunks = chunker.chunk(audio_path)
                # VAD 检出空段时回退到定长分块，避免产出空字幕
                if not chunks:
                    from app.core.asr.vad_chunker import VADChunker
                    if isinstance(chunker, VADChunker):
                        self._log("VAD 未检出语音段，回退到定长分块", "warning")
                        fallback = AudioChunker(log_callback=self._log)
                        try:
                            chunks = fallback.chunk(audio_path)
                        finally:
                            fallback.cleanup()
                self._log(f"音频分块完成，共 {len(chunks)} 个语块", "info")

                doc = asr.run_chunks(
                    chunks=chunks,
                    original_audio_path=audio_path,
                    language=language,
                    model=self._asr_model,
                    one_word=asr_config.get("one_word", 1),
                    sentence=asr_config.get("sentence", True),
                    max_line_width=asr_config.get("max_line_width", 80),
                    max_line_count=asr_config.get("max_line_count", 1),
                    max_comma=asr_config.get("max_comma", 20),
                    max_comma_cent=asr_config.get("max_comma_cent", 50),
                    progress_callback=on_progress,
                    log_callback=self._log,
                )
            finally:
                chunker.cleanup()
        except Exception as e:
            error_msg = str(e)
            self._log(f"语音识别失败: {error_msg}", "error")
            signal_bus.asr_failed.emit(error_msg)
            signal_bus.pipeline_step_error.emit("asr", error_msg)
            return False

        if is_auto_lang:
            detected = normalize_language(doc.metadata.source_lang)
            if detected and not is_auto_language(detected):
                self._source_lang = detected
                self._log(f"自动检测到源语言: {detected}", "info")
            else:
                self._source_lang = "en"
                self._log("未能检测到源语言，回退为英语", "warning")
            if self._state:
                self._state.parameters["source_lang"] = self._source_lang
                self._save_state()

        doc.metadata.source_lang = self._source_lang
        doc.metadata.target_lang = self._target_lang
        doc.metadata.asr_model = self._asr_model

        output_path = get_step_output_path(self._video_path, "asr")
        doc.save(output_path)

        signal_bus.asr_completed.emit()
        self._log(f"语音识别完成，共 {len(doc.subtitles)} 条字幕", "info")
        return True

    def _make_vad_chunker(self, asr_config: dict):
        """构造 VAD 分块器，失败返回 None（调用方回退到定长分块）。"""
        from app.core.asr.vad import SileroVAD
        from app.core.asr.vad_chunker import VADChunker
        from app.core.utils.file_utils import get_vad_models_dir
        try:
            vad = SileroVAD(
                model_dir=get_vad_models_dir(),
                threshold=asr_config.get("vad_threshold", 0.25),
                min_speech_duration_ms=asr_config.get("vad_min_speech_duration_ms", 100),
                min_silence_duration_ms=asr_config.get("vad_min_silence_duration_ms", 500),
                speech_pad_ms=asr_config.get("vad_speech_pad_ms", 300),
                log_callback=self._log,
            )
            if not vad.ensure_model():
                self._log("VAD 模型不可用，回退到定长分块", "warning")
                return None
            # 响度归一化对 VAD 检出率基本无影响（根因是 v5 上下文缺失，已在
            # vad.SileroVAD 修复），仅保留 ASMR 预设下的开关用于对比。
            normalize = asr_config.get("vad_preset", "asmr") == "asmr"
            return VADChunker(vad=vad, log_callback=self._log, normalize_audio=normalize)
        except Exception as e:
            self._log(f"VAD 初始化失败，回退到定长分块: {e}", "warning")
            return None

    def _resolve_source_lang(self, document: Optional[SubtitleDocument] = None) -> str:
        """把 "auto" 之类的语言标识解析为可用的 ISO 代码。"""
        raw = self._source_lang or ""
        if not is_auto_language(raw):
            return normalize_language(raw) or raw
        sample = ""
        if document is not None:
            sample = " ".join(item.text for item in document.subtitles[:80] if item.text)
        return resolve_segmentation_language(raw, sample)

    def _step_split1(self) -> bool:
        from app.core.subtitle.splitter import SemanticSplitter

        asr_path = get_step_output_path(self._video_path, "asr")
        if not os.path.isfile(asr_path):
            self._log(f"ASR 输出文件不存在: {asr_path}", "error")
            signal_bus.pipeline_step_error.emit("split1", "ASR 输出文件不存在")
            return False

        signal_bus.split_started.emit("split1")
        self._log("正在进行语义分段...", "info")

        doc = SubtitleDocument.load(asr_path)

        # 源语言可能是 "auto" 或 BCP 47 标签，语义分段只接受 ISO 639-1
        raw_language = self._source_lang or doc.metadata.source_lang or ""
        splitter = SemanticSplitter(language=raw_language)
        result = splitter.split(doc)

        if splitter.language != normalize_language(raw_language):
            self._log(
                f"源语言 '{raw_language or '(未设置)'}' 已解析为 '{splitter.language}' 用于语义分段",
                "warning",
            )
        if is_auto_language(raw_language):
            # 自动检测模式下把结果回写，后续翻译/对齐的提示词才能用上真实语言
            self._source_lang = splitter.language
            if self._state:
                self._state.parameters["source_lang"] = self._source_lang
                self._save_state()
            self._log(f"语义分段使用检测到的语言: {splitter.language}", "info")

        output_path = get_step_output_path(self._video_path, "split1")
        result.save(output_path)

        signal_bus.split_completed.emit("split1")
        self._log(f"语义分段完成，共 {len(result.subtitles)} 条", "info")
        return True

    def _step_translate(self) -> bool:
        from app.core.translator.openai_compat import OpenAICompatTranslator
        from app.core.translator.prompt_template import PromptTemplateManager, VIDEO_STYLES
        from app.core.translator.glossary import GlossaryManager
        from app.core.translator.sliding_window import SlidingWindowTranslator

        split1_path = get_step_output_path(self._video_path, "split1")
        if not os.path.isfile(split1_path):
            self._log(f"分段输出文件不存在: {split1_path}", "error")
            signal_bus.pipeline_step_error.emit("translate", "分段输出文件不存在")
            return False

        # 不翻译模式：跳过翻译，直接将 split1 结果复制为 translate 输出（translation 字段为空）
        if not self._enable_translation:
            signal_bus.translate_started.emit()
            self._log("翻译已禁用，跳过翻译步骤", "info")

            doc = SubtitleDocument.load(split1_path)
            # 清空所有翻译字段
            for item in doc.subtitles:
                item.translation = ""

            output_path = get_step_output_path(self._video_path, "translate")
            doc.save(output_path)

            signal_bus.translate_completed.emit()
            self._log(f"跳过翻译完成，共 {len(doc.subtitles)} 条", "info")
            return True

        translate_config = config_manager.get_section("translate")
        service = translate_config.get("service", "openai_compat")

        translator = None
        if service == "llamacpp":
            # 使用 llama.cpp 引擎：先清理后台，启动 server 等待就绪
            if not self._prepare_llamacpp():
                msg = "llama.cpp server 启动失败，无法进行翻译"
                self._log(msg, "error")
                signal_bus.translate_failed.emit(msg)
                signal_bus.pipeline_step_error.emit("translate", msg)
                return False
            from app.core.llamacpp.translator import LlamaCppTranslator
            llamacpp_config = config_manager.get_section("llamacpp")
            if not llamacpp_config.get("model_path"):
                msg = "llama.cpp 模型文件路径未配置"
                self._log(msg, "error")
                signal_bus.translate_failed.emit(msg)
                signal_bus.pipeline_step_error.emit("translate", msg)
                return False
            translator = LlamaCppTranslator(llamacpp_config)
        else:
            api_base_url = translate_config.get("api_base_url", "")
            api_key = translate_config.get("api_key", "")
            model = translate_config.get("model", "")

            if not api_base_url or not api_key or not model:
                msg = "翻译服务未配置 (API URL / Key / Model)"
                self._log(msg, "error")
                signal_bus.translate_failed.emit(msg)
                signal_bus.pipeline_step_error.emit("translate", msg)
                return False

            translator = OpenAICompatTranslator(
                api_base_url=api_base_url,
                api_key=api_key,
                model=model,
                timeout=_api_timeout_seconds(translate_config),
                max_retries=translate_config.get("max_retries", 10),
                retry_delay=translate_config.get("retry_delay", 30.0),
            )

        signal_bus.translate_started.emit()
        timeout_sec = float(getattr(translator, "timeout_seconds", 0.0) or 0.0)
        if timeout_sec > 0:
            engine_name = "本地模型" if service == "llamacpp" else "远程 API"
            self._log(f"翻译请求超时: {engine_name} {timeout_sec / 60:.0f} 分钟", "dim")
        self._log("正在翻译字幕...", "info")

        doc = SubtitleDocument.load(split1_path)

        # 源语言若仍是"自动检测"，用字幕内容解析出真实语言，避免提示词里出现 "auto"
        if is_auto_language(self._source_lang):
            resolved = self._resolve_source_lang(doc)
            self._log(f"源语言为自动检测，实际使用: {resolved}", "info")
            self._source_lang = resolved
            if self._state:
                self._state.parameters["source_lang"] = resolved
                self._save_state()

        template_manager = PromptTemplateManager()
        glossary_manager = GlossaryManager()

        window_size = translate_config.get("context_window_size", 3)
        context_size = translate_config.get("context_size", 1)
        concurrency = translate_config.get("concurrency", 1)
        template_name = translate_config.get("prompt_template", "auto")
        glossary_name = translate_config.get("glossary", "")
        video_style = translate_config.get("video_style", "")
        auto_detect_style = translate_config.get("auto_detect_style", True)

        sw_translator = SlidingWindowTranslator(
            translator=translator,
            template_manager=template_manager,
            glossary_manager=glossary_manager,
            window_size=window_size,
            context_size=context_size,
            concurrency=concurrency,
            video_style=video_style,
            auto_detect_style=auto_detect_style,
        )

        detected_style = sw_translator.pre_resolve_style(doc.subtitles)
        if detected_style and detected_style in VIDEO_STYLES:
            if video_style and video_style in VIDEO_STYLES:
                self._log(f"视频类型: {VIDEO_STYLES[detected_style]} (手动指定)", "info")
            else:
                self._log(f"检测到视频类型: {VIDEO_STYLES[detected_style]}", "info")
        elif detected_style == "default":
            self._log("视频类型: 默认", "dim")

        def on_progress(current: int, total: int) -> None:
            signal_bus.translate_progress.emit(current, total)
            if self._progress_callback:
                self._progress_callback(int(current / total * 100) if total > 0 else 0)

        try:
            result = sw_translator.translate_document(
                document=doc,
                source_lang=self._source_lang,
                target_lang=self._target_lang,
                template_name=template_name,
                glossary_name=glossary_name,
                progress_callback=on_progress,
            )
        except Exception as e:
            error_msg = str(e)
            self._log(f"翻译失败: {error_msg}", "error")
            signal_bus.translate_failed.emit(error_msg)
            signal_bus.pipeline_step_error.emit("translate", error_msg)
            return False

        output_path = get_step_output_path(self._video_path, "translate")

        # 翻译后处理：单行化 + 注释清洗 + 未翻译/扩写修复
        result = self._postprocess_translation(result, sw_translator=sw_translator)

        result.save(output_path)

        signal_bus.translate_completed.emit()
        self._log(f"翻译完成，共 {len(result.subtitles)} 条", "info")
        return True

    def _postprocess_translation(self, document: SubtitleDocument,
                                 sw_translator=None) -> SubtitleDocument:
        """翻译后处理。

        处理：
        1. 译文统一规范成单行（LLM 经常把一条字幕写成多行，导出后会变成
           "一条字幕下面好几行中文"）
        2. 清洗纯括号注释型翻译
        3. 修复"没翻出来 / 仍是源语言 / 明显扩写"的条目（严格重译）
        4. 纯符号/emoji 条目清空译文，避免 SRT 里同一行重复显示两次
        """
        import re

        from app.core.translator.openai_compat import (
            cjk_char_count,
            has_letters,
            normalize_translation_text,
        )

        # 纯括号注释模式：整个翻译只包含括号注释
        bracket_only_pattern = re.compile(
            r'^[\s]*[（(（][^）)）]*[）)）][\s]*$'
        )

        # 括号注释前缀：翻译以括号注释开头
        bracket_prefix_pattern = re.compile(
            r'^[\s]*[（(（][^）)）]*[）)）][\s]*[：:，,、]?\s*'
        )

        for item in document.subtitles:
            item.translation = normalize_translation_text(item.translation or "")

            if not item.text.strip():
                item.translation = ""
                continue

            translation = item.translation.strip()
            if not translation:
                continue

            # 1. 纯括号注释：整个翻译只是解释性注释
            if bracket_only_pattern.match(translation):
                item.translation = ""
                continue

            # 2. 翻译以括号注释开头，后面有实际翻译内容
            cleaned = bracket_prefix_pattern.sub('', translation)
            if cleaned.strip() and cleaned != translation:
                item.translation = cleaned.strip()
                translation = cleaned.strip()

            # 3. 翻译长度异常检测（CJK 按字符数比较，避免俄/英等长单词语言误判）
            orig_len = len(item.text.strip())
            trans_len = cjk_char_count(translation) or len(translation)
            if orig_len > 0 and trans_len > orig_len * 3:
                # 可能是上下文溢出，尝试在第一个句末标点处截断
                sentence_end = -1
                for end_char in ['。', '！', '？', '.', '!', '?']:
                    pos = translation.find(end_char)
                    if pos > 0 and (sentence_end == -1 or pos < sentence_end):
                        sentence_end = pos + 1
                if sentence_end > 0 and sentence_end < len(translation) * 0.6:
                    item.translation = translation[:sentence_end].strip()

        # 4. 未翻译/扩写条目重译修复
        if sw_translator is not None:
            try:
                stats = sw_translator.repair_document(
                    document, self._source_lang, self._target_lang,
                    log_callback=self._log,
                )
                if stats.get("untranslated") or stats.get("expanded"):
                    self._log(
                        "译文质量修复: 未翻译 {u} 条 / 疑似扩写 {e} 条 -> 已重译 {r} 条，仍失败 {f} 条".format(
                            u=stats.get("untranslated", 0),
                            e=stats.get("expanded", 0),
                            r=stats.get("repaired", 0),
                            f=stats.get("failed", 0),
                        ),
                        "warning" if stats.get("failed") else "info",
                    )
            except Exception as exc:  # noqa: BLE001 - 修复失败不应中断流水线
                self._log(f"译文质量修复失败: {exc}", "warning")

        # 5. 纯符号/emoji 条目：不需要译文（否则 SRT 里会把 emoji 重复显示一行）
        for item in document.subtitles:
            if (item.text.strip() and not has_letters(item.text)
                    and item.translation and cjk_char_count(item.translation) == 0):
                item.translation = ""

        missing = [item.index for item in document.subtitles
                   if has_letters(item.text) and not item.translation.strip()]
        if missing:
            preview = ", ".join(str(i) for i in missing[:10])
            self._log(
                f"有 {len(missing)} 条字幕缺少译文（{preview}{' ...' if len(missing) > 10 else ''}），"
                f"导出后这些条目只会显示原文",
                "warning",
            )

        return document

    def _step_split2(self) -> bool:
        from app.core.subtitle.aligner import AIAligner
        from app.core.translator.openai_compat import OpenAICompatTranslator

        asr_path = get_step_output_path(self._video_path, "asr")
        split1_path = get_step_output_path(self._video_path, "split1")
        translate_path = get_step_output_path(self._video_path, "translate")

        if not os.path.isfile(asr_path):
            self._log(f"ASR 输出文件不存在: {asr_path}", "error")
            signal_bus.pipeline_step_error.emit("split2", "ASR 输出文件不存在")
            return False
        if not os.path.isfile(split1_path):
            self._log(f"分段输出文件不存在: {split1_path}", "error")
            signal_bus.pipeline_step_error.emit("split2", "分段输出文件不存在")
            return False
        if not os.path.isfile(translate_path):
            self._log(f"翻译输出文件不存在: {translate_path}", "error")
            signal_bus.pipeline_step_error.emit("split2", "翻译输出文件不存在")
            return False

        signal_bus.align_started.emit("split2")
        self._log("正在进行智能分割...", "info")

        doc_01 = SubtitleDocument.load(asr_path)
        doc_02 = SubtitleDocument.load(split1_path)
        doc_03 = SubtitleDocument.load(translate_path)

        translate_config = config_manager.get_section("translate")

        # 不翻译模式：不使用翻译器进行 AI 拆分，仅对原文做规则拆分
        translator = None
        if self._enable_translation:
            service = translate_config.get("service", "openai_compat")
            if service == "llamacpp":
                # 使用 llama.cpp 引擎：确保 server 已就绪
                if not self._prepare_llamacpp():
                    self._log("llama.cpp server 未就绪，跳过 AI 对齐", "warning")
                else:
                    from app.core.llamacpp.translator import LlamaCppTranslator
                    llamacpp_config = config_manager.get_section("llamacpp")
                    if llamacpp_config.get("model_path"):
                        translator = LlamaCppTranslator(llamacpp_config)
            else:
                api_base_url = translate_config.get("api_base_url", "")
                api_key = translate_config.get("api_key", "")
                model = translate_config.get("model", "")
                if api_base_url and api_key and model:
                    translator = OpenAICompatTranslator(
                        api_base_url=api_base_url,
                        api_key=api_key,
                        model=model,
                        timeout=_api_timeout_seconds(translate_config),
                        max_retries=translate_config.get("max_retries", 10),
                        retry_delay=translate_config.get("retry_delay", 30.0),
                    )

        aligner = AIAligner(translator=translator)

        def on_progress(current: int, total: int) -> None:
            signal_bus.translate_progress.emit(current, total)
            if self._progress_callback:
                self._progress_callback(int(current / total * 100) if total > 0 else 0)

        result = aligner.align(
            doc_01=doc_01,
            doc_02=doc_02,
            doc_03=doc_03,
            source_lang=self._source_lang,
            target_lang=self._target_lang,
            progress_callback=on_progress,
        )

        output_path = get_step_output_path(self._video_path, "split2")
        result.save(output_path)

        signal_bus.align_completed.emit("split2")
        self._log(f"AI 对齐完成，共 {len(result.subtitles)} 条", "info")
        return True

    def _step_export(self) -> bool:
        from app.core.subtitle.converter import SubtitleConverter

        split2_path = get_step_output_path(self._video_path, "split2")
        if not os.path.isfile(split2_path):
            self._log(f"对齐输出文件不存在: {split2_path}", "error")
            signal_bus.pipeline_step_error.emit("export", "对齐输出文件不存在")
            return False

        signal_bus.subtitle_export_started.emit(self._video_path)
        self._log("正在导出字幕文件...", "info")

        doc = SubtitleDocument.load(split2_path)

        output_formats = config_manager.get("general", "output_formats", ["srt"])
        bilingual_layout = config_manager.get("output", "bilingual_layout", BILINGUAL_LAYOUT_STACKED)
        has_translation = any(item.translation for item in doc.subtitles)

        stem = get_video_stem(self._video_path)
        output_dir = self._output_dir

        style_config = {
            "fontname": config_manager.get("output", "ass_font", "Source Han Sans SC"),
            "fontsize": config_manager.get("output", "ass_font_size", 20),
            "primary_colour": config_manager.get("output", "ass_primary_color", "&H00FFFFFF"),
            "outline_colour": config_manager.get("output", "ass_outline_color", "&H00000000"),
            "outline": config_manager.get("output", "ass_outline_width", 2),
            "shadow": config_manager.get("output", "ass_shadow_distance", 1),
            "alignment": config_manager.get("output", "ass_alignment", 2),
        }

        exported_files: list[str] = []

        for fmt in output_formats:
            fmt_lower = fmt.lower().strip(".")

            try:
                if fmt_lower == "srt":
                    content = SubtitleConverter.to_srt(
                        doc,
                        include_translation=has_translation,
                        bilingual_layout=bilingual_layout,
                    )
                    file_path = os.path.join(output_dir, f"{stem}.srt")

                elif fmt_lower in ("ass", "ssa"):
                    content = SubtitleConverter.to_ass(
                        doc,
                        style_config=style_config,
                        include_translation=has_translation,
                        bilingual_layout=bilingual_layout,
                    )
                    file_path = os.path.join(output_dir, f"{stem}.ass")

                elif fmt_lower == "vtt":
                    content = SubtitleConverter.to_vtt(
                        doc,
                        include_translation=has_translation,
                        bilingual_layout=bilingual_layout,
                    )
                    file_path = os.path.join(output_dir, f"{stem}.vtt")

                else:
                    self._log(f"不支持的格式: {fmt}，跳过", "warning")
                    continue

                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)

                exported_files.append(file_path)
                self._log(f"已导出: {os.path.basename(file_path)}", "info")

            except Exception as e:
                error_msg = f"导出 {fmt} 失败: {e}"
                self._log(error_msg, "error")
                signal_bus.subtitle_export_failed.emit(self._video_path, error_msg)

        if not exported_files:
            msg = "未导出任何字幕文件"
            self._log(msg, "error")
            signal_bus.subtitle_export_failed.emit(self._video_path, msg)
            signal_bus.pipeline_step_error.emit("export", msg)
            return False

        signal_bus.subtitle_export_completed.emit(self._video_path)
        self._log(f"字幕导出完成，共 {len(exported_files)} 个文件", "info")
        return True

    def _load_step_document(self, step_id: str) -> Optional[SubtitleDocument]:
        if step_id not in STEP_FILE_MAP:
            return None

        file_path = get_step_output_path(self._video_path, step_id)
        if not os.path.isfile(file_path):
            return None

        try:
            return SubtitleDocument.load(file_path)
        except (json.JSONDecodeError, IOError):
            return None

    def _log(self, message: str, level: str = "info"):
        signal_bus.log_message.emit(message, level)
        if self._log_callback:
            self._log_callback(message, level)

    def _find_next_step(self) -> str:
        if not self._state:
            return _PIPELINE_STEPS[0]

        completed = set(self._state.completed_steps)
        for step in _PIPELINE_STEPS:
            if step not in completed:
                return step

        return ""
