"""M5 写章共享工具方法库

写章闭环（上下文装配 → 生成 → 质量闸 → 净化 → 落盘）已收敛为**唯一入口**
``AgenticWriteWorkflow``（``workflows/writing/agentic_write.py``）；本模块
**不再提供服务端的写章入口**。

历史沿革
--------
- **R2-C（2026-09-06）**：``M5WriteChapterWorkflow.run()`` 标记 @deprecated，
  仅作测试基线保留、生产零调用；
- **2026-09-16**（登记单 ``20260916_闸门信号可达性普查`` §三.C2）：该废弃入口
  **连同其专属链路一并删除** —— ``run()`` / ``_generate_chapter`` /
  ``_maybe_deslop`` / ``M5Result`` / ``mode_controller`` property。
  删除而非补丁的理由：入口内的 ``_quality_check_and_revise`` 在质检 JSON 解析
  失败时降级 ``overall_pass=True`` 且**无任何留痕**，而该路径**只有废弃入口可达**。

  ⚠ **删除前置条件**：``_maybe_deslop`` 内的 **L1 禁词硬拦截 + ``l1_trace`` 轨迹
  落盘** 此前**从未随迁**到生产入口（与 2026-09-11 G15 ``_archive_chapter``
  同构：收敛丢能力，且被「整方法豁免」掩盖）。故**先迁移到
  ``AgenticWriteWorkflow._run_deslop``，再删除**（红线
  ``tests/test_l1_hard_block_reachability.py``）。

保留本类的唯一目的：作为**共享工具方法库**，供 ``AgenticWriteWorkflow`` 复用
确定性、已验证的实现：
    m5_context.py      上下文装配（M5ContextMixin）
    m5_quality_gate.py 评审校准常量 + ``_stage_calibration``（原质量闸主体已删）
    m5_text_hygiene.py 文本净化 / 去重（M5TextHygieneMixin + 模块级净化函数）
    m5_persist.py      依据链 / 持久化 / 归档 / 进度（M5PersistMixin）

⚠️ 新增代码禁止在本类上新增写章入口；写章请经 ``AgenticWriteWorkflow``。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

from rich.console import Console

from agent.core.base.exceptions import PreValidationBlocked  # noqa: F401 - re-export（agentic_write 由此导入）
from agent.core.engine.state_machine import StateMachine
from agent.core.engine.workflow_registry import workflow
from agent.core.quality.consistency import ConflictArbiter
from agent.core.quality.scoring import QualityChecker
from agent.core.registry.genre_pack import GenrePackRegistry
from agent.core.story.injected_trope_store import InjectedTropeStore
from agent.core.story.setting_manager import SettingManager
from agent.client.gateway_adapter import create_gateway
from llmagent.gateway import Gateway

from agent.workflows.writing.m5_context import M5ContextMixin
from agent.workflows.writing.m5_persist import M5PersistMixin, PreValidationResult  # noqa: F401 - re-export
from agent.workflows.writing.m5_quality_gate import (  # noqa: F401 - re-export
    GOLDEN_WRITE_GATE_FIRST_N,
    MAX_REVISIONS,
    M5QualityGateMixin,
)
from agent.workflows.writing.m5_text_hygiene import (  # noqa: F401 - re-export 兼容旧导入
    M5TextHygieneMixin,
    _auto_split_paragraphs,
    _collapse_cjk_spaces,
    _strip_frontmatter,
    hard_replace_english,
    scan_english_contamination,
)

logger = logging.getLogger(__name__)


@workflow("m5_write_chapter")
class M5WriteChapterWorkflow(
    M5ContextMixin, M5QualityGateMixin, M5TextHygieneMixin, M5PersistMixin
):
    """M5 写章共享工具方法库（写章入口已收敛为 ``AgenticWriteWorkflow``）"""

    def __init__(
        self,
        project_dir: Path,
        llm_client: Gateway | None = None,
        setting_manager: SettingManager | None = None,
        state_machine: StateMachine | None = None,
        console: Console | None = None,
        mode_controller: "ModeController | None" = None,
        conflict_arbiter: ConflictArbiter | None = None,
        pre_validate: bool = True,
        genre_registry: GenrePackRegistry | None = None,
        enable_structured_qc: bool = False,
        strict_review: bool = False,
        # ---- G9 新增参数：章内子阶段事件（默认 None 零开销；由 pipeline 注入）----
        event_emitter: Callable[[dict[str, Any]], None] | None = None,
        # ---- G11 新增参数：风格模仿（默认开：project/style.md 存在即注入）----
        style_enabled: bool = True,
        style_file: str | None = None,
        # ---- G12 新增参数：爽点剧本/情绪目标注入（默认开：.state/payoff_script.json 存在即注入）----
        payoff_enabled: bool = True,
        # ---- P0 新增参数：去AI味（默认开；--no-deslop 关闭）----
        deslop_enabled: bool = True,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.llm = llm_client or create_gateway()
        self.sm = setting_manager or SettingManager(self.project_dir)
        self.state_machine = state_machine or StateMachine(self.project_dir)
        self.console = console or Console()
        self.chapters_dir = self.project_dir / "chapters"
        # M8 介入频率控制（懒加载避免循环导入）
        self._mode_controller = mode_controller
        # E3 前置冲突检测
        self.conflict_arbiter = conflict_arbiter
        self.pre_validate = pre_validate
        # E2 题材动态注入（运行期上下文，独立存储，不污染 state.json）
        self._genre_registry = genre_registry
        self._injected_store = InjectedTropeStore(self.project_dir)
        # T-5：可选启用结构化质量校验（仅补充，不替换主路径 LLM 校验）
        self.enable_structured_qc = enable_structured_qc
        # D：多维 LLM 质量审查（默认关；开启后把爽点/OOC/连贯性/追读力并入 revise_loop）
        self.strict_review = strict_review
        # D：质量校验器实例（惰性持有 LLM 维度规则，供 LLMBackedChecker 合并驱动）
        self._qc = QualityChecker(self.project_dir, self.llm)
        # G9：章内子阶段事件发射器（pipeline 注入；None 时零开销）
        self.event_emitter = event_emitter
        # G11：风格模仿（project/style.md 存在即注入；--no-style 关闭）
        self.style_enabled = style_enabled
        self.style_file = style_file
        # G12：爽点剧本/情绪目标注入（default 开；--no-payoff 关闭）
        self.payoff_enabled = payoff_enabled
        # P0：去AI味开关（质量门禁通过后、落盘前执行；--no-deslop 关闭）
        self.deslop_enabled = deslop_enabled

    def _load_published_titles(self) -> set[str]:
        """扫描 chapters/ 已发布章节的标题（实例内缓存一次；本方法在落盘前调用，
        因此缓存不含本章）。"""
        cache = getattr(self, "_published_titles_cache", None)
        if cache is not None:
            return cache
        titles: set[str] = set()
        try:
            for f in self.chapters_dir.glob("ch*.md"):
                try:
                    m = re.search(r"^title: (.+)$", f.read_text(encoding="utf-8"), re.M)
                    if m:
                        titles.add(m.group(1).strip())
                except Exception:  # noqa: BLE001 - 单文件读失败跳过
                    continue  # noqa: SILENT_DEGRADE
        except Exception:  # noqa: BLE001 - 目录不存在等 → 空集合
            pass  # noqa: SILENT_DEGRADE
        self._published_titles_cache = titles
        return titles

    def _ensure_unique_title(
        self, chapter_num: int, title: str, body: str
    ) -> str:
        """保证本章标题非占位且与全书已发布标题不重复（2026-09-06）。

        占位（「第N章」「第N章·第N章」）/ 过短（<4 字）/ 重复 → 用一次轻量
        LLM 调用基于本章结尾内容生成新的场景化标题；LLM 失败或仍撞名时，
        退化为确定性编号后缀（「原标题·二」「·三」…），保证收敛且不阻断。
        """
        used = self._load_published_titles()

        def is_bad(t: str) -> bool:
            t = (t or "").strip()
            if len(t) < 4:
                return True
            if t == f"第{chapter_num}章" or t.startswith(f"第{chapter_num}章"):
                return True
            return t in used

        if not is_bad(title):
            return title

        # 1) 轻量 LLM 重生（creative，低 token；失败降级）
        try:
            from agent.client.gateway_adapter import chat_creative

            tail = (body or "")[-300:]
            resp = chat_creative(
                self.llm,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是中文网文编辑。根据章节结尾内容拟一个 4-10 字的"
                            "场景化章节标题：具体、有画面感、含信息量。"
                            "只输出标题本身，不要书名号、引号、序号或任何解释。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"本章结尾片段：\n{tail}\n\n"
                            f"禁止使用以下已用过的标题：\n"
                            + "\n".join(sorted(used)[-80:])
                        ),
                    },
                ],
                temperature=0.9,
                max_tokens=30,
            )
            new = str(getattr(resp, "content", resp) or "").strip().strip("《》\"“”'")
            new = new.splitlines()[0].strip() if new else ""
            if new and is_bad(new) is False:
                return new[:30]
        except Exception:  # noqa: BLE001 - 重生失败退化为确定性后缀
            pass  # noqa: SILENT_DEGRADE

        # 2) 确定性兜底：编号后缀直到唯一
        base = (
            title.strip()
            if title
            and not title.startswith(f"第{chapter_num}章")
            and len(title) >= 4
            else "凡骨争锋"
        )
        n = 2
        while f"{base}·{n}" in used:
            n += 1
        return f"{base}·{n}"
