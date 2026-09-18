# -*- coding: utf-8 -*-
"""Application service for reusable linear Tasks."""

import asyncio
from datetime import datetime, timezone

from ._executor import PreviewTaskExecutor, TaskExecutor
from ._models import (
    CreateTaskRequest,
    ExecutionContext,
    NodeRunRecord,
    NodeRunStatus,
    RunStatus,
    TaskContext,
    TaskEventRecord,
    TaskRecord,
    TaskRunRecord,
    TaskRunRequest,
    TaskStatus,
    UpdateTaskRequest,
)
from ._store import TaskStore


def _utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(timezone.utc)


_TERMINAL_RUN_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELED,
    RunStatus.TIMED_OUT,
}


class TaskService:
    """Own Task lifecycle while keeping execution behind an adapter port."""

    def __init__(
        self,
        store: TaskStore,
        *,
        agentscope_executor: TaskExecutor | None = None,
        preview_executor: TaskExecutor | None = None,
    ) -> None:
        """Bind repository and execution adapters."""

        self._store = store
        self._agentscope_executor = agentscope_executor
        self._preview_executor = preview_executor or PreviewTaskExecutor()
        self._active_runs: dict[str, asyncio.Task[None]] = {}
        self._run_users: dict[str, str] = {}

    async def create_task(self, user_id: str, body: CreateTaskRequest) -> TaskRecord:
        """Create a reusable task definition."""

        record = TaskRecord(
            user_id=user_id,
            title=body.title.strip(),
            goal=body.goal.strip(),
            nodes=body.nodes,
            source_context=body.source_context,
        )
        return await self._store.save_task(record)

    async def list_tasks(self, user_id: str) -> list[TaskRecord]:
        """List the caller's active tasks."""

        return await self._store.list_tasks(user_id)

    async def get_task(self, user_id: str, task_id: str) -> TaskRecord | None:
        """Get one task owned by the caller."""

        return await self._store.get_task(user_id, task_id)

    async def update_task(
        self,
        user_id: str,
        task_id: str,
        body: UpdateTaskRequest,
    ) -> TaskRecord | None:
        """Update a task definition and bump its reusable revision."""

        current = await self._store.get_task(user_id, task_id)
        if current is None:
            return None
        if current.status == TaskStatus.ARCHIVED:
            return None
        updates: dict[str, object] = {"revision": current.revision + 1}
        if body.title is not None:
            updates["title"] = body.title.strip()
        if body.goal is not None:
            updates["goal"] = body.goal.strip()
        if body.nodes is not None:
            updates["nodes"] = body.nodes
        return await self._store.save_task(current.model_copy(update=updates))

    async def delete_task(self, user_id: str, task_id: str) -> bool:
        """Archive a task while keeping its runs inspectable."""

        return await self._store.delete_task(user_id, task_id)

    async def create_run(
        self,
        user_id: str,
        task_id: str,
        body: TaskRunRequest,
    ) -> TaskRunRecord | None:
        """Snapshot a task and schedule one asynchronous execution."""

        task = await self._store.get_task(user_id, task_id)
        if task is None or task.status == TaskStatus.ARCHIVED:
            return None
        context = task.source_context.model_copy(update=body.context.model_dump(exclude_none=True))
        run = TaskRunRecord(
            task_id=task.id,
            user_id=user_id,
            task_revision=task.revision,
            nodes=task.nodes,
            node_runs=[
                NodeRunRecord(
                    node_id=node.id,
                    name=node.name,
                    prompt=node.prompt,
                    order=node.order,
                )
                for node in task.nodes
            ],
            input=body.input,
            context=context,
        )
        await self._store.save_run(run)
        await self._store.save_task(
            task.model_copy(
                update={
                    "last_run_id": run.id,
                    "last_run_status": RunStatus.QUEUED,
                },
            ),
        )
        self._run_users[run.id] = user_id
        await self._emit(run, "run.created", payload={"task_revision": task.revision})
        execution = asyncio.create_task(self._execute_run(run.id))
        self._active_runs[run.id] = execution
        execution.add_done_callback(lambda _: self._active_runs.pop(run.id, None))
        return await self._store.get_run(user_id, run.id)

    async def get_run(self, user_id: str, run_id: str) -> TaskRunRecord | None:
        """Get one run owned by the caller."""

        return await self._store.get_run(user_id, run_id)

    async def list_runs(self, user_id: str, task_id: str) -> list[TaskRunRecord]:
        """List all runs for one task."""

        if await self._store.get_task(user_id, task_id) is None:
            return []
        return await self._store.list_runs(user_id, task_id)

    async def cancel_run(self, user_id: str, run_id: str) -> TaskRunRecord | None:
        """Cancel an in-flight run when the task executor is local."""

        run = await self._store.get_run(user_id, run_id)
        if run is None:
            return None
        if run.status in _TERMINAL_RUN_STATUSES:
            return run
        active = self._active_runs.get(run_id)
        if active is not None:
            active.cancel()
        node_runs = [
            node_run.model_copy(
                update={"status": NodeRunStatus.CANCELED},
            )
            if node_run.status in {
                NodeRunStatus.PENDING,
                NodeRunStatus.RUNNING,
            }
            else node_run
            for node_run in run.node_runs
        ]
        updated = await self._store.update_run(
            user_id,
            run_id,
            status=RunStatus.CANCELED,
            node_runs=node_runs,
            finished_at=_utc_now(),
            error="Run canceled by the user.",
        )
        await self._emit(updated, "run.canceled", payload={})
        return updated

    async def retry_run(self, user_id: str, run_id: str) -> TaskRunRecord | None:
        """Create a fresh Run from a previous Run's input and context."""

        run = await self._store.get_run(user_id, run_id)
        if run is None:
            return None
        return await self.create_run(
            user_id,
            run.task_id,
            TaskRunRequest(input=run.input, context=run.context),
        )

    async def list_events(self, user_id: str, run_id: str) -> list[TaskEventRecord]:
        """Replay lifecycle events for a run."""

        return await self._store.list_events(user_id, run_id)

    async def shutdown(self) -> None:
        """Cancel task-module workers during application shutdown."""

        active_runs = list(self._active_runs.values())
        for active in active_runs:
            active.cancel()
        if active_runs:
            await asyncio.gather(*active_runs, return_exceptions=True)
        self._active_runs.clear()

    async def _execute_run(self, run_id: str) -> None:
        """Execute a task snapshot from first node to last node."""

        run = await self._find_run(run_id)
        if run is None:
            return
        try:
            run = await self._store.update_run(
                run.user_id,
                run.id,
                status=RunStatus.RUNNING,
                started_at=_utc_now(),
            )
            await self._emit(run, "run.started", payload={})
            previous_output = run.input
            for node in run.nodes:
                run = await self._store.update_node_run(
                    run.user_id,
                    run.id,
                    node.id,
                    status=NodeRunStatus.RUNNING,
                    input=previous_output,
                    started_at=_utc_now(),
                )
                await self._emit(run, "step.started", node_id=node.id, payload={})
                context = ExecutionContext(
                    **run.context.model_dump(),
                    user_id=run.user_id,
                    task_id=run.task_id,
                    run_id=run.id,
                    task_revision=run.task_revision,
                )
                executor = self._select_executor(run.context)
                output = await executor.execute_node(
                    node=node,
                    previous_output=previous_output,
                    context=context,
                )
                previous_output = output
                run = await self._store.update_node_run(
                    run.user_id,
                    run.id,
                    node.id,
                    status=NodeRunStatus.SUCCEEDED,
                    output=output,
                    finished_at=_utc_now(),
                )
                await self._emit(
                    run,
                    "step.succeeded",
                    node_id=node.id,
                    payload={"output": output},
                )

            run = await self._store.update_run(
                run.user_id,
                run.id,
                status=RunStatus.SUCCEEDED,
                final_output=previous_output,
                finished_at=_utc_now(),
            )
            await self._mark_task_run_status(run)
            await self._emit(run, "run.succeeded", payload={"output": previous_output})
        except asyncio.CancelledError:
            # ``cancel_run`` persists the terminal status; a shutdown does not
            # need to publish a user-facing event.
            return
        except Exception as error:  # pylint: disable=broad-except
            message = str(error) or "Task node execution failed."
            current = await self._find_run(run_id)
            if current is None or current.status in _TERMINAL_RUN_STATUSES:
                return
            failed_node = next(
                (
                    node_run
                    for node_run in current.node_runs
                    if node_run.status == NodeRunStatus.RUNNING
                ),
                None,
            )
            if failed_node is not None:
                current = await self._store.update_node_run(
                    current.user_id,
                    current.id,
                    failed_node.node_id,
                    status=NodeRunStatus.FAILED,
                    error=message,
                    finished_at=_utc_now(),
                )
                await self._emit(
                    current,
                    "step.failed",
                    node_id=failed_node.node_id,
                    payload={"message": message},
                )
            current = await self._store.update_run(
                current.user_id,
                current.id,
                status=RunStatus.FAILED,
                error=message,
                finished_at=_utc_now(),
            )
            await self._mark_task_run_status(current)
            await self._emit(current, "run.failed", payload={"message": message})

    def _select_executor(self, context: TaskContext) -> TaskExecutor:
        """Use AgentScope only when a real Chat/Session context is attached."""

        if (
            self._agentscope_executor is not None
            and context.agent_id
            and context.session_id
        ):
            return self._agentscope_executor
        return self._preview_executor

    async def _find_run(self, run_id: str) -> TaskRunRecord | None:
        """Find a run without knowing its owner for internal execution."""

        user_id = self._run_users.get(run_id)
        if user_id is None:
            return None
        return await self._store.get_run(user_id, run_id)

    async def _mark_task_run_status(self, run: TaskRunRecord) -> None:
        """Reflect the latest run status on the reusable task summary."""

        task = await self._store.get_task(run.user_id, run.task_id)
        if task is not None:
            await self._store.save_task(
                task.model_copy(
                    update={"last_run_id": run.id, "last_run_status": run.status},
                ),
            )

    async def _emit(
        self,
        run: TaskRunRecord,
        event_type: str,
        *,
        payload: dict[str, object],
        node_id: str | None = None,
    ) -> None:
        """Append one event with a monotonic sequence number."""

        sequence = await self._store.next_event_sequence(run.id)
        await self._store.append_event(
            TaskEventRecord(
                type=event_type,
                task_id=run.task_id,
                run_id=run.id,
                node_id=node_id,
                sequence=sequence,
                payload=payload,
            ),
        )
