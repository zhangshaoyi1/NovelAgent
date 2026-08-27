"""AI 味检测器——多维度检测 AI 生成特征"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class DetectionResult:
    """检测结果"""
    score: float = 0.0  # 0-100, 越高越像 AI
    is_ai_likely: bool = False
    details: dict = field(default_factory=dict)
    flagged_items: list[str] = field(default_factory=list)


class AILikenessDetector:
    """AI 味检测器——组合多维度检测"""

    def __init__(self) -> None:
        self._checkers: list[AILikenessChecker] = [
            LexicalChecker(),
            SyntacticChecker(),
            SemanticChecker(),
            StatisticalChecker(),
        ]

    def detect(self, text: str) -> DetectionResult:
        """全维度检测文本"""
        if not text or len(text.strip()) < 50:
            return DetectionResult(score=0.0, is_ai_likely=False)

        total_score = 0.0
        all_details = {}
        all_flagged: list[str] = []

        for checker in self._checkers:
            result = checker.check(text)
            all_details[checker.name] = {
                "score": result.score,
                "details": result.details,
                "flagged": result.flagged_items,
            }
            total_score += result.score
            all_flagged.extend(result.flagged_items)

        # 加权平均（各维度权重相等）
        avg_score = total_score / len(self._checkers)
        return DetectionResult(
            score=round(avg_score, 1),
            is_ai_likely=avg_score >= 40.0,
            details=all_details,
            flagged_items=all_flagged,
        )


class AILikenessChecker:
    """检测器基类"""
    name: str = "base"

    def check(self, text: str) -> DetectionResult:
        raise NotImplementedError


class LexicalChecker(AILikenessChecker):
    """词汇维度检测——AI 高频词统计"""

    name = "lexical"

    # AI 高频词列表（中文网文场景）
    AI_HIGH_FREQ_WORDS: ClassVar[list[str]] = [
        "然而", "只见", "突然", "忽然", "似乎", "仿佛", "宛如",
        "不禁", "不由得", "下意识", "莫名", "某种", "某种程度",
        "某种程度上", "不得不说", "不可否认", "毫无疑问",
        "值得注意的是", "值得一提的是", "需要注意的是",
        "总体来说", "总的来说", "总而言之",
        "愈发", "愈加", "日渐", "日益",
        "或许", "也许", "可能", "大概",
        "但", "但是", "却", "不过", "然而",
        "因此", "所以", "于是", "因而",
        "随即", "紧接着", "接下来", "随后",
    ]

    # 人类作家高频词（正常阈值）
    HUMAN_THRESHOLD: ClassVar[float] = 0.03  # 3% 的词汇是 AI 高频词

    def check(self, text: str) -> DetectionResult:
        words = self._tokenize(text)
        if not words:
            return DetectionResult(score=0.0)

        total_words = len(words)
        ai_word_count = 0
        flagged: list[str] = []

        for word in words:
            if word in self.AI_HIGH_FREQ_WORDS:
                ai_word_count += 1
                if ai_word_count <= 5:  # 只记录前 5 个
                    flagged.append(word)

        ratio = ai_word_count / total_words
        # 分数计算：超过阈值线性增加
        score = min(100.0, max(0.0, (ratio / self.HUMAN_THRESHOLD) * 50))

        return DetectionResult(
            score=round(score, 1),
            is_ai_likely=score >= 40,
            details={
                "total_words": total_words,
                "ai_word_count": ai_word_count,
                "ratio": round(ratio, 4),
                "threshold": self.HUMAN_THRESHOLD,
            },
            flagged_items=flagged,
        )

    def _tokenize(self, text: str) -> list[str]:
        """简单分词（按非中文字符分割）"""
        # 提取中文部分
        chinese_chars = re.findall(r"[\u4e00-\u9fff]+", text)
        # 按字符切分（中文词通常是单字或双字）
        tokens: list[str] = []
        for chunk in chinese_chars:
            # 简单分词：尝试匹配双字词
            i = 0
            while i < len(chunk):
                if i + 1 < len(chunk):
                    bigram = chunk[i : i + 2]
                    if bigram in self.AI_HIGH_FREQ_WORDS:
                        tokens.append(bigram)
                        i += 2
                        continue
                tokens.append(chunk[i])
                i += 1
        return tokens


class SyntacticChecker(AILikenessChecker):
    """句式维度检测——句式多样性/句子长度分布"""

    name = "syntactic"

    # AI 常见句式模式
    AI_PATTERNS: ClassVar[list[re.Pattern]] = [
        re.compile(r"他[她]不禁"),
        re.compile(r"他[她]不由得"),
        re.compile(r"只见[他她]"),
        re.compile(r"似乎[^。]*[。！]"),
        re.compile(r"仿佛[^。]*[。！]"),
        re.compile(r"某种程度"),
        re.compile(r"不得不说"),
        re.compile(r"值得注意的是"),
    ]

    def check(self, text: str) -> DetectionResult:
        sentences = self._split_sentences(text)
        if len(sentences) < 3:
            return DetectionResult(score=0.0)

        # 1. 句子长度方差
        lengths = [len(s.strip()) for s in sentences if s.strip()]
        if not lengths:
            return DetectionResult(score=0.0)

        avg_len = sum(lengths) / len(lengths)
        variance = sum((l - avg_len) ** 2 for l in lengths) / len(lengths)
        std_dev = math.sqrt(variance)

        # 太均匀 = AI 特征
        length_score = 0.0
        if std_dev < 10:  # 句子长度过于均匀
            length_score = 50.0 * (1 - std_dev / 10)
        elif std_dev > 50:  # 过于不均匀
            length_score = 20.0

        # 2. AI 句式匹配
        pattern_count = 0
        flagged: list[str] = []
        for pattern in self.AI_PATTERNS:
            matches = pattern.findall(text)
            if matches:
                pattern_count += len(matches)
                flagged.append(pattern.pattern[:20])

        pattern_score = min(50.0, pattern_count * 10)

        # 3. 段落长度方差
        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        para_lengths = [len(p) for p in paragraphs]
        if len(para_lengths) > 1:
            para_avg = sum(para_lengths) / len(para_lengths)
            para_var = sum((l - para_avg) ** 2 for l in para_lengths) / len(para_lengths)
            para_std = math.sqrt(para_var)
            para_score = 0.0
            if para_std < 30:  # 段落过于均匀
                para_score = 30.0 * (1 - para_std / 30)
        else:
            para_score = 0.0

        total_score = length_score * 0.4 + pattern_score * 0.4 + para_score * 0.2

        return DetectionResult(
            score=round(total_score, 1),
            is_ai_likely=total_score >= 40,
            details={
                "sentence_count": len(sentences),
                "avg_length": round(avg_len, 1),
                "std_dev": round(std_dev, 1),
                "pattern_count": pattern_count,
                "para_count": len(paragraphs),
            },
            flagged_items=flagged,
        )

    def _split_sentences(self, text: str) -> list[str]:
        """按句号/感叹号/问号/省略号分割句子"""
        return re.split(r"[。！？…]+", text)


class SemanticChecker(AILikenessChecker):
    """语义维度检测——信息密度/留白比例"""

    name = "semantic"

    def check(self, text: str) -> DetectionResult:
        if not text:
            return DetectionResult(score=0.0)

        total_chars = len(text)

        # 1. 对话比例
        dialogue_chars = self._count_dialogue(text)
        dialogue_ratio = dialogue_chars / total_chars if total_chars > 0 else 0

        # AI 倾向于对话过多或过少
        dialogue_score = 0.0
        if dialogue_ratio > 0.7:  # 对话过多
            dialogue_score = 40.0
        elif dialogue_ratio < 0.05:  # 对话过少
            dialogue_score = 30.0

        # 2. 描写/动作比例
        desc_chars = self._count_description(text)
        desc_ratio = desc_chars / total_chars if total_chars > 0 else 0

        desc_score = 0.0
        if desc_ratio < 0.15:  # 描写不足
            desc_score = 30.0
        elif desc_ratio > 0.6:  # 描写过多
            desc_score = 20.0

        # 3. 情感词密度
        emotion_words = self._count_emotion_words(text)
        emotion_density = emotion_words / (total_chars / 100)  # 每百字情感词数

        emotion_score = 0.0
        if emotion_density > 5:  # 情感词过多
            emotion_score = 30.0
        elif emotion_density < 0.5:  # 情感词过少
            emotion_score = 20.0

        total_score = dialogue_score * 0.4 + desc_score * 0.3 + emotion_score * 0.3

        return DetectionResult(
            score=round(total_score, 1),
            is_ai_likely=total_score >= 40,
            details={
                "total_chars": total_chars,
                "dialogue_ratio": round(dialogue_ratio, 3),
                "desc_ratio": round(desc_ratio, 3),
                "emotion_density": round(emotion_density, 2),
            },
            flagged_items=[],
        )

    def _count_dialogue(self, text: str) -> int:
        """统计对话字符数（引号内内容）"""
        count = 0
        # 中文双引号（非 raw string 以支持 Unicode 转义）
        count += sum(len(m) for m in re.findall('\u201c[^\u201d]*\u201d', text))
        # 中文单引号
        count += sum(len(m) for m in re.findall('\u2018[^\u2019]*\u2019', text))
        # 英文双引号
        count += sum(len(m) for m in re.findall('"[^"]*"', text))
        # 英文单引号
        count += sum(len(m) for m in re.findall("'[^']*'", text))
        return count

    def _count_description(self, text: str) -> int:
        """统计描写/动作字符数"""
        # 简单的启发式：排除对话后的剩余部分
        dialogue = self._count_dialogue(text)
        return len(text) - dialogue

    def _count_emotion_words(self, text: str) -> int:
        """统计情感词数量"""
        emotion_words = [
            "愤怒", "开心", "悲伤", "恐惧", "惊讶", "厌恶",
            "高兴", "难过", "害怕", "震惊", "痛苦", "快乐",
            "激动", "兴奋", "沮丧", "绝望", "焦虑", "紧张",
            "温暖", "感动", "欣慰", "愧疚", "羞耻", "自豪",
        ]
        count = 0
        for word in emotion_words:
            count += text.count(word)
        return count


class StatisticalChecker(AILikenessChecker):
    """统计维度检测——与人类作家语料库对比"""

    name = "statistical"

    # 人类作家参考基线（基于网文语料统计）
    HUMAN_BASELINE: ClassVar[dict[str, float]] = {
        "avg_word_length": 1.8,  # 中文平均词长
        "dialogue_ratio": 0.35,  # 对话比例
        "paragraph_length": 120,  # 平均段落长度
        "sentence_length": 25,  # 平均句子长度
        "punctuation_ratio": 0.12,  # 标点符号比例
    }

    def check(self, text: str) -> DetectionResult:
        if not text:
            return DetectionResult(score=0.0)

        total_chars = len(text)

        # 1. 标点符号比例
        punct_count = len(re.findall(r"[，。！？、；：""''「」『』（）【】《》——…·]", text))
        punct_ratio = punct_count / total_chars if total_chars > 0 else 0
        punct_diff = abs(punct_ratio - self.HUMAN_BASELINE["punctuation_ratio"])
        punct_score = min(50.0, punct_diff * 200)

        # 2. 段落长度
        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        if paragraphs:
            avg_para_len = sum(len(p) for p in paragraphs) / len(paragraphs)
            para_diff = abs(avg_para_len - self.HUMAN_BASELINE["paragraph_length"])
            para_score = min(40.0, para_diff / 10)
        else:
            para_score = 0.0

        # 3. 句子长度
        sentences = re.split(r"[。！？…]+", text)
        sentences = [s.strip() for s in sentences if s.strip()]
        if sentences:
            avg_sent_len = sum(len(s) for s in sentences) / len(sentences)
            sent_diff = abs(avg_sent_len - self.HUMAN_BASELINE["sentence_length"])
            sent_score = min(30.0, sent_diff / 5)
        else:
            sent_score = 0.0

        total_score = punct_score * 0.4 + para_score * 0.3 + sent_score * 0.3

        return DetectionResult(
            score=round(total_score, 1),
            is_ai_likely=total_score >= 40,
            details={
                "punct_ratio": round(punct_ratio, 4),
                "avg_para_length": round(avg_para_len, 1) if paragraphs else 0,
                "avg_sent_length": round(avg_sent_len, 1) if sentences else 0,
                "baseline": self.HUMAN_BASELINE,
            },
            flagged_items=[],
        )