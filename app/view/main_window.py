import os

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QHBoxLayout, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from app.common.constants import (
    PAGE_DOWNLOAD, PAGE_FASTERWHISPER, PAGE_LLAMACPP, PAGE_SETTINGS, PAGE_WORKSPACE,
)
from app.common.signal_bus import signal_bus
from app.common.stylesheet import APP_QSS
from app.view.main_layout import Sidebar, StatusBar
from app.view.pages.download import DownloadPage
from app.view.pages.fasterwhisper import FasterWhisperPage
from app.view.pages.llamacpp import LlamaCppPage
from app.view.pages.settings import SettingsPage
from app.view.pages.workspace import WorkspacePage


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._setup_window()
        self._setup_ui()
        self._connect_signals()

    def _setup_window(self):
        from app.core.utils.file_utils import get_app_icon_path

        icon_path = get_app_icon_path()
        if icon_path and os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.setWindowTitle("DeepSub")
        self.setMinimumSize(1180, 740)
        self.resize(1380, 860)
        self.setStyleSheet(APP_QSS)

    def _setup_ui(self):
        central = QWidget()
        central.setObjectName("AppRoot")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._sidebar = Sidebar()
        body.addWidget(self._sidebar)

        self._pages = {
            PAGE_WORKSPACE: WorkspacePage(),
            PAGE_DOWNLOAD: DownloadPage(),
            PAGE_FASTERWHISPER: FasterWhisperPage(),
            PAGE_LLAMACPP: LlamaCppPage(),
            PAGE_SETTINGS: SettingsPage(),
        }

        self._stack = QStackedWidget()
        self._stack.setObjectName("PageStack")
        for page_id in [PAGE_WORKSPACE, PAGE_DOWNLOAD, PAGE_FASTERWHISPER, PAGE_LLAMACPP, PAGE_SETTINGS]:
            self._stack.addWidget(self._pages[page_id])
        body.addWidget(self._stack, 1)

        root.addLayout(body, 1)

        self._status_bar = StatusBar()
        self._status_bar.set_status("就绪", "本地处理 · 不上传文件")
        root.addWidget(self._status_bar)

    def _connect_signals(self):
        self._sidebar.page_selected.connect(self._on_page_changed)
        signal_bus.nav_page_changed.connect(self._navigate_to)
        signal_bus.pipeline_step_changed.connect(self._on_pipeline_step)
        signal_bus.pipeline_started.connect(lambda: self._sidebar.set_state("正在处理", "warning"))
        signal_bus.pipeline_completed.connect(lambda: self._sidebar.set_state("已完成", "success"))
        signal_bus.pipeline_step_error.connect(lambda *_: self._sidebar.set_state("出错", "error"))

    def _on_page_changed(self, page_id: str):
        idx = list(self._pages.keys()).index(page_id)
        self._stack.setCurrentIndex(idx)

    def _navigate_to(self, page_id: str):
        if page_id not in self._pages:
            return
        self._sidebar.set_current_page(page_id)
        self._on_page_changed(page_id)

    def _on_pipeline_step(self, step_id: str):
        workspace = self._pages.get(PAGE_WORKSPACE)
        if workspace:
            workspace.get_pipeline().set_current_step(step_id)
