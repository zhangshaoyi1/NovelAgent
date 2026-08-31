# Agent Note: 项目脚手架（story-setup 移植）—— 写作基础设施纯文件部署

Status: implemented

## Problem

NovelAgent 只能从零走 `start → discuss → architecture → ...` 全流程项目初始化，但已有剧本/想用现成故事结构直接开写的用户没有一条「向已有项目目录部署写作基础设施」的轻量通道。Claude Code 生态的 oh-story-claudecod `story-setup` skill 提供了完整模板（CLAUDE.md / rules / agents / 上下文快照 / 部署钩子），值得按 NovelAgent 的架构约定移植为原生工作流 + CLI 命令。要求：只新建文件，禁止修改任何既有文件；纯文件部署、无需 LLM；测试必须离线。

## Decision

新增 M22 工作流与 `/setup` 全局命令，职责锁定为「写作基础设施部署」（非项目初始化，与 `/start` 严格分工）。全部为新建文件，未改动任何既有文件。

**文件拓扑（均入对应职责位置，无 core/ 根文件）**

* 模板：`src/agent/templates/scaffold/`
  * `CLAUDE.md.tmpl`、`上下文.md.tmpl`（含 `{项目名}/{书名}/{目标平台}/{作者名}` 占位符）
  * `rules/*.md`（4 个：story-consistency / story-format / story-narrative / story-outline）
  * `agents/*.md`（7 个：story-architect / character-designer / narrative-writer / consistency-checker / story-researcher / story-explorer / chapter-extractor）
* 工作流：`src/agent/workflows/m22_setup.py`（`@workflow("m22_setup")`，无 LLM）
* 命令：`src/agent/cli/commands/setup.py`（`@command(global_=True)`，参数 `--dir` 默认 `novels/my-novel` / `--book` / `--platform` 默认 起点 / `--author` 默认 作者 / `--force` / `--json`）
* 测试：`tests/test_m22_setup.py`（17 例，全离线）

**部署策略（三层分治）**

* `CLAUDE.md`：**按 `## ` section 合并**而非覆盖。`split_sections` 按 `## ` 标题切分，`merge_markdown_sections` 保留用户已有 section（同名不覆盖）、追加模板新增 section、用户 preamble 优先保留（用户无 preamble 才用模板 preamble）。`render_placeholders` 只替换四个命名占位符，空值保留原样（agents/rules 内泛用 `{...}` 叙事占位符不受影响）。
* `rules/*.md`：**合并**——目标已存在同名文件则保留（记入 preserved），缺失则复制。
* `agents/*.md`：**可覆盖**——始终用模板覆盖目标同名文件（story-setup 管理文件）。
* `上下文.md.tmpl`：目标存在 `{书名}/` 目录时复制到 `{书名}/追踪/上下文.md`，否则跳过并提示。
* 哨兵 `.story-deployed`：记录 `deployed_at` / `agents_version` / `setup_skill_version`；已部署（文件存在）时默认跳过并提示 `--force` 重新部署。无状态机转换（纯文件部署）。

**与 /start 的边界**：`/start` 负责项目初始化（收集标题/体量/题材/风格、LLM 生成世界观、渲染 world.md、驱动状态机 INIT→CONFIGURING）；`/setup` 只做写作基础设施文件部署，不生成故事内容、不驱动状态机。两者职责不重叠。

**实现期修复**：`merge_markdown_sections` 初版重建文档时仅拼接 body 丢弃 section 标题（测试首跑暴露），改为 `"".join(h + body for h, body in out)` 后 17 例全绿。

## Alternatives considered

* **整体覆盖目标 CLAUDE.md / rules**：直接复制模板覆盖用户文件。落选：会抹掉用户已有自定义规则与约定，非破坏性要求不满足。
* **接入 /start 内一条路径**：把部署并入现有项目初始化命令。落选：与「只新建文件、禁止改既有文件」冲突，且混淆「初始化」与「基础设施部署」两个职责。
* **复用 start.py 的项目目录/状态逻辑**：落选：setup 是全局命令、纯文件操作，不应受状态机约束；目标目录也未必是已初始化的 NovelAgent 项目。
* **LLM 生成部署内容**：落选：模板是确定性文件，无需也不应调 LLM（符合依赖方向 base→client→core→agents→workflows 且保持离线可测）。

## Consequences

* 收益：已有剧本/想独立部署基础设施的用户可 `novel-agent setup -d <dir> --book <书名>` 一键获得 CLAUDE.md/rules/agents/上下文模板/哨兵；CLAUDE.md 与 rules 非破坏合并，二次部署安全；纯文件零 LLM、17 例离线测试保障。
* 代价：`agents/*.md` 覆盖策略会静默覆盖同名 agent 文件（有哨兵 + `preserved_files` 报告可感知）；上下文模板仅在 `{书名}/` 目录存在时部署；`--dir` 默认值 `novels/my-novel` 与既有命令 `projects/my-novel` 惯例不一致（按交付规格要求，后续可统一）。
* 门禁：`/setup` 为全局命令，任意状态放行；项目未初始化（无 state.json）也放行（部署不依赖状态机）。
