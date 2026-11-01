"""离线回归测试：语音识别失败必须给出原因，不能静默产出空字幕。

背景：
  ``run_chunks`` 原先逐个语块 catch 异常后只写一条 warning 就跳过。当 GPU 显存不足、
  CUDA 运行库缺失、驱动过旧时，**每个**语块都会失败，最终返回 0 条字幕的文档，
  上层于是显示「识别完成，共 0 条字幕」——用户完全看不出失败原因。

覆盖点：
  1. 常见 GPU/环境错误能被归类成「原因 + 建议」
  2. 全部语块失败 → 抛 RuntimeError，消息里带原因
  3. 部分语块失败 → 不抛错，但返回文档且发出 warning
  4. GPU 版程序缺失回退 CPU 时，必须同时关掉 GPU 标记（否则会给 CPU 版传 -d cuda）

运行：
    python tests/test_asr_failure.py
"""
import os
import sys
import tempfile

BASE = (
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "__file__" in globals() else os.getcwd()
)
sys.path.insert(0, BASE)

from app.core.asr import faster_whisper as fw  # noqa: E402
from app.core.asr.chunker import AudioChunk  # noqa: E402
from app.core.subtitle.models import (  # noqa: E402
    SubtitleDocument, SubtitleItem, SubtitleMetadata,
)


class _NoSleep:
    @staticmethod
    def sleep(_seconds):
        pass


def _make_asr() -> fw.FasterWhisperASR:
    """绕过 __init__（它会真的去探测显卡）。"""
    asr = fw.FasterWhisperASR.__new__(fw.FasterWhisperASR)
    asr._gpu_mode = False
    asr._exe_path = os.path.join(tempfile.gettempdir(), "faster-whisper.exe")
    asr._program_name = "faster-whisper.exe"
    asr._process = None
    return asr


def _patch_no_sleep_and_chunks(asr, chunks, run_impl):
    tmp = os.path.join(tempfile.gettempdir(), "deepsub_test_chunk.wav")
    with open(tmp, "wb") as f:
        f.write(b"\0" * 64)
    asr._extract_chunk_audio = staticmethod(lambda *a, **k: tmp)
    asr.run = run_impl
    original_time = fw.time
    fw.time = _NoSleep
    return original_time, tmp


def _cleanup(path: str) -> None:
    """run_chunks 自己会删掉语块音频，这里只兜底。"""
    if os.path.exists(path):
        os.remove(path)


def case_diagnosis():
    cases = [
        ("CUDA out of memory. Tried to allocate 2.00 GiB", "显存"),
        ("no kernel image is available for execution on the device", "算力"),
        ("Could not load library cudnn_ops_infer64_8.dll", "运行库"),
        ("CUDA driver version is insufficient for CUDA runtime", "驱动"),
        ("Invalid input features shape: expected 80", "mel"),
        ("这是一行无关输出", None),
        ("", None),
    ]
    for text, expect in cases:
        got = fw.diagnose_asr_failure(text, 1)
        if expect is None:
            assert got is None, (text, got)
        else:
            assert got is not None and expect in got[0], (text, got)
            assert got[1].strip(), "建议不能为空"


def case_all_chunks_failed():
    asr = _make_asr()
    chunks = [AudioChunk(offset=i * 10.0, duration=1.0) for i in range(3)]

    def boom(*_a, **_k):
        raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")

    original_time, tmp = _patch_no_sleep_and_chunks(asr, chunks, boom)
    logs = []
    try:
        asr.run_chunks(
            chunks=chunks,
            original_audio_path=tmp,
            model="large-v3",
            log_callback=lambda msg, level="info": logs.append((msg, level)),
        )
    except RuntimeError as exc:
        message = str(exc)
        assert "全部 3 个语块识别失败" in message, message
        assert "显存不足" in message, message
        assert "CUDA out of memory" in message, message
    else:
        raise AssertionError("全部语块失败时必须抛错，而不是返回空字幕")
    finally:
        fw.time = original_time
        _cleanup(tmp)
    assert any(level == "warning" for _, level in logs), logs


def case_partial_failure():
    asr = _make_asr()
    chunks = [AudioChunk(offset=0.0, duration=1.0), AudioChunk(offset=1.0, duration=1.0)]
    calls = {"n": 0}

    def flaky(*_a, **_k):
        calls["n"] += 1
        if calls["n"] <= 3:  # 第一个语块重试 3 次全失败
            raise RuntimeError("boom")
        return SubtitleDocument(
            metadata=SubtitleMetadata(source_file="x.wav", source_lang="en-US",
                                      target_lang="", asr_model="tiny", source_step="01_asr"),
            subtitles=[SubtitleItem(index=1, start=1.0, end=2.0, text="hello", words=[], confidence=0.9)],
        )

    original_time, tmp = _patch_no_sleep_and_chunks(asr, chunks, flaky)
    logs = []
    try:
        doc = asr.run_chunks(
            chunks=chunks,
            original_audio_path=tmp,
            model="large-v3",
            log_callback=lambda msg, level="info": logs.append((msg, level)),
        )
    finally:
        fw.time = original_time
        _cleanup(tmp)

    assert len(doc.subtitles) == 1, doc.subtitles
    assert any("识别失败并已跳过" in msg for msg, _ in logs), logs


def case_fallback_clears_gpu_flag():
    asr = fw.FasterWhisperASR.__new__(fw.FasterWhisperASR)
    asr._gpu_mode = True
    asr._fallback_to_cpu("未找到 GPU 版识别程序")
    assert asr._gpu_mode is False, "回退 CPU 后必须关掉 GPU 标记"


def main():
    cases = [
        case_diagnosis,
        case_all_chunks_failed,
        case_partial_failure,
        case_fallback_clears_gpu_flag,
    ]
    for case in cases:
        case()
        print(f"  PASS  {case.__name__}")
    print(f"全部通过：{len(cases)} 项")


if __name__ == "__main__":
    main()
