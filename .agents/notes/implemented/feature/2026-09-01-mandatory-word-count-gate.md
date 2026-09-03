# Agent Note: 强制字数门禁

Status: implemented

## Problem

模拟推进多章后发现 ch196 等章节正文中途截断（仅 662 字），但质量门禁误判通过落盘。
原质检完全依赖 LLM 自觉（m5 `quality_check` 提示词 / D 维 LLM 评审），字数不足这类
确定性缺陷 LLM 无法稳定拦住，导致残缺短章入库。

## Decision

新增**纯规则、确定性**的字数硬关卡，不依赖 LLM：

1. `quality_checker.py`：

   * 新增模块常量 `MIN_CJK_WORDS = 2000`。

   * 新增 `_count_cjk(text)` 统计中文字数（与 `count_words` 工具口径一致，
     区间 `\u4e00-\u9fff`）。

   * 新增 `_check_word_count` 规则检查：中文字数 < 下限即报 `BLOCK` 级 Issue。

   * 在 `_register_common_rules` 的通用层注册 `word_count` 规则（severity=BLOCK）。
     该路径覆盖 `quality_check` 工具 → WriterAgent 默认门禁及 `QualityChecker.check`。

2. `m5_write_chapter.py`：在 `_quality_check_and_revise` 修订循环中，主 LLM 校验之后、
   `overall_pass` 判定之前，追加确定性字数硬关卡：

   * `_count_cjk(text)` 低于 `MIN_CJK_WORDS` 即强制 `overall_pass=False`，追加
     `word_count` 未通过规则与扩写建议，并把「硬性扩写指令」拼接进修订提示词
     （要求扩写补齐、禁止重复段/空行充数）。

   * 该关卡不依赖 `enable_structured_qc` 开关，随主修订循环恒生效。

## Alternatives considered

### Why not 仅在 LLM 校验提示里提高篇幅要求、继续依赖 LLM 扩写？

LLM 对"字数不足"这类数字型缺陷不稳定，且截断场景下可能反复输出同样残缺内容，
无法作为确定性门禁。

### Why not 仅依赖 `quality_check` 工具路径（WriterAgent）？

m5 默认 `enable_structured_qc=False`，`QualityChecker.check` 不参与主修订循环；
故需在 m5 主循环内单独加一道硬关卡，才能兜住 M5 写章路径。

## Consequences

* 收益：字数不足的截断短章在修订循环内即被判不通过并触发扩写，杜绝残缺内容落盘；
  纯规则判定零网络、稳定复现、可单测。

* 代价：所有章节都必须达到 `MIN_CJK_WORDS`（2000 中文字）门槛，若既有大纲设定每章
  篇幅低于此值需复核该常量；属于全局质量基线收紧，可能轻微提升单章耗时/成本。

* 兼容：`MIN_CJK_WORDS` 集中在 quality\_checker.py 常量，便于按题材/项目调整。

