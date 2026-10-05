"""缺陷归口表红线（20261003 复盘·类 4：一个发现 → 一类解决）。

守卫：
- **分类正确性**：正文缺陷（篇幅/套话/重复/漂移）→ prose；调度缺陷
  （配角停滞/伏笔账龄/档位/高潮）→ plan；设定/真相 → setting；乱码 → human。
- **消费规则**：plan_owned() 只放行 plan 归口——prose 归口的连批偏离
  （实弹《凡尘炼废》死循环根因）不得触发规划评审。
- **接线在链**：book_checkup 的连批升级必须经过 plan_owned 过滤（静态红线）；
  检查点卡 risks 必须带归口标签。
- **守势缺省**：未登记缺陷按 prose（不升级到计划层）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.quality.defect_routing import (
    DEFAULT_ROUTE,
    classify,
    fix_channel,
    plan_owned,
    tag,
)


# ---------------------------------------------------------------- 分类
@pytest.mark.parametrize("text,owner", [
    ("ch017 篇幅 154 字偏离中位数", "prose"),
    ("章尾套话 12 处", "prose"),
    ("重复句占比 0.3", "prose"),
    ("实体漂移：林远/林远尘混用", "prose"),
    ("配角停滞：仇天连续 8 章未出场", "plan"),
    ("实体休眠预警：仇天", "plan"),
    ("伏笔账龄：F3 已 25 章未回收", "plan"),
    ("节奏连续：连续 5 章同档位", "plan"),
    ("高潮事件逾期未引爆", "plan"),
    ("设定冲突：境界体系矛盾", "setting"),
    ("真相断言被静默推翻", "setting"),
    ("第17章全文为乱码", "human"),
])
def test_classify_owners(text: str, owner: str) -> None:
    assert classify(text).owner == owner


def test_unregistered_defaults_to_prose() -> None:
    """守势缺省：未登记缺陷不升级到计划层。"""
    assert classify("某种全新缺陷").owner == "prose"
    assert DEFAULT_ROUTE.owner == "prose"


def test_plan_owned_filters_prose_issues() -> None:
    issues = [
        {"metric": "章尾套话", "detail": "12 处"},
        {"metric": "ch_length", "detail": "ch019 篇幅 1196 字偏离中位数"},
        {"metric": "side_char_stall", "detail": "配角停滞：仇天 8 章未出场"},
        {"metric": "foreshadow_aging", "detail": "F3 账龄 25 章"},
    ]
    assert plan_owned(issues) == ["side_char_stall", "foreshadow_aging"]


def test_fix_channel_and_tag() -> None:
    assert "rewrite" in fix_channel("ch019 篇幅偏离")
    tagged = tag("配角停滞：仇天")
    assert tagged.startswith("[归口:plan]")


# ---------------------------------------------------------------- 接线在链
def test_book_checkup_streak_routes_through_plan_owned() -> None:
    """连批升级规划评审必须经过归口过滤（静态红线，防归口错位回归）。"""
    src = (
        Path(__file__).resolve().parents[1]
        / "src" / "agent" / "core" / "quality" / "book_checkup.py"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "plan_owned" in src
    i_filter = src.index("hit_rules = plan_owned(")
    i_review = src.index("review_plan_diff(")
    assert i_filter < i_review, "归口过滤必须发生在规划评审触发之前"


def test_checkpoint_card_tags_routes() -> None:
    """检查点卡 risks 组装必须经归口标签（作者看卡即知归谁修）。"""
    src = (
        Path(__file__).resolve().parents[1]
        / "src" / "agent" / "workflows" / "pipeline" / "agentic_pipeline_events.py"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "defect_routing import tag" in src
