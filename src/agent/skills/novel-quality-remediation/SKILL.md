---
name: novel-quality-remediation
version: 0.1.0
type: remediation
description: 成书质量体检与修复工作流——针对已生成小说中的英文残留、占位标题、跨章逐字/近似重复三类问题，提供"治本（写时护栏）+ 治标（存量修复脚本）"的完整方案。
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
- 也用于**治本**：确保下一本书在写作阶段就被 `guardrails.py` 拦截，不再产生上述问题。

## 两层防线

### 治本：写时自动校验（guardrails.py，已落地）

`src/agent/core/guardrails.py` 在 `autowrite` 落盘前 `gate(mode="block")` 注入三条规则，命中即打回 Writer 重写 1 次，仍不过则降级告警（写 `.state/chapter_quality_flags.json`）不阻断：

| rule_id | 含义 | 阈值/判定 |
|---|---|---|
| `non_chinese_junk` | 英文/乱码残留 | 剥离 frontmatter 后，连续≥3字母英文 + 特征串（`Visualization failed`/`Cost`/`[system]`/`undefined`/`null`），白名单豁免 |
| `title_placeholder` | 占位标题 | 标题为空 / 长度<4 / `第N章·第N章` / 与 `published_titles` 重复 → error |
| `paragraph_dup` | 跨章重复 | 与全书指纹库比对，相似度≥0.85（仅≥40字长段）→ error |

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

## 标准工作流（runbook）

```bash
cd D:/project/NovelAgent/agent
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

## 残留处置约定

- 逐字重复（1.00）→ 自动去重清零。
- ≥0.95 近似重复 → 列出清单（章节对、相似度、片段），用户确认 A 类（场景雷同/明显漏改）自动改、B 类（仪式咒文/合理回忆呼应）保留。
- 0.85–0.95 近似重复 → 默认保留，除非用户明确要改。

## 输出物

- `.state/english_scan.json` / `.state/guardrail_scan_report.md` / `.state/dup_scan_v2.json` / `.state/dedup_fix_manifest.json` / `.state/highsim_fix_manifest.json` —— 体检与改写清单，供复核。
