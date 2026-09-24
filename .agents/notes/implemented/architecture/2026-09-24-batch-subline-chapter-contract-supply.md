# Agent Note: 批前逐章契约滚动供给（供给端补上「21 章之后」这一环）

Status: implemented

## Problem

autowrite 任务 `20260924090214-9ca0a6` 写到 ch45 后熔断 `escalated`。实弹（灵荒工坊）
的失败形态不是"某一章写坏了"，而是**整窗销毁-重写死循环**：回退 → 重写 → 评委仍判不
合格 → 再回退。回退输入一字不变，故必然再次失败。

根因在**供给端缺环**，链条如下：

1. `workflows/planning/m3_outline.py::_render_and_save_sublines` 是 subline.md
   逐章行（`第N章：…`）的**唯一写入方**，而它**只在 M3 首轮大纲生成时跑一次**；
2. `agents/planner.py::PlannerAgent.replan_batch`（批间复规划）只产 **arc 级**
   `ReplanOutput`，**从不写逐章行**；
3. ⇒ 首轮开篇窗口（20 章）之后，逐章契约**永久缺位**；
4. ⇒ 写手 `chapter_contract.select_chapter_lines` 取不到本章行，**回退到阶段级模板**
   （「铺垫阶段：章尾=日常小悬念」）⇒ 每章只剩阶段模板可依据 ⇒ 同质/注水；
5. ⇒ 评委判不合格 ⇒ 回退 → 重写时输入仍然只有阶段模板 ⇒ 死循环。

这与 `core/story/chapter_contract.py` 模块头记录的 09-18 P0（"章级意图缺位，
净推进 0 章"）**同源**，只是触发区间从"开篇之后"提前到了"21 章之后"——
09-18 的修复把供给做进了 M3，却没有**滚动供给**这一环。

门禁侧的证据链是自洽的：`plan_consistency.check_subline_plot_source` 判据 2
（窗口内无逐章行）按 C 方案降为「告警 + 计数台账」，所以它**一直在报警但不阻断**
（`.state/plan_gate_stage_level.jsonl` 的计数就是本缺陷的仪表），而告警的**收敛目标
（供给侧）此前并不存在** —— 有表针、无阀门。

## Decision

新增 `workflows/pipeline/subline_contract.py`，并在 `batch_replan.maybe_replan`
里与复规划**并列**挂载；口径与门禁**同源复用**。

1. **供给实现**（`subline_contract.ensure_window_contracts`）
   `ensure_window_contracts(project_dir, *, summary="", llm=None, console=None) -> list[str]`
   - **窗口**：`plan_consistency.write_window(project_dir)`（`cur+1 .. cur+20`）——
     公开薄包装，与门禁**同一实现**；
   - **范围**：支线区间与该窗口的交集（`plan_consistency.subline_range`），
     不相交即跳过（远期支线不因缺行被提前补）；
   - **缺口**：`plan_consistency.chapter_numbers`（**公开薄包装**，与门禁同一解析函数）
     取该文件已覆盖章号，与范围求差；**无缺口 ⇒ 零 LLM 调用**（幂等重跑的关键）；
   - **生成**：三小节（`章节钩子设计` / `章节强度档位` / `情节点序列`）各一组逐章行，
     字段名 `chapter_hooks` / `chapter_tiers` / `plot_points` 与 M3 `sublines[*]` **同构**；
     `max_tokens=12288`；首败追加「强制纯 JSON」指令重试一次（与写手/评委同策略）；
   - **合并**（`_merge_section`）：按章号**删旧行 + 追加新行**，写在原子替换
     （`f.name + ".tmp"` → `replace`）里落地 ⇒ **subline.md 仍是唯一真源**，
     历史章节行不被销毁（只替换同章号）。
2. **接线**（`batch_replan.maybe_replan`）
   - 批级摘要 `build_batch_summary` 提到两条链**之前**只装一次，复规划与补齐共用
     同一份进展口径（各装一次会让两条链看到不同的"实际进展"）；
   - 补齐**与复规划并列**（先后互不依赖）：复规划失败也必须补齐——二者是两条独立的
     批前增强链，而复规划**结构上不会**产出逐章行；
   - 两条链**各自** `degrade()` 留痕后继续写，都不阻断开工。
3. **降级可见度**：新命名空间 `autowire.subline_contract`（接线）、
   `batch_replan.summary`（摘要装配）、`subline_contract.read / generate / empty /
   partial / thin_section`（供给实现）全部登记进
   `core/infra/degrade_registry.py::DEGRADE_NAMESPACES`，满足架构红线
   `test_every_degrade_call_is_registered` / `test_no_silent_degrades` /
   `test_new_exemptions_must_cite_reason`。
   - `subline_contract.partial`：整体缺章（合并后仍有章号缺行）；
   - `subline_contract.thin_section`：**分小节**统计的缺章（`per_section`）——
     专为暴露"钩子全覆盖、情节点只到一半"这类**半供给**：它整体看是补齐成功的，
     但写手在 `plot_points` 上仍会回退阶段模板。
4. **辅因修复：M2 脉络讨论状态守卫**
   `agentic_pipeline_planning.py` 的 M2 段每次无条件跑 `M2Input`，在状态已推进到
   `WRITING` 时被状态机连拒 3 次（日志「M2 3 次失败：当前状态 WRITING 不允许讨论」）。
   改为：已有 `discussion.md` ⇒ 直接推进 `ARCHITECTING`；否则 `/discuss` 不被允许
   ⇒ 显式 `# noqa: SILENT_DEGRADE reason=expected-skip` 跳过 M2（该产物缺失不影响下游）；
   仅两者都不成立时才真正跑 M2。

## Alternatives considered

### Why not 让 `replan_batch` 直接产出逐章行？
职责越界：`replan_batch` 的契约是 **arc 级**规划（`ReplanOutput`，`max_tokens=3000`），
让它同时产逐章细纲会把 prompt/预算/测试面全部撑开；更致命的是**复规划失败即断供**——
而缺供才是本缺陷，把供给绑在一条可能失败的链上是重复犯同一个错。故并列而非串联。

### Why not 在门禁 `check_subline_plot_source` 内部直接改写 subline.md？
闸门失去独立性：闸门的价值在于"它能拒绝"，一旦它开始"顺手修补被审对象"，告警与计数
就永远不会有观察者，也无法回答"这次开工到底用的是补前的还是补后的供给"。
且门禁在 `workflows/pipeline/plan_consistency.py`，写文件会让它从"判据"变成"生产者"。

### Why not 新建独立文件（如 `subline_chapters.md`）承载滚动逐章行？
破坏单一真源。写手 `chapter_contract.select_chapter_lines`、评委 `design_brief`、
门禁 `plan_consistency` 三端都按**小节名**从 subline.md 取行；
换文件就要三端各加一条回退路径 ⇒ 三处解析分叉 ⇒ 纪律 #19/#20 的参照系错位。

### Why not 让补供给端自己算窗口 / 自己写正则数逐章行？
会出现"**补了行但门禁看不见**"的静默失真（自算窗口与门禁分叉、正则与
`chapter_contract.chapter_line_pattern` 分叉），白烧 token 且缺陷被掩盖。
故 `plan_consistency` 新增 `write_window` / `subline_range` / `chapter_numbers`
三个**公开薄包装**（行为零改动），作为补供给端与门禁的**唯一共用口径**。

### Why not 只用整文件计数判断"补齐是否完整"？
整文件计数会把"1-20 章有行、21-60 章没行"判成"有供给"。必须**窗口口径**（与门禁
判据 2 同口径），且必须**分小节**统计 —— 否则半供给（钩子全、情节点半）整体看是成功的。

## Consequences

- **死循环的两半都被打断**：写手每章都能取到"本章那一行"（不再是阶段模板），
  评委的判据也随之有了可对照的章级契约；回退重写时输入**变了**。
- **可下降的计数有了抓手**：`.state/plan_gate_stage_level.jsonl` 的计数在补齐后
  **停止增长**（实测：补前门禁报"窗口内没有逐章契约行"并写 total=1；补后返回 `[]`、
  无告警、计数仍为 1 —— 证明补上的行对门禁**可见**）。
- **幂等且省 token**：无缺口时**零 LLM 调用**，可被任何批前钩子反复调用；
  同章号旧行被替换而非追加，重跑不膨胀文件。
- **失败语义与复规划一致**：补齐失败 ⇒ `degrade()` + 黄字告警 + 沿用既有细纲继续写
  （写手仍有阶段级回退，不会因补齐失败而停工）；但绝不静默。
- **测试**：新增 `tests/test_subline_contract_supply.py` 7 项
  （只留区间内行 / 同章号替换且历史不销毁 / 缺小节则末尾新建 / **门禁与写手双可见**
  且计数停止增长 / 幂等且无缺口零 LLM / 失败降级且保留原文件 / 窗口外支线跳过 /
  复规划失败也补齐）；回归 `test_plan_consistency_chapter_level`、`test_batch_replan`、
  `test_g4_breaker`、`test_plan_review_reachability`、`test_plan_change_invalidation`、
  `test_phase2_ledgers` 全绿；`tests/architecture` 257 passed。
- **历史缺陷口径**：本修复对**历史作品同样生效**（下一次批前即会补上窗口内的逐章行），
  与 09-18 P0 的"供给侧真解"是同一条路的续段，不适用"只对新数据生效"。

## Risks

- **逐章行的质量依赖 LLM**：补齐是"有行"而非"行好"。若补齐内容与支线设定脱节，
  写手会从"阶段模板同质"变成"错误契约同质"。缓解：提示词注入支线目标/角色/冲突/
  约束/压力曲线 + **前文已定逐章行**（承接锚点）+ 实际写作进展摘要，并硬约束
  「承接前文与进展、在场白名单、禁模板词」；后续若实测质量不足，优先改提示词与
  补 `chapter_tiers` 的四档语义约束，而非改结构。
- **多支线交集**：窗口可能同时落在两条支线区间内（灵荒工坊当前 S01 覆盖 1-150，
  其余支线起始 151+，暂不冲突）。逐支线独立补齐，但**跨支线一致性无仲裁** ——
  若未来出现区间重叠，需在同窗口内确保出场角色/境界口径一致。
- **`partial` / `thin_section` 只降级不阻断**：这两条正是"半供给"的仪表，
  短期内允许存在（写手有阶段回退），但应在体检看板上被消费；若长期不下降，
  说明提示词侧的"每章一行 + 分小节齐全"约束不足。