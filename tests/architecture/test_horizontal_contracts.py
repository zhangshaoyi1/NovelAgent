"""横向契约红线（20261003 复盘行动项 1/2/4：状态消费者清单 / 控制流异常基类 / 路径解析）

三类「跨组件约定」没有组件级测试形态可挂靠，集中在本文件以静态扫描钉住：
- **状态消费者清单（行动项 1）**：``State.<MEMBER>`` 的比较/成员构造点全量入册
  （state_consumer_baseline.json，158 处/37 文件）——新增枚举成员时，任何
  消费点增减都会使基线漂移而 FAIL，强制有意识核对（AWAITING_CHECKPOINT
  三处漏改教训的机制化）。
- **控制流异常（行动项 2）**：``*GateRejected`` / ``*Escalation`` / ``*Abort``
  命名的异常类必须继承 :class:`ControlFlowError`（BaseException）——
  穿透 149 处 ``except Exception`` 降级网由类型系统保证，不靠人肉 re-raise。
- **路径解析（行动项 4）**：core/ 与 workflows/ 中 ``.resolve()`` 仅允许
  ``__file__`` 派生路径或带 ``path-resolve-ok`` 豁免标注（对上层传入路径
  重复锚定 = novels 三重叠加 bug 家族）。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "agent"
BASELINE = Path(__file__).resolve().parent / "state_consumer_baseline.json"


def _py_files(root: Path):
    return [p for p in sorted(root.rglob("*.py")) if "tests" not in p.parts]


# ================================================================
# 行动项 1：状态消费者清单（增量拦截）
# ================================================================
def _count_state_sites(tree: ast.AST) -> int:
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for side in (node.left, *node.comparators):
                if (isinstance(side, ast.Attribute)
                        and isinstance(side.value, ast.Name)
                        and side.value.id == "State"):
                    n += 1
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for elt in node.elts:
                if (isinstance(elt, ast.Attribute)
                        and isinstance(elt.value, ast.Name)
                        and elt.value.id == "State"):
                    n += 1
    return n


def test_state_consumer_baseline_no_drift() -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current: dict[str, int] = {}
    for py in _py_files(SRC):
        rel = py.relative_to(SRC.parent).as_posix()
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        n = _count_state_sites(tree)
        if n:
            current[rel] = n

    added = {k: v for k, v in current.items() if k not in baseline}
    removed = {k: v for k, v in baseline.items() if k not in current}
    changed = {
        k: (baseline[k], current[k])
        for k in baseline.keys() & current.keys()
        if baseline[k] != current[k]
    }
    assert not (added or removed or changed), (
        "状态消费者点位相对基线漂移——新增/删改 State 成员比较点必须核对全部消费者"
        "（AWAITING_CHECKPOINT 三处漏改教训），并有意识地更新基线：\n"
        f"新增：{added}\n移除：{removed}\n变化：{changed}"
    )


# ================================================================
# 行动项 2：控制流异常基类
# ================================================================
_CONTROL_FLOW_NAMES = ("GateRejected", "Escalation", "Abort")


def test_control_flow_subclasses_use_base() -> None:
    """``*GateRejected``/``*Escalation``/``*Abort`` 异常必须继承 ControlFlowError。"""
    offenders: list[str] = []
    for py in _py_files(SRC):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(node.name.endswith(s) for s in _CONTROL_FLOW_NAMES):
                continue
            bases = [ast.unparse(b) for b in node.bases]
            if not any("ControlFlowError" in b for b in bases):
                offenders.append(f"{py.as_posix()}:{node.lineno} {node.name}({','.join(bases)})")
    assert not offenders, (
        "升级语义异常必须继承 ControlFlowError（BaseException）——"
        "否则会被 except Exception 降级网吞噬（类 2 三次实证）：\n" + "\n".join(offenders)
    )


def test_control_flow_error_penetrates_except_exception() -> None:
    """行为级：``except Exception`` 结构上接不住 ControlFlowError。"""
    from agent.core.base.control_flow import ControlFlowError

    class _Demo(ControlFlowError):
        pass

    with pytest.raises(_Demo):
        try:
            raise _Demo("穿透验证")
        except Exception:  # noqa: BLE001 - 恰好必须捕获不到
            pass  # 若走到这里即穿透失效


# ================================================================
# 行动项 4：路径解析单一出口
# ================================================================
def test_resolve_only_on_file_derived_or_marked() -> None:
    """core/ + workflows/ 中 ``.resolve()`` 仅允许 ``__file__`` 派生或
    ``path-resolve-ok`` 豁免标注（对上层传入路径重复锚定 = 三重叠加家族）。"""
    offenders: list[str] = []
    for root in (SRC / "core", SRC / "workflows"):
        for py in _py_files(root):
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if ".resolve()" not in line:
                    continue
                if "__file__" in line or "path-resolve-ok" in line:
                    continue
                offenders.append(f"{py.as_posix()}:{i}  {line.strip()[:100]}")
    assert not offenders, (
        "core/workflows 不得对上层传入路径调用 .resolve()（重复锚定 bug 家族）；"
        "确属入口层解析/缓存键归一请加 `# path-resolve-ok: <原因>`：\n"
        + "\n".join(offenders)
    )
