import sys
import os

# 强制 Python 使用 UTF-8 模式，避免 Windows 中文环境下 subprocess 解码使用 GBK 导致 UnicodeDecodeError
os.environ["PYTHONUTF8"] = "1"
import locale
locale.setlocale(locale.LC_ALL, "")

if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.common.logger import (
    get_logger, install_exception_hooks, log_environment, set_debug, setup_logging,
)

setup_logging()
install_exception_hooks()
log_environment()

try:
    import torch  # noqa: F401
except Exception:
    pass

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt, QSharedMemory, QEventLoop
from PyQt5.QtGui import QFont, QIcon

from app.view.main_window import MainWindow
from app.common.stylesheet import APP_QSS
from app.common.config import config_manager
from app.common.paths import get_config_dir
from app.core.utils.file_utils import get_app_icon_path

set_debug(bool(config_manager.get("performance", "debug_mode", False)))


def _probe_gpu_async() -> None:
    """启动时后台探一次显卡，结果写进日志（不阻塞界面）。"""
    import threading

    def worker():
        from app.core.utils.gpu_utils import detect_cuda

        try:
            info = detect_cuda()
        except Exception:
            get_logger("gpu").exception("显卡检测异常")
            return
        log = get_logger("gpu")
        if info.get("available"):
            log.info(
                "显卡检测: 可用 | %s | 驱动 %s | CUDA %s | 显存 %s/%s MB | reason=%s",
                info.get("gpu_name"), info.get("driver_version"), info.get("cuda_version"),
                info.get("vram_free_mb"), info.get("vram_total_mb"), info.get("reason"),
            )
        else:
            log.warning(
                "显卡检测: 不可用 | reason=%s | 系统登记设备=%s | nvidia-smi=%s",
                info.get("reason"), info.get("nvidia_devices"), info.get("smi_path") or "(未找到)",
            )

    threading.Thread(target=worker, name="gpu-probe", daemon=True).start()


def is_first_run() -> bool:
    config_path = os.path.join(get_config_dir(), "settings.json")
    return not os.path.exists(config_path)


def show_wizard(app: QApplication) -> bool:
    from app.view.pages.wizard import SetupWizard
    wizard = SetupWizard()
    wizard.setWindowTitle("DeepSub — 新手引导")
    wizard.resize(900, 720)
    wizard.setWindowFlags(Qt.Window)

    result = False

    def on_finished():
        nonlocal result
        result = True

    wizard.finished.connect(on_finished)

    loop = QEventLoop()
    wizard.finished.connect(loop.quit)
    wizard.destroyed.connect(loop.quit)
    wizard.show()
    loop.exec()

    return result


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    font = QFont()
    if hasattr(font, "setFamilies"):
        font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"])
    else:
        font.setFamily("Segoe UI")
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(APP_QSS)

    icon_path = get_app_icon_path()
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    shared_memory = QSharedMemory("DeepSub_SingleInstance")
    if not shared_memory.create(1):
        sys.exit(0)

    _probe_gpu_async()

    if is_first_run():
        if not show_wizard(app):
            sys.exit(0)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
