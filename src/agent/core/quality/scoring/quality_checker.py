"""质量校验器（5.4）

职责：章节产出后执行通用层 + 题材层规则，未通过自动修订。

通用层规则：
    - 开篇钩子 / 情绪锚点 / 章末悬念 / 场景描写占比 / 禁用词限量
    - 设定一致性 / 台词个性化 / 伏笔状态 / 高潮扩写

题材层规则：由题材包提供（如修仙的境界推进、战力校验）

修订循环：检查 → 不通过 → 调用 LLM 按修订提示改写 → 再检查（≤ M 次）
"""

from __future__ import annotations

from agent.core.infra.prompt_manager import pm
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from agent.utils import parse_llm_json


class RuleLayer(str, Enum):
    """规则层级"""

    COMMON = "common"    # 通用层
    GENRE = "genre"      # 题材层


class Severity(str, Enum):
    """规则严重度"""

    BLOCK = "block"      # 阻断，必须修订
    WARN = "warn"        # 警告


@dataclass
class QualityRule:
    """质量校验规则"""

    id: str
    name: str
    layer: RuleLayer
    severity: Severity
    # check 为可调用 (text, ctx, llm) -> list[Issue]；默认 None（子类/延迟绑定）
    check: Any = None
    revise_hint: str = ""  # 给 LLM 的修订提示


@dataclass
class Issue:
    """校验问题"""

    rule_id: str
    severity: Severity
    description: str
    location: str = ""  # 章节中的位置（如"前 500 字"）


@dataclass
class QualityReport:
    """质量校验报告"""

    passed: bool
    issues: list[Issue] = field(default_factory=list)
    revision_attempts: int = 0


# 修订重试上限（M18 决策）
MAX_REVISION_ATTEMPTS = 3

# 通用层禁用词限量配置
BANNED_WORDS = ["突然", "忽然", "就在这时", "微微一笑"]
BANNED_WORDS_MAX_COUNT = 2

# 场景描写最低占比
MIN_SCENE_RATIO = 0.30

# ============================================================
# 题材层规则注册表（T-3：模块级单一通道，供题材包 hook 注册）
# ============================================================
GENRE_RULES: list[QualityRule] = []


def register_genre_rules(project_dir: Path, genre: str, pack: "Any") -> None:
    """题材包 hook：解析 pack.quality_rules 文本并注册为题材层 QualityRule

    供 SKILL.md hooks 调用（签名遵循共享约定 4：project_dir, genre, pack）。
    解析结果追加到模块级 GENRE_RULES，QualityChecker 构造时自动合并（见 __init__）。
    """
    GENRE_RULES.extend(_parse_genre_quality_rules(pack))


def _parse_genre_quality_rules(pack: "Any") -> list[QualityRule]:
    """将题材包的 quality-rules.md 文本解析为 QualityRule 列表（T-3）

    题材层规则目前以自由文本描述（见各题材包 quality-rules.md），拆分为
    ``### G-0X 名称`` 块，逐块生成一个 QualityRule（check 暂为占位 no-op，
    真实检查逻辑由 T-5 的 QualityChecker.check 统一驱动）。
    """
    text = getattr(pack, "quality_rules", "") or ""
    if not text:
        return []
    rules: list[QualityRule] = []
    # 按 "### " 切分规则块（首个块为标题/前言，跳过）
    blocks = re.split(r"(?m)^###\s+", text)
    for block in blocks[1:]:
        lines = block.strip().splitlines()
        if not lines:
            continue
        heading = lines[0].strip()
        parts = heading.split(None, 1)
        rule_id = parts[0] if parts else f"G-{len(rules) + 1:02d}"
        rule_name = parts[1] if len(parts) > 1 else heading
        severity = Severity.WARN
        revise_hint = ""
        for line in lines[1:]:
            low = line.lower()
            if "严重度" in line:
                severity = Severity.BLOCK if "block" in low else Severity.WARN
            if "修订提示" in line:
                revise_hint = line.split("修订提示", 1)[1].strip().lstrip("：:").strip()
        rules.append(QualityRule(
            id=rule_id,
            name=rule_name,
            layer=RuleLayer.GENRE,
            severity=severity,
            # 题材层规则检查由 T-5 的 QualityChecker.check 统一驱动；此处占位 no-op
            check=lambda chapter_text, ctx, llm: [],  # noqa: ARG005
            revise_hint=revise_hint,
        ))
    return rules


def _noop_quality_check(chapter_text: str, ctx: dict[str, Any], llm: Any) -> list[Issue]:
    """占位通用层规则检查（无问题）"""
    return []


# ============================================================
# D：LLM 驱动的多维网文质量评审（增量 D / T04）
# ============================================================
@dataclass
class LLMQualityRule(QualityRule):
    """LLM 驱动的网文维度质量规则（D）

    主观维度（爽点/OOC/连贯性/追读力）由 ``LLMBackedChecker`` 合并为「单次
    chat_utility 调用」统一驱动（避免每维度一次网络往返）。``check()`` 在
    ``llm is None`` 时返回空（降级，绝不阻断写章）。

    注意：本类为 dataclass（继承 ``QualityRule``），``dimension`` / ``prompt_template``
    均为字段；``check`` 在 ``__post_init__`` 中绑定到 ``_check``（基类 ``check`` 默认 None）。
    """

    dimension: str = ""        # 维度 key（如 cool_point），对应合并 JSON 的键
    prompt_template: str = ""  # 该维度评审标准描述
    # 重写基类 check 字段，给默认 None；实际在 __post_init__ 绑定到 _check
    check: Any = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(self, "check", self._check)

    def _check(self, text: str, ctx: dict[str, Any], llm: Any) -> list[Issue]:
        """单维度 LLM 检查（供单独调用/降级；合并路径由 LLMBackedChecker 负责）"""
        if llm is None:
            return []
        user = pm.get("m_d.review").render_user(
            chapter_text=text,
            dimensions=f"- {self.dimension}（{self.name}）：{self.prompt_template}",
        )
        try:
            resp = llm.chat_utility(
                [
                    {"role": "system", "content": pm.get("m_d.review").system},
                    {"role": "user", "content": user},
                ],
                max_tokens=800,
                enable_thinking=False,
            )
            data = parse_llm_json(resp.text)
        except Exception:  # noqa: BLE001 - 单维度失败降级为空
            return []
        dim = data.get(self.dimension)
        if not isinstance(dim, dict):
            return []
        if dim.get("pass", True) and not dim.get("blocking", False):
            return []
        return [
            Issue(
                rule_id=self.id,
                severity=(
                    Severity.BLOCK
                    if (dim.get("blocking") or self.severity == Severity.BLOCK)
                    else self.severity
                ),
                description=f"[{self.name}] {dim.get('issue', '')}".strip(),
            )
        ]


class LLMBackedChecker:
    """LLM 维度评审驱动器（D）

    把所有 ``LLMQualityRule`` 合并为「一次 chat_utility 调用」，解析合并 JSON 后映射回
    各维度 Issue。LLM 不可用时返回空；调用异常/超时降级为空（放行 + 记录）。
    """

    def __init__(self, llm: Any) -> None:
        self.llm = llm

    def run_rules(self, rules: list[QualityRule], text: str, ctx: dict[str, Any]) -> list[Issue]:
        llm_rules = [r for r in rules if isinstance(r, LLMQualityRule)]
        if not llm_rules or self.llm is None:
            return []
        dimensions_block = "\n".join(
            f"- {r.dimension}（{r.name}）：{r.prompt_template}" for r in llm_rules
        )
        user = pm.get("m_d.review").render_user(
            chapter_text=text, dimensions=dimensions_block
        )

        result = self._with_timeout(
            lambda: self.llm.chat_utility(
                [
                    {"role": "system", "content": pm.get("m_d.review").system},
                    {"role": "user", "content": user},
                ],
                max_tokens=1500,
                enable_thinking=False,
            ),
            default=None,
        )
        if result is None:
            return []
        try:
            data = parse_llm_json(result.text)
        except Exception:  # noqa: BLE001 - 解析失败降级为空
            return []

        issues: list[Issue] = []
        for r in llm_rules:
            dim = data.get(r.dimension)
            if not isinstance(dim, dict):
                continue
            if dim.get("pass", True) and not dim.get("blocking", False):
                continue
            issues.append(
                Issue(
                    rule_id=r.id,
                    severity=(
                        Severity.BLOCK
                        if (dim.get("blocking") or r.severity == Severity.BLOCK)
                        else r.severity
                    ),
                    description=f"[{r.name}] {dim.get('issue', '')}".strip(),
                )
            )
        return issues

    @staticmethod
    def _with_timeout(fn: Any, default: Any, timeout: float = 30.0) -> Any:
        """带超时的安全执行（异常/超时均返回 default，放行不阻断）"""
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(fn).result(timeout=timeout)
        except (FuturesTimeout, Exception):  # noqa: BLE001
            return default


def _check_banned_words(
    chapter_text: str, ctx: dict[str, Any], llm: Any
) -> list[Issue]:
    """禁用词限量检查：全章禁用词出现次数超过上限则报 BLOCK issue（T-5）"""
    issues: list[Issue] = []
    total = 0
    detail: list[str] = []
    for word in BANNED_WORDS:
        count = chapter_text.count(word)
        if count:
            total += count
            detail.append(f"{word}×{count}")
    if total > BANNED_WORDS_MAX_COUNT:
        issues.append(Issue(
            rule_id="banned_words",
            severity=Severity.BLOCK,
            description=(
                f"禁用词出现 {total} 次（上限 {BANNED_WORDS_MAX_COUNT}）："
                + "、".join(detail)
            ),
        ))
    return issues


class QualityChecker:
    """质量校验器"""

    def __init__(self, project_dir: Path, llm: Any | None = None) -> None:
        self.project_dir = project_dir
        self.llm = llm
        self.rules: list[QualityRule] = []
        self._register_common_rules()
        # T-3：合并模块级题材层规则（由题材包 hook 注册，单一通道）
        self.rules.extend(GENRE_RULES)

    def _register_common_rules(self) -> None:
        """注册通用层规则（T-5：挂可调用 check）+ D 的 LLM 维度规则"""
        common_rules = [
            ("hook", "开篇钩子", "前 500 字内出现冲突/悬念/反差", _noop_quality_check),
            ("emotion_anchor", "情绪锚点", "本章至少含一个爽/虐/燃/甜/惊锚点", _noop_quality_check),
            ("chapter_end_hook", "章末悬念", "章末必须有悬念/反转/期待", _noop_quality_check),
            ("scene_ratio", "场景描写占比", f"场景+动作+环境 ≥ {MIN_SCENE_RATIO*100:.0f}%", _noop_quality_check),
            ("banned_words", "禁用词限量", f"{BANNED_WORDS} 全章 ≤ {BANNED_WORDS_MAX_COUNT} 次", _check_banned_words),
            ("dialogue_style", "台词个性化", "角色台词符合其语言指纹", _noop_quality_check),
            ("foreshadow_state", "伏笔状态", "如埋/回收伏笔，foreshadows.md 已更新", _noop_quality_check),
            ("climax_expansion", "高潮扩写", "高潮章节自动扩篇幅+多视角+慢镜头", _noop_quality_check),
        ]
        for rule_id, name, hint, check_fn in common_rules:
            self.rules.append(QualityRule(
                id=rule_id,
                name=name,
                layer=RuleLayer.COMMON,
                severity=Severity.BLOCK,
                check=check_fn,
                revise_hint=hint,
            ))

        # D：LLM 驱动的网文维度（合并为单次调用，由 LLMBackedChecker 驱动）
        llm_dimensions = [
            ("d_cool_point", "爽点密度", "cool_point",
             "本章是否有明确的爽/虐/燃/甜/惊锚点，是否让读者获得情绪满足", Severity.WARN),
            ("d_ooc", "角色一致性(OOC)", "ooc",
             "角色台词/行为是否符合其语言指纹与既定内核，有无 OOC 崩坏", Severity.BLOCK),
            ("d_coherence", "连贯性", "coherence",
             "本章情节/时间线/设定是否与前后文连贯，有无突兀跳跃", Severity.WARN),
            ("d_pacing_hook", "追读力", "pacing_hook",
             "章末是否有足够悬念/反转/期待支撑读者继续读下去（追读力）", Severity.WARN),
        ]
        for rule_id, name, dim, criterion, sev in llm_dimensions:
            self.rules.append(LLMQualityRule(
                id=rule_id,
                name=name,
                layer=RuleLayer.COMMON,
                severity=sev,
                dimension=dim,
                prompt_template=criterion,
                revise_hint=criterion,
            ))

    @property
    def llm_rules(self) -> list["LLMQualityRule"]:
        """返回所有 LLM 维度规则（供 LLMBackedChecker 合并驱动）"""
        return [r for r in self.rules if isinstance(r, LLMQualityRule)]

    def register_genre_rules(self, genre_rules: list[QualityRule]) -> None:
        """注册题材层规则（由题材包调用）"""
        self.rules.extend(genre_rules)

    def check(self, chapter_text: str, ctx: dict[str, Any] | None = None) -> QualityReport:
        """执行质量校验（T-5：遍历 rules 收集 Issue，按 severity 定 passed）

        Args:
            chapter_text: 章节正文
            ctx: 上下文（涉及角色档案、伏笔表等）

        Returns:
            QualityReport（存在 BLOCK 级 Issue 时 passed=False）
        """
        ctx = ctx or {}
        issues: list[Issue] = []
        for rule in self.rules:
            if rule.check is None:
                continue
            # LLM 维度规则由 LLMBackedChecker 统一合并驱动（单次调用），
            # 不在通用循环里逐条触发网络往返
            if isinstance(rule, LLMQualityRule):
                continue
            try:
                rule_issues = rule.check(chapter_text, ctx, getattr(self, "llm", None))
            except Exception:  # noqa: BLE001 - 单条规则异常不影响整体校验
                continue
            if rule_issues:
                issues.extend(rule_issues)
        passed = not any(i.severity == Severity.BLOCK for i in issues)
        return QualityReport(passed=passed, issues=issues)


    def revise_loop(
        self,
        chapter_text: str,
        ctx: dict[str, Any],
        revise_fn: Any,  # Callable[[str, list[Issue]], str]
    ) -> tuple[str, QualityReport]:
        """修订循环：检查 → 修订 → 再检查（≤ MAX_REVISION_ATTEMPTS 次，T-5）

        Args:
            chapter_text: 原始章节
            ctx: 上下文
            revise_fn: 修订函数（接收原文+issues，返回修订后文本）

        Returns:
            (最终文本, 最终报告)
        """
        text = chapter_text
        ctx = ctx or {}
        report = self.check(text, ctx)
        attempts = 0
        while not report.passed and attempts < MAX_REVISION_ATTEMPTS:
            text = revise_fn(text, report.issues)
            report = self.check(text, ctx)
            attempts += 1
            report.revision_attempts = attempts
        return text, report
