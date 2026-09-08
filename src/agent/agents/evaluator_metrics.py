"""EvaluatorAgent 拆分的 Mixin（机械搬移，行为零改动）。

拆分背景：单文件 1200+ 行不利维护（对齐 m5 / agentic_pipeline 拆分模式）。
主文件 ``evaluator.py`` 保留类定义、``__init__``、汇总与主入口；
本文件承载对应方法组。仅供 ``EvaluatorAgent`` 继承组合，不要单独使用。
"""

from __future__ import annotations



import re
from pathlib import Path
from typing import Any

from agent.agents.evaluator_types import DimensionResult, NovelHealthReport
from agent.core.quality.dimension_registry import safe_default_for


import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from rich.console import Console

from agent.core.story.chapters import iter_chapter_texts  # G6：公共章节读取 helper（根因 B6-3）
from agent.core.engine.state_machine import StateMachine
# D-J（2026-08-29）：不再在 agents 层直接 import workflows（违反依赖方向）。
# 回退能力经 ``rollback_provider`` 构造注入（见 RollbackProvider），未注入时懒加载兜底。
from agent.core.quality.scoring.reader_appeal import (  # G5：迷爱看六维双闸
    ReaderAppealScorer,
    APPEAL_DIMENSIONS,
    APPEAL_PASS_LINE,
    APPEAL_DIM_FLOOR,
    APPEAL_GATE_PREFIX,
    APPEAL_LABELS,
    gate_chapter,
    gate_first_chapters,   # G6：B4 黄金三章门禁
    GOLDEN_GATE_PREFIX,    # G6：golden_* 维度名前缀
    _verdict,
)


# ============================================================
# 报告结构
# ============================================================
from agent.agents.evaluator_types import (  # noqa: F401
    _SOFT_MARGIN,
    DimensionResult,
    NovelHealthReport,
    RepairPlan,
)

class _EvaluatorMetricsMixin:
    def _score(self, name: str) -> float:
        if self.score_fn is not None:
            try:
                return float(self.score_fn(name, str(self.project_dir)))
            except Exception:  # noqa: BLE001
                pass  # noqa: SILENT_DEGRADE
        # 安全默认（无 LLM）：硬指标 0 通过，评分维度给满分。
        # HA-Eval L2：由 dimension_registry 登记表派生（SSOT）。
        return safe_default_for(name)

    def _evidence_for(self, name: str) -> Any | None:
        """取该维度的评分证据（HA-Eval L3）。

        ``score_fn`` 协议是 ``(name, project_dir) -> float``，无法回传证据对象，
        故经其绑定对象（``ReaderAppealScorer``）的 ``get_evidence()`` 旁路获取。
        ``score_fn`` 非绑定方法 / 无该能力时返回 ``None``（置信度按 1.0 处理）。
        """
        provider = getattr(self.score_fn, "__self__", None)
        getter = getattr(provider, "get_evidence", None)
        if not callable(getter):
            return None
        try:
            return getter(name)
        except Exception:  # noqa: BLE001 - 取证据失败不影响评分本身
            return None  # noqa: SILENT_DEGRADE

    def _metric_foreshadow_recycle(self) -> tuple[float, dict[str, int | list[dict[str, str]]]]:
        """确定性：伏笔回收率（到期口径）。

        2026-09-07 根治（五灵破归档 20:58 轮假失败同族问题之三）：旧实现统计
        全书未回收伏笔——中途窗口（如 1200 章书写到 16 章、回收点在 ch25+）
        回收率必然趋 0，误触发末窗回退重写（修不到、白烧 token）。改为到期口径：
        - 到期（due）= 状态为「逾期」，或 预期回收点章号 <= 当前进度（磁盘章数）；
        - rate = 到期项中已回收占比；无到期项 → 1.0（不误杀）；
        - 回收点无法解析（如「全书末段」）视为未到期；已废弃不参与。
        兼容键：resolved/unresolved/total 仍统计全书口径供报告展示；
        新增 due/due_resolved/overdue 供明细。
        反馈闭环（2026-09-08）：新增 ``due_open``（到期未回收的逐条明细：
        id/内容/回收点），经 evidence 编入 rewrite hint / 教训，让 Writer
        精确知道该回收哪几条伏笔，而不是只知道"回收率不达标"。
        """
        f_file = self.project_dir / "foreshadows.md"
        resolved = 0
        unresolved = 0
        due_total = 0
        due_resolved = 0
        due_open: list[dict[str, str]] = []
        if f_file.exists():
            current = self._current_chapter_for_due()
            for line in f_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line.startswith("|") or line.startswith("|---") or line.startswith("| ID"):
                    continue
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if len(cells) < 5:
                    continue
                state = cells[4]
                if state == "已废弃":
                    continue
                if state == "已回收":
                    resolved += 1
                else:
                    unresolved += 1
                # 到期判定：状态逾期，或回收点章号 <= 当前进度
                overdue_marked = state == "逾期"
                due_point = self._parse_recycle_chapter(cells[3] if len(cells) > 3 else "")
                is_due = overdue_marked or (due_point is not None and due_point <= current)
                if is_due:
                    due_total += 1
                    if state == "已回收":
                        due_resolved += 1
                    else:
                        due_open.append({
                            "id": cells[0],
                            "content": (cells[1] or "")[:40],
                            "recycle_point": cells[3] if len(cells) > 3 else "",
                        })
        rate = (due_resolved / due_total) if due_total > 0 else 1.0
        return rate, {
            "resolved": resolved,
            "unresolved": unresolved,
            "total": resolved + unresolved,
            "due": due_total,
            "due_resolved": due_resolved,
            "overdue": due_total - due_resolved,
            "due_open": due_open,
        }

    def _current_chapter_for_due(self) -> int:
        """当前写章进度（到期判定的基准）：磁盘 ch*.md 数（顶层，不含归档）。"""
        ch_dir = self.project_dir / "chapters"
        if not ch_dir.exists():
            return 0
        return sum(1 for f in ch_dir.glob("ch*.md") if f.is_file())

    @staticmethod
    def _parse_recycle_chapter(cell: str) -> int | None:
        """从预期回收点单元格解析章号。

        支持格式：``ch025`` / ``25`` / ``第25章`` / ``S03/E01/ch401``（取 ch 前缀
        章号，避免把阶段编号 S03 误当章号）；解析失败返回 None。
        """
        text = cell or ""
        m = re.search(r"ch\s*(\d+)", text)
        if not m:
            matches = re.findall(r"(\d+)", text)
            m2 = matches[-1] if matches else None
            return int(m2) if m2 else None
        return int(m.group(1))

    def _metric_pacing(self) -> tuple[float, dict[str, Any]]:
        """确定性：异常章节比例（注水/赶进度）。G6：读取改走公共 helper（行为零变化）。

        反馈闭环（2026-09-08）：新增 ``abnormal_chapters``（异常章号+篇幅明细），
        供 evidence 编入 rewrite hint / 教训，让 Writer 知道具体哪些章失衡。
        """
        counts: list[tuple[str, int]] = []
        for f, text in iter_chapter_texts(self.project_dir):
            counts.append((f.stem, len(re.sub(r"\s", "", text))))
        if not counts:
            return 0.0, {"chapters": 0, "abnormal": 0, "abnormal_chapters": []}
        lengths = [c for _, c in counts]
        median = statistics.median(lengths)
        if median <= 0:
            return 0.0, {"chapters": len(counts), "abnormal": 0, "abnormal_chapters": []}
        abnormal_items = [
            {"chapter": stem, "length": c}
            for stem, c in counts
            if c < 0.5 * median or c > 2.0 * median
        ]
        return len(abnormal_items) / len(counts), {
            "chapters": len(counts),
            "abnormal": len(abnormal_items),
            "median": median,
            "abnormal_chapters": abnormal_items,
        }

    # ---- G6：B6 防注水确定性指标（拍板 #5：重复度硬闸 + 信息密度软标红）----
    _REPEAT_SIM_THRESHOLD: float = 0.85      # 句级相似度（字符集合 Jaccard）≥ 此值视为重复句
    _INFO_DENSITY_FLOOR: float = 0.25        # 推进句占比 < 25% 触发信息密度软标红
    _ADVANCE_WORDS = ("说", "道", "问", "答", "喊", "吼", "叫", "走", "冲", "打", "夺",
                      "跳", "追", "抢", "看", "笑", "哭", "跪", "拔", "挥", "杀", "逃")
    _SHIFT_WORDS = ("忽然", "瞬间", "下一刻", "终于", "然后", "接着", "随即", "来到", "离开", "进入")

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """按中文句末标点切分句子，去空白；长度 < 8 字忽略（太短无判重意义）。"""
        parts = re.split(r"[。！？…；\n]+", text)
        return [p.strip() for p in parts if len(p.strip()) >= 8]

    @staticmethod
    def _sent_sim(a: str, b: str) -> float:
        """确定性相似度：字符集合 Jaccard（0-1）。"""
        sa, sb = set(a), set(b)
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    def _metric_repetition(self) -> tuple[float, dict[str, Any]]:
        """确定性：重复句占比（0-1）。每章内句子两两比较，任一先前句子相似度 ≥ 0.85 即判重复。
        全书占比 = 重复句总数 / 总句数。O(章内 n²)，单章句数有限、耗时毫秒级。"""
        total = 0
        repeated = 0
        by_chapter: list[dict[str, Any]] = []
        for f, text in iter_chapter_texts(self.project_dir):
            sents = self._split_sentences(text)
            if not sents:
                continue
            rep = 0
            seen: list[str] = []
            for s in sents:
                if any(self._sent_sim(s, t) >= self._REPEAT_SIM_THRESHOLD for t in seen):
                    rep += 1
                seen.append(s)
            total += len(sents)
            repeated += rep
            by_chapter.append({"chapter": f.stem, "total": len(sents), "repeated": rep})
        ratio = (repeated / total) if total else 0.0
        return ratio, {"chapters": len(by_chapter), "total_sentences": total,
                       "repeated_sentences": repeated, "by_chapter": by_chapter}

    def _metric_info_density(self) -> tuple[float, dict[str, Any]]:
        """确定性：推进句占比（软标红用）。启发式：含对话（引号/说/道/问…）或动作/位移/时间推进
        信号词的句子视为推进句。粗糙代理，仅作报告标注收集数据（拍板 #5，P1）。"""
        total = 0
        advancing = 0
        for _, text in iter_chapter_texts(self.project_dir):
            for s in self._split_sentences(text):
                total += 1
                if self._is_advancing_sentence(s):
                    advancing += 1
        ratio = (advancing / total) if total else 1.0
        return ratio, {"total_sentences": total, "advancing_sentences": advancing}

    def _is_advancing_sentence(self, s: str) -> bool:
        if any(q in s for q in ("「", "」", "“", "”", "『", "』")):
            return True
        if any(w in s for w in self._ADVANCE_WORDS):
            return True
        return any(w in s for w in self._SHIFT_WORDS)

    # ---------------------------------------------------------------- 单轮评测
