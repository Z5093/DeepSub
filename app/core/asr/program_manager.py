from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable, Optional

from app.core.utils.gpu_utils import detect_cuda
from app.common.paths import get_bin_path
from app.core.utils.file_utils import get_fw_gpu_dir, get_fw_cpu_exe

_GPU_EXE_NAME = "faster-whisper-xxl.exe"

_DOWNLOAD_URLS = {
    "gpu": [
        "https://modelscope.cn/models/bkfengg/whisper-cpp/resolve/master/Faster-Whisper-XXL_r245.2_windows.7z",
        "https://github.com/Purfview/whisper-standalone-win/releases/download/Faster-Whisper-XXL/Faster-Whisper-XXL_r245.4_windows.7z",
    ],
    "cpu": [
        "https://modelscope.cn/models/bkfengg/whisper-cpp/resolve/master/whisper-faster.exe",
        "https://github.com/Purfview/whisper-standalone-win/releases/download/faster-whisper/Whisper-Faster_r192.3_windows.zip",
    ],
}

_GITHUB_MIRRORS = [
    "",
    "https://ghfast.top/",
    "https://gh-proxy.com/",
    "https://mirror.ghproxy.com/",
    "https://ghps.cc/",
]


def _get_gpu_exe_path() -> str:
    base_dir = get_fw_gpu_dir()
    direct = os.path.join(base_dir, _GPU_EXE_NAME)
    if os.path.isfile(direct):
        return direct
    for name in os.listdir(base_dir) if os.path.isdir(base_dir) else []:
        sub = os.path.join(base_dir, name, _GPU_EXE_NAME)
        if os.path.isfile(sub):
            return sub
    return direct


def _get_cpu_exe_path() -> str:
    return get_fw_cpu_exe()


def _get_aria2c_path() -> Optional[str]:
    bundled = get_bin_path("aria2c.exe")
    if os.path.isfile(bundled):
        return bundled
    return shutil.which("aria2c")


class FasterWhisperManager:
    def get_installed_version(self) -> dict:
        gpu_path = _get_gpu_exe_path()
        cpu_path = _get_cpu_exe_path()
        result: dict = {
            "gpu_installed": os.path.isfile(gpu_path),
            "cpu_installed": os.path.isfile(cpu_path),
            "gpu_version": "",
            "cpu_version": "",
        }

        if result["gpu_installed"]:
            result["gpu_version"] = self._get_version(gpu_path)

        if result["cpu_installed"]:
            result["cpu_version"] = self._get_version(cpu_path)

        return result

    def _get_version(self, exe_path: str) -> str:
        try:
            proc = subprocess.run(
                [exe_path, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            output = (proc.stdout or "").strip()
            if not output:
                output = (proc.stderr or "").strip()
            match = re.search(r"[\d.]+", output)
            return match.group(0) if match else output
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    def download_program(
        self,
        version: str = "gpu",
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> bool:
        urls = _DOWNLOAD_URLS.get(version, [])
        if not urls:
            return False

        target_dir = get_fw_gpu_dir() if version == "gpu" else os.path.dirname(_get_cpu_exe_path())

        for url in urls:
            is_github = "github.com" in url
            urls_to_try = []

            if is_github:
                for mirror in _GITHUB_MIRRORS:
                    urls_to_try.append(mirror + url if mirror else url)
            else:
                urls_to_try.append(url)

            for try_url in urls_to_try:
                try:
                    tmp_dir = tempfile.mkdtemp(prefix="deepsub_fw_")
                    is_exe = try_url.endswith(".exe")
                    is_7z = try_url.endswith(".7z")
                    ext = ".exe" if is_exe else (".7z" if is_7z else ".zip")
                    archive_path = os.path.join(tmp_dir, f"download{ext}")

                    if self._download_file(try_url, archive_path, progress_callback):
                        if is_exe:
                            os.makedirs(target_dir, exist_ok=True)
                            dest = os.path.join(target_dir, "faster-whisper.exe")
                            if os.path.exists(dest):
                                os.remove(dest)
                            shutil.move(archive_path, dest)
                            shutil.rmtree(tmp_dir, ignore_errors=True)
                            return True
                        elif self._extract_archive(archive_path, target_dir):
                            shutil.rmtree(tmp_dir, ignore_errors=True)
                            return True

                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    continue

        return False

    def _download_file(
        self,
        url: str,
        dest_path: str,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> bool:
        aria2c = _get_aria2c_path()
        if aria2c:
            return self._download_with_aria2c(aria2c, url, dest_path, progress_callback)
        return self._download_with_httpx(url, dest_path, progress_callback)

    def _download_with_aria2c(
        self,
        aria2c_path: str,
        url: str,
        dest_path: str,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> bool:
        dest_dir = os.path.dirname(dest_path)
        dest_name = os.path.basename(dest_path)

        cmd = [
            aria2c_path,
            "--show-console-readout=false",
            "--summary-interval=1",
            "-x2",
            "-s2",
            "--connect-timeout=10",
            "--timeout=10",
            "--max-tries=2",
            "--retry-wait=1",
            "--continue=true",
            "--auto-file-renaming=false",
            "--allow-overwrite=true",
            "--check-certificate=false",
            f"--dir={dest_dir}",
            f"--out={dest_name}",
            url,
        ]

        try:
            creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creation_flags,
            )

            progress_pattern = re.compile(r"\((\d+)%\)")

            while True:
                line = process.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if progress_callback and line:
                    match = progress_pattern.search(line)
                    if match:
                        try:
                            pct = int(match.group(1))
                            progress_callback(min(pct, 100.0))
                        except (ValueError, TypeError):
                            pass

            process.wait()

            if not os.path.isfile(dest_path) or os.path.getsize(dest_path) < 1024:
                return False

            return process.returncode == 0
        except Exception:
            return False

    def _download_with_httpx(
        self,
        url: str,
        dest_path: str,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> bool:
        try:
            import httpx

            headers = {"User-Agent": "DeepSub/0.1.0"}
            with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=180.0) as response:
                if response.status_code >= 400:
                    return False

                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0

                with open(dest_path, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size > 0:
                            pct = min(downloaded / total_size * 100, 100.0)
                            progress_callback(pct)

            if not os.path.isfile(dest_path) or os.path.getsize(dest_path) < 1024:
                if os.path.isfile(dest_path):
                    os.remove(dest_path)
                return False
            return True
        except Exception:
            return False

    def _extract_archive(self, archive_path: str, target_dir: str) -> bool:
        seven_zip = get_bin_path("7z.exe")
        if not os.path.isfile(seven_zip):
            seven_zip = shutil.which("7z")
            if not seven_zip:
                if archive_path.endswith(".zip"):
                    import zipfile
                    try:
                        with zipfile.ZipFile(archive_path, "r") as zf:
                            zf.extractall(target_dir)
                        return True
                    except Exception:
                        return False
                return False

        try:
            os.makedirs(target_dir, exist_ok=True)
            result = subprocess.run(
                [seven_zip, "x", archive_path, f"-o{target_dir}", "-y"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def remove_program(self, version: str) -> bool:
        if version == "gpu":
            target_dir = get_fw_gpu_dir()
            if os.path.isdir(target_dir):
                shutil.rmtree(target_dir, ignore_errors=True)
                return True
        elif version == "cpu":
            cpu_dir = os.path.dirname(_get_cpu_exe_path())
            if os.path.isdir(cpu_dir):
                shutil.rmtree(cpu_dir, ignore_errors=True)
                return True
        return False

    def get_executable_path(self, mode: str = "auto") -> str:
        gpu_path = _get_gpu_exe_path()
        cpu_path = _get_cpu_exe_path()

        if mode == "gpu":
            return gpu_path
        if mode == "cpu":
            return cpu_path

        info = detect_cuda()
        if info["available"]:
            vram = int(info["vram_free_mb"])
            if vram >= 2048 and os.path.isfile(gpu_path):
                return gpu_path

        return cpu_path
