"""M3 组件单元测试：Session 聚合根 / AGENT Task / Planner / 记忆 / 人类介入"""

from __future__ import annotations

import pytest

from llmagent.kernel.session import (
    ContextBuilder,
    DialogueInterpreter,
    InputQueue,
    Session,
    SessionGate,
    SessionManager,
    SessionState,
    TaskContext,
)
from llmagent.kernel.agent import (
    AgentLoopExecutor,
    EchoTool,
    Scratchpad,
    StopDecision,
    StopPolicy,
    ToolCall,
    ToolsetPolicy,
    TurnValidator,
    WriteTool,
)
from llmagent.kernel.planner import (
    Plan,
    PlanNode,
    StaticDAG,
    TemplateRetrieval,
)
from llmagent.kernel.memory import (
    MemoryEntry,
    MemoryManager,
    MemoryStore,
    SalienceFilter,
    WriteFailureCase,
    WriteHumanCorrection,
    WriteOnSuccess,
)
from llmagent.kernel.human import (
    HUMAN_TASK_SPEC,
    HumanTaskExecutor,
    HumanTicket,
    HumanTicketManager,
    SLAPolicy,
    TimeoutDefaultStrategy,
)
from llmagent.kernel.task import TaskKind, TaskRun, TaskSpec, TaskStatus


# ===== M3.1 Session 聚合根 =====


class TestSession:
    def test_create_session(self):
        session = Session(user_id="u1", project="test")
        assert session.session_id.startswith("sess-")
        assert session.state == SessionState.OPEN
        assert session.session_ctx.user_id == "u1"
        assert session.session_ctx.project == "test"

    def test_add_turn(self):
        session = Session()
        turn = session.add_turn("user", "你好")
        assert turn.turn_id == 1
        assert turn.role == "user"
        assert turn.content == "你好"
        assert len(session.dialogue_turns) == 1
        assert session.chat_ctx.turn_count == 1

    def test_close_session(self):
        session = Session()
        session.close()
        assert session.state == SessionState.CLOSED
        assert session.closed_at != ""

    def test_to_dict(self):
        session = Session(user_id="u1", project="p1")
        d = session.to_dict()
        assert d["user_id"] == "u1"
        assert d["project"] == "p1"
        assert d["trace_id"] == ""
        assert d["turn_count"] == 0


class TestSessionManager:
    def test_open_and_get(self):
        mgr = SessionManager()
        session = mgr.open(user_id="u1", project="p1")
        assert session.session_id != ""
        got = mgr.get(session.session_id)
        assert got is not None
        assert got.session_ctx.user_id == "u1"

    def test_submit(self):
        mgr = SessionManager()
        session = mgr.open()
        mgr.submit(session, "hello")
        assert session.state == SessionState.SUBMITTED
        assert len(session.dialogue_turns) == 1

    def test_submit_closed_raises(self):
        mgr = SessionManager()
        session = mgr.open()
        mgr.close(session)
        with pytest.raises(RuntimeError, match="已关闭"):
            mgr.submit(session, "data")

    def test_list_active(self):
        mgr = SessionManager()
        s1 = mgr.open(user_id="u1")
        s2 = mgr.open(user_id="u2")
        mgr.close(s1)
        active = mgr.list_active()
        assert len(active) == 1
        assert active[0].session_ctx.user_id == "u2"

    def test_persist_and_reload(self):
        import tempfile, os
        db_path = tempfile.mktemp(suffix=".db")
        try:
            mgr = SessionManager(db_path)
            s1 = mgr.open(user_id="u1", project="p1")
            mgr.submit(s1, "data")
            sid = s1.session_id
            mgr.close_all()

            mgr2 = SessionManager(db_path)
            loaded = mgr2.get(sid)
            assert loaded is not None
            assert loaded.session_ctx.user_id == "u1"
            assert loaded.state == SessionState.SUBMITTED
            mgr2.close_all()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestSessionGate:
    def test_validate_open(self):
        session = Session()
        SessionGate.validate(session)  # should not raise

    def test_validate_closed(self):
        session = Session()
        session.close()
        with pytest.raises(RuntimeError, match="已关闭"):
            SessionGate.validate(session)


class TestContextBuilder:
    def test_build_system_prompt(self):
        session = Session(user_id="u1", project="p1")
        prompt = ContextBuilder.build_system_prompt(session, extra_context="extra")
        assert "u1" in prompt
        assert "p1" in prompt
        assert "extra" in prompt


class TestInputQueue:
    def test_enqueue_dequeue(self):
        session = Session()
        queue = InputQueue(session)
        queue.enqueue("msg1")
        queue.enqueue("msg2")
        assert queue.size() == 2
        assert queue.dequeue() == "msg1"
        assert queue.dequeue() == "msg2"
        assert queue.dequeue() is None

    def test_peek(self):
        session = Session()
        queue = InputQueue(session)
        queue.enqueue("hello")
        assert queue.peek() == "hello"
        assert queue.size() == 1

    def test_clear(self):
        session = Session()
        queue = InputQueue(session)
        queue.enqueue("hello")
        queue.clear()
        assert queue.size() == 0


class TestDialogueInterpreter:
    def test_interpret_command(self):
        session = Session()
        result = DialogueInterpreter.interpret(session, "/help")
        assert result["intent"] == "command"

    def test_interpret_question(self):
        session = Session()
        result = DialogueInterpreter.interpret(session, "这是什么？")
        assert result["intent"] == "question"

    def test_interpret_generate(self):
        session = Session()
        result = DialogueInterpreter.interpret(session, "写一章")
        assert result["intent"] == "generate"


# ===== M3.2 AGENT Task =====


class TestToolsetPolicy:
    def test_register_and_get(self):
        tsp = ToolsetPolicy()
        tool = EchoTool()
        tsp.register(tool)
        assert tsp.get("echo") is tool
        assert "echo" in tsp.list_available()

    def test_get_unregistered(self):
        tsp = ToolsetPolicy()
        assert tsp.get("nonexistent") is None


class TestScratchpad:
    def test_add_and_get_turns(self):
        sp = Scratchpad()
        sp.add_turn({"turn": 0, "output": "hello"})
        assert len(sp.get_turns()) == 1
        assert sp.get_last_turn()["output"] == "hello"

    def test_checkpoints(self):
        sp = Scratchpad()
        sp.add_turn({"turn": 0, "output": "hello"})
        cps = sp.get_checkpoints()
        assert len(cps) == 1
        assert cps[0]["turn"] == 1

    def test_clear(self):
        sp = Scratchpad()
        sp.add_turn({"turn": 0, "output": "hello"})
        sp.clear()
        assert len(sp.get_turns()) == 0


class TestStopPolicy:
    def test_max_turns(self):
        policy = StopPolicy(max_turns=3)
        for i in range(3):
            decision = policy.should_stop(i, {"status": "running"})
            assert not decision.should_stop
        decision = policy.should_stop(3, {"status": "running"})
        assert decision.should_stop
        assert "最大轮次" in decision.reason

    def test_completed(self):
        policy = StopPolicy()
        decision = policy.should_stop(0, {"status": "completed"})
        assert decision.should_stop
        assert "任务完成" in decision.reason


class TestTurnValidator:
    def test_validate_no_tool_no_output(self):
        issues = TurnValidator.validate_turn({"turn": 0, "tool_name": "", "output": ""})
        assert len(issues) > 0
        assert "无工具调用也无输出" in issues[0]

    def test_valid_turn(self):
        issues = TurnValidator.validate_turn({"turn": 0, "tool_name": "echo", "output": "hello"})
        assert len(issues) == 0


class TestEchoTool:
    @pytest.mark.asyncio
    async def test_execute(self):
        tool = EchoTool()
        result = await tool.execute({"message": "test"})
        assert result["echo"] == "test"


class TestWriteTool:
    @pytest.mark.asyncio
    async def test_execute(self):
        tool = WriteTool()
        result = await tool.execute({"prompt": "write something"})
        assert "content" in result
        assert "写工具占位" in result["content"]


class TestAgentLoopExecutor:
    @pytest.mark.asyncio
    async def test_execute_no_llm(self):
        """无 LLM 时应空转直到 max_turns"""
        executor = AgentLoopExecutor(stop_policy=StopPolicy(max_turns=3))
        spec = TaskSpec(name="test", kind=TaskKind.AGENT)
        run = TaskRun(run_id="r1", spec=spec, output={"input": "hello"})
        result = await executor.execute(run)
        assert result.status == TaskStatus.SUCCEEDED
        assert result.output["turns"] == 3
        assert result.output["stop_reason"] == "达到最大轮次 (3)"

    @pytest.mark.asyncio
    async def test_with_tool(self):
        """带工具时，如果 llm_think 返回工具调用应执行"""
        async def fake_llm(ctx):
            return {"thought": "need echo", "action": "echo", "args": {"message": "hi"}}
        tsp = ToolsetPolicy()
        tsp.register(EchoTool())
        executor = AgentLoopExecutor(
            toolset_policy=tsp,
            stop_policy=StopPolicy(max_turns=2),
            llm_think=fake_llm,
        )
        spec = TaskSpec(name="test", kind=TaskKind.AGENT)
        run = TaskRun(run_id="r2", spec=spec, output={"input": "hello"})
        result = await executor.execute(run)
        assert result.status == TaskStatus.SUCCEEDED
        assert result.output["turns"] == 2


# ===== M3.3 Planner =====


class TestPlan:
    def test_create_plan(self):
        plan = Plan(name="test")
        node = PlanNode(task_name="t1", task_kind=TaskKind.LLM)
        plan.add_node(node)
        assert len(plan.nodes) == 1
        assert plan.plan_id.startswith("plan-")

    def test_validate_empty(self):
        plan = Plan()
        errors = plan.validate()
        assert "计划为空" in errors

    def test_validate_missing_dep(self):
        plan = Plan()
        n1 = PlanNode(task_name="t1")
        n2 = PlanNode(task_name="t2", depends_on=["nonexistent"])
        plan.add_node(n1)
        plan.add_node(n2)
        errors = plan.validate()
        assert any("依赖不存在的节点" in e for e in errors)

    def test_validate_cycle(self):
        plan = Plan()
        n1 = PlanNode(node_id="a", task_name="t1")
        n2 = PlanNode(node_id="b", task_name="t2", depends_on=["a"])
        n3 = PlanNode(node_id="c", task_name="t3", depends_on=["b"])
        n1.depends_on = ["c"]
        plan.add_node(n1)
        plan.add_node(n2)
        plan.add_node(n3)
        errors = plan.validate()
        assert any("循环依赖" in e for e in errors)


class TestStaticDAG:
    def test_topological_sort(self):
        dag = StaticDAG()
        plan = Plan(name="dag_test")
        a = PlanNode(node_id="a", task_name="A")
        b = PlanNode(node_id="b", task_name="B", depends_on=["a"])
        c = PlanNode(node_id="c", task_name="C", depends_on=["a"])
        d = PlanNode(node_id="d", task_name="D", depends_on=["b", "c"])
        plan.add_node(d)
        plan.add_node(c)
        plan.add_node(b)
        plan.add_node(a)
        result = dag.expand(plan)
        ids = [n.node_id for n in result.nodes]
        assert ids.index("a") < ids.index("b")
        assert ids.index("a") < ids.index("c")
        assert ids.index("b") < ids.index("d")
        assert ids.index("c") < ids.index("d")

    def test_create_linear_plan(self):
        plan = StaticDAG.create_linear_plan(["a", "b", "c"])
        assert len(plan.nodes) == 3
        assert plan.nodes[0].task_name == "a"
        assert plan.nodes[1].depends_on == [plan.nodes[0].node_id]
        assert plan.nodes[2].depends_on == [plan.nodes[1].node_id]

    def test_create_write_chapter_plan(self):
        plan = StaticDAG.create_write_chapter_plan()
        assert len(plan.nodes) == 4
        names = [n.task_name for n in plan.nodes]
        assert "generate_outline" in names
        assert "write_chapter" in names
        assert "review_chapter" in names
        assert "analyze_content" in names


class TestTemplateRetrieval:
    def test_save_and_find(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            tr = TemplateRetrieval(db_path)
            plan = StaticDAG.create_linear_plan(["a", "b"])
            tid = tr.save_template("test_plan", plan)
            assert tid.startswith("tpl-")

            found = tr.find_by_name("test_plan")
            assert found is not None
            assert len(found.nodes) == 2
            tr.close()
        finally:
            os.unlink(db_path)

    def test_list_templates(self):
        tr = TemplateRetrieval()
        plan = StaticDAG.create_linear_plan(["a"])
        tr.save_template("t1", plan)
        tr.save_template("t2", plan)
        templates = tr.list_templates()
        assert len(templates) == 2
        names = [t["name"] for t in templates]
        assert "t1" in names
        assert "t2" in names


# ===== M3.4 记忆写入 =====


class TestMemoryEntry:
    def test_create_entry(self):
        entry = MemoryEntry(content="test", source="success")
        assert entry.entry_id.startswith("mem-")
        assert entry.priority == 0.5
        assert entry.created_at != ""


class TestMemoryWritePolicy:
    def test_write_on_success(self):
        spec = TaskSpec(name="t", kind=TaskKind.LLM)
        run = TaskRun(run_id="r1", spec=spec, status=TaskStatus.SUCCEEDED)
        entry = MemoryEntry(content="ok")
        assert WriteOnSuccess.should_write(run, entry) is True

    def test_write_on_failure(self):
        spec = TaskSpec(name="t", kind=TaskKind.LLM)
        run = TaskRun(run_id="r1", spec=spec, status=TaskStatus.FAILED)
        entry = MemoryEntry(content="fail")
        assert WriteFailureCase.should_write(run, entry) is True

    def test_write_human_correction(self):
        entry = MemoryEntry(content="fixed", source="human_correction")
        assert WriteHumanCorrection.should_write(None, entry) is True  # type: ignore


class TestSalienceFilter:
    def test_low_priority_filtered(self):
        filter = SalienceFilter(min_priority=0.5)
        entry = MemoryEntry(content="test", priority=0.1)
        assert filter.should_keep(entry, []) is False

    def test_high_priority_kept(self):
        filter = SalienceFilter(min_priority=0.3)
        entry = MemoryEntry(content="test", priority=0.8)
        assert filter.should_keep(entry, []) is True

    def test_dedup(self):
        filter = SalienceFilter(dedup_threshold=0.5)
        entry = MemoryEntry(content="hello world")
        existing = [MemoryEntry(content="hello world!!!")]
        assert filter.should_keep(entry, existing) is False


class TestMemoryStore:
    def test_write_and_query(self):
        store = MemoryStore()
        entry = MemoryEntry(content="test", scope="task", task_name="t1")
        store.write(entry)
        results = store.query(scope="task")
        assert len(results) == 1
        assert results[0].content == "test"

    def test_query_by_task(self):
        store = MemoryStore()
        store.write(MemoryEntry(content="a", task_name="t1"))
        store.write(MemoryEntry(content="b", task_name="t2"))
        results = store.query(task_name="t1")
        assert len(results) == 1
        assert results[0].content == "a"

    def test_delete(self):
        store = MemoryStore()
        entry = MemoryEntry(content="test")
        store.write(entry)
        store.delete(entry.entry_id)
        assert len(store.query()) == 0


class TestMemoryManager:
    def test_write_success(self):
        mgr = MemoryManager()
        spec = TaskSpec(name="t1", kind=TaskKind.LLM)
        run = TaskRun(run_id="r1", spec=spec, status=TaskStatus.SUCCEEDED)
        assert mgr.write(run, "success content") is True

    def test_write_failure_high_priority(self):
        mgr = MemoryManager()
        spec = TaskSpec(name="t1", kind=TaskKind.LLM)
        run = TaskRun(run_id="r1", spec=spec, status=TaskStatus.FAILED)
        assert mgr.write(run, "failure content") is True

    def test_recall(self):
        mgr = MemoryManager()
        spec = TaskSpec(name="t1", kind=TaskKind.LLM)
        run = TaskRun(run_id="r1", spec=spec, status=TaskStatus.SUCCEEDED)
        mgr.write(run, "content1")
        results = mgr.recall(task_name="t1")
        assert len(results) >= 1


# ===== M3.5 人类介入 =====


class TestHumanTicketManager:
    def test_create_ticket(self):
        mgr = HumanTicketManager()
        ticket = mgr.create_ticket(
            run_id="r1", task_name="task1",
            title="需要审批", description="请审批",
        )
        assert ticket.ticket_id.startswith("ticket-")
        assert ticket.status == "pending"

    def test_resolve_ticket(self):
        mgr = HumanTicketManager()
        ticket = mgr.create_ticket(run_id="r1", task_name="t1", title="t", description="d")
        mgr.resolve_ticket(ticket.ticket_id, "approved", approved=True)
        resolved = mgr.get_ticket(ticket.ticket_id)
        assert resolved is not None
        assert resolved.status == "resolved"

    def test_get_pending_tickets(self):
        mgr = HumanTicketManager()
        mgr.create_ticket(run_id="r1", task_name="t1", title="t1", description="d1")
        mgr.create_ticket(run_id="r2", task_name="t2", title="t2", description="d2")
        pending = mgr.get_pending_tickets()
        assert len(pending) == 2


class TestHUMAN_TASK_SPEC:
    def test_spec_defined(self):
        assert HUMAN_TASK_SPEC.name == "human_intervention"
        assert HUMAN_TASK_SPEC.kind == TaskKind.HUMAN
        assert "title" in HUMAN_TASK_SPEC.input_schema["required"]


class TestTimeoutDefaultStrategy:
    def test_skip(self):
        policy = SLAPolicy(default_action="skip")
        ticket = HumanTicket(ticket_id="t1")
        result = TimeoutDefaultStrategy.apply(ticket, policy)
        assert result["action"] == "skip"

    def test_degrade(self):
        policy = SLAPolicy(default_action="degrade")
        ticket = HumanTicket(ticket_id="t1")
        result = TimeoutDefaultStrategy.apply(ticket, policy)
        assert result["action"] == "degrade"

    def test_fail(self):
        policy = SLAPolicy(default_action="fail")
        ticket = HumanTicket(ticket_id="t1")
        result = TimeoutDefaultStrategy.apply(ticket, policy)
        assert result["action"] == "fail"