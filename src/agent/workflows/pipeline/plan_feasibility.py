"""批前细纲可行性对账（2026-09-25，灵荒工坊 ch80-84 窗口实证驱动）。

问题
----
写手连续升级停批的最深层根因在**规划层**：细纲（逐章契约行）给出的数字/
事件与冻结设定互相矛盾——台账写「吞噬诀每日三次、九件为上限」，细纲却安排
「三天后复验首批五十件、一百二十件」。写手被迫在两个不可同时满足的指令间
缝合数字（9/12/50/107/108/120 全是挣扎痕迹），评委每轮都抓到算术矛盾，
修复重写怎么改都修不好——**不可满足的计划，任何修复策略都收敛不了**。

方案
----
批前（maybe_replan 内、逐章契约补齐之后）跑一次 LLM 对账：
对比窗口内逐章细纲 vs 设定台账/金手指硬规则，**只修数字与事件上的不可满足
冲突，不改剧情走向**，把修正后的整行合并回 subline.md。修复可解释（notes
返回改了哪些章），失败降级不阻断写作。

依赖方向：workflows 层，可消费 core（SettingCanon）与 client（gateway）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rich.console import Console

from agent.core.infra.degrade import degrade

__all__ = ["ensure_window_feasibility"]

_SECTIONS = ("章节钩子设计", "情节点序列")


def ensure_window_feasibility(
    project_dir: str | Path,
    summary: str = "",
    llm: Any = None,
    console: Any = None,
) -> list[str]:
    """批前细纲可行性对账：细纲数字/事件 vs 冻结设定，自动修正不可满足行。

    Returns:
        修复说明列表（空 = 无冲突或对账失败降级）。
    """
    console = console or Console()
    project = Path(project_dir)
    sublines_dir = project / "sublines"
    if not sublines_dir.exists():
        return []

    win = _write_window(project)
    if win is None:
        return []

    notes: list[str] = []
    for sub_dir in sorted(sublines_dir.iterdir()):
        f = sub_dir / "subline.md"
        if not (sub_dir.is_dir() and f.exists()):
            continue
        try:
            content = f.read_text(encoding="utf-8")
        except OSError as e:
            degrade("plan_feasibility.read", f"sublines/{sub_dir.name} 读取失败", e)
            continue

        lines_for_window = _window_lines(content, win)
        if not lines_for_window:
            continue

        hard_rules = _hard_rules(project)
        if not hard_rules:
            continue  # 无冻结规则可比对（首本书早期），跳过

        try:
            if llm is None:
                from agent.client.gateway_adapter import create_gateway

                llm = create_gateway()
            fixes = _audit_lines(llm, lines_for_window, hard_rules, summary)
        except Exception as e:  # noqa: BLE001 - 增强项：失败降级不阻断
            degrade(
                "plan_feasibility.audit",
                f"sublines/{sub_dir.name} 可行性对账失败，本批沿用既有细纲",
                e,
            )
            continue
        if not fixes:
            continue

        merged, changed = _apply_fixes(content, fixes)
        if not changed:
            continue
        try:
            tmp = f.parent / (f.name + ".feas.tmp")
            tmp.write_text(merged, encoding="utf-8")
            tmp.replace(f)
        except OSError as e:
            degrade("plan_feasibility.write", f"sublines/{sub_dir.name} 修正写盘失败", e)
            continue
        chs = ",".join(f"ch{n:03d}" for n in sorted(changed))
        line = f"{sub_dir.name} 细纲可行性修正：{chs}（数字/事件与冻结设定对齐）"
        notes.append(line)
        console.print(f"[cyan]✓ {line}[/cyan]")
    return notes


# ---------------------------------------------------------------- 窗口与行收集


def _write_window(project: Path) -> tuple[int, int] | None:
    """写作窗口 [cur+1, cur+eval_window]；从 subline_contract 复用口径。"""
    try:
        from agent.workflows.pipeline.subline_contract import write_window

        return write_window(project)
    except Exception as e:  # noqa: BLE001
        degrade("plan_feasibility.window", "写作窗口推导失败，跳过可行性对账", e)
        return None


def _window_lines(content: str, win: tuple[int, int]) -> list[str]:
    """窗口内的逐章细纲行（两小节并集，保持文件顺序）。"""
    out: list[str] = []
    section = ""
    for ln in content.splitlines():
        s = ln.strip()
        if s.startswith("## "):
            section = s[3:].strip()
            continue
        m = re.match(r"^第(\d+)章：", s)
        if m and section in _SECTIONS:
            n = int(m.group(1))
            if win[0] <= n <= win[1]:
                out.append(s)
    return out


def _hard_rules(project: Path) -> str:
    """冻结设定的硬规则文本（设定台账 + world.md 金手指登记）。"""
    parts: list[str] = []
    try:
        from agent.core.story.setting_canon import SettingCanon

        canon = SettingCanon.load(project)
        rendered = canon.render_for_prompt(limit=25)
        if rendered:
            parts.append("【设定台账（已确立，禁止矛盾）】\n" + rendered)
    except Exception as e:  # noqa: BLE001
        degrade("plan_feasibility.canon", "设定台账读取失败", e)
    world = project / "world.md"
    if world.exists():
        try:
            wt = world.read_text(encoding="utf-8")
            m = re.search(r"##\s*金手指登记.*?(?=\n##\s|\Z)", wt, re.S)
            if m:
                parts.append("【金手指登记（冻结）】\n" + re.sub(r"\n{3,}", "\n\n", m.group(0))[:1200])
        except OSError:  # noqa: BLE001
            pass  # noqa: SILENT_DEGRADE reason=expected-skip - 新项目无 world.md 属预期状态，台账仍可对账
    return "\n\n".join(parts)


# ---------------------------------------------------------------- LLM 对账


_AUDIT_SYSTEM = (
    "你是长篇小说规划的连续性审计员。给你一个写作窗口内的逐章细纲（钩子设计行、"
    "情节点行）与冻结的硬性设定（设定台账/金手指登记）。任务：**只找并只修**细纲"
    "与硬规则在数字、数量、期限、能力使用频次上的不可满足冲突（例如细纲要求单日"
    "产量超过金手指上限、期限与既定事件对不上、同一事件两次发生）。"
    "禁止改变剧情走向、禁止增删情节点主题、禁止改写没有冲突的行。"
    "输出严格 JSON：{\"fixes\": [{\"chapter\": 章号, \"hook_line\": \"第N章：…整行或空\", "
    "\"plot_line\": \"第N章：…整行或空\"}]}；无冲突输出 {\"fixes\": []}。"
    "整行必须保持原行格式（以「第N章：」开头，字段用｜分隔，字段顺序不变）。"
)


def _audit_lines(llm: Any, lines: list[str], hard_rules: str, summary: str) -> list[dict[str, Any]]:
    """调 LLM 对账，返回 fixes 列表（解析失败抛异常由上层降级）。"""
    from agent.client.gateway_adapter import chat_utility_response
    from agent.utils import parse_llm_json

    user = (
        (f"【批级进展摘要（已发生事实）】\n{summary[:1500]}\n\n" if summary else "")
        + hard_rules
        + "\n\n【待审计的窗口细纲行】\n"
        + "\n".join(lines)
    )
    resp = chat_utility_response(
        llm,
        [
            {"role": "system", "content": _AUDIT_SYSTEM},
            {"role": "user", "content": user},
        ],
        max_tokens=4096,
        enable_thinking=False,
    )
    data = parse_llm_json(getattr(resp, "text", "") or "")
    fixes = data.get("fixes") if isinstance(data, dict) else None
    return [x for x in (fixes or []) if isinstance(x, dict)]


# ---------------------------------------------------------------- 行合并


def _apply_fixes(content: str, fixes: list[dict[str, Any]]) -> tuple[str, set[int]]:
    """把修正行按章号+小节替换回 subline.md；返回 (新内容, 改动的章号集合)。"""
    repl: dict[int, dict[str, str]] = {}
    for x in fixes:
        # 章号容错：LLM 可能返回 80 / "80" / "第80章" 三种形态
        m = re.search(r"\d+", str(x.get("chapter", "") or ""))
        if not m:
            continue
        n = int(m.group(0))
        entry = repl.setdefault(n, {})
        for key, field in (("hook_line", "章节钩子设计"), ("plot_line", "情节点序列")):
            v = str(x.get(key, "") or "").strip()
            if v and v.startswith(f"第{n}章："):
                entry[field] = v
    if not repl:
        return content, set()

    out_lines: list[str] = []
    section = ""
    changed: set[int] = set()
    for ln in content.splitlines():
        s = ln.strip()
        if s.startswith("## "):
            section = s[3:].strip()
            out_lines.append(ln)
            continue
        m = re.match(r"^第(\d+)章：", s)
        if m and section in _SECTIONS and int(m.group(1)) in repl:
            n = int(m.group(1))
            new_line = repl[n].get(section, "")
            if new_line and new_line != s:
                out_lines.append(new_line)
                changed.add(n)
                continue
        out_lines.append(ln)
    return "\n".join(out_lines), changed
