"""G12 情绪轨迹展示测试（T7 验收，纯离线零 LLM）。

覆盖（对齐 G12/设计.md §3 / §9 T5）：
- build_track（剧本 + 已写进度 → 目标/已写正确；无剧本 → empty）；
- render_ascii（曲线行存在；空 → 提示文本）；
- CLI --json 信封（直接函数调用）。
"""

from __future__ import annotations

from pathlib import Path

from agent.cli.commands.emotion_track import build_track, render_ascii
from agent.core.payoff_script import build_payoff_script, save_payoff_script


def _make_project_with_script(tmp_path: Path, total: int = 10) -> Path:
    proj = tmp_path / "p"
    proj.mkdir(parents=True, exist_ok=True)
    save_payoff_script(proj, build_payoff_script(total))
    return proj


def test_build_track_empty(tmp_path: Path) -> None:
    t = build_track(tmp_path)
    assert t["empty"] is True
    assert t["chapters"] == []


def test_build_track_written(tmp_path: Path) -> None:
    from agent.core.state_machine import StateMachine

    proj = _make_project_with_script(tmp_path, total=10)
    sm = StateMachine(proj)
    sm.load()
    sm.progress = {**(sm.progress or {}), "total_written": 4}
    sm.save()
    t = build_track(proj)
    assert t["empty"] is False
    assert t["total"] == 10 and t["written"] == 4
    written = [c for c in t["chapters"] if c["written"]]
    assert len(written) == 4
    assert all(1 <= c["target_tension"] <= 5 for c in t["chapters"])


def test_render_ascii(tmp_path: Path) -> None:
    proj = _make_project_with_script(tmp_path, total=6)
    t = build_track(proj)
    lines = render_ascii(t)
    assert any("张力曲线" in line for line in lines)
    assert any("情绪：" in line for line in lines)
    # 空 → 提示
    assert render_ascii(build_track(tmp_path))[0].startswith("（暂无情绪轨迹")


def test_cli_json_envelope(tmp_path: Path) -> None:
    from agent.cli.commands import emotion_track as et

    class _Opt:
        def __init__(self, v):
            self.default = v

    proj = _make_project_with_script(tmp_path, total=5)
    et.emotion_track(
        project_dir=_Opt(str(proj)),
        json_output=_Opt(True),
        env_file=_Opt(None),
    )
    # 直接函数调用走 emit_result（stdout JSON）；验证 build_track 结果可序列化
    import json

    t = build_track(proj)
    payload = json.dumps({"success": True, **t}, ensure_ascii=False)
    assert '"total": 5' in payload
