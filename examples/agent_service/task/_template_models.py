# -*- coding: utf-8 -*-
"""Validated data contracts for built-in Task templates."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    model_validator,
)

from ._models import (
    NodeOutputContract,
    TaskContext,
    TaskInformationRequirement,
)
from ._output_validation import validate_output_schema_definition


_CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{1,119}$")


class _TemplateModel(BaseModel):
    """Base model that rejects unrecognized template fields."""

    model_config = ConfigDict(extra="forbid", strict=True)


class TemplateInputQuestion(_TemplateModel):
    """One question presented by an existing Task InputStep."""

    key: str = Field(min_length=1, max_length=80, pattern=r"^[\w.-]+$")
    label: str = Field(min_length=1, max_length=500)
    required: StrictBool = True
    options: list[str] = Field(default_factory=list, max_length=8)


class TemplateArtifactConfig(_TemplateModel):
    """A safe artifact choice with no user-controlled directory component."""

    format: Literal["markdown", "docx", "xlsx"]
    filename: str | None = Field(
        default=None,
        max_length=120,
        pattern=r"^[^/\\<>:\"|?*\x00-\x1f]+$",
    )

    @model_validator(mode="after")
    def validate_filename(self) -> "TemplateArtifactConfig":
        if self.filename is not None and (
            self.filename != self.filename.strip()
            or self.filename in {".", ".."}
        ):
            raise ValueError("Artifact filename must be one safe path component.")
        return self


class TemplateAgentConfig(_TemplateModel):
    """Agent configuration stored in a template before Task instantiation."""

    type: Literal["agent"]
    prompt: str = Field(min_length=1, max_length=20_000)


class TemplateToolConfig(_TemplateModel):
    """Tool arguments keyed by a logical capability, without a tool name."""

    type: Literal["tool"]
    arguments: dict[str, Any] = Field(default_factory=dict)


class TemplateInputConfig(_TemplateModel):
    """Question collection supported by the existing Task InputStep."""

    type: Literal["input"]
    reason: str = Field(default="", max_length=2_000)
    questions: list[TemplateInputQuestion] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_question_keys(self) -> "TemplateInputConfig":
        keys = [question.key for question in self.questions]
        if len(keys) != len(set(keys)):
            raise ValueError("Input question keys must be unique.")
        return self


class TemplatePythonConfig(_TemplateModel):
    """Python config matching the existing Task PythonStep shape."""

    type: Literal["python"]
    code: str = Field(min_length=1, max_length=50_000)
    timeout_seconds: int = Field(default=30, ge=1, le=300)


TemplateStepConfig = Annotated[
    TemplateAgentConfig
    | TemplateToolConfig
    | TemplateInputConfig
    | TemplatePythonConfig,
    Field(discriminator="type"),
]


class TemplateStep(_TemplateModel):
    """One logical step and its supported Task-side representation."""

    step_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    title: str = Field(min_length=1, max_length=120)
    kind: Literal["agent", "tool", "python", "input"]
    required: StrictBool
    editable: StrictBool
    capability_key: str | None
    config: TemplateStepConfig
    output_requirement: str = Field(min_length=1, max_length=2_000)
    failure_handling: str = Field(min_length=1, max_length=2_000)
    sensitive_information: str = Field(min_length=1, max_length=2_000)
    artifact: TemplateArtifactConfig | None = None
    output_schema: dict[str, Any] | None = None
    output_contract: NodeOutputContract | None = None
    information_requirements: list[TaskInformationRequirement] = Field(
        default_factory=list,
        max_length=32,
    )

    @model_validator(mode="after")
    def validate_kind_and_capability(self) -> "TemplateStep":
        if self.config.type != self.kind:
            raise ValueError("Step kind must match config.type.")
        if self.kind == "tool":
            if self.capability_key is None:
                raise ValueError("Tool steps require a capability_key.")
            if not _CAPABILITY_PATTERN.fullmatch(self.capability_key):
                raise ValueError("Tool capability_key has an invalid format.")
        elif self.capability_key is not None:
            raise ValueError(
                "Only Tool steps may declare a capability_key binding."
            )
        if self.output_schema is not None:
            validate_output_schema_definition(self.output_schema)
            if self.kind != "agent":
                raise ValueError(
                    "Template output schemas are supported on Agent steps only."
                )
        if (
            self.output_contract is not None
            and self.output_contract.validation_mode
            in {"citations", "claim_evidence"}
            and self.kind != "agent"
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
        return self


class TemplateInputProperty(_TemplateModel):
    """A bounded JSON Schema property used by template input validation."""

    type: Literal["string", "integer", "number", "boolean", "array"]
    description: str = Field(default="", max_length=500)
    items: TemplateInputProperty | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    min_items: int | None = Field(default=None, ge=0, alias="minItems")

    @model_validator(mode="after")
    def validate_constraints(self) -> "TemplateInputProperty":
        if self.type == "array" and self.items is None:
            raise ValueError("Array properties require an items schema.")
        if self.type != "array" and (self.items is not None or self.min_items is not None):
            raise ValueError("Only array properties may define items or minItems.")
        if self.type not in {"integer", "number"} and (
            self.minimum is not None or self.maximum is not None
        ):
            raise ValueError("Only numeric properties may define bounds.")
        if self.type == "integer" and any(
            bound is not None and not isinstance(bound, int)
            for bound in (self.minimum, self.maximum)
        ):
            raise ValueError("Integer properties require integer bounds.")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("minimum cannot exceed maximum.")
        return self


class TemplateInputSchema(_TemplateModel):
    """The supported object subset of JSON Schema for template inputs."""

    type: Literal["object"]
    required: list[str] = Field(min_length=1)
    properties: dict[str, TemplateInputProperty] = Field(min_length=1)
    additional_properties: Literal[False] = Field(
        default=False,
        alias="additionalProperties",
    )

    @model_validator(mode="after")
    def validate_required_properties(self) -> "TemplateInputSchema":
        if len(self.required) != len(set(self.required)):
            raise ValueError("Input schema required fields must be unique.")
        if not set(self.required) <= set(self.properties):
            raise ValueError("Input schema required fields must be declared.")
        if any(
            not prop.description.strip()
            for prop in self.properties.values()
        ):
            raise ValueError("Input schema properties require descriptions.")
        return self


class TemplateUnsupportedCapability(_TemplateModel):
    """A named capability that prevents a scenario from being production-ready."""

    capability_key: str = Field(
        min_length=2,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_.-]{1,119}$",
    )
    reason: str = Field(min_length=1, max_length=2_000)


class TaskTemplateDefinition(_TemplateModel):
    """Versioned, read-only business workflow definition."""

    schema_version: Literal[1]
    template_id: str = Field(
        min_length=4,
        max_length=80,
        pattern=r"^s\d{2}_[a-z0-9_]+$",
    )
    template_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=2_000)
    category: str = Field(min_length=1, max_length=80)
    required_capabilities: list[str] = Field(min_length=1, max_length=64)
    input_schema: TemplateInputSchema
    output_requirement: str = Field(min_length=1, max_length=4_000)
    steps: list[TemplateStep] = Field(min_length=1, max_length=8)
    unsupported_capabilities: list[TemplateUnsupportedCapability] = Field(
        default_factory=list,
        max_length=32,
    )

    @model_validator(mode="after")
    def validate_template_invariants(self) -> "TaskTemplateDefinition":
        if len(self.required_capabilities) != len(
            set(self.required_capabilities)
        ):
            raise ValueError("required_capabilities must be unique.")
        if any(
            not _CAPABILITY_PATTERN.fullmatch(capability)
            for capability in self.required_capabilities
        ):
            raise ValueError("A required capability key has an invalid format.")

        step_keys = [step.step_key for step in self.steps]
        if len(step_keys) != len(set(step_keys)):
            raise ValueError("Template step_key values must be unique.")
        for step in self.steps:
            requirement_keys = [
                item.key for item in step.information_requirements
            ]
            if len(requirement_keys) != len(set(requirement_keys)):
                raise ValueError(
                    f"Step {step.step_key!r} has duplicate information requirements."
                )
        for index, step in enumerate(self.steps):
            if step.artifact is not None and (
                index != len(self.steps) - 1 or step.kind == "input"
            ):
                raise ValueError(
                    "Artifact configuration is allowed only on a final output step."
                )
        if not any(step.required for step in self.steps):
            raise ValueError("A template must contain at least one required step.")

        tool_capabilities = {
            step.capability_key
            for step in self.steps
            if step.kind == "tool"
        }
        if not tool_capabilities <= set(self.required_capabilities):
            raise ValueError(
                "Every Tool capability_key must be declared in "
                "required_capabilities."
            )

        serialized = json.dumps(
            self.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
        )
        if re.search(r"https?://", serialized, flags=re.IGNORECASE):
            raise ValueError("Template data must not contain network addresses.")
        if re.search(
            r"\b(api[_-]?key|password|secret)\b",
            serialized,
            flags=re.IGNORECASE,
        ):
            raise ValueError("Template data must not contain credentials.")
        return self


class TemplateInstantiateRequest(_TemplateModel):
    """User input for making a private Task copy of a built-in template."""

    template_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    inputs: dict[str, Any] = Field(default_factory=dict)
    source_context: TaskContext = Field(default_factory=TaskContext)


class TemplateInstantiationErrorDetail(_TemplateModel):
    """Structured error body for template instantiation API failures."""

    code: Literal[
        "TEMPLATE_NOT_FOUND",
        "TEMPLATE_VERSION_MISMATCH",
        "TEMPLATE_INPUT_INVALID",
        "TEMPLATE_INPUT_SCHEMA_INVALID",
        "TEMPLATE_TOOL_CATALOG_INVALID",
        "TEMPLATE_CAPABILITY_MISSING",
        "TEMPLATE_CAPABILITY_UNSAFE",
        "TEMPLATE_VARIABLE_INVALID",
        "TEMPLATE_NODE_INVALID",
        "TEMPLATE_CAPABILITY_MAP_INVALID",
    ]
    error_category: Literal[
        "configuration_error",
        "missing_tool",
        "permission_denied",
        "data_source_not_ready",
        "schema_mismatch",
        "external_interface_failed",
    ] = "configuration_error"
    message: str = Field(min_length=1, max_length=500)
    missing_capabilities: list[str] = Field(default_factory=list, max_length=64)
    blocked_capabilities: list[str] = Field(default_factory=list, max_length=64)
    issues: list[str] = Field(default_factory=list, max_length=16)


TemplateInputProperty.model_rebuild()
