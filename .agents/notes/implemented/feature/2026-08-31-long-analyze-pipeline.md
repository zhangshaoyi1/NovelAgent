# Agent Note: M20 长篇拆文管线（story-long-analyze 移植）
Status: implemented

## Problem

NovelAgent 具备完整的「写书」能力，但缺少「拆书」能力：无法对爆款长篇原文做结构化深度拆解（概要、黄金三章、逐章摘要、聚合分析、设定与角色关系、汇总报告），导致对标书分析依赖人工或外部工具。oh-story-claudecod 的 `story-long-analyze` skill 提供 6 阶段拆解管道，但它是面向 Claude Code 的交互式 skill，NovelAgent 需要落为可复用、可断点续跑的 CLI 工作流。

## Decision

新增 `M20AnalyzeWorkflow`（`workflows/m20_analyze.py`）+ `analyze` 命令（`cli/commands/analyze.py`），6 阶段深度拆解管道：

- **Stage 0 概要提取** → `概要.md` + `章节/章节索引.md`
- **Stage 1 黄金三章** → `章节/第N章_深度拆解.md` ×3 + `快速预览.md`（**停靠点**）
- **Stage 2 逐章摘要** → `章节/第N章_摘要.md`（串行循环，单章失败容忍）
- **Stage 3 聚合分析** → `剧情/故事线.md` + `剧情/{标题}.md` + `剧情/散落情节.md`（含轻量质量门控：覆盖率/置信度/孤立比例写入进度）
- **Stage 4 设定+角色关系** → `设定/*.md` + `角色/*.md` + `角色/角色关系.md`
- **Stage 5 汇总报告** → `拆文报告.md`

产物落盘 `{project_dir}/deconstruction/{book}/`（含 `原文/` 备份 + `_progress.md`）。

关键设计：
- **断点恢复**：`_progress.md` 记录阶段状态/已完成章节/失败记录表；`final_status=paused_after_stage1` 停靠续跑（续跑跳过 Stage 0/1 从 Stage 2 开始）；无停靠询问的 `--full` 一次跑完。
- **原文备份前置**：`--source` 复制到 `原文/`；无 `--source` 时要求已有备份。
- **章节切分**：按「第N章/回/卷」标题正则切分（中文/阿拉伯数字混合），无标题时整体视为单章。
- **部分失败容忍**：单章/单阶段失败记入失败记录表，不阻断管道；最终状态 `completed_with_errors`。
- **所有 LLM 调用走 `agent.client.LLMClient`**，提示词走 `prompt_manager.pm.get("m20.xxx")`，LLM 事件经 `wire_llm_event_hook` 接线。
- **架构约束**：只新增文件（workflow/命令/prompts/测试/Agent Note），依赖方向 base → client → core → agents → workflows，未改动任何既有文件。

## Alternatives considered

### Why not 逐章并行 spawn chapter-extractor 子代理？
Story-long-analyze 原版 Stage 2 并行 spawn 子代理。NovelAgent 是 CLI 工具，无子代理运行时；故改为串行循环 + 逐章保存 + 部分失败容忍，正确性与原版一致，仅速度略慢。

### Why not 复用 import-draft / m11_import？
评估过复用 `m11_import` 的 prompts 与存储（曾作为拆文需求方案）。但拆文的输出契约（深度拆解/摘要/剧情聚合/角色档案）与导入草稿差异大，耦合会带来理解负担，故独立实现 M20 并复用公共 `LLMClient`/`pm`/`wire_llm_event_hook` 基建。

## Consequences

- 收益：NovelAgent 获得对标书拆解能力，输出与写作侧（world.md/outline.md/characters/）同构，可直接反哺写书。
- 成本：新增 7 份提示词 + 1 workflow + 1 命令 + 1 测试文件；长书（>200 章）Stage 2 逐章 LLM 调用量大，需 `--full` + 断点续跑分批执行。
- 约束：输出目录 `deconstruction/` 为项目内产物，遵循「不在 core/ 根建文件」原则。
