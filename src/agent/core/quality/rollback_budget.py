"""跨批回退预算（P1，2026-09-12）——把「连续几次回退仍不达标」这件事持久化。

背景（五灵破归档 31 次回退复盘）：
``max_rollback_attempts=3`` 形同虚设——它只在
:meth:`EvaluatorAgent.evaluate_with_repair` 的循环里计数，而**滚动体检走的是
``evaluate()``（不计数）**；每批又会新建一个 ``EvaluatorAgent``，计数随之归零。
于是「回退 → 外层盲目重写同样的 5 章 → 同样的章间矛盾 → 再回退」永不收敛，
且没有任何一条路径会 ``escalated`` 上报人工。

本模块把这件事**落到磁盘**，跨批次、跨进程生效：

- :meth:`bump` —— 每次回退 +1，并记录回退目标章，用于识别「同一窗口反复翻车」；
- :meth:`reset` —— 体检通过时归零（因此统计的是**连续**失败，不会把历史成功后的
  偶发失败一并算进来）；
- :meth:`tripped` —— 连续次数超过上限 ⇒ 上层应 ``escalated`` 停批、上报人工，
  禁止无限重试。

存储：``.state/rollback_budget.json``。

2026-09-15 加固（登记单 ``20260915_回退熔断账实不符与降级当通过`` §二.R2）
------------------------------------------------------------------------------
实测：灵荒薪传 09-15 发生 5 次真实回退（`state.progress.last_rollback_at`
= `2026-09-15 17:10:04`），而本文件 `updated_at` 停在 09-14 21:33、`consecutive` 恒为 2
—— **熔断护栏从未生效，回退可无限进行**。三处加固：

1. **落盘加固**：唯一 tmp 名（`pid` + `uuid`，避免复用同名 `*.tmp` 撞 Windows 占用）
   + 重试 + 非原子覆盖兜底。旧实现固定 `.tmp` 后缀且失败即放弃 ⇒ 一旦占用就永久丢计数。
2. **进程级共享实例**：``load()`` 返回按解析路径为键的**进程内缓存实例**。
   旧实现每次检查点都重新读盘 ⇒ 落盘一失败，下一秒就被旧值覆盖，
   所谓"退化为进程内计数"根本不成立。跨进程仍以磁盘为准（``refresh=True`` 强制重读）。
3. **独立硬闸**：``tripped()`` 除 ``consecutive > limit`` 外，
   新增 ``same_target_streak >= SAME_TARGET_LIMIT``——同一章节窗口连续翻车是
   死循环的直接特征，不该被"次数未到上限"掩盖（且不依赖单一计数器）。

读写失败一律降级为「不阻断」——本模块是护栏，不是关键路径。

2026-09-16 加固（登记单 ``20260916_独立账本首轮对齐豁免使熔断失效``，**回开** 09-15 那条）
------------------------------------------------------------------------------------------
实测：灵荒薪传当日 5 次回退**全部指向第 27 章**（09:32/10:02/10:30/10:59/11:32），
而本文件 mtime 停在 10:55:30（``consecutive=3 / same_target_streak=1``）
⇒ ``tripped() = (3>3) or (1>=2)`` 恒 False ⇒ **回退在无人干预下持续销毁章节**。

三处加固：

4. **对账水位持久化**（``last_counted_seq``）：水位原先留在 pipeline **实例属性**
   （``_rollback_ledger_seq``），而 pipeline 每次 autowrite 运行新建 ⇒ 每次运行的
   首轮回退都落进对账的「首轮豁免」分支 ⇒ 独立账本 ``seq`` 从未被使用 ⇒
   判据退回单点源。改为写进本文件 ⇒ 豁免只对"装机前的历史快照"生效**一次**。
5. **目标章未知不归零**：``bump()`` 原先在 ``target == 0`` 时把
   ``same_target_streak`` 置 0 ⇒ 抹掉"同一窗口反复翻车"这一最强死循环信号。
6. **落盘失败可取证**：最终落盘失败除 ``degrade`` 外，回调 ``on_persist_failure``
   上报 **failure 事件**（daemon stdout 为 0 字节，仅 logging 事后不可取证）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.core.infra.degrade import degrade

BUDGET_FILE = ".state/rollback_budget.json"

#: 同一章节窗口连续回退达到该次数 ⇒ 独立硬闸熔断（死循环特征）。
#: 取值 2 的理由：同一窗口第二次翻车说明"重写同一批内容"这一动作已被证伪，
#: 再回退第三次不会产生不同结果（实测 4 批 21 次开章全部锁死在同一窗口）。
SAME_TARGET_LIMIT = 2

#: 进程内共享实例（键 = 解析后的绝对路径）——见模块头 §2。
_CACHE: dict[str, "RollbackBudget"] = {}


def _as_int(value: Any) -> int:
    """宽松取整：字段缺失/类型异常按 0 处理（预算文件是护栏旁路，不因它阻断写章）。"""
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        degrade("rollback_budget.as_int", "预算字段类型异常，按 0 处理", e)
        return 0


def _cache_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:  # pragma: no cover - 罕见路径解析失败
        return str(path)


@dataclass
class RollbackBudget:
    """连续回退计数器（持久化于项目 ``.state/rollback_budget.json``）。"""

    project_dir: Path
    limit: int = 3
    consecutive: int = 0
    total: int = 0
    last_target: int = 0
    same_target_streak: int = 0
    last_reason: str = ""
    updated_at: str = ""
    #: 「独立账本已记账到的快照序号」水位（2026-09-16）。
    #: ``-1`` = 尚未初始化（装机前历史，仅对齐一次基线）。**必须持久化**：
    #: 旧实现把水位留在 pipeline 实例属性上，而 pipeline 每次 autowrite 运行新建
    #: ⇒ 每次运行的首轮回退都退回 ``report.rolled_back`` 单点源 ⇒ 独立账本从未生效、
    #: 熔断形同虚设（登记单 ``20260916_独立账本首轮对齐豁免使熔断失效`` §二.R1）。
    last_counted_seq: int = -1
    #: 落盘失败回调（2026-09-16）：护栏失效必须**可取证**。``degrade`` 只走 logging，
    #: 而 daemon stdout 为 0 字节 ⇒ 事后无法区分"没回退"与"记不上账"。
    #: 由生产入口（pipeline）注入 ``_emit_failure``；非 dataclass 比较字段。
    on_persist_failure: "Callable[[str], None] | None" = None

    @property
    def path(self) -> Path:
        return Path(self.project_dir) / BUDGET_FILE

    # ---- 存取 ----
    @classmethod
    def load(
        cls, project_dir: str | Path, limit: int = 3, *, refresh: bool = False
    ) -> "RollbackBudget":
        """读取预算。

        Args:
            project_dir: 小说项目目录。
            limit: 上限（缺省 3）。
            refresh: 强制重新读盘（测试/跨进程复核用）。默认返回进程内共享实例——
                这是 §2 的核心：落盘失败时计数仍能单调推进，不会下一秒被旧值覆盖。
        """
        probe = cls(project_dir=Path(project_dir), limit=max(1, _as_int(limit) or 3))
        key = _cache_key(probe.path)
        cached = _CACHE.get(key)
        if cached is not None and not refresh:
            cached.limit = max(1, _as_int(limit) or cached.limit)
            return cached

        budget = probe
        f = budget.path
        if not f.exists():
            _CACHE[key] = budget
            return budget
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            degrade("rollback_budget.load", "预算文件损坏或不可读，视为未计数", e)
            _CACHE[key] = budget
            return budget
        if not isinstance(data, dict):
            _CACHE[key] = budget
            return budget
        budget.consecutive = _as_int(data.get("consecutive"))
        budget.total = _as_int(data.get("total"))
        budget.last_target = _as_int(data.get("last_target"))
        budget.same_target_streak = _as_int(data.get("same_target_streak"))
        budget.last_reason = str(data.get("last_reason") or "")
        budget.updated_at = str(data.get("updated_at") or "")
        # 水位：**键缺失/为 null ⇒ -1（未初始化）**，不得按 0 处理
        # （0 会被读成"已记账到序号 0" ⇒ 首轮就把历史回退全部补记）。
        _seq_raw = data.get("last_counted_seq")
        budget.last_counted_seq = _as_int(_seq_raw) if _seq_raw is not None else -1
        _CACHE[key] = budget
        return budget

    def save(self) -> bool:
        """落盘（加固：进程内唯一 tmp + 重试 + 非原子兜底）。返回是否写出成功。

        设计取舍：

        - tmp 名含 ``pid``（**每进程一个**，不逐次新建）→ 既避开同名 ``*.tmp``
          跨进程撞车，也**无需删除**残留文件（删除在 WorkBuddy safe-delete 护栏下
          是高风险调用，见 ``tests/architecture/test_hostile_delete_env.py``）；
        - 重试失败只记 DEBUG（属预期抖动），**最终**失败才 WARNING——
          避免"其实第三次成功了"却刷屏告警。
        """
        self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "consecutive": self.consecutive,
            "total": self.total,
            "last_target": self.last_target,
            "same_target_streak": self.same_target_streak,
            "last_reason": self.last_reason,
            "updated_at": self.updated_at,
            "limit": self.limit,
            # 对账水位（2026-09-16）：跨运行对账依赖它——见字段 docstring。
            "last_counted_seq": self.last_counted_seq,
        }
        blob = json.dumps(payload, ensure_ascii=False, indent=2)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            degrade(
                "rollback_budget.save.mkdir",
                "预算目录创建失败，仍尝试直接写入（父目录可能已存在）",
                e,
            )
        tmp = self.path.parent / f"{self.path.name}.{os.getpid()}.tmp"
        last_exc: BaseException | None = None
        for attempt in range(3):
            try:
                tmp.write_text(blob, encoding="utf-8")
                os.replace(tmp, self.path)
                return True
            except OSError as e:
                last_exc = e
                degrade(
                    "rollback_budget.save.replace",
                    f"预算原子落盘第 {attempt + 1}/3 次失败（os.replace 被占用/拒绝），稍后重试",
                    e,
                    level=logging.DEBUG,
                )
                time.sleep(0.05 * (attempt + 1))
        degrade(
            "rollback_budget.save.replace",
            "预算原子落盘重试 3 次均失败，改用非原子兜底写入",
            last_exc,
        )
        try:  # 兜底：非原子覆盖写（牺牲原子性换"账实相符"）
            self.path.write_text(blob, encoding="utf-8")
            return True
        except OSError as e:
            degrade(
                "rollback_budget.save.fallback",
                "非原子兜底写入仍失败——本进程内计数仍有效，重启后可能回退到旧值",
                e,
            )
            # 2026-09-16（登记单 ``20260916_独立账本首轮对齐豁免使熔断失效`` §三.C）：
            # 护栏**整体失效**必须产生可取证的事件，不能只留一行 logging
            # （daemon stdout 为 0 字节 ⇒ 事后无法区分"没回退"与"记不上账"）。
            self._notify_persist_failure(
                "回退预算落盘失败（原子重试 3 次 + 非原子兜底均失败）："
                "熔断计数可能丢失、判据不可信，需人工核查磁盘/权限"
            )
            return False

    def _notify_persist_failure(self, msg: str) -> None:
        """落盘失败上报（护栏失效可取证）；回调自身异常不得掩盖主流程。"""
        cb = self.on_persist_failure
        if cb is None:
            return
        try:
            cb(msg)
        except Exception as e:  # noqa: BLE001 - 上报通道故障不得阻断护栏自身
            degrade(
                "rollback_budget.persist_failure_notify",
                "落盘失败上报回调异常（上报通道本身故障）",
                e,
            )

    # ---- 计数 ----
    def bump(self, target_chapter: int = 0, reason: str = "") -> int:
        """记一次「体检不达标 → 回退」。返回累计的连续回退次数。"""
        self.consecutive += 1
        self.total += 1
        target = _as_int(target_chapter)
        # 2026-09-16（登记单 ``20260916_独立账本首轮对齐豁免使熔断失效`` §三.B）：
        # 目标章**取不到时不得归零** ``same_target_streak``。归零会把
        # "同一窗口反复翻车"这个最强死循环信号抹掉——实测灵荒薪传当日 5 次回退
        # 全部指向第 27 章，而 ``streak`` 恒为 1，独立硬闸因此从未成立。
        if target:
            self.same_target_streak = (
                self.same_target_streak + 1 if target == self.last_target else 1
            )
            self.last_target = target
        self.last_reason = (reason or "")[:200]
        if not self.save():
            degrade(
                "rollback_budget.bump.save",
                "回退预算落盘失败（重试与非原子兜底均失败）——"
                "本进程内计数仍有效，但重启后可能回退到旧值",
            )
        return self.consecutive

    def mark_ledger(self, seq: int) -> None:
        """持久化「独立账本已记账到的快照序号」水位（2026-09-16）。

        跨运行对账的关键：下一个 autowrite 运行（新 pipeline 实例、可能新进程）
        据此得到 ``new = seq - last_counted_seq``，而不再退回 ``report.rolled_back``
        单点源。**首次启用**时由调用方显式对齐基线（仅一次），此后一律以账本为准。
        """
        if seq == self.last_counted_seq:
            return
        self.last_counted_seq = seq
        if not self.save():
            degrade(
                "rollback_budget.mark_ledger.save",
                "对账水位落盘失败——跨运行对账将退回基线，熔断判据可能失真",
            )

    def reset(self) -> None:
        """体检通过 → 连续计数归零（累计 total 保留，供复盘）。"""
        if self.consecutive == 0 and self.same_target_streak == 0:
            return
        self.consecutive = 0
        self.same_target_streak = 0
        if not self.save():
            degrade("rollback_budget.reset.save", "回退预算落盘失败，跳过持久化")

    def tripped(self) -> bool:
        """是否应停批并上报人工。

        2026-09-15 新增**独立硬闸**（§2.3）：除「连续次数超上限」外，
        「同一章节窗口连续回退 ``SAME_TARGET_LIMIT`` 次」同样熔断——
        后者是死循环的直接特征，且**不依赖 consecutive**：
        即便主账本因接线缺陷漏记，只要目标章相同就必然被识别。
        """
        if self.consecutive > self.limit:
            return True
        return bool(self.last_target) and self.same_target_streak >= SAME_TARGET_LIMIT

    def trip_reason(self) -> str:
        """熔断的触发条款（供上报文案区分两种硬闸）。"""
        if self.consecutive > self.limit:
            return f"连续回退 {self.consecutive} 次，超过上限 {self.limit}"
        return (
            f"同一章节窗口（第 {self.last_target} 章附近）连续回退 "
            f"{self.same_target_streak} 次（阈值 {SAME_TARGET_LIMIT}）"
        )

    def reason_text(self) -> str:
        """给人工看的一句话：为什么停、停在哪。"""
        churn = (
            f"；同一章节窗口（第 {self.last_target} 章附近）已连续回退 {self.same_target_streak} 次"
            if self.last_target and self.same_target_streak >= 2
            else ""
        )
        return (
            f"{self.trip_reason()}（累计回退 {self.total} 次）{churn}。自动重试已停止，需人工介入："
            f"请检查设定/细纲一致性，或降低阈值后重新发起。最近一次原因：{self.last_reason}"
        )


__all__ = ["BUDGET_FILE", "SAME_TARGET_LIMIT", "RollbackBudget"]
