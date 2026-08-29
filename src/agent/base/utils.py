"""基础通用工具函数（仅依赖标准库）

职责：提供不依赖任何上层语义的纯工具函数，被 core / client / workflows 等所有上层复用。

下沉说明（2026-08-29）：
- ``parse_llm_json`` / ``safe_remove`` / ``chunk_text`` 原位于 ``agent/utils.py``，
  因 ``core/base/structured_output`` 等底层模块需要它们、而 ``agent/utils.py`` 混入了
  rich 依赖，导致底层基础设施间接依赖第三方库。现下沉到本模块，保证 base 层零外部依赖。
- ``make_quiet_console``（依赖 rich）保留在 ``agent/utils.py``，属于 CLI 渲染层职责。

依赖规则：仅标准库，不依赖任何 agent 包内模块。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import warnings
from pathlib import Path
from typing import Any


def parse_llm_json(text: str) -> dict[str, Any]:
    """容错解析 LLM 输出的 JSON

    LLM 常在 JSON 外包裹 ```json ... ``` 标记或添加额外说明。
    本函数尝试多种策略提取并解析 JSON。

    Args:
        text: LLM 原始输出

    Returns:
        解析后的 dict

    Raises:
        ValueError: 无法解析为 JSON
    """
    text = text.strip()

    # 策略 1: 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 策略 2: 去除 ```json ... ``` 标记
    fence_pattern = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)
    match = fence_pattern.search(text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 策略 3: 提取第一个 { ... } 块（简单范围）
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    # 策略 4: 严格括号配对，找从 start 开始的匹配 } 范围（处理 LLM 在 JSON 后
    # 又追加解释文本，导致 rfind('}') 错配的情形）
    start = text.find("{")
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        match_end = -1
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    match_end = i
                    break
        if match_end != -1:
            try:
                return json.loads(text[start : match_end + 1])
            except json.JSONDecodeError:
                pass

    raise ValueError(f"无法解析为 JSON: {text[:200]}...")


def safe_remove(path: "Path | str", *, trash_root: "Path | str | None" = None) -> bool:
    """安全删除文件或目录，绝不抛错中断主流程（增量 A/B：软删除策略）。

    Args:
        path: 待删除的文件或目录路径。
        trash_root: 目录删除失败时的改名目标根目录；None 时默认 path.parent / ".trash"。

    Returns:
        True  表示 path 当前已不存在（已删除 / 已改名兜底 / 原本就不存在）。
        False 表示所有策略均失败（仅 warning，不抛错）。

    行为契约（回退链，绝不抛错）：
        1. path 不存在 → 直接返回 True（幂等）。
        2. 文件：优先 os.remove；失败 → 清空内容 + 改名 <name>.bak；仍失败 → warning + False。
        3. 目录：优先 shutil.rmtree；失败 → 改名到 <trash_root>/<name>；仍失败 → warning + False。
    """
    p = Path(path)

    # 1. 幂等：不存在直接 True
    if not p.exists():
        return True

    # 2/3. 主策略
    try:
        if p.is_file():
            os.remove(p)
        else:
            shutil.rmtree(p)
    except OSError:
        # 回退策略
        try:
            if p.is_file():
                # 清空内容后改名 .bak（同目录惰性残骸）
                p.write_text("")
                p.rename(p.with_name(p.name + ".bak"))
            else:
                # 目录改名到 trash_root（默认 <parent>/.trash/<name>）
                target = (Path(trash_root) if trash_root else p.parent / ".trash") / p.name
                target.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(target))
        except OSError:
            warnings.warn(f"safe_remove 无法安全删除 {p}", stacklevel=2)
            return False

    # 末态判断：仍存在的视为失败（从不抛错）
    if p.exists():
        warnings.warn(f"safe_remove 未能删除 {p}", stacklevel=2)
        return False
    return True


def chunk_text(text: str, size: int = 500) -> list[str]:
    """按 ~size 字符的段落级切分（RAG 索引 / 上下文召回用）

    切分策略：优先以空行（段落）为边界；段落超出 ``size`` 时退化为按句切分；
    单句仍超出时硬截断。尽量不切断语义单元，保证召回片段可读。

    Args:
        text: 待切分文本。
        size: 单片段目标字符数（默认 500，对应 PRD 段落级切片约定）。

    Returns:
        片段列表；空文本返回 ``[]``。
    """
    if not text:
        return []
    if size <= 0:
        return [text]

    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""

    def _flush() -> None:
        nonlocal buf
        if buf:
            chunks.append(buf)
            buf = ""

    for para in paragraphs:
        if len(buf) + len(para) + 1 <= size:
            buf = (buf + "\n\n" + para) if buf else para
            continue
        _flush()
        if len(para) <= size:
            buf = para
            continue
        # 段落超长：按句切分后合并
        sentences = re.split(r"(?<=[。！？!?；;])", para)
        for sent in sentences:
            if not sent:
                continue
            if len(buf) + len(sent) <= size:
                buf = (buf + sent) if buf else sent
            else:
                _flush()
                if len(sent) <= size:
                    buf = sent
                else:
                    # 单句超长：按 size 硬切多段
                    for i in range(0, len(sent), size):
                        chunks.append(sent[i : i + size])
        _flush()

    _flush()
    return chunks or [text]


__all__ = ["parse_llm_json", "safe_remove", "chunk_text"]
