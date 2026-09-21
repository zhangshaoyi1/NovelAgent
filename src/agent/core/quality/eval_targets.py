"""七维「不崩」合格线的**唯一真源**（纪律 #19）

为什么单独成模块
----------------
这七个默认值此前在**两处各写一份**，且两处的注释都写着「**与 XX 默认两处同步**」：

- ``agents/evaluator.py`` —— ``self.qt`` 的 ``qt.get(k, 默认值)``
- ``agents/planner.py`` —— ``QualityTargets`` 的 pydantic ``Field(default=...)``

**用注释担保一致性**正是纪律 #19 点名的反模式：注释不参与编译、不参与断言，
G2 那次「80→85 / 75→80」的收紧如果只改了一边，运行时会以**哪一处生效**为准
取决于调用路径（planner 产出 plan → evaluator 读 ``quality_targets``），
静默分叉且无任何测试会发现。

★ 哪些是 provider 敏感项（本模块的存在理由）
--------------------------------------------
- ``COHERENCE_MIN`` / ``READABILITY_MIN`` / ``HARD_DIM_MAX`` / ``LOGIC_HOLES_MAX``
  —— 分值由 **LLM 打分**产生 ⇒ 换 provider 后分布可能整体位移，
  **绝对阈值会随之系统性假失败或假通过**。敏感面必须只有一处可改。
- ``FORESHADOW_RECYCLE_MIN`` / ``PACING_ABNORMAL_MAX`` —— **确定性**计算
  （从 ``foreshadows.md`` 与章节字数直接算出）⇒ **provider 无关**。

登记表与抽检结论见 ``core/quality/provider_sensitivity.py``。
改动任一值等于同时改「规划期承诺」与「批末验收」两处口径。
"""

from __future__ import annotations

#: 人设硬伤（高严重度）条数上限 —— ``=0`` 不可放宽（LLM 判定）
HARD_DIM_MAX = 0
#: 设定一致性（高严重度）冲突条数上限 —— ``=0`` 不可放宽（LLM 判定）
SETTING_HARD_DIM_MAX = 0
#: 逻辑漏洞条数上限 —— ``=0`` 不可放宽（LLM 判定）
LOGIC_HOLES_MAX = 0
#: 伏笔回收率下限（**确定性**：从 foreshadows.md 直接算 ⇒ provider 无关）
FORESHADOW_RECYCLE_MIN = 0.90
#: 连贯性自评下限（/100，**LLM 打分** ⇒ provider 敏感）
COHERENCE_MIN = 85.0
#: 追读力综合评分下限（/100，**LLM 打分** ⇒ provider 敏感）
READABILITY_MIN = 80.0
#: 异常章节（注水/赶进度）比例上限（**确定性** ⇒ provider 无关）
PACING_ABNORMAL_MAX = 0.03

__all__ = [
    "HARD_DIM_MAX",
    "SETTING_HARD_DIM_MAX",
    "LOGIC_HOLES_MAX",
    "FORESHADOW_RECYCLE_MIN",
    "COHERENCE_MIN",
    "READABILITY_MIN",
    "PACING_ABNORMAL_MAX",
]
