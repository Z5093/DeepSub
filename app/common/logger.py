"""落盘日志与诊断信息。

设计目标：用户遇到「检测不到显卡」「识别失败」这类问题时，能直接把
``logs/`` 下的文件发出来，而不是只截一张界面图。

- 日志落在可写数据根下的 ``logs/deepsub.log``（绿色版即 exe 同级目录）
- 单文件 2MB 轮转，保留 3 份，不会无限增长
- 源码运行时额外输出到控制台；打包成窗口程序后 stderr 可能为 None，需跳过
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import sys
import traceback
from datetime import datetime

from app.common.paths import get_app_root, is_frozen

LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "deepsub.log"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 3

_ROOT_NAME = "deepsub"
_configured = False


def get_log_dir() -> str:
    path = os.path.join(get_app_root(), LOG_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def get_log_file() -> str:
    return os.path.join(get_log_dir(), LOG_FILE_NAME)


def setup_logging(level: int = logging.INFO) -> str:
    """初始化根日志（幂等）。返回日志文件路径。"""
    global _configured
    log_path = get_log_file()
    if _configured:
        return log_path

    root = logging.getLogger(_ROOT_NAME)
    root.setLevel(level)
    root.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        # 目录不可写时不要因为日志把程序拖死
        pass

    if not is_frozen() and sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        root.addHandler(stream)

    _configured = True
    return log_path


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{_ROOT_NAME}.{name}" if name else _ROOT_NAME)


def set_debug(enabled: bool) -> None:
    """跟随设置里的「输出调试日志」开关调整级别。"""
    logging.getLogger(_ROOT_NAME).setLevel(logging.DEBUG if enabled else logging.INFO)


def log_environment() -> None:
    """启动时记一条环境快照，报错时能和用户环境对上。"""
    log = get_logger("env")
    log.info(
        "DeepSub 启动 | 版本=%s | 平台=%s %s | Python=%s | 打包=%s | 程序目录=%s",
        _app_version(),
        platform.system(),
        platform.release(),
        platform.python_version(),
        "是" if is_frozen() else "否",
        get_app_root(),
    )


def _app_version() -> str:
    try:
        from app.common.constants import APP_VERSION

        return APP_VERSION
    except Exception:  # noqa: BLE001 - 日志本身不能抛
        return "unknown"


def _log_uncaught(exc_type, exc_value, exc_tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    get_logger("crash").error("未捕获异常:\n%s", text.rstrip())
    if sys.__stderr__ is not None:
        sys.__stderr__.write(text)


def install_exception_hooks() -> None:
    """未捕获异常（含非主线程）写入日志，避免窗口程序静默退出。"""
    sys.excepthook = _log_uncaught

    if hasattr(sys, "unraisablehook"):
        original_unraisable = sys.unraisablehook

        def _unraisable(hook_args):
            try:
                get_logger("crash").error(
                    "未处理的资源/析构异常: %s",
                    hook_args.exc_value,
                    exc_info=(hook_args.exc_type, hook_args.exc_value, hook_args.exc_traceback),
                )
            finally:
                original_unraisable(hook_args)

        sys.unraisablehook = _unraisable


def tail_log(lines: int = 120) -> str:
    """读最近若干行日志，用于「复制诊断信息」。"""
    path = get_log_file()
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read().splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def _version_text(raw) -> str:
    """版本串只有标点（如 "."）时没有信息量，直接忽略。"""
    text = str(raw or "").strip()
    return text if any(ch.isdigit() for ch in text) else ""


def _describe_install(installed: dict, kind: str) -> str:
    if not installed.get(f"{kind}_installed"):
        return "未安装"
    ver = _version_text(installed.get(f"{kind}_version"))
    return f"已安装 {ver}".strip()


def collect_diagnostics() -> str:
    """拼一份可以直接粘贴给开发者的诊断文本。"""
    from app.core.utils.gpu_utils import detect_cuda

    lines: list[str] = []
    add = lines.append

    add("===== DeepSub 诊断信息 =====")
    add(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    add(f"版本: {_app_version()}")
    add(f"平台: {platform.platform()}")
    add(f"Python: {platform.python_version()} · 打包运行: {'是' if is_frozen() else '否'}")
    add(f"程序目录: {get_app_root()}")
    add(f"日志文件: {get_log_file()}")

    add("")
    add("--- 显卡 / CUDA ---")
    try:
        info = detect_cuda()
        add(f"检测结果: {'可用' if info.get('available') else '不可用'} (reason={info.get('reason')})")
        add(f"显卡: {info.get('gpu_name') or '(未识别)'}")
        add(f"驱动: {info.get('driver_version') or '(未知)'}")
        add(f"CUDA: {info.get('cuda_version') or '(未知)'}")
        add(f"显存: {info.get('vram_free_mb')} / {info.get('vram_total_mb')} MB")
        add(f"显卡数量: {info.get('gpu_count')}")
        add(f"nvidia-smi: {info.get('smi_path') or '(未找到)'}")
        devices = info.get("nvidia_devices") or []
        add(f"系统登记设备: {', '.join(devices) if devices else '(无)'}")
    except Exception as exc:  # noqa: BLE001 - 诊断输出不能抛
        add(f"检测异常: {exc}")

    add("")
    add("--- 识别程序 / 模型 ---")
    try:
        from app.core.asr.program_manager import FasterWhisperManager
        from app.core.utils.file_utils import get_fw_cpu_exe, get_fw_gpu_dir, get_whisper_models_dir

        manager = FasterWhisperManager()
        installed = manager.get_installed_version()
        add(f"GPU 版: {_describe_install(installed, 'gpu')}  ({get_fw_gpu_dir()})")
        add(f"CPU 版: {_describe_install(installed, 'cpu')}  ({get_fw_cpu_exe()})")

        models_dir = get_whisper_models_dir()
        add(f"模型目录: {models_dir}")
        if os.path.isdir(str(models_dir)):
            models = sorted(
                d for d in os.listdir(str(models_dir))
                if os.path.isdir(os.path.join(str(models_dir), d))
            )
            add(f"已安装模型: {', '.join(models) if models else '(空)'}")
    except Exception as exc:  # noqa: BLE001
        add(f"读取失败: {exc}")

    add("")
    add("--- 最近日志 ---")
    add(tail_log(80) or "(无日志)")

    return "\n".join(lines)
