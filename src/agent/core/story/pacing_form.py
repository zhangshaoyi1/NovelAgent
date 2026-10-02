"""节奏形态审计（登记单 20261001_信任链与叙事上限六项能力·子项 5）

背景
----
章级强度档位（``chapter_contract.PACE_TIERS``）已三端同源（规划登记 / 写手注入 /
评委参照系），但只保证"每章各归其档"，不保证**跨章形态**——「该高潮的时候高潮、
该平淡的时候平淡」的"该"字没有机制承载。本模块把节奏感的可机制化部分收成
确定性判据，在**批前**对即将写作窗口的逐章档位序列做形态审计。

判据（全部确定性、零 LLM，纯函数可测）
--------------------------------------
- **R1 平推**：连续同档位 >3 章（放松档 >2 章）——连平弃读、连高疲劳；
- **R2 无回落**：「高潮」档的下一章仍是「高潮」或「推进」——高潮失效；
- **R3 跳变**：跳入「高潮」档且序位差 ≥2（如 日常→高潮、垫片→高潮）——缺铺垫的爆发是假爆发；一般性爬坡与向下回落不判。

动作强度（纪律 #13/#20：闸门强度须与证据匹配）
----------------------------------------------
第一期=**批前告警 + 留痕**（advisory）：判据虽确定性，但"打回给谁修"缺承载
（逐章档位由 M3/契约补齐产出，无重排器），硬 BLOCK 会一拦全冻。待真实项目
分位标定后（登记单目标线）升级为计划期 BLOCK。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.core.story.chapter_contract import (
    PACE_TIER_BY_NAME,
    PACE_TIER_NAMES,
    pace_tier_of,
)


@dataclass(frozen=True)
class PacingFormFinding:
    """一条形态审计发现。"""

    rule: str        # R1 平推 / R2 无回落 / R3 跳变
    chapter: int     # 锚定章号（窗口内）
    message: str
    severity: str = "warn"  # 第一期恒 warn；标定后 R2/R3 升 block


def _rank(tier: str) -> int:
    """档位序位（张力降序，0=最高），未登记档位返回 -1（不参与跳变判定）。"""
    try:
        return PACE_TIER_NAMES.index(tier)
    except ValueError:
        return -1


def audit_tier_form(tiers: dict[int, str]) -> list[PacingFormFinding]:
    """对「章号 → 档位名」序列做形态审计（纯函数）。

    Args:
        tiers: 即将写作窗口内的逐章档位（未标注档位的章不参与判定）。
    """
    chapters = sorted(n for n, t in tiers.items() if t in PACE_TIER_BY_NAME)
    findings: list[PacingFormFinding] = []
    if len(chapters) < 2:
        return findings

    # R1 平推：连续同档位（放松档阈值更严）
    run_start = chapters[0]
    run_tier = tiers[chapters[0]]
    prev = chapters[0]
    seq = chapters + [chapters[-1] + 10**6]  # 哨兵触发收尾判定
    for ch in seq[1:]:
        t = tiers.get(ch)
        if t == run_tier and ch == prev + 1:
            prev = ch
            continue
        length = prev - run_start + 1
        tier_def = PACE_TIER_BY_NAME.get(run_tier)
        limit = 2 if (tier_def is not None and tier_def.relaxed) else 3
        if length > limit:
            findings.append(
                PacingFormFinding(
                    rule="R1 平推",
                    chapter=run_start,
                    message=(
                        f"第 {run_start}-{prev} 章连续 {length} 章同为「{run_tier}」档"
                        f"（上限 {limit} 章）——"
                        + ("连排放松章易成注水段" if (tier_def and tier_def.relaxed) else "节奏无起伏")
                        + "，建议错开一档。"
                    ),
                )
            )
        run_start, run_tier, prev = ch, t or "", ch

    # R2 / R3：相邻章检查
    for a, b in zip(chapters, chapters[1:]):
        if b != a + 1:
            continue  # 序列有洞（跨支线/未标注）不判相邻关系
        ta, tb = tiers[a], tiers[b]
        ra, rb = _rank(ta), _rank(tb)
        if ra < 0 or rb < 0:
            continue
        # R2 无回落：高潮档的下一章不得仍是高潮/推进（须垫片/日常余韵）
        if ta == "高潮" and tb in ("高潮", "推进"):
            findings.append(
                PacingFormFinding(
                    rule="R2 无回落",
                    chapter=b,
                    message=(
                        f"第 {a} 章高潮后第 {b} 章紧接「{tb}」档——高潮后应有 "
                        f"1-2 章垫片/日常回落（余韵/缓冲），否则爆点失效。"
                    ),
                )
            )
        # R3 跳变：**跳入高潮**且序位差 ≥2（如 日常(3)→高潮(0)、垫片(2)→高潮(0)）
        # ——缺铺垫的爆发是假爆发。一般性爬坡（日常→推进，差 2）是合法的缓升；
        # 向下跳（高潮→日常回落）是 R2 要求的合法形态。均不判。
        if tb == "高潮" and (ra - rb) >= 2:
            findings.append(
                PacingFormFinding(
                    rule="R3 跳变",
                    chapter=b,
                    message=(
                        f"第 {a} 章「{ta}」直接跳第 {b} 章「{tb}」（序位差 {abs(ra - rb)}）"
                        "——缺铺垫的爆发是假爆发，建议插入过渡档。"
                    ),
                )
            )
    return findings


def load_window_tiers(project_dir: str | Path, window: tuple[int, int]) -> dict[int, str]:
    """装配写作窗口的逐章档位（扫 sublines/*/subline.md，三端共用 pace_tier_of）。

    未标注档位的章不出现在结果里（审计只判有据的章）。
    """
    project_path = Path(project_dir)
    lo, hi = int(window[0]), int(window[1])
    tiers: dict[int, str] = {}
    sublines_dir = project_path / "sublines"
    if not sublines_dir.exists():
        return tiers
    contents: list[str] = []
    for sub_dir in sorted(sublines_dir.iterdir()):
        f = sub_dir / "subline.md"
        if sub_dir.is_dir() and f.exists():
            try:
                contents.append(f.read_text(encoding="utf-8"))
            except OSError as e:
                from agent.core.infra.degrade import degrade

                degrade("pacing_form.load", f"{f} 读取失败，该支线不参与档位装配", e)
                continue
    for ch in range(lo, hi + 1):
        for md in contents:
            tier = pace_tier_of(md, ch)
            if tier:
                tiers[ch] = tier
                break
    return tiers


def format_findings(findings: list[PacingFormFinding], limit: int = 6) -> str:
    """审计发现渲染为人读文本（console/检查点卡共用）。"""
    if not findings:
        return ""
    lines = [f"- [{f.rule}/第{f.chapter}章] {f.message}" for f in findings[:limit]]
    more = f"\n- （另有 {len(findings) - limit} 条）" if len(findings) > limit else ""
    return "\n".join(lines) + more
