from __future__ import annotations

import os
import re
import json
import subprocess
from typing import Optional, Callable, Dict, Any

from app.core.utils.ytdlp_utils import (
    get_ytdlp_path,
    build_download_command,
    parse_download_progress,
    js_runtime_args,
)
from app.core.subtitle.converter import SubtitleConverter
from app.core.subtitle.models import SubtitleMetadata


_VIDEO_EXTS = (
    ".mp4", ".mkv", ".webm", ".avi", ".flv", ".mov",
    ".m4a", ".mp3", ".opus", ".ogg", ".wav", ".flac",
)
_SUBTITLE_EXTS = (".srt", ".vtt", ".ass", ".ssa")


class VideoDownloader:
    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._cancelled: bool = False

    def download(
        self,
        url: str,
        output_dir: str,
        format_pref: str = "best",
        proxy_type: str = "system",
        proxy_host: str = "",
        cookie_file: str = "",
        browser_cookie: str = "",
        merge_video_audio: bool = True,
        download_subtitle: bool = False,
        progress_callback: Optional[Callable[[dict], None]] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Optional[str]:
        self._cancelled = False
        proxy = self._build_proxy_arg(proxy_type, proxy_host)
        cmd = build_download_command(
            url=url,
            output_dir=output_dir,
            format_pref=format_pref,
            proxy=proxy,
            cookie_file=cookie_file,
            browser_cookie=browser_cookie,
            merge_video_audio=merge_video_audio,
            download_subtitle=download_subtitle,
        )

        url_arg = cmd.pop()
        if proxy_type == "none":
            cmd.extend(["--proxy", ""])

        cmd.append(url_arg)

        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creation_flags,
            )
        except FileNotFoundError:
            if log_callback:
                log_callback("yt-dlp executable not found", "error")
            return None
        except OSError:
            if log_callback:
                log_callback("Failed to start yt-dlp process", "error")
            return None

        destination: Optional[str] = None

        for line in iter(self._process.stdout.readline, ""):
            if self._cancelled:
                break
            line_str = line.strip()
            if not line_str:
                continue

            if "[download] Destination:" in line_str:
                destination = line_str.split("Destination:", 1)[1].strip()
            elif "[Merger] Merging formats into" in line_str:
                match = re.search(r'"([^"]+)"', line_str)
                if match:
                    destination = match.group(1)
            elif "[ExtractAudio] Destination:" in line_str:
                destination = line_str.split("Destination:", 1)[1].strip()
            elif "has already been downloaded" in line_str:
                match = re.search(r"\[download\]\s+(.+?)\s+has already", line_str)
                if match:
                    destination = match.group(1).strip()

            progress = parse_download_progress(line_str)
            if progress and progress_callback:
                progress_callback(progress)

            if log_callback:
                level = "error" if "ERROR" in line_str else "info"
                log_callback(line_str, level)

        return_code = self._process.wait()
        self._process = None

        if self._cancelled:
            return None

        if return_code != 0:
            return None

        if destination and os.path.isfile(destination):
            return destination

        try:
            files = [
                os.path.join(output_dir, f)
                for f in os.listdir(output_dir)
                if os.path.isfile(os.path.join(output_dir, f))
                and os.path.splitext(f)[1].lower() in _VIDEO_EXTS
            ]
            if files:
                files.sort(key=os.path.getmtime, reverse=True)
                return files[0]
        except OSError:
            pass

        return None

    def get_video_info(
        self,
        url: str,
        proxy_type: str = "system",
        proxy_host: str = "",
        browser_cookie: str = "",
    ) -> Optional[Dict[str, Any]]:
        ytdlp = get_ytdlp_path()
        cmd = [ytdlp, "--dump-json", "--no-download"]
        cmd.extend(js_runtime_args())

        proxy = self._build_proxy_arg(proxy_type, proxy_host)
        if proxy:
            cmd.extend(["--proxy", proxy])
        elif proxy_type == "none":
            cmd.extend(["--proxy", ""])

        if browser_cookie and browser_cookie not in ("auto", "none", ""):
            cmd.extend(["--cookies-from-browser", browser_cookie])

        cmd.append(url)

        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creation_flags,
            )
        except (FileNotFoundError, OSError):
            return None

        if result.returncode != 0:
            return None

        try:
            info = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

        return {
            "title": info.get("title", ""),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail", ""),
            "description": info.get("description", ""),
            "uploader": info.get("uploader", ""),
            "formats": info.get("formats", []),
            "id": info.get("id", ""),
        }

    def download_subtitles(
        self,
        url: str,
        output_dir: str,
        proxy_type: str = "system",
        proxy_host: str = "",
        cookie_file: str = "",
        browser_cookie: str = "",
    ) -> Optional[str]:
        ytdlp = get_ytdlp_path()
        cmd = [ytdlp, "--write-sub", "--write-auto-sub", "--skip-download", "--sub-format", "srt/vtt/ass",
               "--sub-langs", "zh,zh-CN,zh-TW,en,en-US"]
        cmd.extend(js_runtime_args())

        proxy = self._build_proxy_arg(proxy_type, proxy_host)
        if proxy:
            cmd.extend(["--proxy", proxy])
        elif proxy_type == "none":
            cmd.extend(["--proxy", ""])

        if cookie_file:
            cmd.extend(["--cookies", cookie_file])
        elif browser_cookie and browser_cookie not in ("auto", "none", ""):
            cmd.extend(["--cookies-from-browser", browser_cookie])

        output_template = os.path.join(output_dir, "%(title)s.%(ext)s")
        cmd.extend(["-o", output_template])
        cmd.append(url)

        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creation_flags,
            )
        except (FileNotFoundError, OSError):
            return None

        if result.returncode != 0:
            return None

        try:
            subtitle_files = [
                f
                for f in os.listdir(output_dir)
                if os.path.splitext(f)[1].lower() in _SUBTITLE_EXTS
                and os.path.isfile(os.path.join(output_dir, f))
            ]
        except OSError:
            return None

        if not subtitle_files:
            return None

        subtitle_files.sort(
            key=lambda f: os.path.getmtime(os.path.join(output_dir, f)),
            reverse=True,
        )
        subtitle_path = os.path.join(output_dir, subtitle_files[0])
        video_title = os.path.splitext(subtitle_files[0])[0]

        return self._convert_subtitle_to_01(subtitle_path, video_title)

    def cancel(self) -> None:
        self._cancelled = True
        if self._process:
            self._process.terminate()

    def _build_proxy_arg(self, proxy_type: str, proxy_host: str) -> str:
        if proxy_type == "http":
            return f"http://{proxy_host}" if proxy_host else ""
        if proxy_type == "socks5":
            return f"socks5://{proxy_host}" if proxy_host else ""
        return ""

    def _convert_subtitle_to_01(self, subtitle_path: str, video_title: str) -> Optional[str]:
        try:
            doc = SubtitleConverter.from_file(subtitle_path)
        except (ValueError, OSError):
            return None

        doc.metadata.source_file = video_title
        doc.metadata.source_step = "01_asr"

        json_path = os.path.splitext(subtitle_path)[0] + ".json"
        try:
            doc.save(json_path)
        except OSError:
            return None

        return json_path
