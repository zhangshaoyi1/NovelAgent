# Agent Note: G15 借鉴 DeepWrite —— 长篇一致性账本 + 确定性伏笔状态机 + 边界不变式 + 技法治化 + 用量汇总
Status: implemented

## Problem

NovelAgent 的长篇一致性与情节纪律依赖「写后检查 / 提示词前章结尾」，缺乏结构化语义账本；伏笔是扁平 4 态，状态靠 LLM 主观断言，易漏回收、误标；DD/时序在闪回/转述时容易漂移；结构化输出脏数据在进入领域层后才暴露，且缺少统一入口校验；技法学习不可复用；绕行 DeepWrite（`long-ledger` / `plot.ts` / CLvm）的核心工程点没有落到 NovelAgent。

## Decision

对标 DeepWrite 非 UI 工程点，新增/增强以下模块（全部依赖 base → client → core → workflows 单向；新增文件不落在 `core/` 根目录）：

- **`core/continuity/` 子包**（`models` / `ledger` / `projection` / `derive`，仅依赖 base）：结构化语义事实 `ContinuityFact`（按 `(domain, subject_id, field)` 唯一）+ 信息差 `Knowledge` + 未闭环 `OpenLoop` + 章交接 `Handoff`。`ContinuityLedgerStore.commit` 按章原子归档（tmp+replace），损坏降级为空账本。`project/project_to_text` 产出**有界去重**的写前投影（覆盖后只剩最终态，注入写章提示词）。`derive.py` 用纯函数推导 open_loop 状态与逾期。
- **`core/story/foresight.py`（ForesightThread+ForesightBeat + ForesightStore + derive_status/mark_committed）**：伏笔从扁平 4 态升级为 thread+beats 生命周期，`derive_status` 是**纯函数**，状态由「已 committed 的 beat 类型」决定（不靠 LLM 断言）。与 M13 扁平表格**并存**（不替换）。
- **`core/story/timeline.py`**：真实时间线 `StoryEvent` 与叙事呈现 `NarrativePlacement`（flashback/retelling/disclosure）解耦，闪回/转述不污染本体时序。
- **`core/base/validation.py`**：`validate_model / validate_many` 统一边界入口，先于领域层拒绝脏数据（空值、非法枚举、唯一性、引用一致性、状态机合法性），绝不透传。
- **`core/llmops/usage_reporter.py`**：复用既有 TraceStore，按 run + sub-agent 汇总 token 用量（aggregate / snapshot / diff）。
- **`workflows/m17_learn.py` + `core/story/technique_store.py` + `skills/learning-imitation/`**：三阶段（拆素材/剧情/文风）+ 六槽输出 + 预览-确认工作流。
- **写章 hook（`workflows/m5_write_chapter.py`）**：章前注入有界投影（退化空则跳过）；`_archive_chapter` 章后归档——写最小交接进账本 + 把 `anchor_chapter==本章` 的伏笔 beat 标记 committed，全部 `try/except`，**降级不阻断**。
- **`cli/commands/continuity.py`**：`continuity show / import-foreshadow / repair`（只读适配层 + status 纠偏）。
- **新增 4 份测试**：`test_continuity_ledger.py` / `test_foresight_state_machine.py` / `test_timeline.py` / `test_boundary_invariants.py`。

commit_id 统一用本章 ID（如 `ch001`），作为证据链锚。

## Alternatives considered

### Why not 用一张大 JSON 存全部事件，丢弃缓存？
全量历史会让写前投影无限膨胀。采用「当前视图」物化投影，facts 覆盖去重、有界，写手只收到最终态 + 最近交接 + 未闭环，体量可控。

### Why not 让 LLM 直接从章末交接文本推进数量？
交接文本无结构、不可查询、不可回溯。改为结构化 Handoff + evidence 证据链，可回滚、可投影。

### Why not 在既有 M13 扁平表格上改状态推导？
M13 表格是乔装面向用户的存在（命令/报告已依赖）。新增 `foresight.py` 作为并存确定性层，`import-foreshadow` 只读做适配，零回归 M13。

### Why not 在 Writer 本体里做归档？
「不改造 Writer」：只加 hook（对齐 `_maybe_advance_mainline` 位置），复用主循环，降级不阻断。

## Consequences

- 收益：长篇一致性从事后检查升级为「写前结构化账本 + 有界投影」；伏笔状态确定性可测试；时序双层解耦；脏数据结构化输出在入口被拒；技法资产可预览确认后沉淀；token 用量可追溯按 run/sub-agent 汇总。
- 成本：新增 `core/continuity/` 子包 + 多份测试；写章多一次账本落盘（最小 handoff，开销可忽略）。
- 约束：`core/continuity/` 仅依赖 base；新增文件不进 `core/` 根；与 M13 并存；缺账本一律降级不阻断写作。