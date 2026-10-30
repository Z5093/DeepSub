from __future__ import annotations

import logging
import re
import time

import httpx

from typing import Optional, Callable

from app.core.translator.base import TranslatorBase
from app.common.constants import DEFAULT_API_TIMEOUT_MS

logger = logging.getLogger(__name__)

# 远程 API 超时（秒）。与本地 llama.cpp 的 30 分钟分开配置，默认 15 分钟
_DEFAULT_TIMEOUT = DEFAULT_API_TIMEOUT_MS / 1000.0
_DEFAULT_MAX_RETRIES = 10
_DEFAULT_RETRY_DELAY = 30.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Index-Translate 官方服务的客户端标识（文本/图片接口都建议带）
_INDEX_TRANSLATE_UA = "Index-Translate-Client/1.0"


_XML_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9-]*(?:\s[^>]*)?\s*/?>", re.IGNORECASE)

_TRANSLATION_TAG_RE = re.compile(
    r"</?t+r?anslation>", re.IGNORECASE,
)

_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff\u00ad]")
# 行首序号：[1] / 1) / 1、 / 1: / "1. "（排除 3.14 这类小数、"1, 2, 3" 这类计数）
_LEADING_NUMBER_RE = re.compile(r"^\s*(?:\[\d+\]|【\d+】|\d+\s*[\)、:：]|\d+\.(?!\d)\s*)\s*")
# 整段被引号包住（模型偶尔会加）
_WRAPPED_QUOTE_RE = re.compile(
    r'^["“”「『\'](.+)["“”」』\']$', re.DOTALL,
)
_CJK_RANGE = (
    (0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0x3040, 0x30FF), (0xAC00, 0xD7AF),
)
_CJK_PUNCT = set("，。！？、；：…～“”‘’（）《》【】「」『』—·－")
_FULLWIDTH_RANGES = ((0x3000, 0x303F), (0xFF01, 0xFF60))


def _is_cjk_char(ch: str) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in _CJK_RANGE)


def is_cjk_like(ch: str) -> bool:
    """字符是否属于"中日韩文本"（含全角标点），用于拼接时不加空格。"""
    if not ch:
        return False
    if _is_cjk_char(ch) or ch in _CJK_PUNCT:
        return True
    code = ord(ch)
    return any(low <= code <= high for low, high in _FULLWIDTH_RANGES)


def cjk_char_count(text: str) -> int:
    return sum(1 for ch in text or "" if _is_cjk_char(ch))


def has_letters(text: str) -> bool:
    return any(ch.isalpha() for ch in text or "")


def strip_llm_tags(text: str) -> str:
    return _XML_TAG_RE.sub("", text).strip()


def normalize_translation_text(text: str) -> str:
    """把 LLM 输出的译文规范成"单行纯文本"。

    LLM 经常把一条字幕的译文写成多行（甚至写成一首小诗），直接落盘会让
    一条字幕下面出现好几行中文。这里统一处理：
    - 去掉 <translation> 等标签、行首序号、Zero-width 字符
    - 多行压成一行（CJK 之间不加空格，其余用空格连接）
    - 压缩多余空白，去掉整段包裹的引号
    """
    if not text:
        return ""

    cleaned = _TRANSLATION_TAG_RE.sub("", text)
    cleaned = strip_llm_tags(cleaned)
    cleaned = _ZERO_WIDTH_RE.sub("", cleaned)

    lines: list[str] = []
    for raw_line in cleaned.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _LEADING_NUMBER_RE.sub("", raw_line).strip()
        if line:
            lines.append(line)

    if not lines:
        return ""

    merged = lines[0]
    for line in lines[1:]:
        if is_cjk_like(merged[-1]) and is_cjk_like(line[0]):
            merged += line
        else:
            merged = f"{merged} {line}"

    merged = re.sub(r"[ \t\u3000]+", " ", merged).strip()

    wrapped = _WRAPPED_QUOTE_RE.match(merged)
    if wrapped and len(wrapped.group(1).strip()) >= 2:
        merged = wrapped.group(1).strip()

    return merged


def looks_untranslated(source: str, translation: str, target_lang: str) -> bool:
    """判断译文是否等于没翻（空译文 / 仍是源语言 / 只抄了原文）。

    仅对目标语言是中日韩的情况做字符集校验，避免把拉丁语系互译误判。
    语言名可能写作 "Chinese/zh" 或 "中文/日语/韩语"，两种写法都要认。
    """
    if not re.search(r"zh|ja|ko|chinese|japanese|korean|中文|汉语|汉字|日文|日语|日本語|韩文|韩语|韩国語",
                     target_lang or "", re.IGNORECASE):
        return False
    if not has_letters(source):
        # 纯符号/emoji 条目无需翻译
        return False
    if not (translation or "").strip():
        return True
    if cjk_char_count(translation) == 0:
        return True
    return False


class OpenAICompatTranslator(TranslatorBase):
    def __init__(self, api_base_url: str, api_key: str, model: str = "gpt-4o-mini",
                 timeout: float = _DEFAULT_TIMEOUT, max_retries: int = _DEFAULT_MAX_RETRIES,
                 retry_delay: float = _DEFAULT_RETRY_DELAY,
                 chat_template_kwargs: Optional[dict] = None):
        self._api_base_url = api_base_url.rstrip("/")
        if self._api_base_url.endswith("/v1"):
            self._api_base_url = self._api_base_url[:-3]
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        # 透传给服务端的 chat template 参数（Index-Translate 需要 enable_thinking=false）
        self._chat_template_kwargs = chat_template_kwargs or {}

    @property
    def timeout_seconds(self) -> float:
        """当前生效的请求超时（秒），供日志/界面显示。"""
        return self._timeout

    def translate(self, text: str, source_lang: str, target_lang: str,
                  context: str = "", glossary: str = "", prompt: str = "") -> str:
        if not text.strip():
            return ""

        if prompt:
            # 自定义 prompt 已内含原文/上下文/术语表占位，user 消息只送原文，
            # 避免同一内容发送两遍浪费输入 token
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ]
        else:
            system_msg = (
                f"You are a professional translator. Translate from {source_lang} to {target_lang}. "
                f"Provide your translation wrapped in <translation> tags."
            )
            user_parts: list[str] = []
            if glossary:
                user_parts.append(f"Glossary:\n{glossary}")
            if context:
                user_parts.append(f"Context:\n{context}")
            user_parts.append(f"Source text:\n{text}")
            messages = [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": "\n\n".join(user_parts)},
            ]

        raw = self._call_api(messages)
        return self._extract_translation(raw)

    def _call_api(self, messages: list[dict], max_tokens: Optional[int] = None,
                  timeout: Optional[float] = None) -> str:
        url = f"{self._api_base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        # 官方 Index-Translate 服务端会校验客户端标识，缺这个 header 可能被拒
        if "index-translate" in self._api_base_url or "bilibili" in self._api_base_url:
            headers["User-Agent"] = _INDEX_TRANSLATE_UA

        payload: dict = {
            "model": self._model,
            "messages": messages,
            # 专用翻译模型官方默认贪心解码（temperature=0），不需要发散性
            "temperature": 0.0,
        }
        if max_tokens is not None and int(max_tokens) > 0:
            payload["max_tokens"] = int(max_tokens)
        if self._chat_template_kwargs:
            payload["chat_template_kwargs"] = dict(self._chat_template_kwargs)

        request_timeout = self._timeout if timeout is None else float(timeout)
        # 显式传入 timeout 的调用（例如风格分类）不重试：失败要立刻回退，而不是翻倍等待
        max_retries = 0 if timeout is not None else self._max_retries
        last_error: Optional[Exception] = None
        timeout_retries = 0
        for attempt in range(max_retries + 1):
            started = time.monotonic()
            try:
                with httpx.Client(timeout=request_timeout) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    try:
                        return data["choices"][0]["message"]["content"]
                    except (KeyError, IndexError, TypeError) as e:
                        raise RuntimeError(f"Unexpected API response format: {data}") from e
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code
                if status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries:
                    logger.warning(
                        "API 返回 %d，第 %d/%d 次重试，等待 %.0fs",
                        status_code, attempt + 1, max_retries, self._retry_delay,
                    )
                    time.sleep(self._retry_delay)
                    last_error = e
                    continue
                raise RuntimeError(
                    f"API request failed with status {e.response.status_code}: {e.response.text}"
                ) from e
            except httpx.RequestError as e:
                elapsed = time.monotonic() - started
                is_timeout = isinstance(e, httpx.TimeoutException)
                # 超时不算瞬时故障：等到超时上限还没响应，多试几次只是把等待时间翻倍
                if attempt >= max_retries or (is_timeout and timeout_retries >= 1):
                    if is_timeout:
                        raise RuntimeError(
                            f"API request error: {e}"
                            f"（单次已等待 {elapsed:.0f}s，超时上限 {request_timeout:.0f}s，放弃重试）"
                        ) from e
                    raise RuntimeError(f"API request error: {e}") from e
                if is_timeout:
                    timeout_retries += 1
                logger.warning(
                    "API 请求异常 (%s，耗时 %.0fs)，第 %d/%d 次重试，等待 %.0fs",
                    type(e).__name__, elapsed, attempt + 1, max_retries, self._retry_delay,
                )
                time.sleep(self._retry_delay)
                last_error = e
                continue

        raise RuntimeError(f"API request error after {max_retries} retries") from last_error

    def _extract_translation(self, response: str) -> str:
        result = self._try_extract_translation(response)
        if result is not None:
            return normalize_translation_text(result)
        return normalize_translation_text(response)

    def _try_extract_translation(self, response: str) -> Optional[str]:
        match = _TRANSLATION_TAG_RE.search(response)
        if match:
            content = response[match.end():]
            end_match = _TRANSLATION_TAG_RE.search(content)
            if end_match and end_match.start() > 0:
                return content[:end_match.start()].strip()
            return content.strip()

        for pattern in [
            r"```(?:translation)?\s*\n(.*?)```",
            r"【(.*?)】",
            r"翻译[结果]?\s*[:：]\s*(.*?)(?:\n\n|\Z)",
            r"Translation\s*[:：]\s*(.*?)(?:\n\n|\Z)",
        ]:
            match = re.search(pattern, response, re.DOTALL)
            if match:
                return match.group(1).strip()

        cleaned = self._strip_non_translation_content(response)
        if cleaned and cleaned != response.strip():
            return cleaned

        return None

    def _strip_non_translation_content(self, response: str) -> Optional[str]:
        lines = response.strip().split("\n")
        content_lines: list[str] = []
        skip_patterns = [
            re.compile(r'^(?:好的|当然|以下是|Here\s+is|Sure|Of\s+course|I\'ll|Let\s+me|The\s+translation)', re.IGNORECASE),
            re.compile(r'^(?:解释|说明|备注|注|Note|Explanation|Comment)', re.IGNORECASE),
            re.compile(r'^(?:希望|如果|如有|Hope|If\s+you)', re.IGNORECASE),
            re.compile(r'^\s*[-*#]\s*(?:翻译|Translation|说明|Note)', re.IGNORECASE),
        ]

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if content_lines:
                    content_lines.append("")
                continue

            is_meta = False
            for pat in skip_patterns:
                if pat.match(stripped):
                    is_meta = True
                    break

            if not is_meta:
                content_lines.append(line)

        while content_lines and not content_lines[-1].strip():
            content_lines.pop()

        result = "\n".join(content_lines).strip()
        return result if result else None

    def is_available(self) -> bool:
        url = f"{self._api_base_url}/v1/models"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if "index-translate" in self._api_base_url or "bilibili" in self._api_base_url:
            headers["User-Agent"] = _INDEX_TRANSLATE_UA
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, headers=headers)
                # 部分兼容端点不实现 /v1/models（返回 401/404），只要不是 5xx 就认为服务在线
                return resp.status_code < 500
        except httpx.RequestError:
            return False
