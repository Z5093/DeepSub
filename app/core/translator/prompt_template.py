from __future__ import annotations


import os
import json

from typing import Dict, List, Optional

from app.core.utils.language import language_display_name


VIDEO_STYLES = {
    "movie": "电影",
    "documentary": "纪录片",
    "anime": "动漫",
    "variety": "综艺",
    "lecture": "讲座",
    "drama": "电视剧",
    "asmr": "ASMR助眠",
    "vlog": "视频博客",
    "gaming": "游戏解说",
    "news": "新闻播报",
    "education": "知识科普",
    "default": "默认",
}

# 每个风格只保留两样东西：填进头部的「文体名」+ 一句自然语言软约束。
# Index-Translate 的官方范式就是「请将以下{源语言}{文体}翻译成{目标语言}」+【注意】软约束，
# 不需要为每种风格重写一整套规则模板。
STYLE_STRINGS: Dict[str, dict] = {
    "default": {
        "genre": "视频字幕",
        "soft": [
            "译文准确达意、符合目标语言母语者的自然表达；口语对白口语化，书面内容规范化",
        ],
    },
    "movie": {
        "genre": "电影字幕",
        "soft": [
            "对白口语化、贴合角色性格与情绪，保留语气词和停顿感；旁白/独白用书面语",
            "俚语、双关和文化梗找目标语言的等价表达，无法等价时意译并保持原有语气",
        ],
    },
    "documentary": {
        "genre": "纪录片字幕",
        "soft": [
            "旁白用规范书面语、语气客观准确；采访与引用保留说话人的口语特征",
            "术语首次出现括注原文，地名/人名按目标语言通用译名；信息不删减不增补",
        ],
    },
    "anime": {
        "genre": "动漫字幕",
        "soft": [
            "对白体现角色个性：热血角色用短句，冷静角色用长句；招式名用四字格或短词组",
            "称呼语与敬语按目标语言习惯调整，特有表达用近似说法替代，保留原作趣味",
        ],
    },
    "variety": {
        "genre": "综艺字幕",
        "soft": [
            "语言活泼、保留嘉宾说话风格与幽默感；笑点优先保证观众能接住，保留节奏和时机",
            "网络用语、流行梗用目标语言的互联网等价表达；语气词和感叹词保留原味",
        ],
    },
    "lecture": {
        "genre": "讲座字幕",
        "soft": [
            "用规范学术语言、术语准确统一；逻辑连接词明确；不添加个人解读",
            "长句可适当拆分为短句，但保持论证逻辑完整，保留讲者的论述节奏",
        ],
    },
    "drama": {
        "genre": "电视剧字幕",
        "soft": [
            "对白自然口语化，不同角色说话方式有区分度（长辈/晚辈/同事语体不同）",
            "情感场景保留原文的含蓄或直白程度，社交礼仪用目标语言观众能理解的方式表达",
        ],
    },
    "asmr": {
        "genre": "ASMR助眠音频",
        "soft": [
            "氛围优先于语义：语言轻柔舒缓，拟声词/轻语保留或音译",
            "碎片化短句保留原貌，不必补全为完整句子；外语拟声词用目标语言观众熟悉的放松音替代",
        ],
    },
    "vlog": {
        "genre": "Vlog字幕",
        "soft": [
            "口语化、亲切自然，像博主在对镜头说话；保留口头禅和个人风格",
            "网络流行语与称呼语用目标语言互联网等价表达，不必逐字翻译",
        ],
    },
    "gaming": {
        "genre": "游戏解说字幕",
        "soft": [
            "游戏术语（技能/装备/角色名）用通用译名，无官方译名时保留原文",
            "翻译简洁有力、多用短句，保持玩家口语和夸张情绪，避免书面化",
        ],
    },
    "news": {
        "genre": "新闻字幕",
        "soft": [
            "规范书面语、语气客观正式，有新闻播报感；消除口语化和情绪化表达",
            "人名/地名/机构名按目标语言通用译名，数字与日期按新闻惯例表达，信息不增不减",
        ],
    },
    "education": {
        "genre": "知识科普字幕",
        "soft": [
            "规范书面语、概念通俗清晰，术语准确统一；不添加个人解读",
            "长句可适当拆分但保持逻辑完整，必要时用通俗表达解释概念",
        ],
    },
}

# 保留供外部引用/展示；实际提示词已改为「文体名 + 软约束」，不再拼进模板正文
STYLE_GUIDES = {
    "movie": "对白口语化贴角色性格；旁白书面语；人名首次括注；俚语找等价表达",
    "documentary": "旁白规范书面语；术语首现括注；度量衡按中文习惯",
    "anime": "对白体现角色个性；招式名四字格；称呼语本地化",
    "variety": "保留幽默感；网络梗中文等价；语气词保留",
    "lecture": "规范学术语言；术语统一；逻辑连接词明确",
    "drama": "对白自然口语化；角色语体区分；俚语等价",
    "asmr": "氛围优先；拟声词音译；碎片短句保留原貌；不加戏",
    "vlog": "口语化亲切；网络用语等价；保留博主风格",
    "gaming": "术语用通用译名；短句有力；保留感叹词",
    "news": "规范书面语客观正式；译名通用；数字按新闻惯例",
    "education": "规范书面语；术语准确统一；概念通俗清晰",
}

# ---------------------------------------------------------------------------
# instTrans 结构化提示词（对齐 Index-Translate 官方模板）
#
# 官方结构：
#   请将以下{源语言}{文体}翻译成{目标语言}，并且严格遵循所有约束要求。
#   【源文】
#   ...
#   【约束要求】
#   1. 【硬性要求】...
#   2. 【注意】...
#   只输出译文，不要有任何额外说明。
# ---------------------------------------------------------------------------

CONTEXT_HEADER = "【参考上文，只用于理解语境，不要翻译、不要输出】"
SOURCE_HEADER = "【源文】"
CONSTRAINT_HEADER = "【约束要求】"
PROMPT_TAIL = "只输出译文，不要有任何额外说明。"
HARD_PREFIX = "【硬性要求】"
NOTE_PREFIX = "【注意】"


def default_hard_constraints(count: int) -> List[str]:
    """窗口级硬约束：行数、行号、不许增删。"""
    n = max(1, int(count))
    marks = " ".join(f"[{i + 1}]" for i in range(n))
    return [
        f"{HARD_PREFIX}译文与源文条目一一对应，共 {n} 条，每条占一行，以 {marks} 开头",
        f"{HARD_PREFIX}不要合并、拆分或增删条目，不要输出源文",
        f"{HARD_PREFIX}只翻译原文内容，不添加原文没有的语气词、拟声词或描写",
    ]


def default_soft_constraints() -> List[str]:
    """全局软约束：ASR 噪音处理与术语一致性。"""
    return [
        f"{NOTE_PREFIX}原文疑似语音识别错误或残缺时，按字面最可能的意思译，不虚构原文没有的细节",
        f"{NOTE_PREFIX}同一短语反复出现时保持同一译法，不要换着说法译",
    ]


def build_insttrans_prompt(*, genre: str = "视频字幕", source_lang_text: str = "",
                           target_lang_text: str = "", body_text: str = "",
                           context_block: str = "",
                           constraints: Optional[List[str]] = None) -> str:
    """按官方 instTrans 槽位拼装单条 user 提示词。

    - 头部声明「源语言 + 文体 → 目标语言」
    - 【源文】块里只放待翻译内容（上下文放在它之前，避免被当成正文翻译）
    - 【约束要求】里每行自带【硬性要求】/【注意】前缀，由本函数统一编号
    """
    src = (source_lang_text or "").strip()
    tgt = (target_lang_text or "").strip() or "目标语言"
    genre = (genre or "视频字幕").strip()

    header = (
        f"请将以下{src}{genre}翻译成{tgt}，并且严格遵循所有约束要求。"
        if src else
        f"请将以下{genre}翻译成{tgt}，并且严格遵循所有约束要求。"
    )

    blocks: List[str] = [header]

    context = (context_block or "").strip()
    if context:
        blocks.append(f"{CONTEXT_HEADER}\n{context}")

    blocks.append(f"{SOURCE_HEADER}\n{(body_text or '').strip()}")

    items = [c.strip() for c in (constraints or []) if c and c.strip()]
    if items:
        numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(items))
        blocks.append(f"{CONSTRAINT_HEADER}\n{numbered}")

    blocks.append(PROMPT_TAIL)
    return "\n\n".join(blocks)


# 旧版输出格式约束：仅供「用户自定义模板（legacy）」使用，已去掉 <translation> 标签
def build_format_safeguard(item_count: int = 3) -> str:
    n = item_count if 1 <= item_count <= 8 else 3
    example = "\n".join(f"[{i + 1}] 第{i + 1}条译文" for i in range(n))
    return (
        "\n【输出格式】\n"
        "只输出译文，不要任何解释、原文、序号说明或Markdown。\n"
        "硬性要求：\n"
        "1. 原文有几条就输出几行，行号与顺序一一对应（多一行或少一行都会让字幕错位）\n"
        "2. 每条译文写在同一行，禁止换行、合并、拆分\n"
        "3. 只翻译原文内容，不添加语气词、拟声词或修饰，宁短勿长\n"
        "4. 每条都必须译成目标语言，禁止照抄原文\n"
        "5. 每行以 [1] [2] … 这样的编号开头\n"
        f"输出示例（{n}条原文→{n}行译文）：\n"
        + example
    )


FORMAT_SAFEGUARD = build_format_safeguard(3)

FORMAT_SAFEGUARD_JSON = (
    "\n【输出格式】\n"
    "只输出纯JSON，不要用```json```包裹，不要添加任何解释。\n"
    "正确示例：{\"original_parts\": [\"part1\"], \"translated_parts\": [\"part1\"]}"
)
from app.common.paths import get_config_dir


class PromptTemplateManager:
    """模板管理：内置风格走 instTrans 结构，用户自定义 JSON 走 legacy 结构。

    - 内置风格模板：{"format": "insttrans", "genre": "电影字幕", "soft": [...]}
    - 用户自定义模板：{"format": "legacy", "main": "含 {source_lang} 等占位符的模板"}
    兼容旧版 JSON（含 step1_accuracy 的三步结构），取其值作为 legacy main。
    """

    def __init__(self, config_dir: str = ""):
        self._config_dir = config_dir or os.path.join(get_config_dir(), "prompt_templates")
        self._templates: Dict[str, dict] = {}
        self._load()

    def _load(self):
        self._templates = {
            name: {
                "format": "insttrans",
                "genre": spec.get("genre", "视频字幕"),
                "soft": list(spec.get("soft", [])),
            }
            for name, spec in STYLE_STRINGS.items()
        }

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
                if isinstance(data, dict) and "main" in data and isinstance(data.get("main"), str):
                    self._templates[name] = {"format": "legacy", "main": data["main"]}
                elif isinstance(data, dict) and "step1_accuracy" in data:
                    # 旧三步格式：取第一步模板作为单步 main（用户旧自定义模板的兼容迁移）
                    self._templates[name] = {"format": "legacy", "main": data["step1_accuracy"]}
            except (json.JSONDecodeError, OSError):
                continue

    def get_template(self, name: str) -> Optional[dict]:
        template = self._templates.get(name)
        if template is None:
            return None
        copied = dict(template)
        if isinstance(copied.get("soft"), list):
            copied["soft"] = list(copied["soft"])
        return copied

    def get_all_template_names(self) -> List[str]:
        return list(self._templates.keys())

    def save_template(self, name: str, template: dict):
        if template.get("format") == "insttrans":
            # 内置风格不落盘，避免用户自定义覆盖内置语义
            return
        self._templates[name] = {"format": "legacy", "main": template.get("main", "")}
        os.makedirs(self._config_dir, exist_ok=True)
        filepath = os.path.join(self._config_dir, f"{name}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump({"main": template.get("main", "")}, f, ensure_ascii=False, indent=2)

    def get_style_guide(self, video_style: str) -> str:
        """仅供展示/兼容；提示词已改为「文体名 + 软约束」。"""
        return STYLE_GUIDES.get(video_style, "")

    def build_messages(self, template: dict, *, source_lang: str = "", target_lang: str = "",
                       body_text: str = "", count: int = 1, context_block: str = "",
                       glossary_lines: Optional[List[str]] = None) -> List[dict]:
        """按模板生成 chat messages。

        - instTrans 模板：单条 user 消息（官方示例里没有 system 角色）
        - legacy 模板：system 放模板正文，user 放原文，保持旧行为
        """
        glossary_lines = [g for g in (glossary_lines or []) if g]
        count = max(1, int(count))

        if template.get("format") == "legacy":
            prompt = self.format_prompt(
                template.get("main", ""),
                source_lang=source_lang,
                target_lang=target_lang,
                glossary_section="\n".join(glossary_lines),
                format_safeguard=build_format_safeguard(count),
                source_text=body_text,
            )
            user_parts: List[str] = []
            if context_block.strip():
                user_parts.append(f"{CONTEXT_HEADER}\n{context_block.strip()}")
            user_parts.append(body_text)
            return [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "\n\n".join(user_parts)},
            ]

        constraints = (
            default_hard_constraints(count)
            + glossary_lines
            + default_soft_constraints()
            + [f"{NOTE_PREFIX}{s}" for s in template.get("soft", []) if s]
        )
        prompt = build_insttrans_prompt(
            genre=template.get("genre", "视频字幕"),
            source_lang_text=language_display_name(source_lang),
            target_lang_text=language_display_name(target_lang) or (target_lang or "目标语言"),
            body_text=body_text,
            context_block=context_block,
            constraints=constraints,
        )
        return [{"role": "user", "content": prompt}]

    def format_prompt(self, template_str: str, source_lang: str = "", target_lang: str = "",
                      glossary_section: str = "", format_safeguard: str = "",
                      source_text: str = "", context: str = "",
                      previous_result: str = "", video_style: str = "",
                      style_guide: str = "") -> str:
        """填充 legacy 模板。

        占位符同时支持语言代码和语言名：`{source_lang}` 给中文名（如「俄语」），
        `{source_lang_code}` 给原始代码（如「ru-RU」），避免自定义模板里出现 "ru-RU字幕"。
        """
        if not format_safeguard:
            item_count = len([line for line in (source_text or "").split("\n") if line.strip()])
            format_safeguard = build_format_safeguard(item_count)

        src_name = language_display_name(source_lang) or source_lang
        tgt_name = language_display_name(target_lang) or target_lang

        try:
            return template_str.format(
                source_lang=src_name,
                target_lang=tgt_name,
                source_lang_code=source_lang,
                target_lang_code=target_lang,
                source_text=source_text,
                glossary_section=glossary_section,
                format_safeguard=format_safeguard,
                context=context,
                previous_result=previous_result,
                style_guide=style_guide,
            )
        except (KeyError, IndexError, ValueError):
            # 自定义模板里出现未知占位符时不要让整条流程崩掉
            return template_str
