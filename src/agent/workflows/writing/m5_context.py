from __future__ import annotations

import logging
import re
from typing import Any

from agent.core.registry.genre_pack import first_genre, first_genre_label
from agent.core.story.volume import estimate_chapters
import frontmatter

from agent.core.registry.genre_pack import GenrePackRegistry
from agent.core.story.method_style import load_style_guide

logger = logging.getLogger(__name__)



class M5ContextMixin:
    """上下文装配（F5.1 七步读取 + 题材注入 + 章节提取辅助）（由 m5_write_chapter 拆出，仅由 M5WriteChapterWorkflow 组合使用）"""

    # ============================================================
    # 1. 上下文加载（7 步读取）
    # ============================================================
    def _load_context(self) -> dict[str, Any]:
        """F5.1 七步上下文加载"""
        # Step 1: world.md
        world_data = self.sm.load_world()
        if not world_data["exists"]:
            raise RuntimeError("world.md 不存在，请先运行 M1 配置")
        world_info = self._extract_world_info(world_data)

        # Step 2: 当前 subline.md
        progress = self.state_machine.progress or {}
        subline_id = progress.get("current_subline", "")
        if not subline_id:
            # 首次：取第一个支线
            sublines = self.sm.list_sublines()
            if not sublines:
                raise RuntimeError("没有支线，请先运行 /outline 生成大纲")
            subline_id = sublines[0]
        subline_data = self.sm.load_subline(subline_id)
        if not subline_data["exists"]:
            raise RuntimeError(f"支线 {subline_id} 的 subline.md 不存在")

        # Step 3: 主角路线当前节点
        route_info = self._load_route_node(progress)

        # Step 4: 关系网
        relations_info = self._load_relations()

        # Step 5: 本章涉及角色
        characters_info, characters_fingerprint = self._load_characters(subline_data)
        # P-C 修复：角色生死/时间线硬约束（来自 characters/*.md 真源）
        character_constraints = self._build_character_constraints(subline_data)

        # Step 6: 伏笔任务
        foreshadow_task = self._load_foreshadow_task(progress)

        # Step 7: 题材层质量规则（MVP 内置修仙）
        # — 已在 prompt 中编码

        # 章节号
        chapter_num = progress.get("total_written", 0) + 1

        # 前情提要
        prev_summary = self._load_prev_summary(chapter_num)

        # 压力曲线阶段（B 方案：曲线缺失/退化时按体量估算的真实总章数推导）
        pressure_stage, tension_level = self._determine_pressure_stage(
            subline_data, chapter_num, default_hi=world_info.get("expected_chapters", 200)
        )

        # A：RAG 语义召回（仅当 .state/rag/ 已建立；否则空，绝不阻断写章）
        rag_dir = self.project_dir / ".state" / "rag"
        rag_context: list = []
        if rag_dir.exists():
            try:
                from agent.core.rag.retriever import Retriever

                retriever = Retriever(self.project_dir)
                subline_goal = self._extract_section(subline_data["content"], "支线目标")
                subline_name = subline_data["metadata"].get("subline_name", subline_id)
                # 主线 query（推进本章情节）
                main_query = (
                    f"第{chapter_num}章 {subline_name} {subline_goal} {pressure_stage}"
                )
                # 易漂移维度保底 query（保证影响前后一致性的关键片段被带进上下文）
                guard_queries = [
                    f"第{chapter_num}章 {subline_name} {subline_goal} {pressure_stage}",
                    "角色生死、是否死亡、是否复活、下落、身份",
                    "金手指规则、能力上限、境界、修炼体系",
                    "已揭开的真相、秘密、往事、身世",
                    "人物关系、恩怨、敌对、师徒、盟友当前状态",
                ]
                rag_context = retriever.retrieve_multi(
                    guard_queries, top_k_each=5, max_total=12
                ) or retriever.retrieve(main_query, top_k=5)
            except Exception:  # noqa: BLE001 - RAG 失败降级为空，不影响写章
                rag_context = []

        # C：追读力账本中的开放债务（缺账本则空，不阻断写章）
        open_debts: list = []
        reader_signals: list = []  # G12：读者反馈信号（kind=reader_feedback 分离，其余维持既有行为）
        try:
            from agent.core.story.pacing_store import PacingStore

            all_debts = PacingStore(self.project_dir).get_open_debts(n=50)
            open_debts = [
                {"id": d.id, "desc": d.desc, "kind": d.kind, "planted_ch": d.planted_ch}
                for d in all_debts
            ]
            reader_signals = [
                {
                    "desc": d.desc,
                    "planted_ch": d.planted_ch,
                    "id": d.id,
                }
                for d in all_debts
                if d.kind == "reader_feedback"  # G12：kind 字面量扩展（结构零改动）
            ]
        except Exception:  # noqa: BLE001 - 账本读取失败降级为空
            open_debts = []

        # E：项目学习记忆（长期保留，注入生成 prompt；缺则空，不阻断写章）
        learnings: list = []
        learnings_text = "（暂无已沉淀的写法记忆）"
        try:
            from agent.core.story.learning_store import LearningStore
            from agent.core.infra.prompt_helpers import format_learnings

            # 限额注入（避免 prompt 膨胀；按存储顺序取前 20 条）
            capped = LearningStore(self.project_dir).load()[:20]
            learnings = [
                {
                    "id": x.id,
                    "category": x.category,
                    "text": x.text,
                    "source_chapters": x.source_chapters,
                }
                for x in capped
            ]
            learnings_text = format_learnings(capped)
        except Exception:  # noqa: BLE001 - 学习记忆读取失败降级为空
            learnings = []
            learnings_text = "（暂无已沉淀的写法记忆）"

        # ---- G15：连续性账本投影（写前注入；缺账本 → 降级为空，不阻断写章）----
        continuity_projection = ""
        continuity_loops: list = []
        try:
            from agent.core.continuity import ContinuityLedgerStore, project, project_to_text

            _ledger = ContinuityLedgerStore(self.project_dir)
            _ledger.load()
            if _ledger.has_any():
                _proj = project(_ledger)
                continuity_projection = project_to_text(_proj)
                continuity_loops = [
                    {
                        "loop_id": lo.loop_id,
                        "kind": lo.kind,
                        "status": lo.status,
                        "detail": lo.detail,
                    }
                    for lo in _proj.open_loops
                ]
        except Exception:  # noqa: BLE001 - 账本投影失败降级为空
            continuity_projection = ""
            continuity_loops = []

        # ---- G12：本章爽点剧本 + 情绪目标（缺失/损坏/关闭 → ""）----
        _payoff_task, _emotion_target = "", ""
        if getattr(self, "payoff_enabled", True):  # 默认开；--no-payoff 关闭
            try:
                from agent.core.story.payoff_script import chapter_payoff, load_payoff_script

                _script = load_payoff_script(self.project_dir, enabled=True)
                _payoff_task, _emotion_target = chapter_payoff(_script, chapter_num)
            except Exception:  # noqa: BLE001 - 剧本读取失败降级为空
                pass

        # ---- 细纲钩子设计（m3 chapter_hooks → subline.md「章节钩子设计」段；缺则空，不阻断）----
        chapter_hooks = self._extract_chapter_hooks(
            subline_data.get("content", ""), chapter_num
        )

        # ---- 细纲情节点序列（m3 plot_points → subline.md「情节点序列」段；缺则空，不阻断）----
        plot_points = self._extract_plot_points(
            subline_data.get("content", ""), pressure_stage
        )

        return {
            "world_info": world_info,
            "subline_id": subline_id,
            "subline_name": subline_data["metadata"].get("subline_name", subline_id),
            "subline_goal": self._extract_section(subline_data["content"], "支线目标"),
            "pressure_stage": pressure_stage,
            "tension_level": tension_level,
            "chapter_hooks": chapter_hooks,
            "plot_points": plot_points,
            "chapter_num": chapter_num,
            "route_node_id": route_info["node_id"],
            "route_milestone": route_info["milestone"],
            "route_main_title": route_info["main_title"],
            "route_main_result": route_info["main_result"],
            "route_main_growth": route_info["main_growth"],
            "characters_info": characters_info,
            "characters_fingerprint": characters_fingerprint,
            "character_constraints": character_constraints,  # P-C 修复：角色生死/时间线硬约束
            "relations_info": relations_info,
            "foreshadow_task": foreshadow_task,
            "prev_chapter_summary": prev_summary,
            "rag_context": rag_context,
            "open_debts": open_debts,
            "learnings": learnings,
            "learnings_text": learnings_text,
            # ---- G15：连续性账本投影（写前输入，有界；缺 → 空降级）----
            "continuity_projection": continuity_projection,
            "continuity_open_loops": continuity_loops,
            # ---- G8（补充边界 4）：结局/主线上下文注入 ----
            "ending": self._load_architecture_ending(),  # architecture.md frontmatter（空串=降级）
            "ending_mode": bool(progress.get("ending_mode", False)),  # 是否结局模式
            "mainline": list(progress.get("mainline_visited", []) or []),  # 已访问支线
            # ---- G11：风格指引（project/style.md；--no-style 或缺失 → ""）----
            "style_guide": load_style_guide(
                self.project_dir, self.style_enabled, self.style_file
            ),
            # ---- G12（读者反馈闭环）：爽点剧本 / 情绪目标 / 读者反馈 ----
            "payoff_task": _payoff_task,
            "emotion_target": _emotion_target,
            "reader_signals": reader_signals,
            # ---- B1：写章防模板注入（本卷已用手段清单 + 灭门回忆计数；缺则降级为空）----
            "reuse_guard_text": self._build_reuse_guard(chapter_num),
        }
    def _build_reuse_guard(self, chapter_num: int) -> str:
        """生成写章时防模板注入文本；读失败→"" 降级不阻断（B1）。"""
        try:
            from agent.core.story.reuse_guard import build_reuse_guard

            return build_reuse_guard(self.project_dir, chapter_num)
        except Exception:  # noqa: BLE001 - 降级不阻断
            return ""
    def _load_architecture_ending(self) -> str:
        """读 architecture.md frontmatter 的 architecture.ending（m14 行 447/460 写入）。

        读失败/缺失 → 返回 ""（降级不阻断，拍板 2/补充边界 4）。
        """
        try:
            f = self.project_dir / "architecture.md"
            if not f.exists():
                return ""
            post = frontmatter.load(f)
            arch = post.metadata.get("architecture", {}) or {}
            return str(arch.get("ending", "") or "").strip()
        except Exception:  # noqa: BLE001 - 读失败降级为空
            return ""
    def _extract_world_info(self, world_data: dict[str, Any]) -> dict[str, Any]:
        metadata = world_data.get("metadata", {}) or {}
        content = world_data.get("content", "")
        style = metadata.get("style", {}) or {}

        # 故事简介
        synopsis = self._extract_section(content, "故事简介") or ""

        # 境界体系
        realm_system = self._extract_section(content, "境界体系") or ""

        # 金手指
        golden_finger = self._extract_section(content, "金手指登记") or ""

        # B 方案：由体量估算全书总章数（曲线缺失/退化时按真实跨度推导压力阶段）
        scope_key = metadata.get("scope", "medium")
        scope_total_words = metadata.get("scope_total_words")
        scope_cl = (
            metadata.get("scope_chapter_length")
            or (style.get("chapter_length") if isinstance(style, dict) else None)
        )
        expected_chapters = estimate_chapters(scope_key, scope_total_words, scope_cl)

        return {
            "title": metadata.get("title", ""),
            "scope": metadata.get("scope", ""),
            "genre": first_genre(metadata),
            "genre_label": first_genre_label(metadata),
            "genres": list(metadata.get("genres") or []),
            "tone": style.get("tone", ""),
            "pov": style.get("pov", ""),
            "rhythm": style.get("rhythm", ""),
            "chapter_length": style.get("chapter_length", 3000),
            "info_density": style.get("info_density", ""),
            "banned_elements": style.get("banned_elements", []),
            "synopsis": synopsis,
            "realm_system": realm_system,
            "golden_finger_info": golden_finger,
            "expected_chapters": expected_chapters,
        }
    def _load_route_node(self, progress: dict[str, Any]) -> dict[str, str]:
        """从 protagonist_route.md 读取当前章节对应的节点"""
        route_file = self.project_dir / "protagonist_route.md"
        if not route_file.exists():
            return {"node_id": "", "milestone": "", "main_title": "", "main_result": "", "main_growth": ""}

        text = route_file.read_text(encoding="utf-8")
        chapter_num = progress.get("total_written", 0) + 1

        # 按 ## NXX 分段
        blocks = re.split(r"\n## (N\d+)", text)
        # blocks = ["前置", "N01", "N01内容", "N02", "N02内容", ...]
        for i in range(1, len(blocks), 2):
            node_id = blocks[i]
            block = blocks[i + 1] if i + 1 < len(blocks) else ""
            # 提取章节范围
            range_match = re.search(r"章节范围[：:]\s*(\d+)[-~](\d+)", block)
            if range_match:
                lo = int(range_match.group(1))
                hi = int(range_match.group(2))
                if lo <= chapter_num <= hi:
                    milestone = re.search(r"## N\d+ · (.+)", block)
                    milestone_str = milestone.group(1).strip() if milestone else ""
                    # 主分支
                    main_title = ""
                    main_result = ""
                    main_growth = ""
                    main_match = re.search(r"### 主分支 · (.+)", block)
                    if main_match:
                        main_title = main_match.group(1).strip()
                    result_match = re.search(r"\*\*结果\*\*[：:]\s*(.+)", block)
                    if result_match:
                        main_result = result_match.group(1).strip()
                    growth_match = re.search(r"\*\*成长\*\*[：:]\s*(.+)", block)
                    if growth_match:
                        main_growth = growth_match.group(1).strip()
                    return {
                        "node_id": node_id,
                        "milestone": milestone_str,
                        "main_title": main_title,
                        "main_result": main_result,
                        "main_growth": main_growth,
                    }
        # 没匹配到范围 → P-B 修复：均匀分配节点使路线随章节推进，而非恒为 N01
        node_ids = [blocks[i] for i in range(1, len(blocks), 2)]
        if node_ids:
            # 用节点范围的最大 hi 作为全书跨度（无范围则退化为本章号）
            his: list[int] = []
            for i in range(1, len(blocks), 2):
                m = re.search(r"章节范围[：:]\s*(\d+)[-~](\d+)", blocks[i + 1] if i + 1 < len(blocks) else "")
                if m:
                    his.append(int(m.group(2)))
            total = max(his) if his else chapter_num
            idx = min(len(node_ids) - 1, int((chapter_num - 1) / max(1, total) * len(node_ids)))
            node_id = node_ids[idx]
            block = ""
            for i in range(1, len(blocks), 2):
                if blocks[i] == node_id:
                    block = blocks[i + 1] if i + 1 < len(blocks) else ""
                    break
            milestone = re.search(r"## N\d+ · (.+)", block)
            logger.warning(
                "[route] 第 %d 章未被任何路线节点范围覆盖（全书跨度 %d），"
                "按位置分配到节点 %s",
                chapter_num, total, node_id,
            )
            return {
                "node_id": node_id,
                "milestone": milestone.group(1).strip() if milestone else "",
                "main_title": "",
                "main_result": "",
                "main_growth": "",
            }
        return {"node_id": "", "milestone": "", "main_title": "", "main_result": "", "main_growth": ""}
    def _load_relations(self) -> str:
        """读取 relations/graph.md"""
        graph_file = self.project_dir / "relations" / "graph.md"
        if not graph_file.exists():
            return "（关系网未生成）"
        return graph_file.read_text(encoding="utf-8")[:1500]
    def _extract_character_names(self, subline_data: dict[str, Any]) -> list[str]:
        """从 subline.md 解析本章出场角色名列表（供角色信息加载与状态硬约束复用）。"""
        # 从 subline.md 的出场角色字段获取角色列表
        raw_chars = subline_data["metadata"].get("characters") or subline_data["metadata"].get("出场角色")
        if not raw_chars:
            # 从 content 提取
            content = subline_data.get("content", "")
            section = self._extract_section(content, "出场角色")
            if section:
                raw_chars = section

        names: list[str] = []
        if isinstance(raw_chars, list):
            names = [str(n) for n in raw_chars]
        elif isinstance(raw_chars, str):
            # 去括号、引号、逗号
            cleaned = raw_chars.strip("[]'\" ")
            names = [n.strip("'\" ") for n in cleaned.split(",") if n.strip()]

        # 如果没提取到角色名，加载所有角色
        if not names:
            chars_dir = self.project_dir / "characters"
            if chars_dir.exists():
                names = [p.stem for p in chars_dir.glob("*.md")]
        return names
    def _load_characters(
        self, subline_data: dict[str, Any]
    ) -> tuple[str, str]:
        """读取本章涉及角色的 character.md"""
        names = self._extract_character_names(subline_data)

        chars_dir = self.project_dir / "characters"
        info_parts: list[str] = []
        fingerprint_parts: list[str] = []

        for name in names[:6]:  # 最多 6 个
            # 尝试加载
            char_data = self.sm.load_character(name)
            if not char_data["exists"]:
                # 模糊匹配
                if chars_dir.exists():
                    for p in chars_dir.glob("*.md"):
                        if name in p.stem or p.stem in name:
                            char_data = self.sm.load_character(p.stem)
                            break
            if char_data["exists"]:
                content = char_data["content"]
                # 提取内核摘要
                motivation = self._extract_section(content, "核心动机") or ""
                identity = char_data["metadata"].get("identity", "")
                info_parts.append(f"- **{name}**（{char_data['metadata'].get('role','')}）：{identity}。动机：{motivation[:80]}")

                # 语言指纹
                catchphrase = self._extract_field(content, "口头禅")
                sentence_style = self._extract_field(content, "句式偏好")
                fingerprint_parts.append(f"- {name}：口头禅「{catchphrase}」| 句式：{sentence_style}")
            else:
                info_parts.append(f"- **{name}**（角色档案未找到）")

        return (
            "\n".join(info_parts) if info_parts else "（无角色信息）",
            "\n".join(fingerprint_parts) if fingerprint_parts else "（无语言指纹）",
        )
    def _build_character_constraints(self, subline_data: dict[str, Any]) -> str:
        """P-C 修复：把 characters/*.md 的生死/状态/时间线真源抽取为「不可违背」硬约束。

        直接喂给 writer 的系统提示，防止出现 ch049「周伯十年前便已故去」这类与前文
        角色档案矛盾的内容。纯规则提取，零网络；缺失档案则跳过该角色。
        """
        names = self._extract_character_names(subline_data)
        chars_dir = self.project_dir / "characters"
        if not names or not chars_dir.exists():
            return ""

        parts: list[str] = []
        for name in names[:6]:  # 与 _load_characters 取前 6 个保持一致
            char_data = self.sm.load_character(name)
            if not char_data["exists"]:
                fuzzy = False
                for p in chars_dir.glob("*.md"):
                    if name in p.stem or p.stem in name:
                        char_data = self.sm.load_character(p.stem)
                        fuzzy = True
                        break
                if not fuzzy:
                    continue
            content = char_data["content"]
            # 1) 优先取结构化状态段落
            status = (
                self._extract_section(content, "状态")
                or self._extract_section(content, "当前状态")
                or self._extract_section(content, "生死")
                or self._extract_section(content, "存活状态")
            )
            # 2) 关键词兜底（无结构化段落时）
            if not status:
                if re.search(r"已故|去世|死亡|牺牲|阵亡|陨落|辞世", content):
                    status = "（档案正文提及已故/牺牲，按已故处理）"
                elif re.search(r"在世|存活|健在", content):
                    status = "（档案正文提及在世/存活）"
            # 3) 关键时间线
            timeline = (
                self._extract_section(content, "时间线")
                or self._extract_section(content, "关键时间线")
                or self._extract_section(content, "生平")
            )
            bits: list[str] = []
            if status:
                bits.append(f"权威状态：{status.strip()[:120]}")
            if timeline:
                bits.append(f"关键时间线：{timeline.strip()[:160]}")
            if bits:
                parts.append(
                    f"- {name}：{'；'.join(bits)}。"
                    f"本章正文不可与上述状态/时间线矛盾（尤其角色生死、所处年代须一致）。"
                )
        return "\n".join(parts)
    def _load_foreshadow_task(self, progress: dict[str, Any]) -> str:
        """读取 foreshadows.md，检查本章是否需埋/回收伏笔"""
        f_file = self.project_dir / "foreshadows.md"
        if not f_file.exists():
            return "（伏笔表未生成）"

        text = f_file.read_text(encoding="utf-8")
        chapter_num = progress.get("total_written", 0) + 1

        # ---- G8（拍板 5）：结局段「回收优先 + 禁新埋长线」 ----
        if progress.get("ending_mode"):
            open_items: list[str] = []
            for line in text.splitlines():
                if line.startswith("| F-"):
                    parts = [p.strip() for p in line.split("|")]
                    # 同源解析：状态列 = parts[5]（split 后第 6 个元素），
                    # 与 evaluator_agent._metric_foreshadow_recycle（cells[4]）同表同列语义（共享知识 #12）
                    if len(parts) >= 7 and parts[5] in ("未埋", "已埋"):
                        open_items.append(
                            f"  可回收 {parts[1]}：{parts[2]}（预期回收：{parts[4]}）"
                        )
            tasks = ["  ★ 结局阶段：本章强制回收 ≥1 条未回收伏笔"]
            tasks.extend(open_items[:3])  # 最多列 3 条，避免 prompt 膨胀
            tasks.append("  ★ 结局阶段：禁止新埋长线伏笔；短线（1-2 章内可自然回收）允许。")
            return "本章伏笔任务：\n" + "\n".join(tasks)

        # ---- 非结局段：既有逻辑原样（每 10 章强制埋/回收）----
        # 找本章应埋的伏笔（planted_at 包含当前章节号）
        tasks: list[str] = []
        for line in text.splitlines():
            if line.startswith("| F-"):
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 7:
                    fid, content, planted_at, expected, state, related = parts[1:7]
                    # 检查 planted_at 是否匹配本章
                    if f"ch{chapter_num:03d}" in planted_at or f"ch{chapter_num}" in planted_at:
                        if state == "未埋":
                            tasks.append(f"  埋设 {fid}：{content}（预期回收：{expected}）")
                    # 每 10 章强制埋 1 条 + 回收 1 条
                    if chapter_num % 10 == 0 and state == "已埋":
                        tasks.append(f"  可回收 {fid}：{content}")

        # 每 10 章强制提示
        if chapter_num % 10 == 0:
            tasks.append("  ★ 本章为第 {}0 章，强制埋 ≥1 长线伏笔、回收 ≥1 旧伏笔".format(chapter_num // 10))

        if not tasks:
            return "本章无强制伏笔任务。自然写作即可，如有合适时机可埋设新伏笔。"
        return "本章伏笔任务：\n" + "\n".join(tasks)
    def _load_prev_summary(self, chapter_num: int) -> str:
        """读取上一章的摘要（本章必须从这里无缝续写）。

        连续性问题根治：旧实现只取上一章「开头 300 字」做前情，writer 无法得知上一章
        「结尾」的真实状态，导致误把上一章开头场景重演、或自创与既定设定冲突的并行背景
        （历史 ch6 时间回环、ch7「青阳/青门/婉儿死而复生」漂移根因）。
        现改为：短章给全文；长章给出「开头 + 结尾」两段，【结尾】作为本章必须接续的
        权威状态，并附严禁重演上一章已发生场景/对话的硬约束。
        """
        if chapter_num <= 1:
            return "（第一章，无前情）"
        prev_file = self.chapters_dir / f"ch{chapter_num - 1:03d}.md"
        if not prev_file.exists():
            return "（上一章文件未找到）"
        text = prev_file.read_text(encoding="utf-8")
        # 去掉 frontmatter
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                text = parts[2]
        body = text.strip()
        body = re.sub(r"\s*\n\s*", "\n", body)
        if len(body) <= 600:
            return (
                body
                + "\n\n（上一章全文较短。本章必须从上一章结尾处无缝续写，"
                "严禁重演上一章已出现的场景/对话。如果上一章已解决的冲突（如灭掉追兵、"
                "抓住刺客），本章不得让同一事件再次原样发生。）"
            )
        head = body[:160].rstrip()
        tail = body[-300:].rstrip()
        return (
            "【上一章开头（情景氛围）】"
            + head
            + "\n\n【上一章结尾 · 权威接续状态】本章必须从该状态之后无缝续写，不得回退、"
            "不得重演上一章已经发生的场景与对话：\n"
            + tail
            + "\n\n【续写硬约束】1) 严格从上述「上一章结尾」的真实状态继续推进，不得重演/倒退。"
            "2) 涉及无名身世、宗门、角色关系、金手指等既有设定，必须全线沿用前文与角色档案，"
            "严禁凭空发明并行背景（如改名换姓、换师门、已死角色无故复活）。"
        )
    def _determine_pressure_stage(
        self, subline_data: dict[str, Any], chapter_num: int, default_hi: int = 200
    ) -> tuple[str, str]:
        """从 subline.md 的压力曲线表确定当前阶段。

        B 方案：default_hi 为体量估算的预计总章数（默认回退 200），
        在「曲线缺失/退化/未命中」时按真实全书跨度推导压力阶段，
        避免百万字（如 500 章）项目被 200 字面量上限误判阶段。
        """
        content = subline_data.get("content", "")
        section = self._extract_section(content, "剧集压力曲线")
        if not section:
            logger.warning(
                "[pacing] subline「剧集压力曲线」缺失，第 %d 章按位置推导压力阶段",
                chapter_num,
            )
            return self._position_based_stage(chapter_num, 1, default_hi)

        bands: list[tuple[str, int, int, str]] = []
        for line in section.splitlines():
            if line.startswith("|") and "阶段" not in line and "---" not in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 4:
                    range_match = re.match(r"(\d+)[-~](\d+)", parts[2])
                    if range_match:
                        bands.append(
                            (parts[1], int(range_match.group(1)), int(range_match.group(2)), parts[3])
                        )
        if not bands:
            logger.warning(
                "[pacing] subline「剧集压力曲线」无可解析区间，第 %d 章按位置推导",
                chapter_num,
            )
            return self._position_based_stage(chapter_num, 1, default_hi)

        # 整体跨度
        min_lo = min(b[1] for b in bands)
        max_hi = max(b[2] for b in bands)
        # 命中区间优先
        for stage, lo, hi, tension in bands:
            if lo <= chapter_num <= hi:
                # 退化检测：铺垫段过长且存在后续阶段 → 视为平坦曲线，改按位置推导
                setup_bands = [b for b in bands if b[0] == "铺垫"]
                has_later = any(b[0] != "铺垫" for b in bands)
                if (
                    stage == "铺垫"
                    and has_later
                    and setup_bands
                    and (setup_bands[0][2] - setup_bands[0][1] + 1)
                    > 0.5 * max(1, max_hi - min_lo + 1)
                ):
                    logger.warning(
                        "[pacing] 检测到退化压力曲线（铺垫段覆盖 %d-%d，占全书 %d%%），"
                        "第 %d 章改按位置推导压力阶段",
                        setup_bands[0][1], setup_bands[0][2],
                        round(100 * (setup_bands[0][2] - setup_bands[0][1] + 1) / max(1, max_hi - min_lo + 1)),
                        chapter_num,
                    )
                    return self._position_based_stage(chapter_num, min_lo, max_hi)
                return stage, tension
        # 未命中任何区间 → 按位置推导
        logger.warning(
            "[pacing] 第 %d 章未被任何压力曲线区间覆盖（全书 %d-%d），按位置推导",
            chapter_num, min_lo, max_hi,
        )
        return self._position_based_stage(chapter_num, min_lo, max_hi)
    @staticmethod
    def _position_based_stage(chapter_num: int, lo: int, hi: int) -> tuple[str, str]:
        """按章节在 [lo, hi] 跨度中的位置推导爬升压力阶段（铺垫→冲突→高潮→舒缓）。"""
        if hi <= lo:
            return "铺垫", "低"
        frac = (chapter_num - lo) / (hi - lo)
        if frac < 0.15:
            return "铺垫", "低"
        if frac < 0.5:
            return "冲突", "中"
        if frac < 0.85:
            return "高潮", "高"
        return "舒缓", "低"
    # ============================================================
    # E2 题材动态注入
    # ============================================================
    def _collect_injected_tropes(self, ctx: dict[str, Any]) -> str:
        """读取运行时注入的套路列表，提取对应套路模板文本（F-E2.2）

        套路列表来自独立的 ``.state/injected_tropes.json``（运行期上下文），
        不污染持久化状态。返回拼接后的套路文本；无注入时返回空字符串。
        """
        trope_names = self._injected_store.get()
        if not trope_names:
            return ""

        if self._genre_registry is None:
            self._genre_registry = GenrePackRegistry()

        # 多题材：world_info.genres 为列表；兼容旧 world_info.genre 单值与元数据兜底
        wi = ctx["world_info"]
        genres: list[str] = list(wi.get("genres") or [])
        if not genres and wi.get("genre"):
            genres = [wi["genre"]]
        if not genres:
            md = self.sm.load_world()["metadata"]
            genres = list(md.get("genres") or [])
            if not genres and md.get("genre"):
                genres = [md["genre"]]

        parts: list[str] = []
        for name in trope_names:
            found = False
            for g in genres:
                try:
                    trope = self._genre_registry.load_trope(g, name)
                    parts.append(f"### {trope.name}\n{trope.text}")
                    found = True
                    break
                except ValueError:
                    continue
            if not found:
                self.console.print(
                    f"[yellow]⚠ 注入套路失败（{name}）：未在题材 {genres or ['(未声明)']} 中找到[/yellow]"
                )
        return "\n\n".join(parts)
    # ============================================================
    # E3 前置式冲突检测与仲裁
    # ============================================================
    def _build_planned_setting(self, ctx: dict[str, Any]) -> str:
        """构建本章"计划设定变更"文本，供冲突检测门禁使用"""
        wi = ctx["world_info"]
        return (
            "【本章计划设定变更】\n"
            f"支线：{ctx['subline_name']}（{ctx['subline_goal']}）\n"
            f"主角路线节点：{ctx['route_node_id']}｜里程碑：{ctx['route_milestone']}\n"
            f"主线结果预期：{ctx['route_main_result']}\n"
            f"成长预期：{ctx['route_main_growth']}\n"
            f"涉及角色：{wi.get('title', '')}\n"
            f"伏笔任务：{ctx['foreshadow_task']}\n"
            f"题材：{' / '.join(wi.get('genres') or ([wi['genre']] if wi.get('genre') else ['未声明']))}"
        )
    # ============================================================
    # 工具
    # ============================================================
    @staticmethod
    def _extract_section(content: str, section_name: str) -> str:
        """从 markdown 内容提取 ## 段落"""
        pattern = rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\Z)"
        m = re.search(pattern, content, re.DOTALL)
        return m.group(1).strip() if m else ""
    @staticmethod
    def _extract_chapter_hooks(content: str, chapter_num: int) -> str:
        """从 subline.md 提取「章节钩子设计」段；优先取与当前章号匹配的行，否则整段。

        细纲钩子设计（m3 chapter_hooks）可能按压力阶段给基调（如「铺垫章：…」）
        或按章给设计（如「第1章：…」）。有逐章行时只取当前章相关行，控制 token；
        否则整段注入（通常仅 4-6 行）。
        """
        section = M5ContextMixin._extract_section(content, "章节钩子设计")
        if not section:
            return ""
        lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
        matched = [
            ln
            for ln in lines
            if re.search(rf"第\s*{chapter_num}\s*章|^\s*{chapter_num}\s*[章:]", ln)
        ]
        if matched:
            return "\n".join(matched)
        return section
    @staticmethod
    def _extract_plot_points(content: str, pressure_stage: str) -> str:
        """从 subline.md 提取「情节点序列」段；优先取与当前压力阶段匹配的行，否则整段。

        细纲情节点序列（m3 plot_points）按压力阶段组织（如「铺垫阶段：主角查账发现异常…」），
        写章时只取当前阶段的行（约 3-6 个动作化子事件），控制 token 且与本章直接相关；
        无阶段匹配时整段注入作为可选用素材。
        """
        section = M5ContextMixin._extract_section(content, "情节点序列")
        if not section:
            return ""
        if pressure_stage:
            lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
            matched = [
                ln for ln in lines if f"{pressure_stage}阶段" in ln or pressure_stage in ln
            ]
            if matched:
                return "\n".join(matched)
        return section
    @staticmethod
    def _extract_field(content: str, field_name: str) -> str:
        """从 markdown 提取 - **字段**：值"""
        pattern = rf"\*\*{re.escape(field_name)}\*\*[：:]\s*(.+)"
        m = re.search(pattern, content)
        return m.group(1).strip() if m else ""
