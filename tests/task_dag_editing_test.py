# -*- coding: utf-8 -*-
"""Task API contract tests for custom DAG editing permissions and state."""

from unittest import IsolatedAsyncioTestCase

import httpx
from fastapi import FastAPI

from agentscope.app.deps import get_current_user_id
from examples.agent_service.task._models import (
    AgentStepConfig,
    CreateTaskRequest,
    NodeRunRecord,
    NodeRunStatus,
    RunStatus,
    TaskGraph,
    TaskGraphEdge,
    TaskGenerationStatus,
    TaskNode,
    TaskRecord,
    TaskRunRecord,
    TaskRunControlRequest,
    TaskStatus,
    TaskWorkflowMode,
)
from examples.agent_service.task._router import task_router
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._service import TaskWorkflowConflictError
from examples.agent_service.task._store import TaskStore


def _nodes() -> list[TaskNode]:
    return [
        TaskNode(
            id=node_id,
            name=node_id,
            prompt=f"Run {node_id}",
            type="agent",
            config=AgentStepConfig(prompt=f"Run {node_id}"),
            order=index,
        )
        for index, node_id in enumerate(("A", "B", "C"))
    ]


def _graph(edges: list[tuple[str, str]] | None = None) -> TaskGraph:
    return TaskGraph(
        edges=[
            TaskGraphEdge(source_node_id=source, target_node_id=target)
            for source, target in (edges or [("A", "B")])
        ],
        max_concurrency=3,
    )


def _app(service: TaskService) -> FastAPI:
    app = FastAPI()
    app.state.task_service = service
    app.dependency_overrides[get_current_user_id] = lambda: "owner"
    app.include_router(task_router)
    return app


class TaskDagEditingApiTests(IsolatedAsyncioTestCase):
    async def test_capabilities_are_separate_and_custom_graph_is_saved_through_api(self) -> None:
        service = TaskService(
            TaskStore(),
            enable_dag_editing=True,
            enable_dag_scheduler=False,
        )
        app = _app(service)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                capabilities = await client.get("/tasks/capabilities")
                self.assertEqual(
                    capabilities.json(),
                    {
                        "editing_enabled": True,
                        "execution_enabled": False,
                        "sop_control_enabled": True,
                        "sop_node_retry_enabled": True,
                    },
                )
                created = await client.post(
                    "/tasks/",
                    json={
                        "title": "Custom DAG",
                        "goal": "Build and save a custom DAG.",
                        "nodes": [node.model_dump(mode="json") for node in _nodes()],
                        "graph": _graph().model_dump(mode="json"),
                    },
                )
                self.assertEqual(created.status_code, 201, created.text)
                task = created.json()
                self.assertEqual(task["graph"]["edges"], [{"source_node_id": "A", "target_node_id": "B"}])
                self.assertFalse(capabilities.json()["execution_enabled"])
                self.assertTrue(capabilities.json()["sop_control_enabled"])
                self.assertTrue(capabilities.json()["sop_node_retry_enabled"])

                invalid = await client.patch(
                    f"/tasks/{task['id']}",
                    json={"graph": _graph([("A", "B"), ("B", "A")]).model_dump(mode="json")},
                )
                self.assertEqual(invalid.status_code, 422, invalid.text)
                current = await client.get(f"/tasks/{task['id']}")
                self.assertEqual(current.json()["revision"], task["revision"])
                self.assertEqual(current.json()["graph"], task["graph"])
        finally:
            await service.shutdown()

    async def test_editing_flag_template_provenance_and_run_state_are_enforced(self) -> None:
        store = TaskStore()
        service = TaskService(store, enable_dag_editing=False, enable_dag_scheduler=True)
        app = _app(service)
        free_task = await service.create_task(
            "owner",
            CreateTaskRequest(title="Linear", goal="Task", nodes=_nodes()),
        )
        template_task = TaskRecord(
            id="template-task",
            user_id="owner",
            title="Scenario",
            goal="Scenario Task",
            nodes=_nodes(),
            status=TaskStatus.ACTIVE,
            workflow_mode=TaskWorkflowMode.TEMPLATE_INSTANCE,
            source_template_id="s01_project_init",
            source_template_version="2.0.0",
        )
        await store.save_task(template_task)
        active_run = TaskRunRecord(
            id="active-dag-run",
            task_id=free_task.id,
            user_id="owner",
            task_revision=free_task.revision,
            nodes=_nodes(),
            node_runs=[],
            status=RunStatus.RUNNING,
            engine="dag",
            graph=_graph(),
        )
        await store.save_run(active_run)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                capabilities = await client.get("/tasks/capabilities")
                self.assertEqual(
                    capabilities.json(),
                    {
                        "editing_enabled": False,
                        "execution_enabled": True,
                        "sop_control_enabled": True,
                        "sop_node_retry_enabled": True,
                    },
                )
                denied_create = await client.post(
                    "/tasks/",
                    json={
                        "goal": "Cannot author when disabled",
                        "nodes": [node.model_dump(mode="json") for node in _nodes()],
                        "graph": _graph().model_dump(mode="json"),
                    },
                )
                self.assertEqual(denied_create.status_code, 409)
                denied_update = await client.patch(
                    f"/tasks/{free_task.id}",
                    json={"graph": _graph().model_dump(mode="json")},
                )
                self.assertEqual(denied_update.status_code, 409)
                template_update = await client.patch(
                    "/tasks/template-task",
                    json={"graph": _graph().model_dump(mode="json")},
                )
                self.assertEqual(template_update.status_code, 409)
        finally:
            await service.shutdown()

    async def test_active_run_and_wrong_owner_cannot_edit_graph(self) -> None:
        store = TaskStore()
        service = TaskService(store, enable_dag_editing=True)
        task = await service.create_task(
            "owner",
            CreateTaskRequest(title="Active", goal="Task", nodes=_nodes()),
        )
        await store.save_run(
            TaskRunRecord(
                id="busy-run",
                task_id=task.id,
                user_id="owner",
                task_revision=task.revision,
                nodes=_nodes(),
                node_runs=[],
                status=RunStatus.RUNNING,
                engine="dag",
                graph=_graph(),
            ),
        )
        await store.save_task(
            TaskRecord(
                id="generating-task",
                user_id="owner",
                title="Generating",
                goal="Task",
                nodes=[],
                status=TaskStatus.DRAFT,
                generation_status=TaskGenerationStatus.GENERATING,
                workflow_mode=TaskWorkflowMode.FREE_AI_PLANNER,
            ),
        )
        owner_app = _app(service)
        other_app = FastAPI()
        other_app.state.task_service = service
        other_app.dependency_overrides[get_current_user_id] = lambda: "other-user"
        other_app.include_router(task_router)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=owner_app),
                base_url="http://test",
            ) as client:
                active = await client.patch(
                    f"/tasks/{task.id}",
                    json={"graph": _graph().model_dump(mode="json")},
                )
                self.assertEqual(active.status_code, 409)
                generating = await client.patch(
                    "/tasks/generating-task",
                    json={"graph": _graph().model_dump(mode="json")},
                )
                self.assertEqual(generating.status_code, 409)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=other_app),
                base_url="http://test",
            ) as client:
                denied = await client.patch(
                    f"/tasks/{task.id}",
                    json={"graph": _graph().model_dump(mode="json")},
                )
                self.assertEqual(denied.status_code, 404)
        finally:
            await service.shutdown()

    async def test_execution_flag_blocks_continue_step_and_node_retry(self) -> None:
        store = TaskStore()
        service = TaskService(store, enable_dag_scheduler=False)
        graph = _graph([("A", "B")])
        nodes = _nodes()
        paused = TaskRunRecord(
            id="paused-run",
            task_id="task-paused",
            user_id="owner",
            task_revision=1,
            nodes=nodes,
            node_runs=[
                NodeRunRecord(node_id=node.id, name=node.name, prompt=node.prompt, type=node.type, order=node.order)
                for node in nodes
            ],
            status=RunStatus.RUNNING,
            control_mode="paused",
            engine="dag",
            graph=graph,
        )
        failed = TaskRunRecord(
            id="failed-run",
            task_id="task-failed",
            user_id="owner",
            task_revision=1,
            nodes=nodes,
            node_runs=[
                NodeRunRecord(
                    node_id=node.id,
                    name=node.name,
                    prompt=node.prompt,
                    type=node.type,
                    order=node.order,
                    status=NodeRunStatus.FAILED if node.id == "A" else NodeRunStatus.PENDING,
                )
                for node in nodes
            ],
            status=RunStatus.FAILED,
            engine="dag",
            graph=graph,
        )
        await store.save_run(paused)
        await store.save_run(failed)
        command = TaskRunControlRequest(request_id="blocked-command", expected_run_version=0)
        try:
            with self.assertRaises(TaskWorkflowConflictError):
                await service.continue_run("owner", paused.id, command)
            with self.assertRaises(TaskWorkflowConflictError):
                await service.step_run("owner", paused.id, command)
            with self.assertRaises(TaskWorkflowConflictError):
                await service.retry_node("owner", failed.id, "A", command)
        finally:
            await service.shutdown()
