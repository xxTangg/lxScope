# -*- coding: utf-8 -*-
"""Task DAG scheduler integration through the Task SOP adapter."""

import asyncio
from unittest import IsolatedAsyncioTestCase

from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._models import (
    AgentStepConfig,
    InputStepConfig,
    NodeRunRecord,
    NodeRunStatus,
    RunStatus,
    StepVerdict,
    TaskGraph,
    TaskGraphEdge,
    TaskGraphInputBinding,
    TaskEventDraft,
    ToolStepConfig,
    TaskInputQuestion,
    TaskInputReply,
    TaskNode,
    TaskRunRecord,
)
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore


def _agent(node_id: str) -> TaskNode:
    return TaskNode(
        id=node_id,
        name=node_id,
        prompt=f"Run {node_id}",
        config=AgentStepConfig(prompt=f"Run {node_id}"),
    )


def _run(nodes: list[TaskNode], graph: TaskGraph) -> TaskRunRecord:
    return TaskRunRecord(
        id="dag-run",
        task_id="dag-task",
        user_id="owner",
        task_revision=1,
        engine="dag",
        graph=graph,
        nodes=nodes,
        node_runs=[
            NodeRunRecord(
                node_id=node.id,
                name=node.name,
                prompt=node.prompt,
                type=node.type,
                order=node.order,
            )
            for node in nodes
        ],
        input="initial request",
    )


class _Executor:
    def __init__(
        self,
        *,
        fail: set[str] | None = None,
        reject_once: set[str] | None = None,
    ) -> None:
        self.fail = fail or set()
        self.reject_once = reject_once or set()
        self.rejected: set[str] = set()
        self.started: set[str] = set()
        self.finished: set[str] = set()
        self.calls: list[tuple[str, str, object]] = []
        self.both_branches_started = asyncio.Event()
        self.entered = asyncio.Event()
        self.blocker: asyncio.Event | None = None
        self.swallow_cancel = False

    async def execute_node(self, *, node, previous_output, context):
        self.calls.append((node.id, previous_output, context))
        self.started.add(node.id)
        if node.id == "A" and self.blocker is not None:
            self.entered.set()
            try:
                await self.blocker.wait()
            except asyncio.CancelledError:
                if not self.swallow_cancel:
                    raise
                return StepExecutionResult(
                    text="late result",
                    result={"late": True},
                )
        if {"B", "C"}.issubset(self.started):
            self.both_branches_started.set()
        if node.id in {"B", "C"}:
            await asyncio.wait_for(self.both_branches_started.wait(), timeout=2)
        if node.id in self.fail:
            return StepExecutionResult(
                text="",
                status="failed",
                reason=f"{node.id} failed",
            )
        self.finished.add(node.id)
        return StepExecutionResult(
            text=f"output:{node.id}",
            result={"node_id": node.id},
        )

    async def verify_node(self, **kwargs):
        node = kwargs["node"]
        if node.id in self.reject_once and node.id not in self.rejected:
            self.rejected.add(node.id)
            return StepVerdict(
                passed=False,
                message="Retry with a corrected output.",
            )
        return StepVerdict(passed=True)


class TaskDagSchedulerTest(IsolatedAsyncioTestCase):
    async def _execute(self, run: TaskRunRecord, executor: _Executor):
        store = TaskStore()
        await store.save_run(run)
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        service._schedule_run(run, None)
        worker = service._active_runs[run.id]
        await worker
        result = await store.get_run(run.user_id, run.id)
        return store, service, result

    async def test_fan_out_runs_in_parallel_and_fan_in_waits_for_persisted_parents(self):
        nodes = [_agent(item) for item in ("A", "B", "C", "D")]
        graph = TaskGraph(
            edges=[
                TaskGraphEdge(source_node_id="A", target_node_id="B"),
                TaskGraphEdge(source_node_id="A", target_node_id="C"),
                TaskGraphEdge(source_node_id="B", target_node_id="D"),
                TaskGraphEdge(source_node_id="C", target_node_id="D"),
            ],
            input_bindings={
                "D": {
                    "branch_b": TaskGraphInputBinding(
                        source_node_id="B",
                        source_value="result",
                    ),
                    "branch_c": TaskGraphInputBinding(
                        source_node_id="C",
                        source_value="result",
                    ),
                }
            },
            max_concurrency=2,
        )
        executor = _Executor()

        store, service, result = await self._execute(_run(nodes, graph), executor)
        try:
            self.assertEqual(result.status, RunStatus.SUCCEEDED)
            self.assertEqual(executor.started, {"A", "B", "C", "D"})
            self.assertIn("A", executor.finished)
            self.assertTrue({"B", "C"}.issubset(executor.finished))
            self.assertTrue(
                {"B", "C"}.issubset(
                    {
                        node_id
                        for node_id, _, _ in executor.calls
                        if node_id in {"B", "C"}
                    }
                )
            )
            d_context = next(
                context for node_id, _, context in executor.calls if node_id == "D"
            )
            self.assertEqual(
                d_context.inputs_by_node_id["B"]["output"],
                "output:B",
            )
            self.assertEqual(
                d_context.named_inputs,
                {
                    "branch_b": {"node_id": "B"},
                    "branch_c": {"node_id": "C"},
                },
            )
            events = await store.list_events("owner", result.id)
            self.assertEqual(
                [event.sequence for event in events],
                list(range(1, len(events) + 1)),
            )
            self.assertEqual(
                sum(event.type == "run.succeeded" for event in events),
                1,
            )
        finally:
            await service.shutdown()

    async def test_get_run_lazily_recovers_a_queued_dag_after_service_rebuild(self):
        run = _run([_agent("A")], TaskGraph())
        store = TaskStore()
        await store.save_run(run)
        executor = _Executor()
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        try:
            visible = await service.get_run("owner", run.id)
            self.assertEqual(visible.status, RunStatus.QUEUED)
            worker = service._active_runs[run.id]
            await worker
            completed = await store.get_run("owner", run.id)
            self.assertEqual(completed.status, RunStatus.SUCCEEDED)
            self.assertEqual(executor.started, {"A"})
        finally:
            await service.shutdown()

    async def test_expired_running_node_is_failed_for_manual_review_not_replayed(self):
        run = _run([_agent("A")], TaskGraph())
        store = TaskStore()
        await store.save_run(run)
        claimed = await store.claim_run(
            "owner",
            run.id,
            owner_token="expired-worker",
            lease_seconds=0.01,
            audit_events=[TaskEventDraft(type="run.claimed")],
        )
        running = await store.transition_run(
            "owner",
            run.id,
            expected_version=claimed.run_version,
            run_patch={"status": RunStatus.RUNNING},
            node_patches={
                "A": {
                    "status": NodeRunStatus.RUNNING,
                    "attempt": 1,
                }
            },
            audit_events=[
                TaskEventDraft(type="step.started", node_id="A")
            ],
            owner_token="expired-worker",
            expected_fence_epoch=claimed.fence_epoch,
        )
        await asyncio.sleep(0.02)
        executor = _Executor()
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        try:
            service._run_users[run.id] = run.user_id
            service._schedule_run(running, None)
            await service._active_runs[run.id]
            recovered = await store.get_run("owner", run.id)
            self.assertEqual(recovered.status, RunStatus.FAILED)
            self.assertEqual(executor.started, set())
            self.assertTrue(recovered.node_runs[0].metadata["recovery_required"])
            self.assertIn(
                "manual_verification",
                recovered.node_runs[0].metadata["recovery_policy"],
            )
        finally:
            await service.shutdown()

    async def test_failed_branch_blocks_its_join_after_other_ready_sibling_finishes(self):
        nodes = [_agent(item) for item in ("A", "B", "C", "D")]
        graph = TaskGraph(
            edges=[
                TaskGraphEdge(source_node_id="A", target_node_id="B"),
                TaskGraphEdge(source_node_id="A", target_node_id="C"),
                TaskGraphEdge(source_node_id="B", target_node_id="D"),
                TaskGraphEdge(source_node_id="C", target_node_id="D"),
            ],
            max_concurrency=2,
        )
        executor = _Executor(fail={"B"})

        store, service, result = await self._execute(_run(nodes, graph), executor)
        try:
            self.assertEqual(result.status, RunStatus.FAILED)
            self.assertEqual(
                next(item for item in result.node_runs if item.node_id == "B").status,
                NodeRunStatus.FAILED,
            )
            self.assertEqual(
                next(item for item in result.node_runs if item.node_id == "C").status,
                NodeRunStatus.SUCCEEDED,
            )
            self.assertNotIn("D", executor.started)
            events = await store.list_events("owner", result.id)
            self.assertEqual(
                sum(event.type == "run.failed" for event in events),
                1,
            )
        finally:
            await service.shutdown()

    async def test_sop_verifier_retry_stays_inside_one_dag_node(self):
        node = _agent("A")
        executor = _Executor(reject_once={"A"})
        store, service, result = await self._execute(
            _run([node], TaskGraph()),
            executor,
        )
        try:
            self.assertEqual(result.status, RunStatus.SUCCEEDED)
            node_run = result.node_runs[0]
            self.assertEqual(node_run.attempt, 2)
            self.assertEqual(
                [attempt.status for attempt in node_run.attempt_history],
                ["failed", "succeeded"],
            )
            events = await store.list_events("owner", result.id)
            self.assertEqual(
                sum(event.type == "step.attempt_rejected" for event in events),
                1,
            )
        finally:
            await service.shutdown()

    async def test_tool_node_is_not_automatically_replayed_after_rejection(self):
        node = TaskNode(
            id="submit",
            name="submit",
            type="tool",
            config=ToolStepConfig(tool_name="erp.submit", arguments={}),
        )
        executor = _Executor(reject_once={"submit"})
        store, service, result = await self._execute(
            _run([node], TaskGraph()),
            executor,
        )
        try:
            self.assertEqual(result.status, RunStatus.FAILED)
            self.assertEqual(executor.started, {"submit"})
            self.assertEqual(
                sum(node_id == "submit" for node_id, _, _ in executor.calls),
                1,
            )
            self.assertEqual(
                result.node_runs[0].attempt_history[0].status,
                "failed",
            )
        finally:
            await service.shutdown()

    async def test_input_node_waits_and_resumes_the_same_run(self):
        input_node = TaskNode(
            id="collect",
            name="collect",
            type="input",
            config=InputStepConfig(
                questions=[
                    TaskInputQuestion(key="date", label="Date?"),
                ]
            ),
        )
        downstream = _agent("draft")
        graph = TaskGraph(
            edges=[
                TaskGraphEdge(
                    source_node_id="collect",
                    target_node_id="draft",
                )
            ]
        )
        run = _run([input_node, downstream], graph)
        store = TaskStore()
        await store.save_run(run)
        executor = _Executor()
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        try:
            service._schedule_run(run, None)
            worker = service._active_runs[run.id]
            await worker
            parked = await store.get_run("owner", run.id)
            self.assertEqual(parked.status, RunStatus.AWAITING_INPUT)
            self.assertIsNotNone(parked.pending_input)
            self.assertNotIn("draft", executor.started)

            answered = await service.answer_run(
                "owner",
                run.id,
                TaskInputReply(
                    request_id=parked.pending_input.id,
                    answers={"date": "tomorrow"},
                ),
            )
            self.assertEqual(answered.id, run.id)
            resumed_worker = service._active_runs[run.id]
            await resumed_worker
            completed = await store.get_run("owner", run.id)
            self.assertEqual(completed.status, RunStatus.SUCCEEDED)
            self.assertEqual(completed.id, run.id)
            draft_context = next(
                context
                for node_id, _, context in executor.calls
                if node_id == "draft"
            )
            self.assertEqual(draft_context.user_answers[0].answers["date"], "tomorrow")
        finally:
            await service.shutdown()

    async def test_cancel_fences_a_late_executor_result(self):
        nodes = [_agent("A"), _agent("B")]
        graph = TaskGraph(
            edges=[
                TaskGraphEdge(source_node_id="A", target_node_id="B"),
            ]
        )
        run = _run(nodes, graph)
        store = TaskStore()
        await store.save_run(run)
        executor = _Executor()
        executor.blocker = asyncio.Event()
        executor.swallow_cancel = True
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        try:
            service._schedule_run(run, None)
            worker = service._active_runs[run.id]
            await asyncio.wait_for(executor.entered.wait(), timeout=2)
            canceled = await service.cancel_run("owner", run.id)
            self.assertEqual(canceled.status, RunStatus.CANCELED)
            await asyncio.gather(worker, return_exceptions=True)
            final = await store.get_run("owner", run.id)
            self.assertEqual(final.status, RunStatus.CANCELED)
            self.assertEqual(final.node_runs[0].status, NodeRunStatus.CANCELED)
            self.assertEqual(final.node_runs[0].output, "")
            self.assertNotIn("B", executor.started)
        finally:
            await service.shutdown()
