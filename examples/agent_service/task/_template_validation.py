# -*- coding: utf-8 -*-
"""Task-side validation for template copies and run preflight."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from ._capability_contracts import is_task_artifact_capability
from ._models import (
    TaskNode,
    TaskRecord,
    TaskToolSchema,
    TaskWorkflowMode,
    TaskValidationErrorDetail,
    TaskValidationIssue,
    TaskValidationResponse,
    ToolStepConfig,
)
from ._plan_validation import validate_task_plan
from ._template_compiler import (
    TaskTemplateCompiler,
    TemplateCapabilityMappingError,
    TemplateCompileError,
)
from ._template_registry import TemplateRegistry, TemplateRegistryError
from ._template_models import TaskTemplateDefinition, TemplateArtifactConfig


_VARIABLE_PATTERN = re.compile(r"\{\{([^{}]+)\}\}")
_ANSWER_PATTERN = re.compile(r"^answers\.([\w.-]+)$")
_NODE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")


class TaskValidationFailure(ValueError):
    """A Task save or run blocked by field-specific validation findings."""

    def __init__(self, result: TaskValidationResponse) -> None:
        self.result = result
        if result.missing_capabilities:
            self.code = "TEMPLATE_CAPABILITY_MISSING"
            self.message = "当前会话缺少模板运行所需的工具能力。"
        elif result.blocked_capabilities:
            self.code = "TEMPLATE_CAPABILITY_UNSAFE"
            self.message = "模板包含当前阶段未授权的非只读工具。"
        elif result.capability_checks:
            self.code = "TASK_PREFLIGHT_BLOCKED"
            self.message = "Task 尚未通过运行前能力检查。"
        else:
            self.code = "TASK_VALIDATION_FAILED"
            self.message = "Task 校验未通过，请按字段说明修正后重试。"
        super().__init__(self.code)

    def public_detail(self) -> dict[str, object]:
        """Return a stable API payload without internal exception details."""

        return TaskValidationErrorDetail(
            code=self.code,
            message=self.message,
            issues=self.result.issues,
            missing_capabilities=self.result.missing_capabilities,
            blocked_capabilities=self.result.blocked_capabilities,
            capability_checks=self.result.capability_checks,
        ).model_dump(exclude_none=True)


class TaskTemplateValidator:
    """Validate ordinary Task structure and template-owned invariants."""

    def __init__(
        self,
        registry: TemplateRegistry | None = None,
        compiler: TaskTemplateCompiler | None = None,
    ) -> None:
        self._registry = registry or TemplateRegistry()
        self._compiler = compiler

    def validate(
        self,
        task: TaskRecord | None,
        nodes: list[TaskNode],
        *,
        tool_schemas: list[TaskToolSchema] | None = None,
        run_input: str | None = None,
    ) -> TaskValidationResponse:
        """Validate generic structure, then provenance-bound template rules."""

        issues: list[TaskValidationIssue] = []
        missing: set[str] = set()
        blocked: set[str] = set()
        task_id = task.id if task is not None else None

        if len(nodes) > 8:
            issues.append(
                TaskValidationIssue(
                    path="nodes",
                    message="Task 最多包含 8 个节点。",
                ),
            )
        seen_ids: set[str] = set()
        for index, node in enumerate(nodes):
            if node.id in seen_ids:
                issues.append(
                    TaskValidationIssue(
                        path=f"nodes[{index}].id",
                        message="节点 ID 重复。",
                    ),
                )
            seen_ids.add(node.id)
        if nodes:
            issues.extend(
                TaskValidationIssue(path=issue.path, message=issue.message)
                for issue in validate_task_plan(
                    {"nodes": nodes},
                    tool_schemas=None,
                    require_final_agent=False,
                )
            )
            for index, node in enumerate(nodes):
                if node.artifact is not None and (
                    index != len(nodes) - 1 or node.type.value == "input"
                ):
                    issues.append(
                        TaskValidationIssue(
                            path=f"nodes[{index}].artifact",
                            message="文件产物只能配置在最终输出节点。",
                        ),
                    )
        elif task is not None and task.source_template_id:
            issues.append(
                TaskValidationIssue(
                    path="nodes",
                    message="模板副本必须保留其强制工作流步骤。",
                ),
            )

        if run_input is not None and not run_input.strip():
            issues.append(
                TaskValidationIssue(
                    path="input",
                    message="运行输入不能为空；请填写本次输入或任务目标。",
                ),
            )

        if task is None:
            return _result(task_id, issues, missing, blocked)

        has_template_id = bool(task.source_template_id)
        has_template_version = bool(task.source_template_version)
        if not has_template_id and not has_template_version:
            if task.workflow_mode == TaskWorkflowMode.CUSTOM_FLOW:
                issues.append(
                    TaskValidationIssue(
                        path="source_template_id",
                        message="自定义流程必须保留可追溯的来源模板。",
                    ),
                )
            if task.workflow_mode == TaskWorkflowMode.TEMPLATE_INSTANCE:
                issues.append(
                    TaskValidationIssue(
                        path="source_template_id",
                        message="标准模板任务缺少来源模板 ID 和版本。",
                    ),
                )
            if task.template_step_bindings or task.template_inputs is not None:
                issues.append(
                    TaskValidationIssue(
                        path="source_template_id",
                        message="普通 Task 不得包含模板来源绑定数据。",
                    ),
                )
            if task.source_task_id is not None or task.source_task_revision is not None:
                issues.append(
                    TaskValidationIssue(
                        path="source_task_id",
                        message="自由 Task 不得携带自定义流程来源记录。",
                    ),
                )
            return _result(task_id, issues, missing, blocked)

        if not has_template_id or not has_template_version:
            issues.append(
                TaskValidationIssue(
                    path="source_template_id",
                    message="模板来源 ID 与版本必须同时存在。",
                ),
            )
            return _result(task_id, issues, missing, blocked)
        if task.workflow_mode == TaskWorkflowMode.FREE_AI_PLANNER:
            issues.append(
                TaskValidationIssue(
                    path="workflow_mode",
                    message="自由 AI Planner Task 不能同时声明标准模板来源。",
                ),
            )

        try:
            template = self._registry.get_template_version(
                task.source_template_id,
                task.source_template_version,
            )
        except TemplateRegistryError:
            template = None
        if template is None:
            issues.append(
                TaskValidationIssue(
                    path="source_template_id",
                    message="模板来源版本无法校验；请先完成受控迁移或恢复。",
                ),
            )
            return _result(task_id, issues, missing, blocked)
        for index, node in enumerate(nodes):
            if node.artifact is None:
                continue
            try:
                TemplateArtifactConfig.model_validate(
                    {
                        "format": node.artifact.format.value,
                        "filename": node.artifact.filename,
                    },
                )
            except ValidationError:
                issues.append(
                    TaskValidationIssue(
                        path=f"nodes[{index}].artifact",
                        message="模板文件产物仅接受支持的格式和单一安全文件名。",
                    ),
                )
        compiler = self._compiler
        if compiler is None:
            try:
                compiler = TaskTemplateCompiler.from_environment()
            except TemplateCapabilityMappingError:
                issues.append(
                    TaskValidationIssue(
                        path="capability_tool_map",
                        message="Task 模板能力映射配置无效。",
                    ),
                )
                return _result(task_id, issues, missing, blocked)

        stored_inputs = _stored_template_inputs(task)
        if stored_inputs is None:
            issues.append(
                TaskValidationIssue(
                    path="template_inputs",
                    message="模板来源输入缺失；请重新从当前模板实例化任务。",
                ),
            )
        else:
            try:
                compiler.validate_inputs(template, stored_inputs)
            except TemplateCompileError as error:
                issues.extend(_issues_from_text(error.issues, "template_inputs"))

        if task.workflow_mode == TaskWorkflowMode.CUSTOM_FLOW:
            issues.extend(_validate_custom_flow(task, nodes, template))
            if tool_schemas is not None and nodes:
                issues.extend(
                    TaskValidationIssue(
                        path=issue.path,
                        message=issue.message,
                    )
                    for issue in validate_task_plan(
                        {"nodes": nodes},
                        tool_schemas=tool_schemas,
                        require_final_agent=False,
                    )
                )
            issues.extend(_validate_variables(nodes))
            return _result(task_id, issues, missing, blocked)

        try:
            compiled = compiler.compile_nodes(
                template,
                [],
                require_tool_bindings=False,
            )
            canonical_nodes = {
                step.step_key: node
                for step, node in zip(template.steps, compiled.nodes)
            }
        except TemplateCompileError as error:
            canonical_nodes = {}
            issues.extend(_issues_from_text(error.issues, "nodes"))

        if tool_schemas is not None:
            configured_artifact_capabilities = (
                _configured_task_artifact_capabilities(template, nodes)
            )
            compile_template = template
            if configured_artifact_capabilities:
                compile_template = template.model_copy(
                    update={
                        "required_capabilities": [
                            capability
                            for capability in template.required_capabilities
                            if capability not in configured_artifact_capabilities
                        ],
                    },
                )
            try:
                compiler.compile_nodes(compile_template, tool_schemas)
            except TemplateCompileError as error:
                missing.update(error.missing_capabilities)
                blocked.update(error.blocked_capabilities)
                issues.extend(_issues_from_text(error.issues, "nodes"))

        if tool_schemas is not None:
            for step_key, canonical in canonical_nodes.items():
                if not isinstance(canonical.config, ToolStepConfig):
                    continue
                tool_index = next(
                    (
                        index
                        for index, schema in enumerate(tool_schemas)
                        if schema.name == canonical.config.tool_name
                    ),
                    None,
                )
                if tool_index is not None and not tool_schemas[
                    tool_index
                ].input_schema:
                    issues.append(
                        TaskValidationIssue(
                            path=f"tools.{step_key}.input_schema",
                            message="模板运行所需工具必须提供可校验的 JSON Schema。",
                        ),
                    )

        bindings = task.template_step_bindings
        expected_keys = {step.step_key for step in template.steps}
        if set(bindings) != expected_keys:
            issues.append(
                TaskValidationIssue(
                    path="template_step_bindings",
                    message="服务端模板步骤绑定不完整或包含未知步骤。",
                ),
            )
        binding_ids = list(bindings.values())
        if any(not value.strip() for value in binding_ids) or len(
            binding_ids
        ) != len(set(binding_ids)):
            issues.append(
                TaskValidationIssue(
                    path="template_step_bindings",
                    message="服务端模板步骤绑定 ID 无效或重复。",
                ),
            )

        by_id = {node.id: node for node in nodes}
        positions: list[tuple[int, int]] = []
        allowed_ids = set(binding_ids)
        for index, node in enumerate(nodes):
            if node.id not in allowed_ids:
                issues.append(
                    TaskValidationIssue(
                        path=f"nodes[{index}].id",
                        message="模板副本不能新增未在来源模板中定义的步骤。",
                    ),
                )

        for template_index, step in enumerate(template.steps):
            node_id = bindings.get(step.step_key)
            if not node_id:
                if step.required:
                    issues.append(
                        TaskValidationIssue(
                            path=f"template_step_bindings.{step.step_key}",
                            message="强制步骤缺少服务端节点绑定。",
                        ),
                    )
                continue
            node = by_id.get(node_id)
            if node is None:
                if step.required:
                    issues.append(
                        TaskValidationIssue(
                            path=f"nodes.{node_id}",
                            message=f"强制步骤“{step.title}”不能删除。",
                        ),
                    )
                continue

            positions.append((template_index, nodes.index(node)))
            if node.type.value != step.kind:
                issues.append(
                    TaskValidationIssue(
                        path=f"nodes.{node.id}.type",
                        message=f"步骤“{step.title}”的类型由模板确定，不能修改。",
                    ),
                )

            canonical = canonical_nodes.get(step.step_key)
            if canonical is not None:
                issues.extend(
                    _validate_edit_policy(
                        node,
                        canonical,
                        step_key=step.step_key,
                        title=step.title,
                        editable=step.editable,
                        required=step.required,
                    ),
                )

        if any(
            positions[index][1] >= positions[index + 1][1]
            for index in range(len(positions) - 1)
        ):
            issues.append(
                TaskValidationIssue(
                    path="nodes.order",
                    message="模板步骤必须保持来源模板规定的前置顺序。",
                ),
            )

        if tool_schemas is not None and nodes:
            issues.extend(
                TaskValidationIssue(path=issue.path, message=issue.message)
                for issue in validate_task_plan(
                    {"nodes": nodes},
                    tool_schemas=tool_schemas,
                    require_final_agent=False,
                )
            )

        issues.extend(_validate_variables(nodes))
        return _result(task_id, issues, missing, blocked)

    def template_for_task(self, task: TaskRecord) -> Any | None:
        """Return the immutable source definition for a saved template copy."""

        if not task.source_template_id or not task.source_template_version:
            return None
        try:
            return self._registry.get_template_version(
                task.source_template_id,
                task.source_template_version,
            )
        except TemplateRegistryError:
            return None

    def runtime_nodes(
        self,
        task: TaskRecord,
        nodes: list[TaskNode],
        tool_schemas: list[TaskToolSchema],
    ) -> list[TaskNode]:
        """Restore server-owned capability metadata into a run snapshot."""

        template = self.template_for_task(task)
        if template is None:
            return nodes
        compiler = self._compiler
        if compiler is None:
            try:
                compiler = TaskTemplateCompiler.from_environment()
            except TemplateCapabilityMappingError:
                return nodes
        try:
            compiled = compiler.compile_nodes(template, tool_schemas)
        except TemplateCompileError:
            return nodes

        canonical = {
            step.step_key: node
            for step, node in zip(template.steps, compiled.nodes)
        }
        node_by_id = {node.id: node for node in nodes}
        enriched: dict[str, TaskNode] = {}
        for step_key, node_id in task.template_step_bindings.items():
            node = node_by_id.get(node_id)
            expected = canonical.get(step_key)
            if node is None or expected is None or expected.capability_key is None:
                continue
            update: dict[str, Any] = {
                "capability_key": expected.capability_key,
            }
            if node.output_schema is None and expected.output_schema is not None:
                update["output_schema"] = expected.output_schema
            enriched[node_id] = node.model_copy(update=update)
        return [enriched.get(node.id, node) for node in nodes]


def _configured_task_artifact_capabilities(
    template: TaskTemplateDefinition,
    nodes: list[TaskNode],
) -> set[str]:
    """Resolve only artifact capabilities backed by one valid final output."""

    configured = [node for node in nodes if node.artifact is not None]
    if (
        len(configured) != 1
        or not nodes
        or configured[0] is not nodes[-1]
        or nodes[-1].type.value == "input"
    ):
        return set()
    artifact = configured[0].artifact
    if artifact is None:  # pragma: no cover — narrowed by configured
        return set()
    try:
        TemplateArtifactConfig.model_validate(
            {"format": artifact.format.value, "filename": artifact.filename},
        )
    except ValidationError:
        return set()
    return {
        capability
        for capability in template.required_capabilities
        if is_task_artifact_capability(capability)
    }


def _validate_custom_flow(
    task: TaskRecord,
    nodes: list[TaskNode],
    template: TaskTemplateDefinition,
) -> list[TaskValidationIssue]:
    """Allow free editing while retaining source capability guardrails."""

    issues: list[TaskValidationIssue] = []
    if not task.source_task_id or task.source_task_revision is None:
        issues.append(
            TaskValidationIssue(
                path="source_task_id",
                message="自定义流程缺少原 Task 及版本追溯信息。",
            ),
        )

    bindings = task.template_step_bindings
    expected_keys = {step.step_key for step in template.steps}
    if set(bindings) != expected_keys:
        issues.append(
            TaskValidationIssue(
                path="template_step_bindings",
                message="自定义流程的来源步骤绑定不完整或包含未知步骤。",
            ),
        )
    binding_ids = list(bindings.values())
    if any(not value.strip() for value in binding_ids) or len(
        binding_ids
    ) != len(set(binding_ids)):
        issues.append(
            TaskValidationIssue(
                path="template_step_bindings",
                message="自定义流程的来源节点绑定 ID 无效或重复。",
            ),
        )

    by_id = {node.id: node for node in nodes}
    bound_ids = set(binding_ids)
    step_by_key = {step.step_key: step for step in template.steps}
    required_capabilities = set(template.required_capabilities)
    unsupported_capabilities = {
        item.capability_key for item in template.unsupported_capabilities
    }
    reserved_capabilities = required_capabilities | unsupported_capabilities
    protected_positions: list[tuple[int, int]] = []
    input_questions: set[str] = set()

    if nodes and nodes[-1].type.value == "input":
        issues.append(
            TaskValidationIssue(
                path=f"nodes.{nodes[-1].id}",
                message="自定义流程最终节点必须能够输出结果，不能以 InputStep 结束。",
            ),
        )

    for index, node in enumerate(nodes):
        if not _NODE_ID_PATTERN.fullmatch(node.id):
            issues.append(
                TaskValidationIssue(
                    path=f"nodes[{index}].id",
                    message="节点 ID 不能为空，且长度和字符必须符合 Task 标识格式。",
                ),
            )
        if node.type.value != "input":
            continue
        for question in node.config.questions:
            if question.key in input_questions:
                issues.append(
                    TaskValidationIssue(
                        path=f"nodes[{index}].config.questions.{question.key}",
                        message="InputStep 问题键在同一流程中必须唯一。",
                    ),
                )
            input_questions.add(question.key)

    for template_index, step in enumerate(template.steps):
        node_id = bindings.get(step.step_key)
        if node_id is None:
            continue
        node = by_id.get(node_id)
        is_required_capability_tool = (
            step.kind == "tool"
            and step.capability_key in required_capabilities
        )
        if is_required_capability_tool:
            if node is None:
                issues.append(
                    TaskValidationIssue(
                        path=f"nodes.{node_id}",
                        message=(
                            f"来源场景必需能力“{step.capability_key}”的 Tool 节点"
                            "不能删除。"
                        ),
                    ),
                )
                continue
            if node.type.value != "tool":
                issues.append(
                    TaskValidationIssue(
                        path=f"nodes.{node.id}.type",
                        message=(
                            f"来源场景必需能力“{step.capability_key}”必须保留为 Tool 节点。"
                        ),
                    ),
                )
                continue
            if node.capability_key not in {None, step.capability_key}:
                issues.append(
                    TaskValidationIssue(
                        path=f"nodes.{node.id}.capability_key",
                        message="来源 Tool 节点不能改写服务端能力绑定。",
                    ),
                )
            protected_positions.append((template_index, nodes.index(node)))
            continue

        if node is not None and node.type.value == "tool":
            if node.capability_key is not None:
                issues.append(
                    TaskValidationIssue(
                        path=f"nodes.{node.id}.capability_key",
                        message="非 Tool 来源步骤不能声明模板能力绑定。",
                    ),
                )
            if node.config.tool_name in reserved_capabilities:
                issues.append(
                    TaskValidationIssue(
                        path=f"nodes.{node.id}.config.tool_name",
                        message=(
                            "自定义节点不能冒用来源模板要求或标记为未支持的能力。"
                        ),
                    ),
                )

    if any(
        protected_positions[index][1]
        >= protected_positions[index + 1][1]
        for index in range(len(protected_positions) - 1)
    ):
        issues.append(
            TaskValidationIssue(
                path="nodes.order",
                message=(
                    "来源场景必需能力 Tool 节点必须保持相对顺序，"
                    "以保留业务校验链。"
                ),
            ),
        )

    for index, node in enumerate(nodes):
        if node.id in bound_ids or node.type.value != "tool":
            continue
        if node.capability_key is not None:
            issues.append(
                TaskValidationIssue(
                    path=f"nodes[{index}].capability_key",
                    message="新增 Tool 节点不能伪造来源模板能力绑定。",
                ),
            )
        if node.config.tool_name in reserved_capabilities:
            issues.append(
                TaskValidationIssue(
                    path=f"nodes[{index}].config.tool_name",
                    message=(
                        "新增 Tool 节点不能冒用来源模板要求或标记为未支持的能力。"
                    ),
                ),
            )

    # Template binding keys are server-owned metadata. A template-bound
    # non-Tool step may change type, but it cannot acquire a capability claim.
    for step_key, node_id in bindings.items():
        step = step_by_key.get(step_key)
        node = by_id.get(node_id)
        if (
            step is not None
            and step.kind != "tool"
            and node is not None
            and node.type.value == "tool"
            and node.capability_key is not None
        ):
            issues.append(
                TaskValidationIssue(
                    path=f"nodes.{node.id}.capability_key",
                    message="转换后的节点不能继承或声明来源模板 Tool 能力。",
                ),
            )

    return issues


def _validate_edit_policy(
    node: TaskNode,
    canonical: TaskNode,
    *,
    step_key: str,
    title: str,
    editable: bool,
    required: bool,
) -> list[TaskValidationIssue]:
    """Keep template-owned structure fixed while allowing declared edits."""

    issues: list[TaskValidationIssue] = []
    path = f"nodes.{node.id}"
    actual_dump = node.model_dump(mode="python", exclude={"id", "order"})
    canonical_dump = canonical.model_dump(
        mode="python",
        exclude={"id", "order"},
    )
    # Capability identity and its strict Tool result Schema are derived from
    # the immutable source template at run time. Older saved task copies do
    # not have these additive fields and remain readable without migration.
    actual_dump.pop("capability_key", None)
    canonical_dump.pop("capability_key", None)
    if node.type.value == "tool" and node.output_schema is None:
        actual_dump["output_schema"] = canonical_dump.get("output_schema")
    if not editable:
        actual_dump.pop("artifact", None)
        canonical_dump.pop("artifact", None)
        if actual_dump != canonical_dump:
            issues.append(
                TaskValidationIssue(
                    path=path,
                    message=f"步骤“{title}”由模板锁定，不能修改。",
                ),
            )
        return issues

    for field in (
        "type",
        "acceptance_criteria",
        "output_schema",
        "knowledge_graph_enabled",
        "knowledge_base_mode",
        "knowledge_base_ids",
        "max_attempts",
    ):
        if getattr(node, field) != getattr(canonical, field):
            issues.append(
                TaskValidationIssue(
                    path=f"{path}.{field}",
                    message=f"步骤“{title}”的模板约束字段不能修改。",
                ),
            )

    if node.config.type == "agent" and canonical.config.type == "agent":
        if (
            node.config.agent_id != canonical.config.agent_id
            or node.config.session_id != canonical.config.session_id
        ):
            issues.append(
                TaskValidationIssue(
                    path=f"{path}.config",
                    message="模板 Agent 的执行身份不能通过节点配置改写。",
                ),
            )
    elif node.config.type == "input" and canonical.config.type == "input":
        allowed_questions = {
            question.key: question
            for question in canonical.config.questions
        }
        actual_questions = {
            question.key: question for question in node.config.questions
        }
        for key, question in allowed_questions.items():
            actual = actual_questions.get(key)
            if actual is None and question.required:
                issues.append(
                    TaskValidationIssue(
                        path=f"{path}.config.questions.{key}",
                        message="模板要求的问题不能删除。",
                    ),
                )
            elif actual is not None and actual.required != question.required:
                issues.append(
                    TaskValidationIssue(
                        path=f"{path}.config.questions.{key}.required",
                        message="模板问题的必填状态不能修改。",
                    ),
                )
        if set(actual_questions) - set(allowed_questions):
            issues.append(
                TaskValidationIssue(
                    path=f"{path}.config.questions",
                    message="不能新增未由模板定义的问题字段。",
                ),
            )
    elif node.config != canonical.config:
        issues.append(
            TaskValidationIssue(
                path=f"{path}.config",
                message=f"步骤“{title}”的配置类型或能力不能修改。",
            ),
        )

    # Required means the server keeps this step present.  Editable controls
    # only the content fields above; it never grants deletion or reordering.
    del step_key, required
    return issues


def _validate_variables(nodes: list[TaskNode]) -> list[TaskValidationIssue]:
    """Check simple Task variables against only preceding InputStep fields."""

    issues: list[TaskValidationIssue] = []
    input_keys: set[str] = set()
    for index, node in enumerate(nodes):
        config = node.config.model_dump(mode="python")
        for text in _strings(config):
            matches = list(_VARIABLE_PATTERN.finditer(text))
            if (
                text.count("{{") != len(matches)
                or text.count("}}") != len(matches)
            ):
                issues.append(
                    TaskValidationIssue(
                        path=f"nodes[{index}].config",
                        message="Task 变量格式无效。",
                    ),
                )
                continue
            for match in matches:
                variable = match.group(1).strip()
                answer = _ANSWER_PATTERN.fullmatch(variable)
                if variable in {"user_input", "previous_output"}:
                    continue
                if answer and answer.group(1) in input_keys:
                    continue
                issues.append(
                    TaskValidationIssue(
                        path=f"nodes[{index}].config",
                        message=f"输入变量“{variable}”在前序节点中没有来源。",
                    ),
                )
        if node.type.value == "input":
            input_keys.update(
                question.key for question in node.config.questions
            )
    return issues


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _stored_template_inputs(task: TaskRecord) -> dict[str, Any] | None:
    """Read the trusted P3 goal snapshot for records predating template_inputs."""

    if task.template_inputs is not None:
        return task.template_inputs
    marker = "模板输入参数（JSON）：\n"
    if marker not in task.goal:
        return None
    raw = task.goal.rsplit(marker, 1)[-1].strip()
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _issues_from_text(
    issues: tuple[str, ...],
    default_path: str,
) -> list[TaskValidationIssue]:
    result: list[TaskValidationIssue] = []
    for issue in issues:
        if ":" in issue:
            path, message = issue.split(":", 1)
        else:
            path, message = default_path, issue
        result.append(
            TaskValidationIssue(
                path=path.strip() or default_path,
                message=message.strip() or "配置无效。",
            ),
        )
    return result


def _result(
    task_id: str | None,
    issues: list[TaskValidationIssue],
    missing: set[str],
    blocked: set[str],
) -> TaskValidationResponse:
    missing_capabilities = sorted(missing, key=str.casefold)
    blocked_capabilities = sorted(blocked, key=str.casefold)
    valid = not issues and not missing_capabilities and not blocked_capabilities
    return TaskValidationResponse(
        task_id=task_id,
        valid=valid,
        issues=issues,
        missing_capabilities=missing_capabilities,
        blocked_capabilities=blocked_capabilities,
    )
