"""R2-C 红线：写章入口唯一（M5 废弃入口已**删除**）

背景：本项目写章唯一入口已收敛为 ``AgenticWriteWorkflow``（agentic_write.py）。
``M5WriteChapterWorkflow.run()`` 曾标记 @deprecated 仅作测试基线保留；
**2026-09-16 已连同其专属链路删除**（登记单 ``20260916_闸门信号可达性普查``
§三.C2：废弃入口内的质检 JSON 解析失败降级 ``overall_pass=True`` 且无任何留痕，
该路径只有废弃入口可达 —— 按拍板「废弃即删除」清理，而非给不可达路径打补丁）。

红线（较「仅打废弃标记」更强：由「禁止调用」升级为「不得存在」）：
1. ``M5WriteChapterWorkflow`` **不得再有 ``run`` 属性** —— 防止废弃入口被
   "顺手恢复"；一旦恢复，C2 那类「不可达却无留痕」的降级路径会立即复活；
2. 废弃入口专属产物（``M5Result``）不得复活；
3. ``src/`` 生产代码禁止调用 ``M5WriteChapterWorkflow().run()``（双重保险）；
4. 写章构造收敛到 service 层（``build_write_workflow``）。
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
ALLOWED_FILES = {"m5_write_chapter.py"}


def _production_run_calls() -> list[tuple[str, int]]:
    """扫描 src/ 下对 M5WriteChapterWorkflow(...).run() 的调用（AST 级）。"""
    hits: list[tuple[str, int]] = []
    m5_file = SRC / "agent" / "workflows" / "writing" / "m5_write_chapter.py"
    for py in SRC.rglob("*.py"):
        if py.name in ALLOWED_FILES or py == m5_file:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            # 匹配 Call 形态：X.run(...) 且 X 与 M5WriteChapterWorkflow 相关
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "run":
                continue
            base = node.func.value
            name = ""
            if isinstance(base, ast.Name):
                name = base.id
            elif isinstance(base, ast.Call) and isinstance(base.func, ast.Name):
                name = base.func.id
            if "M5WriteChapterWorkflow" in name or name == "M5WriteChapterWorkflow":
                hits.append((str(py.relative_to(SRC)), node.lineno))
    return hits


def test_m5_run_entry_is_removed() -> None:
    """废弃写章入口不得复活：``run`` 与 ``M5Result`` 都必须不存在。"""
    mod = importlib.import_module("agent.workflows.writing.m5_write_chapter")
    assert not hasattr(mod.M5WriteChapterWorkflow, "run"), (
        "M5WriteChapterWorkflow.run() 已删除（R2-C 收敛 + 2026-09-16 清理）——"
        "写章唯一入口是 AgenticWriteWorkflow。恢复废弃入口会让「不可达却无留痕」的"
        "质检降级路径复活（登记单 20260916_闸门信号可达性普查 §三.C2）"
    )
    assert not hasattr(mod, "M5Result"), (
        "M5Result 是废弃入口专属产物，不得复活"
    )


def test_m5_module_docstring_records_removal() -> None:
    """模块 docstring 必须写明「入口已删除」及其前置条件（防后人误判为待办）。"""
    src = (
        SRC / "agent" / "workflows" / "writing" / "m5_write_chapter.py"
    ).read_text(encoding="utf-8")
    assert "不再提供服务端的写章入口" in src
    assert "删除前置条件" in src, (
        "必须记录「L1 能力先迁移再删除」的前置条件，否则后人无法判断删除是否安全"
    )


def test_no_production_run_calls() -> None:
    """生产代码（src/）禁止调用 M5WriteChapterWorkflow().run()。"""
    hits = _production_run_calls()
    assert not hits, (
        f"发现 {len(hits)} 处生产代码调用 M5WriteChapterWorkflow.run()——"
        f"写章唯一入口应为 AgenticWriteWorkflow（R2-C）：\n"
        + "\n".join(f"  {f}:{ln}" for f, ln in hits)
    )


def test_agentic_is_writing_entry() -> None:
    """确认写章入口收敛：write 命令经 service 层（R2-B），不再直接 import AgenticWriteWorkflow。"""
    write_cmd = SRC / "agent" / "cli" / "commands" / "write.py"
    if write_cmd.exists():
        txt = write_cmd.read_text(encoding="utf-8")
        assert "build_write_workflow" in txt, "write 命令应经 service.build_write_workflow 构造写章工作流（R2-B 收敛）"
        assert "agent.workflows.writing.agentic_write" not in txt, (
            "write 命令不应直接 import agentic_write（写章构造已收敛到 service 层，R2-B）"
        )
    svc = SRC / "agent" / "service" / "agent_service.py"
    if svc.exists():
        stxt = svc.read_text(encoding="utf-8")
        assert "build_write_workflow" in stxt, "service 层应提供 build_write_workflow（写章唯一构造入口）"
        assert "acquire_project_lock" in stxt, "service 层构造写章工作流应含单写者锁检查（R2-B/F-5）"
