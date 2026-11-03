# -*- coding: utf-8 -*-
"""Task DAG pause, continue, step, and stop behavior."""

import asyncio
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
    TaskInputQuestion,
    TaskNode,
    TaskRunControlRequest,
    TaskRunRecord,
)
from examples.agent_service.task._router import task_router
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore


def _agent(node_id: str, order: int) -> TaskNode:
    return TaskNode(
        id=node_id,
        name=node_id,
        order=order,
        config=AgentStepConfig(prompt=node_id),
    )


def _run(
    nodes: list[TaskNode],
    edges: list[tuple[str, str]] | None = None,
) -> TaskRunRecord:
    return TaskRunRecord(
        id="control-run",
        task_id="control-task",
        user_id="owner",
        task_revision=1,
        engine="dag",
        graph=TaskGraph(
            edges=[
                TaskGraphEdge(source_node_id=source, target_node_id=target)
                for source, target in (edges or [])
            ],
            max_concurrency=max(1, len(nodes)),
        ),
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
        input="input",
    )


class _Executor:
    def __init__(self, *, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.calls: list[str] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block_node: str | None = None
        self.swallow_cancel = False

    async def execute_node(self, *, node, previous_output, context):
        del previous_output, context
        self.calls.append(node.id)
        if node.id == self.block_node:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                if not self.swallow_cancel:
                    raise
                return StepExecutionResult(text="late")
        if node.id in self.fail:
            return StepExecutionResult(
                text="",
                status="failed",
                reason="controlled failure",
            )
        return StepExecutionResult(text=f"done:{node.id}")

    async def verify_node(self, **kwargs):
        del kwargs
        return StepVerdict(passed=True)


class TaskDagControlTest(IsolatedAsyncioTestCase):
    async def test_pause_waits_for_node_checkpoint_then_continue_skips_success(self):
        run = _run([_agent("A", 0), _agent("B", 1)], [("A", "B")])
        store = TaskStore()
        await store.save_run(run)
        executor = _Executor()
        executor.block_node = "A"
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        try:
            service._schedule_run(run, None)
            worker = service._active_runs[run.id]
            await asyncio.wait_for(executor.entered.wait(), timeout=2)
            current = await store.get_run("owner", run.id)
            requested = await service.pause_run(
                "owner",
                run.id,
                TaskRunControlRequest(
                    request_id="pause-1",
                    expected_run_version=current.run_version,
                ),
            )
            self.assertEqual(requested.control_mode, "pause_requested")
            executor.release.set()
            await worker
            paused = await store.get_run("owner", run.id)
            self.assertEqual(paused.control_mode, "paused")
            self.assertEqual(paused.status, RunStatus.QUEUED)
            self.assertEqual(paused.node_runs[0].status, NodeRunStatus.SUCCEEDED)
            self.assertEqual(paused.node_runs[1].status, NodeRunStatus.PENDING)

            await service.shutdown()
            service = TaskService(
                store,
                preview_executor=executor,
                enable_dag_scheduler=True,
            )
            resumed = await service.continue_run(
                "owner",
                run.id,
                TaskRunControlRequest(
                    request_id="continue-1",
                    expected_run_version=paused.run_version,
                ),
            )
            await service._active_runs[run.id]
            completed = await store.get_run("owner", run.id)
            self.assertEqual(resumed.id, completed.id)
            self.assertEqual(completed.status, RunStatus.SUCCEEDED)
            self.assertEqual(executor.calls.count("A"), 1)
            self.assertEqual(executor.calls.count("B"), 1)
        finally:
            await service.shutdown()

    async def test_step_runs_only_first_ready_node_and_pauses(self):
        run = _run(
            [_agent("B", 1), _agent("C", 2)],
        )
        store = TaskStore()
        await store.save_run(run)
        executor = _Executor()
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        try:
            paused = await service.pause_run(
                "owner",
                run.id,
                TaskRunControlRequest(
                    request_id="pause-before-step",
                    expected_run_version=run.run_version,
                ),
            )
            await service.shutdown()
            service = TaskService(
                store,
                preview_executor=executor,
                enable_dag_scheduler=True,
            )
            stepped = await service.step_run(
                "owner",
                run.id,
                TaskRunControlRequest(
                    request_id="step-1",
                    expected_run_version=paused.run_version,
                ),
            )
            await service._active_runs[run.id]
            result = await store.get_run("owner", run.id)
            self.assertEqual(stepped.id, result.id)
            self.assertEqual(executor.calls, ["B"])
            self.assertEqual(result.control_mode, "paused")
            self.assertEqual(result.status, RunStatus.QUEUED)
            self.assertEqual(result.node_runs[0].status, NodeRunStatus.SUCCEEDED)
            self.assertEqual(result.node_runs[1].status, NodeRunStatus.PENDING)
            self.assertTrue(
                any(event.type == "run.step_completed" for event in await store.list_events("owner", run.id))
            )
        finally:
            await service.shutdown()

    async def test_step_failure_and_stop_are_terminal_or_recoverable_as_defined(self):
        failed_run = _run([_agent("A", 0)])
        failed_store = TaskStore()
        await failed_store.save_run(failed_run)
        failed_service = TaskService(
            failed_store,
            preview_executor=_Executor(fail={"A"}),
            enable_dag_scheduler=True,
        )
        try:
            paused = await failed_service.pause_run(
                "owner",
                failed_run.id,
                TaskRunControlRequest(
                    request_id="pause-failed",
                    expected_run_version=failed_run.run_version,
                ),
            )
            await failed_service.step_run(
                "owner",
                failed_run.id,
                TaskRunControlRequest(
                    request_id="step-fails",
                    expected_run_version=paused.run_version,
                ),
            )
            await failed_service._active_runs[failed_run.id]
            failed = await failed_store.get_run("owner", failed_run.id)
            self.assertEqual(failed.status, RunStatus.FAILED)
        finally:
            await failed_service.shutdown()

        run = _run([_agent("A", 0), _agent("B", 1)], [("A", "B")])
        store = TaskStore()
        await store.save_run(run)
        executor = _Executor()
        executor.block_node = "A"
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
            current = await store.get_run("owner", run.id)
            stopped = await service.stop_run(
                "owner",
                run.id,
                TaskRunControlRequest(
                    request_id="stop-1",
                    expected_run_version=current.run_version,
                ),
            )
            self.assertEqual(stopped.status, RunStatus.CANCELED)
            await asyncio.gather(worker, return_exceptions=True)
            final = await store.get_run("owner", run.id)
            self.assertEqual(final.status, RunStatus.CANCELED)
            self.assertNotIn("B", executor.calls)
            events = await store.list_events("owner", run.id)
            stop_event = next(event for event in events if event.type == "run.canceled")
            self.assertFalse(stop_event.payload["side_effects_rolled_back"])
            with self.assertRaisesRegex(Exception, "terminal"):
                await service.continue_run(
                    "owner",
                    run.id,
                    TaskRunControlRequest(
                        request_id="continue-after-stop",
                        expected_run_version=final.run_version,
                    ),
                )
        finally:
            await service.shutdown()

    async def test_http_commands_return_409_for_stale_versions_and_invalid_continue(self):
        run = _run([_agent("A", 0)])
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
                    f"/tasks/runs/{run.id}/pause",
                    json={
                        "request_id": "stale-pause",
                        "expected_run_version": 99,
                    },
                )
                self.assertEqual(stale.status_code, 409)
                unpaused = await client.post(
                    f"/tasks/runs/{run.id}/continue",
                    json={
                        "request_id": "continue-running",
                        "expected_run_version": run.run_version,
                    },
                )
                self.assertEqual(unpaused.status_code, 409)
        finally:
            await service.shutdown()
