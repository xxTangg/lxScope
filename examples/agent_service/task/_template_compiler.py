# -*- coding: utf-8 -*-
"""Compile validated Task templates into existing Task nodes."""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Mapping

from jsonschema import Draft202012Validator, SchemaError
from pydantic import ValidationError

from ._models import (
    AgentStepConfig,
    InputStepConfig,
    PythonStepConfig,
    StepType,
    TaskInputQuestion,
    TaskNode,
    TaskToolSchema,
    ToolStepConfig,
)
from ._plan_validation import validate_task_plan
from ._output_validation import (
    OutputSchemaDefinitionError,
    describe_output_schema,
    validate_output_schema_definition,
)
from ._capability_contracts import (
    capability_contract,
    is_task_native_capability,
    validate_contract_tool_schema,
)
from ._scenario_runtime import native_tool_schema
from ._template_models import (
    TaskTemplateDefinition,
    TemplateAgentConfig,
    TemplateArtifactConfig,
    TemplateInputConfig,
    TemplatePythonConfig,
    TemplateToolConfig,
)


_CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{1,119}$")
_VARIABLE_PATTERN = re.compile(r"\{\{([^{}]+)\}\}")
_ANSWER_VARIABLE_PATTERN = re.compile(r"^answers\.([\w.-]+)$")
_CAPABILITY_MAP_ENV = "LXSCOPE_TASK_CAPABILITY_TOOL_MAP"


class TemplateCapabilityMappingError(ValueError):
    """Raised when trusted server-side capability mapping is malformed."""


class TemplateCompileError(ValueError):
    """Safe, structured failure while validating inputs or compiling nodes."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        missing_capabilities: tuple[str, ...] = (),
        blocked_capabilities: tuple[str, ...] = (),
        issues: tuple[str, ...] = (),
    ) -> None:
        self.code = code
        self.message = message
        self.missing_capabilities = missing_capabilities
        self.blocked_capabilities = blocked_capabilities
        self.issues = issues
        super().__init__(code)


@dataclass(frozen=True)
class CompiledTemplateNodes:
    """Task nodes and their server-owned stable template key mapping."""

    nodes: list[TaskNode]
    step_bindings: dict[str, str]


class TaskTemplateCompiler:
    """Compile logical capabilities to existing Task Tool nodes.

    ``LXSCOPE_TASK_CAPABILITY_TOOL_MAP`` may contain a JSON object mapping
    template capability keys to actual Task tool names. Without an alias, a
    strict compilation binds only when the current authenticated session
    exposes a tool whose name exactly matches the server-owned capability key.
    Gallery creation can use relaxed compilation and defer availability checks
    until the existing run preflight.
    """

    def __init__(
        self,
        capability_tool_aliases: Mapping[str, str] | None = None,
    ) -> None:
        aliases: dict[str, str] = {}
        for capability_key, tool_name in (
            capability_tool_aliases or {}
        ).items():
            if (
                not isinstance(capability_key, str)
                or not _CAPABILITY_PATTERN.fullmatch(capability_key)
                or not isinstance(tool_name, str)
                or not tool_name.strip()
                or len(tool_name) > 200
            ):
                raise TemplateCapabilityMappingError(
                    "Capability mappings require valid keys and tool names."
                )
            aliases[capability_key] = tool_name.strip()
        self._aliases = aliases

    @classmethod
    def from_environment(cls) -> "TaskTemplateCompiler":
        """Load an optional server-owned alias map; never accept it via API."""

        raw = os.getenv(_CAPABILITY_MAP_ENV, "").strip()
        if not raw:
            return cls()
        try:
            aliases = json.loads(raw)
        except json.JSONDecodeError as error:
            raise TemplateCapabilityMappingError(
                "The Task capability mapping must be valid JSON."
            ) from error
        if not isinstance(aliases, dict):
            raise TemplateCapabilityMappingError(
                "The Task capability mapping must be a JSON object."
            )
        return cls(aliases)

    def validate_inputs(
        self,
        template: TaskTemplateDefinition,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate caller values against the template's JSON Schema subset."""

        try:
            encoded = json.dumps(
                inputs,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            raise TemplateCompileError(
                "TEMPLATE_INPUT_INVALID",
                "输入内容不是可保存的 JSON 数据。",
                issues=("inputs: 输入内容格式无效。",),
            ) from error
        if len(encoded) > 18_000:
            raise TemplateCompileError(
                "TEMPLATE_INPUT_INVALID",
                "输入内容超过 Task 可保存的长度限制。",
                issues=("inputs: 输入内容过长。",),
            )

        schema = template.input_schema.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            raise TemplateCompileError(
                "TEMPLATE_INPUT_SCHEMA_INVALID",
                "模板输入 Schema 无效。",
                issues=("input_schema: 模板定义未通过 JSON Schema 校验。",),
            ) from error

        validator = Draft202012Validator(schema)
        errors = sorted(
            validator.iter_errors(inputs),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
        if errors:
            issues = tuple(
                _input_issue(error)
                for error in errors[:16]
            )
            raise TemplateCompileError(
                "TEMPLATE_INPUT_INVALID",
                "输入内容不符合模板要求。",
                issues=issues,
            )

        return inputs

    def compile_nodes(
        self,
        template: TaskTemplateDefinition,
        tool_schemas: list[TaskToolSchema],
        *,
        require_tool_bindings: bool = True,
    ) -> CompiledTemplateNodes:
        """Compile TaskNodes, optionally deferring live Tool checks to preflight."""

        tool_steps = {
            step.capability_key: step
            for step in template.steps
            if step.kind == "tool"
        }
        native_capabilities = {
            capability
            for capability in tool_steps
            if is_task_native_capability(capability)
        }
        if require_tool_bindings and native_capabilities:
            native_names = native_capabilities
            tool_schemas = [
                schema
                for schema in tool_schemas
                if (
                    schema.name
                    if isinstance(schema, TaskToolSchema)
                    else schema.get("name")
                )
                not in native_names
            ]
            tool_schemas = [
                *tool_schemas,
                *[
                    TaskToolSchema(
                        name=capability,
                        description="由 Task 模块受控适配器提供。",
                        input_schema=native_tool_schema(capability),
                        is_read_only=True,
                    )
                    for capability in sorted(native_capabilities)
                ],
            ]
        missing: set[str] = (
            {
                capability
                for capability in template.required_capabilities
                if capability not in tool_steps
            }
            if require_tool_bindings
            else set()
        )
        if any(
            step.kind == "tool"
            and step.capability_key == "project.similar_search"
            for step in template.steps
        ):
            # The Task-native authorized RAG adapter checks the authenticated
            # owner's selected KB scope directly. Older S01 templates declare
            # this implicit permission capability without a separate Tool node.
            missing.discard("project.authorization.read")

        tools_by_name: dict[str, list[TaskToolSchema]] = defaultdict(list)
        for schema in tool_schemas:
            try:
                tool = TaskToolSchema.model_validate(schema)
            except ValidationError as error:
                issues = tuple(
                    f"tool_schemas[{index}]."
                    f"{'.'.join(str(part) for part in issue['loc'])} "
                    f"[{issue['type']}]"
                    for index, issue in enumerate(
                        error.errors(include_url=False)
                    )
                )
                raise TemplateCompileError(
                    "TEMPLATE_TOOL_CATALOG_INVALID",
                    "当前会话的 Task 工具目录无效。",
                    issues=issues,
                ) from error
            if tool.name.strip():
                tools_by_name[tool.name].append(tool)

        bound_tools: dict[str, TaskToolSchema] = {}
        tool_names = {
            capability_key: (
                capability_key
                if is_task_native_capability(capability_key)
                else self._aliases.get(capability_key, capability_key)
            )
            for capability_key in tool_steps
        }
        for capability_key in tool_steps:
            tool_name = tool_names[capability_key]
            if not require_tool_bindings:
                continue
            candidates = tools_by_name.get(tool_name, [])
            if len(candidates) != 1:
                missing.add(capability_key)
                continue
            bound_tools[capability_key] = candidates[0]
            tool_names[capability_key] = candidates[0].name

        if missing and require_tool_bindings:
            raise TemplateCompileError(
                "TEMPLATE_CAPABILITY_MISSING",
                "当前会话缺少可执行模板所需的 Task 工具或能力。",
                missing_capabilities=tuple(
                    sorted(missing, key=lambda item: item.casefold())
                ),
            )

        blocked = tuple(
            sorted(
                (
                    capability_key
                    for capability_key, tool in bound_tools.items()
                    if not tool.is_read_only
                ),
                key=lambda item: item.casefold(),
            )
        )
        if blocked and require_tool_bindings:
            raise TemplateCompileError(
                "TEMPLATE_CAPABILITY_UNSAFE",
                "模板包含非只读工具；当前 Task 阶段未提供副作用授权和幂等保护。",
                blocked_capabilities=blocked,
            )

        incompatible: list[str] = []
        for capability_key, tool in bound_tools.items():
            contract = capability_contract(capability_key)
            if contract is None:
                continue
            if not tool.input_schema:
                incompatible.append(
                    f"tools.{tool.name}.input_schema: Tool 未提供可校验的 JSON Schema。"
                )
                continue
            incompatible.extend(
                f"{capability_key}: {issue}"
                for issue in validate_contract_tool_schema(
                    capability_key,
                    tool.input_schema,
                )
            )
        if incompatible and require_tool_bindings:
            raise TemplateCompileError(
                "TEMPLATE_CAPABILITY_INCOMPATIBLE",
                "当前会话的工具接口与 Task 业务能力契约不兼容。",
                issues=tuple(incompatible[:16]),
            )

        self._validate_template_variables(template)
        nodes: list[TaskNode] = []
        bindings: dict[str, str] = {}
        for order, step in enumerate(template.steps):
            runtime_prompt = _runtime_prompt(step)
            if len(runtime_prompt) > 20_000:
                raise TemplateCompileError(
                    "TEMPLATE_NODE_INVALID",
                    "模板节点提示词与结构化输出约束合计超过 Task 长度限制。",
                    issues=(f"steps.{step.step_key}.output_schema: 提示词过长。",),
                )
            if step.output_schema is not None:
                try:
                    validate_output_schema_definition(step.output_schema)
                except OutputSchemaDefinitionError as error:
                    raise TemplateCompileError(
                        "TEMPLATE_NODE_INVALID",
                        "模板结构化输出 Schema 无效。",
                        issues=(f"steps.{step.step_key}.output_schema: {error}",),
                    ) from error
            config: (
                AgentStepConfig
                | ToolStepConfig
                | PythonStepConfig
                | InputStepConfig
            )
            if isinstance(step.config, TemplateAgentConfig):
                config = AgentStepConfig(prompt=runtime_prompt)
            elif isinstance(step.config, TemplateToolConfig):
                if step.capability_key is None:  # guarded by the schema model
                    raise TemplateCompileError(
                        "TEMPLATE_NODE_INVALID",
                        "模板工具步骤缺少能力键。",
                        issues=(f"steps.{step.step_key}.capability_key",),
                    )
                config = ToolStepConfig(
                    tool_name=tool_names[step.capability_key],
                    arguments=dict(step.config.arguments),
                )
            elif isinstance(step.config, TemplateInputConfig):
                config = InputStepConfig(
                    reason=step.config.reason,
                    questions=[
                        TaskInputQuestion.model_validate(
                            question.model_dump(mode="python")
                        )
                        for question in step.config.questions
                    ],
                )
            elif isinstance(step.config, TemplatePythonConfig):
                config = PythonStepConfig(
                    code=step.config.code,
                    timeout_seconds=step.config.timeout_seconds,
                )
            else:  # pragma: no cover - guarded by TemplateStepConfig
                raise TemplateCompileError(
                    "TEMPLATE_NODE_INVALID",
                    "模板节点类型不受 Task 支持。",
                    issues=(f"steps.{step.step_key}.kind",),
                )

            contract = capability_contract(step.capability_key)
            output_schema = step.output_schema
            if contract is not None and contract.output_schema is not None:
                output_schema = contract.output_schema

            try:
                node = TaskNode.model_validate(
                    {
                        "name": step.title,
                        "prompt": runtime_prompt,
                        "type": StepType(step.kind),
                        "config": config.model_dump(mode="python"),
                        "order": order,
                        "capability_key": step.capability_key,
                        "acceptance_criteria": step.output_requirement,
                        "artifact": (
                            step.artifact.model_dump(mode="python")
                            if isinstance(step.artifact, TemplateArtifactConfig)
                            else None
                        ),
                        "output_schema": output_schema,
                        "output_contract": (
                            step.output_contract.model_dump(
                                mode="python", by_alias=True
                            )
                            if step.output_contract is not None
                            else None
                        ),
                    }
                )
            except ValidationError as error:
                issues = tuple(
                    " ".join(str(part) for part in issue["loc"])
                    + f" [{issue['type']}]"
                    for issue in error.errors(include_url=False)[:16]
                )
                raise TemplateCompileError(
                    "TEMPLATE_NODE_INVALID",
                    "模板节点无法转换为 Task 节点。",
                    issues=issues,
                ) from error
            nodes.append(node)
            bindings[step.step_key] = node.id

        plan_issues = validate_task_plan(
            {"nodes": nodes},
            tool_schemas if require_tool_bindings else None,
            require_final_agent=False,
        )
        if plan_issues:
            raise TemplateCompileError(
                "TEMPLATE_NODE_INVALID",
                "模板编译结果未通过现有 Task 节点校验。",
                issues=tuple(issue.as_text() for issue in plan_issues[:16]),
            )
        return CompiledTemplateNodes(nodes=nodes, step_bindings=bindings)

    @staticmethod
    def _validate_template_variables(
        template: TaskTemplateDefinition,
    ) -> None:
        """Reject expressions and references outside Task's variable model."""

        prior_input_keys: set[str] = set()
        previous_result_available = False
        issues: list[str] = []
        for index, step in enumerate(template.steps):
            config = step.config.model_dump(mode="python")
            for text in _strings(config):
                matches = list(_VARIABLE_PATTERN.finditer(text))
                if (
                    text.count("{{") != len(matches)
                    or text.count("}}") != len(matches)
                ):
                    issues.append(
                        f"steps[{index}].config: 模板变量格式无效。"
                    )
                    continue
                for match in matches:
                    variable = match.group(1).strip()
                    if variable in {"user_input", "previous_output"}:
                        continue
                    if variable == "previous_result":
                        if previous_result_available:
                            continue
                        issues.append(
                            f"steps[{index}].config: previous_result 需要上一步声明并通过 JSON Schema 校验的输出契约。"
                        )
                        continue
                    answer_match = _ANSWER_VARIABLE_PATTERN.fullmatch(variable)
                    if (
                        answer_match is not None
                        and answer_match.group(1) in prior_input_keys
                    ):
                        continue
                    issues.append(
                        f"steps[{index}].config: 不支持或尚无来源的变量。"
                    )
            if isinstance(step.config, TemplateInputConfig):
                prior_input_keys.update(
                    question.key for question in step.config.questions
                )
            contract = step.output_contract
            previous_result_available = bool(
                contract is not None
                and contract.validation_mode == "json_schema"
                and contract.json_schema is not None
                and contract.downstream_binding == "validated_result"
            ) or step.output_schema is not None
        if issues:
            raise TemplateCompileError(
                "TEMPLATE_VARIABLE_INVALID",
                "模板包含 Task 执行器不支持的变量。",
                issues=tuple(issues[:16]),
            )


def _input_issue(error: Any) -> str:
    """Describe a JSON Schema failure without echoing the submitted value."""

    parts = [str(part) for part in error.absolute_path]
    location = "inputs" + ("." + ".".join(parts) if parts else "")
    messages = {
        "required": "缺少必填字段。",
        "additionalProperties": "包含模板未声明的字段。",
        "type": "数据类型不符合模板要求。",
        "minItems": "数组元素数量少于模板要求。",
        "maxItems": "数组元素数量超过模板要求。",
        "minimum": "数值小于模板允许的范围。",
        "maximum": "数值超过模板允许的范围。",
    }
    if error.validator == "required" and isinstance(error.instance, dict):
        missing = [
            key
            for key in error.validator_value
            if key not in error.instance
        ]
        if missing:
            return "; ".join(
                f"{location}.{key}: 缺少必填字段。" for key in missing
            )
    return f"{location}: {messages.get(error.validator or '', '不符合模板输入规则。')}"


def _runtime_prompt(step: Any) -> str:
    """Carry template guidance into the Task node's existing text field."""

    sections = [
        (
            step.config.prompt
            if isinstance(step.config, TemplateAgentConfig)
            else ""
        ),
        f"输出要求：{step.output_requirement}",
        f"失败处理说明：{step.failure_handling}",
        f"敏感信息要求：{step.sensitive_information}",
    ]
    output_schema = step.output_schema
    if output_schema is None and step.output_contract is not None:
        output_schema = step.output_contract.json_schema
    if output_schema is not None:
        sections.append(
            "结构化输出必须符合以下 JSON 字段和类型要求，只返回 JSON，不要代码围栏。"
            "服务端会独立校验，不能自称校验通过。\n"
            + describe_output_schema(output_schema),
        )
    return "\n\n".join(sections).strip()


def _strings(value: Any):
    """Yield nested strings from JSON-like template configuration."""

    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
