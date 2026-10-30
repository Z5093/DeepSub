import sys
import os

# 强制 Python 使用 UTF-8 模式，避免 Windows 中文环境下 subprocess 解码使用 GBK 导致 UnicodeDecodeError
os.environ["PYTHONUTF8"] = "1"
import locale
locale.setlocale(locale.LC_ALL, "")

if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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

    if is_first_run():
        if not show_wizard(app):
            sys.exit(0)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
