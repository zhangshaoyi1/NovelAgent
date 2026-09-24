"""T-5 ConsistencyChecker 数据化落地测试

覆盖：
- check() 不再抛 NotImplementedError，返回 ConsistencyReport
- assess_architecture_impact() 返回空壳报告（不再抛 NotImplementedError）
- 内置 field_conflict 规则委托 ConflictArbiter（mock 验证）
- 五个 post-write 真实规则：timeline_conflict / relation_conflict /
  golden_finger_overstep / realm_overstep / realm_span
- to_markdown() 不再抛 NotImplementedError
"""
from __future__ import annotations

import json
from pathlib import Path

from agent.core.quality.consistency.checker import (
    ConsistencyChecker,
    ConsistencyReport,
    CheckTrigger,
)


def _seed_project(project: Path) -> None:
    """构造最小可校验项目：角色档案 + 关系网 + 世界观境界体系。"""
    (project / "characters").mkdir(parents=True)
    # 周伯：后期才牺牲（当前应存活）——用于 timeline/relation 校验
    (project / "characters" / "周伯.md").write_text(
        "---\nname: \"周伯\"\n---\n# 角色档案\n## 内核\n"
        "- 弧光：终结状态：为保护线索而死，死前将尸语秘术传予李承安\n",
        encoding="utf-8",
    )
    # 李承安：禁用词含 系统、金手指——用于 golden_finger 校验
    (project / "characters" / "李承安.md").write_text(
        "---\nname: \"李承安\"\n---\n# 角色档案\n## 语言指纹\n- 禁用词：系统、金手指\n",
        encoding="utf-8",
    )
    (project / "relations").mkdir(parents=True)
    (project / "relations" / "graph.md").write_text(
        "# 关系网\n"
        "## 节点\n| ID | 角色 | 分组 |\n|---|---|---|\n"
        "| A | 李承安 | protagonist |\n| C | 周伯 | mentor |\n\n"
        "## 边（关系）\n"
        "| 起 | 止 | 类型 | 强度 | 起于 | 备注 |\n"
        "|---|---|---|---|---|---|\n"
        "| A | C | 和解 | 9 | ch164 | 周伯相助 |\n",
        encoding="utf-8",
    )
    (project / "world.md").write_text(
        "## 世界观\n境界：凡人 < 练气 < 筑基\n", encoding="utf-8"
    )


def test_check_no_longer_raises(tmp_path: Path) -> None:
    """check() 不再抛 NotImplementedError，无设定变更且无章节时无冲突（passed=True）"""
    checker = ConsistencyChecker(project_dir=tmp_path)
    report = checker.check(CheckTrigger.PRE_WRITE, ctx={})
    assert isinstance(report, ConsistencyReport)
    assert report.passed is True
    assert report.conflicts == []


def test_assess_architecture_impact_returns_report(tmp_path: Path) -> None:
    """架构影响评估返回空壳报告"""
    checker = ConsistencyChecker(project_dir=tmp_path)
    report = checker.assess_architecture_impact()
    assert isinstance(report, ConsistencyReport)
    assert report.conflicts == []
    assert report.passed is True


def test_field_conflict_delegates_to_arbiter(tmp_path: Path) -> None:
    """field_conflict 规则委托 ConflictArbiter.check_new_setting 收集冲突"""

    class _FakeConflict:
        is_block = True
        description = "主角属性与世界观冲突"
        affected_chapters = ["ch3"]
        suggestions = ["调整设定"]

    class _FakeReport:
        conflicts = [_FakeConflict()]

    class _FakeArbiter:
        def check_new_setting(self, new_setting, subline_id=None):
            return _FakeReport()

    checker = ConsistencyChecker(project_dir=tmp_path)
    checker._arbiter = _FakeArbiter()
    report = checker.check(
        CheckTrigger.PRE_UPDATE_SETTING,
        ctx={"new_setting": "主角改为水属性灵根"},
    )
    assert any(c.rule_id == "field_conflict" for c in report.conflicts)
    assert report.passed is False


def test_timeline_conflict_catches_dead_character(tmp_path: Path) -> None:
    """POST_WRITE：角色档案为后期才牺牲，本章却称其已故 → BLOCK"""
    _seed_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "周伯在十年前便已经故去，尸骨早已凉透。"},
    )
    assert any(c.rule_id == "timeline_conflict" for c in report.conflicts)
    assert report.passed is False  # BLOCK 阻断


def test_timeline_conflict_passes_when_alive(tmp_path: Path) -> None:
    """POST_WRITE：角色在世验尸 → 不误报 timeline_conflict"""
    _seed_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "周伯蹲在尸身旁，低声道：尸体会说话。"},
    )
    assert not any(c.rule_id == "timeline_conflict" for c in report.conflicts)


def test_relation_conflict_fires_for_dead_with_active_edge(tmp_path: Path) -> None:
    """POST_WRITE：断言周伯已故，且关系网有互动型活跃边 → WARN"""
    _seed_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "李承安想起，周伯早已故去。"},
    )
    assert any(c.rule_id == "relation_conflict" for c in report.conflicts)


def test_golden_finger_overstep_fires(tmp_path: Path) -> None:
    """POST_WRITE：角色禁用词含系统/金手指，本章却触发系统 → WARN"""
    _seed_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "李承安脑海中的系统猛然激活，金手指展开。"},
    )
    assert any(c.rule_id == "golden_finger_overstep" for c in report.conflicts)


def test_realm_overstep_fires_on_unregistered(tmp_path: Path) -> None:
    """POST_WRITE：宣称突破至未登记境界 → WARN"""
    _seed_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "李承安突破至化神境，气息暴涨。"},
    )
    assert any(c.rule_id == "realm_overstep" for c in report.conflicts)


def test_realm_overstep_no_false_on_registered(tmp_path: Path) -> None:
    """POST_WRITE：突破至已登记境界 → 不误报"""
    _seed_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "李承安突破至练气境，气息微涨。"},
    )
    assert not any(c.rule_id == "realm_overstep" for c in report.conflicts)


# ============================================================================
# realm_span：境界**跨度**检测（2026-09-24 灵荒工坊 ch41 引灵→淳真 连跳两境）
# ============================================================================
def _seed_realm_span_project(
    project: Path, carried: str = "引灵中期", *, with_ledger: bool = True
) -> None:
    """构造带「有序境界体系 + 主角承接境界」的项目。

    境界序位：引灵(0) < 栖气(1) < 淳真(2) < 开玄(3)。
    """
    (project / "characters").mkdir(parents=True)
    (project / "characters" / "李承安.md").write_text(
        '---\nname: "李承安"\n---\n# 角色档案\n## 内核\n- 弧光：隐忍→果敢\n',
        encoding="utf-8",
    )
    (project / "characters" / "周伯.md").write_text(
        '---\nname: "周伯"\n---\n# 角色档案\n## 内核\n- 弧光：旁观者→导师\n',
        encoding="utf-8",
    )
    (project / "world.md").write_text(
        "## 修炼境界体系\n"
        "1. **引灵**：引天地灵气入体\n"
        "2. **栖气**：灵气稳定蓄于体内\n"
        "3. **淳真**：灵气提纯转化为淳真之力\n"
        "4. **开玄**：周身玄络贯通\n",
        encoding="utf-8",
    )
    (project / ".state").mkdir(parents=True)
    (project / ".state" / "plan.json").write_text(
        json.dumps(
            {"character_skeleton": [{"name": "李承安", "role": "主角"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if not with_ledger:
        return
    ledger_dir = project / ".state" / "continuity"
    ledger_dir.mkdir(parents=True)
    (ledger_dir / "ledger.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "facts": [
                    {
                        "domain": "character",
                        "subject_id": "李承安",
                        "field": "cultivation",
                        "value": carried,
                        "source_commit_id": "ch1",
                        "evidence": "登记",
                    }
                ],
                "knowledge": [],
                "open_loops": [],
                "handoffs": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_realm_span_blocks_two_stage_jump(tmp_path: Path) -> None:
    """POST_WRITE：承接引灵，本章宣称突破至淳真（差 2 档）→ BLOCK"""
    _seed_realm_span_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "李承安盘膝而坐。境界突破至淳真期初期，气息暴涨。"},
    )
    assert any(c.rule_id == "realm_span" for c in report.conflicts)
    assert report.passed is False


def test_realm_span_allows_single_stage_jump(tmp_path: Path) -> None:
    """POST_WRITE：承接引灵，本章推进一境至栖气 → 放行（连续演进一境合法）"""
    _seed_realm_span_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "李承安眉头一皱，境界突破至栖气期。"},
    )
    assert not any(c.rule_id == "realm_span" for c in report.conflicts)


def test_realm_span_allows_when_carry_missing(tmp_path: Path) -> None:
    """POST_WRITE：账本无承接境界 → 放行（基线不可确定就不判，宁漏不误）"""
    _seed_realm_span_project(tmp_path, with_ledger=False)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "李承安境界突破至淳真期初期。"},
    )
    assert not any(c.rule_id == "realm_span" for c in report.conflicts)


def test_realm_span_allows_unregistered_claim(tmp_path: Path) -> None:
    """POST_WRITE：宣称境界不在世界体系内 → 交 realm_overstep（WARN），本规则放行"""
    _seed_realm_span_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "李承安突破至化神境，气息暴涨。"},
    )
    assert not any(c.rule_id == "realm_span" for c in report.conflicts)


def test_realm_span_allows_other_subject(tmp_path: Path) -> None:
    """POST_WRITE：越级声明归属**非承接主体**（周伯）→ 放行，不栽到主角头上"""
    _seed_realm_span_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE,
        ctx={"chapter_text": "周伯突破至淳真期初期，气息暴涨。"},
    )
    assert not any(c.rule_id == "realm_span" for c in report.conflicts)


def test_writer_hard_refuses_realm_span() -> None:
    """realm_span 是确定性 blocking 证据 ⇒ 写手不得"兜底落盘"把它固化入库。"""
    from agent.agents.writer_agent import WriterAgent

    report = {
        "issues": [{"rule_id": "consistency_realm_span", "severity": "blocking"}]
    }
    assert WriterAgent._golden_refuse_save(report, {"chapter_num": 41}) is True


def test_to_markdown_no_raise(tmp_path: Path) -> None:
    """to_markdown() 不再抛 NotImplementedError"""
    _seed_project(tmp_path)
    checker = ConsistencyChecker(tmp_path)
    report = checker.check(
        CheckTrigger.POST_WRITE, ctx={"chapter_text": "周伯早已故去。"}
    )
    md = report.to_markdown()
    assert isinstance(md, str) and "一致性" in md


# ============================================================================
# derive_realm_fact：从正文确定性推导**主角境界推进**事实（2026-09-24 灵荒工坊
# ch043 实证——突破写在引号独立句里 「"突破了。栖气期初期。"」，既没被确定性抽取器
# 落盘、也没被 LLM 结算记入 ⇒ 承接锚点永不前进 ⇒ ch044/ch045 按旧境界写自相矛盾）
# ============================================================================
def test_carried_realm_recognizes_state_suffixed_alias(tmp_path: Path) -> None:
    """承接锚点须认「修为状态」等别名——结算员对同一概念会写出多个字段名。"""
    from agent.core.quality.consistency.checker import (
        _carried_realm_index,
        _load_world_realm_order,
    )

    _seed_realm_span_project(tmp_path)
    ledger_file = tmp_path / ".state" / "continuity" / "ledger.json"
    data = json.loads(ledger_file.read_text(encoding="utf-8"))
    data["facts"][0]["field"] = "修为状态"
    ledger_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    order = _load_world_realm_order(tmp_path)
    assert _carried_realm_index(tmp_path, order) == ("李承安", 0)


def test_derive_realm_fact_cross_sentence_declaration(tmp_path: Path) -> None:
    """ch043 形态：突破宣告独立成句（角色名不在同句）也要推导出事实。"""
    from agent.core.quality.consistency.checker import derive_realm_fact

    _seed_realm_span_project(tmp_path)
    body = (
        "李承安躺到草席上，运转功法。\n"
        "轰——\n瓶颈冲破。\n"
        '李承安睁开眼睛，吐出一口浊气。"突破了。栖气期初期。"\n'
    )
    fact = derive_realm_fact(tmp_path, body)
    assert fact is not None, "跨句突破宣告必须推导出境界事实"
    assert fact["subject_id"] == "李承安"
    assert fact["field"] == "realm"
    assert "栖气" in fact["value"]


def test_derive_realm_fact_rejects_leap(tmp_path: Path) -> None:
    """越级（承接 引灵 → 淳真，差 2 档）不得由代码追认——留给 realm_span 阻断。"""
    from agent.core.quality.consistency.checker import derive_realm_fact

    _seed_realm_span_project(tmp_path)
    assert derive_realm_fact(tmp_path, '李承安盘膝而坐。"突破了。淳真期初期。"') is None


def test_derive_realm_fact_rejects_other_subject(tmp_path: Path) -> None:
    """突破归属配角（周伯）⇒ 不记到主角头上。"""
    from agent.core.quality.consistency.checker import derive_realm_fact

    _seed_realm_span_project(tmp_path)
    assert derive_realm_fact(tmp_path, '周伯大笑。"突破了。栖气期初期。"') is None


def test_derive_realm_fact_none_without_carry(tmp_path: Path) -> None:
    """账本无承接境界 ⇒ 无从判断"是否只推进一档" ⇒ 不追认（宁漏不误）。"""
    from agent.core.quality.consistency.checker import derive_realm_fact

    _seed_realm_span_project(tmp_path, with_ledger=False)
    assert derive_realm_fact(tmp_path, '李承安盘膝而坐。"突破了。栖气期初期。"') is None


def test_derive_realm_fact_ignores_regression(tmp_path: Path) -> None:
    """正文宣称低于承接（回退）⇒ 不追认，锚点保持单调不回退。"""
    from agent.core.quality.consistency.checker import derive_realm_fact

    _seed_realm_span_project(tmp_path, carried="栖气中期")
    assert derive_realm_fact(tmp_path, '李承安低语。"修为跌回。引灵期。"') is None
