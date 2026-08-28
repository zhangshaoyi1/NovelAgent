"""E4 内容证据可视化 - 结构化证据链

将 M5 产出的依据链从扁平文本列表提升为结构化分类引用
（角色 / 伏笔 / 设定），支持落盘时校验引用源是否存在。

证据引用映射（F-E4.2）：
    每条生成章节对应的设定条目，都以 EvidenceRef 记录：
        - name / ref_id：角色名或伏笔 ID 或设定字段名
        - field：被引用的具体字段（身份 / 境界 / 动机 ...）
        - source：相对项目根的设置文件路径
        - content：伏笔内容等可选备注
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidenceRef:
    """单条证据引用"""

    name: str = ""           # 角色名 / 设定字段名
    ref_id: str = ""         # 伏笔 ID（如 F-01）
    field: str = ""          # 引用字段（身份 / 境界 / 动机 ...）
    source: str = ""         # 相对项目根的设置文件路径（world.md / characters/X.md ...）
    content: str = ""        # 备注（伏笔内容等）


@dataclass
class EvidenceChain:
    """章节结构化证据链（F-E4.1）"""

    characters: list[EvidenceRef] = field(default_factory=list)
    foreshadows: list[EvidenceRef] = field(default_factory=list)
    settings: list[EvidenceRef] = field(default_factory=list)
    # 落盘校验时发现的缺失引用源（不阻断，仅告警）
    missing_sources: list[str] = field(default_factory=list)

    def all_refs(self) -> list[EvidenceRef]:
        """返回全部引用（用于校验 / 统计）"""
        return [*self.characters, *self.foreshadows, *self.settings]

    def total(self) -> int:
        return len(self.all_refs())

    def to_dict(self) -> dict[str, Any]:
        """序列化为可写入 frontmatter 的嵌套 dict"""

        def _ref(r: EvidenceRef) -> dict[str, Any]:
            d: dict[str, Any] = {}
            if r.ref_id:
                d["id"] = r.ref_id
            if r.name:
                d["name"] = r.name
            if r.field:
                d["field"] = r.field
            d["source"] = r.source
            if r.content:
                d["content"] = r.content
            return d

        return {
            "characters": [_ref(r) for r in self.characters],
            "foreshadows": [_ref(r) for r in self.foreshadows],
            "settings": [_ref(r) for r in self.settings],
        }
