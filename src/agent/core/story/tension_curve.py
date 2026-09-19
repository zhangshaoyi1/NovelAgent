"""高潮曲线管理——章节紧张度评估、弧级曲线规划、节奏异常检测"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class TensionScore:
    """章节紧张度评分"""
    chapter: int = 0
    tension: float = 0.0  # 0-10
    emotion: str = ""  # 情绪标签
    details: dict = field(default_factory=dict)


@dataclass
class ArcPlan:
    """弧级曲线规划"""
    arc_id: int = 0
    start_chapter: int = 0
    end_chapter: int = 0
    label: str = ""
    phases: list[ArcPhase] = field(default_factory=list)


@dataclass
class ArcPhase:
    """弧级阶段"""
    phase: str = ""  # build_up / escalate / climax / aftermath
    start_chapter: int = 0
    end_chapter: int = 0
    target_tension_min: float = 0.0
    target_tension_max: float = 0.0


@dataclass
class RhythmAlert:
    """节奏异常告警"""
    alert_type: str = ""  # flat / no_climax / no_aftermath
    message: str = ""
    severity: str = "warning"  # info / warning / critical
    start_chapter: int = 0
    end_chapter: int = 0


class TensionCurveManager:
    """高潮曲线管理器"""

    #: ★ M6-B4（2026-09-19）：参与评分所需的**最少句数**。
    #:
    #: 实证：残缺章（只有标题行，1 句）在"按句归一"下拿满分 5.00，
    #: 三个项目因此出现虚假 `max`（真实内容章上限 3.00）。
    #: 阈值 3 的依据：本阈值只判"**有没有足够文本可供统计**"，
    #: 与"张力高低"无关 ⇒ 取得很低即可，避免把合法短章（附录/间章）排除。
    #: 低于此值 ⇒ 返回 0.0（**无数据**语义），细则见 ``_compute_tension``。
    MIN_SENTENCES_FOR_SCORE: int = 3

    # 弧级模型
    ARC_PHASES: list[dict] = [
        {"phase": "build_up", "ratio_start": 0.0, "ratio_end": 0.15,
         "label": "建立期待", "tension_min": 2, "tension_max": 3},
        {"phase": "escalate", "ratio_start": 0.15, "ratio_end": 0.50,
         "label": "逐步升温", "tension_min": 4, "tension_max": 6},
        {"phase": "climax", "ratio_start": 0.50, "ratio_end": 0.75,
         "label": "冲突升级", "tension_min": 7, "tension_max": 8},
        {"phase": "peak", "ratio_start": 0.75, "ratio_end": 0.85,
         "label": "高潮爆发", "tension_min": 9, "tension_max": 10},
        {"phase": "aftermath", "ratio_start": 0.85, "ratio_end": 1.0,
         "label": "余波收尾", "tension_min": 3, "tension_max": 5},
    ]

    def __init__(self) -> None:
        self._scores: list[TensionScore] = []
        self._arcs: list[ArcPlan] = []
        self._alerts: list[RhythmAlert] = []

    def evaluate_chapter(self, chapter: int, text: str) -> TensionScore:
        """评估单章紧张度"""
        if not text:
            return TensionScore(chapter=chapter, tension=0.0)

        score = self._compute_tension(text)
        tension_score = TensionScore(
            chapter=chapter,
            tension=score,
            emotion=self._classify_emotion(text),
            details={
                "conflict_density": self._conflict_density(text),
                "pacing": self._pacing_score(text),
                "dialogue_tension": self._dialogue_tension(text),
            },
        )
        self._scores.append(tension_score)
        return tension_score

    def plan_arc(self, arc_id: int, start_chapter: int, end_chapter: int) -> ArcPlan:
        """规划弧级曲线"""
        total_chapters = end_chapter - start_chapter + 1
        phases: list[ArcPhase] = []

        for phase_info in self.ARC_PHASES:
            phase_start = start_chapter + int(total_chapters * phase_info["ratio_start"])
            phase_end = start_chapter + int(total_chapters * phase_info["ratio_end"])
            phase = ArcPhase(
                phase=phase_info["phase"],
                start_chapter=phase_start,
                end_chapter=phase_end,
                target_tension_min=phase_info["tension_min"],
                target_tension_max=phase_info["tension_max"],
            )
            phases.append(phase)

        # 确保最后一阶段覆盖到终点
        if phases:
            phases[-1].end_chapter = end_chapter

        arc = ArcPlan(
            arc_id=arc_id,
            start_chapter=start_chapter,
            end_chapter=end_chapter,
            label=f"弧 {arc_id}: 第{start_chapter}-{end_chapter}章",
            phases=phases,
        )
        self._arcs.append(arc)
        return arc

    def check_rhythm(self, window: int = 10, *, corpus: list[float] | None = None) -> list[RhythmAlert]:
        """检查节奏异常。

        ★★ M6-B4（2026-09-19）：三条阈值**全部重标定 + 改为相对判据**。
        ------------------------------------------------------------------
        标定实证（M6-B4，8 项目 1251 章 / 1179 个窗口）：

        | 判据 | 原阈值 | 真实触发率 | 原判定 |
        |---|---|---|---|
        | ``no_climax``（最高 < 7.0） | 7.0 | **0.0%** | ❌ 不可达（#13 摧毁扳机）|
        | ``no_aftermath``（首个 ≥ 8.0）| 8.0 | **0 次** | ❌ 不可达 |
        | ``flat``（极差 < 1.0） | 1.0 | **74.6%** | ❌ 几乎全覆盖（同样不可用）|

        ⇒ **三条没有一条可用**。根因不是数字选错，是**度量与文体不匹配**：
        真实网文张力分布集中在 **p50=0.90 / p95=1.50 / max=3.00**，
        而量程设计是 0–10 ⇒ 任何按全量程标定的绝对阈值都不可能可达。

        ★ 候选 D vs 候选 E 的可比对取证（同批语料）：

        | 方案 | 读数 | 触发率 | 判定 |
        |---|---|---|---|
        | D：绝对阈值 = 全局 p95(1.50) | 近 10 章无 ≥1.50 | **72.3%** | ❌ 仍近乎全覆盖 |
        | E：相对判据 = 项目内 top-decile | 近 10 章无达标章 | **28.2%** | ✅ 既可达又有区分度 |

        且**项目间 p95 极差＝1.30**（`gouzailuanshi` 2.40 vs `changan-binyiguan` 1.10）
        ⇒ 单一绝对阈值**无法跨项目通用**（每本书文体不同）。
        ⇒ 采用**候选 E**（纪律 #13①：达成率既非 0% 也非 100%）。

        Args:
            window: 滚动窗口章数。
            corpus: **全书张力序列**（相对判据的分母）。缺省时退回 ``self._scores``
                ——⚠ 仅为兼容既有单测；生产侧应由调用方显式传入全书序列，
                否则"相对"退化为"窗口内相对"，语义变弱。

        ⚠ 本方法**只产出告警、不做动作**（观测面）。接线为闸门须另立登记单
          （纪律 #17：判据强度必须与修复手段配对）。
        """
        self._alerts.clear()

        scores = list(self._scores)
        if len(scores) < window:
            return self._alerts

        recent = scores[-window:]
        tensions = [s.tension for s in recent]

        # ---- 相对判据的基准线：全书 top-decile 门槛 ----
        ref = list(self._corpus_tensions(corpus))
        if len(ref) >= RELATIVE_MIN_CORPUS:
            clim_thr = self._quantile(ref, 0.90)
            flat_thr = self._flat_threshold(ref, window)
            mode = "relative"
        else:
            # 语料不足以定分位 ⇒ **退回绝对阈值 + 显式降级**（不静默）
            clim_thr = RELATIVE_MIN_CORPUS_FALLBACK_HIGH
            flat_thr = RELATIVE_MIN_CORPUS_FALLBACK_SPREAD
            mode = "absolute-fallback"
            self._alerts.append(RhythmAlert(
                alert_type="corpus_insufficient",
                message=f"全书语料 {len(ref)} 章不足以标定相对判据（需 ≥{RELATIVE_MIN_CORPUS}），"
                        f"本次退回绝对阈值（高潮线 {clim_thr:.2f} / 波动线 {flat_thr:.2f}）",
                severity="info",
                start_chapter=recent[0].chapter,
                end_chapter=recent[-1].chapter,
            ))

        # 1. 连续平缓（无波动）—— 相对化：极差小于全书下四分位的"跨度"
        if (max(tensions) - min(tensions)) < flat_thr:
            self._alerts.append(RhythmAlert(
                alert_type="flat",
                message=f"连续 {window} 章紧张度无波动（{min(tensions):.2f}-{max(tensions):.2f}"
                        f"，波动线 {flat_thr:.2f}，基准 {mode}）",
                severity="warning",
                start_chapter=recent[0].chapter,
                end_chapter=recent[-1].chapter,
            ))

        # 2. ★ 缺高潮（原 no_climax）—— 重定义为**因果判据**：
        #    「本窗口内没有任何章进入全书 top-decile」＝该有高潮却没写出来。
        #    （原实现"持续上升且未达 7.0"既不可达、又与语义无关）
        if all(t < clim_thr for t in tensions):
            self._alerts.append(RhythmAlert(
                alert_type="no_climax",
                message=f"连续 {window} 章均未进入全书前 10% 张力区"
                        f"（本窗最高 {max(tensions):.2f} < 门槛 {clim_thr:.2f}，基准 {mode}）",
                severity="warning",
                start_chapter=recent[0].chapter,
                end_chapter=recent[-1].chapter,
            ))

        # 3. ★ 高潮后无余波 —— 相对化：峰值触及 top-decile 后未回落
        if len(scores) >= 3:
            last_three = scores[-3:]
            t3 = [s.tension for s in last_three]
            if t3[0] >= clim_thr and all(t >= clim_thr * AFTERMATH_DECAY_RATIO for t in t3[1:]):
                self._alerts.append(RhythmAlert(
                    alert_type="no_aftermath",
                    message=f"高潮后连续多章未降紧张度（门槛 {clim_thr:.2f}，"
                            f"需回落至 {clim_thr * AFTERMATH_DECAY_RATIO:.2f} 以下）",
                    severity="info",
                    start_chapter=last_three[0].chapter,
                    end_chapter=last_three[-1].chapter,
                ))

        return self._alerts

    def _corpus_tensions(self, corpus: list[float] | None) -> list[float]:
        """取用于标定分位的**全书张力序列**（相对判据的分母）。"""
        if corpus is not None:
            return [float(x) for x in corpus]
        return [s.tension for s in self._scores]

    @staticmethod
    def _quantile(values: list[float], q: float) -> float:
        """线性插值分位数（与标定脚本同口径，避免两处各写一份）。"""
        if not values:
            return 0.0
        s = sorted(values)
        k = (len(s) - 1) * q
        lo = int(k)
        hi = min(lo + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (k - lo)

    @staticmethod
    def _flat_threshold(ref: list[float], window: int) -> float:
        """``flat`` 的**相对阈值**：全书「窗口内极差」的低分位（M6-B4）。

        ★ 为什么不能直接拿样本分位（如 p25=0.70）当极差阈值：
          「极差」是 **window 个样本** 的统计量，其期望**必然小于**单个样本的
          分位跨度 —— 直接比会系统性误报（实证：真实尺度序列波动 ±0.25 时，
          10 章极差约 0.45 < p25=0.70 ⇒ 100% 误报）。

        正确做法：**在全书真实序列上模拟滑动窗口**，取其极差的低分位
          （默认 p10）作阈值 ⇒ 只有"比全书 90% 的窗口都更平"才告警。

        ⚠ 这是"相对判据"该有的形态（纪律 #13①）：达成率由分位**设计保证**
          在 10% 左右，而不是靠拍一个绝对数字。
        """
        if len(ref) < window:
            return RELATIVE_MIN_CORPUS_FALLBACK_SPREAD
        spreads = [
            max(ref[i:i + window]) - min(ref[i:i + window])
            for i in range(len(ref) - window + 1)
        ]
        if not spreads:
            return RELATIVE_MIN_CORPUS_FALLBACK_SPREAD
        thr = TensionCurveManager._quantile(spreads, FLAT_SPREAD_QUANTILE)
        # ★ 全书恒定（所有窗口极差 = 0）⇒ p10 也是 0 ⇒ 严格 `<` 永不成立
        #   （与纪律 #24 同型的边界失效）。此时恒零波动本身就是平的
        #   ⇒ 给一个**极小的正值**，使"与全书一样平"也能被标出。
        if thr <= 0.0:
            return FLAT_SPREAD_ZERO_EPSILON
        return thr

    def get_suggestions(self, arc: ArcPlan) -> list[str]:
        """获取调整建议"""
        suggestions: list[str] = []
        arc_scores = [
            s for s in self._scores
            if arc.start_chapter <= s.chapter <= arc.end_chapter
        ]

        if not arc_scores:
            return suggestions

        for phase in arc.phases:
            phase_scores = [
                s for s in arc_scores
                if phase.start_chapter <= s.chapter <= phase.end_chapter
            ]
            if not phase_scores:
                continue

            avg_tension = sum(s.tension for s in phase_scores) / len(phase_scores)

            if avg_tension < phase.target_tension_min:
                suggestions.append(
                    f"阶段 '{phase.phase}' 紧张度不足（实际 {avg_tension:.1f} < 目标 {phase.target_tension_min}），"
                    f"建议增加冲突或悬念"
                )
            elif avg_tension > phase.target_tension_max:
                suggestions.append(
                    f"阶段 '{phase.phase}' 紧张度过高（实际 {avg_tension:.1f} > 目标 {phase.target_tension_max}），"
                    f"建议加入缓冲或舒缓段落"
                )

        return suggestions

    def _compute_tension(self, text: str) -> float:
        """计算文本紧张度（0-10）。

        ★ M6-B3（2026-09-19）：**按句数归一化**（此前量纲自相矛盾）。
        ------------------------------------------------------------
        标定实证（M6-B2，1266 章真实语料）：
          - 旧实现：冲突词 `count/(len/100)` **按百字**归一，悬念词
            `count*0.3` **绝对计数**（随章长线性膨胀）⇒ 两个分量量纲相反；
          - 且网文单章 2500–3000 字，冲突词几十次 ⇒ 密度被稀释到 ~0.3；
          - 结果：真实 max 仅 **4.40**，而 `no_climax` 阈值是 7.0
            ⇒ **0/1266 章可达**（纪律 #13「阈值成了摧毁扳机」）。

        新实现：三个分量**全部按「每句」归一**，与章长解耦。
          - 分量上界不变（5.0 + 3.0 + 2.0 = 10.0），量程语义不变；
          - 常量按「每句期望值」重标（见各分量注释的标定依据）。

        ★★ M6-B4（2026-09-19）：**短文本不参与评分**（此前空章被判满分）。
        ------------------------------------------------------------
        标定实证（M6-B4 真实语料复查，1266 章）：三个 `max=5.00` 的章
        **全是残缺文件**——
          · ``gouzailuanshi/ch015`` = 15 字、1 句 ⇒ 5.00
          · ``xiuxian-performance/ch150`` = 44 字、1 句 ⇒ 5.00
          · ``五灵破归档/ch198`` = 24 字、1 句 ⇒ 5.00
        内容只有标题行（``# 第 N 章 · …``）。根因：``n_sent=1`` 时
        任何含冲突词的单句都会算出 ``密度=1.0`` ⇒ **满分**。
        ⇒ **空章/占位章会成为全书的"最高潮"**，污染一切分位标定与相对判据
          （纪律 #21 静默失真；真实内容章的上限实为 3.00）。

        处置：句数 < ``MIN_SENTENCES_FOR_SCORE`` ⇒ 返回 **0.0 且不进样本**。
        ⚠ 为什么不"按字数补齐分母"：那是**替空章编造张力**（纪律 #2/#15
          同族——消费者不能编造）；空章的正确语义是"**无数据**"，
          而不是"给了个低分"。由调用方按 0.0 与 `is_measureable()` 区分。
        """
        if not text:
            return 0.0

        # 先切句（三个分量共用，避免重复切分导致口径漂移）
        sentences = [
            s.strip()
            for s in text.replace("！", "。").replace("？", "。").split("。")
            if s.strip()
        ]
        # ★ M6-B4：样本量不足 ⇒ 无数据（不评分，防"1 句满分"的脏读数）
        if len(sentences) < self.MIN_SENTENCES_FOR_SCORE:
            return 0.0
        n_sent = len(sentences)

        score = 0.0

        # ---- 分量 1：冲突词「句密度」（上界 5.0）----
        # 标定：32 组对抗句全含冲突词 ⇒ 密度 1.0 ⇒ 得 5.0（满分）。
        conflict_words = [
            "杀", "战", "斗", "怒", "危", "险", "逃", "追",
            "埋伏", "陷阱", "阴谋", "背叛", "决斗", "爆炸",
            "攻击", "防御", "受伤", "死亡", "危机",
        ]
        conflict_count = sum(text.count(w) for w in conflict_words)
        conflict_per_sentence = conflict_count / n_sent
        score += min(5.0, conflict_per_sentence * 5.0)

        # ---- 分量 2：悬念词「句密度」（上界 3.0）----
        # 标定：每 2 句一个悬念标记 ⇒ 密度 0.5 ⇒ 得 3.0（满分）。
        suspense_markers = [
            "突然", "竟然", "没想到", "谁知", "难道",
            "究竟", "到底", "会不会", "莫非",
        ]
        suspense_count = sum(text.count(m) for m in suspense_markers)
        suspense_per_sentence = suspense_count / n_sent
        score += min(3.0, suspense_per_sentence * 6.0)

        # ---- 分量 3：短句比例（上界 2.0）----
        # 本是比例量，与章长无关，**保持不变**（比例 > 2/3 ⇒ 满分）。
        short_sentences = sum(1 for s in sentences if len(s) < 10)
        short_ratio = short_sentences / n_sent
        score += min(2.0, short_ratio * 3.0)

        return round(min(10.0, score), 1)

    def _classify_emotion(self, text: str) -> str:
        """分类文本情绪基调"""
        emotions = {
            "紧张": ["紧张", "危险", "危机", "紧迫", "急"],
            "悲伤": ["悲伤", "哭泣", "眼泪", "痛苦", "绝望"],
            "愤怒": ["愤怒", "怒火", "愤", "怒"],
            "温馨": ["温馨", "温暖", "感动", "幸福", "甜蜜"],
            "恐惧": ["恐惧", "害怕", "恐怖", "惊悚", "可怕"],
            "惊喜": ["惊喜", "意外", "开心", "高兴", "欢乐"],
        }

        scores = {}
        for emotion, words in emotions.items():
            scores[emotion] = sum(text.count(w) for w in words)

        if not any(scores.values()):
            return "中性"

        return max(scores, key=scores.get)

    def _conflict_density(self, text: str) -> float:
        """冲突密度"""
        conflict_markers = ["冲突", "对抗", "矛盾", "争执", "对立", "战斗"]
        count = sum(text.count(m) for m in conflict_markers)
        return round(count / (len(text) / 100), 2)

    def _pacing_score(self, text: str) -> float:
        """节奏评分（0-10）"""
        sentences = [s.strip() for s in text.replace("！", "。").replace("？", "。").split("。") if s.strip()]
        if len(sentences) < 2:
            return 5.0

        lengths = [len(s) for s in sentences]
        avg = sum(lengths) / len(lengths)
        std = math.sqrt(sum((l - avg) ** 2 for l in lengths) / len(lengths))

        # 适中的句子长度变化 = 好节奏
        if 5 < std < 20:
            return 8.0
        elif std <= 5:
            return 4.0  # 过于均匀
        else:
            return 6.0  # 变化过大

    def _dialogue_tension(self, text: str) -> float:
        """对话紧张度（0-10）"""
        dialogues = re.findall(r"「[^」]*」|『[^』]*』|“[^”]*”", text)
        if not dialogues:
            return 0.0

        tension_words = ["！", "？", "!", "?", "绝不", "休想", "找死", "可恶"]
        tension_count = sum(
            1 for d in dialogues
            for w in tension_words
            if w in d
        )
        return round(min(10.0, tension_count * 2.0), 1)


import re


# ============================================================
# M6-B1（2026-09-19）：实测张力落盘（纯观测面）
#
# 背景：``tension_curve`` 自诞生起**零生产调用点**（M6-A §19.2 取证）——
# 它不是"没做好"，是"从来没被喂过数据"。本段落是它的第一条生产链路。
#
# ⚠ 重要边界（2026-09-19 复核，纪律 #7）：
#   本段落（``measure/record/read_tension``）与 ``TensionCurveManager`` 的
#   跨章分析**是两条通道**，当前**并不相通**：
#     · 本段落 → 写**文件台账** ``TENSION_LEDGER``；读方 = ``read_tension()``
#     · ``check_rhythm`` → 读**内存 state** ``self._scores``；而 ``_scores``
#       只由 ``evaluate_chapter()`` 填充，该入口**仍是零生产调用点**
#   ⇒ 不要写"落盘供 check_rhythm 消费"这类注释（那是给缺陷做伪装）。
#
# 定位（关键，防走错方向）：这是**观测面**，不是供给面。
#   · 强度档位（M1–M4）= **意图**（规划标注"该写多强"）
#   · 实测张力（本函数） = **事实**（正文统计"实际写出来多强"）
# ⇒ 两者是「意图 vs 事实」的对账关系（纪律 #9：主链路自证的现象要独立对账）。
# ⇒ **绝不用张力值去反推/覆盖档位**——那是系统替作者定意图，
#    违反 ``chapter_contract.py:447`` 的既有红线。
#
# 纪律遵守：
#   · 纪律 #2：落盘失败**不转致命**（纯观测面，动作强度 ≤ 判据）
#   · 纪律 #7：不写"供 XX 消费"的注释（消费者不存在就不写）
#   · 纪律 #9：与档位（意图）分属两条独立通道，互不覆盖
# ============================================================

#: 实测张力台账（纯观测面；落盘失败不转致命）。
TENSION_LEDGER = Path(".state") / "memory" / "tension_readings.json"

#: ★ M6-B4：标定相对判据所需的**最少全书章数**（低于此值改用绝对兜底）。
#: 依据：分位数在 n<20 时极不稳定（一个极值就移动门槛），
#: 而"相对判据"的前提是分母可信。不足时**显式降级**（发 corpus_insufficient 告警），
#: 不静默退回绝对阈值（纪律 #1）。
RELATIVE_MIN_CORPUS = 20

#: ★ M6-B4：语料不足时的**兜底绝对阈值**（仅作降级路径，非主判据）。
#: 取自 M6-B4 真实分布：p95=1.50 作高潮线、p25 跨度=0.20 作波动线。
#: ⚠ 这两个值**跨项目不通用**（项目间 p95 极差 1.30），故只用于"冷启动"，
#: 一旦语料 ≥ RELATIVE_MIN_CORPUS 即切回相对判据。
RELATIVE_MIN_CORPUS_FALLBACK_HIGH = 1.50
RELATIVE_MIN_CORPUS_FALLBACK_SPREAD = 0.20

#: ★ M6-B4：高潮后"余波"的回落比例（相对门槛的倍数）。
#: 原实现写死 8.0/7.0（绝对，不可达）；改为"回落到门槛的 70% 以下"。
AFTERMATH_DECAY_RATIO = 0.70

#: ★ M6-B4：``flat`` 判据的极差分位（相对阈值）。
#: 取 0.10 ⇒ 只有"比全书 90% 的窗口都更平"才告警 ⇒ 达成率设计在 ≈10%。
#: ⚠ 不能用**样本分位**代替"窗口极差分位"（见 ``_flat_threshold`` 注释）。
FLAT_SPREAD_QUANTILE = 0.10

#: ★ M6-B4：全书**恒定零波动**时的兜底阈值（见 ``_flat_threshold``）。
#: 严格 `<` 在阈值 0 下永不成立（纪律 #24 同型的边界失效）⇒ 给极小正值。
FLAT_SPREAD_ZERO_EPSILON = 1e-9


def measure_chapter_tension(chapter: int, text: str) -> float:
    """度量单章实测张力（0-10）。**纯函数，零副作用**。

    独立于 ``TensionCurveManager`` 实例状态，便于落盘链路任意调用。

    ⚠ M6-B4：文本不足以统计时返回 ``0.0``（= **无数据**，非"低张力"）。
    需要区分这两种情形时用 :func:`is_measureable`。
    """
    if not text:
        return 0.0
    return TensionCurveManager()._compute_tension(text)


def is_measureable(text: str) -> bool:
    """该文本是否**有足够样本**参与张力统计（M6-B4）。

    用途（纪律 #15 同族）：区分「**无数据**」与「**低张力**」。
    残缺章（只有标题行）不得被当作"最平缓的章"参与分位标定或相对判据
    ——那会让"没写"看起来像"写得很平"，两种失败互相掩盖（纪律 #1）。
    """
    if not text:
        return False
    n = len([
        s for s in text.replace("！", "。").replace("？", "。").split("。")
        if s.strip()
    ])
    return n >= TensionCurveManager.MIN_SENTENCES_FOR_SCORE


def record_tension(
    project_dir: str | Path, chapter: int, text: str, *, cap: int = 500
) -> bool:
    """把本章**实测张力**写入台账（纯观测面）。返回是否落盘成功。

    ⚠ 失败返回 ``False`` 而**不抛**：本台账是观测面，落盘失败只等于
    "这次没记上"，用观测面失败去阻断写章＝动作强度超过判据（纪律 #2）。
    """
    import json
    import logging
    import time

    logger = logging.getLogger("agent.core.story.tension_curve")
    try:
        path = Path(project_dir) / TENSION_LEDGER
        data: dict = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except Exception as e:  # noqa: BLE001 - 坏行当空表重来，不阻断
                logger.warning("[degrade] tension_curve.read_ledger %r", e)
        records = list(data.get("records") or [])
        records.append(
            {
                "ch": int(chapter),
                "tension": measure_chapter_tension(chapter, text),
                "at": time.time(),
            }
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {"records": records[-cap:], "updated_at": time.time()},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)
        return True
    except Exception as e:  # noqa: BLE001 - 观测面失败不阻断写作
        logger.warning("[degrade] tension_curve.record %r", e)
        return False


def read_tension(project_dir: str | Path) -> list[dict]:
    """读回实测张力台账；缺失/坏文件返回空表（观测面不得抛）。"""
    import json

    try:
        path = Path(project_dir) / TENSION_LEDGER
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        records = data.get("records") if isinstance(data, dict) else None
        return [r for r in (records or []) if isinstance(r, dict)]
    except Exception:  # noqa: BLE001 - 观测面读取失败＝无数据
        return []