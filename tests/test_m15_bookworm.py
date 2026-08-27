"""M15 书虫 Skill 单元测试

覆盖：
- Skill 加载（SKILL.md frontmatter 解析、persona/rubrics/genre_expectations 读取）
- 独立性契约（纯文本输入 / JSON+MD 双形态输出）
- 测评主流程（LLM 调用 + JSON 解析 + 维度补齐 + 分数范围校验）
- 总分加权计算（按 rubrics 权重）
- 版本对比（v1→v2 提升点/退步点/已解决问题/新增问题）
- 问题严重度规范化（block/warn）
- 题材期待加载（xiuxian 内置 / 未知题材降级）
- 结果保存（JSON + MD 双文件）
- SkillRegistry 注册与命令路由
- 终端展示（不报错即可）
- 异常：SKILL.md 缺失 / name 不匹配 / 无正文
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.client import LLMResponse
from agent.workflows.m15_bookworm import (
    BookwormComparison,
    BookwormInput,
    BookwormIssue,
    BookwormReview,
    BookwormSkill,
    DIMENSION_KEYS,
    DIMENSION_WEIGHTS,
    SkillManifest,
    SkillRegistry,
    load_skill_manifest,
    _score_verdict,
)


# ============================================================
# 假数据
# ============================================================
REVIEW_JSON_HIGH = {
    "total_score": 88,
    "dimensions": {
        "title_appeal": 90,
        "opening_hook": 85,
        "pacing": 88,
        "character_distinctiveness": 92,
        "genre_fit": 80,
        "originality": 85,
        "chapter_end_hook": 90,
    },
    "one_liner_feeling": "开篇有钩子，主角立得住，会追更。",
    "issues": [
        {"severity": "warn", "description": "前 1000 字信息密度略高", "location": "前1000字"},
    ],
    "suggestions": [
        "前 1000 字适当加入感官描写降低信息密度",
        "章末钩子可以更狠一点",
    ],
    "reference": "对照《凡人修仙传》开篇——主角处境 + 金手指初现",
}

REVIEW_JSON_LOW = {
    "total_score": 55,
    "dimensions": {
        "title_appeal": 50,
        "opening_hook": 40,
        "pacing": 60,
        "character_distinctiveness": 55,
        "genre_fit": 70,
        "originality": 45,
        "chapter_end_hook": 50,
    },
    "one_liner_feeling": "开篇太平，主角没立住，弃书。",
    "issues": [
        {"severity": "block", "description": "前 3000 字无冲突无悬念", "location": "前3000字"},
        {"severity": "block", "description": "主角台词 AI 腔严重", "location": "对话"},
        {"severity": "warn", "description": "标题缺乏记忆点", "location": "标题"},
    ],
    "suggestions": [
        "前 300 字必须出现冲突或反差",
        "重写主角台词，去掉'微微一笑''心中暗想'",
    ],
    "reference": "",
}


def make_mock_llm(response_json: dict) -> MagicMock:
    """构造返回指定 JSON 的 mock LLM"""
    llm = MagicMock()
    llm.chat_utility.return_value = LLMResponse(text=json.dumps(response_json))
    return llm


# ============================================================
# Skill 加载
# ============================================================
class TestSkillLoad:
    def test_load_builtin_bookworm(self) -> None:
        """从内置 agent/skills/bookworm/ 加载"""
        skill = BookwormSkill.load(llm=MagicMock())
        assert skill.manifest.name == "bookworm"
        assert skill.manifest.independent is True
        assert "bookworm-review" in skill.manifest.command_names
        assert len(skill.persona) > 0
        assert len(skill.rubrics) > 0
        assert "xiuxian" in skill.genre_expectations

    def test_load_manifest_parses_frontmatter(self) -> None:
        """SKILL.md frontmatter 正确解析"""
        skill = BookwormSkill.load(llm=MagicMock())
        m = skill.manifest
        assert m.name == "bookworm"
        assert m.version == "0.1.0"
        assert m.type == "evaluator"
        assert m.independent is True
        assert m.dependencies == []

    def test_load_skill_manifest_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_skill_manifest(tmp_path / "no_such")

    def test_load_skill_manifest_missing_name(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "bad"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nversion: 0.1\n---\nbody", encoding="utf-8")
        with pytest.raises(ValueError, match="name"):
            load_skill_manifest(skill_dir)

    def test_load_wrong_skill_name(self, tmp_path: Path) -> None:
        """skill 目录 name 不是 bookworm 应报错"""
        skill_dir = tmp_path / "wrong"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: other\nversion: 0.1\n---\nbody", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="bookworm"):
            BookwormSkill.load(skill_dir=skill_dir, llm=MagicMock())

    def test_load_from_custom_dir(self, tmp_path: Path) -> None:
        """从自定义目录加载"""
        src = Path(__file__).resolve().parent.parent / "src" / "agent" / "skills" / "bookworm"
        dst = tmp_path / "bookworm"
        import shutil

        shutil.copytree(src, dst)
        skill = BookwormSkill.load(skill_dir=dst, llm=MagicMock())
        assert skill.manifest.name == "bookworm"


# ============================================================
# 独立性契约
# ============================================================
class TestIndependenceContract:
    def test_input_is_plain_text(self) -> None:
        """F15.6: 输入是纯文本字段"""
        inp = BookwormInput(
            title="第一章 血色试炼",
            book_name="凡人修仙",
            opening_text="寒风割过绝灵崖..." * 10,
            genre="xiuxian",
        )
        assert isinstance(inp.title, str)
        assert isinstance(inp.book_name, str)
        assert isinstance(inp.opening_text, str)

    def test_output_json_serializable(self) -> None:
        """F15.6: 输出 JSON 形态可序列化"""
        review = BookwormReview(
            total_score=80,
            dimensions={k: 80 for k in DIMENSION_KEYS},
            one_liner_feeling="不错",
            issues=[BookwormIssue(severity="warn", description="x", location="标题")],
            suggestions=["建议1"],
            reference="参考",
        )
        d = review.to_dict()
        # 可被 json.dumps
        json_str = json.dumps(d, ensure_ascii=False)
        assert json.loads(json_str)["total_score"] == 80

    def test_output_markdown_form(self) -> None:
        """F15.6: 输出 Markdown 形态"""
        review = BookwormReview(
            total_score=75,
            dimensions={k: 75 for k in DIMENSION_KEYS},
            one_liner_feeling="还行",
            issues=[BookwormIssue(severity="block", description="严重问题", location="开头")],
            suggestions=["改这里"],
            reference="对照 X",
            version="1",
        )
        md = review.to_markdown()
        assert "# 书虫测评报告 v1" in md
        assert "75/100" in md
        assert "还行" in md
        assert "严重问题" in md
        assert "改这里" in md
        assert "对照 X" in md


# ============================================================
# 测评主流程
# ============================================================
class TestReview:
    def test_review_high_score(self) -> None:
        skill = BookwormSkill.load(llm=make_mock_llm(REVIEW_JSON_HIGH))
        inp = BookwormInput(
            title="第一章 血色试炼",
            book_name="凡人修仙",
            opening_text="寒风割过绝灵崖..." * 20,
            genre="xiuxian",
        )
        review = skill.review(inp)
        assert review.total_score == 88
        assert review.dimensions["title_appeal"] == 90
        assert review.one_liner_feeling == "开篇有钩子，主角立得住，会追更。"
        assert len(review.issues) == 1
        assert review.issues[0].severity == "warn"
        assert len(review.suggestions) == 2
        assert "凡人修仙传" in review.reference

    def test_review_low_score_with_blocks(self) -> None:
        skill = BookwormSkill.load(llm=make_mock_llm(REVIEW_JSON_LOW))
        inp = BookwormInput(
            title="第一章",
            book_name="某书",
            opening_text="平淡的开头..." * 20,
        )
        review = skill.review(inp)
        assert review.total_score == 55
        blocks = [i for i in review.issues if i.severity == "block"]
        assert len(blocks) == 2

    def test_review_stores_input_snapshot(self) -> None:
        """测评结果记录输入快照（可追溯）"""
        skill = BookwormSkill.load(llm=make_mock_llm(REVIEW_JSON_HIGH))
        inp = BookwormInput(title="T", book_name="B", opening_text="X", genre="xiuxian")
        review = skill.review(inp)
        assert review.input_snapshot is not None
        assert review.input_snapshot.book_name == "B"

    def test_review_with_version_tag(self) -> None:
        skill = BookwormSkill.load(llm=make_mock_llm(REVIEW_JSON_HIGH))
        inp = BookwormInput(title="T", book_name="B", opening_text="X")
        review = skill.review(inp, version="2")
        assert review.version == "2"

    def test_review_calls_llm_with_utility_mode(self) -> None:
        """应使用 chat_utility（低温度）调用 LLM"""
        llm = make_mock_llm(REVIEW_JSON_HIGH)
        skill = BookwormSkill.load(llm=llm)
        skill.review(BookwormInput(title="T", book_name="B", opening_text="X"))
        llm.chat_utility.assert_called_once()
        # 检查 temperature 为低值
        kwargs = llm.chat_utility.call_args.kwargs
        assert kwargs.get("temperature", 1.0) <= 0.5

    def test_review_system_prompt_contains_persona_and_rubrics(self) -> None:
        """system prompt 应包含 persona 与 rubrics"""
        llm = make_mock_llm(REVIEW_JSON_HIGH)
        skill = BookwormSkill.load(llm=llm)
        skill.review(BookwormInput(title="T", book_name="B", opening_text="X"))
        messages = llm.chat_utility.call_args.kwargs["messages"]
        system_msg = messages[0]["content"]
        assert "资深书虫" in system_msg  # persona
        assert "title_appeal" in system_msg  # rubrics

    def test_review_user_prompt_contains_input(self) -> None:
        llm = make_mock_llm(REVIEW_JSON_HIGH)
        skill = BookwormSkill.load(llm=llm)
        skill.review(
            BookwormInput(
                title="血色试炼", book_name="凡人修仙", opening_text="开头正文ABC", genre="xiuxian"
            )
        )
        messages = llm.chat_utility.call_args.kwargs["messages"]
        user_msg = messages[1]["content"]
        assert "凡人修仙" in user_msg
        assert "血色试炼" in user_msg
        assert "开头正文ABC" in user_msg
        assert "xiuxian" in user_msg

    def test_review_genre_expectations_included(self) -> None:
        """指定 genre 时 system prompt 应包含题材期待"""
        llm = make_mock_llm(REVIEW_JSON_HIGH)
        skill = BookwormSkill.load(llm=llm)
        skill.review(BookwormInput(title="T", book_name="B", opening_text="X", genre="xiuxian"))
        system_msg = llm.chat_utility.call_args.kwargs["messages"][0]["content"]
        assert "修仙题材读者期待" in system_msg

    def test_review_unknown_genre_degrades_gracefully(self) -> None:
        """未知题材应降级为通用网文标准"""
        llm = make_mock_llm(REVIEW_JSON_HIGH)
        skill = BookwormSkill.load(llm=llm)
        skill.review(BookwormInput(title="T", book_name="B", opening_text="X", genre="scifi"))
        system_msg = llm.chat_utility.call_args.kwargs["messages"][0]["content"]
        assert "未内置 scifi 题材期待" in system_msg

    def test_review_no_genre(self) -> None:
        """不指定 genre 时不包含题材期待块"""
        llm = make_mock_llm(REVIEW_JSON_HIGH)
        skill = BookwormSkill.load(llm=llm)
        skill.review(BookwormInput(title="T", book_name="B", opening_text="X"))
        system_msg = llm.chat_utility.call_args.kwargs["messages"][0]["content"]
        assert "题材读者期待" not in system_msg


# ============================================================
# 维度补齐与分数校验
# ============================================================
class TestDimensionNormalization:
    def test_missing_dimensions_filled_with_zero(self) -> None:
        """LLM 漏给维度应补 0"""
        partial = {
            "total_score": 50,
            "dimensions": {"title_appeal": 80},  # 只给一个
            "one_liner_feeling": "x",
            "issues": [],
            "suggestions": [],
            "reference": "",
        }
        skill = BookwormSkill.load(llm=make_mock_llm(partial))
        review = skill.review(BookwormInput(title="T", book_name="B", opening_text="X"))
        for key in DIMENSION_KEYS:
            assert key in review.dimensions
        assert review.dimensions["title_appeal"] == 80
        assert review.dimensions["opening_hook"] == 0

    def test_score_clamped_to_0_100(self) -> None:
        """分数超出范围应截断"""
        bad = {
            "total_score": 150,
            "dimensions": {k: 120 for k in DIMENSION_KEYS},
            "one_liner_feeling": "x",
            "issues": [],
            "suggestions": [],
            "reference": "",
        }
        skill = BookwormSkill.load(llm=make_mock_llm(bad))
        review = skill.review(BookwormInput(title="T", book_name="B", opening_text="X"))
        assert review.total_score == 100
        for v in review.dimensions.values():
            assert v == 100

    def test_total_recomputed_when_missing(self) -> None:
        """LLM 未给 total_score 时按权重重算"""
        no_total = {
            "dimensions": {k: 80 for k in DIMENSION_KEYS},
            "one_liner_feeling": "x",
            "issues": [],
            "suggestions": [],
            "reference": "",
        }
        skill = BookwormSkill.load(llm=make_mock_llm(no_total))
        review = skill.review(BookwormInput(title="T", book_name="B", opening_text="X"))
        # 所有人都是 80，加权后还是 80
        assert review.total_score == 80

    def test_total_recompute_respects_weights(self) -> None:
        """加权计算正确"""
        no_total = {
            "dimensions": {
                "opening_hook": 100,  # 权重 0.25
                "title_appeal": 0,    # 权重 0.15
                "pacing": 0,
                "character_distinctiveness": 0,
                "genre_fit": 0,
                "originality": 0,
                "chapter_end_hook": 0,
            },
            "one_liner_feeling": "x",
            "issues": [],
            "suggestions": [],
            "reference": "",
        }
        skill = BookwormSkill.load(llm=make_mock_llm(no_total))
        review = skill.review(BookwormInput(title="T", book_name="B", opening_text="X"))
        # 100 * 0.25 = 25
        assert review.total_score == 25

    def test_invalid_severity_normalized(self) -> None:
        """非法 severity 规范为 warn"""
        bad_sev = {
            "total_score": 70,
            "dimensions": {k: 70 for k in DIMENSION_KEYS},
            "one_liner_feeling": "x",
            "issues": [
                {"severity": "critical", "description": "x", "location": ""},
                {"severity": "BLOCK", "description": "y", "location": ""},  # 大写
            ],
            "suggestions": [],
            "reference": "",
        }
        skill = BookwormSkill.load(llm=make_mock_llm(bad_sev))
        review = skill.review(BookwormInput(title="T", book_name="B", opening_text="X"))
        assert all(i.severity in ("block", "warn") for i in review.issues)
        # 大写 BLOCK 应转为 block
        assert any(i.severity == "block" for i in review.issues)


# ============================================================
# 版本对比
# ============================================================
class TestComparison:
    def _make_review(
        self,
        score: int,
        dims: dict[str, int] | None = None,
        issues: list[BookwormIssue] | None = None,
        version: str = "1",
    ) -> BookwormReview:
        return BookwormReview(
            total_score=score,
            dimensions=dims or {k: score for k in DIMENSION_KEYS},
            one_liner_feeling="x",
            issues=issues or [],
            suggestions=[],
            reference="",
            version=version,
        )

    def test_compare_score_improvement(self) -> None:
        old = self._make_review(60, version="1")
        new = self._make_review(80, version="2")
        skill = BookwormSkill.load(llm=MagicMock())
        comp = skill.compare(old, new)
        assert comp.score_delta == 20
        assert len(comp.improvements) > 0
        assert len(comp.regressions) == 0

    def test_compare_score_regression(self) -> None:
        old = self._make_review(80, version="1")
        new = self._make_review(60, version="2")
        skill = BookwormSkill.load(llm=MagicMock())
        comp = skill.compare(old, new)
        assert comp.score_delta == -20
        assert len(comp.regressions) > 0

    def test_compare_dimension_deltas(self) -> None:
        old = self._make_review(70, version="1")
        new_dims = {k: 70 for k in DIMENSION_KEYS}
        new_dims["opening_hook"] = 90  # +20
        new_dims["pacing"] = 50        # -20
        new = self._make_review(70, dims=new_dims, version="2")
        skill = BookwormSkill.load(llm=MagicMock())
        comp = skill.compare(old, new)
        assert comp.dimension_deltas["opening_hook"] == 20
        assert comp.dimension_deltas["pacing"] == -20
        assert comp.dimension_deltas["title_appeal"] == 0
        assert any("开篇钩子" in i for i in comp.improvements)
        assert any("节奏" in r for r in comp.regressions)

    def test_compare_resolved_block_issues(self) -> None:
        old = self._make_review(
            60,
            issues=[
                BookwormIssue(severity="block", description="前 3000 字无冲突", location=""),
                BookwormIssue(severity="block", description="主角台词 AI 腔", location=""),
            ],
            version="1",
        )
        new = self._make_review(
            80,
            issues=[BookwormIssue(severity="warn", description="信息密度略高", location="")],
            version="2",
        )
        skill = BookwormSkill.load(llm=MagicMock())
        comp = skill.compare(old, new)
        assert len(comp.resolved_issues) == 2
        assert "前 3000 字无冲突" in comp.resolved_issues
        assert len(comp.new_issues) == 0  # warn 不算 new block

    def test_compare_new_block_issues(self) -> None:
        old = self._make_review(80, issues=[], version="1")
        new = self._make_review(
            70,
            issues=[BookwormIssue(severity="block", description="新增严重问题", location="")],
            version="2",
        )
        skill = BookwormSkill.load(llm=MagicMock())
        comp = skill.compare(old, new)
        assert len(comp.new_issues) == 1
        assert "新增严重问题" in comp.new_issues

    def test_compare_flat_score_no_change(self) -> None:
        old = self._make_review(75, version="1")
        new = self._make_review(75, version="2")
        skill = BookwormSkill.load(llm=MagicMock())
        comp = skill.compare(old, new)
        assert comp.score_delta == 0


# ============================================================
# 结果保存
# ============================================================
class TestSave:
    def test_save_writes_json_and_md(self, tmp_path: Path) -> None:
        skill = BookwormSkill.load(llm=make_mock_llm(REVIEW_JSON_HIGH))
        inp = BookwormInput(title="T", book_name="B", opening_text="X", genre="xiuxian")
        review = skill.review(inp, version="1", save_dir=tmp_path)
        assert (tmp_path / "bookworm_review_1.json").exists()
        assert (tmp_path / "bookworm_review_1.md").exists()
        # JSON 可解析
        data = json.loads((tmp_path / "bookworm_review_1.json").read_text(encoding="utf-8"))
        assert data["total_score"] == 88
        # MD 含标题
        md = (tmp_path / "bookworm_review_1.md").read_text(encoding="utf-8")
        assert "书虫测评报告 v1" in md

    def test_save_creates_dir(self, tmp_path: Path) -> None:
        """保存目录不存在时应创建"""
        skill = BookwormSkill.load(llm=make_mock_llm(REVIEW_JSON_HIGH))
        save_dir = tmp_path / "nested" / "reviews"
        skill.review(
            BookwormInput(title="T", book_name="B", opening_text="X"),
            version="1",
            save_dir=save_dir,
        )
        assert (save_dir / "bookworm_review_1.json").exists()


# ============================================================
# SkillRegistry
# ============================================================
class TestSkillRegistry:
    def test_load_builtin_bookworm(self) -> None:
        reg = SkillRegistry()
        skill = reg.load_builtin("bookworm", llm=MagicMock())
        assert skill.manifest.name == "bookworm"
        assert reg.is_loaded("bookworm")
        assert "bookworm-review" in reg.command_to_skill

    def test_load_unknown_skill_raises(self) -> None:
        reg = SkillRegistry()
        with pytest.raises(ValueError, match="未知 skill"):
            reg.load_builtin("nonexistent")

    def test_get_skill_for_command(self) -> None:
        reg = SkillRegistry()
        reg.load_builtin("bookworm", llm=MagicMock())
        skill = reg.get_skill_for_command("bookworm-review")
        assert skill is not None
        assert skill.manifest.name == "bookworm"

    def test_get_skill_for_unknown_command(self) -> None:
        reg = SkillRegistry()
        assert reg.get_skill_for_command("nope") is None

    def test_list_loaded(self) -> None:
        reg = SkillRegistry()
        assert reg.list_loaded() == []
        reg.load_builtin("bookworm", llm=MagicMock())
        assert reg.list_loaded() == ["bookworm"]


# ============================================================
# 评分评语
# ============================================================
class TestScoreVerdict:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (95, "会追更、会安利"),
            (90, "会追更、会安利"),
            (85, "会追更"),
            (80, "会追更"),
            (75, "可看可不看"),
            (70, "可看可不看"),
            (65, "勉强能看"),
            (60, "勉强能看"),
            (50, "弃书"),
            (0, "弃书"),
        ],
    )
    def test_verdict(self, score: int, expected: str) -> None:
        assert _score_verdict(score) == expected


# ============================================================
# 终端展示（不报错即可）
# ============================================================
class TestShow:
    def test_show_review_does_not_raise(self) -> None:
        import io

        from rich.console import Console

        skill = BookwormSkill.load(
            llm=MagicMock(), console=Console(width=100, file=io.StringIO())
        )
        review = BookwormReview(
            total_score=78,
            dimensions={k: 78 for k in DIMENSION_KEYS},
            one_liner_feeling="还行",
            issues=[
                BookwormIssue(severity="block", description="严重问题", location="开头"),
                BookwormIssue(severity="warn", description="小问题", location="标题"),
            ],
            suggestions=["改这里", "改那里"],
            reference="对照 X",
            version="1",
        )
        skill.show_review(review)

    def test_show_comparison_does_not_raise(self) -> None:
        import io

        from rich.console import Console

        skill = BookwormSkill.load(
            llm=MagicMock(), console=Console(width=100, file=io.StringIO())
        )
        old = BookwormReview(
            total_score=60,
            dimensions={k: 60 for k in DIMENSION_KEYS},
            one_liner_feeling="差",
            issues=[BookwormIssue(severity="block", description="问题A", location="")],
            suggestions=[],
            reference="",
            version="1",
        )
        new = BookwormReview(
            total_score=80,
            dimensions={k: 80 for k in DIMENSION_KEYS},
            one_liner_feeling="好",
            issues=[],
            suggestions=[],
            reference="",
            version="2",
        )
        comp = skill.compare(old, new)
        skill.show_comparison(comp)


# ============================================================
# 权重完整性
# ============================================================
class TestWeights:
    def test_weights_sum_to_one(self) -> None:
        assert abs(sum(DIMENSION_WEIGHTS.values()) - 1.0) < 1e-6

    def test_all_dimensions_have_weights(self) -> None:
        for key in DIMENSION_KEYS:
            assert key in DIMENSION_WEIGHTS
