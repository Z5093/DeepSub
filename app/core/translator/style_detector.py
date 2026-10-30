from __future__ import annotations

import logging
import re
import time
from typing import Optional

from app.core.translator.prompt_template import VIDEO_STYLES

logger = logging.getLogger(__name__)

# 分类只要一个「编号 + key」，给 16 个 token 足够；本地小模型啰嗦时也不会无限生成
_STYLE_MAX_TOKENS = 16
# 分类是可选前置步骤，不能像翻译那样等几分钟：超时立刻退回规则结果
_STYLE_TIMEOUT_SECONDS = 120.0


# 视频类型判别特征（供 AI 提示词使用，与 STYLE_KEYWORDS 保持一致）
# 优化要点：RTF 结构 + 对比性描述 + 强格式约束，适配 4B 小模型
STYLE_DETECTION_PROMPT = (
    "【任务】判断字幕属于哪种视频类型。\n"
    "【输出】只输出「编号 key」，如「7 asmr」，不要任何其他内容。\n\n"
    "类型（注意区分相似类型）：\n"
    "1 movie   — 角色对白，有情感冲突，叙事性强（区别于 drama：电影更浓缩、冲突更外显）\n"
    "2 documentary — 旁白解说，信息密集，客观陈述（区别于 news：纪录片更深入、有完整叙事）\n"
    "3 anime   — 感叹号多，有技能/招式名，含日语敬语（区别于 gaming：动漫有角色对话，游戏侧重操作）\n"
    "4 variety — 对话轻松，笑点密集，语气词多（区别于 vlog：综艺多人互动，vlog 单人自述）\n"
    "5 lecture — 学术术语多，论证结构清晰（区别于 education：讲座更正式、更深奥）\n"
    "6 drama   — 日常对白，情感细腻，称呼语多（区别于 movie：电视剧节奏更慢、对话更生活化）\n"
    "7 asmr    — 轻语/拟声词/呼吸声为主，信息密度极低（区别于所有：几乎没有完整句子）\n"
    "8 vlog    — 博主对镜头说话，含自我介绍/日常分享（区别于 variety：vlog 单人、无互动）\n"
    "9 gaming  — 游戏术语密集，节奏快，情绪化（区别于 anime：游戏术语是技能/装备/操作，动漫是招式/变身）\n"
    "10 news   — 语气正式，含地名/机构名/数字/日期（区别于 documentary：新闻更短、更碎片化）\n"
    "11 education — 术语+通俗讲解混合，有逻辑连接词（区别于 lecture：科普更通俗、更短）\n"
    "0 other   — 以上都不符合\n\n"
    "【方法】先看关键词（拟声词/术语/称呼），再看语气和信息密度。\n\n"
    "字幕：\n{subtitle_text}\n\n"
    "类型："
)

STYLE_KEYWORDS = {
    "anime": [
        "ちゃん", "くん", "さん", "さま", "せんせい", "お兄",
        "attack", "skill", "power", "transform", "evolve",
        "魔法", "变身", "必杀", "战斗", "守护", "觉醒",
        "sensei", "senpai", "kawaii", "baka",
        " naruto", " goku", "kamehameha",
    ],
    "documentary": [
        "narrator", "narration", "according to", "research shows",
        "studies indicate", "data suggests", "scientists",
        "据研究", "研究表明", "数据显示", "科学家",
        "纪录片", "探索", "发现", "调查",
        "in this episode", "this film explores",
    ],
    "variety": [
        "hahaha", "lol", "omg", "wow", "laughing",
        "哈哈哈", "笑死", "天哪", "我的天", "太搞笑了",
        "嘉宾", "主持人", "观众", "掌声",
        "you guys", "let's go", "battle", "winner",
    ],
    "lecture": [
        "therefore", "however", "furthermore", "consequently",
        "hypothesis", "theorem", "equation", "analysis",
        "因此", "然而", "此外", "定理", "公式", "假设",
        "chapter", "section", "definition", "lemma",
        "as you can see", "let's consider", "we have",
    ],
    "movie": [
        "whispers", "voiceover", "fade in", "fade out",
        "旁白", "画外音", "独白",
        "he said", "she replied", "scene",
    ],
    "drama": [
        "honey", "darling", "sweetheart", "dear",
        "亲爱的", "老公", "老婆", "妈妈", "爸爸",
        "i love you", "don't leave me", "how could you",
    ],
    "asmr": [
        # 英文/通用
        "asmr", "whisper", "whispering", "tingle", "tingles",
        "ear", "ears", "massage", "massaging", "tapping", "taps",
        "scratching", "scratches", "brushing", "brushes",
        "breathing", "breath", "softly", "gentle", "relax",
        "sleep", "sleepy", "fall asleep", "close your eyes",
        "binaural", "ear to ear", "ear cleaning", "ear exam",
        # 俄语
        "шепот", "шептать", "шуршать", "ушко", "уши", "ушек",
        "массаж", "постукивание", "звук", "звуки", "расслаб", "спать", "сон",
        "дыхание", "тише", "тихо", "мягко", "нежно",
        # 中文/日文
        "轻语", "耳语", "助眠", "沙沙", "敲击", "摩擦", "酥酥",
        "ささやき", "耳かき", "リラックス",
    ],
    "vlog": [
        "hey guys", "hi guys", "welcome back", "welcome to my channel",
        "today i'm", "today we're", "let's", "let me show you",
        "follow me", "on my channel", "subscribe", "like and subscribe",
        "in this video", "so today", "what's up",
        "大家好", "今天给大家", "欢迎来到", "我的频道", "关注我", "点赞", "三连",
        "今天我们", "今天我要", "跟我一起",
    ],
    "gaming": [
        # 通用游戏术语
        "gg", "wp", "noob", "pwned", "let's go", "let's play",
        "level", "level up", "xp", "exp", "boss", "boss fight",
        "skill", "skills", "ability", "abilities", "combo", "combos",
        "item", "items", "loot", "inventory", "equip", "equipped",
        "health", "mana", "stamina", "damage", "dps", "tank", "healer",
        "spawn", "respawn", "lag", "glitch", "cheat", "hack",
        # 英文游戏圈
        "minecraft", "fortnite", "league", "valorant", "pubg",
        "headshot", "kill", "kills", "death", "deaths",
        # 中文游戏圈
        "技能", "装备", "属性", "角色", "怪物", "副本", "升级",
        "操作", "走位", "输出", "奶妈", "坦克", "boss战", "Boss战",
        "队友", "对手", "翻车", "秒杀", "爆发", "控制",
    ],
    "news": [
        # 英文新闻
        "breaking news", "this just in", "according to reports",
        "reported that", "reports say", "sources say",
        "correspondent", "reporter", "anchorman", "anchor",
        "today in history", "developing story", "live coverage",
        # 中文新闻
        "本台报道", "本台消息", "据本台", "记者", "报道", "消息称",
        "新华社", "路透社", "美联社", "法新社",
        "今天", "昨日", "据悉", "据了解", "有关方面",
        "新闻", "晚间新闻", "早间新闻",
    ],
    "education": [
        # 英文科普/教育
        "let's learn", "in this lesson", "in this video we'll",
        "today we're going to learn", "did you know",
        "for example", "for instance", "in other words",
        "this means that", "the reason is", "because of",
        "as we know", "as we can see", "notice that",
        # 中文科普/教育
        "同学们", "我们来看", "举个例子", "换句话说",
        "为什么", "原因是", "这是因为", "也就是说",
        "知识点", "考点", "例题", "练习", "思考",
        "首先", "其次", "然后", "最后", "总结一下",
        "科普", "知识", "原理", "机制", "过程",
    ],
}


# AI 返回编号 → style key 的映射
_STYLE_NUMBER_MAP = {
    "1": "movie", "2": "documentary", "3": "anime", "4": "variety",
    "5": "lecture", "6": "drama", "7": "asmr", "8": "vlog",
    "9": "gaming", "10": "news", "11": "education",
    "0": "other",
}


class StyleDetector:
    def __init__(self, translator=None):
        self._translator = translator

    def detect(self, subtitle_texts: list[str], use_ai: bool = True) -> str:
        if not subtitle_texts:
            return "default"

        sample = self._build_sample(subtitle_texts)
        rule_based = self._rule_based_detect(sample)

        # 规则置信度高且不要求用模型时直接采纳（节省一次 API 调用）
        if rule_based and rule_based[1] >= 3.0 and not use_ai:
            logger.info("视频风格: %s（规则，得分 %.1f）", rule_based[0], rule_based[1])
            return rule_based[0]
        rule_style = rule_based[0] if rule_based else None
        rule_score = rule_based[1] if rule_based else 0.0

        if use_ai and self._translator is not None:
            try:
                ai_result = self._ai_detect(sample)
                if ai_result and ai_result in VIDEO_STYLES:
                    logger.info("视频风格: %s（模型分类）", ai_result)
                    return ai_result
                # AI 返回 other 或无法解析：高置信规则兜底
                if rule_style and rule_score >= 2.0:
                    logger.info("视频风格: %s（模型无结论，规则兜底 %.1f）", rule_style, rule_score)
                    return rule_style
            except Exception as exc:  # noqa: BLE001 - 分类失败不能影响翻译主流程
                logger.warning("风格分类失败，回退规则: %s", exc)
                if rule_style and rule_score >= 2.0:
                    logger.info("视频风格: %s（规则兜底，得分 %.1f）", rule_style, rule_score)
                    return rule_style

        if rule_style:
            logger.info("视频风格: %s（规则，得分 %.1f）", rule_style, rule_score)
        return rule_style or "default"

    def _build_sample(self, texts: list[str], max_chars: int = 1500) -> str:
        """首/中/尾各取一段，避免长视频头部偏置。"""
        non_empty = [t for t in texts if t and t.strip()]
        if not non_empty:
            return ""

        per_part = max_chars // 3
        n = len(non_empty)

        def take_slice(start: int, end: int, budget: int) -> list[str]:
            parts: list[str] = []
            used = 0
            for i in range(start, end):
                t = non_empty[i]
                if used + len(t) > budget:
                    break
                parts.append(t)
                used += len(t)
            return parts

        # 三段：前 1/3、中 1/3、尾 1/3
        head_end = max(1, n // 3)
        mid_start = max(0, n // 3)
        mid_end = max(mid_start + 1, (2 * n) // 3)
        tail_start = max(0, (2 * n) // 3)

        head = take_slice(0, head_end, per_part)
        mid = take_slice(mid_start, mid_end, per_part)
        tail = take_slice(tail_start, n, per_part)

        return "\n".join(head + mid + tail)

    def _rule_based_detect(self, sample: str) -> Optional[tuple[str, float]]:
        """返回 (style, score)，无匹配时返回 None。"""
        sample_lower = sample.lower()
        scores: dict[str, float] = {}

        for style, keywords in STYLE_KEYWORDS.items():
            score = 0.0
            for kw in keywords:
                count = sample_lower.count(kw.lower())
                if count > 0:
                    score += count
            scores[style] = score

        if not scores or max(scores.values()) == 0:
            return None

        avg_len = sum(len(t.strip()) for t in sample.split("\n") if t.strip()) / max(len(sample.split("\n")), 1)

        if avg_len > 80:
            scores["lecture"] = scores.get("lecture", 0) + 2
            scores["documentary"] = scores.get("documentary", 0) + 1
            scores["news"] = scores.get("news", 0) + 1

        if avg_len < 20:
            scores["variety"] = scores.get("variety", 0) + 1
            scores["anime"] = scores.get("anime", 0) + 0.5
            scores["gaming"] = scores.get("gaming", 0) + 0.5

        question_count = sample.count("?") + sample.count("？")
        exclamation_count = sample.count("!") + sample.count("！")
        if exclamation_count > question_count * 2:
            scores["anime"] = scores.get("anime", 0) + 1.5
            scores["variety"] = scores.get("variety", 0) + 1
            scores["gaming"] = scores.get("gaming", 0) + 1

        if question_count > exclamation_count * 2:
            scores["documentary"] = scores.get("documentary", 0) + 1
            scores["education"] = scores.get("education", 0) + 0.5

        best_style = max(scores, key=scores.get)
        best_score = scores[best_style]
        if best_score < 1.0:
            return None

        return (best_style, best_score)

    def _ai_detect(self, sample: str) -> Optional[str]:
        prompt = STYLE_DETECTION_PROMPT.format(subtitle_text=sample)
        messages = [
            {"role": "system", "content": "You are a video content classifier. Output only the category number and key."},
            {"role": "user", "content": prompt},
        ]
        started = time.monotonic()
        # 限 max_tokens + 独立超时：分类只是可选前置步骤，
        # 不能让本地模型卡住时把整条翻译流水线一起拖死
        raw = self._translator._call_api(
            messages, max_tokens=_STYLE_MAX_TOKENS, timeout=_STYLE_TIMEOUT_SECONDS,
        )
        logger.info("风格分类耗时 %.1fs，原始输出: %r", time.monotonic() - started, (raw or "")[:60])
        return self._parse_style_result(raw)

    def _parse_style_result(self, response: str) -> Optional[str]:
        response = response.strip().lower()

        # 优先匹配 "7 asmr" 这种 "编号 + key" 格式
        for num, key in _STYLE_NUMBER_MAP.items():
            if re.search(rf"\b{num}\s+{key}\b", response):
                return key if key != "other" else "other"

        # 退而求其次：直接匹配 style key
        for style_key in VIDEO_STYLES:
            if style_key in response:
                return style_key
        if "other" in response:
            return "other"

        # 最后：提取首个数字
        for char in response:
            if char in _STYLE_NUMBER_MAP:
                return _STYLE_NUMBER_MAP[char]

        for style_name in VIDEO_STYLES.values():
            if style_name in response:
                for k, v in VIDEO_STYLES.items():
                    if v == style_name:
                        return k

        return None
