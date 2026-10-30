from __future__ import annotations
from PyQt5.QtCore import QThread, pyqtSignal


class YtdlpUpdateThread(QThread):
    log = pyqtSignal(str, str)
    version_info = pyqtSignal(str, str, bool)
    completed = pyqtSignal(bool, str)

    def __init__(self, upgrade: bool = False, parent=None):
        super().__init__(parent)
        self._upgrade = upgrade

    def run(self):
        try:
            from app.core.utils.ytdlp_utils import (
                get_ytdlp_path, get_ytdlp_version, get_latest_ytdlp_version,
                is_newer_version, upgrade_ytdlp,
            )

            ytdlp_path = get_ytdlp_path()
            current = get_ytdlp_version(ytdlp_path)
            if not current:
                self.completed.emit(
                    False,
                    "无法读取当前 yt-dlp 版本，请确认 resource/bin/yt-dlp.exe 存在",
                )
                return

            self.log.emit(f"当前版本: {current}", "info")
            self.log.emit("正在查询最新版本...", "dim")
            latest = get_latest_ytdlp_version()
            if not latest:
                self.completed.emit(False, "无法查询最新版本，请检查网络或代理设置")
                return

            has_update = is_newer_version(latest, current)
            self.version_info.emit(current, latest, has_update)

            if not has_update:
                self.completed.emit(True, f"已是最新版本 ({current})")
                return

            self.log.emit(f"发现新版本: {latest}", "success")

            if not self._upgrade:
                self.completed.emit(True, f"发现新版本 {latest}，可点击「升级」按钮更新")
                return

            self.log.emit("正在下载官方 yt-dlp.exe（约 20MB）...", "info")
            success, new_version, msg = upgrade_ytdlp()
            if success:
                self.log.emit(f"升级完成: {current} → {new_version}", "success")
                self.completed.emit(True, f"升级成功: {current} → {new_version}")
            else:
                self.completed.emit(False, msg or "升级失败")
        except Exception as e:
            self.completed.emit(False, f"升级过程出错: {e}")
