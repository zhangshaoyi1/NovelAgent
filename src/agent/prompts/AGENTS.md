# AGENTS.md - prompts/ Prompt 配置

## 职责

存放 Prompt 配置文件（markdown 模板 + 元配置），按里程碑/功能域分子目录。

## 目录结构（2026-09-12，20+ 子目录）

| 分组 | 子目录 | 作用 |
|------|--------|------|
| 阶段提示词 | `m1` ~ `m23`、`m_d` | 各里程碑工作流（配置/讨论/大纲/写章/质检/导出…） |
| 门禁/约束 | `g`、`g8`、`g11`、`g12` | 通用约束与门禁提示词（`g/beat_ban.md` 桥段禁用清单、`g/setting_canon_constraint.md` 设定正典约束） |
| 写章 | `m5` | 写章族（`generate.md` 生成、`quality_check.md` 质检、`deslop.md` 净化问题模式目录） |
| 质检 | `quality` | 质量评估提示词（如 `reader_appeal_eval.md` 读者吸引力） |
| 其他 | `e`、`agents`、`budget`、`methods` | 评估/智能体/预算/方法 |
| 元配置 | `_meta.yaml` | Prompt 元配置 |

## 修改约定

- 提示词随改进批次迭代（P-1~P-12 已落地：质检输出精简、硬约束校验、细纲覆盖校验、deslop 问题模式、事实对照卡、章内情绪节奏、教训进复审重点等），改动需附验收证据
- 改 prompt 不影响已运行进程：换档/改档后必须重启 Web/daemon 进程才生效
- `version` 字段随每次实质内容修改 +1（热重载靠 mtime，version 用于人工追踪）
- 原 `shared/`（json_output / score_calibration）无任何代码与模板引用，2026-09-12 已删除；~~JSON 输出约束在各提示词内联维护~~
- **2026-10-01 批次收口（二）**（《项目文档/优化/20261001_提示词批次收口二_共享片段与死文件收口.md》）：重建 `shared/` 目录为**下划线前缀片段**（`_json_output` / `_quality_rules13` / `_b_review` / `_c_anchors` / `_cliche_words` / `_no_english_examples`），PromptManager 已支持 `{% include %}`（FileSystemLoader），**改共享口径只改片段文件**；30 份提示词的 JSON 输出约束统一引用 `_json_output.md`；`m5/generate.md` system 段收口为 live 写手提示唯一真源（writer_agent 内联 `_WRITER_HEAD/_WRITER_TAIL` 已删除，改模板含 21 条写作要求）；m21 补 `jinjiang.md` rubric 并统一 rubric 裁决口径（默认 warn、连续多章 FAIL 升级 block）。片段文件以 `_` 开头，不入版本面板
- 2026-09-30 批次收口（一）（《项目文档/优化/20260930_提示词批次收口一_矛盾与评分信号.md》）：消除文件内/文件间直接矛盾（revise 标题与 Writer 合同对齐、deslop 族容差统一 ±20%/三档删除上限、m1 world 体系优先级补兜底、m3 五检/m14 编号）；评分信号修复（m_d.review 与 m23.short_analyze 示例分去趋中、m21.verdict 补映射规则、combined C 部分/reader_appeal 六维补 0-100 分档锚点）；JSON 契约补齐（agents.planner 补全 schema、m20.aggregate 补 overlap/overlap_ok、combined 输出键 `nine_item`→`rules_item` 并在 agentic_write.py 过渡兼容）；m21.consistency 删 grep-first 残留、补「宁缺毋滥」。剩余结构性项（JSON 约束 6 种措辞漂移、generate.md system 死文件、m3 chapter_hooks 非结构化、枚举三套、晋江 rubric 缺失）待第 II 批登记
