"""A 系列：问答约束读取与 prompt 注入（工作流层共享助手）

Web 问答面板把「各阶段作者的偏好回答 + 末轮补充」保存到
``.state/qa/{stage_key}.json``（见 agent.web.state.save_qa）。

各生成工作流在构建初始生成 prompt 时，通过 :func:`format_qa_constraints`
把该文件格式化为「作者在问答引导中确定的偏好」段落追加进 user prompt，
让 Agent 严格按作者的偏好方向生成。

仅作用于「初始生成」，不作用于带 ``--feedback`` 的迭代修订（迭代以作者意见为准）。
"""

from __future__ import annotations

import json
from pathlib import Path


def qa_file(project_dir: Path | str, stage_key: str) -> Path:
    """问答结果文件路径。"""
    return Path(project_dir) / ".state" / "qa" / f"{stage_key}.json"


def load_qa(project_dir: Path | str, stage_key: str) -> dict:
    """读取问答结果；文件缺失或解析失败返回空 dict（不抛出）。"""
    try:
        return json.loads(qa_file(project_dir, stage_key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def format_qa_constraints(project_dir: Path | str, stage_key: str) -> str:
    """把问答结果格式化为 prompt 注入文本；无问答文件或全空时返回空串。

    注入段落示例：
    【作者在「问答引导」中确定的偏好（务必遵守）】
    - 故事倾向：逆袭复仇（跳过，采用默认）
    - 作者补充：希望结局留白
    """
    data = load_qa(project_dir, stage_key)
    if not data:
        return ""
    answers = data.get("answers") or {}
    skipped = data.get("skipped") or {}
    supplementary = str(data.get("supplementary") or "").strip()

    lines: list[str] = []
    for key, val in answers.items():
        val = str(val).strip()
        if not val:
            continue
        if skipped.get(key):
            lines.append(f"- {key}：{val}（跳过，采用默认）")
        else:
            lines.append(f"- {key}：{val}")
    if supplementary:
        lines.append(f"- 作者补充：{supplementary}")
    if not lines:
        return ""
    return "\n\n【作者在「问答引导」中确定的偏好（务必遵守）】\n" + "\n".join(lines)
