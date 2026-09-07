"""Writer daemon：NovelAgent 单一写执行权威（Phase 6 / D1+D2）。

包结构：
- ``task_queue``  落盘任务队列（pending/running/done 目录 + 原子 rename）
- ``core``        daemon 消费循环（全局串行、进程树可管、崩溃恢复）
- ``__main__``    ``python -m agent.daemon`` 入口

背景：Web/CLI 双入口各自 spawn 写进程 + 文件锁互斥（L1/L2-A）已把并发写
事故压到接近零，但仍有三类结构性残留：Web 重启丢运行状态、子进程孤儿、
陈旧锁需人工回收。daemon 化后**所有入口降为薄客户端**（提交任务 + 订阅
事件），同一项目同一时刻只有一个写任务在执行，并发问题结构性消失。
"""
