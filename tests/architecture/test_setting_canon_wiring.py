"""设定台账接线红线（P0-1 补，2026-09-12）。

背景：P0-1 把设定台账（``.state/continuity/setting_canon.json``）渲染成
``ctx["setting_canon"]`` 放进写章上下文，但**没有任何调用点消费它**——
模板没建、生产入口没渲染。这正是本仓反复出现的「哑火接线」缺陷
（同 2026-09-11 复盘里的 ``_validate_evidence`` / M18 / E2 三处）：
数据算出来了、参数传进去了、提示词里却没有这一行，Writer 永远看不到。

本红线把「注入 → 消费」两端钉死：

1. 模板 ``prompts/g/setting_canon_constraint.md`` 必须存在且带占位符；
2. 生产入口 ``agentic_write._build_task`` 必须真的读 ``ctx["setting_canon"]``
   并渲染该模板（源码级断言，避免"加在废弃入口上"）。
"""

from __future__ import annotations

from pathlib import Path

WRITING = Path(__file__).resolve().parents[2] / "src" / "agent" / "workflows" / "writing"


def test_setting_canon_prompt_template_exists_and_renders() -> None:
    """模板存在、带 ``setting_constraints`` 占位符，且渲染后内容真的出现。"""
    from agent.core.infra.prompt_manager import pm

    p = pm.get("g.setting_canon_constraint")
    out = p.render_user(setting_constraints="阵盘 = 净化（ch178）")
    assert "阵盘 = 净化（ch178）" in out, "模板未渲染 setting_constraints —— 占位符缺失"
    assert "设定" in out, "模板正文应说明这是设定硬约束"


def test_setting_canon_consumed_by_production_entry() -> None:
    """生产入口必须消费 ctx["setting_canon"]（否则就是哑火接线）。"""
    src = (WRITING / "agentic_write.py").read_text(encoding="utf-8")
    assert 'ctx.get("setting_canon")' in src, (
        "agentic_write 未读取 ctx['setting_canon'] —— 设定台账注入了但没人消费（哑火接线）"
    )
    assert 'pm.get("g.setting_canon_constraint")' in src, (
        "agentic_write 未渲染 g.setting_canon_constraint —— Writer 看不到已确立设定"
    )


def test_setting_canon_injected_by_context_layer() -> None:
    """m5_context 必须仍然注入该键（否则消费端拿到空串，静默失效）。"""
    src = (WRITING / "m5_context.py").read_text(encoding="utf-8")
    assert '"setting_canon"' in src, "m5_context 未注入 setting_canon"
    assert "_load_setting_canon" in src, "m5_context 缺少 _load_setting_canon 实现"
