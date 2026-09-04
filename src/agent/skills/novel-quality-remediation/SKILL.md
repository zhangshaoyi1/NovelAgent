---
name: novel-quality-remediation
version: 0.1.0
type: remediation
description: 成书质量体检与修复工作流——针对已生成小说中的英文残留、占位标题、跨章逐字/近似重复、写作元指令泄漏四类问题，提供"治本（写时护栏）+ 治标（存量修复脚本）"的完整方案。
commands:
  - name: quality-remediation
    args:
      - project_dir (必填，chapters/ 所在项目目录)
      - scope (可选: junk|title|dup|highsim，逗号分隔，默认全做)
      - dry_run (可选: 只分析不落盘)
hooks: []
dependencies:
  - src/agent/core/guardrails.py
  - src/agent/cli/commands/guardrail_scan.py
  - scripts/scan_english.py
  - scripts/fix_english.py
  - scripts/fix_titles.py
  - scripts/scan_dup.py
  - scripts/fix_dup.py
  - scripts/fix_dup_highsim.py
independent: true
---

# Novel Quality Remediation · 成书质量体检与修复

## 适用场景

- 已生成的长篇小说（如 changan-binyiguan）中存在三类脏数据：
  1. **英文残留**：模型生成时混入的英文单词/短语（如 `voice`、`lantern`、`Visualization failed. Cost 1 year of lifespan.`）。
  2. **占位标题**：每章 `# 第N章 · 第N章` 或空标题、过短标题。
  3. **跨章重复**：同一段落被逐字/近似复制到多章（"开场重演/场景雷同"缺陷，最狠可达百次级）。
  4. **写作元指令泄漏**：agent 下发给 LLM 的"章末悬念 / 章节钩子 / 内部章节号"等写作元指令被模型原样写进小说正文（如 ch026 结尾的 `【章末悬念】…`、ch002 的 `章末悬念：…`），属 prompt 污染，需整段剔除。
- 也用于**治本**：确保下一本书在写作阶段就被 `guardrails.py` 拦截，不再产生上述问题。

## 两层防线

### 治本：写时自动校验（guardrails.py，已落地）

`src/agent/core/guardrails.py` 在 `autowrite` 落盘前 `gate(mode="block")` 注入以下规则，命中即打回 Writer 重写 1 次，仍不过则降级告警（写 `.state/chapter_quality_flags.json`）不阻断：

| rule_id | 含义 | 阈值/判定 |
|---|---|---|
| `non_chinese_junk` | 英文/乱码残留 | 剥离 frontmatter 后，连续≥3字母英文 + 特征串（`Visualization failed`/`Cost`/`[system]`/`undefined`/`null`），白名单豁免 |
| `title_placeholder` | 占位标题 | 标题为空 / 长度<4 / `第N章·第N章` / 与 `published_titles` 重复 → error |
| `paragraph_dup` | 跨章重复 | 与全书指纹库比对，相似度≥0.85（仅≥40字长段）→ error |
| `meta_instruction_leak` | 写作元指令泄漏（章末悬念/章节规划标记/内部章节号写入正文） | 正文含 `章末悬念`/`（章末悬念`/`【章末悬念`/`留下悬念`/`悬念[:：]` 等标记，或叙事中冒出 `ch\d+章`/`第\d+章` 指代写作进度 → error |

配套：
- `autowrite` 构建 guardrails 时注入全书标题 + `load` 指纹库（续写复用）；
- `compose_runner` 完本时全量段落去重扫描，输出 `.state/dup_scan_report.md`；
- `guardrail_scan` 命令（`-d <项目目录> --scope junk,title,dup`）手动体检，输出终端报告 + `.state/guardrail_scan_report.md`。

> 这是"下一本不再出问题"的根本保障，已由 commit 落库，无需本项目重新跑。

### 治标：存量修复脚本（scripts/，本项目 changan 已跑通）

针对**已经写好的书**的存量脏数据，按以下顺序执行：

| 步骤 | 脚本 | 作用 | 依赖 |
|---|---|---|---|
| 1 | `scan_english.py` | 扫描英文残留 → `.state/english_scan.json` | 无 |
| 2 | `fix_english.py` | LLM 把英文片段译为中文最小补丁（含重试/退避/批处理） | `english_scan.json` |
| 3 | `fix_titles.py` | LLM 基于首段生成场景化标题替换占位（与护栏规则对齐检测） | 无 |
| 4 | `scan_dup.py` | 跨章重复分析 → `.state/dup_scan_v2.json` | 无 |
| 5 | `fix_dup.py` | 对相似度 1.00 的逐字重复，LLM 改写去重（保留首次为基准，变种轮换） | `dup_scan_v2.json` |
| 6 | `fix_dup_highsim.py` | 对 ≥0.95 的近似重复，定向改写（用户确认要改的段落） | 无 |

### 第四类缺陷：写作元指令泄漏（meta-instruction leak）

**现象**：agent 给 LLM 的"写作指令 / 章节规划标记"被模型原样写进小说正文，而非仅作为 prompt 字段。changan 实测形态：

- `【章末悬念】老宦官离开后，李承安在灵堂供桌底下发现了一行用指甲刻出的字迹……`（ch026 结尾）
- `章末悬念：李承安决定夜晚去鬼市调查……`（ch002 / ch029）
- `（章末悬念：周伯的背叛、皇宫里的老者、玉坠的双生之谜）`（ch088 / ch096 / ch119）
- `留下悬念：阴阳平衡能否恢复？人心能否向善？`（ch146）
- 正文叙事里冒出内部章节号引用，如"那个在 ch32 章里把羊皮纸塞进他手里的老伯"（ch045 / ch113 / ch163）

**为什么是缺陷**：章末悬念 / 章节钩子是 **agent→LLM 的指令**，应作为独立 prompt 字段（或 system 提示）下发，绝不该出现在交付给读者的正文中。写进正文既破坏阅读、又暴露写作流程内部信息，是 prompt 污染的典型信号。

**检测（scan）**：

```python
import re
# 实际实现见 guardrails._META_LEAK_RE（剥离 frontmatter 后扫描正文）
META_LEAK = re.compile(
    r'章末悬念|留下悬念|悬念[：:]|本章要求[：:]|写作指令|作者指令|系统指令|写作提示'
    r'|ch\d+章|第\d+章里|第\d+段[：:]'
)
# 对每章正文（剥离 frontmatter）扫描，命中即报告「章节 + 行号 + 片段」
```

可沉淀为 `scripts/scan_meta_leak.py`，并在 `guardrail_scan` 的 `--scope` 增加 `meta` 维度。

**修复（fix，存量）**：这类标记属"元信息"，应**整段删除**（钩子内容不进正文）。用 `scripts/fix_meta_leak.py` 按行删除命中行（含其后的钩子 payload，直到行尾或下一个空行）；正文叙事里的 `ch\d+章` / `第\d+章里` 引用需 LLM 改写为自然叙述（如"周伯在城南老槐树下塞给他玉坠那次"）。

**预防（治本，写时）**：

- Writer 角色（novel-writer skill）在写前准备里新增硬约束：**交付正文禁止包含任何写作元指令**；章末悬念由 Lead/Outline 作为独立字段传入，Writer 只产出纯小说文本。
- `guardrails.py` 新增第 4 条规则 `meta_instruction_leak`（见上表），`autowrite` 落盘前 `gate(block)` 拦截，命中打回重写。
- 写后自检：grep 本章是否含 `章末悬念|留下悬念|（章末悬念` 等标记，命中即判"指令泄漏"剔除后重交。

## agent 侧防御（第五~七类缺陷，2026-08-25 落地于代码）

changan 复盘还暴露三类**根因在 agent 而非小说正文**的缺陷，已修复在写作系统代码层，使下一本书不再复现（小说正文本身按用户要求未改动）：

### 第五类：状态机冻结（pressure_stage 恒为铺垫）

- **现象**：`pressure_stage` 全 164 章中 109 章为「铺垫」，ch001–ch117 全部卡在铺垫，仅 ch118 才转冲突。
- **根因**：`m5_write_chapter._determine_pressure_stage` 在「曲线表缺失」或「章节号未被任何区间覆盖」时**一律回退 `铺垫/低`**，盲信规划产出的平坦曲线（如 setup 覆盖 1-117）。
- **修复**（`workflows/m5_write_chapter.py`）：
  - 解析全部区间，命中区间直接采用；
  - 未命中 → 按章节在全书跨度中的位置推导爬升曲线（`_position_based_stage`：前 15% 铺垫 / 15–50% 冲突 / 50–85% 高潮 / 后 15% 舒缓）；
  - 退化检测：铺垫段占比 > 50% 且存在后续阶段 → 视为平坦曲线，强制按位置推导并 `logger.warning`。
  - `M5_GENERATE_SYSTEM_PROMPT` 第 2 条已要求"本章必须属于当前压力曲线阶段，按阶段控制张力"，与推导结果一致。

### 第六类：路线节点冻结（route_node 恒为 N01）

- **现象**：`route_node` 114/164 为空，其余几乎全是 N01；autowrite 日志每章同写 `route_node:N01` + `subline:S04`。
- **根因**：`m5_write_chapter._load_route_node` 在「章节号未被任何节点范围覆盖」时**静默回退第一个节点（N01）**。
- **修复**（`workflows/m5_write_chapter.py`）：未命中范围时按全书跨度**均匀分配节点**（`idx = chapter/total * node_count`），使路线随章节推进，并 `logger.warning`。

### 第七类：角色状态治理缺失（周伯生死矛盾）

- **现象**：ch049 写周伯「十年前便已故去」，但其角色档案 `characters/仵作周伯.md` 载明其为后期殉职、ch003–ch059 一直在世。
- **根因**：`core/consistency_checker.py` 的 `timeline_conflict`/`relation_conflict` 等规则**原先全是 `_noop_consistency_check` 空壳**，角色生死/时间线从未被强制校验；novel-writer skill 的"写前拉白名单"也未落到代码。
- **双重修复**（写前约束注入 + 写后真实校验，二者互补）：
  - **写前**（治本，`workflows/m5_write_chapter.py` + `prompts.py`）：新增 `_build_character_constraints(subline_data)` 从 `characters/<name>.md` 抽取结构化「状态/生死/时间线」真源，拼成**不可违背硬约束**注入 Writer system prompt（配合 `M5_GENERATE_SYSTEM_PROMPT` 第 9 条"不与 character.md 冲突"、第 13 条"禁止元指令泄漏"）。
  - **写后**（`core/consistency_checker.py`，已将空壳落地为真实规则）：
    - `timeline_conflict`（BLOCK）：比对 `characters/*.md` 生死真源，正文断言某角色「已故/死了/牺牲」但档案为在世或后期才牺牲 → 阻断；死亡断言**归属到正文中最近角色称呼**，避免张冠李戴（已修李承安误报）。
    - `relation_conflict`（WARN）：比对 `relations/graph.md` 活跃边，正文称某角色已故但其关系网仍有互动型活跃边 → 告警。
    - `golden_finger_overstep`（WARN）：比对角色「禁用词」（如周伯禁用 系统/金手指），正文让其触发系统/金手指 → 告警。
    - `realm_overstep`（WARN）：比对 `world.md` 境界体系（仅当显式定义时才触发，防误报），宣称突破至未登记境界 → 告警。
    - 门禁接线：`agentic_pipeline.py` 编辑并联审查原仅为 advisory；现改为 **BLOCK 一致性冲突与 Guardrails 同级**——自动打回 Writer 重写 1 次，仍不过则标记告警保留（不阻断流水线）。`_get_arbiter` 改为按需惰性构造，避免 post-write 校验触发无谓的 LLM/网络初始化。
  - 单测：`tests/test_consistency_checker.py` 覆盖四规则（含周伯式矛盾、金手指、境界越级、关系网），全绿。

> 上述代码修改（guardrails `meta_instruction_leak` + m5 pacing/route/character 修复 + prompts 强化 + consistency_checker 四规则真实化 + pipeline 硬门禁接线）已通过逻辑单测，待 agent 仓提交。

## 标准工作流（runbook）

```bash
cd <NovelAgent>/agent
# 0) 先全量体检，拿到基线数字
python -m agent.cli guardrail-scan -d <PROJECT> --scope junk,title,dup

# 1) 英文残留
python scripts/scan_english.py
python scripts/fix_english.py
python scripts/scan_english.py   # 复扫应趋近 0

# 2) 占位标题
python scripts/fix_titles.py     # 跑完复扫 title 应为 0；漏网/垃圾标题再跑一次自动补齐

# 3) 跨章逐字重复
python scripts/scan_dup.py
python scripts/fix_dup.py        # 两遍（第二遍清残留小簇）

# 4) ≥0.95 近似重复（需人工确认范围后再跑）
python scripts/fix_dup_highsim.py

# 5) 终验
python -m agent.cli guardrail-scan -d <PROJECT> --scope junk,title,dup
```

## 实现要点 / 踩坑记录（务必保留）

1. **英文改写用稳定行 ID 做 key，不要用原文作 JSON key**。早期版本让模型以"原行完整文本"为键，模型轻微改写字符（如 `暗忖`→`暗稔`）导致 `mapping.get(para)` 匹配不上、改了白改。改为 `#行号` 稳定 ID 后正常。
2. **标题改写要剥离模型回吐的"第N章"前缀**，否则出现 `# 第 7 章 · 第7章·鬼市初探` 畸形；检测范围须与护栏对齐——**任何以 `第N章` 开头的标题都算占位**（不止 `第N章·第N章`），否则漏掉 ~23 章。
3. **去重改写必须加英文守卫**：LLM 变体一度回吐 `cessation`，污染已干净正文。检测方法：变体含 `[A-Za-z]{2,}` 即丢弃重试；必要时确定性替换（如 `lantern`→`灯笼`、`cessation`→`止息`）。
4. **去重保留最早出现章为 canonical，后续章换视角/句式**，不要全删（会丢情节）。同一唯一段被复制百次时，53 个唯一段改写即可覆盖全部副本。
5. **长度上限 + 硬上限**：LLM 重写易过度展开（曾出现 1907 字 vs 原 80 字），脚本内设 `budget=max(40, len(old)*1.3)` 软上限、`hard_cap=len(old)*2.2` 硬上限，超限丢弃重试。
6. **幂等**：`fix_dup_highsim.py` 的 `get_para` 在锚点已不存在时返回 None 并跳过，可安全重复运行（已改写的章节不会二次处理）。
7. **API 限流/网络抖动**：所有 LLM 调用包 `call_llm_with_retry`（5 次指数退避 + 90s 超时）；批量脚本降批大小（每批 8 章）+ 章节级 sleep，避免触发限频。
8. **判定边界**：相似度 0.85–0.99 的近似重复属"合理呼应 vs 仍偏雷同"的逐处判断范畴，默认不自动改，先出清单（见下）交用户定夺。
9. **⚠️ 重装配不得丢弃标题行**：用"按空行切段再 join"方式重装配正文时，若切段函数过滤掉 `#` 开头行（`not p.startswith("#")`），会**静默删除章节标题**（`# 第N章 · ...`）。务必保留标题行，或用"前插到 frontmatter 之后"的插入式写法。本次 ch012/ch019/ch146 曾因此丢标题，已用 LLM 重生成标题修复。
10. **⚠️ 去重改写要"远离"基准而非"趋同"基准**：给 LLM 的 canonical 示例若与待改段高度相似，模型可能只改一两个字反而**撞上**基准（如 ch067 赵铁段把 `藏好`→`藏入袖中`，恰与 ch062 基准一致，变成新重复）。要求模型换不同细节/句式/观察角度，且改写后人工 diff 确认与 canonical 不同。
11. **"相似度 1.00"可能是护栏评分假象**：`guardrail_scan` 的 `paragraph_dup` 在某些短段落上会报 1.00，但用护栏自身的 `_normalize_paragraph` + difflib 做逐对复算可能只有 0.5–0.6。**"真正逐字重复"以归一化完全相等为准**（即 `fix_dup.py` 的去重判定方法），它才是可信的清零判据；护栏报告的 0.85–0.999 为判定带，1.00 单点需复算确认。
12. **⚠️ 写作元指令会"泄漏"进正文**：autowrite 把"章末悬念"等钩子作为 prompt 下发时，模型可能把它连同钩子内容一起写进正文（ch026 `【章末悬念】…`、ch002/ch029 `章末悬念：…`、ch088/ch096/ch119 `（章末悬念：…）`、ch146 `留下悬念：…`）。根因是钩子指令与正文生成共用同一输出通道，缺少"元指令不得落盘"的硬约束。修复=整段删标记；治本=新增 `meta_instruction_leak` 护栏规则 + Writer 侧"元指令不入正文"约束（见上「第四类缺陷」）。

## 残留处置约定

- 逐字重复（1.00）→ 自动去重清零。
- ≥0.95 近似重复 → 列出清单（章节对、相似度、片段），用户确认 A 类（场景雷同/明显漏改）自动改、B 类（仪式咒文/合理回忆呼应）保留。
- 0.85–0.95 近似重复 → 默认保留，除非用户明确要改。

## 输出物

- `.state/english_scan.json` / `.state/guardrail_scan_report.md` / `.state/dup_scan_v2.json` / `.state/dedup_fix_manifest.json` / `.state/highsim_fix_manifest.json` / `.state/meta_leak_scan.json` —— 体检与改写清单，供复核。
