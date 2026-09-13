"""B1 真 LLM 追读力 / 读者吸引力评分器（迷爱看核心）

把 Evaluator 现有 5 个「pass 默认」维度升级为**真实 LLM 评分**，并新增作者侧可直接使用的
「迷爱看」6 维评分（钩子强度/爽点密度/代入感/人物弧光/世界观新颖度/情绪曲线）。

两条使用路径：
1. ``score(dimension, project_dir) -> float``：签名兼容 ``EvaluatorAgent.score_fn``，
   让全书「不崩」终审的 人设稳定/设定一致/连贯/追读/逻辑 维度由真 LLM 判定
   （替代离线时的满分安全默认）。供 ``evaluate --real-score`` 启用。
2. ``score_chapter(chapter_text, ...) -> ReaderAppealReport``：作者侧独立评分，
   直接回答「读者会不会爱看」，给出 6 维分数 + 一句话感受 + 改进建议。供 ``/appeal`` 命令。

降级不阻断（项目哲学）：
- LLM 不可达 / 调用异常 / 返回无法解析 → ``score`` 回退 Evaluator 安全默认；
  ``score_chapter`` 返回 ``llm_used=False`` 的占位报告，绝不抛异常中断用户流程。
"""

from __future__ import annotations

import json
import logging
import re
import time as _time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from llmagent.gateway import Gateway
from rich.console import Console

from agent.core.story.chapters import (  # G6：公共章节读取 helper（消除根因 B6-3 重复实现）
    iter_chapter_texts,
    list_chapter_files,
    read_chapters_text,
    strip_frontmatter,
    take_chapter_files,
)
from agent.utils import parse_llm_json
from agent.client.gateway_adapter import (
    _is_transient_provider_error,
    chat_utility,
    chat_utility_response,
    create_gateway,
)
from agent.core.infra.prompt_manager import pm
from agent.core.infra.degrade import degrade
from agent.core.quality.dimension_registry import clamp_value, safe_default_for
from agent.core.quality.eval_evidence import EvalEvidence, build_evidence, build_degraded_evidence


# ============================================================
# 提示词
# ============================================================
# Evaluator 维度评分（单维，要求 LLM 给一个数值）
# HA-Eval L2：改由 dimension_registry 登记表派生（SSOT），新增维度只需登记一次。
from agent.core.quality.dimension_registry import _EVAL_DIM_LABELS  # noqa: F401

# 评分调用外层再加长退避（秒）：网关收口点退避只撑 ~15s 故障窗，网关故障
# 实测可达数分钟；此处 20s/40s 两档长退避，仍失败才降级为默认。测试可置
# () 关闭。仅对瞬时故障（_is_transient_provider_error）生效，解析失败不重试。
_EVAL_RETRY_DELAYS_S: tuple[float, ...] = (20.0, 40.0)

logger = logging.getLogger(__name__)


def _chat_with_eval_backoff(
    llm: Any, messages: list, *, dimension: str, console: Any,
    temperature: float = 0.2, max_tokens: int = 8192,
) -> Any:
    """带长退避的评分调用：瞬时故障按 _EVAL_RETRY_DELAYS_S 退避重试后上抛。

    2026-09-12 类级修复：path 1（Evaluator 逐维）与 path 2（迷爱看/黄金三章
    整章六维）共用本助手——此前只有 path 1 有长退避，网关故障时 path 2 单发
    失败即降级，是同一类问题只修了一半。
    """
    delays = tuple(_EVAL_RETRY_DELAYS_S)
    for i in range(len(delays) + 1):
        try:
            return chat_utility_response(
                llm,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                enable_thinking=False,
            )
        except Exception as e:  # noqa: BLE001 - 瞬时故障退避，其余立即上抛，不吞错
            if i >= len(delays) or not _is_transient_provider_error(e):
                raise
            wait = delays[i]
            logger.warning(
                "维度 %s 评分调用瞬时故障（%s），%.0fs 后重试（%d/%d）",
                dimension, e, wait, i + 1, len(delays),
            )
            if console is not None:
                console.print(
                    f"[yellow]⚠ 维度 {dimension} 评分调用瞬时故障（{e}），"
                    f"{wait:.0f}s 后重试（{i + 1}/{len(delays)}）[/yellow]"
                )
            _time.sleep(wait)
            continue


# 迷爱看 6 维（作者侧独立评分）
APPEAL_DIMENSIONS = {
    "hook_strength": "章末钩子强度（让读者想翻下一章的抓力）",
    "payoff_density": "爽点密度（反转/打脸/成长/揭密的爽感浓度）",
    "immersion": "代入感（视角稳定、细节可信、情绪可被带入）",
    "character_arc": "人物弧光（角色有成长/转变，不是工具人）",
    "world_novelty": "世界观新颖度（设定有新意、有记忆点）",
    "emotion_curve": "情绪曲线（节奏起伏有呼吸感，不Flat不注水）",
}

APPEAL_WEIGHTS = {
    "hook_strength": 0.20,
    "payoff_density": 0.20,
    "immersion": 0.20,
    "character_arc": 0.15,
    "world_novelty": 0.10,
    "emotion_curve": 0.15,
}

# G5 门禁合格线（主理人拍板 #3：综合线 + 单维触底兜底）
APPEAL_PASS_LINE: int = 60        # 综合分合格线（可被 --appeal-threshold 覆盖）
APPEAL_DIM_FLOOR: int = 40        # 单维触底兜底线
APPEAL_GATE_PREFIX: str = "appeal_"   # 六维 DimensionResult 名前缀
APPEAL_LABELS: dict[str, str] = {     # 短中文标签（展示 + is_pass 失败维命名）
    "hook_strength": "钩子强度",
    "payoff_density": "爽点密度",
    "immersion": "代入感",
    "character_arc": "人物弧光",
    "world_novelty": "世界观新颖度",
    "emotion_curve": "情绪曲线",
}

# ---- G6：黄金三章门禁常量（主理人拍板 #3：复用 G5 阈值 60/40，可被 CLI 覆盖）----
GOLDEN_PASS_LINE: int = 60          # 三章拼接综合分合格线（--golden-three-threshold 覆盖）
GOLDEN_BORDERLINE_BAND: int = 5     # 贴线复核带宽：首评落在 threshold±5 触发二次采样（优化登记 20260913）
GOLDEN_DIM_FLOOR: int = 40          # 单维触底线（--golden-three-floor 覆盖）
GOLDEN_GATE_PREFIX: str = "golden_" # golden_* DimensionResult 名前缀
GOLDEN_JOIN_CHAR_LIMIT: int = 10000 # 与 score_chapter 截断（行 315）对齐；超长 fallback 每章独立评分

# ---- HA-Eval L2（2026-09-08）：以下两块语义已上收至 dimension_registry（SSOT）----
# 计数类维度集合（以 issues 重算 value）；评分类维度用自报 value。
from agent.core.quality.dimension_registry import COUNT_DIMS  # noqa: F401
# 计入硬门禁的 severity 集合（high 必计、mid 计入以收紧；low 仅上报，不计入门禁）。
SEVERITY_GATE = {"high", "mid"}

# 需要对照「设定真源」判定的维度（2026-09-12）。这些维度评的是"正文是否违反
# 既定设定/角色状态"，评委必须先拿到真源，否则只能凭简介猜 → 恒挑出伪不一致。
_CANON_DIMS = {
    "character_stability_high",
    "setting_consistency_high",
    "logic_holes",
}
# 注入真源后正文窗口不变，放宽这几维的 prompt 截断（8000 → 12000 字符），
# 避免 3 章正文被挤出窗口。成本仅批末体检每 5 章一次，远低于一次整窗回退。
_CANON_PROMPT_CHARS = 12000


def _extract_md_section(content: str, title: str) -> str:
    """取 `## <title>` 小节正文（到下一个 `##` 为止）；不存在返回空串。"""
    m = re.search(rf"^##\s*{re.escape(title)}\s*\n", content, re.M)
    if not m:
        return ""
    rest = content[m.end():]
    nxt = re.search(r"^##\s", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def _gather_canon(project_dir: Path) -> str:
    """汇总设定真源：world.md 冻结设定（境界体系/金手指）+ 角色状态/时间线。

    与写作端 m5_context 的真源口径一致（状态段落 → 关键词兜底 → 基础信息），
    让审端与写端对着同一份事实说话。文件缺失/读取失败静默跳过（真源是
    增强信息，不阻断评分）。
    """
    parts: list[str] = []
    world = project_dir / "world.md"
    if world.exists():
        try:
            content = world.read_text(encoding="utf-8")
        except OSError:
            content = ""  # noqa: SILENT_DEGRADE - 增强信息，缺失静默跳过（见 docstring）
        for title, tag in (("修炼境界体系", "境界体系（冻结）"), ("金手指登记", "金手指登记")):
            section = _extract_md_section(content, title).strip()
            if section:
                parts.append(f"【设定真源·{tag}】{section[:1200]}")
    chars_dir = project_dir / "characters"
    if chars_dir.exists():
        bits: list[str] = []
        for p in sorted(chars_dir.glob("*.md"))[:8]:
            try:
                card = p.read_text(encoding="utf-8")
            except OSError:
                continue  # noqa: SILENT_DEGRADE
            status = ""
            for t in ("状态", "当前状态", "生死", "存活状态"):
                status = _extract_md_section(card, t).strip()
                if status:
                    break
            if not status:
                if re.search(r"已故|去世|死亡|牺牲|阵亡|陨落|辞世", card):
                    status = "（档案正文提及已故/牺牲，按已故处理）"
                elif re.search(r"在世|存活|健在", card):
                    status = "（档案正文提及在世/存活）"
            timeline = (
                _extract_md_section(card, "时间线").strip()
                or _extract_md_section(card, "关键时间线").strip()
                or _extract_md_section(card, "生平").strip()
            )
            basis = _extract_md_section(card, "基础").strip()[:150]
            info = "；".join(
                b for b in (f"状态：{status[:120]}" if status else "",
                            f"时间线：{timeline[:160]}" if timeline else "",
                            f"基础：{basis}" if basis else "") if b
            )
            if info:
                bits.append(f"- {p.stem}：{info}")
        if bits:
            parts.append(
                "【角色真源（判定角色状态/言行矛盾时以此为准）】\n" + "\n".join(bits)
            )
    if not parts:
        return ""
    return (
        "【设定真源】以下为既定设定与角色状态，正文与之**冲突**才计 issue；"
        "评委不知道但设定允许的内容不算不一致。\n" + "\n".join(parts)
    )


def _count_gated_issues(issues: list) -> float:
    """计数门禁：仅统计 severity ∈ SEVERITY_GATE 且**带原文定位（quote）**的 issue。

    2026-09-10（回滚率削减·P0）：LLM 评委在 5 章窗口上挑刺恒能挑出非零，
    且部分 issue 属幻觉（无法在原文定位）。约定 issue 必须附 quote（≤30 字
    原文引用）作为定位凭据：无 quote 的条目视为臆测、不计入门禁。
    若全部条目均无 quote（旧格式响应/过渡期），回退原口径全量计数，
    避免格式过渡期把真问题整体清零。
    """
    locatable = [
        it for it in issues
        if isinstance(it, dict) and str(it.get("quote", "") or "").strip()
    ]
    counted = locatable if locatable else issues
    return float(sum(
        1 for it in counted
        if isinstance(it, dict)
        and str(it.get("severity", "")).lower() in SEVERITY_GATE
    ))

# ============================================================
# 报告
# ============================================================
@dataclass
class ReaderAppealReport:
    """迷爱看 6 维评分报告。"""

    dimensions: dict[str, int]
    total_score: int
    one_liner: str
    suggestions: list[str]
    llm_used: bool = True
    error: str = ""
    # G5：评分来源标记（"llm" 真评测 / "offline" 离线降级占位）
    source: str = "llm"
    # G6：本次评分实际评的章节数（拼接=1，fallback=3）
    chapters_scored: int = 1
    # G6：True=超长回退为每章独立评分取最差
    fallback: bool = False
    # 维度中文标签（展示用）
    labels: dict[str, str] = field(default_factory=lambda: {
        "hook_strength": "钩子强度",
        "payoff_density": "爽点密度",
        "immersion": "代入感",
        "character_arc": "人物弧光",
        "world_novelty": "世界观新颖度",
        "emotion_curve": "情绪曲线",
    })
    # ---- G7：人话总结行（主理人拍板 2：表格前插入总结段；离线分支原样已有人话）----
    summary_lines: list[str] = field(default_factory=list)

    @staticmethod
    def _compute_total(dims: dict[str, int]) -> int:
        total = 0.0
        for k, w in APPEAL_WEIGHTS.items():
            total += dims.get(k, 0) * w
        return int(round(total))

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimensions": dict(self.dimensions),
            "total_score": self.total_score,
            "one_liner": self.one_liner,
            "suggestions": list(self.suggestions),
            "llm_used": self.llm_used,
            "error": self.error,
            "source": self.source,
            # ---- G7（只增不删）：人话总结行 ----
            "summary_lines": list(self.summary_lines),
        }

    def to_markdown(self) -> str:
        if not self.llm_used:
            return (
                "# 迷爱看评分（不可用）\n\n"
                f"> LLM 不可用，无法评分：{self.error or '未知错误'}\n\n"
                "配置真实 LLM（把 .env_ai 复制为 .env）后即可获得真实读者吸引力评分。"
            )
        lines = ["# 迷爱看评分报告", ""]
        # ---- G7：人话总结段（表格前插入；summary_lines 为空则跳过，离线分支原样）----
        if self.summary_lines:
            lines.append("## 一句话总结")
            for ln in self.summary_lines:
                lines.append(f"- {ln}")
            lines.append("")
        verdict = _verdict(self.total_score)
        lines.append(f"**总评分**：{self.total_score}/100 （{verdict}）")
        lines.append(f"> {self.one_liner}")
        lines.append("")
        lines.append("| 维度 | 得分 |")
        lines.append("|---|---|")
        for k in APPEAL_DIMENSIONS:
            lines.append(f"| {self.labels.get(k, k)} | {self.dimensions.get(k, 0)}/100 |")
        if self.suggestions:
            lines.append("")
            lines.append("## 改进建议")
            for i, s in enumerate(self.suggestions, 1):
                lines.append(f"{i}. {s}")
        return "\n".join(lines)


def _verdict(score: int) -> str:
    if score >= 85:
        return "会追更、会安利"
    if score >= 75:
        return "会追更"
    if score >= 60:
        return "可看可不看"
    if score >= 45:
        return "勉强能看"
    return "容易弃书"


# ============================================================
# G7 人话总结行（主理人拍板 1/2：确定性拼装、零 LLM；表格前插总结段）
# ============================================================
def build_appeal_summary_lines(report: "ReaderAppealReport") -> list[str]:
    """确定性拼装迷爱看人话总结行（零 LLM，素材全取自 ReaderAppealReport）。

    失败维判定与 is_pass（行 381-397）同源：单维 < APPEAL_DIM_FLOOR(40) 或
    综合 total_score < APPEAL_PASS_LINE(60)。建议来源：LLM suggestions 优先 + 维度模板兜底。
    离线（llm_used=False）返回 []（to_markdown 离线分支已有人话，不重复）。
    """
    if not report.llm_used:
        return []
    lines: list[str] = []
    total = report.total_score
    if total < APPEAL_PASS_LINE:
        lines.append(
            f"综合分 {total}/100 未达合格线 {APPEAL_PASS_LINE}"
            f"（{_verdict(total)}）——读者吸引力整体偏弱，建议按下方建议提升。"
        )
    for k, v in report.dimensions.items():
        if v < APPEAL_DIM_FLOOR:
            label = APPEAL_LABELS.get(k, k)
            gap = APPEAL_DIM_FLOOR - v
            lines.append(
                f"{label}：实测 {v} ＜ 触底线 {APPEAL_DIM_FLOOR}（差 {gap}）"
            )
    # 下一步建议：LLM suggestions 优先（注明来源），无则维度级模板兜底
    if report.suggestions:
        lines.append("下一步建议（来自 LLM）：")
        for i, s in enumerate(report.suggestions[:5], 1):
            lines.append(f"  {i}. {s}")
    else:
        lines.append("下一步建议（模板）：请针对上述未达触底线的维度逐项优化。")
    return lines


# ============================================================
# 评分器
# ============================================================
class ReaderAppealScorer:
    """真 LLM 评分器（迷爱看），离线优雅降级。

    Args:
        llm_client: LLM 客户端；None 则内部惰性构造。
        console: rich 控制台。
    """

    def __init__(
        self,
        llm_client: Gateway | None = None,
        console: Console | None = None,
    ) -> None:
        self._llm = llm_client
        self.console = console or Console()
        # G2：保存各维度最近一次结构化评分结果（含 issues/rationale），供报告展示。
        self._last_eval: dict[str, dict] = {}

    @property
    def llm(self) -> Gateway:
        if self._llm is None:
            self._llm = create_gateway()
        return self._llm

    # ---------------------------------------------------------- 路径 1：Evaluator 维度
    def score(self, dimension: str, project_dir: str | Path) -> float:
        """兼容 ``EvaluatorAgent.score_fn``：对单维做真 LLM 评分。

        G2：计数类维度以 LLM 列举的 ``issues`` 为准重算 value（忽略自报，
        封堵"报 0 实则列举 N 条"的漏判）；评分维无 issues 时回退自报 value。
        结果（value/rationale/issues）存入 ``self._last_eval[dimension]``。
        LLM 不可用时回退 Evaluator 安全默认（硬计数维 0、评分维满分）。
        """
        try:
            text = self._gather_for_eval(dimension, str(project_dir))
            if not text:
                # 无评估素材 = 没有真实评分 → 记不可信证据，禁止据此处置
                self._record_degraded(dimension, "评估素材为空，降级为安全默认")
                return self._default_for(dimension)
            prompt = (
                f"请评估以下小说片段在「{_EVAL_DIM_LABELS.get(dimension, dimension)}」"
                f"维度上的表现。\n\n"
                f"{text[:_CANON_PROMPT_CHARS if dimension in _CANON_DIMS else 8000]}"
            )
            _messages = [
                {"role": "system", "content": pm.get("quality.reader_appeal_eval").system},
                {"role": "user", "content": prompt},
            ]
            # 思考型模型（如 dots3-note-prev）即便 enable_thinking=False 也会产思考，
            # 预算过小会被思考占满 → content 为空或 JSON 被截断、解析失败。
            # 真实小说实测：评分/计数维均须 ≥8192 才稳定出完整 JSON。
            # HA-Eval L3：改用 chat_utility_response 取完整响应对象，
            # 以便把 cache_hit / prompt_hash / response_hash 记入证据。
            # 2026-09-12：网关收口点的瞬时故障退避只撑 ~15s 故障窗，网关故障
            # 实测可达数分钟——退避耗尽后维度"降级为默认"会污染体检分，导致
            # 假性不达标 → 无谓回滚（五灵破归档 ch190 前夜）。此处对瞬时故障
            # 再加长退避重试（20s/40s），仍失败才降级。
            resp = _chat_with_eval_backoff(
                self.llm, _messages, dimension=dimension, console=self.console
            )
            raw = getattr(resp, "text", "") or ""
            data = parse_llm_json(raw)
            issues = data.get("issues") or []
            if dimension in COUNT_DIMS and issues:
                # 以 issues 为准重算：仅计入 severity ∈ SEVERITY_GATE 的条数，忽略 LLM 自报 value。
                # 2026-09-10：进一步要求 issue 附原文定位（quote），无定位的臆测不计入
                # （全部无 quote 时回退原口径，见 _count_gated_issues docstring）。
                val = _count_gated_issues(issues)
            else:
                # 评分维或无 issues：回退 LLM 自报 value（向后兼容、行为不变）。
                # 2026-09-09：评分维 value 键缺失 = 形状异常（非真实 0 分），按解析失败
                # 走降级默认（评分维满分），避免伪 0 分 → SCORE_TOO_LOW → 批不可信
                # → L4 升级人工打断写作（五灵破两次批末误升级根因之一）。
                if "value" not in data and dimension not in COUNT_DIMS:
                    raise ValueError(
                        f"评分维 {dimension} 输出缺少 value 键（形状异常）"
                    )
                val = float(data.get("value", 0))
        except Exception as e:  # noqa: BLE001 - LLM 不可达/解析失败：降级默认
            if self.console is not None:
                self.console.print(f"[yellow]⚠ 维度 {dimension} 评分降级为默认：{e}[/yellow]")
            # 类级修复：降级必须携带 confidence=0 证据，否则降级值会被 L4 当成
            # 可信失败 → 假性不达标 → 无谓回滚（五灵破归档 ch190 前夜）。
            self._record_degraded(dimension, f"评分失败降级：{e}")
            return self._default_for(dimension)
        value = self._clamp(dimension, val)
        # G2：结构化结果落 _last_eval（issues/rationale），不扩展 score_fn 返回协议。
        # HA-Eval L3：附带 EvalEvidence，供 L4 处置层判断"这个分数能不能信"。
        self._last_eval[dimension] = {
            "value": value,
            "rationale": data.get("rationale", ""),
            "issues": issues,
            "evidence": build_evidence(
                messages=_messages,
                raw_response=raw,
                cache_hit=bool(getattr(resp, "cache_hit", False)),
                model=str(getattr(resp, "model", "") or ""),
                latency_ms=float(getattr(resp, "elapsed_ms", 0.0) or 0.0),
                issues=issues,
                rationale=str(data.get("rationale", "") or ""),
            ),
        }
        return value

    def _record_degraded(self, dimension: str, reason: str) -> None:
        """把降级结果写入 _last_eval，附 confidence=0 证据（供 get_evidence 旁路）。"""
        self._last_eval[dimension] = {
            "value": self._default_for(dimension),
            "rationale": reason,
            "issues": [],
            "evidence": build_degraded_evidence(reason, dimension=dimension),
        }

    def get_evidence(self, dimension: str) -> "EvalEvidence | None":
        """取该维度最近一次评分的证据（HA-Eval L3）。

        ``score_fn`` 协议只能返回 float，证据经本方法旁路提供给 EvaluatorAgent。
        未评分 / 降级路径返回 ``None``。
        """
        entry = self._last_eval.get(dimension)
        if not isinstance(entry, dict):
            return None
        return entry.get("evidence")

    # ---- HA-Eval L2：降级值与值域钳制改由 dimension_registry 派生（SSOT）----
    @staticmethod
    def _default_for(dimension: str) -> float:
        # 与 EvaluatorAgent._score 安全默认保持一致
        return safe_default_for(dimension)

    @staticmethod
    def _clamp(dimension: str, val: float) -> float:
        return clamp_value(dimension, val)

    def _gather_for_eval(self, dimension: str, project_dir: str) -> str:
        """收集评分所需文本（最新 1-3 章正文 + 世界观简介）。"""
        d = Path(project_dir)
        parts: list[str] = []
        # 世界观简介
        world = d / "world.md"
        if world.exists():
            content = world.read_text(encoding="utf-8")
            idx = content.find("## 故事简介")
            if idx >= 0:
                parts.append("【世界观简介】" + content[idx: idx + 400])
        # 设定真源（2026-09-12，五灵破归档 31 次回退复盘）：一致性类维度必须
        # 对照真源判定——此前评委只拿 400 字简介对 3 章正文自由裁量，
        # 长窗口上恒能挑出"不一致"（多为评委不知道设定），造成假性不达标 →
        # 整窗 5 章回退。注入冻结设定与角色状态后，只有与真源冲突才计 issue。
        if dimension in _CANON_DIMS:
            canon = _gather_canon(d)
            if canon:
                parts.insert(1, canon)
        # 最新章节（最多 3 章）——复用公共 helper（G6，消除根因 B6-3 重复实现）
        for f in take_chapter_files(list_chapter_files(project_dir), side="last", n=3):
            try:
                text = strip_frontmatter(f.read_text(encoding="utf-8")).strip()
            except OSError:
                continue  # noqa: SILENT_DEGRADE
            parts.append(f"【{f.stem}】\n{text[:2500]}")
        return "\n\n".join(parts)

    # ---------------------------------------------------------- 路径 2：迷爱看 6 维
    def score_chapter(
        self,
        chapter_text: str,
        *,
        title: str = "",
        genre: str = "",
        synopsis: str = "",
    ) -> ReaderAppealReport:
        """作者侧独立评分：迷爱看 6 维。LLM 不可用返回占位报告。"""
        context = ""
        if title:
            context += f"【章节标题】{title}\n"
        if genre:
            context += f"【题材】{genre}\n"
        if synopsis:
            context += f"【世界观简介】{synopsis[:300]}\n"
        user_prompt = (
            f"{context}\n【本章正文】\n{chapter_text[:10000]}"
        )
        try:
            # 类级修复：与 path 1 共用长退避（网关故障实测可达数分钟，单发失败
            # 即降级会让迷爱看/黄金三章闸门频繁短路）。
            resp = _chat_with_eval_backoff(
                self.llm,
                messages=[
                    {"role": "system", "content": pm.get("quality.reader_appeal").system},
                    {"role": "user", "content": user_prompt},
                ],
                dimension="appeal_six",
                console=self.console,
                temperature=0.3,
                # 思考型模型需留足预算才能产出完整 JSON（实测 ≥8192 稳）。
                max_tokens=8192,
            )
            return self._parse_appeal(getattr(resp, "text", "") or "")
        except Exception as e:  # noqa: BLE001 - 见下：区分形状异常与网络故障
            # 形状异常（缺维度/不可解析/全 0，_parse_appeal 抛 ValueError）可修复：
            # 附错误详情重试一次（对齐九项质检 fail-open 收口，优化登记
            # 20260913_金三评分降级重试与贴线复核）。此前形状异常直接降级离线占位
            # → 写时金三门禁短路放行，开头章免检落盘（灵荒薪传 ch003 实证）。
            if isinstance(e, ValueError):
                try:
                    resp = _chat_with_eval_backoff(
                        self.llm,
                        messages=[
                            {"role": "system", "content": pm.get("quality.reader_appeal").system},
                            {"role": "user", "content": user_prompt
                             + f"\n\n【上次评分输出解析失败原因，务必修正】{e}"
                               "请只输出一个合法的 JSON 对象：必须包含全部六个维度键"
                               "（hook_strength/payoff_density/immersion/character_arc/"
                               "world_novelty/emotion_curve），不要包含 ```json 标记。"},
                        ],
                        dimension="appeal_six",
                        console=self.console,
                        temperature=0.3,
                        max_tokens=8192,
                    )
                    return self._parse_appeal(getattr(resp, "text", "") or "")
                except Exception as e2:  # noqa: BLE001 - 重试仍失败才降级（显性登记后走下方占位）
                    degrade(
                        "reader_appeal.score_chapter.retry",
                        "迷爱看评分带错重试仍失败，降级离线占位（门禁短路放行）",
                        e2,
                    )
                    e = e2
            # 网络类故障（_chat_with_eval_backoff 已长退避）或重试仍失败：降级占位报告
            if self.console is not None:
                self.console.print(f"[yellow]⚠ 迷爱看评分降级（LLM 不可用）：{e}[/yellow]")
            return ReaderAppealReport(
                dimensions={k: 0 for k in APPEAL_DIMENSIONS},
                total_score=0,
                one_liner="LLM 不可用，无法评分",
                suggestions=[],
                llm_used=False,
                error=str(e),
                source="offline",
            )

    def _parse_appeal(self, raw: str) -> ReaderAppealReport:
        data = parse_llm_json(raw)
        dims_raw = data.get("dimensions", {}) or {}
        dims: dict[str, int] = {}
        for k in APPEAL_DIMENSIONS:
            # 类级修复：缺键 = 形状异常（与"全 0"同族）——若当 0 分参与门禁，
            # 单维漏答即假性不达标（低于触底线 40）→ 无谓回滚。抛错走调用方
            # 的离线占位路径（llm_used=False → evaluator_dims 离线短路放行）。
            if k not in dims_raw:
                raise ValueError(f"迷爱看评分输出缺少维度 {k}（形状异常）")
            try:
                v = int(dims_raw[k])
            except (TypeError, ValueError):
                raise ValueError(f"迷爱看维度 {k} 值不可解析（形状异常）") from None
            dims[k] = max(0, min(100, v))
        # 2026-09-09（五灵破归档两次批末误升级复盘）：解析"成功"但六维全 0 =
        # 输出形状异常（dimensions 键缺失/模型漏答），真实文本六维同时 0 分不可能。
        # 若照常返回 llm_used=True 全 0 报告 → L3 SCORE_TOO_LOW 降级 → L4 拒绝处置
        # → 升级人工打断写作。此处改为走 LLM 不可达同路径（离线占位，G3 降级不阻断），
        # 由调用方（evaluator_dims）短路为通过。
        if all(v == 0 for v in dims.values()):
            if self.console is not None:
                self.console.print(
                    "[yellow]⚠ 迷爱看评分解析为全 0（疑似输出形状异常），按离线短路处理[/yellow]"
                )
            return ReaderAppealReport(
                dimensions=dims,
                total_score=0,
                one_liner="评分输出形状异常（全 0），已按离线短路处理",
                suggestions=[],
                llm_used=False,
                error="all-zero dimensions",
                source="offline",
            )
        total = ReaderAppealReport._compute_total(dims)
        suggestions = [str(s) for s in (data.get("suggestions", []) or [])][:5]
        one_liner = str(data.get("one_liner", ""))[:60]
        return ReaderAppealReport(
            dimensions=dims,
            total_score=total,
            one_liner=one_liner,
            suggestions=suggestions,
            llm_used=True,
        )


# ============================================================
# G5 门禁判定 + 按章节取末章评分助手
# ============================================================
def is_pass(
    report: "ReaderAppealReport",
    threshold: int = APPEAL_PASS_LINE,
    floor: int = APPEAL_DIM_FLOOR,
) -> tuple[bool, list[str]]:
    """综合分 >= threshold 且 每个单维 >= floor 才通过。

    Returns:
        (passed, failed_dims)：passed=True 当且仅当
        综合 total_score >= threshold 且 每个维度 >= floor；
        failed_dims 为不达标维度（单维触底）的中文标签列表。
    """
    failed_dims = [
        APPEAL_LABELS.get(k, k) for k, v in report.dimensions.items() if v < floor
    ]
    passed = (report.total_score >= threshold) and (len(failed_dims) == 0)
    return passed, failed_dims


def gate_chapter(
    scorer: "ReaderAppealScorer",
    project_dir: str | Path,
    window: int = 1,
    *,
    title: str = "",
    genre: str = "",
    synopsis: str = "",
) -> "ReaderAppealReport":
    """读末 window 章正文拼接，调 scorer.score_chapter 得迷爱看报告。

    无 chapters 目录或 LLM 不可达时返回 llm_used=False 占位报告（不抛异常）；
    synopsis 为空时尝试从 project_dir/world.md 的『## 故事简介』段提取
    （复用 _gather_for_eval 风格）。章节文件匹配 ch*.md，去 frontmatter
    （以 '---' 开头则切掉首段）。G6：读取改走公共 helper（行为零变化）。
    """
    d = Path(project_dir)
    if not list_chapter_files(project_dir):
        # 无章节可评：返回离线占位（不抛异常），由调用方短路为通过。
        return ReaderAppealReport(
            dimensions={k: 0 for k in APPEAL_DIMENSIONS},
            total_score=0,
            one_liner="无章节可评",
            suggestions=[],
            llm_used=False,
            error="no chapters dir",
            source="offline",
        )
    texts = read_chapters_text(project_dir, side="last", n=window)
    chapter_text = "\n\n".join(texts)

    # synopsis 为空时尝试从 world.md 的『## 故事简介』段提取（复用 _gather_for_eval 风格）
    if not synopsis:
        world = d / "world.md"
        if world.exists():
            try:
                content = world.read_text(encoding="utf-8")
                idx = content.find("## 故事简介")
                if idx >= 0:
                    synopsis = content[idx: idx + 300]
            except OSError:
                pass  # noqa: SILENT_DEGRADE

    return scorer.score_chapter(
        chapter_text, title=title, genre=genre, synopsis=synopsis
    )


def _golden_cache_path(project_dir: str | Path) -> Path:
    return Path(project_dir) / ".state" / "golden_score_cache.json"


def _golden_fingerprint(project_dir: str | Path, n: int) -> str:
    """前 n 章内容指纹：正文任一字节变化即失效（重写前三章 → 自动重评）。"""
    import hashlib

    try:
        texts = read_chapters_text(project_dir, side="first", n=n)
    except Exception:  # noqa: BLE001 - 指纹计算失败仅放弃缓存，不阻断评分
        return ""
    return hashlib.sha256("\n\n".join(texts).encode("utf-8")).hexdigest()


def _load_golden_cache(project_dir: str | Path, fingerprint: str) -> "ReaderAppealReport | None":
    """命中且指纹一致且上次为真实 LLM 评分 → 返回缓存报告；否则 None。"""
    if not fingerprint:
        return None
    path = _golden_cache_path(project_dir)
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("fingerprint") != fingerprint:
        return None
    rep = data.get("report") or {}
    if not rep.get("llm_used") or rep.get("source") != "llm":
        return None  # 离线占位/异常报告不缓存不复用
    try:
        return ReaderAppealReport(
            dimensions=dict(rep["dimensions"]),
            total_score=int(rep["total_score"]),
            one_liner=str(rep.get("one_liner", "")),
            suggestions=list(rep.get("suggestions", [])),
            llm_used=True,
            error="",
            source="llm",
            chapters_scored=int(rep.get("chapters_scored", 1)),
            fallback=bool(rep.get("fallback", False)),
            summary_lines=list(rep.get("summary_lines", [])),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _save_golden_cache(project_dir: str | Path, fingerprint: str, report: "ReaderAppealReport") -> None:
    """只缓存真实 LLM 评分（llm_used=True 且无 error），失败静默放弃（G3）。"""
    if not fingerprint or not report.llm_used or report.error:
        return
    import json
    from datetime import datetime, timezone

    payload = {
        "fingerprint": fingerprint,
        "ts": datetime.now(timezone.utc).isoformat(),
        "report": {
            "dimensions": report.dimensions,
            "total_score": report.total_score,
            "one_liner": report.one_liner,
            "suggestions": report.suggestions,
            "chapters_scored": report.chapters_scored,
            "fallback": report.fallback,
            "summary_lines": report.summary_lines,
            "llm_used": True,
            "source": "llm",
        },
    }
    try:
        path = _golden_cache_path(project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # noqa: SILENT_DEGRADE


def gate_first_chapters(
    scorer: "ReaderAppealScorer",
    project_dir: str | Path,
    n: int = 3,
    *,
    title: str = "",
    genre: str = "",
    synopsis: str = "",
    threshold: int = GOLDEN_PASS_LINE,
) -> "ReaderAppealReport":
    """B4 黄金三章门禁评分：读前 n 章（默认 3）正文。

    评测方式（拍板 #3，C 拼接一次评分默认）：
    - 三章正文拼接为一段 → scorer.score_chapter 一次评分（成本 = 终审多 1 次 LLM 调用）；
    - 拼接长度超 GOLDEN_JOIN_CHAR_LIMIT(10000)（score_chapter 内部会截断，等价于只评了开头）
      → **fallback 每章独立评分取最差**：对每章分别 score_chapter，逐维取 min、
      total_score 取 min，llm_used = 任一在线（all 在线才 True），并置 fallback=True。
    离线（LLM 不可用）时各次 score_chapter 返回 llm_used=False 占位，由 Evaluator 短路为通过。
    无章节可评返回 llm_used=False 占位（不抛异常，仿 gate_chapter 行 403-413）。

    跨批缓存（2026-09-12）：前 n 章内容指纹未变时直接复用上次真实 LLM 评分
    （落盘 .state/golden_score_cache.json）。前三章一经写完通常不再变动，
    缓存避免每批评估都重评 6 维（每批 6 次 LLM 调用白烧），也避免同一
    低分开局反复触发金三熔断；重写前三章后指纹变化自动失效重评。
    """
    files = take_chapter_files(list_chapter_files(project_dir), side="first", n=n)
    if not files:
        return ReaderAppealReport(
            dimensions={k: 0 for k in APPEAL_DIMENSIONS},
            total_score=0, one_liner="无章节可评", suggestions=[],
            llm_used=False, error="no chapters dir", source="offline",
        )
    fingerprint = _golden_fingerprint(project_dir, n)
    texts = read_chapters_text(project_dir, side="first", n=n)
    joined = "\n\n".join(texts)

    def _score_sample() -> tuple[dict[str, int], int, bool, list[str]]:
        """一次完整采样：拼接路径评一次；超长回退逐章评分逐维取最差（拍板 #3）。

        Returns: (逐维得分, 综合分, 是否在线, suggestions)
        """
        if len(joined) <= GOLDEN_JOIN_CHAR_LIMIT:
            r = scorer.score_chapter(joined, title=title, genre=genre, synopsis=synopsis)
            return dict(r.dimensions), r.total_score, r.llm_used, list(r.suggestions)
        worst: dict[str, int] = {k: 100 for k in APPEAL_DIMENSIONS}
        worst_total = 100
        any_online = False
        for t in texts:
            r = scorer.score_chapter(t, title=title, genre=genre, synopsis=synopsis)
            if r.llm_used:
                any_online = True
            for k in APPEAL_DIMENSIONS:
                worst[k] = min(worst.get(k, 100), r.dimensions.get(k, 0))
            worst_total = min(worst_total, r.total_score)
        return worst, worst_total, any_online, []

    def _borderline(total: int, online: bool) -> bool:
        """综合分是否落在达标线 ±GOLDEN_BORDERLINE_BAND 的贴线带内（且在线）"""
        return (
            online
            and (threshold - GOLDEN_BORDERLINE_BAND) <= total <= (threshold + GOLDEN_BORDERLINE_BAND)
        )

    def _avg_report(
        dims1: dict[str, int], total1: int, sugg1: list[str],
        chapters_scored: int, fallback: bool,
    ) -> "ReaderAppealReport | None":
        """追加一次采样并与 (dims1, total1) 取逐维均值；离线/异常返回 None（保留首评）。"""
        try:
            dims2, total2, online2, _ = _score_sample()
            if not online2:
                return None
            merged = {
                k: int(round((dims1.get(k, 0) + dims2.get(k, 0)) / 2))
                for k in APPEAL_DIMENSIONS
            }
            new_total = ReaderAppealReport._compute_total(merged)
            return ReaderAppealReport(
                dimensions=merged,
                total_score=new_total,
                one_liner=f"贴线二次采样复核：首评 {total1}/复核 {total2}，取逐维均值",
                suggestions=sugg1,
                llm_used=True,
                source="llm",
                chapters_scored=chapters_scored,
                fallback=fallback,
            )
        except Exception as e:  # noqa: BLE001 - 复核异常保留首评（复核是方差抑制，不是新门禁）
            degrade(
                "reader_appeal.gate_first_chapters.recheck",
                "金三贴线二次采样复核失败，保留首评",
                e,
            )
            return None

    cached = _load_golden_cache(project_dir, fingerprint)
    if cached is not None:
        # 贴线缓存复核（优化登记 20260913 补丁，灵荒薪传 59/60 缓存复用二次熔断实证）：
        # 缓存命中会短路与贴线复核叠加——一次贴线波动被缓存永久固化，每批重复熔断。
        # 缓存分落在贴线带内时追加一次采样取均值并刷新缓存；远离贴线带维持缓存零成本。
        if _borderline(cached.total_score, bool(cached.llm_used)):
            _re = _avg_report(
                dict(cached.dimensions), cached.total_score,
                list(cached.suggestions), cached.chapters_scored, bool(cached.fallback),
            )
            if _re is not None:
                _save_golden_cache(project_dir, fingerprint, _re)
                return _re
        return cached

    fallback = len(joined) > GOLDEN_JOIN_CHAR_LIMIT
    dims1, total1, online1, sugg1 = _score_sample()
    report = ReaderAppealReport(
        dimensions=dims1,
        total_score=total1,
        one_liner="三章拼接超长，已按每章独立评分取最差" if fallback else "",
        suggestions=sugg1,
        llm_used=online1,
        source="llm" if online1 else "offline",
        chapters_scored=n if fallback else 1,
        fallback=fallback,
    )

    # 贴线二次采样复核（优化登记 20260913_金三评分降级重试与贴线复核）：
    # 首评落在达标线 ±GOLDEN_BORDERLINE_BAND 且在线时，再采一次取逐维均值——
    # 单样本 LLM 评分在 60 线附近方差足以"1 分之差"误熔断整本书
    # （灵荒薪传 59/60 熔断，同文本重评 65/64/74 实证）。复核失败保留首评不阻断。
    if _borderline(total1, online1):
        _re = _avg_report(dims1, total1, sugg1, report.chapters_scored, fallback)
        if _re is not None:
            report = _re

    _save_golden_cache(project_dir, fingerprint, report)
    return report
