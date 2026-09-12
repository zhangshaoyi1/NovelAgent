"""书级台账（2026-09-12，用户对成书质量的批评驱动）

背景
----
两本成书的读者批评暴露出：现有质检只在"这一章像不像一章"的粒度闭环，
没有在"这本书是不是一本书"的粒度闭环。三本书级信号全部缺失：

1. **主线契约**（theme promise）——《五灵破》192 章无主题牵引、复印机式
   推进，正因为系统里没有"这本书承诺讲什么"这个一等公民（《无灵》的
   "凡骨 vs 天道"贯穿全书是模板偶然带来的，不是机制保证）；
2. **登场连续性**——《无灵》ch2 金手指以「残魂的声音**再次**响起」登场，
   而它此前从未被引入：初次登场却用"再次"口吻，等于默认读者自带前情；
3. **质量基线**——两本书都在个别章节（五灵破 ch191 / 无灵 ch350）突然写出
   远超平均水准的文笔，说明每章独立判 pass/fail 时全书质量漂移不可见。

设计
----
- 状态落 ``.state/memory/*.json``（eval_lessons 同位先例），**不碰运行态主状态
  文件**（棘轮红线：直写面冻结，所有权在 state_machine）；
- 纯标准库、无 LLM；所有写路径 try/except + ``logger.warning`` 显性降级
  （degrade 约线的 logger 分支），绝不静默、绝不阻断写作流水线；
- 主线契约首次使用时从 ``plan.json.brief`` 播种（PlanStore 的数据，
  这里只读不写，避免双写入口）。

依赖方向：本模块属 agent.core.quality（领域层），仅依赖标准库。
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent.core.quality.book_ledger")

_MEMORY_REL = Path(".state") / "memory"
_CONTRACT_REL = _MEMORY_REL / "book_contract.json"
_DEBUT_REL = _MEMORY_REL / "debut_registry.json"
_BASELINE_REL = _MEMORY_REL / "quality_baseline.json"

# 登场口吻误用检测：「X……再次/又一次/依旧/仍旧」且 X 从未登场登记。
# 窄口径（同人句内、30 字窗口），宁漏勿误——命中的是确定性错误口吻。
_DEBUT_ECHO_RE = re.compile(
    r"([\u4e00-\u9fff]{2,8})[^\n。！？]{0,30}?(?:再次|又一次|依旧|仍旧|照旧)"
)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:  # noqa: BLE001
        logger.warning("[degrade] book_ledger.read_json %s：%r", path, e)
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(path)
    except Exception as e:  # noqa: BLE001
        logger.warning("[degrade] book_ledger.write_json %s：%r", path, e)


# ============================================================
# 1) 主线契约（theme promise）
# ============================================================
def load_theme_contract(project_dir: str | Path) -> dict[str, Any] | None:
    """读主线契约；无则从 plan.json.brief 播种（只读 PlanStore 数据，不回写）。"""
    root = Path(project_dir)
    contract = _read_json(root / _CONTRACT_REL)
    if contract and contract.get("promise"):
        return contract
    try:
        plan = _read_json(root / ".state" / "plan.json")
        brief = str((plan or {}).get("brief", "") or "").strip()
        if not brief:
            return None
    except Exception as e:  # noqa: BLE001
        logger.warning("[degrade] book_ledger.seed_from_plan：%r", e)
        return None
    contract = {
        "promise": brief[:300],
        "title": str((plan or {}).get("title", "") or ""),
        "seeded_from": "plan.json",
        "seeded_at": time.time(),
    }
    _write_json(root / _CONTRACT_REL, contract)
    return contract


def theme_contract_text(project_dir: str | Path, chapter_num: int) -> str:
    """渲染注入 Writer 任务的主线契约段；无契约返回空串（调用方跳过注入）。"""
    contract = load_theme_contract(project_dir)
    if not contract:
        return ""
    return (
        f"# 主线契约（全书承诺，第 {chapter_num} 章必须推进或显式经营它）\n"
        f"{contract.get('promise', '')}\n"
        "本章的核心冲突、转折或人物变化至少要有一处直接服务上述主线承诺；"
        "若本章为主经营支线，也须以一句以上的情节显式勾连主线，禁止完全脱钩。"
    )


# ============================================================
# 2) 登场连续性（初次登场 ≠ 再次口吻）
# ============================================================
def _known_entities(project_dir: Path) -> list[str]:
    """实体名清单 = characters/*.md 文件名（stem）；不可得时返回空。"""
    names: list[str] = []
    try:
        cdir = project_dir / "characters"
        if cdir.is_dir():
            for p in sorted(cdir.glob("*.md")):
                stem = p.stem.strip()
                if len(stem) >= 2 and stem not in names:
                    names.append(stem)
    except Exception as e:  # noqa: BLE001
        logger.warning("[degrade] book_ledger.known_entities：%r", e)
    return names


def check_debut_echo(
    project_dir: str | Path, text: str, chapter_num: int
) -> list[dict[str, str]]:
    """检查已登记实体是否以「再次/依旧」口吻登场且无首次登场记录。

    返回 warning 级问题列表（窄口径，不做 blocking——误报的代价是重写，
    而本检查的语义证据不足以支撑不可逆处置，走 ESCALATE 哲学）。
    """
    root = Path(project_dir)
    body = (text or "").strip()
    if not body:
        return []
    registry = _read_json(root / _DEBUT_REL) or {}
    seen: dict[str, Any] = registry.get("entities") or {}
    issues: list[dict[str, str]] = []
    for name in _known_entities(root):
        if name in seen or name not in body:
            continue
        for m in _DEBUT_ECHO_RE.finditer(body):
            if m.group(1) == name or name in m.group(0):
                issues.append(
                    {
                        "rule_id": "debut_continuity",
                        "severity": "warning",
                        "description": (
                            f"实体「{name}」此前从未登场登记，本章却以"
                            f"「{m.group(0)[:40]}」的『再次/依旧』口吻出现"
                            f"（第 {chapter_num} 章）——读者没有前情提要是"
                            "接不住的。请补写引入（这是谁、如何认识）或改掉"
                            "『再次』措辞。"
                        ),
                    }
                )
                break
    return issues


def record_debuts(project_dir: str | Path, text: str, chapter_num: int) -> None:
    """把本章出现过的实体登记进登场表（已有记录的实体刷新 last_chapter）。"""
    root = Path(project_dir)
    body = (text or "").strip()
    if not body:
        return
    registry = _read_json(root / _DEBUT_REL) or {}
    entities: dict[str, Any] = dict(registry.get("entities") or {})
    changed = False
    for name in _known_entities(root):
        if name not in body:
            continue
        if name not in entities:
            entities[name] = {"first_chapter": int(chapter_num)}
            changed = True
        else:
            entities[name]["last_chapter"] = int(chapter_num)
    if changed or entities:
        _write_json(
            root / _DEBUT_REL,
            {"entities": entities, "updated_at": time.time()},
        )


# ============================================================
# 3) 质量基线（滑动窗口通过率）
# ============================================================
def record_quality(
    project_dir: str | Path, chapter_num: int, passed: bool, revisions: int
) -> None:
    """记录单章质检结果（滑窗基线数据源）。失败显性降级，不阻断。"""
    root = Path(project_dir)
    data = _read_json(root / _BASELINE_REL) or {}
    records = list(data.get("records") or [])
    records.append(
        {
            "ch": int(chapter_num),
            "pass": bool(passed),
            "revisions": int(revisions or 0),
            "at": time.time(),
        }
    )
    _write_json(
        root / _BASELINE_REL, {"records": records[-500:], "updated_at": time.time()}
    )


def baseline_drift_text(
    project_dir: str | Path, window: int = 10, min_pass_rate: float = 0.5
) -> str:
    """最近 ``window`` 章质检通过率低于 ``min_pass_rate`` 时返回人话告警段。

    这是「质量彩票」问题的书级信号：单章独立判过/挂时，全书系统性劣化
    不可见；本函数让 Writer 在写下一章前看到漂移事实。健康返回空串。
    """
    data = _read_json(Path(project_dir) / _BASELINE_REL)
    records = list((data or {}).get("records") or [])
    recent = [r for r in records[-window:] if isinstance(r, dict)]
    if len(recent) < window:
        return ""  # 窗口未满，样本不足不做判断
    passed = sum(1 for r in recent if r.get("pass"))
    rate = passed / len(recent)
    if rate >= min_pass_rate:
        return ""
    avg_rev = sum(int(r.get("revisions", 0) or 0) for r in recent) / len(recent)
    return (
        f"# 书级质量漂移告警（连续性信号）\n"
        f"最近 {len(recent)} 章质检通过率仅 {rate:.0%}"
        f"（{passed}/{len(recent)}），平均每章打回 {avg_rev:.1f} 次——"
        "全书质量正在系统性下滑，不是单章偶发。本章请回到最近一次质检一次通过"
        "的章节的写法（可在 .state/memory/quality_baseline.json 定位），"
        "优先恢复稳定性而非追求单章亮点。"
    )
