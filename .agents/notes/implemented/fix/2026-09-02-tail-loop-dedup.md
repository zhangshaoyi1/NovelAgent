# Agent Note: 章节尾部循环段落去重（P-DEDUP-2）

Status: implemented

## Problem

LLM 在 Agentic 写章时，除了「正文中途再次出现章节标题 → 整章重复」（已有
`_dedup_repeated_chapter` 覆盖），还会在**章节结尾把前面已写过的整段连续段落
原样复读一遍**。ch238 实证：萌芽在结尾把「山谷里火把 → 无名眯眼 → 血屠现身」
整组战斗段落完整复述一次。

由于这种循环复读**不产生第二个** **`# 第N章·…`** **标题**，`_dedup_repeated_chapter`
的标题锚点无法命中，导致：

* 重复段虚增章节字数，门禁按含重复的字数放行，掩盖了真实正文偏短的事实；

* 落盘正文含车轱辘复读，观感差，需人工事后清理。

## Decision

在 `M5WriteChapterWorkflow` 新增 `_dedup_tail_loop(text)` 静态方法（纯确定性、
无 LLM 调用），检测**最长的尾部循环复读块**：

* 按空行切分为段落块 blocks；

* 寻找最长循环长度 L（≥ min\_run=2）：存在源起点 i 与复读起点 r=i+L，使
  blocks\[i:i+L] 与 blocks\[r:] 逐段对齐（段级字符集合 Jaccard ≥ 0.8，口径与
  evaluator.\_sent\_sim 一致）；

* 命中即以 r 为界返回 blocks\[:r]，删除复读尾部。

保守性（不误删正常续写）：

* 阈值 0.8 + 要求**整块循环**逐段对齐（非单段重合）；

* 循环块长度上界 n//2（复读块不可能超过正文一半）；

* 正常「剧情呼应/平行叙事」措辞有差异，Jaccard 不达标，不会误触发。

调用点（与 `_dedup_repeated_chapter` 并排）：

* `m5_write_chapter._save_chapter`：P-DEDUP 之后、字数统计之前；

* `agentic_write._llm_quality_gate`：字数门禁前清理，保证门禁基于去重后文本。

## Alternatives considered

* 靠修订循环 + Critic LLM 自查删重复：成本高、非确定性、且 Critic 未必拦得住
  （ch238 revision\_attempts=0 一次通过即说明审稿放行了重复稿）。

* 提高好稿兜底/门禁阈值逼迫重写：可缓解但治标不治本，且浪费修订预算。

* 段落级哈希精确匹配：只能拦逐字，漏掉轻微改写。

## Consequences

* P-DEDUP-2 在落盘前确定性剔除尾部循环复读，字数统计更真实，门禁判定更准；

* ch238 从 115 段收敛到 108 段，复读战斗块被清除，结尾正确收在悬念句；

* 不改变句法/段落格式化（与 P-DEDUP 同层的独立步骤，可独立开关）；

* 已加 4 条回归单测（agent/tests/test\_m5\_dedup\_tail\_loop.py）。

