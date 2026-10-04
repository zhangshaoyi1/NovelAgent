"""DESIGN_EXEMPTION 豁免量级上限单测（登记单 20261004·件 6）。

覆盖：台账计数 >= 3 时 render_for_judge 追加上限文案；无记录时输出与旧版
逐字一致（DESIGN_EXEMPTION 原文仍在、上限文案不在）；record 函数落盘。
"""

from __future__ import annotations

from agent.core.story.design_brief import (
    DESIGN_EXEMPTION,
    DESIGN_EXEMPTION_CAP,
    DesignBrief,
    record_design_exemption_use,
)


def _brief(project_dir) -> DesignBrief:
    """最小评委端 brief：仅 route_track（保证 DESIGN_EXEMPTION 块出现）。"""
    return DesignBrief(
        route_track="【角色弧线轨迹（设计轨，非漂移）】\n隐忍→果敢",
        project_dir=project_dir,
    )


def test_no_records_output_unchanged(tmp_path) -> None:
    text = _brief(tmp_path).render_for_judge()
    # 历史路径零改动：前提原文仍在、上限文案不在
    assert DESIGN_EXEMPTION in text
    assert DESIGN_EXEMPTION_CAP not in text


def test_cap_appended_after_limit(tmp_path) -> None:
    for i in range(3):
        record_design_exemption_use(tmp_path, note=f"issue#{i}")
    text = _brief(tmp_path).render_for_judge()
    assert DESIGN_EXEMPTION in text
    assert DESIGN_EXEMPTION_CAP in text
    # 上限文案在判定前提之后（追加而非替换）
    assert text.index(DESIGN_EXEMPTION) < text.index(DESIGN_EXEMPTION_CAP)


def test_under_limit_no_cap(tmp_path) -> None:
    record_design_exemption_use(tmp_path, note="x")
    record_design_exemption_use(tmp_path, note="y")
    text = _brief(tmp_path).render_for_judge()
    assert DESIGN_EXEMPTION_CAP not in text


def test_project_dir_none_is_zero_use_path() -> None:
    brief = DesignBrief(route_track="轨", project_dir=None)
    assert DESIGN_EXEMPTION_CAP not in brief.render_for_judge()
