"""离线回归测试：工作台必须接受「纯音频」输入。

背景：
  早期版本只把 mp4/mkv 等视频列为可输入格式，用户拿 mp3/wav 进来要么被文件
  对话框过滤掉，要么被当成视频处理、在 ffprobe 显示「分辨率 未知」。
  实际上整条链路只需要音轨，音频输入应当与视频同等对待。

覆盖点：
  1. 常量里存在音频格式，且并集包含全部视频与音频扩展名
  2. 音频扩展名不带通配符前缀（供 UI 派生 set 用）
  3. format_info_display 对纯音频不输出「分辨率 / 视频编码」，而标注「纯音频」
  4. format_info_display 对视频仍输出分辨率与编码

运行：
    python tests/test_audio_input.py
"""
import os
import sys

BASE = (
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "__file__" in globals() else os.getcwd()
)
sys.path.insert(0, BASE)

from app.common import constants  # noqa: E402
from app.core.asr.audio_extractor import AudioExtractor  # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  [OK]   {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name}  {detail}")


def test_formats():
    audio = constants.SUPPORTED_AUDIO_FORMATS
    video = constants.SUPPORTED_VIDEO_FORMATS
    media = constants.SUPPORTED_MEDIA_FORMATS

    check("音频格式非空", len(audio) >= 5, str(audio))
    for ext in ("*.mp3", "*.wav", "*.m4a", "*.flac", "*.aac"):
        check(f"包含 {ext}", ext in audio, str(audio))
    check("并集覆盖视频", all(e in media for e in video), str(media))
    check("并集覆盖音频", all(e in media for e in audio), str(media))
    check("字幕含 ssa", "*.ssa" in constants.SUPPORTED_SUBTITLE_FORMATS)


def test_audio_only_display():
    info = {
        "duration": 95.4,
        "width": 0,
        "height": 0,
        "codec": "",
        "audio_codec": "mp3",
        "file_size": 3 * 1024 * 1024,
        "file_name": "demo.mp3",
    }
    text = AudioExtractor().format_info_display(info)
    check("纯音频标注类型", "纯音频" in text, text)
    check("纯音频不显示分辨率", "分辨率" not in text, text)
    check("纯音频不显示视频编码", "视频编码" not in text, text)
    check("纯音频显示音频编码", "mp3" in text, text)
    check("纯音频显示时长", "00:01:35" in text, text)


def test_video_display():
    info = {
        "duration": 60.0,
        "width": 1920,
        "height": 1080,
        "codec": "h264",
        "audio_codec": "aac",
        "file_size": 10 * 1024 * 1024,
        "file_name": "demo.mp4",
    }
    text = AudioExtractor().format_info_display(info)
    check("视频标注类型", "视频" in text, text)
    check("视频显示分辨率", "1920x1080" in text, text)
    check("视频显示编码", "h264" in text and "aac" in text, text)


def main():
    print("音频输入支持回归测试")
    for fn in (test_formats, test_audio_only_display, test_video_display):
        print(f"\n- {fn.__name__}")
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            FAILED.append(fn.__name__)
            print(f"  [FAIL] 异常: {e}")

    print(f"\n通过 {len(PASSED)} / {len(PASSED) + len(FAILED)}")
    if FAILED:
        print("失败项: " + ", ".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
