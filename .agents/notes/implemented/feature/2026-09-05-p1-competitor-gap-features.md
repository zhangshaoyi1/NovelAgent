# Agent Note: P1 竞品差距落地——delta 结算 / 评审确认 / 段落重写 / 伏笔预警

Status: implemented

## Problem

`项目文档/竞品差距改进计划.md` P1 四项：

1. **P1-5**：账本 `commit()` 只接受已构造好的模型对象，LLM 结算输出缺少"结构化 delta 契约 + 严格校验应用器"——全量重写状态是破坏主因（对标 inkos Reflector 的 hookOps/currentStatePatch delta 结算）。
2. **P1-6**：`rewrite` 把自由语言反馈（用户输入或 AI 评审原文）直接拼进改写 prompt，评审语气与幻觉"事实"会污染改写方向（对标 AI-Novel-Writer "Raw AI review text must not become a model instruction"）。
3. **P1-7**：只想改一段时只能整章重写，token 浪费且有改动扩散风险（对标 MuMuAINovel partial-regenerate + diff 对比）。
4. **P1-8**：`foreshadow_report` 只有事后"逾期"标记，缺"提前 N 章预警"（对标 MuMuAINovel 紧急度 3 级 + remind_before_chapters）。

## Decision

1. **P1-5** `core/continuity/delta.py`（新增）：`LedgerDelta`（facts/knowledge/loop_ops/handoff）+ `LoopOp`（advance/resolve/defer/abandon），全部 ``extra="forbid"``（拒绝 LLM 幻觉字段）；`apply_ledger_delta` 纯内存应用（预检 loop 目标存在 → 杜绝半应用；终态不回退 → 幂等重放；证据链锚统一收口为本 commit）；`ContinuityLedgerStore.apply_delta` 端到端：校验 → 应用 → 不变式校验 → 原子落盘，失败回滚显式报错不落盘。delta 的 LLM 生产者（Editor 结算提取）为后续接线点，本期交付数据工程内核。
2. **P1-6** `core/quality/rewrite/instruction.py`（新增）：`structure_feedback` 确定性结构化（子句拆分 → 位置/类型归类 → 问题清单，零 LLM）+ `render_instruction` 标准修改指令块；`FeedbackRewriter.rewrite` 新增 `confirm_fn` 闸口——不确认零 LLM 调用、原章不动；确认后 prompt 注入渲染后的指令块而非原始反馈；`rewrite` CLI 在自主度 <50 且非 JSON 模式时默认要确认，`--yes` 跳过。
3. **P1-7** `core/quality/rewrite/paragraph_rewriter.py`（新增）+ CLI `rewrite_paragraph`（默认 `--plan` 离线预览，`--apply` 才调 LLM 落盘）：定位（1-based 序号或唯一片段，歧义/未命中报错）→ 仅携带前后各 1 段受限窗口 → 输出 unified diff → 确认 → 仅替换目标段落（标题行不计段落序号；落盘走 P0-3 原子写；备份同 `.state/rewrite_backups/`）。
4. **P1-8** `workflows/evaluation/m13_foreshadow.py`：模块级纯函数 `foreshadow_urgency`（normal/due/overdue 三级，窗口 `REMIND_BEFORE_CHAPTERS=3`）+ `ForeshadowStats.due` 计数 + `M13Report.due` 清单 + 报告"⏳ 即将到期伏笔"分节与预警建议；`m5_context._load_foreshadow_task` 注入"即将到期/已逾期"提醒行（上限 3 条防 prompt 膨胀；结局段分支不受影响）。

## Alternatives considered

- **P1-5 让 LLM 直接输出 ContinuityFact 列表并复用 `commit()`**：缺操作语义（resolve/defer 无法表达）与未知字段防线；独立 delta 层让 prompt 契约更小、校验更硬。
- **P1-6 用 LLM 做结构化抽取**：多一次调用与失败面；确定性规则对本场景够用（目标是去污染 + 可确认，不是完美 NLP）。
- **P1-7 复用 FeedbackRewriter 截取整章**：改动扩散风险依旧；独立受限窗口 prompt 把"只改一段"写进铁律。
- **P1-8 把 urgency 放进 `core/story/foresight.py`**：foresight 是 thread+beats 确定性状态机（另一套数据模型），M13 表格的紧急度放 M13 工作流内聚性更高；纯函数可被 foresight 侧后续复用。

## Consequences

- `rewrite` CLI 行为变化：自主度 <50 时新增交互确认（自动化脚本需加 `--yes`）；JSON 模式不确认（供 Web/自动化走结构化结果自行裁决）。
- `m5_context._load_foreshadow_task` 非结局段输出可能多出 ⏳/⚠ 提醒行：依赖其精确文本的测试需更新（现有 g8 结局段测试不受影响——结局分支提前 return）。
- 新 CLI 命令 `rewrite-paragraph`：`agent/cli/__init__.py` 补了向后兼容导出（与既有命令同模式）。
- 测试：`tests/test_continuity_delta.py`（10）、`tests/test_rewrite_instruction.py`（7）、`tests/test_paragraph_rewrite.py`（11）、`tests/test_foreshadow_urgency.py`（11）。
