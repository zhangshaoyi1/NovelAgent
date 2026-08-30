# Agent Note: 不手动介入的消硬伤/破模板自动化闭环
Status: implemented

## Problem

已完成章节存在设定硬伤（血刀门主死而复生、林婉下落断裂、玉简/残魂/寂灭之眼冲突）、人物塑造矛盾（赵七人设三变、感情线与"苟道杀伐"取向冲突）、模板复用（6章"借刀杀人"、8次灭门回忆）。旧能力是点状工具（`FeedbackRewriter` 需人工逐章提反馈、`ConflictService` 只检单项新设定、`LearningStore` 只在人工重写后沉淀），缺一条不靠逐章手动介入的自动化闭环与写章防模板机制。

## Decision

三层约定（用户拍板）：① 事实型坏点自动重写落盘，取向型只进待拍板清单；② "关键一次拍板"落地为书级偏好文件 `repair/preferences.md`（草拟→作者确认一次→自动执行）；③ 破模板用写章运行时注入动态清单，只约束字数/花式不硬删。

新增模块（均入对应职责子包，无 core/ 根文件）：

- `core/quality/scan/bad_point_scanner.py`（A1）：双通道采集 `BadPoint`。
  - **静态通道**（零 LLM）：字数门禁（<1500 high / <3000 low）、灭门回忆计数（>3 报 self_repeat）、手段复用统计、编辑残留/标题占位。
  - **LLM 精扫通道**：一次 `chat_utility` 扫 ch001-020+设定，输出 `fact_conflict/plot_hole/character_drift/orientation`；首败追加「强制纯 JSON」重试一次；再败降级保留静态结果（不中断）。
- `core/quality/repair/repair_orchestrator.py`（A2→A4）：分层仲裁 + 批量重写 + 回归。
  - 分层：`FACT_TYPES={fact_conflict,plot_hole,character_drift}` 可自动改；`ORIENTATION_TYPES={orientation}` 只进 `pending_decisions.md`。
  - `preferences.md` 需含 `CONFIRMED_MARKER` 才视为已确认；未确认或 dry-run 一律不改正文。
  - 事实型执行：`SettingManager.create_snapshot("repair_before_rewrite")` 先快照 + `append_revision_log`，再逐章调 `FeedbackRewriter.rewrite(chapter, feedback=坏点.suggested_fix, gate_mode="advisory")`。
  - 回归（A4）：`_regress_check` 校验字数>=1500 且无编辑残留，否则记 `regress_failures`。
- `core/story/reuse_guard.py`（B1）：读前文章节自动统计手段复用 + 灭门回忆计数，生成动态注入文本；只约束不硬删；读失败→"" 降级。
- B1 注入点：`workflows/m5_write_chapter.py` `_build_context` 新增 `reuse_guard_text` key，在 system prompt 组装处与 learnings 同位注入。
- `cli/commands/repair.py`（A5）：`repair` 命令默认 dry-run；`--apply` 且偏好已确认才自动改；`--include-orientation` 强制连取向（慎用）；`--no-llm-scan` 禁 LLM；接入 tracer + `wire_llm_event_hook`（全入口统一）。

产物：`repair/bad_points.json`、`repair/pending_decisions.md`、`repair/preferences.md`。

## Alternatives considered

- **全自动含取向型**：感情线等取向问题也自动定版重写。落选：会改掉作者有意设定、不可逆。
- **只采集不停改**：坏点仅出报告、重写走人工 `rewrite`。落选：未达"不再手动介入"。
- **写章硬性拒绝模板**：命中重复手段强制重写/报错。落选：矫枉过正，改为只约束字数/花式。
- **偏好完全自动零确认**：落选：取向可能推断错，需一次确认兜底。

## Consequences

- 收益：事实型硬伤可无人值守自动修复（先快照可回滚），取向型集中一次拍板；写章时程序化感知手段复用防模板。
- 代价：静态规则是估算（关键词近似）；LLM 精扫依赖真实 API 连接，无连接时仅静态结果；agentic_write 写章路径未注入 reuse_guard（由 agent 内 WriterAgent 间接覆盖 learnings，记为已知边界）。
- 门禁：默认 advisory，重写越界仅告警；`repair --apply` 需偏好已确认，无确认不改任何正文。