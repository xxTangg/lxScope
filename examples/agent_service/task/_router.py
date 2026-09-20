# -*- coding: utf-8 -*-
"""HTTP routes for the standalone Task module."""

import asyncio
import json
from collections.abc import AsyncIterator
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from agentscope.app.deps import get_current_user_id

from ._models import (
    CreateTaskRequest,
    GenerateTaskRequest,
    TaskContext,
    TaskArtifactRecord,
    TaskKnowledgeBaseOption,
    TaskKnowledgeGraphRequest,
    TaskKnowledgeGraphRebuildResponse,
    TaskKnowledgeGraphResponse,
    TaskRecord,
    TaskRunRecord,
    TaskRunRequest,
    TaskStepKnowledgeGraphRequest,
    TaskToolSchema,
    UpdateTaskRequest,
)
from ._knowledge import TaskKnowledgeGateway
from ._service import TaskService


task_router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    responses={404: {"description": "Not found"}},
)


async def _get_task_service(request: Request) -> TaskService:
    """Resolve the application-owned Task service from app state."""

    task_service = getattr(request.app.state, "task_service", None)
    if task_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Task module is not configured.",
        )
    return task_service


def _get_task_knowledge_gateway(request: Request) -> TaskKnowledgeGateway:
    """Resolve the optional Task-to-AgentScope knowledge adapter."""

    knowledge_base_service = getattr(
        request.app.state,
        "knowledge_base_service",
        None,
    )
    if knowledge_base_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Knowledge base service is not configured.",
        )
    cached = getattr(request.app.state, "task_knowledge_gateway", None)
    if isinstance(cached, TaskKnowledgeGateway):
        return cached
    gateway = TaskKnowledgeGateway(
        knowledge_base_service,
        request.app.state,
    )
    request.app.state.task_knowledge_gateway = gateway
    return gateway


def _maybe_get_task_knowledge_gateway(request: Request) -> TaskKnowledgeGateway | None:
    """Keep Task execution backward-compatible when KB is disabled."""

    try:
        return _get_task_knowledge_gateway(request)
    except HTTPException as error:
        if error.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
            return None
        raise


def _not_found(resource: str, resource_id: str) -> HTTPException:
    """Build the consistent not-found error used by all Task routes."""

    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{resource} '{resource_id}' not found.",
    )


@task_router.get("/", response_model=list[TaskRecord], summary="List reusable tasks")
async def list_tasks(
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> list[TaskRecord]:
    """List active reusable tasks for the current user."""

    return await task_service.list_tasks(user_id)


@task_router.get(
    "/knowledge-bases",
    response_model=list[TaskKnowledgeBaseOption],
    summary="List knowledge bases available to Tasks",
)
async def list_task_knowledge_bases(
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> list[TaskKnowledgeBaseOption]:
    """Expose a Task-scoped KB selector without coupling the UI to core APIs."""

    gateway = _get_task_knowledge_gateway(request)
    return await gateway.list_knowledge_bases(user_id)


@task_router.post(
    "/",
    response_model=TaskRecord,
    status_code=status.HTTP_201_CREATED,
    summary="Create a reusable linear task",
)
async def create_task(
    body: CreateTaskRequest,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> TaskRecord:
    """Create a task definition without touching AgentScope internals."""

    return await task_service.create_task(user_id, body)


@task_router.post(
    "/{task_id}/generate",
    response_model=TaskRecord,
    summary="Generate task nodes from the goal",
)
async def generate_task(
    task_id: str,
    body: GenerateTaskRequest,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> TaskRecord:
    """Generate an editable linear workflow for a task draft."""

    try:
        task = await task_service.generate_task(
            user_id,
            task_id,
            body.context,
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    if task is None:
        raise _not_found("Task", task_id)
    return task


@task_router.get(
    "/tools",
    response_model=list[TaskToolSchema],
    summary="List tools available to a Task context",
)
async def list_task_tools(
    agent_id: str | None = None,
    session_id: str | None = None,
    workspace_id: str | None = None,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> list[TaskToolSchema]:
    """Expose the selected AgentScope session's tool capabilities."""

    return await task_service.list_tools(
        user_id,
        TaskContext(
            agent_id=agent_id,
            session_id=session_id,
            workspace_id=workspace_id,
        ),
    )


@task_router.get("/runs/{run_id}", response_model=TaskRunRecord, summary="Get a task run")
async def get_run(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> TaskRunRecord:
    """Return one run owned by the current user."""

    run = await task_service.get_run(user_id, run_id)
    if run is None:
        raise _not_found("Run", run_id)
    return run


@task_router.post(
    "/runs/{run_id}/nodes/{node_id}/knowledge-graph",
    response_model=TaskKnowledgeGraphResponse,
    summary="Generate a Step-scoped knowledge graph",
)
async def get_step_knowledge_graph(
    run_id: str,
    node_id: str,
    body: TaskStepKnowledgeGraphRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> TaskKnowledgeGraphResponse:
    """Generate a graph only when a configured Task step asks for it."""

    run = await task_service.get_run(user_id, run_id)
    if run is None:
        raise _not_found("Run", run_id)
    node = next((item for item in run.nodes if item.id == node_id), None)
    if node is None:
        raise _not_found("Task node", node_id)
    if not node.knowledge_graph_enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="当前任务节点未开启知识图谱展示。",
        )

    node_run = next(
        (item for item in run.node_runs if item.node_id == node_id),
        None,
    )
    if node_run is None:
        raise _not_found("Task node run", node_id)
    selected_ids = (
        node.knowledge_base_ids
        if node.knowledge_base_mode == "override"
        else run.knowledge_base_ids
    )
    if node.knowledge_base_mode == "disabled":
        selected_ids = []
    query = body.query or "\n".join(
        item
        for item in (
            node.name,
            node.prompt,
            node_run.input,
            node_run.output,
        )
        if item and item.strip()
    )
    gateway = _get_task_knowledge_gateway(request)
    try:
        graph = await gateway.get_context_graph(
            user_id,
            selected_ids,
            query,
            force_extract=body.force_extract,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    return TaskKnowledgeGraphResponse(task_id=run.task_id, **graph)


@task_router.get(
    "/runs/{run_id}/artifacts",
    response_model=list[TaskArtifactRecord],
    summary="List task run artifacts",
)
async def list_run_artifacts(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> list[TaskArtifactRecord]:
    """Return artifact metadata associated with one owned run."""

    artifacts = await task_service.list_artifacts(user_id, run_id)
    if artifacts is None:
        raise _not_found("Run", run_id)
    return artifacts


@task_router.get(
    "/runs/{run_id}/artifacts/{artifact_id}/content",
    summary="Read or download a task artifact",
)
async def read_run_artifact(
    run_id: str,
    artifact_id: str,
    download: bool = False,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> Response:
    """Stream an artifact from the Workspace through the Task boundary."""

    try:
        result = await task_service.read_artifact(
            user_id,
            run_id,
            artifact_id,
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    if result is None:
        raise _not_found("Artifact", artifact_id)
    artifact, data = result
    disposition = "attachment" if download else "inline"
    # RFC 5987 keeps non-ASCII filenames valid in HTTP response headers.
    fallback_name = artifact.name.encode("ascii", "ignore").decode("ascii")
    fallback_name = fallback_name.replace('"', "_") or f"artifact-{artifact.id}"
    content_disposition = (
        f'{disposition}; filename="{fallback_name}"; '
        f"filename*=UTF-8''{quote(artifact.name, safe='')}"
    )
    return Response(
        content=data,
        media_type=artifact.media_type,
        headers={"Content-Disposition": content_disposition},
    )

@task_router.get(
    "/runs/{run_id}/events",
    summary="Stream task run events",
)
async def stream_run_events(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> StreamingResponse:
    """Replay and then stream lifecycle events until the run is terminal."""

    run = await task_service.get_run(user_id, run_id)
    if run is None:
        raise _not_found("Run", run_id)

    async def event_stream() -> AsyncIterator[str]:
        """Yield server-sent events while preserving replay order."""

        cursor = 0
        while True:
            events = await task_service.list_events(user_id, run_id)
            for event in events[cursor:]:
                payload = json.dumps(
                    event.model_dump(mode="json"),
                    ensure_ascii=False,
                )
                yield f"id: {event.id}\nevent: {event.type}\ndata: {payload}\n\n"
            cursor = len(events)
            latest = await task_service.get_run(user_id, run_id)
            if latest is None:
                return
            if latest.status.value in {
                "succeeded",
                "failed",
                "canceled",
                "timed_out",
            } and cursor >= len(events):
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@task_router.get(
    "/{task_id}/knowledge-graph",
    response_model=TaskKnowledgeGraphResponse,
    summary="Read the selected knowledge graph for a Task",
)
async def get_task_knowledge_graph(
    task_id: str,
    request: Request,
    query: str | None = None,
    knowledge_base_ids: str | None = None,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> TaskKnowledgeGraphResponse:
    """Read a bounded merged graph for the Task's selected KBs."""

    task = await task_service.get_task(user_id, task_id)
    if task is None:
        raise _not_found("Task", task_id)
    selected_ids = (
        [item for item in knowledge_base_ids.split(",") if item]
        if knowledge_base_ids is not None
        else task.knowledge_base_ids
    )
    gateway = _get_task_knowledge_gateway(request)
    try:
        graph = await gateway.get_graph(
            user_id,
            selected_ids,
            query=query,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    return TaskKnowledgeGraphResponse(task_id=task_id, **graph)


@task_router.post(
    "/{task_id}/knowledge-graph/rebuild",
    response_model=TaskKnowledgeGraphRebuildResponse,
    summary="Extract entities and relations for a Task KB",
)
async def rebuild_task_knowledge_graph(
    task_id: str,
    body: TaskKnowledgeGraphRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> TaskKnowledgeGraphRebuildResponse:
    """Run the Task-owned, on-demand graph extraction adapter."""

    task = await task_service.get_task(user_id, task_id)
    if task is None:
        raise _not_found("Task", task_id)
    selected_ids = body.knowledge_base_ids or task.knowledge_base_ids
    gateway = _get_task_knowledge_gateway(request)
    try:
        result = await gateway.rebuild_graph(
            user_id,
            selected_ids,
            force_extract=body.force_extract,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    return TaskKnowledgeGraphRebuildResponse(task_id=task_id, **result)


@task_router.get("/{task_id}", response_model=TaskRecord, summary="Get a task")
async def get_task(
    task_id: str,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> TaskRecord:
    """Return one reusable task definition."""

    task = await task_service.get_task(user_id, task_id)
    if task is None:
        raise _not_found("Task", task_id)
    return task


@task_router.patch("/{task_id}", response_model=TaskRecord, summary="Update a task")
async def update_task(
    task_id: str,
    body: UpdateTaskRequest,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> TaskRecord:
    """Update a task definition and create a new revision."""

    task = await task_service.update_task(user_id, task_id, body)
    if task is None:
        raise _not_found("Task", task_id)
    return task


@task_router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a task",
)
async def delete_task(
    task_id: str,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> Response:
    """Archive a task while retaining its run history."""

    if not await task_service.delete_task(user_id, task_id):
        raise _not_found("Task", task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@task_router.get(
    "/{task_id}/runs",
    response_model=list[TaskRunRecord],
    summary="List task runs",
)
async def list_runs(
    task_id: str,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> list[TaskRunRecord]:
    """List execution history for a task."""

    if await task_service.get_task(user_id, task_id) is None:
        raise _not_found("Task", task_id)
    return await task_service.list_runs(user_id, task_id)


@task_router.post(
    "/{task_id}/runs",
    response_model=TaskRunRecord,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run a saved task",
)
async def create_run(
    task_id: str,
    request: Request,
    body: TaskRunRequest,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> TaskRunRecord:
    """Queue one execution of the saved task definition."""

    run = await task_service.create_run(
        user_id,
        task_id,
        body,
        _maybe_get_task_knowledge_gateway(request),
    )
    if run is None:
        raise _not_found("Task", task_id)
    return run


@task_router.post(
    "/runs/{run_id}/cancel",
    response_model=TaskRunRecord,
    summary="Cancel a task run",
)
async def cancel_run(
    run_id: str,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> TaskRunRecord:
    """Cancel one in-flight task run."""

    run = await task_service.cancel_run(user_id, run_id)
    if run is None:
        raise _not_found("Run", run_id)
    return run


@task_router.post(
    "/runs/{run_id}/retry",
    response_model=TaskRunRecord,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry a task run",
)
async def retry_run(
    run_id: str,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> TaskRunRecord:
    """Create a fresh run without mutating the previous result."""

    run = await task_service.retry_run(
        user_id,
        run_id,
        _maybe_get_task_knowledge_gateway(request),
    )
    if run is None:
        raise _not_found("Run", run_id)
    return run
