from __future__ import annotations

import os
import json
import re
from typing import Dict, Optional, List

from app.common.paths import get_resource_path
from app.core.subtitle.converter import SubtitleConverter


class SubtitleStyleManager:

    DEFAULT_STYLE = {
        "font_name": "Source Han Sans SC",
        "font_name_en": "Arial",
        "font_size": 20,
        "bold": False,
        "italic": False,
        "underline": False,
        "primary_color": "#FFFFFF",
        "outline_color": "#000000",
        "shadow_color": "#000000",
        "back_color": "#000000",
        "back_opacity": 0,
        "outline_width": 2,
        "shadow_distance": 1,
        "shadow_angle": 45,
        "shadow_blur": 0,
        "alignment": 2,
        "margin_left": 10,
        "margin_right": 10,
        "margin_vertical": 30,
    }

    PRESET_STYLES = {
        "default": {},
        "cinema": {
            "font_name": "Source Han Sans SC",
            "font_size": 22,
            "outline_width": 3,
            "shadow_distance": 2,
            "alignment": 2,
        },
        "anime": {
            "font_name": "Source Han Sans SC",
            "font_size": 18,
            "primary_color": "#FFFF00",
            "outline_color": "#000080",
            "outline_width": 2,
            "alignment": 2,
        },
    }

    def __init__(self, config_dir: str = ""):
        self._config_dir = config_dir or get_resource_path("styles")
        self._styles: Dict[str, dict] = {}
        self._load()

    def _load(self):
        for name, overrides in self.PRESET_STYLES.items():
            merged = dict(self.DEFAULT_STYLE)
            merged.update(overrides)
            self._styles[name] = merged

        if os.path.isdir(self._config_dir):
            for filename in os.listdir(self._config_dir):
                if not filename.endswith(".json"):
                    continue
                name = filename[:-5]
                filepath = os.path.join(self._config_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        merged = dict(self.DEFAULT_STYLE)
                        merged.update(data)
                        self._styles[name] = merged
                except (json.JSONDecodeError, OSError):
                    continue

    def get_style(self, name: str = "default") -> dict:
        if name in self._styles:
            return dict(self._styles[name])
        return dict(self.DEFAULT_STYLE)

    def get_all_style_names(self) -> list[str]:
        return list(self._styles.keys())

    def save_style(self, name: str, style: dict):
        merged = dict(self.DEFAULT_STYLE)
        merged.update(style)
        self._styles[name] = merged

        os.makedirs(self._config_dir, exist_ok=True)
        filepath = os.path.join(self._config_dir, f"{name}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)

    def to_ass_style_line(self, name: str = "Default") -> str:
        style = self.get_style("default")
        return SubtitleStyleManager._style_dict_to_ass_line(name, style)

    @staticmethod
    def _style_dict_to_ass_line(name: str, style: dict) -> str:
        primary = SubtitleConverter._rgb_to_ass_color(style.get("primary_color", "#FFFFFF"))
        secondary = SubtitleConverter._rgb_to_ass_color(style.get("primary_color", "#FFFFFF"))
        outline = SubtitleConverter._rgb_to_ass_color(style.get("outline_color", "#000000"))
        back_opacity = style.get("back_opacity", 0)
        back_hex = style.get("back_color", "#000000").lstrip("#")
        if len(back_hex) == 6:
            alpha = format(int((1 - back_opacity / 100.0) * 255), "02X") if back_opacity < 100 else "00"
            back = f"&H{alpha}{back_hex[4:6]}{back_hex[2:4]}{back_hex[0:2]}"
        else:
            back = "&H00000000"

        bold = -1 if style.get("bold", False) else 0
        italic = -1 if style.get("italic", False) else 0
        underline = -1 if style.get("underline", False) else 0

        return (
            f"Style: {name},{style.get('font_name', 'Arial')},"
            f"{style.get('font_size', 20)},{primary},{secondary},{outline},{back},"
            f"{bold},{italic},{underline},0,"
            f"100,100,0,0,1,"
            f"{style.get('outline_width', 2)},{style.get('shadow_distance', 1)},"
            f"{style.get('alignment', 2)},"
            f"{style.get('margin_left', 10)},{style.get('margin_right', 10)},"
            f"{style.get('margin_vertical', 30)},1"
        )

    def import_ass_styles(self, ass_text: str) -> Optional[dict]:
        lines = ass_text.split("\n")
        in_styles = False
        format_fields: List[str] = []

        for line in lines:
            line_stripped = line.strip()

            if line_stripped.lower().startswith("[v4+ styles]"):
                in_styles = True
                format_fields = []
                continue

            if line_stripped.startswith("[") and line_stripped.endswith("]"):
                if in_styles:
                    in_styles = False
                continue

            if not in_styles:
                continue

            if line_stripped.lower().startswith("format:"):
                fmt = line_stripped[len("format:"):].strip()
                format_fields = [f.strip().lower() for f in fmt.split(",")]
                continue

            if line_stripped.lower().startswith("style:"):
                style_data = line_stripped[len("style:"):].strip()
                parts = [p.strip() for p in style_data.split(",")]

                if not format_fields:
                    format_fields = [
                        "name", "fontname", "fontsize", "primarycolour",
                        "secondarycolour", "outlinecolour", "backcolour",
                        "bold", "italic", "underline", "strikeout",
                        "scalex", "scaley", "spacing", "angle",
                        "borderstyle", "outline", "shadow", "alignment",
                        "marginl", "marginr", "marginv", "encoding",
                    ]

                field_map: Dict[str, str] = {}
                for idx, field_name in enumerate(format_fields):
                    if idx < len(parts):
                        field_map[field_name] = parts[idx]

                result = dict(self.DEFAULT_STYLE)

                fontname = field_map.get("fontname", "")
                if fontname:
                    result["font_name"] = fontname

                try:
                    result["font_size"] = int(float(field_map.get("fontsize", "20")))
                except (ValueError, TypeError):
                    pass

                bold_val = field_map.get("bold", "0")
                result["bold"] = bold_val in ("-1", "1", "True", "true")

                italic_val = field_map.get("italic", "0")
                result["italic"] = italic_val in ("-1", "1", "True", "true")

                underline_val = field_map.get("underline", "0")
                result["underline"] = underline_val in ("-1", "1", "True", "true")

                primary_colour = field_map.get("primarycolour", "")
                if primary_colour:
                    result["primary_color"] = self._ass_color_to_hex(primary_colour)

                outline_colour = field_map.get("outlinecolour", "")
                if outline_colour:
                    result["outline_color"] = self._ass_color_to_hex(outline_colour)

                shadow_colour = field_map.get("backcolour", "")
                if shadow_colour:
                    result["shadow_color"] = self._ass_color_to_hex(shadow_colour)

                try:
                    result["outline_width"] = int(float(field_map.get("outline", "2")))
                except (ValueError, TypeError):
                    pass

                try:
                    result["shadow_distance"] = int(float(field_map.get("shadow", "1")))
                except (ValueError, TypeError):
                    pass

                try:
                    result["alignment"] = int(field_map.get("alignment", "2"))
                except (ValueError, TypeError):
                    pass

                try:
                    result["margin_left"] = int(field_map.get("marginl", "10"))
                except (ValueError, TypeError):
                    pass

                try:
                    result["margin_right"] = int(field_map.get("marginr", "10"))
                except (ValueError, TypeError):
                    pass

                try:
                    result["margin_vertical"] = int(field_map.get("marginv", "30"))
                except (ValueError, TypeError):
                    pass

                return result

        return None

    @staticmethod
    def _ass_color_to_hex(ass_color: str) -> str:
        ass_color = ass_color.strip()
        if not ass_color.startswith("&H"):
            return "#FFFFFF"

        hex_part = ass_color[2:].upper()
        if len(hex_part) >= 8:
            hex_part = hex_part[2:8]
        elif len(hex_part) < 6:
            return "#FFFFFF"

        b = hex_part[0:2]
        g = hex_part[2:4]
        r = hex_part[4:6]
        return f"#{r}{g}{b}"
