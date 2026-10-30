from __future__ import annotations

import collections
import logging
import os
import socket
import subprocess
import threading
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_HEALTH_CHECK_TIMEOUT = 5.0
_DEFAULT_STARTUP_WAIT_TIMEOUT = 180.0
_HEALTH_CHECK_INTERVAL = 2.0
_LOG_BUFFER_SIZE = 300


def _friendly_spawn_error(err: OSError, exe: str, port: int) -> str:
    """把 Popen 的 OSError 翻译成可操作的提示。"""
    winerror = getattr(err, "winerror", None)
    if winerror == 5:
        return (
            f"无法启动 {os.path.basename(exe)}：拒绝访问 (WinError 5: Access is denied)。\n"
            "  常见原因与处理方式：\n"
            "  1) 安全软件（Windows Defender / 火绒 / 360 等）拦截了该程序 —— 将 llama.cpp 目录加入白名单；\n"
            "  2) exe 带有“来自 Internet”的锁定标记 —— 右键文件属性 → 勾选“解除锁定”；\n"
            "  3) 当前用户对 llama.cpp 目录没有执行权限 —— 换用有权限的目录或调整 NTFS 权限；\n"
            f"  4) 端口 {port} 已被占用或程序正被其他进程锁定 —— 关闭占用程序后重试。"
        )
    if winerror == 2:
        return f"找不到可执行文件：{exe}"
    if winerror == 193:
        return f"不是有效的 Windows 可执行文件（可能下载不完整或架构不匹配）：{exe}"
    if winerror in (267, 3):
        return f"llama.cpp 目录路径无效：{os.path.dirname(exe)}"
    return f"无法启动 {exe}：{err}"


class LlamaCppServer:
    """管理 llama.cpp server 子进程的生命周期。

    同一个 host:port 在进程内只对应一个实例，任何地方通过
    ``LlamaCppServer(config)`` 拿到的都是共享对象，避免“A 处启动、B 处找不到进程”
    以及重复拉起监听同一端口的多个 server。
    """

    _registry: dict[tuple[str, int], "LlamaCppServer"] = {}
    _registry_lock = threading.RLock()

    def __new__(cls, config: Optional[dict] = None, *args, **kwargs):
        cfg = config or {}
        key = (str(cfg.get("host", "127.0.0.1")), int(cfg.get("port", 8080)))
        with cls._registry_lock:
            inst = cls._registry.get(key)
            if inst is None:
                inst = super().__new__(cls)
                inst._process = None
                inst._external = False
                inst._log_buffer = collections.deque(maxlen=_LOG_BUFFER_SIZE)
                inst._reader_threads: list[threading.Thread] = []
                inst._start_lock = threading.RLock()
                inst.last_error = ""
                cls._registry[key] = inst
            inst._config = cfg
            return inst

    def __init__(self, config: Optional[dict] = None):
        # 状态由 __new__ 维护，这里只更新配置引用
        self._config = config or {}

    # ------------------------------------------------------------------ 配置
    @property
    def host(self) -> str:
        return self._config.get("host", "127.0.0.1")

    @property
    def port(self) -> int:
        return int(self._config.get("port", 8080))

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def startup_timeout(self) -> float:
        try:
            return float(self._config.get("startup_timeout", _DEFAULT_STARTUP_WAIT_TIMEOUT))
        except (TypeError, ValueError):
            return _DEFAULT_STARTUP_WAIT_TIMEOUT

    # ------------------------------------------------------------------ 状态
    @property
    def is_running(self) -> bool:
        if self._process is not None:
            if self._process.poll() is not None:
                self._process = None
                return False
            return self._check_health()
        if self._external:
            if self._check_health():
                return True
            self._external = False
        return False

    def _check_health(self) -> bool:
        try:
            with httpx.Client(timeout=_HEALTH_CHECK_TIMEOUT) as client:
                resp = client.get(f"{self.base_url}/v1/models")
                return resp.status_code == 200
        except (httpx.RequestError, httpx.HTTPStatusError):
            return False

    def _port_in_use(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((self.host, self.port)) == 0

    # ------------------------------------------------------------------ 启动
    def _build_command(self) -> list[str]:
        bin_dir = self._config.get("bin_dir", "")
        model_path = self._config.get("model_path", "")

        if not bin_dir:
            raise ValueError("llama.cpp 目录未配置")
        if not model_path:
            raise ValueError("模型文件路径未配置")
        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"模型文件不存在: {model_path}")

        server_exe = os.path.join(bin_dir, "llama-server.exe")
        if not os.path.isfile(server_exe):
            server_exe = os.path.join(bin_dir, "llama-server")
        if not os.path.isfile(server_exe):
            raise FileNotFoundError(f"找不到 llama-server: {bin_dir}")

        cmd = [
            server_exe,
            "-m", model_path,
            "--ctx-size", str(self._config.get("ctx_size", 32768)),
            "--host", self.host,
            "--port", str(self.port),
        ]

        # CPU 版本不传 --n-gpu-layers，避免部分构建直接报错退出
        try:
            n_gpu_layers = int(self._config.get("n_gpu_layers", 99))
        except (TypeError, ValueError):
            n_gpu_layers = 0
        if n_gpu_layers > 0:
            cmd.extend(["--n-gpu-layers", str(n_gpu_layers)])

        extra = self._config.get("extra_launch_args", "")
        if extra and str(extra).strip():
            cmd.extend(str(extra).strip().split())

        return cmd

    def start(self) -> bool:
        with self._start_lock:
            self.last_error = ""

            if self.is_running:
                logger.info("llama.cpp server 已在运行")
                return True

            # 端口上已有可用的 server（例如上个任务遗留的进程）：直接复用
            if self._check_health():
                self._external = True
                logger.info("检测到 %s 已有 llama.cpp server 在运行，直接复用", self.base_url)
                return True

            try:
                cmd = self._build_command()
            except (ValueError, FileNotFoundError) as e:
                self.last_error = str(e)
                logger.error("构建启动命令失败: %s", e)
                return False

            if self._port_in_use():
                self.last_error = (
                    f"端口 {self.port} 已被其他程序占用，且不是可用的 llama.cpp server。"
                    "请关闭占用程序或在设置中更换端口。"
                )
                logger.error(self.last_error)
                return False

            self._log_buffer.clear()
            logger.info("启动 llama.cpp server: %s", " ".join(cmd))

            try:
                self._process = self._spawn(cmd)
            except OSError as e:
                self._process = None
                self.last_error = _friendly_spawn_error(e, cmd[0], self.port)
                logger.error("启动 llama.cpp server 失败: %s", e)
                return False

            return self._wait_for_ready()

    def _spawn(self, cmd: list[str]) -> subprocess.Popen:
        """启动子进程，按几种方式依次尝试（部分环境下 CREATE_NO_WINDOW 会被拒绝）。"""
        bin_dir = os.path.dirname(cmd[0]) or None
        stdio = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }

        if os.name == "nt":
            attempts = [
                {"creationflags": subprocess.CREATE_NO_WINDOW, "cwd": bin_dir},
                {"creationflags": 0, "cwd": bin_dir},
                {"creationflags": subprocess.CREATE_NO_WINDOW, "cwd": None},
            ]
        else:
            attempts = [{"cwd": bin_dir}, {"start_new_session": True}]

        last_error: Optional[OSError] = None
        for kwargs in attempts:
            try:
                proc = subprocess.Popen(cmd, **{**stdio, **kwargs})
            except OSError as e:
                last_error = e
                logger.warning(
                    "启动方式 %s 失败 (winerror=%s): %s",
                    kwargs, getattr(e, "winerror", None), e,
                )
                continue
            self._attach_reader(proc)
            return proc

        raise last_error if last_error else OSError("未知错误")

    def _attach_reader(self, proc: subprocess.Popen) -> None:
        """持续读取子进程输出，防止管道写满导致 server 阻塞。"""

        def drain(stream, name: str) -> None:
            try:
                for raw in iter(stream.readline, b""):
                    line = raw.decode("utf-8", "replace").rstrip()
                    if line:
                        self._log_buffer.append(line)
                        logger.debug("[llama-server:%s] %s", name, line)
            except (OSError, ValueError):
                pass

        self._reader_threads = []
        for stream, name in ((proc.stdout, "out"), (proc.stderr, "err")):
            if stream is None:
                continue
            thread = threading.Thread(
                target=drain, args=(stream, name), daemon=True,
                name=f"llama-server-{name}",
            )
            thread.start()
            self._reader_threads.append(thread)

    def _tail(self, count: int = 8) -> str:
        lines = list(self._log_buffer)[-count:]
        return "\n".join(lines)

    def _wait_for_ready(self) -> bool:
        deadline = time.time() + self.startup_timeout
        time.sleep(0.5)  # 给进程一点启动时间，避免刚开始 poll 就判死

        while time.time() < deadline:
            if self._process is None:
                break
            code = self._process.poll()
            if code is not None:
                tail = self._tail()
                self.last_error = f"llama.cpp server 进程已退出 (退出码 {code})"
                if tail:
                    self.last_error += f"\n{tail}"
                logger.error(self.last_error)
                self._process = None
                return False
            if self._check_health():
                logger.info("llama.cpp server 已就绪 (%s)", self.base_url)
                return True
            time.sleep(_HEALTH_CHECK_INTERVAL)

        tail = self._tail()
        self.last_error = f"llama.cpp server 启动超时 ({self.startup_timeout:.0f}s)"
        if tail:
            self.last_error += f"\n{tail}"
        logger.error(self.last_error)
        self.stop()
        return False

    # ------------------------------------------------------------------ 停止
    def stop(self) -> None:
        proc, self._process = self._process, None
        self._external = False
        if proc is None:
            return

        logger.info("停止 llama.cpp server (PID: %s)", proc.pid)
        try:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except OSError:
            pass
        finally:
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass

    def ensure_running(self) -> bool:
        if self.is_running:
            return True
        return self.start()
