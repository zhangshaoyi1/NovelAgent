"""章节变更 → 派生状态失效总线（2026-09-15）

背景（架构复盘第 3 类结构性根因）
--------------------------------
单项目 ``.state/`` 有 25+ 个派生文件，其中带章节语义的至少 10 个
（``chapter_fingerprints`` / ``chapter_quality_flags`` / ``continuity/`` /
``foreshadow_sync`` / ``mainline`` / ``golden_score_cache`` / ``pacing`` /
``issue_debts`` / ``foresight`` / ``payoff_script`` …），而回滚
（``m10_rollback.rollback_to_chapter``）原先只同步了 **2** 个——且都是**逐次手工
追加**的（``_sync_rag_index`` 2026-09-09、``_sync_fingerprints`` 2026-09-15）。

这是同一病灶的第三次显影：**章节内容变了，由它派生的索引/台账/水位线没有随之失效。**

- 辅助索引过期 → 假结论（灵荒薪传 ch025「相似度 1.00 跨章重复」假阳性）；
- 水位线过期 → 增量扫描器认为被重写的章节"已扫过" → **永久漏扫**；
- 台账过期 → 拿已判废版本的结论去指导新的写作。

处置
----
把「章节变更 → 谁是派生状态 → 各自怎么失效」收敛成一张**声明式登记表**，
回滚只调一次 :func:`invalidate_chapters`。新增派生状态必须登记
（红线 ``tests/architecture/test_chapter_invalidation_registry.py`` 棘轮锁定）。

失效模式
--------
- ``auto``   ：可机械失效，总线直接改盘（删条目/退水位线），返回**变更条数**；
- ``detect`` ：**不能**机械失效（自由文本台账等），只统计"引用了被归档章节的条目数"
               供人工/后续流程处置——**绝不猜测性改写**（改写台账比留在原地的风险更大）；
- ``none``   ：已确认与章节号无关（内容寻址缓存、阶段配额等），登记以自证"不是遗漏"。

设计纪律
--------
- 单条失效失败**只降级不阻断**：回滚本身是救命动作，不能因为某个派生文件坏了就失败
  （与 ``_sync_rag_index`` / ``_sync_fingerprints`` 既有契约一致）；
- 任何改盘都走「临时文件 + ``os.replace``」，避免半截 JSON（宿主 safe-delete shim
  会 hook stdlib 删除，热路径不得用 ``unlink``/``rmtree``）。

依赖方向：``agent.core.story``（领域层）→ 仅依赖标准库与 ``core`` 内模块。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from agent.core.infra.degrade import degrade

#: 失效模式
MODE_AUTO = "auto"        # 总线可机械失效
MODE_DETECT = "detect"    # 只能侦测（报数），不猜测性改写
MODE_NONE = "none"        # 与章节号无关（登记以自证非遗漏）


# ============================================================
# 结果结构
# ============================================================
@dataclass
class InvalidationResult:
    """单条派生状态的失效结果。"""

    name: str
    mode: str
    #: ``auto`` → 实际变更条数；``detect`` → 引用被归档章节的条目数；``none`` → 0
    count: int = 0
    applied: bool = False
    note: str = ""
    error: str = ""


@dataclass
class InvalidationReport:
    """一次章节变更的失效清算单。"""

    chapters: list[int] = field(default_factory=list)
    results: list[InvalidationResult] = field(default_factory=list)

    @property
    def changed(self) -> list[InvalidationResult]:
        return [r for r in self.results if r.applied]

    @property
    def stale(self) -> list[InvalidationResult]:
        """侦测到陈旧引用、但无法机械失效的（需人工/后续流程）。"""
        return [r for r in self.results if r.mode == MODE_DETECT and r.count > 0]

    @property
    def failed(self) -> list[InvalidationResult]:
        return [r for r in self.results if r.error]

    def summary(self) -> str:
        parts: list[str] = []
        for r in self.changed:
            parts.append(f"{r.name}×{r.count}")
        for r in self.stale:
            parts.append(f"{r.name} 待人工×{r.count}")
        return "、".join(parts)


# ============================================================
# 通用工具
# ============================================================
def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_json(path: Path, data: Any) -> None:
    """原子写（临时文件 + os.replace）——禁止 unlink/rmtree 热路径删除。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(str(tmp), str(path))


def _chapter_keys(n: int) -> tuple[str, ...]:
    """同一章号在历史数据里出现过的键形（兼容既有指纹库三种写法）。"""
    return (str(n), f"ch{n:03d}", f"ch{n}")


# ============================================================
# 各派生状态的失效实现
# ============================================================
def _inv_fingerprints(project_dir: Path, nums: list[int]) -> int:
    """``.state/chapter_fingerprints.json``：删掉被归档章号的指纹。

    清理动机（2026-09-15 灵荒薪传 ch025）：旧指纹残留时，重写同号章节会与
    **自己的上一版**比对命中「相似度 1.00」→ 判跨章重复 → 打回重写 → 再回退（振荡）。
    """
    fp_file = project_dir / ".state" / "chapter_fingerprints.json"
    if not fp_file.exists():
        return 0
    from agent.core.quality.guardrails import load_fingerprints, save_fingerprints

    db = load_fingerprints(fp_file)
    removed = 0
    for n in nums:
        for key in _chapter_keys(n):
            if key in db:
                del db[key]
                removed += 1
    if removed:
        save_fingerprints(db, fp_file)
    return removed


def _inv_quality_flags(project_dir: Path, nums: list[int]) -> int:
    """``.state/chapter_quality_flags.json``：删掉被归档章节的质量标记。

    标记是"这一版正文有什么毛病"的结论；正文被归档后该结论失去载体，
    留着会在下次体检/报告里指向不复存在的正文。
    """
    path = project_dir / ".state" / "chapter_quality_flags.json"
    if not path.exists():
        return 0
    data = _load_json(path)
    if not isinstance(data, dict):
        return 0
    flags = data.get("flags")
    if not isinstance(flags, list):
        return 0
    keep = [f for f in flags if not (
        isinstance(f, dict) and f.get("chapter") in nums
    )]
    removed = len(flags) - len(keep)
    if removed:
        data["flags"] = keep
        _save_json(path, data)
    return removed


def _inv_payoff_script(project_dir: Path, nums: list[int]) -> int:
    """``.state/payoff_script.json``：删掉被归档章节的爽点排期。

    ``chapters[]`` 是"第 N 章的爽点类型/强度"排期；章节被归档后该排期失效，
    否则重写时会被旧排期约束（或统计时把已判废章节算进去）。

    注意：这是**逐章排期**，整份删除会丢失未归档章节的排期，故只删命中条目；
    ``generated_at`` 保留（下次写入会刷新）。
    """
    path = project_dir / ".state" / "payoff_script.json"
    if not path.exists():
        return 0
    data = _load_json(path)
    if not isinstance(data, dict):
        return 0
    chapters = data.get("chapters")
    if not isinstance(chapters, list):
        return 0
    keep = [c for c in chapters if not (
        isinstance(c, dict) and c.get("chapter") in nums
    )]
    removed = len(chapters) - len(keep)
    if removed:
        data["chapters"] = keep
        _save_json(path, data)
    return removed


def _inv_foreshadow_sync_watermark(project_dir: Path, nums: list[int]) -> int:
    """``.state/foreshadow_sync.json``：把增量扫描水位线退回到最早被归档章之前。

    这是最隐蔽的一类：文件里只有 ``{"scanned_to": N}`` 一个**水位线**。
    水位线不退回，增量扫描器会认为被重写的章节"已经扫过"，于是**永久漏扫**——
    伏笔台账与正文悄悄分叉，且没有任何报错。
    """
    path = project_dir / ".state" / "foreshadow_sync.json"
    if not path.exists() or not nums:
        return 0
    data = _load_json(path)
    if not isinstance(data, dict):
        return 0
    scanned_to = data.get("scanned_to")
    if not isinstance(scanned_to, int) or isinstance(scanned_to, bool):
        return 0
    target = min(nums) - 1
    if scanned_to <= target:
        return 0
    data["scanned_to"] = target
    _save_json(path, data)
    return 1


def _inv_rag_index(project_dir: Path, nums: list[int]) -> int:
    """``.state/rag``：清除被归档章节的向量切片（防幽灵召回）。

    P0-2（2026-09-09）：不清则重写后新旧两版正文同时被召回。
    无索引目录时 no-op（自举由写章侧 ``Indexer.ensure`` 负责），
    **不得**因回滚而凭空创建索引。
    """
    if not (project_dir / ".state" / "rag").exists():
        return 0
    from agent.core.rag.indexer import Indexer

    return int(Indexer(project_dir).drop_chapters(nums) or 0)


def _detect_issue_debts(project_dir: Path, nums: list[int]) -> int:
    """``.state/issue_debts.json``：统计引用了被归档章节的遗留债条目数。

    ``debts[].registered_ch`` 指向登记该债的章节。债是自由文本（"第17章一致性
    警告…"），**不能**机械判定它在重写后是否依然成立 ⇒ 只报数给人工，
    由后续体检重新登记，不做猜测性删除。
    """
    path = project_dir / ".state" / "issue_debts.json"
    if not path.exists():
        return 0
    data = _load_json(path)
    if not isinstance(data, dict):
        return 0
    debts = data.get("debts")
    if not isinstance(debts, list):
        return 0
    return sum(
        1 for d in debts
        if isinstance(d, dict) and d.get("registered_ch") in nums
    )


def _detect_foresight_beats(project_dir: Path, nums: list[int]) -> int:
    """``.state/foresight.json``：统计锚定在被归档章节上的伏笔节拍数。

    ``threads[].beats[].anchor_chapter`` / ``commit_id``（如 ``ch003``）指向
    具体章节；章节被归档后这些节拍的 ``exec_status`` 不再可信。因涉及状态机
    语义（plant/payoff 的提交记录），只报数交人工，不自动回滚节拍状态。
    """
    path = project_dir / ".state" / "foresight.json"
    if not path.exists():
        return 0
    data = _load_json(path)
    if not isinstance(data, dict):
        return 0
    threads = data.get("threads")
    if not isinstance(threads, list):
        return 0
    keys = {k for n in nums for k in _chapter_keys(n)}
    hits = 0
    for t in threads:
        if not isinstance(t, dict):
            continue
        for beat in t.get("beats") or []:
            if not isinstance(beat, dict):
                continue
            if beat.get("anchor_chapter") in nums or str(beat.get("commit_id", "")) in keys:
                hits += 1
    return hits


# ============================================================
# 登记表（SSOT：章节变更必须清算的派生状态）
# ============================================================
@dataclass(frozen=True)
class DerivedState:
    """一个"由章节内容派生、章节变更后必须清算"的状态。"""

    name: str
    relpath: str
    mode: str
    note: str
    invalidate: Callable[[Path, list[int]], int] | None = None
    detect: Callable[[Path, list[int]], int] | None = None


REGISTRY: tuple[DerivedState, ...] = (
    DerivedState(
        "chapter_fingerprints", ".state/chapter_fingerprints.json", MODE_AUTO,
        "跨章重复检测指纹；不清理会与自己的上一版撞出「相似度 1.00」假阳性",
        invalidate=_inv_fingerprints,
    ),
    DerivedState(
        "chapter_quality_flags", ".state/chapter_quality_flags.json", MODE_AUTO,
        "逐章质量标记（AI 腔/复读等）；正文被归档后结论失去载体",
        invalidate=_inv_quality_flags,
    ),
    DerivedState(
        "payoff_script", ".state/payoff_script.json", MODE_AUTO,
        "逐章爽点排期；已归档章节的排期不得继续约束重写",
        invalidate=_inv_payoff_script,
    ),
    DerivedState(
        "foreshadow_sync_watermark", ".state/foreshadow_sync.json", MODE_AUTO,
        "伏笔增量扫描水位线；不退会让被重写章节被当作「已扫过」→ 永久漏扫",
        invalidate=_inv_foreshadow_sync_watermark,
    ),
    DerivedState(
        "rag_index", ".state/rag", MODE_AUTO,
        "向量索引切片；不清会召回已判废的旧版正文（幽灵召回）",
        invalidate=_inv_rag_index,
    ),
    DerivedState(
        "issue_debts", ".state/issue_debts.json", MODE_DETECT,
        "遗留债登记（自由文本，无法机械判定重写后是否仍成立）→ 只报数交人工",
        detect=_detect_issue_debts,
    ),
    DerivedState(
        "foresight_beats", ".state/foresight.json", MODE_DETECT,
        "伏笔节拍锚点（涉及 plant/payoff 提交语义，不自动回滚状态）→ 只报数交人工",
        detect=_detect_foresight_beats,
    ),
    DerivedState(
        "golden_score_cache", ".state/golden_score_cache.json", MODE_NONE,
        "内容寻址（fingerprint 覆盖前三章正文）：正文一改指纹即失配，自失效",
    ),
    DerivedState(
        "mainline", ".state/mainline.json", MODE_NONE,
        "阶段/支线配额配置（version/horizon_chapters/phase_ratio），非章节派生",
    ),
    DerivedState(
        "continuity_ledgers", ".state/continuity", MODE_NONE,
        "实体/设定台账为追加式事实库，无章节键；陈旧事实由 m5_persist "
        "事实抽取器重跑覆盖（另见登记单「设定只进不出」）",
    ),
)


def registered_names() -> list[str]:
    return [d.name for d in REGISTRY]


def chapter_keyed_names() -> list[str]:
    """带章节语义、必须随章节变更清算的状态（``auto`` + ``detect``）。"""
    return [d.name for d in REGISTRY if d.mode in (MODE_AUTO, MODE_DETECT)]


def get_state(name: str) -> DerivedState | None:
    return next((d for d in REGISTRY if d.name == name), None)


# ============================================================
# 入口
# ============================================================
def invalidate_chapters(
    project_dir: Path | str,
    chapters: Iterable[int],
    *,
    only: Iterable[str] | None = None,
) -> InvalidationReport:
    """章节内容变更后，清算全部登记的派生状态。

    Args:
        project_dir: 项目根目录。
        chapters: 被变更（归档/回滚/重写）的章号集合。
        only: 只跑指定状态名（默认全跑）。

    Returns:
        :class:`InvalidationReport`——含每条状态的变更/命中/失败情况。

    纪律：单条失败**只降级不阻断**（回滚是救命动作）。失败信息进报告与
    ``degrade`` 通道，由调用方决定是否提示。
    """
    root = Path(project_dir)
    nums = sorted({int(c) for c in chapters})
    report = InvalidationReport(chapters=nums)
    if not nums:
        return report
    allow = set(only) if only is not None else None

    for st in REGISTRY:
        if allow is not None and st.name not in allow:
            continue
        if st.mode == MODE_NONE:
            report.results.append(
                InvalidationResult(st.name, st.mode, note=st.note)
            )
            continue
        fn = st.invalidate if st.mode == MODE_AUTO else st.detect
        if fn is None:  # pragma: no cover - 登记表自检会拦下
            report.results.append(
                InvalidationResult(
                    st.name, st.mode, error="登记表缺少实现", note=st.note
                )
            )
            continue
        try:
            count = int(fn(root, nums))
            report.results.append(
                InvalidationResult(
                    st.name, st.mode, count=count,
                    applied=(st.mode == MODE_AUTO and count > 0),
                    note=st.note,
                )
            )
        except Exception as e:  # noqa: BLE001 - 单条失效失败不得阻断回滚
            degrade(
                f"chapter_invalidation.{st.name}",
                f"派生状态 {st.name} 未随章节变更失效（不影响回滚本身）：{st.note}",
                e,
            )
            report.results.append(
                InvalidationResult(
                    st.name, st.mode, error=str(e), note=st.note
                )
            )
    return report
