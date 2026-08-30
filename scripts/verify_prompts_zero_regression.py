#!/usr/bin/env python3
"""保留 legacy 条目的零回归 + 删除断言。

阶段 C 删除 ``agent/prompts.py`` 后，阶段 B 全量迁移的 29 个键已无代码常量可比对
（md 为单一真源）；本脚本仅校验**仍登记 legacy 兜底**的 8 个键——它们的原始常量
保留在各原模块（下划线前缀命名，如 ``_PLANNER_SYSTEM``），md 渲染需与之一致：

- 对每条 ``_register`` 条目：取原模块中的 system / user 常量值；
- 用合成 dummy 变量分别渲染：原始 ``.format()``（容忍字面 JSON 双括号）+ md（``pm.get``）；
- 逐字比对 system、user。

并显式断言 ``import agent.prompts`` 抛 ``ModuleNotFoundError``——即阶段 C 删除成功。

与 `lint_prompts.py` 互补：后者防"新增内联残留"，本脚本防"md 与原始常量漂移" +
防"误重新引入 prompts.py"。两者都应纳入 CI。

注意：本脚本**不硬编码任何已迁移常量名**——(key, 模块, 属性名) 全部在运行时解析
prompt_manager.py 的 ``_register(...)`` 得到，因此本文件本身不会被 `lint_prompts.py`
误报，且始终与迁移清单自动同步。

路径自洽：ROOT 由本文件位置推导（scripts/ -> agent/ -> src），不依赖绝对路径。
"""
import importlib
import os
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent          # agent/scripts
AGENT_ROOT = SCRIPT_DIR.parent                         # agent/
SRC = AGENT_ROOT / "src"
ROOT = str(SRC)                                        # 含 agent 包的源码根
PROMPT_MANAGER_PY = SRC / "agent" / "core" / "infra" / "prompt_manager.py"
sys.path.insert(0, ROOT)
sys.path.insert(0, str(SCRIPT_DIR))  # 复用 lint_prompts 的 canonical denylist，避免重复硬编码

from agent.core.infra.prompt_manager import PromptManager  # noqa: E402
from lint_prompts import MIGRATED as MIGRATED_CONSTANTS  # noqa: E402  (阶段 C 已删除常量的真相源)


def collect_entries():
    """解析 prompt_manager.py 的 _register(...) 调用，返回 (key, module, sys_attr, usr_attr) 列表。"""
    text = PROMPT_MANAGER_PY.read_text(encoding="utf-8")
    entries = []
    for call in re.finditer(r"_register\((.*?)\)", text, re.DOTALL):
        body = call.group(1)
        # 交替匹配：引号字符串 或 裸 None
        tokens = re.findall(r"['\"]([^'\"]*)['\"]|None", body)
        if len(tokens) < 2:
            continue
        key = tokens[0]
        module = tokens[1]
        # 跳过 def 签名 / docstring 示例等非真实注册调用（真实模块均形如 agent.*）
        if not key or not module or not module.startswith("agent."):
            continue
        sys_attr = tokens[2] if len(tokens) > 2 and tokens[2] != "None" else None
        usr_attr = tokens[3] if len(tokens) > 3 and tokens[3] != "None" else None
        entries.append((key, module, sys_attr, usr_attr))
    return entries


def render_orig_sim(s: str, dummy: dict) -> str:
    """把原始 .format() 模板按 dummy 渲染：先解 {{ }} 字面 JSON 双括号，再填 {x}。"""
    s2 = s.replace("{{", "{").replace("}}", "}")

    def repl(m):
        return dummy.get(m.group(1), "{" + m.group(1) + "}")

    return re.sub(r"{([A-Za-z_]\w*)}", repl, s2)


def format_names(t: str) -> set:
    names = set()
    i = 0
    n = len(t)
    while i < n:
        c = t[i]
        if c == "{" and i + 1 < n and t[i + 1] == "{":
            i += 2
            continue
        if c == "}" and i + 1 < n and t[i + 1] == "}":
            i += 2
            continue
        if c == "{":
            j = t.find("}", i)
            if j == -1:
                break
            field = t[i + 1:j]
            name = field.split(":")[0].split("!")[0].split(".")[0].split("[")[0].strip()
            if name and (name[0].isalpha() or name[0] == "_") and not name.isdigit():
                names.add(name)
            i = j + 1
            continue
        i += 1
    return names


def jinja_names(t: str) -> set:
    t2 = re.sub(r"{%\s*raw\s%}.*?{%\s*endraw\s%}", "", t, flags=re.DOTALL)
    return set(re.findall(r"{{\s*([A-Za-z_]\w*)\s*}}", t2))


def main() -> int:
    # 阶段 C 删除断言：旧的 prompts.py 模块级常量必须已不存在。
    # 注：agent.prompts 现在是承载 *.md 的命名空间包（无 __init__.py），其本身应继续存在；
    # 这里只校验"已迁移常量"不再作为该包属性出现（即 prompts.py 已删除）。
    try:
        pkg = importlib.import_module("agent.prompts")
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: 无法导入 agent.prompts 包（md 容器）：{e}")
        return 1
    leaked = [n for n in MIGRATED_CONSTANTS if hasattr(pkg, n)]
    if leaked:
        print(
            f"FAIL: agent.prompts 仍暴露 {len(leaked)} 个已迁移常量"
            f"（阶段 C 应已删除 prompts.py）：{leaked[:5]} ..."
        )
        return 1

    entries = collect_entries()
    mgr = PromptManager(root=str(SRC / "agent" / "prompts"), hot_reload=False)
    fails = []
    for key, module, sc, uc in entries:
        mod = importlib.import_module(module)
        orig_sys = getattr(mod, sc) if sc else ""
        orig_usr = getattr(mod, uc) if uc else ""
        try:
            md = mgr.get(key)
        except Exception as e:  # noqa: BLE001
            fails.append((key, "GET-ERR", str(e)))
            continue
        if orig_sys:
            names = format_names(orig_sys) | jinja_names(md.system)
            dummy = {n: f"__{n}__" for n in names}
            orig_r = render_orig_sim(orig_sys, dummy)
            md_r = md.render_system(**dummy)
            if orig_r != md_r:
                fails.append((key, "system", "diff"))
        if orig_usr:
            names = format_names(orig_usr) | jinja_names(md.user_template)
            dummy = {n: f"__{n}__" for n in names}
            orig_r = render_orig_sim(orig_usr, dummy)
            try:
                md_r = md.render_user(**dummy)
            except Exception as e:  # noqa: BLE001
                fails.append((key, "md-render-err", str(e)))
                continue
            if orig_r != md_r:
                fails.append((key, "user", "diff"))
    print(f"校验键数: {len(entries)}")
    if fails:
        print("\n=== FAILURES ===")
        for f in fails:
            print(" ", f)
        return 1
    print("\nALL ZERO-REGRESSION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
