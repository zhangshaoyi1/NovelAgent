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
| `tension_curve.py` | `TensionCurveManager`, `TensionScore`, `ArcPlan`, `ArcPhase` | 高潮曲线 |
| `pacing_store.py` | `PacingStore`, `Debt` | 追读力账本（钩子债/伏笔债） |
| `payoff_script.py` | `build_payoff_script`, `load_payoff_script`, `chapter_payoff` | 爽点剧本 |
| `injected_trope_store.py` | `InjectedTropeStore` | 注入套路存储 |
| `learning_store.py` | `LearningStore` | 学习存储（长期记忆与偏好沉淀） |
| `foresight.py` | `ForesightStore`, `ForesightThread`, `ForesightBeat` | 前瞻性规划 |
| `timeline.py` | `Timeline`, `StoryEvent`, `NarrativePlacement` | 时间线管理 |
| `method_style.py` | `load_style_guide`, `load_method_text` | 写作方法风格 |
| `meta/worldbuilding_schema.py` | `IcebergField`, `IcebergDimension`, `IcebergGroup` | 世界观 Schema |
| `meta/philosophy.py` | `TAGLINE`, `OPENING`, `PILLARS`, `Pillar`, `CLOSING` | 设计哲学文案 |

## 依赖规则

- 依赖 `base`、`client`
- 不依赖 `quality`/`engine` 等其他业务包