"""离线回归测试：不依赖模型，验证滑动窗口翻译链路与提示词结构。

覆盖点：
  1. 5 条字幕按窗口 2 → 恰好 3 次调用，无漏翻
  2. 编号在每个窗口内从 [1] 重新开始
  3. 上下文只进 prompt、不进结果；首窗只有「下|」，中间窗上下各一条
  4. 提示词是单条 user 消息（Index-Translate 官方结构），且包含【源文】/【约束要求】
  5. 严格重译单独走一条精简约束

运行：
    python tests/test_translation_flow.py
"""
import os
import sys

BASE = (
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if "__file__" in globals() else os.getcwd()
)
sys.path.insert(0, BASE)

from app.core.subtitle.models import (  # noqa: E402
    SubtitleDocument, SubtitleItem, SubtitleMetadata,
)
from app.core.translator.glossary import GlossaryManager  # noqa: E402
from app.core.translator.prompt_template import PromptTemplateManager  # noqa: E402
from app.core.translator.sliding_window import SlidingWindowTranslator  # noqa: E402


class FakeTranslator:
    """按【源文】里的条目数返回等量的编号译文。"""

    def __init__(self):
        self.calls = []
        self.timeout_seconds = 300.0

    def _call_api(self, messages, max_tokens=None, timeout=None):
        self.calls.append({"messages": messages, "max_tokens": max_tokens, "timeout": timeout})
        content = messages[-1]["content"]
        n = max(1, len([line for line in content.splitlines() if line.startswith("[")]))
        return "\n".join(f"[{i + 1}] 译文{i + 1}" for i in range(n))

    def _extract_translation(self, response):
        return response


def make_doc(n=5):
    items = [
        SubtitleItem(index=i + 1, start=float(i), end=float(i) + 0.9, text=f"source line {i + 1}")
        for i in range(n)
    ]
    meta = SubtitleMetadata(
        source_file="demo.mp4", source_lang="ru-RU", target_lang="zh-CN",
        asr_model="large-v3", created_at="2026-10-30T00:00:00", source_step="02_split",
    )
    return SubtitleDocument(metadata=meta, subtitles=items)


def main():
    fake = FakeTranslator()
    sw = SlidingWindowTranslator(
        translator=fake,
        template_manager=PromptTemplateManager(),
        glossary_manager=GlossaryManager(),
        window_size=2, context_size=1, concurrency=1,
        video_style="", auto_detect_style=False,
    )

    out = sw.translate_document(make_doc(5), "ru-RU", "zh-CN",
                                template_name="auto", glossary_name="")

    assert len(fake.calls) == 3, f"5 条 / 窗口 2 应为 3 次调用，实际 {len(fake.calls)}"
    got = [s.translation for s in out.subtitles]
    assert got == ["译文1", "译文2", "译文1", "译文2", "译文1"], got

    first_msgs = fake.calls[0]["messages"]
    assert len(first_msgs) == 1 and first_msgs[0]["role"] == "user", "应为单条 user 消息"
    first = first_msgs[0]["content"]
    assert "【源文】" in first and "【约束要求】" in first, "缺少 instTrans 结构块"
    assert "只输出译文，不要有任何额外说明。" in first, "缺少结尾指令"
    assert "<translation>" not in first, "不应再要求 <translation> 标签"
    assert "上|" not in first and "下| source line 3" in first, "首窗上下文不对"

    second = fake.calls[1]["messages"][0]["content"]
    assert "上| source line 2" in second and "下| source line 5" in second, "中间窗上下文不对"

    strict = sw.translate_strict("source line 1", "ru-RU", "zh-CN")
    assert strict.startswith("译文"), strict

    print("调用次数:", len(fake.calls))
    print("译文:", got)
    print("\n---- 首个窗口 prompt ----")
    print(first)
    print("\n严格重译:", strict)
    print("\nOK: 滑动窗口链路与提示词结构正常")


if __name__ == "__main__":
    main()
