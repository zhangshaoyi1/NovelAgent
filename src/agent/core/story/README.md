# story/ — 故事领域模型层

## 职责
封装小说创作的核心领域逻辑，管理所有叙事元素。

## 包含文件
| 文件 | 职责 |
|------|------|
| `setting_manager.py` | 设定集管理器（world.md / subline.md / 角色档案读写） |
| `foreshadow_manager.py` | 伏笔管理器（埋设、回收、跟踪） |
| `relation_manager.py` | 关系网管理器（角色/势力/地点/物品的关系图谱） |
| `chapters.py` | 章节文件 Helper（读取、排序、去 frontmatter） |
| `snapshot_manager.py` | 快照与回滚（设定/关系/章节的版本管理） |
| `evidence_chain.py` | 证据链（章节内容与设定的引用追溯） |
| `tension_curve.py` | 高潮曲线（章节紧张度评估与弧级规划） |
| `pacing_store.py` | 追读力账本（钩子债/伏笔债的跟踪） |
| `payoff_script.py` | 爽点剧本（章节级爽点类型与情绪目标配置） |
| `injected_trope_store.py` | 注入套路存储 |
| `learning_store.py` | 学习存储（长期记忆与用户偏好沉淀） |
| `method_style.py` | 写作方法风格（叙事方法模板） |
| `worldbuilding_schema.py` | 世界观 Schema 定义 |
| `philosophy.py` | 设计哲学（产品理念文案） |

## 依赖规则
- 依赖 base/、client/
- 不依赖 quality/、engine/ 等其他业务包

## 被依赖
- workflows/ (M2-M6, M10-M18)
- agents/ (Planner, Editor, Evaluator)
- infra/ (冲突仲裁)