from __future__ import annotations
from PyQt5.QtCore import QThread, pyqtSignal
import subprocess
import os

from app.common.paths import get_bin_path


class UnzipThread(QThread):
    progress = pyqtSignal(int)
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, archive_path: str, output_dir: str, parent=None):
        super().__init__(parent)
        self._archive_path = archive_path
        self._output_dir = output_dir
        self._cancelled = False

    def run(self):
        try:
            sevenz = get_bin_path("7z.exe")

            if not os.path.exists(sevenz):
                self.failed.emit("7z.exe 未找到")
                return

            os.makedirs(self._output_dir, exist_ok=True)

            cmd = [sevenz, "x", self._archive_path, f"-o{self._output_dir}", "-y"]
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace'
            )

            for line in process.stdout:
                if self._cancelled:
                    process.terminate()
                    return
                self.progress.emit(50)

            process.wait()
            if process.returncode == 0 and not self._cancelled:
                self.progress.emit(100)
                self.completed.emit(self._output_dir)
            elif not self._cancelled:
                self.failed.emit(f"解压失败，退出码: {process.returncode}")
        except Exception as e:
            if not self._cancelled:
                self.failed.emit(str(e))

    def cancel(self):
        self._cancelled = True
