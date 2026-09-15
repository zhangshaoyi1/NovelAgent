"""质量门禁「假失败 / 回退死循环」修复回归测试（2026-09-15）

对应登记单：``项目文档/优化/20260915_质检假失败与回退死循环.md``

背景
----
用户反馈「写作慢、质检通过率低」。复盘两书 94 轮批末体检 + 62 份回退快照，
按处置规则表复算每轮最终动作后定位到 5 条独立根因（本文件逐条锁死）：

- **A** 软维漏配兜底 → 轻微超标被升级成「销毁整窗 5 章」    （见 test_disposition.py）
- **B** 兜底默认回滚 → 未登记维度也销毁内容                （见 test_disposition.py）
- **C** ``pacing_abnormal`` 全书统计 vs 窗口回退 → 判得对但永远修不好（死循环）
- **D** 一致性检测器按「最近提及」归因 → 配角死亡栽赃主角  （8 条假 watch 债）
- **E** 回退不清理章节指纹 → 重写同号章节与自己的旧版撞车（相似度 1.00 假阳性）
- **F** 回退事实在升级出口丢失 → ``RollbackBudget`` 永不计数、熔断护栏失效
- **G** 基建故障（LLM 网关不可达）被当成「内容不达标」

共同主题：**动作强度必须与证据等级、作用域、失败来源一致；辅助证据/索引
一旦失败或过期，绝不能被解读成一个「可信的结论」**。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.agents.evaluator import EvaluatorAgent
from agent.agents.evaluator_types import (
    DimensionResult,
    NovelHealthReport,
    RepairPlan,
)
from agent.core.quality.consistency.checker import (
    CheckTrigger,
    ConsistencyChecker,
)
from agent.core.quality.eval_evidence import EvalEvidence
from agent.core.quality.guardrails import load_fingerprints
from agent.workflows.evaluation.m10_rollback import M10RollbackWorkflow

from tests.conftest import make_project

# ============================================================
# C. pacing_abnormal 作用域对齐（窗口内判定 + 窗口外显性列出）
# ============================================================
class TestPacingScopeAlignment:
    """登记表声明 ``Scope.WINDOW``（末窗回滚可修）→ 统计范围必须与之一致。

    旧实现把**全书**异常章数 / **全书**章数：ch012 这类"窗口外的历史遗留异常章"
    会让回退末 5 章永远修不好它，同一批被回退 8 次仍必然失败（灵荒薪传实证）。
    """

    @staticmethod
    def _make(tmp_path: Path, *, normal: int = 1000) -> Path:
        d = make_project(tmp_path, n_chapters=10)
        return d

    @staticmethod
    def _write(d: Path, n: int, length: int) -> None:
        (d / "chapters" / f"ch{n:03d}.md").write_text(
            "正" * length, encoding="utf-8"
        )

    def test_out_of_window_abnormal_does_not_fail(self, tmp_path: Path) -> None:
        d = self._make(tmp_path)
        for n in range(1, 11):
            self._write(d, n, 1000)
        self._write(d, 1, 10)          # 1/10 = 0.10 > 0.03 —— 旧口径必失败

        rate, stat = EvaluatorAgent(d, rollback_window=5)._metric_pacing()

        assert stat["abnormal_total"] == 1, "全书口径仍应看见 ch001"
        assert [it["chapter"] for it in stat["abnormal_chapters_out_of_window"]] == ["ch001"]
        assert stat["abnormal"] == 0, "窗口内无异常 → 判定值不受窗口外影响"
        assert rate == 0.0

    def test_in_window_abnormal_fails(self, tmp_path: Path) -> None:
        d = self._make(tmp_path)
        for n in range(1, 11):
            self._write(d, n, 1000)
        self._write(d, 10, 10)         # 落在末 5 章窗口内 → 可修 → 应当失败

        rate, stat = EvaluatorAgent(d, rollback_window=5)._metric_pacing()

        assert [it["chapter"] for it in stat["abnormal_chapters"]] == ["ch010"]
        assert abs(rate - 0.2) < 1e-9
        assert rate > 0.03

    def test_window_size_follows_rollback_window(self, tmp_path: Path) -> None:
        d = self._make(tmp_path)
        for n in range(1, 11):
            self._write(d, n, 1000)
        self._write(d, 5, 10)          # 第 5 章

        assert EvaluatorAgent(d, rollback_window=5)._metric_pacing()[0] == 0.0
        assert EvaluatorAgent(d, rollback_window=6)._metric_pacing()[0] > 0.03


# ============================================================
# D. 一致性检测器主体锚定（配角死亡不栽赃主角）
# ============================================================
class TestDeathAssertionSubjectAnchoring:
    """交付正文里「**周德顺**已经死了」（配角、不在 characters/ 索引）不得被判成
    「林凡已故」——旧实现按 24 字半径内**最近**角色名归因，活着的**主角**被判已故，
    还与关系网的「合作」活跃边冲突，连报 5 章、重写无法消除（灵荒薪传 DEBT-0001~0008）。
    """

    @staticmethod
    def _seed(tmp_path: Path) -> Path:
        d = tmp_path / "proj"
        (d / "characters").mkdir(parents=True)
        (d / "relations").mkdir(parents=True)
        # 主角在世
        (d / "characters" / "林凡.md").write_text(
            "---\nname: 林凡\n---\n\n林凡，本作主角，当前在世，修为筑基。\n",
            encoding="utf-8",
        )
        # 导师在世，且与主角有互动型活跃边
        (d / "characters" / "沈长风.md").write_text(
            "---\nname: 沈长风\n---\n\n沈长风，导师，当前在世。\n",
            encoding="utf-8",
        )
        (d / "relations" / "graph.md").write_text(
            "# 关系网\n\n## 节点\n\n"
            "| ID | 姓名 | 定位 |\n|---|---|---|\n| A | 林凡 | 主角 |\n| C | 沈长风 | 导师 |\n\n"
            "## 边（关系）\n\n| 起 | 止 | 类型 | 备注 | 起始 | 状态 |\n"
            "|---|---|---|---|---|---|\n| A | C | 合作 | 师徒合作 | S01 | 活跃 |\n",
            encoding="utf-8",
        )
        return d

    def _conflicts(self, d: Path, text: str) -> list[Any]:
        rep = ConsistencyChecker(d).check(CheckTrigger.POST_WRITE, ctx={"chapter_text": text})
        return [c for c in rep.conflicts if c.rule_id in ("timeline_conflict", "relation_conflict")]

    def test_unregistered_subject_does_not_implicate_nearby_character(
        self, tmp_path: Path
    ) -> None:
        d = self._seed(tmp_path)
        # 主语是「周德顺」（未登记配角）；林凡只是同句出现
        got = self._conflicts(d, "林凡走进工坊时，周德顺已经死了，尸骨埋在乱坟岗。")
        assert got == [], f"不得把配角之死栽赃给在场角色：{got}"

    def test_distant_mention_does_not_implicate(self, tmp_path: Path) -> None:
        d = self._seed(tmp_path)
        got = self._conflicts(
            d, "林凡推开那扇吱呀作响的木门，穿过满是灰尘的长廊，他递交文书的那天晚上，就已经死了。"
        )
        assert got == []

    def test_adjacent_subject_still_detected(self, tmp_path: Path) -> None:
        """正向对照：主体紧邻断言时**必须**照旧报出（修的是误报，不是关掉检测）。"""
        d = self._seed(tmp_path)
        got = self._conflicts(d, "林凡已经死了。")
        assert any(c.rule_id == "timeline_conflict" for c in got), got

    def test_adjacent_subject_with_connector_still_detected(self, tmp_path: Path) -> None:
        d = self._seed(tmp_path)
        got = self._conflicts(d, "众人皆知，林凡早已故去。")
        assert any(c.rule_id == "timeline_conflict" for c in got), got

    def test_time_expression_between_subject_and_assertion(self, tmp_path: Path) -> None:
        """「在十年前便已故去」这类时间状语不应被当成"主体不明"。"""
        d = self._seed(tmp_path)
        got = self._conflicts(d, "沈长风在十年前便已经故去，尸骨早已凉透。")
        assert any(c.rule_id == "timeline_conflict" for c in got), got


# ============================================================
# E. 回退同步清理章节指纹
# ============================================================
class TestRollbackFingerprintSync:
    """指纹库不随回滚失效 → 重写同号章节与**自己的上一版**命中，
    产出「相似度 1.00 疑似跨章重复」假阳性（灵荒薪传 ch025 实证）。"""

    @staticmethod
    def _seed_fingerprints(d: Path, chapters: list[int]) -> Path:
        fp = d / ".state" / "chapter_fingerprints.json"
        fp.parent.mkdir(parents=True, exist_ok=True)
        db = {str(n): [[123456 + n, f"第{n}章某段归一化正文"]] for n in chapters}
        fp.write_text(
            json.dumps({"fingerprints": db}, ensure_ascii=False), encoding="utf-8"
        )
        return fp

    def test_archived_chapter_fingerprints_are_removed(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=5)
        fp = self._seed_fingerprints(d, [1, 2, 3, 4, 5])

        res = M10RollbackWorkflow(d).rollback_to_chapter(3)
        assert res.success is True
        assert sorted(res.archived_chapters) == ["ch003.md", "ch004.md", "ch005.md"]

        assert set(load_fingerprints(fp)) == {"1", "2"}

    def test_ch_prefixed_keys_also_cleaned(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=4)
        fp = d / ".state" / "chapter_fingerprints.json"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(
            json.dumps({"fingerprints": {
                "ch003": [[1, "a"]], "3": [[2, "b"]], "1": [[3, "c"]],
            }}, ensure_ascii=False),
            encoding="utf-8",
        )
        M10RollbackWorkflow(d).rollback_to_chapter(3)
        assert set(load_fingerprints(fp)) == {"1"}

    def test_missing_fingerprint_file_is_noop(self, tmp_path: Path) -> None:
        d = make_project(tmp_path, n_chapters=3)
        res = M10RollbackWorkflow(d).rollback_to_chapter(2)
        assert res.success is True
        assert not (d / ".state" / "chapter_fingerprints.json").exists()

    def test_sync_failure_does_not_break_rollback(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """指纹同步异常必须只告警，不得阻断回滚本身（与 RAG 索引同步同契约）。"""
        from agent.core.quality import guardrails

        def _boom(*a: Any, **kw: Any) -> None:
            raise RuntimeError("指纹库不可用")

        monkeypatch.setattr(guardrails, "save_fingerprints", _boom)
        d = make_project(tmp_path, n_chapters=3)
        self._seed_fingerprints(d, [1, 2, 3])

        res = M10RollbackWorkflow(d).rollback_to_chapter(2)
        assert res.success is True
        assert sorted(res.archived_chapters) == ["ch002.md", "ch003.md"]


# ============================================================
# F. 回退事实跨封装层存活（否则 RollbackBudget 永不计数）
# ============================================================
def _hard_fail_report() -> NovelHealthReport:
    return NovelHealthReport(
        overall_pass=False,
        dimensions=[
            DimensionResult(
                "logic_holes", "逻辑漏洞", 3.0, 0.0, "<=", True, "llm/default",
                evidence=EvalEvidence(),
            ),
        ],
    )


class TestRollbackFactSurvivesEscalation:
    """``evaluate_with_repair`` 走升级出口时，报告必须仍带着「本轮回环内回过退」的事实。

    旧实现 ``rolled_back`` 只在**闭环成功**出口赋值，循环里每轮回退后 ``report``
    都被换成新的 ``_evaluate_once()`` 结果 → 升级出口的 ``rolled_back`` 恒为 False
    → 上层 ``RollbackBudget.bump`` 永不触发（实测 ``rollback_budget.json`` 停在
    09-14 21:33，而 09-15 又回退了两次）→ 熔断护栏形同虚设、回退循环无人上报。
    """

    def _ev(self, tmp_path: Path) -> EvaluatorAgent:
        return EvaluatorAgent(
            tmp_path, rollback_window=5, max_rollback_attempts=1,
        )

    def test_escalated_report_keeps_rolled_back_and_target(self, tmp_path: Path) -> None:
        ev = self._ev(tmp_path)
        rolls: list[Any] = []

        def fake_eval(self: Any) -> NovelHealthReport:  # noqa: ANN001
            return _hard_fail_report()

        def fake_rollback(self: Any, last_written: Any = None) -> RepairPlan:  # noqa: ANN001
            rolls.append(last_written)
            return RepairPlan(8, [8, 9, 10, 11, 12], "硬指标不达标", rolled_back=True)

        ev._evaluate_once = fake_eval.__get__(ev, type(ev))        # type: ignore[method-assign]
        ev.trigger_rollback = fake_rollback.__get__(ev, type(ev))  # type: ignore[method-assign]

        report = ev.evaluate_with_repair(lambda ch: None)

        assert rolls, "本轮应确实发生过回退"
        assert report.escalated is True
        assert report.rolled_back is True, "升级出口不得丢掉回退事实"
        assert report.repair is not None, "回退目标必须可被上层取出"
        # 上层 agentic_pipeline 的计数条件
        assert getattr(report, "rolled_back", False) is True
        # _last_rollback_target 的取值路径
        assert int(report.repair.target_chapter) == 8

    def test_no_rollback_when_untrusted_keeps_false(self, tmp_path: Path) -> None:
        """无回退的升级路径不应被误标 rolled_back（否则护栏会被假计数吃满）。"""
        ev = self._ev(tmp_path)

        def fake_eval(self: Any) -> NovelHealthReport:  # noqa: ANN001
            ev_dim = EvalEvidence()
            ev_dim.degrade("test")
            return NovelHealthReport(
                overall_pass=False,
                dimensions=[
                    DimensionResult(
                        "logic_holes", "逻辑漏洞", 3.0, 0.0, "<=", True,
                        "llm/default", evidence=ev_dim,
                    ),
                ],
            )

        ev._evaluate_once = fake_eval.__get__(ev, type(ev))  # type: ignore[method-assign]
        report = ev.evaluate_with_repair(lambda ch: None)
        assert report.escalated is True
        assert report.rolled_back is False


# ============================================================
# G. 评测基建不可用 vs 内容不达标（分层）
# ============================================================
class TestInfraFailureIsNotContentFailure:
    """全部 LLM 评分维 ``confidence=0`` = 网关不可达，**不是**内容质量结论。

    旧行为把它当「体检不通过 / 证据不可信」：先白烧一次复评，再把基建故障写进
    失败明细落盘，污染注入下一批写作的「上轮教训」（两书累计 190 次 confidence=0）。
    """

    @staticmethod
    def _llm_dim(name: str, *, confidence: float) -> DimensionResult:
        """LLM 计数维（阈值 0，value=3 → 不达标）＋可选降级证据。"""
        ev = EvalEvidence()
        if confidence <= 0.0:
            ev.degrade("test")
        return DimensionResult(name, name, 3.0, 0.0, "<=", True, "llm/default", evidence=ev)

    def test_all_llm_dims_degraded_flags_infra(self) -> None:
        rep = NovelHealthReport(overall_pass=False, dimensions=[
            self._llm_dim("logic_holes", confidence=0.0),
            self._llm_dim("setting_consistency_high", confidence=0.0),
        ])
        assert rep.eval_infra_unavailable is True
        assert rep.to_dict()["eval_infra_unavailable"] is True

    def test_one_credible_dim_is_not_infra(self) -> None:
        rep = NovelHealthReport(overall_pass=False, dimensions=[
            self._llm_dim("logic_holes", confidence=0.0),
            self._llm_dim("setting_consistency_high", confidence=1.0),
        ])
        assert rep.eval_infra_unavailable is False

    def test_computed_only_report_is_not_infra(self) -> None:
        """确定性计算维不依赖 LLM（无 evidence → confidence=1.0）→ 不算基建故障。"""
        rep = NovelHealthReport(overall_pass=False, dimensions=[
            DimensionResult("pacing_abnormal", "节奏异常", 0.9, 0.03, "<=", False, "computed"),
        ])
        assert rep.eval_infra_unavailable is False

    def test_escalation_reason_distinguishes_infra(self, tmp_path: Path) -> None:
        ev = EvaluatorAgent(tmp_path, rollback_window=5, max_rollback_attempts=3)
        cls = TestInfraFailureIsNotContentFailure

        def fake_eval(self: Any) -> NovelHealthReport:  # noqa: ANN001
            return NovelHealthReport(overall_pass=False, dimensions=[
                cls._llm_dim("logic_holes", confidence=0.0),
                cls._llm_dim("setting_consistency_high", confidence=0.0),
            ])

        ev._evaluate_once = fake_eval.__get__(ev, type(ev))  # type: ignore[method-assign]
        report = ev.evaluate_with_repair(lambda ch: None)

        assert report.escalated is True
        assert "基建不可用" in report.escalated_reason
        assert "不代表内容质量" in report.escalated_reason
        assert report.rolled_back is False, "基建故障不得触发回退"


# ============================================================
# C2. padding_repetition_abnormal 作用域对齐（硬闸：窗口内判定）
# ============================================================
class TestRepetitionScopeAlignment:
    """``padding_repetition_abnormal`` 是 **required=True 硬闸** 且声明
    ``Scope.WINDOW``（末窗回滚可修）→ 统计范围必须与之一致。

    旧实现用**全书**重复句数 / 全书句数：早段章节的车轱辘话会把全书占比顶过阈值，
    而回退末 N 章碰不到早段 → 硬闸死循环（比 pacing 更重：硬闸会真实删章重写 +
    双证据守门）。该实例被登记单 §二 的"阈值取字段名"脚本 bug 误判为无问题而漏过。
    """

    @staticmethod
    def _low() -> str:
        """20 句两两字符集不相交 → 相似度 0 → 无重复句。"""
        return "".join(chr(0x4E00 + i) * 9 + "。" for i in range(20))

    @staticmethod
    def _high() -> str:
        """20 句完全相同 → 19 句判重（占比 0.95）。"""
        return "这片灰蒙蒙的天空依旧是那副模样。" * 20

    def _seed(self, tmp_path: Path, highs: set[int]) -> Path:
        d = make_project(tmp_path, n_chapters=10)
        for n in range(1, 11):
            (d / "chapters" / f"ch{n:03d}.md").write_text(
                self._high() if n in highs else self._low(), encoding="utf-8"
            )
        return d

    def test_gated_value_is_window_scoped(self, tmp_path: Path) -> None:
        d = self._seed(tmp_path, highs={10})
        ratio, stat = EvaluatorAgent(d, rollback_window=5)._metric_repetition()

        # 门禁值 = 末 5 章聚合占比；ch010 高重复 → 明显超阈值
        assert stat["window_repeated_sentences"] == 19
        assert stat["window_total_sentences"] == 100
        assert abs(ratio - 0.19) < 1e-9
        assert ratio <= 0.30, "仅末窗 1/5 章高重复时聚合占比未越线（阈值 0.30）"

    def test_all_high_window_chapters_fail(self, tmp_path: Path) -> None:
        d = self._seed(tmp_path, highs={6, 7, 8, 9, 10})
        ratio, stat = EvaluatorAgent(d, rollback_window=5)._metric_repetition()

        assert stat["window"] == 5
        assert ratio > 0.30, "整窗车轱辘话必须越线（硬闸应当拦截）"

    def test_out_of_window_repetition_does_not_fail(self, tmp_path: Path) -> None:
        d = self._seed(tmp_path, highs={1})   # ch001 高重复，但在末窗之外
        ratio, stat = EvaluatorAgent(d, rollback_window=5)._metric_repetition()

        assert ratio == 0.0, "窗口外历史注水不得顶起门禁值（否则判而不可修）"
        assert stat["window_repeated_sentences"] == 0
        # 全书口径仍看得见 ch001，供人工排查早段注水
        assert stat["repetition_ratio_full"] > 0.0
        assert [it["chapter"] for it in stat["chapters_out_of_window"]] == ["ch001"]

    def test_window_size_follows_rollback_window(self, tmp_path: Path) -> None:
        d = self._seed(tmp_path, highs={5})
        assert EvaluatorAgent(d, rollback_window=5)._metric_repetition()[0] == 0.0
        assert EvaluatorAgent(d, rollback_window=6)._metric_repetition()[0] > 0.0
