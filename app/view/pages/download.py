import os

from PyQt5.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget,
)

from app.common.config import config_manager
from app.common.constants import SUPPORTED_BROWSERS
from app.common.signal_bus import signal_bus
from app.common.theme import UITheme as T
from app.components.buttons import Button
from app.components.panels import Card, LogView, Meter, StatTile, hairline
from app.components.widgets import CheckBox, Field, Label, Select, TextField
from app.thread.file_download_thread import FileDownloadThread
from app.thread.ytdlp_update_thread import YtdlpUpdateThread


class DownloadPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self._download_thread = None
        self._update_thread = None
        self._cookie_file = ""
        self._pending_latest = ""
        self._pending_current = ""
        self._pending_has_update = False
        self._setup_ui()
        self._connect_signals()
        self._refresh_ytdlp_version_label()

    # ------------------------------------------------------------------ UI
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 12)
        root.setSpacing(14)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        h1 = QLabel("视频下载")
        h1.setObjectName("H2")
        sub = QLabel("粘贴链接抓取视频与平台字幕，完成后自动进入处理工作台")
        sub.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; font-size: {T.SIZE_SUB}px; background: transparent;"
        )
        titles.addWidget(h1)
        titles.addWidget(sub)
        root.addLayout(titles)

        body = QHBoxLayout()
        body.setSpacing(16)

        # ------------------------- 左：链接 / 进度 / 日志 -------------------------
        left = QVBoxLayout()
        left.setSpacing(14)

        url_card = Card()
        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self._url_input = TextField("粘贴视频链接，支持 YouTube、Bilibili、抖音等")
        self._url_input.setMinimumHeight(34)
        url_row.addWidget(self._url_input, 1)

        self._paste_btn = Button("粘贴")
        self._paste_btn.setMinimumHeight(34)
        url_row.addWidget(self._paste_btn)

        self._download_btn = Button("下载", variant="primary")
        self._download_btn.setMinimumHeight(34)
        url_row.addWidget(self._download_btn)
        url_card.add_layout(url_row)
        left.addWidget(url_card)

        progress_card = Card("进度")
        stats = QHBoxLayout()
        stats.setSpacing(12)
        self._card_speed = StatTile("下载速度", "0", "KB/s")
        self._card_size = StatTile("已下载", "0", "MB")
        self._card_eta = StatTile("剩余时间", "—")
        for tile in (self._card_speed, self._card_size, self._card_eta):
            stats.addWidget(tile)
        progress_card.add_layout(stats)

        progress_meta = QHBoxLayout()
        progress_meta.setSpacing(8)
        progress_meta.addWidget(Label("下载进度", "hint"))
        progress_meta.addStretch(1)
        self._pct_label = Label("0%", "hint")
        progress_meta.addWidget(self._pct_label)
        progress_card.add_layout(progress_meta)

        self._progress_bar = Meter(0)
        progress_card.add_widget(self._progress_bar)
        left.addWidget(progress_card)

        self._terminal = LogView("下载日志")
        left.addWidget(self._terminal, 1)
        body.addLayout(left, 3)

        # ------------------------------ 右：设置 ------------------------------
        right = QVBoxLayout()
        right.setSpacing(14)

        config = Card("下载设置")

        self._proxy_combo = Select()
        self._proxy_combo.addItems(["系统代理", "HTTP 代理", "SOCKS5 代理", "不使用代理"])
        config.add_widget(Field("代理模式", self._proxy_combo))

        self._proxy_host = TextField("127.0.0.1:7890")
        config.add_widget(Field("代理地址", self._proxy_host))

        config.add_widget(hairline())

        self._format_combo = Select()
        self._format_combo.addItems(["最佳质量", "1080p", "720p", "480p", "仅音频"])
        config.add_widget(Field("格式偏好", self._format_combo))

        self._sub_check = CheckBox("同时下载平台字幕（若提供）")
        self._sub_check.setChecked(True)
        config.add_widget(self._sub_check)

        config.add_widget(hairline())

        self._browser_combo = Select()
        self._populate_browser_combo()
        config.add_widget(Field("浏览器", self._browser_combo, hint="读取浏览器 Cookie 以下载会员视频"))

        cookie_row = QHBoxLayout()
        cookie_row.setSpacing(8)
        self._cookie_btn = Button("导入 Cookie 文件")
        cookie_row.addWidget(self._cookie_btn)
        self._cookie_status_label = Label("", "hint")
        cookie_row.addWidget(self._cookie_status_label, 1)
        config.add_layout(cookie_row)

        config.add_widget(hairline())

        path_header = QHBoxLayout()
        path_header.setSpacing(8)
        path_header.addWidget(Label("保存位置", "section"))
        path_header.addStretch(1)
        config.add_layout(path_header)

        self._download_path_label = Label("", "hint")
        self._download_path_label.setWordWrap(True)
        config.add_widget(self._download_path_label)

        path_btns = QHBoxLayout()
        path_btns.setSpacing(8)
        self._browse_path_btn = Button("更改目录")
        path_btns.addWidget(self._browse_path_btn)
        self._open_dir_btn = Button("打开目录")
        path_btns.addWidget(self._open_dir_btn)
        path_btns.addStretch(1)
        config.add_layout(path_btns)

        right.addWidget(config)

        engine = Card("下载引擎")
        self._ytdlp_version_label = Label("正在检测 yt-dlp…", "hint")
        self._ytdlp_version_label.setWordWrap(True)
        engine.add_widget(self._ytdlp_version_label)

        engine_btns = QHBoxLayout()
        engine_btns.setSpacing(8)
        self._check_update_btn = Button("检查更新")
        engine_btns.addWidget(self._check_update_btn)
        self._upgrade_btn = Button("升级")
        self._upgrade_btn.setEnabled(False)
        engine_btns.addWidget(self._upgrade_btn)
        engine_btns.addStretch(1)
        engine.add_layout(engine_btns)
        right.addWidget(engine)

        right.addStretch(1)
        body.addLayout(right, 2)
        root.addLayout(body, 1)

        self._update_download_path_display()
        self._terminal.append_line("下载模块就绪", "success")
        self._terminal.append_line("视频流会自动合并（需要 ffmpeg）", "dim")

    def _populate_browser_combo(self):
        self._browser_combo.clear()
        for browser_id, browser_label in SUPPORTED_BROWSERS:
            self._browser_combo.addItem(browser_label, browser_id)

        saved_browser = config_manager.get("general", "browser_cookie", "auto")
        idx = self._browser_combo.findData(saved_browser)
        if idx >= 0:
            self._browser_combo.setCurrentIndex(idx)

    def _update_download_path_display(self):
        from app.core.utils.file_utils import get_downloads_root, get_videos_dir

        root = get_downloads_root()
        videos_dir = get_videos_dir()
        self._download_path_label.setText(f"{root}\n视频保存到 {os.path.basename(videos_dir)}/")

    def _connect_signals(self):
        self._download_btn.clicked.connect(self._on_download_clicked)
        self._paste_btn.clicked.connect(self._on_paste_clicked)
        self._cookie_btn.clicked.connect(self._on_cookie_clicked)
        self._browse_path_btn.clicked.connect(self._on_browse_path)
        self._open_dir_btn.clicked.connect(self._on_open_dir)
        self._browser_combo.currentIndexChanged.connect(self._on_browser_changed)
        self._check_update_btn.clicked.connect(self._on_check_update_clicked)
        self._upgrade_btn.clicked.connect(self._on_upgrade_clicked)

    def _on_browser_changed(self, index):
        browser_id = self._browser_combo.currentData()
        if browser_id and browser_id not in ("none", ""):
            self._cookie_file = ""
            self._cookie_status_label.setText(f"使用 {self._browser_combo.currentText()} Cookie")
        else:
            self._cookie_status_label.setText("")

        config_manager.set("general", "browser_cookie", browser_id or "none")

    def _on_browse_path(self):
        from app.core.utils.file_utils import get_downloads_root

        current = get_downloads_root()
        new_path = QFileDialog.getExistingDirectory(self, "选择下载根目录", current)
        if new_path:
            config_manager.set("general", "download_path", new_path)
            config_manager.apply()
            self._update_download_path_display()
            self._terminal.append_line(f"下载目录已更改：{new_path}", "success")

    def _on_open_dir(self):
        import subprocess

        from app.core.utils.file_utils import get_downloads_root

        path = get_downloads_root()
        os.makedirs(path, exist_ok=True)
        try:
            os.startfile(path)
        except (OSError, AttributeError):
            subprocess.Popen(["explorer", path])

    def _on_download_clicked(self):
        url = self._url_input.text().strip()
        if not url:
            self._terminal.append_line("请输入视频链接", "error")
            return

        if self._download_thread and self._download_thread.isRunning():
            self._terminal.append_line("下载正在进行中", "warning")
            return

        proxy_config = self._get_proxy_config()
        format_pref = self._get_format_pref()
        browser_cookie = self._browser_combo.currentData() or "auto"
        merge_video_audio = config_manager.get("general", "merge_video_audio", True)
        download_subtitle = self._sub_check.isChecked()

        if format_pref == "audio_only":
            from app.core.utils.file_utils import get_audio_dir
            output_dir = get_audio_dir()
        else:
            from app.core.utils.file_utils import get_videos_dir
            output_dir = get_videos_dir()

        os.makedirs(output_dir, exist_ok=True)

        self._download_thread = FileDownloadThread(
            url=url,
            output_dir=output_dir,
            format_pref=format_pref,
            proxy=proxy_config["proxy"],
            cookie_file=self._cookie_file,
            browser_cookie=browser_cookie,
            merge_video_audio=merge_video_audio,
            download_subtitle=download_subtitle,
            parent=self,
        )
        self._download_thread.progress.connect(self._on_download_progress)
        self._download_thread.speed.connect(self._on_download_speed)
        self._download_thread.completed.connect(self._on_download_completed)
        self._download_thread.failed.connect(self._on_download_failed)
        self._download_thread.log.connect(self._on_download_log)

        self._progress_bar.set_value(0)
        self._pct_label.setText("0%")
        self._card_speed.set_value("0")
        self._card_size.set_value("0")
        self._card_eta.set_value("—")

        self._download_btn.setEnabled(False)
        self._download_thread.start()

        signal_bus.download_started.emit(url)
        self._terminal.append_line(f"开始下载：{url}", "success")
        if browser_cookie and browser_cookie not in ("none", ""):
            self._terminal.append_line(f"使用浏览器 Cookie：{self._browser_combo.currentText()}", "dim")
        if merge_video_audio and format_pref != "audio_only":
            self._terminal.append_line("将自动合并音视频流", "dim")

    def _on_paste_clicked(self):
        clipboard = QApplication.clipboard()
        text = clipboard.text().strip()
        if text:
            self._url_input.setText(text)
            self._terminal.append_line("已粘贴链接", "dim")

    def _on_cookie_clicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Cookie 文件", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if path:
            self._cookie_file = path
            self._cookie_status_label.setText(os.path.basename(path))
            self._browser_combo.setCurrentIndex(self._browser_combo.findData("none"))
            self._terminal.append_line(f"已导入 Cookie：{os.path.basename(path)}", "success")

    def _on_download_progress(self, url, percent):
        self._progress_bar.set_value(percent)
        self._pct_label.setText(f"{percent}%")
        signal_bus.download_progress.emit(url, percent)

    def _on_download_speed(self, url, speed_str):
        if speed_str:
            self._card_speed.set_value(speed_str)

    def _on_download_completed(self, url, output_dir):
        self._download_btn.setEnabled(True)
        self._progress_bar.set_value(100)
        self._pct_label.setText("100%")
        self._card_eta.set_value("—")
        self._terminal.append_line(f"下载完成：{url}", "success")
        self._terminal.append_line(f"保存至：{output_dir}", "dim")
        signal_bus.download_completed.emit(url)

    def _on_download_failed(self, url, error_msg):
        self._download_btn.setEnabled(True)
        self._card_eta.set_value("—")
        lines = [line for line in str(error_msg).splitlines() if line.strip()]
        head = lines[0] if lines else str(error_msg)
        self._terminal.append_line(f"下载失败：{head}", "error")
        for line in lines[1:]:
            self._terminal.append_line(line, "warning")
        signal_bus.download_failed.emit(url, error_msg)

    def _on_download_log(self, line, level):
        self._terminal.append_line(line, level)
        from app.core.utils.ytdlp_utils import parse_download_progress

        progress_info = parse_download_progress(line)
        if progress_info:
            if progress_info.get("size"):
                self._card_size.set_value(progress_info["size"])
            if progress_info.get("eta"):
                self._card_eta.set_value(progress_info["eta"])

    def _get_proxy_config(self):
        proxy_text = self._proxy_combo.currentText()
        proxy_host = self._proxy_host.text().strip()
        if proxy_text == "HTTP 代理":
            return {"proxy_type": "http", "proxy": f"http://{proxy_host}" if proxy_host else ""}
        if proxy_text == "SOCKS5 代理":
            return {"proxy_type": "socks5", "proxy": f"socks5://{proxy_host}" if proxy_host else ""}
        if proxy_text == "不使用代理":
            return {"proxy_type": "none", "proxy": ""}
        return {"proxy_type": "system", "proxy": ""}

    def _get_format_pref(self):
        text = self._format_combo.currentText()
        mapping = {
            "最佳质量": "best",
            "1080p": "1080p",
            "720p": "720p",
            "480p": "480p",
            "仅音频": "audio_only",
        }
        return mapping.get(text, "best")

    def _refresh_ytdlp_version_label(self, latest: str = "", has_update: bool = False):
        try:
            from app.core.utils.ytdlp_utils import detect_js_runtime, get_ytdlp_path, get_ytdlp_version

            current = get_ytdlp_version(get_ytdlp_path())
            js_runtime = detect_js_runtime()
        except Exception:
            current = ""
            js_runtime = ""

        parts = []
        parts.append(f"yt-dlp v{current}" if current else "yt-dlp（版本未知）")
        parts.append(f"JS 运行时 {js_runtime}" if js_runtime else "未检测到 JS 运行时")
        if has_update and latest:
            parts.append(f"可升级到 v{latest}")
        self._ytdlp_version_label.setText(" · ".join(parts))

    def _on_check_update_clicked(self):
        if self._update_thread and self._update_thread.isRunning():
            self._terminal.append_line("正在检查更新，请稍候", "warning")
            return
        self._check_update_btn.setEnabled(False)
        self._upgrade_btn.setEnabled(False)
        self._pending_latest = ""
        self._pending_current = ""
        self._pending_has_update = False
        self._terminal.append_line("正在检查 yt-dlp 更新", "info")
        self._update_thread = YtdlpUpdateThread(upgrade=False, parent=self)
        self._update_thread.log.connect(self._on_update_log)
        self._update_thread.version_info.connect(self._on_update_version_info)
        self._update_thread.completed.connect(self._on_check_update_completed)
        self._update_thread.start()

    def _on_upgrade_clicked(self):
        if self._update_thread and self._update_thread.isRunning():
            self._terminal.append_line("正在处理中，请稍候", "warning")
            return
        self._start_ytdlp_upgrade()

    def _start_ytdlp_upgrade(self):
        self._check_update_btn.setEnabled(False)
        self._upgrade_btn.setEnabled(False)
        self._terminal.append_line("开始通过 pip 升级 yt-dlp", "info")
        self._update_thread = YtdlpUpdateThread(upgrade=True, parent=self)
        self._update_thread.log.connect(self._on_update_log)
        self._update_thread.completed.connect(self._on_upgrade_completed)
        self._update_thread.start()

    def _on_update_version_info(self, current, latest, has_update):
        self._pending_current = current
        self._pending_latest = latest
        self._pending_has_update = has_update
        self._refresh_ytdlp_version_label(latest=latest, has_update=has_update)

    def _on_check_update_completed(self, success, message):
        self._check_update_btn.setEnabled(True)
        self._upgrade_btn.setEnabled(self._pending_has_update)
        if success and self._pending_has_update:
            self._terminal.append_line(message, "success")
            reply = QMessageBox.question(
                self,
                "发现新版本",
                f"新版本 {self._pending_latest}\n当前版本 {self._pending_current}\n\n是否立即升级？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                self._start_ytdlp_upgrade()
        else:
            level = "success" if success else "error"
            self._terminal.append_line(message, level)

    def _on_upgrade_completed(self, success, message):
        self._check_update_btn.setEnabled(True)
        self._upgrade_btn.setEnabled(False)
        level = "success" if success else "error"
        self._terminal.append_line(message, level)
        self._refresh_ytdlp_version_label()

    def _on_update_log(self, message, level):
        self._terminal.append_line(message, level)
