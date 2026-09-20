# -*- coding: utf-8 -*-
"""HTTP routes for the standalone Task module."""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from agentscope.app.deps import get_current_user_id

from ._models import (
    CreateTaskRequest,
    GenerateTaskRequest,
    TaskContext,
    TaskRecord,
    TaskRunRecord,
    TaskRunRequest,
    TaskToolSchema,
    UpdateTaskRequest,
)
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
    body: TaskRunRequest,
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> TaskRunRecord:
    """Queue one execution of the saved task definition."""

    run = await task_service.create_run(user_id, task_id, body)
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
    user_id: str = Depends(get_current_user_id),
    task_service: TaskService = Depends(_get_task_service),
) -> TaskRunRecord:
    """Create a fresh run without mutating the previous result."""

    run = await task_service.retry_run(user_id, run_id)
    if run is None:
        raise _not_found("Run", run_id)
    return run
