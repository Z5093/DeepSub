APP_NAME = "DeepSub"
APP_VERSION = "0.1.0"
APP_AUTHOR = "DeepSub"

SUPPORTED_VIDEO_FORMATS = [
    "*.mp4", "*.mkv", "*.avi", "*.mov", "*.wmv", "*.flv",
]

SUPPORTED_SUBTITLE_FORMATS = [
    "*.srt", "*.ass", "*.vtt",
]

WHISPER_MODELS = {
    "tiny": {"size_mb": 77, "label": "Tiny"},
    "base": {"size_mb": 148, "label": "Base"},
    "small": {"size_mb": 488, "label": "Small"},
    "medium": {"size_mb": 1530, "label": "Medium"},
    "large-v1": {"size_mb": 3090, "label": "Large V1"},
    "large-v2": {"size_mb": 3090, "label": "Large V2"},
    "large-v3": {"size_mb": 3090, "label": "Large V3"},
    "large-v3-turbo": {"size_mb": 1720, "label": "Large V3 Turbo"},
}

PROCESSING_STEPS = [
    "download", "audio", "asr", "split1", "translate", "split2", "export",
]

STEP_STATUS_PENDING = "pending"
STEP_STATUS_ACTIVE = "active"
STEP_STATUS_COMPLETED = "completed"
STEP_STATUS_ERROR = "error"
STEP_STATUS_SKIPPED = "skipped"

STEP_IDS = {
    "download": "download",
    "audio": "audio",
    "asr": "asr",
    "split1": "split1",
    "translate": "translate",
    "split2": "split2",
    "export": "export",
}

STEP_FILE_MAP = {
    "asr": "01_原始字幕.json",
    "split1": "02_语义分段.json",
    "translate": "03_初步翻译.json",
    "split2": "04_最终字幕.json",
}

STATE_FILE = "处理状态.json"
AUDIO_SUFFIX = "_音频.flac"
OUTPUT_SUBDIR = "DeepSub"

DOWNLOAD_DIR_NAME = "downloads"
DOWNLOAD_SUBDIR_VIDEOS = "videos"
DOWNLOAD_SUBDIR_SUBTITLES = "subtitles"
DOWNLOAD_SUBDIR_AUDIO = "audio"
DOWNLOAD_SUBDIR_GENERATED = "generated"

MODELS_DIR_NAME = "models"
TOOLS_DIR_NAME = "tools"

SUPPORTED_BROWSERS = [
    ("auto", "自动检测"),
    ("chrome", "Google Chrome"),
    ("edge", "Microsoft Edge"),
    ("firefox", "Mozilla Firefox"),
    ("brave", "Brave"),
    ("opera", "Opera"),
    ("vivaldi", "Vivaldi"),
    ("none", "不使用浏览器 Cookie"),
]

PROXY_SYSTEM = "system"
PROXY_HTTP = "http"
PROXY_SOCKS5 = "socks5"
PROXY_NONE = "none"

FORMAT_BEST = "best"
FORMAT_1080P = "1080p"
FORMAT_720P = "720p"
FORMAT_480P = "480p"
FORMAT_AUDIO_ONLY = "audio_only"

MODEL_SOURCE_HUGGINGFACE = "huggingface"
MODEL_SOURCE_MODELSCOPE = "modelscope"

TRANSLATION_STEP_ACCURACY = "accuracy"
TRANSLATION_STEP_FLUENCY = "fluency"
TRANSLATION_STEP_LOCALIZATION = "localization"

BILINGUAL_LAYOUT_STACKED = "stacked"
BILINGUAL_LAYOUT_SIDE_BY_SIDE = "side_by_side"

BCP47_LANGUAGES = {
    "zh-CN": "简体中文",
    "zh-TW": "繁体中文",
    "en-US": "英语 (美式)",
    "en-GB": "英语 (英式)",
    "ja-JP": "日语",
    "ko-KR": "韩语",
    "fr-FR": "法语",
    "de-DE": "德语",
    "es-ES": "西班牙语",
    "ru-RU": "俄语",
    "pt-BR": "葡萄牙语 (巴西)",
    "it-IT": "意大利语",
    "ar-SA": "阿拉伯语",
    "th-TH": "泰语",
    "vi-VN": "越南语",
}

PAGE_WORKSPACE = "workspace"
PAGE_DOWNLOAD = "download"
PAGE_SETTINGS = "settings"
PAGE_FASTERWHISPER = "fasterwhisper"
PAGE_LLAMACPP = "llamacpp"

# 翻译请求超时（毫秒）。本地模型与远程 API 分开配置：
# - 本地 llama.cpp 纯 CPU 推理慢，单窗口几分钟很常见，留足 30 分钟
# - 远程 API 一般几秒返回，15 分钟足够，超了就是服务端异常
DEFAULT_LLAMACPP_TIMEOUT_MS = 1_800_000
DEFAULT_API_TIMEOUT_MS = 900_000
