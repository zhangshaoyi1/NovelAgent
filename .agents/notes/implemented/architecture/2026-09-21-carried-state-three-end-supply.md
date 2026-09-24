# Agent Note: 承接= 章首状态结转三端供给

Status: implemented

## Problem

长篇一致性的「章际承接」在**评委端断裂**。写手/落盘两端早已拿到承接状态：

- **写手端**（`m5_context`）：`continuity_projection`（`core/continuity.projection.project_to_text`）
  每章写前注入，含事实/信息差/未闭环/上一章章末交接。
- **落盘端**（`m5_persist` + `ledger_delta_producer`）：章尾 `ContinuityLedgerStore.commit` 结账，
  `_render_opening_state` 把期初投影喂给结算 LLM——承接的**写**与**读**都已存在。

唯独**批末体检评委**什么都拿不到：`_gather_for_eval` 喂给「设定一致/人设稳定/逻辑漏洞」的只有
被污染的正典（`setting_canon.render_for_prompt(limit=30)` 取最早最脏条目）与
路线级弧线（60–120 章一个节点，太粗）。评委判定一致性没有**真实的章际连续锚点**可对齐，
只能拿「旧台账」当标尺 ⇒ 正文明明按承接连续演进，却被判「与旧台账冲突」⇒ 死循环回退。

实弹（灵荒工坊, 2026-09-21）：`20260921152200-81d8b6.log` 显示 ch11-15 批末 ESCALATED，
`设定一致=2 / 人设稳定=3 / 迷·综合=53 / 追读力=72`。判据断裂而非质量断裂——正文按上一章结转的
境界/心性/五行单一属性规则演进，评委手里却没有上一章结转的权威状态。

方法论上这是「只设计不通知 / 只写不读」同族的又一实例：**承接只进生产链、不进判定链**。

并发事故（同批 ch15）：单稿 18145 字（超硬上限，target 2500），定向压缩一次 18145→2297——上限是
**事后压缩**型而非**生成期上限**型（`max_tokens` 档位 floor 可被 gateway 抬升），且压缩目标用
合理上限 ×1.2 把稿子打向"贴着下限"，产出残章。

## Decision

把「承接=」作为设计产出的一部分，经既有**单一装配点** `core/story/design_brief.py` 供给到评委/落盘两端
（写手端保持既有 `continuity_projection`，不重复注入）：

1. `DesignBrief` 新增 `opening_state` 字段。装配由新函数 `_render_opening_state(project_dir)` 完成：
   只读 `core/continuity` 账本，`project()` + `project_to_text()` 组装有界投影——与写手端
   `continuity_projection` **同源同字段**，消除"评委自抽一套"的漂移；无账本/失败 → 空串，经
   `degrade("design_brief.carry")` 显性化，绝不阻断评分。
2. `render_for_judge()` 把承接=**置于最前**（对齐起点最不易被预算挤出），附 `CARRIED_TO_JUDGE`
   判定前提：「沿承接状态连续演进 ≠ 前后矛盾/设定冲突；只有与承接状态**冲突或断裂**才计 issue；
   承接为空时回落设定台账/角色档案」——不放松门槛，只补全判据起点。
3. `render_for_persist()` 附 `CARRIED_TO_PERSIST` 结转核对基线（落盘端已有 Continuity 结账，
   此块为兜底"只把正文实际发生的变化结账"的核对清单）。
4. 写手端**不变**（`render_for_writer` 不注入承接=，防与 `continuity_projection` 双份）。
5. `WriterAgent._normalize_overlong` 压缩目标由 `resolve_max_cjk_words`（×1.2）改为**目标中值**，
   让压缩稿落到目标区间中心附近，避免 18145→2297 这类"白烧巨型 draft + 产出贴底残章"。
6. `core/infra/degrade_registry.py` 补登记 `design_brief.carry`（G2 降级契约）。

## Alternatives considered

### Why not 给写手的 continuity_projection 也改走 design_brief？
写手端该注入已被 `agentic_write` 消费且经既有测试锁定，改动纯属重构、无行为收益；承接= 的真实缺口
在评委端，最小改动即可闭合。故写手端保持现状，只收口评委/落盘两端到 SSOT。

### Why not 新建立一个独立的 `承接=` 账本/快照文件？
项目已有 `ContinuityLedgerStore` 承担「章际结转」的持久化与投影，另起炉灶=第三份真源，
必然与其他两端漂移。承接=NULL 该由账本缺失表达，而非新建存储。

### Why not 把承接=注入评分器私有上下文而非 design_brief？
`reader_appeal._design_facts` 已缓存并统一经 `render_for_judge` 注入全部维度，在 design_brief
装配可复用缓存 + 有界 + 降级纪律，避免在评分器再造一条抽取通路。

## Consequences

- **评委端历史性缺口闭合**：设定一致/人设稳定/逻辑漏洞拿到"上一章结转的权威章首状态"做对齐起点，
  正典污染的误判源头（对旧台账）被承接=取代；判据更精准（误报降、漏报不升）。
- **降级护栏**：空账本/老项目 → 承接=空串 → 评委行为逐字等同改造前（纪律 #4，历史路径零改动）；
  新 degrade `design_brief.carry` 已登记，违约契约测试通过。
- **跨包依赖**：`core.story` 延迟导入 `core.continuity`（函数内、非顶部），规避 import-time 循环，
  架构 redline 测试全绿。
- **篇幅**：压缩目标对齐中值，18145 型事故压出的章节贴近目标而非贴底；但生成端仍是
  "事后压缩"而非"生成期上限"——`max_tokens` 档位抬升这一放大器未在本次根治（见风险）。
- 测试：`test_design_intent_propagation.py` 新增 5 条承接= redline；`test_degrade_contract`、
  writer 测试全绿，架构目录 251+ passed。

### 追加：生成期收紧（2026-09-21，回应用户"为什么不是生成约束而是事后解决"）

问题重捡：单稿 18145 字即**生成期失控**，随后硬上限压缩收口——即使在写手提示词有
「目标×0.8~1.2」软要求的情况下。取证：配置层 floor 未设（`auto` profile 无 `max_tokens`、
`.env` 无 `LLM_MAX_TOKENS`），故放大器不在 gateway 抬升，而在**模型单次吐字密度**——
生成调用本身可一次吐出远超目标的正文。

决策（生成期收紧，与既有落盘期压缩**双保险**，非二选一）：
1. `_WRITER_TAIL` 第 16 条由「不宜过度注水超过上限」升级为显式说明"超上限会被压缩、浪费算力"，
   把上限从"可忽略的软性提醒"变为"有明确后果的约束"。
2. 动态注入的第 17 条由**只自检下限**升级为**上下限公共硬约束**：提交前**必须调用 `count_words`
   工具**自检实际字数；不足下限 → 补充情节点扩写；**超出上限 → 就地删减冗余描写/注水，保留全部
   情节/线索/章尾钩子，压回区间再 commit，重复修剪至达标**。
   纪律 27（「注入了」必须行为级断言）本已存在 `count_words` 工具注册（`core/tools/builtins.py:111`）
   且写手工具协议早已声明可用，提示词调用它合法。
3. 既有 `_normalize_overlong`（落盘期硬上限压缩）保留不动——**提示词是软防线，落盘压缩是硬防线**；
   提示词升级只是让模型尽量一次到位、减少被迫压缩的频率与算力浪费（根因：让模型在可读目标附近生成）。

影响：`test_m3_pace_relaxed_rules.py` 第 16 条快照同步更新；`test_writer_prompt_and_topup`/
`test_m3_pace_relaxed_rules`/`test_design_intent_propagation` 全绿（54 passed）。

## Risks

- **提示词收紧≠确定性保证**：LLM 对「必须自检」的遵守是非确定性的，超上限时它可能仍不调用
  `count_words` 或调用后仍不修剪。故**不能**把生成期收紧当作唯一防线——落盘期 `_normalize_overlong`
  仍是唯一权威兜底。这是有意设计（生成期软、落盘期硬），非遗漏。
- 校准风险（纪律 17/18）：未对真实语料做"超上限达成率"体检即上调限制强度——但本次未新增任何
  拒绝/回退判据，仅改提示词文案，无破坏性阈值，风险低。若后续要把它变闸门，必须先体检。
- 承接=新增判定前提，防"过度免罪"：文案已钉死「只有冲突/断裂才计 issue」+ 空回落，不放松门槛。