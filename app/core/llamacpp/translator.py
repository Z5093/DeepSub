from __future__ import annotations

import logging
import re
import time
from typing import Optional

import httpx

from app.core.translator.base import TranslatorBase
from app.core.translator.openai_compat import (
    normalize_translation_text,
    strip_llm_tags,
    _TRANSLATION_TAG_RE,
)
from app.common.constants import DEFAULT_LLAMACPP_TIMEOUT_MS
from app.core.llamacpp.server import LlamaCppServer

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RETRIES = 10
_DEFAULT_RETRY_DELAY = 30.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class LlamaCppTranslator(TranslatorBase):
    """通过 llama.cpp server 的 OpenAI 兼容 API 进行翻译。"""

    def __init__(self, config: dict):
        self._config = config
        self._server = LlamaCppServer(config)
        self._max_retries = int(config.get("max_retries", _DEFAULT_MAX_RETRIES))
        self._retry_delay = float(config.get("retry_delay", _DEFAULT_RETRY_DELAY))
        # llamacpp.timeout 以毫秒存储，默认 30 分钟（本地推理比远程 API 慢得多）
        try:
            timeout_ms = float(config.get("timeout", DEFAULT_LLAMACPP_TIMEOUT_MS))
        except (TypeError, ValueError):
            timeout_ms = float(DEFAULT_LLAMACPP_TIMEOUT_MS)
        self._timeout = (timeout_ms if timeout_ms > 0 else DEFAULT_LLAMACPP_TIMEOUT_MS) / 1000.0

    @property
    def server(self) -> LlamaCppServer:
        return self._server

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
        if not self._server.ensure_running():
            raise RuntimeError("llama.cpp server 未运行且无法启动")

        url = f"{self._server.base_url}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}

        payload: dict = {
            "messages": messages,
        }

        # Index-Translate 官方默认贪心解码；未配置时也用 0.0
        temperature = self._config.get("temperature", 0.0)
        if temperature is not None:
            payload["temperature"] = float(temperature)

        top_k = self._config.get("top_k")
        if top_k is not None:
            payload["top_k"] = int(top_k)

        top_p = self._config.get("top_p")
        if top_p is not None:
            payload["top_p"] = float(top_p)

        min_p = self._config.get("min_p")
        if min_p is not None and float(min_p) > 0:
            payload["min_p"] = float(min_p)

        # presence_penalty 会逼模型换词，对拟声词/重复台词有害，官方默认不设：
        # 只有显式配了非零值才发
        presence_penalty = self._config.get("presence_penalty")
        if presence_penalty not in (None, "") and float(presence_penalty) != 0.0:
            payload["presence_penalty"] = float(presence_penalty)

        repeat_penalty = self._config.get("repeat_penalty")
        if repeat_penalty is not None and repeat_penalty != "":
            payload["repeat_penalty"] = float(repeat_penalty)

        # 显式传入的 max_tokens（如风格分类只要 16 个 token）优先于全局 n_predict
        effective_max_tokens = max_tokens if max_tokens else self._config.get("n_predict")
        if effective_max_tokens is not None:
            try:
                if int(effective_max_tokens) > 0:
                    payload["max_tokens"] = int(effective_max_tokens)
            except (TypeError, ValueError):
                pass

        chat_template_kwargs = self._config.get("chat_template_kwargs")
        if chat_template_kwargs:
            if isinstance(chat_template_kwargs, str):
                import json
                try:
                    chat_template_kwargs = json.loads(chat_template_kwargs)
                except (json.JSONDecodeError, TypeError):
                    chat_template_kwargs = None
            if chat_template_kwargs and isinstance(chat_template_kwargs, dict):
                payload["chat_template_kwargs"] = chat_template_kwargs

        last_error: Optional[Exception] = None
        timeout_retries = 0
        request_timeout = self._timeout if timeout is None else float(timeout)
        # 显式传入 timeout 的调用（例如风格分类）不重试：失败要立刻回退
        max_retries = 0 if timeout is not None else self._max_retries
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
                        "llama.cpp API 返回 %d，第 %d/%d 次重试，等待 %.0fs",
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
                # 超时不算瞬时故障：本地模型等到超时上限仍无响应时，
                # 再重试 10 次只是把等待时间乘以 11，所以最多补一次
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
                    "llama.cpp API 请求异常 (%s，耗时 %.0fs)，第 %d/%d 次重试，等待 %.0fs",
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
        return self._server.is_running
