"""AI 味修正引擎——后处理修正 AI 生成文本"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class ProcessingResult:
    """处理结果"""
    text: str = ""
    modified: bool = False
    changes: list[str] = field(default_factory=list)


class PostProcessor:
    """修正引擎——组合多个修正器"""

    def __init__(self) -> None:
        self._processors: list[TextProcessor] = [
            StylisticNoiseInjector(),
            DialogueDifferentiator(),
            AIismCleaner(),
        ]

    def process(self, text: str) -> ProcessingResult:
        """全流水线处理"""
        if not text:
            return ProcessingResult(text=text)

        current = text
        all_changes: list[str] = []

        for processor in self._processors:
            result = processor.process(current)
            if result.modified:
                current = result.text
                all_changes.extend(result.changes)

        return ProcessingResult(
            text=current,
            modified=len(all_changes) > 0,
            changes=all_changes,
        )


class TextProcessor:
    """文本处理器基类"""
    name: str = "base"

    def process(self, text: str) -> ProcessingResult:
        raise NotImplementedError


class StylisticNoiseInjector(TextProcessor):
    """风格化噪声注入器——可控的"不完美" """

    name = "stylistic_noise"

    # 句式变化模板
    SENTENCE_VARIANTS: ClassVar[dict[str, list[str]]] = {
        "他": ["他", "他这个人", "这家伙", "对方"],
        "她": ["她", "她这个人", "这姑娘", "这女人", "对方"],
        "但是": ["但是", "可", "不过", "然而", "但"],
        "所以": ["所以", "于是", "因而", "因此", "便"],
        "突然": ["突然", "猛地", "骤然", "冷不丁", "一下子"],
        "非常": ["非常", "十分", "很是", "相当", "特别", "格外"],
        "似乎": ["似乎", "好像", "仿佛", "像是", "隐约"],
        "然后": ["然后", "接着", "随后", "紧跟着", "接下来"],
    }

    # 闲笔插入模板
    FILLER_PHRASES: ClassVar[list[str]] = [
        "说起来，",
        "话说回来，",
        "也不知道为什么，",
        "说起来也怪，",
        "你别说，",
        "就这么着，",
        "要说这事，",
    ]

    def process(self, text: str) -> ProcessingResult:
        if not text:
            return ProcessingResult(text=text)

        changes: list[str] = []
        modified_text = text

        # 1. 替换同义句式（10-20% 的概率）
        for target, variants in self.SENTENCE_VARIANTS.items():
            if random.random() < 0.15:  # 15% 概率替换
                replacement = random.choice(variants)
                count = modified_text.count(target)
                if count > 0 and replacement != target:
                    modified_text = modified_text.replace(target, replacement, 1)
                    changes.append(f"替换 '{target}' → '{replacement}'")

        # 2. 偶尔插入闲笔（5% 概率，且只在段落开头）
        paragraphs = modified_text.split("\n\n")
        for i in range(len(paragraphs)):
            if random.random() < 0.05 and len(paragraphs[i]) > 60:
                filler = random.choice(self.FILLER_PHRASES)
                paragraphs[i] = filler + paragraphs[i][0].lower() + paragraphs[i][1:]
                changes.append(f"插入闲笔: '{filler}'")
                break  # 只插入一次

        modified_text = "\n\n".join(paragraphs)

        return ProcessingResult(
            text=modified_text,
            modified=len(changes) > 0,
            changes=changes,
        )


class DialogueDifferentiator(TextProcessor):
    """对话差异化器——为每个角色建立词汇偏好"""

    name = "dialogue_diff"

    # 角色语气词偏好
    CHARACTER_PATTERNS: ClassVar[dict[str, dict[str, list[str]]]] = {
        "爽朗": {
            "句末": ["！", "！！", "哈哈", "啊！"],
            "语气词": ["哎呀", "嘿", "哟"],
            "口头禅": ["我跟你说", "你瞧", "看我的"],
        },
        "沉稳": {
            "句末": ["。", "……", "嗯。"],
            "语气词": ["嗯", "唔", "也罢"],
            "口头禅": ["依我看", "照我说", "不妨"],
        },
        "活泼": {
            "句末": ["~", "！", "诶！"],
            "语气词": ["哇", "诶", "嘻嘻"],
            "口头禅": ["超", "超级", "好厉害"],
        },
        "阴冷": {
            "句末": ["。", "呵。", "……"],
            "语气词": ["哼", "呵", "嗤"],
            "口头禅": ["有意思", "不自量力", "可笑"],
        },
    }

    def process(self, text: str) -> ProcessingResult:
        if not text:
            return ProcessingResult(text=text)

        changes: list[str] = []
        modified_text = text

        # 找到对话内容，对部分对话做差异化处理
        dialogues = re.findall(r"「[^」]*」|『[^』]*』|“[^”]*”", text)

        if not dialogues:
            return ProcessingResult(text=text, modified=False)

        # 随机选择一种角色类型
        char_type = random.choice(list(self.CHARACTER_PATTERNS.keys()))
        patterns = self.CHARACTER_PATTERNS[char_type]

        # 对 20% 的对话做差异化
        for dial in dialogues:
            if random.random() < 0.2:
                # 替换句末标点
                for old, new_list in [("。", ["！", "……"]), ("！", ["……", "。"]), ("？", ["？", "……！"])]:
                    if dial.endswith(old):
                        new_end = random.choice(new_list)
                        new_dial = dial[:-1] + new_end
                        modified_text = modified_text.replace(dial, new_dial, 1)
                        changes.append(f"对话 '{dial}' → '{new_dial}' ({char_type})")
                        break

        return ProcessingResult(
            text=modified_text,
            modified=len(changes) > 0,
            changes=changes,
        )


class AIismCleaner(TextProcessor):
    """AI 话术清理器——检测并替换 AI 高频词/模式"""

    name = "aiism_cleaner"

    # AI 高频词替换映射
    AIISM_REPLACEMENTS: ClassVar[dict[str, list[str]]] = {
        "然而": ["可", "不过", "但", ""],
        "只见": ["看", "瞧", "就见", "看到"],
        "不禁": ["忍不住", "不由", "下意识"],
        "不由得": ["忍不住", "不禁", "下意识"],
        "仿佛": ["像是", "好像", "跟……似的"],
        "宛如": ["就像", "好比", "好像"],
        "不得不说": ["", "得说", "必须承认"],
        "毫无疑问": ["", "肯定", "不用说"],
        "值得注意的是": ["", "有意思的是", "值得一提的是"],
        "愈发": ["越来越", "更", "越加"],
        "某种程度": ["", "有些", "有点"],
    }

    # 完美结构模式（AI 特征）
    PERFECT_STRUCTURE_PATTERNS: ClassVar[list[re.Pattern]] = [
        re.compile(r"他[她]?[^。]{5,15}，[^。]{10,25}。"),  # 他...，...。
        re.compile(r"[^。]{10,20}，[^。]{10,20}，[^。]{10,20}。"),  # 排比式
    ]

    def process(self, text: str) -> ProcessingResult:
        if not text:
            return ProcessingResult(text=text)

        changes: list[str] = []
        modified_text = text

        # 1. 替换 AI 高频词
        for target, replacements in self.AIISM_REPLACEMENTS.items():
            count = modified_text.count(target)
            if count > 0:
                # 替换 50% 的实例
                replace_count = max(1, count // 2)
                for _ in range(replace_count):
                    replacement = random.choice(replacements)
                    if replacement:
                        modified_text = modified_text.replace(target, replacement, 1)
                        changes.append(f"替换 '{target}' → '{replacement}'")
                    else:
                        # 删除（用空字符串替换）
                        modified_text = modified_text.replace(target, "", 1)
                        changes.append(f"删除 '{target}'")

        # 2. 打破完美结构——在长句中插入口语化打断
        # 找过长的句子，随机插入口语化元素
        paragraphs = modified_text.split("\n\n")
        for i, para in enumerate(paragraphs):
            if len(para) > 100 and random.random() < 0.1:
                # 在中间位置插入口语化打断
                mid = len(para) // 2
                insertions = ["——", "……", "，怎么说呢，", "——说起来，"]
                insertion = random.choice(insertions)
                # 找到最近的句号位置
                insert_pos = para.find("。", mid)
                if insert_pos == -1:
                    insert_pos = mid
                paragraphs[i] = para[:insert_pos] + insertion + para[insert_pos:]
                changes.append(f"插入口语化打断 '{insertion}'")
                break

        modified_text = "\n\n".join(paragraphs)

        return ProcessingResult(
            text=modified_text,
            modified=len(changes) > 0,
            changes=changes,
        )