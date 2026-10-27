# -*- coding: utf-8 -*-
"""Atomic same-Run retry semantics for Task DAG nodes."""

from unittest import IsolatedAsyncioTestCase

from fastapi import FastAPI
import httpx

from agentscope.app.deps import get_current_user_id
from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._models import (
    AgentStepConfig,
    NodeRunRecord,
    NodeRunStatus,
    RunStatus,
    StepVerdict,
    TaskGraph,
    TaskGraphEdge,
    TaskNode,
    TaskNodeAttemptRecord,
    TaskRunControlRequest,
    TaskRunRecord,
    ToolStepConfig,
)
from examples.agent_service.task._service import (
    TaskService,
    TaskWorkflowConflictError,
)
from examples.agent_service.task._router import task_router
from examples.agent_service.task._store import TaskStore, TaskStoreConflictError


def _agent(node_id: str, order: int) -> TaskNode:
    return TaskNode(
        id=node_id,
        name=node_id,
        order=order,
        config=AgentStepConfig(prompt=node_id),
    )


def _attempt(
    node_id: str,
    status: str,
    output: str,
) -> TaskNodeAttemptRecord:
    return TaskNodeAttemptRecord(
        attempt=1,
        status=status,
        output=output,
        result={"node_id": node_id},
    )


class _Executor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute_node(self, *, node, previous_output, context):
        del previous_output, context
        self.calls.append(node.id)
        return StepExecutionResult(
            text=f"new:{node.id}",
            result={"node_id": node.id, "new": True},
        )

    async def verify_node(self, **kwargs):
        del kwargs
        return StepVerdict(passed=True)


def _failed_run() -> TaskRunRecord:
    # D is a successful sibling whose display order is later than failed C.
    nodes = [
        _agent("A", 0),
        _agent("C", 1),
        _agent("E", 2),
        _agent("D", 3),
    ]
    graph = TaskGraph(
        edges=[
            TaskGraphEdge(source_node_id="A", target_node_id="C"),
            TaskGraphEdge(source_node_id="A", target_node_id="D"),
            TaskGraphEdge(source_node_id="C", target_node_id="E"),
            TaskGraphEdge(source_node_id="D", target_node_id="E"),
        ]
    )
    node_runs = [
        NodeRunRecord(
            node_id="A",
            name="A",
            prompt="A",
            order=0,
            status=NodeRunStatus.SUCCEEDED,
            attempt=1,
            input="request",
            output="A result",
            result={"node_id": "A"},
            attempt_history=[_attempt("A", "succeeded", "A result")],
        ),
        NodeRunRecord(
            node_id="C",
            name="C",
            prompt="C",
            order=1,
            status=NodeRunStatus.FAILED,
            attempt=1,
            input="A result",
            output="old C failure",
            error="C failed",
            attempt_history=[_attempt("C", "failed", "old C failure")],
        ),
        NodeRunRecord(
            node_id="E",
            name="E",
            prompt="E",
            order=2,
            status=NodeRunStatus.SUCCEEDED,
            attempt=1,
            input="old inputs",
            output="stale E result",
            result={"node_id": "E"},
            attempt_history=[_attempt("E", "succeeded", "stale E result")],
        ),
        NodeRunRecord(
            node_id="D",
            name="D",
            prompt="D",
            order=3,
            status=NodeRunStatus.SUCCEEDED,
            attempt=1,
            input="A result",
            output="D result",
            result={"node_id": "D"},
            attempt_history=[_attempt("D", "succeeded", "D result")],
        ),
    ]
    return TaskRunRecord(
        id="retry-run",
        task_id="retry-task",
        user_id="owner",
        task_revision=1,
        nodes=nodes,
        node_runs=node_runs,
        engine="dag",
        graph=graph,
        status=RunStatus.FAILED,
        error="C failed",
    )


class TaskDagRetryTest(IsolatedAsyncioTestCase):
    async def test_retry_preserves_ancestors_and_late_order_sibling(self):
        run = _failed_run()
        store = TaskStore()
        await store.save_run(run)
        executor = _Executor()
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        try:
            service._run_users[run.id] = run.user_id
            request = TaskRunControlRequest(
                request_id="retry-c-1",
                expected_run_version=run.run_version,
            )
            paused = await service.retry_node(
                "owner",
                run.id,
                "C",
                request,
            )
            self.assertEqual(paused.status, RunStatus.QUEUED)
            self.assertEqual(paused.control_mode, "paused")
            after = {item.node_id: item for item in paused.node_runs}
            self.assertEqual(after["A"].output, "A result")
            self.assertEqual(after["A"].attempt, 1)
            self.assertEqual(after["D"].output, "D result")
            self.assertEqual(after["D"].attempt, 1)
            self.assertEqual(after["C"].status, NodeRunStatus.PENDING)
            self.assertEqual(after["C"].output, "")
            self.assertEqual(after["C"].attempt_history[0].output, "old C failure")
            self.assertEqual(after["C"].metadata["next_attempt_source"], "manual")
            self.assertEqual(after["E"].status, NodeRunStatus.PENDING)
            self.assertEqual(after["E"].output, "")
            self.assertEqual(after["E"].result, None)
            self.assertEqual(
                after["E"].attempt_history[0].output,
                "stale E result",
            )

            duplicate = await service.retry_node(
                "owner",
                run.id,
                "C",
                request,
            )
            self.assertEqual(duplicate.run_version, paused.run_version)
            with self.assertRaises(TaskStoreConflictError):
                await service.retry_node(
                    "owner",
                    run.id,
                    "C",
                    TaskRunControlRequest(
                        request_id="retry-c-stale",
                        expected_run_version=run.run_version,
                    ),
                )

            resumed = await service.continue_run(
                "owner",
                run.id,
                TaskRunControlRequest(
                    request_id="continue-after-retry",
                    expected_run_version=paused.run_version,
                ),
            )
            await service._active_runs[run.id]
            completed = await store.get_run("owner", run.id)
            self.assertEqual(completed.status, RunStatus.SUCCEEDED)
            self.assertEqual(completed.id, resumed.id)
            self.assertEqual(set(executor.calls), {"C", "E"})
            final_nodes = {item.node_id: item for item in completed.node_runs}
            self.assertEqual(final_nodes["A"].attempt, 1)
            self.assertEqual(final_nodes["D"].attempt, 1)
            self.assertEqual(final_nodes["D"].output, "D result")
            self.assertEqual(final_nodes["C"].attempt, 2)
            self.assertEqual(
                [item.source for item in final_nodes["C"].attempt_history],
                ["automatic", "manual"],
            )
            self.assertEqual(final_nodes["E"].output, "new:E")
        finally:
            await service.shutdown()

    async def test_tool_retry_requires_explicit_side_effect_verification(self):
        node = TaskNode(
            id="submit",
            name="submit",
            type="tool",
            config=ToolStepConfig(tool_name="erp.submit", arguments={}),
            capability_key="business.submit",
            order=0,
        )
        run = TaskRunRecord(
            id="side-effect-run",
            task_id="task",
            user_id="owner",
            task_revision=1,
            nodes=[node],
            node_runs=[
                NodeRunRecord(
                    node_id=node.id,
                    name=node.name,
                    prompt=node.prompt,
                    type=node.type,
                    order=0,
                    status=NodeRunStatus.FAILED,
                    attempt=1,
                    error="Unknown submission result",
                )
            ],
            engine="dag",
            graph=TaskGraph(),
            status=RunStatus.FAILED,
        )
        store = TaskStore()
        await store.save_run(run)
        service = TaskService(store, enable_dag_scheduler=True)
        try:
            with self.assertRaisesRegex(
                TaskWorkflowConflictError,
                "side_effects_verified=true",
            ):
                await service.retry_node(
                    "owner",
                    run.id,
                    node.id,
                    TaskRunControlRequest(
                        request_id="unsafe",
                        expected_run_version=run.run_version,
                    ),
                )
        finally:
            await service.shutdown()

    async def test_independent_failed_branches_require_separate_retry_commands(self):
        run = _failed_run()
        current_d = run.node_runs[3]
        run.node_runs[3] = current_d.model_copy(
            update={
                "status": NodeRunStatus.FAILED,
                "error": "D failed",
                "output": "old D failure",
                "attempt_history": [
                    _attempt("D", "failed", "old D failure")
                ],
            }
        )
        store = TaskStore()
        await store.save_run(run)
        service = TaskService(store, enable_dag_scheduler=True)
        try:
            first = await service.retry_node(
                "owner",
                run.id,
                "C",
                TaskRunControlRequest(
                    request_id="retry-c",
                    expected_run_version=run.run_version,
                ),
            )
            self.assertEqual(first.status, RunStatus.FAILED)
            self.assertEqual(first.control_mode, "paused")
            self.assertEqual(
                next(item for item in first.node_runs if item.node_id == "D").status,
                NodeRunStatus.FAILED,
            )

            second = await service.retry_node(
                "owner",
                run.id,
                "D",
                TaskRunControlRequest(
                    request_id="retry-d",
                    expected_run_version=first.run_version,
                ),
            )
            self.assertEqual(second.status, RunStatus.QUEUED)
            self.assertEqual(second.control_mode, "paused")
            self.assertEqual(
                {
                    item.node_id: item.status
                    for item in second.node_runs
                }["C"],
                NodeRunStatus.PENDING,
            )
            self.assertEqual(
                {
                    item.node_id: item.status
                    for item in second.node_runs
                }["D"],
                NodeRunStatus.PENDING,
            )
        finally:
            await service.shutdown()

    async def test_retry_route_reports_conflicts_as_http_409(self):
        run = _failed_run()
        store = TaskStore()
        await store.save_run(run)
        service = TaskService(store)
        app = FastAPI()
        app.state.task_service = service
        app.dependency_overrides[get_current_user_id] = lambda: "owner"
        app.include_router(task_router)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                stale = await client.post(
                    f"/tasks/runs/{run.id}/nodes/C/retry",
                    json={
                        "request_id": "stale",
                        "expected_run_version": 5,
                    },
                )
                self.assertEqual(stale.status_code, 409)
                invalid_node = await client.post(
                    f"/tasks/runs/{run.id}/nodes/not-a-node/retry",
                    json={
                        "request_id": "succeeded-node",
                        "expected_run_version": run.run_version,
                    },
                )
                self.assertEqual(invalid_node.status_code, 409)
        finally:
            await service.shutdown()

    async def test_successful_node_retry_invalidates_descendants_and_reruns_them(self):
        nodes = [_agent("A", 0), _agent("B", 1), _agent("C", 2)]
        graph = TaskGraph(
            edges=[
                TaskGraphEdge(source_node_id="A", target_node_id="B"),
                TaskGraphEdge(source_node_id="B", target_node_id="C"),
            ]
        )
        run = TaskRunRecord(
            id="successful-node-retry-run",
            task_id="retry-task",
            user_id="owner",
            task_revision=1,
            nodes=nodes,
            graph=graph,
            engine="dag",
            status=RunStatus.FAILED,
            error="C failed",
            node_runs=[
                NodeRunRecord(
                    node_id="A",
                    name="A",
                    prompt="A",
                    order=0,
                    status=NodeRunStatus.SUCCEEDED,
                    attempt=1,
                    output="A retained",
                    attempt_history=[_attempt("A", "succeeded", "A retained")],
                ),
                NodeRunRecord(
                    node_id="B",
                    name="B",
                    prompt="B",
                    order=1,
                    status=NodeRunStatus.SUCCEEDED,
                    attempt=1,
                    output="B old result",
                    attempt_history=[_attempt("B", "succeeded", "B old result")],
                ),
                NodeRunRecord(
                    node_id="C",
                    name="C",
                    prompt="C",
                    order=2,
                    status=NodeRunStatus.FAILED,
                    attempt=1,
                    output="C old failure",
                    error="C failed",
                    attempt_history=[_attempt("C", "failed", "C old failure")],
                ),
            ],
        )
        store = TaskStore()
        await store.save_run(run)
        executor = _Executor()
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        service._run_users[run.id] = run.user_id
        try:
            retried = await service.retry_node(
                "owner",
                run.id,
                "B",
                TaskRunControlRequest(
                    request_id="retry-successful-B",
                    expected_run_version=run.run_version,
                ),
            )
            self.assertEqual(retried.status, RunStatus.QUEUED)
            self.assertEqual(retried.control_mode, "paused")
            current = {item.node_id: item for item in retried.node_runs}
            self.assertEqual(current["A"].status, NodeRunStatus.SUCCEEDED)
            self.assertEqual(current["A"].output, "A retained")
            self.assertEqual(current["B"].status, NodeRunStatus.PENDING)
            self.assertEqual(current["B"].output, "")
            self.assertEqual(
                current["B"].attempt_history[-1].status,
                "succeeded",
            )
            self.assertEqual(current["C"].status, NodeRunStatus.PENDING)
            self.assertEqual(
                current["C"].attempt_history[-1].status,
                "failed",
            )

            resumed = await service.continue_run(
                "owner",
                run.id,
                TaskRunControlRequest(
                    request_id="continue-successful-B",
                    expected_run_version=retried.run_version,
                ),
            )
            await service._active_runs[run.id]
            completed = await store.get_run("owner", run.id)
            self.assertEqual(completed.id, resumed.id)
            self.assertEqual(completed.status, RunStatus.SUCCEEDED)
            self.assertEqual(executor.calls, ["B", "C"])
            final = {item.node_id: item for item in completed.node_runs}
            self.assertEqual(final["A"].attempt, 1)
            self.assertEqual(final["B"].attempt, 2)
            self.assertEqual(final["C"].attempt, 2)
        finally:
            await service.shutdown()

    async def test_retry_rejects_when_a_descendant_is_still_running(self):
        run = _failed_run()
        node_runs = [
            item.model_copy(update={"status": NodeRunStatus.RUNNING})
            if item.node_id == "C"
            else item
            for item in run.node_runs
        ]
        run = run.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "node_runs": node_runs,
            }
        )
        store = TaskStore()
        await store.save_run(run)
        service = TaskService(store, enable_dag_scheduler=True)
        try:
            with self.assertRaisesRegex(Exception, "node is still active"):
                await service.retry_node(
                    "owner",
                    run.id,
                    "A",
                    TaskRunControlRequest(
                        request_id="unsafe-in-flight-retry",
                        expected_run_version=run.run_version,
                    ),
                )
            unchanged = await store.get_run("owner", run.id)
            self.assertEqual(unchanged.run_version, run.run_version)
            self.assertEqual(
                next(
                    item.status
                    for item in unchanged.node_runs
                    if item.node_id == "C"
                ),
                NodeRunStatus.RUNNING,
            )
        finally:
            await service.shutdown()
