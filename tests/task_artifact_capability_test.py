"""Task-owned Artifact Writer capability preflight coverage."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from examples.agent_service.task._agentscope_executor import AgentScopeTaskExecutor
from examples.agent_service.task._models import (
    ArtifactConfig,
    ArtifactFormat,
    ExecutionContext,
    TaskContext,
    TaskPreflightRequest,
    TaskRunRequest,
    TaskToolSchema,
    UpdateTaskRequest,
)
from examples.agent_service.task._service import TaskService
from examples.agent_service.task._template_validation import TaskValidationFailure
from examples.agent_service.task._store import TaskStore
from examples.agent_service.task._template_compiler import TaskTemplateCompiler
from examples.agent_service.task._template_models import TemplateInstantiateRequest
from examples.agent_service.task._template_registry import TemplateRegistry
from examples.agent_service.task._template_service import TaskTemplateService
from examples.agent_service.task._template_validation import TaskTemplateValidator


class _PlanningTaskExecutor:
    """Expose no business Tools and never execute nodes in preflight tests."""

    async def list_tools(self, context: Any) -> list[TaskToolSchema]:
        del context
        return []

    async def execute_node(self, **_: Any) -> None:
        raise AssertionError("Preflight must not execute Task nodes.")


class _ArtifactWriter:
    """Test-only writer that reports explicit Workspace readiness."""

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.preflight_contexts: list[ExecutionContext] = []
        self.write_calls = 0

    async def preflight_artifact_writer(
        self,
        *,
        context: ExecutionContext,
    ) -> tuple[bool, str]:
        self.preflight_contexts.append(context)
        message = (
            "TEST FIXTURE ONLY — Workspace writer ready."
            if self.ready
            else "TEST FIXTURE ONLY — Workspace writer unavailable."
        )
        return self.ready, message

    async def write_artifact(
        self,
        *,
        path: str,
        data: bytes,
        context: ExecutionContext,
    ) -> int:
        del path, context
        self.write_calls += 1
        return len(data)

    async def read_artifact(
        self,
        *,
        path: str,
        context: ExecutionContext,
    ) -> bytes:
        del path, context
        return b""


def _planning_request() -> TemplateInstantiateRequest:
    return TemplateInstantiateRequest(
        template_version="1.0.0",
        inputs={
            "project_name": "Task Artifact 能力测试",
            "project_brief": "验证 Task 文件产物能力映射。",
            "professional_fields": ["工程"],
            "source_material_ids": [],
        },
        source_context=TaskContext(
            agent_id="artifact-test-agent",
            session_id="artifact-test-session",
        ),
    )


async def _custom_planning_task(
    *,
    executor: _PlanningTaskExecutor,
    writer: _ArtifactWriter | None,
    artifact: ArtifactConfig | None,
) -> tuple[TaskService, Any]:
    registry = TemplateRegistry()
    store = TaskStore()
    service = TaskService(
        store,
        agentscope_executor=executor,  # type: ignore[arg-type]
        artifact_writer=writer,  # type: ignore[arg-type]
    )
    service._template_validator = TaskTemplateValidator(
        registry=registry,
        compiler=TaskTemplateCompiler(),
    )
    templates = TaskTemplateService(registry, service)
    standard = await templates.instantiate(
        "artifact-owner",
        "s02_project_planning",
        _planning_request(),
    )
    task = await service.create_custom_flow_copy("artifact-owner", standard.id)
    assert task is not None
    if artifact is not None:
        nodes = [node.model_copy(deep=True) for node in task.nodes]
        nodes[-1] = nodes[-1].model_copy(update={"artifact": artifact})
        task = await service.update_task(
            "artifact-owner",
            task.id,
            UpdateTaskRequest(nodes=nodes),
        )
        assert task is not None
    return service, task


def test_document_render_requires_configured_final_artifact() -> None:
    async def scenario() -> None:
        service, task = await _custom_planning_task(
            executor=_PlanningTaskExecutor(),
            writer=_ArtifactWriter(),
            artifact=None,
        )

        result = await service.preflight_task(
            "artifact-owner",
            task.id,
            TaskPreflightRequest(input="项目策划要求"),
        )

        assert result is not None and result.ready is False
        render = next(
            check
            for check in result.capability_checks
            if check.capability_key == "document.render"
        )
        assert render.status == "missing"
        assert "最终输出节点" in render.message
        assert "document.render" in result.missing_capabilities

    asyncio.run(scenario())


def test_template_validation_maps_render_to_configured_final_artifact() -> None:
    async def scenario() -> None:
        registry = TemplateRegistry()
        service = TaskService(TaskStore())
        service._template_validator = TaskTemplateValidator(
            registry=registry,
            compiler=TaskTemplateCompiler(),
        )
        templates = TaskTemplateService(registry, service)
        task = await templates.instantiate(
            "artifact-owner",
            "s02_project_planning",
            _planning_request(),
        )
        nodes = [node.model_copy(deep=True) for node in task.nodes]
        nodes[-1] = nodes[-1].model_copy(
            update={
                "artifact": ArtifactConfig(
                    format=ArtifactFormat.MARKDOWN,
                    filename="策划结果.md",
                ),
            },
        )

        result = service._template_validator.validate(
            task,
            nodes,
            tool_schemas=[],
        )

        assert "document.render" not in result.missing_capabilities
        assert "planning.template_retrieve" in result.missing_capabilities
        assert "document.citation_verify" in result.missing_capabilities

    asyncio.run(scenario())


def test_document_render_uses_ready_task_artifact_writer_without_writing() -> None:
    async def scenario() -> None:
        writer = _ArtifactWriter()
        service, task = await _custom_planning_task(
            executor=_PlanningTaskExecutor(),
            writer=writer,
            artifact=ArtifactConfig(
                format=ArtifactFormat.MARKDOWN,
                filename="策划结果.md",
            ),
        )

        result = await service.preflight_task(
            "artifact-owner",
            task.id,
            TaskPreflightRequest(input="项目策划要求"),
        )

        assert result is not None and result.ready is False
        render = next(
            check
            for check in result.capability_checks
            if check.capability_key == "document.render"
        )
        assert render.status == "available"
        assert render.step_id == task.nodes[-1].id
        assert render.tool_available is None
        assert render.permission_status == "not_applicable"
        assert "planning.template_retrieve" in result.missing_capabilities
        assert "document.citation_verify" in result.missing_capabilities
        assert "document.render" not in result.missing_capabilities
        assert writer.preflight_contexts[0].user_id == "artifact-owner"
        assert writer.write_calls == 0
        assert await service.list_runs("artifact-owner", task.id) == []
        with pytest.raises(TaskValidationFailure) as error:
            await service.create_run(
                "artifact-owner",
                task.id,
                TaskRunRequest(input="项目策划要求"),
            )
        assert error.value.result.ready is False
        assert error.value.result.missing_capabilities == [
            "document.citation_verify",
            "planning.template_retrieve",
        ]
        assert writer.write_calls == 0
        assert await service.list_runs("artifact-owner", task.id) == []

    asyncio.run(scenario())


def test_document_render_fails_closed_without_workspace_preflight() -> None:
    async def scenario() -> None:
        service, task = await _custom_planning_task(
            executor=_PlanningTaskExecutor(),
            writer=None,
            artifact=ArtifactConfig(
                format=ArtifactFormat.MARKDOWN,
                filename="策划结果.md",
            ),
        )

        result = await service.preflight_task(
            "artifact-owner",
            task.id,
            TaskPreflightRequest(input="项目策划要求"),
        )

        assert result is not None and result.ready is False
        render = next(
            check
            for check in result.capability_checks
            if check.capability_key == "document.render"
        )
        assert render.status == "unverified"
        assert "Artifact Writer" in render.message
        assert "document.render" not in result.missing_capabilities

    asyncio.run(scenario())


def test_document_render_fails_closed_when_workspace_preflight_denies_access() -> None:
    async def scenario() -> None:
        writer = _ArtifactWriter(ready=False)
        service, task = await _custom_planning_task(
            executor=_PlanningTaskExecutor(),
            writer=writer,
            artifact=ArtifactConfig(
                format=ArtifactFormat.MARKDOWN,
                filename="策划结果.md",
            ),
        )

        result = await service.preflight_task(
            "artifact-owner",
            task.id,
            TaskPreflightRequest(input="项目策划要求"),
        )

        assert result is not None and result.ready is False
        render = next(
            check
            for check in result.capability_checks
            if check.capability_key == "document.render"
        )
        assert render.status == "unverified"
        assert "unavailable" in render.message
        assert writer.preflight_contexts
        assert writer.write_calls == 0

    asyncio.run(scenario())


def test_document_render_requires_available_serializer(monkeypatch) -> None:
    async def scenario() -> None:
        from examples.agent_service.task import _service as service_module

        monkeypatch.setattr(
            service_module,
            "artifact_renderer_available",
            lambda _: False,
        )
        writer = _ArtifactWriter()
        service, task = await _custom_planning_task(
            executor=_PlanningTaskExecutor(),
            writer=writer,
            artifact=ArtifactConfig(
                format=ArtifactFormat.DOCX,
                filename="策划结果.docx",
            ),
        )

        result = await service.preflight_task(
            "artifact-owner",
            task.id,
            TaskPreflightRequest(input="项目策划要求"),
        )

        assert result is not None and result.ready is False
        render = next(
            check
            for check in result.capability_checks
            if check.capability_key == "document.render"
        )
        assert render.status == "unverified"
        assert "DOCX" in render.message
        assert writer.preflight_contexts == []
        assert writer.write_calls == 0

    asyncio.run(scenario())


def test_agentscope_artifact_preflight_resolves_owner_workspace_without_io() -> None:
    class _Storage:
        async def get_session(self, user_id, agent_id, session_id):
            assert (user_id, agent_id, session_id) == (
                "artifact-owner",
                "artifact-agent",
                "artifact-session",
            )
            return SimpleNamespace(config=SimpleNamespace(workspace_id="workspace-1"))

    class _Backend:
        def __init__(self) -> None:
            self.write_calls = 0
            self.read_calls = 0

        def abspath(self, path, *, cwd):
            return f"{cwd}/{path}"

        async def write_file(self, path, data):
            del path, data
            self.write_calls += 1

        async def read_file(self, path):
            del path
            self.read_calls += 1
            return b""

        async def stat(self, path):
            del path
            return None

    class _Workspace:
        workdir = "/user/workspace"

        def __init__(self) -> None:
            self.backend = _Backend()

        def get_backend(self):
            return self.backend

    class _WorkspaceManager:
        def __init__(self) -> None:
            self.workspace = _Workspace()
            self.calls = []

        async def get_workspace(
            self,
            user_id,
            agent_id,
            session_id,
            workspace_id,
        ):
            self.calls.append((user_id, agent_id, session_id, workspace_id))
            return self.workspace

    async def scenario() -> None:
        manager = _WorkspaceManager()
        executor = AgentScopeTaskExecutor(
            storage=_Storage(),  # type: ignore[arg-type]
            resource_access_service=object(),  # type: ignore[arg-type]
            workspace_manager=manager,  # type: ignore[arg-type]
        )
        context = ExecutionContext(
            user_id="artifact-owner",
            task_id="artifact-task",
            run_id="task-preflight",
            task_revision=1,
            agent_id="artifact-agent",
            session_id="artifact-session",
            initial_input="项目策划要求",
        )

        ready, message = await executor.preflight_artifact_writer(
            context=context,
        )

        assert ready is True
        assert "Workspace" in message
        assert manager.calls == [
            (
                "artifact-owner",
                "artifact-agent",
                "artifact-session",
                "workspace-1",
            ),
        ]
        assert manager.workspace.backend.write_calls == 0
        assert manager.workspace.backend.read_calls == 0

    asyncio.run(scenario())
