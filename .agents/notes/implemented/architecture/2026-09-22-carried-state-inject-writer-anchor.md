# Agent Note: 承接= 注入写手端（CARRIED_TO_WRITER），闭合越级硬约束

Status: implemented

## Problem

承接=（章首权威状态）此前只供给**评委/落盘两端**（见同族 note
`2026-09-21-carried-state-three-end-supply.md` 决策点 4「写手端保持不变」）。该设计的假设是：
写手端已有 `continuity_projection` 注入承接状态，无需在 `design_brief` 重复。

该假设在长篇小说**爆级场景**被证伪。实弹（灵荒工坊 ch25）：
写手拿到 `continuity_projection` 是一份**有界的紧凑事实投影**，但缺少对「境界不得无机制暴涨、
功法规则不得无登记升级」这类**硬约束的显式锚点**。于是 ch25 一边按旧台账自创，一边产出了：
境界从 **引灵中期 → 淳真期初期**越级、五行吞噬诀规则**无登记升级**（三次→五次/全属性50%）、
并编造「越阶吞噬赵天霸」事件——全部与承接应为的「连续演进」相悖，直接触发批末体检 ESCALATED。

根因不是「评委看不到承接」有误，而是**写手端缺少一处把承接=上升为『连续演进 + 禁止无机制越级』
硬约束的文字锚**：软性事实投影够宽，但不够「硬」。

## Decision

三端供给中，把承接=（`opening_state`）**双向硬化到写手端**：

1. `render_for_writer()` 在区块**最前**（`chapter_intent` 之前）注入 `CARRIED_TO_WRITER` 锚 +
   `self.opening_state`。措辞锁死两条：
   - **连续演进**：境界/功法规则/关系一律从承接值连续演进，禁止无契机的跳变；
   - **规则升级须登记**：任何功法升级（次数/属性/境界上限）必须由本章**已发生的触发源**支撑，
     否则不得升级——这与 `ledger_delta_producer`「增量必须标注来源」同构，只是前置到写手生成期。
2. 复用 SSOT（`opening_state` 与 judge/persist 三端同源同字段，`_render_opening_state` 装配），
   不再为写手另开投影通路，避免第三份真源漂移。
3. 承接为空（老项目/账本缺失）→ 保持 inject 空块、逐字等价改造前行为（`degrade` 纪律）。

这是一次**承接=的全量收口**：写手端也从「软投影」升级为「承接为先 + 越级禁行」的硬地基，
与评审/落盘两端的判据主体真正对齐，而非三端各握一块。

## Alternatives considered

### Why not 让 judge/persist 两端回归"软提示"，与写手保持对称？
反方向，等于放弃已获得的评委/落盘判据精度，纯粹迁就写手自由度——正是本次事故要封堵的口子。

### Why not 新增一个独立的「境界/RULES 冻结清单」注入写手？
会引入第四份真源（项目已有 setting_canon + ledger 两套正典，已足够复杂）。越级根因不在「没有清单」，
而在「承接=没有被声明为硬约束」——在原通道上加强制语义即可，避免再建一套平行系统。

### Why not 仅靠落盘端 `_archive_chapter` 的确定性 realm 覆盖兜底？
`_archive_chapter` 只在**事后**把确定性 realm 结入账本，覆盖不到 LLM 自造的 `field`（如「境界/修为/
淳真期引导能力」这类自定义字段不在此抽取器视野内）。要阻止越级，必须在**生成期**给写手硬约束。

## Consequences

- **写手端越级硬约束闭合**：ch26 起，写手在生成期即被承接=锚定「境界不动到引灵中期、规则保持三次/
  单一/低于一阶」，与评委判据同源；爆级事故的架构诱因（写手无承接硬锚）被移除。
- **三端供给行为对齐**：render_for_writer/judge/persist 三端均携带承接=（写手最前、评委最前、
  落盘为结转核对基线），SSOT 单一，杜绝端间漂移。
- **历史路径零改动**：承接为空 → 注入空块，行为逐字等同改造前；无账本的旧项目不受影响。
- 测试：`test_design_intent_propagation.py` 将原「写手不重复承接」用例改为 `test_writer_render_
  injects_carried_anchor`（断言 `CARRIED_TO_WRITER` 必现、`CARRIED_TO_JUDGE/PERSIST` 不混入、
  锚在 `chapter_intent` 前、常量存在）；该文件 15 项测试全绿；`test_continuity_delta`/
  `test_continuity_ledger`/`test_design_intent_propagation` 合计 39 项通过。

## Risks

- **提示词收紧≠确定性保证**：LLM 对「禁止越级」的遵守是非确定性的。硬约束消除的是**架构缺口**
  （此前写手根本没有承接锚），不是模型 100% 服从。若仍爆级，逐一结账 + 账本复核（本批已完成）
  仍为兜底。本批据此把 ch021-025 账本污染清零、正文境界/日号回正，为 ch26 提供干净承接=起点。
- 此锚同时注入 judge/persist 的既有文案不得被删——三端锚各有职责，删除任一端会重开对应缝。