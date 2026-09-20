# -*- coding: utf-8 -*-
"""Application service for reusable linear Tasks."""

import asyncio
from datetime import datetime, timezone

from ._artifact_spec import ArtifactSpec, artifact_spec_prompt, parse_agent_spec, spec_from_markdown
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
    StepType,
    TaskStatus,
    UpdateTaskRequest,
)
from ._planner import PreviewTaskPlanner, TaskPlanner
from ._plan_validation import format_plan_issues, validate_task_plan
from ._store import TaskStoreProtocol


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
        store: TaskStoreProtocol,
        *,
        agentscope_executor: TaskExecutor | None = None,
        preview_executor: TaskExecutor | None = None,
        planner: TaskPlanner | None = None,
        artifact_writer: TaskArtifactWriter | None = None,
    ) -> None:
        """Bind repository and execution adapters."""

        self._store = store
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
        self._active_runs: dict[str, asyncio.Task[None]] = {}
        self._run_users: dict[str, str] = {}

    async def create_task(self, user_id: str, body: CreateTaskRequest) -> TaskRecord:
        """Create a draft or manually-defined reusable task."""

        has_nodes = bool(body.nodes)
        record = TaskRecord(
            user_id=user_id,
            title=(body.title or "").strip() or "未命名任务",
            goal=body.goal.strip(),
            nodes=body.nodes,
            title_source="user" if body.title and body.title.strip() else "auto",
            status=TaskStatus.ACTIVE if has_nodes else TaskStatus.DRAFT,
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
            context
            if context is not None
            else current.source_context
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
                tool_schemas = await self.list_tools(user_id, merged_context)
            except Exception:  # pylint: disable=broad-except
                # Tool discovery is an enhancement for planning. A broken
                # optional MCP must not prevent Agent/Python planning.
                tool_schemas = []
            draft = await self._planner.plan(
                user_id=user_id,
                goal=current.goal,
                context=merged_context,
                current_title=(
                    current.title if current.title_source == "user" else None
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
        if list_tools is None or not context.agent_id or not context.session_id:
            return []
        execution_context = ExecutionContext(
            **context.model_dump(),
            user_id=user_id,
            task_id="task-tool-catalog",
            run_id="task-tool-catalog",
            task_revision=0,
        )
        return await list_tools(execution_context)

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
    ) -> TaskRunRecord | None:
        """Snapshot a task and schedule one asynchronous execution."""

        task = await self._store.get_task(user_id, task_id)
        if (
            task is None
            or task.status != TaskStatus.ACTIVE
            or not task.nodes
        ):
            return None
        context = task.source_context.model_copy(
            update=body.context.model_dump(exclude_none=True),
        )
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
                    type=node.type,
                    order=node.order,
                )
                for node in task.nodes
            ],
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
            raise RuntimeError("Task artifact Workspace storage is unavailable.")
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
            execution_history: list[dict[str, object]] = []
            for node in run.nodes:
                run = await self._store.update_node_run(
                    run.user_id,
                    run.id,
                    node.id,
                    status=NodeRunStatus.RUNNING,
                    input=previous_output,
                    started_at=_utc_now(),
                )
                await self._emit(
                    run,
                    "step.started",
                    node_id=node.id,
                    payload={"step_type": node.type.value},
                )
                context = ExecutionContext(
                    **run.context.model_dump(),
                    user_id=run.user_id,
                    task_id=run.task_id,
                    run_id=run.id,
                    task_revision=run.task_revision,
                    history=execution_history,
                )
                executor = self._select_executor(run.context)
                execution_result = await executor.execute_node(
                    node=node,
                    previous_output=previous_output,
                    context=context,
                )
                normalized = _normalize_execution_result(execution_result)
                previous_output = normalized.text
                execution_history.append(
                    {
                        "node_id": node.id,
                        "name": node.name,
                        "type": node.type.value,
                        "output": _history_text(normalized.text),
                        "metadata": normalized.metadata or {},
                    },
                )
                run = await self._store.update_node_run(
                    run.user_id,
                    run.id,
                    node.id,
                    status=NodeRunStatus.SUCCEEDED,
                    output=normalized.text,
                    display_summary=_compact_text(normalized.text),
                    result=normalized.result,
                    metadata=normalized.metadata or {},
                    finished_at=_utc_now(),
                )
                await self._emit(
                    run,
                    "step.succeeded",
                    node_id=node.id,
                    payload={
                        "summary": _compact_text(normalized.text),
                        "step_type": node.type.value,
                        **(normalized.metadata or {}),
                    },
                )

            artifacts: list[TaskArtifactRecord] = []
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
        if not (self._agentscope_executor and context.agent_id and context.session_id):
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
