from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

from app.common.paths import get_app_root, get_bin_dir, get_bin_path, is_frozen

_PROJECT_ROOT = get_app_root()

#: 官方 standalone 单文件（约 20MB 起）。pip 装出来的 yt-dlp.exe 只有 ~100KB，
#: 本质是指向构建机 python.exe 的启动壳（内嵌 ``#!"C:\...\python.exe"``），
#: 拷进发布包后换台机器必然起不来 —— v0.2.0 的发布包就是这么坏的。
#: 因此内置文件和升级逻辑只认官方单文件，任何情况下都不再引入 pip 启动壳。
_YTDLP_EXE_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
_YTDLP_API_URL = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
_GH_MIRRORS = [
    "",
    "https://ghfast.top/",
    "https://gh-proxy.com/",
    "https://mirror.ghproxy.com/",
    "https://ghps.cc/",
]
_MIN_STANDALONE_SIZE = 5 * 1024 * 1024

_FORMAT_MAP = {
    # 优先选 mp4 视频 + m4a 音频，保证能干净地合进 mp4 容器
    # （bestaudio 默认会选 opus/webm，塞进 mp4 会导致合并失败或没声音）
    "best": "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b",
    "1080p": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]",
    "720p": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/bv*[height<=720]+ba/b[height<=720]",
    "480p": "bv*[height<=480][ext=mp4]+ba[ext=m4a]/bv*[height<=480]+ba/b[height<=480]",
    "audio_only": "ba/b",
}

_BROWSER_NAMES = ["chrome", "edge", "firefox", "brave", "opera", "vivaldi"]

_BROWSER_COOKIE_PATHS = {
    "chrome": [
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cookies"),
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Profile *\Cookies"),
    ],
    "edge": [
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cookies"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Profile *\Cookies"),
    ],
    "firefox": [
        os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles"),
    ],
    "brave": [
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data\Default\Cookies"),
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data\Profile *\Cookies"),
    ],
    "opera": [
        os.path.expandvars(r"%APPDATA%\Opera Software\Opera Stable\Cookies"),
    ],
    "vivaldi": [
        os.path.expandvars(r"%LOCALAPPDATA%\Vivaldi\User Data\Default\Cookies"),
        os.path.expandvars(r"%LOCALAPPDATA%\Vivaldi\User Data\Profile *\Cookies"),
    ],
}


_BROWSER_PROCESS_NAMES = {
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "firefox": "firefox.exe",
    "brave": "brave.exe",
    "opera": "opera.exe",
    "vivaldi": "vivaldi.exe",
}


def kill_browser_background(browser_id: str) -> tuple[bool, str]:
    if os.name != "nt":
        return False, ""
    process_name = _BROWSER_PROCESS_NAMES.get(browser_id)
    if not process_name:
        return False, ""
    try:
        check = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if process_name.lower() not in check.stdout.lower():
            return False, ""
        subprocess.run(
            ["taskkill", "/F", "/IM", process_name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        import time
        time.sleep(1)
        return True, f"已关闭 {browser_id} 后台进程以释放 Cookie 数据库锁"
    except (FileNotFoundError, OSError):
        return False, ""


def _detect_available_browser() -> str:
    import glob as _glob

    for browser_id in _BROWSER_NAMES:
        paths = _BROWSER_COOKIE_PATHS.get(browser_id, [])
        for pattern in paths:
            if _glob.glob(pattern):
                return browser_id
    return ""


def get_ytdlp_path() -> str:
    bundled = get_bin_path("yt-dlp.exe")
    if os.path.isfile(bundled):
        return bundled
    system = shutil.which("yt-dlp")
    if system:
        return system
    return bundled


def is_ytdlp_available() -> bool:
    path = get_ytdlp_path()
    if os.path.isfile(path):
        return True
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def get_ffmpeg_dir() -> str:
    return get_bin_dir()


def get_ffmpeg_exe() -> str:
    """按优先级查找 ffmpeg 可执行文件，找不到返回空字符串。"""
    candidates = [
        get_bin_path("ffmpeg.exe"),
        os.path.join(_PROJECT_ROOT, "tools", "ffmpeg", "ffmpeg.exe"),
        os.path.join(os.path.dirname(get_ytdlp_path()), "ffmpeg.exe"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    which = shutil.which("ffmpeg")
    if which and os.path.isfile(which):
        return which
    return ""


def is_ffmpeg_available() -> bool:
    return bool(get_ffmpeg_exe())


def ffmpeg_location_arg() -> list[str]:
    """把 ffmpeg 所在目录传给 yt-dlp，避免它误报 ffmpeg 未安装。"""
    exe = get_ffmpeg_exe()
    if not exe:
        return []
    return ["--ffmpeg-location", os.path.dirname(exe)]


def is_pip_stub(executable_path: str) -> bool:
    """判断 yt-dlp.exe 是否为 pip 生成的启动壳（约 100KB），而非官方 standalone 单文件。"""
    try:
        return os.path.isfile(executable_path) and os.path.getsize(executable_path) < 2 * 1024 * 1024
    except OSError:
        return False


def detect_available_browsers() -> list[tuple[str, str]]:
    from app.common.constants import SUPPORTED_BROWSERS

    ytdlp = get_ytdlp_path()
    if not os.path.isfile(ytdlp):
        try:
            shutil.which("yt-dlp")
        except OSError:
            return [("none", "不使用浏览器 Cookie")]

    available = [("auto", "自动检测")]
    for browser_id, browser_label in SUPPORTED_BROWSERS:
        if browser_id in ("auto", "none"):
            continue
        try:
            result = subprocess.run(
                [ytdlp, "--cookies-from-browser", browser_id, "--skip-download", "--simulate", "https://example.com"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode == 0 or "Cookie" not in result.stderr:
                available.append((browser_id, browser_label))
            else:
                available.append((browser_id, browser_label))
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            pass

    available.append(("none", "不使用浏览器 Cookie"))
    return available


_JS_RUNTIME_PRIORITY = ("deno", "node", "qjs", "bun")


def detect_js_runtime() -> str:
    for name in _JS_RUNTIME_PRIORITY:
        if shutil.which(name):
            return name
    return ""


def js_runtime_args() -> list[str]:
    runtime = detect_js_runtime()
    return ["--js-runtimes", runtime] if runtime else []


_SINGLE_FILE_FORMAT = "best[ext=mp4]/best"
_SINGLE_FILE_FORMAT_BY_HEIGHT = "best[height<={h}][ext=mp4]/best[height<={h}]/best"


def _effective_format(format_pref: str, need_merge: bool) -> str:
    """没有 ffmpeg 时退化为单文件格式，否则 yt-dlp 会把音视频轨分别落盘且不合并。"""
    if need_merge:
        return _FORMAT_MAP.get(format_pref, _FORMAT_MAP["best"])
    height = re.sub(r"\D", "", format_pref)
    if format_pref == "audio_only" or not height:
        return _SINGLE_FILE_FORMAT
    return _SINGLE_FILE_FORMAT_BY_HEIGHT.format(h=height)


def build_download_command(
    url: str,
    output_dir: str,
    format_pref: str = "best",
    proxy: str = "",
    cookie_file: str = "",
    browser_cookie: str = "",
    merge_video_audio: bool = True,
    download_subtitle: bool = False,
    no_continue: bool = False,
    use_cookies: bool = True,
) -> list[str]:
    ytdlp = get_ytdlp_path()
    cmd = [ytdlp]

    ffmpeg_exe = get_ffmpeg_exe()
    want_merge = merge_video_audio and format_pref != "audio_only"
    need_merge = want_merge and bool(ffmpeg_exe)

    fmt = _effective_format(format_pref, need_merge)
    cmd.extend(["-f", fmt])

    cmd.extend(js_runtime_args())

    # 传目录而不是让 yt-dlp 自己找，解决「ffmpeg is not installed」误报
    cmd.extend(ffmpeg_location_arg())

    # 新版 yt-dlp 会在版本超过 90 天时打印更新提醒，这里统一压掉
    cmd.extend(["--no-update", "--newline", "--retries", "10", "--fragment-retries", "10"])

    if need_merge:
        # 容器放不下当前编码时退回 mkv，而不是直接放弃合并
        cmd.extend(["--merge-output-format", "mp4/mkv"])

    if no_continue:
        # 断点续传遇到 403 时，从头下载成功率更高（旧 .part 会被覆盖）
        cmd.append("--no-continue")

    output_template = os.path.join(output_dir, "%(title)s.%(ext)s")
    cmd.extend(["-o", output_template])

    if proxy:
        cmd.extend(["--proxy", proxy])

    if use_cookies:
        if cookie_file:
            cmd.extend(["--cookies", cookie_file])
        elif browser_cookie and browser_cookie not in ("auto", "none", ""):
            cmd.extend(["--cookies-from-browser", browser_cookie])
        elif browser_cookie == "auto":
            detected = _detect_available_browser()
            if detected:
                cmd.extend(["--cookies-from-browser", detected])
            else:
                for browser_id in _BROWSER_NAMES:
                    cmd.extend(["--cookies-from-browser", browser_id])
                    break

    if download_subtitle:
        cmd.extend(["--write-sub", "--write-auto-sub", "--sub-format", "srt/vtt/ass",
                     "--sub-langs", "zh,zh-CN,zh-TW,en,en-US"])

    cmd.append(url)
    return cmd


def parse_download_progress(line: str) -> dict[str, object] | None:
    pattern = re.compile(
        r"\[download\]\s+"
        r"(?P<percent>[\d.]+)%\s+"
        r"of\s+(?P<size>[\d.]+\w+)\s+"
        r"at\s+(?P<speed>[\d.]+\w+/s)\s+"
        r"ETA\s+(?P<eta>[\d:]+)"
    )
    match = pattern.search(line)
    if not match:
        return None
    try:
        percent = float(match.group("percent"))
    except (ValueError, TypeError):
        percent = 0.0
    return {
        "percent": percent,
        "speed": match.group("speed"),
        "eta": match.group("eta"),
        "size": match.group("size"),
    }


def _no_window_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def get_ytdlp_version(executable_path: str) -> str:
    if not executable_path or not os.path.isfile(executable_path):
        return ""
    try:
        result = subprocess.run(
            [executable_path, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_no_window_flags(),
            timeout=15,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    out = (result.stdout or "").strip()
    return out.splitlines()[0].strip() if out else ""


def _get_latest_ytdlp_version_fallback() -> str:
    """源码环境下用 pip 兜底查最新版；打包后 sys.executable 是 DeepSub.exe，直接跳过。"""
    if is_frozen():
        return ""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "yt-dlp=="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_no_window_flags(),
            timeout=60,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return ""
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    m = re.search(r"from versions:\s*([0-9][0-9.\-]*)", combined)
    return m.group(1).strip() if m else ""


def get_latest_ytdlp_version_remote() -> str:
    """从 GitHub Release 取最新版本号，失败时依次走镜像。"""
    try:
        import httpx
    except ImportError:
        return ""
    for mirror in _GH_MIRRORS:
        url = mirror + _YTDLP_API_URL if mirror else _YTDLP_API_URL
        try:
            with httpx.Client(
                timeout=20.0,
                follow_redirects=True,
                headers={"User-Agent": "DeepSub/1.0", "Accept": "application/vnd.github+json"},
            ) as client:
                resp = client.get(url)
            if resp.status_code >= 400:
                continue
            tag = str((resp.json() or {}).get("tag_name") or "").strip()
            if tag:
                return tag.lstrip("vV")
        except Exception:
            continue
    return ""


def get_latest_ytdlp_version() -> str:
    remote = get_latest_ytdlp_version_remote()
    if remote:
        return remote
    return _get_latest_ytdlp_version_fallback()


def is_newer_version(latest: str, current: str) -> bool:
    def to_tuple(v: str) -> tuple[int, ...]:
        parts = []
        for p in v.replace("-", ".").split("."):
            try:
                parts.append(int(p))
            except ValueError:
                parts.append(0)
        return tuple(parts)
    return to_tuple(latest) > to_tuple(current)


def _upgrade_via_self_update(bundled: str) -> tuple[bool, str, str]:
    """官方 standalone 单文件直接用 `-U` 自更新，不会把可执行文件换成 pip 启动壳。"""
    try:
        result = subprocess.run(
            [bundled, "-U"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_no_window_flags(),
            timeout=300,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as e:
        return False, "", f"自更新失败: {e}"
    if result.returncode != 0:
        return False, "", ""
    new_version = get_ytdlp_version(bundled)
    if not new_version:
        return False, "", ""
    return True, new_version, "升级成功（官方单文件自更新）"


def download_standalone_ytdlp(dest_path: str) -> tuple[bool, str]:
    """下载官方 standalone 单文件到 dest_path（先写 .tmp，失败不动现有文件）。"""
    try:
        import httpx
    except ImportError:
        return False, ""

    dest_dir = os.path.dirname(dest_path)
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)
    tmp_path = dest_path + ".tmp"

    for mirror in _GH_MIRRORS:
        url = mirror + _YTDLP_EXE_URL if mirror else _YTDLP_EXE_URL
        try:
            with httpx.stream(
                "GET",
                url,
                follow_redirects=True,
                timeout=180.0,
                headers={"User-Agent": "DeepSub/1.0"},
            ) as resp:
                if resp.status_code >= 400:
                    continue
                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        f.write(chunk)
            size = os.path.getsize(tmp_path)
            if size < _MIN_STANDALONE_SIZE:
                raise OSError(f"下载内容过小（{size} 字节）")
            with open(tmp_path, "rb") as f:
                if f.read(2) != b"MZ":
                    raise OSError("下载内容不是 Windows 可执行文件")
            os.replace(tmp_path, dest_path)
            return True, mirror or "github.com"
        except Exception:
            if os.path.isfile(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            continue
    return False, ""


def upgrade_ytdlp() -> tuple[bool, str, str]:
    """升级内置 yt-dlp，只走官方 standalone 单文件。

    为什么不走 pip：打包后 sys.executable 是 DeepSub.exe，`-m pip` 根本没有；
    即便在源码环境，pip 产出的 yt-dlp.exe 也只是个指向本机 python 的启动壳，
    拷进发布包就是坏件（v0.2.0 发布包即由此损坏）。
    """
    bundled = get_ytdlp_path()

    # 已经是官方单文件：先让它自己升级（最快，也最不容易被镜像拖慢）
    if os.path.isfile(bundled) and not is_pip_stub(bundled):
        ok, version, msg = _upgrade_via_self_update(bundled)
        if ok:
            return ok, version, msg

    backup = ""
    if os.path.isfile(bundled):
        backup = bundled + ".bak"
        try:
            shutil.copy2(bundled, backup)
        except OSError:
            backup = ""

    ok, source = download_standalone_ytdlp(bundled)
    if not ok:
        return False, "", "下载官方 yt-dlp.exe 失败，请检查网络或代理后重试"

    new_version = get_ytdlp_version(bundled)
    if not new_version:
        # 下载到的文件跑不起来，回滚，避免把能用的旧版本覆盖成坏件
        if backup and os.path.isfile(backup):
            try:
                shutil.copy2(backup, bundled)
            except OSError:
                pass
        return False, "", "下载的文件无法运行，已回滚到原版本"

    return True, new_version, f"升级成功（官方单文件，来源 {source}）"


# ---------------------------------------------------------------- 错误诊断

_COOKIE_LOCK_HINTS = (
    "database is locked",
    "could not copy",
    "failed to decrypt",
    "permission denied",
    "unable to open database",
)


def is_cookie_db_locked(lines: list[str]) -> bool:
    """浏览器正在运行导致 Cookie 数据库被占用（不是 Cookie 失效）。"""
    blob = "\n".join(lines).lower()
    return "cookie" in blob and any(h in blob for h in _COOKIE_LOCK_HINTS)


def has_stale_cookie_warning(lines: list[str]) -> bool:
    blob = "\n".join(lines).lower()
    return "cookies are no longer valid" in blob or "cookie" in blob and "rotated" in blob


def has_http_403(lines: list[str]) -> bool:
    blob = "\n".join(lines).lower()
    return "http error 403" in blob or "403: forbidden" in blob or "403 forbidden" in blob


def _pick_detail(lines: list[str]) -> str:
    tail = [line.strip() for line in lines if line.strip()]
    if not tail:
        return ""
    errs = [line for line in tail if "error" in line.lower()]
    detail = (errs[-1] if errs else tail[-1]).strip()
    detail = re.sub(r"^ERROR:\s*", "", detail, flags=re.IGNORECASE)
    # 删掉 yt-dlp 附带的长篇报告链接，界面里看着干净些
    detail = re.split(r";?\s*please report this issue", detail, flags=re.IGNORECASE)[0]
    return detail.strip()[:300]


def classify_download_error(lines: list[str]) -> str:
    """把 yt-dlp 输出归类成可读的中文提示，避免把所有失败都误报成 Cookie 问题。"""
    blob = "\n".join(lines).lower()
    detail = _pick_detail(lines)
    suffix = f"\n原始信息: {detail}" if detail else ""

    if has_stale_cookie_warning(lines) and has_http_403(lines):
        return ("Cookie 已失效：YouTube 判定浏览器 Cookie 无效并拒绝请求（403）。\n"
                "建议：1) 在浏览器中重新登录 YouTube 后关闭浏览器再重试；"
                "2) 或在「浏览器」里选「不使用浏览器 Cookie」下载公开视频。" + suffix)

    if "sign in to confirm" in blob or "confirm you" in blob and "bot" in blob:
        return ("该视频要求登录/人机验证（YouTube 判定为机器人流量）。\n"
                "建议：1) 在所选浏览器中登录对应网站后重试；"
                "2) 或导入 Cookie 文件；3) 换用代理节点后重试。" + suffix)

    if "failed to extract cookies" in blob or "could not find" in blob and "cookie" in blob \
            or "no cookies" in blob or "cookie file" in blob and "not found" in blob:
        return ("Cookie 读取失败：无法从浏览器提取 Cookie。\n"
                "建议：1) 确认所选浏览器已安装且已登录；2) 或点击「导入 Cookie 文件」手动导入；"
                "3) 浏览器正在运行时也可能读到空库，可关闭浏览器后重试。" + suffix)

    if is_cookie_db_locked(lines):
        return ("Cookie 数据库被占用：浏览器正在运行，yt-dlp 读不到 Cookie。\n"
                "建议：关闭所选浏览器（含后台进程）后重试，或改用「导入 Cookie 文件」。" + suffix)

    if has_http_403(lines):
        return ("服务器拒绝访问（HTTP 403）：通常是下载链接已过期或断点续传失效。\n"
                "建议：1) 重试一次（程序会自动清除断点从头下载）；"
                "2) 关闭/切换代理节点；3) 若仍失败，导入最新 Cookie。" + suffix)

    if "requested format is not available" in blob:
        return ("所选清晰度/格式不可用（该视频可能最高只有 720p 或为纯音频）。\n"
                "建议：把「格式偏好」改为「最佳质量」后重试。" + suffix)

    if any(k in blob for k in ("video unavailable", "private video", "members-only",
                               "has been removed", "copyright", "age-restricted",
                               "this video is not available")):
        return ("视频不可用：可能已被删除、设为私享、地区限制或需要会员/年龄验证。\n"
                "建议：确认链接可在浏览器正常播放，必要时登录后导入 Cookie。" + suffix)

    if "ffmpeg" in blob and ("not installed" in blob or "not found" in blob):
        return ("未找到 ffmpeg，音视频流无法合并。\n"
                "建议：把 ffmpeg.exe 放回 resource/bin/ 目录后重试。" + suffix)

    if any(k in blob for k in ("postprocessing", "not currently supported in container",
                               "could not find tag for codec", "invalid argument")):
        return ("音视频合并（后处理）失败：容器与编码不兼容，常见于 opus 音频写入 mp4。\n"
                "建议：1) 格式偏好改为「最佳质量」后重试（会优先选 mp4 + m4a 组合）；"
                "2) 删除残留的 .fXXX 文件后重试。" + suffix)

    if any(k in blob for k in ("unable to connect", "proxy", "timed out", "timeout",
                               "getaddrinfo", "connection reset", "ssl", "certificate",
                               "tunnel connection failed")):
        return ("网络/代理连接失败。\n"
                "建议：1) 检查代理地址与端口是否正确；2) 切换代理模式或节点；"
                "3) 确认能直连目标站点。" + suffix)

    if "unsupported url" in blob:
        return ("不支持的链接，yt-dlp 无法解析该站点。\n建议：确认链接为视频页面地址而不是播放列表/短链跳转页。")

    if "http error 429" in blob or "too many requests" in blob:
        return ("请求过于频繁（429），已被站点限流。\n建议：稍等几分钟再试，或更换代理节点。" + suffix)

    return ("下载失败。" + (f"\n原始信息: {detail}" if detail else " 请查看上方日志定位原因。"))


def last_error_line(lines: list[str]) -> str:
    for line in reversed(lines):
        if "error" in line.lower():
            return line.strip()
    return lines[-1].strip() if lines else ""


# ---------------------------------------------------------------- 下载结果自检

_PLAYABLE_EXTS = (
    ".mp4", ".mkv", ".webm", ".mov", ".flv", ".avi",
    ".m4a", ".mp3", ".opus", ".flac", ".wav",
)
_NOT_MEDIA_EXTS = (".srt", ".vtt", ".ass", ".ssa", ".json", ".part", ".ytdl", ".txt")
_SPLIT_RE = re.compile(
    r"^(?P<base>.+)\.f(?P<fid>[0-9]+)(?:-[A-Za-z0-9_]+)?\.(?P<ext>[A-Za-z0-9]+)$"
)


def _recent_media_files(output_dir: str, since_ts: float) -> list[str]:
    try:
        names = os.listdir(output_dir)
    except OSError:
        return []

    files: list[str] = []
    for name in names:
        path = os.path.join(output_dir, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() in _NOT_MEDIA_EXTS:
            continue
        try:
            # 留 5 秒容差，避免文件系统时间精度问题误判
            if os.path.getmtime(path) + 5 < since_ts:
                continue
        except OSError:
            continue
        files.append(path)
    return files


def ensure_playable_output(output_dir: str, since_ts: float) -> tuple[str, str]:
    """下载后自检：成品缺音轨、或音视频分轨残留时，用 ffmpeg 合成完整文件。

    返回 (状态, 说明)：
      ok            —— 无需处理
      merged        —— 已自动合并，说明为成品路径
      missing_audio —— 只有画面的文件，说明为路径
      failed        —— 合并失败，说明为原因
    """
    from app.core.utils.ffmpeg_utils import probe_stream_kinds, merge_av_streams

    files = _recent_media_files(output_dir, since_ts)
    if not files:
        return "ok", ""

    groups: dict[str, dict[str, str]] = {}
    standalone: list[str] = []
    video_only: list[str] = []
    audio_only: list[str] = []

    for path in files:
        name = os.path.basename(path)
        has_video, has_audio = probe_stream_kinds(path)
        if has_video and not has_audio:
            video_only.append(path)
        elif has_audio and not has_video:
            audio_only.append(path)

        match = _SPLIT_RE.match(name)
        if match:
            groups.setdefault(match.group("base"), {})[match.group("ext").lower()] = path
        elif os.path.splitext(name)[1].lower() in _PLAYABLE_EXTS:
            standalone.append(path)

    def _merge(video_part: str, audio_part: str, base: str) -> tuple[str, str]:
        output_path = os.path.join(output_dir, base + ".mp4")
        ok, detail = merge_av_streams(video_part, audio_part, output_path)
        if not ok:
            return "failed", detail
        for part in (video_part, audio_part):
            try:
                os.remove(part)
            except OSError:
                pass
        return "merged", detail

    # 1) 同名分轨（title.f137.mp4 + title.f251.webm）
    for base, parts in groups.items():
        video_part = audio_part = ""
        for path in parts.values():
            has_video, has_audio = probe_stream_kinds(path)
            if has_video and not has_audio:
                video_part = video_part or path
            elif has_audio and not has_video:
                audio_part = audio_part or path

        if video_part and audio_part:
            return _merge(video_part, audio_part, base)

    # 2) 名称对不上（多次重试导致标题略有差异）时，退化为按流类型配对
    if len(video_only) == 1 and len(audio_only) == 1:
        name = os.path.basename(video_only[0])
        match = _SPLIT_RE.match(name)
        base = match.group("base") if match else os.path.splitext(name)[0]
        return _merge(video_only[0], audio_only[0], base)

    if video_only:
        return "missing_audio", video_only[0]

    for path in standalone:
        has_video, has_audio = probe_stream_kinds(path)
        if has_video and not has_audio:
            return "missing_audio", path

    return "ok", ""


