# -*- coding: utf-8 -*-
"""Deterministic Task dependency, binding, and parallel-safety checks."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
import re
from typing import Any

from pydantic import ValidationError

from ._capability_contracts import (
    is_task_native_capability,
    task_tool_has_external_side_effect,
)
from ._dag import (
    TaskGraphValidationError,
    ancestors,
    topological_node_ids,
    validate_task_graph,
)
from ._models import (
    InputStepConfig,
    StepType,
    TaskGraph,
    TaskGraphEdge,
    TaskGraphInputBinding,
    TaskNode,
    TaskToolSchema,
    ToolStepConfig,
)
from ._scenario_runtime import native_tool_schema
from ._plan_validation import validate_task_plan
from ._planning_contract import (
    TaskPlanningCandidate,
    TaskPlanningNode,
    TaskPlanningRuntimePolicy,
    TaskPlanningTemplateContract,
    TaskPlanningValidationIssue,
    available_tool_map,
)


_VARIABLE_PATTERN = re.compile(r"\{\{([^{}]+)\}\}")
_S01_E2_STEP_KEYS = (
    "collect_project_input",
    "extract_project_profile",
    "search_similar_projects",
    "check_project_rules",
    "draft_initiation_recommendation",
    "review_project_facts",
    "produce_initiation_card",
)


@dataclass
class TaskDependencyAssessment:
    """Results of graph and capability checks before mode authorization."""

    nodes: list[TaskPlanningNode]
    graph: TaskGraph | None
    issues: list[TaskPlanningValidationIssue] = field(default_factory=list)
    tool_by_name: dict[str, TaskToolSchema] = field(default_factory=dict)
    effectful_node_ids: list[str] = field(default_factory=list)
    confirmed_read_only_ids: set[str] = field(default_factory=set)
    parallel_node_group: tuple[str, ...] = ()
    s01_e2_profile: bool = False
    ordered_nodes: list[TaskPlanningNode] = field(default_factory=list)


def assess_task_dependencies(
    candidate: TaskPlanningCandidate,
    *,
    tool_schemas: list[TaskToolSchema] | None,
    runtime_policy: TaskPlanningRuntimePolicy,
    template_contract: TaskPlanningTemplateContract | None = None,
) -> TaskDependencyAssessment:
    """Validate a candidate using the existing Task and TaskGraph contracts."""

    # TaskRecord normalizes display order to list order at persistence time.
    # Normalize before validation so the same deterministic order is reviewed.
    nodes = [
        node.model_copy(update={"order": index}, deep=True)
        for index, node in enumerate(candidate.nodes)
    ]
    issues: list[TaskPlanningValidationIssue] = []
    tool_schemas = list(tool_schemas or [])
    tool_schemas = _include_task_native_tools(nodes, tool_schemas)
    tool_by_name = available_tool_map(tool_schemas)
    duplicated_tools = _duplicate_tool_names(tool_schemas)
    for name in duplicated_tools:
        issues.append(
            TaskPlanningValidationIssue(
                path="tools",
                message=f"会话工具清单包含重复名称，无法唯一绑定：{name}",
            )
        )

    if template_contract is not None:
        issues.extend(_validate_frozen_template_nodes(nodes, template_contract))

    required_resources = {
        resource
        for node in nodes
        for resource in node.resource_requirements
    }
    graph_resource_limits = {
        resource: runtime_policy.resource_limits[resource]
        for resource in sorted(required_resources)
        if resource in runtime_policy.resource_limits
    }
    graph: TaskGraph | None = None
    try:
        graph = TaskGraph(
            edges=[edge.model_copy(deep=True) for edge in candidate.edges],
            input_bindings={
                node_id: {
                    name: binding.model_copy(deep=True)
                    for name, binding in named.items()
                }
                for node_id, named in candidate.input_bindings.items()
            },
            # The candidate schema deliberately has no LLM-owned concurrency.
            max_concurrency=1,
            resource_limits=graph_resource_limits,
        )
        validate_task_graph(graph, nodes)
    except TaskGraphValidationError as error:
        issues.append(
            TaskPlanningValidationIssue(path=error.path, message=error.message)
        )
    except ValidationError as error:
        for item in error.errors(include_url=False)[:16]:
            path = ".".join(str(part) for part in item["loc"]) or "graph"
            issues.append(
                TaskPlanningValidationIssue(
                    path=path,
                    message="DAG 数据不符合现有 Task Graph Schema。",
                )
            )

    if graph is not None:
        issues.extend(_validate_bindings(nodes, graph, template_contract))

    node_order = nodes
    if graph is not None:
        try:
            ordered_ids = topological_node_ids(graph, nodes)
            by_id = {node.id: node for node in nodes}
            node_order = [
                by_id[node_id].model_copy(update={"order": index}, deep=True)
                for index, node_id in enumerate(ordered_ids)
            ]
        except TaskGraphValidationError:
            pass

    issues.extend(
        _validate_task_nodes(
            node_order,
            tool_schemas=tool_schemas,
            template_contract=template_contract,
        )
    )
    if graph is not None:
        issues.extend(_validate_task_variables(node_order, graph))

    effectful_node_ids: list[str] = []
    confirmed_read_only_ids: set[str] = set()
    for node in nodes:
        if node.type is not StepType.TOOL:
            continue
        if not isinstance(node.config, ToolStepConfig):
            continue  # TaskNode validation already rejects this combination.
        tool = tool_by_name.get(node.config.tool_name)
        if tool is None:
            continue
        if task_tool_has_external_side_effect(
            tool,
            capability_key=node.capability_key,
        ):
            effectful_node_ids.append(node.id)
        else:
            confirmed_read_only_ids.add(node.id)

    assessment = TaskDependencyAssessment(
        nodes=nodes,
        graph=graph,
        issues=_unique_issues(issues),
        tool_by_name=tool_by_name,
        effectful_node_ids=effectful_node_ids,
        confirmed_read_only_ids=confirmed_read_only_ids,
        ordered_nodes=node_order,
    )
    if graph is not None and not assessment.issues:
        assessment.s01_e2_profile = is_s01_e2_parallel_profile(
            nodes=nodes,
            graph=graph,
            template_contract=template_contract,
        )
        if assessment.s01_e2_profile:
            step_ids = [
                template_contract.step_bindings[key]
                for key in _S01_E2_STEP_KEYS
            ]
            assessment.parallel_node_group = (
                step_ids[1],
                step_ids[2],
                step_ids[3],
            )
        else:
            assessment.parallel_node_group = find_safe_read_only_merge_group(
                nodes=nodes,
                graph=graph,
                tool_by_name=tool_by_name,
                confirmed_read_only_ids=confirmed_read_only_ids,
            )
    return assessment


def is_s01_e2_parallel_profile(
    *,
    nodes: list[TaskPlanningNode] | list[TaskNode],
    graph: TaskGraph,
    template_contract: TaskPlanningTemplateContract | None,
) -> bool:
    """Recognize only the exact frozen S01 v3 graph accepted by E2."""

    if (
        template_contract is None
        or template_contract.source_template_id != "s01_project_init"
        or template_contract.source_template_version != "3.0.0"
        or tuple(
            step.step_key for step in template_contract.template.steps
        )
        != _S01_E2_STEP_KEYS
        or len(nodes) != 7
    ):
        return False

    step_ids = [
        template_contract.step_bindings[key] for key in _S01_E2_STEP_KEYS
    ]
    (
        collect_id,
        profile_id,
        similar_id,
        rules_id,
        draft_id,
        review_id,
        output_id,
    ) = step_ids
    expected_edges = {
        (collect_id, profile_id),
        (collect_id, similar_id),
        (collect_id, rules_id),
        (profile_id, draft_id),
        (similar_id, draft_id),
        (rules_id, draft_id),
        (draft_id, review_id),
        (review_id, output_id),
    }
    actual_edges = {
        (edge.source_node_id, edge.target_node_id) for edge in graph.edges
    }

    def binding(source_id: str, source_value: str) -> tuple[str, str]:
        return (source_id, source_value)

    expected_bindings = {
        similar_id: {
            field: binding(collect_id, "output")
            for field in ("project_name", "project_brief", "industry")
        },
        rules_id: {
            field: binding(collect_id, "output")
            for field in ("project_brief", "industry", "scope")
        },
        draft_id: {
            "project_profile": binding(profile_id, "output"),
            "similar_projects": binding(similar_id, "result"),
            "rule_candidates": binding(rules_id, "result"),
        },
    }
    actual_bindings = {
        target_id: {
            name: binding(item.source_node_id, item.source_value)
            for name, item in named.items()
        }
        for target_id, named in graph.input_bindings.items()
    }
    return bool(
        [node.id for node in nodes] == step_ids
        and len(graph.edges) == len(expected_edges)
        and actual_edges == expected_edges
        and actual_bindings == expected_bindings
        and not graph.resource_limits
    )


def find_safe_read_only_merge_group(
    *,
    nodes: list[TaskPlanningNode] | list[TaskNode],
    graph: TaskGraph,
    tool_by_name: dict[str, TaskToolSchema],
    confirmed_read_only_ids: set[str],
) -> tuple[str, ...]:
    """Find a parallel retrieval layer with verified read-only tools and a merge.

    A model explanation never counts as proof. The accepted pattern is a set of
    sibling Tool nodes using distinct confirmed read-only Tools, all fed by the
    same declared prerequisites and stable user/input-answer values, followed
    by an Agent or Python merge that explicitly binds every branch output.
    """

    node_by_id = {node.id: node for node in nodes}
    parents: dict[str, set[str]] = {node.id: set() for node in nodes}
    for edge in graph.edges:
        parents[edge.target_node_id].add(edge.source_node_id)
    layers = _topological_layers(nodes, parents)
    ancestors_by_node = {node_id: ancestors(graph, node_id) for node_id in node_by_id}
    input_nodes = {
        node.id: {
            question.key
            for question in node.config.questions
        }
        for node in nodes
        if node.type is StepType.INPUT
        and isinstance(node.config, InputStepConfig)
    }
    answer_sources: dict[str, set[str]] = defaultdict(set)
    for node_id, keys in input_nodes.items():
        for key in keys:
            answer_sources[key].add(node_id)

    candidates: list[tuple[str, ...]] = []
    parallel_layers = [layer for layer in layers if len(layer) > 1]
    if len(parallel_layers) != 1:
        return ()
    for layer in parallel_layers:
        branch_nodes = [node_by_id[node_id] for node_id in layer]
        if any(
            node.type is not StepType.TOOL
            or not isinstance(node.config, ToolStepConfig)
            or node.id not in confirmed_read_only_ids
            for node in branch_nodes
        ):
            return ()
        if len({node.config.tool_name for node in branch_nodes}) != len(layer):
            return ()
        shared_parents = parents[layer[0]]
        if any(parents[node_id] != shared_parents for node_id in layer[1:]):
            return ()
        if any(graph.input_bindings.get(node.id) for node in branch_nodes):
            return ()
        if not all(
            _branch_arguments_have_stable_inputs(
                node=node,
                ancestors_by_node=ancestors_by_node,
                answer_sources=answer_sources,
            )
            for node in branch_nodes
        ):
            return ()

        branch_ids = set(layer)
        bound_merges = [
            target_id
            for target_id in node_by_id
            if branch_ids == parents[target_id]
            and branch_ids
            <= {
                item.source_node_id
                for item in graph.input_bindings.get(target_id, {}).values()
            }
            and node_by_id[target_id].type
            in {StepType.AGENT, StepType.PYTHON}
        ]
        if len(bound_merges) != 1:
            return ()

        merge_id = bound_merges[0]
        allowed_nodes = set(branch_ids)
        for branch_id in branch_ids:
            allowed_nodes.update(ancestors_by_node[branch_id])
        allowed_nodes.update(_descendants(parents, merge_id))
        allowed_nodes.add(merge_id)
        if allowed_nodes != set(node_by_id):
            return ()

        parallel_layer = layers.index(layer)
        if any(
            len(other) > 1
            for index, other in enumerate(layers)
            if index != parallel_layer
        ):
            return ()
        candidates.append(tuple(layer))

    if not candidates:
        return ()
    return max(candidates, key=lambda group: (len(group), group))


def resource_limited_concurrency(
    *,
    node_ids: tuple[str, ...],
    nodes: list[TaskPlanningNode] | list[TaskNode],
    runtime_policy: TaskPlanningRuntimePolicy,
) -> int:
    """Compute the largest branch subset that fits server-owned capacities."""

    node_by_id = {node.id: node for node in nodes}
    group_nodes = [node_by_id[node_id] for node_id in node_ids]
    if not group_nodes:
        return 1
    for size in range(len(group_nodes), 0, -1):
        for subset in combinations(group_nodes, size):
            totals: dict[str, int] = defaultdict(int)
            for node in subset:
                for name, amount in node.resource_requirements.items():
                    totals[name] += amount
            if all(
                name in runtime_policy.resource_limits
                and amount <= runtime_policy.resource_limits[name]
                for name, amount in totals.items()
            ):
                return size
    return 1


def _validate_frozen_template_nodes(
    nodes: list[TaskPlanningNode],
    template_contract: TaskPlanningTemplateContract,
) -> list[TaskPlanningValidationIssue]:
    expected_ids = list(template_contract.step_bindings.values())
    if [node.id for node in nodes] != expected_ids:
        return [
            TaskPlanningValidationIssue(
                path="nodes",
                message=(
                    "版本化模板规划必须保留冻结模板的全部节点、ID 和顺序；"
                    "不得删除、重排或新增业务步骤。"
                ),
            )
        ]
    expected = [
        node.model_dump(mode="json") for node in template_contract.frozen_nodes
    ]
    actual = [node.model_dump(mode="json") for node in nodes]
    if actual != expected:
        return [
            TaskPlanningValidationIssue(
                path="nodes",
                message=(
                    "版本化模板节点与冻结来源不一致；不得通过修改节点配置、"
                    "能力、输出契约或审批要求改变模板业务契约。"
                ),
            )
        ]
    return []


def _validate_bindings(
    nodes: list[TaskPlanningNode],
    graph: TaskGraph,
    template_contract: TaskPlanningTemplateContract | None,
) -> list[TaskPlanningValidationIssue]:
    issues: list[TaskPlanningValidationIssue] = []
    node_by_id = {node.id: node for node in nodes}
    parents: dict[str, set[str]] = {node.id: set() for node in nodes}
    for edge in graph.edges:
        if edge.source_node_id in parents and edge.target_node_id in parents:
            parents[edge.target_node_id].add(edge.source_node_id)

    s01_targets: set[str] = set()
    s01_e2_profile = is_s01_e2_parallel_profile(
        nodes=nodes,
        graph=graph,
        template_contract=template_contract,
    )
    if template_contract is not None and (
        template_contract.source_template_id == "s01_project_init"
        and template_contract.source_template_version == "3.0.0"
    ):
        s01_targets = {
            template_contract.step_bindings[key]
            for key in (
                "search_similar_projects",
                "check_project_rules",
                "draft_initiation_recommendation",
            )
            if key in template_contract.step_bindings
        }

    for target_id, named in graph.input_bindings.items():
        target = node_by_id.get(target_id)
        if target is None:
            continue  # The existing graph validator reports the unknown id.
        bound_sources = {item.source_node_id for item in named.values()}
        if len(parents[target_id]) > 1 and not parents[target_id] <= bound_sources:
            missing = sorted(parents[target_id] - bound_sources)
            issues.append(
                TaskPlanningValidationIssue(
                    path=f"graph.input_bindings.{target_id}",
                    message=(
                        "多前驱汇合必须显式绑定每个直接前驱的输入；缺少："
                        + ", ".join(missing)
                    ),
                )
            )
        if target.type not in {StepType.AGENT, StepType.PYTHON} and not (
            target.type is StepType.TOOL and target.id in s01_targets
        ):
            issues.append(
                TaskPlanningValidationIssue(
                    path=f"graph.input_bindings.{target_id}",
                    message=(
                        "现有 Task 通用输入绑定仅支持 Agent/Python 汇合节点；"
                        "Tool 绑定只接受冻结 S01 v3 已验收字段。"
                    ),
                )
            )
        if target.type is StepType.TOOL and target.id not in s01_targets:
            continue
        if target.type is StepType.TOOL and target.id in s01_targets:
            allowed_fields = {
                template_contract.step_bindings["search_similar_projects"]: {
                    "project_name",
                    "project_brief",
                    "industry",
                },
                template_contract.step_bindings["check_project_rules"]: {
                    "project_brief",
                    "industry",
                    "scope",
                },
                template_contract.step_bindings[
                    "draft_initiation_recommendation"
                ]: {
                    "project_profile",
                    "similar_projects",
                    "rule_candidates",
                },
            }
            if set(named) != allowed_fields.get(target_id, set()):
                issues.append(
                    TaskPlanningValidationIssue(
                        path=f"graph.input_bindings.{target_id}",
                        message="S01 v3 冻结字段绑定必须完整且逐字段匹配。",
                    )
                )
        for name, item in named.items():
            source = node_by_id.get(item.source_node_id)
            if source is None:
                continue  # The existing graph validator reports unknown source.
            if item.source_value == "result":
                contract = source.effective_output_contract
                if s01_e2_profile and source.capability_key in {
                    "project.similar_search",
                    "project.rule_check",
                }:
                    continue
                if (
                    contract is None
                    or contract.validation_mode != "json_schema"
                    or contract.downstream_binding != "validated_result"
                ):
                    issues.append(
                        TaskPlanningValidationIssue(
                            path=f"graph.input_bindings.{target_id}.{name}",
                            message=(
                                "result 绑定仅能读取通过结构化输出契约校验的前驱结果。"
                            ),
                        )
                    )
    return issues


def _validate_task_nodes(
    nodes: list[TaskPlanningNode],
    *,
    tool_schemas: list[TaskToolSchema],
    template_contract: TaskPlanningTemplateContract | None,
) -> list[TaskPlanningValidationIssue]:
    issues = [
        TaskPlanningValidationIssue(path=item.path, message=item.message)
        for item in validate_task_plan(
            {"nodes": nodes},
            tool_schemas,
            require_final_agent=True,
        )
    ]
    for index, node in enumerate(nodes):
        if node.type is StepType.TOOL and not isinstance(node.config, ToolStepConfig):
            issues.append(
                TaskPlanningValidationIssue(
                    path=f"nodes[{index}].config",
                    message="Tool 节点必须使用现有 ToolStepConfig。",
                )
            )
        if node.type is StepType.PYTHON and node.config.type != "python":
            issues.append(
                TaskPlanningValidationIssue(
                    path=f"nodes[{index}].config",
                    message="Python 节点必须使用现有 PythonStepConfig。",
                )
            )
    return issues


def _validate_task_variables(
    nodes: list[TaskPlanningNode],
    graph: TaskGraph,
) -> list[TaskPlanningValidationIssue]:
    issues: list[TaskPlanningValidationIssue] = []
    node_by_id = {node.id: node for node in nodes}
    answers_by_node: dict[str, set[str]] = {}
    answer_nodes: dict[str, set[str]] = defaultdict(set)
    for node in nodes:
        if node.type is StepType.INPUT and isinstance(node.config, InputStepConfig):
            keys = {question.key for question in node.config.questions}
            answers_by_node[node.id] = keys
            for key in keys:
                answer_nodes[key].add(node.id)
    ancestors_by_node = {node.id: ancestors(graph, node.id) for node in nodes}

    for index, node in enumerate(nodes):
        config = node.config.model_dump(mode="python")
        for text in _strings(config):
            for variable in _VARIABLE_PATTERN.findall(text):
                if variable == "user_input" or variable == "previous_output":
                    continue
                if variable == "previous_result":
                    incoming = {
                        edge.source_node_id
                        for edge in graph.edges
                        if edge.target_node_id == node.id
                    }
                    if len(incoming) == 1:
                        source = node_by_id[next(iter(incoming))]
                        contract = source.effective_output_contract
                        if (
                            contract is not None
                            and contract.validation_mode == "json_schema"
                            and contract.downstream_binding == "validated_result"
                        ):
                            continue
                    issues.append(
                        TaskPlanningValidationIssue(
                            path=f"nodes[{index}].config",
                            message=(
                                "previous_result 必须来自唯一前驱的结构化输出契约。"
                            ),
                        )
                    )
                    continue
                if variable.startswith("answers."):
                    key = variable[8:]
                    source_nodes = answer_nodes.get(key, set())
                    if source_nodes and source_nodes & ancestors_by_node[node.id]:
                        continue
                    issues.append(
                        TaskPlanningValidationIssue(
                            path=f"nodes[{index}].config",
                            message=f"输入变量 answers.{key} 没有合法 InputStep 前驱。",
                        )
                    )
                    continue
                issues.append(
                    TaskPlanningValidationIssue(
                        path=f"nodes[{index}].config",
                        message=f"Task 执行 Schema 不支持该输入变量：{variable}",
                    )
                )
    return issues


def _branch_arguments_have_stable_inputs(
    *,
    node: TaskPlanningNode | TaskNode,
    ancestors_by_node: dict[str, set[str]],
    answer_sources: dict[str, set[str]],
) -> bool:
    if not isinstance(node.config, ToolStepConfig):
        return False
    for text in _strings(node.config.arguments):
        for variable in _VARIABLE_PATTERN.findall(text):
            if variable == "user_input":
                continue
            if variable.startswith("answers."):
                key = variable[8:]
                if answer_sources.get(key, set()) & ancestors_by_node[node.id]:
                    continue
            return False
    return True


def _topological_layers(
    nodes: list[TaskPlanningNode] | list[TaskNode],
    parents: dict[str, set[str]],
) -> list[list[str]]:
    remaining = set(parents)
    completed: set[str] = set()
    layers: list[list[str]] = []
    order = {node.id: (node.order, node.id) for node in nodes}
    while remaining:
        ready = sorted(
            (
                node_id
                for node_id in remaining
                if parents[node_id] <= completed
            ),
            key=lambda node_id: order[node_id],
        )
        if not ready:
            return []
        layers.append(ready)
        completed.update(ready)
        remaining.difference_update(ready)
    return layers


def _descendants(
    parents: dict[str, set[str]],
    node_id: str,
) -> set[str]:
    children: dict[str, set[str]] = {key: set() for key in parents}
    for target, source_ids in parents.items():
        for source in source_ids:
            children[source].add(target)
    result: set[str] = set()
    pending = list(children.get(node_id, set()))
    while pending:
        current = pending.pop()
        if current in result:
            continue
        result.add(current)
        pending.extend(children.get(current, set()))
    return result


def _duplicate_tool_names(tools: list[TaskToolSchema]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for tool in tools:
        if tool.name in seen:
            duplicates.add(tool.name)
        seen.add(tool.name)
    return duplicates


def _include_task_native_tools(
    nodes: list[TaskPlanningNode],
    tool_schemas: list[TaskToolSchema],
) -> list[TaskToolSchema]:
    """Add Task-owned native capabilities through their existing schema port."""

    native_by_name: dict[str, TaskToolSchema] = {}
    for node in nodes:
        if node.type is not StepType.TOOL or not isinstance(
            node.config,
            ToolStepConfig,
        ):
            continue
        capability = node.capability_key
        if (
            capability is None
            or capability != node.config.tool_name
            or not is_task_native_capability(capability)
        ):
            continue
        native_by_name[capability] = TaskToolSchema(
            name=capability,
            description="Task 服务端提供的场景原生能力",
            input_schema=native_tool_schema(capability),
            is_read_only=True,
        )
    result = [tool for tool in tool_schemas if tool.name not in native_by_name]
    result.extend(native_by_name.values())
    return result


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _unique_issues(
    issues: list[TaskPlanningValidationIssue],
) -> list[TaskPlanningValidationIssue]:
    unique: dict[tuple[str, str], TaskPlanningValidationIssue] = {}
    for issue in issues:
        unique[(issue.path, issue.message)] = issue
    return list(unique.values())
