"""M20 长篇拆文管线单元测试（离线，mock LLMClient）

覆盖：
- workflow 注册（m20_analyze）
- 章节切分 split_chapters / 中文数字 _cn_to_int
- Stage 0 概要提取产物（概要.md / 章节/章节索引.md）
- 默认运行在 Stage 1 停靠（快速预览.md + paused_after_stage1）
- full 全量跑通 6 阶段（全部产物 + LLM 调用次数）
- 断点续跑：paused_after_stage1 后续跑跳过 Stage 0/1，从 Stage 2 开始
- Stage 2 单章摘要失败容忍（失败记入 _progress.md 不阻断）
- CLI analyze --json 输出结构 / 缺失原文错误信封
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from typer.testing import CliRunner

from agent.client.gateway_adapter import GatewayAdapter, create_gateway_adapter, LLMResponse
from agent.core.engine.workflow_registry import get_workflow
from agent.workflows.m20_analyze import (
    M20AnalyzeWorkflow,
    _cn_to_int,
    _is_chapter_header,
    split_chapters,
)


# ============================================================
# 假数据
# ============================================================
SAMPLE_SOURCE = """第1章 觉醒
寒风割过绝灵崖的枯木，林寻伏在血泊里，丹田破碎。
「计算完毕，存活率 0.13%。」太虚镜的声音冰冷如机械。

第2章 入门
宗门长老审视着林寻，目光如电。
「你从何而来？」

第3章 试炼
试炼场中，林寻一拳轰出。空气炸裂。
远处执法堂的铃声响了。
"""

OUTLINE_JSON = {
    "genre": "玄幻",
    "platform": "起点",
    "summary": "寒门少年林寻唤醒太虚镜，在宗门垄断的修仙世界中以推演破局，"
    "历经入门试炼与逃亡，逐步揭开宗门奴役寒门弟子的真相。",
    "volumes": [{"name": "开篇·入宗", "chapters": "1-3", "count": 3, "words": "约0.1万字"}],
    "protagonist": "林寻，寒门少年",
    "core_gimmick": "太虚镜推演破局",
}

AGGREGATE_JSON = {
    "framework": {
        "framework_type": "升级流",
        "core_driver": "推演破局",
        "spine_conflict": "寒门vs宗门",
        "upgrade_mechanism": "境界递进",
        "rhythm_pattern": "铺垫→冲突→爽点→新悬念",
        "basis": "基于逐章摘要判断",
    },
    "plots": [
        {
            "title": "宗门入门之争",
            "type": "战斗",
            "summary": "林寻带伤通过宗门入门试炼，与执法堂结怨，为后续逃亡埋下伏笔。",
            "goal": "通过入门试炼",
            "conflict": "与执法堂冲突",
            "chapter_range": "第1-3章",
            "structure": {"铺垫": "1", "发展": "2", "高潮": "3", "收尾": "-"},
            "plot_points": [{"chapter": 1, "desc": "林寻唤醒太虚镜"}],
        }
    ],
    "storylines": [
        {
            "title": "太虚镜觉醒线",
            "type": "成长线",
            "description": "太虚镜从工具逐渐觉醒自主意识，林寻的推演能力随境界提升。",
            "themes": ["成长"],
            "plot_titles": ["宗门入门之争"],
        }
    ],
    "characters": [
        {
            "name": "林寻",
            "archetype": "protagonist",
            "appearance_chapters": 3,
            "total_chapters": 3,
            "ratio": 1.0,
            "aliases": [],
            "note": "主角",
        }
    ],
    "orphan_ratio": 0.0,
    "coverage": 1.0,
    "confidence": 1.0,
    "orphan_notes": "无",
    "quality_gate": {"coverage_ok": True, "confidence_ok": True, "issues": []},
}

SETTING_JSON = {
    "worldview": {
        "type": "奇幻",
        "power_system": "炼气→筑基",
        "geography": "宗门",
        "factions": ["执法堂"],
        "core_rules": "宗门垄断修炼资源",
        "special": "-",
        "reference_chapters": [1],
    },
    "golden_finger": {
        "type": "artifact",
        "name": "太虚镜",
        "description": "以精血驱动，可推演生路与功法暗门，随境界提升解锁新能力。",
        "core_mechanism": "精血推演",
        "current_abilities": "推演生路",
    },
    "characters": [
        {
            "name": "林寻",
            "archetype": "protagonist",
            "profile": "寒门少年，丹田破碎后唤醒太虚镜，性格坚韧，选择笨路以伤换机。",
            "key_plots": ["唤醒太虚镜", "通过试炼"],
            "arc": "从绝境到初具自保之力",
            "aliases": [],
        }
    ],
    "relations": [
        {
            "a": "林寻",
            "b": "执法堂",
            "relation_type": "敌人",
            "emotion": "负面",
            "description": "宗门垄断下与执法堂的对立。",
            "evolution": "",
            "inferred": False,
            "confidence": 0.9,
        }
    ],
}

GOLDEN_TEXT = """# 深度拆解

**核心信息**：字数约1000字 | 核心事件：林寻唤醒太虚镜

## 开篇钩子
类型：悬念 | 手法：绝境开局 | 效果：强

## 可借鉴要素
绝境开局 + 信息差碾压
"""

PREVIEW_TEXT = """# 快速预览：测试书

## 基本信息
书名 | 题材 | 总章数 | 总字数 | 目标平台

## 黄金三章评分
| 维度 | 评分(1-5) | 说明 |
|------|----------|------|
| 开篇钩子 | 5 | 绝境开局 |

## 是否值得全量拆解 — 建议
建议继续全量拆解。
"""

SUMMARY_TEXT = """**概要**：林寻在绝灵崖被重创，太虚镜苏醒。
**关键事件**：1.林寻被袭 2.太虚镜苏醒 3.林寻选择笨路
**出场人物**：| 角色 | 本章重要性 | 别名 | 本章表现 |
|------|----------|------|------|
| 林寻 | major | 无 | 苏醒并选择笨路 |
**情节点**：
P1 **太虚镜苏醒**：类型{信息揭示} | 林寻唤醒太虚镜 | 涉及{林寻} | 地点 | 物品{太虚镜} | 时间
**主题标签**{热血} | 基调：{紧张}
"""

REPORT_TEXT = """# 拆文报告

## 基本信息
测试书 | 玄幻 | 3 | 1000 | 起点

## 黄金三章评分
| 维度 | 评分(1-5) | 说明 |
|------|----------|------|
| 开篇钩子 | 5 | |

## 结构分析
主线 / 副线 / 剧情条 / 覆盖率

## 可借鉴套路
1.绝境开局
"""


def make_mock_llm() -> MagicMock:
    """构造按 system 提示词路由的 mock LLM：
    JSON 阶段（概要/聚合/设定）返回合法 JSON，Markdown 阶段返回对应文本。"""
    llm = MagicMock(spec=GatewayAdapter)

    def _side_effect(messages=None, **kwargs):
        system = ""
        if messages:
            first = messages[0]
            system = first.get("content", "") if isinstance(first, dict) else ""
        if "提取全书概要" in system:
            text = json.dumps(OUTLINE_JSON, ensure_ascii=False)
        elif "跨章聚合分析" in system:
            text = json.dumps(AGGREGATE_JSON, ensure_ascii=False)
        elif "提取世界观设定与角色关系" in system:
            text = json.dumps(SETTING_JSON, ensure_ascii=False)
        elif "单章进行深度拆解" in system:
            text = GOLDEN_TEXT
        elif "早期判断" in system:
            text = PREVIEW_TEXT
        elif "原子提取" in system:
            text = SUMMARY_TEXT
        elif "汇总为终态" in system:
            text = REPORT_TEXT
        else:
            text = "OK"
        return LLMResponse(text=text)

    llm.chat.side_effect = _side_effect
    return llm


def _make_workflow(
    tmp_path: Path,
) -> tuple[M20AnalyzeWorkflow, MagicMock, Path, Path]:
    """搭建带 3 章原文的拆文目录 + mock LLM + 工作流。"""
    d = tmp_path / "p"
    d.mkdir(parents=True)
    source = d / "novel.txt"
    source.write_text(SAMPLE_SOURCE, encoding="utf-8")
    llm = make_mock_llm()
    wf = M20AnalyzeWorkflow(project_dir=d, llm=llm)
    return wf, llm, d, source


# ============================================================
# 工作流注册
# ============================================================
class TestRegistry:
    def test_workflow_registered(self) -> None:
        assert get_workflow("m20_analyze") is M20AnalyzeWorkflow


# ============================================================
# 章节切分 / 数字解析
# ============================================================
class TestChapterSplit:
    def test_split_by_header(self) -> None:
        chapters = split_chapters("第1章 觉醒\n正文一\n第2章 入门\n正文二\n")
        assert len(chapters) == 2
        assert chapters[0]["number"] == 1
        assert chapters[0]["title"] == "第1章 觉醒"
        assert chapters[0]["text"] == "正文一"
        assert chapters[1]["number"] == 2
        assert chapters[1]["text"] == "正文二"

    def test_split_no_header_single_chapter(self) -> None:
        chapters = split_chapters("没有章节标题的整段文本")
        assert len(chapters) == 1
        assert chapters[0]["number"] == 1
        assert chapters[0]["title"] == "第1章"

    def test_is_chapter_header(self) -> None:
        assert _is_chapter_header("第1章 觉醒")
        assert _is_chapter_header("第 12 回 风云")
        assert _is_chapter_header("第三章 入门")
        assert not _is_chapter_header("章节回顾")

    def test_cn_to_int(self) -> None:
        assert _cn_to_int("三") == 3
        assert _cn_to_int("二十") == 20
        assert _cn_to_int("一百二十三") == 123
        assert _cn_to_int("105") == 105


# ============================================================
# Stage 0 概要提取
# ============================================================
class TestStage0:
    def test_stage0_writes_outline(self, tmp_path: Path) -> None:
        wf, _, d, source = _make_workflow(tmp_path)
        result = wf.run(source=source, stage=0, full=True)
        assert result.success
        out = d / "deconstruction" / "p"
        assert (out / "概要.md").exists()
        assert (out / "章节" / "章节索引.md").exists()
        content = (out / "概要.md").read_text(encoding="utf-8")
        assert "玄幻" in content
        assert "总章数：3" in content

    def test_invalid_stage_raises(self, tmp_path: Path) -> None:
        wf, _, d, source = _make_workflow(tmp_path)
        try:
            wf.run(source=source, stage=9, full=True)
            assert False, "应抛出非法阶段错误"
        except ValueError as e:
            assert "非法阶段" in str(e)


# ============================================================
# Stage 1 停靠
# ============================================================
class TestPauseAtStage1:
    def test_default_run_pauses_after_stage1(self, tmp_path: Path) -> None:
        wf, llm, d, source = _make_workflow(tmp_path)
        result = wf.run(source=source)
        assert result.paused is True
        assert result.status == "paused_after_stage1"
        assert result.completed_stages == [0, 1]
        # 1 概要 + 3 黄金三章 + 1 快速预览
        assert llm.chat.call_count == 5
        out = d / "deconstruction" / "p"
        assert (out / "快速预览.md").exists()
        for n in (1, 2, 3):
            assert (out / "章节" / f"第{n}章_深度拆解.md").exists()
        # 停靠时不生成 Stage 2 产物
        assert not (out / "章节" / "第1章_摘要.md").exists()


# ============================================================
# full 全量跑通
# ============================================================
class TestFullPipeline:
    def test_full_pipeline_generates_all_artifacts(self, tmp_path: Path) -> None:
        wf, llm, d, source = _make_workflow(tmp_path)
        result = wf.run(source=source, full=True)
        assert result.success
        assert result.status == "completed"
        assert result.paused is False
        assert result.completed_stages == [0, 1, 2, 3, 4, 5]
        # 1 概要 + 4 黄金三章/预览 + 3 摘要 + 1 聚合 + 1 设定 + 1 报告
        assert llm.chat.call_count == 11
        out = d / "deconstruction" / "p"
        assert (out / "概要.md").exists()
        assert (out / "快速预览.md").exists()
        assert (out / "拆文报告.md").exists()
        assert (out / "剧情" / "故事线.md").exists()
        assert (out / "剧情" / "宗门入门之争.md").exists()
        assert (out / "剧情" / "散落情节.md").exists()
        assert (out / "设定" / "世界观.md").exists()
        assert (out / "设定" / "金手指.md").exists()
        assert (out / "角色" / "林寻.md").exists()
        assert (out / "角色" / "角色关系.md").exists()
        assert (out / "_progress.md").exists()
        for n in (1, 2, 3):
            assert (out / "章节" / f"第{n}章_摘要.md").exists()

    def test_full_pipeline_progress_markdown(self, tmp_path: Path) -> None:
        wf, _, d, source = _make_workflow(tmp_path)
        wf.run(source=source, full=True)
        text = (d / "deconstruction" / "p" / "_progress.md").read_text(
            encoding="utf-8"
        )
        assert "最终状态：completed" in text
        assert "| Stage 0 | done" in text
        assert "| Stage 5 | done" in text


# ============================================================
# 断点恢复
# ============================================================
class TestBreakpointResume:
    def test_resume_skips_stage01(self, tmp_path: Path) -> None:
        wf, llm, d, source = _make_workflow(tmp_path)
        r1 = wf.run(source=source)  # paused_after_stage1
        assert r1.paused
        calls_after_pause = llm.chat.call_count  # 5

        r2 = wf.run(full=True)  # 续跑：跳过 0/1，从 Stage 2 开始
        assert r2.success
        assert r2.status == "completed"
        assert r2.completed_stages == [2, 3, 4, 5]
        # 续跑只增 3 摘要 + 1 聚合 + 1 设定 + 1 报告 = 6 次调用
        assert llm.chat.call_count == calls_after_pause + 6
        out = d / "deconstruction" / "p"
        assert (out / "拆文报告.md").exists()
        # 不重新生成 Stage 0/1（原文件保留）
        content = (out / "概要.md").read_text(encoding="utf-8")
        assert "玄幻" in content


# ============================================================
# Stage 2 单章失败容忍
# ============================================================
class TestStage2Tolerance:
    def test_single_chapter_failure_does_not_block(self, tmp_path: Path) -> None:
        d = tmp_path / "p"
        d.mkdir(parents=True)
        source = d / "novel.txt"
        source.write_text(SAMPLE_SOURCE, encoding="utf-8")
        llm = make_mock_llm()
        real_side = llm.chat.side_effect

        def failing_side(messages=None, **kwargs):
            system = (
                messages[0].get("content", "") if messages and isinstance(messages[0], dict) else ""
            )
            user = (
                messages[1].get("content", "") if len(messages) > 1 and isinstance(messages[1], dict) else ""
            )
            if "原子提取" in system and "第2章" in user:
                raise RuntimeError("mock 摘要失败")
            return real_side(messages, **kwargs)

        llm.chat.side_effect = failing_side
        wf = M20AnalyzeWorkflow(project_dir=d, llm=llm)
        result = wf.run(source=source, stage=2, full=True)
        assert result.success
        out = d / "deconstruction" / "p"
        assert (out / "章节" / "第1章_摘要.md").exists()
        assert not (out / "章节" / "第2章_摘要.md").exists()
        assert (out / "章节" / "第3章_摘要.md").exists()
        progress_text = (out / "_progress.md").read_text(encoding="utf-8")
        assert "第2章" in progress_text  # 失败已记入 _progress.md


# ============================================================
# CLI --json
# ============================================================
class TestCLI:
    def _patch_llm(self, monkeypatch, mock: MagicMock) -> None:
        zero_arg = lambda *a, **kw: mock  # noqa: E731
        monkeypatch.setattr("agent.client.gateway_adapter.create_gateway_adapter", zero_arg)
        monkeypatch.setattr("agent.workflows.m20_analyze.create_gateway_adapter", zero_arg)

    def test_analyze_json_structure(self, tmp_path: Path, monkeypatch) -> None:
        from agent.cli import app

        d = tmp_path / "p"
        d.mkdir(parents=True)
        source = d / "novel.txt"
        source.write_text(SAMPLE_SOURCE, encoding="utf-8")
        self._patch_llm(monkeypatch, make_mock_llm())
        runner = CliRunner()
        result = runner.invoke(
            app, ["analyze", "--json", "-d", str(d), "--source", str(source), "--full"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["success"] is True
        for key in (
            "book",
            "total_chapters",
            "output_dir",
            "start_stage",
            "completed_stages",
            "failures",
            "paused",
            "status",
        ):
            assert key in data, f"analyze --json 缺少字段 {key}"
        assert data["status"] == "completed"
        assert data["total_chapters"] == 3
        assert data["completed_stages"] == [0, 1, 2, 3, 4, 5]
        assert (Path(data["output_dir"]) / "拆文报告.md").exists()

    def test_analyze_json_pause_default(self, tmp_path: Path, monkeypatch) -> None:
        """不带 --full 时默认停靠 Stage 1，JSON 含 paused/status。"""
        from agent.cli import app

        d = tmp_path / "p"
        d.mkdir(parents=True)
        source = d / "novel.txt"
        source.write_text(SAMPLE_SOURCE, encoding="utf-8")
        self._patch_llm(monkeypatch, make_mock_llm())
        runner = CliRunner()
        result = runner.invoke(
            app, ["analyze", "--json", "-d", str(d), "--source", str(source)]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["success"] is True
        assert data["paused"] is True
        assert data["status"] == "paused_after_stage1"

    def test_analyze_json_missing_source_error(self, tmp_path: Path, monkeypatch) -> None:
        from agent.cli import app

        d = tmp_path / "p"
        d.mkdir(parents=True)
        self._patch_llm(monkeypatch, make_mock_llm())
        runner = CliRunner()
        result = runner.invoke(
            app, ["analyze", "--json", "-d", str(d), "--source", str(d / "nope.txt")]
        )
        assert result.exit_code == 1, result.output
        data = json.loads(result.stdout)
        assert data["success"] is False
        assert data["error"]["code"] == "analyze_failed"
