"""语言代码解析工具。

项目里语言存在多种表示形式（"auto"、"zh-CN"、"en-US" 等 BCP 47 标签），
而下游工具（pysbd 语义分段、prompt 模板）只接受有限的 ISO 639-1 代码。
本模块负责把任意来源的语言标识收敛为一个确定、可用的代码。
"""
from __future__ import annotations

import re
from typing import Optional

# pysbd 0.3.4 支持的 ISO 639-1 代码
SUPPORTED_SEGMENTATION_LANGUAGES = frozenset({
    "am", "ar", "bg", "da", "de", "el", "en", "es", "fa", "fr", "hi",
    "hy", "it", "ja", "kk", "mr", "my", "nl", "pl", "ru", "sk", "ur", "zh",
})

DEFAULT_SEGMENTATION_LANGUAGE = "en"

# 需要特殊处理的完整标签（BCP 47 变体 / 历史别名）
_ALIASES = {
    "zh-hans": "zh",
    "zh-hant": "zh",
    "zh-cn": "zh",
    "zh-tw": "zh",
    "zh-hk": "zh",
    "zh-mo": "zh",
    "zh-sg": "zh",
    "cmn": "zh",
    "yue": "zh",
    "jp": "ja",
    "jpn": "ja",
    "in": "id",
    "iw": "he",
    "nb": "no",
    "nn": "no",
    "pt-br": "pt",
    "pt-pt": "pt",
}

_AUTO_VALUES = {"", "auto", "autodetect", "auto-detect", "none", "null", "unknown"}

# 按字符脚本猜测语言（只在提示缺失/无效时使用）
_SCRIPT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ja", re.compile(r"[\u3040-\u30ff]")),      # 平假名 / 片假名
    ("ko", re.compile(r"[\uac00-\ud7af\u1100-\u11ff]")),
    ("zh", re.compile(r"[\u4e00-\u9fff]")),      # 汉字
    ("ru", re.compile(r"[\u0400-\u04ff]")),
    ("el", re.compile(r"[\u0370-\u03ff]")),
    ("hy", re.compile(r"[\u0530-\u058f]")),
    ("hi", re.compile(r"[\u0900-\u097f]")),
    ("my", re.compile(r"[\u1000-\u109f]")),
    ("am", re.compile(r"[\u1200-\u137f]")),
    ("ar", re.compile(r"[\u0600-\u06ff]")),
]

_CJK_LANGUAGES = frozenset({"zh", "ja", "ko"})

# 提示词里使用的语言中文名（Index-Translate 官方模板要求填语言名而非语言代码）
_LANGUAGE_DISPLAY_NAMES = {
    "zh": "中文", "en": "英语", "ja": "日语", "ko": "韩语", "ru": "俄语",
    "fr": "法语", "de": "德语", "es": "西班牙语", "pt": "葡萄牙语", "it": "意大利语",
    "ar": "阿拉伯语", "th": "泰语", "vi": "越南语", "id": "印尼语", "ms": "马来语",
    "hi": "印地语", "bn": "孟加拉语", "ta": "泰米尔语", "te": "泰卢固语",
    "tr": "土耳其语", "nl": "荷兰语", "pl": "波兰语", "uk": "乌克兰语",
    "cs": "捷克语", "sk": "斯洛伐克语", "hu": "匈牙利语", "ro": "罗马尼亚语",
    "bg": "保加利亚语", "el": "希腊语", "he": "希伯来语", "fa": "波斯语",
    "sv": "瑞典语", "da": "丹麦语", "no": "挪威语", "fi": "芬兰语",
    "hr": "克罗地亚语", "sr": "塞尔维亚语", "ca": "加泰罗尼亚语",
    "tl": "菲律宾语", "my": "缅甸语", "hy": "亚美尼亚语", "am": "阿姆哈拉语",
    "kk": "哈萨克语", "ur": "乌尔都语", "sw": "斯瓦希里语",
}


def language_display_name(raw: Optional[str]) -> str:
    """语言标识 → 提示词可用的中文名（"ru-RU" → "俄语"）。

    无法映射时返回空串，调用方自行决定回退（通常是保留原始标识）。
    """
    code = normalize_language(raw)
    if not code:
        return ""
    name = _LANGUAGE_DISPLAY_NAMES.get(code)
    if name:
        return name
    # 退而求其次：从 BCP47_LANGUAGES 里取名字并去掉括注（"英语 (美式)" → "英语"）
    try:
        from app.common.constants import BCP47_LANGUAGES
    except ImportError:
        return ""
    for tag, label in BCP47_LANGUAGES.items():
        if normalize_language(tag) == code:
            return label.split("(")[0].strip()
    return ""


def normalize_language(raw: Optional[str]) -> str:
    """把 BCP 47 标签规整为主语言代码（小写、下划线转连字符）。"""
    if not raw:
        return ""
    text = str(raw).strip().lower().replace("_", "-")
    if text in _AUTO_VALUES:
        return ""
    if text in _ALIASES:
        return _ALIASES[text]
    primary = text.split("-")[0].split("/")[0].strip()
    if primary in _ALIASES:
        return _ALIASES[primary]
    return primary


def is_auto_language(raw: Optional[str]) -> bool:
    """判断语言标识是否表示"自动检测"或未填写。"""
    return normalize_language(raw) == ""


def detect_language_from_text(text: str) -> Optional[str]:
    """按字符脚本粗判语言，无法判断时返回 None。"""
    if not text:
        return None
    for code, pattern in _SCRIPT_PATTERNS:
        if len(pattern.findall(text[:4000])) >= 2:
            return code
    return None


def resolve_segmentation_language(raw: Optional[str], sample_text: str = "") -> str:
    """返回可直接传给 pysbd 的语言代码，任何输入都不会抛异常。

    优先级：显式且受支持的代码 → 文本脚本识别 → 默认英语。
    """
    code = normalize_language(raw)
    if code in SUPPORTED_SEGMENTATION_LANGUAGES:
        return code

    detected = detect_language_from_text(sample_text)
    if detected in SUPPORTED_SEGMENTATION_LANGUAGES:
        return detected

    # 日文/中文以外的 CJK（如韩文）没有专用分段器，退回英语分段器
    return DEFAULT_SEGMENTATION_LANGUAGE


def is_cjk_language(raw: Optional[str]) -> bool:
    """判断是否中日韩语言（用于合并策略）。"""
    code = normalize_language(raw)
    return code in _CJK_LANGUAGES
