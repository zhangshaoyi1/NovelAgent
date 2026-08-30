# Agent Note:

Status: implemented

## Problem

成书长期由 LLM 批量生成后，普遍带有明显「AI 味」：过度工整、模板化句式（仿佛/一丝/缓缓/眼中闪过/嘴角勾起）、
万能状语、连续排比、心理描写告知化、公式化对话标签、结尾强行升华。此前系统只有一套历史能力
`AILikenessDetector`（四维加权打分，0-100 阈值），既没有可操作的分级（轻/中/重），也没有配套的改写手段，
无法在生产管线里把「检测 → 改写」闭环落地，导致 150+ 章的成书风格雷同、读者观感下降。

## Decision

在 `core/anti_ai/` 子包内实现「检测分级 + 分级改写」闭环（对齐 `story-deslop` skill 的 6 门禁 + 三遍法），
并用 CLI 暴露批量/单章操作：

* **`detector.py` 新增 `AIFlavorScanner`**：6 项客观指标（禁用词密度/连续排比/心理词占比/对话标签密度/
  平均段落句数/重复描写密度），按阈值映射轻/中/重；综合判定为「任一重→重；否则中度≥2 或禁用词密度单独达中→中；否则轻」。
  支持项目白名单 `.deslop-whitelist`（一行一词，跳过命中）。

* **`rewriter.py` 新增 `DeslopRewriter`**：`rewrite(text, level=auto)` 按分级执行——轻度走规则后处理
  （复用 `PostProcessor`，零 LLM 成本）；中/重度走 `LLMClient.chat_creative`（`max_tokens=8192`，
  prompt 用 `pm.get("m5.deslop")`），严格解析【修改记录】/【润色后全文】标记，失败降级返回原文绝不抛异常。

* **写章管线接线**：`agentic_write.py` 与 `m5_write_chapter.py` 在质量门禁通过后、落盘前新增 `_run_deslop`/`_maybe_deslop`
  （参数 `deslop_enabled=True`，`--no-deslop` 关闭）；发射 `deslop:{level}` 子阶段事件；改写失败降级原文。

* **Tool 暴露**：`builtins.py` 新增 `deslop_check` tool（agentic loop 内可按章节调用，含 `dry_run`）。

* **CLI 暴露**：`cli/commands/deslop.py` 新增 `deslop`（批量，`--scope` 支持 all/区间/列表，默认 dry-run，
  `--apply` 才写回并自动备份到 `.state/deslop_backups/`）与 `deslop-chapter`（单章，同语义），均 global 命令、含 `--json`。
  LLM 事件经 `wire_llm_event_hook` 接线进 `events.jsonl`。

## Alternatives considered

* A. 全用规则后处理（不调 LLM）：成本低但只能处理禁用词/固定句式，对中/重度「书面腔、过度解释」几乎无效，否决。
* B. 全用 LLM 重写：每章多一次长 token 调用，轻度章节性价比低、且会破坏已被门禁确认的原文，否决。
  分级处理（轻规则/中重 LLM）兼顾成本与效果，胜出。
* C. 放在 `post_processor.py` 内扩展：职责已定义为「规则后处理」，混入 LLM 改写会破坏单职责，故新建 `rewriter.py` 独立承担。
* D. CLI 直接改文件（无备份）：有破坏风险，统一在写回前备份原章到 `deslop_backups/`，与 `rewrite` 命令的备份惯例一致。

## Consequences

* 检测可操作化：dry-run 报告按章节给出 6 指标 + 轻/中/重分级 + 禁用词命中清单，用户先看报告再决定是否 `--apply`。
* 管线自动去 AI 味：新写章节在落盘前自动执行（默认开，可 `--no-deslop` 关闭），不新增外部依赖、不阻断写章。
* 验证中发现并修复 3 处检测器误报：① `_detect_repeat` 单字身体词（手/心/眼）导致每章重复密度爆表→改多字词+相邻段落同词交集；
  ② 显式排比把通用否定词「不是/也是」当排比引导词→仅保留强排比标记（有的/一边/时而/一会儿）；
  ③ 对话检测漏 ASCII 双引号导致 `dialogue_tag_density=1.0` 误判重度→扩充引号字符类。
* 154 章全量 dry-run：轻 102 / 中 47 / 重 5，分级分布合理；`--apply` 实测重度章节 LLM 改写成功且 frontmatter 保留、备份生成。
* LLM 改写属「表达层」变更：prompt 强约束只改怎么说不改说什么，但 LLM 输出仍需抽检，避免极端情况下改写引入表述偏差。
