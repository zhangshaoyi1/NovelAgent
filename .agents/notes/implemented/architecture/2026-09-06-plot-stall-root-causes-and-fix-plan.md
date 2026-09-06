# Agent Note: 自动写作剧情原地打转（无灵 ch100-205）根因分析与架构修复方案

Status: implemented（2026-09-06 按方案 1-3 落地；方案 4 双入口收敛留待后续）

> 作者：agent · 日期：2026-09-06
> 实证样本：`novels/无灵`（plan 350 章，autowrite 停在第 205 章）

***

## 1. 症状

1. **剧情原地打转**：ch100-198 反复循环「矿渊深处 / 骨 / 血池 / 石台 / 雾」母题；
   路线节点 N02（51-200 章）承诺的「建立凡骨殿 / 杀至宗门高层」到 ch198 几乎未启动
   （凡骨殿 ch193 才首次被口头提及，ch198 才以「旧址」出现）；S02 支线整书不可达。
2. **跨章重复未拦住**：ch184 标题与 ch177 完全相同（「矿渊深处的脚步声」）；
   ch191 重写 ch190 结尾同一场景（断点恢复覆盖）。
3. **标题门禁全量误报**：`chapter_quality_flags.json` 中 86 章几乎全是
   「未检测到合规章节标题」，且每章触发一次整章重写（token 双倍燃烧）。
4. **规模数据互相矛盾**：`plan.json` total=350、`mainline.json` horizon=1200、
   `protagonist_route.md` N04=351-400，无任何对账。

## 2. 根因（按权重）

### 根因 1 · 主线调度被陈旧的 horizon 锁死（结构性）

`mainline.json` 由 `MainlineOrchestrator._default_plan`/`_expected_chapters`
依据 world.md 体量 `mega` 机械估算出 `horizon_chapters=1200`，从未与
`plan.json.total_chapters=350` 校验。支线切换条件
`chapter > min(压力曲线上限, subline_share cap)`（`workflows/pipeline/mainline.py:76-82`）
中 S01 的 cap=480，在 350 章的书里**永不成立** → S01→S02 不可达。
叠加 S01 压力曲线把阶段锁在「冲突」，RAG 查询又以
`subline_goal + pressure_stage` 构建（`m5_context.py:85-98`），每章召回同批
矿渊旧文，形成母题循环。历史注记 `2026-08-30-wire-mainline-advance` /
`2026-08-31-dynamic-subline-budget` 修好了**机制**，但喂给机制的**数据**是陈旧的。

### 根因 2 · 每章剧情源缺失

每章前瞻内容取自 subline.md 的「情节点序列 / 章节钩子设计」两节
（`m5_context.py:183-191`），无灵的 S01 subline.md **不存在这两节** → 静默注入空串。
唯一连续性信号只剩上一章正文尾部 300 字（`m5_context.py:579-621`），
模型只能无限续写同场景。没有「情节点完成/耗尽」的概念。

### 根因 3 · 门禁检查的对象 ≠ 落盘的对象（第三次同族复发）

`m5_text_hygiene._clean_chapter_body` 刻意剥掉标题（防双标题），
标题在落盘时才由 `m5_persist._save_chapter` 补上；但管线门禁
（`agentic_pipeline.py:1429-1432`）检查的是去标题正文 →
`guardrails._check_title` 必然误报 → block 触发整章重写（重写后仍无标题，
永远修不好）→ 同时 `guardrails.py:557` 的标题查重（G14）因提取不到标题成为
死代码，ch184 同名标题因此过审。同族前科：`2026-08-30` 短章按原始文本计数漏判、
`2026-09-02-tail-loop-dedup` 复读虚增字数。**每次都是点修一个检查项的口径。**

### 根因 4 · 断点恢复覆盖重写

持久化顺序「先章节文件（原子写），后进度」（`m5_persist.py:255-278`），
恢复时用 `progress.total_written + 1` 定章号（`m5_context.py:64`），
两步之间崩溃 → 恢复后**直接覆盖已存在的 chNNN.md**，无「文件已存在则跳过/对账」守卫。
ch191 重演 ch190 结尾即此。另：subline 中途重新生成/改名
（`_duplicate_backup`、`mainline_visited` 两套命名）亦属同类——数据再生无迁移。

## 3. 架构缺口归纳（高于单个根因）

| 缺口 | 覆盖根因 | 一句话 |
|---|---|---|
| A. 故事规模/计划无单一权威 | 1、4(改名) | plan/mainline/route/outline 各说各话，无加载时校验，改一处不级联 |
| B. 门禁与落盘不消费同一成文产物 | 3 | 每个检查项各自对齐一次口径，就漏一次错一次 |
| C. 「降级不阻断」被滥用到确定性错误 | 1、2、4 | LLM 失败可降级；数据/状态不变量破坏必须 fail-fast，静默降级产出全为废稿 |

## 4. 修复方案（用户已认可，按序实施）

1. **缺口 A**：`plan.json` 确立为规模唯一权威；autowrite 启动时对
   plan/mainline(horizon、subline_share)/route 章节区间做一致性校验，
   矛盾即 fail-fast（提示重生成或自动对齐 horizon=min(horizon, plan_total)）。
2. **缺口 B**：建立 canonical chapter 成文管线（清理 → 去重 → 补标题），
   落盘、门禁、指纹注册、字数统计只消费同一产物；标题门禁改为在补标题后的
   成文上检查，G14 标题查重随之复活。
3. **缺口 C**：确定性不变量破坏改为 fail-fast——subline 缺「情节点序列/章节钩子」
   时启动报错；autowrite 启动对账 chapters 目录 vs `total_written`（文件多则采纳
   文件、补记进度），消除覆盖重写。
4. **双入口收敛**（跟随 B）：「发布一章」（成文管线 + 门禁 + 持久化 + 进度 + 指纹）
   收敛为共享组件，`agentic_write` 与 `agentic_pipeline` 只剩薄壳。

## 5. Consequences

- 修好后 S01→S02 切换在 350 章内可达，凡骨殿线（N02 主分支）按路线兑现；
- 每章 token 消耗约减半（消除标题误报引发的整章重写）；
- 未来 resize plan / 重生成大纲若造成数据矛盾，启动即报错而非默默打转 100 章；
- 门禁口径与成稿天然一致，新增检查项不再需要各自对齐。
