# Agent Note: 定向改写纳入「全书指纹库」增量维护契约
Status: implemented

## Problem

`rewrite`（反馈→定向改写）是回修存量章节的唯一入口，但它在两个方向上都与
**全书跨章去重契约**脱节：

1. **判定侧失效**：`cli/commands/rewrite.py` 用裸 `build_guardrails()` 构建护栏，
   `fingerprint_db` 恒为空。`Guardrails._check_dup`（≥40 字长段落、相似度 ≥0.85）
   因此从不命中——**带着跨章重复的章改不出来，还可能改出新的重复**。
   灵荒工坊 ch015/ch016 相似度 0.91 实证：两章后段近乎逐段雷同，却在 rewrite 路径
   一路放行。
2. **登记侧缺失**：改写落盘（`_save_rewritten`）只改章节文件，不回写
   `.state/chapter_fingerprints.json`（写章路径由 `agentic_write` /
   `fullbook_dup_scan` 负责登记）。指纹库于是长期指向**已不存在的旧正文**：
   后续写章按陈旧指纹判重 ⇒ 误报；新正文不入库 ⇒ 漏报。

后果：回修动作本身会污染全书去重基线，使「改完还是被判重复」或「复制粘贴过审」。

## Decision

`FeedbackRewriter` 在改写前后各做一次指纹库同步，与写章路径**同口径**（同一个
`.state/chapter_fingerprints.json`、同一套 `load_fingerprints` / `register_fingerprints`
/ `save_fingerprints`）：

- `_inject_fingerprint_db(chapter_num)`：护栏校验**之前**加载全书指纹库并
  **剔除本章自身**后注入 `self.guardrails.fingerprint_db`。剔除自身是
  `_check_dup` 的调用方契约（库中仍留本章旧文时，本章段落与自身指纹自比，
  相似度恒为 1.0 而误报）。
- `_refresh_fingerprint(chapter_num, text)`：`_save_rewritten` **之后**重新登记本章
  指纹（旧条目被覆盖），只动本章键，他章条目不受影响。

两处的 load/save 沿用 `guardrails.py` 既有的内部降级（解析失败降级为空库、
持久化失败静默跳过），因此本改动**不新增** `degrade()` 调用与静默豁免预算。

命中的 `paragraph_dup` 是 error 级违规：`--gate block` 下拒绝落盘，
默认 advisory 下仅告警并落盘（与改写路径既有门禁语义一致，未做升级）。

同批修复的展示层缺陷：`cli/commands/rewrite.py` 的 `_print_result` 与 `--json`
分支曾把 `RewriteResult.error` 当 dict 处理（`error` 实为 str；`to_dict()` 恒带
该键，无错时为空串），导致 `AttributeError` 崩溃且**成功也被报成失败**。
现按 `isinstance(err, dict)` 区分「结构性失败（章节不存在）」与「降级原因
（LLM 不可达 / 确认被拒）」。

## Alternatives considered

**Why not 在 rewrite 里做整书全量重扫（`fullbook_dup_scan`）？**
全量重扫是完本关卡的 O(全书) 操作，单章回修要付出整本代价，且会重写整份报告文件；
增量登记本章 + 复用现有库即够用。

**Why not 只修 CLI（把指纹库传给 `build_guardrails`）？**
那只解决判定侧；登记侧缺失会让下一次改写的判定继续基于陈旧库——判定与登记是同一
契约的两半，必须同时补。

**Why not 让 `FeedbackRewriter` 每次新建一份 `Guardrails` 副本以避免就地改注入？**
实例由本类持有（构造默认即 `build_guardrails()`，CLI 也每次新建），就地更新
`fingerprint_db` 不会外溢；重建副本需要复制全部护栏配置，收益不抵复杂度。已在
`_inject_fingerprint_db` 的 docstring 中显式声明该就地语义。

**Why not 把改写产物也接入软门禁（LLM 质检/吸引力）重跑？**
超出本缺陷范围：软门禁是写章主线的多轮评审链路，回修路径的既定语义是
「用户反馈驱动的局部定向改写」。此处只补确定性去重契约。

## Consequences

- 正向：导致 ch015/ch016 类事故的确定性通道被堵住；改写不再污染指纹基线；
  `--json` 输出的成功/失败判定恢复正确。
- 代价：每次改写多两次 `.state/chapter_fingerprints.json` 读写（同一小文件）。
- 已知边界：本改动只让**重复被检出**，不自动消除存量重复——存量内容仍需逐章处理
  （见灵荒工坊 16 章未通过稿的回修清单）。