# -*- coding: utf-8 -*-
"""Pydantic contracts for the standalone Task module."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def _utc_now() -> datetime:
    """Return an aware UTC timestamp for task records."""

    return datetime.now(timezone.utc)


def _generate_id() -> str:
    """Generate an ID without depending on the AgentScope runtime."""

    return uuid4().hex


class TaskStatus(StrEnum):
    """Lifecycle of a reusable task definition."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class RunStatus(StrEnum):
    """Lifecycle of one task execution."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    TIMED_OUT = "timed_out"


class NodeRunStatus(StrEnum):
    """Lifecycle of one node within a task run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class TaskNode(BaseModel):
    """One configurable AI node in the V1 linear workflow."""

    id: str = Field(default_factory=_generate_id)
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=20_000)
    type: str = Field(default="ai", pattern="^ai$")
    order: int = Field(default=0, ge=0)


class TaskContext(BaseModel):
    """References carried through the task execution boundary."""

    source_chat_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    workspace_id: str | None = None


class ExecutionContext(TaskContext):
    """Execution identity passed to a TaskExecutor adapter."""

    user_id: str
    task_id: str
    run_id: str
    task_revision: int


class TaskRecord(BaseModel):
    """Reusable task definition owned by one user."""

    id: str = Field(default_factory=_generate_id)
    user_id: str
    title: str = Field(min_length=1, max_length=160)
    goal: str = Field(default="", max_length=20_000)
    nodes: list[TaskNode] = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    status: TaskStatus = TaskStatus.ACTIVE
    source_context: TaskContext = Field(default_factory=TaskContext)
    last_run_id: str | None = None
    last_run_status: RunStatus | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("nodes")
    @classmethod
    def normalize_node_order(cls, nodes: list[TaskNode]) -> list[TaskNode]:
        """Keep persisted order contiguous and deterministic."""

        return [node.model_copy(update={"order": index}) for index, node in enumerate(nodes)]


class NodeRunRecord(BaseModel):
    """A node definition snapshot plus its result for one run."""

    node_id: str
    name: str
    prompt: str
    order: int
    status: NodeRunStatus = NodeRunStatus.PENDING
    input: str = ""
    output: str = ""
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskRunRecord(BaseModel):
    """One execution snapshot; never mutate the task definition in place."""

    id: str = Field(default_factory=_generate_id)
    task_id: str
    user_id: str
    task_revision: int
    nodes: list[TaskNode]
    node_runs: list[NodeRunRecord]
    input: str = ""
    final_output: str = ""
    status: RunStatus = RunStatus.QUEUED
    error: str | None = None
    context: TaskContext = Field(default_factory=TaskContext)
    created_at: datetime = Field(default_factory=_utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskEventRecord(BaseModel):
    """Replayable lifecycle event for a task run."""

    id: str = Field(default_factory=_generate_id)
    type: str
    task_id: str
    run_id: str
    node_id: str | None = None
    sequence: int = Field(ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)


class CreateTaskRequest(BaseModel):
    """Request body for creating a reusable linear task."""

    title: str = Field(min_length=1, max_length=160)
    goal: str = Field(default="", max_length=20_000)
    nodes: list[TaskNode] = Field(min_length=1)
    source_context: TaskContext = Field(default_factory=TaskContext)


class UpdateTaskRequest(BaseModel):
    """Partial task update; omitted fields keep their current values."""

    title: str | None = Field(default=None, min_length=1, max_length=160)
    goal: str | None = Field(default=None, max_length=20_000)
    nodes: list[TaskNode] | None = Field(default=None, min_length=1)


class TaskRunRequest(BaseModel):
    """Request body for starting a run from a saved task definition."""

    input: str = Field(default="", max_length=50_000)
    context: TaskContext = Field(default_factory=TaskContext)
