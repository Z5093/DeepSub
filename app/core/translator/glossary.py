from __future__ import annotations


import os
import json

from typing import Dict, Optional
from app.common.paths import get_config_dir


class GlossaryManager:
    def __init__(self, config_dir: str = ""):
        self._config_dir = config_dir or os.path.join(get_config_dir(), "glossaries")
        self._glossaries: Dict[str, dict] = {}
        self._load()

    def _load(self):
        if not os.path.isdir(self._config_dir):
            return

        for filename in os.listdir(self._config_dir):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(self._config_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                name = os.path.splitext(filename)[0]
                if isinstance(data, dict):
                    self._glossaries[name] = data
            except (json.JSONDecodeError, OSError):
                continue

    def get_glossary(self, name: str) -> Optional[dict]:
        return self._glossaries.get(name)

    def get_all_glossary_names(self) -> list[str]:
        return list(self._glossaries.keys())

    def format_glossary_section(self, glossary_name: str) -> str:
        """旧格式（英文段落），仅供 legacy 模板使用。"""
        glossary = self.get_glossary(glossary_name)
        if not glossary:
            return ""

        lines = ["Glossary (use these translations consistently):"]
        for source_term, target_term in glossary.items():
            lines.append(f"- {source_term} → {target_term}")
        return "\n".join(lines) + "\n"

    def build_constraint_lines(self, glossary_name: str) -> list[str]:
        """Index-Translate 官方术语约束：`【硬性要求】专名/术语对照: A→B、C→D`。

        与旧的英文段落相比，这条是官方 instTrans 的硬约束槽位（训练时做了硬门控），
        术语遵循度显著更高。等值映射（A→A）还能当免译词锁定用。
        """
        glossary = self.get_glossary(glossary_name)
        if not glossary:
            return []

        pairs = "、".join(
            f"{str(k).strip()}→{str(v).strip()}"
            for k, v in glossary.items()
            if str(k).strip()
        )
        if not pairs:
            return []
        return [f"【硬性要求】专名/术语对照: {pairs}"]

    def save_glossary(self, name: str, entries: dict):
        self._glossaries[name] = dict(entries)
        os.makedirs(self._config_dir, exist_ok=True)
        filepath = os.path.join(self._config_dir, f"{name}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
