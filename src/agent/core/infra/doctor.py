"""健康体检 doctor（增量 F）

只读诊断：分模块检查项目目录 / 状态机 / 设定集(DB) / RAG 索引 / 依赖可用性，
给出修复建议命令（fix_command）。doctor 只读取、绝不修改任何项目文件。

设计要点：
- 阶段感知：依据 ``.state/state.json`` 的 ``state`` 决定"应存在哪些产物"
  （如 WRITING 下 ``chapters/`` 应非空、``foreshadows.md`` 应存在）。
- 文件级校验：``RelationManager`` / ``ForeshadowManager`` 当前是 stub，
  因此仅检查 ``relations/graph.md``、``foreshadows.md`` 等文件的**存在性与
  frontmatter 可解析性**，不做语义级校验（语义级记为 P2）。
- 依赖检查：复用 ``LLMClient.preflight()``（不发起网络调用）；仅当用户加
  ``--ping`` 时才探测 embedding / LLM 端点。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import frontmatter

from agent.core.engine.state_machine import State


@dataclass
class CheckItem:
    """单项健康检查结果

    Attributes:
        module: 所属模块（structure / state / db / rag / deps）
        status: 状态：``ok`` / ``info`` / ``warn`` / ``error``
        detail: 人类可读说明
        fix_command: 修复建议命令（空字符串表示无需处理）
    """

    module: str
    status: str
    detail: str
    fix_command: str = ""


# 各创作阶段「预期应存在的产物」（相对项目根的路径）
# 用作阶段感知校验，避免硬编码散落各处。
STAGE_EXPECTED: dict[State, list[str]] = {
    State.INIT: [],  # 尚未 start，无需产物
    State.CONFIGURING: ["world.md"],
    State.DISCUSSING: ["world.md"],
    State.ARCHITECTING: ["world.md", "architecture.md"],
    State.ARCH_CONFIRMED: [
        "world.md",
        "architecture.md",
        "outline.md",
        "sublines",
        "characters",
        "relations/graph.md",
        "foreshadows.md",
    ],
    State.ARCH_REVISION: [
        "world.md",
        "architecture.md",
        "outline.md",
        "sublines",
        "characters",
        "relations/graph.md",
        "foreshadows.md",
    ],
    State.OUTLINING: [
        "world.md",
        "architecture.md",
        "outline.md",
        "sublines",
        "characters",
        "relations/graph.md",
        "foreshadows.md",
    ],
    State.CHARACTER_DESIGN: [
        "world.md",
        "architecture.md",
        "outline.md",
        "sublines",
        "characters",
        "relations/graph.md",
        "foreshadows.md",
    ],
    State.WRITING: [
        "world.md",
        "architecture.md",
        "outline.md",
        "sublines",
        "characters",
        "relations/graph.md",
        "foreshadows.md",
        "chapters",
    ],
    State.PAUSED: [
        "world.md",
        "architecture.md",
        "outline.md",
        "sublines",
        "characters",
        "relations/graph.md",
        "foreshadows.md",
        "chapters",
    ],
    State.COMPLETED: [
        "world.md",
        "architecture.md",
        "outline.md",
        "sublines",
        "characters",
        "relations/graph.md",
        "foreshadows.md",
        "chapters",
    ],
}


class Doctor:
    """项目健康体检器（只读）"""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)

    # ============================================================
    # 入口
    # ============================================================
    def check(self, *, ping: bool = False) -> list[CheckItem]:
        """执行全量只读诊断

        Args:
            ping: 是否探测 embedding / LLM 端点（默认 False，不联网）

        Returns:
            按模块聚合的 ``CheckItem`` 列表（顺序：structure → state → db → rag → deps）
        """
        checks: list[CheckItem] = []
        checks += self._check_structure()
        checks += self._check_state()
        checks += self._check_db()
        checks += self._check_rag(ping=ping)
        checks += self._check_deps(ping=ping)
        return checks

    @staticmethod
    def is_healthy(checks: list[CheckItem]) -> bool:
        """判断是否健康：无任何 warn / error 即视为健康"""
        return all(c.status in ("ok", "info") for c in checks)

    # ============================================================
    # 结构（阶段感知产物存在性）
    # ============================================================
    def _read_state(self) -> tuple[dict[str, Any] | None, "State | None", str | None]:
        """读取并解析 state.json

        Returns:
            (raw_data, state_enum, error_message)。任一为 None 表示缺失/解析失败。
        """
        state_file = self.project_dir / ".state" / "state.json"
        if not state_file.exists():
            return None, None, None
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return None, None, f"state.json 解析失败：{e}"
        state_val = data.get("state", "")
        try:
            state = State(state_val)
        except ValueError:
            return data, None, f"非法状态值：{state_val!r}"
        return data, state, None

    def _check_structure(self) -> list[CheckItem]:
        state_file = self.project_dir / ".state" / "state.json"
        if not state_file.exists():
            return [
                CheckItem(
                    module="structure",
                    status="info",
                    detail="项目尚未初始化（缺少 .state/state.json）",
                    fix_command=f"novel-agent start -d {self.project_dir}",
                )
            ]

        _data, state, err = self._read_state()
        if err is not None or state is None:
            # 状态机问题交由 _check_state 报告，这里跳过阶段感知产物检查
            return [
                CheckItem(
                    module="structure",
                    status="info",
                    detail="状态机状态异常，跳过阶段感知产物检查（见 state 模块）",
                )
            ]

        expected = STAGE_EXPECTED.get(state, [])
        checks: list[CheckItem] = []
        for rel in expected:
            path = self.project_dir / rel
            if rel == "chapters":
                # chapters 目录需非空
                if not path.exists() or not any(path.glob("ch*.md")):
                    checks.append(
                        CheckItem(
                            module="structure",
                            status="error",
                            detail="已写章节目录 chapters/ 为空（当前处于写作阶段）",
                            fix_command=f"novel-agent write -d {self.project_dir}",
                        )
                    )
                else:
                    checks.append(
                        CheckItem(
                            module="structure",
                            status="ok",
                            detail=f"已写章节 chapters/ 存在（{len(list(path.glob('ch*.md')))} 章）",
                        )
                    )
            elif not path.exists():
                # 缺失产物：给出对应修复命令
                fix = self._fix_for_missing(rel)
                checks.append(
                    CheckItem(
                        module="structure",
                        status="error",
                        detail=f"缺失预期产物：{rel}",
                        fix_command=fix,
                    )
                )
            else:
                checks.append(
                    CheckItem(
                        module="structure",
                        status="ok",
                        detail=f"产物存在：{rel}",
                    )
                )
        return checks

    def _fix_for_missing(self, rel: str) -> str:
        """为缺失产物生成修复命令（含真实项目路径）"""
        table = {
            "world.md": "novel-agent start -d {dir}",
            "architecture.md": "novel-agent architecture -d {dir}",
            "outline.md": "novel-agent outline -d {dir}",
            "sublines": "novel-agent outline -d {dir}",
            "characters": "novel-agent design-characters -d {dir}",
            "relations/graph.md": "novel-agent design-characters -d {dir}",
            "foreshadows.md": "novel-agent design-characters -d {dir}",
            "chapters": "novel-agent write -d {dir}",
        }
        tmpl = table.get(rel, "novel-agent status -d {dir}")
        return tmpl.format(dir=self.project_dir)

    # ============================================================
    # 状态机
    # ============================================================
    def _check_state(self) -> list[CheckItem]:
        state_file = self.project_dir / ".state" / "state.json"
        if not state_file.exists():
            return []
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return [
                CheckItem(
                    module="state",
                    status="error",
                    detail=f"state.json 解析失败：{e}",
                    fix_command=f"novel-agent reset-state -d {self.project_dir}",
                )
            ]
        state_val = data.get("state", "")
        try:
            State(state_val)
        except ValueError:
            return [
                CheckItem(
                    module="state",
                    status="error",
                    detail=f"非法状态值：{state_val!r}",
                    fix_command=f"novel-agent reset-state -d {self.project_dir}",
                )
            ]
        progress = data.get("progress")
        if not isinstance(progress, dict):
            return [
                CheckItem(
                    module="state",
                    status="warn",
                    detail="progress 字段缺失或非 dict（可能影响进度恢复）",
                    fix_command=f"novel-agent write -d {self.project_dir}",
                )
            ]
        return [
            CheckItem(
                module="state",
                status="ok",
                detail=f"状态机合法：state={state_val}",
            )
        ]

    # ============================================================
    # DB（设定集：world/characters/sublines/relations/foreshadows 存在性与可解析）
    # ============================================================
    def _check_db(self) -> list[CheckItem]:
        checks: list[CheckItem] = []

        # world.md frontmatter 可解析
        world = self.project_dir / "world.md"
        if world.exists():
            checks.append(self._parse_check("db", "world.md", world))

        # characters/*.md
        chars_dir = self.project_dir / "characters"
        if chars_dir.exists():
            char_files = list(chars_dir.glob("*.md"))
            if not char_files:
                checks.append(
                    CheckItem(
                        module="db",
                        status="warn",
                        detail="characters/ 目录为空（尚无角色档案）",
                        fix_command=f"novel-agent design-characters -d {self.project_dir}",
                    )
                )
            for f in char_files:
                checks.append(self._parse_check("db", f"characters/{f.name}", f))

        # sublines/*/subline.md
        sublines_dir = self.project_dir / "sublines"
        if sublines_dir.exists():
            sub_files = list(sublines_dir.glob("*/subline.md"))
            if not sub_files:
                checks.append(
                    CheckItem(
                        module="db",
                        status="warn",
                        detail="sublines/ 下无支线设定（subline.md）",
                        fix_command=f"novel-agent outline -d {self.project_dir}",
                    )
                )
            for f in sub_files:
                checks.append(self._parse_check("db", str(f.relative_to(self.project_dir)), f))

        # relations/graph.md
        graph = self.project_dir / "relations" / "graph.md"
        if graph.exists():
            checks.append(self._parse_check("db", "relations/graph.md", graph))

        # foreshadows.md
        fs = self.project_dir / "foreshadows.md"
        if fs.exists():
            checks.append(self._parse_check("db", "foreshadows.md", fs))

        return checks

    def _parse_check(self, module: str, label: str, path: Path) -> CheckItem:
        """尝试解析 markdown frontmatter，返回可解析性 CheckItem"""
        try:
            post = frontmatter.load(path)
            _ = post.metadata
            return CheckItem(module=module, status="ok", detail=f"{label} 可解析")
        except Exception as e:  # noqa: BLE001 - frontmatter 解析失败即报错
            return CheckItem(
                module=module,
                status="error",
                detail=f"{label} 解析失败：{e}",
                fix_command=f"novel-agent frozen-fields -d {self.project_dir}  # 或手动修复 {label}",
            )

    # ============================================================
    # RAG 索引
    # ============================================================
    def _check_rag(self, *, ping: bool = False) -> list[CheckItem]:
        rag_dir = self.project_dir / ".state" / "rag"
        index_file = rag_dir / "index.json"
        if index_file.exists():
            detail = "RAG 语义索引就绪（.state/rag/index.json 存在）"
            if ping:
                probe = self._probe_embed()
                if probe is False:
                    return [
                        CheckItem(
                            module="rag",
                            status="warn",
                            detail="RAG 索引存在，但 embedding 端点不可达（检索降级为 BM25-only）",
                            fix_command="检查 .env 的 EMBEDDING_MODEL_ID / LLM_BASE_URL / LLM_API_KEY",
                        )
                    ]
            return [CheckItem(module="rag", status="ok", detail=detail)]

        # 索引缺失：阶段感知是否推荐建立
        _data, state, _err = self._read_state()
        chapters_dir = self.project_dir / "chapters"
        chapter_count = (
            len(list(chapters_dir.glob("ch*.md"))) if chapters_dir.exists() else 0
        )
        if state in (State.WRITING, State.PAUSED, State.COMPLETED) and chapter_count >= 10:
            return [
                CheckItem(
                    module="rag",
                    status="warn",
                    detail=f"长篇项目（{chapter_count} 章）缺失 RAG 语义索引，跨章召回不可用",
                    fix_command=f"novel-agent reindex -d {self.project_dir}",
                )
            ]
        return [
            CheckItem(
                module="rag",
                status="info",
                detail="RAG 语义索引未建立（可选；长篇章节建议运行 reindex）",
                fix_command=f"novel-agent reindex -d {self.project_dir}",
            )
        ]

    @staticmethod
    def _probe_embed() -> bool:
        """探测 embedding 端点是否可达（仅 --ping 时调用）

        Returns:
            True 表示可达（embed 返回非空向量）；False 表示不可达（返回空）。
        """
        try:
            from agent.client import LLMClient

            vectors = LLMClient().embed(["健康检查探针"])
            return bool(vectors)
        except Exception:  # noqa: BLE001
            return False

    # ============================================================
    # 依赖
    # ============================================================
    def _check_deps(self, *, ping: bool = False) -> list[CheckItem]:
        try:
            from agent.client import LLMClient

            pre = LLMClient().preflight()
        except Exception as e:  # noqa: BLE001
            return [
                CheckItem(
                    module="deps",
                    status="error",
                    detail=f"LLM 客户端初始化失败：{e}",
                    fix_command="检查 .env 的 LLM_PROVIDER / LLM_API_KEY / LLM_BASE_URL",
                )
            ]

        provider = pre.get("provider", "")
        model = pre.get("model", "")
        is_local = pre.get("is_local", False)
        problems: list[str] = []
        if not provider:
            problems.append("provider 未配置")
        if not model:
            problems.append("model 未配置")
        if not is_local and not pre.get("has_fallback") and not _env_has_api_key():
            problems.append("api_key 未配置")

        if problems:
            return [
                CheckItem(
                    module="deps",
                    status="warn",
                    detail="LLM 依赖配置不完整：" + "；".join(problems),
                    fix_command="配置 .env 的 LLM_PROVIDER / LLM_MODEL_ID / LLM_API_KEY",
                )
            ]

        if ping:
            ok = self._probe_endpoint()
            if not ok:
                return [
                    CheckItem(
                        module="deps",
                        status="warn",
                        detail=f"LLM 端点不可达（provider={provider}）",
                        fix_command="检查网络 / LLM_BASE_URL / LLM_API_KEY",
                    )
                ]

        return [
            CheckItem(
                module="deps",
                status="ok",
                detail=f"LLM 依赖就绪（provider={provider}, model={model}）",
            )
        ]

    @staticmethod
    def _probe_endpoint() -> bool:
        """探测 LLM 端点是否可达（仅 --ping 时调用）

        复用 ``LLMClient.chat`` 的一次极简调用；失败即视为不可达。
        """
        try:
            from agent.client import LLMClient

            resp = LLMClient().chat(
                [{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0,
            )
            return bool(resp and resp.text is not None)
        except Exception:  # noqa: BLE001
            return False


def doctor_to_dict(checks: list[CheckItem]) -> list[dict[str, Any]]:
    """将 CheckItem 列表序列化为 dict 列表（供 --json 输出）"""
    return [asdict(c) for c in checks]


def _env_has_api_key() -> bool:
    """检查环境变量 ``LLM_API_KEY`` 是否已配置（供 deps 检查）

    仅看进程环境变量（``load_dotenv`` 默认不覆盖已存在的环境变量，
    因此测试侧 ``monkeypatch.setenv`` 可稳定注入）。
    """
    return bool(os.getenv("LLM_API_KEY", "").strip())
