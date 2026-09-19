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

    def check_rhythm(self, window: int = 10) -> list[RhythmAlert]:
        """检查节奏异常"""
        self._alerts.clear()

        if len(self._scores) < window:
            return self._alerts

        recent = self._scores[-window:]

        # 1. 连续平缓（无波动）
        tensions = [s.tension for s in recent]
        if max(tensions) - min(tensions) < 1.0:
            self._alerts.append(RhythmAlert(
                alert_type="flat",
                message=f"连续 {window} 章紧张度无波动（{min(tensions):.1f}-{max(tensions):.1f}）",
                severity="warning",
                start_chapter=recent[0].chapter,
                end_chapter=recent[-1].chapter,
            ))

        # 2. 持续上升无爆发
        if len(tensions) >= window:
            increasing = all(
                tensions[i] <= tensions[i + 1]
                for i in range(len(tensions) - 1)
            )
            if increasing and tensions[-1] < 7.0:
                self._alerts.append(RhythmAlert(
                    alert_type="no_climax",
                    message=f"连续 {window} 章持续上升但未达到高潮（最高 {tensions[-1]:.1f}）",
                    severity="critical",
                    start_chapter=recent[0].chapter,
                    end_chapter=recent[-1].chapter,
                ))

        # 3. 高潮后无余波
        if len(self._scores) >= 3:
            last_three = self._scores[-3:]
            if last_three[0].tension >= 8.0 and all(s.tension >= 7.0 for s in last_three[1:]):
                self._alerts.append(RhythmAlert(
                    alert_type="no_aftermath",
                    message="高潮后连续多章未降紧张度，缺少余波收尾",
                    severity="info",
                    start_chapter=last_three[0].chapter,
                    end_chapter=last_three[-1].chapter,
                ))

        return self._alerts

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
        """
        if not text:
            return 0.0

        # 先切句（三个分量共用，避免重复切分导致口径漂移）
        sentences = [
            s.strip()
            for s in text.replace("！", "。").replace("？", "。").split("。")
            if s.strip()
        ]
        n_sent = max(1, len(sentences))

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
# 背景：``tension_curve`` 自诞生起**零生产调用点**（M6-A §19.2 取证），
# 导致 ``check_rhythm`` 这类跨章张力分析**永远没有输入**——它不是"没做好"，
# 是"从来没被喂过数据"。
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
#   · 纪律 #9：与档位（意图）分属两条独立通道，互不覆盖
# ============================================================

#: 实测张力台账（纯观测面；落盘失败不转致命）。
TENSION_LEDGER = Path(".state") / "memory" / "tension_readings.json"


def measure_chapter_tension(chapter: int, text: str) -> float:
    """度量单章实测张力（0-10）。**纯函数，零副作用**。

    独立于 ``TensionCurveManager`` 实例状态，便于落盘链路任意调用。
    """
    if not text:
        return 0.0
    return TensionCurveManager()._compute_tension(text)


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