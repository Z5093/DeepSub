from __future__ import annotations

import copy
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Callable

from app.core.subtitle.models import SubtitleDocument, SubtitleItem, SubtitleMetadata
from app.core.translator.openai_compat import (
    OpenAICompatTranslator,
    cjk_char_count,
    has_letters,
    looks_untranslated,
    normalize_translation_text,
    strip_llm_tags,
    _TRANSLATION_TAG_RE,
)
from app.core.translator.prompt_template import (
    PromptTemplateManager,
    VIDEO_STYLES,
    build_insttrans_prompt,
    HARD_PREFIX,
    NOTE_PREFIX,
)
from app.core.translator.glossary import GlossaryManager
from app.core.translator.style_detector import StyleDetector


class SlidingWindowTranslator:
    """单步滑动窗口翻译器。

    两个独立参数：
    - window_size（界面「正文条数」）：一个窗口里需要翻译并最终输出的条目数
    - context_size（界面「上下文条数」）：窗口前后各取多少条原文给模型当参考

    每个窗口只调用一次模型：system = 角色/任务/规则/术语表/输出格式，
    user = 上下文参考（标注为不翻译）+ 编号原文。模型只输出正文译文，
    代码按顺序取回正文对应的条目，上下文永远不进结果。
    直接解析模型原始响应（不经 normalize_translation_text 的合并逻辑），
    避免多行译文被压成一行导致窗口解析失败、触发逐条重译的级联浪费。
    """

    def __init__(self, translator: OpenAICompatTranslator, template_manager: PromptTemplateManager,
                 glossary_manager: GlossaryManager, window_size: int = 3, context_size: int = 1,
                 concurrency: int = 1, video_style: str = "", auto_detect_style: bool = True):
        self._translator = translator
        self._template_manager = template_manager
        self._glossary_manager = glossary_manager
        self._window_size = window_size
        self._context_size = context_size
        self._concurrency = concurrency
        self._video_style = video_style
        self._auto_detect_style = auto_detect_style
        self._detected_style: str = ""

    def translate_document(self, document: SubtitleDocument,
                           source_lang: str, target_lang: str,
                           template_name: str = "default",
                           glossary_name: str = "",
                           progress_callback: Optional[Callable[[int, int], None]] = None) -> SubtitleDocument:
        subtitles = document.subtitles
        total = len(subtitles)
        if total == 0:
            return copy.deepcopy(document)

        effective_style = self._resolve_video_style(subtitles)
        effective_template = self._resolve_template_name(template_name, effective_style)

        # 并发只是把多个窗口同时送给模型，每个窗口仍带着自己的上下文；
        # 不再像旧版那样在并发时退化成"逐条裸翻、无窗口无上下文"
        translations = self._translate_with_context(
            subtitles, source_lang, target_lang, effective_template, glossary_name,
            effective_style, progress_callback, workers=self._concurrency,
        )

        new_subtitles: List[SubtitleItem] = []
        for i, item in enumerate(subtitles):
            new_item = SubtitleItem(
                index=item.index,
                start=item.start,
                end=item.end,
                text=item.text,
                translation=translations[i] if i < len(translations) else "",
                words=list(item.words),
                confidence=item.confidence,
            )
            new_subtitles.append(new_item)

        new_metadata = SubtitleMetadata(
            source_file=document.metadata.source_file,
            source_lang=source_lang,
            target_lang=target_lang,
            asr_model=document.metadata.asr_model,
            created_at=document.metadata.created_at,
            source_step=document.metadata.source_step,
        )

        return SubtitleDocument(metadata=new_metadata, subtitles=new_subtitles)

    @property
    def detected_style(self) -> str:
        return self._detected_style

    def pre_resolve_style(self, subtitles: List[SubtitleItem]) -> str:
        return self._resolve_video_style(subtitles)

    def _resolve_video_style(self, subtitles: List[SubtitleItem]) -> str:
        if self._detected_style:
            return self._detected_style

        if self._video_style and self._video_style in VIDEO_STYLES:
            self._detected_style = self._video_style
            return self._video_style

        if self._auto_detect_style:
            texts = [item.text for item in subtitles if item.text.strip()]
            detector = StyleDetector(translator=self._translator)
            detected = detector.detect(texts, use_ai=True)
            self._detected_style = detected
            return detected

        self._detected_style = ""
        return ""

    def _resolve_template_name(self, template_name: str, video_style: str) -> str:
        if video_style and video_style in VIDEO_STYLES:
            style_template = self._template_manager.get_template(video_style)
            if style_template:
                return video_style

        if template_name and template_name != "default":
            custom_template = self._template_manager.get_template(template_name)
            if custom_template:
                return template_name

        return "default"

    def _get_template(self, template_name: str) -> dict:
        template = self._template_manager.get_template(template_name)
        if not template:
            template = self._template_manager.get_template("default")
        return template

    def _get_glossary_section(self, glossary_name: str) -> str:
        if glossary_name:
            return self._glossary_manager.format_glossary_section(glossary_name)
        return ""

    def _get_glossary_lines(self, glossary_name: str) -> List[str]:
        """官方硬约束格式的术语行（`【硬性要求】专名/术语对照: A→B`）。"""
        if glossary_name:
            return self._glossary_manager.build_constraint_lines(glossary_name)
        return []

    def _translate_with_context(self, subtitles: List[SubtitleItem], source_lang: str, target_lang: str,
                                 template_name: str, glossary_name: str, video_style: str,
                                 progress_callback: Optional[Callable] = None,
                                 workers: int = 1) -> List[str]:
        """按「正文条数」切窗口，每窗前后各带「上下文条数」条原文作参考。

        窗口 = 需要翻译并输出的正文；上下文 = 只给模型看的原文，不翻译、不输出。
        多个窗口可以并行，因为上下文只取自原文、不依赖其他窗口的译文。
        """
        n = len(subtitles)
        translations: List[str] = [""] * n

        if n == 0:
            return translations

        window_size = max(1, self._window_size)
        context_size = max(0, self._context_size)
        template = self._get_template(template_name)
        glossary_lines = self._get_glossary_lines(glossary_name)
        windows = [(s, min(s + window_size, n)) for s in range(0, n, window_size)]

        def translate_range(start: int, end: int) -> List[str]:
            body_items = subtitles[start:end]
            above_items = subtitles[max(0, start - context_size):start]
            below_items = subtitles[end:min(n, end + context_size)]
            source_context = self._build_source_context(above_items, below_items)

            body_text = "\n".join(
                f"[{i + 1}] {item.text}" for i, item in enumerate(body_items)
            )
            results = self._translate_window(
                body_text, len(body_items), source_context,
                source_lang, target_lang, template, glossary_lines,
            )

            # 漏翻/空行兜底：单条重译时同样带上上下文，避免又退回逐句裸翻
            for i, translated in enumerate(results):
                if translated.strip() or not body_items[i].text.strip():
                    continue
                retry = self._translate_single(
                    body_items[i].text, source_lang, target_lang,
                    source_context, template_name, glossary_name, video_style,
                )
                if retry.strip():
                    results[i] = retry
            return results

        done = 0

        def commit(start: int, results: List[str]) -> None:
            nonlocal done
            for i, translated in enumerate(results):
                translations[start + i] = translated
            done += len(results)
            if progress_callback:
                progress_callback(min(done, n), n)

        if workers <= 1:
            for start, end in windows:
                commit(start, translate_range(start, end))
            return translations

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(translate_range, start, end): start for start, end in windows}
            for future in as_completed(futures):
                commit(futures[future], future.result())
        return translations

    def _translate_window(self, body_text: str, count: int, source_context: str,
                          source_lang: str, target_lang: str,
                          template: dict, glossary_lines: List[str]) -> List[str]:
        """单次调用翻译一个窗口，返回逐条译文列表。

        消息体由模板管理器生成：内置风格走官方 instTrans 结构（单条 user 消息），
        用户自定义模板仍走 system + user 的旧结构。
        """
        messages = self._template_manager.build_messages(
            template,
            source_lang=source_lang,
            target_lang=target_lang,
            body_text=body_text,
            count=count,
            context_block=source_context,
            glossary_lines=glossary_lines,
        )

        # 直接解析原始响应：不能先经过 _extract_translation，
        # 否则多行译文会被 normalize 成一行，窗口解析必然失败
        raw = self._translator._call_api(messages)
        return self._parse_window_result(raw, count)

    def _translate_single(self, text: str, source_lang: str, target_lang: str,
                           context: str, template_name: str, glossary_name: str,
                           video_style: str = "") -> str:
        """单条翻译（窗口兜底/并发模式使用），返回单行译文。"""
        if not text.strip():
            return ""

        glossary_lines = self._get_glossary_lines(glossary_name)
        template = self._get_template(template_name)

        messages = self._template_manager.build_messages(
            template,
            source_lang=source_lang,
            target_lang=target_lang,
            body_text=f"[1] {text}",
            count=1,
            context_block=context,
            glossary_lines=glossary_lines,
        )

        # 单条翻译天然是单行，可以走 _parse_window_result 做规整
        items = self._parse_window_result(self._translator._call_api(messages), 1)
        return items[0] if items else ""

    # 严格重译用的约束：只翻这一条，禁止一切"发挥"
    STRICT_CONSTRAINTS = (
        f"{HARD_PREFIX}只输出译文本身，不要输出原文、解释、序号、引号或任何标签",
        f"{HARD_PREFIX}译文必须是单独一行，绝对不能换行",
        f"{HARD_PREFIX}只翻译原文有的内容：不扩写、不加戏、不添加原文没有的语气词或描写",
        f"{NOTE_PREFIX}按字面最可能的意思译，不虚构细节",
    )

    def translate_strict(self, text: str, source_lang: str, target_lang: str) -> str:
        """单条严格重译，用于修复"没翻出来/扩写过头"的条目。"""
        from app.core.utils.language import language_display_name

        prompt = build_insttrans_prompt(
            genre="字幕",
            source_lang_text=language_display_name(source_lang),
            target_lang_text=language_display_name(target_lang) or target_lang,
            body_text=text,
            constraints=list(self.STRICT_CONSTRAINTS),
        )
        messages = [{"role": "user", "content": prompt}]
        raw = self._translator._call_api(messages)
        return normalize_translation_text(self._translator._extract_translation(raw))

    @staticmethod
    def _is_expanded(source: str, translation: str) -> bool:
        """粗判译文是否被明显扩写（比原文信息量大很多）。"""
        src_len = len(re.sub(r"\s+", "", source or ""))
        if src_len < 10 or not translation:
            return False
        tgt_len = cjk_char_count(translation) or len(re.sub(r"\s+", "", translation))
        return tgt_len > src_len * 1.5 + 8

    def repair_document(self, document: SubtitleDocument, source_lang: str, target_lang: str,
                        log_callback: Optional[Callable[[str, str], None]] = None,
                        max_attempts: int = 2) -> dict:
        """重译"空译文 / 仍是源语言 / 明显扩写"的条目。

        单步翻译偶发漏翻某条（直接把源语原文当译文输出）或自己加戏。
        这里对可疑条目单独做严格重译，保证最终字幕里不会出现
        "中文位置上是原文"或"一条字幕换好几行"。
        """
        stats = {"checked": len(document.subtitles), "untranslated": 0,
                 "expanded": 0, "repaired": 0, "failed": 0}

        for item in document.subtitles:
            if not item.text.strip():
                item.translation = ""
                continue

            # 纯符号/emoji 条目：不需要译文，也避免重复显示一行一样的 emoji
            if not has_letters(item.text):
                if item.translation and cjk_char_count(item.translation) == 0:
                    item.translation = ""
                continue

            item.translation = normalize_translation_text(item.translation or "")

            untranslated = looks_untranslated(item.text, item.translation, target_lang)
            expanded = (not untranslated) and self._is_expanded(item.text, item.translation)
            if not untranslated and not expanded:
                continue

            if untranslated:
                stats["untranslated"] += 1
            if expanded:
                stats["expanded"] += 1

            repaired = ""
            for _ in range(max(1, max_attempts)):
                try:
                    candidate = self.translate_strict(item.text, source_lang, target_lang)
                except Exception as exc:  # noqa: BLE001 - 单条失败不应中断整片
                    if log_callback:
                        log_callback(f"第 {item.index} 条重译失败: {exc}", "warning")
                    continue
                if candidate and not looks_untranslated(item.text, candidate, target_lang):
                    repaired = candidate
                    break

            if repaired:
                item.translation = repaired
                stats["repaired"] += 1
            elif untranslated:
                # 宁可先留空（只显示原文），也不要把源语言当成中文塞进字幕
                stats["failed"] += 1
                if log_callback:
                    log_callback(
                        f"第 {item.index} 条译文缺失且重译未成功: {item.text[:40]}",
                        "warning",
                    )

        return stats

    @staticmethod
    def _build_source_context(above_items: List[SubtitleItem],
                               below_items: List[SubtitleItem]) -> str:
        """压缩版上下文：上下文各一行（只供理解，不参与输出）。

        这里只返回正文行，标题由 prompt 层统一加，避免同一段说明出现两次。
        """
        lines: List[str] = []
        for item in above_items:
            lines.append(f"上| {item.text}")
        for item in below_items:
            lines.append(f"下| {item.text}")
        return "\n".join(lines)

    def _parse_window_result(self, result: str, count: int) -> List[str]:
        """解析一个窗口的译文并统一规范成单行。"""
        items = self._parse_window_result_raw(result, count)
        return [normalize_translation_text(t) for t in items]

    def _parse_window_result_raw(self, result: str, count: int) -> List[str]:
        items = self._try_parse_translation_tag(result, count)
        if items is not None:
            return items

        items = self._try_parse_numbered(result, count)
        if items is not None:
            return [strip_llm_tags(t) for t in items]

        items = self._try_parse_by_paragraph(result, count)
        if items is not None:
            return [strip_llm_tags(t) for t in items]

        lines = [strip_llm_tags(line) for line in result.split("\n") if line.strip()]
        if len(lines) == count:
            return lines

        if len(lines) > count:
            return self._merge_lines_to_count(lines, count)

        items = lines[:]
        while len(items) < count:
            items.append("")
        return items[:count]

    def _try_parse_numbered(self, result: str, count: int) -> Optional[List[str]]:
        patterns = [
            r"\[(\d+)\]\s*(.*?)(?=\s*\[\d+\]|$)",
            r"(?:^|\n)\s*(\d+)\.\s+(.*?)(?=\n\s*\d+\.|$)",
            r"(?:^|\n)\s*(\d+)\)\s+(.*?)(?=\n\s*\d+\)|$)",
            r"(?:^|\n)\s*(\d+)[：:]\s*(.*?)(?=\n\s*\d+[：:]|$)",
        ]
        for pattern in patterns:
            matches = re.findall(pattern, result, re.DOTALL)
            if matches:
                items: List[str] = []
                for _, text in sorted(matches, key=lambda m: int(m[0])):
                    cleaned = text.strip()
                    cleaned = re.sub(r"\s+", " ", cleaned)
                    items.append(cleaned)
                if len(items) == count:
                    return items
                if len(items) > count:
                    return self._merge_lines_to_count(items, count)
                padded = items[:]
                while len(padded) < count:
                    padded.append("")
                return padded[:count]
        return None

    def _try_parse_translation_tag(self, result: str, count: int) -> Optional[List[str]]:
        tag_match = _TRANSLATION_TAG_RE.search(result)
        if not tag_match:
            return None

        content = result[tag_match.end():]
        end_match = _TRANSLATION_TAG_RE.search(content)
        if end_match and end_match.start() > 0:
            content = content[:end_match.start()]
        content = content.strip()

        inner_numbered = self._try_parse_numbered(content, count)
        if inner_numbered is not None:
            return [strip_llm_tags(t) for t in inner_numbered]

        lines = [strip_llm_tags(line) for line in content.split("\n") if line.strip()]
        if len(lines) == count:
            return lines
        if len(lines) > count:
            return self._merge_lines_to_count(lines, count)
        padded = lines[:]
        while len(padded) < count:
            padded.append("")
        return padded[:count]

    def _try_parse_by_paragraph(self, result: str, count: int) -> Optional[List[str]]:
        paragraphs = re.split(r'\n\s*\n', result.strip())
        paragraphs = [p.strip() for p in paragraphs if p.strip()]
        if len(paragraphs) == count:
            return paragraphs
        if len(paragraphs) > count:
            return self._merge_lines_to_count(paragraphs, count)
        return None

    def _merge_lines_to_count(self, lines: List[str], count: int) -> List[str]:
        if count <= 0:
            return []
        if count >= len(lines):
            result = lines[:]
            while len(result) < count:
                result.append("")
            return result[:count]

        result: List[str] = [""] * count
        group_size = len(lines) / count
        for i, line in enumerate(lines):
            target_idx = min(int(i / group_size), count - 1)
            if result[target_idx]:
                result[target_idx] += " " + line
            else:
                result[target_idx] = line
        return result
