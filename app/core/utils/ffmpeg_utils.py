from __future__ import annotations

import json
import os
import subprocess

from app.common.paths import get_bin_path


def get_ffmpeg_path() -> str:
    return get_bin_path("ffmpeg.exe")


def get_ffprobe_path() -> str:
    return get_bin_path("ffprobe.exe")


def extract_audio(video_path: str, output_path: str, sample_rate: int = 16000) -> tuple[bool, str]:
    ffmpeg = get_ffmpeg_path()
    cmd = [
        ffmpeg,
        "-i", video_path,
        "-vn",
        "-acodec", "flac",
        "-ar", str(sample_rate),
        "-y",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            return False, result.stderr.strip()
        return True, ""
    except FileNotFoundError:
        return False, f"ffmpeg not found at {ffmpeg}"


def get_video_info(video_path: str) -> dict[str, object] | None:
    ffprobe = get_ffprobe_path()
    cmd = [
        ffprobe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    info: dict[str, object] = {
        "duration": 0.0,
        "width": 0,
        "height": 0,
        "codec": "",
        "audio_codec": "",
    }

    fmt = data.get("format", {})
    duration_str = fmt.get("duration")
    if duration_str is not None:
        try:
            info["duration"] = float(duration_str)
        except (ValueError, TypeError):
            pass

    for stream in data.get("streams", []):
        codec_type = stream.get("codec_type", "")
        if codec_type == "video" and not info["codec"]:
            info["codec"] = stream.get("codec_name", "")
            info["width"] = int(stream.get("width", 0))
            info["height"] = int(stream.get("height", 0))
        elif codec_type == "audio" and not info["audio_codec"]:
            info["audio_codec"] = stream.get("codec_name", "")

    return info


def format_timestamp(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    whole_secs = int(secs)
    millis = int(round((secs - whole_secs) * 1000))
    return f"{hours:02d}:{minutes:02d}:{whole_secs:02d}.{millis:03d}"


def parse_timestamp(ts_str: str) -> float:
    ts_str = ts_str.strip()
    ts_str = ts_str.replace(",", ".")
    parts = ts_str.split(":")
    if len(parts) != 3:
        return 0.0
    try:
        hours = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])
        return hours * 3600 + minutes * 60 + seconds
    except (ValueError, TypeError):
        return 0.0


def probe_stream_kinds(path: str) -> tuple[bool, bool]:
    """返回 (是否有视频轨, 是否有音频轨)。"""
    info = get_video_info(path)
    if not info:
        return False, False
    return bool(info.get("codec")), bool(info.get("audio_codec"))


_MP4_UNSAFE_VIDEO = ("vp8",)
_MP4_SAFE_AUDIO = ("aac", "mp3", "alac")


def merge_av_streams(video_path: str, audio_path: str, output_path: str) -> tuple[bool, str]:
    """把独立的视频轨/音频轨合成一个可播放文件。

    mp4 容器放不下 vp8 视频或 opus 音频时，自动改用 mkv / 把音频转成 aac，
    避免出现「视频没有声音」或 yt-dlp 合并直接失败。
    """
    ffmpeg = get_ffmpeg_path()
    if not os.path.isfile(ffmpeg):
        return False, "未找到 ffmpeg"

    video_info = get_video_info(video_path) or {}
    audio_info = get_video_info(audio_path) or {}
    video_codec = str(video_info.get("codec") or "")
    audio_codec = str(audio_info.get("audio_codec") or "")

    extra_args: list[str] = []
    if video_codec in _MP4_UNSAFE_VIDEO:
        output_path = os.path.splitext(output_path)[0] + ".mkv"
        audio_args = ["-c:a", "copy"]
    elif audio_codec in _MP4_SAFE_AUDIO:
        audio_args = ["-c:a", "copy"]
        extra_args = ["-movflags", "+faststart"]
    else:
        # opus/vorbis 等塞进 mp4 会出现无声或播放器不认，转成 aac
        audio_args = ["-c:a", "aac", "-b:a", "192k"]
        extra_args = ["-movflags", "+faststart"]

    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        *audio_args,
        *extra_args,
        output_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except (FileNotFoundError, OSError) as e:
        return False, f"ffmpeg 启动失败: {e}"

    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()
        return False, (tail[-1] if tail else "ffmpeg 合并失败")

    has_video, has_audio = probe_stream_kinds(output_path)
    if not (has_video and has_audio):
        return False, "合并后文件仍缺少音视频轨"
    return True, output_path
