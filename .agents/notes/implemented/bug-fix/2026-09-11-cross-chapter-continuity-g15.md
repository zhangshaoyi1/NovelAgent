# Agent Note: G15 章间细节断层——连续性三通道接线与事实抽取器
Status: implemented

## Problem

长篇写作出现「上一章刚交代的信息下一章不知道」的断层。定位为**连续性三通道全断 + 前情提要中段
盲区**：负责把本章事实沉淀进连续性账本的归档 hook 此前只挂在废弃的 M5 入口，生产入口从未调用；
且前情注入窗口过窄，中段一直是盲区。

事实：`_archive_chapter`（章后归档 hook）此前只挂在废弃的 M5 入口，生产入口
`AgenticWriteWorkflow.run()` 从未调用——章与章之间的事实链从不断裂，而是从未建立。连续性账本
（continuity ledger）因此长期为空，下游 RAG 与前情注入都拿不到跨章事实。

## Decision

- **① 接回生产路径**：把 `_archive_chapter` 接进 `workflows/writing/agentic_write.py` 的生产
  写章路径（与 M5 同位），章后归档 hook 现在每次写章都会触发，账本开始按章累积。
- **② 确定性事实抽取器**：`m5_persist.py` 新增事实抽取器（**不靠 LLM**），领域限定为五类——
  character / relationship / world / plot / foreshadowing，把本章抽取的事实写入连续性账本。
  零推理成本、可复现，避免再引入 LLM 抖动；写入时带章号与来源标记，便于下游按章回溯。
- **③ 前情注入窗口放宽**：`m5_context.py` 中窗口 ≤4000 字时注入全文；超长时取「头 600 + 中 1800 +
  尾 1200」三段采样（原先中段是盲区，现在补齐）——头尾保边界、中段保被遗忘的情节点。
- **④ 红线防回归**：`tests/architecture/test_capability_parity.py` 把 `_archive_chapter` 接线
  纳入能力对账差集（见架构笔记第 2 份），防止接线再次丢失。

同一批附带（非本 bug 主线，仅记录）：
- 删掉 `core/infra/context_loader.py`、`core/story/foreshadow_manager.py`、
  `core/story/snapshot_manager.py` 等 stub（commit `583773b`）；
- 把 m13 伏笔播种闭环补到 `workflows/evaluation/m13_foreshadow.py`；
- 把 `max_tokens` 做成模型档位字段（`base/model_profiles.py` + `client/gateway_adapter.py`，
  floor 语义）。

## Alternatives considered

- **用 LLM 抽取章间事实**：引入推理成本与抖动，且断章事故恰因链路不稳，确定性抽取器更可复现——
  否决 LLM 方案。
- **前情全量注入**：超长篇会撑爆上下文且稀释焦点，三段采样在成本与覆盖间取平衡。
- **只修 M5 入口**：M5 是废弃入口，生产走 agentic，修 M5 无收益，必须接回 `agentic_write.py`。

## Consequences

- 这是「工程质量」而非「ff 章数事故」的胜利：组件被调用了，连续性账本开始按章累积事实、前情
  注入覆盖中段——但**未量化断章率改善幅度**，收益以「链路接通」为准，不夸大。
- 事实抽取器五类领域是否覆盖完整、准确率如何，留待后续 corpus 评估确认（**待核实**）。
- 能力对账红线使 `_archive_chapter` 再脱钩会被测试拦截；抽取器与账本为 P-9 事实对照卡（提示词笔记）
  提供数据底座。
- G15 在体检中作为连续性维度被 HA-Eval 采集；本次仅接通链路与抽取器，未调整该维度阈值或权重。
- 能力对账差集的具体含义：M5 链路 `self._archive_chapter` 与 agentic 链路调用名差集必须为空，
  否则 `test_capability_parity` 失败——这是 `_archive_chapter` 不脱钩的硬保障。
- 三段采样窗口常量（头 600 / 中 1800 / 尾 1200）与「≤4000 字注入全文」阈值都写在 `m5_context.py`，
  调参只动这一处。
- 抽取器五类之外的事实（如 timeline/时间线）暂未纳入，属已知收敛边界（**待核实**：是否需扩类以覆盖
  时序类断章）。

## 三通道接线说明

- 通道 A（沉淀）：`_archive_chapter` → 抽取器写账本，原只挂 M5，现双路径接。
- 通道 B（注入）：`m5_context.py` 前情窗口放宽，中段盲区消除。
- 通道 C（防回归）：能力对账差集盯住 `_archive_chapter` 是否在 agentic 路径可达。
- 相关 commit：`9754d7c`（stub 清理 `583773b` 为同批附带）。
