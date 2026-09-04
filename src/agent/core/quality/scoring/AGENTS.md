# AGENTS.md - quality/scoring/ 质量评分

## 职责

覆盖章节产出后的质量度量与门禁。

## 核心模块

| 文件 | 作用 |
|------|------|
| `quality_checker.py` | 通用+题材层规则校验与自动修正（QualityChecker / LLMBackedChecker） |
| `reader_appeal.py` | LLM 驱动的 6 维读者吸引力评分（ReaderAppealScorer）与章节/首章门禁 |

## 依赖规则

- 依赖 story、client（经 LLMClient 调用）
- 不依赖其他 sibling 子包