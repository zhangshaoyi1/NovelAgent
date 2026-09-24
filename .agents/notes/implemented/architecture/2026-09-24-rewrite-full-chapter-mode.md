# Agent Note: rewrite 整章重写模式（逐章契约注入 + 提示词变体）
Status: implemented

## Problem

`rewrite`（反馈→改写）是回修存量章节的唯一入口，但它按设计**只做局部最小改动**：

- `prompts/quality/rewrite.md` 的铁律 #1「只改用户要求改的地方，其余一律原样保留」、
  #5「用最少的改动」，收尾又写「用最少的改动，精准命中用户反馈」；
- `instruction.render_instruction` 在指令块里硬编码「问题清单（逐条命中，
  未提及的内容一律保留原样）」；
- user 提示把邻章锚点标为「衔接上文（不可破坏）/ 衔接下文（不可破坏）」。

三者叠加 ⇒ 该路径**结构上无法**消除雷同与同质化。灵荒工坊实证（16 章
`quality_passed: false` 的存量稿）：

- ch015 与 ch016 后段近乎逐段雷同（实测 9 处 ≥0.85，其中 2 处 1.0）——
  这是**契约层事故**：ch015 的逐章契约本应写「测试不同属性催化效果」，
  实际写成了 ch016 的「推演/图表」内容，ch016 又照抄一遍。要修必须两章各按
  **自己的契约**整章重写，且 ch016 不得复述 ch015。
- 两次远端模型 pilot（`mp-6232594841`）证实：一次原样回吐原文；加「严禁原样返回」
  后仅开头多出 184 字，后段重复原样保留，护栏照旧报 `paragraph_dup`。
- 定点整章重写的既有工具只有「rollback + autowrite」，覆盖不全：退到 15 只覆盖
  11/16 章，退到 2 才覆盖全部但会重写 44 章（用户已否决回退方案）。

## Decision

给改写路径增加第二种模式，模式差异落在**提示词**与**契约注入**两处，其余链路
（备份、护栏、L2 硬污染扫描、指纹同步、偏好沉淀）完全共用：

1. **提示词变体** `prompts/quality/rewrite_full.md`（`name: quality.rewrite_full`）。
   铁律改写为整章重写语义：允许重排场景/重写句式，**严禁把原文段落原样搬回**；
   邻章锚点降级为「只用于衔接、禁止复述」；新增「按本章逐章契约写」「字数硬约束」
   两条。user 槽新增 `chapter_contract` 与 `word_range`。
   `PromptManager._path_for` 按 `quality/rewrite_full.md` 定位，**无需注册**。
2. **契约注入**（`core/quality/rewrite/feedback_rewriter.py::_chapter_contract`）：
   定位口径与 `design_brief` 同源——章 frontmatter 的 `subline` →
   `sublines/<id>/subline.md`，再调**唯一真源** `chapter_contract()`（钩子 + 情节点）
   与 `pace_tier_of()`（四档强度档位），渲染成「本章逐章契约」块。写手/评委/改写
   三端因此共用同一份契约，不存在第二套解析。
3. **字数区间**（`_word_range`）：派生自写时门禁的唯一真源
   `resolve_min_cjk_words / resolve_max_cjk_words`（目标×0.8 ~ ×1.2），不另写比例常量。
4. **指令块措辞分叉**：`render_instruction(inst, *, keep_others=True)`。
   `full` 传 `False` ⇒ 输出「本章为**整章重写**，不受『仅改本清单』限制」，
   不再出现与整章重写直接矛盾的「未提及的内容一律保留原样」。默认 `True`
   保持既有定向修补语义（零回归）。
5. **入口**：`FeedbackRewriter.rewrite(..., mode: str = "patch")`（非法值回落
   `patch`）；CLI `rewrite --mode patch|full`（非法值 `bad_mode` 退出码 2）；
   `AgentService.rewrite_chapter(..., mode=...)` 同步透传。
6. **新增降级命名空间** `rewrite.contract`：契约取不到（frontmatter 无 `subline`
   字段 / `subline.md` 不存在 / 读取失败）时 `degrade()` 留痕并降级为「无契约」
   提示——整章重写没有靶子会退化成自由发挥，这种失效必须可见（不允许静默）。
7. **篇幅补足**（`_ensure_full_length` + `prompts/quality/rewrite_expand.md`）：
   full 首稿低于动态下限时，做最多 2 轮**整章扩写**（不是追加拼段）——
   扩写提示词要求「情节/事件顺序/章末钩子不变，只把场景铺开」，禁止注水。
   每轮扩写后重跑 `guardrails.check`：**若相对当前稿新增 error 级违规，弃用本轮**
   （宁短不脏），留痕于新降级命名空间 `rewrite.expand`。实证依据：本套模型对
   「整章重写」提示词系统性偏短（2918 字原章重写后仅 1120–1255 中文字），
   而追加式补字补到 2178 字时**重新引入**第 14 章相似度 1.00 的 `paragraph_dup`。
8. **字数硬检查**（`_length_issues`）：full 模式产物落盘前调写时门禁的**同一条规则**
   `_check_word_count`（不另写比例常量）——低于动态下限为 BLOCK 级（`--gate block`
   下拒绝落盘、`error="word_count_short"`；advisory 下仅告警），超上限为 WARN 级。
   仅 full 适用：patch 是用户指定的「最小改动」，不因篇幅被拦。
9. **空转判定**（`_is_noop` + `_NOOP_RETRY_HINT`）：full 首稿与原文「去空白后全等」
   时，追加一句硬指令重试一次；仍全等则**显性失败**——不落盘、不备份、
   `error="noop_output"`，留痕 `rewrite.noop`。实证：2026-09-24 22:26 的 ch015
   整章重写任务返回「字数 2290→2290」，正文与原文**逐字相同**（模型原样回吐），
   旧链路照常落盘并报「✓ 已落盘」——用户以为改过了，其实一字未动。
   判据用「全等」而非相似度：只放宽排版差异，任何真实改写都会改到字面。
10. **落盘元数据同口径刷新**（`_gate_passed` / `_wc`）：`_save_rewritten` 落盘时
    按**写时规则层门禁**（`QualityChecker(project_dir, None).check(body)`，即
    `quality_check` 工具的同一条链路）重算 `quality_passed`。此前该字段只在写章
    当时写入、改写路径从不刷新 ⇒ 改好了章节、标记仍是 `false`（灵荒工坊 16 章
    实证：离线复核 14 章已无缺陷，仪表盘照旧显示「未通过」）。
    同一落盘点补齐 `word_count`（公式与写章唯一写盘点 `m5_persist._save_chapter`
    相同：`len(text.replace("\n","").replace(" ",""))`，且**不含** H1 标题行——
    `body_text` 已 `strip_leading_headings`）。此前只刷 `quality_passed` 不刷
    `word_count`，带外改动（回滚重生成/批量重写/人工编辑）后该字段永远停在写章
    当时的旧值（灵荒工坊 5 章实证：ch002/ch015/ch021/ch022/ch025 与正文实际不符，
    `write` / 仪表盘读到的都是陈旧字数）。

`full` 模式的 `max_tokens` 提到 8192（patch 仍 6000）：整章重写要输出完整一章，
按 patch 上限输出会被截断，与 M3 大细纲同口径。

## Alternatives considered

**Why not 直接放宽 `rewrite.md` 的铁律（一个提示词吃两种模式）？**
patch 的「最小改动」是它存在的理由（用户说"这章太拖"时不该整章换血），
full 的「严禁照搬」与它直接冲突。同一提示词里同时写两条互斥铁律，模型只能任选，
等于把模式选择交给随机性——正是 `chapter_intent` 混装导致写手冲突的同型缺陷。

**Why not 新增独立 CLI 命令 `rewrite-full`？**
模式是同一闭环的参数而非另一条链路：备份/护栏/门禁/指纹/偏好沉淀全都要复用。
新命令会让这些步骤出现第二份接线，违反单一真源。

**Why not 让 full 模式绕过 `structure_feedback`（直接用原始反馈）？**
原始评审文本不得直接成为模型指令（P1-6）。full 模式仍走结构化 + 确认闸口，
只是指令块的「保留」措辞分叉。

**Why not 在契约缺失时直接报错不落盘？**
违反本项目「降级不阻断」哲学；且 `subline` 字段在历史章上可能缺失（早期章节）。
改为显性 `degrade()` + 退化提示，用户仍可拿到"按事实+反馈"的重写结果。

**Why not 复用 `repair`（`RepairOrchestrator`）？**
它复用同一个 `FeedbackRewriter`，因此同样受"最小改动"铁律约束（已实测），
且其定位是"扫描坏点→自动改"，不含逐章契约对齐与批量定点重写。

**Why not 把 `WriterAgent._top_up_length`（续写补字）下沉为 core 共享函数，供两路复用？**
技术上可行（core 可依赖 client），但语义不对：`_top_up_length` 是**追加拼段**
（`merged + "\n\n" + piece`），会留下拼接缝——正是「整章重写」要消除的东西；
且它位于 agents 层、被 `tests/test_writer_prompt_and_topup.py` 直接锚定，下沉要
动写章主路径（回归面大）。本缺陷需要的是**整章扩写**（无接缝、情节顺序不变），
与追加补字是两种操作，故在 `FeedbackRewriter` 内实现，并复用同一条护栏复检契约
（`guardrails.check` 的 error 集合）而非复用代码。二者共用的只有阈值真源
（`resolve_min_cjk_words`）与「降级必留痕」纪律。

**Why not 只重跑整章重写（不改提示词）直到达标？**
实测首稿偏短是**系统性**而非偶发（两次 pilot 均 1120–1255 字），重跑同一提示词
大概率仍是短稿，白付整章生成成本。扩写提示词把「补足」变成明确目标（含当前字数
与目标区间），命中率高得多。

## Consequences

- 正向：存量雷同/注水章有了可控的定点整章重写通道（`--mode full` 逐章指定）；
  三端共用同一份逐章契约，重写产物与写手/评委的参照系一致；跨章去重、字数下限
  在重写路径同样生效（指纹同步见同批次 note
  `2026-09-24-rewrite-fingerprint-sync.md`）；篇幅不足有确定性的补足与硬检查，
  不再"多短都落盘"。
- 代价：`full` 模式单次调用更贵（输出整章、`max_tokens` 8192），最长 3 次调用
  （首稿 + 2 轮扩写）；prompt 增加两个变体文件，铁律需同步维护（测试把
  「full 模式的指令块不得出现『原样保留』」钉在了
  `tests/phase5/test_feedback_rewrite.py`，并把"扩写引入违规须弃用""补足后仍短须
  BLOCK"两条行为各钉了一个测试）。
- 已知边界：契约缺失的退化路径不保证情节位次正确，只保证不静默；
  补足轮数用尽仍未达下限时（`--gate block`）该章不落盘，需人工介入或换更大
  `_FULL_EXPAND_ROUNDS` 重试；空转两次即报 `noop_output`，需换模型/调反馈重试
  （不自动第三次——连续空转是模型侧问题，重试只烧钱）；`_gate_passed` 与
  `--gate block` 是两套口径：前者是规则层通行标记（只判 `banned_words` /
  `word_count` 等规则层规则），护栏 error（如跨章 `paragraph_dup`）不反映在该标记里。
- 未覆盖：批量按清单重写 16 章仍需逐章调用（CLI 无 `--chapters-range`），
  本轮不新增批量编排。