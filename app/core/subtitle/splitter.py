from __future__ import annotations

import re
from typing import Optional

import pysbd

from app.core.subtitle.models import SubtitleDocument, SubtitleItem, SubtitleWord, SubtitleMetadata
from app.core.utils.language import (
    DEFAULT_SEGMENTATION_LANGUAGE,
    is_cjk_language,
    resolve_segmentation_language,
)


class SemanticSplitter:
    MAX_MERGED_DURATION = 8.0
    MAX_GAP_FOR_MERGE = 3.0

    # 单条字幕的时长上限。ASR 的句子级 segment 有时会把一大段静音包进来，
    # 一条字幕停留十几秒甚至几十秒根本看不清是哪句在说话，因此在停顿处切开。
    MAX_ITEM_DURATION = 8.0
    HARD_MAX_DURATION = 10.0      # 超过该时长即使没有停顿也强行切分
    MIN_PAUSE_TO_SPLIT = 0.6      # 词间停顿达到该值才视为自然断点
    MIN_PART_DURATION = 0.8       # 切分后每段最短时长（允许很短的收尾句）
    MIN_PART_WORDS = 2            # 切分后每段最少词数
    _SENTENCE_END_CHARS = '.!?。！？…'
    _CLAUSE_END_CHARS = ',;:，；：、—'

    def __init__(self, language: str = "en"):
        # 原始语言标识（可能是 "auto" 或 "zh-CN"），保留用于合并策略判断
        self._raw_language = language or ""
        self._language = resolve_segmentation_language(self._raw_language)
        self._segmenter = self._make_segmenter(self._language)

    @property
    def language(self) -> str:
        """实际用于语义分段（pysbd）的 ISO 代码。"""
        return self._language

    @property
    def is_cjk(self) -> bool:
        return is_cjk_language(self._language) or is_cjk_language(self._raw_language)

    @staticmethod
    def _make_segmenter(language: str):
        try:
            return pysbd.Segmenter(language=language, clean=False)
        except ValueError:
            # 极端兜底：未知语言退回默认分段器，避免整条流水线中断
            return pysbd.Segmenter(language=DEFAULT_SEGMENTATION_LANGUAGE, clean=False)

    def _ensure_language(self, document: SubtitleDocument) -> None:
        """在拿到字幕文本后重新解析语言（"auto" 需要靠文本内容判断）。"""
        if not document.subtitles:
            return
        sample = " ".join(item.text for item in document.subtitles[:80] if item.text)
        resolved = resolve_segmentation_language(self._raw_language, sample)
        if resolved != self._language:
            self._language = resolved
            self._segmenter = self._make_segmenter(resolved)

    def split(self, document: SubtitleDocument) -> SubtitleDocument:
        self._ensure_language(document)

        if self._is_word_level(document):
            return self._split_from_word_level(document)

        new_items: list[SubtitleItem] = []
        item_index = 1

        for item in document.subtitles:
            text = item.text.strip()

            if not text:
                new_items.append(SubtitleItem(
                    index=item_index,
                    start=item.start,
                    end=item.end,
                    text=item.text,
                    words=list(item.words),
                    confidence=item.confidence,
                ))
                item_index += 1
                continue

            sentences = self._split_text(text)
            sentences = [s.strip() for s in sentences if s.strip()]

            if len(sentences) <= 1:
                new_items.append(SubtitleItem(
                    index=item_index,
                    start=item.start,
                    end=item.end,
                    text=(sentences[0].strip() if sentences else text),
                    words=list(item.words),
                    confidence=item.confidence,
                ))
                item_index += 1
                continue

            split_items = self._realign_timestamps(item, sentences)
            for si in split_items:
                si.index = item_index
                item_index += 1
            new_items.extend(split_items)

        new_metadata = SubtitleMetadata(
            source_file=document.metadata.source_file,
            source_lang=document.metadata.source_lang,
            target_lang=document.metadata.target_lang,
            asr_model=document.metadata.asr_model,
            source_step="02_split",
        )

        # 后处理：合并不完整句和短片段
        new_items = self._merge_incomplete_items(new_items)
        # 后处理：按停顿切分超长条目（一条字幕停留十几秒无法阅读）
        new_items = self._split_long_items(new_items)

        return SubtitleDocument(metadata=new_metadata, subtitles=new_items)

    def _is_word_level(self, document: SubtitleDocument) -> bool:
        if not document.subtitles:
            return False
        valid_items = [item for item in document.subtitles if item.text.strip()]
        if not valid_items:
            return False
        word_counts = sorted(len(item.text.split()) for item in valid_items)
        mid = len(word_counts) // 2
        median_words = (word_counts[mid] + word_counts[~mid]) / 2 if len(word_counts) % 2 == 0 else word_counts[mid]
        return median_words <= 2.0 and len(valid_items) > 10

    def _split_from_word_level(self, document: SubtitleDocument) -> SubtitleDocument:
        valid_items = [item for item in document.subtitles if item.text.strip()]

        if not valid_items:
            return SubtitleDocument(
                metadata=SubtitleMetadata(
                    source_file=document.metadata.source_file,
                    source_lang=document.metadata.source_lang,
                    target_lang=document.metadata.target_lang,
                    asr_model=document.metadata.asr_model,
                    source_step="02_split",
                ),
                subtitles=document.subtitles,
            )

        full_text = " ".join(item.text for item in valid_items)
        full_text = self._normalize_word_joins(full_text)
        sentences = self._split_text(full_text)
        sentences = [s.strip() for s in sentences if s.strip()]

        if not sentences:
            return SubtitleDocument(
                metadata=SubtitleMetadata(
                    source_file=document.metadata.source_file,
                    source_lang=document.metadata.source_lang,
                    target_lang=document.metadata.target_lang,
                    asr_model=document.metadata.asr_model,
                    source_step="02_split",
                ),
                subtitles=document.subtitles,
            )

        # 直接返回 list[list[SubtitleWord]]，支持跨句 item 拆分
        word_groups = self._assign_items_to_sentences(valid_items, sentences)

        new_items: list[SubtitleItem] = []
        item_index = 1

        for sentence, words in zip(sentences, word_groups):
            if not words:
                continue
            confidence = sum(w.probability for w in words) / len(words) if words else 0.0
            new_items.append(SubtitleItem(
                index=item_index,
                start=words[0].start,
                end=words[-1].end,
                text=sentence,
                words=words,
                confidence=confidence,
            ))
            item_index += 1

        new_items = self._merge_incomplete_items(new_items)
        new_items = self._split_long_items(new_items)

        return SubtitleDocument(
            metadata=SubtitleMetadata(
                source_file=document.metadata.source_file,
                source_lang=document.metadata.source_lang,
                target_lang=document.metadata.target_lang,
                asr_model=document.metadata.asr_model,
                source_step="02_split",
            ),
            subtitles=new_items,
        )

    def _assign_items_to_sentences(self, items: list[SubtitleItem], sentences: list[str]) -> list[list[SubtitleWord]]:
        """将词级 items 分配给句子，支持跨句 item 拆分。

        当一个 item 的文本跨越句子边界时（如 "Roundtable. I'm"），
        按字符比例拆分时间戳，分别分配给前后两句。
        """
        groups: list[list[SubtitleWord]] = []
        item_idx = 0
        # 残余：上一个 item 被拆分后，属于下一句的部分
        pending: Optional[SubtitleWord] = None

        for sentence in sentences:
            target = self._normalize_for_match(sentence)
            group_words: list[SubtitleWord] = []
            accumulated = ""

            # 先处理上一个 item 拆分后遗留的部分
            if pending is not None:
                p_norm = self._normalize_for_match(pending.word)
                candidate = accumulated + p_norm

                if target == candidate:
                    # 残余恰好完成当前句
                    group_words.append(pending)
                    pending = None
                    groups.append(group_words)
                    continue
                elif target.startswith(candidate):
                    # 残余属于当前句，但还需要更多 items
                    group_words.append(pending)
                    accumulated = candidate
                    pending = None
                elif candidate.startswith(target):
                    # 残余也跨越了当前句，需要再次拆分
                    remaining_chars = len(target) - len(accumulated)
                    split_pos = self._find_split_position(pending.word, remaining_chars)
                    if 0 < split_pos < len(pending.word):
                        left_text, right_text, split_time = self._split_word(pending, split_pos)
                        group_words.append(SubtitleWord(
                            word=left_text, start=pending.start, end=split_time,
                            probability=pending.probability,
                        ))
                        pending = SubtitleWord(
                            word=right_text, start=split_time, end=pending.end,
                            probability=pending.probability,
                        )
                    else:
                        group_words.append(pending)
                        pending = None
                    groups.append(group_words)
                    continue
                else:
                    # 残余不匹配当前句，保留给后续处理
                    pass

            # 处理 items
            while item_idx < len(items):
                item = items[item_idx]
                item_text_norm = self._normalize_for_match(item.text)
                candidate = accumulated + item_text_norm

                if target == candidate:
                    # 完全匹配
                    group_words.append(SubtitleWord(
                        word=item.text, start=item.start, end=item.end,
                        probability=item.confidence,
                    ))
                    item_idx += 1
                    break
                elif target.startswith(candidate):
                    # 当前 item 完全属于当前句，继续累积
                    group_words.append(SubtitleWord(
                        word=item.text, start=item.start, end=item.end,
                        probability=item.confidence,
                    ))
                    accumulated = candidate
                    item_idx += 1
                elif candidate.startswith(target):
                    # item 跨越句子边界，需要拆分
                    remaining_chars = len(target) - len(accumulated)
                    split_pos = self._find_split_position(item.text, remaining_chars)
                    if 0 < split_pos < len(item.text):
                        left_text, right_text, split_time = self._split_item(item, split_pos)
                        group_words.append(SubtitleWord(
                            word=left_text, start=item.start, end=split_time,
                            probability=item.confidence,
                        ))
                        pending = SubtitleWord(
                            word=right_text, start=split_time, end=item.end,
                            probability=item.confidence,
                        )
                    else:
                        group_words.append(SubtitleWord(
                            word=item.text, start=item.start, end=item.end,
                            probability=item.confidence,
                        ))
                    item_idx += 1
                    break
                else:
                    # 不匹配：ASR 文本与 pysbd 分段不一致
                    if not group_words:
                        # 强制分配，避免级联错位
                        group_words.append(SubtitleWord(
                            word=item.text, start=item.start, end=item.end,
                            probability=item.confidence,
                        ))
                        item_idx += 1
                    break

            groups.append(group_words)

        # 处理剩余 items
        while item_idx < len(items):
            if groups:
                groups[-1].append(SubtitleWord(
                    word=items[item_idx].text, start=items[item_idx].start,
                    end=items[item_idx].end, probability=items[item_idx].confidence,
                ))
            item_idx += 1

        # 处理残余
        if pending is not None and groups:
            groups[-1].append(pending)

        return groups

    @staticmethod
    def _find_split_position(item_text: str, remaining_chars: int) -> int:
        """在 item_text 中找到包含 remaining_chars 个非空白字符的分割位置。

        返回原始文本中的字符索引，分割点包含紧随的标点和空白。
        """
        if remaining_chars <= 0:
            return 0

        count = 0
        for i, ch in enumerate(item_text):
            if not ch.isspace():
                count += 1
                if count >= remaining_chars:
                    # 找到位置，包含紧随的标点（如句号、逗号）
                    pos = i + 1
                    while pos < len(item_text) and item_text[pos] in '.,;:!?':
                        pos += 1
                    # 包含后续空白（自然断词点）
                    while pos < len(item_text) and item_text[pos].isspace():
                        pos += 1
                    return pos

        return len(item_text)

    @staticmethod
    def _effective_char_count(text: str) -> int:
        """计算有效字符数（排除空白和标点），用于时间比例分配。"""
        return len(re.sub(r'[\s.,;:!?，。；：！？、…\-—\(\)\[\]()【】「」『』"\'"]', '', text))

    @staticmethod
    def _split_item(item: SubtitleItem, split_pos: int) -> tuple[str, str, float]:
        """将 SubtitleItem 在 split_pos 处拆分，返回 (left_text, right_text, split_time)。"""
        left_text = item.text[:split_pos].rstrip()
        right_text = item.text[split_pos:].lstrip()
        duration = item.end - item.start
        # 按有效字符数（排除空白和标点）比例分配时间戳
        left_count = SemanticSplitter._effective_char_count(left_text)
        total_count = SemanticSplitter._effective_char_count(item.text)
        left_ratio = left_count / max(total_count, 1)
        split_time = item.start + duration * left_ratio
        return left_text, right_text, split_time

    @staticmethod
    def _split_word(word: SubtitleWord, split_pos: int) -> tuple[str, str, float]:
        """将 SubtitleWord 在 split_pos 处拆分，返回 (left_text, right_text, split_time)。"""
        left_text = word.word[:split_pos].rstrip()
        right_text = word.word[split_pos:].lstrip()
        duration = word.end - word.start
        left_count = SemanticSplitter._effective_char_count(left_text)
        total_count = SemanticSplitter._effective_char_count(word.word)
        left_ratio = left_count / max(total_count, 1)
        split_time = word.start + duration * left_ratio
        return left_text, right_text, split_time

    _PROTECT_PATTERNS = [
        (re.compile(r'https?://\S+'), True),
        (re.compile(r'www\.\S+'), True),
        (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), True),
        (re.compile(r'(?<![a-zA-Z0-9/])(?:(?:[a-zA-Z][a-zA-Z0-9-]*|\d[a-zA-Z0-9-]+)\.)+[a-z]{2,}(?![a-zA-Z0-9])'), False),
        (re.compile(r'\b\d+\.\d+\b'), False),
    ]

    _TRAILING_PUNCT = '.,;:!?，。；：！？）)]}'

    def _protect_special(self, text: str) -> tuple[str, dict[str, str]]:
        placeholders = {}
        counter = [0]

        def make_replacer(strip_trailing):
            def replacer(match):
                original = match.group()
                if strip_trailing:
                    stripped = original.rstrip(self._TRAILING_PUNCT)
                    trailing = original[len(stripped):]
                else:
                    stripped = original
                    trailing = ''
                key = f'\x00P{counter[0]}\x00'
                placeholders[key] = stripped
                counter[0] += 1
                return key + trailing
            return replacer

        protected = text
        for pattern, strip_trailing in self._PROTECT_PATTERNS:
            protected = pattern.sub(make_replacer(strip_trailing), protected)

        return protected, placeholders

    def _restore_special(self, text: str, placeholders: dict[str, str]) -> str:
        for key, value in placeholders.items():
            text = text.replace(key, value)
        return text

    def _split_text(self, text: str) -> list[str]:
        protected, placeholders = self._protect_special(text)
        segments = self._segmenter.segment(protected)
        return [self._restore_special(s, placeholders) for s in segments]

    def _realign_timestamps(self, original_item: SubtitleItem, sentences: list[str]) -> list[SubtitleItem]:
        words = original_item.words

        if words:
            word_groups = self._assign_words_to_sentences(words, sentences)

            # 第一遍：为有 words 的 group 分配时间戳，空 group 标记为 None
            time_ranges: list[Optional[tuple[float, float]]] = []
            for group in word_groups:
                if group:
                    time_ranges.append((group[0].start, group[-1].end))
                else:
                    time_ranges.append(None)

            # 第二遍：为空 group 按字符比例从相邻非空 group 插值，避免多句共享原区间
            time_ranges = self._fill_empty_time_ranges(
                time_ranges, sentences, original_item.start, original_item.end,
            )

            result: list[SubtitleItem] = []
            for sentence, group, tr in zip(sentences, word_groups, time_ranges):
                start_t, end_t = tr if tr else (original_item.start, original_item.end)
                result.append(SubtitleItem(
                    index=0,
                    start=start_t,
                    end=end_t,
                    text=sentence,
                    words=group,
                    confidence=original_item.confidence,
                ))

            return result

        text_lengths = [self._effective_char_count(s) for s in sentences]
        time_ranges = self._proportional_time_split(original_item.start, original_item.end, text_lengths)

        return [
            SubtitleItem(
                index=0,
                start=tr[0],
                end=tr[1],
                text=sentence,
                words=[],
                confidence=original_item.confidence,
            )
            for sentence, tr in zip(sentences, time_ranges)
        ]

    @staticmethod
    def _fill_empty_time_ranges(
        time_ranges: list[Optional[tuple[float, float]]],
        sentences: list[str],
        item_start: float,
        item_end: float,
    ) -> list[Optional[tuple[float, float]]]:
        """为空时间戳的条目按字符比例从相邻非空时间戳插值。

        避免多条字幕共享原 item 的完整 [start, end] 区间（D3）。
        """
        n = len(time_ranges)
        if n == 0:
            return time_ranges

        i = 0
        while i < n:
            if time_ranges[i] is not None:
                i += 1
                continue
            # 找到连续空区段 [i, j)
            j = i
            while j < n and time_ranges[j] is None:
                j += 1
            # 左边界：前一个非空区间的 end，或 item_start
            seg_start = time_ranges[i - 1][1] if i > 0 and time_ranges[i - 1] is not None else item_start
            # 右边界：下一个非空区间的 start，或 item_end
            seg_end = time_ranges[j][0] if j < n and time_ranges[j] is not None else item_end
            if seg_end < seg_start:
                seg_end = seg_start
            # 按有效字符比例分配 [seg_start, seg_end] 给 [i, j)
            char_counts = [SemanticSplitter._effective_char_count(sentences[k]) for k in range(i, j)]
            total_chars = sum(char_counts)
            if total_chars > 0 and seg_end > seg_start:
                duration = seg_end - seg_start
                current = seg_start
                for k in range(i, j):
                    if k == j - 1:
                        time_ranges[k] = (current, seg_end)
                    else:
                        seg_duration = duration * (char_counts[k - i] / total_chars)
                        time_ranges[k] = (current, current + seg_duration)
                        current += seg_duration
            else:
                # 无字符或无时长，等分
                count = j - i
                equal_dur = (seg_end - seg_start) / count if count > 0 else 0.0
                for k in range(i, j):
                    time_ranges[k] = (seg_start + (k - i) * equal_dur, seg_start + (k - i + 1) * equal_dur)
            i = j
        return time_ranges

    def _assign_words_to_sentences(self, words: list[SubtitleWord], sentences: list[str]) -> list[list[SubtitleWord]]:
        groups: list[list[SubtitleWord]] = []
        word_idx = 0

        for sentence in sentences:
            target = self._normalize_for_match(sentence)
            group: list[SubtitleWord] = []
            accumulated = ""

            while word_idx < len(words):
                word_text = self._normalize_for_match(words[word_idx].word)
                candidate = accumulated + word_text

                if target == candidate:
                    group.append(words[word_idx])
                    word_idx += 1
                    break
                elif target.startswith(candidate):
                    group.append(words[word_idx])
                    accumulated = candidate
                    word_idx += 1
                else:
                    if not group:
                        group.append(words[word_idx])
                        word_idx += 1
                    break

            groups.append(group)

        while word_idx < len(words):
            if groups:
                groups[-1].append(words[word_idx])
            word_idx += 1

        return groups

    @staticmethod
    def _normalize_for_match(text: str) -> str:
        return re.sub(r'\s+', '', text)

    def _merge_incomplete_items(self, items: list[SubtitleItem]) -> list[SubtitleItem]:
        """后处理：合并不完整句和短片段。

        规则：
        1. 间隔超过 MAX_GAP_FOR_MERGE（3.0s）不合并（ASMR 静音段不跨段）
        2. 合并后时长超过 MAX_MERGED_DURATION（10.0s）不合并
        3. 不以句末标点结尾且下一条不以大写字母开头的条目，合并（CJK 仅凭标点）
        4. 时长 < 1s 且不以大写字母开头的条目，合并到前一条
        """
        if not items:
            return items

        is_cjk = self.is_cjk

        merged: list[SubtitleItem] = [items[0]]

        for item in items[1:]:
            prev = merged[-1]
            prev_text = prev.text.rstrip()
            curr_text = item.text.strip()

            if not curr_text:
                merged.append(item)
                continue

            # 间隔超过 3 秒不合并
            gap = item.start - prev.end
            if gap > self.MAX_GAP_FOR_MERGE:
                merged.append(item)
                continue

            # 合并后时长超过 10 秒不合并
            merged_duration = item.end - prev.start
            if merged_duration > self.MAX_MERGED_DURATION:
                merged.append(item)
                continue

            prev_ends_sentence = prev_text and prev_text[-1] in '.!?。！？'
            curr_starts_upper = curr_text[0].isupper() if curr_text else False

            should_merge = False
            if is_cjk:
                # CJK 无大小写概念，仅凭标点判断
                if not prev_ends_sentence:
                    should_merge = True
            else:
                if not prev_ends_sentence and not curr_starts_upper:
                    should_merge = True

            # 时长 < 1s 且不以大写字母开头的条目，合并到前一条
            # （CJK 无大小写概念，套用此规则会把所有短句合并成一整段，故跳过）
            item_duration = item.end - item.start
            if not is_cjk and item_duration < 1.0 and not curr_starts_upper:
                should_merge = True

            if should_merge:
                # 合并到前一条
                new_text = prev_text + " " + curr_text
                new_words = list(prev.words) + list(item.words)
                merged[-1] = SubtitleItem(
                    index=prev.index,
                    start=prev.start,
                    end=item.end,
                    text=new_text,
                    words=new_words,
                    confidence=prev.confidence,
                )
            else:
                merged.append(item)

        # 重新编号
        for i, item in enumerate(merged):
            merged[i] = SubtitleItem(
                index=i + 1,
                start=item.start,
                end=item.end,
                text=item.text,
                words=item.words,
                confidence=item.confidence,
            )

        return merged

    def _split_long_items(self, items: list[SubtitleItem]) -> list[SubtitleItem]:
        """按词间停顿切分时长过长的条目。

        ASR 的句子级 segment 会把静音算进条目区间（一条字幕停留十几秒甚至
        几十秒），阅读体验极差。这里用词级时间戳找内部真实停顿，在停顿处
        切成多条，让每条字幕都贴着实际发音区间显示。文本与 words 同步切分，
        保证后续 split2.0 仍能用词级时间戳精确对齐。
        """
        if not items:
            return items

        result: list[SubtitleItem] = []
        for item in items:
            result.extend(self._split_item_by_duration(item, depth=0))

        for i, item in enumerate(result):
            item.index = i + 1
        return result

    def _split_item_by_duration(self, item: SubtitleItem, depth: int) -> list[SubtitleItem]:
        duration = item.end - item.start
        if duration <= self.MAX_ITEM_DURATION or depth >= 6:
            return [item]

        words = item.words or []
        if len(words) < self.MIN_PART_WORDS * 2:
            return [item]

        split_idx = self._find_best_split_word_index(words, duration)
        if split_idx is None:
            return [item]

        offset = self._word_index_to_char_offset(item.text, words, split_idx)
        if offset is None:
            return [item]

        left_text = item.text[:offset].strip()
        right_text = item.text[offset:].strip()
        if not left_text or not right_text:
            return [item]

        left_words = words[:split_idx]
        right_words = words[split_idx:]
        gap_start = max(w.end for w in left_words) if left_words else item.start
        gap_end = right_words[0].start if right_words else item.end

        left_end = min(gap_start + 0.25, max(gap_end - 0.02, gap_start))
        left_end = max(left_end, item.start + 0.1)
        right_start = max(gap_end - 0.15, left_end + 0.02)

        left_item = SubtitleItem(
            index=item.index,
            start=item.start,
            end=left_end,
            text=left_text,
            words=left_words,
            confidence=item.confidence,
        )
        right_item = SubtitleItem(
            index=item.index,
            start=right_start,
            end=item.end,
            text=right_text,
            words=right_words,
            confidence=item.confidence,
        )

        return (
            self._split_item_by_duration(left_item, depth + 1)
            + self._split_item_by_duration(right_item, depth + 1)
        )

    def _find_best_split_word_index(self, words: list[SubtitleWord], duration: float) -> Optional[int]:
        """在词序列中找最佳切点，返回切点词的下标（在该词之前切开）。"""
        span_start = min(w.start for w in words)
        span_end = max(w.end for w in words)
        if span_end <= span_start:
            return None

        best_idx: Optional[int] = None
        best_score = float("-inf")

        for k in range(self.MIN_PART_WORDS, len(words) - self.MIN_PART_WORDS + 1):
            prev_word = words[k - 1]
            next_word = words[k]
            gap = next_word.start - prev_word.end
            left_dur = prev_word.end - span_start
            right_dur = span_end - next_word.start
            if left_dur < self.MIN_PART_DURATION or right_dur < self.MIN_PART_DURATION:
                continue
            if gap < 0:
                continue

            tail = prev_word.word.strip()[-1:] if prev_word.word.strip() else ""
            if tail and tail in self._SENTENCE_END_CHARS:
                punctuation_bonus = 1.2
            elif tail and tail in self._CLAUSE_END_CHARS:
                punctuation_bonus = 0.6
            else:
                punctuation_bonus = 0.0

            # 停顿越长越像换气点；两段时长越均衡越好
            score = min(gap, 2.0) + punctuation_bonus - abs(left_dur - right_dur) * 0.05
            if score > best_score:
                best_score = score
                best_idx = k

        if best_idx is None:
            return None

        gap = words[best_idx].start - words[best_idx - 1].end
        if gap >= self.MIN_PAUSE_TO_SPLIT or duration > self.HARD_MAX_DURATION:
            return best_idx
        return None

    def _word_index_to_char_offset(self, text: str, words: list[SubtitleWord], split_idx: int) -> Optional[int]:
        """把"在第 split_idx 个词之前切分"换算成 text 中的字符偏移量。"""
        left_norm = self._normalize_for_match("".join(w.word for w in words[:split_idx]))
        total_norm = self._normalize_for_match("".join(w.word for w in words))
        text_norm_len = len(self._normalize_for_match(text))
        left_len = len(left_norm)
        total_len = len(total_norm)
        if left_len <= 0 or total_len <= 0 or text_norm_len <= 0:
            return None

        tolerance = max(3, int(text_norm_len * 0.05))
        if abs(total_len - text_norm_len) <= tolerance:
            count = 0
            for i, ch in enumerate(text):
                if ch.isspace():
                    continue
                count += 1
                if count == left_len:
                    pos = i + 1
                    while pos < len(text) and text[pos] in '.,;:!?，。；：！？、…':
                        pos += 1
                    while pos < len(text) and text[pos].isspace():
                        pos += 1
                    return pos if 0 < pos < len(text) else None
            return None

        # 文本与词序列差异较大（ASR 改写了标点/断词），退化为按比例找最近的空白处
        ratio = left_len / total_len
        target = int(len(text) * ratio)
        pos = text.rfind(' ', 0, min(target + 1, len(text)))
        if pos <= 0:
            pos = text.find(' ', target)
        if pos <= 0 or pos >= len(text) - 1:
            return None
        return pos + 1

    @staticmethod
    def _normalize_word_joins(text: str) -> str:
        text = re.sub(r' \.', '.', text)
        while re.search(r'\b([A-Za-z])\. ([A-Za-z])\.', text):
            text = re.sub(r'\b([A-Za-z])\. ([A-Za-z])\.', r'\1.\2.', text)
        return text

    def _proportional_time_split(self, start: float, end: float, text_lengths: list[int]) -> list[tuple[float, float]]:
        if not text_lengths:
            return []

        total_length = sum(text_lengths)
        duration = end - start

        if total_length == 0:
            equal_duration = duration / len(text_lengths) if len(text_lengths) > 0 else 0.0
            return [
                (start + i * equal_duration, start + (i + 1) * equal_duration)
                for i in range(len(text_lengths))
            ]

        result: list[tuple[float, float]] = []
        current = start

        for i, length in enumerate(text_lengths):
            if i == len(text_lengths) - 1:
                result.append((current, end))
            else:
                segment_duration = duration * (length / total_length)
                result.append((current, current + segment_duration))
                current += segment_duration

        return result
