# Agent Note: M23 短篇扫榜 + 拆文（外部市场/作品分析）
Status: implemented

## Problem

项目具备长篇创作闭环，但缺少「短篇网文」的外部市场感知能力：不知道当前短篇市场在
追什么情绪/题材、风口与饱和点在哪，也没有对爆款短篇进行结构化拆解的方法。团队希望
移植 `oh-story-claudecod` 的 `story-short-scan`（扫榜）与 `story-short-analyze`（拆文）
两个 skill，为后续短篇创作提供外部输入。硬性约束：只新建文件、不得修改既有文件，
依赖方向 `base → client → core → agents → workflows`，LLM 统一走 `agent.client.LLMClient`。

## Decision

新增两套独立能力（全为新建文件），与项目既有 `m17_learn` 学习闭环明确隔离：

- **工作流** `src/agent/workflows/m23_short.py`
  - `@workflow("m23_short_scan")` `M23ShortScanWorkflow`：扫榜。输入榜单样本
    （`--input` 文件 / `--text`）或空（回退内置市场知识），LLM 输出情绪方向、题材候选、
    风险阈值、验证动作 → `ScanReport`。
  - `@workflow("m23_short_analyze")` `M23ShortAnalyzeWorkflow`：拆文。输入短篇正文，
    LLM 输出故事核/结构/情感曲线/爆点/反转/手法/共鸣 → `AnalyzeReport`。
  - 两类报告均提供 `to_dict()/to_json()/to_markdown()` 三种形态；JSON 解析失败
    降级返回空报告不抛错（LLM 不可用不阻断）；正文/榜单样本超长截断控制 token。
- **提示词** `src/agent/prompts/m23/short_scan.md` / `short_analyze.md`
  - 经 `PromptManager`（`pm.get("m23.short_scan")`）按 frontmatter 契约加载，
    `# system` / `# user` 分节 + Jinja2 渲染，`validation.json_valid` 走标准校验通道。
- **知识包** `src/agent/skills/short-story/`（SKILL.md + 5 个参考文件）
  - 扫榜加载 `real-market-data.md`；拆文加载 `output-templates.md` +
    `quality-checklist.md` + `zhihu-style.md` + `deconstruction-examples.md`。
- **CLI** `src/agent/cli/commands/short_scan.py` / `short_analyze.py`
  - `@command(global_=True)` 全局命令 `/short-scan`、`/short-analyze`，支持 `--json`
    信封（`success` + `report`/`error`）、`--env` 透传、`--save` 可选落盘
    `<dir>/.state/analyze/`；LLM 事件接线复用 `wire_llm_event_hook`。
- **测试** `tests/test_m23_short.py`（19 项离线，零真实 LLM，mock `LLMClient`）。
- **与 m17_learn 边界**：本功能是外部市场/作品分析，产物仅输出报告（可选保存到
  `.state/analyze/`），不写学习库 `learnings.json`；`learn` 命令负责写后沉淀技法。

## Alternatives considered

### Why not 直接复用 SkillRegistry（bookworm 式 skill 对象）?
bookworm 走 `SkillRegistry.load_builtin` + `Skill` 对象模式。但本功能是纯外部分析、
无项目内「skill 注册/门禁」语义，且团队约定新功能走 workflow + CLI 组合；故按
`workflows/m23_short.py` 承载逻辑，知识以 `skills/short-story/` 目录承载，二者解耦。

### Why not 把产物写入学习库（learnings.json）?
扫榜/拆文是外部输入，掺入项目「自己的技法沉淀」会污染学习库语义（m17_learn 负责
写后沉淀、写前注入）。故产物默认仅输出，可选落 `.state/analyze/`，与 learn 边界清晰。

### Why not 拆成单个 workflow + action 参数?
扫榜与拆文输入/输出契约差异大（榜单 vs 正文、市场报告 vs 作品拆解），合并会引入
分支复杂度和契约混乱；两个独立 `@workflow` 注册更清晰，各管一个职责。

## Consequences

- 收益：短篇市场扫榜与爆款拆解成为可复用的外部输入能力；降级策略保证 LLM 异常时
  命令不崩溃；报告双形态（JSON/Markdown）便于 CLI 与落盘复用。
- 代价：知识包为内置历史市场数据，实时性有限（提示词明确标注候选假设 + 复扫节点，
  由用户提供榜单样本时以样本为准）；新增 9 个文件需随代码同步维护。
