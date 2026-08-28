# quality/ — 质量保证层

## 职责
小说质量相关的校验、门禁、评分和反馈重写。

## 包含文件
| 文件 | 职责 |
|------|------|
| `confirmation.py` | 架构确认门禁（is_architecture_confirmed, confirm_architecture） |
| `consistency_checker.py` | 一致性检查器（CheckTrigger, ConsistencyChecker, Severity） |
| `feedback_rewriter.py` | 反馈重写器（FeedbackRewriter） |
| `guardrails.py` | 护栏检查（GuardrailConfig, GuardrailChecker, save_fingerprints） |
| `quality_checker.py` | 质量检查器（QualityChecker, LLMBackedChecker） |
| `reader_appeal.py` | 读者吸引力评分（ReaderAppealScorer, 六维评分） |

## 依赖规则
- 依赖 base/、client/、story/
- 不依赖 engine/、registry/ 等

## 被依赖
- workflows/ (M5 写章、M14 架构、agentic_pipeline)
- service/ (AgentService)
- agents/ (EditorAgent, EvaluatorAgent)