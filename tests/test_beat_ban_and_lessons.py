"""回滚率削减 P0 两件套测试（2026-09-10）。

背景（五灵破归档 4 次回滚、重写第 4 轮崩盘的复盘）：
- 重写循环缺"上一版写过什么"的输入 → Writer 用"教学→失败→成功→感动"
  模板连灌 3 章（coherence 85→30）→ 桥段禁用清单注入重写 prompt
- 体检教训只说"哪里错了"不给"怎么做" → Writer 保守灌水 → 正向指引成对注入
- LLM 评委在 5 章窗口挑刺恒非零且含幻觉 → issue 必须附原文定位（quote）
"""

from pathlib import Path
from types import SimpleNamespace

from agent.core.quality.beat_sketch import extract_beat_sketch, render_beats
from agent.core.quality.eval_lessons import (
    _LESSONS_REL,
    guidance_for,
    load_eval_lessons_text,
    save_eval_lessons,
)
from agent.core.quality.scoring.reader_appeal import _count_gated_issues


# ---------------------------------------------------------------- beat_sketch
def test_extract_beat_sketch_basic() -> None:
    """frontmatter 剥离、按段序提取、句末标点截断、max_beats 上限。"""
    body = "\n".join(
        f"---\nchapter: {i}\n---\n\n# 第 {i} 章 · 标题\n\n"
        "赵三蹲在演武场角落里，手里摩挲着灵石。他望着远处，眼中闪过犹豫。\n\n"
        "林凡转过身拿起一张符纸。符纸在阳光下泛着微光。\n\n"
        "他开始演示，运笔极慢。符纸冒起淡烟化为灰烬。\n\n"
        "一片嘘声。散修们纷纷附和。\n\n"
        "他拿起第二张符纸继续画。这一次他更加缓慢。\n\n"
        "当最后一笔落下，符纸光芒一闪。"
        for i in range(1)
    )
    # 15 段正文（用循环生成超出 max_beats 的段落数）
    long_body = "---\nchapter: 1\n---\n\n# 第 1 章\n\n" + "\n\n".join(
        f"第{n}个场景开始，有人物有事件。后续内容。" for n in range(20)
    )
    beats = extract_beat_sketch(long_body, max_beats=5)
    assert len(beats) == 5
    assert all(not b.startswith("#") for b in beats)
    assert all(len(b) <= 36 for b in beats)
    assert "第0个场景" in beats[0]
    # 基础版：frontmatter 与标题不出现
    beats2 = extract_beat_sketch(body)
    assert beats2 and all("第 1 章" not in b for b in beats2)
    assert any("赵三" in b for b in beats2)


def test_extract_beat_sketch_single_paragraph_fallback() -> None:
    """无空行单段长文：按句切分为伪段落。"""
    text = "林凡起身离开茅屋。他独自走向后山禁地。山路陡峭难行全程无话。半路遇见黑衣人拦路去路。"
    beats = extract_beat_sketch(text)
    assert len(beats) == 4
    assert beats[0].startswith("林凡起身离开")


def test_render_beats_empty() -> None:
    assert render_beats([]) == ""
    assert render_beats(["场景一"]).startswith("  - 场景一")


# ---------------------------------------------------------------- eval_lessons
def _mk_report(overall_pass: bool, dims: list[SimpleNamespace], score: float = 50.0):
    return SimpleNamespace(
        dimensions=dims,
        overall_pass=overall_pass,
        score=score,
        appeal={"suggestions": ["钩子再强一点"]},
        golden_three={"suggestions": []},
    )


def _mk_dim(name: str, label: str, issues: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        label=label,
        value=len(issues),
        threshold=0,
        direction="<=",
        passed=not issues,
        evidence=SimpleNamespace(confidence=1.0, issues=issues),
    )


def test_eval_lessons_guidance_and_history(tmp_path: Path) -> None:
    """失败两轮 → history 连续计数 + 正向做法注入；通过 → 清零。"""
    issues = [{"type": "逻辑", "severity": "high", "desc": "巧合推进剧情"}]
    report = _mk_report(False, [_mk_dim("logic_holes", "逻辑漏洞", issues)])
    save_eval_lessons(tmp_path, report)
    save_eval_lessons(tmp_path, report)  # 第二轮：同维度再失败

    text = load_eval_lessons_text(tmp_path)
    assert "已连续 2 轮不达标" in text
    assert "正向做法" in text
    assert "巧合" in guidance_for("logic_holes")

    # 第三轮通过 → lessons 清零、history 归零
    report_pass = _mk_report(True, [_mk_dim("logic_holes", "逻辑漏洞", [])], score=85.0)
    save_eval_lessons(tmp_path, report_pass)
    assert load_eval_lessons_text(tmp_path) == ""
    import json

    data = json.loads((tmp_path / _LESSONS_REL).read_text(encoding="utf-8"))
    assert data["history"] == {}


def test_guidance_fallback_for_unknown_dim() -> None:
    assert guidance_for("unknown_dim_xyz")  # 返回默认指引非空
    assert "对照" in guidance_for("unknown_dim_xyz")


# ---------------------------------------------------------------- quote 过滤
def test_count_gated_issues_quote_filter() -> None:
    """带 quote 的 high/mid 计入；无 quote 的臆测丢弃；全无 quote 回退原口径。"""
    issues = [
        {"severity": "high", "quote": "他忽然会飞了", "desc": "凭空获得能力"},
        {"severity": "mid", "quote": "", "desc": "无定位凭据，应丢弃"},
        {"severity": "low", "quote": "某低危片段", "desc": "low 不计入门禁"},
    ]
    assert _count_gated_issues(issues) == 1.0  # 只有第一条
    # 全部无 quote（旧格式）→ 回退全量计数
    legacy = [{"severity": "high"}, {"severity": "mid"}]
    assert _count_gated_issues(legacy) == 2.0
    assert _count_gated_issues([]) == 0.0


# ---------------------------------------------------------------- beat_ban 注入
def test_build_beat_ban_injection(tmp_path: Path) -> None:
    """重写场景：相邻新章 + 上一版归档的骨架都进清单。"""
    from agent.workflows.writing.agentic_write import AgenticWriteWorkflow

    chapters = tmp_path / "chapters"
    archived = chapters / "_archived" / "rollback_to_151_20260910_150000"
    archived.mkdir(parents=True)
    (chapters / "ch149.md").write_text(
        "---\nchapter: 149\n---\n\n# 第 149 章\n\n赵三教学画符失败十次终于成功。众人欢呼。",
        encoding="utf-8",
    )
    (chapters / "ch150.md").write_text(
        "---\nchapter: 150\n---\n\n# 第 150 章\n\n黑衣人夜间潜入据点被当场抓获。林凡放走他以示警告。",
        encoding="utf-8",
    )
    (archived / "ch151.md").write_text(
        "---\nchapter: 151\n---\n\n# 第 151 章\n\n散修们学习种植灵植。三天后灵草发芽众人感动。",
        encoding="utf-8",
    )

    wf = AgenticWriteWorkflow.__new__(AgenticWriteWorkflow)
    wf.project_dir = str(tmp_path)
    section = wf._build_beat_ban({"chapter_num": 151})
    assert "已用桥段禁用清单" in section
    assert "赵三教学画符" in section  # 相邻新章
    assert "散修们学习种植灵植" in section  # 上一版归档（段首骨架）
    assert "上一版" in section


def test_build_beat_ban_silent_when_no_material(tmp_path: Path) -> None:
    """无相邻章、无归档 → 返回空串（不阻断重写）。"""
    from agent.workflows.writing.agentic_write import AgenticWriteWorkflow

    wf = AgenticWriteWorkflow.__new__(AgenticWriteWorkflow)
    wf.project_dir = str(tmp_path)
    assert wf._build_beat_ban({"chapter_num": 151}) == ""
    assert wf._build_beat_ban({}) == ""
