# -*- coding: utf-8 -*-
"""Pydantic contracts for the standalone Task module."""

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from ._output_validation import validate_output_schema_definition


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


class TaskWorkflowMode(StrEnum):
    """Origin and editing mode of a Task workflow."""

    TEMPLATE_INSTANCE = "template_instance"
    CUSTOM_FLOW = "custom_flow"
    FREE_AI_PLANNER = "free_ai_planner"


class RunStatus(StrEnum):
    """Lifecycle of one task execution."""

    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    TIMED_OUT = "timed_out"


class NodeRunStatus(StrEnum):
    """Lifecycle of one node within a task run."""

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class TaskGraphEdge(BaseModel):
    """One explicit dependency in an opt-in Task DAG."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source_node_id: str = Field(min_length=1, max_length=80)
    target_node_id: str = Field(min_length=1, max_length=80)


class TaskGraphInputBinding(BaseModel):
    """Bind a named executor input to a completed ancestor node."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source_node_id: str = Field(min_length=1, max_length=80)
    source_value: Literal["output", "result"] = "output"


class TaskGraph(BaseModel):
    """Versioned graph contract frozen into each DAG run."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    edges: list[TaskGraphEdge] = Field(default_factory=list, max_length=56)
    input_bindings: dict[str, dict[str, TaskGraphInputBinding]] = Field(
        default_factory=dict,
        max_length=8,
    )
    max_concurrency: int = Field(default=1, ge=1, le=8)
    resource_limits: dict[str, int] = Field(default_factory=dict, max_length=32)

    @field_validator("resource_limits")
    @classmethod
    def validate_resource_limits(cls, limits: dict[str, int]) -> dict[str, int]:
        if any(
            not resource.strip()
            or len(resource) > 120
            or limit < 1
            for resource, limit in limits.items()
        ):
            raise ValueError("Resource names and limits must be valid and positive.")
        return limits

    @field_validator("input_bindings")
    @classmethod
    def validate_binding_names(
        cls,
        bindings: dict[str, dict[str, TaskGraphInputBinding]],
    ) -> dict[str, dict[str, TaskGraphInputBinding]]:
        for node_id, named_bindings in bindings.items():
            if not node_id.strip() or len(node_id) > 80:
                raise ValueError("Input binding target node id is invalid.")
            if len(named_bindings) > 32 or any(
                not name.strip() or len(name) > 120 for name in named_bindings
            ):
                raise ValueError("Input binding names must be bounded and non-empty.")
        return bindings


TaskErrorCategory = Literal[
    "configuration_error",
    "missing_tool",
    "permission_denied",
    "data_source_not_ready",
    "schema_mismatch",
    "input_incomplete",
    "citation_verification_failed",
    "external_interface_failed",
    "artifact_generation_failed",
    "execution_failed",
]


class TaskErrorDetail(BaseModel):
    """Backward-compatible, locatable classification for a Task failure."""

    category: TaskErrorCategory
    code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=4_000)
    task_id: str = Field(min_length=1, max_length=80)
    run_id: str = Field(min_length=1, max_length=80)
    node_id: str | None = Field(default=None, max_length=80)
    details: dict[str, Any] = Field(default_factory=dict, max_length=32)
    occurred_at: datetime = Field(default_factory=_utc_now)


class StepType(StrEnum):
    """Supported Task step implementations."""

    AGENT = "agent"
    TOOL = "tool"
    PYTHON = "python"
    INPUT = "input"


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
    delivery_status: Literal["strict_verified", "draft_unverified"] | None = None
    created_at: datetime = Field(default_factory=_utc_now)


class TaskInputQuestion(BaseModel):
    """A business-data question; options are suggestions, free text is allowed."""

    key: str = Field(min_length=1, max_length=80, pattern=r"^[\w.-]+$")
    label: str = Field(min_length=1, max_length=500)
    required: bool = True
    options: list[str] = Field(default_factory=list, max_length=8)


class InformationCondition(BaseModel):
    """A bounded, deterministic condition for a conditional requirement."""

    model_config = ConfigDict(extra="forbid", strict=True)

    field: str = Field(min_length=1, max_length=120, pattern=r"^[\w.-]+$")
    operator: Literal["equals", "not_equals", "in", "present"]
    value: Any | None = None

    @model_validator(mode="after")
    def validate_value(self) -> "InformationCondition":
        if self.operator == "in":
            if not isinstance(self.value, list) or not self.value:
                raise ValueError("The 'in' condition requires a non-empty list.")
        elif self.operator in {"equals", "not_equals"}:
            if self.value is None or isinstance(self.value, (dict, list)):
                raise ValueError("Equality conditions require a scalar value.")
        elif self.value is not None:
            raise ValueError("The 'present' condition does not accept a value.")
        return self


class TaskInformationRequirement(BaseModel):
    """A versioned, Task-owned declaration of information required by a node."""

    model_config = ConfigDict(extra="forbid", strict=True)

    key: str = Field(min_length=1, max_length=80, pattern=r"^[\w.-]+$")
    label: str = Field(min_length=1, max_length=500)
    classification: Literal[
        "required",
        "conditional_required",
        "optional",
        "pending_verification",
        "human_confirmation",
    ]
    reason: str = Field(default="", max_length=1_000)
    condition: InformationCondition | None = None

    @model_validator(mode="after")
    def validate_condition(self) -> "TaskInformationRequirement":
        if self.classification == "conditional_required" and self.condition is None:
            raise ValueError(
                "conditional_required needs an explicit deterministic condition."
            )
        if self.classification != "conditional_required" and self.condition is not None:
            raise ValueError(
                "Only conditional_required may declare a condition."
            )
        return self


class TaskInputRequest(BaseModel):
    """Persisted correlation between a question batch and its parked step."""

    id: str = Field(default_factory=_generate_id)
    node_id: str
    reason: str = Field(default="", max_length=2_000)
    questions: list[TaskInputQuestion] = Field(min_length=1, max_length=16)
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("questions")
    @classmethod
    def unique_keys(
        cls, questions: list[TaskInputQuestion]
    ) -> list[TaskInputQuestion]:
        """Keep answers unambiguous."""
        if len({question.key for question in questions}) != len(questions):
            raise ValueError("Question keys must be unique.")
        return questions


class TaskInputReply(BaseModel):
    """Answer exactly one outstanding question batch."""

    request_id: str = Field(min_length=1, max_length=80)
    answers: dict[str, str] = Field(min_length=1, max_length=16)

    @field_validator("answers")
    @classmethod
    def bounded_answers(cls, answers: dict[str, str]) -> dict[str, str]:
        """Bound user data before it reaches model context or persistence."""
        if any(
            len(key) > 80 or len(value) > 20_000
            for key, value in answers.items()
        ):
            raise ValueError("An answer is too long.")
        return {key: value.strip() for key, value in answers.items()}


class TaskRunControlRequest(BaseModel):
    """Idempotent, version-checked command for a durable Task Run."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=80)
    expected_run_version: int = Field(ge=0)
    side_effects_verified: bool = False


class InputStepConfig(BaseModel):
    """An explicit data-collection milestone in a reusable SOP."""

    type: Literal["input"] = "input"
    reason: str = Field(default="请补充本步骤需要的信息。", max_length=2_000)
    questions: list[TaskInputQuestion] = Field(min_length=1, max_length=16)

    @field_validator("questions")
    @classmethod
    def unique_keys(
        cls, questions: list[TaskInputQuestion]
    ) -> list[TaskInputQuestion]:
        return TaskInputRequest.unique_keys(questions)


class AgentStepOutcome(BaseModel):
    """The model must explicitly distinguish a result from a request for input."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["succeeded", "needs_input", "failed"]
    output: str = Field(default="", max_length=100_000)
    reason: str = Field(default="", max_length=2_000)
    questions: list[TaskInputQuestion] = Field(
        default_factory=list, max_length=16
    )

    @model_validator(mode="after")
    def validate_outcome(self) -> "AgentStepOutcome":
        if self.status == "needs_input":
            if not self.questions:
                raise ValueError("needs_input requires questions.")
            TaskInputRequest.unique_keys(self.questions)
        elif self.questions:
            raise ValueError("Only needs_input may contain questions.")
        if self.status == "succeeded" and not self.output.strip():
            raise ValueError("succeeded requires an actual result.")
        if self.status == "failed" and not (self.output.strip() or self.reason.strip()):
            raise ValueError("failed requires a concrete failure reason.")
        return self


class AgentStepOutcomeCandidate(BaseModel):
    """Bounded wire shape that lets Task inspect status/question mismatches.

    AgentScope validates structured output before returning it to Task. The
    wire shape permits status/question mismatches and malformed question
    contents to reach Task, where they can be rejected without losing a real
    failure reason. The outer list and question count remain bounded. Every
    candidate is revalidated against ``AgentStepOutcome`` before use.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["succeeded", "needs_input", "failed"]
    output: str = Field(default="", max_length=100_000)
    reason: str = Field(default="", max_length=2_000)
    questions: list[Any] = Field(default_factory=list, max_length=16)


class StepVerificationIssue(BaseModel):
    """An AI verifier observation linked to an exact frozen contract source."""

    model_config = ConfigDict(extra="forbid")

    category: Literal[
        "contract_violation",
        "pending_verification",
        "suggestion",
        "human_confirmation_required",
    ]
    message: str = Field(min_length=1, max_length=1_000)
    source_path: str | None = Field(default=None, max_length=300)
    contract_quote: str = Field(default="", max_length=1_000)


class StepVerdict(BaseModel):
    """Structured business acceptance, separate from successful execution."""

    passed: bool
    message: str = ""
    retryable: bool = True
    issues: list[StepVerificationIssue] = Field(default_factory=list, max_length=16)


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
    AgentStepConfig | ToolStepConfig | PythonStepConfig | InputStepConfig,
    Field(discriminator="type"),
]


class NodeOutputContract(BaseModel):
    """Optional, versioned contract for a Task node's persisted output."""

    model_config = ConfigDict(
        extra="forbid", strict=True, populate_by_name=True
    )

    contract_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_.-]{1,119}$",
    )
    contract_version: Literal[1] = 1
    output_type: Literal[
        "text",
        "markdown",
        "structured_json",
        "business_result",
        "evidence_claims",
        "artifact_descriptor",
    ]
    json_schema: dict[str, Any] | None = Field(
        default=None, alias="schema", serialization_alias="schema"
    )
    validation_mode: Literal[
        "none",
        "json_schema",
        "citations",
        "claim_evidence",
    ] = "none"
    provenance_requirement: Literal[
        "none", "authorized_run_snapshot"
    ] = "none"
    downstream_binding: Literal["text_only", "validated_result"] = "text_only"
    failure_policy: Literal["fail_closed"] = "fail_closed"

    @model_validator(mode="after")
    def validate_contract(self) -> "NodeOutputContract":
        if self.json_schema is not None:
            validate_output_schema_definition(self.json_schema)
        if self.validation_mode == "json_schema" and self.json_schema is None:
            raise ValueError("json_schema validation requires a schema.")
        if (
            self.downstream_binding == "validated_result"
            and self.validation_mode != "json_schema"
        ):
            raise ValueError(
                "validated_result binding requires JSON Schema validation."
            )
        if self.provenance_requirement == "authorized_run_snapshot" and (
            self.validation_mode not in {"citations", "claim_evidence"}
        ):
            raise ValueError(
                "authorized provenance requires citations or claim_evidence validation."
            )
        if self.validation_mode == "citations" and self.output_type not in {
            "text",
            "markdown",
        }:
            raise ValueError(
                "citations validation requires a text or markdown output."
            )
        if self.validation_mode in {"citations", "claim_evidence"} and (
            self.provenance_requirement != "authorized_run_snapshot"
        ):
            raise ValueError(
                "citations and claim_evidence validation require an authorized run snapshot."
            )
        if self.validation_mode in {"citations", "claim_evidence"} and (
            self.json_schema is not None
        ):
            raise ValueError(
                "citation validation modes do not accept a JSON Schema."
            )
        return self


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
    capability_key: str | None = Field(default=None, max_length=120)
    artifact: ArtifactConfig | None = None
    output_schema: dict[str, Any] | None = None
    output_contract: NodeOutputContract | None = None
    information_requirements: list[TaskInformationRequirement] = Field(
        default_factory=list,
        max_length=32,
    )
    knowledge_graph_enabled: bool = False
    knowledge_base_mode: Literal["inherit", "override", "disabled"] = "inherit"
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=16)
    acceptance_criteria: str = Field(default="", max_length=4_000)
    max_attempts: int = Field(default=3, ge=1, le=10)
    resource_requirements: dict[str, int] = Field(
        default_factory=dict,
        max_length=32,
    )

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
            elif step_type == StepType.INPUT:
                config = {
                    "type": StepType.INPUT,
                    "questions": payload.get("questions", []),
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
        if self.output_schema is not None:
            validate_output_schema_definition(self.output_schema)
        if (
            self.output_contract is not None
            and self.output_contract.validation_mode
            in {"citations", "claim_evidence"}
            and self.type is not StepType.AGENT
        ):
            raise ValueError(
                "Citation output contracts are supported on Agent steps only."
            )
        if (
            self.output_schema is not None
            and self.output_contract is not None
            and self.output_contract.json_schema is not None
            and self.output_schema != self.output_contract.json_schema
        ):
            raise ValueError(
                "output_schema and output_contract.schema must match."
            )
        if isinstance(self.config, AgentStepConfig):
            object.__setattr__(self, "prompt", self.config.prompt)
        if any(
            not resource.strip()
            or len(resource) > 120
            or amount < 1
            for resource, amount in self.resource_requirements.items()
        ):
            raise ValueError(
                "Resource requirements must use valid names and positive amounts."
            )
        if len({item.key for item in self.information_requirements}) != len(
            self.information_requirements
        ):
            raise ValueError("Task information requirement keys must be unique.")
        return self

    @property
    def effective_output_contract(self) -> NodeOutputContract | None:
        """Return the explicit contract or a compatibility view of v1 schema."""

        if self.output_contract is not None:
            return self.output_contract
        if self.output_schema is None:
            return None
        return NodeOutputContract(
            contract_id="task.output-schema",
            contract_version=1,
            output_type="structured_json",
            schema=self.output_schema,
            validation_mode="json_schema",
            downstream_binding="validated_result",
        )


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
    read_only_hint: bool | None = None


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
    source_template_id: str | None = Field(default=None, max_length=80)
    source_template_version: str | None = Field(
        default=None,
        pattern=r"^\d+\.\d+\.\d+$",
    )
    history: list[dict[str, Any]] = Field(default_factory=list)
    inputs_by_node_id: dict[str, Any] = Field(default_factory=dict)
    named_inputs: dict[str, Any] = Field(default_factory=dict)
    previous_result: Any | None = None
    initial_input: str = ""
    user_answers: list[TaskInputReply] = Field(default_factory=list)
    clarification_state: dict[str, Any] = Field(default_factory=dict)
    information_contract: dict[str, Any] = Field(default_factory=dict)
    clarification_rounds: int = Field(default=0, ge=0)
    verification_feedback: str = ""
    knowledge_context: str = ""


class TaskPlanningAuditRecord(BaseModel):
    """One reviewable planner lifecycle event attached to a Task."""

    planning_id: str = Field(min_length=1, max_length=80)
    plan_version: int = Field(ge=1)
    action: Literal["proposed", "confirmed", "edited_and_confirmed"]
    actor_user_id: str = Field(min_length=1, max_length=160)
    recommended_mode: Literal["sop", "dag"] | None = None
    validation_status: Literal["pending", "valid", "invalid"] | None = None
    created_at: datetime = Field(default_factory=_utc_now)


class TaskRecord(BaseModel):
    """Reusable task definition owned by one user."""

    id: str = Field(default_factory=_generate_id)
    user_id: str
    title: str = Field(default="未命名任务", min_length=1, max_length=160)
    goal: str = Field(default="", max_length=20_000)
    nodes: list[TaskNode] = Field(default_factory=list)
    graph: TaskGraph | None = None
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=16)
    intent: str | None = Field(default=None, max_length=2_000)
    title_source: str = Field(default="auto", pattern="^(auto|user)$")
    revision: int = Field(default=1, ge=1)
    status: TaskStatus = TaskStatus.DRAFT
    generation_status: TaskGenerationStatus = TaskGenerationStatus.IDLE
    generation_error: str | None = None
    source_context: TaskContext = Field(default_factory=TaskContext)
    # Optional for compatibility with Task records persisted before P6-C-1.
    workflow_mode: TaskWorkflowMode | None = None
    source_template_id: str | None = Field(default=None, max_length=80)
    source_template_version: str | None = Field(
        default=None,
        pattern=r"^\d+\.\d+\.\d+$",
    )
    template_step_bindings: dict[str, str] = Field(default_factory=dict)
    template_inputs: dict[str, Any] | None = None
    source_task_id: str | None = Field(default=None, max_length=80)
    source_task_revision: int | None = Field(default=None, ge=1)
    planning_decision: dict[str, Any] | None = None
    planning_version: int = Field(default=0, ge=0)
    planning_history: list[dict[str, Any]] = Field(default_factory=list, max_length=64)
    planning_audit: list[TaskPlanningAuditRecord] = Field(default_factory=list, max_length=128)
    planning_goal_fingerprint: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    planning_goal_snapshot: str | None = Field(default=None, max_length=20_000)
    planning_confirmed_id: str | None = Field(default=None, max_length=80)
    planning_confirmed_version: int | None = Field(default=None, ge=1)
    planning_confirmed_fingerprint: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    planning_confirmed_at: datetime | None = None
    last_run_id: str | None = None
    last_run_status: RunStatus | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("nodes")
    @classmethod
    def normalize_node_order(cls, nodes: list[TaskNode]) -> list[TaskNode]:
        """Keep persisted order contiguous and deterministic."""

        return [
            node.model_copy(update={"order": index})
            for index, node in enumerate(nodes)
        ]


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
    # Exact node-scoped checks captured by Task Preflight when this Run was
    # created. Run-level checks are deliberately not copied to every node.
    security_reviews: list[dict[str, Any]] = Field(default_factory=list)
    attempt: int = Field(default=0, ge=0)
    execution_epoch: int = Field(default=0, ge=0)
    attempt_history: list["TaskNodeAttemptRecord"] = Field(
        default_factory=list,
        max_length=128,
    )
    error: str | None = None
    error_detail: TaskErrorDetail | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskNodeAttemptRecord(BaseModel):
    """Immutable outcome snapshot for one automatic or manual attempt."""

    attempt: int = Field(ge=1)
    source: Literal["automatic", "manual"] = "automatic"
    status: Literal["succeeded", "failed", "canceled"]
    input: str = ""
    output: str = ""
    result: Any | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    error_detail: TaskErrorDetail | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


NodeRunRecord.model_rebuild()


class TaskEventDraft(BaseModel):
    """Event payload accepted by an atomic run transition."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=64)
    node_id: str | None = Field(default=None, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskRunRecord(BaseModel):
    """One execution snapshot; never mutate the task definition in place."""

    id: str = Field(default_factory=_generate_id)
    task_id: str
    user_id: str
    task_revision: int
    nodes: list[TaskNode]
    node_runs: list[NodeRunRecord]
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=16)
    source_material_ids: list[str] = Field(
        default_factory=list,
        max_length=128,
    )
    input: str = ""
    final_output: str = ""
    final_summary: str = ""
    artifacts: list[TaskArtifactRecord] = Field(default_factory=list)
    status: RunStatus = RunStatus.QUEUED
    run_version: int = Field(default=0, ge=0)
    event_sequence: int = Field(default=0, ge=0)
    control_mode: Literal[
        "continuous",
        "pause_requested",
        "paused",
        "single_step",
        "stop_requested",
        "stopped",
    ] = "continuous"
    owner_token: str | None = Field(default=None, max_length=160)
    lease_expires_at: datetime | None = None
    fence_epoch: int = Field(default=0, ge=0)
    processed_command_ids: list[str] = Field(default_factory=list, max_length=512)
    error: str | None = None
    error_detail: TaskErrorDetail | None = None
    context: TaskContext = Field(default_factory=TaskContext)
    created_at: datetime = Field(default_factory=_utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    engine: Literal["legacy", "sop", "dag"] = "legacy"
    graph: TaskGraph | None = None
    # Optional provenance fields keep previously stored TaskRuns readable.
    workflow_mode: TaskWorkflowMode | None = None
    source_template_id: str | None = Field(default=None, max_length=80)
    source_template_version: str | None = Field(
        default=None,
        pattern=r"^\d+\.\d+\.\d+$",
    )
    source_task_id: str | None = Field(default=None, max_length=80)
    source_task_revision: int | None = Field(default=None, ge=1)
    planning_id: str | None = Field(default=None, max_length=80)
    planning_version: int | None = Field(default=None, ge=1)
    planning_snapshot: dict[str, Any] | None = None
    node_snapshot_fingerprint: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    sop_state: dict[str, Any] | None = None
    pending_input: TaskInputRequest | None = None
    delivery_mode: Literal[
        "strict_verified", "demo_draft", "lightweight_draft"
    ] = "strict_verified"
    delivery_status: Literal["strict_verified", "draft_unverified"] | None = None
    verification_report: dict[str, Any] = Field(default_factory=dict)


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
    output_contract: NodeOutputContract | None = None
    acceptance_criteria: str = Field(default="", max_length=4_000)
    max_attempts: int = Field(default=3, ge=1, le=10)

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
    graph: TaskGraph | None = None
    source_context: TaskContext = Field(default_factory=TaskContext)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=16)


class TaskCustomFlowCopyRequest(BaseModel):
    """Choose the starting execution mode for a template-derived copy."""

    mode: Literal["sop", "dag"] = "sop"


class UpdateTaskRequest(BaseModel):
    """Partial task update; omitted fields keep their current values."""

    title: str | None = Field(default=None, min_length=1, max_length=160)
    goal: str | None = Field(default=None, max_length=20_000)
    nodes: list[TaskNode] | None = Field(default=None, max_length=8)
    graph: TaskGraph | None = None
    source_context: TaskContext | None = None
    knowledge_base_ids: list[str] | None = Field(default=None, max_length=16)


class TaskValidationIssue(BaseModel):
    """One field-specific Task validation finding."""

    path: str = Field(min_length=1, max_length=500)
    message: str = Field(min_length=1, max_length=2_000)


class TaskValidationRequest(BaseModel):
    """Validate proposed Task nodes before saving them."""

    task_id: str | None = Field(default=None, min_length=1, max_length=80)
    nodes: list[TaskNode] | None = Field(default=None, max_length=8)
    graph: TaskGraph | None = None
    context: TaskContext | None = None

    @model_validator(mode="after")
    def require_task_or_nodes(self) -> "TaskValidationRequest":
        if self.task_id is None and self.nodes is None:
            raise ValueError("task_id or nodes must be provided.")
        return self


class TaskPreflightRequest(BaseModel):
    """Read-only check before a Task run is created."""

    input: str = Field(default="", max_length=50_000)
    context: TaskContext = Field(default_factory=TaskContext)


class TaskValidationResponse(BaseModel):
    """Result shared by save validation and run preflight routes."""

    task_id: str | None = None
    valid: bool
    ready: bool | None = None
    issues: list[TaskValidationIssue] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    blocked_capabilities: list[str] = Field(default_factory=list)
    capability_checks: list["TaskCapabilityCheck"] = Field(default_factory=list)


class TaskDagScenarioExecutionScope(BaseModel):
    """Exact frozen template versions allowed by Task DAG execution policy."""

    template_id: str = Field(min_length=1, max_length=80)
    template_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")


class TaskDagCapabilitiesResponse(BaseModel):
    """Task-local authoring, execution and linear control capabilities."""

    editing_enabled: bool
    execution_enabled: bool
    sop_control_enabled: bool = True
    sop_node_retry_enabled: bool = True
    scenario_execution_allowlist: list[TaskDagScenarioExecutionScope] = Field(
        default_factory=list,
    )


class TaskCapabilityCheck(BaseModel):
    """Read-only dimensions inspected for one required Task capability."""

    step_id: str | None = None
    step_name: str
    capability_key: str
    category: str
    tool_name: str | None = None
    tool_available: bool | None = None
    interface_compatible: bool | None = None
    permission_status: Literal[
        "allowed",
        "denied",
        "requires_runtime_check",
        "requires_confirmation",
        "not_applicable",
        "unavailable",
    ] = "unavailable"
    business_data_verified: bool | None = None
    external_side_effect: bool = False
    status: Literal[
        "available",
        "missing",
        "incompatible",
        "permission_denied",
        "unsupported",
        "unverified",
    ]
    message: str
    error_category: TaskErrorCategory | None = None
    error_code: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def classify_unavailable_capability(self) -> "TaskCapabilityCheck":
        """Expose a stable error code for every blocking Preflight result."""

        if self.status == "available" or self.error_category is not None:
            return self
        if self.status == "missing":
            self.error_category = "missing_tool"
            self.error_code = self.error_code or "TASK_TOOL_MISSING"
        elif self.status == "incompatible":
            self.error_category = "schema_mismatch"
            self.error_code = self.error_code or "TASK_TOOL_SCHEMA_MISMATCH"
        elif self.status == "permission_denied":
            self.error_category = "permission_denied"
            self.error_code = self.error_code or "TASK_PERMISSION_DENIED"
        elif self.status == "unsupported":
            self.error_category = "configuration_error"
            self.error_code = self.error_code or "TASK_CAPABILITY_BLOCKED"
        elif self.business_data_verified is False:
            self.error_category = "data_source_not_ready"
            self.error_code = self.error_code or "TASK_DATA_SOURCE_UNVERIFIED"
        elif self.permission_status != "allowed":
            self.error_category = "permission_denied"
            self.error_code = self.error_code or "TASK_PERMISSION_UNVERIFIED"
        else:
            self.error_category = "external_interface_failed"
            self.error_code = self.error_code or "TASK_CAPABILITY_UNVERIFIED"
        return self


class TaskValidationErrorDetail(BaseModel):
    """Structured failure returned when save or run validation blocks."""

    code: str = "TASK_VALIDATION_FAILED"
    error_category: TaskErrorCategory = "configuration_error"
    message: str = "Task 校验未通过。"
    issues: list[TaskValidationIssue] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    blocked_capabilities: list[str] = Field(default_factory=list)
    capability_checks: list["TaskCapabilityCheck"] = Field(default_factory=list)


class GenerateTaskRequest(BaseModel):
    """Request body for generating nodes from a saved task goal."""

    context: TaskContext | None = None


class TaskRunRequest(BaseModel):
    """Request body for starting a run from a saved task definition."""

    input: str = Field(default="", max_length=50_000)
    context: TaskContext = Field(default_factory=TaskContext)
    delivery_mode: Literal[
        "strict_verified", "demo_draft", "lightweight_draft"
    ] = "strict_verified"
