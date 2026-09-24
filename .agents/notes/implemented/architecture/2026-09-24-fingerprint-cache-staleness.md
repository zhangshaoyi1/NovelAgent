# Agent Note: 指纹库按章文件自校验（跨章去重不得盲信缓存）
Status: implemented

## Problem

跨章段落去重（G14）依赖一份派生数据 `.state/chapter_fingerprints.json`，它**只在
「写章 / 改写 / 回滚」三条路径增量更新**：

- 写章落盘：`workflows/pipeline/agentic_pipeline.py` 用常驻 `guardrails` 对象
  `register_fingerprints` 后 `save_fingerprints(整个内存库)`——一旦启动时载入的是旧库，
  之后每次保存都会把旧条目**原样写回**，过期状态是**粘滞**的；
- 改写落盘：`core/quality/rewrite/feedback_rewriter._refresh_fingerprint`；
- 回滚：`core/story/chapter_invalidation._inv_fingerprints` 只删归档章条目。

任何**带外改动**（回滚后重生成、批量重写、人工编辑）都不会更新它，而所有写时
去重门禁都直接 `load_fingerprints(...)` **盲信**该缓存：

- `core/quality/rewrite/feedback_rewriter._inject_fingerprint_db`（rewrite 门禁）
- `workflows/writing/agentic_write.py`（写时 blocking 门禁 `cross_chapter_dup`）
- `cli/commands/autowrite.py`（续写启动注入）

**实证（2026-09-24 灵荒工坊）**：45 章中 **14 章**的缓存指纹与章文件**零重叠**
（例：ch036 缓存里是「铜镜 / 灰雾 / 黑色颗粒」的旧正文，章文件已是「齿轮 / 公差 /
人工作坊」），`22–25` 章甚至整体缺键。后果是 ch021 与 ch036 相似度 **0.987**、
与 ch039 **1.00** 的重复段落，在 `rewrite --chapter 21 --gate block` 下：

- 门禁比对的 ch036 条目是**已不存在的旧文** ⇒ 命中相似度仅 **0.18** ⇒ 判定「无重复」；
- 于是落盘并报「✓ 已落盘 第 21 章 · 字数 2978→2977」——用户以为改好了，重复一字未动；
- 离线用「现读章文件」重建指纹库后，同一段立刻以 0.98 / 1.00 命中并判 `paragraph_dup`。
  缺陷在**缓存**，不在判定规则。

同时暴露出键形不一致的次生缺陷：`fullbook_dup_scan` 写入 `ch036`、`guardrail_scan`
用 `ch[2:]`（`036`），而门禁按 `str(chapter_num)`（`36`）剔除本章自身 ⇒ 非规范键
**剔不掉自己**，本章旧段落与自身旧指纹相似度恒 1.0 的假阳性会打回重写（振荡），
与 `chapter_invalidation` 已记录的历史事故同源。

## Decision

把「写时跨章去重的指纹库」从**盲信缓存**改为**按章文件自校验**，章文件是唯一真源。
`core/quality/guardrails/guardrails.py` 新增三个符号（经子包 `__init__` 导出）：

1. `canonical_chapter_key(stem)`：`ch036` / `036` / `ch36` → 规范键 `36`，
   与门禁 `str(chapter_num)` 的剔除口径对齐。`fullbook_dup_scan` 改用该键
   （原 `f.stem` 会写进 `ch036`），`cli/commands/guardrail_scan.py` 同步改用
   （原 `ch[2:]` 得到 `036`）。
2. `rebuild_fingerprints(project_dir, *, chapters_dir=None, save=True)`：
   遍历 `chapters/ch*.md`，逐章 `register_fingerprints(canonical_chapter_key(stem), text)`
   重建整库并回写缓存（单章不可读按 `expected-skip` 显性豁免跳过）。
3. `load_book_fingerprints(project_dir, *, exclude=None)`：`rebuild_fingerprints`
   + 剔除 `str(exclude)`，供门禁直接使用。

接线（三处门禁统一改为 `load_book_fingerprints`）：

| 调用点 | 旧 | 新 |
| --- | --- | --- |
| `feedback_rewriter._inject_fingerprint_db` | `load_fingerprints(path)` + `pop(str(n))` | `load_book_fingerprints(project_dir, exclude=n)` |
| `agentic_write` 写时 blocking 门禁 | 同上 | `load_book_fingerprints(self.project_dir, exclude=ctx["chapter_num"])` |
| `cli/commands/autowrite.py` 续写注入 | 同上 | `load_book_fingerprints(project_path)` |

`load_fingerprints` / `save_fingerprints` 语义与格式**不变**（仍供指纹库读写与
`chapter_invalidation` 使用）；本变更只改变**消费方**拿到的是「缓存」还是「真源」。

## Alternatives considered

**Why not 保留缓存，仅在写入侧补刷新（给回滚/批量重写加钩子）？**
带外改动的入口在未来不可枚举（人工编辑、脚本改稿、模型批量重跑、git 恢复），
漏一个就退回旧病；而「派生数据可自校验」对入口数量不敏感。补钩子是把正确性
寄托在「未来所有人都记得调刷新」上，与本项目「单一真源」纪律相悖。

**Why not 用 mtime/size 做惰性失效（存 meta，只在文件变更时重算）？**
能把每次调用的成本从「读全书」降到「stat 全书」，但引入第二份派生状态
（meta 本身也会过期、被复制/git checkout 打乱），且本规模下没有必要：
45 章 ≈ 数十毫秒，相对一次 LLM 调用（分钟级）可忽略；1200 章时全书读取
在整本书写作周期内累计也只有数秒。用最简形态换取「不可能过期」。

**Why not 直接改 `load_fingerprints` 让它自己重建？**
它只拿得到缓存路径，拿不到章目录；且 `chapter_invalidation` 需要读**缓存现状**
来做归档删除，语义不同。分开成 `load_book_fingerprints` 让「谁需要真源」显式。

**Why not 顺手把 `_paragraph_similarity`（字符集合 Jaccard）换成序列相似度？**
它确实**顺序无关**（同字符集的异序段落会虚高），但本轮无实证误报（灵荒工坊
命中项经 difflib 复核全为真重复 0.91–1.00），且该函数是门禁的核心阈值真源，
改动需独立评估阈值面。遵循「只修有实证的缺陷」，本轮不动，另行登记再议。

## Consequences

- 正向：写时跨章去重不再可能被过期缓存欺骗——`--gate block` 能真正拦住跨章复制
  （灵荒工坊 ch021 复检：同一章由「0.18 无重复」变为「ch036 0.98 / ch039 1.00」）；
  缓存成为**可随时重建**的派生数据，带外改动自动收敛；键形统一消除「剔不掉自己」
  的假阳性振荡；`fullbook_dup_scan` / `guardrail_scan` 的键与门禁口径一致。
- 代价：每次门禁调用读一遍全书章文件（O(n) 读取，n=章数）。实测 45 章 < 50ms；
  写章路径每章一次，相对 LLM 调用可忽略；`rebuild_fingerprints` 每次都会回写
  缓存（内容不变时写同样的内容，无副作用但有一次写盘）。
- 已知边界：仍以「章文件正文」为真源——若章文件本身落后于作者意图，指纹也随之
  落后（这是正确行为：门禁判断的是**成书事实**）。`_paragraph_similarity` 的
  顺序无关性未修（见 Alternatives）。**测试锚点**：
  `tests/test_quality_gate_integrity.py::TestFingerprintCacheStaleness`（4 例，
  含「过期缓存漏检 / 重建后检出」的正反对照）与
  `tests/phase5/test_feedback_rewrite.py` 的跨章去重用例已由「预置缓存」改为
  「写入章文件正文」——后者是本次语义变更的直接体现。