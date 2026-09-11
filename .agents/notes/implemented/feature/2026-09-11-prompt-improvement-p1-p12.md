# Agent Note: 提示词改进 P-1~P-12 落地（质检三对照 + 输出精简 + 情绪节奏）
Status: implemented

## Problem

承接 2026-09-05《竞品提示词优化方案 P1+P2》笔记的后续批次。本次 P-1~P-12 中除 P-13 外全部落地，
目标：质检输出瘦身、质检与写前共享教训、新增硬约束/情节点/事实卡三类对照、反 AI 味细则、章内情绪
节奏。**提示词全部在 `src/agent/prompts/m5/` 单一真源**，代码侧只是把新变量透传进两条质检路径。

## Decision

**第一批（commit `fd524f6`）**：
- P-10 质检输出精简：`quality_check.md` 输出 schema 的 rules **只列不通过的规则**（通过项省略，
  默认 pass），每章省约 30-40% 质检 token（质检+修订循环会放大），解析侧天然兼容（缺失=通过）。
- P-1 教训注入完整化：`agentic_write._lessons_focus()`（复用 `load_eval_lessons_text`，截断 400 字，
  失败降级为空）把上轮体检教训填进质检 prompt 的 `recheck_focus`，使教训不仅写前注入、质检复查同样聚焦。

**第二批（commit `515d118`）**：
- P-4 新增质检规则 11 `hard_constraint`：校验角色硬约束未被违背，数据源 `ctx["character_constraints"]`，
  缺省为空跳过规则。
- P-8 新增规则 12 `plot_coverage`：校验本章覆盖/推进细纲情节点，数据源 `ctx["plot_points"]`，完全不
  体现即不通过。
- P-12 `deslop.md` 新增「AI 味指纹判别细则」：叠加式描写 / 章末总结体 / 段落密度 / 万能动词腔 /
  解释性对话 / 重复信息三连 + 短句优先表，原则是**识别比改写更重要**（给模型判别清单而非代写）。

**第三批（commit `8c6f198`）**：
- P-9 事实对照卡：强化 `quality_check` 规则 6，要求与【本章事实对照卡】逐条对照，任一不一致即不通过；
  事实卡 = 连续性账本投影（`project_to_text` 已有事实清单格式，零新逻辑），双质检路径透传且截断 ≤800 字，
  缺省为空时退回自觉对照；与 P-4/P-8 组成「质检三大对照清单」（约束 / 情节点 / 事实）。
- P-11 章内情绪节奏：`generate.md` system 第 18 条——单章情绪要有小起伏（紧张后喘息 / 压抑后亮色 /
  争执后静默），日常章给一个情绪高点或低点，避免全程平铺或全程绷紧，起伏服务于本章目标情绪。

**双路径透传是要点**：`AgenticWriteWorkflow._llm_quality_gate` 与 `M5QualityGateMixin`
（`m5_quality_gate.py`）两条质检路径都要传 `hard_constraints` / `plot_points` / `fact_card` /
`recheck_focus`，只改一处会让废弃 M5 路径与生产 agentic 路径行为分叉——能力对账红线（第 2 份笔记）
守这道关。

## Alternatives considered

- **P-10 改为通过项也输出（带 pass 标记）**：翻倍质检 token 且解析方需改契约，省略即默认 pass 更省——
  否决冗余输出。
- **P-4/P-8/P-9 只在 agentic 路径加**：会让 M5 路径行为分叉、对账红线告警，故双路径同步透传。
- **情绪节奏用硬规则约束句式**：不可执行且误伤风格，P-11 仅作 system 指引（soft），不进确定性 guardrails。

## Consequences

- 写章/质检/CLI 相关用例全过（第一批 29 例、第二批 43 例、第三批 29 例）。
- 质检 token 下降（P-10）+ 三类对照使无效/矛盾章节更早被拦；情绪节奏为软指引，无阻断副作用。
- 事实对照卡依赖连续性账本已按章累积（见 G15 笔记），账本为空时 P-9 退回自觉对照、不报错。
- P-9/P-4/P-8 三张卡的数据源分别来自 `_archive_chapter` 账本、`ctx["plot_points"]`、
  `ctx["character_constraints"]`，任一为空时该规则自动降级而非报错。
- **P-13 未做**，不在本次范围，勿据本笔记推断其已落地。
- 相关 commit：`fd524f6` / `515d118` / `8c6f198`。

## 透传清单

- `hard_constraints` → `ctx["character_constraints"]`（P-4 规则 11）。
- `plot_points` → `ctx["plot_points"]`（P-8 规则 12）。
- `fact_card` → 连续性账本投影（P-9 规则 6），截断 ≤800 字。
- `recheck_focus` → `load_eval_lessons_text` 截断 400 字（P-1）。
- 以上四字段在 `_llm_quality_gate` 与 `M5QualityGateMixin` 必须同步传入，缺一则路径分叉。
