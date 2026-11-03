# -*- coding: utf-8 -*-
"""Task-owned contracts for planning candidates and reviewed decisions.

The model-facing candidate intentionally excludes validation and authorization
fields. Those are populated only by the deterministic Task planning layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ._models import (
    TaskDagScenarioExecutionScope,
    TaskContext,
    TaskGraph,
    TaskGraphEdge,
    TaskGraphInputBinding,
    TaskNode,
    TaskRecord,
    TaskToolSchema,
    TaskValidationResponse,
)
from ._template_models import TaskTemplateDefinition

if TYPE_CHECKING:
    from ._template_compiler import CompiledTemplateNodes


class TaskPlanningNode(TaskNode):
    """A Task-schema node whose stable id is required from the planner."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)


class TaskPlanningCandidate(BaseModel):
    """Untrusted structured output requested from the existing session model."""

    model_config = ConfigDict(extra="forbid")

    suggested_title: str = Field(min_length=1, max_length=160)
    intent: str = Field(min_length=1, max_length=2_000)
    recommended_mode: Literal["sop", "dag"]
    reason: str = Field(min_length=1, max_length=2_000)
    nodes: list[TaskPlanningNode] = Field(min_length=1, max_length=8)
    edges: list[TaskGraphEdge] = Field(default_factory=list, max_length=56)
    input_bindings: dict[str, dict[str, TaskGraphInputBinding]] = Field(
        default_factory=dict,
        max_length=8,
    )


class TaskPlanningRequest(BaseModel):
    """Context used to propose a plan for an owned Task draft."""

    model_config = ConfigDict(extra="forbid")

    context: TaskContext | None = None


class TaskPlanningConfirmRequest(BaseModel):
    """User confirmation plus optional edited Task-schema content."""

    model_config = ConfigDict(extra="forbid")

    planning_id: str = Field(min_length=1, max_length=80)
    plan_version: int = Field(ge=1)
    nodes: list[TaskPlanningNode] | None = Field(default=None, max_length=8)
    graph: TaskGraph | None = None
    title: str | None = Field(default=None, max_length=160)
    input: str = Field(default="", max_length=50_000)
    context: TaskContext = Field(default_factory=TaskContext)


class TaskPlanningResponse(BaseModel):
    """Persisted candidate returned to the Task page for human review."""

    task: TaskRecord
    plan: "TaskPlanningDecision"
    plan_version: int = Field(ge=1)


class TaskPlanningConfirmResponse(BaseModel):
    """Saved confirmed plan plus the current read-only Run preflight."""

    task: TaskRecord
    plan: "TaskPlanningDecision"
    plan_version: int = Field(ge=1)
    preflight: TaskValidationResponse


class TaskPlanningTemplateContract(BaseModel):
    """Server-owned frozen source used when planning from an official template."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    template: TaskTemplateDefinition
    step_bindings: dict[str, str]
    frozen_nodes: list[TaskNode]

    @classmethod
    def from_compiled(
        cls,
        template: TaskTemplateDefinition,
        compiled: CompiledTemplateNodes,
    ) -> "TaskPlanningTemplateContract":
        """Capture a registry-loaded template and its canonical compiled nodes."""

        return cls(
            template=template,
            step_bindings=dict(compiled.step_bindings),
            frozen_nodes=[node.model_copy(deep=True) for node in compiled.nodes],
        )

    @model_validator(mode="after")
    def validate_frozen_mapping(self) -> "TaskPlanningTemplateContract":
        step_keys = [step.step_key for step in self.template.steps]
        node_ids = [node.id for node in self.frozen_nodes]
        if set(self.step_bindings) != set(step_keys):
            raise ValueError("Frozen template step bindings do not match its steps.")
        if [self.step_bindings[key] for key in step_keys] != node_ids:
            raise ValueError("Frozen template bindings do not preserve node order.")
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Frozen template node ids must be unique.")
        return self

    @classmethod
    def from_official_template(
        cls,
        template_id: str,
        template_version: str,
        tool_schemas: list[TaskToolSchema],
    ) -> "TaskPlanningTemplateContract":
        """Resolve an exact registry version and compile its frozen Task nodes."""

        from ._template_compiler import TaskTemplateCompiler
        from ._template_registry import TemplateRegistry

        template = TemplateRegistry().get_template_version(
            template_id,
            template_version,
        )
        if template is None:
            raise ValueError("The requested official template version is unknown.")
        compiled = TaskTemplateCompiler.from_environment().compile_nodes(
            template,
            tool_schemas,
            require_tool_bindings=False,
        )
        return cls.from_compiled(template, compiled)

    @property
    def source_template_id(self) -> str:
        return self.template.template_id

    @property
    def source_template_version(self) -> str:
        return self.template.template_version


class TaskPlanningRuntimePolicy(BaseModel):
    """Server-supplied resource and execution authorization snapshot."""

    model_config = ConfigDict(extra="forbid")

    sop_execution_enabled: bool = False
    dag_execution_enabled: bool = False
    authorized_tool_node_ids: list[str] = Field(default_factory=list, max_length=8)
    scenario_execution_allowlist: list[TaskDagScenarioExecutionScope] = Field(
        default_factory=list,
    )
    max_concurrency_limit: int = Field(default=1, ge=1, le=8)
    resource_limits: dict[str, int] = Field(default_factory=dict, max_length=32)

    @field_validator("resource_limits")
    @classmethod
    def validate_resource_limits(cls, limits: dict[str, int]) -> dict[str, int]:
        if any(
            not name.strip()
            or len(name) > 120
            or amount < 1
            for name, amount in limits.items()
        ):
            raise ValueError("Resource names and capacities must be valid and positive.")
        return limits


class TaskPlanningValidationIssue(BaseModel):
    """One deterministic planning or authorization finding."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    message: str = Field(min_length=1, max_length=2_000)


class TaskPlanningDecision(BaseModel):
    """Reviewable Task plan with server-derived validation and execution state."""

    model_config = ConfigDict(extra="forbid")

    planning_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=80)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    suggested_title: str = Field(min_length=1, max_length=160)
    intent: str = Field(min_length=1, max_length=2_000)
    planner_suggested_mode: Literal["sop", "dag"]
    planner_reason: str = Field(default="", max_length=2_000)
    recommended_mode: Literal["sop", "dag"]
    reason: str = Field(min_length=1, max_length=4_000)
    nodes: list[TaskNode] = Field(min_length=1, max_length=8)
    edges: list[TaskGraphEdge] = Field(default_factory=list, max_length=56)
    input_bindings: dict[str, dict[str, TaskGraphInputBinding]] = Field(
        default_factory=dict,
        max_length=8,
    )
    max_concurrency: int = Field(ge=1, le=8)
    resource_limits: dict[str, int] = Field(default_factory=dict, max_length=32)
    validation_status: Literal["pending", "valid", "invalid"]
    execution_allowed: bool
    validation_issues: list[TaskPlanningValidationIssue] = Field(
        default_factory=list,
        max_length=64,
    )
    approval_required_node_ids: list[str] = Field(default_factory=list, max_length=8)
    requires_existing_rerun_gate: bool = False
    source_template_id: str | None = Field(default=None, max_length=80)
    source_template_version: str | None = Field(
        default=None,
        pattern=r"^\d+\.\d+\.\d+$",
    )

    def as_task_graph(self) -> TaskGraph:
        """Return the existing frozen TaskGraph contract represented here."""

        if self.validation_status == "invalid":
            raise ValueError("Invalid planning decisions cannot create a TaskGraph.")
        if self.recommended_mode != "dag":
            raise ValueError("SOP decisions do not create a TaskGraph.")
        return TaskGraph(
            edges=[edge.model_copy(deep=True) for edge in self.edges],
            input_bindings={
                node_id: {
                    name: binding.model_copy(deep=True)
                    for name, binding in named.items()
                }
                for node_id, named in self.input_bindings.items()
            },
            max_concurrency=self.max_concurrency,
            resource_limits=dict(self.resource_limits),
        )


TaskPlanningResponse.model_rebuild()
TaskPlanningConfirmResponse.model_rebuild()


def available_tool_map(
    tool_schemas: list[TaskToolSchema],
) -> dict[str, TaskToolSchema]:
    """Index the current session's exact Task tool catalog by public name."""

    return {tool.name: tool for tool in tool_schemas if tool.name.strip()}
