# Agent Note: 好稿兜底落盘机制（Best-Draft Fallback）

Status: implemented

## Problem

WriterAgent 的修订循环存在「主观审稿误杀好稿 → 整章作废」的缺陷：

* LLM 九项审稿属主观门禁，会出现**篇幅达标且已接近合格**的草稿被反复否决的情况。

* 被否决后，模型继续按审稿意见改写，却因生成不稳定**退化为 stub 短稿**（6\~24 字，如「请提供前文信息」）。

* 旧逻辑在循环结束时直接以**最后一版草稿**返回；若最后一版是 stub，即便此前出现过错失的好稿，也会因 P0 全程 stub 守卫抛错或落盘残缺，导致「好稿丢失、整章作废」。ch234 即为此类事故（2928 字合格稿被否决后退化为 stub）。

## Decision

在 `WriterAgent.run()` 与 `run_async()` 统一引入**好稿兜底（Best-Draft Fallback）**：修订循环全程跟踪「篇幅达标且质量更优」的最佳草稿，作为落盘备选。

* 新增依赖：`writer_agent` → `agent.core.quality.scoring.quality_checker`（`_count_cjk` / `_chapter_length_from_ctx` / `resolve_min_cjk_words`）。方向与既有 `agents → core` 分层一致，未破坏单向依赖。

* 择优规则 `_keep_best(best, cand, min_len)`（优先级从高到低）：

  1. 篇幅达标者优先于不达标者（**stub 绝不让位**）；
  2. 同达标时字数更多者优先（更接近目标篇幅）；
  3. 字数相同时问题数更少者优先。

* 字数达标下限复用既有动态口径：`resolve_min_cjk_words(_chapter_length_from_ctx(ctx))`，未知目标时取绝对下限 1500；与质量门禁完全同源。

* 循环结束后若 `passed=False`：

  * 有篇幅达标的最佳稿 → **用最佳稿兜底落盘，并明确标记** **`passed=False`**（quality\_passed 保持未通过语义），打印黄色提示；

  * 全程无达标稿（全 stub）→ 仍抛 `RuntimeError` 放弃落盘（保留 P0 守卫语义）。

* 删除旧的 `_is_word_count_block`（其判定已由「`best_cjk < min_len`」直接替代，逻辑更稳定，不再依赖 issue description 文本匹配）。

## Alternatives considered

### Why not 保留旧「最后一版为准 + 文本匹配阻断」？

旧 `_is_word_count_block` 依赖审稿报告里 issue 的 `description` 文本（「过短 / 字数不足」）做启发式匹配，脆弱易漂移；且无论门禁是否通过都直接落盘最后一版，无法还原中途的好稿。替换为「篇幅达标的直接度量 + 择优缓存」更确定、更能实现「保留好稿」的初衷。

### Why not 显著放宽/移除 LLM 主观审稿门禁？

调低门禁阈值可减少误杀，但会牺牲质量基线，属于「削合格标尺迁就不稳定」，而非解决「好稿回退」本身。兜底机制在保留严格门禁的同时，为被误杀的好稿留了出路，风险更小。

## Consequences

* **收益**：好稿不再因一轮主观否决 + 模型退化而丢失；篇幅达标稿在门禁全不过时仍能落盘，仅标记未通过，供人工复核。

* **代价**：

  * 未通过时回落盘仍沿用最佳稿，需在 `_save_chapter` / 门禁链路确保 `quality_passed=False` 语义被正确透传与记录；

  * 笔数近似则优先更多字数的稿，极端情况下可能优先冗长版本（通过问题数并列与上限告警部分对冲）。

* `revision_attempts` 仍按最后一轮修订次数返回，兜底时未随回退稿的轮次修正（保持简单，不影响落盘判定）。

* 测试补强：`test_writer_auto_best_draft_fallback`（好稿回退）、`test_writer_auto_all_stub_raises`（全程 stub 仍抛错）、`test_writer_auto_caps_revisions`（篇幅达标 + 门禁全不过 → 修订封顶并返回最好稿）。

