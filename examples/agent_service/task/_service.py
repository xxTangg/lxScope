# -*- coding: utf-8 -*-
"""Application service for reusable linear Tasks."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any

from agentscope.event import CustomEvent
from agentscope.message import UserMsg
from agentscope.sop import SOP, SOPEngine, SOPPhase, SOPRunState
from datetime import datetime, timezone

from ._artifact_spec import (
    ArtifactSpec,
    artifact_spec_prompt,
    parse_agent_spec,
    spec_from_markdown,
)
from ._artifacts import GeneratedArtifact, generate_artifact
from ._executor import (
    PreviewTaskExecutor,
    StepExecutionResult,
    TaskArtifactWriter,
    TaskExecutor,
)
from ._models import (
    AgentStepConfig,
    CreateTaskRequest,
    ExecutionContext,
    NodeRunRecord,
    NodeRunStatus,
    RunStatus,
    TaskArtifactRecord,
    TaskContext,
    TaskEventRecord,
    TaskGenerationStatus,
    TaskNode,
    TaskRecord,
    TaskToolSchema,
    TaskRunRecord,
    TaskRunRequest,
    TaskInputReply,
    StepVerdict,
    StepType,
    TaskStatus,
    UpdateTaskRequest,
)
from ._planner import PreviewTaskPlanner, TaskPlanner
from ._plan_validation import format_plan_issues, validate_task_plan
from ._store import TaskStoreProtocol
from ._knowledge import TaskKnowledgeGateway
from ._sop import TaskSOPStep, TaskStepState


def _utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(timezone.utc)


_TERMINAL_RUN_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELED,
    RunStatus.TIMED_OUT,
}


logger = logging.getLogger(__name__)

_DEFAULT_TASK_GENERATION_TIMEOUT_SECONDS = 45.0


def _configured_generation_timeout() -> float:
    """Read the planner deadline without allowing an invalid env to disable it."""

    value = os.getenv(
        "LXSCOPE_TASK_GENERATION_TIMEOUT_SECONDS",
        str(_DEFAULT_TASK_GENERATION_TIMEOUT_SECONDS),
    )
    try:
        return max(1.0, float(value))
    except (TypeError, ValueError):
        return _DEFAULT_TASK_GENERATION_TIMEOUT_SECONDS


class TaskService:
    """Own Task lifecycle while keeping execution behind an adapter port."""

    def __init__(
        self,
        store: TaskStoreProtocol,
        *,
        agentscope_executor: TaskExecutor | None = None,
        preview_executor: TaskExecutor | None = None,
        planner: TaskPlanner | None = None,
        artifact_writer: TaskArtifactWriter | None = None,
        generation_timeout_seconds: float | None = None,
        message_bus: Any | None = None,
    ) -> None:
        """Bind repository and execution adapters."""

        self._store = store
        self._message_bus = message_bus
        self._run_locks: dict[str, asyncio.Lock] = {}
        self._agentscope_executor = agentscope_executor
        candidate = artifact_writer or agentscope_executor
        self._artifact_writer = (
            candidate
            if callable(getattr(candidate, "write_artifact", None))
            and callable(getattr(candidate, "read_artifact", None))
            else None
        )
        self._preview_executor = preview_executor or PreviewTaskExecutor()
        self._planner = planner or PreviewTaskPlanner()
        self._generation_timeout_seconds = max(
            1.0,
            (
                generation_timeout_seconds
                if generation_timeout_seconds is not None
                else _configured_generation_timeout()
            ),
        )
        self._active_runs: dict[str, asyncio.Task[None]] = {}
        self._run_users: dict[str, str] = {}
        # Some adapters turn ``CancelledError`` into a normal error/result.
        # Retain the cancellation request until the worker exits so such an
        # adapter cannot later overwrite the user's terminal cancellation.
        self._cancel_requested_run_ids: set[str] = set()

    async def create_task(
        self, user_id: str, body: CreateTaskRequest
    ) -> TaskRecord:
        """Create a draft or manually-defined reusable task."""

        has_nodes = bool(body.nodes)
        record = TaskRecord(
            user_id=user_id,
            title=(body.title or "").strip() or "未命名任务",
            goal=body.goal.strip(),
            nodes=body.nodes,
            title_source=(
                "user" if body.title and body.title.strip() else "auto"
            ),
            status=TaskStatus.ACTIVE if has_nodes else TaskStatus.DRAFT,
            knowledge_base_ids=body.knowledge_base_ids,
            generation_status=(
                TaskGenerationStatus.SUCCEEDED
                if has_nodes
                else TaskGenerationStatus.IDLE
            ),
            source_context=body.source_context,
        )
        return await self._store.save_task(record)

    async def generate_task(
        self,
        user_id: str,
        task_id: str,
        context: TaskContext | None,
    ) -> TaskRecord | None:
        """Generate and persist editable nodes for one task draft."""

        current = await self._store.get_task(user_id, task_id)
        if current is None or current.status == TaskStatus.ARCHIVED:
            return None
        merged_context = (
            context if context is not None else current.source_context
        )
        await self._store.save_task(
            current.model_copy(
                update={
                    "source_context": merged_context,
                    "generation_status": TaskGenerationStatus.GENERATING,
                    "generation_error": None,
                },
            ),
        )
        try:
            tool_schemas: list[TaskToolSchema] = []
            try:
                tool_schemas = await asyncio.wait_for(
                    self.list_tools(user_id, merged_context),
                    timeout=self._generation_timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Task tool discovery timed out after %.1fs for task=%s; "
                    "continuing without tool schemas",
                    self._generation_timeout_seconds,
                    task_id,
                )
                tool_schemas = []
            except Exception:  # pylint: disable=broad-except
                # Tool discovery is an enhancement for planning. A broken
                # optional MCP must not prevent Agent/Python planning.
                tool_schemas = []
            try:
                draft = await asyncio.wait_for(
                    self._planner.plan(
                        user_id=user_id,
                        goal=current.goal,
                        context=merged_context,
                        current_title=(
                            current.title
                            if current.title_source == "user"
                            else None
                        ),
                        tool_schemas=tool_schemas,
                    ),
                    timeout=self._generation_timeout_seconds,
                )
            except asyncio.TimeoutError:
                # A planner may be waiting on a model or a stateful MCP.  The
                # editor must never leave a newly created task in "generating"
                # forever; a deterministic editable plan keeps the UI usable
                # while still allowing the user to refine or regenerate it.
                logger.warning(
                    "Task planner timed out after %.1fs for task=%s; "
                    "using the editable fallback plan",
                    self._generation_timeout_seconds,
                    task_id,
                )
                draft = await PreviewTaskPlanner().plan(
                    user_id=user_id,
                    goal=current.goal,
                    context=merged_context,
                    current_title=(
                        current.title
                        if current.title_source == "user"
                        else None
                    ),
                    tool_schemas=tool_schemas,
                )
            plan_issues = validate_task_plan(draft, tool_schemas)
            if plan_issues:
                raise ValueError(
                    "Generated task plan is invalid:\n"
                    + format_plan_issues(plan_issues),
                )
            nodes = [
                TaskNode(
                    name=node.name.strip(),
                    prompt=node.prompt.strip(),
                    type=node.type,
                    config=node.config,
                    order=index,
                    artifact=node.artifact,
                    acceptance_criteria=node.acceptance_criteria,
                    max_attempts=node.max_attempts,
                )
                for index, node in enumerate(draft.nodes)
            ]
            title = (
                current.title
                if current.title_source == "user"
                else draft.suggested_title.strip()
            )
            updated = current.model_copy(
                update={
                    "title": title or "未命名任务",
                    "goal": current.goal.strip(),
                    "intent": draft.intent.strip(),
                    "nodes": nodes,
                    "status": TaskStatus.ACTIVE,
                    "revision": current.revision + 1,
                    "generation_status": TaskGenerationStatus.SUCCEEDED,
                    "generation_error": None,
                    "source_context": merged_context,
                },
            )
            return await self._store.save_task(updated)
        except Exception as error:  # pylint: disable=broad-except
            message = str(error) or "任务节点生成失败。"
            await self._store.save_task(
                current.model_copy(
                    update={
                        "source_context": merged_context,
                        "generation_status": TaskGenerationStatus.FAILED,
                        "generation_error": message,
                    },
                ),
            )
            raise

    async def list_tasks(self, user_id: str) -> list[TaskRecord]:
        """List the caller's active tasks."""

        return await self._store.list_tasks(user_id)

    async def list_tools(
        self,
        user_id: str,
        context: TaskContext,
    ) -> list[TaskToolSchema]:
        """List AgentScope tools available to the selected Task context."""

        executor = self._agentscope_executor
        list_tools = getattr(executor, "list_tools", None)
        if (
            list_tools is None
            or not context.agent_id
            or not context.session_id
        ):
            return []
        execution_context = ExecutionContext(
            **context.model_dump(),
            user_id=user_id,
            task_id="task-tool-catalog",
            run_id="task-tool-catalog",
            task_revision=0,
        )
        try:
            return await list_tools(execution_context)
        except Exception as error:  # noqa: BLE001 — tool discovery is optional
            # Tool discovery runs while the Task editor is loading.  A stale
            # stateful MCP must not make the whole page look unreachable; the
            # planner and editor can safely continue with no optional tools.
            logger.warning(
                "Task tool discovery degraded for agent=%s session=%s; "
                "continuing with no optional tools: %s",
                context.agent_id,
                context.session_id,
                error,
            )
            return []

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
        updates: dict[str, object] = {
            "revision": current.revision + 1,
            "generation_error": None,
        }
        if body.title is not None:
            updates["title"] = body.title.strip()
            updates["title_source"] = "user"
        if body.goal is not None:
            updates["goal"] = body.goal.strip()
        if body.source_context is not None:
            updates["source_context"] = body.source_context
        if body.knowledge_base_ids is not None:
            updates["knowledge_base_ids"] = body.knowledge_base_ids
        if body.nodes is not None:
            updates["nodes"] = body.nodes
            updates["status"] = (
                TaskStatus.ACTIVE if body.nodes else TaskStatus.DRAFT
            )
            updates["generation_status"] = (
                TaskGenerationStatus.SUCCEEDED
                if body.nodes
                else TaskGenerationStatus.IDLE
            )
        return await self._store.save_task(current.model_copy(update=updates))

    async def delete_task(self, user_id: str, task_id: str) -> bool:
        """Archive a task while keeping its runs inspectable."""

        return await self._store.delete_task(user_id, task_id)

    async def create_run(
        self,
        user_id: str,
        task_id: str,
        body: TaskRunRequest,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> TaskRunRecord | None:
        """Snapshot a task and schedule one asynchronous execution."""

        task = await self._store.get_task(user_id, task_id)
        if task is None or task.status != TaskStatus.ACTIVE or not task.nodes:
            return None
        context = task.source_context.model_copy(
            update=body.context.model_dump(exclude_none=True),
        )
        run = TaskRunRecord(
            task_id=task.id,
            user_id=user_id,
            task_revision=task.revision,
            engine="sop",
            nodes=task.nodes,
            node_runs=[
                NodeRunRecord(
                    node_id=node.id,
                    name=node.name,
                    prompt=node.prompt,
                    type=node.type,
                    order=node.order,
                )
                for node in task.nodes
            ],
            knowledge_base_ids=task.knowledge_base_ids,
            input=body.input.strip() or task.goal,
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
        await self._emit(
            run, "run.created", payload={"task_revision": task.revision}
        )
        self._schedule_run(run, knowledge_gateway)
        return await self._store.get_run(user_id, run.id)

    async def get_run(self, user_id: str, run_id: str) -> TaskRunRecord | None:
        """Get one run owned by the caller."""

        return await self._store.get_run(user_id, run_id)

    async def list_runs(
        self, user_id: str, task_id: str
    ) -> list[TaskRunRecord]:
        """List all runs for one task."""

        if await self._store.get_task(user_id, task_id) is None:
            return []
        return await self._store.list_runs(user_id, task_id)

    async def cancel_run(
        self, user_id: str, run_id: str
    ) -> TaskRunRecord | None:
        """Persist cancellation and notify the worker that owns execution."""

        run = await self._store.get_run(user_id, run_id)
        if run is None:
            return None
        if run.status in _TERMINAL_RUN_STATUSES:
            return run
        # Record intent before yielding to storage or cancellation.  A task
        # executor may catch CancelledError internally and otherwise continue
        # to write a later failed/succeeded state.
        self._cancel_requested_run_ids.add(run_id)
        active = self._active_runs.get(run_id)
        if active is not None:
            active.cancel()
        node_runs = [
            (
                node_run.model_copy(
                    update={"status": NodeRunStatus.CANCELED},
                )
                if node_run.status
                in {
                    NodeRunStatus.PENDING,
                    NodeRunStatus.RUNNING,
                    NodeRunStatus.AWAITING_INPUT,
                }
                else node_run
            )
            for node_run in run.node_runs
        ]
        updated = await self._store.update_run(
            user_id,
            run_id,
            status=RunStatus.CANCELED,
            node_runs=node_runs,
            finished_at=_utc_now(),
            error="Run canceled by the user.",
            pending_input=None,
        )
        # The task list renders ``last_run_status`` rather than loading every
        # Run.  Keep that summary in sync for cancellation just as we do for
        # successful and failed executions; otherwise a stopped Run can stay
        # labelled queued/running in the sidebar.
        await self._mark_task_run_status(updated)
        await self._emit(updated, "run.canceled", payload={})
        if self._message_bus is not None:
            try:
                await self._message_bus.publish(
                    f"lxscope:task:run:{run_id}:cancel",
                    {"run_id": run_id},
                )
            except Exception:
                logger.exception(
                    "Task cancellation persisted, but worker notification failed: %s",
                    run_id,
                )
        if active is None:
            self._cancel_requested_run_ids.discard(run_id)
        return updated

    async def retry_run(
        self,
        user_id: str,
        run_id: str,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> TaskRunRecord | None:
        """Create a fresh Run from a previous Run's input and context."""

        run = await self._store.get_run(user_id, run_id)
        if run is None:
            return None
        return await self.create_run(
            user_id,
            run.task_id,
            TaskRunRequest(input=run.input, context=run.context),
            knowledge_gateway,
        )

    async def list_artifacts(
        self,
        user_id: str,
        run_id: str,
    ) -> list[TaskArtifactRecord] | None:
        """Return artifact metadata for one owned run."""

        run = await self._store.get_run(user_id, run_id)
        return None if run is None else run.artifacts

    async def read_artifact(
        self,
        user_id: str,
        run_id: str,
        artifact_id: str,
    ) -> tuple[TaskArtifactRecord, bytes] | None:
        """Read one owned artifact through the Task storage adapter."""

        run = await self._store.get_run(user_id, run_id)
        if run is None:
            return None
        artifact = next(
            (item for item in run.artifacts if item.id == artifact_id),
            None,
        )
        if artifact is None:
            return None
        if self._artifact_writer is None:
            raise RuntimeError(
                "Task artifact Workspace storage is unavailable."
            )
        context = ExecutionContext(
            **run.context.model_dump(),
            user_id=run.user_id,
            task_id=run.task_id,
            run_id=run.id,
            task_revision=run.task_revision,
        )
        data = await self._artifact_writer.read_artifact(
            path=artifact.path,
            context=context,
        )
        return artifact, data

    async def list_events(
        self, user_id: str, run_id: str
    ) -> list[TaskEventRecord]:
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

    async def _execute_run(
        self,
        run_id: str,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> None:
        """Advance the official engine under a per-run execution lock."""
        owner = asyncio.current_task()
        listener: asyncio.Task[None] | None = None
        try:
            if self._message_bus is not None:
                ready = asyncio.Event()

                async def watch_cancel() -> None:
                    async for _ in self._message_bus.subscribe(
                        f"lxscope:task:run:{run_id}:cancel",
                        on_ready=ready.set,
                    ):
                        self._cancel_requested_run_ids.add(run_id)
                        if owner is not None:
                            owner.cancel()
                        return

                listener = asyncio.create_task(watch_cancel())
                await asyncio.wait_for(ready.wait(), timeout=10)
            async with self._run_guard(run_id):
                await self._advance_sop(run_id, knowledge_gateway)
        except asyncio.CancelledError:
            return
        except Exception as error:
            logger.exception("Task worker could not start: %s", run_id)
            run = await self._find_run(run_id)
            if run is not None and run.status not in _TERMINAL_RUN_STATUSES:
                run = await self._store.update_run(
                    run.user_id,
                    run.id,
                    status=RunStatus.FAILED,
                    error=str(error) or "Task worker could not start.",
                    finished_at=_utc_now(),
                )
                await self._mark_task_run_status(run)
                await self._emit(
                    run, "run.failed", payload={"message": run.error}
                )
        finally:
            if listener is not None:
                listener.cancel()
                await asyncio.gather(listener, return_exceptions=True)

    async def _advance_sop(
        self,
        run_id: str,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> None:
        """Rebuild the engine from durable state, then persist its transitions."""

        run = await self._find_run(run_id)
        if (
            run is None
            or run.status in _TERMINAL_RUN_STATUSES
            or self._is_cancel_requested(run_id)
        ):
            return
        engine: SOPEngine | None = None
        try:
            run = await self._store.update_run(
                run.user_id,
                run.id,
                status=RunStatus.RUNNING,
                started_at=run.started_at or _utc_now(),
            )
            await self._mark_task_run_status(run)
            await self._emit(run, "run.started", payload={})
            engine = self._build_sop_engine(run, knowledge_gateway)
            async for event in engine.reply_stream():
                if self._is_cancel_requested(run_id):
                    return
                if isinstance(event, CustomEvent) and event.name.startswith(
                    "TASK_STEP_"
                ):
                    run = await self._checkpoint_sop(run, engine, event)
            run = await self._checkpoint_sop(run, engine)
            if engine.phase is SOPPhase.AWAITING:
                return
            if engine.phase is SOPPhase.FAILED:
                rejected = next(
                    step
                    for step in engine.state.steps
                    if step.phase is SOPPhase.FAILED
                )
                raise RuntimeError(
                    rejected.metadata.get("error")
                    or (
                        rejected.verifications[-1].message
                        if rejected.verifications
                        else "SOP step failed."
                    )
                )
            if engine.phase is not SOPPhase.COMPLETED:
                raise RuntimeError("SOP ended without completing all steps.")
            previous_output = engine.state.steps[-1].output
            execution_history = self._execution_history(run)

            artifacts: list[TaskArtifactRecord] = []
            if self._is_cancel_requested(run_id):
                return
            artifact_node = _artifact_node(run.nodes)
            if artifact_node is not None:
                artifact_config = artifact_node.artifact
                if artifact_config is None:  # pragma: no cover
                    raise RuntimeError("Artifact configuration is missing.")
                artifact_context = ExecutionContext(
                    **run.context.model_dump(),
                    user_id=run.user_id,
                    task_id=run.task_id,
                    run_id=run.id,
                    task_revision=run.task_revision,
                    history=execution_history,
                )
                artifact_spec = await self._build_artifact_spec(
                    output=previous_output,
                    context=artifact_context,
                    format=artifact_config.format.value,
                )
                generated = generate_artifact(
                    output=previous_output,
                    config=artifact_config,
                    default_stem=f"task-{run.id}",
                    spec=artifact_spec,
                )
                artifact = await self._store_artifact(
                    run=run,
                    node=artifact_node,
                    generated=generated,
                    context=artifact_context,
                )
                artifacts.append(artifact)
                run = await self._store.update_run(
                    run.user_id,
                    run.id,
                    artifacts=artifacts,
                )
                await self._emit(
                    run,
                    "artifact.created",
                    node_id=artifact_node.id,
                    payload=artifact.model_dump(mode="json"),
                )
            if self._is_cancel_requested(run_id):
                return
            run = await self._store.update_run(
                run.user_id,
                run.id,
                status=RunStatus.SUCCEEDED,
                final_output=previous_output,
                final_summary=_compact_text(previous_output),
                finished_at=_utc_now(),
            )
            await self._mark_task_run_status(run)
            await self._emit(
                run,
                "run.succeeded",
                payload={"summary": _compact_text(previous_output)},
            )
        except asyncio.CancelledError:
            # ``cancel_run`` persists the terminal status; a shutdown does not
            # need to publish a user-facing event.
            return
        except Exception as error:  # pylint: disable=broad-except
            message = str(error) or "Task node execution failed."
            if self._is_cancel_requested(run_id):
                return
            if engine is not None:
                running = next(
                    (
                        step
                        for step in engine.state.steps
                        if step.phase is SOPPhase.RUNNING
                    ),
                    None,
                )
                if running is not None:
                    running.phase = SOPPhase.FAILED
                    running.metadata["error"] = message
                    await self._checkpoint_sop(run, engine)
            current = await self._find_run(run_id)
            if current is None or current.status in _TERMINAL_RUN_STATUSES:
                return
            failed_node = next(
                (
                    node_run
                    for node_run in current.node_runs
                    if node_run.status
                    in {NodeRunStatus.RUNNING, NodeRunStatus.FAILED}
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
            await self._emit(
                current, "run.failed", payload={"message": message}
            )

    @asynccontextmanager
    async def _run_guard(self, run_id: str) -> AsyncIterator[None]:
        """Serialize input admission and execution, including across workers."""
        async with self._run_locks.setdefault(run_id, asyncio.Lock()):
            if self._message_bus is None:
                yield
            else:
                async with self._message_bus.acquire_lock(
                    f"lxscope:task:run:{run_id}:lock",
                    ttl_secs=3600,
                ):
                    yield

    def _schedule_run(
        self,
        run: TaskRunRecord,
        knowledge_gateway: TaskKnowledgeGateway | None,
    ) -> None:
        self._run_users[run.id] = run.user_id
        execution = asyncio.create_task(
            self._execute_run(run.id, knowledge_gateway)
        )
        self._active_runs[run.id] = execution

        def forget(_: asyncio.Task[None]) -> None:
            if self._active_runs.get(run.id) is execution:
                self._active_runs.pop(run.id, None)
                self._cancel_requested_run_ids.discard(run.id)
                self._run_users.pop(run.id, None)

        execution.add_done_callback(forget)

    async def answer_run(
        self,
        user_id: str,
        run_id: str,
        reply: TaskInputReply,
        knowledge_gateway: TaskKnowledgeGateway | None = None,
    ) -> TaskRunRecord | None:
        """Validate a correlated reply and queue the same run from its checkpoint."""
        run = await self._store.get_run(user_id, run_id)
        if run is None:
            return None
        # Never block an HTTP request behind a long-running model call.
        if run.status != RunStatus.AWAITING_INPUT:
            return self._duplicate_reply(run, reply)
        async with self._run_guard(run_id):
            run = await self._store.get_run(user_id, run_id)
            if run is None:
                return None
            if run.status != RunStatus.AWAITING_INPUT:
                return self._duplicate_reply(run, reply)
            request = run.pending_input
            if request is None or request.id != reply.request_id:
                return self._duplicate_reply(run, reply)
            allowed = {question.key for question in request.questions}
            if set(reply.answers) - allowed:
                raise ValueError("回答包含当前问题之外的字段。")
            missing = [
                question.label
                for question in request.questions
                if question.required
                and not reply.answers.get(question.key, "").strip()
            ]
            if missing:
                raise ValueError("请补充必填信息：" + "、".join(missing))
            if not any(reply.answers.values()):
                raise ValueError("回答不能为空。")
            state = SOPRunState.model_validate(run.sop_state)
            index = next(
                index
                for index, node in enumerate(run.nodes)
                if node.id == request.node_id
            )
            step = TaskStepState.model_validate(
                state.steps[index].model_dump()
            )
            step.answers.append(reply)
            step.input_request = None
            state.steps[index] = step
            run = await self._store.update_run(
                user_id,
                run_id,
                status=RunStatus.QUEUED,
                pending_input=None,
                sop_state=state.model_dump(mode="json"),
            )
            await self._mark_task_run_status(run)
            await self._emit(
                run,
                "input.answered",
                node_id=request.node_id,
                payload={"request_id": request.id, "answers": reply.answers},
            )
            self._schedule_run(run, knowledge_gateway)
            return run

    @staticmethod
    def _duplicate_reply(
        run: TaskRunRecord, reply: TaskInputReply
    ) -> TaskRunRecord:
        """A retried HTTP answer must not schedule a second execution."""
        for step in (run.sop_state or {}).get("steps", []):
            for stored in step.get("answers", []):
                if stored["request_id"] == reply.request_id:
                    if stored["answers"] == reply.answers:
                        return run
                    raise ValueError("该问题已收到不同的回答。")
        raise ValueError("本次运行没有等待此问题的回答。")

    @staticmethod
    def _execution_history(run: TaskRunRecord) -> list[dict[str, object]]:
        return [
            {
                "node_id": node.node_id,
                "name": node.name,
                "type": node.type.value,
                "output": _history_text(node.output),
                "metadata": node.metadata,
            }
            for node in run.node_runs
            if node.status is NodeRunStatus.SUCCEEDED
        ]

    def _build_sop_engine(
        self, run: TaskRunRecord, gateway: TaskKnowledgeGateway | None
    ) -> SOPEngine:
        async def execute(
            node: TaskNode, state: TaskStepState
        ) -> StepExecutionResult:
            current = await self._store.get_run(run.user_id, run.id)
            if current is None or current.status is RunStatus.CANCELED:
                raise asyncio.CancelledError
            previous_output = (
                run.input
                if node.order == 0
                else current.node_runs[node.order - 1].output
            )
            answers = [
                TaskInputReply.model_validate(answer)
                for step in (current.sop_state or {}).get("steps", [])
                for answer in step.get("answers", [])
            ]
            context = ExecutionContext(
                **self._step_context(run, node).model_dump(),
                user_id=run.user_id,
                task_id=run.task_id,
                run_id=run.id,
                task_revision=run.task_revision,
                history=self._execution_history(current),
                initial_input=run.input,
                user_answers=answers,
                verification_feedback=(
                    state.verifications[-1].message
                    if state.verifications
                    else ""
                ),
            )
            state.knowledge_context = ""
            if gateway is not None and node.type is StepType.AGENT:
                knowledge = await self._retrieve_knowledge(
                    gateway, current, node, previous_output
                )
                if knowledge:
                    state.knowledge_context = knowledge
                    context = context.model_copy(
                        update={"knowledge_context": knowledge}
                    )
            return _normalize_execution_result(
                await self._select_executor(context).execute_node(
                    node=node,
                    previous_output=previous_output,
                    context=context,
                )
            )

        async def verify(
            node: TaskNode, output: str, state: TaskStepState
        ) -> StepVerdict:
            executor = self._select_executor(self._step_context(run, node))
            method = getattr(executor, "verify_node", None)
            if not node.acceptance_criteria.strip() and (
                not callable(method) or node.type is not StepType.AGENT
            ):
                return StepVerdict(passed=True)
            if not callable(method):
                raise RuntimeError(
                    "当前执行器不支持步骤验收，请使用已配置模型的会话。"
                )
            current = await self._store.get_run(run.user_id, run.id)
            context = ExecutionContext(
                **self._step_context(run, node).model_dump(),
                user_id=run.user_id,
                task_id=run.task_id,
                run_id=run.id,
                task_revision=run.task_revision,
                initial_input=run.input,
                knowledge_context=state.knowledge_context,
                history=self._execution_history(current or run),
                user_answers=[
                    TaskInputReply.model_validate(answer)
                    for step in ((current or run).sop_state or {}).get(
                        "steps", []
                    )
                    for answer in step.get("answers", [])
                ],
            )
            return StepVerdict.model_validate(
                await method(node=node, output=output, context=context)
            )

        steps = [
            TaskSOPStep(
                node,
                lambda state, node=node: execute(node, state),
                lambda output, state, node=node: verify(node, output, state),
            )
            for node in run.nodes
        ]
        state = (
            SOPRunState.model_validate(run.sop_state)
            if run.sop_state
            else SOPRunState(
                id=run.id,
                inputs=[UserMsg(name="user", content=run.input)],
            )
        )
        return SOPEngine(SOP(name=run.task_id, steps=steps), state)

    @staticmethod
    def _step_context(run: TaskRunRecord, node: TaskNode) -> TaskContext:
        """Honor frozen per-step bindings without modifying the run context."""
        overrides = {}
        if isinstance(node.config, AgentStepConfig):
            if node.config.agent_id:
                overrides["agent_id"] = node.config.agent_id
            if node.config.session_id:
                overrides["session_id"] = node.config.session_id
        return run.context.model_copy(update=overrides)

    async def _checkpoint_sop(
        self,
        run: TaskRunRecord,
        engine: SOPEngine,
        event: CustomEvent | None = None,
    ) -> TaskRunRecord:
        """Project official state to the legacy UI, without deciding progression."""
        current = await self._store.get_run(run.user_id, run.id)
        if current is None or current.status in _TERMINAL_RUN_STATUSES:
            raise asyncio.CancelledError
        projections = []
        pending = None
        for definition, record, stored in zip(
            run.nodes, engine.state.steps, current.node_runs
        ):
            status = {
                SOPPhase.PENDING: NodeRunStatus.PENDING,
                SOPPhase.RUNNING: NodeRunStatus.RUNNING,
                SOPPhase.AWAITING: NodeRunStatus.AWAITING_INPUT,
                SOPPhase.COMPLETED: NodeRunStatus.SUCCEEDED,
                SOPPhase.FAILED: NodeRunStatus.FAILED,
            }[record.phase]
            started = stored.started_at
            if record.phase is not SOPPhase.PENDING:
                started = started or _utc_now()
            finished = stored.finished_at
            if status in {NodeRunStatus.SUCCEEDED, NodeRunStatus.FAILED}:
                finished = finished or _utc_now()
            pending = record.input_request or pending
            projections.append(
                stored.model_copy(
                    update={
                        "status": status,
                        "output": record.output,
                        "display_summary": _compact_text(record.output),
                        "result": record.result,
                        "metadata": {
                            **record.metadata,
                            "attempts": len(record.verifications),
                            "verifications": [
                                v.model_dump(mode="json")
                                for v in record.verifications
                            ],
                        },
                        "error": record.metadata.get("error")
                        or (
                            record.verifications[-1].message
                            if record.phase is SOPPhase.FAILED
                            and record.verifications
                            else None
                        ),
                        "started_at": started,
                        "finished_at": finished,
                        "input": (
                            run.input
                            if definition.order == 0
                            else projections[-1].output
                        ),
                    }
                )
            )
        status = (
            RunStatus.AWAITING_INPUT
            if engine.phase is SOPPhase.AWAITING
            else RunStatus.RUNNING
        )
        current = await self._store.update_run(
            run.user_id,
            run.id,
            engine="sop",
            sop_state=engine.state.model_dump(mode="json"),
            node_runs=projections,
            status=status,
            pending_input=pending,
        )
        await self._mark_task_run_status(current)
        if event is not None:
            node_id = event.value["node_id"]
            node = next(
                node for node in projections if node.node_id == node_id
            )
            if event.name == "TASK_STEP_STARTED":
                await self._emit(
                    current,
                    "step.started",
                    node_id=node_id,
                    payload={"step_type": node.type.value},
                )
            elif node.status is NodeRunStatus.AWAITING_INPUT:
                await self._emit(
                    current,
                    "input.requested",
                    node_id=node_id,
                    payload=pending.model_dump(mode="json"),
                )
                await self._emit(
                    current,
                    "run.awaiting_input",
                    node_id=node_id,
                    payload={"request_id": pending.id},
                )
            elif node.status is NodeRunStatus.SUCCEEDED:
                await self._emit(
                    current,
                    "step.succeeded",
                    node_id=node_id,
                    payload={
                        "summary": node.display_summary,
                        "step_type": node.type.value,
                    },
                )
            elif node.status is NodeRunStatus.PENDING:
                await self._emit(
                    current,
                    "step.rejected",
                    node_id=node_id,
                    payload={
                        "message": node.metadata["verifications"][-1][
                            "message"
                        ]
                    },
                )
        return current

    async def _build_artifact_spec(
        self,
        *,
        output: str,
        context: ExecutionContext,
        format: str,
    ) -> ArtifactSpec:
        """Use the bound AgentScope model to shape Office output when available."""

        fallback = spec_from_markdown(output)
        if format not in {"docx", "xlsx"}:
            return fallback
        if not (
            self._agentscope_executor
            and context.agent_id
            and context.session_id
        ):
            return fallback
        prompt = artifact_spec_prompt(output)
        planner = TaskNode(
            id=f"artifact-spec-{context.run_id}",
            name="规划文件结构",
            prompt=prompt,
            type=StepType.AGENT,
            config=AgentStepConfig(prompt=prompt),
            order=999,
        )
        try:
            result = await self._agentscope_executor.execute_node(
                node=planner,
                previous_output="",
                context=context,
            )
            normalized = _normalize_execution_result(result)
            return parse_agent_spec(normalized.text, output)
        except Exception:  # pylint: disable=broad-except
            # Artifact planning is an enhancement; the deterministic fallback
            # keeps the Task result available when a model call is unavailable.
            return fallback

    async def _store_artifact(
        self,
        *,
        run: TaskRunRecord,
        node: TaskNode,
        generated: GeneratedArtifact,
        context: ExecutionContext,
    ) -> TaskArtifactRecord:
        """Write generated bytes and return the persisted Task metadata."""

        if self._artifact_writer is None:
            raise RuntimeError(
                "生成文件产物需要绑定 AgentScope 会话和 Workspace。"
            )
        path = f"artifacts/{run.id}/{generated.name}"
        size_bytes = await self._artifact_writer.write_artifact(
            path=path,
            data=generated.data,
            context=context,
        )
        return TaskArtifactRecord(
            run_id=run.id,
            task_id=run.task_id,
            node_id=node.id,
            name=generated.name,
            format=generated.format,
            media_type=generated.media_type,
            path=path,
            size_bytes=size_bytes,
            preview_text=generated.preview_text,
        )

    def _select_executor(self, context: TaskContext) -> TaskExecutor:
        """Use AgentScope only when a real Chat/Session context is attached."""

        if (
            self._agentscope_executor is not None
            and context.agent_id
            and context.session_id
        ):
            return self._agentscope_executor
        return self._preview_executor

    async def _retrieve_knowledge(
        self,
        gateway: TaskKnowledgeGateway,
        run: TaskRunRecord,
        node: TaskNode,
        previous_output: str,
    ) -> str:
        """Resolve a node's effective KB scope without breaking execution."""

        if node.knowledge_base_mode == "disabled":
            return ""
        knowledge_base_ids = (
            node.knowledge_base_ids
            if node.knowledge_base_mode == "override"
            else run.knowledge_base_ids
        )
        if not knowledge_base_ids:
            return ""
        query = (
            f"任务节点：{node.name}\n"
            f"节点要求：{node.prompt}\n"
            f"上一步输出：{previous_output[-4000:]}"
        )
        try:
            return await gateway.retrieve(
                run.user_id,
                knowledge_base_ids,
                query,
            )
        except Exception:  # noqa: BLE001 — KB is an optional execution aid
            return ""

    async def _find_run(self, run_id: str) -> TaskRunRecord | None:
        """Find a run without knowing its owner for internal execution."""

        user_id = self._run_users.get(run_id)
        if user_id is None:
            return None
        return await self._store.get_run(user_id, run_id)

    def _is_cancel_requested(self, run_id: str) -> bool:
        """Return whether this local worker must preserve cancellation."""

        return run_id in self._cancel_requested_run_ids

    async def _mark_task_run_status(self, run: TaskRunRecord) -> None:
        """Reflect the latest run status on the reusable task summary."""

        task = await self._store.get_task(run.user_id, run.task_id)
        if task is not None and task.last_run_id == run.id:
            await self._store.save_task(
                task.model_copy(
                    update={
                        "last_run_id": run.id,
                        "last_run_status": run.status,
                    },
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


def _compact_text(value: str, limit: int = 240) -> str:
    """Create a short UI summary without an additional model call."""

    compact = " ".join(value.split())
    return compact if len(compact) <= limit else f"{compact[:limit]}…"


def _history_text(value: str, limit: int = 12_000) -> str:
    """Bound one prior result before exposing it to a later AgentStep."""

    text = str(value)
    return text if len(text) <= limit else f"{text[:limit]}…"


def _normalize_execution_result(
    value: StepExecutionResult | str,
) -> StepExecutionResult:
    """Keep custom TaskExecutor adapters source-compatible with the MVP."""

    if isinstance(value, StepExecutionResult):
        return value
    return StepExecutionResult(text=str(value))


def _artifact_node(nodes: list[TaskNode]) -> TaskNode | None:
    """Return the only final-node artifact configuration, if present."""

    configured = [node for node in nodes if node.artifact is not None]
    if not configured:
        return None
    if len(configured) != 1 or configured[0].id != nodes[-1].id:
        raise RuntimeError("文件产物只能配置在最后一个 Task 节点。")
    return configured[0]
