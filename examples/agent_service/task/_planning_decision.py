# -*- coding: utf-8 -*-
"""Deterministic SOP/DAG mode selection for Task planning candidates."""

from __future__ import annotations

from ._dag import TaskGraphValidationError, validate_task_graph
from ._models import TaskGraph, TaskToolSchema
from ._planning_contract import (
    TaskPlanningCandidate,
    TaskPlanningDecision,
    TaskPlanningRuntimePolicy,
    TaskPlanningTemplateContract,
    TaskPlanningValidationIssue,
)
from ._planning_dependencies import (
    TaskDependencyAssessment,
    assess_task_dependencies,
    resource_limited_concurrency,
)


def decide_task_plan(
    candidate: TaskPlanningCandidate,
    *,
    tool_schemas: list[TaskToolSchema] | None,
    runtime_policy: TaskPlanningRuntimePolicy,
    template_contract: TaskPlanningTemplateContract | None = None,
) -> TaskPlanningDecision:
    """Validate a model candidate and select a server-authorized plan mode.

    Model-proposed mode, rationale, graph dependencies, and tool metadata are
    treated as candidate data. Execution status and concurrency are always
    recomputed here from Task contracts and the caller's runtime policy.
    """

    assessment = assess_task_dependencies(
        candidate,
        tool_schemas=tool_schemas,
        runtime_policy=runtime_policy,
        template_contract=template_contract,
    )
    graph = assessment.graph
    strict_linear = _is_strict_linear(assessment)
    has_parallel_frontier = _has_parallel_frontier(assessment)
    safe_parallel = bool(assessment.parallel_node_group)

    if assessment.issues:
        mode = candidate.recommended_mode
    elif strict_linear:
        mode = "sop"
    else:
        # Keep a graph candidate when serializing it would lose explicit
        # bindings or alter branch inputs. Authorization is checked below.
        mode = "dag"

    selected_concurrency = 1
    resource_limits: dict[str, int] = {}
    if mode == "dag" and graph is not None:
        used_resources = {
            resource
            for node in assessment.nodes
            for resource in node.resource_requirements
        }
        resource_limits = {
            name: runtime_policy.resource_limits[name]
            for name in sorted(used_resources)
            if name in runtime_policy.resource_limits
        }
        if assessment.parallel_node_group:
            resource_cap = resource_limited_concurrency(
                node_ids=assessment.parallel_node_group,
                nodes=assessment.nodes,
                runtime_policy=runtime_policy,
            )
            selected_concurrency = max(
                1,
                min(
                    runtime_policy.max_concurrency_limit,
                    len(assessment.nodes),
                    len(assessment.parallel_node_group),
                    resource_cap,
                ),
            )

    validation_issues = list(assessment.issues)
    validation_status: str
    if validation_issues:
        validation_status = "invalid"
    elif mode == "dag" and has_parallel_frontier and not safe_parallel:
        validation_status = "pending"
        validation_issues.append(
            TaskPlanningValidationIssue(
                path="graph.parallelism",
                message=(
                    "依赖图存在并发候选，但 Task 规则无法证明这些分支是独立只读检索；"
                    "候选图可审查，执行保持阻断。"
                ),
            )
        )
    else:
        validation_status = "valid"

    if mode == "dag" and graph is not None and validation_status != "invalid":
        final_graph = TaskGraph(
            edges=[edge.model_copy(deep=True) for edge in candidate.edges],
            input_bindings={
                node_id: {
                    name: binding.model_copy(deep=True)
                    for name, binding in named.items()
                }
                for node_id, named in candidate.input_bindings.items()
            },
            max_concurrency=selected_concurrency,
            resource_limits=resource_limits,
        )
        try:
            validate_task_graph(final_graph, assessment.nodes)
        except TaskGraphValidationError as error:
            validation_status = "invalid"
            validation_issues.append(
                TaskPlanningValidationIssue(
                    path=error.path,
                    message=error.message,
                )
            )

    reason_parts = [f"模型建议（仅供审查）：{candidate.reason}"]
    if strict_linear and not assessment.issues:
        reason_parts.append(
            "Task 校验确认依赖为严格单链，推荐按现有 SOP 顺序执行。"
        )
    elif safe_parallel and not assessment.issues:
        reason_parts.append(
            "Task 校验确认存在独立只读检索分支，且汇合节点绑定覆盖全部前驱；推荐 DAG。"
        )
    elif mode == "dag" and not assessment.issues:
        reason_parts.append(
            "保留 DAG 候选以维护节点依赖和输入语义；当前规则未确认可安全并行，执行保持阻断。"
        )

    execution_allowed = validation_status == "valid"
    authorization_reason = _execution_authorization_reason(
        mode=mode,
        execution_allowed=execution_allowed,
        assessment=assessment,
        runtime_policy=runtime_policy,
        template_contract=template_contract,
        selected_concurrency=selected_concurrency,
    )
    if authorization_reason is not None:
        reason_parts.append(authorization_reason)
        execution_allowed = False

    if assessment.effectful_node_ids:
        reason_parts.append(
            "以下 Tool 节点具有外部副作用或缺少只读证明，必须经过既有权限确认；"
            "任何重跑仍由现有 Run 控制门禁决定："
            + ", ".join(assessment.effectful_node_ids)
            + "。"
        )
        execution_allowed = False

    return TaskPlanningDecision(
        suggested_title=candidate.suggested_title,
        intent=candidate.intent,
        planner_suggested_mode=candidate.recommended_mode,
        planner_reason=candidate.reason,
        recommended_mode=mode,
        reason="\n".join(reason_parts),
        nodes=[node.model_copy(deep=True) for node in assessment.nodes],
        edges=[edge.model_copy(deep=True) for edge in candidate.edges],
        input_bindings={
            node_id: {
                name: binding.model_copy(deep=True)
                for name, binding in named.items()
            }
            for node_id, named in candidate.input_bindings.items()
        },
        max_concurrency=selected_concurrency,
        resource_limits=resource_limits,
        validation_status=validation_status,
        execution_allowed=execution_allowed,
        validation_issues=validation_issues,
        approval_required_node_ids=list(assessment.effectful_node_ids),
        requires_existing_rerun_gate=bool(assessment.effectful_node_ids),
        source_template_id=(
            template_contract.source_template_id
            if template_contract is not None
            else None
        ),
        source_template_version=(
            template_contract.source_template_version
            if template_contract is not None
            else None
        ),
    )


def _is_strict_linear(assessment: TaskDependencyAssessment) -> bool:
    if assessment.issues or assessment.graph is None:
        return False
    if assessment.graph.input_bindings:
        return False
    ordered_ids = [node.id for node in assessment.ordered_nodes]
    expected = set(zip(ordered_ids, ordered_ids[1:]))
    actual = {
        (edge.source_node_id, edge.target_node_id)
        for edge in assessment.graph.edges
    }
    return len(assessment.nodes) == 1 and not actual or actual == expected


def _has_parallel_frontier(assessment: TaskDependencyAssessment) -> bool:
    if assessment.graph is None:
        return False
    parents = {node.id: set() for node in assessment.nodes}
    for edge in assessment.graph.edges:
        if edge.target_node_id in parents and edge.source_node_id in parents:
            parents[edge.target_node_id].add(edge.source_node_id)
    remaining = set(parents)
    completed: set[str] = set()
    while remaining:
        ready = {
            node_id
            for node_id in remaining
            if parents[node_id] <= completed
        }
        if not ready:
            return False
        if len(ready) > 1:
            return True
        completed.update(ready)
        remaining.difference_update(ready)
    return False


def _execution_authorization_reason(
    *,
    mode: str,
    execution_allowed: bool,
    assessment: TaskDependencyAssessment,
    runtime_policy: TaskPlanningRuntimePolicy,
    template_contract: TaskPlanningTemplateContract | None,
    selected_concurrency: int,
) -> str | None:
    if not execution_allowed:
        return None
    if mode == "sop":
        if not runtime_policy.sop_execution_enabled:
            return "当前运行环境尚未授权 SOP 执行。"
        return _tool_authorization_reason(assessment, runtime_policy)

    if not runtime_policy.dag_execution_enabled:
        return (
            "当前运行环境未授权 DAG 执行；保留候选供审查，不自动降级到 SOP，"
            "因为分支输入或汇合绑定可能改变业务语义。"
        )

    if template_contract is None:
        return _tool_authorization_reason(assessment, runtime_policy)

    source = (
        template_contract.source_template_id,
        template_contract.source_template_version,
    )
    allowed_scopes = {
        (item.template_id, item.template_version)
        for item in runtime_policy.scenario_execution_allowlist
    }
    if source not in allowed_scopes:
        return (
            "版本化模板 DAG 未获得运行环境的精确 ID/版本授权；候选仅供审查。"
        )
    if source[0] == "s01_project_init" and source[1] == "3.0.0":
        if (
            not assessment.s01_e2_profile
            or selected_concurrency != 3
            or assessment.graph is None
            or assessment.graph.resource_limits
        ):
            return (
                "S01 v3 仅识别 E2 已验收的精确并行图、字段绑定和并发度 3。"
            )
        return _tool_authorization_reason(assessment, runtime_policy)

    return (
        "版本化场景非线性图继续受既有执行阻断；只有精确的 S01 v3 E2 图"
        "可由此 Planner 识别，其他场景或版本不得扩展授权。"
    )


def _tool_authorization_reason(
    assessment: TaskDependencyAssessment,
    runtime_policy: TaskPlanningRuntimePolicy,
) -> str | None:
    required = {
        node.id for node in assessment.nodes if node.type.value == "tool"
    }
    missing = required - set(runtime_policy.authorized_tool_node_ids)
    if missing:
        return (
            "Task 运行权限门禁尚未为以下 Tool 节点返回授权结果："
            + ", ".join(sorted(missing))
            + "。"
        )
    return None
