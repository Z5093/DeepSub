from __future__ import annotations
from PyQt5.QtCore import QThread, pyqtSignal
import os
import json
import shutil


class ModelDownloadThread(QThread):
    progress = pyqtSignal(str, int)
    completed = pyqtSignal(str)
    failed = pyqtSignal(str, str)
    log = pyqtSignal(str, str)

    MODEL_URLS = {
        "huggingface": {
            "tiny": "https://huggingface.co/Systran/faster-whisper-tiny/resolve/main/",
            "tiny.en": "https://huggingface.co/Systran/faster-whisper-tiny.en/resolve/main/",
            "base": "https://huggingface.co/Systran/faster-whisper-base/resolve/main/",
            "base.en": "https://huggingface.co/Systran/faster-whisper-base.en/resolve/main/",
            "small": "https://huggingface.co/Systran/faster-whisper-small/resolve/main/",
            "small.en": "https://huggingface.co/Systran/faster-whisper-small.en/resolve/main/",
            "medium": "https://huggingface.co/Systran/faster-whisper-medium/resolve/main/",
            "medium.en": "https://huggingface.co/Systran/faster-whisper-medium.en/resolve/main/",
            "large-v1": "https://huggingface.co/Systran/faster-whisper-large-v1/resolve/main/",
            "large-v2": "https://huggingface.co/Systran/faster-whisper-large-v2/resolve/main/",
            "large-v3": "https://huggingface.co/Systran/faster-whisper-large-v3/resolve/main/",
            "large-v3-turbo": "https://huggingface.co/Systran/faster-whisper-large-v3-turbo/resolve/main/",
        },
        "modelscope": {
            "tiny": "https://modelscope.cn/models/pengzhendong/faster-whisper-tiny/resolve/master/",
            "base": "https://modelscope.cn/models/pengzhendong/faster-whisper-base/resolve/master/",
            "small": "https://modelscope.cn/models/pengzhendong/faster-whisper-small/resolve/master/",
            "medium": "https://modelscope.cn/models/pengzhendong/faster-whisper-medium/resolve/master/",
            "large-v1": "https://modelscope.cn/models/pengzhendong/faster-whisper-large-v1/resolve/master/",
            "large-v2": "https://modelscope.cn/models/pengzhendong/faster-whisper-large-v2/resolve/master/",
            "large-v3": "https://modelscope.cn/models/pengzhendong/faster-whisper-large-v3/resolve/master/",
            "large-v3-turbo": "https://modelscope.cn/models/pengzhendong/faster-whisper-large-v3-turbo/resolve/master/",
        }
    }

    MODELSCOPE_SDK_IDS = {
        "tiny": "pengzhendong/faster-whisper-tiny",
        "base": "pengzhendong/faster-whisper-base",
        "small": "pengzhendong/faster-whisper-small",
        "medium": "pengzhendong/faster-whisper-medium",
        "large-v1": "pengzhendong/faster-whisper-large-v1",
        "large-v2": "pengzhendong/faster-whisper-large-v2",
        "large-v3": "pengzhendong/faster-whisper-large-v3",
        "large-v3-turbo": "pengzhendong/faster-whisper-large-v3-turbo",
    }

    MODEL_FILES = [
        "config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.json",
        "vocabulary.txt",
    ]

    MIN_FILE_SIZES = {
        "config.json": 100,
        "model.bin": 1024,
        "tokenizer.json": 100,
        "vocabulary.json": 100,
        "vocabulary.txt": 100,
    }

    def __init__(self, model_id: str, source: str = "modelscope",
                 model_dir: str = "", parent=None):
        super().__init__(parent)
        self._model_id = model_id
        self._source = source
        self._model_dir = model_dir
        self._cancelled = False

    def run(self):
        try:
            if not self._model_dir:
                from app.core.utils.file_utils import get_whisper_models_dir
                self._model_dir = get_whisper_models_dir()

            os.makedirs(self._model_dir, exist_ok=True)
            model_path = os.path.join(self._model_dir, self._model_id)

            if os.path.exists(model_path) and os.path.exists(os.path.join(model_path, "model.bin")):
                self.completed.emit(self._model_id)
                return

            if self._source == "modelscope":
                sdk_ok = self._try_modelscope_sdk(model_path)
                if sdk_ok:
                    self.progress.emit(self._model_id, 100)
                    self.completed.emit(self._model_id)
                    return
                if self._cancelled:
                    return
                self.log.emit("ModelScope SDK 不可用，回退到 HTTP 下载...", "info")

            self._download_via_http(model_path)

            if not self._cancelled:
                self.progress.emit(self._model_id, 100)
                self.completed.emit(self._model_id)
        except Exception as e:
            if not self._cancelled:
                self.failed.emit(self._model_id, str(e))

    def _try_modelscope_sdk(self, model_path: str) -> bool:
        sdk_id = self.MODELSCOPE_SDK_IDS.get(self._model_id)
        if not sdk_id:
            return False

        try:
            from modelscope.hub.snapshot_download import snapshot_download
        except ImportError:
            try:
                from modelscope import snapshot_download
            except ImportError:
                return False

        try:
            self.log.emit("使用 ModelScope SDK 下载...", "info")
            local_dir = os.path.join(self._model_dir, self._model_id + "_tmp")
            os.makedirs(local_dir, exist_ok=True)

            downloaded_path = snapshot_download(
                model_id=sdk_id,
                local_dir=local_dir,
            )

            if self._cancelled:
                shutil.rmtree(local_dir, ignore_errors=True)
                return False

            if os.path.isdir(downloaded_path):
                if os.path.isdir(model_path):
                    shutil.rmtree(model_path, ignore_errors=True)
                os.rename(downloaded_path, model_path)
                self._ensure_vocabulary_json(model_path)
                return True

            return False
        except Exception as e:
            self.log.emit(f"ModelScope SDK 下载失败: {e}", "warning")
            tmp_dir = os.path.join(self._model_dir, self._model_id + "_tmp")
            if os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)
            return False

    def _download_via_http(self, model_path: str):
        base_url = self.MODEL_URLS.get(self._source, {}).get(self._model_id)
        if not base_url:
            raise RuntimeError(
                f"未找到模型 {self._model_id} 的下载地址（源: {self._source}）"
            )

        os.makedirs(model_path, exist_ok=True)
        state_file = os.path.join(model_path, ".download_state.json")
        downloaded_files = self._load_download_state(state_file)

        total_files = len(self.MODEL_FILES)
        for i, filename in enumerate(self.MODEL_FILES):
            if self._cancelled:
                return
            if filename in downloaded_files:
                self.progress.emit(self._model_id, int((i + 1) / total_files * 100))
                continue

            file_url = base_url + filename
            file_path = os.path.join(model_path, filename)
            temp_path = file_path + ".tmp"

            self.log.emit(f"下载 {filename}...", "info")

            try:
                self._download_file_with_progress(
                    file_url, temp_path,
                    base_percent=int(i / total_files * 100),
                    file_percent=int(100 / total_files)
                )
            except Exception as e:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise e

            if self._cancelled:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return

            if os.path.exists(temp_path):
                file_size = os.path.getsize(temp_path)
                min_size = self.MIN_FILE_SIZES.get(filename, 0)
                if file_size < min_size:
                    os.remove(temp_path)
                    raise RuntimeError(
                        f"文件 {filename} 下载不完整（{file_size} 字节，预期至少 {min_size} 字节），"
                        f"可能是下载源不可用，请尝试切换下载源"
                    )

                if filename.endswith(".json"):
                    validation_error = self._validate_json_file(temp_path, filename)
                    if validation_error:
                        os.remove(temp_path)
                        if filename == "vocabulary.json":
                            self.log.emit(
                                f"vocabulary.json 下载内容无效，将尝试从 vocabulary.txt 生成",
                                "warning"
                            )
                            continue
                        raise RuntimeError(validation_error)

                if os.path.exists(file_path):
                    os.remove(file_path)
                os.rename(temp_path, file_path)
                downloaded_files.append(filename)
                self._save_download_state(state_file, downloaded_files)

        self._ensure_vocabulary_json(model_path)

        if os.path.exists(state_file):
            os.remove(state_file)

    def _validate_json_file(self, file_path: str, filename: str) -> str | None:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return (
                f"文件 {filename} 不是有效的 JSON 格式，"
                f"可能是下载源返回了错误响应，请尝试切换下载源"
            )

        if isinstance(data, dict):
            error_keys = {"Code", "Message", "RequestId", "Success"}
            if error_keys.issubset(data.keys()):
                msg = data.get("Message", "未知错误")
                return (
                    f"文件 {filename} 下载失败: 服务端返回错误 ({msg})，"
                    f"请尝试切换下载源"
                )

        if filename == "vocabulary.json" and not isinstance(data, list):
            return (
                f"文件 {filename} 内容无效: 应为 JSON 数组，"
                f"请尝试切换下载源"
            )

        return None

    def _ensure_vocabulary_json(self, model_path: str):
        vocab_json = os.path.join(model_path, "vocabulary.json")
        vocab_txt = os.path.join(model_path, "vocabulary.txt")

        need_generate = False
        if not os.path.isfile(vocab_json):
            need_generate = True
        else:
            try:
                with open(vocab_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    need_generate = True
            except (json.JSONDecodeError, UnicodeDecodeError):
                need_generate = True

        if not need_generate:
            return

        if not os.path.isfile(vocab_txt):
            return

        self.log.emit("从 vocabulary.txt 生成 vocabulary.json...", "info")
        try:
            with open(vocab_txt, "r", encoding="utf-8") as f:
                tokens = [line.rstrip("\r\n") for line in f]
            with open(vocab_json, "w", encoding="utf-8") as f:
                json.dump(tokens, f, ensure_ascii=False)
            self.log.emit("vocabulary.json 生成成功", "info")
        except Exception as e:
            self.log.emit(f"生成 vocabulary.json 失败: {e}", "warning")

    def _download_file_with_progress(self, url: str, output_path: str,
                                      base_percent: int = 0, file_percent: int = 100):
        import httpx

        headers = {"User-Agent": "DeepSub/0.1.0"}
        if os.path.exists(output_path):
            existing_size = os.path.getsize(output_path)
            if existing_size > 0:
                headers["Range"] = f"bytes={existing_size}-"
        else:
            existing_size = 0

        with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=120.0) as response:
            if response.status_code == 416:
                return

            total_size = int(response.headers.get("content-length", 0))
            if response.status_code == 206:
                content_range = response.headers.get("content-range", "")
                if "/" in content_range:
                    total_size = int(content_range.split("/")[-1])

            downloaded = existing_size
            mode = "ab" if existing_size > 0 and response.status_code == 206 else "wb"

            with open(output_path, mode) as f:
                for chunk in response.iter_bytes(chunk_size=8192):
                    if self._cancelled:
                        return
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        file_pct = int(downloaded / total_size * file_percent)
                        self.progress.emit(self._model_id, base_percent + file_pct)

    def _load_download_state(self, state_file: str) -> list:
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    return json.load(f).get("downloaded_files", [])
            except Exception:
                pass
        return []

    def _save_download_state(self, state_file: str, downloaded_files: list):
        try:
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump({"downloaded_files": downloaded_files}, f)
        except Exception:
            pass

    def cancel(self):
        self._cancelled = True
