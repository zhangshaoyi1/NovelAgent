"""宿主敌意环境：删除入口全部被拦时，关键路径不得逃逸（架构红线，2026-09-11 事故）。

背景
----
WorkBuddy 的 safe-delete 批量护栏（``cli/vendor/shim/sitecustomize.py``）在「同一
turn 累计删除数越过阈值」后，对 ``os.remove`` / ``os.unlink`` / ``os.rmdir`` /
``shutil.rmtree`` / ``Path.unlink`` / ``Path.rmdir`` 抛 ``SystemExit(1)``——
**不是 OSError**（``SystemExit`` 继承 ``BaseException``）。于是全仓 ``except OSError``
一律拦不住，异常直接逃逸，并在最难受的两个位置造成事故：

- worker ``atexit`` 释放写锁失败 → 进程带失败码退出（**批次写完却判 failed**）；
- daemon 归档任务 ``src.unlink()`` 失败 → **daemon 被打死**，任务同时残留
  ``running/`` 与 ``done/``。

本文件把这条经验固化为两条红线：

1. **静态红线** ``test_no_unguarded_delete_calls``：``src/agent`` 中每一处删除调用，
   必须处于「捕获 ``SystemExit`` 的 ``try`` 体内」（``except (OSError, SystemExit)`` /
   ``except SystemExit`` / 裸 ``except`` / ``except BaseException``），或在调用语句上
   显式标注 ``# noqa: HOSTILE_DELETE`` 豁免。
   注意 ``except OSError`` / ``except Exception`` **不算**守卫（都接不住 SystemExit）。
2. **运行时红线**（其余用例）：把全部删除入口换成抛 ``SystemExit(1)``，跑关键路径
   （``safe_remove`` 回退链 → 原子写 → 状态机落盘 → 解锁 CLI → 草稿清理 → 设定集回滚），
   断言**不逃逸、结果正确**。

修法原则
--------
关键路径的「删除」优先用「改名」（``os.replace`` / ``shutil.move`` 是移动操作，不在
hook 名单内）；工具层（``safe_remove`` / ``atomic._discard_tmp``）则必须同时吞
``OSError`` 与 ``SystemExit``，且**不得掩盖真实异常**（见最后两个用例）。
"""

from __future__ import annotations

import ast
import json
import os
import shutil
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"

#: 调用行显式豁免标记（新增裸删除必须有非删除的替代理由）
_EXEMPT_MARK = "HOSTILE_DELETE"

#: 按「完整限定名」识别的删除入口（避免误伤 list.remove 之类同名方法）
_DELETE_FULL_NAMES = {("os", "remove"), ("os", "unlink"), ("os", "rmdir"), ("shutil", "rmtree")}

#: 按「方法名」识别的删除入口（Path.unlink / os.unlink / Path.rmdir 等）
_DELETE_METHOD_NAMES = {"unlink", "rmdir"}

#: 能接住 SystemExit 的异常名
_SYSTEM_EXIT_NAMES = {"SystemExit", "BaseException"}


# ============================================================
# 静态红线
# ============================================================
def _full_name(node: ast.AST) -> tuple[str, str] | None:
    """把 ``os.remove`` / ``shutil.rmtree`` 解析成 ``(owner, attr)``。"""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return (node.value.id, node.attr)
    return None


def _collect_import_aliases(tree: ast.AST) -> tuple[dict[str, str], set[str]]:
    """收集本文件的删除入口别名表（消除 ``from os import remove`` 类盲区）。

    Returns:
        ``(module_alias, imported_funcs)``：
        - ``module_alias``：本地模块名 → 真实模块名（``import shutil as sh`` → ``{"sh": "shutil"}``）；
        - ``imported_funcs``：``from os import remove`` / ``from shutil import rmtree as rt``
          引入的**本地函数名**集合。
    """
    module_alias: dict[str, str] = {}
    imported_funcs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                local = a.asname or a.name.split(".")[0]
                module_alias[local] = a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod not in ("os", "shutil"):
                continue
            for a in node.names:
                if (mod, a.name) in _DELETE_FULL_NAMES:
                    imported_funcs.add(a.asname or a.name)
    return module_alias, imported_funcs


def _is_delete_call(
    node: ast.Call,
    module_alias: dict[str, str] | None = None,
    imported_funcs: set[str] | None = None,
) -> bool:
    """是否删除调用。

    识别三类（2026-09-11 补别名盲区）：
    ① 全限定名 ``os.remove`` / ``os.unlink`` / ``os.rmdir`` / ``shutil.rmtree``
       （owner 先经 ``module_alias`` 归一，故 ``sh.rmtree`` / ``o.remove`` 也命中）；
    ② ``from os import remove`` / ``from shutil import rmtree as rt`` 引入的本地名直调；
    ③ 任意对象上的 ``.unlink()`` / ``.rmdir()``（``Path.unlink`` 等）。
    注：裸 ``.remove()`` 不判（``list.remove`` 是常见非删除语义）。
    """
    module_alias = module_alias or {}
    imported_funcs = imported_funcs or set()
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in imported_funcs
    full = _full_name(func)
    if full is not None:
        owner = module_alias.get(full[0], full[0])
        if (owner, full[1]) in _DELETE_FULL_NAMES:
            return True
    return isinstance(func, ast.Attribute) and func.attr in _DELETE_METHOD_NAMES


def _catches_system_exit(handler: ast.ExceptHandler) -> bool:
    """handler 是否接得住 ``SystemExit``（裸 except 视为接得住）。"""
    t = handler.type
    if t is None:  # bare except:
        return True
    elts = t.elts if isinstance(t, ast.Tuple) else [t]
    for elt in elts:
        if isinstance(elt, ast.Name) and elt.id in _SYSTEM_EXIT_NAMES:
            return True
        if isinstance(elt, ast.Attribute) and elt.attr in _SYSTEM_EXIT_NAMES:
            return True
    return False


def _is_guarded(call: ast.Call, ancestors: list[ast.AST], lines: list[str]) -> str | None:
    """返回守卫来源（``"exempt"`` / ``"try"``），未受守卫返回 ``None``。"""
    end = getattr(call, "end_lineno", None) or call.lineno
    for line in lines[call.lineno - 1 : end]:
        if _EXEMPT_MARK in line:
            return "exempt"

    # 从内到外找最近的 try：只认「调用位于 try.body 内」的情况——
    # 位于 except / else / finally 子句里的调用不受该 try 的 handler 保护。
    for idx, node in enumerate(ancestors):
        if not isinstance(node, ast.Try):
            continue
        inner = ancestors[idx + 1] if idx + 1 < len(ancestors) else None
        if inner is None or inner not in node.body:
            continue
        if any(_catches_system_exit(h) for h in node.handlers):
            return "try"
    return None


def _collect_unguarded_deletes(root: Path = SRC) -> list[str]:
    """扫描 ``root`` 下所有 .py，返回未受 SystemExit 守卫的删除调用（``file:line``）。"""
    violations: list[str] = []
    for py in sorted(root.rglob("*.py")):
        try:
            lines = py.read_text(encoding="utf-8").splitlines()
            tree = ast.parse("\n".join(lines), filename=str(py))
        except SyntaxError:  # pragma: no cover - 语法错误由 lint/编译流程兜住
            continue

        # 2026-09-11：先建本文件的 import 别名表，再判定删除调用
        # （否则 ``from os import remove`` / ``import shutil as sh`` 会漏检）。
        module_alias, imported_funcs = _collect_import_aliases(tree)
        ancestors: list[ast.AST] = []

        def walk(node: ast.AST) -> None:
            ancestors.append(node)
            if isinstance(node, ast.Call) and _is_delete_call(
                node, module_alias, imported_funcs
            ):
                if _is_guarded(node, ancestors, lines) is None:
                    try:
                        rel = py.relative_to(root).as_posix()
                    except ValueError:  # pragma: no cover
                        rel = py.as_posix()
                    violations.append(f"{rel}:{node.lineno}  {lines[node.lineno - 1].strip()}")
            for child in ast.iter_child_nodes(node):
                walk(child)
            ancestors.pop()

        walk(tree)
    return violations


def test_no_unguarded_delete_calls() -> None:
    """红线：任何删除调用都必须能接住 SystemExit（或显式豁免）。

    违反时的修法（按优先级）：
    1. 关键路径「删除」改「改名」——``os.replace`` / ``shutil.move`` 不触发护栏；
    2. 必须删除时改走 ``safe_remove`` / ``atomic._discard_tmp`` 适配层；
    3. 就地补 ``except (OSError, SystemExit)``；
    4. 确有非删除语义的同名调用，在行尾标注 ``# noqa: HOSTILE_DELETE``。
    """
    violations = _collect_unguarded_deletes()
    assert not violations, (
        "以下删除调用未捕获 SystemExit——WorkBuddy safe-delete 护栏触发时异常会直接"
        "逃逸（写批次误判 failed / daemon 被打死）：\n  " + "\n  ".join(violations)
    )


def test_static_check_actually_detects_unguarded_delete(tmp_path: Path) -> None:
    """自检：静态红线本身有效（避免 AST 判据写错导致永远绿灯）。"""
    bad = tmp_path / "bad.py"
    bad.write_text("import os\n\ndef f(p):\n    os.remove(p)\n", encoding="utf-8")
    assert _collect_unguarded_deletes(tmp_path) == ["bad.py:4  os.remove(p)"]

    (tmp_path / "bad.py").unlink()
    good = tmp_path / "good.py"
    good.write_text(
        "import os\n\ndef f(p):\n    try:\n        os.remove(p)\n"
        "    except (OSError, SystemExit):\n        pass\n",
        encoding="utf-8",
    )
    assert _collect_unguarded_deletes(tmp_path) == [], "已受守卫的删除不得误报"

    # 反向：位于 except 子句里的删除不受该 try 的 handler 保护
    (tmp_path / "good.py").unlink()
    guard_body_only = tmp_path / "guard_body_only.py"
    guard_body_only.write_text(
        "import os\n\ndef f(p):\n    try:\n        pass\n"
        "    except OSError:\n        os.remove(p)\n",
        encoding="utf-8",
    )
    assert _collect_unguarded_deletes(tmp_path) == ["guard_body_only.py:7  os.remove(p)"]

    # 反向：except OSError 接不住 SystemExit，不算守卫
    (tmp_path / "guard_body_only.py").unlink()
    oserror_only = tmp_path / "oserror_only.py"
    oserror_only.write_text(
        "import os\n\ndef f(p):\n    try:\n        os.remove(p)\n"
        "    except OSError:\n        pass\n",
        encoding="utf-8",
    )
    assert _collect_unguarded_deletes(tmp_path) == ["oserror_only.py:5  os.remove(p)"]

    # 豁免标记生效
    (tmp_path / "oserror_only.py").unlink()
    exempt = tmp_path / "exempt.py"
    exempt.write_text(
        "import os\n\ndef f(p):\n    os.remove(p)  # noqa: HOSTILE_DELETE - 假想理由\n",
        encoding="utf-8",
    )
    assert _collect_unguarded_deletes(tmp_path) == []


def test_static_check_detects_import_aliases(tmp_path: Path) -> None:
    """自检（2026-09-11 补盲区）：import 别名形式的删除调用同样必须被抓住。

    原判据只认全限定名，``from os import remove`` / ``import shutil as sh`` /
    ``from shutil import rmtree as rt`` 三类写法全部漏检——红线形同虚设。
    """

    def _scan(name: str, code: str) -> list[str]:
        d = tmp_path / name
        d.mkdir()
        (d / "m.py").write_text(code, encoding="utf-8")
        return _collect_unguarded_deletes(d)

    # ① from os import remove → 裸名直调
    assert _scan("c1", "from os import remove\n\ndef f(p):\n    remove(p)\n") == [
        "m.py:4  remove(p)"
    ]
    # ② from shutil import rmtree as rt → 别名单调
    assert _scan("c2", "from shutil import rmtree as rt\n\ndef f(p):\n    rt(p)\n") == [
        "m.py:4  rt(p)"
    ]
    # ③ import shutil as sh → 模块别名属性调用
    assert _scan("c3", "import shutil as sh\n\ndef f(p):\n    sh.rmtree(p)\n") == [
        "m.py:4  sh.rmtree(p)"
    ]
    # ④ 非删除同名调用（list.remove）不得误报
    assert _scan("c4", "def f(xs):\n    xs.remove(1)\n") == [], "list.remove 不是删除入口"
    # ⑤ 别名调用已受守卫 → 不报
    assert _scan(
        "c5",
        "from os import remove\n\ndef f(p):\n    try:\n        remove(p)\n"
        "    except (OSError, SystemExit):\n        pass\n",
    ) == [], "受守卫的别名删除不得误报"


# ============================================================
# 运行时红线：全部删除入口被拦
# ============================================================
def _raise_system_exit(*_args: object, **_kwargs: object) -> None:
    raise SystemExit(1)


@pytest.fixture()
def hostile_fs(monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟监护人级敌意：所有删除入口都抛 ``SystemExit(1)``（移动操作不受影响）。"""
    for name in ("remove", "unlink", "rmdir"):
        monkeypatch.setattr(os, name, _raise_system_exit, raising=True)
    monkeypatch.setattr(shutil, "rmtree", _raise_system_exit, raising=True)
    monkeypatch.setattr(Path, "unlink", _raise_system_exit, raising=True)
    monkeypatch.setattr(Path, "rmdir", _raise_system_exit, raising=True)


# ---------------- safe_remove：工具层底座 ----------------
def test_safe_remove_file_falls_back_to_rename(tmp_path: Path, hostile_fs: None) -> None:
    """删文件被拦 → 清空 + 改名 .bak，返回 True，不逃逸。"""
    from agent.utils import safe_remove

    f = tmp_path / "draft.wip"
    f.write_text("正文", encoding="utf-8")

    assert safe_remove(f) is True
    assert not f.exists()
    assert (tmp_path / "draft.wip.bak").exists()


def test_safe_remove_dir_falls_back_to_trash(tmp_path: Path, hostile_fs: None) -> None:
    """删目录被拦 → 改名进 .trash/，返回 True，不逃逸。"""
    from agent.utils import safe_remove

    d = tmp_path / "preview"
    (d / "a").mkdir(parents=True)
    (d / "a" / "x.json").write_text("{}", encoding="utf-8")

    assert safe_remove(d) is True
    assert not d.exists()


def test_safe_remove_reports_false_instead_of_raising(tmp_path: Path, hostile_fs: None) -> None:
    """连回退都失败时：只降级为 False + warning，绝不抛错（契约：「绝不中断主流程」）。"""
    from agent.utils import safe_remove

    f = tmp_path / "x.txt"
    f.write_text("x", encoding="utf-8")
    # 回退用的是 os.replace / shutil.move：把这两条也堵死
    with pytest.MonkeyPatch.context() as m:
        m.setattr(os, "replace", _raise_system_exit, raising=True)
        m.setattr(shutil, "move", _raise_system_exit, raising=True)
        with pytest.warns(UserWarning):
            assert safe_remove(f) is False


# ---------------- 原子写：finally 里的清理不得掩盖结果 ----------------
def test_atomic_write_text_commits(tmp_path: Path, hostile_fs: None) -> None:
    from agent.core.infra.atomic import atomic_write_text

    target = tmp_path / "state.json"
    atomic_write_text(target, '{"a": 1}')
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}


def test_atomic_write_set_commits_despite_blocked_cleanup(tmp_path: Path, hostile_fs: None) -> None:
    """多文件提交成功后，finally 里清临时目录被拦也不得让提交结果作废。"""
    from agent.core.infra.atomic import atomic_write_set

    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    assert atomic_write_set({a: "A", b: "B"}) == [a, b]
    assert a.read_text(encoding="utf-8") == "A"
    assert b.read_text(encoding="utf-8") == "B"


def test_atomic_write_set_original_error_not_masked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hostile_fs: None
) -> None:
    """真实异常必须原样透传——不能被清理阶段的 SystemExit 顶掉。"""
    from agent.core.infra.atomic import atomic_write_set

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("模拟写临时文件失败")

    monkeypatch.setattr(Path, "write_bytes", _boom, raising=True)

    with pytest.raises(OSError) as excinfo:
        atomic_write_set({tmp_path / "x.txt": "X"})
    assert not isinstance(excinfo.value, SystemExit), "SystemExit 掩盖了真实 OSError"
    assert not (tmp_path / "x.txt").exists(), "预写阶段失败不得触碰目标"


# ---------------- 状态机落盘（每章/每次转移都走） ----------------
def test_state_machine_save_survives_hostile_delete(tmp_path: Path, hostile_fs: None) -> None:
    from agent.core.engine.state_machine import StateMachine

    sm = StateMachine(tmp_path)
    sm.save()
    assert (tmp_path / ".state" / "state.json").exists()


# ---------------- 解锁 CLI（事故后的排障入口） ----------------
def test_unlock_cli_quarantines_lock(tmp_path: Path, hostile_fs: None) -> None:
    import agent.cli.commands  # noqa: F401  # 触发命令注册
    from agent.cli._app import app
    from typer.testing import CliRunner

    proj = tmp_path / "book"
    (proj / ".state").mkdir(parents=True)
    lock = proj / ".state" / "writer.lock"
    lock.write_text(json.dumps({"pid": 999_999, "command": "dead"}), encoding="utf-8")

    result = CliRunner().invoke(app, ["unlock", "-d", str(proj), "--force"])

    assert result.exit_code == 0, result.output
    assert not lock.exists(), "解锁命令必须真的腾出锁名（改名挪走也算）"


# ---------------- 草稿清理（每章持久化后的热路径） ----------------
def test_draft_clear_survives_hostile_delete(tmp_path: Path, hostile_fs: None) -> None:
    from agent.workflows.evaluation.m18_recovery import DraftManager

    mgr = DraftManager(tmp_path)
    mgr.draft_file.parent.mkdir(parents=True, exist_ok=True)
    mgr.draft_file.write_text("{}", encoding="utf-8")

    assert mgr.clear_draft() is True
    assert not mgr.draft_file.exists()


# ---------------- 设定集回滚（safe_remove + copytree 的合成路径） ----------------
def test_setting_rollback_survives_hostile_delete(tmp_path: Path, hostile_fs: None) -> None:
    from agent.core.story.setting_manager import SettingManager

    sm = SettingManager(tmp_path)
    sm.characters_dir.mkdir(parents=True, exist_ok=True)
    (sm.characters_dir / "lin.md").write_text("旧人设", encoding="utf-8")
    snapshot = sm.create_snapshot("baseline")

    # 快照创建后改动现状：回滚必须能把它覆盖回去
    (sm.characters_dir / "lin.md").write_text("被改坏的现状", encoding="utf-8")

    sm.rollback_to_snapshot(snapshot)  # 不抛即通过

    assert (sm.characters_dir / "lin.md").read_text(encoding="utf-8") == "旧人设"
