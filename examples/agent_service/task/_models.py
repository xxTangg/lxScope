# -*- coding: utf-8 -*-
"""Pydantic contracts for the standalone Task module."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


def _utc_now() -> datetime:
    """Return an aware UTC timestamp for task records."""

    return datetime.now(timezone.utc)


def _generate_id() -> str:
    """Generate an ID without depending on the AgentScope runtime."""

    return uuid4().hex


class TaskStatus(StrEnum):
    """Lifecycle of a reusable task definition."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class TaskGenerationStatus(StrEnum):
    """Lifecycle of AI-generated task planning."""

    IDLE = "idle"
    GENERATING = "generating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


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


class StepType(StrEnum):
    """Supported Task step implementations."""

    AGENT = "agent"
    TOOL = "tool"
    PYTHON = "python"


class ArtifactFormat(StrEnum):
    """File formats a final Task node can materialize."""

    MARKDOWN = "markdown"
    DOCX = "docx"
    XLSX = "xlsx"


class ArtifactConfig(BaseModel):
    """Output-file settings owned by a Task node."""

    format: ArtifactFormat
    filename: str | None = Field(default=None, max_length=120)


class TaskArtifactRecord(BaseModel):
    """Metadata for one file materialized in the run's Workspace."""

    id: str = Field(default_factory=_generate_id)
    run_id: str
    task_id: str
    node_id: str
    name: str
    format: ArtifactFormat
    media_type: str
    path: str
    size_bytes: int = Field(ge=0)
    preview_text: str = ""
    created_at: datetime = Field(default_factory=_utc_now)

class AgentStepConfig(BaseModel):
    """Configuration for one AgentScope model step."""

    type: Literal["agent"] = "agent"
    prompt: str = Field(min_length=1, max_length=20_000)
    agent_id: str | None = None
    session_id: str | None = None


class ToolStepConfig(BaseModel):
    """Configuration for one AgentScope Toolkit/MCP invocation."""

    type: Literal["tool"] = "tool"
    tool_name: str = Field(min_length=1, max_length=200)
    arguments: dict[str, Any] = Field(default_factory=dict)


class PythonStepConfig(BaseModel):
    """Configuration for one Python program executed in the workspace."""

    type: Literal["python"] = "python"
    code: str = Field(min_length=1, max_length=50_000)
    timeout_seconds: int = Field(default=30, ge=1, le=300)


StepConfig = Annotated[
    AgentStepConfig | ToolStepConfig | PythonStepConfig,
    Field(discriminator="type"),
]


class TaskNode(BaseModel):
    """One typed step in the linear workflow.

    The prompt field and legacy ai type remain accepted so tasks created by
    the first MVP can be loaded and edited without a data migration. New
    clients should use type plus the typed config payload.
    """

    id: str = Field(default_factory=_generate_id)
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(default="", max_length=20_000)
    type: StepType = StepType.AGENT
    config: StepConfig
    order: int = Field(default=0, ge=0)
    artifact: ArtifactConfig | None = None
    knowledge_graph_enabled: bool = False
    knowledge_base_mode: Literal["inherit", "override", "disabled"] = "inherit"
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_payload(cls, value: Any) -> Any:
        """Translate the first MVP's type=ai shape into AgentStep."""

        if not isinstance(value, dict):
            return value
        payload = dict(value)
        step_type = payload.get("type", StepType.AGENT)
        if step_type == "ai":
            step_type = StepType.AGENT
        payload["type"] = step_type
        config = payload.get("config")
        if config is None:
            if step_type == StepType.AGENT:
                config = {
                    "type": StepType.AGENT,
                    "prompt": payload.get("prompt", ""),
                }
            elif step_type == StepType.TOOL:
                config = {
                    "type": StepType.TOOL,
                    "tool_name": payload.get("tool_name", ""),
                    "arguments": payload.get("arguments", {}),
                }
            else:
                config = {
                    "type": StepType.PYTHON,
                    "code": payload.get("code", ""),
                    "timeout_seconds": payload.get("timeout_seconds", 30),
                }
        elif isinstance(config, dict) and "type" not in config:
            config = {"type": step_type, **config}
        payload["config"] = config
        return payload

    @model_validator(mode="after")
    def validate_step_config(self) -> "TaskNode":
        """Keep the top-level type and typed configuration consistent."""

        if self.type.value != self.config.type:
            raise ValueError("Task node type does not match its config type.")
        if isinstance(self.config, AgentStepConfig):
            object.__setattr__(self, "prompt", self.config.prompt)
        return self


class TaskContext(BaseModel):
    """References carried through the task execution boundary."""

    source_chat_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    workspace_id: str | None = None


class TaskToolSchema(BaseModel):
    """Tool capability exposed to the Task editor for one session."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    is_mcp: bool = False
    is_read_only: bool = False


class TaskKnowledgeBaseOption(BaseModel):
    """Minimal knowledge-base projection used by the Task editor."""

    id: str
    name: str
    description: str = ""
    document_count: int = 0
    chunk_count: int = 0
    ready_document_count: int = 0


class TaskKnowledgeGraphResponse(BaseModel):
    """Bounded graph payload scoped to one Task's selected knowledge bases."""

    task_id: str
    knowledge_base_ids: list[str] = Field(default_factory=list)
    extraction_enabled: bool = False
    status: str = "empty"
    error: str | None = None
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0
    version: int = 0
    mode: str = "stored"
    matched_chunk_count: int = 0
    extracted_chunk_count: int = 0


class TaskKnowledgeGraphRebuildResponse(BaseModel):
    """Result of on-demand entity/relation extraction for a Task."""

    task_id: str
    knowledge_base_ids: list[str] = Field(default_factory=list)
    extraction_enabled: bool = False
    status: str = "empty"
    documents: int = 0
    skipped: int = 0
    reused: int = 0
    error: str | None = None


class TaskKnowledgeGraphRequest(BaseModel):
    """Optional preview selection for Task graph operations."""

    knowledge_base_ids: list[str] | None = Field(default=None, max_length=16)
    force_extract: bool = False


class TaskStepKnowledgeGraphRequest(BaseModel):
    """Optional controls for a Step-scoped, on-demand graph view."""

    query: str | None = Field(default=None, max_length=8_000)
    force_extract: bool = False


class ExecutionContext(TaskContext):
    """Execution identity passed to a TaskExecutor adapter."""

    user_id: str
    task_id: str
    run_id: str
    task_revision: int
    history: list[dict[str, Any]] = Field(default_factory=list)


class TaskRecord(BaseModel):
    """Reusable task definition owned by one user."""

    id: str = Field(default_factory=_generate_id)
    user_id: str
    title: str = Field(default="未命名任务", min_length=1, max_length=160)
    goal: str = Field(default="", max_length=20_000)
    nodes: list[TaskNode] = Field(default_factory=list)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=16)
    intent: str | None = Field(default=None, max_length=2_000)
    title_source: str = Field(default="auto", pattern="^(auto|user)$")
    revision: int = Field(default=1, ge=1)
    status: TaskStatus = TaskStatus.DRAFT
    generation_status: TaskGenerationStatus = TaskGenerationStatus.IDLE
    generation_error: str | None = None
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
    type: StepType = StepType.AGENT
    order: int
    status: NodeRunStatus = NodeRunStatus.PENDING
    input: str = ""
    output: str = ""
    display_summary: str = ""
    result: Any | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
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
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=16)
    input: str = ""
    final_output: str = ""
    final_summary: str = ""
    artifacts: list[TaskArtifactRecord] = Field(default_factory=list)
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


class TaskPlanNode(BaseModel):
    """One node returned by the task planner before it is persisted."""

    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(default="", max_length=20_000)
    type: StepType = StepType.AGENT
    config: StepConfig | None = None
    artifact: ArtifactConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_type(cls, value: Any) -> Any:
        """Accept the first planner schema's optional legacy ai value."""

        if isinstance(value, dict) and value.get("type") == "ai":
            return {**value, "type": StepType.AGENT}
        return value

    @model_validator(mode="after")
    def normalize_config(self) -> "TaskPlanNode":
        """Fill the legacy Agent shape and validate typed planner output."""

        if self.config is None:
            if self.type != StepType.AGENT:
                raise ValueError(
                    f"{self.type.value} planner nodes require a config.",
                )
            self.config = AgentStepConfig(prompt=self.prompt)
        if isinstance(self.config, AgentStepConfig):
            self.prompt = self.config.prompt
        else:
            self.prompt = ""
        return self


class TaskPlanDraft(BaseModel):
    """Structured output requested from the planner model."""

    suggested_title: str = Field(min_length=1, max_length=160)
    intent: str = Field(min_length=1, max_length=2_000)
    nodes: list[TaskPlanNode] = Field(min_length=1, max_length=8)


class CreateTaskRequest(BaseModel):
    """Request body for creating a reusable linear task."""

    title: str | None = Field(default=None, max_length=160)
    goal: str = Field(min_length=1, max_length=20_000)
    nodes: list[TaskNode] = Field(default_factory=list, max_length=8)
    source_context: TaskContext = Field(default_factory=TaskContext)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=16)


class UpdateTaskRequest(BaseModel):
    """Partial task update; omitted fields keep their current values."""

    title: str | None = Field(default=None, min_length=1, max_length=160)
    goal: str | None = Field(default=None, max_length=20_000)
    nodes: list[TaskNode] | None = Field(default=None, max_length=8)
    source_context: TaskContext | None = None
    knowledge_base_ids: list[str] | None = Field(default=None, max_length=16)


class GenerateTaskRequest(BaseModel):
    """Request body for generating nodes from a saved task goal."""

    context: TaskContext | None = None


class TaskRunRequest(BaseModel):
    """Request body for starting a run from a saved task definition."""

    input: str = Field(default="", max_length=50_000)
    context: TaskContext = Field(default_factory=TaskContext)
