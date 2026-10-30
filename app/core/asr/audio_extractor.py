from __future__ import annotations

import os
from typing import Optional, Dict, Any

from app.core.utils.ffmpeg_utils import extract_audio, get_video_info, format_timestamp
from app.core.utils.file_utils import get_audio_output_path, ensure_output_dir


class AudioExtractor:

    def __init__(self):
        pass

    def extract(self, video_path: str, output_path: Optional[str] = None) -> tuple[bool, str]:
        if not os.path.isfile(video_path):
            return False, f"视频文件不存在: {video_path}"

        if output_path is None:
            output_path = get_audio_output_path(video_path)

        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        success, error = extract_audio(video_path, output_path)
        if success:
            return True, output_path
        return False, error

    def get_video_info(self, video_path: str) -> Optional[Dict[str, Any]]:
        if not os.path.isfile(video_path):
            return None

        info = get_video_info(video_path)
        if info is None:
            return None

        try:
            file_size = os.path.getsize(video_path)
        except OSError:
            file_size = 0

        info["file_size"] = file_size
        info["file_name"] = os.path.basename(video_path)
        return info

    def format_info_display(self, info: Dict[str, Any]) -> str:
        duration = info.get("duration", 0.0)
        if isinstance(duration, (int, float)) and duration > 0:
            ts = format_timestamp(float(duration))
            duration_display = ts.split(".")[0]
        else:
            duration_display = "00:00:00"

        width = info.get("width", 0)
        height = info.get("height", 0)
        resolution = f"{width}x{height}" if width and height else "未知"

        codec = info.get("codec", "") or "未知"
        audio_codec = info.get("audio_codec", "") or "未知"

        file_size = info.get("file_size", 0)
        if isinstance(file_size, (int, float)) and file_size > 0:
            size_mb = file_size / (1024 * 1024)
            size_display = f"{size_mb:.1f} MB"
        else:
            size_display = "未知"

        file_name = info.get("file_name", "未知")

        lines = [
            f"文件: {file_name}",
            f"时长: {duration_display}",
            f"分辨率: {resolution}",
            f"视频编码: {codec}",
            f"音频编码: {audio_codec}",
            f"文件大小: {size_display}",
        ]
        return "\n".join(lines)
