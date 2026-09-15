"""架构红线：单一真源（Single Source of Truth）。

背景（2026-09-15 缺陷族普查，登记单 `项目文档/优化/20260915_单一真源收敛.md`）
────────────────────────────────────────────────────────────────────
工作区根目录 `D:\\project\\NovelAgent\\` **不是任何 git 仓** —— `agent/` 与
`项目文档/` 是它的两个子仓。于是「看起来属于项目、但不属于某个仓」的东西全部
堆在根目录且无人监管：权威内容出现第二副本 → 副本漂移 → 无声误导后续工作。

已发生实例（9 处，逐一在册）：
  · 根 `AGENTS.md` 与仓内版本分叉（读到过期规则）
  · `agent-repair/`（807MB / 328 dirty）与 `docs-repair/`（含 4 份同名权威文档，
    严重过期）挂在根目录，是整棵旧树 → cp 回主仓即污染（在册 3 次事故）
  · 根 `.agents/notes/` 内 2 份决策记录从未被版本控制
  · `scripts/AGENTS.md` 被 .gitignore 永久吞掉，从未入库

本红线覆盖三类形态（判据是**成员资格**，不是计数配额）：
  M1 仓外副本      test_no_authority_copy_at_workspace_root
  M2 版本控制失明  test_in_repo_docs_are_tracked
  M3 兄弟旧树      test_sibling_worktrees_are_registered / _have_no_zombies
                   / _do_not_outlive_their_remove_by

纪律依据：AGENTS.md 第 14 条（监管必须是否决型）、第 15 条（知识 ≠ 机制）、
第 16 条（单一真源与仓外资产）。
"""

from __future__ import annotations

import subprocess
import warnings
from datetime import date
from pathlib import Path

# `tests/architecture/xxx.py` → parents[0]=architecture, [1]=tests, [2]=agent
REPO_ROOT = Path(__file__).resolve().parents[2]


# ── M1 仓外副本 ───────────────────────────────────────────────────────────
# 工作区根目录**不得出现**与仓内权威资产同名的条目。
# 根目录的正当住户只有：数据目录（novels/ .state/）、工具目录（.workbuddy/
# .pytest_cache/ .trae/）、两个子仓（agent/ 项目文档/）、两个登记在册的
# 兄弟 worktree（见 SIBLING_WORKTREES）。任何"权威文件"出现在这里都是
# 第二副本 —— 它与真源没有任何同步机制，只会漂移。
ROOT_AUTHORITY_DENYLIST = (
    "AGENTS.md",  # 权威规则：唯一真源 = agent/AGENTS.md
    "CLAUDE.md",
    ".cursorrules",
    ".cursor",
    "README.md",
    "pyproject.toml",
    "setup.py",
    "models.json",
    ".agents",  # 决策记录：唯一真源 = agent/.agents/notes/
    "src",
    "tests",
    "llmagent_tests",
)


# ── M2 版本控制失明 ───────────────────────────────────────────────────────
# 仓内这些文件**必须**被 git 跟踪：它们是"给人读的权威说明"，
# 未被跟踪 = 改了没人知道、丢了没人发现（scripts/AGENTS.md 就是这样活了很久）。
VERSION_CONTROLLED_DOC_NAMES = frozenset({"AGENTS.md", "README.md", "TEMPLATE.md"})

# 目录级排除：第三方 / 缓存 / 构建产物，其中的 README 不属于本仓权威内容。
DOC_SCAN_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "node_modules",
        "dist",
        "build",
        "site-packages",
    }
)


# ── M3 兄弟旧树 ───────────────────────────────────────────────────────────
# 与本仓共享对象库、位于工作区根目录旁的 linked worktree，必须**登记在册**。
# 未登记 = FAIL：不允许"旧副本静默堆积"（这正是 cp 污染的源头）。
# status:
#   active            —— 正在使用；仍须登记（显式化即可）
#   deprecated-stale  —— 已停用但未移除；**必须**带 remove_by 到期闸门
# 到期闸门：超过 remove_by 日期仍存在 = FAIL。把"临时容忍"变成"到期硬约束"，
# 防止"暂缓移除"永久化（本项目反复出现的漂移模式）。
SIBLING_WORKTREES: dict[str, dict[str, str]] = {
    "agent-repair": {
        "branch": "release/20260906",
        "purpose": "修复专用工作树（cp 四件套回主仓，AGENTS.md 第 6 条）",
        "status": "deprecated-stale",
        "remove_by": "2026-09-29",
    },
    "docs-repair": {
        "branch": "release/20260906",
        "purpose": "文档修复专用工作树",
        "status": "deprecated-stale",
        "remove_by": "2026-09-29",
    },
}

# 允许的 status 取值（枚举，防自由文本绕过）
ALLOWED_WORKTREE_STATUS = frozenset({"active", "deprecated-stale"})


# ── M1b 根目录运行脚本 ────────────────────────────────────────────────────
# 根目录的运行脚本（*.py / *.bat / *.cmd / *.ps1）不在任何仓内 —— 改了没人知道。
# 它们多数被 **Windows 计划任务的绝对路径**引用，不能随意迁移（沙箱内 schtasks
# 被程序黑名单拦截，改动计划任务需人工执行）。
# 因此采取与 worktree 相同的**成员资格契约**：必须在册并写明用途与阻塞原因。
# blocker 非空 = 当前不可迁移；blocker 为空 = 可迁移项（应尽快迁入 agent/scripts/）。
ROOT_SCRIPT_REGISTRY: dict[str, dict[str, str]] = {
    "monitor_novel.py": {
        "purpose": "小说进度巡检（每小时），由计划任务 NovelAgent_HourlyMonitor 绝对路径引用",
        "blocker": "schtasks 受沙箱程序黑名单限制；迁移需同步 /Change 计划任务",
    },
    "monitor_daemon.py": {
        "purpose": "监控守护进程（后台常驻，按小时检测进度）",
        "blocker": "schtasks 受沙箱程序黑名单限制；与 monitor_novel.py 成对迁移",
    },
    "setup_task.py": {
        "purpose": "注册 Windows 计划任务（调用 schtasks /Create）",
        "blocker": "schtasks 受沙箱程序黑名单限制",
    },
    "create_task.bat": {
        "purpose": "注册 Windows 计划任务（bat 版）",
        "blocker": "schtasks 受沙箱程序黑名单限制",
    },
    "setup_monitor.bat": {
        "purpose": "注册监控计划任务（bat 版）",
        "blocker": "schtasks 受沙箱程序黑名单限制",
    },
    "start_autowrite.bat": {
        "purpose": "启动自动写作（内含 novels/五灵破归档 绝对路径与 writer.lock 清理）",
        "blocker": "含硬编码绝对路径，迁移需一并改路径并更新快捷方式",
    },
    "restart_novelagent.bat": {
        "purpose": "重启 Web/daemon（杀旧进程 → 清锁 → 起新进程，使新代码生效）",
        "blocker": "被日常排障流程直接调用；迁移需同步文档与个人习惯路径",
    },
}

ROOT_SCRIPT_SUFFIXES = (".py", ".bat", ".cmd", ".ps1")


# ── 判据（纯函数，便于单测）──────────────────────────────────────────────
def workspace_root_of(repo_root: Path) -> Path | None:
    """返回工作区根目录；布局不符（如 CI 里的独立克隆）时返回 None。

    认定标准：「父目录下同时存在 agent/AGENTS.md 与 项目文档/，且本仓就是那个
    agent/」。抽成可注入函数，便于用合成目录单测判据本身（防恒真）。
    """
    root = repo_root.parent
    if (root / "agent") != repo_root:
        return None
    if not (root / "agent" / "AGENTS.md").is_file():
        return None
    if not (root / "项目文档").is_dir():
        return None
    return root


def workspace_root() -> Path | None:
    return workspace_root_of(REPO_ROOT)


def authority_copies(root: Path) -> list[str]:
    """根目录下的权威副本（纯函数：给定目录，返回命中的黑名单条目）。"""
    return [name for name in ROOT_AUTHORITY_DENYLIST if (root / name).exists()]


def ignored_dir(name: str) -> bool:
    return name in DOC_SCAN_EXCLUDED_DIRS or name.endswith(".egg-info")


def repo_docs(repo_root: Path) -> set[str]:
    """仓内全部权威文档的相对路径（posix 形式，已排除第三方/缓存目录）。"""
    found: set[str] = set()
    stack = [repo_root]
    while stack:
        current = stack.pop()
        for entry in current.iterdir():
            if entry.is_dir():
                if not ignored_dir(entry.name):
                    stack.append(entry)
            elif entry.name in VERSION_CONTROLLED_DOC_NAMES:
                found.add(entry.relative_to(repo_root).as_posix())
    return found


def tracked_files(repo_root: Path) -> set[str]:
    """git index 中的文件集合。

    git 不可用或返回非零时**抛错**（不返回空集）——按项目纪律，
    辅助证据获取失败绝不能被解读为「没有违规」（AGENTS.md 第 9/15 条）。
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        raise AssertionError(
            f"无法执行 `git ls-files`（{repo_root}）：{exc!r}；"
            "取不到索引就不能判定文档是否被跟踪 —— 视为失败而非通过。"
        ) from exc
    if proc.returncode != 0:  # pragma: no cover
        raise AssertionError(
            f"`git ls-files` 返回 {proc.returncode}：{proc.stderr.strip()[:300]}；"
            "取不到索引就不能判定文档是否被跟踪 —— 视为失败而非通过。"
        )
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def sibling_worktrees(root: Path) -> list[str]:
    """根目录下 `.git` 为**文件**的子目录 —— 即 linked worktree（含子模块）。"""
    found: list[str] = []
    for entry in root.iterdir():
        if entry.is_dir() and (entry / ".git").is_file():
            found.append(entry.name)
    return sorted(found)


def root_scripts(root: Path) -> list[str]:
    """根目录下的运行脚本名（不含子目录）。"""
    return sorted(e.name for e in root.iterdir() if e.is_file() and e.suffix.lower() in ROOT_SCRIPT_SUFFIXES)


# ── 测试 ─────────────────────────────────────────────────────────────────
class TestNoAuthorityCopyOutsideRepo:
    """M1：权威资产只能有一份，且必须在受版本控制的仓内。"""

    def test_no_authority_copy_at_workspace_root(self) -> None:
        root = workspace_root()
        if root is None:
            return  # 独立克隆：不适用
        copies = authority_copies(root)
        assert not copies, (
            "工作区根目录出现「权威内容副本」——根目录不是仓，副本没有同步机制，"
            "只会漂移后无声误导（曾因此读到过期的 AGENTS.md）：\n"
            + "\n".join(f"  {root / name}" for name in copies)
            + "\n真源：AGENTS.md→agent/AGENTS.md；.agents/→agent/.agents/notes/。"
            "\n处置：删除副本，或改成一行指针指向真源。"
        )


class TestRootScriptsAreRegistered:
    """M1b：根目录运行脚本必须在册（它们不在任何仓内，改无记录）。"""

    def test_root_scripts_are_registered(self) -> None:
        root = workspace_root()
        if root is None:
            return
        unregistered = [n for n in root_scripts(root) if n not in ROOT_SCRIPT_REGISTRY]
        assert not unregistered, (
            "根目录出现未登记的运行脚本 —— 它不在任何仓内，改了没人知道、丢了没人发现：\n"
            + "\n".join(f"  {root / n}" for n in unregistered)
            + "\n处置二选一：(a) 登记进 ROOT_SCRIPT_REGISTRY（写明 purpose 与 blocker）；"
            "\n           (b) 迁入版本控制（`agent/scripts/`，必要时在 .gitignore 加负向规则）。"
        )

    def test_root_script_registry_has_no_zombies(self) -> None:
        root = workspace_root()
        if root is None:
            return
        on_disk = set(root_scripts(root))
        zombies = sorted(n for n in ROOT_SCRIPT_REGISTRY if n not in on_disk)
        assert not zombies, (
            "ROOT_SCRIPT_REGISTRY 存在僵尸条目（已不在磁盘上）：\n"
            + "\n".join(f"  {n}" for n in zombies)
            + "\n处置：文件已迁走或删除后，请同步删除登记条目。"
        )

    def test_registry_entries_explain_themselves(self) -> None:
        problems = [
            f"  {n}: 缺 {field}"
            for n, meta in ROOT_SCRIPT_REGISTRY.items()
            for field in ("purpose", "blocker")
            if not meta.get(field)
        ]
        assert not problems, (
            "ROOT_SCRIPT_REGISTRY 条目必须写明 purpose（它做什么）与 blocker（为何还不能迁）：\n"
            + "\n".join(problems)
        )

    def test_root_script_judge_ignores_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        (tmp_path / "notes.md").write_text("x", encoding="utf-8")
        assert root_scripts(tmp_path) == []
        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        (tmp_path / "b.BAT").write_text("x", encoding="utf-8")
        assert root_scripts(tmp_path) == ["a.py", "b.BAT"]


class TestDocsAreVersionControlled:
    """M2：仓内权威文档必须被 git 看见。"""

    def test_in_repo_docs_are_tracked(self) -> None:
        present = repo_docs(REPO_ROOT)
        tracked = tracked_files(REPO_ROOT)
        missing = sorted(present - tracked)
        assert not missing, (
            f"{len(missing)} 份仓内文档存在于磁盘却未被 git 跟踪 —— 改了没人知道、"
            "丢了没人发现（scripts/AGENTS.md 曾长期如此，被 .gitignore 永久吞掉）：\n"
            + "\n".join(f"  {p}" for p in missing)
            + "\n处置：加进版本控制（必要时在 .gitignore 加负向规则，如 `!scripts/*.md`），"
            "或确认它不该存在后删除。"
        )

    def test_judge_sees_known_tracked_docs(self) -> None:
        """防退化：判据必须能看见已知被跟踪的文档（否则断言恒真）。"""
        tracked = tracked_files(REPO_ROOT)
        assert "AGENTS.md" in tracked, (
            "判据取到的索引里没有 AGENTS.md —— `git ls-files` 很可能没在仓根执行，"
            "本类断言会退化成恒真。"
        )


class TestSiblingWorktreesAreRegistered:
    """M3：兄弟旧树必须显式登记，且到期必须清偿。"""

    def test_sibling_worktrees_are_registered(self) -> None:
        root = workspace_root()
        if root is None:
            return
        on_disk = sibling_worktrees(root)
        unregistered = [name for name in on_disk if name not in SIBLING_WORKTREES]
        assert not unregistered, (
            "发现未登记的兄弟 worktree —— 它们是整棵旧树的副本，"
            "cp 回主仓即污染（在册 3 次事故，代价是「测试全绿上线 NameError」）：\n"
            + "\n".join(f"  {root / name}" for name in unregistered)
            + "\n处置二选一：(a) 登记进 tests/architecture/test_single_source_of_truth.py"
            " 的 SIBLING_WORKTREES（含 branch / purpose / status / remove_by）；"
            "\n           (b) 移除：`git worktree remove <path>`"
            "（分支与提交仍在对象库，随时可 add 回来）。"
        )

    def test_registry_has_no_zombies(self) -> None:
        root = workspace_root()
        if root is None:
            return
        on_disk = set(sibling_worktrees(root))
        zombies = sorted(name for name in SIBLING_WORKTREES if name not in on_disk)
        assert not zombies, (
            "SIBLING_WORKTREES 存在僵尸条目（已不在磁盘上）—— 登记表失真比没有表更危险：\n"
            + "\n".join(f"  {name}" for name in zombies)
            + "\n处置：移除磁盘后请同步删除登记条目。"
        )

    def test_registry_entries_are_well_formed(self) -> None:
        problems: list[str] = []
        for name, meta in SIBLING_WORKTREES.items():
            status = meta.get("status")
            if status not in ALLOWED_WORKTREE_STATUS:
                problems.append(f"  {name}: status={status!r} 不在枚举 {sorted(ALLOWED_WORKTREE_STATUS)}")
            if not meta.get("branch"):
                problems.append(f"  {name}: 缺 branch（无法判断它与主分支的关系）")
            if not meta.get("purpose"):
                problems.append(f"  {name}: 缺 purpose（无法判断它为何还活着）")
            if status == "deprecated-stale" and not meta.get("remove_by"):
                problems.append(f"  {name}: status=deprecated-stale 必须带 remove_by 到期日")
        assert not problems, "SIBLING_WORKTREES 条目不合规：\n" + "\n".join(problems)

    def test_worktrees_do_not_outlive_their_remove_by(self) -> None:
        """到期闸门：把「临时容忍」变成「到期硬约束」。"""
        root = workspace_root()
        if root is None:
            return
        on_disk = set(sibling_worktrees(root))
        expired: list[str] = []
        for name, meta in SIBLING_WORKTREES.items():
            if name not in on_disk or meta.get("status") != "deprecated-stale":
                continue
            deadline = date.fromisoformat(meta["remove_by"])
            if date.today() > deadline:
                expired.append(f"  {name}: remove_by={deadline} 已过期，仍在磁盘上")
        assert not expired, (
            "兄弟旧树已过清偿期限还挂在根目录 —— 「暂缓移除」正在永久化：\n"
            + "\n".join(expired)
            + "\n处置二选一：(a) `git worktree remove <path>`（推荐，分支与提交不丢）；"
            "\n           (b) 确有用途则延长 remove_by 并在登记单写明理由。"
        )


class TestJudgementIsSemantic:
    """判据本身的行为 —— 防退化成「恒真」或「字符串计数」。"""

    def test_authority_copy_judge_fires(self, tmp_path: Path) -> None:
        assert authority_copies(tmp_path) == []
        (tmp_path / "AGENTS.md").write_text("stale copy", encoding="utf-8")
        (tmp_path / ".agents").mkdir()
        assert sorted(authority_copies(tmp_path)) == [".agents", "AGENTS.md"]

    def test_authority_copy_judge_ignores_legitimate_root_dwellers(self, tmp_path: Path) -> None:
        for name in ("novels", ".state", ".workbuddy", "agent", "项目文档", "monitor_novel.py"):
            (tmp_path / name).mkdir()
        assert authority_copies(tmp_path) == []

    def test_doc_scan_respects_excluded_dirs(self, tmp_path: Path) -> None:
        (tmp_path / ".venv" / "lib").mkdir(parents=True)
        (tmp_path / ".venv" / "lib" / "README.md").write_text("third party", encoding="utf-8")
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "AGENTS.md").write_text("mine", encoding="utf-8")
        assert repo_docs(tmp_path) == {"scripts/AGENTS.md"}

    def test_sibling_worktree_judge_reads_git_file(self, tmp_path: Path) -> None:
        (tmp_path / "real-repo").mkdir()
        (tmp_path / "real-repo" / ".git").mkdir()  # 主工作树：.git 是目录
        (tmp_path / "linked").mkdir()
        (tmp_path / "linked" / ".git").write_text("gitdir: /x/.git/worktrees/linked", encoding="utf-8")
        assert sibling_worktrees(tmp_path) == ["linked"]

    def test_workspace_root_detection_is_not_blind(self, tmp_path: Path) -> None:
        """判据本身必须能分辨「标准工作区 / 非标准目录」——防恒真。"""
        repo = tmp_path / "agent"
        repo.mkdir()
        (repo / "AGENTS.md").write_text("x", encoding="utf-8")
        # 正例：父目录含 项目文档/ → 认定为工作区
        assert workspace_root_of(repo) is None, "缺 项目文档/ 时不应认定为工作区"
        (tmp_path / "项目文档").mkdir()
        assert workspace_root_of(repo) == tmp_path
        # 反例：本仓不是父目录下的 agent/（例如独立克隆到别处）
        elsewhere = tmp_path / "deep" / "agent"
        elsewhere.mkdir(parents=True)
        (elsewhere / "AGENTS.md").write_text("x", encoding="utf-8")
        assert workspace_root_of(elsewhere) is None

    def test_workspace_root_resolves_or_warns(self) -> None:
        """真实工作区里 M1/M3 必须生效；解析不出时必须**出声**（不许静默失明）。"""
        if workspace_root() is not None:
            return
        warnings.warn(
            f"单一真源红线的 M1（仓外副本）/ M3（兄弟旧树）在本次运行中未生效："
            f"未能从 {REPO_ROOT} 解析出标准工作区布局（缺 同级 项目文档/ 或不在 agent/ 下）。"
            "CI 独立克隆属预期；但若在真实工作区运行，说明红线失明 —— 请修 "
            "workspace_root_of() 的判据。",
            RuntimeWarning,
            stacklevel=1,
        )
