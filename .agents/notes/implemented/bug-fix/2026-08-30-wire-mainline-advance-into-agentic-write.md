# Agent Note: 单章写章工作流接入主线推进决策 & 短章门禁按清理后口径计数
Status: implemented

## Problem

1. **主线推进失效**：从 Web `/api/run` 逐章推进走的是 `AgenticWriteWorkflow.run()`
   （`agentic_write.py`），但确定性支线推进决策 `decide_mainline_advance`
   只被接在批量流水线 `agentic_pipeline.py._maybe_advance_mainline` 上，单章写章路径从未调用。
   结果 `progress.current_subline` 恒为首条（S01），`mainline_visited` 只含一条，全书无论多少章都不切支线。
2. **短章漏判**：`_llm_quality_gate` 在清理前对原始 `text` 统计字数。模型「提示词回显 / 编辑批注 / 重复正文」
   会抬高原始字数，使 ch081 这类清理后仅 244 字的章节逃过下限门禁。

## Decision

1. 在 `AgenticWriteWorkflow` 增加 `_maybe_advance_mainline()`，与 `agentic_pipeline` 同源
   （每 `mainline_window` 章一次，调用 `workflows.mainline.decide_mainline_advance`），
   在 `run()` 内 `_guard()` 之后、`_load_context()` 之前调用，写 `current_subline` 与
   `mainline_visited` 并 `save()`，保证支线切换在上下文加载前落盘生效。异常降级不阻断写章（G3）。
   `AgenticWriteWorkflow.__init__` 新增 `mainline_window: int = 5`。
2. `_llm_quality_gate` 改先 `_clean_chapter_body` + `_dedup_repeated_chapter`（与落盘同口径）
   再统计字数并判 `min_len`，且 LLM 审稿 prompt 用清理后文本。

## Alternatives considered

### Why not 只靠 agentic_pipeline？
批量流水线覆盖不了 Web 逐章单章写路径，两者是并行的写章入口，必须在单章工作流内也生效。

### Why not 在 M5 里加同判断？
M5 与 Agentic 共用 `_load_context` 等落盘逻辑，但推进决策是「写章前置」动作，
归 AgenticWriteWorkflow（对外门禁/看板入口）更贴近职责边界，避免 M5 承担推进状态机职责。

## Consequences

- Web 逐章推进与批量流水线推进行为一致，均能按 outline 压力曲线/决策窗口自动切支线。
- 短章下限门禁与落盘字数统计口径统一，回显/批注/重复正文不再掩护过短章节。
- 行为变化从下一章起生效；已写 134 章不追溯改写。