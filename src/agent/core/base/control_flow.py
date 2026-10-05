"""控制流异常基类（20261003 复盘·类 2 的机制化根治）

问题
----
pipeline 层有 **149 处裸 ``except Exception``**（降级不阻断哲学的实现载体）。
任何新引入的「升级语义」控制流异常（如 :class:`~agent.core.plan_gate.PlanGateRejected`）
默认会被这张吞噬网拦下——2026-10-03~04 三次实证（升级被吞成 degrade /
UnboundLocalError / pre-push 抓出的 import 求值失败）。靠人肉「记得在每层
显式 re-raise」在 149 层网面前必然再犯。

机制
----
:class:`ControlFlowError` 继承 **BaseException** 而非 Exception——
与宿主敌意边界的 ``SystemExit`` 同型：``except Exception`` / ``except OSError``
**结构上接不住它**，穿透性由类型系统保证，不依赖任何一层的人肉纪律。

使用约定
--------
1. 凡「必须穿透降级网、由调用方/入口显式处理」的异常，继承本基类；
2. 子类命名约定：``*GateRejected`` / ``*Escalation`` / ``*Abort``（红线扫描）；
3. 捕获点必须是**可信边界**（CLI 命令入口 / daemon 任务包装），并显性处置
   （exit code / 任务失败信封），不得静默吞掉；
4. 不得在 ``except ControlFlowError`` 里做降级放行——那等于重新打开吞噬网。

先例：《架构文档.md》§3.4 不变性 7（SystemExit 从 atexit 逃逸的攻防）。
"""

from __future__ import annotations


class ControlFlowError(BaseException):
    """控制流异常基类：穿透一切 ``except Exception`` 降级网（见模块 docstring）。"""
