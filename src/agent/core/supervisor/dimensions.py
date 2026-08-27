"""长小说监督维度实现"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from agent.core.supervisor.supervisor import SupervisionIssue, SupervisorPlugin


class PlotProgressChecker(SupervisorPlugin):
    """情节推进监督——检测连续多章无明显情节推进"""

    name = "plot_progress"
    check_interval_chapters = 10

    # 情节推进关键词
    PROGRESS_KEYWORDS = [
        "发现", "揭露", "突破", "升级", "获得", "得到",
        "前往", "到达", "离开", "进入", "找到",
        "击败", "战胜", "解决", "化解", "突破",
        "转变", "变化", "改变", "成长", "觉醒",
        "关键", "转折", "意外", "真相", "秘密",
    ]

    # 非推进关键词（水字数）
    FILLER_KEYWORDS = [
        "日常", "吃饭", "睡觉", "休息", "闲聊",
        "回忆", "回想", "沉思", "思考", "发呆",
    ]

    def check(self, project_dir: str) -> list[SupervisionIssue]:
        issues: list[SupervisionIssue] = []
        chapters_dir = Path(project_dir) / "chapters"
        if not chapters_dir.exists():
            return issues

        chapter_files = sorted(chapters_dir.glob("ch*.md"))
        if len(chapter_files) < self.check_interval_chapters:
            return issues

        # 检查最近 N 章
        recent = chapter_files[-self.check_interval_chapters :]
        no_progress_count = 0
        total_chapters = len(chapter_files)

        for cf in recent:
            try:
                text = cf.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            progress_count = sum(text.count(w) for w in self.PROGRESS_KEYWORDS)
            filler_count = sum(text.count(w) for w in self.FILLER_KEYWORDS)

            if progress_count < 3:
                no_progress_count += 1

        if no_progress_count >= self.check_interval_chapters * 0.6:
            issues.append(SupervisionIssue(
                dimension="plot_progress",
                severity="warning",
                message=f"最近 {self.check_interval_chapters} 章中 {no_progress_count} 章无明显情节推进",
                chapter=total_chapters,
                details={
                    "total_chapters": total_chapters,
                    "no_progress_chapters": no_progress_count,
                    "window": self.check_interval_chapters,
                },
            ))

        return issues


class LanguageGuardChecker(SupervisorPlugin):
    """语言合规监督——检测中文占比/非中文标点/英文专有名词"""

    name = "language_guard"
    check_interval_chapters = 5

    # 允许的英文专有名词
    ALLOWED_ENGLISH = {
        "AI", "VIP", "CEO", "CTO", "CFO", "DNA", "RNA", "APP",
        "OK", "NO", "WC", "QQ", "VIP", "WiFi", "Wi-Fi",
        "iPhone", "iPad", "iOS", "Android", "Windows", "Linux",
        "Python", "Java", "C++", "API", "SDK", "UI", "UX",
    }

    # 非中文标点（网文中应避免的）
    NON_CHINESE_PUNCTUATION = re.compile(r"[;:!?,;:]")

    def check(self, project_dir: str) -> list[SupervisionIssue]:
        issues: list[SupervisionIssue] = []
        chapters_dir = Path(project_dir) / "chapters"
        if not chapters_dir.exists():
            return issues

        for cf in sorted(chapters_dir.glob("ch*.md")):
            try:
                text = cf.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            # 提取章节号
            chapter_num = self._extract_chapter_num(cf.stem)

            # 1. 中文占比
            chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
            total_chars = len(text.strip())
            if total_chars > 0:
                chinese_ratio = chinese_chars / total_chars
                if chinese_ratio < 0.7:
                    issues.append(SupervisionIssue(
                        dimension="language_guard",
                        severity="warning",
                        message=f"章节 {chapter_num} 中文占比仅 {chinese_ratio:.0%}",
                        chapter=chapter_num,
                        details={
                            "chinese_ratio": round(chinese_ratio, 3),
                            "total_chars": total_chars,
                            "chinese_chars": chinese_chars,
                        },
                    ))

            # 2. 非中文标点
            non_chinese_punct = self.NON_CHINESE_PUNCTUATION.findall(text)
            if non_chinese_punct:
                # 过滤掉常见的英文专有名词中的标点
                filtered = [p for p in non_chinese_punct if not self._is_in_allowed_english(text, p)]
                if filtered:
                    issues.append(SupervisionIssue(
                        dimension="language_guard",
                        severity="info",
                        message=f"章节 {chapter_num} 发现非中文标点 {len(filtered)} 处",
                        chapter=chapter_num,
                        details={
                            "punctuation_found": list(set(filtered)),
                            "count": len(filtered),
                        },
                    ))

            # 3. 未授权英文专有名词
            english_words = re.findall(r"[A-Za-z]{2,}", text)
            unauthorized = [w for w in english_words if w not in self.ALLOWED_ENGLISH]
            if len(unauthorized) > 5:
                issues.append(SupervisionIssue(
                    dimension="language_guard",
                    severity="info",
                    message=f"章节 {chapter_num} 发现 {len(unauthorized)} 个未授权英文词",
                    chapter=chapter_num,
                    details={
                        "unauthorized_words": list(set(unauthorized))[:10],
                        "count": len(unauthorized),
                    },
                ))

        return issues

    def _extract_chapter_num(self, stem: str) -> int:
        match = re.search(r"ch(\d+)", stem)
        return int(match.group(1)) if match else 0

    def _is_in_allowed_english(self, text: str, punct: str) -> bool:
        """检查标点是否在允许的英文专有名词中"""
        # 简单启发式：检查标点周围是否都是字母
        idx = text.find(punct)
        if idx < 0:
            return False
        # 检查前后字符
        before = text[idx - 1 : idx] if idx > 0 else ""
        after = text[idx + 1 : idx + 2] if idx + 1 < len(text) else ""
        return before.isalpha() and after.isalpha()


class StyleDriftChecker(SupervisorPlugin):
    """风格漂移监督——检测后期章节是否偏离初期风格"""

    name = "style_drift"
    check_interval_chapters = 20

    # 风格特征提取用关键词
    STYLE_KEYWORDS = {
        "文风": {
            "华丽": ["华丽", "绚烂", "璀璨", "辉煌", "瑰丽"],
            "质朴": ["朴实", "简单", "直接", "平淡", "自然"],
            "幽默": ["搞笑", "幽默", "逗趣", "滑稽", "玩笑"],
            "严肃": ["严肃", "庄重", "郑重", "正经", "正式"],
        },
        "叙事": {
            "心理": ["心想", "思考", "觉得", "感觉", "想道"],
            "动作": ["动作", "行动", "做", "走", "跑"],
            "对话": ["说道", "道", "回答", "问", "答"],
        },
    }

    def check(self, project_dir: str) -> list[SupervisionIssue]:
        issues: list[SupervisionIssue] = []
        chapters_dir = Path(project_dir) / "chapters"
        if not chapters_dir.exists():
            return issues

        chapter_files = sorted(chapters_dir.glob("ch*.md"))
        if len(chapter_files) < 10:
            return issues

        # 分析前 5 章风格特征
        early_chapters = chapter_files[:5]
        early_style = self._extract_style_profile(early_chapters)

        # 分析最近 5 章风格特征
        late_chapters = chapter_files[-5:]
        late_style = self._extract_style_profile(late_chapters)

        # 计算风格漂移
        drift_score = self._compute_style_drift(early_style, late_style)
        total_chapters = len(chapter_files)

        if drift_score > 0.3:
            issues.append(SupervisionIssue(
                dimension="style_drift",
                severity="warning",
                message=f"风格漂移检测：后期风格与初期差异较大（漂移度 {drift_score:.0%}）",
                chapter=total_chapters,
                details={
                    "drift_score": round(drift_score, 3),
                    "early_style": early_style,
                    "late_style": late_style,
                    "total_chapters": total_chapters,
                },
            ))

        return issues

    def _extract_style_profile(self, chapter_files: list[Path]) -> dict:
        """提取风格特征画像"""
        profile: dict = {}
        for category, sub_keywords in self.STYLE_KEYWORDS.items():
            category_scores = {}
            for sub_name, keywords in sub_keywords.items():
                count = 0
                for cf in chapter_files:
                    try:
                        text = cf.read_text(encoding="utf-8")
                        count += sum(text.count(k) for k in keywords)
                    except (OSError, UnicodeDecodeError):
                        continue
                category_scores[sub_name] = count
            profile[category] = category_scores
        return profile

    def _compute_style_drift(self, early: dict, late: dict) -> float:
        """计算风格漂移度（0-1）"""
        total_diff = 0.0
        total_count = 0

        for category in early:
            for sub_name in early[category]:
                early_val = early[category].get(sub_name, 0)
                late_val = late[category].get(sub_name, 0)
                max_val = max(early_val, late_val, 1)
                diff = abs(early_val - late_val) / max_val
                total_diff += diff
                total_count += 1

        return total_diff / total_count if total_count > 0 else 0.0


class TropePayoffChecker(SupervisorPlugin):
    """伏笔回收监督——检测进度 > 80% 时仍有大量未回收伏笔"""

    name = "trope_payoff"
    check_interval_chapters = 15

    def check(self, project_dir: str) -> list[SupervisionIssue]:
        issues: list[SupervisionIssue] = []
        project_path = Path(project_dir)

        # 读取伏笔表
        foreshadow_file = project_path / "foreshadows.md"
        outline_file = project_path / "outline.md"

        if not foreshadow_file.exists() or not outline_file.exists():
            return issues

        # 估算总章数
        total_chapters = self._estimate_total_chapters(project_path)
        if total_chapters <= 0:
            return issues

        # 当前进度
        current_chapter = self._get_current_chapter(project_path)
        if current_chapter <= 0:
            return issues

        progress = current_chapter / total_chapters

        # 只在进度 > 60% 时检查
        if progress < 0.6:
            return issues

        # 分析伏笔表（简化版：统计未回收伏笔）
        try:
            text = foreshadow_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return issues

        # 简化检测：查找 "未埋" 或 "已埋" 状态
        unburied = text.count("未埋")
        buried = text.count("已埋")
        recovered = text.count("已回收")
        total_foreshadows = unburied + buried + recovered

        if total_foreshadows == 0:
            return issues

        recovery_rate = recovered / total_foreshadows

        if progress > 0.8 and recovery_rate < 0.7:
            issues.append(SupervisionIssue(
                dimension="trope_payoff",
                severity="critical",
                message=f"进度 {progress:.0%} 但伏笔回收率仅 {recovery_rate:.0%}（{recovered}/{total_foreshadows}）",
                chapter=current_chapter,
                details={
                    "progress": round(progress, 2),
                    "recovery_rate": round(recovery_rate, 3),
                    "total_foreshadows": total_foreshadows,
                    "recovered": recovered,
                    "unburied": unburied,
                    "buried": buried,
                },
            ))
        elif progress > 0.6 and recovery_rate < 0.5:
            issues.append(SupervisionIssue(
                dimension="trope_payoff",
                severity="warning",
                message=f"进度 {progress:.0%} 时回收率仅 {recovery_rate:.0%}，建议开始回收伏笔",
                chapter=current_chapter,
                details={
                    "progress": round(progress, 2),
                    "recovery_rate": round(recovery_rate, 3),
                },
            ))

        return issues

    def _estimate_total_chapters(self, project_path: Path) -> int:
        """估算总章数"""
        outline_file = project_path / "outline.md"
        if outline_file.exists():
            try:
                text = outline_file.read_text(encoding="utf-8")
                # 估算：按章节标记数量
                chapters = re.findall(r"第[一二三四五六七八九十百千\d]+[章节]", text)
                if chapters:
                    return len(chapters) * 2  # 每个大纲章节约 2 个实际章节
            except (OSError, UnicodeDecodeError):
                pass
        return 100  # 默认 100 章

    def _get_current_chapter(self, project_path: Path) -> int:
        """获取当前写到第几章"""
        chapters_dir = project_path / "chapters"
        if chapters_dir.exists():
            files = list(chapters_dir.glob("ch*.md"))
            if files:
                # 取最大章节号
                nums = []
                for f in files:
                    match = re.search(r"ch(\d+)", f.stem)
                    if match:
                        nums.append(int(match.group(1)))
                return max(nums) if nums else 0
        return 0


import re