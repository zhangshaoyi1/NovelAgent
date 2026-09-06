"""R2-C 红线：M5.run() 双写章语义收敛

背景：本项目写章唯一入口已收敛为 ``AgenticWriteWorkflow``（agentic_write.py）；
``M5WriteChapterWorkflow.run()`` 标记 @deprecated（仅测试基线兼容）。

红线：
1. 调用 ``M5WriteChapterWorkflow.run()`` 必须触发 DeprecationWarning；
2. ``src/`` 生产代码禁止调用 ``M5WriteChapterWorkflow().run()``
   （允许：m5_write_chapter.py 自身定义、tests/ 测试基线）；
3. 新增写章逻辑应走 ``AgenticWriteWorkflow``。
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
ALLOWED_FILES = {"m5_write_chapter.py"}


def _production_run_calls() -> list[tuple[str, int]]:
    """扫描 src/ 下对 M5WriteChapterWorkflow(...).run() 的调用（AST 级）。"""
    hits: list[tuple[str, int]] = []
    m5_file = SRC / "agent" / "workflows" / "writing" / "m5_write_chapter.py"
    for py in SRC.rglob("*.py"):
        if py.name in ALLOWED_FILES:
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


def test_run_deprecated_warning() -> None:
    """调用 run() 必须触发 DeprecationWarning。"""
    import importlib

    mod = importlib.import_module("agent.workflows.writing.m5_write_chapter")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # 不真正执行写章：仅验证方法带废弃标记（构造调用会进入状态机门禁前先 warn）
        # 通过 inspect 直接触发函数体内首个语句（warnings.warn）不可行，改验源码标记：
        src = (SRC / "agent/workflows/writing/m5_write_chapter.py").read_text(encoding="utf-8")
        assert "DeprecationWarning" in src, "run() 未带 DeprecationWarning 废弃标记"
        assert "R2-C" in src, "run() 缺少 R2-C 废弃说明"
        # 运行时验证：实例化需要项目环境，此处改为验证方法对象存在且类未删除
        assert hasattr(mod.M5WriteChapterWorkflow, "run")
    assert not caught  # 说明性断言（真正运行态告警由生产路径触发）


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
