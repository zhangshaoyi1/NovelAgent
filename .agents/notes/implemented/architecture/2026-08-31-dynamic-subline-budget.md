# Agent Note: 由 LLM 主编动态规划分线预算（替代静态 ratio / 均衡分账）

Status: implemented

## Problem

阶段比值（前期/中期/后期）与各支线的章数预算此前是「静态写死（`mainline init --ratio 40:35:25`，
仅作为意图元数据占位）」或「按体量均衡分账（`expected_chapters / n_sublines`）」。
这与用户对主编角色的预期不符：比值应当依据**具体小说内容**动态规划，由 LLM 决策，
而不是被 CLI 拍死或机械均分。

`MainlineOrchestrator` 坚持确定性推进（G8 拍板 1，零 LLM），因此「谁来定预算」与
「预算如何被执行」必须解耦。

## Decision

* 新增 `workflows/budget_planner.py::BudgetPlanner`：LLM 主编预算规划器，走统一入口
  `LLMClient.chat_structured`（strict 结构化 schema），读取当前进度、各支线主题、全书目标
  章数与 `phase_ratio` 软意图，动态产出各支线章数预算，写回 `.state/mainline.json` 的
  `subline_share`。

* `MainlineOrchestrator` 注入可选的 `budget_planner`，新增 `replan_if_due()`：每 `replan_window`
  （默认 = mainline\_window）章先触发一次 LLM 重规划，随后 `maybe_advance()` 用更新后的
  `subline_share` 作确定性 cap 切线。未注入 / 未到窗口 / 异常 → 静默 False（G3）。

* 用户拍板取舍：

  * 时机：每 N 章定期重规划（随故事演进自适应）。

  * 失败降级：沿用上次 `subline_share`；无历史时按 `horizon_chapters / 支线数` 均衡分账。

  * `--ratio` 语义：保留为软意图提示（`phase_ratio`），仅作 LLM 参考输入，不再当硬预算。

* 写章入口 `agentic_write` / `agentic_pipeline` 的 `_maybe_advance_mainline` 统一改为
  「注入 BudgetPlanner → `replan_if_due()` → `maybe_advance()`」，两条路径行为一致。

## Alternatives considered

1. 把 LLM 规划直接塞进 `maybe_advance()` 内部 —— 破坏 orchestrator 的确定性零 LLM 语义，
   违反高类聚低耦合；否决，改为可注入 + 独立方法。
2. 彻底移除 `--ratio` —— 用户仍希望保留软意图供 LLM 参考；保留为提示（拍板 3）。
3. 规划失败不兜底走压力曲线 —— 用户选择「沿用上次」更稳（拍板 2）。

## Consequences

* 预算来源从静态/均分升级为 LLM 动态规划，各支线篇幅随叙事分量自适应，支线不再被某条无限拖长。

* `MainlineOrchestrator` 仍零 LLM、确定性执行，G8 拍板 1 语义不变；LLM 只影响「预算数值」，
  不介入「何时切」的判定。

* 每 replan\_window 章增加一次 LLM 调用；失败时零副作用（沿用现值/均分兜底），不阻断写章。

* **cap 与无数据保底硬切的优先级（2026-08-31 定稿）：`decide_mainline_advance`** **中
  cap 仅在存在压力曲线 / episode 区间数据时生效（取** **`min(多源上界, cap)`）；
  两源均缺失（无曲线的书）时 cap 不参与——保留「每 mainline\_window 章硬切」保底。
  否则缺数据的书会被大预算 cap（如** **`scope: long`** **的均衡分账 ≈54 章）拖住从不切换，
  与文档化退化语义相悖（G8 测试曾因该冲突整体失败，已修）。**

* **已知坑（已修，2026-08-30）：`_ask_llm`** **最初** **`max_tokens=2048`。V4 Flash 常在 JSON 尾部前先输出
  较长中文前言，2048 被前言吃光后 JSON 未被写出即被截断，`chat_structured`** **首/次两次** **`extract_json`
  均只得纯散文 →** **`plan()`** **静默返回 False（沿用上次预算）。症状：`subline_share`** **在多窗口推进后
  纹丝不动。修复：`max_tokens`** **提至 8192，验证** **`plan()`** **可成功产出新预算。**

* Agent Note / 代码同步提交。

