from __future__ import annotations

import os

from app.common.constants import (
    DOWNLOAD_DIR_NAME,
    DOWNLOAD_SUBDIR_VIDEOS,
    DOWNLOAD_SUBDIR_SUBTITLES,
    DOWNLOAD_SUBDIR_AUDIO,
    DOWNLOAD_SUBDIR_GENERATED,
    MODELS_DIR_NAME,
    TOOLS_DIR_NAME,
)
from app.common.paths import get_app_root, get_resource_path as _resolve_resource

_PROJECT_ROOT = get_app_root()

_STEP_MAP = {
    "asr": "01_原始字幕.json",
    "split1": "02_语义分段.json",
    "translate": "03_初步翻译.json",
    "split2": "04_最终字幕.json",
}


def get_project_root() -> str:
    return _PROJECT_ROOT


def get_resource_path(*parts: str) -> str:
    """返回 resource/ 下某文件的绝对路径（打包后自动定位到 exe 同级资源目录）。"""
    return _resolve_resource(*parts)


def get_app_icon_path() -> str:
    ico = get_resource_path("icons", "app.ico")
    if os.path.exists(ico):
        return ico
    return get_resource_path("icons", "app.png")


def get_downloads_root() -> str:
    from app.common.config import config_manager

    custom_path = config_manager.get("general", "download_path", "")
    if custom_path:
        return custom_path
    return os.path.join(_PROJECT_ROOT, DOWNLOAD_DIR_NAME)


def get_models_root() -> str:
    d = os.path.join(_PROJECT_ROOT, MODELS_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def get_tools_root() -> str:
    d = os.path.join(_PROJECT_ROOT, TOOLS_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def get_whisper_models_dir() -> str:
    d = os.path.join(get_models_root(), "faster-whisper")
    os.makedirs(d, exist_ok=True)
    return d


def get_vad_models_dir() -> str:
    d = os.path.join(get_models_root(), "silero-vad")
    os.makedirs(d, exist_ok=True)
    return d


def get_fw_gpu_dir() -> str:
    d = os.path.join(get_tools_root(), "faster-whisper-xxl")
    os.makedirs(d, exist_ok=True)
    return d


_EXE_MODEL_PREFIX = "faster-whisper-"
_EXE_DISTIL_PREFIX = "faster-distil-whisper-"


def exe_model_dir_name(model_id: str) -> str:
    """识别程序在自身 `_models` 下使用的目录名。

    XXL 版把 `-m large-v3` 解析成 `<exe目录>/_models/faster-whisper-large-v3`，
    distil 系列则用 `faster-distil-whisper-` 前缀。
    """
    if model_id.startswith("distil-"):
        return _EXE_DISTIL_PREFIX + model_id[len("distil-"):]
    return _EXE_MODEL_PREFIX + model_id


def get_whisper_model_candidates(model_id: str) -> list[str]:
    """模型可能存放的所有位置，按优先级排列（默认目录优先）。"""
    candidates = [os.path.join(get_whisper_models_dir(), model_id)]
    gpu_root = get_fw_gpu_dir()
    try:
        entries = sorted(os.listdir(gpu_root))
    except OSError:
        entries = []
    for name in entries:
        sub = os.path.join(gpu_root, name)
        if os.path.isdir(sub):
            candidates.append(
                os.path.join(sub, "_models", exe_model_dir_name(model_id))
            )
    return candidates


def _looks_like_model(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "model.bin"))


def resolve_whisper_model_dir(model_id: str) -> str:
    """返回模型实际所在目录。

    目录联接不可用时，识别模块会把模型**移动**到 `<exe目录>/_models/` 下，
    所以不能再只看默认位置。都没找到时返回默认位置，便于下载流程照常使用。
    """
    candidates = get_whisper_model_candidates(model_id)
    for path in candidates:
        if _looks_like_model(path):
            return path
    return candidates[0]


def is_whisper_model_installed(model_id: str) -> bool:
    return _looks_like_model(resolve_whisper_model_dir(model_id))


def get_fw_cpu_exe() -> str:
    return os.path.join(get_tools_root(), "faster-whisper", "faster-whisper.exe")


def get_videos_dir() -> str:
    d = os.path.join(get_downloads_root(), DOWNLOAD_SUBDIR_VIDEOS)
    os.makedirs(d, exist_ok=True)
    return d


def get_subtitles_dir() -> str:
    d = os.path.join(get_downloads_root(), DOWNLOAD_SUBDIR_SUBTITLES)
    os.makedirs(d, exist_ok=True)
    return d


def get_audio_dir() -> str:
    d = os.path.join(get_downloads_root(), DOWNLOAD_SUBDIR_AUDIO)
    os.makedirs(d, exist_ok=True)
    return d


def get_generated_dir() -> str:
    d = os.path.join(get_downloads_root(), DOWNLOAD_SUBDIR_GENERATED)
    os.makedirs(d, exist_ok=True)
    return d


def get_video_stem(video_path: str) -> str:
    return os.path.splitext(os.path.basename(video_path))[0]


def get_output_dir(video_path: str) -> str:
    return os.path.join(os.path.dirname(video_path), "DeepSub")


def ensure_output_dir(video_path: str) -> str:
    output_dir = get_output_dir(video_path)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def get_audio_output_path(video_path: str) -> str:
    output_dir = get_output_dir(video_path)
    stem = get_video_stem(video_path)
    return os.path.join(output_dir, f"{stem}_音频.flac")


def get_step_output_path(video_path: str, step: str) -> str:
    output_dir = get_output_dir(video_path)
    filename = _STEP_MAP.get(step, f"{step}.json")
    return os.path.join(output_dir, filename)


def get_state_file_path(video_path: str) -> str:
    output_dir = get_output_dir(video_path)
    return os.path.join(output_dir, "处理状态.json")


def validate_path(path: str) -> None:
    normalized = os.path.normpath(path)
    if ".." in normalized:
        raise ValueError(f"Path traversal detected: {path}")
    if os.path.isabs(path) and not path.startswith(os.path.abspath(_PROJECT_ROOT)):
        raise ValueError(f"Unexpected absolute path: {path}")
