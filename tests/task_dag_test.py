# -*- coding: utf-8 -*-
"""Task-local DAG contract tests; no database or AgentScope runtime required."""

import asyncio

import pytest

from examples.agent_service.task._dag import (
    TaskGraphValidationError,
    build_inputs_by_node_id,
    build_named_input_values,
    descendants,
    ready_node_ids,
    resources_fit,
    topological_node_ids,
    validate_task_graph,
)
from examples.agent_service.task._models import (
    AgentStepConfig,
    CreateTaskRequest,
    NodeRunRecord,
    NodeRunStatus,
    RunStatus,
    TaskGraph,
    TaskGraphEdge,
    TaskGraphInputBinding,
    TaskNode,
    TaskRunRequest,
    TaskRunRecord,
)
from examples.agent_service.task._service import (
    TaskService,
    TaskWorkflowConflictError,
)
from examples.agent_service.task._store import TaskStore


def _nodes(*node_ids: str) -> list[TaskNode]:
    return [
        TaskNode(
            id=node_id,
            name=node_id,
            order=index,
            config=AgentStepConfig(prompt=f"Do {node_id}"),
        )
        for index, node_id in enumerate(node_ids)
    ]


def _edge(source: str, target: str) -> TaskGraphEdge:
    return TaskGraphEdge(source_node_id=source, target_node_id=target)


def test_graph_allows_multiple_roots_and_sinks_and_uses_stable_order() -> None:
    nodes = _nodes("A", "B", "C", "D")
    graph = TaskGraph(edges=[_edge("A", "D"), _edge("B", "D")])

    validate_task_graph(graph, nodes)

    assert topological_node_ids(graph, nodes) == ["A", "B", "C", "D"]
    assert ready_node_ids(
        graph,
        nodes,
        succeeded_node_ids=set(),
    ) == ["A", "B", "C"]
    assert ready_node_ids(
        graph,
        nodes,
        succeeded_node_ids={"A", "B"},
    ) == ["C", "D"]


@pytest.mark.parametrize(
    ("edges", "message"),
    [
        ([('A', 'A')], "cannot depend on itself"),
        ([('A', 'B'), ('A', 'B')], "Duplicate dependency edge"),
        ([('A', 'missing')], "Unknown Task node id"),
        ([('A', 'B'), ('B', 'C'), ('C', 'A')], "Dependency cycle"),
    ],
)
def test_graph_rejects_invalid_edges(edges, message: str) -> None:
    graph = TaskGraph(edges=[_edge(source, target) for source, target in edges])

    with pytest.raises(TaskGraphValidationError, match=message):
        validate_task_graph(graph, _nodes("A", "B", "C"))


def test_graph_input_binding_must_be_a_declared_dependency_ancestor() -> None:
    nodes = _nodes("A", "B", "C")
    graph = TaskGraph(
        edges=[_edge("A", "C")],
        input_bindings={
            "C": {"summary": TaskGraphInputBinding(source_node_id="B")}
        },
    )

    with pytest.raises(TaskGraphValidationError, match="dependency ancestor"):
        validate_task_graph(graph, nodes)


def test_fan_in_inputs_are_named_and_come_from_persisted_node_results() -> None:
    graph = TaskGraph(
        edges=[_edge("A", "D"), _edge("B", "D")],
        input_bindings={
            "D": {
                "right": TaskGraphInputBinding(source_node_id="B"),
                "left": TaskGraphInputBinding(source_node_id="A"),
                "structured": TaskGraphInputBinding(
                    source_node_id="A",
                    source_value="result",
                ),
            }
        },
    )
    node_runs = [
        NodeRunRecord(
            node_id="A",
            name="A",
            prompt="",
            order=0,
            status=NodeRunStatus.SUCCEEDED,
            output="alpha",
            result={"ok": True},
        ),
        NodeRunRecord(
            node_id="B",
            name="B",
            prompt="",
            order=1,
            status=NodeRunStatus.SUCCEEDED,
            output="beta",
        ),
    ]

    assert build_inputs_by_node_id(graph, node_runs, "D") == {
        "A": {"output": "alpha", "result": {"ok": True}},
        "B": {"output": "beta", "result": None},
    }
    assert build_named_input_values(graph, node_runs, "D") == {
        "left": "alpha",
        "right": "beta",
        "structured": {"ok": True},
    }
    assert descendants(graph, "A") == {"D"}


def test_graph_resource_requirements_must_fit_declared_capacity() -> None:
    nodes = _nodes("A", "B")
    nodes[0].resource_requirements = {"workspace": 2}
    graph = TaskGraph(resource_limits={"workspace": 1})

    with pytest.raises(TaskGraphValidationError, match="exceeds capacity"):
        validate_task_graph(graph, nodes)

    nodes[0].resource_requirements = {"workspace": 1}
    nodes[1].resource_requirements = {"workspace": 1}
    graph = TaskGraph(resource_limits={"workspace": 1})
    validate_task_graph(graph, nodes)
    assert resources_fit(graph, nodes, ["A"])
    assert not resources_fit(graph, nodes, ["A", "B"])


def test_old_run_json_defaults_to_legacy_engine_and_no_graph() -> None:
    old_payload = {
        "id": "run-old",
        "task_id": "task-old",
        "user_id": "owner",
        "task_revision": 1,
        "nodes": [],
        "node_runs": [],
        "status": RunStatus.SUCCEEDED.value,
    }

    run = TaskRunRecord.model_validate(old_payload)

    assert run.engine == "legacy"
    assert run.graph is None


def test_graph_task_is_saved_but_not_sent_through_linear_sop() -> None:
    async def scenario() -> None:
        store = TaskStore()
        service = TaskService(store)
        task = await service.create_task(
            "owner",
            CreateTaskRequest(
                goal="Join two analyses",
                nodes=_nodes("A", "B", "D"),
                graph=TaskGraph(
                    edges=[_edge("A", "D"), _edge("B", "D")],
                    input_bindings={
                        "D": {
                            "a": TaskGraphInputBinding(source_node_id="A"),
                            "b": TaskGraphInputBinding(source_node_id="B"),
                        }
                    },
                    max_concurrency=2,
                ),
            ),
        )
        saved = await store.get_task("owner", task.id)
        assert saved is not None
        assert saved.graph == task.graph
        with pytest.raises(TaskWorkflowConflictError, match="DAG execution is disabled"):
            await service.create_run("owner", task.id, TaskRunRequest())

    asyncio.run(scenario())
