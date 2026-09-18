"""规划一致性守护（缺口 A/C，2026-09-06）。

背景（novels/无灵 实证，见 .agents/notes/proposed/architecture/
2026-09-06-plot-stall-root-causes-and-fix-plan.md）：
1. ``mainline.json`` 的 horizon/subline_share 由 world.md 体量机械估算生成，
   从未与 ``plan.json.total_chapters`` 校验 → S01 cap=480 在 350 章的书里
   永不触发切线，全书被锁死在首条支线（剧情原地打转的结构性根因）。
2. subline.md 缺「情节点序列 / 章节钩子设计」时 M5 静默注入空串，
   每章只剩「接上一章结尾续写」，母题循环无机制可纠。
3. 持久化顺序「先章节文件后进度」，崩溃后恢复按 ``total_written+1`` 盲写 →
   覆盖重写已存在章节（ch191 重演 ch190 实证）。

本模块确立 **plan.json 为全书规模唯一权威**，并承担写前对账：
- 主线预算（派生物）加载时按 plan 总章数自动对齐（horizon 钳制 + 份额等比缩放）；
- 主角路线节点区间越界 → 告警（用户文档，不自动改）；
- subline 剧情源缺失 → **fail-fast**（确定性不变量破坏，降级只会产出废稿）；
- 章节目录 vs ``total_written`` 对账，文件领先进度则补记进度（采纳文件，消除覆盖重写）。

原则：LLM 失败可降级（G3）；**确定性状态不一致必须响**（缺口 C）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional



from agent.core.story.mainline_align import align_mainline_to_plan  # R6：下沉 core（workflows 再导出）

def load_plan_total(project_dir: str | Path) -> Optional[int]:
    """读 plan.json 的 total_chapters（全书规模唯一权威）；缺失/非法返回 None。

    P2（2026-09-07）：委托 core.progress.book_total（唯一底层实现），本函数
    保留为 workflows 层的既有公共 API。
    """
    from agent.core.progress import book_total

    return book_total(project_dir)


def reconcile_chapters_with_progress(
    project_dir: str | Path, console: Any = None
) -> list[str]:
    """对账 chapters/ 目录与 state.json 的 total_written（缺口 C 恢复对账）。

    「先章节文件后进度」的写序在两步之间崩溃会留下『文件已存在、进度落后』，
    恢复后按 total_written+1 盲写会**覆盖重写**已有章节（ch191 实证）。
    本方法在写前把进度补记到章节文件的真实水位（采纳文件为准）。

    Returns:
        修复说明列表（一致则为空）。
    """
    project_dir = Path(project_dir)
    chapters_dir = project_dir / "chapters"
    if not chapters_dir.exists():
        return []
    max_ch = 0
    for f in chapters_dir.glob("ch*.md"):
        m = re.fullmatch(r"ch(\d+)\.md", f.name)
        if m:
            max_ch = max(max_ch, int(m.group(1)))
    if max_ch <= 0:
        return []

    state_file = project_dir / ".state" / "state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 无状态文件时无从对账
        return []
    progress = state.get("progress") or {}
    try:
        written = int(progress.get("total_written") or 0)
    except (TypeError, ValueError):
        written = 0  # noqa: SILENT_DEGRADE
    if written >= max_ch:
        return []

    notes = [
        f"chapters/ 目录实际已有 {max_ch} 章，progress.total_written={written}，"
        "已按文件水位补记进度（防止恢复后覆盖重写已有章节）"
    ]
    progress["total_written"] = max_ch
    progress["current_chapter"] = max_ch
    state["progress"] = progress
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        notes.append("state.json 进度补记写盘失败")  # noqa: SILENT_DEGRADE
    if console is not None:
        for n in notes:
            console.print(f"[yellow]⚠ 恢复对账：{n}[/yellow]")
    return notes


_PLOT_SOURCE_SECTIONS = ("情节点序列", "章节钩子设计")

#: 开篇窗口（章）—— outline.md 要求规划者逐章给「本支线开篇前 N 章」的章级契约
_OPENING_WINDOW = 20

#: 开篇窗口逐章契约的最低覆盖率（不足只**告警**：分批写作的合法中间态）
_COVERAGE_MIN = 0.9

#: 阶段级供给的显式声明词（豁免路径要求出现，否则连阶段供给都没落地）
_STAGE_DECLARATION = "按阶段"

#: 豁免留痕文件：显式豁免必须落盘可审计。
#: ★ 口子必须「只挡历史、不影响当前」，且**不得静默放行**——
#:   否则口子本身就成了缺陷的新入口（本项目纪律：为兼容历史开的口子必须可审计）。
_WAIVER_LEDGER = Path(".state") / "plan_gate_waivers.jsonl"


def _subline_range(content: str) -> tuple[int, int]:
    """支线的章节区间 (lo, hi)：取「剧集压力曲线」表区间的最小起点/最大上界。

    解析不出返回 ``(0, 0)`` —— 调用方据此**不做窗口过滤**（保持严格）。
    """
    spans = [
        (int(a), int(b))
        for a, b in re.findall(r"\|\s*(\d+)\s*-\s*(\d+)\s*\|", content)
    ]
    if not spans:
        return (0, 0)
    return (min(a for a, _ in spans), max(b for _, b in spans))


def _subline_span(content: str) -> int:
    """支线覆盖的章数（区间上界）；解析不出返回 0。"""
    return _subline_range(content)[1]


def _write_window(project_dir: str | Path, *, size: int = _OPENING_WINDOW) -> tuple[int, int] | None:
    """接下来要写的章节窗口 ``(起, 止)``；读不到进度返回 ``None``。

    用途：把判据 2（章级粒度）的**作用域**收准 —— 尚未进入写作窗口的支线
    （如第 181-420 章的 S02）不该因为"阶段级细纲"把整个项目挡住：
    判据必须只约束它守护的那一段（否则一条远期支线就会冻住全书）。
    """
    state_file = Path(project_dir) / ".state" / "state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 无进度文件时不做窗口过滤
        return None
    progress = state.get("progress") or {}
    try:
        cur = int(progress.get("total_written") or progress.get("current_chapter") or 0)
    except (TypeError, ValueError):
        cur = 0  # noqa: SILENT_DEGRADE
    return (cur + 1, cur + size)


def _chapter_numbers(content: str) -> set[int]:
    """逐章契约行覆盖的**章号集合**（与章级契约真源 ``chapter_contract`` 同源）。

    ⚠ 必须返回**集合**而非计数：判据 2 的作用域是「即将写的窗口」，
    只有章号才能与窗口求交（全局计数无法回答"窗口内有没有行"）。
    """
    from agent.core.story.chapter_contract import extract_section

    nums: set[int] = set()
    for name in _PLOT_SOURCE_SECTIONS:
        section = extract_section(content, name)
        nums.update(int(n) for n in re.findall(r"第\s*(\d+)\s*章\s*[：:]", section))
    return nums


def _chapter_line_count(content: str) -> int:
    """逐章契约行覆盖的**去重章号数**（全文件口径；窗口口径见 ``_chapter_numbers``）。"""
    return len(_chapter_numbers(content))


def _has_stage_lines(content: str) -> bool:
    """是否给出**阶段级**情节点/钩子（豁免路径要求给出——连阶段供给都没有时不给豁免）。"""
    from agent.core.story.chapter_contract import extract_section

    for name in _PLOT_SOURCE_SECTIONS:
        section = extract_section(content, name)
        if section and re.search(r"(铺垫|冲突|高潮|舒缓)\s*阶段", section):
            return True
    return False


def _record_waiver(project_dir: str | Path, subline_id: str, detail: str) -> bool:
    """显式豁免落盘留痕。Returns: 是否落盘成功（失败 ⇒ 调用方必须视为未豁免）。"""
    from datetime import datetime, timezone

    path = Path(project_dir) / _WAIVER_LEDGER
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "subline": subline_id,
                        "gate": "stage_level_exempt",
                        "detail": detail,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        return True
    except Exception:  # noqa: BLE001 - 由调用方转为致命（未留痕的豁免不得放行）
        return False


def check_subline_plot_source(
    project_dir: str | Path,
    *,
    allow_stage_level: bool = False,
    console: Any = None,
) -> list[str]:
    """校验每条 subline.md 的**章级**剧情源（确定性不变量 → fail-fast）。

    ⚠ 2026-09-18 校正：此前只判「两小节**存在且非空**」⇒ 一份 180 章只给 4 行
    阶段模板的支线也「通过」⇒ 09-18 章级意图缺位缺陷**没被启动闸拦住**，
    一路走到写手才炸（登记单 ``20260918_章级意图缺位_细纲按阶段供给.md``）。
    判据必须检查**粒度**，不能只检查"有没有这个段"。

    判据分层（强度与修复手段配对）：
      0. **作用域＝即将写的窗口**（``cur+1 .. cur+20``）。支线区间与窗口不相交
         ⇒ 只告警（远期支线不许冻住全书）；判据 2/3 的**分母分子都只算窗口内**。
      1. 「情节点序列 / 章节钩子设计」至少一处存在且非空 —— 缺则 **fail-fast**
         （确定性不变量；修复手段＝重跑大纲/支线生成）
      2. **窗口内**含逐章契约行（``第N章：…``）—— 缺则 **fail-fast**
         （阶段模板 ≠ 章级供给：写手拿到的"本章意图"可套用到窗口内任意一章 ⇒
         只能自行编造 ⇒ 同质内容 ⇒ 回退重写死循环；修复手段＝用 v4 提示词重跑）
         ⚠ 判定量必须是"**窗口内**的行数"而不是"全文件的行数"：只判"有没有"
         而不判"窗口内够不够"＝形同虚设（2026-09-18 两度踩坑，见下方 §判据量）。
      3. 窗口内逐章契约覆盖率 ≥ 90% —— 不足只**告警**
         （分批写作的合法中间态，不应阻断）

    豁免：``allow_stage_level=True`` 时判据 2 降为告警，但要求 subline **显式给出
    阶段行**，且豁免**必须落盘留痕**（``.state/plan_gate_waivers.jsonl``）；
    落痕失败即视为未豁免（静默放行比不放行更危险）。

    §判据量（本函数最贵的一条教训）
    ------------------------------
    同一个缺陷类（"只判有没有、不判够不够"）在本函数内出现过**两次**：
    第一次 只判「段存在」⇒ 180 章只有 4 行阶段模板也通过；
    第二次（初版修复）只判「全文件有逐章行」⇒ 1-5 章逐章、6 章起阶段模板
    照样通过，而窗口 6-25 内一条逐章行都没有。
    两处都靠**端到端在真实项目上跑闸**才暴露，单元测试当时全绿。
    ⇒ 教训：凡闸门，判据量必须与它守护的**作用域**同口径；且**必须在真实
    项目上验收**，token/代码级单测不能替代。

    Returns:
        致命错误列表（空 = 通过）。
    """
    errors: list[str] = []
    warnings: list[str] = []

    def _emit(msg: str, *, fatal: bool) -> None:
        if fatal:
            errors.append(msg)
        else:
            warnings.append(msg)

    sublines_dir = Path(project_dir) / "sublines"
    if not sublines_dir.exists():
        return []  # 无支线结构的项目走原有流程，不在此拦截
    for sub_dir in sorted(sublines_dir.iterdir()):
        f = sub_dir / "subline.md"
        if sub_dir.is_dir() and not f.exists():
            errors.append(f"sublines/{sub_dir.name}/subline.md 缺失")
            continue
        try:
            content = f.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            errors.append(f"sublines/{sub_dir.name}/subline.md 读取失败")
            continue  # noqa: SILENT_DEGRADE

        # ---- 判据 1：剧情源段存在 ----
        if not any(_has_section(content, s) for s in _PLOT_SOURCE_SECTIONS):
            errors.append(
                f"sublines/{sub_dir.name}/subline.md 缺少剧情源段落"
                f"（「{'」或「'.join(_PLOT_SOURCE_SECTIONS)}」至少其一）。"
                "缺失时每章只能接上一章结尾自由续写，会导致剧情原地打转；"
                "请先运行大纲/支线生成补全后再写作。"
            )
            continue

        # ---- 判据 2：章级粒度（阶段模板不算；**作用域＝即将写的窗口**） ----
        # ⚠ 2026-09-18 二次校正（端到端验收发现）：初版判据量是**全文件的**
        #   `chapter_lines == 0`。于是"S01 只把第 1-5 章写成逐章、第 6 章起全是阶段模板"
        #   这种形态**照样通过** —— 而即将写的窗口（6-25）内一条逐章行都没有，
        #   正是 09-18 P0 的成因。**判据只判"有没有"、不判"窗口内够不够"＝形同虚设**，
        #   所以判定量必须收到窗口上（同一缺陷类，同一个修复里踩了两次）。
        win = _write_window(project_dir)
        nums = _chapter_numbers(content)
        lo, hi = _subline_range(content)

        # 2a) 支线区间与写作窗口不相交 ⇒ 远期支线，只告警
        #     （否则一条 181-420 章的支线会因为"阶段级细纲"冻住整本书）
        if win is not None and lo and (hi < win[0] or lo > win[1]):
            _emit(
                f"sublines/{sub_dir.name}/subline.md 的章级契约不属于本次写作窗口 "
                f"{win[0]}-{win[1]}（该支线区间 {lo}-{hi}），本次只告警；"
                "进入该支线窗口前必须补齐逐章契约。",
                fatal=False,
            )
            continue

        # 2b) 判定量收准到窗口：窗口内被逐章契约行覆盖的章号集合
        if win is None:
            scope = nums
            scope_desc = "全支线"
            span = _subline_span(content)
            denom = min(_OPENING_WINDOW, span) if span > 0 else _OPENING_WINDOW
        else:
            scope_lo, scope_hi = win
            if lo:  # 支线区间已知 ⇒ 与窗口取交集（避免把支线覆盖不到的章算进分母）
                scope_lo = max(scope_lo, lo)
                scope_hi = min(scope_hi, hi)
            scope = {n for n in nums if scope_lo <= n <= scope_hi}
            scope_desc = f"写作窗口 {scope_lo}-{scope_hi}"
            denom = scope_hi - scope_lo + 1

        if not scope:
            msg = (
                f"sublines/{sub_dir.name}/subline.md 在**{scope_desc}**内没有逐章契约行"
                f"（『第N章：…』格式，全支线现有 {len(nums)} 行）。"
                f"窗口内只有阶段级模板 ⇒ 模板可套用到窗口内任意一章 ⇒ 写手只能"
                f"自行编造本章内容 ⇒ 同质/注水 ⇒ 评委判不合格 ⇒ 回退重写时输入不变"
                f" ⇒ 整窗销毁-重写死循环（2026-09-18 实证：1h51m / 净推进 0 章）。"
            )
            if not allow_stage_level:
                errors.append(
                    msg
                    + "请用 v4 大纲提示词重跑支线生成（逐章给开篇窗口）；"
                    "历史项目确需按阶段供给时，显式加 --allow-stage-level 豁免"
                    "（会落盘 .state/plan_gate_waivers.jsonl 留痕）。"
                )
                continue
            if not _has_stage_lines(content):
                errors.append(
                    f"sublines/{sub_dir.name}/subline.md 申请阶段级豁免，但连阶段级"
                    "情节点/钩子行都没有（『铺垫阶段：…』等）—— 豁免不成立。"
                )
                continue
            if not _record_waiver(
                project_dir,
                sub_dir.name,
                f"stage_level_exempt: 显式豁免，{scope_desc}内仅阶段级细纲",
            ):
                errors.append(
                    f"sublines/{sub_dir.name}/subline.md 阶段级豁免**留痕落盘失败**"
                    f"（{_WAIVER_LEDGER}）—— 未留痕的豁免一律视为未豁免"
                    "（静默放行比不放行更危险）。"
                )
                continue
            _emit(msg + "（已按 --allow-stage-level 显式豁免并留痕）", fatal=False)
            continue

        # ---- 判据 3：写作窗口内逐章契约覆盖率（不足只告警） ----
        required = max(1, int(denom * _COVERAGE_MIN))
        if len(scope) < required:
            _emit(
                f"sublines/{sub_dir.name}/subline.md 在{scope_desc}内逐章契约覆盖 "
                f"{len(scope)} 章，低于 {denom} 章的 {int(_COVERAGE_MIN * 100)}%"
                f"（{required} 章）。分批写作时属正常中间态；"
                "若已定稿请补齐写作窗口的逐章契约。",
                fatal=False,
            )

    for w in warnings:
        if console is not None:
            console.print(f"[yellow]⚠ 规划告警：{w}[/yellow]")
    return errors


def _has_section(content: str, section_name: str) -> bool:
    """判断 markdown 是否存在非空 `## <section_name>` 段（与 mainline._extract_section 同型）。"""
    pattern = rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\Z)"
    m = re.search(pattern, content, re.DOTALL)
    return bool(m and m.group(1).strip())


def check_route_ranges(project_dir: str | Path) -> list[str]:
    """校验 protagonist_route.md 节点章节区间与 plan 总章数（用户文档 → 仅告警）。

    节点起点越过 plan 总章数 → 该节点整书不可达（无灵 N04=351-400 vs plan 350 实证）。
    路线文件是用户可编辑文档，不自动改写，只显著告警。
    """
    total = load_plan_total(project_dir)
    if total is None:
        return []
    f = Path(project_dir) / "protagonist_route.md"
    if not f.exists():
        return []
    try:
        content = f.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return []
    warnings: list[str] = []
    for m in re.finditer(r"^\s*-\s*\*\*章节范围\*\*[:：]\s*(\d+)\s*-\s*(\d+)", content, re.MULTILINE):
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > total:
            warnings.append(
                f"protagonist_route.md 存在起点越界节点（章节范围 {lo}-{hi}，"
                f"plan 总章数 {total}）：该节点整书不可达，"
                "请重排路线节点或调整 plan.json total_chapters"
            )
    return warnings


def prepare_for_write(
    project_dir: str | Path,
    console: Any = None,
    *,
    allow_stage_level: bool = False,
) -> list[str]:
    """写前统一入口：对齐派生物 → 恢复对账 → 不变量校验。

    Args:
        allow_stage_level: 显式豁免「细纲必须逐章供给」前置闸（历史项目按压力
            阶段给细纲时使用）。豁免会落盘留痕；不传则默认**严格**。

    Returns:
        致命错误列表（空 = 可以开写）。非致命问题（对齐/告警）直接打印。
    """
    align_mainline_to_plan(project_dir, console=console)
    reconcile_chapters_with_progress(project_dir, console=console)
    for w in check_route_ranges(project_dir):
        if console is not None:
            console.print(f"[yellow]⚠ 规划告警：{w}[/yellow]")
        else:
            print(f"⚠ 规划告警：{w}")
    return check_subline_plot_source(
        project_dir, allow_stage_level=allow_stage_level, console=console
    )
