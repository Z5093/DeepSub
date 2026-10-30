from __future__ import annotations
from PyQt5.QtCore import QThread, pyqtSignal
import subprocess
import os
import time


_MAX_ATTEMPTS = 3


class FileDownloadThread(QThread):
    progress = pyqtSignal(str, int)
    speed = pyqtSignal(str, str)
    completed = pyqtSignal(str, str)
    failed = pyqtSignal(str, str)
    log = pyqtSignal(str, str)

    def __init__(self, url: str, output_dir: str, format_pref: str = "best",
                 proxy: str = "", cookie_file: str = "", browser_cookie: str = "",
                 merge_video_audio: bool = True, download_subtitle: bool = False,
                 parent=None):
        super().__init__(parent)
        self._url = url
        self._output_dir = output_dir
        self._format_pref = format_pref
        self._proxy = proxy
        self._cookie_file = cookie_file
        self._browser_cookie = browser_cookie
        self._merge_video_audio = merge_video_audio
        self._download_subtitle = download_subtitle
        self._cancelled = False
        self._process = None

    def run(self):
        try:
            from app.core.utils.ytdlp_utils import (
                build_download_command, is_ytdlp_available, is_ffmpeg_available,
                classify_download_error, ensure_playable_output,
            )

            if not is_ytdlp_available():
                self.failed.emit(
                    self._url,
                    "未检测到 yt-dlp，请将 yt-dlp.exe 放入 resource/bin/ 目录，或加入系统 PATH",
                )
                return

            wants_merge = self._merge_video_audio and self._format_pref != "audio_only"
            if wants_merge and not is_ffmpeg_available():
                self.log.emit(
                    "未找到 ffmpeg，已自动改用单文件格式下载（无法合并高清音视频轨）", "warning"
                )
                self.log.emit("修复方法：把 ffmpeg.exe 放回 resource/bin/ 目录", "dim")

            state = {"killed_browser": False, "no_continue": False, "use_cookies": True}
            last_lines: list[str] = []
            returncode = -1
            start_ts = time.time()

            for attempt in range(1, _MAX_ATTEMPTS + 1):
                cmd = build_download_command(
                    url=self._url,
                    output_dir=self._output_dir,
                    format_pref=self._format_pref,
                    proxy=self._proxy,
                    cookie_file=self._cookie_file,
                    browser_cookie=self._browser_cookie,
                    merge_video_audio=self._merge_video_audio,
                    download_subtitle=self._download_subtitle,
                    no_continue=state["no_continue"],
                    use_cookies=state["use_cookies"],
                )

                returncode, last_lines = self._run_process(cmd)

                if self._cancelled:
                    return
                if returncode == 0 or attempt == _MAX_ATTEMPTS:
                    break

                next_state = self._prepare_retry(last_lines, state)
                if next_state is None:
                    break
                state = next_state

            if self._cancelled:
                return

            if returncode != 0:
                self.failed.emit(self._url, classify_download_error(last_lines))
                return

            # yt-dlp 有时会静默留下「只有画面」或音视频分轨的文件，这里做一次成品自检
            status, detail = ensure_playable_output(self._output_dir, start_ts)
            if status == "merged":
                self.log.emit(f"已自动合并音视频轨: {os.path.basename(detail)}", "success")
                self.completed.emit(self._url, self._output_dir)
            elif status == "missing_audio":
                self.failed.emit(
                    self._url,
                    "下载到的文件只有画面、没有音轨。\n"
                    f"文件: {os.path.basename(detail)}\n"
                    "建议：1) 删除该文件后重试；"
                    "2) 格式偏好选「最佳质量」；"
                    "3) 确认 resource/bin/ffmpeg.exe 存在。",
                )
            elif status == "failed":
                self.failed.emit(
                    self._url,
                    f"音视频合并失败: {detail}\n建议：确认 ffmpeg 可用（resource/bin/ffmpeg.exe）后重试。",
                )
            else:
                self.completed.emit(self._url, self._output_dir)
        except Exception as e:
            if not self._cancelled:
                self.failed.emit(self._url, f"下载过程出错: {e}")

    # ------------------------------------------------------------------ 重试策略

    def _prepare_retry(self, lines: list[str], state: dict) -> dict | None:
        """根据失败原因决定下一次尝试的参数；返回 None 表示不再重试。"""
        from app.core.utils.ytdlp_utils import (
            has_http_403, has_stale_cookie_warning, is_cookie_db_locked,
            kill_browser_background, _detect_available_browser,
        )

        if is_cookie_db_locked(lines) and not state["killed_browser"]:
            browser = self._resolve_browser(_detect_available_browser)
            if browser:
                killed, msg = kill_browser_background(browser)
                if msg:
                    self.log.emit(msg, "warning")
                self.log.emit("Cookie 数据库被占用，正在重试…", "warning")
                return {**state, "killed_browser": True}
            self.log.emit("Cookie 数据库被占用，改为不使用 Cookie 重试…", "warning")
            return {**state, "killed_browser": True, "use_cookies": False}

        if state["use_cookies"] and has_stale_cookie_warning(lines):
            self.log.emit("浏览器 Cookie 已失效，正在不使用 Cookie 重试…", "warning")
            return {**state, "use_cookies": False, "no_continue": True}

        if has_http_403(lines):
            if not state["no_continue"]:
                self.log.emit("检测到 403（链接过期/断点续传失效），正在清除断点从头重试…", "warning")
                return {**state, "no_continue": True}
            if state["use_cookies"]:
                self.log.emit("再次 403，改为不带 Cookie 重试…", "warning")
                return {**state, "use_cookies": False}

        return None

    def _resolve_browser(self, detect_browser) -> str:
        browser = self._browser_cookie
        if browser == "auto":
            browser = detect_browser()
        return browser if browser and browser not in ("none", "") else ""

    # ------------------------------------------------------------------ 进程执行

    def _run_process(self, cmd) -> tuple[int, list[str]]:
        from app.core.utils.ytdlp_utils import parse_download_progress

        last_lines: list[str] = []

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
            self.log.emit("yt-dlp 可执行文件不存在", "error")
            return -1, ["ERROR: yt-dlp executable not found"]
        except OSError as e:
            self.log.emit(f"无法启动 yt-dlp: {e}", "error")
            return -1, [f"ERROR: {e}"]

        try:
            for raw in self._process.stdout:
                if self._cancelled:
                    self._process.terminate()
                    break
                line = raw.strip()
                if not line:
                    continue

                self.log.emit(line, self._line_level(line))
                last_lines.append(line)
                if len(last_lines) > 40:
                    last_lines.pop(0)

                progress_info = parse_download_progress(line)
                if progress_info:
                    try:
                        percent = int(float(progress_info.get("percent", 0)))
                    except (TypeError, ValueError):
                        percent = 0
                    self.progress.emit(self._url, percent)
                    if progress_info.get("speed"):
                        self.speed.emit(self._url, str(progress_info["speed"]))
        finally:
            process = self._process
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            returncode = process.returncode
            self._process = None

        return returncode, last_lines

    @staticmethod
    def _line_level(line: str) -> str:
        if "ERROR" in line:
            return "error"
        if "WARNING" in line:
            return "warning"
        return "dim"

    def cancel(self):
        self._cancelled = True
        process = self._process
        if process:
            try:
                process.terminate()
            except OSError:
                pass
