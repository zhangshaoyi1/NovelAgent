"""M5 质检常量与评审校准（2026-09-16 瘦身）

本模块原为 ``M5QualityGateMixin``（质量校验 + 自动修订 + D 多维审查），
随废弃写章入口 ``M5WriteChapterWorkflow.run()`` 的删除一并清理 —— 章内质检+修订
已收敛到**唯一入口** ``AgenticWriteWorkflow``（``workflows/writing/agentic_write.py``）。

保留内容（生产入口直接消费，删不得）：

- 金三写时门禁 / 修订上限常量（``agentic_write`` 直接 import）；
- :meth:`M5QualityGateMixin._stage_calibration` —— 评审校准口径的唯一真源，
  ``agentic_write`` 以 ``M5WriteChapterWorkflow._stage_calibration(...)`` 形式调用。

删除依据（登记单 ``20260916_闸门信号可达性普查`` §三.C2）
----------------------------------------------------------
原 ``_quality_check_and_revise`` 在质检 JSON 解析失败时降级
``overall_pass=True`` 且**无任何留痕**（比 C1 的写时门禁更裸：连 ``gate_skipped``
标记都没有）。该路径**只有废弃入口可达** ⇒ 按拍板「废弃即删除」清理，
而非给一条不可达路径补留痕。
"""

from __future__ import annotations

import logging
from typing import Any

# ★ 唯一真源（纪律 #19）：本文件此前用注释「与 B4 golden_three_threshold 默认一致」
#   担保一致性 —— 注释不参与断言，单边改名即双向破裂。
from agent.core.quality.golden_policy import SIX_DIM_FLOOR, SIX_DIM_PASS_LINE

logger = logging.getLogger(__name__)

MAX_REVISIONS = 2

# 金三写时门禁（2026-09-12）：前三章落盘前必须过读者吸引力六维门禁。
# 此前金三只在批末评估（evaluator golden gate），写时 9 项规则质检不含吸引力维度，
# 导致低质量开局照样落盘、批末必然熔断且修不到开头（五灵破 19+7 次实证）。
GOLDEN_WRITE_GATE_FIRST_N = 3
GOLDEN_WRITE_GATE_TOTAL = SIX_DIM_PASS_LINE  # 综合合格线（派生自唯一真源，非本文件字面量）
GOLDEN_WRITE_GATE_FLOOR = SIX_DIM_FLOOR      # 单维触底线（同上）


class M5QualityGateMixin:
    """评审校准辅助（原质量闸主体已随废弃入口删除，见模块 docstring）。"""

    @staticmethod
    def _stage_calibration(ctx: dict[str, Any], attempt: int) -> str:
        """提速·评审校准：开篇/铺垫章不以中后期节奏苛求，复审聚焦上轮失败项，
        减少开篇章被反复打回的无效修订轮（不影响 no_english/字数等硬关卡）。"""
        notes: list[str] = []
        ch = ctx.get("chapter_num", 0)
        stage = str(ctx.get("pressure_stage") or "")
        if ch and ch <= 3:
            notes.append(
                f"本章为开篇章节（第{ch}章）：允许世界观/人物铺垫占比略高，"
                "节奏类规则以「开篇钩子是否成立、关键信息是否清晰」为准，"
                "不以中后期高强度节奏苛求。"
            )
        elif stage and "铺垫" in stage:
            notes.append(
                f"本章为铺垫章节（压力阶段：{stage}）：允许节奏放缓，"
                "重点审查开篇钩子、角色一致性与章末悬念。"
            )
        if attempt > 0:
            notes.append(
                f"本次为第 {attempt} 次修订后的复审：确认上轮未通过项已解决即可，"
                "不要为锦上添花引入新的否决项。"
            )
        return "\n".join("- " + n for n in notes) if notes else "（无特殊校准，按常规标准评审）"
