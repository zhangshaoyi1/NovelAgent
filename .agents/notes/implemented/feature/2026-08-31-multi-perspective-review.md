# Agent Note: 多视角对抗式成书质量评审（M21，移植 story-review skill）

Status: implemented

## Problem

NovelAgent 已有书虫测评（M15，聚焦标题/开头吸引力）与复核同步（M19，上游改动后查未覆盖/冲突），但缺少面向「成书整体质量」的评审：对已有章节做结构架构、设定一致性、读者市场吸引力、埋线与伏笔的多视角对抗式审查，并给出问题清单（severity=block/warn + 位置 + 建议）与总评。外部仓库 oh-story-claudecod 的 `story-review` skill 提供此能力（4 Agent 并行找问题 + 主线程综合裁决 + 平台 rubric 校准），需按 NovelAgent 分层约定移植。

## Decision

新增 M21 成书质量评审（只新建文件，不改任何既有文件，保持向后兼容）：

* `src/agent/workflows/m21_review.py`：`@workflow("m21_review")` 的 `M21ReviewWorkflow`。
  * `review(scope, mode, platform)` 入口，只读分析不改产物。
  * 4 个评审维度各一次 LLM 独立评审：`architect`（结构架构）/ `consistency`（设定一致性）/ `reader`（读者市场吸引力）/ `foreshadow`（埋线与伏笔），再综合裁决一次 LLM（`verdict`）。
  * `mode`：`full`（4 视角 + 1 裁决 = 5 次调用）/ `lean`（结构+一致性 + 1 裁决 = 3 次）/ `solo`（verdict 提示词单视角综合 = 1 次）。
  * `platform`：`fanqie` / `qidian` / `zhihu` 经 `pm.get("m21.<platform>").system` 注入对应 rubric；`general` 用内置 `GENERAL_RUBRIC`（quality-rubric.md 精简）。
  * 读取项目内容：`chapters/`（按 `scope`：`all` / `latest` / `1-10` / `1,3,5`）、`world.md`、`outline.md`、`architecture.md`、`characters/`，拼接后逐维度注入提示词。
  * 输出 `ReviewReport`（dataclass，`to_dict` / `to_json` / `to_markdown`），问题合并去重、block 优先；报告写 `{project_dir}/.state/review/review-{YYYYmmdd-HHMMSS}.md`。
  * 降级不阻断：维度/裁决 JSON 解析失败降级为 CONCERNS 空结果，绝不中断流程（对齐全局不变性 2）。

* `src/agent/cli/commands/review_book.py`：`@command(global_=True)`，参数 `--dir`（默认 `novels/my-novel`）/ `--scope`（`all`/`latest`/区间）/ `--mode`（`full`/`lean`/`solo`）/ `--platform`（`fanqie`/`qidian`/`zhihu`/`general`）/ `--json` / `--env`。复用 `enforce_gate` / `emit_result` / `make_quiet_console`，`--json` 输出 `{success, mode, platform, scope, overall_verdict, total_score, dimensions, issues, verdict_text, recommendations, disagreements, report_file}`。命令经 `commands/__init__.py` 的 glob 自动发现注册，无需改该文件。

* `src/agent/prompts/m21/`：`architect.md` / `consistency.md` / `reader.md` / `foreshadow.md` / `verdict.md`（frontmatter `name`/`version` + `# system`/`# user` 分节，结构化输出问题项列表 + severity），以及三份平台 rubric 参考 `fanqie.md` / `qidian.md` / `zhihu.md`（source rubrics 精简）。

* `tests/test_m21_review.py`：离线 mock `LLMClient`，覆盖 full/lean/solo 调用次数（5/3/1）、报告文件生成、scope 解析、rubric 注入、`--json` 输出结构、非法 mode/platform 报错、降级、workflow 注册、severity 规范化。

## Alternatives considered

* **完全复刻 4 子 Agent spawn（如 SKILL.md 的 Agent 工具并行）**：NovelAgent 无子 Agent spawn 运行时，改为工作流内对同一 `LLMClient` 顺序调用 4 个维度 + 1 个裁决，行为等价（独立视角 + 综合裁决）且可离线测试。
* **把 rubric 作为代码常量硬编码**：违反「提示词单一真源」约定，改为 `prompts/m21/*.md` 经 `pm` 加载，与 m15/m19 一致，可热重载、可版本管理。
* **solo 模式复用某个维度提示词而非 verdict**：solo 的「1 视角综合」语义更接近主线程裁决，故直接复用 `m21.verdict` 提示词做单视角综合评审（1 次调用），并把结果同时呈现为一个 `solo` 视角。
* **复用 `_parse_scope` 从 deslop 导入**：deslop 是 CLI 层既有模块，跨层复用引入不必要耦合，改为在工作流内实现等价 scope 解析（含 `latest` 特例）。

## Consequences

* 收益：获得面向成书整体的多视角对抗式评审能力（含平台 rubric 校准），报告落盘 `.state/review/` 可追溯，JSON/MD 双形态输出，全程只读不污染产物。
* 代价：每维度独立调用 LLM 成本随 mode 线性增长（full=5 次），默认仅 full/general；`solo` 为最低成本路径。评审质量依赖 LLM 输出，解析失败时降级为 CONCERNS 空结果（不误报也不阻断）。
* 门禁：`review-book` 为 global 命令，任意状态可用；无 `chapters/` 时退出码 1，工作流非法 mode/platform 抛 `ValueError`（CLI 捕获后以错误信封退出）。
