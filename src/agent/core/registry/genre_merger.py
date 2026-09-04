"""题材合并器（多题材混搭 / 渐进式披露）

将多个题材包（GenrePack）的资源按顶层 ``##`` 段落合并：
- ``world_template`` / ``terms`` / ``tropes`` / ``quality_rules`` 各自独立合并
- 跨题材出现同名顶层段落 -> 记为冲突（MergeConflict），交由用户逐条裁决
- 非冲突段落按题材顺序拼接；无标题前言（首个 ``##`` 之前的内容）直接拼接
- ``merge()`` 默认以「主题材优先」生成可用合并文本，并把冲突记录为未裁决，
  供后续 ``merge-genres`` 命令 / Web UI 让用户复核裁决

设计要点：
- 仅做静态文本合并，不调用 LLM，成本可控
- 复用 core/conflict_service 的冲突语义（field/existing/new），但本合并器
  面向「多源择一」，结构更贴合（每个冲突含多个候选 entries）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.core.registry.genre_pack import GenrePack


@dataclass
class MergeConflict:
    """单条合并冲突：同一顶层段落出现在多个题材包中，内容不一致。

    entries 为各题材候选；用户裁决后填 resolved_index（选某个候选）
    或 manual（手动合并文本，优先于 resolved_index）。
    """

    resource: str = ""          # 资源类型：world_template/terms/tropes/quality_rules
    section: str = ""           # 段落标题（如 "力量体系"）
    entries: list[dict[str, str]] = field(default_factory=list)  # [{genre,label,content}]
    resolved_index: int | None = None  # 用户选择 entries 的下标；None=未裁决
    manual: str | None = None   # 用户手动合并结果（优先）

    @property
    def is_resolved(self) -> bool:
        return self.resolved_index is not None or self.manual is not None

    def chosen_content(self) -> str:
        """返回当前生效的内容（手动 > 选定候选 > 首个候选占位）。"""
        if self.manual is not None:
            return self.manual
        if self.resolved_index is not None and 0 <= self.resolved_index < len(self.entries):
            return self.entries[self.resolved_index]["content"]
        return self.entries[0]["content"] if self.entries else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "section": self.section,
            "entries": self.entries,
            "resolved_index": self.resolved_index,
            "manual": self.manual,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MergeConflict":
        return cls(
            resource=str(d.get("resource", "")),
            section=str(d.get("section", "")),
            entries=list(d.get("entries", []) or []),
            resolved_index=d.get("resolved_index"),
            manual=d.get("manual"),
        )


@dataclass
class MergeResult:
    """合并结果"""

    world_template: str = ""
    terms: str = ""
    tropes: str = ""
    quality_rules: str = ""
    conflicts: list[MergeConflict] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)  # 参与合并的题材 id 列表

    # 内部：用于按裁决重建（不计入序列化）
    _labels: dict[str, str] = field(default_factory=dict, repr=False)
    _sources: dict[str, list[tuple[str, str]]] = field(default_factory=dict, repr=False)

    @property
    def has_unresolved(self) -> bool:
        return any(not c.is_resolved for c in self.conflicts)

    def conflict_count(self) -> int:
        return len(self.conflicts)

    def unresolved_count(self) -> int:
        return sum(1 for c in self.conflicts if not c.is_resolved)


def _split_sections(text: str) -> list[tuple[str, str]]:
    """将 markdown 文本按顶层 ``## `` 拆分为 [(heading, body)]。

    首个 ``##`` 之前的序言（无标题）归入 heading=''。
    """
    if not text or not text.strip():
        return []
    parts = re.split(r"(?m)^##\s+", text)
    sections: list[tuple[str, str]] = []
    for i, seg in enumerate(parts):
        seg = seg.strip("\n")
        if i == 0:
            if seg.strip():
                sections.append(("", seg.strip()))
            continue
        lines = seg.splitlines()
        if not lines:
            continue
        heading = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        sections.append((heading, body))
    return sections


class GenreMerger:
    """题材合并器

    用法：
        merger = GenreMerger()
        result = merger.merge([pack_a, pack_b])          # 默认主题材优先 + 记录冲突
        # 用户裁决：
        decisions = {("world_template", "力量体系"): 1,   # 选第二个候选
                     ("world_template", "境界体系"): "手动合并文本..."}
        merger.apply_decisions(result, decisions)
        # result.world_template / terms / tropes / quality_rules 已更新
    """

    RESOURCES = ("world_template", "terms", "tropes", "quality_rules")

    # ---------- 主流程 ----------
    def merge(self, packs: list[GenrePack]) -> MergeResult:
        """合并多个题材包，返回 MergeResult（冲突默认未裁决，文本以主题材优先占位）。"""
        sources: dict[str, list[tuple[str, str]]] = {res: [] for res in self.RESOURCES}
        labels: dict[str, str] = {}
        for p in packs:
            labels[p.name] = p.manifest.display_name
            for res in self.RESOURCES:
                content = getattr(p, res) or ""
                if content.strip():
                    sources[res].append((p.name, content))
        result = MergeResult(sources=[p.name for p in packs])
        result._labels = labels
        result._sources = sources
        self._rebuild(result, {})
        return result

    def apply_decisions(self, result: MergeResult, decisions: dict[tuple[str, str], Any]) -> MergeResult:
        """按用户裁决重建合并文本。decisions 键为 (resource, section)，值为下标 int 或手动文本 str。"""
        self._rebuild(result, decisions)
        return result

    def _rebuild(self, result: MergeResult, decisions: dict[tuple[str, str], Any]) -> None:
        labels = result._labels
        conflicts: list[MergeConflict] = []
        merged: dict[str, str] = {}
        for res, texts in result._sources.items():
            parts, res_conflicts = self._merge_one(res, texts, labels, decisions)
            merged[res] = "\n\n".join(parts)
            conflicts.extend(res_conflicts)
        result.world_template = merged["world_template"]
        result.terms = merged["terms"]
        result.tropes = merged["tropes"]
        result.quality_rules = merged["quality_rules"]
        result.conflicts = conflicts

    def _merge_one(
        self,
        resource: str,
        texts: list[tuple[str, str]],
        labels: dict[str, str],
        decisions: dict[tuple[str, str], Any],
    ) -> tuple[list[str], list[MergeConflict]]:
        by_heading: dict[str, list[dict[str, str]]] = {}
        order: list[str] = []
        for gid, text in texts:
            for heading, body in _split_sections(text):
                key = heading.strip() or "__preamble__"
                if key not in by_heading:
                    by_heading[key] = []
                    order.append(key)
                by_heading[key].append(
                    {"genre": gid, "label": labels.get(gid, gid), "content": body}
                )

        parts: list[str] = []
        conflicts: list[MergeConflict] = []
        for key in order:
            entries = by_heading[key]
            if key == "__preamble__":
                # 无标题前缀（首个 ## 之前的内容）：多份时按主题材优先仅保留首份，
                # 避免两份模板说明重复拼接。
                for e in entries:
                    if e["content"].strip():
                        parts.append(e["content"].strip())
                        break
                continue
            if len(entries) == 1:
                parts.append(f"## {key}\n{entries[0]['content']}")
                continue
            # 同名段落跨题材 -> 冲突
            c = MergeConflict(resource=resource, section=key, entries=entries)
            dkey = (resource, key)
            if dkey in decisions:
                dec = decisions[dkey]
                if isinstance(dec, int):
                    c.resolved_index = dec
                else:
                    c.manual = str(dec)
                parts.append(f"## {key}\n{c.chosen_content()}")
            else:
                # 未裁决：文本以主题材（首条）占位，记录为未裁决供后续复核
                c.resolved_index = None
                parts.append(f"## {key}\n{c.chosen_content()}")
            conflicts.append(c)
        return parts, conflicts


# ---------- 冲突持久化（供 merge-genres / Web UI 复核）----------
def save_conflicts(project_dir: str | Path, result: MergeResult) -> Path:
    """将合并冲突写入 .state/merge_conflicts.json（仅冲突元数据，不含全文）。"""
    pdir = Path(project_dir)
    state_dir = pdir / ".state"
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "sources": result.sources,
        "conflicts": [c.to_dict() for c in result.conflicts],
    }
    f = state_dir / "merge_conflicts.json"
    f.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return f


def load_conflicts(project_dir: str | Path) -> dict[str, Any] | None:
    """读取 .state/merge_conflicts.json；不存在返回 None。"""
    f = Path(project_dir) / ".state" / "merge_conflicts.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
