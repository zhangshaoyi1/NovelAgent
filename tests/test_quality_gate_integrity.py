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
from agent.core.quality.guardrails import (
    Guardrails,
    canonical_chapter_key,
    load_book_fingerprints,
    load_fingerprints,
)
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
    # 硬闸样例用 character_stability_high：logic_holes 已于 2026-09-15 退出回退授权
    # （判据不可达，登记单 20260915_回退熔断账实不符与降级当通过 §三.1）。
    return NovelHealthReport(
        overall_pass=False,
        dimensions=[
            DimensionResult(
                "character_stability_high", "人设稳定", 3.0, 0.0, "<=", True,
                "llm/default", evidence=EvalEvidence(),
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


# ============================================================
# H. 降级不得当通过（G1 / H1 接线）
# ============================================================
class TestDegradedDimIsNotPass:
    """降级维（confidence=0）取的是 ``safe_default``，**不得参与通过判定**。

    对应登记单 ``20260915_回退熔断账实不符与降级当通过.md`` §二.R3。

    实测灵荒薪传：``readability`` 降级 7 次、``coherence`` 降级 5 次，降级值恒为
    ``safe_default``（评分维 100.0 / 计数维 0.0）——
    评分维降级 ⇒ ``100 >= 阈值`` ⇒ 判「通过」；``logic_holes`` 降级 ⇒ ``0 <= 0``
    ⇒ 判「零逻辑漏洞」。09-14 那条"历史最高分 85.71"实际有 12 个维度降级。

    旧实现的漏洞在 ``trustworthy`` 只看 ``[d for d in dimensions if not d.passed]``：
    降级维因为取默认值恰好达标、**根本不进这个集合** ⇒ 整份报告带着一堆默认值被判 pass。
    """

    @staticmethod
    def _dim(name: str, *, value: float, threshold: float, direction: str,
             required: bool, confidence: float) -> DimensionResult:
        ev = EvalEvidence()
        if confidence <= 0.0:
            ev.degrade("评分输出缺少 value 键（形状异常）")
        return DimensionResult(
            name, name, value, threshold, direction, required, "llm/default", evidence=ev
        )

    def _degraded_score_report(self) -> NovelHealthReport:
        """全维达标，但 readability 是降级取默认值 100 —— 旧逻辑判「✅ 通过」。"""
        return NovelHealthReport(
            overall_pass=True,
            score=100.0,
            dimensions=[
                self._dim("character_stability_high", value=0.0, threshold=0.0,
                          direction="<=", required=True, confidence=1.0),
                # safe_default=100.0 的评分维降级 → 假"追读力达标"
                self._dim("readability", value=100.0, threshold=80.0,
                          direction=">=", required=False, confidence=0.0),
            ],
        )

    def test_degraded_dim_is_unverified(self) -> None:
        rep = self._degraded_score_report()
        assert [d.name for d in rep.unverified] == ["readability"]
        assert rep.unverified[0].passed is True, "值确实达标（事实），但不可信"

    def test_gate_decision_is_recheck_not_pass(self) -> None:
        rep = self._degraded_score_report()
        assert rep.gate_decision() == "recheck", (
            "存在降级维 ⇒ 不得宣称通过（兜底默认值不能替我们通过）"
        )
        assert rep.verified_pass is False, "可信通过必须排除降级维"

    def test_degraded_zero_holes_is_not_zero_defects(self) -> None:
        """`logic_holes` 降级 ⇒ 旧逻辑读成"零逻辑漏洞"（假硬伤清零）。"""
        rep = NovelHealthReport(
            overall_pass=True,
            score=100.0,
            dimensions=[
                self._dim("logic_holes", value=0.0, threshold=0.0,
                          direction="<=", required=False, confidence=0.0),
            ],
        )
        assert rep.gate_decision() == "recheck"
        assert rep.verified_pass is False
        assert rep.to_dict()["unverified"] == ["logic_holes"]

    def test_verified_pass_when_all_credible_and_green(self) -> None:
        """反向用例：全维可信且达标 → 仍须判 pass（防过度拦截）。"""
        rep = NovelHealthReport(
            overall_pass=True,
            score=100.0,
            dimensions=[
                self._dim("character_stability_high", value=0.0, threshold=0.0,
                          direction="<=", required=True, confidence=1.0),
                self._dim("readability", value=92.0, threshold=80.0,
                          direction=">=", required=False, confidence=1.0),
            ],
        )
        assert rep.unverified == []
        assert rep.gate_decision() == "pass"
        assert rep.verified_pass is True

    def test_computed_dim_without_evidence_is_verified(self) -> None:
        """确定性计算维无 evidence → confidence=1.0，不得被误判为降级。"""
        rep = NovelHealthReport(
            overall_pass=False,
            score=0.0,
            dimensions=[
                DimensionResult("pacing_abnormal", "节奏异常", 0.9, 0.03, "<=", False,
                                "computed"),
            ],
        )
        assert rep.unverified == []
        assert rep.gate_decision() == "warn", "软维失败 → 只告警，不中断整批"

    def test_markdown_verdict_never_claims_pass_on_degraded(self) -> None:
        md = self._degraded_score_report().to_markdown()
        assert "✅ 通过" not in md, "展示层不得比判定层乐观（不得宣称通过）"
        assert "证据不可信" in md


# ============================================================
# I. 指纹库自校验（缓存可过期 ⇒ 写时去重门禁不得盲信缓存）
# ============================================================
#: 两段 ≥40 字的独立正文（``_check_dup`` 只比对 ≥40 字长段落）
_PARA_A = (
    "林凡把锉刀放下，说公差三分毛刺半厘，你的零件误差偏大，"
    "明天开始每一道工序之前先过一道自检，不合格的回炉重造。"
)
_PARA_B = (
    "夜色沉沉，石莽在巷口守着，听见檐上落下一滴水，"
    "握刀的手紧了紧，没有回头去看身后那片晃动的火光。"
)
_PARA_C = (
    "账册翻到新的一页，他用木炭写下第三十日的工坊记录，"
    "标准齿轮十二套，公差为零，合格品与废品分开摆放。"
)


class TestFingerprintCacheStaleness:
    """``.state/chapter_fingerprints.json`` 是**缓存**，不是真源。

    它只在「写章 / 改写 / 回滚」等少数路径增量更新，任何带外改动（回滚后重生成、
    批量重写、人工编辑）都会让它与成书脱节；写时去重门禁若盲信它 ⇒ 真重复漏检、
    又与已不存在的旧文本比对。实证（2026-09-24 灵荒工坊）：45 章里 14 章的缓存
    指纹与章文件**零重叠**，ch021 与 ch036 相似度 0.99 的重复段落在
    ``rewrite --gate block`` 下照常落盘。
    """

    @staticmethod
    def _write_chapter(d: Path, n: int, body: str) -> None:
        (d / "chapters" / f"ch{n:03d}.md").write_text(
            f"---\nchapter: {n}\ntitle: 第{n}章样例\n---\n\n"
            f"# 第 {n} 章 · 第{n}章样例\n\n{body}\n",
            encoding="utf-8",
        )

    @staticmethod
    def _seed_cache(d: Path, db: dict[str, Any]) -> Path:
        fp = d / ".state" / "chapter_fingerprints.json"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(
            json.dumps({"fingerprints": db}, ensure_ascii=False), encoding="utf-8"
        )
        return fp

    @staticmethod
    def _dup_checker(db: dict[str, Any]) -> Guardrails:
        return Guardrails(
            check_junk=False, check_title=False, check_dup=True,
            check_meta_leak=False, check_narrative_tell=False,
            check_density=False, fingerprint_db=db,
        )

    def test_canonical_chapter_key_normalizes_known_forms(self) -> None:
        assert canonical_chapter_key("ch036") == "36"
        assert canonical_chapter_key("036") == "36"
        assert canonical_chapter_key("ch36") == "36"
        assert canonical_chapter_key("36") == "36"

    def test_gate_detects_dup_hidden_by_stale_cache(self, tmp_path: Path) -> None:
        """缓存过期时旧路径漏检真重复；按章文件重建后必须检出。"""
        d = make_project(tmp_path, n_chapters=3)
        self._write_chapter(d, 1, _PARA_B)
        self._write_chapter(d, 2, _PARA_A)
        self._write_chapter(d, 3, _PARA_A)          # 第 3 章整段复制第 2 章
        # 缓存里第 2 章还是「已不存在的旧正文」
        stale = "旧版正文：这一段在成书里早已不存在，只残留在过期的指纹缓存中，长度也够四十字。"
        self._seed_cache(d, {"2": [[1, stale]]})
        ch3 = (d / "chapters" / "ch003.md").read_text(encoding="utf-8")

        # 旧行为：直接读缓存 ⇒ 与真成书比对失败 ⇒ 漏检
        raw = load_fingerprints(d / ".state" / "chapter_fingerprints.json")
        raw.pop("3", None)
        assert self._dup_checker(raw).check_cross_chapter_dup(ch3) == [], (
            "本用例的前提：盲信过期缓存检不出这段复制"
        )

        # 新行为：按章文件重建（唯一真源）⇒ 必须检出
        hits = self._dup_checker(load_book_fingerprints(d, exclude=3)).check_cross_chapter_dup(ch3)
        assert hits, "重建指纹库后第 3 章的整段复制必须被检出"
        assert "第 2 章" in hits[0]

    def test_rebuild_refreshes_cache_and_normalizes_keys(self, tmp_path: Path) -> None:
        """重建后缓存按键规范（``ch036`` / ``036`` → ``36``），过期条目被替换。"""
        d = make_project(tmp_path, n_chapters=3)
        self._write_chapter(d, 1, _PARA_B)
        self._write_chapter(d, 2, _PARA_A)
        self._write_chapter(d, 3, _PARA_C)
        self._seed_cache(d, {"ch002": [[1, "残留甲"]], "003": [[2, "残留乙"]]})

        load_book_fingerprints(d)
        on_disk = load_fingerprints(d / ".state" / "chapter_fingerprints.json")
        assert set(on_disk) == {"1", "2", "3"}
        norms = [e[1] for e in on_disk["2"]]
        assert "残留甲" not in norms and "残留乙" not in norms, "过期条目必须被替换"
        assert any("公差三分" in n for n in norms), "现有正文必须建入指纹库"

    def test_exclude_removes_self_to_avoid_false_positive(self, tmp_path: Path) -> None:
        """不排除自身 ⇒ 本章与自己的旧指纹相似度 1.00（假阳性）；排除后须干净。"""
        d = make_project(tmp_path, n_chapters=3)
        self._write_chapter(d, 1, _PARA_B)
        self._write_chapter(d, 2, _PARA_A)
        self._write_chapter(d, 3, _PARA_C)
        ch2 = (d / "chapters" / "ch002.md").read_text(encoding="utf-8")

        assert self._dup_checker(load_book_fingerprints(d)).check_cross_chapter_dup(ch2), (
            "未排除自身时应命中「与自己上一条指纹相似度 1.00」"
        )
        assert self._dup_checker(
            load_book_fingerprints(d, exclude=2)
        ).check_cross_chapter_dup(ch2) == []
