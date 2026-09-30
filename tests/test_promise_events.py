"""T4 承诺事件台账（长线一致性二期，20260930 登记）单测。"""

from pathlib import Path

from agent.core.continuity.ledger import ContinuityLedgerStore
from agent.core.continuity.models import (
    ContinuityHandoff,
    ContinuityLedger,
    ContinuityOpenLoop,
)
from agent.core.quality.book_checkup import check_promise_events


def _seed(project_dir: Path, loop_id: str, chapter: int, detail: str, status: str = "open") -> None:
    store = ContinuityLedgerStore(project_dir)
    store.ledger = ContinuityLedger(
        open_loops=[
            ContinuityOpenLoop(
                loop_id=loop_id, kind="promise", status=status,
                detail=detail, source_commit_id=f"ch{chapter:03d}",
            )
        ],
        handoffs=[ContinuityHandoff(chapter=chapter, summary="s", source_commit_id=f"ch{chapter:03d}")],
    )
    store.save()


def test_overdue_promise_flagged(tmp_path):
    (tmp_path / "chapters").mkdir()
    _seed(tmp_path, "P-ch45-核查", 45, "陈长老巳时末亲自核查工坊")
    chapters = [{"chapter": c} for c in range(45, 60)]  # 15 章后
    rep = check_promise_events(tmp_path, chapters, grace=10)
    assert len(rep["violations"]) == 1
    assert "核查" in rep["violations"][0]["detail"]


def test_recent_promise_not_flagged(tmp_path):
    (tmp_path / "chapters").mkdir()
    _seed(tmp_path, "P-ch97-矿洞", 97, "独自去西北废弃矿洞")
    chapters = [{"chapter": c} for c in range(97, 104)]  # 7 章，宽限内
    rep = check_promise_events(tmp_path, chapters, grace=10)
    assert rep["violations"] == []


def test_resolved_promise_not_flagged(tmp_path):
    (tmp_path / "chapters").mkdir()
    _seed(tmp_path, "P-ch25-观世客", 25, "明天辰时之后再找我", status="resolved")
    chapters = [{"chapter": c} for c in range(25, 45)]
    rep = check_promise_events(tmp_path, chapters, grace=10)
    assert rep["violations"] == []


def test_advisory_not_in_passed(tmp_path):
    """T4 验收：promise_events 是 advisory，逾期不计入 issues/passed。"""
    (tmp_path / "chapters").mkdir()
    _seed(tmp_path, "P-x", 1, "某承诺")
    chapters = [{"chapter": c} for c in range(1, 30)]
    from agent.core.quality.book_checkup import run_book_checkup

    # 需要 chapters/chNNN.md 文件供 _load_chapters
    for c in range(1, 30):
        f = tmp_path / "chapters" / f"ch{c:03d}.md"
        f.write_text("---\nchapter: " + str(c) + "\n---\n\n# 第" + str(c) + "章\n\n正文。", encoding="utf-8")
    rep = run_book_checkup(tmp_path)
    assert rep["success"]
    assert rep["advisories"], "逾期承诺应出现在 advisories"
    assert not any(i["metric"] == "promise_events" for i in rep["issues"])
