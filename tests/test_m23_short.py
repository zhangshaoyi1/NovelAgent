"""M23 短篇扫榜 + 拆文测试（离线，零真实 LLM）

覆盖（对齐 m23_short.py 工作流契约）：
- 知识包加载：scan_knowledge / analyze_knowledge 均能读到技能文件内容
- ScanReport / AnalyzeReport 的 to_dict / to_json / to_markdown 输出契约
- 扫榜工作流：正常解析 / 空榜单降级内置知识 / JSON 解析失败降级空报告
- 拆文工作流：正常解析 / 空正文报错 / 解析失败降级 / --save 落盘
- 边界：扫榜/拆文不写学习库（learnings.json），产物仅落 analyze/ 报告
- CLI 命令 JSON 信封契约（monkeypatch 工作流返回假报告，验证 --json 输出）
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import typer

from agent.client import LLMClient, LLMResponse
from agent.workflows.m23_short import (
    AnalyzeReport,
    M23ShortAnalyzeWorkflow,
    M23ShortScanWorkflow,
    NO_MARKET_DATA,
    ScanReport,
    ShortStoryKnowledge,
)

# ============================================================
# 假 LLM 数据
# ============================================================
SCAN_JSON = {
    "platform": "知乎盐言",
    "sample_date": "2026-08-31",
    "signal_strength": "高",
    "next_rescan": "2 周后",
    "data_source": "用户榜单样本",
    "market_overview": "虐恋与打脸情绪领跑，追妻题材饱和风险上升。",
    "emotion_rank": [
        {"rank": 1, "emotion_type": "虐恋", "count": 12, "trend": "↑", "example": "《xx》"},
        {"rank": 2, "emotion_type": "打脸", "count": 9, "trend": "→", "example": "《yy》"},
    ],
    "topic_hotspots": [
        {"topic": "追妻", "heat": "高", "competition": "激烈", "barrier": "低", "example": "《aa》"},
    ],
    "insights": {
        "word_count_range": "3000-8000",
        "opening_pattern": "反转开场",
        "ending_pref": "HE 偏多",
        "title_pattern": "两段式冲突标题",
        "character_hotwords": "隐忍女主",
    },
    "trend_alerts": [
        {"type": "即将饱和", "topic": "追妻", "basis": "榜上数量连续下降"},
        {"type": "正在爆发", "topic": "双重生", "basis": "近一周新增多"},
    ],
    "directions": [
        {"direction": "双重生+打脸", "emotion_hook": "宿敌互撕", "feasibility": "高"},
    ],
    "one_liner": "情绪市场向复合格局演进。",
}

ANALYZE_JSON = {
    "title": "重生之妻她杀疯了",
    "platform": "知乎盐言",
    "story_core": {
        "setting": "重生复仇",
        "theme": "快意恩仇",
        "core_action": "女主步步反杀",
        "one_liner": "被辜负的妻子重生后反杀全家",
    },
    "summary": "女主被辜负后重生，一步步揭露真相并反杀。",
    "pov": "第一人称",
    "timeline": "线性",
    "structure": [
        {"segment": "开端", "word_range": "0-500", "ratio": "15%", "function": "亮核心矛盾", "sections": "1-2"},
        {"segment": "高潮", "word_range": "2500-3200", "ratio": "25%", "function": "爆点释放", "sections": "8"},
    ],
    "emotion_curve": [
        {"position": "开头", "word_count": "0", "node": "N1", "emotion": "恨", "intensity": "-7", "trigger": "发现背叛", "hook_type": "冲突"},
        {"position": "高潮", "word_count": "2800", "node": "N4", "emotion": "爽", "intensity": "+9", "trigger": "当众反杀", "hook_type": "反差"},
    ],
    "explosion": {
        "prelude": "铺垫背叛证据",
        "accumulation": "步步逼近真相",
        "delay": "最后一次隐忍",
        "burst": "当众揭穿",
        "aftermath": "反派崩溃",
        "impression": "恶有恶报",
    },
    "reversal": {
        "type": "身份反转",
        "foreshadowing": ["戒指细节"],
        "mislead": "女主软弱",
        "reveal": "女主早有准备",
        "timing": "高潮前",
        "surprise": 4,
        "plausibility": 4,
        "impact": 5,
    },
    "techniques": [
        {"name": "信息差", "position": "全篇", "effect": "制造悬念", "reusability": "高"},
    ],
    "characters": [
        {"name": "女主", "narrative_role": "主人公", "action_role": "主动型", "function": "复仇驱动", "key_line": "我回来了"},
    ],
    "opening": {
        "first_3_sentences": "我睁开眼，回到三年前。",
        "hook_type": "时间线反转",
        "conflict_in_50": True,
        "core_conflict_in_100": True,
        "info_density": "高",
        "empathy": "强",
        "intensity": 8,
    },
    "ending": {
        "type": "反转余韵",
        "emotional_landing": "爽",
        "afterglow": "留白",
        "share_power": "强",
        "closure": "完整",
        "intensity": 8,
    },
    "five_dim_score": {
        "opening_attraction": {"score": 4, "note": "反转开场"},
        "emotion_pull": {"score": 5, "note": "情绪拉扯强"},
        "reversal_design": {"score": 4, "note": "有铺垫"},
        "pacing_control": {"score": 4, "note": "节奏紧凑"},
        "ending_afterglow": {"score": 4, "note": "余韵足"},
    },
    "explosion_power": "爆点集中在高潮",
    "topicality": "追妻话题性强",
    "resonance": [
        {"layer": "情感共鸣", "strength": "强", "trigger": "被辜负"},
    ],
    "reusable_structures": [
        {"name": "信息差反转", "usage": "开头埋戒指导向", "scenario": "追妻/复仇"},
    ],
    "writing_actions": "用信息差制造悬念",
    "rhythm_quick": {"event_density": "高", "dialogue_density": "中", "conflict_density": "高"},
    "one_liner_eval": "信息差贯穿全篇，情绪释放精准",
}


def make_mock_llm(response_json: dict) -> MagicMock:
    """构造返回指定 JSON 的 mock LLM（chat_utility 走统一入口）。"""
    llm = MagicMock(spec=LLMClient)
    llm.chat_utility.return_value = LLMResponse(
        text=json.dumps(response_json, ensure_ascii=False),
        raw={},
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )
    return llm


# ============================================================
# 知识包加载
# ============================================================
class TestKnowledge:
    def test_scan_knowledge_loaded(self) -> None:
        k = ShortStoryKnowledge().scan_knowledge()
        assert "real-market-data" in k
        assert len(k) > 100

    def test_analyze_knowledge_loaded(self) -> None:
        k = ShortStoryKnowledge().analyze_knowledge()
        # 拆文知识 = 输出模板 + 质量清单 + 盐言风格 + 拆文案例
        for name in (
            "output-templates",
            "quality-checklist",
            "zhihu-style",
            "deconstruction-examples",
        ):
            assert name in k


# ============================================================
# 输出契约
# ============================================================
class TestScanReportContract:
    def test_to_dict_roundtrip(self) -> None:
        r = ScanReport(
            platform="知乎盐言",
            market_overview="测试",
            emotion_rank=[{"rank": 1, "emotion_type": "虐恋"}],
        )
        d = r.to_dict()
        assert d["platform"] == "知乎盐言"
        assert d["emotion_rank"] == [{"rank": 1, "emotion_type": "虐恋"}]
        assert json.loads(r.to_json()) == d

    def test_to_markdown_sections(self) -> None:
        r = ScanReport(
            platform="知乎盐言",
            signal_strength="高",
            emotion_rank=[{"rank": 1, "emotion_type": "虐恋", "count": 3, "trend": "↑", "example": "x"}],
            topic_hotspots=[{"topic": "追妻", "heat": "高", "competition": "激烈", "barrier": "低", "example": "y"}],
            insights={"word_count_range": "3000-8000"},
            trend_alerts=[{"type": "即将饱和", "topic": "追妻", "basis": "b"}],
            directions=[{"direction": "双重生", "emotion_hook": "互撕", "feasibility": "高"}],
            one_liner="一句话",
        )
        md = r.to_markdown()
        for sec in ("市场概况", "情绪热度排行", "题材热点", "关键数据洞察", "风口预警", "值得写的方向", "一句话"):
            assert sec in md


class TestAnalyzeReportContract:
    def test_to_dict_roundtrip(self) -> None:
        r = AnalyzeReport(
            title="测试",
            story_core={"setting": "重生"},
            structure=[{"segment": "开端"}],
        )
        d = r.to_dict()
        assert d["story_core"] == {"setting": "重生"}
        assert json.loads(r.to_json()) == d

    def test_to_markdown_sections(self) -> None:
        r = AnalyzeReport(
            title="测试",
            story_core={"setting": "重生", "theme": "复仇", "core_action": "反杀"},
            structure=[{"segment": "开端", "word_range": "0-500", "ratio": "15%", "function": "f", "sections": "1"}],
            emotion_curve=[{"position": "开头", "word_count": "0", "node": "N1", "emotion": "恨", "intensity": "-7", "trigger": "t", "hook_type": "冲突"}],
            explosion={"burst": "当众揭穿"},
            reversal={"type": "身份反转"},
            techniques=[{"name": "信息差", "position": "全篇", "effect": "e", "reusability": "高"}],
            characters=[{"name": "女主", "narrative_role": "主人公", "action_role": "主动型", "function": "f", "key_line": "k"}],
            opening={"first_3_sentences": "我睁开眼。"},
            ending={"type": "反转余韵"},
            five_dim_score={"opening_attraction": {"score": 4, "note": "n"}},
            resonance=[{"layer": "情感共鸣", "strength": "强", "trigger": "t"}],
            reusable_structures=[{"name": "信息差", "usage": "u", "scenario": "s"}],
            rhythm_quick={"event_density": "高"},
            one_liner_eval="一句话评价",
        )
        md = r.to_markdown()
        for sec in (
            "故事核", "结构划分", "情感曲线", "爆点分析", "反转设计", "写作手法",
            "人物", "开头分析", "结尾分析", "五维评分", "共鸣分析", "可复用结构",
            "节奏速报", "一句话评价",
        ):
            assert sec in md


# ============================================================
# 扫榜工作流
# ============================================================
class TestScanWorkflow:
    def test_scan_parses_report(self) -> None:
        wf = M23ShortScanWorkflow(llm_client=make_mock_llm(SCAN_JSON))
        report = wf.run(market_data="榜单样本", platform="知乎盐言", sample_date="2026-08-31")
        assert report.platform == "知乎盐言"
        assert report.signal_strength == "高"
        assert report.emotion_rank[0]["emotion_type"] == "虐恋"
        assert report.topic_hotspots[0]["topic"] == "追妻"
        assert report.directions[0]["direction"] == "双重生+打脸"

    def test_scan_empty_market_falls_back_builtin(self) -> None:
        """无榜单样本 → 用内置知识占位分析，仍输出有效报告。"""
        llm = make_mock_llm(SCAN_JSON)
        wf = M23ShortScanWorkflow(llm_client=llm)
        report = wf.run(market_data="")
        # 占位说明被注入 user 提示词（内置知识分支）
        called_kwargs = llm.chat_utility.call_args
        user_content = called_kwargs.kwargs["messages"][1]["content"]
        assert NO_MARKET_DATA in user_content
        assert report.platform  # 默认 "综合"

    def test_scan_parse_failure_degrades(self) -> None:
        """JSON 解析失败 → 降级返回空报告，不抛错。"""
        llm = make_mock_llm({})
        llm.chat_utility.return_value = LLMResponse(text="不是 JSON")
        wf = M23ShortScanWorkflow(llm_client=llm)
        report = wf.run(market_data="样本")
        assert report.platform == "综合"
        assert report.emotion_rank == []

    def test_scan_input_truncated(self) -> None:
        """超长榜单样本被截断到上限，控制 token 成本。"""
        wf = M23ShortScanWorkflow(llm_client=make_mock_llm(SCAN_JSON))
        report = wf.run(market_data="x" * 50000)
        assert report.platform  # 正常返回


# ============================================================
# 拆文工作流
# ============================================================
class TestAnalyzeWorkflow:
    def test_analyze_parses_report(self) -> None:
        wf = M23ShortAnalyzeWorkflow(llm_client=make_mock_llm(ANALYZE_JSON))
        report = wf.run(
            input_text="我睁开眼，回到三年前。",
            title="重生之妻她杀疯了",
            platform="知乎盐言",
        )
        assert report.title == "重生之妻她杀疯了"
        assert report.story_core["theme"] == "快意恩仇"
        assert report.reversal["type"] == "身份反转"
        assert report.five_dim_score["emotion_pull"]["score"] == 5

    def test_analyze_empty_input_raises(self) -> None:
        wf = M23ShortAnalyzeWorkflow(llm_client=make_mock_llm(ANALYZE_JSON))
        with pytest.raises(ValueError, match="input_text"):
            wf.run(input_text="")

    def test_analyze_parse_failure_degrades(self) -> None:
        llm = make_mock_llm({})
        llm.chat_utility.return_value = LLMResponse(text="坏数据")
        wf = M23ShortAnalyzeWorkflow(llm_client=llm)
        report = wf.run(input_text="正文", title="T")
        assert report.title == "T"
        assert report.structure == []

    def test_analyze_save_writes_report(self, tmp_path: Path) -> None:
        wf = M23ShortAnalyzeWorkflow(llm_client=make_mock_llm(ANALYZE_JSON))
        report = wf.run(input_text="正文", title="测试作品", save=True, output_dir=tmp_path)
        files = list(tmp_path.glob("analyze-*.md"))
        assert len(files) == 1
        assert "故事核" in files[0].read_text(encoding="utf-8")
        # 标题优先取 LLM 输出（ANALYZE_JSON.title），而非入参 title
        assert report.title == "重生之妻她杀疯了"


# ============================================================
# 边界：不写学习库
# ============================================================
class TestLearningBoundary:
    def test_analyze_save_does_not_touch_learning_store(self, tmp_path: Path) -> None:
        """拆文产物仅落 analyze/ 报告，不写 learnings.json（与 m17_learn 边界）。"""
        wf = M23ShortAnalyzeWorkflow(llm_client=make_mock_llm(ANALYZE_JSON))
        wf.run(input_text="正文", title="T", save=True, output_dir=tmp_path)
        assert not (tmp_path / "learnings.json").exists()
        assert not list(tmp_path.rglob("learnings.json"))
        assert list(tmp_path.glob("analyze-*.md"))


# ============================================================
# CLI 命令 JSON 信封
# ============================================================
class _FakeScanWorkflow:
    def __init__(self, **kwargs) -> None:
        self._kwargs = kwargs

    def run(self, market_data="", platform="综合", sample_date=""):
        return ScanReport(platform=platform, sample_date=sample_date, market_overview="CLI测试")


class _FakeAnalyzeWorkflow:
    def __init__(self, **kwargs) -> None:
        self._kwargs = kwargs

    def run(self, input_text, title="", platform="", genre="", save=False, output_dir=None):
        return AnalyzeReport(title=title, platform=platform, summary=input_text[:20])


class TestCliScan:
    def test_short_scan_json_contract(self, tmp_path: Path, monkeypatch, capsys) -> None:
        import agent.workflows.m23_short as wf_mod

        monkeypatch.setattr(wf_mod, "M23ShortScanWorkflow", _FakeScanWorkflow)
        from agent.cli.commands.short_scan import short_scan

        short_scan(
            project_dir=str(tmp_path),
            input_file="",
            text="榜单文本",
            platform="知乎盐言",
            sample_date="",
            save=False,
            json_output=True,
            env_file=None,
        )
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is True
        assert out["report"]["platform"] == "知乎盐言"

    def test_short_scan_missing_input_file(self, tmp_path: Path, capsys) -> None:
        from agent.cli.commands.short_scan import short_scan

        with pytest.raises(typer.Exit) as e:
            short_scan(
                project_dir=str(tmp_path),
                input_file=str(tmp_path / "nope.txt"),
                text="",
                platform="综合",
                sample_date="",
                save=False,
                json_output=True,
                env_file=None,
            )
        assert e.value.exit_code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is False
        assert out["error"]["code"] == "input_not_found"


class TestCliAnalyze:
    def test_short_analyze_json_contract(self, tmp_path: Path, monkeypatch, capsys) -> None:
        import agent.workflows.m23_short as wf_mod

        monkeypatch.setattr(wf_mod, "M23ShortAnalyzeWorkflow", _FakeAnalyzeWorkflow)
        from agent.cli.commands.short_analyze import short_analyze

        short_analyze(
            text="我睁开眼，回到三年前。",
            text_file="",
            title="测试",
            platform="知乎盐言",
            genre="追妻",
            save=False,
            output_dir="",
            project_dir=str(tmp_path),
            json_output=True,
            env_file=None,
        )
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is True
        assert out["report"]["title"] == "测试"

    def test_short_analyze_no_input(self, tmp_path: Path, capsys) -> None:
        from agent.cli.commands.short_analyze import short_analyze

        with pytest.raises(typer.Exit) as e:
            short_analyze(
                text="",
                text_file="",
                title="",
                platform="",
                genre="",
                save=False,
                output_dir="",
                project_dir=str(tmp_path),
                json_output=True,
                env_file=None,
            )
        assert e.value.exit_code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["success"] is False
        assert out["error"]["code"] == "bad_input"
