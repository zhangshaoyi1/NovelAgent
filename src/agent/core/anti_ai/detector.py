"""AI 味检测器——多维度检测 AI 生成特征

包含两套能力：
1. ``AILikenessDetector``：多维度（词汇/句式/语义/统计）加权打分（历史能力，保持兼容）。
2. ``AIFlavorScanner``：去AI味 6 指标客观扫描 + 轻/中/重分级 + 白名单（P0 增强，
   对齐 story-deslop skill 的量化标准）。供 ``DeslopRewriter`` 与 ``deslop`` CLI 使用。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
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
        # P0 扩充：story-deslop 7 模式强标记（组合式，避免误伤）
        "映入眼帘", "此时此刻", "沉声道", "嘴角微扬", "脸色一变",
        "目光如炬", "不由自主", "心中暗道", "心底泛起",
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


# ============================================================================
# P0 去AI味 6 指标扫描 + 轻/中/重分级 + 白名单
# 对齐 story-deslop skill 的量化标准（参考值，需结合题材调整）。
# ============================================================================

AI_FLAVOR_LIGHT = "light"
AI_FLAVOR_MEDIUM = "medium"
AI_FLAVOR_HEAVY = "heavy"

AI_FLAVOR_LEVELS = (AI_FLAVOR_LIGHT, AI_FLAVOR_MEDIUM, AI_FLAVOR_HEAVY)

# 一级禁用词（出现即替换；组合式高置信）
BANNED_WORDS_PRIMARY: tuple[str, ...] = (
    # 情态类
    "仿佛", "犹如", "宛若", "一丝", "一抹", "些许", "几分", "隐约",
    # 动作类
    "深吸一口气", "缓缓", "不禁", "微微", "轻轻", "淡淡",
    # 表情类
    "眼中闪过", "嘴角勾起", "眉头微皱", "眉眼低垂", "瞳孔微缩",
    # 心理类
    "心中一动", "心头一震", "心下了然", "心中暗道", "心底泛起", "不由得",
    # 判断类
    "不容置疑", "不易察觉", "显而易见", "毫无疑问", "不可否认",
    # 形容类
    "坚定", "闪烁着光芒", "狡黠", "深邃", "凛冽",
    # 过渡类
    "不由自主", "情不自禁", "自然而然",
    # 7 模式补充
    "映入眼帘", "沉声道", "脸色一变", "嘴角微扬", "此时此刻", "目光如炬",
    "心中涌起", "涌上一股",
)

# 二级禁用词（语境敏感：仅高频时替换）
BANNED_WORDS_SECONDARY: tuple[str, ...] = ("突然", "好像", "瞬间")

# 直接心理描写词（告知式情绪，而非动作展示）
PSYCHOLOGY_WORDS: tuple[str, ...] = (
    "感到", "觉得", "心想", "暗道", "意识到", "明白",
    "心中", "心底", "内心", "暗自", "默默",
)

# 公式化对话标签
DIALOGUE_TAGS: tuple[str, ...] = (
    "说道", "问道", "笑道", "答道", "回应道", "沉声道", "冷声道",
    "低声道", "厉声道", "喊道", "叫道", "喝道", "轻声道", "淡淡地说",
)

# 重复描写（叠写）探测用的身体反应/感知词（多字词，避免单字"手/心/眼"几乎每段都命中）
_BODY_REACTION_WORDS: tuple[str, ...] = (
    "瞳孔", "指尖", "呼吸", "嘴角", "眉心", "肩头", "心头",
    "掌心", "眼眶", "喉咙", "指甲", "脊背", "太阳穴", "虎口", "耳根",
)


def _metric_level(value: float, light_max: float, medium_max: float) -> str:
    """按阈值映射指标到轻/中/重档位。"""
    if value <= light_max:
        return AI_FLAVOR_LIGHT
    if value <= medium_max:
        return AI_FLAVOR_MEDIUM
    return AI_FLAVOR_HEAVY


@dataclass
class AIFlavorReport:
    """去AI味扫描报告（6 指标 + 分级 + 命中明细）。"""

    level: str = AI_FLAVOR_LIGHT
    score: float = 0.0  # 0-100，由分级派生（轻 0-30 / 中 30-70 / 重 70-100）
    metrics: dict = field(default_factory=dict)  # 指标名 -> {"value", "level", "threshold"}
    banned_hits: list[dict] = field(default_factory=list)  # [{"word", "count"}]
    flagged_items: list[str] = field(default_factory=list)  # 人类可读的命中说明

    @property
    def medium_or_heavy(self) -> bool:
        return self.level in (AI_FLAVOR_MEDIUM, AI_FLAVOR_HEAVY)


class AIFlavorScanner:
    """去AI味 6 指标客观扫描器。

    6 指标（对齐 story-deslop skill）：
    1. banned_word_density  禁用词密度（命中次数/千字）        轻≤5  中6-15  重>15
    2. parallel_count       连续排比段数（结构签名连续命中数）  轻≤2  中3-4   重≥5
    3. psychology_ratio     心理词占比（心理词数/段落数×100）  轻≤10 中10-25 重>25
    4. dialogue_tag_density 对话标签密度（标签数/对话句数）     轻≤30 中30-50 重>50
    5. avg_sentence_per_para 平均段落句数                       轻≤3  中3-5   重>5
    6. repeat_density       重复描写密度（相邻叠写段落对数）    轻≤1  中2-3   重≥4

    综合判定：取六项最高档位；任一指标达重 → 重；无重时中度指标 ≥3 项 → 中，否则轻。
    """

    # 指标阈值：(light_max, medium_max)
    THRESHOLDS: ClassVar[dict[str, tuple[float, float]]] = {
        "banned_word_density": (5.0, 15.0),
        "parallel_count": (2.0, 4.0),
        "psychology_ratio": (10.0, 25.0),
        "dialogue_tag_density": (0.3, 0.5),
        "avg_sentence_per_para": (3.0, 5.0),
        "repeat_density": (1.0, 3.0),
    }

    def __init__(
        self,
        project_dir: str | Path | None = None,
        whitelist: set[str] | None = None,
    ) -> None:
        self._whitelist = (
            self._load_whitelist(project_dir) if whitelist is None else set(whitelist)
        )

    # ------------------------------------------------------------------
    # 白名单
    # ------------------------------------------------------------------
    @staticmethod
    def _load_whitelist(project_dir: str | Path | None) -> set[str]:
        """从 <project_dir>/.deslop-whitelist 读取豁免词（一行一个）。"""
        if not project_dir:
            return set()
        f = Path(project_dir) / ".deslop-whitelist"
        if not f.exists():
            return set()
        words: set[str] = set()
        for ln in f.read_text(encoding="utf-8").splitlines():
            w = ln.strip()
            if w and not w.startswith("#"):
                words.add(w)
        return words

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def scan(self, text: str) -> AIFlavorReport:
        if not text or len(text.strip()) < 50:
            return AIFlavorReport()

        metrics: dict[str, dict] = {}
        # 1. 禁用词密度
        banned_hits = self._scan_banned_words(text)
        density = (
            sum(h["count"] for h in banned_hits) / (len(text) / 1000.0)
            if text
            else 0.0
        )
        metrics["banned_word_density"] = {
            "value": round(density, 2),
            "level": _metric_level(density, *self.THRESHOLDS["banned_word_density"]),
            "threshold": "轻≤5 / 中6-15 / 重>15（次/千字）",
        }
        # 2. 连续排比段数
        parallel = self._detect_parallel(text)
        metrics["parallel_count"] = {
            "value": parallel,
            "level": _metric_level(parallel, *self.THRESHOLDS["parallel_count"]),
            "threshold": "轻≤2 / 中3-4 / 重≥5",
        }
        # 3. 心理词占比
        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        para_count = max(1, len(paragraphs))
        psy_count = sum(text.count(w) for w in PSYCHOLOGY_WORDS)
        psy_ratio = psy_count / para_count * 100.0
        metrics["psychology_ratio"] = {
            "value": round(psy_ratio, 1),
            "level": _metric_level(psy_ratio, *self.THRESHOLDS["psychology_ratio"]),
            "threshold": "轻≤10 / 中10-25 / 重>25（%）",
        }
        # 4. 对话标签密度
        tag_density = self._dialogue_tag_density(text)
        metrics["dialogue_tag_density"] = {
            "value": round(tag_density, 3),
            "level": _metric_level(tag_density, *self.THRESHOLDS["dialogue_tag_density"]),
            "threshold": "轻≤0.30 / 中0.30-0.50 / 重>0.50",
        }
        # 5. 平均段落句数
        sentences = [s.strip() for s in re.split(r"[。！？…]+", text) if s.strip()]
        avg_sp = (len(sentences) / para_count) if para_count else 0.0
        metrics["avg_sentence_per_para"] = {
            "value": round(avg_sp, 2),
            "level": _metric_level(avg_sp, *self.THRESHOLDS["avg_sentence_per_para"]),
            "threshold": "轻≤3 / 中3-5 / 重>5",
        }
        # 6. 重复描写密度
        repeat = self._detect_repeat(text)
        metrics["repeat_density"] = {
            "value": repeat,
            "level": _metric_level(repeat, *self.THRESHOLDS["repeat_density"]),
            "threshold": "轻≤1 / 中2-3 / 重≥4",
        }

        # 综合判定：重优先；否则中（≥2 项中度指标，或禁用词密度单独达中）；否则轻
        level_order = {AI_FLAVOR_LIGHT: 0, AI_FLAVOR_MEDIUM: 1, AI_FLAVOR_HEAVY: 2}
        max_level = max(
            (m["level"] for m in metrics.values()),
            key=lambda lv: level_order[lv],
        )
        medium_count = sum(1 for m in metrics.values() if m["level"] == AI_FLAVOR_MEDIUM)
        if max_level == AI_FLAVOR_HEAVY:
            level = AI_FLAVOR_HEAVY
        elif (
            medium_count >= 2
            or metrics["banned_word_density"]["level"] == AI_FLAVOR_MEDIUM
        ):
            # 禁用词密度是核心信号，单独达中即整体判中（≥6 次/千字已明显"模板化"）
            level = AI_FLAVOR_MEDIUM
        else:
            level = AI_FLAVOR_LIGHT

        score = {"light": 20.0, "medium": 55.0, "heavy": 85.0}[level]
        flagged = [f"禁用词「{h['word']}」×{h['count']}" for h in banned_hits[:8]]
        if parallel >= 3:
            flagged.append(f"连续排比 {parallel} 句")
        if repeat >= 2:
            flagged.append(f"相邻叠写描写 {repeat} 处")

        return AIFlavorReport(
            level=level,
            score=score,
            metrics=metrics,
            banned_hits=banned_hits,
            flagged_items=flagged,
        )

    # ------------------------------------------------------------------
    # 指标实现
    # ------------------------------------------------------------------
    def _scan_banned_words(self, text: str) -> list[dict]:
        """统计一级+二级禁用词命中（白名单跳过；二级仅计入高频）。"""
        counts: dict[str, int] = {}
        for w in BANNED_WORDS_PRIMARY:
            if w in self._whitelist:
                continue
            c = text.count(w)
            if c:
                counts[w] = c
        for w in BANNED_WORDS_SECONDARY:
            if w in self._whitelist:
                continue
            c = text.count(w)
            if c >= 3:  # 语境敏感词：高频才计入
                counts[w] = c
        return [
            {"word": w, "count": c}
            for w, c in sorted(counts.items(), key=lambda kv: -kv[1])
        ]

    @staticmethod
    def _detect_parallel(text: str) -> int:
        """连续排比检测：句子结构签名（前 3 字 + 逗号数）连续命中；另计显式排比引导词。"""
        sentences = [s.strip() for s in re.split(r"[。！？…]+", text) if s.strip()]
        if len(sentences) < 3:
            return 0
        sigs: list[tuple[str, int]] = []
        for s in sentences:
            head = re.sub(r"[\s\u201c\u201d「」“”…——]", "", s)[:3]
            sigs.append((head, s.count("，")))
        max_run = 1
        cur = 1
        for i in range(1, len(sigs)):
            if sigs[i] == sigs[i - 1]:
                cur += 1
                max_run = max(max_run, cur)
            else:
                cur = 1
        # 显式排比引导词（有的/一边/时而 等强排比标记；不用"不是/也是"等通用否定/系词）
        explicit = 0
        for lead in ("有的", "一边", "时而", "一会儿"):
            n = len(re.findall(lead + r"[^。！？，]{1,18}[，。！？]", text))
            if n > explicit:
                explicit = n
        return max(max_run, explicit)

    @staticmethod
    def _dialogue_tag_density(text: str) -> float:
        """对话标签密度 = 公式化标签数 / 对话句数。"""
        # 同时匹配中文「『“”』」与 ASCII 双引号（部分章节用英文引号）
        dialogues = re.findall(r"[「『“\"][^」』”\"]{1,80}[」』”\"]", text)
        dial_count = max(1, len(dialogues))
        tag_count = sum(text.count(t) for t in DIALOGUE_TAGS)
        return tag_count / dial_count

    @staticmethod
    def _detect_repeat(text: str) -> int:
        """重复描写密度：相邻段落命中同一身体反应词 → 叠写一对。

        用「同一词在相邻段落都出现」而非「任意身体词出现」来判定，
        避免常见词（如"呼吸"）在对话段穿插时被误计。
        """
        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        pairs = 0
        prev: set[str] = set()
        for p in paragraphs:
            cur = {w for w in _BODY_REACTION_WORDS if w in p}
            if cur & prev:  # 相邻段落重复同一个身体反应描写
                pairs += 1
            prev = cur
        return pairs