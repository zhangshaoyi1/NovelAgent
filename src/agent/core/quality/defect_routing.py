"""缺陷归口表（20261003 复盘·类 4，行动项 3 的数据层）

背景
----
《凡尘炼废》实弹暴露的结构性错位：体检把**正文债务**（重复段落/物品来源/超短章）
记为问题 → 计划审稿人拿去打回**计划** → 改计划修不了正文 → 每批必停死循环。
根因是没有「哪类问题归谁修」的归口表——每个机制自己决定怎么表达和升级问题。

设计
----
- **数据表**：:data:`ROUTING` 把缺陷特征（metric / 问题文本关键词）映射到
  :class:`Route`（归口 owner + 修复通道 + 说明）。归口是**数据**不是 if-elif，
  新缺陷类 = 加一行。
- **owner 语义**：
  - ``plan``：改计划/弧线排布能修（支线安排、伏笔回收调度、档位排布）；
  - ``prose``：正文重写能修（文风、重复、长度、局部逻辑），改计划无意义；
  - ``setting``：需改设定集/真相（跨机制裁决，转人工或设定回滚）；
  - ``human``：机器无修复通道，直接交作者。
- **消费规则**：``plan_owned()`` 供"连批偏离→规划对账评审"使用——只有 plan 归口
  的偏离才值得评审计划；prose 归口的连批偏离应触发的是重写，不是打回计划。
  默认未登记缺陷按 ``prose`` 处理（守势缺省：不确定时不升级到计划层）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

#: 归口 owner 四值
OWNERS = ("plan", "prose", "setting", "human")


@dataclass(frozen=True)
class Route:
    """一条缺陷归口。"""

    owner: str              # plan / prose / setting / human
    fix_channel: str        # 人读：通过什么机制修
    note: str = ""


#: 归口表（顺序即匹配优先级；pattern 对 metric/问题文本做子串匹配，小写比对）
ROUTING: tuple[tuple[str, Route], ...] = (
    # ---- plan 归口：改弧线排布/调度能修 ----
    ("配角停滞", Route("plan", "batch_replan 弧线排布（安排该配角出场/交代去向）",
                      "配角戏份是规划调度问题")),
    ("休眠", Route("plan", "batch_replan 弧线排布（实体名册休眠预警的回应）")),
    ("伏笔", Route("plan", "foresight 状态机调度（安排埋设/回收章）",
                   "回收时机由计划调度")),
    ("账龄", Route("plan", "foresight 状态机调度")),
    ("节奏连续", Route("plan", "PACE_TIERS 档位重排 + pacing_form 形态审计")),
    ("档位", Route("plan", "PACE_TIERS 档位重排")),
    ("高潮", Route("plan", "高潮账本调度（锚区间/蓄势来源重排）")),
    # ---- prose 归口：正文重写能修，打回计划无意义 ----
    ("篇幅", Route("prose", "rewrite 定向扩写/压缩（字数偏离中位数）")),
    ("超短章", Route("prose", "rewrite 定向扩写")),
    ("重复句", Route("prose", "deslop / rewrite 去重")),
    ("套话", Route("prose", "deslop 模板化措辞改写")),
    ("钩子重复", Route("prose", "rewrite 章末钩子改写")),
    ("实体漂移", Route("prose", "rewrite 名称/称谓统一（对照名册）")),
    ("重复度", Route("prose", "deslop / rewrite 去重")),
    # ---- setting 归口：改设定/真相，转裁决 ----
    ("设定冲突", Route("setting", "audit-setting 冲突裁决页（人工裁决后回写设定）",
                      "改设定或改正文需人工裁决")),
    ("真相", Route("setting", "truth_ledger 裁决通道（人工）")),
    # ---- human 归口：无机器修复通道 ----
    ("乱码", Route("human", "rewrite 整章重建（模型输出残骸，须人工确认重建方向）",
                   "残骸章节的重建方向需人确认")),
)

#: 未登记缺陷的守势缺省：按 prose（不升级到计划层）
DEFAULT_ROUTE = Route("prose", "rewrite 定向修复（未登记缺陷，走默认正文通道）",
                      "未在归口表登记——如需升级计划层请先在 ROUTING 登记")


def classify(text: str) -> Route:
    """按 metric 名/问题文本匹配归口（子串、大小写不敏感；未命中走默认）。"""
    low = (text or "").lower()
    for pattern, route in ROUTING:
        if pattern.lower() in low:
            return route
    return DEFAULT_ROUTE


def plan_owned(issues: Iterable[dict[str, Any]]) -> list[str]:
    """从体检 issue 列表中筛出 **plan 归口** 的 rule_id（供规划对账评审消费）。

    非 plan 归口（prose/setting/human）的连批偏离不触发规划评审——
    它们各自的修复通道在 :attr:`ROUTING` 的 fix_channel 里。
    """
    out: list[str] = []
    seen: set[str] = set()
    for it in issues:
        if not isinstance(it, dict):
            continue
        rule_id = str(it.get("metric", "?"))
        if rule_id in seen:
            continue
        text = rule_id + " " + str(it.get("detail", "") or it.get("message", ""))
        if classify(text).owner == "plan":
            seen.add(rule_id)
            out.append(rule_id)
    return out


def tag(text: str) -> str:
    """给一条问题文本打上归口标签（检查点卡/报告透出用）。"""
    route = classify(text)
    return f"[归口:{route.owner}] {text}"


def fix_channel(text: str) -> str:
    """取缺陷的修复通道说明（failure 事件 next_steps / 卡片动作提示用）。"""
    return classify(text).fix_channel
