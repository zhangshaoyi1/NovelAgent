"""M5 章节创作工作流单元测试

覆盖：
- 门禁（状态、架构确认、world.md/subline.md/protagonist_route.md 不存在）
- 正确流程：生成 chapters/ch001.md、frontmatter 含依据链、状态转换 WRITING
- 进度指针更新（total_written 递增）
- 第二次调用状态 WRITING → WRITING
- 质量校验通过/未通过→自动修订
- 章节文件路径 ch<NNN>.md 三位补零
- 上下文加载：压力曲线阶段判定、角色信息提取、伏笔任务
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import frontmatter
import pytest

from agent.client import LLMClient, LLMResponse
from agent.core.state_machine import State, StateMachine
from agent.workflows.m5_write_chapter import M5WriteChapterWorkflow, MAX_REVISIONS


from tests.conftest import (
    ARCH_JSON,
    CHAPTER_TEXT,
    QUALITY_FAIL,
    QUALITY_PASS,
    _build_minimal_project,
    _build_mock_llm,
)


# ============================================================
# 测试：门禁
# ============================================================
class TestGates:
    def test_m5_requires_world_md(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        (d / "world.md").unlink()
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        with pytest.raises(RuntimeError, match="world.md 不存在"):
            wf.run()

    def test_m5_requires_confirmed_architecture(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        post = frontmatter.load(d / "architecture.md")
        post.metadata["confirmed"] = False
        (d / "architecture.md").write_bytes(frontmatter.dumps(post).encode("utf-8"))
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        with pytest.raises(RuntimeError, match="尚未确认"):
            wf.run()

    def test_m5_requires_correct_state(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path, state=State.OUTLINING)
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        with pytest.raises(RuntimeError, match="CHARACTER_DESIGN"):
            wf.run()

    def test_m5_requires_subline(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        # 删除 sublines 目录
        import shutil

        shutil.rmtree(d / "sublines")
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        with pytest.raises(RuntimeError):
            wf.run()


# ============================================================
# 测试：正确流程
# ============================================================
class TestHappyPath:
    def test_generates_chapter_file(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        r = wf.run()

        assert r.chapter_file.exists()
        assert r.chapter_file.name == "ch001.md"
        assert r.chapter_num == 1
        assert r.word_count > 100
        assert r.quality_passed is True
        assert r.revision_attempts == 0

    def test_state_transitions_to_writing(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        wf.run()
        sm = StateMachine(d)
        sm.load()
        assert sm.state == State.WRITING

    def test_progress_updated(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        wf.run()
        sm = StateMachine(d)
        sm.load()
        assert sm.progress.get("total_written") == 1
        assert sm.progress.get("current_chapter") == 1
        assert "S01_器灵人性觉醒" in sm.progress.get("current_subline", "")

    def test_second_write_increments_chapter(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        wf1 = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        wf1.run()
        # 第二次：状态已 WRITING
        wf2 = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        r2 = wf2.run()
        assert r2.chapter_num == 2
        assert r2.chapter_file.name == "ch002.md"
        sm = StateMachine(d)
        sm.load()
        assert sm.progress.get("total_written") == 2

    def test_chapter_frontmatter_has_evidence_chain(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        r = wf.run()
        post = frontmatter.load(r.chapter_file)
        assert post.metadata.get("chapter") == 1
        assert post.metadata.get("subline") == "S01_器灵人性觉醒"
        assert post.metadata.get("quality_passed") is True
        # E4：evidence_chain 为结构化 dict（不再扁平 list）
        chain = post.metadata.get("evidence_chain")
        assert isinstance(chain, dict)
        assert "characters" in chain and "foreshadows" in chain and "settings" in chain
        # 至少覆盖 3 个设定引用（世界观/支线/路线/关系...）
        total_refs = (
            len(chain.get("characters", []))
            + len(chain.get("foreshadows", []))
            + len(chain.get("settings", []))
        )
        assert total_refs >= 3
        # 每条引用均可序列化（含 source 字段）
        for grp in ("characters", "foreshadows", "settings"):
            for ref in chain.get(grp, []):
                assert "source" in ref

    def test_chapter_text_in_file(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        r = wf.run()
        text = r.chapter_file.read_text(encoding="utf-8")
        assert "林寻" in text
        assert "太虚镜" in text
        assert "# 第 1 章" in text

    def test_pressure_stage_determined(self, tmp_path: Path) -> None:
        """第 1 章应该在铺垫阶段"""
        d = _build_minimal_project(tmp_path)
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=_build_mock_llm())
        ctx = wf._load_context()
        assert ctx["pressure_stage"] == "铺垫"
        assert ctx["tension_level"] == "低"


# ============================================================
# 测试：质量校验 + 自动修订
# ============================================================
class TestQualityAndRevision:
    def test_quality_pass_no_revision(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        llm = _build_mock_llm(quality_report=QUALITY_PASS)
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=llm)
        r = wf.run()
        assert r.quality_passed is True
        assert r.revision_attempts == 0
        # LLM 调用：1 生成 + 1 校验 = 2 次
        assert llm.chat_creative.call_count == 1
        assert llm.chat_utility.call_count == 1

    def test_quality_fail_triggers_revision(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        # 第一次校验失败，修订后再校验通过
        import json as _json

        llm = MagicMock(spec=LLMClient)
        creative_texts = iter([CHAPTER_TEXT, CHAPTER_TEXT + "\n修订后"])
        utility_texts = iter([
            _json.dumps(QUALITY_FAIL, ensure_ascii=False),
            _json.dumps(QUALITY_PASS, ensure_ascii=False),
        ])

        def creative_fn(*a, **kw):
            return LLMResponse(text=next(creative_texts), raw={}, usage={})

        def utility_fn(*a, **kw):
            return LLMResponse(text=next(utility_texts), raw={}, usage={})

        llm.chat_creative.side_effect = creative_fn
        llm.chat_utility.side_effect = utility_fn

        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=llm)
        r = wf.run()
        assert r.quality_passed is True
        assert r.revision_attempts == 1
        # 1 生成 + 1 修订 = 2 次 creative
        assert llm.chat_creative.call_count == 2
        # 2 次校验
        assert llm.chat_utility.call_count == 2

    def test_max_revisions_exhausted(self, tmp_path: Path) -> None:
        """修订 2 次仍不通过，最终 quality_passed=False"""
        d = _build_minimal_project(tmp_path)
        import json as _json

        llm = MagicMock(spec=LLMClient)
        creative_texts = iter([CHAPTER_TEXT, CHAPTER_TEXT, CHAPTER_TEXT])
        # 3 次校验全失败
        fail_text = _json.dumps(QUALITY_FAIL, ensure_ascii=False)
        utility_texts = iter([fail_text, fail_text, fail_text])

        def creative_fn(*a, **kw):
            try:
                return LLMResponse(text=next(creative_texts), raw={}, usage={})
            except StopIteration:
                return LLMResponse(text=CHAPTER_TEXT, raw={}, usage={})

        def utility_fn(*a, **kw):
            try:
                return LLMResponse(text=next(utility_texts), raw={}, usage={})
            except StopIteration:
                return LLMResponse(text=fail_text, raw={}, usage={})

        llm.chat_creative.side_effect = creative_fn
        llm.chat_utility.side_effect = utility_fn

        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=llm)
        r = wf.run()
        assert r.quality_passed is False
        assert r.revision_attempts == 2  # MAX_REVISIONS


# ============================================================
# 测试：D 多维 LLM 质量审查（strict_review 并入 revise_loop）
# ============================================================
class TestStrictReview:
    def _build_strict_llm(self, d_bocking: bool):
        """构造 mock LLM：主校验通过；D 维度评审按 d_bocking 决定是否 blocking

        D 的合并评审 prompt 含「评审维度」字样，借此与 9 项通用校验区分。
        """
        import json as _json

        from agent.client import LLMClient, LLMResponse

        creative_texts = iter([CHAPTER_TEXT, CHAPTER_TEXT, CHAPTER_TEXT])
        main_report = _json.dumps(QUALITY_PASS, ensure_ascii=False)
        if d_bocking:
            d_report = _json.dumps(
                {
                    "cool_point": {"score": 8, "pass": True, "blocking": False, "issue": ""},
                    "ooc": {"score": 2, "pass": False, "blocking": True, "issue": "OOC 崩坏"},
                    "coherence": {"score": 7, "pass": True, "blocking": False, "issue": ""},
                    "pacing_hook": {"score": 8, "pass": True, "blocking": False, "issue": ""},
                },
                ensure_ascii=False,
            )
        else:
            d_report = _json.dumps(QUALITY_PASS, ensure_ascii=False)

        def creative_fn(*a, **kw):
            try:
                return LLMResponse(text=next(creative_texts), raw={}, usage={})
            except StopIteration:
                return LLMResponse(text=CHAPTER_TEXT, raw={}, usage={})

        def utility_fn(messages, *a, **kw):
            user = ""
            for m in messages:
                if m.get("role") == "user":
                    user = m.get("content", "")
            if "评审维度" in user:
                return LLMResponse(text=d_report, raw={}, usage={})
            return LLMResponse(text=main_report, raw={}, usage={})

        llm = MagicMock(spec=LLMClient)
        llm.chat_creative.side_effect = creative_fn
        llm.chat_utility.side_effect = utility_fn
        return llm

    def test_strict_review_blocks_on_ooc(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        llm = self._build_strict_llm(d_bocking=True)
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=llm, strict_review=True)
        r = wf.run()
        # OOC 维度持续 blocking → 修订耗尽后仍不通过
        assert r.quality_passed is False
        assert r.revision_attempts == MAX_REVISIONS
        # d_issues 含 d_ooc 且为 block 级
        assert any(
            i["rule_id"] == "d_ooc" and i["severity"] == "block"
            for i in r.d_issues
        )

    def test_strict_review_passes_when_no_blocking(self, tmp_path: Path) -> None:
        d = _build_minimal_project(tmp_path)
        llm = self._build_strict_llm(d_bocking=False)
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=llm, strict_review=True)
        r = wf.run()
        assert r.quality_passed is True
        # D 审查通过（无 blocking）→ 不记录 issue
        assert r.d_issues == []

    def test_strict_review_default_off_keeps_baseline(self, tmp_path: Path) -> None:
        """默认 strict_review=False：不触发 D 评审，chat_utility 调用次数与基线一致"""
        d = _build_minimal_project(tmp_path)
        llm = _build_mock_llm(quality_report=QUALITY_PASS)
        wf = M5WriteChapterWorkflow(project_dir=d, llm_client=llm)
        r = wf.run()
        assert r.quality_passed is True
        # 与 test_quality_pass_no_revision 一致：1 生成 + 1 校验
        assert llm.chat_utility.call_count == 1
        assert r.d_issues == []
