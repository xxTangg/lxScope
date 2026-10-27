# -*- coding: utf-8 -*-
"""Unified control and retry checks through the TaskService SOP path."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase

from fastapi import FastAPI
import httpx

from agentscope.app.deps import get_current_user_id
from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._models import (
    AgentStepConfig,
    CreateTaskRequest,
    InputStepConfig,
    NodeRunRecord,
    NodeRunStatus,
    RunStatus,
    StepVerdict,
    TaskInputQuestion,
    TaskInputReply,
    TaskNode,
    TaskRunRecord,
    TaskRunControlRequest,
    TaskRunRequest,
    ToolStepConfig,
)
from examples.agent_service.task._service import (
    TaskService,
    TaskWorkflowConflictError,
)
from examples.agent_service.task._router import _get_task_service, task_router
from examples.agent_service.task._store import TaskStore, TaskStoreConflictError
from examples.agent_service.task._sop import TaskSOPRunState, TaskStepState
from agentscope.sop import SOPPhase


def _agent(node_id: str, order: int) -> TaskNode:
    return TaskNode(
        id=node_id,
        name=node_id,
        order=order,
        config=AgentStepConfig(prompt=f"SYNTHETIC {node_id}"),
    )


class _Executor:
    def __init__(self, *, fail_once: str | None = None) -> None:
        self.calls: list[str] = []
        self.fail_once = fail_once
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block_node: str | None = None

    async def execute_node(self, *, node, previous_output, context):
        del previous_output, context
        self.calls.append(node.id)
        if node.id == self.block_node:
            self.entered.set()
            await self.release.wait()
        if node.id == self.fail_once:
            self.fail_once = None
            return StepExecutionResult(
                text="synthetic failure",
                status="failed",
                reason="synthetic failure",
            )
        return StepExecutionResult(
            text=f"SYNTHETIC-OUTPUT-{node.id}",
            result={"node_id": node.id, "synthetic": True},
        )

    async def verify_node(self, **kwargs):
        del kwargs
        return StepVerdict(passed=True)


class TaskSopControlTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = TaskStore()
        self.executor = _Executor()
        self.service = TaskService(self.store, preview_executor=self.executor)

    async def asyncTearDown(self) -> None:
        await self.service.shutdown()

    async def _create_run(self, nodes: list[TaskNode]):
        task = await self.service.create_task(
            "synthetic-owner",
            CreateTaskRequest(
                title="Synthetic SOP controls",
                goal="SYNTHETIC goal used for isolated tests",
                nodes=nodes,
            ),
        )
        run = await self.service.create_run(
            "synthetic-owner",
            task.id,
            TaskRunRequest(input="SYNTHETIC input"),
        )
        self.assertIsNotNone(run)
        assert run is not None
        worker = self.service._active_runs[run.id]  # pylint: disable=protected-access
        return task, run, worker

    async def _request(self, run_id: str, request_id: str, version: int):
        return TaskRunControlRequest(
            request_id=request_id,
            expected_run_version=version,
        )

    async def test_pause_continue_step_stops_at_one_linear_node(self):
        self.executor.block_node = "A"
        task, run, worker = await self._create_run(
            [_agent("A", 0), _agent("B", 1), _agent("C", 2)]
        )
        await asyncio.wait_for(self.executor.entered.wait(), timeout=2)
        current = await self.store.get_run("synthetic-owner", run.id)
        assert current is not None
        requested = await self.service.pause_run(
            "synthetic-owner",
            run.id,
            await self._request(run.id, "pause-first", current.run_version),
        )
        assert requested is not None
        self.executor.release.set()
        await worker
        paused = await self.store.get_run("synthetic-owner", run.id)
        assert paused is not None
        self.assertEqual(paused.status, RunStatus.QUEUED)
        self.assertEqual(paused.control_mode, "paused")
        self.assertEqual(paused.node_runs[0].status, NodeRunStatus.SUCCEEDED)
        self.assertEqual(paused.node_runs[1].status, NodeRunStatus.PENDING)

        await self.service.shutdown()
        self.service = TaskService(self.store, preview_executor=self.executor)

        continued = await self.service.continue_run(
            "synthetic-owner",
            run.id,
            await self._request(run.id, "continue-first", paused.run_version),
        )
        assert continued is not None
        await self.service._active_runs[run.id]  # pylint: disable=protected-access
        completed = await self.store.get_run("synthetic-owner", run.id)
        assert completed is not None
        self.assertEqual(completed.status, RunStatus.SUCCEEDED)
        self.assertEqual(self.executor.calls, ["A", "B", "C"])
        self.assertEqual(
            [item.output for item in completed.node_runs],
            ["SYNTHETIC-OUTPUT-A", "SYNTHETIC-OUTPUT-B", "SYNTHETIC-OUTPUT-C"],
        )

    async def test_step_dispatches_exactly_one_sop_node_and_is_idempotent(self):
        self.executor.block_node = "A"
        _, run, worker = await self._create_run(
            [_agent("A", 0), _agent("B", 1), _agent("C", 2)]
        )
        await asyncio.wait_for(self.executor.entered.wait(), timeout=2)
        current = await self.store.get_run("synthetic-owner", run.id)
        assert current is not None
        await self.service.pause_run(
            "synthetic-owner",
            run.id,
            await self._request(run.id, "pause-for-step", current.run_version),
        )
        self.executor.release.set()
        await worker
        paused = await self.store.get_run("synthetic-owner", run.id)
        assert paused is not None
        step_request = await self._request(run.id, "step-B", paused.run_version)
        await self.service.step_run("synthetic-owner", run.id, step_request)
        await self.service._active_runs[run.id]  # pylint: disable=protected-access
        after_step = await self.store.get_run("synthetic-owner", run.id)
        assert after_step is not None
        self.assertEqual(after_step.control_mode, "paused")
        self.assertEqual(after_step.status, RunStatus.QUEUED)
        self.assertEqual(self.executor.calls, ["A", "B"])
        self.assertEqual(after_step.node_runs[1].status, NodeRunStatus.SUCCEEDED)
        self.assertEqual(after_step.node_runs[2].status, NodeRunStatus.PENDING)

        duplicate = await self.service.step_run(
            "synthetic-owner",
            run.id,
            TaskRunControlRequest(
                request_id="step-B",
                expected_run_version=paused.run_version,
            ),
        )
        self.assertEqual(duplicate.run_version, after_step.run_version)
        await self.service.step_run(
            "synthetic-owner",
            run.id,
            await self._request(run.id, "step-C", after_step.run_version),
        )
        await self.service._active_runs[run.id]  # pylint: disable=protected-access
        completed = await self.store.get_run("synthetic-owner", run.id)
        assert completed is not None
        self.assertEqual(completed.status, RunStatus.SUCCEEDED)
        self.assertEqual(self.executor.calls, ["A", "B", "C"])

    async def test_failed_node_retry_keeps_run_and_successful_prefix(self):
        self.executor.fail_once = "C"
        _, run, worker = await self._create_run(
            [_agent("A", 0), _agent("B", 1), _agent("C", 2), _agent("D", 3)]
        )
        await worker
        failed = await self.store.get_run("synthetic-owner", run.id)
        assert failed is not None
        self.assertEqual(failed.status, RunStatus.FAILED)
        self.assertEqual(failed.node_runs[0].status, NodeRunStatus.SUCCEEDED)
        self.assertEqual(failed.node_runs[1].status, NodeRunStatus.SUCCEEDED)
        self.assertEqual(failed.node_runs[2].status, NodeRunStatus.FAILED)
        self.assertEqual(failed.node_runs[3].status, NodeRunStatus.PENDING)
        self.assertEqual(self.executor.calls, ["A", "B", "C"])

        retry_request = await self._request(run.id, "retry-C", failed.run_version)
        retried = await self.service.retry_node(
            "synthetic-owner", run.id, "C", retry_request
        )
        assert retried is not None
        duplicate = await self.service.retry_node(
            "synthetic-owner", run.id, "C", retry_request
        )
        assert duplicate is not None
        self.assertEqual(retried.id, run.id)
        self.assertEqual(duplicate.run_version, retried.run_version)
        self.assertEqual(retried.node_runs[0].output, "SYNTHETIC-OUTPUT-A")
        self.assertEqual(retried.node_runs[1].output, "SYNTHETIC-OUTPUT-B")
        self.assertEqual(len(retried.node_runs[2].attempt_history), 1)
        await self.service.continue_run(
            "synthetic-owner",
            run.id,
            await self._request(run.id, "continue-after-retry", retried.run_version),
        )
        await self.service._active_runs[run.id]  # pylint: disable=protected-access
        completed = await self.store.get_run("synthetic-owner", run.id)
        assert completed is not None
        self.assertEqual(completed.id, run.id)
        self.assertEqual(completed.status, RunStatus.SUCCEEDED)
        self.assertEqual(self.executor.calls, ["A", "B", "C", "C", "D"])
        self.assertEqual(completed.node_runs[0].output, "SYNTHETIC-OUTPUT-A")
        self.assertEqual(completed.node_runs[1].output, "SYNTHETIC-OUTPUT-B")
        self.assertEqual(len(completed.node_runs[2].attempt_history), 2)
        self.assertEqual(
            [item.status for item in completed.node_runs[2].attempt_history],
            ["failed", "succeeded"],
        )

    async def test_successful_node_retry_invalidates_linear_descendants(self):
        self.executor.fail_once = "C"
        _, run, worker = await self._create_run(
            [_agent("A", 0), _agent("B", 1), _agent("C", 2), _agent("D", 3)]
        )
        await worker
        failed = await self.store.get_run("synthetic-owner", run.id)
        assert failed is not None
        retried = await self.service.retry_node(
            "synthetic-owner",
            run.id,
            "B",
            await self._request(run.id, "retry-successful-B", failed.run_version),
        )
        assert retried is not None
        self.assertEqual(retried.node_runs[0].status, NodeRunStatus.SUCCEEDED)
        self.assertEqual(retried.node_runs[1].status, NodeRunStatus.PENDING)
        self.assertEqual(retried.node_runs[2].status, NodeRunStatus.PENDING)
        self.assertEqual(retried.node_runs[3].status, NodeRunStatus.PENDING)
        self.assertEqual(len(retried.node_runs[1].attempt_history), 1)
        await self.service.continue_run(
            "synthetic-owner",
            run.id,
            await self._request(run.id, "continue-after-B", retried.run_version),
        )
        await self.service._active_runs[run.id]  # pylint: disable=protected-access
        completed = await self.store.get_run("synthetic-owner", run.id)
        assert completed is not None
        self.assertEqual(self.executor.calls, ["A", "B", "C", "B", "C", "D"])
        self.assertEqual(completed.node_runs[0].attempt, 1)
        self.assertEqual(completed.node_runs[1].attempt, 2)

    async def test_stop_is_terminal_and_input_resume_keeps_same_run(self):
        self.executor.block_node = "A"
        _, run, worker = await self._create_run([_agent("A", 0), _agent("B", 1)])
        await asyncio.wait_for(self.executor.entered.wait(), timeout=2)
        current = await self.store.get_run("synthetic-owner", run.id)
        assert current is not None
        stopped = await self.service.stop_run(
            "synthetic-owner",
            run.id,
            await self._request(run.id, "stop-active", current.run_version),
        )
        assert stopped is not None
        await worker
        self.assertEqual(stopped.status, RunStatus.CANCELED)
        self.assertEqual(stopped.control_mode, "stopped")
        self.assertEqual(self.executor.calls, ["A"])
        with self.assertRaises(TaskWorkflowConflictError):
            await self.service.continue_run(
                "synthetic-owner",
                run.id,
                await self._request(run.id, "continue-stopped", stopped.run_version),
            )

        self.executor.block_node = None
        task = await self.service.create_task(
            "synthetic-owner",
            CreateTaskRequest(
                title="Synthetic input resume",
                goal="SYNTHETIC input checkpoint",
                nodes=[
                    TaskNode(
                        id="collect",
                        name="collect",
                        order=0,
                        type="input",
                        config=InputStepConfig(
                            questions=[
                                TaskInputQuestion(key="value", label="Value?")
                            ]
                        ),
                    ),
                    _agent("deliver", 1),
                ],
            ),
        )
        waiting = await self.service.create_run(
            "synthetic-owner",
            task.id,
            TaskRunRequest(input="SYNTHETIC input"),
        )
        assert waiting is not None
        await self.service._active_runs[waiting.id]  # pylint: disable=protected-access
        waiting = await self.store.get_run("synthetic-owner", waiting.id)
        assert waiting is not None and waiting.pending_input is not None
        answered = await self.service.answer_run(
            "synthetic-owner",
            waiting.id,
            TaskInputReply(
                request_id=waiting.pending_input.id,
                answers={"value": "SYNTHETIC answer"},
            ),
        )
        assert answered is not None
        await self.service._active_runs[waiting.id]  # pylint: disable=protected-access
        completed = await self.store.get_run("synthetic-owner", waiting.id)
        assert completed is not None
        self.assertEqual(completed.status, RunStatus.SUCCEEDED)
        self.assertEqual(completed.node_runs[0].attempt, 1)

    async def test_stale_version_and_malformed_snapshot_fail_closed(self):
        _, run, worker = await self._create_run([_agent("A", 0), _agent("B", 1)])
        await worker
        completed = await self.store.get_run("synthetic-owner", run.id)
        assert completed is not None
        with self.assertRaises(TaskStoreConflictError):
            await self.service.pause_run(
                "synthetic-owner",
                run.id,
                await self._request(run.id, "stale-pause", 0),
            )

        state = dict(completed.sop_state or {})
        state["task_node_ids"] = ["B", "A"]
        corrupted = completed.model_copy(update={"sop_state": state})
        with self.assertRaises(TaskWorkflowConflictError):
            self.service._task_sop_state(corrupted)  # pylint: disable=protected-access

    async def test_authenticated_task_api_controls_a_real_sop_run(self):
        self.executor.block_node = "A"
        _, run, worker = await self._create_run([_agent("A", 0), _agent("B", 1)])
        await asyncio.wait_for(self.executor.entered.wait(), timeout=2)
        current = await self.store.get_run("synthetic-owner", run.id)
        assert current is not None
        app = FastAPI()
        app.include_router(task_router)
        app.dependency_overrides[get_current_user_id] = lambda: "synthetic-owner"
        app.dependency_overrides[_get_task_service] = lambda: self.service
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://task.test",
            ) as client:
                response = await client.post(
                    f"/tasks/runs/{run.id}/pause",
                    json={
                        "request_id": "api-pause",
                        "expected_run_version": current.run_version,
                    },
                )
                self.assertEqual(response.status_code, 202, response.text)
                self.assertEqual(response.json()["engine"], "sop")
                self.executor.release.set()
                await worker
                paused = await self.store.get_run("synthetic-owner", run.id)
                assert paused is not None
                response = await client.post(
                    f"/tasks/runs/{run.id}/continue",
                    json={
                        "request_id": "api-continue",
                        "expected_run_version": paused.run_version,
                    },
                )
                self.assertEqual(response.status_code, 202, response.text)
                await self.service._active_runs[run.id]  # pylint: disable=protected-access
                completed = await self.store.get_run("synthetic-owner", run.id)
                assert completed is not None
                self.assertEqual(completed.status, RunStatus.SUCCEEDED)
        finally:
            app.dependency_overrides.clear()

    async def test_authenticated_task_api_retries_node_in_the_same_sop_run(self):
        self.executor.fail_once = "B"
        _, run, worker = await self._create_run(
            [_agent("A", 0), _agent("B", 1), _agent("C", 2)]
        )
        await worker
        failed = await self.store.get_run("synthetic-owner", run.id)
        assert failed is not None
        self.assertEqual(failed.status, RunStatus.FAILED)

        app = FastAPI()
        app.include_router(task_router)
        app.dependency_overrides[get_current_user_id] = lambda: "synthetic-owner"
        app.dependency_overrides[_get_task_service] = lambda: self.service
        payload = {
            "request_id": "api-retry-B",
            "expected_run_version": failed.run_version,
        }
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://task.test",
            ) as client:
                response = await client.post(
                    f"/tasks/runs/{run.id}/nodes/B/retry",
                    json=payload,
                )
                self.assertEqual(response.status_code, 202, response.text)
                self.assertEqual(response.json()["id"], run.id)
                self.assertEqual(response.json()["engine"], "sop")
                self.assertEqual(response.json()["node_runs"][0]["status"], "succeeded")
                self.assertEqual(response.json()["node_runs"][1]["status"], "pending")
                self.assertEqual(response.json()["node_runs"][2]["status"], "pending")

                duplicate = await client.post(
                    f"/tasks/runs/{run.id}/nodes/B/retry",
                    json=payload,
                )
                self.assertEqual(duplicate.status_code, 202, duplicate.text)
                self.assertEqual(
                    duplicate.json()["run_version"],
                    response.json()["run_version"],
                )
        finally:
            app.dependency_overrides.clear()

    async def test_expired_worker_recovery_requires_manual_side_effect_check(self):
        node = TaskNode(
            id="interrupted-tool",
            name="interrupted-tool",
            order=0,
            type="tool",
            config=ToolStepConfig(tool_name="synthetic.write", arguments={}),
        )
        run = TaskRunRecord(
            id="interrupted-sop-run",
            task_id="interrupted-sop-task",
            user_id="synthetic-owner",
            task_revision=1,
            engine="sop",
            status=RunStatus.RUNNING,
            nodes=[node],
            node_runs=[
                NodeRunRecord(
                    node_id=node.id,
                    name=node.name,
                    prompt=node.prompt,
                    type=node.type,
                    order=0,
                    status=NodeRunStatus.RUNNING,
                    attempt=1,
                    started_at=datetime.now(timezone.utc) - timedelta(seconds=3),
                )
            ],
            input="SYNTHETIC input",
            sop_state=TaskSOPRunState(
                id="interrupted-sop-run",
                steps=[TaskStepState(phase=SOPPhase.RUNNING)],
                task_node_ids=[node.id],
            ).model_dump(mode="json"),
        )
        await self.store.save_run(run)
        await self.store.claim_run(
            "synthetic-owner",
            run.id,
            owner_token="expired-owner",
            lease_seconds=0.001,
        )
        await asyncio.sleep(0.01)

        recovery_service = TaskService(self.store, preview_executor=self.executor)
        try:
            await recovery_service.get_run("synthetic-owner", run.id)
            await recovery_service._active_runs[run.id]  # pylint: disable=protected-access
            recovered = await self.store.get_run("synthetic-owner", run.id)
            assert recovered is not None
            self.assertEqual(recovered.status, RunStatus.FAILED)
            self.assertEqual(recovered.node_runs[0].status, NodeRunStatus.FAILED)
            self.assertTrue(recovered.node_runs[0].metadata["recovery_required"])
            self.assertEqual(
                recovered.node_runs[0].metadata["recovery_policy"],
                "manual_verification",
            )
            self.assertEqual(len(recovered.node_runs[0].attempt_history), 1)
            with self.assertRaises(TaskWorkflowConflictError):
                await recovery_service.retry_node(
                    "synthetic-owner",
                    run.id,
                    node.id,
                    await self._request(
                        run.id,
                        "retry-interrupted-unconfirmed",
                        recovered.run_version,
                    ),
                )
            retried = await recovery_service.retry_node(
                "synthetic-owner",
                run.id,
                node.id,
                TaskRunControlRequest(
                    request_id="retry-interrupted-confirmed",
                    expected_run_version=recovered.run_version,
                    side_effects_verified=True,
                ),
            )
            assert retried is not None
            self.assertEqual(retried.status, RunStatus.QUEUED)
            self.assertIsNotNone(
                retried.node_runs[0].metadata.get("side_effects_verified")
            )
        finally:
            await recovery_service.shutdown()
