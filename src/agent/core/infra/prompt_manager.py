"""提示词单一真源加载器（prompts/ 目录 + Markdown）

设计来源：《提示词管理方案设计.md》。把分散在代码里的 LLM 提示词收口到
``prompts/`` 目录下的 Markdown 文件，统一用 YAML frontmatter 管元数据/校验、
``# system`` / ``# user`` 二级标题切分两段、Jinja2 渲染、按题材（``genre``）覆盖。

关键属性：
- **零回归**：``get`` 找不到 md 时回退到 ``LEGACY_MAP`` 里登记的原始代码常量，
  调用点不会因"提示词缺失"而崩；``prompts.py`` 保留到全量迁移完成。
- **热重载**：默认按文件 mtime 比对，CLI 短进程/Web 长驻进程都能"改即生效"。
- **与 §6 同源**：frontmatter 的 ``validation:`` 块直接编译成 ``ValidationSpec``，
  调用方可 ``llm.chat(msgs, validators=p.validation)`` 一处收口。
- **题材参数化**：``get(name, genre=...)`` 优先取 ``<base>.<genre>.md`` 覆盖文件。

分层：放在 ``core/infra``（只依赖 ``base`` 与第三方 jinja2/yaml），不反向依赖 workflow。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import jinja2
import yaml

from agent.base.validation import ValidationSpec

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"  # core/infra -> agent/prompts

# 各 section 的 Jinja2 环境：默认 Undefined 渲染为空串（不抛异常），保证调用点
# 漏传变量时降级为空而非崩溃——与"零回归"一致。
_ENV = jinja2.Environment(trim_blocks=False, lstrip_blocks=False, autoescape=False)


def _split_sections(body: str) -> tuple[str, str]:
    """把 md body 按 ``# system`` / ``# user`` 切成 (system, user) 文本。

    没有对应标题时该段为空；都没有则整段当作 system。

    提取规则：仅去掉标题行自身结尾的换行（结构性分隔），保留内容自身的首尾空白
    （如 G* 注入模板以 ``\\n\\n`` 开头用于拼接时提供空行分隔），再去掉尾部换行。
    md 写作约定为 ``# system\\n<内容>``（标题与内容间不额外留空行），这样内容自身的
    前导换行能被精确保留，与原始常量逐字一致。
    """
    # 收尾用 ``[^\S\n]*``（仅空格/制表，不含换行）+ lookahead，匹配停在标题行自身：
    # 这样标题行后的换行（结构性分隔）会作为段首字符留给 ``_section_text`` 裁掉，
    # 而内容自身的前导换行（如 G* 注入模板开头的 ``\n\n``）被完整保留。
    # 若用 ``\s*$``，MULTILINE 下贪婪的 ``\s*`` 会跨过换行、把内容的 ``\n\n`` 一并吞掉，
    # 导致拼接时丢失空行分隔。
    sys_m = re.search(r"^#\s*system[^\S\n]*(?=\n|$)", body, re.MULTILINE)
    usr_m = re.search(r"^#\s*user[^\S\n]*(?=\n|$)", body, re.MULTILINE)
    if sys_m is None and usr_m is None:
        return body.strip(), ""
    system = ""
    user = ""
    if sys_m is not None:
        start = sys_m.end()
        end = usr_m.start() if usr_m is not None else len(body)
        system = _section_text(body[start:end])
    if usr_m is not None:
        user = _section_text(body[usr_m.end():])
    return system, user


def _section_text(seg: str) -> str:
    """去掉标题行结尾的单个换行（结构性分隔），保留内容自身首尾空白，再裁掉尾部换行。"""
    if seg.startswith("\n"):
        seg = seg[1:]
    return seg.rstrip("\n")


def _build_validation(spec: dict[str, Any] | None) -> list[ValidationSpec]:
    """frontmatter ``validation:`` -> ValidationSpec 列表。

    支持：min_length / max_length / not_empty / required_keys(json) /
    forbid_patterns / score_range(low/high/score_path) / on_fail(retry|warn|block)。
    """
    if not spec:
        return []
    on_fail = (spec.get("on_fail") or "retry").lower()
    severity = "P1" if on_fail == "warn" else "P0"
    out: list[ValidationSpec] = []
    if spec.get("not_empty"):
        out.append(ValidationSpec.not_empty(severity=severity))
    if spec.get("min_length"):
        out.append(ValidationSpec.min_length(int(spec["min_length"]), severity=severity))
    if spec.get("max_length"):
        out.append(ValidationSpec.max_length(int(spec["max_length"]), severity=severity))
    if spec.get("json_valid") or spec.get("required_keys") or spec.get("schema"):
        out.append(
            ValidationSpec.json_valid(
                severity=severity, required_keys=spec.get("required_keys")
            )
        )
    if spec.get("forbid_patterns"):
        out.append(
            ValidationSpec.forbid_patterns(spec["forbid_patterns"], severity="P1")
        )
    sr = spec.get("score_range")
    if sr:
        out.append(
            ValidationSpec.score_in_range(
                low=sr.get("low"),
                high=sr.get("high"),
                score_path=sr.get("score_path", "score"),
                severity=severity,
            )
        )
    return out


@dataclass
class PromptDef:
    """单个提示词定义（已解析可渲染）。"""

    name: str
    version: int = 1
    model: str | None = None
    temperature: float | None = None
    system: str = ""
    user_template: str = ""
    validation: list[ValidationSpec] = field(default_factory=list)
    _user_j2: jinja2.Template = field(default="", repr=False)
    source: str = "md"  # "md" | "legacy"

    def __post_init__(self) -> None:
        try:
            self._user_j2 = _ENV.from_string(self.user_template) if self.user_template else None  # type: ignore[assignment]
        except Exception:
            self._user_j2 = None

    def render_system(self, **ctx: Any) -> str:
        if not self.system:
            return ""
        # 无 Jinja 标记（如含字面 JSON 示例 ``{{...}}`` 的常量 system）直接原样返回，
        # 避免 Jinja 把字面 ``{{`` 误当变量语法而抛 TemplateSyntaxError。
        if not _has_jinja(self.system):
            return self.system
        try:
            return _ENV.from_string(self.system).render(**ctx)
        except Exception:
            return self.system

    def render_user(self, **ctx: Any) -> str:
        if not self.user_template:
            return ""
        if self._user_j2 is None:
            # 编译失败（如字面 ``{{``）→ 原样返回（调用方仍可拿到文本，仅变量未替换）
            return self.user_template
        return self._user_j2.render(**ctx)

    def has_user(self) -> bool:
        return bool(self.user_template)


def _has_jinja(s: str) -> bool:
    return "{{" in s or "{%" in s or "{#" in s


# ============================================================================
# 迁移期 legacy 回退：name -> 返回 (system, user_template) 的惰性函数。
# 仅登记"本次已迁到 md、但 md 万一缺失也要用原文本"的提示词，保证零回归。
# 各函数惰性 import，避免模块加载时的重依赖/循环依赖。
# ============================================================================
LEGACY_MAP: dict[str, Callable[[], tuple[str, str]]] = {}


def _register(name: str, module: str, system_attr: str | None, user_attr: str | None = None) -> None:
    def _loader() -> tuple[str, str]:
        import importlib

        mod = importlib.import_module(module)
        system = getattr(mod, system_attr) if system_attr else ""
        user = getattr(mod, user_attr) if user_attr else ""
        return system, user

    LEGACY_MAP[name] = _loader


_register("agents.planner", "agent.agents.planner", "_PLANNER_SYSTEM")
_register("agents.writer_retry", "agent.agents.writer_agent", "_RETRY_JSON_PROMPT")
_register(
    "quality.bad_point_scan",
    "agent.core.quality.scan.bad_point_scanner",
    "_LLM_SCAN_SYSTEM_PROMPT",
    "_LLM_SCAN_USER_TEMPLATE",
)
_register("quality.reader_appeal_eval", "agent.core.quality.scoring.reader_appeal", "_EVAL_SYSTEM_PROMPT")
_register("quality.reader_appeal", "agent.core.quality.scoring.reader_appeal", "_APPEAL_SYSTEM_PROMPT")
_register(
    "quality.rewrite",
    "agent.core.quality.rewrite.feedback_rewriter",
    "_REWRITE_SYSTEM_PROMPT",
    "_REWRITE_USER_TEMPLATE",
)
_register("budget.branch", "agent.workflows.budget_planner", "_SYSTEM_PROMPT")
_register("m1.world", "agent.prompts", "M1_SYSTEM_PROMPT", "M1_USER_PROMPT_TEMPLATE")

# ---- 阶段 B：prompts.py 全量迁移（M2/M3/M4/M5/M6/M12/M14/M15/M16/M19/m_d/E/G8/G11/G12/G）----
_register("m2.discuss", "agent.prompts", 'M2_SYSTEM_PROMPT', 'M2_USER_PROMPT_TEMPLATE')
_register("m3.outline", "agent.prompts", 'M3_SYSTEM_PROMPT', 'M3_USER_PROMPT_TEMPLATE')
_register("m4.character", "agent.prompts", 'M4_SYSTEM_PROMPT', 'M4_USER_PROMPT_TEMPLATE')
_register("m14.architecture", "agent.prompts", 'M14_SYSTEM_PROMPT', 'M14_USER_PROMPT_TEMPLATE')
_register("m14.iterate", "agent.prompts", 'M14_ITERATE_SYSTEM_PROMPT', 'M14_ITERATE_USER_PROMPT_TEMPLATE')
_register("m14.gap_check", "agent.prompts", 'M14_GAP_CHECK_SYSTEM_PROMPT', 'M14_GAP_CHECK_USER_PROMPT_TEMPLATE')
_register("m5.generate", "agent.prompts", 'M5_GENERATE_SYSTEM_PROMPT', 'M5_GENERATE_USER_TEMPLATE')
_register("m5.quality_check", "agent.prompts", 'M5_QUALITY_CHECK_SYSTEM_PROMPT', 'M5_QUALITY_CHECK_USER_TEMPLATE')
_register("m5.revise", "agent.prompts", 'M5_REVISE_SYSTEM_PROMPT', 'M5_REVISE_USER_TEMPLATE')
_register("m6.adjust_route", "agent.prompts", 'M6_ADJUST_ROUTE_SYSTEM_PROMPT', 'M6_ADJUST_ROUTE_USER_TEMPLATE')
_register("m6.adjust_relation", "agent.prompts", 'M6_ADJUST_RELATION_SYSTEM_PROMPT', 'M6_ADJUST_RELATION_USER_TEMPLATE')
_register("m6.impact_report", "agent.prompts", 'M6_IMPACT_REPORT_SYSTEM_PROMPT', 'M6_IMPACT_REPORT_USER_TEMPLATE')
_register("m12.conflict", "agent.prompts", 'M12_CONFLICT_SYSTEM_PROMPT', 'M12_CONFLICT_USER_TEMPLATE')
_register("m12.content_audit", "agent.prompts", 'M12_CONTENT_AUDIT_SYSTEM_PROMPT', 'M12_CONTENT_AUDIT_USER_TEMPLATE')
_register("m12.summary", "agent.prompts", 'M12_SUMMARY_SYSTEM_PROMPT', 'M12_SUMMARY_USER_TEMPLATE')
_register("m15.bookworm", "agent.prompts", 'M15_BOOKWORM_SYSTEM_PROMPT', 'M15_BOOKWORM_USER_TEMPLATE')
_register("m16.pacing", "agent.prompts", 'M16_PACING_SYSTEM_PROMPT', 'M16_PACING_USER_TEMPLATE')
_register("m19.review", "agent.prompts", 'M19_REVIEW_SYSTEM_PROMPT', 'M19_REVIEW_USER_TEMPLATE')
_register("m_d.review", "agent.prompts", 'M_D_REVIEW_SYSTEM_PROMPT', 'M_D_REVIEW_USER_TEMPLATE')
_register("e.learn_extract", "agent.prompts", 'E_LEARN_EXTRACT_SYSTEM_PROMPT', 'E_LEARN_EXTRACT_USER_TEMPLATE')
_register("g11.method_instruction", "agent.prompts", None, 'G11_METHOD_INSTRUCTION_TEMPLATE')
_register("g11.style_instruction", "agent.prompts", None, 'G11_STYLE_INSTRUCTION_TEMPLATE')
_register("g12.emotion_instruction", "agent.prompts", None, 'G12_EMOTION_INSTRUCTION_TEMPLATE')
_register("g12.payoff_instruction", "agent.prompts", None, 'G12_PAYOFF_INSTRUCTION_TEMPLATE')
_register("g12.reader_feedback", "agent.prompts", None, 'G12_READER_FEEDBACK_TEMPLATE')
_register("g8.ending_fallback_instruction", "agent.prompts", None, 'G8_ENDING_FALLBACK_INSTRUCTION')
_register("g8.ending_instruction", "agent.prompts", None, 'G8_ENDING_INSTRUCTION_TEMPLATE')
_register("g.character_state_constraint", "agent.prompts", None, 'G_CHARACTER_STATE_CONSTRAINT_TEMPLATE')


class PromptManager:
    """提示词加载器（单例 ``pm``）。

    - 启动时扫 ``prompts/``（懒加载：首次 ``get`` 才解析具体文件）。
    - 默认 mtime 热重载：文件改了下次 ``get`` 自动重解析。
    - genre 覆盖：``get("m1.world", genre="funeral")`` 优先 ``prompts/m1/world.funeral.md``。
    """

    def __init__(self, root: Path = PROMPTS_DIR, hot_reload: bool = True) -> None:
        self.root = Path(root)
        self.hot_reload = hot_reload
        self._cache: dict[str, tuple[float, PromptDef]] = {}

    # ---- 路径解析 ----
    def _path_for(self, name: str, genre: str | None) -> Path | None:
        parts = name.split(".")
        if len(parts) >= 2:
            base = self.root / parts[0] / parts[1]
        else:
            base = self.root / parts[0] / parts[0]
        if genre:
            cand = self.root / f"{base}.{genre}.md"
            if cand.exists():
                return cand
        md = self.root / f"{base}.md"
        return md if md.exists() else None

    # ---- 解析单个 md ----
    def _load_file(self, path: Path, name: str) -> PromptDef:
        raw = path.read_text(encoding="utf-8")
        fm_match = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw, re.DOTALL)
        if fm_match:
            meta = yaml.safe_load(fm_match.group(1)) or {}
            body = fm_match.group(2)
        else:
            meta = {}
            body = raw
        system, user = _split_sections(body)
        return PromptDef(
            name=name,
            version=int(meta.get("version", 1) or 1),
            model=meta.get("model"),
            temperature=meta.get("temperature"),
            system=system,
            user_template=user,
            validation=_build_validation(meta.get("validation")),
            source="md",
        )

    def _legacy(self, name: str) -> PromptDef | None:
        loader = LEGACY_MAP.get(name)
        if loader is None:
            return None
        system, user = loader()
        return PromptDef(
            name=name,
            system=system,
            user_template=user,
            validation=[],
            source="legacy",
        )

    # ---- 对外 API ----
    def get(self, name: str, genre: str | None = None) -> PromptDef:
        path = self._path_for(name, genre)
        if path is None:
            legacy = self._legacy(name)
            if legacy is not None:
                return legacy
            raise KeyError(f"提示词未找到且无 legacy 回退：{name}（genre={genre}）")
        mtime = path.stat().st_mtime
        cached = self._cache.get(name)
        if cached is not None and (not self.hot_reload or cached[0] == mtime):
            return cached[1]
        pd = self._load_file(path, name)
        self._cache[name] = (mtime, pd)
        return pd

    def reload(self) -> None:
        """清空缓存，强制下次 get 重新解析全部文件。"""
        self._cache.clear()


# 模块级单例（Web 长驻进程共享一个，CLI 短进程也可直接用）
pm = PromptManager()


__all__ = ["PromptManager", "PromptDef", "pm", "PROMPTS_DIR", "LEGACY_MAP"]
