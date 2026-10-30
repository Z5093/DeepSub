import json
import os
from typing import Any, Dict, Optional
from PyQt5.QtCore import QObject

from app.common.paths import get_config_dir
from app.common.signal_bus import signal_bus


DEFAULT_CONFIG = {
    "general": {
        "download_path": "",
        "output_subdir": "DeepSub",
        "source_language": "en-US",
        "target_language": "zh-CN",
        "output_formats": ["srt"],
        "proxy_type": "system",
        "proxy_host": "",
        "proxy_port": 0,
        "browser_cookie": "auto",
        "merge_video_audio": True,
    },
    "asr": {
        "engine": "faster_whisper",
        "gpu_mode": "auto",
        "model": "large-v3",
        "model_source": "modelscope",
        "one_word": 1,
        "sentence": True,
        "max_line_width": 30,
        "max_line_count": 1,
        "max_comma": 20,
        "max_comma_cent": 50,
        "vad_enabled": False,
        "vad_threshold": 0.18,
        "vad_min_speech_duration_ms": 300,
        "vad_min_silence_duration_ms": 700,
        "vad_speech_pad_ms": 400,
        "vad_preset": "asmr",
    },
    "translate": {
        "enable_translation": True,
        "service": "openai_compat",
        "api_base_url": "",
        "api_key": "",
        "model": "",
        "timeout": 900000,
        "concurrency": 1,
        "context_window_size": 3,
        "context_size": 1,
        "prompt_template": "default",
        "glossary": "",
        "video_style": "",
        "auto_detect_style": True,
    },
    "llamacpp": {
        "bin_dir": "",
        "model_path": "",
        "host": "127.0.0.1",
        "port": 8080,
        "ctx_size": 32768,
        "n_predict": -1,
        "n_gpu_layers": 99,
        "timeout": 1800000,
        "chat_template_kwargs": {"enable_thinking": False},
        "temperature": 0.8,
        "top_k": 20,
        "top_p": 0.95,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "repeat_penalty": None,
        "extra_launch_args": "",
    },
    "output": {
        "subtitle_style": "default",
        "bilingual_layout": "stacked",
        "ass_font": "Source Han Sans SC",
        "ass_font_size": 20,
        "ass_primary_color": "&H00FFFFFF",
        "ass_outline_color": "&H00000000",
        "ass_outline_width": 2,
        "ass_shadow_distance": 1,
        "ass_alignment": 2,
    },
    "interface": {
        "language": "zh-CN",
        "font_size": 13,
    },
    "performance": {
        "thread_count": 4,
        "cache_size_mb": 500,
        "debug_mode": False,
    },
}


class ConfigManager(QObject):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config_dir: str = ""):
        try:
            object.__getattribute__(self, "_initialized")
            return
        except AttributeError:
            pass
        super().__init__()
        self._initialized = True
        self._config_dir = config_dir or get_config_dir()
        self._config_path = os.path.join(self._config_dir, "settings.json")
        self._config: Dict[str, Any] = {}
        self._load()

    def _load(self):
        if os.path.exists(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
                self._merge_defaults()
            except (json.JSONDecodeError, IOError):
                self._config = self._deep_copy(DEFAULT_CONFIG)
        else:
            self._config = self._deep_copy(DEFAULT_CONFIG)

    def _merge_defaults(self):
        for section, values in DEFAULT_CONFIG.items():
            if section not in self._config:
                self._config[section] = self._deep_copy(values)
            else:
                for key, default_val in values.items():
                    if key not in self._config[section]:
                        self._config[section][key] = default_val

    def save(self):
        os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(self._config, f, indent=2, ensure_ascii=False)

    def get(self, section: str, key: str, default: Any = None) -> Any:
        try:
            return self._config[section][key]
        except KeyError:
            return default

    def set(self, section: str, key: str, value: Any):
        if section not in self._config:
            self._config[section] = {}
        self._config[section][key] = value
        signal_bus.config_changed.emit(f"{section}.{key}")

    def get_section(self, section: str) -> Dict[str, Any]:
        return self._config.get(section, {})

    def set_section(self, section: str, values: Dict[str, Any]):
        self._config[section] = values
        signal_bus.config_changed.emit(section)

    def apply(self):
        self.save()
        signal_bus.config_applied.emit()

    @staticmethod
    def _deep_copy(d: dict) -> dict:
        return json.loads(json.dumps(d))


config_manager = ConfigManager()
