"""Phase 5 · Guardrails 硬门禁 离线测试（agent/tests/phase5/test_guardrails_gate.py）"""

import json

from agent.core.guardrails import (
    DEFAULT_GUARDRAIL_CONFIG_PATH,
    GateMode,
    Guardrails,
    build_guardrails,
    load_guardrail_config,
)


def test_default_guardrails_no_false_positive():
    # 默认 Guardrails() 禁用词为空，正常正文不误伤（保持 Phase4 向后兼容）
    g = Guardrails()
    res = g.check("林惊羽拔出长剑，剑光如雪。")
    assert res.passed is True


def test_build_guardrails_loads_default_compliance_words():
    g = build_guardrails()  # 无配置文件 → 默认合规词表
    res = g.check("这是{{未渲染}}模板。")
    assert res.passed is False
    assert any(v.rule_id == "banned_word" for v in res.violations)


def test_advisory_gate_passes_clean_text():
    g = build_guardrails()
    report = g.gate("林惊羽拔出长剑，剑光如雪。", mode=GateMode.ADVISORY)
    assert report.passed is True
    assert report.mode is GateMode.ADVISORY


def test_block_gate_rejects_banned_word():
    g = build_guardrails()
    report = g.gate("章节包含[REDACTED]残留。", mode=GateMode.BLOCK)
    assert report.passed is False
    assert report.mode is GateMode.BLOCK
    assert any(v["rule_id"] == "banned_word" for v in report.violations)


def test_block_gate_auto_cleans_placeholder_and_passes():
    g = build_guardrails()
    draft = "第一章开头。[TODO] 这里待补。结尾正常。"
    report = g.gate(draft, mode=GateMode.BLOCK)
    # 占位残留被自动剥离后可以放行
    assert report.passed is True
    assert report.cleaned is not None
    assert "[TODO]" not in report.text


def test_block_gate_rejects_empty():
    g = build_guardrails()
    report = g.gate("   ", mode=GateMode.BLOCK)
    assert report.passed is False
    assert any(v["rule_id"] == "empty" for v in report.violations)


def test_load_config_missing_file_returns_defaults():
    cfg = load_guardrail_config(".state/__not_exist__.json")
    assert cfg["mode"] == GateMode.ADVISORY.value
    assert "{{" in cfg["banned_words"]
    assert cfg["max_chars"] is None


def test_load_config_from_file_overrides(tmp_path):
    p = tmp_path / "guardrails.json"
    p.write_text(
        json.dumps(
            {
                "mode": "block",
                "banned_words": ["暴力", "色情"],
                "max_chars": 5000,
                "min_chars": 200,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = load_guardrail_config(str(p))
    assert cfg["mode"] == "block"
    assert cfg["banned_words"] == ["暴力", "色情"]
    assert cfg["max_chars"] == 5000
    g = build_guardrails(str(p))
    res = g.check("这段含有暴力描写。", max_chars=5000)
    assert res.passed is False


def test_load_config_corrupt_falls_back(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    cfg = load_guardrail_config(str(p))
    assert cfg["mode"] == GateMode.ADVISORY.value
    assert "{{" in cfg["banned_words"]


def test_default_config_path_constant():
    assert DEFAULT_GUARDRAIL_CONFIG_PATH == ".state/guardrails.json"


def test_pipeline_hard_gate_blocks_publication(tmp_path):
    """硬门禁集成：命中禁用词的章节被拒绝发布并终止流水线。"""
    import tempfile

    from agent.core.guardrails import Guardrails
    from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow

    class _StubChapter:
        chapter_num = 1
        chapter_text = "这一章含有违规词内容。"
        chapter_title = "第一章"

    class _StubWriter:
        def run(self):
            return _StubChapter()

    class _StubMemory:
        def record_chapter(self, *a, **k):
            raise AssertionError("BLOCK 模式不应写记忆（章节被拒）")

    gr = Guardrails(banned_words=["违规词"])
    wf = AgenticPipelineWorkflow(
        project_dir=tmp_path,
        llm_client=None,
        target_chapters=1,
        eval_enabled=False,
        guardrails=gr,
        gate_mode="block",
        writer_workflow=_StubWriter(),
        memory=_StubMemory(),
    )
    result = wf.run()
    assert result.blocked is True
    assert result.chapters_written == 0
    assert "违规词" in result.block_reason or result.block_reason


def test_pipeline_advisory_mode_does_not_block(tmp_path):
    """advisory 默认模式：告警但不阻断出章。"""
    from agent.core.guardrails import Guardrails
    from agent.workflows.agentic_pipeline import AgenticPipelineWorkflow

    class _StubChapter:
        chapter_num = 1
        chapter_text = "这一章含有违规词内容。"
        chapter_title = "第一章"

    class _StubWriter:
        def run(self):
            return _StubChapter()

    class _StubMemory:
        wrote = False

        def record_chapter(self, *a, **k):
            self.wrote = True

    gr = Guardrails(banned_words=["违规词"])
    mem = _StubMemory()
    wf = AgenticPipelineWorkflow(
        project_dir=tmp_path,
        llm_client=None,
        target_chapters=1,
        eval_enabled=False,
        guardrails=gr,
        gate_mode="advisory",  # 默认
        writer_workflow=_StubWriter(),
        memory=mem,
    )
    result = wf.run()
    assert result.blocked is False
    assert mem.wrote is True  # advisory 仍正常出章并写记忆
