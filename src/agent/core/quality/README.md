# quality/ — 质量保证层

## 职责
小说质量相关的校验、仲裁、门禁、评分和反馈重写。

## 子包结构（按关注点拆分）
| 子包 | 职责 | 关键符号 |
|------|------|----------|
| `guardrails/` | 内容与形式合规护栏、配置/指纹/全量去重扫描、架构门禁 | `Guardrails`, `build_guardrails`, `fullbook_dup_scan`, `GuardrailResult`, `GateMode`, `is_architecture_confirmed` |
| `consistency/` | 章节一致性校验与冲突仲裁 | `ConsistencyChecker`, `ConsistencyReport`, `CheckTrigger`, `Severity`, `ConflictArbiter`, `ConflictReport`, `Conflict` |
| `scoring/` | 通用质量评分（LLM 审查）与读者吸引力六维评分 | `QualityChecker`, `RuleLayer`, `QualityReport`, `LLMBackedChecker`, `ReaderAppealScorer`, `ReaderAppealReport`, `gate_chapter` |
| `rewrite/` | 基于反馈的章节重写 | `FeedbackRewriter`, `RewriteResult` |

## 依赖规则
- 依赖 base/、client/、story/
- 不依赖 infra/、engine/、registry/ 等

## 被依赖
- workflows/ (M5 写章、M12 审计、M14 架构、agentic_pipeline)
- cli/ (compose 体检、audit-setting)
- service/ (AgentService)
- agents/ (EditorAgent, EvaluatorAgent)
- tools/ (builtins 的 quality_check 工具，懒加载)

外部使用者统一从 `agent.core.quality` 顶层导入（如 `from agent.core.quality import Guardrails`），无需感知子包细节。