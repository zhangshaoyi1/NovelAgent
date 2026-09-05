# Agent Note: 竞品提示词优化方案 P1+P2 落地

Status: implemented

## Problem

《项目文档/竞品分析/提示词对比分析报告-全竞品.md》与《提示词优化推荐方案.md》评审确认的缺口：
评分提示词无区分度约束（LLM 打分塌缩 7-8 分）、部分评审提示词无原文证据锚点、续写无反重复
指令、多源上下文冲突无行为规范、伏笔回收无质量标准、反 AI 味缺确定性正则、字数下限不随节奏
档位区分、题材包无可执行规则字段。

## Decision

**P1 提示词（单一真源，全部在 `src/agent/prompts/`）**：
- `shared/score_calibration.md`：评分区分度约束的单一真源参考（prompt_manager 无 include
  机制，实际文案内联于 4 个评分提示词：`quality/reader_appeal_eval`、`m21/verdict`、
  `m_d/review`、`m23/short_analyze`；修改时须同步）。
- 举证话术：`m5/quality_check`（新增 quote 字段：不通过规则的证据须逐字摘自正文）、
  `m21/architect|reader|foreshadow`（证据约束 + 宁缺毋滥；foreshadow 另加"内心提及不算回收"）。
- `m5/generate` 第 16 条改写（三源事实论 + 冲突按"冻结设定>账本>大纲>RAG"优先级取权威源）、
  新增第 17 条（续写反重复：禁"书接上文"套话、禁重述已完成剧情）、伏笔任务区注入回收质量标准
  （可定位场景 ≥60 字、可观察动作，内心提及不算）。
- `m12/summary`：key_events 前缀 `[触发]/[转折]/[结果]`、foreshadows 前缀 `[埋]/[钩]` + 逐字
  keyword（8-25 字）、新增 `handoff` 字段（下一章最小事实集）。
- `m20/summary`：情节点粒度三档自检（太粗/太细/恰当）。`m3/outline`：大纲四检 + goal 可验证化。
  `m14/architecture`：目标可验证化 + 节奏表述量化。

**代码（配套接线，全部 additive）**：
- `workflows/evaluation/m12_audit.py`：`ChapterSummary.handoff` 字段（默认 ""，to_dict/to_markdown/
  _parse_summary 同步），旧摘要 JSON 无 handoff 时回空——向后兼容。
- `workflows/writing/m5_context.py`：`_load_prev_handoff` 读上一章 `_summaries/ch{N-1}.json` 的
  handoff，追加进 prev_chapter_summary（缺文件/坏 JSON 降级为空，不阻断）。
- `core/quality/guardrails/guardrails.py`：新增两类确定性检查（均 warn 级不阻断，可经
  `.state/guardrails.json` 配 `check_narrative_tell` / `check_density` /
  `max_paragraph_chars=300` / `max_sentence_chars=120` 关闭或调参）：
  - `narrative_tell`：元叙事旁白 / 分析报告腔 / 全场集体反应三类正则（对标 inkos
    post-write-validator；warn 而非 error——全禁是 inkos 误伤合法用法的反面教训）。
  - `paragraph_density`：超长自然段（>300 字）/ 超长单句（>120 字）；对话段（引号开头）豁免。
- `core/quality/scoring/quality_checker.py`：`resolve_min_cjk_words` 增加可选 `rhythm` 参数——
  仅在目标字数未知时按 `RHYTHM_MIN_CJK_WORDS`（舒缓/正常 3000、高速/高潮 2000）替代统一
  绝对下限；目标已知仍走 目标×0.8，不与用户设定冲突。`m5_quality_gate` 传入 ctx 的 rhythm。
- `core/registry/genre_pack.py`：`GenreManifest` 新增可选 `fatigue_words` / `pacing_rules`
  （SKILL.md frontmatter，缺省空列表——全部旧题材包不受影响）。试点：`skills/xiuxian/SKILL.md`。
- `core/continuity/delta.py`：模块 docstring 增加 LLM 生产者接线约定（期初取账本、增量注明
  来源、期末=期初+增量-消耗）——delta LLM 生产者尚未接线（差距计划 #5 后续点），接线时照用。

## Alternatives considered

- **prompt_manager 实现 include 机制**（替代 score_calibration 内联）：动核心加载器、影响
  全部 29 个键的渲染路径，收益仅一处去重——按推荐方案预案内联，shared/ 文件作为同步真源。
- **narrative_tell 设为 error**：inkos 全禁句式的误伤教训在前，维持 ai_flavor 同款 warn 拍板。
- **节奏档位下限无条件生效**：会与用户显式 chapter_length 冲突（如目标 2000 的舒缓章被强拉
  3000），改为仅目标未知时生效。

## Consequences

- 评分/评审提示词指令密度小幅上升（各 +2~4 行），换取区分度与证据可定位；advisory 层，
  不改变任何 validation 契约。
- guardrails 默认多出两类 warn 标红：advisory 模式（默认）不阻断写作；BLOCK 模式下 warn 仍
  不阻断（`passed` 仅看 error）。
- 旧项目 `_summaries/*.json` 无 handoff 字段时上下文注入自动降级，无迁移成本。
- 全量 pytest：1432 passed；17 个失败经 stash 前后对比确认为工作区存量问题（test_dashboard×8、
  test_g8_ending×4、test_g9×4、test_hook_dispatcher×1、redlines R5×1），与本变更无关。
