"""质量评分子包（scoring/）

覆盖章节产出后的质量度量与门禁：
- 通用 + 题材层规则校验与自动修正（QualityChecker / LLMBackedChecker，quality_checker.py）
- LLM 驱动的 6 维读者吸引力评分（ReaderAppealScorer）与章节/首章门禁
  （gate_chapter / gate_first_chapters / is_pass，reader_appeal.py）

依赖规则：依赖 story、client（经 LLMClient 调用），不依赖其他 sibling 子包。
"""

from agent.core.quality.scoring.quality_checker import (
    LLMBackedChecker,
    QualityChecker,
    QualityReport,
    RuleLayer,
    Severity,
)
from agent.core.quality.scoring.reader_appeal import (
    APPEAL_DIMENSIONS,
    APPEAL_DIM_FLOOR,
    APPEAL_GATE_PREFIX,
    APPEAL_LABELS,
    APPEAL_PASS_LINE,
    ReaderAppealReport,
    ReaderAppealScorer,
    build_appeal_summary_lines,
    gate_chapter,
    gate_first_chapters,
    is_pass,
)

__all__ = [
    "QualityChecker",
    "RuleLayer",
    "QualityReport",
    "LLMBackedChecker",
    "Severity",
    "ReaderAppealScorer",
    "ReaderAppealReport",
    "build_appeal_summary_lines",
    "gate_chapter",
    "gate_first_chapters",
    "is_pass",
    "APPEAL_DIMENSIONS",
    "APPEAL_PASS_LINE",
    "APPEAL_DIM_FLOOR",
    "APPEAL_GATE_PREFIX",
    "APPEAL_LABELS",
]