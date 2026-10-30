from __future__ import annotations

import re
import os
from typing import Optional, List

from app.core.subtitle.models import SubtitleDocument, SubtitleItem, SubtitleWord, SubtitleMetadata
from app.common.constants import BILINGUAL_LAYOUT_STACKED, BILINGUAL_LAYOUT_SIDE_BY_SIDE
from app.core.translator.openai_compat import normalize_translation_text, strip_llm_tags


class SubtitleConverter:

    @staticmethod
    def to_srt(
        document: SubtitleDocument,
        include_translation: bool = False,
        bilingual_layout: str = BILINGUAL_LAYOUT_STACKED,
    ) -> str:
        lines: List[str] = []
        for i, item in enumerate(document.subtitles, 1):
            lines.append(str(i))
            start = SubtitleConverter._format_srt_time(item.start)
            end = SubtitleConverter._format_srt_time(item.end)
            lines.append(f"{start} --> {end}")
            text = strip_llm_tags(item.text)
            if include_translation and item.translation:
                # 译文强制单行：一条字幕下面不允许再折出好几行中文
                translation = normalize_translation_text(item.translation)
                if translation:
                    if bilingual_layout == BILINGUAL_LAYOUT_SIDE_BY_SIDE:
                        text = f"{text} | {translation}"
                    else:
                        text = f"{text}\n{translation}"
            lines.append(text)
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def to_ass(
        document: SubtitleDocument,
        style_config: dict = None,
        include_translation: bool = False,
        bilingual_layout: str = BILINGUAL_LAYOUT_STACKED,
    ) -> str:
        from app.core.subtitle.style import SubtitleStyleManager

        manager = SubtitleStyleManager()
        base_style = manager.get_style("default")
        if style_config:
            base_style.update(style_config)

        style_line = SubtitleStyleManager._style_dict_to_ass_line("Default", base_style)

        script_info = (
            "[Script Info]\n"
            "Title: DeepSub Export\n"
            "ScriptType: v4.00+\n"
            "WrapStyle: 0\n"
            "ScaledBorderAndShadow: yes\n"
            "YCbCr Matrix: None\n"
            "PlayResX: 1920\n"
            "PlayResY: 1080\n"
        )

        styles_section = (
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            + style_line
        )

        events_header = (
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
        )

        dialogue_lines: List[str] = []
        for item in document.subtitles:
            start = SubtitleConverter._format_ass_time(item.start)
            end = SubtitleConverter._format_ass_time(item.end)
            text = strip_llm_tags(item.text).replace("\n", "\\N")
            if include_translation and item.translation:
                translation = normalize_translation_text(item.translation).replace("\n", "\\N")
                if translation:
                    if bilingual_layout == BILINGUAL_LAYOUT_SIDE_BY_SIDE:
                        text = f"{text} | {translation}"
                    else:
                        text = f"{text}\\N{translation}"
            dialogue_lines.append(
                f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}"
            )

        events_section = events_header + "\n" + "\n".join(dialogue_lines)

        return script_info + "\n" + styles_section + "\n" + events_section + "\n"

    @staticmethod
    def to_vtt(
        document: SubtitleDocument,
        include_translation: bool = False,
        bilingual_layout: str = BILINGUAL_LAYOUT_STACKED,
    ) -> str:
        lines: List[str] = ["WEBVTT", ""]
        for item in document.subtitles:
            start = SubtitleConverter._format_vtt_time(item.start)
            end = SubtitleConverter._format_vtt_time(item.end)
            lines.append(f"{start} --> {end}")
            text = strip_llm_tags(item.text)
            if include_translation and item.translation:
                translation = normalize_translation_text(item.translation)
                if translation:
                    if bilingual_layout == BILINGUAL_LAYOUT_SIDE_BY_SIDE:
                        text = f"{text} | {translation}"
                    else:
                        text = f"{text}\n{translation}"
            lines.append(text)
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def from_srt(srt_text: str) -> SubtitleDocument:
        text = srt_text.strip()
        if text.startswith("\ufeff"):
            text = text[1:]

        blocks = re.split(r"\n\s*\n", text)
        subtitles: List[SubtitleItem] = []

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            lines = block.split("\n")
            time_line_idx = -1
            for idx, line in enumerate(lines):
                if "-->" in line:
                    time_line_idx = idx
                    break

            if time_line_idx < 0:
                continue

            time_parts = lines[time_line_idx].split("-->")
            if len(time_parts) != 2:
                continue

            try:
                start = SubtitleConverter._parse_srt_time(time_parts[0].strip())
                end = SubtitleConverter._parse_srt_time(time_parts[1].strip())
            except (ValueError, IndexError):
                continue

            text_lines = lines[time_line_idx + 1:]
            content = "\n".join(line.strip() for line in text_lines if line.strip())

            if not content:
                continue

            index = len(subtitles) + 1
            subtitles.append(SubtitleItem(
                index=index,
                start=start,
                end=end,
                text=content,
            ))

        metadata = SubtitleMetadata(
            source_file="",
            source_lang="",
            target_lang="",
            asr_model="",
            source_step="01_asr",
        )
        return SubtitleDocument(metadata=metadata, subtitles=subtitles)

    @staticmethod
    def from_vtt(vtt_text: str) -> SubtitleDocument:
        text = vtt_text.strip()
        if text.startswith("\ufeff"):
            text = text[1:]

        lines = text.split("\n")
        content_start = 0
        for i, line in enumerate(lines):
            if line.strip().startswith("WEBVTT"):
                content_start = i + 1
                break

        for i in range(content_start, len(lines)):
            if not lines[i].strip():
                content_start = i + 1
                break
            if "-->" in lines[i]:
                content_start = i
                break

        remaining = "\n".join(lines[content_start:])
        blocks = re.split(r"\n\s*\n", remaining.strip())
        subtitles: List[SubtitleItem] = []

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            block_lines = block.split("\n")
            time_line_idx = -1
            for idx, line in enumerate(block_lines):
                if "-->" in line:
                    time_line_idx = idx
                    break

            if time_line_idx < 0:
                continue

            time_parts = block_lines[time_line_idx].split("-->")
            if len(time_parts) != 2:
                continue

            try:
                start_str = time_parts[0].strip()
                end_str = time_parts[1].strip()
                end_str = re.sub(r"\s+.*$", "", end_str)
                start = SubtitleConverter._parse_srt_time(start_str)
                end = SubtitleConverter._parse_srt_time(end_str)
            except (ValueError, IndexError):
                continue

            text_lines = block_lines[time_line_idx + 1:]
            content = "\n".join(line.strip() for line in text_lines if line.strip())

            if not content:
                continue

            index = len(subtitles) + 1
            subtitles.append(SubtitleItem(
                index=index,
                start=start,
                end=end,
                text=content,
            ))

        metadata = SubtitleMetadata(
            source_file="",
            source_lang="",
            target_lang="",
            asr_model="",
            source_step="01_asr",
        )
        return SubtitleDocument(metadata=metadata, subtitles=subtitles)

    @staticmethod
    def from_ass(ass_text: str) -> SubtitleDocument:
        text = ass_text.strip()
        if text.startswith("\ufeff"):
            text = text[1:]

        lines = text.split("\n")
        in_events = False
        dialogue_format_fields: List[str] = []
        subtitles: List[SubtitleItem] = []

        for line in lines:
            line_stripped = line.strip()

            if line_stripped.lower().startswith("[events]"):
                in_events = True
                dialogue_format_fields = []
                continue

            if line_stripped.startswith("[") and line_stripped.endswith("]"):
                if in_events:
                    in_events = False
                continue

            if not in_events:
                continue

            if line_stripped.lower().startswith("format:"):
                format_str = line_stripped[len("format:"):].strip()
                dialogue_format_fields = [f.strip().lower() for f in format_str.split(",")]
                continue

            if line_stripped.lower().startswith("dialogue:"):
                dialogue_data = line_stripped[len("dialogue:"):].strip()
                parts = dialogue_data.split(",", len(dialogue_format_fields) - 1) if dialogue_format_fields else dialogue_data.split(",", 9)

                if dialogue_format_fields:
                    field_map = {}
                    for idx, field_name in enumerate(dialogue_format_fields):
                        if idx < len(parts):
                            field_map[field_name] = parts[idx].strip()
                else:
                    field_map = {}
                    default_fields = ["layer", "start", "end", "style", "name",
                                      "marginl", "marginr", "marginv", "effect", "text"]
                    for idx, field_name in enumerate(default_fields):
                        if idx < len(parts):
                            field_map[field_name] = parts[idx].strip()

                start_str = field_map.get("start", "0:00:00.00")
                end_str = field_map.get("end", "0:00:00.00")
                raw_text = field_map.get("text", "")

                try:
                    start = SubtitleConverter._parse_ass_time(start_str)
                    end = SubtitleConverter._parse_ass_time(end_str)
                except (ValueError, IndexError):
                    continue

                content = SubtitleConverter._strip_ass_tags(raw_text)
                content = content.replace("\\N", "\n").replace("\\n", "\n")
                content = content.strip()

                if not content:
                    continue

                index = len(subtitles) + 1
                subtitles.append(SubtitleItem(
                    index=index,
                    start=start,
                    end=end,
                    text=content,
                ))

        metadata = SubtitleMetadata(
            source_file="",
            source_lang="",
            target_lang="",
            asr_model="",
            source_step="01_asr",
        )
        return SubtitleDocument(metadata=metadata, subtitles=subtitles)

    @staticmethod
    def from_file(file_path: str) -> SubtitleDocument:
        ext = os.path.splitext(file_path)[1].lower()
        with open(file_path, "r", encoding="utf-8-sig") as f:
            content = f.read()

        if ext == ".srt":
            return SubtitleConverter.from_srt(content)
        elif ext == ".vtt":
            return SubtitleConverter.from_vtt(content)
        elif ext in (".ass", ".ssa"):
            return SubtitleConverter.from_ass(content)
        else:
            raise ValueError(f"Unsupported subtitle format: {ext}")

    @staticmethod
    def _format_srt_time(seconds: float) -> str:
        if seconds < 0:
            seconds = 0.0
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds - int(seconds)) * 1000))
        if ms >= 1000:
            ms = 999
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @staticmethod
    def _format_vtt_time(seconds: float) -> str:
        if seconds < 0:
            seconds = 0.0
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds - int(seconds)) * 1000))
        if ms >= 1000:
            ms = 999
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

    @staticmethod
    def _format_ass_time(seconds: float) -> str:
        if seconds < 0:
            seconds = 0.0
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        cs = int(round((seconds - int(seconds)) * 100))
        if cs >= 100:
            cs = 99
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    @staticmethod
    def _parse_srt_time(time_str: str) -> float:
        time_str = time_str.strip()
        time_str = time_str.replace(",", ".")
        match = re.match(
            r"(\d+):(\d+):(\d+)[\.](\d+)", time_str
        )
        if not match:
            raise ValueError(f"Invalid SRT timestamp: {time_str}")
        h = int(match.group(1))
        m = int(match.group(2))
        s = int(match.group(3))
        ms_str = match.group(4)
        ms_str = ms_str[:3].ljust(3, "0")
        ms = int(ms_str)
        return h * 3600 + m * 60 + s + ms / 1000.0

    @staticmethod
    def _parse_ass_time(time_str: str) -> float:
        time_str = time_str.strip()
        match = re.match(
            r"(\d+):(\d+):(\d+)[\.](\d+)", time_str
        )
        if not match:
            raise ValueError(f"Invalid ASS timestamp: {time_str}")
        h = int(match.group(1))
        m = int(match.group(2))
        s = int(match.group(3))
        cs_str = match.group(4)
        cs_str = cs_str[:2].ljust(2, "0")
        cs = int(cs_str)
        return h * 3600 + m * 60 + s + cs / 100.0

    @staticmethod
    def _rgb_to_ass_color(hex_color: str) -> str:
        hex_color = hex_color.lstrip("#")
        if len(hex_color) != 6:
            hex_color = "FFFFFF"
        r = hex_color[0:2]
        g = hex_color[2:4]
        b = hex_color[4:6]
        return f"&H00{b}{g}{r}"

    @staticmethod
    def _strip_ass_tags(text: str) -> str:
        return re.sub(r"\{[^}]*\}", "", text)
