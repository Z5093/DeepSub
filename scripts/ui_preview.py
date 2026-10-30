"""离屏渲染 UI 截图，用于视觉自检（无需真实显示器）。

用法：
    python scripts/ui_preview.py [输出目录]
"""

import os
import sys

# 默认使用真实平台插件：offscreen 平台没有字体数据库，文字不会渲染
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ["PYTHONUTF8"] = "1"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtCore import QTimer  # noqa: E402
from PyQt5.QtGui import QFont  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from app.common.stylesheet import APP_QSS  # noqa: E402
from app.common.theme import UITheme as T  # noqa: E402
from app.view.main_window import MainWindow  # noqa: E402


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "downloads", "ui_preview")
    os.makedirs(out_dir, exist_ok=True)

    app = QApplication(sys.argv)
    font = QFont()
    if hasattr(font, "setFamilies"):
        font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"])
    else:
        font.setFamily("Segoe UI")
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(APP_QSS)

    window = MainWindow()
    window.resize(1440, 900)
    window.move(-4000, -4000)  # 移到屏幕外，避免弹窗打扰
    window.show()
    app.processEvents()

    pages = list(window._pages.items())
    saved = []

    for index, (page_id, page) in enumerate(pages):
        window._stack.setCurrentWidget(page)
        for _ in range(6):
            app.processEvents()
        path = os.path.join(out_dir, f"{index + 1:02d}_{page_id}.png")
        window.grab().save(path)
        saved.append(path)
        print("saved", path, flush=True)

    # 工作台在处理中的状态
    workspace = window._pages["workspace"]
    window._stack.setCurrentWidget(workspace)
    workspace.get_pipeline().set_current_step("translate")
    workspace._terminal.append_line("开始：翻译", "info")
    workspace._terminal.append_line("窗口 12/86 · 已完成 14%", "dim")
    workspace._processing_state = "PROCESSING"
    workspace._current_progress = 46
    workspace._elapsed_timer.start()
    workspace._update_cards()
    workspace._pause_btn.setVisible(True)
    workspace._start_btn.setEnabled(False)
    for _ in range(6):
        app.processEvents()
    path = os.path.join(out_dir, "06_workspace_running.png")
    window.grab().save(path)
    saved.append(path)

    for path in saved:
        print(path, flush=True)
    sys.stdout.flush()
    os._exit(0)  # 跳过 Qt 析构，避免个别驱动在退出时崩溃


if __name__ == "__main__":
    main()
