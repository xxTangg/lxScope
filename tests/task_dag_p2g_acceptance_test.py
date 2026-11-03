# -*- coding: utf-8 -*-
"""P2-G acceptance through the TaskService and Task HTTP API."""

import asyncio
from collections import Counter
import json
from unittest import IsolatedAsyncioTestCase

from fastapi import FastAPI
import httpx

from agentscope.app.deps import get_current_user_id
from examples.agent_service.task._executor import StepExecutionResult
from examples.agent_service.task._models import (
    AgentStepConfig,
    CreateTaskRequest,
    NodeRunRecord,
    NodeRunStatus,
    RunStatus,
    StepType,
    TaskGraph,
    TaskGraphEdge,
    TaskGraphInputBinding,
    TaskNode,
    TaskRunControlRequest,
    TaskRunRecord,
    TaskRunRequest,
    ToolStepConfig,
)
from examples.agent_service.task._router import task_router
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._store import TaskStore


def _node(
    node_id: str,
    order: int,
    *,
    requirements: dict[str, int] | None = None,
) -> TaskNode:
    return TaskNode(
        id=node_id,
        name=node_id,
        prompt=f"Execute {node_id}",
        order=order,
        config=AgentStepConfig(prompt=f"Execute {node_id}"),
        resource_requirements=requirements or {},
    )


def _graph(
    edges: list[tuple[str, str]],
    *,
    max_concurrency: int = 2,
    bindings: dict[str, dict[str, TaskGraphInputBinding]] | None = None,
    resource_limits: dict[str, int] | None = None,
) -> TaskGraph:
    return TaskGraph(
        edges=[
            TaskGraphEdge(source_node_id=source, target_node_id=target)
            for source, target in edges
        ],
        input_bindings=bindings or {},
        max_concurrency=max_concurrency,
        resource_limits=resource_limits or {},
    )


def _app(service: TaskService) -> FastAPI:
    app = FastAPI()
    app.state.task_service = service
    app.dependency_overrides[get_current_user_id] = lambda: "p2g-owner"
    app.include_router(task_router)
    return app


async def _create_task(
    client: httpx.AsyncClient,
    nodes: list[TaskNode],
    graph: TaskGraph | None,
) -> dict:
    body = CreateTaskRequest(
        title="P2-G acceptance task",
        goal="Verify Task execution with a deterministic local executor.",
        nodes=nodes,
        graph=graph,
    )
    response = await client.post("/tasks/", json=body.model_dump(mode="json"))
    if response.status_code != 201:
        raise AssertionError(
            f"Task creation returned {response.status_code}: {response.text}"
        )
    return response.json()


async def _wait_for_worker(service: TaskService, run_id: str) -> None:
    worker = service._active_runs.get(run_id)
    if worker is not None:
        await asyncio.wait_for(worker, timeout=8)


async def _get_run(client: httpx.AsyncClient, run_id: str) -> dict:
    response = await client.get(f"/tasks/runs/{run_id}")
    if response.status_code != 200:
        raise AssertionError(
            f"Run read returned {response.status_code}: {response.text}"
        )
    return response.json()


def _parse_sse_records(body: str) -> list[dict]:
    records: list[dict] = []
    for block in body.strip().split("\n\n"):
        data_line = next(
            (line[6:] for line in block.splitlines() if line.startswith("data: ")),
            None,
        )
        if data_line is not None:
            records.append(json.loads(data_line))
    return records


class _DeterministicExecutor:
    """Local-only executor with barriers for observable concurrency tests."""

    def __init__(
        self,
        *,
        fail_first: dict[str, int] | None = None,
        fail_always: set[str] | None = None,
        sync_branches: set[str] | None = None,
        block_node: str | None = None,
    ) -> None:
        self.fail_first = fail_first or {}
        self.fail_always = fail_always or set()
        self.sync_branches = sync_branches or set()
        self.block_node = block_node
        self.calls: list[str] = []
        self.call_counts: Counter[str] = Counter()
        self.contexts: dict[str, list[object]] = {}
        self.completed: set[str] = set()
        self.active: set[str] = set()
        self.maximum_parallel = 0
        self.resource_active: Counter[str] = Counter()
        self.resource_maximum: Counter[str] = Counter()
        self.entered_branches: set[str] = set()
        self.branches_started = asyncio.Event()
        self.release_branches = asyncio.Event()
        self.node_entered = asyncio.Event()
        self.release_node = asyncio.Event()

    async def execute_node(self, *, node, previous_output, context):
        del previous_output
        self.calls.append(node.id)
        self.call_counts[node.id] += 1
        self.contexts.setdefault(node.id, []).append(context)
        self.active.add(node.id)
        self.maximum_parallel = max(self.maximum_parallel, len(self.active))
        for resource, amount in node.resource_requirements.items():
            self.resource_active[resource] += amount
            self.resource_maximum[resource] = max(
                self.resource_maximum[resource],
                self.resource_active[resource],
            )
        try:
            if node.id == self.block_node and self.call_counts[node.id] == 1:
                self.node_entered.set()
                await self.release_node.wait()
            if node.id in self.sync_branches:
                self.entered_branches.add(node.id)
                if self.sync_branches.issubset(self.entered_branches):
                    self.branches_started.set()
                await asyncio.wait_for(self.branches_started.wait(), timeout=5)
                await self.release_branches.wait()
            if self.call_counts[node.id] <= self.fail_first.get(node.id, 0):
                return StepExecutionResult(
                    text=f"failure:{node.id}",
                    result={"node_id": node.id, "attempt": self.call_counts[node.id]},
                    status="failed",
                    reason=f"{node.id} failed on its first attempt",
                )
            if node.id in self.fail_always:
                return StepExecutionResult(
                    text=f"failure:{node.id}",
                    status="failed",
                    reason=f"{node.id} failed",
                )
            self.completed.add(node.id)
            return StepExecutionResult(
                text=f"output:{node.id}",
                result={"node_id": node.id, "value": f"result:{node.id}"},
            )
        finally:
            self.active.discard(node.id)
            for resource, amount in node.resource_requirements.items():
                self.resource_active[resource] -= amount

    async def verify_node(self, **kwargs):
        del kwargs
        from examples.agent_service.task._models import StepVerdict

        return StepVerdict(passed=True)


class TaskDagP2GApiAcceptanceTest(IsolatedAsyncioTestCase):
    async def test_api_gate_full_dag_execution_and_legacy_serial_compatibility(self):
        store = TaskStore()
        executor = _DeterministicExecutor(
            sync_branches={"B", "C"},
            block_node="D",
        )
        disabled = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=False,
        )
        app = _app(disabled)
        graph = _graph(
            [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D"), ("D", "E")],
            bindings={
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
        )
        nodes = [_node(node_id, index) for index, node_id in enumerate("ABCDE")]
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                task = await _create_task(client, nodes, graph)
                blocked = await client.post(
                    f"/tasks/{task['id']}/runs",
                    json=TaskRunRequest(input="p2g input").model_dump(mode="json"),
                )
                self.assertEqual(blocked.status_code, 409)
                self.assertIn("DAG execution is disabled", blocked.json()["detail"])
                self.assertEqual(executor.calls, [])

                await disabled.shutdown()
                enabled = TaskService(
                    store,
                    preview_executor=executor,
                    enable_dag_scheduler=True,
                )
                app.state.task_service = enabled
                started = await client.post(
                    f"/tasks/{task['id']}/runs",
                    json=TaskRunRequest(input="p2g input").model_dump(mode="json"),
                )
                self.assertEqual(started.status_code, 202, started.text)
                run_id = started.json()["id"]
                self.assertEqual(started.json()["engine"], "dag")

                await asyncio.wait_for(executor.branches_started.wait(), timeout=5)
                fan_out = await _get_run(client, run_id)
                status = {item["node_id"]: item["status"] for item in fan_out["node_runs"]}
                self.assertEqual(status["A"], "succeeded")
                self.assertEqual(status["B"], "running")
                self.assertEqual(status["C"], "running")
                self.assertEqual(status["D"], "pending")
                self.assertEqual(status["E"], "pending")
                self.assertTrue({"B", "C"}.issubset(executor.active))
                self.assertGreaterEqual(executor.maximum_parallel, 2)

                executor.release_branches.set()
                await asyncio.wait_for(executor.node_entered.wait(), timeout=5)
                fan_in = await _get_run(client, run_id)
                status = {item["node_id"]: item["status"] for item in fan_in["node_runs"]}
                self.assertEqual(status["B"], "succeeded")
                self.assertEqual(status["C"], "succeeded")
                self.assertEqual(status["D"], "running")
                self.assertEqual(status["E"], "pending")
                context = executor.contexts["D"][0]
                self.assertEqual(
                    context.named_inputs,
                    {
                        "branch_b": {"node_id": "B", "value": "result:B"},
                        "branch_c": {"node_id": "C", "value": "result:C"},
                    },
                )

                executor.release_node.set()
                await _wait_for_worker(enabled, run_id)
                completed = await _get_run(client, run_id)
                self.assertEqual(completed["status"], "succeeded")
                self.assertEqual(
                    {item["node_id"] for item in completed["node_runs"] if item["status"] == "succeeded"},
                    set("ABCDE"),
                )
                self.assertEqual(executor.call_counts, Counter(dict.fromkeys("ABCDE", 1)))
                self.assertIn("output:E", completed["final_output"])

                linear = await _create_task(client, [_node("L", 0)], None)
                linear_start = await client.post(
                    f"/tasks/{linear['id']}/runs",
                    json=TaskRunRequest(input="legacy input").model_dump(mode="json"),
                )
                self.assertEqual(linear_start.status_code, 202)
                linear_id = linear_start.json()["id"]
                await _wait_for_worker(enabled, linear_id)
                linear_done = await _get_run(client, linear_id)
                self.assertEqual(linear_done["status"], "succeeded")
                self.assertEqual(linear_done["engine"], "sop")
        finally:
            await disabled.shutdown()
            active_service = app.state.task_service
            if active_service is not disabled:
                await active_service.shutdown()

    async def test_task_api_rejects_cycles_bad_bindings_and_resource_oversubscription(self):
        service = TaskService(TaskStore(), enable_dag_scheduler=True)
        app = _app(service)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                invalid_cases = [
                    (
                        [_node("A", 0), _node("B", 1)],
                        _graph([("A", "B"), ("B", "A")]),
                        "graph.edges",
                    ),
                    (
                        [_node("A", 0), _node("B", 1), _node("C", 2)],
                        _graph(
                            [("A", "C")],
                            bindings={
                                "C": {
                                    "branch_b": TaskGraphInputBinding(
                                        source_node_id="B",
                                        source_value="result",
                                    )
                                }
                            },
                        ),
                        "graph.input_bindings.C.branch_b",
                    ),
                    (
                        [_node("A", 0, requirements={"database": 2})],
                        _graph(
                            [],
                            max_concurrency=1,
                            resource_limits={"database": 1},
                        ),
                        "nodes.A.resource_requirements.database",
                    ),
                ]
                for nodes, graph, expected_path in invalid_cases:
                    response = await client.post(
                        "/tasks/",
                        json=CreateTaskRequest(
                            title="invalid DAG",
                            goal="Reject invalid graph",
                            nodes=nodes,
                            graph=graph,
                        ).model_dump(mode="json"),
                    )
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertIn(
                        expected_path,
                        {issue["path"] for issue in response.json()["detail"]["issues"]},
                    )
        finally:
            await service.shutdown()

    async def test_api_resource_limits_prevent_parallel_conflicts(self):
        store = TaskStore()
        executor = _DeterministicExecutor(block_node="B")
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        app = _app(service)
        graph = _graph(
            [("A", "B"), ("A", "C")],
            resource_limits={"database": 1},
        )
        nodes = [
            _node("A", 0),
            _node("B", 1, requirements={"database": 1}),
            _node("C", 2, requirements={"database": 1}),
        ]
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                task = await _create_task(client, nodes, graph)
                started = await client.post(
                    f"/tasks/{task['id']}/runs",
                    json=TaskRunRequest(input="resource check").model_dump(mode="json"),
                )
                self.assertEqual(started.status_code, 202)
                run_id = started.json()["id"]
                await asyncio.wait_for(executor.node_entered.wait(), timeout=5)
                while executor.call_counts["A"] == 0:
                    await asyncio.sleep(0)
                current = await _get_run(client, run_id)
                current_status = {item["node_id"]: item["status"] for item in current["node_runs"]}
                self.assertEqual(current_status["B"], "running")
                self.assertEqual(current_status["C"], "pending")
                self.assertNotIn("C", executor.calls)

                executor.release_node.set()
                await _wait_for_worker(service, run_id)
                completed = await _get_run(client, run_id)
                self.assertEqual(completed["status"], "succeeded")
                self.assertEqual(executor.resource_maximum["database"], 1)
                self.assertEqual(executor.call_counts["B"], 1)
                self.assertEqual(executor.call_counts["C"], 1)
        finally:
            await service.shutdown()

    async def test_api_retry_failed_node_preserves_successful_prefix_and_is_idempotent(self):
        store = TaskStore()
        executor = _DeterministicExecutor(
            fail_first={"C": 1},
            sync_branches={"B", "C"},
        )
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        app = _app(service)
        graph = _graph(
            [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D"), ("D", "E")]
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                task = await _create_task(
                    client,
                    [_node(node_id, index) for index, node_id in enumerate("ABCDE")],
                    graph,
                )
                start = await client.post(
                    f"/tasks/{task['id']}/runs",
                    json=TaskRunRequest(input="retry C").model_dump(mode="json"),
                )
                self.assertEqual(start.status_code, 202)
                run_id = start.json()["id"]
                await asyncio.wait_for(executor.branches_started.wait(), timeout=5)
                executor.release_branches.set()
                await _wait_for_worker(service, run_id)
                failed = await _get_run(client, run_id)
                self.assertEqual(failed["status"], "failed")
                first_nodes = {item["node_id"]: item for item in failed["node_runs"]}
                self.assertEqual(first_nodes["A"]["status"], "succeeded")
                self.assertEqual(first_nodes["B"]["status"], "succeeded")
                self.assertEqual(first_nodes["C"]["status"], "failed")
                self.assertEqual(first_nodes["D"]["status"], "pending")
                self.assertEqual(first_nodes["E"]["status"], "pending")
                self.assertNotIn("D", executor.calls)
                self.assertNotIn("E", executor.calls)

                retry_body = {
                    "request_id": "retry-C-once",
                    "expected_run_version": failed["run_version"],
                }
                retry = await client.post(
                    f"/tasks/runs/{run_id}/nodes/C/retry",
                    json=retry_body,
                )
                self.assertEqual(retry.status_code, 202, retry.text)
                retried = retry.json()
                self.assertEqual(retried["id"], run_id)
                self.assertEqual(retried["control_mode"], "paused")
                reset = {item["node_id"]: item for item in retried["node_runs"]}
                self.assertEqual(reset["A"]["output"], "output:A")
                self.assertEqual(reset["B"]["output"], "output:B")
                self.assertEqual(reset["A"]["attempt"], 1)
                self.assertEqual(reset["B"]["attempt"], 1)
                self.assertEqual(reset["C"]["status"], "pending")
                self.assertEqual(reset["C"]["attempt_history"][0]["status"], "failed")
                self.assertIn("failed on its first attempt", reset["C"]["attempt_history"][0]["error"])
                self.assertEqual(reset["D"]["status"], "pending")
                self.assertEqual(reset["E"]["status"], "pending")

                duplicate = await client.post(
                    f"/tasks/runs/{run_id}/nodes/C/retry",
                    json=retry_body,
                )
                self.assertEqual(duplicate.status_code, 202)
                self.assertEqual(duplicate.json()["run_version"], retried["run_version"])
                stale = await client.post(
                    f"/tasks/runs/{run_id}/nodes/C/retry",
                    json={
                        "request_id": "retry-C-stale",
                        "expected_run_version": failed["run_version"],
                    },
                )
                self.assertEqual(stale.status_code, 409)
                self.assertEqual(stale.json()["detail"]["code"], "TASK_RUN_CAS_CONFLICT")

                continued = await client.post(
                    f"/tasks/runs/{run_id}/continue",
                    json={
                        "request_id": "continue-after-C-retry",
                        "expected_run_version": retried["run_version"],
                    },
                )
                self.assertEqual(continued.status_code, 202)
                await _wait_for_worker(service, run_id)
                completed = await _get_run(client, run_id)
                self.assertEqual(completed["id"], run_id)
                self.assertEqual(completed["status"], "succeeded")
                self.assertEqual(executor.call_counts["A"], 1)
                self.assertEqual(executor.call_counts["B"], 1)
                self.assertEqual(executor.call_counts["C"], 2)
                self.assertEqual(executor.call_counts["D"], 1)
                self.assertEqual(executor.call_counts["E"], 1)
                final_nodes = {item["node_id"]: item for item in completed["node_runs"]}
                self.assertEqual(final_nodes["C"]["attempt"], 2)
                self.assertEqual(
                    [item["source"] for item in final_nodes["C"]["attempt_history"]],
                    ["automatic", "manual"],
                )
                invalid_state = await client.post(
                    f"/tasks/runs/{run_id}/nodes/C/retry",
                    json={
                        "request_id": "retry-after-success",
                        "expected_run_version": completed["run_version"],
                    },
                )
                self.assertEqual(invalid_state.status_code, 409)
                self.assertEqual(
                    invalid_state.json()["detail"]["code"],
                    "TASK_RUN_STATE_CONFLICT",
                )
        finally:
            await service.shutdown()

    async def test_api_can_retry_successful_node_and_invalidates_all_descendants(self):
        store = TaskStore()
        executor = _DeterministicExecutor(fail_first={"D": 1})
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        app = _app(service)
        graph = _graph(
            [("A", "B"), ("B", "C"), ("C", "D"), ("A", "X"), ("X", "D")]
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                task = await _create_task(
                    client,
                    [_node(node_id, index) for index, node_id in enumerate("ABCDX")],
                    graph,
                )
                start = await client.post(
                    f"/tasks/{task['id']}/runs",
                    json=TaskRunRequest(input="recompute descendants").model_dump(mode="json"),
                )
                self.assertEqual(start.status_code, 202)
                run_id = start.json()["id"]
                await _wait_for_worker(service, run_id)
                failed = await _get_run(client, run_id)
                self.assertEqual(failed["status"], "failed")
                before = {item["node_id"]: item for item in failed["node_runs"]}
                for node_id in ("A", "B", "C", "X"):
                    self.assertEqual(before[node_id]["status"], "succeeded")
                self.assertEqual(before["D"]["status"], "failed")

                response = await client.post(
                    f"/tasks/runs/{run_id}/nodes/B/retry",
                    json={
                        "request_id": "rerun-successful-B",
                        "expected_run_version": failed["run_version"],
                    },
                )
                self.assertEqual(response.status_code, 202, response.text)
                reset = {item["node_id"]: item for item in response.json()["node_runs"]}
                self.assertEqual(response.json()["id"], run_id)
                self.assertEqual(reset["A"]["status"], "succeeded")
                self.assertEqual(reset["X"]["status"], "succeeded")
                for node_id in ("B", "C", "D"):
                    self.assertEqual(reset[node_id]["status"], "pending")
                    self.assertEqual(reset[node_id]["output"], "")
                self.assertEqual(reset["B"]["attempt_history"][-1]["status"], "succeeded")
                self.assertEqual(reset["D"]["attempt_history"][-1]["status"], "failed")

                continued = await client.post(
                    f"/tasks/runs/{run_id}/continue",
                    json={
                        "request_id": "continue-after-successful-B",
                        "expected_run_version": response.json()["run_version"],
                    },
                )
                self.assertEqual(continued.status_code, 202)
                await _wait_for_worker(service, run_id)
                completed = await _get_run(client, run_id)
                self.assertEqual(completed["status"], "succeeded")
                self.assertEqual(executor.call_counts["A"], 1)
                self.assertEqual(executor.call_counts["X"], 1)
                self.assertEqual(executor.call_counts["B"], 2)
                self.assertEqual(executor.call_counts["C"], 2)
                self.assertEqual(executor.call_counts["D"], 2)
                events = await client.get(f"/tasks/runs/{run_id}/events")
                self.assertEqual(events.status_code, 200)
                retry_event = next(
                    event
                    for event in _parse_sse_records(events.text)
                    if event["type"] == "node.retry_requested"
                )
                self.assertEqual(retry_event["node_id"], "B")
                self.assertEqual(set(retry_event["payload"]["invalidated_node_ids"]), {"C", "D"})
        finally:
            await service.shutdown()

    async def test_api_side_effect_gate_and_in_flight_descendant_guard(self):
        store = TaskStore()
        executor = _DeterministicExecutor()
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        app = _app(service)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                submit = TaskNode(
                    id="submit",
                    name="submit",
                    type=StepType.TOOL,
                    config=ToolStepConfig(tool_name="fixture.submit", arguments={}),
                )
                tool_task = await _create_task(client, [submit], _graph([], max_concurrency=1))
                tool_run = TaskRunRecord(
                    id="side-effect-run",
                    task_id=tool_task["id"],
                    user_id="p2g-owner",
                    task_revision=tool_task["revision"],
                    nodes=[submit],
                    node_runs=[
                        NodeRunRecord(
                            node_id="submit",
                            name="submit",
                            prompt="",
                            type=StepType.TOOL,
                            order=0,
                            status=NodeRunStatus.FAILED,
                            attempt=1,
                            output="unknown result",
                            error="worker stopped after submitting",
                        )
                    ],
                    engine="dag",
                    graph=_graph([], max_concurrency=1),
                    status=RunStatus.FAILED,
                )
                await store.save_run(tool_run)
                unsafe = await client.post(
                    f"/tasks/runs/{tool_run.id}/nodes/submit/retry",
                    json={
                        "request_id": "unsafe-side-effect-retry",
                        "expected_run_version": tool_run.run_version,
                    },
                )
                self.assertEqual(unsafe.status_code, 409)
                self.assertEqual(unsafe.json()["detail"]["code"], "TASK_RUN_STATE_CONFLICT")
                self.assertIn("side_effects_verified=true", unsafe.json()["detail"]["message"])
                safe = await client.post(
                    f"/tasks/runs/{tool_run.id}/nodes/submit/retry",
                    json={
                        "request_id": "verified-side-effect-retry",
                        "expected_run_version": tool_run.run_version,
                        "side_effects_verified": True,
                    },
                )
                self.assertEqual(safe.status_code, 202, safe.text)
                self.assertEqual(safe.json()["control_mode"], "paused")
                self.assertEqual(executor.calls, [])

                nodes = [_node("A", 0), _node("B", 1)]
                graph = _graph([("A", "B")], max_concurrency=2)
                task = await _create_task(client, nodes, graph)
                running = TaskRunRecord(
                    id="in-flight-descendant-run",
                    task_id=task["id"],
                    user_id="p2g-owner",
                    task_revision=task["revision"],
                    nodes=nodes,
                    node_runs=[
                        NodeRunRecord(
                            node_id="A",
                            name="A",
                            prompt="Execute A",
                            order=0,
                            status=NodeRunStatus.SUCCEEDED,
                            attempt=1,
                            output="A result",
                        ),
                        NodeRunRecord(
                            node_id="B",
                            name="B",
                            prompt="Execute B",
                            order=1,
                            status=NodeRunStatus.RUNNING,
                            attempt=1,
                        ),
                    ],
                    engine="dag",
                    graph=graph,
                    status=RunStatus.RUNNING,
                )
                await store.save_run(running)
                blocked = await client.post(
                    f"/tasks/runs/{running.id}/nodes/A/retry",
                    json={
                        "request_id": "retry-with-running-descendant",
                        "expected_run_version": running.run_version,
                    },
                )
                self.assertEqual(blocked.status_code, 409)
                self.assertEqual(blocked.json()["detail"]["code"], "TASK_RUN_STATE_CONFLICT")
                self.assertIn("node is still active", blocked.json()["detail"]["message"])
                unchanged = await _get_run(client, running.id)
                self.assertEqual(unchanged["run_version"], running.run_version)
                self.assertEqual(
                    next(item["status"] for item in unchanged["node_runs"] if item["node_id"] == "B"),
                    "running",
                )
        finally:
            await service.shutdown()

    async def test_api_pause_parallel_checkpoint_continue_after_service_rebuild(self):
        store = TaskStore()
        executor = _DeterministicExecutor(sync_branches={"B", "C"})
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        app = _app(service)
        graph = _graph(
            [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                task = await _create_task(
                    client,
                    [_node(node_id, index) for index, node_id in enumerate("ABCD")],
                    graph,
                )
                start = await client.post(
                    f"/tasks/{task['id']}/runs",
                    json=TaskRunRequest(input="pause parallel DAG").model_dump(mode="json"),
                )
                self.assertEqual(start.status_code, 202)
                run_id = start.json()["id"]
                await asyncio.wait_for(executor.branches_started.wait(), timeout=5)
                active = await _get_run(client, run_id)
                self.assertEqual(active["status"], "running")
                command = {
                    "request_id": "pause-parallel",
                    "expected_run_version": active["run_version"],
                }
                paused_response = await client.post(
                    f"/tasks/runs/{run_id}/pause",
                    json=command,
                )
                self.assertEqual(paused_response.status_code, 202)
                self.assertEqual(paused_response.json()["control_mode"], "pause_requested")
                duplicate = await client.post(
                    f"/tasks/runs/{run_id}/pause",
                    json=command,
                )
                self.assertEqual(duplicate.status_code, 202)
                self.assertEqual(duplicate.json()["run_version"], paused_response.json()["run_version"])
                conflict = await client.post(
                    f"/tasks/runs/{run_id}/pause",
                    json={
                        "request_id": "stale-pause-parallel",
                        "expected_run_version": active["run_version"],
                    },
                )
                self.assertEqual(conflict.status_code, 409)
                self.assertEqual(conflict.json()["detail"]["code"], "TASK_RUN_CAS_CONFLICT")

                executor.release_branches.set()
                await _wait_for_worker(service, run_id)
                parked = await _get_run(client, run_id)
                self.assertEqual(parked["status"], "queued")
                self.assertEqual(parked["control_mode"], "paused")
                parked_nodes = {item["node_id"]: item for item in parked["node_runs"]}
                self.assertEqual(parked_nodes["B"]["status"], "succeeded")
                self.assertEqual(parked_nodes["C"]["status"], "succeeded")
                self.assertEqual(parked_nodes["D"]["status"], "pending")

                await service.shutdown()
                rebuilt = TaskService(
                    store,
                    preview_executor=executor,
                    enable_dag_scheduler=True,
                )
                app.state.task_service = rebuilt
                resumed = await client.post(
                    f"/tasks/runs/{run_id}/continue",
                    json={
                        "request_id": "continue-after-rebuild",
                        "expected_run_version": parked["run_version"],
                    },
                )
                self.assertEqual(resumed.status_code, 202, resumed.text)
                self.assertEqual(resumed.json()["id"], run_id)
                await _wait_for_worker(rebuilt, run_id)
                completed = await _get_run(client, run_id)
                self.assertEqual(completed["status"], "succeeded")
                self.assertEqual(executor.call_counts["A"], 1)
                self.assertEqual(executor.call_counts["B"], 1)
                self.assertEqual(executor.call_counts["C"], 1)
                self.assertEqual(executor.call_counts["D"], 1)

                duplicate_continue = await client.post(
                    f"/tasks/runs/{run_id}/continue",
                    json={
                        "request_id": "continue-after-rebuild",
                        "expected_run_version": parked["run_version"],
                    },
                )
                self.assertEqual(duplicate_continue.status_code, 202)
                self.assertEqual(
                    duplicate_continue.json()["run_version"],
                    completed["run_version"],
                )
        finally:
            await service.shutdown()
            active_service = app.state.task_service
            if active_service is not service:
                await active_service.shutdown()

    async def test_api_pause_linear_run_resumes_without_replaying_checkpoint(self):
        store = TaskStore()
        executor = _DeterministicExecutor(block_node="A")
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        app = _app(service)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                task = await _create_task(
                    client,
                    [_node("A", 0), _node("B", 1)],
                    _graph([("A", "B")], max_concurrency=1),
                )
                start = await client.post(
                    f"/tasks/{task['id']}/runs",
                    json=TaskRunRequest(input="pause linear DAG").model_dump(mode="json"),
                )
                self.assertEqual(start.status_code, 202)
                run_id = start.json()["id"]
                await asyncio.wait_for(executor.node_entered.wait(), timeout=5)
                active = await _get_run(client, run_id)
                pause = await client.post(
                    f"/tasks/runs/{run_id}/pause",
                    json={
                        "request_id": "pause-linear",
                        "expected_run_version": active["run_version"],
                    },
                )
                self.assertEqual(pause.status_code, 202)
                self.assertEqual(pause.json()["control_mode"], "pause_requested")
                executor.release_node.set()
                await _wait_for_worker(service, run_id)
                parked = await _get_run(client, run_id)
                self.assertEqual(parked["status"], "queued")
                self.assertEqual(parked["control_mode"], "paused")
                parked_nodes = {item["node_id"]: item for item in parked["node_runs"]}
                self.assertEqual(parked_nodes["A"]["status"], "succeeded")
                self.assertEqual(parked_nodes["B"]["status"], "pending")
                self.assertEqual(executor.calls, ["A"])

                await service.shutdown()
                rebuilt = TaskService(
                    store,
                    preview_executor=executor,
                    enable_dag_scheduler=True,
                )
                app.state.task_service = rebuilt
                resumed = await client.post(
                    f"/tasks/runs/{run_id}/continue",
                    json={
                        "request_id": "continue-linear",
                        "expected_run_version": parked["run_version"],
                    },
                )
                self.assertEqual(resumed.status_code, 202, resumed.text)
                self.assertEqual(resumed.json()["id"], run_id)
                await _wait_for_worker(rebuilt, run_id)
                completed = await _get_run(client, run_id)
                self.assertEqual(completed["status"], "succeeded")
                self.assertEqual(executor.call_counts["A"], 1)
                self.assertEqual(executor.call_counts["B"], 1)
        finally:
            await service.shutdown()
            active_service = app.state.task_service
            if active_service is not service:
                await active_service.shutdown()

    async def test_api_step_dispatches_one_ready_node_per_request(self):
        store = TaskStore()
        executor = _DeterministicExecutor(block_node="A")
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        app = _app(service)
        graph = _graph(
            [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")]
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                task = await _create_task(
                    client,
                    [_node(node_id, index) for index, node_id in enumerate("ABCD")],
                    graph,
                )
                started = await client.post(
                    f"/tasks/{task['id']}/runs",
                    json=TaskRunRequest(input="single-step").model_dump(mode="json"),
                )
                self.assertEqual(started.status_code, 202)
                run_id = started.json()["id"]
                await asyncio.wait_for(executor.node_entered.wait(), timeout=5)
                current = await _get_run(client, run_id)
                pause = await client.post(
                    f"/tasks/runs/{run_id}/pause",
                    json={
                        "request_id": "pause-before-step",
                        "expected_run_version": current["run_version"],
                    },
                )
                self.assertEqual(pause.status_code, 202)
                executor.release_node.set()
                await _wait_for_worker(service, run_id)
                parked = await _get_run(client, run_id)
                self.assertEqual(parked["control_mode"], "paused")
                self.assertEqual(parked["node_runs"][0]["status"], "succeeded")

                first_command = {
                    "request_id": "step-B",
                    "expected_run_version": parked["run_version"],
                }
                first = await client.post(
                    f"/tasks/runs/{run_id}/step",
                    json=first_command,
                )
                self.assertEqual(first.status_code, 202)
                await _wait_for_worker(service, run_id)
                after_first = await _get_run(client, run_id)
                self.assertEqual(after_first["control_mode"], "paused")
                nodes_after_first = {item["node_id"]: item for item in after_first["node_runs"]}
                self.assertEqual(nodes_after_first["B"]["status"], "succeeded")
                self.assertEqual(nodes_after_first["C"]["status"], "pending")
                self.assertEqual(nodes_after_first["D"]["status"], "pending")
                self.assertEqual(executor.calls, ["A", "B"])

                duplicate = await client.post(
                    f"/tasks/runs/{run_id}/step",
                    json=first_command,
                )
                self.assertEqual(duplicate.status_code, 202)
                self.assertEqual(duplicate.json()["run_version"], after_first["run_version"])
                self.assertEqual(executor.calls, ["A", "B"])

                second = await client.post(
                    f"/tasks/runs/{run_id}/step",
                    json={
                        "request_id": "step-C",
                        "expected_run_version": after_first["run_version"],
                    },
                )
                self.assertEqual(second.status_code, 202)
                await _wait_for_worker(service, run_id)
                after_second = await _get_run(client, run_id)
                self.assertEqual(after_second["control_mode"], "paused")
                self.assertEqual(executor.calls, ["A", "B", "C"])

                third = await client.post(
                    f"/tasks/runs/{run_id}/step",
                    json={
                        "request_id": "step-D",
                        "expected_run_version": after_second["run_version"],
                    },
                )
                self.assertEqual(third.status_code, 202)
                await _wait_for_worker(service, run_id)
                completed = await _get_run(client, run_id)
                self.assertEqual(completed["status"], "succeeded")
                self.assertEqual(executor.calls, ["A", "B", "C", "D"])
                events = await client.get(f"/tasks/runs/{run_id}/events")
                completed_steps = [
                    event["payload"]["node_ids"]
                    for event in _parse_sse_records(events.text)
                    if event["type"] == "run.step_completed"
                ]
                self.assertEqual(completed_steps, [["B"], ["C"]])
        finally:
            await service.shutdown()

    async def test_api_pause_during_node_error_requires_retry_before_continue(self):
        store = TaskStore()
        executor = _DeterministicExecutor(fail_first={"A": 1}, block_node="A")
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        app = _app(service)
        graph = _graph([("A", "B")], max_concurrency=2)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                task = await _create_task(client, [_node("A", 0), _node("B", 1)], graph)
                start = await client.post(
                    f"/tasks/{task['id']}/runs",
                    json=TaskRunRequest(input="pause with error").model_dump(mode="json"),
                )
                self.assertEqual(start.status_code, 202)
                run_id = start.json()["id"]
                await asyncio.wait_for(executor.node_entered.wait(), timeout=5)
                active = await _get_run(client, run_id)
                pause = await client.post(
                    f"/tasks/runs/{run_id}/pause",
                    json={
                        "request_id": "pause-before-node-error",
                        "expected_run_version": active["run_version"],
                    },
                )
                self.assertEqual(pause.status_code, 202)
                executor.release_node.set()
                await _wait_for_worker(service, run_id)
                failed = await _get_run(client, run_id)
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["node_runs"][0]["status"], "failed")
                self.assertEqual(failed["node_runs"][1]["status"], "pending")
                self.assertNotIn("B", executor.calls)

                invalid_continue = await client.post(
                    f"/tasks/runs/{run_id}/continue",
                    json={
                        "request_id": "continue-before-retry",
                        "expected_run_version": failed["run_version"],
                    },
                )
                self.assertEqual(invalid_continue.status_code, 409)
                self.assertEqual(
                    invalid_continue.json()["detail"]["code"],
                    "TASK_RUN_STATE_CONFLICT",
                )
                retried = await client.post(
                    f"/tasks/runs/{run_id}/nodes/A/retry",
                    json={
                        "request_id": "retry-A-after-pause-error",
                        "expected_run_version": failed["run_version"],
                    },
                )
                self.assertEqual(retried.status_code, 202)
                continued = await client.post(
                    f"/tasks/runs/{run_id}/continue",
                    json={
                        "request_id": "continue-after-A-retry",
                        "expected_run_version": retried.json()["run_version"],
                    },
                )
                self.assertEqual(continued.status_code, 202)
                await _wait_for_worker(service, run_id)
                completed = await _get_run(client, run_id)
                self.assertEqual(completed["status"], "succeeded")
                self.assertEqual(executor.call_counts["A"], 2)
                self.assertEqual(executor.call_counts["B"], 1)
        finally:
            await service.shutdown()

    async def test_api_stop_is_terminal_idempotent_and_fences_late_node_result(self):
        store = TaskStore()
        executor = _DeterministicExecutor(block_node="A")
        service = TaskService(
            store,
            preview_executor=executor,
            enable_dag_scheduler=True,
        )
        app = _app(service)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                task = await _create_task(
                    client,
                    [_node("A", 0), _node("B", 1)],
                    _graph([("A", "B")], max_concurrency=2),
                )
                start = await client.post(
                    f"/tasks/{task['id']}/runs",
                    json=TaskRunRequest(input="stop in flight").model_dump(mode="json"),
                )
                self.assertEqual(start.status_code, 202)
                run_id = start.json()["id"]
                await asyncio.wait_for(executor.node_entered.wait(), timeout=5)
                active = await _get_run(client, run_id)
                command = {
                    "request_id": "stop-in-flight",
                    "expected_run_version": active["run_version"],
                }
                stopped = await client.post(
                    f"/tasks/runs/{run_id}/stop",
                    json=command,
                )
                self.assertEqual(stopped.status_code, 202)
                self.assertEqual(stopped.json()["status"], "canceled")
                self.assertEqual(stopped.json()["control_mode"], "stopped")
                version_after_stop = stopped.json()["run_version"]
                duplicate = await client.post(
                    f"/tasks/runs/{run_id}/stop",
                    json=command,
                )
                self.assertEqual(duplicate.status_code, 202)
                self.assertEqual(duplicate.json()["run_version"], version_after_stop)
                resume = await client.post(
                    f"/tasks/runs/{run_id}/continue",
                    json={
                        "request_id": "continue-after-stop",
                        "expected_run_version": version_after_stop,
                    },
                )
                self.assertEqual(resume.status_code, 409)
                self.assertEqual(resume.json()["detail"]["code"], "TASK_RUN_STATE_CONFLICT")

                executor.release_node.set()
                await _wait_for_worker(service, run_id)
                final = await _get_run(client, run_id)
                self.assertEqual(final["status"], "canceled")
                self.assertEqual(final["control_mode"], "stopped")
                self.assertEqual(executor.call_counts["A"], 1)
                self.assertNotIn("B", executor.calls)
                events = await client.get(f"/tasks/runs/{run_id}/events")
                canceled = [
                    event
                    for event in _parse_sse_records(events.text)
                    if event["type"] == "run.canceled"
                ]
                self.assertEqual(len(canceled), 1)
                self.assertFalse(canceled[0]["payload"]["side_effects_rolled_back"])
        finally:
            await service.shutdown()
