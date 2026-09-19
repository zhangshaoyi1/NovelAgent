"""红线：M3 写手侧「平权规则」按**本章强度档位**分叉（2026-09-19）。

命题（登记单 ``20260918_章间落差被抹平_同类点位全量普查`` §3/§9）
----------------------------------------------------------------
7 条平权规则对**所有章一刀切**。规划层明明登记了 ``垫片``/``日常`` 这类
"本就该放松"的档位，写手侧却仍被要求"前 500 字必须有冲突""必须有爽点锚点"
"章末必须有悬念""描写 ≥ 30%" ⇒ 放松章只能靠注水凑齐这些硬指标 ⇒
与"反注水"的评审维度正面对撞（死循环的成因之一）。

★ 落点纠正（本轮最贵的发现）
--------------------------
规则第 3-6 条此前有**两份真源**：``writer_agent._WRITER_BASE``（硬编码）与
``prompts/m5/generate.md`` 的 ``# system`` 段（注释写着"同源，保证风格一致"，
靠手工同步）。而 ``m5.generate`` 全仓**只有 ``render_user`` 一个调用点**——
它的 system 段在 autowrite 主路径上从未被渲染 ⇒ **改 generate.md 等于改死文件**。
现把第 3-6 条抽到 ``prompts/m5/pace_rules.md`` 单一真源，由 ``_writer_base`` 装配。

本文件钉死四条命脉：
1. 非放松档 ⇒ 写手 system 提示**逐字等同改造前**（纪律 #4：口子只挡历史）；
2. 放松档 ⇒ 确实放松，且**降强度不降"有没有"**（纪律 #17）；
3. 档位 → 提示词整条链**行为级可达**（纪律 #10）；
4. 放松档名单不得在提示词里出现第二份（纪律 #19）。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

_AGENT = Path(__file__).resolve().parents[1]
_SRC = _AGENT / "src" / "agent"
_PROMPTS = _SRC / "prompts"


def _read(rel: str) -> str:
    return (_SRC / rel).read_text(encoding="utf-8")


def _prompt_text(rel: str) -> str:
    return (_PROMPTS / rel).read_text(encoding="utf-8")


# ============================================================
# 改造前 _WRITER_BASE 快照（由 .workbuddy/diag/m3_inject_snapshot.py 注入，勿手改）
# ============================================================
_PRE_M3_WRITER_BASE = r"""【输出协议 · 最高优先级，任何违反都会导致本章作废】
你的每一次输出都必须且只能是**单个裸 JSON 信封对象**，字段名逐字一致：
{"think": "简短思考", "action": "finish 或 tool_call", "tool": null, "args": {}, "draft": "完整章节正文"}。
除裸 JSON 外，禁止输出任何散文、规划思路、思考过程、```json 代码围栏、工具调用日志或解释文字。
若 action 为 tool_call，则只填 tool 与 args，draft 置 null；若 action 为 finish，则 tool 置 null、args 置 {}，draft 填**完整章节正文**。

你是一名顶级修仙小说写手，专职产出上述 JSON 信封 ``draft`` 字段里的章节正文。以下创作要求均作用于 draft 中的正文。

写作要求：
1. 严格遵守设定集（文风/视角/节奏/字数/禁用词/禁用元素）
2. 本章必须属于当前压力曲线阶段，按阶段控制张力
3. 前 500 字内出现冲突/悬念/反差之一
4. 本章至少含一个爽/虐/燃/甜/惊锚点
5. 章末必须有悬念/反转/期待之一
6. 场景+动作+环境描写合计 ≥ 30%
7. 【禁用词与AI腔】禁用词"突然/忽然/就在这时/微微一笑"全章 ≤ 2 次；以下高频AI腔词句全章 = 0 次：喃喃自语、嘴角微微上扬、嘴角勾起、心中一动、心头一震、语气平静、缓缓开口、若有所思、眼底闪过一丝、眼中闪过、深吸一口气、映入眼帘、沉声道——一律用具体的动作/生理反应/对话代替（✗"嘴角微微上扬"→✓"他把茶杯往桌上一磕，笑出了声"）；写完一段后自查，命中即当场改写
8. 角色台词必须符合其语言指纹
9. 不与世界观 / 支线 / 角色档案冲突
10. 如需埋/回收伏笔，自然融入剧情
11. 高潮章节自动扩篇幅 + 多视角 + 慢镜头
12. 【格式提醒】再次强调：每次输出都必须是顶部「输出协议」规定的单个裸 JSON 信封，严禁把正文或规划写成纯文本。
13. 【纯中文约束】正文部分仅输出简体中文小说正文，禁止输出任何英文字母串、系统提示片段、错误/日志信息（如 "Visualization failed"、"Cost"、"undefined"、"[system]" 等）、符号或占位标记；提交前逐段自检：若出现任何英文单词或英文句子（含拟声、感叹、普通词），必须重写该段为中文后再提交。
14. 【标题约束】draft 中章节正文首行必须是「# 第N章 · <有信息量、非模板化的场景化标题>」；禁止「第N章·第N章」这类占位标题，禁止留空，禁止与已发布章节标题重复。
15. 【避免雷同】避免与前述章节使用完全相同的开场白/场景描写（如都从同一句环境白描起笔）；若需描写相似场景，请换视角、换措辞或换切入点，确保本章开头具有独立性。
16. 【字数区间要求】draft 中本章正文的中文字数应在「目标字数×0.8 到 目标字数×1.2」之间（以目标字数为中值的合理区间）；写不足下限就会被打回扩写。请围绕目标字数铺足场景/动作/对白/情节，写够再收尾，禁止用「伏笔或悬念一句带过」来压缩篇幅；也不宜过度注水超过上限。

你拥有若干工具（见下方动作协议中的可用工具）。写之前可调用工具核对设定 / 召回前文 / 自检字数 / 自评质量；准备好后，把 action 设为 'finish' 并在 draft 中提交**完整章节正文**。"""


def _writer_base():
    from agent.agents.writer_agent import _writer_base

    return _writer_base


# ============================================================
# 一、非放松档逐字不变（纪律 #4）
# ============================================================
class TestNotRelaxedIsByteIdentical:
    def test_writer_base_unchanged_when_not_relaxed(self) -> None:
        """未标档位 / 非放松档 ⇒ system 提示与改造前**逐字相同**。

        这是本次改造的安全底线：分叉只许改变放松档，绝不许顺手改动其余所有章
        的提示词（否则等于拿全量章节做未受控实验）。
        """
        assert "<<<SNAPSHOT>>>" not in _PRE_M3_WRITER_BASE, "快照未注入，红线失效"
        got = _writer_base()(False)
        assert got == _PRE_M3_WRITER_BASE, (
            "非放松档的写手 system 提示与改造前不一致——"
            "分叉改动外溢到了所有普通章（纪律 #4）"
        )

    def test_default_argument_is_not_relaxed(self) -> None:
        """缺省即原规则：任何未显式传档位的调用点都不得被放松。"""
        assert _writer_base__default() is False

    def test_no_relaxed_wording_by_default(self) -> None:
        base = _writer_base()(False)
        for word in ("可感知的进入点", "承接或蓄力", "去向感", "≥ 15%"):
            assert word not in base, f"原规则里混入了放松措辞「{word}」"


def _writer_base__default() -> bool:
    """取 ``_writer_base`` 的 ``pace_relaxed`` 缺省值（必须是 False）。"""
    return inspect.signature(_writer_base()).parameters["pace_relaxed"].default


# ============================================================
# 二、放松档确实放松，且守住底限（纪律 #17）
# ============================================================
class TestRelaxedBranch:
    @pytest.fixture()
    def relaxed(self) -> str:
        return _writer_base()(True)

    def test_rules_are_relaxed(self, relaxed: str) -> None:
        for word in ("可感知的进入点", "承接或蓄力", "去向感", "≥ 15%"):
            assert word in relaxed, f"放松档未出现放松措辞「{word}」"

    def test_hard_requirements_are_gone(self, relaxed: str) -> None:
        for word in ("≥ 30%", "爽/虐/燃/甜/惊锚点", "必须有悬念/反转/期待"):
            assert word not in relaxed, f"放松档仍残留硬要求「{word}」"

    def test_floor_is_kept(self, relaxed: str) -> None:
        """★ 降的是**强度**不是"有没有"——否则等于制造新的"判而不可修"。

        放松档仍须有一个情绪落点、仍禁止天气/风景空转开场、仍要有章末去向感。
        """
        for word in (
            "仍须有一个明确的情绪落点",
            "禁止用天气、风景空转开场",
            "去向感",
        ):
            assert word in relaxed, f"放松档把底限也删了：「{word}」"

    def test_only_rules_3_to_6_differ(self) -> None:
        old_lines = _PRE_M3_WRITER_BASE.splitlines()
        new_lines = _writer_base()(True).splitlines()
        assert len(old_lines) == len(new_lines), "放松档改动了行数（应仅替换 3-6 条文案）"
        diff_idx = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
        changed = {old_lines[i][:2] for i in diff_idx}
        assert changed <= {"3.", "4.", "5.", "6."}, (
            f"放松档改动越界：{changed}（只许改第 3-6 条）"
        )


# ============================================================
# 三、真源与可达性（纪律 #19 / #10 / #5）
# ============================================================
class TestSingleSourceAndWiring:
    def test_pace_rules_prompt_is_the_source(self) -> None:
        from agent.core.infra.prompt_manager import pm

        p = pm.get("m5.pace_rules")
        strict = p.render_system(pace_relaxed=False)
        relax = p.render_system(pace_relaxed=True)
        assert "≥ 30%" in strict and "≥ 15%" in relax
        assert strict != relax

    def test_writer_agent_does_not_hardcode_rules_3_to_6(self) -> None:
        """writer_agent 里不得再出现规则 3-6 的字面量（否则又是两份真源）。"""
        src = _read("agents/writer_agent.py")
        for literal in (
            "场景+动作+环境描写合计 ≥ 30%",
            "本章至少含一个爽/虐/燃/甜/惊锚点",
            "章末必须有悬念/反转/期待之一",
        ):
            assert literal not in src, (
                f"writer_agent 又把规则写死了一份：「{literal}」"
            )

    def test_writer_agent_renders_from_pace_rules(self) -> None:
        src = _read("agents/writer_agent.py")
        assert 'pm.get("m5.pace_rules").render_system' in src

    def test_system_prompt_threads_pace_relaxed(self) -> None:
        """行为级：``_system_prompt`` 真把档位信号送进 system 提示。"""
        from agent.agents.writer_agent import WriterAgent

        w = WriterAgent.__new__(WriterAgent)  # 不触碰构造副作用
        strict = w._system_prompt(critique=None, min_words=None, max_words=None,
                                  pace_relaxed=False)
        relax = w._system_prompt(critique=None, min_words=None, max_words=None,
                                 pace_relaxed=True)
        assert "≥ 30%" in strict and "≥ 15%" in relax

    def test_run_and_run_async_read_signal_from_ctx(self) -> None:
        from agent.agents.writer_agent import WriterAgent

        for name in ("run", "run_async"):
            src = inspect.getsource(getattr(WriterAgent, name))
            assert 'ctx.get("pace_relaxed")' in src, f"{name} 未从 ctx 取档位信号"

    def test_m5_context_emits_only_the_boolean(self) -> None:
        """ctx 只透传布尔 ``pace_relaxed``；档位名不得再传（提示词无需第二份）。"""
        src = _read("workflows/writing/m5_context.py")
        assert '"pace_relaxed": pace_relaxed' in src
        assert '"pace_tier"' not in src, "档位名透传已删除，勿再加回（悬挂参数）"

    def test_template_has_no_dangling_pace_tier_variable(self) -> None:
        """generate.md 不得引用 ``pace_tier``——没人会传它（纪律 #7）。"""
        text = _prompt_text("m5/generate.md")
        assert "pace_tier" not in text, (
            "generate.md 又引用了无人传值的 pace_tier（悬挂变量）"
        )


# ============================================================
# 四、档位派生（DesignBrief）
# ============================================================
def _mk_project(tmp_path: Path, subline_md: str) -> Path:
    root = tmp_path / "proj"
    (root / "sublines" / "S01").mkdir(parents=True, exist_ok=True)
    (root / "sublines" / "S01" / "subline.md").write_text(subline_md, encoding="utf-8")
    return root


_SUBLINE = """# 支线设定

## 章节钩子设计

第7章：章首钩子=清晨开铺｜章尾钩子=账目差一笔｜目标情绪=舒缓｜档位=垫片
第8章：章首钩子=对决前夜｜章尾钩子=反派现身｜目标情绪=燃｜档位=高潮

## 情节点序列

第7章：清点存货，与邻铺闲话
"""


class TestTierDerivation:
    def test_relaxed_tier_flags_relaxed(self, tmp_path: Path) -> None:
        from agent.core.story.design_brief import build_design_brief

        b = build_design_brief(_mk_project(tmp_path, _SUBLINE), 7, subline_id="S01")
        assert b.pace_tier == "垫片"
        assert b.pace_relaxed is True

    def test_tense_tier_is_not_relaxed(self, tmp_path: Path) -> None:
        from agent.core.story.design_brief import build_design_brief

        b = build_design_brief(_mk_project(tmp_path, _SUBLINE), 8, subline_id="S01")
        assert b.pace_tier == "高潮"
        assert b.pace_relaxed is False

    def test_unmarked_chapter_keeps_original_rules(self, tmp_path: Path) -> None:
        """★ 纪律 #4：未标档位（老数据）既不放松也不收紧。"""
        from agent.core.story.design_brief import build_design_brief

        b = build_design_brief(_mk_project(tmp_path, _SUBLINE), 9, subline_id="S01")
        assert b.pace_tier == ""
        assert b.pace_relaxed is False

    def test_pace_tier_matches_rendered_intent(self, tmp_path: Path) -> None:
        """★ 两处解析（字段 vs 渲染文本）必须同源——``design_brief.py`` 注释承诺的红线。

        ``build_design_brief`` 里 ``pace_tier`` 与 ``_render_chapter_intent`` 各解析
        一次细纲；若两边算法漂移，写手看到的档位与判据用的档位就会不一致
        （与"档位串档"同型缺陷）。

        ⚠ 不能用 ``parse_pace_tier(chapter_intent)`` 做这件事：N1 回退行会带
        **别章**的 ``档位=`` 字样（已标「非本章」），全块扫描会把别章档位当本章的。
        """
        from agent.core.story.design_brief import build_design_brief

        for ch, want in ((7, "垫片"), (8, "高潮"), (9, "")):
            b = build_design_brief(_mk_project(tmp_path, _SUBLINE), ch, subline_id="S01")
            assert b.pace_tier == want, f"ch{ch} 档位字段异常"
            if want:
                tier_line = next(
                    ln for ln in b.chapter_intent.splitlines()
                    if ln.startswith("- **本章强度档位")
                )
                assert f"本章强度档位：{want}" in tier_line, (
                    f"ch{ch}: 渲染给写手/评委的档位与字段「{want}」不一致"
                )
            else:
                # 未标档位 ⇒ **不得**渲染本章档位行（纪律 #4：不放松也不收紧）
                assert "本章强度档位" not in b.chapter_intent, (
                    f"ch{ch}: 本章未标档位却渲染了档位行（拿别章的尺子量本章）"
                )

    def test_borrowed_tier_is_labelled_non_current(self, tmp_path: Path) -> None:
        """N1 回退行会带**别章**的 ``档位=`` ⇒ 必须带「非本章」标注。

        残留风险（登记在 ``MEMORY-未收口.md``）：标注只降低、不消除串档风险——
        评委仍可能读漏标注。彻底解法是渲染时剔除回退行里的档位字段，
        代价是回退行信息变少，故留作后续收口项。
        """
        from agent.core.story.design_brief import build_design_brief

        b = build_design_brief(_mk_project(tmp_path, _SUBLINE), 9, subline_id="S01")
        assert "档位=" in b.chapter_intent, "样本前提：回退行确实带别章档位"
        assert "非本章" in b.chapter_intent, (
            "回退行带了别章档位却没有「非本章」标注 ⇒ 档位串档"
        )

    def test_relaxed_delegates_to_pace_tier_ssot(self) -> None:
        """「哪些档算放松」只能来自 ``PaceTier.relaxed``，不得在此另列名单。"""
        src = _read("core/story/design_brief.py")
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "DesignBrief":
                for kw in node.keywords:
                    if kw.arg == "pace_relaxed":
                        found = True
                        seg = ast.dump(kw.value)
                        assert "relaxed" in seg, "pace_relaxed 未取自 PaceTier.relaxed"
        assert found, "build_design_brief 未装配 pace_relaxed"


# ============================================================
# 五、放松档名单不得在提示词里出现第二份（纪律 #19）
# ============================================================
class TestNoSecondTierList:
    def test_prompts_do_not_enumerate_relaxed_tiers(self) -> None:
        """除规划侧 ``m3/outline.md``（必须列出全部档位供规划者选）外，
        任何提示词文件都不得**枚举**放松档名单——枚举即第二份真源，
        新增/改名一个档位就会静默失效。
        """
        from agent.core.story.chapter_contract import PACE_TIERS

        relaxed_names = [t.name for t in PACE_TIERS if t.relaxed]
        assert len(relaxed_names) >= 2, "样本前提：放松档至少两个"
        whitelist = {"m3/outline.md"}
        for p in sorted(_PROMPTS.rglob("*.md")):
            rel = p.relative_to(_PROMPTS).as_posix()
            if rel in whitelist:
                continue
            text = p.read_text(encoding="utf-8")
            hit = [n for n in relaxed_names if n in text]
            assert len(hit) < 2, (
                f"{rel} 枚举了放松档名单 {hit}——"
                f"档位真源是 chapter_contract.PACE_TIERS，提示词不许再抄一份"
            )


# ============================================================
# 六、跨文件豁免一致性（规则说豁免，下游就必须真放行）
# ============================================================
class TestExemptionIsReachable:
    def test_hooks_exemption_landed_in_pace_rules(self) -> None:
        """hooks.md 承诺"放松档不必强钩子" ⇒ 规则 3 必须真的不强钩子。"""
        hooks = _prompt_text("methods/hooks.md")
        assert "不必强钩子" in hooks
        from agent.core.infra.prompt_manager import pm

        relax = pm.get("m5.pace_rules").render_system(pace_relaxed=True)
        assert "不必强凑冲突或反差" in relax

    def test_payoff_exemption_landed_in_pace_rules(self) -> None:
        payoff = _prompt_text("methods/payoff.md")
        assert "放松档" in payoff and "承接" in payoff
        from agent.core.infra.prompt_manager import pm

        relax = pm.get("m5.pace_rules").render_system(pace_relaxed=True)
        assert "承接或蓄力" in relax
