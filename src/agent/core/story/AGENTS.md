# AGENTS.md - core/story/ 故事领域模型层

## 职责

封装小说创作的核心领域逻辑，管理所有叙事元素。

## 核心模块

| 文件 | 导出 | 作用 |
|------|------|------|
| `setting_manager.py` | `SettingManager` | 设定集管理器（world.md / subline.md / 角色档案） |
| `foreshadow_manager.py` | `ForeshadowManager`, `ForeshadowState` | 伏笔管理器（埋设/回收/跟踪） |
| `relation_manager.py` | `RelationManager` | 关系网管理器（角色/势力/地点/物品） |
| `chapters.py` | `strip_frontmatter`, `list_chapter_files`, `iter_chapter_texts`, `read_chapters_text` | 章节文件 Helper |
| `snapshot_manager.py` | `SnapshotManager`, `ResumeBriefing` | 快照与回滚 |
| `evidence_chain.py` | `EvidenceRef`, `EvidenceChain` | 证据链（章节与设定的引用追溯） |
| `tension_curve.py` | `TensionCurveManager`, `TensionScore`, `ArcPlan`, `ArcPhase`, `RhythmAlert`, `measure_chapter_tension`, `record_tension`, `read_tension`, `TENSION_LEDGER` | 高潮曲线。★**2026-09-19（M6）起为「已接线的观测面」**：`measure/record/read_tension` 构成章后实测张力落盘链路（生产入口 `workflows/writing/agentic_write.py` 调 `m5._record_tension`），台账 `.state/memory/tension_readings.json` |
| `pacing_store.py` | `PacingStore`, `Debt` | 追读力账本（钩子债/伏笔债） |
| `payoff_script.py` | `build_payoff_script`, `load_payoff_script`, `chapter_payoff` | 爽点剧本 |
| `injected_trope_store.py` | `InjectedTropeStore` | 注入套路存储 |
| `learning_store.py` | `LearningStore` | 学习存储（长期记忆与偏好沉淀） |
| `foresight.py` | `ForesightStore`, `ForesightThread`, `ForesightBeat` | 前瞻性规划 |
| `timeline.py` | `Timeline`, `StoryEvent`, `NarrativePlacement` | 时间线管理 |
| `method_style.py` | `load_style_guide`, `load_method_text` | 写作方法风格 |
| `meta/worldbuilding_schema.py` | `IcebergField`, `IcebergDimension`, `IcebergGroup` | 世界观 Schema |
| `meta/philosophy.py` | `TAGLINE`, `OPENING`, `PILLARS`, `Pillar`, `CLOSING` | 设计哲学文案 |

## ★ 纪律：张力是「观测面」，不是「供给面」（2026-09-19 M6 确立）

张力分（`measure_chapter_tension` / `record_tension`）度量的是**正文文本统计事实**，
强度档位（`pace_tier`）是**规划阶段的创作意图**。两者是**独立 side-channel 对账关系**，
不是"用张力去指导写作"。**红线钉死**：

- `tests/test_m6_tension.py::TestObservationNotSupply` —— 本模块**不得**产出任何 `pace_tier*` 文件、**不得**导出任何含 `tier` 的 API。
- 禁止在写手端按张力值反推 / 覆盖档位（既有红线见 `workflows/writing/chapter_contract.py:447`）。

理由：把观测值回灌成约束会让"事实"污染"意图"，两条线一起失真 → 无法再对账。

## 依赖规则

- 依赖 `base`、`client`
- 不依赖 `quality`/`engine` 等其他业务包