# Agent Note: 不可放宽硬指标不得由单次 LLM 判定（判定抖动）
Status: proposed

## Problem

`setting_consistency_high` 是 `<= 0` 的**不可放宽硬指标**（`evaluator.py` 注释「=0 不可放宽」），
但它的取值来自**单次 LLM 调用**：`score_fn` = `ReaderAppealScorer(...).score`
（`agents/evaluator_metrics.py::_score` → `core/quality/scoring/reader_appeal.py`），
取样窗口 = `eval_window`（= 回滚窗口，默认末 5 章），且无多次采样、无判定缓存。

后果是**同一份稿件、同一份台账，判定值随批次漂移**，而硬指标一旦不达标，
`evaluator` 走「硬指标不达标（不可放宽）」分支 ⇒ 整轮 escalated（或触发回溯重写）。

**实证（2026-09-25 灵荒工坊，A/B 对照）**：内容零改动的前提下，对该维度采样 6 次：

| 采样 | 值 | issues |
|---|---|---|
| autowrite 批末评测 | 2.0 | 未留存 |
| 手测 1 | 3.0 | — |
| 手测 2 | 0.0 | 0 |
| 手测 3 / 4 / 5 | 0.0 / 0.0 / 0.0 | 0 |
| 手测 6 | 1.0 | 1 |

同一内容 0–3 抖动（`latency_ms≈22s`，`model='auto'`）。其中值 3.0 与 0.0 出现在
**相邻两次调用**上。判定器在返回 0.0 时给出的理由明确列出「五行吞噬诀第一层规则…
与设定台账和设计意图完全一致」——即**真值是 0，抖动来自判定器本身**。

派生影响（同一根因的三个出口）：
1. `eval_lessons` / `batch_directive` 会把一次抖动写成「必须优先修复的断层」，
   下一批据此去修一个并不存在的问题（本次已在 `batch_directive.json` 观察到实证）；
2. 值 3.0 的那次抓到的其实是 ch048 的「机会/株数」算术（真缺陷）——**有用信号与
   噪声混在同一次采样里**，无法区分；
3. 硬指标的抖动直接决定 `--max-rollback` 是否触发 ⇒ 随机回滚、白烧预算。

## Proposal

拟议三层去抖（可独立落地，按性价比排序）：

> **交付进度（2026-09-25）**
> - ❌ **第 1 项的「在 evaluator 层采样」实现已撤回**（曾提交 3 次采样取多数于
>   `agents/evaluator_metrics.py::_score`）。实测引入**生产级回归**：`validators.
>   _check_count_consistency` 要求 value 与证据 ``issues`` 条数一致，而多数值可能取自
>   **非最后一次**采样，``get_evidence`` 却只暴露最后一次的 issues
>   ⇒ ``COUNT_MISMATCH`` ⇒ 证据不可信 ⇒ ``RETRY_EVAL`` 停批上报。
>   实证：20260925021521 批出现「判定证据不可信/缓存碰撞」**13 次**，而此前两批均为 **0**。
> - ★ **正确层（下次实施的前提）**：去抖必须在**同时持有 value 与 evidence 的那一层**
>   （``core/quality/scoring/reader_appeal.ReaderAppealScorer.score``，其中 value 由该次
>   采样的 ``issues`` 重算、证据同源）完成——按多数选中的那次采样的 value **与** evidence
>   一起落库；在 evaluator 层二次加工数值必然造成 value/evidence 失配。
> - ✅ 同族问题在金三门禁侧的对应修复（估计量去偏 + 单维触底复核）**已交付且验证通过**，
>   见 `implemented/bug-fix/2026-09-25-golden-gate-estimator-bias.md`
>   （该路径无 value↔issues 一致性校验，故不受本条约束）。
> - ⬜ 第 2、3 项（内容哈希缓存 / 可确定性部分下沉）仍待定。

1. **多次采样取多数（针对不可放宽硬指标）**：`<= K` 且 K==0 的维度采样 N 次
   （建议 N=3），取**多数值**；N 次全不一致时取**最大值**（宁严勿宽）并标 `confidence<1`。
   门禁阈值不变，只改取数方式。
2. **按内容哈希缓存判定结果**：证据已是 `EvalEvidence(prompt_hash, response_hash)`，
   把命中同一个 prompt_hash 的判定值缓存（同批内 N 次采样即复用同一 hash），
   既省成本又保证同批内一致。
3. **可确定性化的部分下沉到确定性检查器**：如「量纲/次数算术」（本次 ch048 那类）
   属可判定项，应由规则检查器给出，不让 LLM 承担；LLM 只判无语义边界的部分。

## Alternatives considered

**Why not 直接放宽该硬指标（阈值 0 → 1）？**
硬指标的存在理由正是「设定被打破不可容忍」；放宽等于把判定器的噪声写进质量标准。

**Why not 换一个更稳的模型/降温度？**
治标且不可控：抖动来源不透明（本次同模型同参数即出现 0 与 3），换模型只把方差搬家，
且违背「LLM 调用统一入口」下按维度挑模型的维护成本。

**Why not 只在评测层做「抖动白名单」（同内容重复评测取最好）？**
等于用多次采样给门禁开后门（取最好 = 取最宽），与「不可放宽」的语义直接冲突。

## Acceptance criteria

- 同一稿件连续 5 次评测，`setting_consistency_high` 取值完全一致（或落在同一档）。
- 因该维度触发的 escalated 次数可归因到**具体 issue 文本**，而非仅一个计数。
- 单次评测的 LLM 调用量增幅 ≤ 3×（仅限硬指标维度），其余维度不重复采样。

## Risks

- 硬指标采样 N 次 ⇒ 单批成本上升（仅硬指标维度，按「取最大」策略不会放宽判定）。
- 缓存键若只用 prompt_hash，语料变更后需失效；需确认 `EvalEvidence` 的 hash 口径
  （是否含台账/窗口章号）后再定失效策略。
- 「取最大值」在 N 次全不一致时会偏严，可能引入新的误报；需配合 issue 文本留痕，
  便于人工复核。