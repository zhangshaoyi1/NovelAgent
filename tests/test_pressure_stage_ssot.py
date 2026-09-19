"""M6-B 红线：压力阶段词表「单一真源 + 三端同源」（纪律 #19/#22）。

问题（M6-B 取证，2026-09-19）
────────────────────────────────────────────────────────────────────
「压力阶段」此前是**没有登记表的自由文本**：
  · 生产者 ``m5_context._determine_pressure_stage`` 把 subline.md
    压力曲线表格第 1 列**原样** return；
  · 消费者调 ``agentic_write.py:339`` / ``:809`` 用
    ``ctx["pressure_stage"] == "高潮"`` 做**字面量比较**；
  · 结果：曲线表写「舒缓/收束」⇒ 比较静默为假，**无报错无日志**。

实测（70 份含压力曲线表的 subline.md）：
  · 主词表（52 项目）：铺垫 / 冲突 / 高潮 / 舒缓
  · 未登记变体：``舒缓/收束``（2）、``结局/收束``（1）
  ⇒ 纪律 #22「判据不能建立在调用方猜对语义之上」。

本红线把"同源"变成**机器可核对**的关系（成员/派生），不是"数值相等"：
  R1 主词表非空、无重复、顺序即张力降序
  R2 归一表 target 必须是主词（防链式漂移）
  R3 归一函数对主词**幂等**（normalize(normalize(x)) == normalize(x)）
  R4 归一函数不臆造：未登记且无前缀匹配 ⇒ 返回空串（不是原样）
  R5 ★ **真实语料核对**：扫真实 subline.md，所有出现的阶段词
     必须能归一到主词（否则 = 新变体漏网）
  R6 ★ **消费者字面量核对**：下游 ``== "高潮"`` 的比较对象
     必须来自 PRESSURE_STAGES，不得手写（AST 级检查）

纪律依据：AGENTS.md 第 19 条（跨模块常量必须机器交叉核对）、
第 22 条（判据不得靠调用方猜语义）、第 21 条（静默失真）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from agent.core.story.chapter_contract import (
    PRESSURE_STAGES,
    PRESSURE_STAGE_ALIASES,
    PRESSURE_STAGE_RANK,
    is_registered_pressure_stage,
    normalize_pressure_stage,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = REPO_ROOT.parent
NOVELS = WORKSPACE / "novels"


# ── R1 主词表结构 ────────────────────────────────────────────────────────
class TestMainVocabulary:
    def test_non_empty_and_no_duplicates(self) -> None:
        assert PRESSURE_STAGES, "主词表不得为空"
        assert len(set(PRESSURE_STAGES)) == len(PRESSURE_STAGES), "主词表不得有重复"

    def test_rank_is_derived_and_consistent(self) -> None:
        """张力序位必须由主词表**派生**（成员关系，不是手写数值）。"""
        assert set(PRESSURE_STAGE_RANK) == set(PRESSURE_STAGES)
        # 序位唯一（防两个阶段同序位）
        assert len(set(PRESSURE_STAGE_RANK.values())) == len(PRESSURE_STAGES)
        ranks = [PRESSURE_STAGE_RANK[s] for s in PRESSURE_STAGES]
        assert ranks == sorted(ranks, reverse=True), (
            "PRESSURE_STAGES 声明顺序即张力降序，序位须递减"
        )

    def test_expected_members_present(self) -> None:
        """四主词是真实语料的观测结果，缺一即上游模板改了（须显式跟改）。"""
        assert set(PRESSURE_STAGES) == {"铺垫", "冲突", "高潮", "舒缓"}


# ── R2/R3/R4 归一函数行为 ────────────────────────────────────────────────
class TestNormalization:
    def test_alias_targets_are_main_words(self) -> None:
        """R2：别名只能指向主词（防「别名→别名」链式漂移）。"""
        for alias, canon in PRESSURE_STAGE_ALIASES.items():
            assert canon in PRESSURE_STAGES, f"{alias!r} 指向未登记主词 {canon!r}"
            assert alias not in PRESSURE_STAGES, f"{alias!r} 既是别名又是主词，属自相矛盾"

    def test_idempotent(self) -> None:
        """R3：归一对主词幂等，且对别名结果幂等。"""
        for name in PRESSURE_STAGES:
            assert normalize_pressure_stage(name) == name
            assert normalize_pressure_stage(normalize_pressure_stage(name)) == name
        for alias in PRESSURE_STAGE_ALIASES:
            once = normalize_pressure_stage(alias)
            assert normalize_pressure_stage(once) == once

    def test_does_not_fabricate(self) -> None:
        """R4：无证据的值必须「显性丢失」（返回空串），不得原样透传。"""
        for junk in ("", "   ", "乱七八糟", "unknown", "SEGMENT"):
            assert normalize_pressure_stage(junk) == "", (
                f"{junk!r} 既不登记也无前缀匹配 ⇒ 必须返回空串（不透传）"
            )

    def test_registered_predicate(self) -> None:
        for name in PRESSURE_STAGES:
            assert is_registered_pressure_stage(name)
        for alias in PRESSURE_STAGE_ALIASES:
            assert not is_registered_pressure_stage(alias), "别名不是主词"


# ── R5 真实语料核对（★ 最重要：防新变体漏网） ─────────────────────────────
@pytest.mark.skipif(not NOVELS.exists(), reason="无 novels/ 语料（CI/干净检出）")
class TestRealCorpusStages:
    """扫真实 subline.md 压力曲线表，所有阶段词必须可归一。"""

    @staticmethod
    def _collect_stage_words() -> dict[str, list[str]]:
        """返回 {阶段词: [来源文件...]}。"""
        found: dict[str, list[str]] = {}
        pat = re.compile(r"#+\s*剧集压力曲线(.*?)(?=\n#+\s|\Z)", re.S)
        for f in NOVELS.rglob("subline.md"):
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                continue
            m = pat.search(text)
            if not m:
                continue
            for line in m.group(1).splitlines():
                if line.startswith("|") and "阶段" not in line and "---" not in line:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 4 and parts[1]:
                        found.setdefault(parts[1], []).append(str(f))
        return found

    def test_every_real_stage_word_normalizes(self) -> None:
        """★★ 核心：真实语料里的每个阶段词都必须能归一到主词。

        失败含义：出现了**新变体**。处置二选一：
          · 若是合法创作语义 ⇒ 加进 PRESSURE_STAGE_ALIASES 并写明来源；
          · 若是笔误/脏数据 ⇒ 修数据。
        **不允许**靠"反正下游能跑"放过（本红线正是为拦这个）。
        """
        words = self._collect_stage_words()
        if not words:
            pytest.skip("未找到含「剧集压力曲线」表的 subline.md")
        unnormalizable = {
            w: files[:2]
            for w, files in words.items()
            if not normalize_pressure_stage(w)
        }
        assert not unnormalizable, (
            "真实语料存在**无法归一**的阶段词（新变体漏网，纪律 #22）：\n"
            + "\n".join(f"  {w!r} ← {files}" for w, files in unnormalizable.items())
        )

    def test_aliases_have_real_provenance(self) -> None:
        """别名必须有真实语料出处（防"猜的兼容"，纪律 #4）。"""
        words = self._collect_stage_words()
        if not words:
            pytest.skip("无语料")
        for alias in PRESSURE_STAGE_ALIASES:
            assert alias in words, (
                f"别名 {alias!r} 在真实语料中找不到出处 ⇒ 属凭空兼容，"
                f"应删除或补出处"
            )


# ── R7 压力曲线表「区间/里程碑」双写法解析（★ M6-B 实测 40/280 行曾被丢弃） ──
class TestBandResolution:
    """表格的「章节」列有两种合法写法，都必须被正确解析为覆盖区间。

    · 区间式 ``| 冲突 | 51-57 |``
    · 里程碑式 ``| 高潮 | 50 |``（该章是阶段起点）
    """

    @staticmethod
    def _cls():
        import agent.workflows.writing.m5_context as mod

        for name in dir(mod):
            obj = getattr(mod, name)
            if isinstance(obj, type) and hasattr(obj, "_resolve_bands"):
                return obj
        raise AssertionError("未找到实现 _resolve_bands 的类")

    def _resolve(self, bands):
        mx = max(b[2] for b in bands)
        return self._cls()._resolve_bands(bands, mx)

    def test_pure_range_bands_unchanged(self) -> None:
        """纯区间式必须**逐字不变**（不得因新逻辑改动既有正确行为）。"""
        bands = [("铺垫", 1, 10, "低"), ("冲突", 11, 20, "中")]
        assert self._resolve(bands) == bands

    def test_milestone_expands_to_next_anchor(self) -> None:
        """里程碑式必须向后扩展，不再被丢弃。"""
        bands = [("铺垫", 1, 1, "低"), ("冲突", 30, 30, "中")]
        got = self._resolve(bands)
        assert ("铺垫", 1, 29, "低") in got, f"里程碑未扩展：{got}"
        assert ("冲突", 30, 30, "中") in got

    def test_milestone_last_wins_on_same_chapter(self) -> None:
        """同一章被多条声明 ⇒ 靠后者胜（表格顺序 = 剧情推进顺序）。"""
        bands = [
            ("铺垫", 48, 49, "低"),
            ("冲突", 50, 50, "中"),
            ("高潮", 50, 50, "高"),
            ("结局/收束", 50, 50, "低"),
        ]
        got = self._resolve(bands)
        at50 = [b for b in got if b[1] <= 50 <= b[2]]
        assert len(at50) == 1, f"第 50 章应只命中一条：{at50}"
        assert at50[0][0] == "结局/收束", f"应取最后声明者，实际 {at50[0]}"

    def test_output_has_no_overlap_and_is_sorted(self) -> None:
        """归一后必须**互不重叠且按起点升序**（消费者按序取首个命中）。"""
        bands = [
            ("铺垫", 50, 50, "低"), ("冲突", 120, 120, "中"),
            ("高潮", 100, 100, "高"), ("舒缓", 30, 30, "低"),
        ]
        got = self._resolve(bands)
        for i in range(len(got) - 1):
            assert got[i][2] < got[i + 1][1], f"区间重叠：{got[i]} 与 {got[i+1]}"
        # 覆盖 min..max 全程（不出现"无人认领"的空洞）
        covered = sum(b[2] - b[1] + 1 for b in got)
        span = max(b[2] for b in got) - min(b[1] for b in got) + 1
        assert covered == span, f"存在未覆盖空洞：覆盖 {covered} vs 跨度 {span}"


# ── R6 消费者不得手写阶段字面量（★ 结构性，AST 级） ──────────────────────
class TestConsumersUseSSOT:
    """下游按阶段词做分支的地方，比较对象必须来自 SSOT。"""

    CTX = REPO_ROOT / "src/agent/workflows/writing/m5_context.py"
    WRITE = REPO_ROOT / "src/agent/workflows/writing/agentic_write.py"

    def _string_literals_compared(self, path: Path) -> set[str]:
        """收集 `<expr> == "<阶段词>"` 形态里的字面量（AST，不算注释/日志）。"""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for op, comp in zip(node.ops, node.comparators):
                    if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) \
                            and isinstance(comp.value, str):
                        if comp.value in PRESSURE_STAGES:
                            hits.add(comp.value)
        return hits

    def test_position_based_stage_has_no_hardcoded_stage_names(self) -> None:
        """生产端（m5_context）不得手写阶段字面量做返回值。"""
        tree = ast.parse(self.CTX.read_text(encoding="utf-8"))
        target = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef,)) and node.name == "_position_based_stage":
                target = node
                break
        assert target is not None, "未找到 _position_based_stage"
        # 该函数体内，作为**返回值**出现的阶段字面量 = 0
        offenders: list[str] = []
        for node in ast.walk(target):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and elt.value in PRESSURE_STAGES:
                        offenders.append(elt.value)
        assert not offenders, (
            f"_position_based_stage 手写了阶段字面量 {offenders}；"
            f"必须从 PRESSURE_STAGES 取（纪律 #22）"
        )

    def test_consumer_comparisons_are_documented(self) -> None:
        """消费者按阶段词字面量比较的地方必须被登记（防『新增一处无人知』）。

        这不是禁止比较，而是**要求显式**：命中即须在 _KNOWN_STAGE_COMPARISONS
        登记（含说明），新增未登记的比较点即 FAIL。
        """
        known = {
            # (文件名, 阶段词) → 用途说明
            ("agentic_write.py", "高潮"): "加载爽点技法 + is_climax 送评委",
        }
        unregistered: list[str] = []
        for path in (self.CTX, self.WRITE):
            for word in self._string_literals_compared(path):
                key = (path.name, word)
                if key not in known:
                    unregistered.append(f"{path.name}: == {word!r}")
        assert not unregistered, (
            "发现未登记的阶段词字面量比较（消费者清单未同步）：\n"
            + "\n".join(f"  {u}" for u in unregistered)
        )
