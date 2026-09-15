#!/usr/bin/env python
"""单一真源巡检（SSOT audit）—— AGENTS.md 第 16 条指定入口。

背景
────
工作区根目录 `D:\\project\\NovelAgent\\` **不是任何 git 仓**：`agent/`（代码仓）
与 `项目文档/`（文档仓）是它的两个子仓。因此「看起来属于项目、但不属于某个仓」
的东西全部堆在根目录且无人监管，随时间演化为**权威内容的第二副本**，副本漂移
后无声误导后续工作（读过期的 `AGENTS.md`、cp 旧树污染主仓……）。

本工具做**全谱**巡检（红线只做秒级必须拦的那几条，这里做完整画像）：

  1. 根目录资产归属      —— 每个条目属哪个仓 / 是否无主
  2. 兄弟 worktree 画像  —— 分支、落后 master 多少提交、dirty 数、体积
  3. 登记表一致性        —— 只读红线里的 SIBLING_WORKTREES（单一来源，不复制）
  4. 版本控制失明        —— 仓内 AGENTS/README/TEMPLATE 是否都被 git 看见

用法
────
    python scripts/ssot_audit.py            # 从脚本位置推断工作区根目录
    python scripts/ssot_audit.py --root D:/project/NovelAgent
    python scripts/ssot_audit.py --deep     # 额外统计各 worktree 的 dirty 文件数

退出码：0 = 无异常；1 = 存在无主资产 / 未登记 worktree / 失明文档。
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from datetime import date
from pathlib import Path

SCRIPT = Path(__file__).resolve()
REPO_ROOT = SCRIPT.parents[1]  # agent/
REGISTRY_SOURCE = REPO_ROOT / "tests" / "architecture" / "test_single_source_of_truth.py"

# 根目录的**正当住户**：工具目录 / 数据目录 / 两个子仓。
# 凡不在此列、也不在 SIBLING_WORKTREES 登记表里的条目 = 无主资产。
LEGITIMATE_ROOT_ENTRIES = {
    "agent": "代码仓（agent/AGENTS.md 为权威规则）",
    "项目文档": "文档仓（四份权威文档 + 优化登记）",
    "novels": "小说数据目录",
    ".workbuddy": "工具目录（记忆 / 报告 / 诊断）",
    ".state": "运行状态",
    ".pytest_cache": "测试缓存",
    ".trae": "IDE 工作区目录",
}

OK = "[ OK ]"
WARN = "[WARN]"
FAIL = "[FAIL]"


def run_git(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{exc!r}"
    return proc.returncode, (proc.stdout or proc.stderr).strip()


def load_registry(name: str) -> dict[str, dict[str, str]]:
    """从红线文件读取具名登记表 —— **单一来源**，避免工具与红线两处定义分叉。

    读不到 / 解析不出**必须抛错**：按项目纪律，辅助证据失败不能被当成「无问题」。
    """
    if not REGISTRY_SOURCE.is_file():
        raise SystemExit(f"{FAIL} 找不到登记表来源：{REGISTRY_SOURCE}")
    tree = ast.parse(REGISTRY_SOURCE.read_text(encoding="utf-8-sig"))

    def matches(target: ast.expr) -> bool:
        return isinstance(target, ast.Name) and target.id == name

    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and matches(node.target):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(matches(t) for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit(f"{FAIL} 在 {REGISTRY_SOURCE} 中找不到 {name} 定义")


def load_registries() -> tuple[
    dict[str, dict[str, str]],
    dict[str, dict[str, str]],
    dict[str, dict[str, str]],
]:
    return (
        load_registry("SIBLING_WORKTREES"),
        load_registry("ROOT_SCRIPT_REGISTRY"),
        load_registry("NESTED_REPO_ALLOWLIST"),
    )


def real_repo_roots(root: Path) -> list[Path]:
    """工作区内的真仓根（`.git` 是目录）；linked worktree（`.git` 是文件）由第 2 节管。"""
    return sorted(e for e in root.iterdir() if e.is_dir() and (e / ".git").is_dir())


def nested_repos(repo_root: Path) -> list[str]:
    """仓内嵌套的独立仓（相对仓根 posix 路径）。"""
    found: list[str] = []
    stack = [repo_root]
    while stack:
        current = stack.pop()
        for entry in current.iterdir():
            if not entry.is_dir():
                continue
            if entry.name == ".git":
                if entry.parent != repo_root:
                    found.append(entry.parent.relative_to(repo_root).as_posix())
                continue
            if entry.name not in {".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", "site-packages", "dist", "build"}:
                stack.append(entry)
    return sorted(found)


def dir_size(path: Path) -> str:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return f"{total / 1024 / 1024:.1f} MB"


def is_linked_worktree(path: Path) -> bool:
    return path.is_dir() and (path / ".git").is_file()


def section(title: str) -> None:
    print(f"\n{'─' * 66}\n{title}\n{'─' * 66}")


def audit_root(
    root: Path,
    registry: dict[str, dict[str, str]],
    scripts: dict[str, dict[str, str]],
) -> list[str]:
    section(f"1. 根目录资产归属  ({root})")
    problems: list[str] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        name = entry.name
        if name in registry:
            meta = registry[name]
            status = meta.get("status", "?")
            deadline = meta.get("remove_by", "—")
            print(f"  {OK}  {name:20s} 登记 worktree [{status}]  分支={meta.get('branch', '?')}  remove_by={deadline}")
        elif name in scripts:
            meta = scripts[name]
            print(f"  {OK}  {name:20s} 登记脚本 —— {meta.get('purpose', '?')}")
            print(f"  {'':4s}{'':20s} 阻塞迁移原因：{meta.get('blocker', '?')}")
        elif name in LEGITIMATE_ROOT_ENTRIES:
            print(f"  {OK}  {name:20s} {LEGITIMATE_ROOT_ENTRIES[name]}")
        elif is_linked_worktree(entry) or entry.is_file():
            print(f"  {FAIL} {name:20s} ★ 无主资产（不在白名单、也不在任一张登记表中）")
            problems.append(f"无主资产：{entry}")
        else:
            print(f"  {WARN} {name:20s} 未分类目录（请确认归属后补进白名单或登记表）")
            problems.append(f"未分类目录：{entry}")

    unregistered = [e.name for e in root.iterdir() if is_linked_worktree(e) and e.name not in registry]
    if unregistered:
        problems.append(f"未登记的兄弟 worktree：{', '.join(unregistered)}")

    for label, table in (("worktree", registry), ("脚本", scripts)):
        stale = [n for n in table if not (root / n).exists()]
        if stale:
            print(f"  {FAIL} {label}登记表僵尸条目：{', '.join(stale)}")
            problems.append(f"{label}登记表僵尸条目：{', '.join(stale)}")
    return problems


def audit_worktrees(root: Path, registry: dict[str, dict[str, str]], deep: bool) -> list[str]:
    section("2. 兄弟 worktree 画像（divergence / 体积）")
    problems: list[str] = []
    for name, meta in sorted(registry.items()):
        path = root / name
        if not path.is_dir():
            print(f"  {WARN} {name}: 不在磁盘上（僵尸条目）")
            continue
        branch = meta.get("branch", "")
        code, lag = run_git(["rev-list", "--count", f"{branch}..master"], REPO_ROOT)
        lag_txt = lag if code == 0 else f"取不到（{lag[:60]}）"
        if code != 0:
            problems.append(f"{name}: 无法计算落后提交数")
        dirty = "未统计"
        if deep:
            dcode, dout = run_git(["status", "--porcelain"], path)
            dirty = str(len([ln for ln in dout.splitlines() if ln.strip()])) if dcode == 0 else f"取不到（{dout[:40]}）"
            if dcode != 0:
                problems.append(f"{name}: 无法统计 dirty 文件数")
        print(f"  {name}")
        print(f"    分支 {branch} | 落后 master {lag_txt} 个提交 | dirty {dirty} | 体积 {dir_size(path)}")
        print(f"    用途：{meta.get('purpose', '?')} | status={meta.get('status', '?')} | remove_by={meta.get('remove_by', '—')}")
        print(f"    ⚠ 该目录内全部内容为旧快照 —— 只可读用于对照，**禁止 cp 回主仓**（曾 3 次污染事故）")
    return problems


def audit_nested_repos(root: Path, allowlist: dict[str, dict[str, str]]) -> list[str]:
    section("4. 嵌套仓库（住在本仓内的独立 git 仓）")
    problems: list[str] = []
    seen: set[str] = set()
    for repo in real_repo_roots(root):
        for rel in nested_repos(repo):
            key = f"{repo.name}/{rel}"
            seen.add(key)
            meta = allowlist.get(key)
            if meta:
                print(f"  {WARN} {key}  已登记（待处置）")
                print(f"        理由：{meta.get('reason', '?')} | remove_by={meta.get('remove_by', '—')}")
            else:
                print(f"  {FAIL} {key}  ★ 未登记 —— 该子树对父仓完全不可见")
                problems.append(f"未登记的嵌套仓：{key}")

    for key, meta in sorted(allowlist.items()):
        if key not in seen:
            print(f"  {FAIL} 登记表僵尸条目：{key}")
            problems.append(f"嵌套仓登记表僵尸条目：{key}")
            continue
        deadline = meta.get("remove_by")
        if deadline and date.today() > date.fromisoformat(deadline):
            print(f"  {FAIL} {key}  已过 remove_by={deadline}")
            problems.append(f"嵌套仓超过清偿期限：{key}")

    if not seen:
        print(f"  {OK}  两个仓内均无嵌套独立仓")
    return problems


def audit_blind_docs(repo_root: Path) -> list[str]:
    section("3. 版本控制失明（仓内文档是否被 git 看见）")
    problems: list[str] = []
    code, out = run_git(["ls-files"], repo_root)
    if code != 0:
        print(f"  {FAIL} 无法执行 git ls-files：{out[:120]}")
        return [f"git ls-files 失败：{out[:80]}"]
    tracked = {ln.strip() for ln in out.splitlines() if ln.strip()}
    names = {"AGENTS.md", "README.md", "TEMPLATE.md"}
    excluded = {".venv", "venv", "__pycache__", ".pytest_cache", "node_modules", "dist", "build", "site-packages", ".git"}
    missing: list[str] = []
    stack = [repo_root]
    while stack:
        cur = stack.pop()
        for item in cur.iterdir():
            if item.is_dir():
                if item.name not in excluded:
                    stack.append(item)
            elif item.name in names:
                rel = item.relative_to(repo_root).as_posix()
                if rel not in tracked:
                    missing.append(rel)
    for rel in sorted(missing):
        print(f"  {FAIL} 存在于磁盘但未被跟踪：{rel}")
        problems.append(f"未被跟踪的文档：{rel}")
    if not missing:
        print(f"  {OK}  仓内 AGENTS/README/TEMPLATE 全部被跟踪（共 {len(tracked)} 个受控文件）")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="单一真源巡检（AGENTS.md 第 16 条）")
    parser.add_argument("--root", default=None, help="工作区根目录（默认从脚本位置推断）")
    parser.add_argument("--deep", action="store_true", help="统计各 worktree 的 dirty 文件数（只读 git status）")
    args = parser.parse_args()

    root = Path(args.root).resolve() if args.root else REPO_ROOT.parent
    registry, scripts, nested = load_registries()

    print("=" * 66)
    print("单一真源巡检 SSOT audit")
    print(f"工作区根：{root}")
    print(f"登记表来源：{REGISTRY_SOURCE.relative_to(REPO_ROOT.parent)}（单一来源，不复制）")
    print("=" * 66)

    problems: list[str] = []
    if not (root / "agent").is_dir() or not (root / "项目文档").is_dir():
        print(f"{FAIL} 布局不符：根目录下应有 agent/ 与 项目文档/，实际缺一。")
        return 1
    problems += audit_root(root, registry, scripts)
    problems += audit_worktrees(root, registry, args.deep)
    problems += audit_blind_docs(REPO_ROOT)
    problems += audit_nested_repos(root, nested)

    section("结论")
    if problems:
        print(f"{FAIL} {len(problems)} 项待处理：")
        for item in problems:
            print(f"  · {item}")
        return 1
    print(f"{OK}  未发现单一真源问题：根目录无无主资产、worktree 全部登记、无失明文档、无未登记嵌套仓。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
