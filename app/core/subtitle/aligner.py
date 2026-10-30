from __future__ import annotations

import math
import re
import json
import logging
import unicodedata
from typing import Optional, Callable, List, Tuple

from app.core.subtitle.models import SubtitleDocument, SubtitleItem, SubtitleWord, SubtitleMetadata
from app.core.translator.prompt_template import FORMAT_SAFEGUARD_JSON
from app.core.translator.openai_compat import normalize_translation_text, strip_llm_tags


class AIAligner:
    MAX_CHARS_PER_LINE = 60
    MAX_CJK_CHARS_PER_LINE = 20   # 中文/日文一行放 20 字左右比较舒服
    MAX_TRANS_CHARS = 24      # 译文超过该长度也考虑切分（中文一行别太长）
    MAX_ITEM_DURATION = 8.0   # 切分后单条字幕的目标时长上限
    SHORT_FRAGMENT_DURATION = 0.35  # 低于该时长且不是完整句的碎片才并入前一条

    def __init__(self, translator=None):
        self._translator = translator

    def align(self, doc_01: SubtitleDocument, doc_02: SubtitleDocument,
              doc_03: SubtitleDocument, source_lang: str = "", target_lang: str = "",
              progress_callback: Optional[Callable[[int, int], None]] = None) -> SubtitleDocument:
        long_indices = self._find_long_subtitles(doc_02, doc_03)
        long_set = set(long_indices)
        total = len(doc_02.subtitles)
        word_entries = self._build_word_map(doc_01)
        segment_map = self._build_segment_map(doc_01)

        new_items: List[SubtitleItem] = []
        new_index = 1

        for i, item_02 in enumerate(doc_02.subtitles):
            item_03 = self._find_translation_item(doc_03, i, item_02.text)
            original_text = item_02.text
            translated_text = item_03.translation if item_03 else ""

            if i not in long_set:
                new_items.append(SubtitleItem(
                    index=new_index,
                    start=item_02.start,
                    end=item_02.end,
                    text=original_text,
                    translation=translated_text,
                    words=list(item_02.words),
                    confidence=item_02.confidence,
                ))
                new_index += 1
                if progress_callback:
                    progress_callback(i + 1, total)
                continue

            orig_has_comma = self._has_comma_separators(original_text)
            trans_has_comma = self._has_comma_separators(translated_text) if translated_text.strip() else True
            need_ai = not orig_has_comma or not trans_has_comma

            if need_ai and self._translator is not None and translated_text.strip():
                try:
                    split_orig, split_trans = self._ai_split_subtitle(
                        original_text, translated_text, source_lang, target_lang,
                    )
                except Exception:
                    split_orig = self._rule_based_split(original_text)
                    split_trans = self._rule_based_split(translated_text)
            else:
                split_orig = self._rule_based_split(original_text)
                split_trans = self._rule_based_split(translated_text) if translated_text.strip() else [""] * len(split_orig)

            n_orig = len(split_orig)
            n_trans = len(split_trans)

            # 段数不一致时重试AI拆分，仍失败则回退到规则拆分
            if n_trans > 0 and n_orig != n_trans and self._translator is not None:
                logging.getLogger(__name__).warning(
                    "AI拆分段数不一致: 原文%d段, 译文%d段, 尝试重新拆分", n_orig, n_trans,
                )
                try:
                    split_orig, split_trans = self._ai_split_subtitle(
                        original_text, translated_text, source_lang, target_lang,
                    )
                    n_orig = len(split_orig)
                    n_trans = len(split_trans)
                except Exception:
                    pass
                if n_trans > 0 and n_orig != n_trans:
                    split_orig = self._rule_based_split(original_text)
                    split_trans = self._rule_based_split(translated_text) if translated_text.strip() else [""] * len(split_orig)
                    n_orig = len(split_orig)
                    n_trans = len(split_trans)

            # 在最终拆分结果上计算时间戳（避免重试改变段数后时间戳失效）
            timestamps = self._compute_split_timestamps(
                split_orig, original_text, item_02, word_entries, segment_map,
            )

            aligned_pairs = self._align_split_parts(split_orig, split_trans)

            for k, (orig_part, trans_part, orig_start_idx, orig_end_idx) in enumerate(aligned_pairs):
                # 根据合并后的原始段索引范围合并时间戳
                # 避免合并段只获取第一个子段的时间戳（D1: 拆分段数不一致时时间戳丢失）
                if timestamps and orig_start_idx < len(timestamps):
                    start_t = timestamps[orig_start_idx][0]
                    end_idx = min(orig_end_idx, len(timestamps)) - 1
                    end_t = timestamps[end_idx][1] if end_idx >= 0 else timestamps[orig_start_idx][1]
                else:
                    start_t, end_t = timestamps[-1] if timestamps else (item_02.start, item_02.end)

                new_items.append(SubtitleItem(
                    index=new_index,
                    start=start_t,
                    end=end_t,
                    text=orig_part,
                    translation=trans_part,
                    words=[],
                    confidence=item_02.confidence,
                ))
                new_index += 1

            if progress_callback:
                progress_callback(i + 1, total)

        new_metadata = SubtitleMetadata(
            source_file=doc_02.metadata.source_file,
            source_lang=source_lang or doc_02.metadata.source_lang,
            target_lang=target_lang or doc_02.metadata.target_lang,
            asr_model=doc_02.metadata.asr_model,
            created_at=doc_02.metadata.created_at,
            source_step="04_align",
        )

        cleaned_items = self._postprocess_items(new_items)

        return SubtitleDocument(metadata=new_metadata, subtitles=cleaned_items)

    def _find_long_subtitles(self, doc_02: SubtitleDocument, doc_03: SubtitleDocument) -> list[int]:
        indices: list[int] = []
        for i, item_02 in enumerate(doc_02.subtitles):
            should_split = len(item_02.text) > self.MAX_CHARS_PER_LINE
            item_03 = self._find_translation_item(doc_03, i, item_02.text)
            if item_03 is not None and len(item_03.translation) > self.MAX_TRANS_CHARS:
                should_split = True
            if should_split:
                indices.append(i)
        return indices

    @staticmethod
    def _find_translation_item(doc_03: SubtitleDocument, index: int,
                               source_text: str) -> Optional[SubtitleItem]:
        """按索引取 03 的译文条目，并校验原文一致，避免错位串行。

        正常情况下 02 与 03 条目一一对应；一旦某一步过滤掉条目导致数量
        不一致，按索引取会整体错位（译文贴到别的句子上）。这里做一次文本
        校验，不一致时按文本内容回退匹配。
        """
        subtitles = doc_03.subtitles
        if not subtitles:
            return None

        if index < len(subtitles):
            candidate = subtitles[index]
            if candidate.text.strip() == source_text.strip():
                return candidate

        target = re.sub(r"\s+", "", source_text)
        if not target:
            return None
        for candidate in subtitles:
            if re.sub(r"\s+", "", candidate.text) == target:
                return candidate
        return None

    @staticmethod
    def _timestamps_in_range(timestamps: List[Tuple[float, float]], seg_start: float,
                             seg_end: float, tolerance: float = 2.0) -> bool:
        """验证所有时间戳在原始段范围内。

        优先级1/2可能匹配到01中错误的段落（如"that door?"匹配到后续段落），
        导致时间戳超出当前段的范围，此时应丢弃并回退到字符比例分配。
        """
        for ts_start, ts_end in timestamps:
            if ts_start < seg_start - tolerance or ts_end > seg_end + tolerance:
                return False
        return True

    def _compute_split_timestamps(
        self,
        split_orig: List[str],
        original_text: str,
        item_02: SubtitleItem,
        word_entries: list[tuple[str, list[SubtitleWord]]],
        segment_map: list[tuple[str, float, float]],
    ) -> List[Tuple[float, float]]:
        """计算拆分后的时间戳，按3个优先级尝试，并验证范围。"""
        # 优先级 1：01 段落级时间戳（文本匹配 01 的对应段落）
        timestamps = self._find_matching_segment_times(split_orig, segment_map)

        # 优先级 2：01 word 级时间戳
        if not timestamps:
            words_for_item = self._find_matching_words(original_text, word_entries, list(item_02.words))
            timestamps = self._realign_with_word_timestamps(split_orig, words_for_item)

        # 验证时间戳在原始段范围内（优先级1/2可能匹配到错误的01段落）
        if timestamps and not self._timestamps_in_range(timestamps, item_02.start, item_02.end):
            timestamps = []

        # 优先级 3：基于 02 的字符比例分配
        if not timestamps:
            total_duration = item_02.end - item_02.start
            text_lengths = [len(p.strip()) for p in split_orig]
            total_length = sum(text_lengths)
            if total_length > 0 and total_duration > 0:
                current = item_02.start
                timestamps = []
                for k, length in enumerate(text_lengths):
                    if k == len(text_lengths) - 1:
                        timestamps.append((current, item_02.end))
                    else:
                        seg_duration = total_duration * (length / total_length)
                        timestamps.append((current, current + seg_duration))
                        current += seg_duration
            else:
                n_parts = len(split_orig)
                for k in range(n_parts):
                    part_start = item_02.start + total_duration * k / n_parts
                    part_end = item_02.start + total_duration * (k + 1) / n_parts
                    timestamps.append((part_start, part_end))

        return timestamps

    def _postprocess_items(self, items: List[SubtitleItem]) -> List[SubtitleItem]:
        if not items:
            return items

        # 1. 修复零时长/倒序条目（原实现只替换了局部变量，等于没生效）
        for i, item in enumerate(items):
            if item.end <= item.start:
                items[i] = SubtitleItem(
                    index=item.index,
                    start=item.start,
                    end=item.start + 0.5,
                    text=item.text,
                    translation=item.translation,
                    words=item.words,
                    confidence=item.confidence,
                )

        # 2. 按时间全局排序（修复时间戳乱序）
        items.sort(key=lambda x: x.start)

        # 3. 修复时间重叠：优先裁剪前一条的 end（保留后一条的 start，
        #    避免把后一条整体往后推造成链式偏移）
        MIN_DURATION = 0.4
        GAP = 0.02
        for i in range(1, len(items)):
            prev = items[i - 1]
            curr = items[i]
            if curr.start >= prev.end:
                continue

            trimmed_end = curr.start - GAP
            if trimmed_end - prev.start >= MIN_DURATION:
                items[i - 1] = SubtitleItem(
                    index=prev.index,
                    start=prev.start,
                    end=trimmed_end,
                    text=prev.text,
                    translation=prev.translation,
                    words=prev.words,
                    confidence=prev.confidence,
                )
            elif curr.end - (prev.end + GAP) >= MIN_DURATION:
                items[i] = SubtitleItem(
                    index=curr.index,
                    start=prev.end + GAP,
                    end=curr.end,
                    text=curr.text,
                    translation=curr.translation,
                    words=curr.words,
                    confidence=curr.confidence,
                )
            else:
                # 两条都太短，无法靠裁剪解决：保持原时长，仅把 start 推到前一条之后
                items[i] = SubtitleItem(
                    index=curr.index,
                    start=prev.end + GAP,
                    end=max(curr.end, prev.end + GAP + MIN_DURATION),
                    text=curr.text,
                    translation=curr.translation,
                    words=curr.words,
                    confidence=curr.confidence,
                )

        # 4. 去重：基于文本相似度+时间重叠
        filtered: List[SubtitleItem] = []
        for item in items:
            if not filtered:
                filtered.append(item)
                continue

            prev = filtered[-1]

            # 完全相同的文本+翻译+时间接近 → 合并
            if (prev.text.strip() == item.text.strip()
                    and prev.translation.strip() == item.translation.strip()
                    and abs(prev.start - item.start) < 1.0):
                merged = SubtitleItem(
                    index=prev.index,
                    start=prev.start,
                    end=item.end,
                    text=prev.text,
                    translation=prev.translation,
                    words=prev.words,
                    confidence=prev.confidence,
                )
                filtered[-1] = merged
                continue

            # 时间重叠 + 文本相似度检测
            overlap_start = max(prev.start, item.start)
            overlap_end = min(prev.end, item.end)
            overlap_duration = max(0.0, overlap_end - overlap_start)
            prev_duration = prev.end - prev.start
            item_duration = item.end - item.start

            if prev_duration > 0 and item_duration > 0:
                overlap_ratio = overlap_duration / min(prev_duration, item_duration)

                if overlap_ratio > 0.3:
                    # 有显著时间重叠，检查文本相似度
                    similarity = self._text_similarity(prev.text, item.text)

                    if similarity > 0.3:
                        # 文本相似，视为重复，保留更完整的
                        if len(item.text) > len(prev.text):
                            merged = SubtitleItem(
                                index=prev.index,
                                start=prev.start,
                                end=max(prev.end, item.end),
                                text=item.text,
                                translation=item.translation or prev.translation,
                                words=item.words or prev.words,
                                confidence=max(prev.confidence, item.confidence),
                            )
                            filtered[-1] = merged
                        # 否则保留 prev（更早出现的）
                        continue

                    # 子串包含检测：一条是另一条的子串
                    prev_clean = re.sub(r'[^\w]', '', prev.text.lower())
                    item_clean = re.sub(r'[^\w]', '', item.text.lower())
                    if prev_clean and item_clean:
                        if prev_clean in item_clean or item_clean in prev_clean:
                            # 保留更完整的
                            if len(item.text) >= len(prev.text):
                                merged = SubtitleItem(
                                    index=prev.index,
                                    start=prev.start,
                                    end=max(prev.end, item.end),
                                    text=item.text,
                                    translation=item.translation or prev.translation,
                                    words=item.words or prev.words,
                                    confidence=max(prev.confidence, item.confidence),
                                )
                                filtered[-1] = merged
                            continue

            # 极短碎片合并：只有"不是完整句"的碎片才并入前一条。
            # 完整句（如 0.4s 的 "Как ты?"）必须保留，否则会丢条、且译文会被
            # 拼到上一条后面，造成原文/译文错位。
            if (item_duration < self.SHORT_FRAGMENT_DURATION
                    and prev_duration > 0
                    and not self._is_complete_sentence(item.text)
                    and item.end - prev.start <= self.MAX_ITEM_DURATION):
                merged = SubtitleItem(
                    index=prev.index,
                    start=prev.start,
                    end=item.end,
                    text=self._smart_join([prev.text.rstrip(), item.text.strip()]),
                    translation=self._join_translation(prev.translation, item.translation),
                    words=prev.words + item.words,
                    confidence=prev.confidence,
                )
                filtered[-1] = merged
                continue

            filtered.append(item)

        # 5. 重新编号
        for i, item in enumerate(filtered):
            filtered[i] = SubtitleItem(
                index=i + 1,
                start=item.start,
                end=item.end,
                text=item.text,
                translation=item.translation,
                words=item.words,
                confidence=item.confidence,
            )

        return filtered

    @staticmethod
    def _is_complete_sentence(text: str) -> bool:
        """粗略判断一段文本是否为完整句（以句末标点结尾）。"""
        stripped = (text or "").strip()
        if not stripped:
            return False
        return stripped[-1] in '.!?。！？…'

    @staticmethod
    def _join_translation(prev: str, curr: str) -> str:
        """合并两句译文：中文之间不加空格，其余按空格连接。"""
        from app.core.translator.openai_compat import is_cjk_like

        prev = (prev or "").strip()
        curr = (curr or "").strip()
        if not prev:
            return curr
        if not curr:
            return prev
        if is_cjk_like(prev[-1]) and is_cjk_like(curr[0]):
            return prev + curr
        return f"{prev} {curr}"

    @staticmethod
    def _text_similarity(text_a: str, text_b: str) -> float:
        """计算两段文本的词级 Jaccard 相似度。"""
        words_a = set(text_a.lower().split())
        words_b = set(text_b.lower().split())
        if not words_a and not words_b:
            return 1.0
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union)

    @staticmethod
    def _smart_join(parts: List[str]) -> str:
        """合并分段文本，仅在两个字母/数字字符之间补空格。

        避免单词边界丢失空格（如"behind"+"that"→"behindthat"，俄语、日语
        假名同理），同时不在 CJK 字符之间插入空格。
        """
        from app.core.translator.openai_compat import is_cjk_like

        if not parts:
            return ""
        result = parts[0]
        for part in parts[1:]:
            if not result or not part:
                result += part
                continue
            last_char = result[-1]
            first_char = part[0]
            if (last_char.isalnum() and first_char.isalnum()
                    and not is_cjk_like(last_char) and not is_cjk_like(first_char)):
                result += " " + part
            else:
                result += part
        return result

    def _align_split_parts(self, split_orig: List[str], split_trans: List[str]) -> List[Tuple[str, str, int, int]]:
        """对齐拆分后的原文和译文段。

        返回 [(orig_part, trans_part, orig_start_idx, orig_end_idx), ...]
        其中 orig_start_idx/orig_end_idx 为该合并段在 split_orig 中的索引范围 [start, end)，
        用于合并时间戳：合并段的 start 取首个子段 start，end 取末个子段 end。
        """
        n_orig = len(split_orig)
        n_trans = len(split_trans)

        if n_trans == 0:
            return [(part, "", k, k + 1) for k, part in enumerate(split_orig)]

        if n_orig == n_trans:
            return [(o, t, k, k + 1) for k, (o, t) in enumerate(zip(split_orig, split_trans))]

        if n_orig < n_trans:
            result: List[Tuple[str, str, int, int]] = []
            trans_idx = 0
            remaining = n_trans
            for k in range(n_orig):
                if k == n_orig - 1:
                    merged_trans = self._smart_join(split_trans[trans_idx:])
                    result.append((split_orig[k], merged_trans, k, k + 1))
                else:
                    remaining_orig = n_orig - k
                    trans_count = max(1, round(remaining / remaining_orig))
                    end_idx = min(trans_idx + trans_count, n_trans)
                    merged_trans = self._smart_join(split_trans[trans_idx:end_idx])
                    result.append((split_orig[k], merged_trans, k, k + 1))
                    remaining -= (end_idx - trans_idx)
                    trans_idx = end_idx
            return result

        # n_orig > n_trans：原文需要切成更多段，但译文段数不够。
        # 直接把原文合回去会让"超长字幕"白切（这也是之前一条字幕停留十几秒的
        # 原因之一），因此按原文各段的字数比例把译文重新切开，保持 1:1。
        redistributed = self._split_translation_by_ratios(split_trans, split_orig)
        if redistributed:
            return [(o, t, k, k + 1) for k, (o, t) in enumerate(zip(split_orig, redistributed))]

        result: List[Tuple[str, str, int, int]] = []
        orig_idx = 0
        remaining = n_orig
        for k in range(n_trans):
            if k == n_trans - 1:
                merged_orig = self._smart_join(split_orig[orig_idx:])
                result.append((merged_orig, split_trans[k], orig_idx, n_orig))
            else:
                remaining_trans = n_trans - k
                orig_count = max(1, round(remaining / remaining_trans))
                end_idx = min(orig_idx + orig_count, n_orig)
                merged_orig = self._smart_join(split_orig[orig_idx:end_idx])
                result.append((merged_orig, split_trans[k], orig_idx, end_idx))
                remaining -= (end_idx - orig_idx)
                orig_idx = end_idx
        return result

    _BREAK_CHARS = '，。！？、；：…,.;:!? '

    def _split_translation_by_ratios(self, split_trans: List[str], split_orig: List[str]) -> Optional[List[str]]:
        """把译文合并后按原文各段的字符比例重新切开，用于"原文段数多于译文"的场景。"""
        if not split_trans or len(split_orig) < 2:
            return None
        joined = self._smart_join(split_trans).strip()
        if not joined:
            return None

        weights = [max(len(p.strip()), 1) for p in split_orig]
        parts: List[str] = []
        remaining = joined
        remaining_weight = sum(weights)

        for idx, weight in enumerate(weights[:-1]):
            if remaining_weight <= 0 or not remaining:
                return None
            target = max(1, int(round(len(remaining) * (weight / remaining_weight))))
            cut = self._nearest_break_position(remaining, target)
            if cut is None or cut <= 0 or cut >= len(remaining):
                return None
            head = remaining[:cut].strip()
            tail = remaining[cut:].lstrip()
            if not head or not tail:
                return None
            parts.append(head)
            remaining = tail
            remaining_weight -= weight

        parts.append(remaining.strip())
        if any(not p for p in parts) or len(parts) != len(split_orig):
            return None
        return parts

    def _nearest_break_position(self, text: str, target: int) -> Optional[int]:
        """在 target 附近（±6 字符）找标点/空格作为切分点。"""
        span = 6
        lower = max(1, target - span)
        upper = min(len(text) - 1, target + span)
        best = None
        for pos in range(lower, upper + 1):
            if text[pos - 1] in self._BREAK_CHARS:
                best = pos
                if pos >= target:
                    break
        if best is not None:
            return best
        return target if 0 < target < len(text) else None

    def _ai_split_subtitle(self, original_text: str, translated_text: str,
                            source_lang: str, target_lang: str) -> Tuple[List[str], List[str]]:
        prompt = self._build_split_prompt(original_text, translated_text, source_lang, target_lang)
        messages = [
            {"role": "system", "content": (
                "你是字幕分割助手。严格按照指令操作，只输出JSON，不要输出任何其他内容。"
            )},
            {"role": "user", "content": prompt},
        ]
        raw = self._translator._call_api(messages)
        split_orig, split_trans = self._parse_split_result(raw, source_lang, target_lang)

        # Validate that joined parts reconstruct the original text (whitespace-insensitive)
        joined_orig = self._smart_join(split_orig)
        if re.sub(r'\s+', '', joined_orig) != re.sub(r'\s+', '', original_text):
            raise ValueError("Joined original parts do not match original text")
        if translated_text.strip():
            joined_trans = self._smart_join(split_trans)
            if re.sub(r'\s+', '', joined_trans) != re.sub(r'\s+', '', translated_text):
                raise ValueError("Joined translated parts do not match translated text")

        return split_orig, split_trans

    _BRACKET_CONTENT_RE = re.compile(
        r'\([^()]*\)'
        r'|（[^（）]*）'
        r'|\[[^\[\]]*\]'
        r'|【[^【】]*】'
        r'|《[^《》]*》'
        r'|「[^「」]*」'
        r'|『[^『』]*』'
        r'|"[^"]*"'
        r'|"[^"]*"'
        r"|'[^']*'"
        r"|'[^']*'"
    )

    _COMMA_SEPARATOR_RE = re.compile(r'[,，;；:：、]')

    def _has_comma_separators(self, text: str) -> bool:
        cleaned = self._BRACKET_CONTENT_RE.sub('', text)
        cleaned = cleaned.rstrip()
        cleaned = re.sub(r'[。！？.!?…\s]+$', '', cleaned)
        return bool(self._COMMA_SEPARATOR_RE.search(cleaned))

    _PROTECT_PATTERNS = [
        (re.compile(r'https?://\S+'), True),
        (re.compile(r'www\.\S+'), True),
        (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), True),
        (re.compile(r'(?<![a-zA-Z0-9/])(?:(?:[a-zA-Z][a-zA-Z0-9-]*|\d[a-zA-Z0-9-]+)\.)+[a-z]{2,}(?![a-zA-Z0-9])'), False),
        (re.compile(r'\b\d+\.\d+\b'), False),
        (re.compile(r'\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|Inc|Ltd|Corp|vs|etc|e\.g|i\.e)\.'), False),
        (re.compile(r'\b[A-Z]\.(?:[A-Z]\.)+'), False),
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

    @staticmethod
    def _line_char_limit(text: str) -> int:
        """按文本语种给出行长上限（CJK 一行容纳的字数明显少于拉丁语系）。"""
        if not text:
            return AIAligner.MAX_CHARS_PER_LINE
        cjk = sum(1 for ch in text if '\u3400' <= ch <= '\u9fff' or '\u3040' <= ch <= '\u30ff')
        if cjk / max(len(text), 1) > 0.3:
            return AIAligner.MAX_CJK_CHARS_PER_LINE
        return AIAligner.MAX_CHARS_PER_LINE

    def _rule_based_split(self, text: str) -> List[str]:
        limit = self._line_char_limit(text)
        if len(text) <= limit:
            return [text]

        # 目标段数确定后按平均长度合并，避免"第一段塞满 limit、最后一段只剩几个字"
        part_count = math.ceil(len(text) / limit)
        target = math.ceil(len(text) / part_count)

        protected, placeholders = self._protect_special(text)

        split_pattern = r'([,;，；。！？!?\.\s、—–]+)'
        segments = re.split(split_pattern, protected)

        merged: List[str] = []
        i = 0
        while i < len(segments):
            segment = segments[i]
            if not segment:
                i += 1
                continue
            if i + 1 < len(segments) and re.match(split_pattern, segments[i + 1]):
                segment += segments[i + 1]
                i += 2
            else:
                i += 1

            if merged and len(merged[-1]) + len(segment) <= target:
                merged[-1] += segment
            else:
                merged.append(segment)

        if not merged:
            return [text]

        # 收尾段太短（比如只剩一个词）时并回上一段，避免出现 1 秒的碎片字幕
        if len(merged) > 1:
            last = merged[-1]
            prev = merged[-2]
            if len(last.strip()) < target * 0.5 and len(prev) + len(last) <= limit:
                merged[-2] = prev + last
                merged.pop()

        if len(merged) == 1 and len(merged[0]) > limit:
            mid = len(merged[0]) // 2
            space_pos = merged[0].rfind(" ", 0, mid + len(merged[0]) // 4)
            if space_pos == -1:
                cjk_positions = [j for j, ch in enumerate(merged[0]) if '\u4e00' <= ch <= '\u9fff']
                if cjk_positions:
                    best = min(cjk_positions, key=lambda p: abs(p - mid))
                    parts = [merged[0][:best], merged[0][best:]]
                    return [self._restore_special(p, placeholders) for p in parts]
                space_pos = mid
            parts = [merged[0][:space_pos], merged[0][space_pos:]]
            return [self._restore_special(p, placeholders) for p in parts]

        return [self._restore_special(s, placeholders) for s in merged]

    def _realign_with_word_timestamps(self, original_parts: List[str],
                                       words: List[SubtitleWord]) -> List[Tuple[float, float]]:
        if not words or not original_parts:
            return []

        timestamps: List[Tuple[float, float]] = []
        word_idx = 0  # 全局消费指针，基于内容定位每个 part 对应的 word 范围

        for part in original_parts:
            part_norm = re.sub(r'\s+', '', part.strip())
            if not part_norm:
                continue

            consumed: List[SubtitleWord] = []
            accumulated = ""
            # 从当前 word_idx 开始累积，直到 accumulated 包含 part_norm
            while word_idx < len(words):
                w_norm = re.sub(r'\s+', '', words[word_idx].word)
                accumulated += w_norm
                consumed.append(words[word_idx])
                word_idx += 1
                if part_norm in accumulated or accumulated in part_norm:
                    # 累积文本已完全包含 part，完成匹配
                    if part_norm in accumulated:
                        break
                    # accumulated 是 part_norm 的一部分，若为前缀则继续消费
                    if not part_norm.startswith(accumulated):
                        break
                # accumulated 已超过 part_norm 长度仍未匹配，停止避免吞掉后续 words
                elif len(accumulated) > len(part_norm):
                    break

            if consumed:
                timestamps.append((consumed[0].start, consumed[-1].end))
            else:
                # 兜底：word 已耗尽，用前一个时间戳的 end
                last_end = timestamps[-1][1] if timestamps else words[0].end
                timestamps.append((last_end, last_end))

        return timestamps

    def _build_split_prompt(self, original_text: str, translated_text: str,
                             source_lang: str, target_lang: str) -> str:
        return (
            f"分析以下字幕并决定是否需要拆分。如果原文已是单一完整句子且长度不超过{self.MAX_CHARS_PER_LINE}字符，返回1段（不拆分）。\n\n"
            f"源语言: {source_lang}\n"
            f"目标语言: {target_lang}\n\n"
            f"原文({source_lang}):\n{original_text}\n\n"
            f"译文({target_lang}):\n{translated_text}\n\n"
            f"拆分规则：\n"
            f"1. 仅当原文包含多个独立句子，或单句长度超过{self.MAX_CHARS_PER_LINE}字符时才拆分；否则返回1段\n"
            f"2. 拆分点必须在语义自然断点：句末标点(.!?)之后，或逗号/分号后的从句边界\n"
            f"3. 严禁把标点单独拆成一段（如把\"?\"拆出来作为独立段落）\n"
            f"4. 严禁把词或词组从中间拆开（如\"behind that\"不能拆成\"behind\"+\"that\"）\n"
            f"5. 原文和译文的分段数必须相同（1:1对应）\n"
            f"6. 不要修改文本内容（包括空格），只做拆分；译文不得换行，每段就是一行\n"
            f"7. 原文所有分段按顺序拼接必须完全还原原文，译文同理\n"
            f"8. 各段长度尽量均衡，避免出现只有一个词的碎片段\n\n"
            f"示例1（无需拆分，返回1段）:\n"
            f"原文: Do you recognize who I am?\n"
            f"译文: 你可知我何人？\n"
            f'输出: {{"original_parts": ["Do you recognize who I am?"], "translated_parts": ["你可知我何人？"]}}\n\n'
            f"示例2（多句，需拆分）:\n"
            f"原文: There you are. I was getting bored.\n"
            f"译文: 你来了。正觉得无聊呢。\n"
            f'输出: {{"original_parts": ["There you are.", "I was getting bored."], "translated_parts": ["你来了。", "正觉得无聊呢。"]}}\n'
            f"{FORMAT_SAFEGUARD_JSON}\n\n"
            f"输出格式:\n"
            f'{{"original_parts": ["分段1", "分段2", ...], "translated_parts": ["译文1", "译文2", ...]}}'
        )

    def _parse_split_result(self, response: str, source_lang: str = "", target_lang: str = "") -> Tuple[List[str], List[str]]:
        json_str = self._extract_json(response)

        if json_str:
            try:
                data = json.loads(json_str)
                original_parts = data.get("original_parts", [])
                translated_parts = data.get("translated_parts", [])

                if isinstance(original_parts, list) and isinstance(translated_parts, list):
                    original_parts = [str(p) for p in original_parts if str(p).strip()]
                    translated_parts = [str(p) for p in translated_parts if str(p).strip()]
                    original_parts = [self._clean_split_part(p) for p in original_parts]
                    translated_parts = [normalize_translation_text(self._clean_split_part(p))
                                        for p in translated_parts]
                    original_parts = [p for p in original_parts if p]
                    translated_parts = [p for p in translated_parts if p]

                    if original_parts and self._validate_split_parts(original_parts, translated_parts, source_lang, target_lang):
                        return original_parts, translated_parts
            except json.JSONDecodeError:
                pass

        json_match = re.search(r'\{[^{}]*"original_parts"[^{}]*"translated_parts"[^{}]*\}', response, re.DOTALL)
        if not json_match:
            json_match = re.search(r'\{.*\}', response, re.DOTALL)

        if not json_match:
            raise ValueError("No JSON found in AI response")

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse JSON: {e}") from e

        original_parts = data.get("original_parts", [])
        translated_parts = data.get("translated_parts", [])

        if not isinstance(original_parts, list) or not isinstance(translated_parts, list):
            raise ValueError("original_parts and translated_parts must be lists")

        original_parts = [str(p) for p in original_parts if str(p).strip()]
        translated_parts = [str(p) for p in translated_parts if str(p).strip()]
        original_parts = [self._clean_split_part(p) for p in original_parts]
        translated_parts = [normalize_translation_text(self._clean_split_part(p))
                            for p in translated_parts]
        original_parts = [p for p in original_parts if p]
        translated_parts = [p for p in translated_parts if p]

        if not original_parts:
            raise ValueError("original_parts is empty after parsing")

        if not self._validate_split_parts(original_parts, translated_parts, source_lang, target_lang):
            raise ValueError("Split validation failed: original_parts contain target language text")

        return original_parts, translated_parts

    _GARBAGE_PATTERNS = re.compile(
        r'^(?:译文|翻译|translation|translated)\s*[\(（].*?[\)）]\s*[:：]?\s*$',
        re.IGNORECASE,
    )

    def _clean_split_part(self, text: str) -> str:
        text = text.strip()
        if self._GARBAGE_PATTERNS.match(text):
            return ""
        text = re.sub(r'^(?:译文|翻译|translation|translated)\s*[\(（].*?[\)）]\s*[:：]\s*', '', text, flags=re.IGNORECASE)
        text = strip_llm_tags(text)
        return text.strip()

    def _validate_split_parts(self, original_parts: List[str], translated_parts: List[str],
                               source_lang: str, target_lang: str) -> bool:
        if not original_parts:
            return False

        # Reject parts that contain no alphanumeric content (only punctuation/whitespace)
        for part in original_parts:
            if not any(ch.isalnum() for ch in part):
                return False
        for part in translated_parts:
            if not any(ch.isalnum() for ch in part):
                return False

        target_is_cjk = bool(target_lang and re.search(r'zh|ja|ko', target_lang, re.IGNORECASE))
        source_is_cjk = bool(source_lang and re.search(r'zh|ja|ko', source_lang, re.IGNORECASE))

        if target_is_cjk and not source_is_cjk:
            for part in original_parts:
                cjk_ratio = self._cjk_ratio(part)
                if cjk_ratio > 0.3:
                    return False

        if source_is_cjk and not target_is_cjk:
            for part in translated_parts:
                cjk_ratio = self._cjk_ratio(part)
                if cjk_ratio > 0.3:
                    return False

        return True

    @staticmethod
    def _cjk_ratio(text: str) -> float:
        if not text:
            return 0.0
        cjk_count = 0
        for ch in text:
            if unicodedata.category(ch) == 'Lo' or '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf':
                cjk_count += 1
        return cjk_count / len(text)

    def _extract_json(self, response: str) -> Optional[str]:
        code_block = re.search(r'```(?:json)?\s*\n?(.*?)```', response, re.DOTALL)
        if code_block:
            return code_block.group(1).strip()

        brace_start = response.find('{')
        brace_end = response.rfind('}')
        if brace_start >= 0 and brace_end > brace_start:
            return response[brace_start:brace_end + 1]

        return None

    @staticmethod
    def _normalize_for_segment_match(text: str) -> str:
        """归一化文本用于段落级匹配：去除空白和标点。"""
        return re.sub(r'[\s\.,;:!?，。；：！？、…\-—\(\)\[\]()【】「」『』"\'"]+', '', text)

    def _build_segment_map(self, doc_01: SubtitleDocument) -> list[tuple[str, float, float]]:
        """构建 01 的段落文本→(start, end) 映射，用于段落级时间戳匹配。"""
        entries: list[tuple[str, float, float]] = []
        for item in doc_01.subtitles:
            if item.text.strip():
                norm_text = self._normalize_for_segment_match(item.text)
                entries.append((norm_text, item.start, item.end))
        return entries

    def _find_matching_segment_times(
        self,
        split_orig: List[str],
        segment_map: List[Tuple[str, float, float]],
    ) -> List[Tuple[float, float]]:
        """对拆分后的每段文本，在 01 的段落映射中查找匹配，返回段落级时间戳。

        匹配策略：
        1. 精确子串匹配：split 段是某条 01 段落的子串 → 用该段落的 start/end
        2. 跨相邻段落拼接：split 段跨越多条 01 段落 → 用首段 start + 末段 end
        3. 匹配失败：返回空列表，交由上层回退
        """
        if not split_orig or not segment_map:
            return []

        timestamps: List[Tuple[float, float]] = []
        seg_idx = 0

        for part in split_orig:
            part_norm = self._normalize_for_segment_match(part)
            if not part_norm:
                continue

            matched = False
            # 策略1：单条精确子串匹配（part 是某条 01 段落的子串）
            for idx in range(seg_idx, len(segment_map)):
                norm_text, seg_start, seg_end = segment_map[idx]
                if not norm_text:
                    continue
                if part_norm in norm_text:
                    timestamps.append((seg_start, seg_end))
                    seg_idx = idx + 1
                    matched = True
                    break

            if matched:
                continue

            # 策略2：跨相邻段落拼接匹配（part 跨越多条 01 段落）
            if seg_idx < len(segment_map):
                accumulated = ""
                first_start = segment_map[seg_idx][1]
                last_end = segment_map[seg_idx][2]
                for idx in range(seg_idx, min(seg_idx + 5, len(segment_map))):
                    norm_text, seg_start, seg_end = segment_map[idx]
                    if not norm_text:
                        continue
                    accumulated += norm_text
                    last_end = seg_end
                    if part_norm in accumulated:
                        timestamps.append((first_start, last_end))
                        seg_idx = idx + 1
                        matched = True
                        break
                    if len(accumulated) > len(part_norm) * 1.5:
                        break

            if not matched:
                return []

        return timestamps

    def _build_word_map(self, doc_01: SubtitleDocument) -> list[tuple[str, list[SubtitleWord]]]:
        """构建基于文本内容的词级时间戳映射。

        不再使用索引映射（split1 会改变条目数导致索引错位），
        改为存储每条字幕的归一化文本和对应词列表，
        后续通过文本内容匹配找到正确的词级时间戳。
        """
        word_entries: list[tuple[str, list[SubtitleWord]]] = []
        for item in doc_01.subtitles:
            if item.words:
                norm_text = re.sub(r'\s+', '', item.text)
                word_entries.append((norm_text, list(item.words)))
        return word_entries

    def _find_matching_words(self, target_text: str,
                              word_entries: list[tuple[str, list[SubtitleWord]]],
                              fallback_words: list[SubtitleWord]) -> list[SubtitleWord]:
        """根据文本内容在 word_entries 中查找匹配的词级时间戳。

        匹配策略：
        1. 单条精确子串匹配：target_text 是某条字幕的子串（split1 拆分后的子条目）
        2. 跨相邻条目拼接匹配：target 跨越多条 ASR 条目（split1 合并后的长文本）
        3. 单条最长公共子串匹配（带覆盖率验证）：找到文本重叠最大的条目
        4. 回退：使用 fallback_words（split1 已正确合并的完整词集）
        """
        target_norm = re.sub(r'\s+', '', target_text)
        if not target_norm:
            return fallback_words

        best_match_idx = -1
        best_match_len = 0

        # 策略 1：单条精确子串匹配
        for idx, (norm_text, words) in enumerate(word_entries):
            if not norm_text or not words:
                continue

            # 精确子串匹配：target 是该条字幕的子串
            if target_norm in norm_text:
                # 从匹配的词列表中提取对应子集
                return self._extract_words_for_substring(target_norm, norm_text, words)

            # 计算最长公共子串长度
            common_len = self._longest_common_substring_len(target_norm, norm_text)
            if common_len > best_match_len:
                best_match_len = common_len
                best_match_idx = idx

        # 策略 2：跨相邻条目拼接匹配（split1 合并多条 ASR 条目的场景）
        cross_match = self._find_cross_entry_match(target_norm, word_entries)
        if cross_match:
            return cross_match

        # 策略 3：单条最长公共子串（带覆盖率验证）
        # 50% 公共子串阈值不能保证返回的 words 覆盖完整 target 文本，
        # 当 target 跨多条 ASR 条目时，需验证提取词集是否覆盖 target_norm，
        # 覆盖不完整则回退到 fallback_words（split1 已正确合并的词集）。
        if best_match_idx >= 0 and best_match_len >= len(target_norm) * 0.5:
            _, words = word_entries[best_match_idx]
            # 尝试从匹配的词列表中提取子集
            extracted = self._extract_words_for_substring(target_norm, word_entries[best_match_idx][0], words)
            if extracted:
                # 验证提取的 words 是否覆盖完整 target 文本
                extracted_norm = "".join(re.sub(r'\s+', '', w.word) for w in extracted)
                if target_norm in extracted_norm or extracted_norm == target_norm:
                    return extracted
            # 覆盖不完整，回退到 fallback_words
            return fallback_words

        return fallback_words

    def _find_cross_entry_match(self, target_norm: str,
                                 word_entries: list[tuple[str, list[SubtitleWord]]]) -> list[SubtitleWord]:
        """跨相邻 ASR 条目拼接查找 target 对应的 words。

        当 split1 合并了多条 ASR 条目时，target_norm 跨越 2+ 条 word_entries。
        本方法将相邻条目的 norm_text 拼接，查找 target_norm 在拼接文本中的位置，
        然后从对应的多条 entry 中提取 words。最多拼接 5 条相邻条目。
        """
        n = len(word_entries)
        for start_idx in range(n):
            norm_text, words = word_entries[start_idx]
            if not norm_text or not words:
                continue
            # target 从本条开始：校验前缀（取前 20 字符避免整串比较开销）
            if not target_norm.startswith(norm_text[:min(len(norm_text), 20)]):
                continue
            accumulated_norm = norm_text
            accumulated_words = list(words)
            if target_norm in accumulated_norm:
                return self._extract_words_for_substring(target_norm, accumulated_norm, accumulated_words)
            # 继续拼接后续条目
            for end_idx in range(start_idx + 1, min(start_idx + 5, n)):
                next_norm, next_words = word_entries[end_idx]
                if not next_norm or not next_words:
                    continue
                accumulated_norm += next_norm
                accumulated_words.extend(next_words)
                if target_norm in accumulated_norm:
                    return self._extract_words_for_substring(
                        target_norm, accumulated_norm, accumulated_words,
                    )
                # 拼接后已超过 target 长度仍未匹配，停止
                if len(accumulated_norm) > len(target_norm) * 1.5:
                    break
        return []

    @staticmethod
    def _extract_words_for_substring(target_norm: str, source_norm: str,
                                      words: list[SubtitleWord]) -> list[SubtitleWord]:
        """从词列表中提取与 target_norm 对应的子集。

        将所有词拼接后，找到 target_norm 在拼接文本中的位置，
        然后确定对应的词范围。
        """
        if target_norm == source_norm:
            return list(words)

        # 找到 target 在 source 中的起始位置
        start_pos = source_norm.find(target_norm)
        if start_pos == -1:
            # 尝试模糊匹配：从头开始匹配
            if source_norm.startswith(target_norm[:max(1, len(target_norm) // 2)]):
                start_pos = 0
            else:
                return list(words)

        end_pos = start_pos + len(target_norm)

        # 将每个词归一化，计算累积位置，找到对应的词范围
        cum_pos = 0
        start_word_idx = None
        end_word_idx = None

        for w_idx, w in enumerate(words):
            w_norm = re.sub(r'\s+', '', w.word)
            w_len = len(w_norm)

            if start_word_idx is None and cum_pos + w_len > start_pos:
                start_word_idx = w_idx

            if cum_pos + w_len >= end_pos:
                end_word_idx = w_idx + 1
                break

            cum_pos += w_len

        if start_word_idx is not None and end_word_idx is not None:
            return words[start_word_idx:end_word_idx]
        if start_word_idx is not None:
            return words[start_word_idx:]

        return list(words)

    @staticmethod
    def _longest_common_substring_len(s1: str, s2: str) -> int:
        """计算两个字符串的最长公共子串长度。使用后缀数组方法。"""
        if not s1 or not s2:
            return 0
        if len(s1) > len(s2):
            s1, s2 = s2, s1
        max_len = 0
        # 使用滑动窗口：从最长可能长度开始递减搜索
        for length in range(len(s1), 0, -1):
            if length <= max_len:
                break
            for start in range(len(s1) - length + 1):
                if s1[start:start + length] in s2:
                    return length
        return max_len
