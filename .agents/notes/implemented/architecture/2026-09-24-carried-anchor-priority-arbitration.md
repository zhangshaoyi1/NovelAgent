# Agent Note: 设计意图的粒度标注 + 承接锚优先级仲裁 + 境界跨度的确定性阻断

Status: implemented

## Problem

autowrite 任务 `20260924090214-9ca0a6` 写到 ch45 后熔断 `escalated`（exit_code=2）。
实弹（灵荒工坊）：**ch41 一章之内 引灵 → 淳真 连跳两境**，且该越级**落盘固化**，
污染连续性账本（`境界 = 淳真期初期` 结在 ch41），批末体检才判崩坏，回退无法收敛
（回退到 ch41 后连贯性反而从 75 掉到 1.0）。

三处**架构缺口**叠加，缺一不可：

1. **粒度混淆且无仲裁**（主因）。`design_brief._render_chapter_intent()` 把
   **阶段级**（跨多章）的「支线目标」「主线方向」与本章级内容（钩子/情节点）混在同一个
   `chapter_intent` 块里，而 `render_for_writer()` 用标题
   「【本章设计意图（规划端已登记，本章须落实；不得自行改道）】」覆盖整块。
   同函数下方**早已**把钩子/情节点按章切分并标注证据等级（`_intent_label`），
   这两行是漏网之鱼。后果：写手拿到「本章须落实」的强指令 + 一条**跨几百章的阶段目标**，
   最省事的服从方式就是在一章里把阶段目标做完 ⇒ 境界连跳。写手另一端同时收到
   `CARRIED_TO_WRITER`（"禁止无契机的越级跳变"），**两条强指令方向相反、且无优先级仲裁**，
   写手只能自行取舍——它选了"本章须落实"。

2. **门禁不阻断**。`writer_agent._HARD_REFUSE_RULE_IDS` 只列 4 条规则；其余 blocking
   证据走"兜底落盘（标记未通过）"（`_save_chapter` 门禁未过仍落盘），缺陷由此固化入库。

3. **境界规则不是跨度检测**。`consistency/checker._rule_realm_overstep` **只校验
   "正文宣称的境界名是否在 world.md 清单内"**，对"两个境界名都在体系内、但中间隔了
   两档"完全无感；且其 `severity=WARN`，而 `agentic_write` 只在 `severity == BLOCK`
   时才判不通过 ⇒ **把 realm_overstep 提为 BLOCK 无效**（对 ch41 仍无感），
   必须**新增跨度检测**。

## Decision

三端同源、最小缝合：

1. **粒度标注（`design_brief._render_chapter_intent`）**
   - 两行阶段级信息加前缀 `【阶段方向·跨多章｜本章不必完成】`；
   - `render_for_writer` 块标题改中性：「【本章设计意图与阶段方向（规划端已登记；
     标注「本章」的须落实，标注「跨多章」的禁止在单章内完成）】」。
   - `chapter_intent` 仍为**单一真源**（gather 链路、预算、`_INTENT_KEEP_PREFIXES` 守卫锚不动）；
     评委端 `render_for_judge` 复用同一块，故同步带上标注——这是**期望行为**：
     评委也必须知道这两行是阶段级，否则会把"阶段目标未在窗口内完成"误判成违约。
   - ⚠ **措辞不使用「非本章」**：该三字是 `chapter_contract.NON_CURRENT_LABEL_SUFFIX`
     的既有专义（"回退到最近前文、证据弱"）。首版措辞「非本章须完成」在
     红线 `test_n1_nearest_prior` 的 `test_judge_brief_marks_exact_hit_as_current`
     （断言精确命中时输出不含「非本章」）上失败——该断言虽是朴素子串比较，但它暴露的
     是**真问题**：提示词里出现两个同词异义的参照系（写手/评委要猜是"证据弱"还是
     "别在本章做完"），即纪律 #20 的参照系错位。故改词而非改断言，
     并新增 ``test_stage_direction_label_does_not_reuse_non_current_token`` 钉住。

2. **优先级仲裁（`CARRIED_TO_WRITER`）**追加【优先级仲裁】段，把此前缺失的
   "冲突时听谁的"写死：与阶段方向冲突时**一律以承接=为准**；本章境界**至多推进一境**；
   规则至多升级一次且须有本章内触发事件；**跨多章目标不得在单章内完成**。

3. **境界跨度确定性阻断（新增 `checker._rule_realm_span`，`Severity.BLOCK`）**
   - 承接境界从**连续性账本**取（主角的境界事实，`_carried_realm_index`）；
   - 世界体系**序位**从 `world.md` 的**有序**境界列表解析（`_load_world_realm_order`，
     支持 "小节 + `N. **境界名**`" 与 "`境界：A < B < C`" 两种登记形态）；
   - 正文宣称境界由既有 `_REALM_BREAK` 抽取，声明**归属**用
     `_nearest_registered_name`（200 字窗口内最近已登记角色）锚定；
   - **跨度 ≥2 档**才 BLOCK；差 1 档属"连续演进一境"，合法。
   - 规则在 `_builtin_rules()` 注册；`writer_agent._HARD_REFUSE_RULE_IDS` 增
     `"consistency_realm_span"`（前缀由 `agentic_write` 的 POST_WRITE 接入点添加）。

## Alternatives considered

### Why not 直接把 `realm_overstep` 提为 `Severity.BLOCK`？
零成本，但对本次事故**完全无效**：该规则只做"境界名是否在体系内"的成员校验，
引灵与淳真**都在** world.md 清单内，它一条都不会报。把它提为 BLOCK 只会把
"合法的越级命名"变成误阻断，而真正的跨度跳变照旧漏过。

### Why not 在 `agentic_write` 里改 `severity == BLOCK` 的判据（让 WARN 也可阻断）？
会把另外几条 WARN 规则（`realm_overstep`、部分 relation 判定）一并变成阻断，
误报代价远高于收益（BLOCK 会阻断落盘）。要阻断就得**新增一条有精确判据的规则**。

### Why not 修 `_load_world_realms` 让它也能解析编号列表？
可以，但那是"成员集合"语义（`set`），**没有序位**，无法算"跳了几档"。
跨度检测需要的是**有序**序列，故新增 `_load_world_realm_order` 而非改旧函数
（旧函数保持原行为，`realm_overstep` 不受影响）。

### Why not 用"本章宣称境界 vs 上一章正文里的境界"做比较（读正文而非账本）？
正文是自然语言，境界表述形态发散（"引灵中期/引灵期边界/身具引灵修为"），
正则召回不稳；账本是**已结算的权威状态**（承接=的既定真源），且
`CARRIED_TO_WRITER/JUDGE` 三端已同源对齐——沿用它才有单一真源。

### Why not 让跨度检测也拦"倒退 ≥2 档"？
倒退是另一类缺陷（且正文一般不会写成"突破至更低境界"），纳入会扩大误报面。
本次只封"无契机的**越级**跳变"这一条已实证的事故路径，保持规则判据单一。

## Consequences

- **生成期即阻断**：ch41 那类越级在**写章当时**就会被判 `realm_span` BLOCK ⇒
  写手修订或 `_golden_refuse_save` 硬拒绝落盘，账本不再被污染。
- **防误报（宁漏不误）**：承接境界不可确定（账本无主角境界事实）/ 世界体系无有序列表 /
  体系少于 3 档 / 宣称境界不在体系内 / 声明归属非承接主体 —— 任一成立即**放行**。
  老项目（无 `plan.json` 主角、无账本）行为逐字不变。
- **快照再基线（唯一一次有意变更）**：`test_m4_eval_pace_reference` 的
  `WRITER_BEFORE` / `JUDGE_BEFORE` 同步重算（差异经逐一核对：仅标题 + 两行标注共三处）。
  该缺陷存在于**全部历史作品**（灵荒工坊本身即老数据），故不再适用"新机制只对新数据生效"
  的口径——这是修一个历史缺陷。
- **测试**：`test_consistency_checker.py` 增 6 项（两档跳 BLOCK / 一档放行 /
  承接缺失放行 / 体系外放行 / 归属他人放行 / 写手硬拒绝含 `realm_span`）；
  `architecture/test_design_intent_propagation.py` 增 5 项（阶段级标注必现、
  **标注不复用「非本章」专义**、标注不泛化到章级行、写手标题中性、仲裁段关键约束必现）。
  相关回归（`continuity`/`ledger`/`writer`/`consistency`/`design`/`m4`/`realm`/
  `test_n1_nearest_prior`）全绿。
- **真实项目验证**：灵荒工坊 `world.md` 解析出 12 档有序境界
  （引灵/栖气/淳真/开玄/铸府/凝曜/蕴神/御冥/墟照/合宸/载世/归一）；
  用"ch41 写章**之前**"的承接（引灵）跑真实 ch41 正文 → `realm_span` BLOCK；
  ch40/ch42/ch43 无命中。

## Risks

- **账本已被历史污染**：灵荒工坊当前账本已含 `境界 = 淳真期初期`（ch41）与
  `修炼境界 = 淳真期初期`（ch42），故对**已落盘**的 ch41 复查不会再命中
  （承接已被污染成同一值）。本修复治的是"**生成期**不再产生越级"，不负责洗历史数据——
  该书正文/账本的清理属独立任务（用户明确排除）。
- **提示词收紧 ≠ 确定性保证**：改动 1/2 是提示词层的软约束，LLM 遵守是非确定性的；
  真正确定性的兜底是改动 3 的 `realm_span` BLOCK。
- **`_REALM_FIELD_NAMES` 是显式白名单**：结算端 LLM 自造的境界字段名若不在名单内，
  承接取不到 ⇒ 规则放行（宁漏不误）。新增字段名是**扩充召回**的常规动作，
  但每次扩充都应确认其值仍受"必须命中世界体系"约束，避免把非境界事实当境界。
- **主体锚定是启发式**：200 字窗口内最近已登记角色若恰好是**未登记**配角，会被误归到主角。
  以"跨度 ≥2 档 + 两境界均在体系内"双重前置压低概率；若实测误报，优先缩短窗口
  （而非降级 severity）——BLOCK 误报会阻断落盘，代价高。