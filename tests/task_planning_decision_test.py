"""Task P1 deterministic checks for SOP/DAG planning decisions."""

from __future__ import annotations

from typing import Any

import pytest

from examples.agent_service.task._dag import (
    build_named_input_values,
    validate_task_graph,
)
from examples.agent_service.task._models import (
    AgentStepConfig,
    NodeRunRecord,
    NodeRunStatus,
    RunStatus,
    StepType,
    TaskGraph,
    TaskGraphEdge,
    TaskGraphInputBinding,
    TaskNode,
    TaskRunRecord,
    TaskToolSchema,
    ToolStepConfig,
)
from examples.agent_service.task._planning_contract import (
    TaskPlanningCandidate,
    TaskPlanningNode,
    TaskPlanningRuntimePolicy,
    TaskPlanningTemplateContract,
)
from examples.agent_service.task._planning_decision import decide_task_plan
from examples.agent_service.task._template_compiler import TaskTemplateCompiler
from examples.agent_service.task._template_registry import TemplateRegistry


def _agent(node_id: str, name: str, prompt: str, order: int) -> TaskPlanningNode:
    return TaskPlanningNode(
        id=node_id,
        name=name,
        type=StepType.AGENT,
        config=AgentStepConfig(prompt=prompt),
        order=order,
    )


def _tool(
    node_id: str,
    name: str,
    tool_name: str,
    order: int,
    *,
    arguments: dict[str, Any] | None = None,
    capability_key: str | None = None,
) -> TaskPlanningNode:
    return TaskPlanningNode(
        id=node_id,
        name=name,
        type=StepType.TOOL,
        config=ToolStepConfig(
            tool_name=tool_name,
            arguments=arguments or {"query": "{{user_input}}"},
        ),
        capability_key=capability_key,
        order=order,
    )


def _candidate(
    nodes: list[TaskPlanningNode],
    edges: list[tuple[str, str]],
    *,
    mode: str = "dag",
    bindings: dict[str, dict[str, tuple[str, str]]] | None = None,
) -> TaskPlanningCandidate:
    return TaskPlanningCandidate(
        suggested_title="规划候选",
        intent="依据用户目标完成结果",
        recommended_mode=mode,
        reason="模型认为这些节点可以并行。",
        nodes=nodes,
        edges=[
            TaskGraphEdge(source_node_id=source, target_node_id=target)
            for source, target in edges
        ],
        input_bindings={
            target: {
                name: TaskGraphInputBinding(
                    source_node_id=source,
                    source_value=source_value,
                )
                for name, (source, source_value) in named.items()
            }
            for target, named in (bindings or {}).items()
        },
    )


def _retrieval_tools(*names: str) -> list[TaskToolSchema]:
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    return [
        TaskToolSchema(
            name=name,
            description="只读检索",
            input_schema=schema,
            is_read_only=True,
        )
        for name in names
    ]


def _independent_retrieval_candidate() -> TaskPlanningCandidate:
    return _candidate(
        [
            _tool("projects", "检索相似项目", "project.search", 0),
            _tool("policies", "检索授权规则", "policy.search", 1),
            _agent("merge", "汇总检索结果", "综合两个已绑定来源，形成结果。", 2),
        ],
        [("projects", "merge"), ("policies", "merge")],
        bindings={
            "merge": {
                "project_records": ("projects", "output"),
                "policy_records": ("policies", "output"),
            }
        },
    )


def test_strict_linear_candidate_is_recommended_as_sop() -> None:
    candidate = _candidate(
        [
            _agent("analyze", "分析目标", "提取目标与约束。", 0),
            _agent("deliver", "形成结果", "结合前序结果形成交付。", 1),
        ],
        [("analyze", "deliver")],
        mode="dag",
    )

    decision = decide_task_plan(
        candidate,
        tool_schemas=[],
        runtime_policy=TaskPlanningRuntimePolicy(sop_execution_enabled=True),
    )

    assert decision.recommended_mode == "sop"
    assert decision.max_concurrency == 1
    assert decision.validation_status == "valid"
    assert decision.execution_allowed is True
    with pytest.raises(ValueError, match="SOP decisions"):
        decision.as_task_graph()


def test_two_verified_independent_read_only_branches_recommend_dag() -> None:
    candidate = _independent_retrieval_candidate()

    decision = decide_task_plan(
        candidate,
        tool_schemas=_retrieval_tools("project.search", "policy.search"),
        runtime_policy=TaskPlanningRuntimePolicy(
            dag_execution_enabled=True,
            authorized_tool_node_ids=["projects", "policies"],
            max_concurrency_limit=4,
        ),
    )

    assert decision.recommended_mode == "dag"
    assert decision.validation_status == "valid"
    assert decision.execution_allowed is True
    assert decision.max_concurrency == 2
    validate_task_graph(decision.as_task_graph(), decision.nodes)


def test_model_candidate_schema_cannot_claim_server_outcomes() -> None:
    schema = TaskPlanningCandidate.model_json_schema()
    properties = schema["properties"]

    assert "max_concurrency" not in properties
    assert "validation_status" not in properties
    assert "execution_allowed" not in properties
    with pytest.raises(ValueError):
        TaskPlanningCandidate.model_validate(
            {
                **_independent_retrieval_candidate().model_dump(mode="python"),
                "execution_allowed": True,
            }
        )


def test_parallelism_is_capped_by_server_resource_policy() -> None:
    candidate = _independent_retrieval_candidate()
    candidate.nodes = [
        node.model_copy(update={"resource_requirements": {"search": 1}})
        if node.id in {"projects", "policies"}
        else node
        for node in candidate.nodes
    ]

    decision = decide_task_plan(
        candidate,
        tool_schemas=_retrieval_tools("project.search", "policy.search"),
        runtime_policy=TaskPlanningRuntimePolicy(
            dag_execution_enabled=True,
            authorized_tool_node_ids=["projects", "policies"],
            max_concurrency_limit=6,
            resource_limits={"search": 1},
        ),
    )

    assert decision.validation_status == "valid"
    assert decision.max_concurrency == 1
    assert decision.resource_limits == {"search": 1}


def test_uncertain_parallel_candidate_stays_unexecutable_dag() -> None:
    candidate = _independent_retrieval_candidate()
    tools = _retrieval_tools("project.search", "policy.search")
    for tool in tools:
        tool.is_read_only = False

    decision = decide_task_plan(
        candidate,
        tool_schemas=tools,
        runtime_policy=TaskPlanningRuntimePolicy(dag_execution_enabled=True),
    )

    assert decision.recommended_mode == "dag"
    assert decision.validation_status == "pending"
    assert decision.execution_allowed is False
    assert decision.max_concurrency == 1
    assert decision.approval_required_node_ids == ["projects", "policies"]


def test_fan_in_bindings_resolve_every_predecessor_output() -> None:
    candidate = _independent_retrieval_candidate()
    decision = decide_task_plan(
        candidate,
        tool_schemas=_retrieval_tools("project.search", "policy.search"),
        runtime_policy=TaskPlanningRuntimePolicy(dag_execution_enabled=True),
    )
    graph = decision.as_task_graph()
    node_runs = [
        NodeRunRecord(
            node_id="projects",
            name="检索相似项目",
            prompt="",
            order=0,
            status=NodeRunStatus.SUCCEEDED,
            output="项目来源记录",
        ),
        NodeRunRecord(
            node_id="policies",
            name="检索授权规则",
            prompt="",
            order=1,
            status=NodeRunStatus.SUCCEEDED,
            output="规则来源记录",
        ),
        NodeRunRecord(
            node_id="merge",
            name="汇总检索结果",
            prompt="",
            order=2,
        ),
    ]

    assert build_named_input_values(graph, node_runs, "merge") == {
        "project_records": "项目来源记录",
        "policy_records": "规则来源记录",
    }


def test_cyclic_candidate_is_rejected() -> None:
    candidate = _candidate(
        [
            _tool("lookup", "检索资料", "project.search", 0),
            _agent("finish", "形成结果", "依据检索资料形成结果。", 1),
        ],
        [("lookup", "finish"), ("finish", "lookup")],
    )

    decision = decide_task_plan(
        candidate,
        tool_schemas=_retrieval_tools("project.search"),
        runtime_policy=TaskPlanningRuntimePolicy(dag_execution_enabled=True),
    )

    assert decision.validation_status == "invalid"
    assert decision.execution_allowed is False
    assert any("Dependency cycle" in issue.message for issue in decision.validation_issues)
    with pytest.raises(ValueError, match="Invalid planning decisions"):
        decision.as_task_graph()


def test_missing_tool_capability_cannot_be_executable() -> None:
    candidate = _candidate(
        [
            _tool("lookup", "检索资料", "missing.search", 0),
            _agent("finish", "形成结果", "依据检索资料形成结果。", 1),
        ],
        [("lookup", "finish")],
        mode="sop",
    )

    decision = decide_task_plan(
        candidate,
        tool_schemas=[],
        runtime_policy=TaskPlanningRuntimePolicy(sop_execution_enabled=True),
    )

    assert decision.validation_status == "invalid"
    assert decision.execution_allowed is False
    assert any(".tool_name" in issue.path for issue in decision.validation_issues)


def test_tool_plan_waits_for_server_permission_gate_result() -> None:
    candidate = _candidate(
        [
            _tool("lookup", "检索资料", "project.search", 0),
            _agent("finish", "形成结果", "依据检索资料形成结果。", 1),
        ],
        [("lookup", "finish")],
        mode="sop",
    )

    decision = decide_task_plan(
        candidate,
        tool_schemas=_retrieval_tools("project.search"),
        runtime_policy=TaskPlanningRuntimePolicy(sop_execution_enabled=True),
    )

    assert decision.validation_status == "valid"
    assert decision.execution_allowed is False
    assert "lookup" in decision.reason


def test_input_binding_to_non_ancestor_is_rejected() -> None:
    candidate = _candidate(
        [
            _tool("first", "检索项目", "project.search", 0),
            _tool("second", "检索规则", "policy.search", 1),
            _agent("merge", "汇总", "综合输入并形成结果。", 2),
        ],
        [("first", "merge")],
        bindings={"merge": {"policy_records": ("second", "output")}},
    )

    decision = decide_task_plan(
        candidate,
        tool_schemas=_retrieval_tools("project.search", "policy.search"),
        runtime_policy=TaskPlanningRuntimePolicy(dag_execution_enabled=True),
    )

    assert decision.validation_status == "invalid"
    assert decision.execution_allowed is False
    assert any(
        "explicit dependency ancestor" in issue.message
        for issue in decision.validation_issues
    )


def test_side_effect_tool_still_requires_existing_approval_and_rerun_gates() -> None:
    candidate = _candidate(
        [
            _tool(
                "submit",
                "提交外部申请",
                "application.submit",
                0,
                arguments={"request": "{{user_input}}"},
            ),
            _agent("finish", "整理提交结果", "说明提交结果及后续事项。", 1),
        ],
        [("submit", "finish")],
        mode="sop",
    )
    tool = TaskToolSchema(
        name="application.submit",
        input_schema={
            "type": "object",
            "properties": {"request": {"type": "string"}},
            "required": ["request"],
            "additionalProperties": False,
        },
        is_read_only=False,
    )

    decision = decide_task_plan(
        candidate,
        tool_schemas=[tool],
        runtime_policy=TaskPlanningRuntimePolicy(sop_execution_enabled=True),
    )

    assert decision.recommended_mode == "sop"
    assert decision.validation_status == "valid"
    assert decision.approval_required_node_ids == ["submit"]
    assert decision.requires_existing_rerun_gate is True
    assert decision.execution_allowed is False
    assert "submit" in decision.reason


def test_unapproved_versioned_template_dag_is_never_executable() -> None:
    template, contract, candidate, tools = _s02_non_linear_template_candidate()

    decision = decide_task_plan(
        candidate,
        tool_schemas=tools,
        runtime_policy=TaskPlanningRuntimePolicy(
            dag_execution_enabled=True,
            scenario_execution_allowlist=[
                {
                    "template_id": template.template_id,
                    "template_version": template.template_version,
                }
            ],
        ),
        template_contract=contract,
    )

    assert decision.recommended_mode == "dag"
    assert decision.validation_status == "valid"
    assert decision.execution_allowed is False
    assert decision.source_template_id == "s02_project_planning"
    assert decision.source_template_version == "5.1.0"
    assert "S01 v3 E2" in decision.reason


def test_s01_v3_recognizes_only_the_exact_e2_parallel_profile() -> None:
    registry = TemplateRegistry()
    template = registry.get_template_version("s01_project_init", "3.0.0")
    assert template is not None
    compiled = TaskTemplateCompiler().compile_nodes(
        template,
        [],
        require_tool_bindings=False,
    )
    contract = TaskPlanningTemplateContract.from_compiled(template, compiled)
    nodes = [
        TaskPlanningNode.model_validate(node.model_dump(mode="python"))
        for node in compiled.nodes
    ]
    ids = contract.step_bindings
    edges = [
        (ids["collect_project_input"], ids["extract_project_profile"]),
        (ids["collect_project_input"], ids["search_similar_projects"]),
        (ids["collect_project_input"], ids["check_project_rules"]),
        (ids["extract_project_profile"], ids["draft_initiation_recommendation"]),
        (ids["search_similar_projects"], ids["draft_initiation_recommendation"]),
        (ids["check_project_rules"], ids["draft_initiation_recommendation"]),
        (ids["draft_initiation_recommendation"], ids["review_project_facts"]),
        (ids["review_project_facts"], ids["produce_initiation_card"]),
    ]
    bindings = {
        ids["search_similar_projects"]: {
            name: (ids["collect_project_input"], "output")
            for name in ("project_name", "project_brief", "industry")
        },
        ids["check_project_rules"]: {
            name: (ids["collect_project_input"], "output")
            for name in ("project_brief", "industry", "scope")
        },
        ids["draft_initiation_recommendation"]: {
            "project_profile": (ids["extract_project_profile"], "output"),
            "similar_projects": (ids["search_similar_projects"], "result"),
            "rule_candidates": (ids["check_project_rules"], "result"),
        },
    }
    candidate = _candidate(nodes, edges, bindings=bindings)
    tools = [
        TaskToolSchema(
            name=node.config.tool_name,
            input_schema={},
            is_read_only=True,
        )
        for node in nodes
        if isinstance(node.config, ToolStepConfig)
    ]

    decision = decide_task_plan(
        candidate,
        tool_schemas=tools,
        runtime_policy=TaskPlanningRuntimePolicy(
            dag_execution_enabled=True,
            scenario_execution_allowlist=[
                {"template_id": "s01_project_init", "template_version": "3.0.0"}
            ],
            authorized_tool_node_ids=[
                ids["search_similar_projects"],
                ids["check_project_rules"],
            ],
            max_concurrency_limit=3,
        ),
        template_contract=contract,
    )

    assert decision.recommended_mode == "dag"
    assert decision.validation_status == "valid"
    assert decision.max_concurrency == 3
    assert decision.execution_allowed is True


def test_planning_decision_does_not_mutate_existing_run_snapshot() -> None:
    candidate = _independent_retrieval_candidate()
    run_nodes = [TaskNode.model_validate(node.model_dump(mode="python")) for node in candidate.nodes]
    run = TaskRunRecord(
        task_id="task-1",
        user_id="user-1",
        task_revision=3,
        nodes=run_nodes,
        node_runs=[
            NodeRunRecord(
                node_id=node.id,
                name=node.name,
                prompt=node.prompt,
                order=node.order,
            )
            for node in run_nodes
        ],
        input="already frozen input",
        status=RunStatus.QUEUED,
        engine="dag",
        graph=TaskGraph(
            edges=[
                TaskGraphEdge(source_node_id="projects", target_node_id="merge"),
                TaskGraphEdge(source_node_id="policies", target_node_id="merge"),
            ],
            input_bindings={
                "merge": {
                    "project_records": TaskGraphInputBinding(
                        source_node_id="projects",
                    ),
                    "policy_records": TaskGraphInputBinding(
                        source_node_id="policies",
                    ),
                }
            },
            max_concurrency=2,
        ),
    )
    snapshot_before = run.model_dump_json()

    decide_task_plan(
        candidate,
        tool_schemas=_retrieval_tools("project.search", "policy.search"),
        runtime_policy=TaskPlanningRuntimePolicy(
            dag_execution_enabled=True,
            authorized_tool_node_ids=["projects", "policies"],
        ),
    )

    assert run.model_dump_json() == snapshot_before


class _DeterministicCandidatePlanner:
    """Model-free stand-in that returns a fixed structured proposal."""

    def __init__(self, candidate: TaskPlanningCandidate) -> None:
        self.candidate = candidate
        self.calls = 0

    def propose(self) -> TaskPlanningCandidate:
        self.calls += 1
        return self.candidate


def test_deterministic_planner_double_exercises_decision_without_model() -> None:
    planner = _DeterministicCandidatePlanner(_independent_retrieval_candidate())

    decision = decide_task_plan(
        planner.propose(),
        tool_schemas=_retrieval_tools("project.search", "policy.search"),
        runtime_policy=TaskPlanningRuntimePolicy(dag_execution_enabled=True),
    )

    assert planner.calls == 1
    assert decision.recommended_mode == "dag"
    assert decision.validation_status == "valid"


def _s02_non_linear_template_candidate() -> tuple[Any, Any, Any, list[TaskToolSchema]]:
    template = TemplateRegistry().get_template_version(
        "s02_project_planning",
        "5.1.0",
    )
    assert template is not None
    compiled = TaskTemplateCompiler().compile_nodes(
        template,
        [],
        require_tool_bindings=False,
    )
    contract = TaskPlanningTemplateContract.from_compiled(template, compiled)
    nodes = [
        TaskPlanningNode.model_validate(node.model_dump(mode="python"))
        for node in compiled.nodes
    ]
    ids = [node.id for node in nodes]
    candidate = _candidate(
        nodes,
        [
            (ids[0], ids[1]),
            (ids[0], ids[2]),
            (ids[1], ids[2]),
            (ids[2], ids[3]),
            (ids[3], ids[4]),
        ],
        bindings={
            ids[2]: {
                "collected_material": (ids[0], "output"),
                "template_result": (ids[1], "output"),
            }
        },
    )
    tools = [
        TaskToolSchema(name=node.config.tool_name, input_schema={}, is_read_only=True)
        for node in nodes
        if isinstance(node.config, ToolStepConfig)
    ]
    return template, contract, candidate, tools
