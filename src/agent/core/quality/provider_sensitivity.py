"""provider 敏感判据**登记表**与抽检探针（2026-09-20）

背景：为什么要做这件事
----------------------
用户**频繁切换 LLM provider**（Qwen ↔ opencode.ai ↔ …），而本仓的判据阈值
（分位表、绝对分值线、token 预算基线）**都是在单一 provider 的真实数据上标定的**
（如 M6 分位表在 ``ddac3d9``、金三 60/40 来自早期 LLM 评分）。

⇒ 换 provider 后，判据可能**系统性假失败**（好章被回退，白烧 token）
或**假通过**（坏章放行）。而此前**没有任何机制**能回答"这些判据换 provider 还成立吗"。
本模块把这个风险面**登记成可核对象**，并提供一个可复跑的抽检探针。

分类维度（决定敏感与否的**结构性**理由，不是印象）
--------------------------------------------------
1. ``rule``：纯规则确定性计算（字数/实体/说话人/阶段连续）⇒ **provider 无关**。
2. ``rule_relative``：纯规则，但阈值**已相对化到对象自身分布**（如 M6 的
   "全书 top-decile" / "窗口极差 p10"）⇒ 结构性缓解 provider 位移。
3. ``llm_score``：判据是 **LLM 打出的绝对分值** 与固定阈值比较 ⇒ **provider 敏感**。
4. ``llm_semantic``：判据是 LLM 语义判断（通过/不通过），无固定阈值 ⇒
   provider 敏感但**无阈值可位移**；若其动作是 advisory 则风险进一步降低。

★ 抽检结论（诚实声明，勿过度解读）
----------------------------------
探针实测：现有 trace 的 ``meta.provider`` **全部为 ``openai`` 单一取值**
（9 项目 / 8513 条 span，去重后）⇒ **无法做跨 provider 比较**，
结论强度＝**未验证**（纪律 #23②：结论强度须与取证等级匹配）。

且**分值类判据的 provider 位移当前结构性不可测**：``quality_audit.jsonl`` /
``AuditRecord`` 不含 provider 字段 ⇒ 无法把"某轮体检的连贯性 85 分"归因到具体 provider。
这是本模块暴露出的**真实缺口**，修法（给审计记录加 provider 落点）需另立登记单。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

# ---------------------------------------------------------------- 来源类别
#: 纯规则确定性计算 ⇒ provider 无关
SOURCE_RULE = "rule"
#: 纯规则 + 阈值已相对化到对象自身分布 ⇒ 结构性缓解
SOURCE_RULE_RELATIVE = "rule_relative"
#: LLM 打出的**绝对分值** vs 固定阈值 ⇒ provider 敏感（可位移）
SOURCE_LLM_SCORE = "llm_score"
#: LLM 语义判断，无固定阈值 ⇒ 敏感但无阈值可位移
SOURCE_LLM_SEMANTIC = "llm_semantic"


@dataclass(frozen=True)
class JudgeSpec:
    """一条判据的 provider 敏感度登记。

    Args:
        key: 稳定标识（红线用它做僵尸检查）。
        label: 人读名称。
        source: 见上方 ``SOURCE_*``。
        location: ``相对 src/agent 的路径::符号``（**必须真实存在**，红线校验）。
        provider_sensitive: 换 provider 后是否可能系统性错判。
        relative: 阈值是否已相对化到对象自身分布。
        evidence: 判定依据（实测分位 / 登记单 / 代码位置）。
    """

    key: str
    label: str
    source: str
    location: str
    provider_sensitive: bool
    relative: bool
    evidence: str


#: ★ provider 敏感度登记表（新增判据须登记；僵尸条目由红线拦截）
JUDGES: tuple[JudgeSpec, ...] = (
    # ---- LLM 分值 + 绝对阈值：**风险面** ----
    JudgeSpec(
        "golden_three_total", "黄金三章综合合格线", SOURCE_LLM_SCORE,
        "core/quality/golden_policy.py::SIX_DIM_PASS_LINE",
        provider_sensitive=True, relative=False,
        evidence="六维分由 ReaderAppealScorer（LLM）产出；阈值 60 为绝对分。"
                 "2026-09-20 已收口为唯一真源（此前 11 处副本）⇒ 现在只有一处可改。",
    ),
    JudgeSpec(
        "golden_three_floor", "黄金三章单维触底线", SOURCE_LLM_SCORE,
        "core/quality/golden_policy.py::SIX_DIM_FLOOR",
        provider_sensitive=True, relative=False,
        evidence="同上；40 为绝对分，且**写时门禁**（m5_quality_gate）会据此拦下前三章落盘。",
    ),
    JudgeSpec(
        "eval_coherence_min", "连贯性自评下限", SOURCE_LLM_SCORE,
        "core/quality/eval_targets.py::COHERENCE_MIN",
        provider_sensitive=True, relative=False,
        evidence="85/100 由 LLM 自评；G2 曾从 80 收紧到 85，说明该线对分布敏感。"
                 "2026-09-20 收口为唯一真源（此前 evaluator.qt 与 planner.QualityTargets 各写一份）。",
    ),
    JudgeSpec(
        "eval_readability_min", "追读力下限", SOURCE_LLM_SCORE,
        "core/quality/eval_targets.py::READABILITY_MIN",
        provider_sensitive=True, relative=False,
        evidence="80/100 由 LLM 自评；G2 从 75 收紧到 80。同上已收口单一真源。",
    ),
    JudgeSpec(
        "eval_hard_dims_zero", "硬维恒 0（人设/设定/逻辑）", SOURCE_LLM_SCORE,
        "core/quality/eval_targets.py::HARD_DIM_MAX",
        provider_sensitive=True, relative=False,
        evidence="阈值恒 0（计数型），不随分布平移；但**检出率**仍受 provider 影响"
                 "（检不出 ≠ 没问题）⇒ 仍列入敏感面，动作是回退整窗，强度高。",
    ),
    # ---- 纯规则：provider 无关 ----
    JudgeSpec(
        "chapter_length_floor", "章节字数硬下限", SOURCE_RULE,
        "core/quality/length_policy.py::ABSOLUTE_MIN_CJK_WORDS",
        provider_sensitive=False, relative=False,
        evidence="确定性计数。2026-09-20 收口：写时门禁与 book_checkup 同源"
                 "（此前两处各写 1500）。实测 89 条「超短章」为门禁上线前的历史章"
                 "（三本书 mtime ≤ 09-02，违规率随写作时间单调下降 29%→18%→1%）。",
    ),
    JudgeSpec(
        "book_checkup_stage_streak", "阶段连续上限", SOURCE_RULE,
        "core/quality/book_checkup.py::DEFAULT_STAGE_STREAK_LIMIT",
        provider_sensitive=False, relative=False,
        evidence="确定性：读 pressure_stage 字段序列。",
    ),
    JudgeSpec(
        "book_checkup_char_streak", "配角连续出场上限", SOURCE_RULE,
        "core/quality/book_checkup.py::DEFAULT_CHAR_STREAK_LIMIT",
        provider_sensitive=False, relative=False,
        evidence="确定性。阈值 10 有实测依据：9 项目 865 条 streak 分位 p95=9。"
                 "2026-09-20 作用域收窄到配角（此前 antagonist/mentor 被误报）。",
    ),
    JudgeSpec(
        "book_checkup_hook_similarity", "章末钩子近重复阈值", SOURCE_RULE,
        "core/quality/book_checkup.py::DEFAULT_HOOK_SIMILARITY",
        provider_sensitive=False, relative=False,
        evidence="确定性相似度（SequenceMatcher）。",
    ),
    JudgeSpec(
        "book_checkup_speaker_rules", "说话人四条结构判据", SOURCE_RULE,
        "core/quality/book_checkup.py::_speaker_name_ok",
        provider_sensitive=False, relative=False,
        evidence="确定性：左边界/叠字/代词/姓氏首字。2026-09-20 修复后 229→55（削减 76%）。",
    ),
    JudgeSpec(
        "eval_foreshadow_recycle", "伏笔回收率下限", SOURCE_RULE,
        "core/quality/eval_targets.py::FORESHADOW_RECYCLE_MIN",
        provider_sensitive=False, relative=False,
        evidence="确定性：从 foreshadows.md 直接算出（模块 docstring 自述）。",
    ),
    JudgeSpec(
        "eval_pacing_abnormal", "节奏异常率上限", SOURCE_RULE,
        "core/quality/eval_targets.py::PACING_ABNORMAL_MAX",
        provider_sensitive=False, relative=False,
        evidence="确定性：pacing_abnormal = 异常章节（注水/赶进度）占比，"
                 "由章节字数与结构直接算出（evaluator 模块自述「完全确定性，无需 LLM」）。",
    ),
    # ---- 已相对化：结构性缓解 ----
    JudgeSpec(
        "tension_no_climax", "张力「无高潮」判据", SOURCE_RULE_RELATIVE,
        "core/story/tension_curve.py::RELATIVE_MIN_CORPUS",
        provider_sensitive=False, relative=True,
        evidence="已相对化到**全书 top-decile 因果判据**（M6-B4 候选 E，ddac3d9）；"
                 "且设 RELATIVE_MIN_CORPUS=20 的语料门槛。相对判据天然抗分布整体位移。",
    ),
    # ---- LLM 语义、无阈值 ----
    JudgeSpec(
        "plan_critic_semantic", "规划评委语义维度", SOURCE_LLM_SEMANTIC,
        "core/story/plan_critic.py::review_plan",
        provider_sensitive=True, relative=False,
        evidence="LLM 语义评审（弧线是否推进母题）。**恒 Returns: []、不阻断**"
                 "（模块自述），故敏感性风险局限于结论措辞；升 blocking 前须先跑阈值分位表。",
    ),
)


# ---------------------------------------------------------------- 查询
def llm_absolute_judges() -> list[JudgeSpec]:
    """**风险面**：LLM 分值 + 未相对化 ⇒ 换 provider 可能系统性错判。"""
    return [j for j in JUDGES if j.source == SOURCE_LLM_SCORE and not j.relative]


def relative_judges() -> list[JudgeSpec]:
    """已相对化的判据（结构性缓解 provider 位移）。"""
    return [j for j in JUDGES if j.relative]


def by_source() -> dict[str, list[JudgeSpec]]:
    out: dict[str, list[JudgeSpec]] = {}
    for j in JUDGES:
        out.setdefault(j.source, []).append(j)
    return out


# ---------------------------------------------------------------- 抽检探针
def _pct(values: Sequence[float], q: float) -> float | None:
    """朴素分位（近邻法）。空序列 ⇒ ``None``（**不编造**）。"""
    if not values:
        return None
    xs = sorted(values)
    idx = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return float(xs[idx])


def probe_provider_shift(spans: Iterable[Any]) -> dict[str, Any]:
    """按 provider 分组给出 **token 读数**分位，并显式判定**可比较性**。

    为什么比 token 而不是比分值：分值类判据的 provider 位移**当前结构性不可测**
    —— ``quality_audit.jsonl`` / ``AuditRecord`` 不含 provider 字段，无法把
    "某轮体检的连贯性得分"归因到具体 provider。而 token 是判别「熔断/降档」
    这类**读数驱动动作**（纪律 #16）是否被 provider 位移影响的可测代理。

    ★ 硬约束：provider 数 < 2 ⇒ ``comparable=False``，且
    ``token_p50_ratio``/``verdict`` 一律为 ``None`` —— **禁止在单 provider 数据上
    给出"判据稳健"的结论**（那正是"假结论"，比没有结论更坏）。
    """
    order: list[Any] = list(spans)

    def f(s: Any, name: str, default: Any = 0) -> Any:
        if isinstance(s, dict):
            return s.get(name, default)
        return getattr(s, name, default)

    buckets: dict[str, dict[str, list[float]]] = {}
    for s in order:
        meta = f(s, "meta", {}) or {}
        prov = str((meta.get("provider") if isinstance(meta, dict) else "") or "<unknown>")
        d = buckets.setdefault(prov, {"tokens_in": [], "tokens_out": []})
        d["tokens_in"].append(float(f(s, "tokens_in", 0)))
        d["tokens_out"].append(float(f(s, "tokens_out", 0)))

    by_provider: dict[str, dict[str, Any]] = {}
    for prov, d in buckets.items():
        pin = _pct(d["tokens_in"], 0.50)
        pout = _pct(d["tokens_out"], 0.50)
        by_provider[prov] = {
            "calls": len(d["tokens_in"]),
            "tokens_in_p50": pin,
            "tokens_in_p90": _pct(d["tokens_in"], 0.90),
            "tokens_out_p50": pout,
            "tokens_out_p90": _pct(d["tokens_out"], 0.90),
            # ★ 有效读数判定：**中位调用必须真的有 token 使用**。
            # 真实 LLM 调用不可能一半以上不返回 usage ⇒ p50=0 只可能来自
            # stub / 未接线 / 未返回 usage 的假 provider。
            # 实测教训（2026-09-20 两次迭代）：trace 里有 provider="fake" 的 32 条
            # 调用，p50=0 但 p90=10（偶有非零），若用"全为 0"判定会漏过它，
            # 把单 provider 数据读成"2 个 provider 可比较" ⇒ **假结论**
            # （比"未验证"更坏）。故判据取 p50（中位）而非 max。
            "effective": bool(pin and pin > 0) or bool(pout and pout > 0),
        }

    named = [p for p, v in by_provider.items() if p != "<unknown>"]
    effective = [p for p in named if by_provider[p]["effective"]]
    no_reading = [p for p in named if not by_provider[p]["effective"]]
    comparable = len(effective) >= 2
    result: dict[str, Any] = {
        "providers": sorted(effective),
        "providers_without_reading": sorted(no_reading),
        "unknown_bucket_calls": by_provider.get("<unknown>", {}).get("calls", 0),
        "by_provider": by_provider,
        "comparable": comparable,
        "token_p50_ratio": None,
        "verdict": None,
        "score_judges_comparable": False,
        "score_judges_blocker": (
            "quality_audit.jsonl / AuditRecord 不含 provider 字段 ⇒ "
            "分值类判据（金三 60/40、连贯性 85、追读力 80）的 provider 位移"
            "**结构性不可测**；要解锁须先给审计记录补 provider 落点。"
        ),
    }
    if comparable and len(effective) == 2:
        a, b = sorted(effective)
        ai = by_provider[a]["tokens_in_p50"] or 0.0
        bi = by_provider[b]["tokens_in_p50"] or 0.0
        ratio = (bi / ai) if ai else None
        result["token_p50_ratio"] = {f"{b}/{a}": round(ratio, 3) if ratio else None}
    extra = (
        f"（另有 {len(no_reading)} 个 provider 标签无有效读数：{', '.join(sorted(no_reading))}）"
        if no_reading else ""
    )
    result["verdict"] = (
        f"可比较（{len(effective)} 个有有效读数的 provider：{', '.join(sorted(effective))}）"
        f"{extra}；token 位移见 token_p50_ratio —— 仅覆盖读数驱动类判据"
        if comparable
        else (
            f"**无法比较**：仅有 {len(effective)} 个 provider 有有效读数"
            f"（{', '.join(sorted(effective)) or '无'}）{extra}"
            " ⇒ 跨 provider 判据稳健性**未验证**（结论强度=未验证，不得读作'稳健'）"
        )
    )
    return result


def registry_path_valid(location: str, src_root: Path) -> tuple[bool, str]:
    """校验 ``location`` 的 ``路径::符号`` 是否真实存在（僵尸检查用）。"""
    if "::" not in location:
        return False, "格式应为 相对路径::符号"
    rel, sym = location.split("::", 1)
    path = src_root / rel
    if not path.is_file():
        return False, f"文件不存在：{rel}"
    if sym not in path.read_text(encoding="utf-8"):
        return False, f"符号未在文件中出现：{sym}"
    return True, ""
