"""Whisper (CTranslate2) 模型目录里「mel 特征维度」相关文件的读写与修复。

背景：
  large-v3 / large-v3-turbo 这类模型是 **128 维** mel 输入。faster-whisper 生成
  特征时按 `preprocessor_config.json` 里的 `feature_size` 决定维度，**文件缺失时
  默认 80 维**，于是进程直接崩：

      ValueError: Invalid input features shape:
      expected an input with shape (1, 128, 3000),
      but got an input with shape (1, 80, 3000) instead

  这个文件只有 340 字节，早期的下载清单漏了它，装出来的 large-v3 目录缺这一个
  文件就必然失败（模型权重 3GB 都是好的）。所以这里提供「检测 + 就地补齐」：
  缺失或维度不对时按标准 Whisper 参数写一份，正常目录一个字节都不动。

  不联网：内容对 Whisper 全系列是固定的（见 `_STANDARD_PREPROCESSOR`），
  离线也能修，避免为了 340 字节去阻塞界面。
"""
from __future__ import annotations

import json
import os

#: 决定 mel 通道数的文件，faster-whisper 启动时读它。
PREPROCESSOR_FILENAME = "preprocessor_config.json"

#: 模型名里出现这些片段即为 128 维 mel 模型（large-v3 / large-v3-turbo /
#: distil-large-v3 / large-v3-XXL 等）。tiny/small/medium/large-v1/v2 都是 80 维。
_MEL128_NAME_HINTS = ("large-v3", "turbo")

#: 与 HuggingFace `Systran/faster-whisper-large-v3/preprocessor_config.json`
#: 逐字段一致（340 字节），只有 feature_size 按模型调整。
_STANDARD_PREPROCESSOR = {
    "feature_size": 128,
    "hop_length": 160,
    "n_fft": 400,
    "n_samples": 480000,
    "nb_max_frames": 3000,
    "padding_side": "right",
    "padding_value": 0.0,
    "processor_class": "WhisperProcessor",
    "return_attention_mask": False,
    "sampling_rate": 16000,
}


def needs_128_mels(model_name: str) -> bool:
    """该模型是否需要 128 维 mel 特征（large-v3 系列）。"""
    name = (model_name or "").lower()
    return any(hint in name for hint in _MEL128_NAME_HINTS)


def read_feature_size(model_path: str) -> int | None:
    """读 `preprocessor_config.json` 里的 feature_size。

    文件不存在、内容损坏（例如下载源 404 时写入的 HTML 错误页）都返回 None，
    由调用方决定是「重建」还是「忽略」。
    """
    cfg_path = os.path.join(model_path, PREPROCESSOR_FILENAME)
    if not os.path.isfile(cfg_path):
        return None
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("feature_size")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def ensure_preprocessor_config(model_path: str, model_name: str) -> tuple[bool, str]:
    """保证模型目录里有与模型匹配的 `preprocessor_config.json`。

    返回 `(是否就绪, 说明)`：
      - 就绪且无需改动 → `(True, "")`
      - 已补齐/已修正   → `(True, 提示文字)`，调用方可以打到日志里
      - 写不进去        → `(False, 失败原因)`

    只有 128 维模型才会动这个文件；80 维模型（tiny/small/medium/large-v1/v2）
    本来就不需要它，缺失属正常，直接放行。
    """
    if not model_path or not os.path.isdir(model_path):
        # 目录本身的问题交给上层报「模型不存在」，这里不重复判定
        return True, ""

    expected = 128 if needs_128_mels(model_name) else None
    current = read_feature_size(model_path)

    if expected is None:
        # 80 维模型：有文件且能用就放行，缺了也不补
        return True, ""
    if current == expected:
        return True, ""

    payload = dict(_STANDARD_PREPROCESSOR)
    payload["feature_size"] = expected
    cfg_path = os.path.join(model_path, PREPROCESSOR_FILENAME)
    tmp_path = cfg_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, cfg_path)
    except OSError as e:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return False, f"无法写入 {cfg_path}：{e}"

    if current is None:
        return True, (
            f"模型目录缺少 {PREPROCESSOR_FILENAME}，已自动补齐 "
            f"(feature_size={expected})。缺这个文件时识别程序会按 80 维提取特征，"
            f"必定报 mel 通道维度不匹配"
        )
    return True, (
        f"{PREPROCESSOR_FILENAME} 的 feature_size={current} 与模型要求的 "
        f"{expected} 不符，已自动修正"
    )
