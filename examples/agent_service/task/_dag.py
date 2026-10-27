# -*- coding: utf-8 -*-
"""Task-owned graph validation and deterministic DAG helpers."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from ._models import NodeRunRecord, NodeRunStatus, TaskGraph, TaskNode


class TaskGraphValidationError(ValueError):
    """A graph contract error with a field path suitable for API responses."""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


def validate_task_graph(graph: TaskGraph, nodes: list[TaskNode]) -> None:
    """Validate graph references, cycles, bindings, and resource capacity.

    A graph may have any number of roots or terminal nodes. Those are valid
    fan-out/fan-in shapes; every dependency is expressed by ``edges`` rather
    than inferred from ``TaskNode.order``.
    """

    node_by_id: dict[str, TaskNode] = {}
    for node in nodes:
        if node.id in node_by_id:
            raise TaskGraphValidationError(
                "nodes", f"Duplicate Task node id '{node.id}'."
            )
        node_by_id[node.id] = node
    if not node_by_id:
        raise TaskGraphValidationError("nodes", "A DAG Task must contain nodes.")

    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    indegree = {node_id: 0 for node_id in node_by_id}
    seen_edges: set[tuple[str, str]] = set()
    for index, edge in enumerate(graph.edges):
        path = f"graph.edges[{index}]"
        source = edge.source_node_id
        target = edge.target_node_id
        if source not in node_by_id:
            raise TaskGraphValidationError(
                f"{path}.source_node_id", f"Unknown Task node id '{source}'."
            )
        if target not in node_by_id:
            raise TaskGraphValidationError(
                f"{path}.target_node_id", f"Unknown Task node id '{target}'."
            )
        if source == target:
            raise TaskGraphValidationError(path, "A node cannot depend on itself.")
        pair = (source, target)
        if pair in seen_edges:
            raise TaskGraphValidationError(path, "Duplicate dependency edge.")
        seen_edges.add(pair)
        adjacency[source].add(target)
        indegree[target] += 1

    stable_key = lambda node_id: (node_by_id[node_id].order, node_id)
    ready = sorted(
        (node_id for node_id, degree in indegree.items() if degree == 0),
        key=stable_key,
    )
    visited: list[str] = []
    while ready:
        current = ready.pop(0)
        visited.append(current)
        for target in sorted(adjacency[current], key=stable_key):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=stable_key)
    if len(visited) != len(node_by_id):
        cyclic = sorted(
            (node_id for node_id, degree in indegree.items() if degree > 0),
            key=stable_key,
        )
        raise TaskGraphValidationError(
            "graph.edges", "Dependency cycle includes: " + ", ".join(cyclic)
        )

    ancestors: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    for source in visited:
        for target in adjacency[source]:
            ancestors[target].add(source)
            ancestors[target].update(ancestors[source])
    for target_id, bindings in graph.input_bindings.items():
        if target_id not in node_by_id:
            raise TaskGraphValidationError(
                f"graph.input_bindings.{target_id}",
                f"Unknown Task node id '{target_id}'.",
            )
        for name, binding in bindings.items():
            source_id = binding.source_node_id
            if source_id not in node_by_id:
                raise TaskGraphValidationError(
                    f"graph.input_bindings.{target_id}.{name}",
                    f"Unknown Task node id '{source_id}'.",
                )
            if source_id not in ancestors[target_id]:
                raise TaskGraphValidationError(
                    f"graph.input_bindings.{target_id}.{name}",
                    "Input source must be an explicit dependency ancestor.",
                )

    if graph.max_concurrency > len(node_by_id):
        raise TaskGraphValidationError(
            "graph.max_concurrency",
            "Concurrency cannot exceed the number of nodes in the Task.",
        )
    for node in nodes:
        for resource, amount in node.resource_requirements.items():
            capacity = graph.resource_limits.get(resource)
            if capacity is None:
                raise TaskGraphValidationError(
                    f"nodes.{node.id}.resource_requirements.{resource}",
                    "Resource requirement has no graph capacity declaration.",
                )
            if amount > capacity:
                raise TaskGraphValidationError(
                    f"nodes.{node.id}.resource_requirements.{resource}",
                    f"Requested amount {amount} exceeds capacity {capacity}.",
                )
    artifact_nodes = [node for node in nodes if node.artifact is not None]
    terminal_ids = {
        node_id
        for node_id in node_by_id
        if not adjacency[node_id]
    }
    if len(artifact_nodes) > 1:
        raise TaskGraphValidationError(
            "nodes", "A DAG Task may configure at most one artifact node."
        )
    if artifact_nodes and (
        artifact_nodes[0].type.value == "input"
        or artifact_nodes[0].id not in terminal_ids
    ):
        raise TaskGraphValidationError(
            f"nodes.{artifact_nodes[0].id}.artifact",
            "A DAG artifact must be configured on a terminal non-Input node.",
        )


def topological_node_ids(graph: TaskGraph, nodes: list[TaskNode]) -> list[str]:
    """Return one deterministic topological order for display and stepping."""

    validate_task_graph(graph, nodes)
    node_by_id = {node.id: node for node in nodes}
    dependencies: dict[str, set[str]] = {node.id: set() for node in nodes}
    for edge in graph.edges:
        dependencies[edge.target_node_id].add(edge.source_node_id)
    remaining = set(node_by_id)
    ordered: list[str] = []
    while remaining:
        available = sorted(
            (
                node_id
                for node_id in remaining
                if dependencies[node_id].issubset(ordered)
            ),
            key=lambda node_id: (node_by_id[node_id].order, node_id),
        )
        if not available:  # defensive; validation already rejects cycles
            raise TaskGraphValidationError("graph.edges", "Dependency cycle.")
        selected = available[0]
        ordered.append(selected)
        remaining.remove(selected)
    return ordered


def ready_node_ids(
    graph: TaskGraph,
    nodes: list[TaskNode],
    *,
    succeeded_node_ids: set[str],
    running_node_ids: set[str] | None = None,
) -> list[str]:
    """Return ready nodes in stable ``(order, node_id)`` order."""

    validate_task_graph(graph, nodes)
    node_by_id = {node.id: node for node in nodes}
    parents: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        parents[edge.target_node_id].add(edge.source_node_id)
    running = running_node_ids or set()
    return sorted(
        (
            node.id
            for node in nodes
            if node.id not in succeeded_node_ids
            and node.id not in running
            and parents[node.id].issubset(succeeded_node_ids)
        ),
        key=lambda node_id: (node_by_id[node_id].order, node_id),
    )


def descendants(graph: TaskGraph, node_id: str) -> set[str]:
    """Return every transitive dependent of ``node_id``."""

    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        adjacency[edge.source_node_id].add(edge.target_node_id)
    result: set[str] = set()
    queue = deque(adjacency[node_id])
    while queue:
        current = queue.popleft()
        if current in result:
            continue
        result.add(current)
        queue.extend(adjacency[current])
    return result


def ancestors(graph: TaskGraph, node_id: str) -> set[str]:
    """Return every transitive dependency of a node."""

    parents: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        parents[edge.target_node_id].add(edge.source_node_id)
    result: set[str] = set()
    queue = deque(parents[node_id])
    while queue:
        current = queue.popleft()
        if current in result:
            continue
        result.add(current)
        queue.extend(parents[current])
    return result


def terminal_node_ids(graph: TaskGraph, nodes: list[TaskNode]) -> list[str]:
    """Return terminal nodes in stable display order."""

    has_children = {edge.source_node_id for edge in graph.edges}
    return [
        node.id
        for node in sorted(nodes, key=lambda item: (item.order, item.id))
        if node.id not in has_children
    ]


def build_inputs_by_node_id(
    graph: TaskGraph,
    node_runs: list[NodeRunRecord],
    target_node_id: str,
) -> dict[str, Any]:
    """Resolve all direct dependency outputs under their stable node ids."""

    run_by_id = {node_run.node_id: node_run for node_run in node_runs}
    source_ids = {
        edge.source_node_id
        for edge in graph.edges
        if edge.target_node_id == target_node_id
    }
    source_ids.update(
        binding.source_node_id
        for binding in graph.input_bindings.get(target_node_id, {}).values()
    )
    resolved: dict[str, Any] = {}
    for source_id in sorted(source_ids):
        source_run = run_by_id.get(source_id)
        if source_run is None:
            raise TaskGraphValidationError(
                f"graph.input_bindings.{target_node_id}",
                f"Input source '{source_id}' has no persisted node run.",
            )
        if source_run.status is not NodeRunStatus.SUCCEEDED:
            raise TaskGraphValidationError(
                f"graph.input_bindings.{target_node_id}",
                f"Input source '{source_id}' has not succeeded.",
            )
        resolved[source_id] = {
            "output": source_run.output,
            "result": source_run.result,
        }
    return resolved


def build_named_input_values(
    graph: TaskGraph,
    node_runs: list[NodeRunRecord],
    target_node_id: str,
) -> dict[str, Any]:
    """Resolve named executor aliases from persisted successful ancestors."""

    run_by_id = {node_run.node_id: node_run for node_run in node_runs}
    resolved: dict[str, Any] = {}
    for name, binding in sorted(
        graph.input_bindings.get(target_node_id, {}).items()
    ):
        source_run = run_by_id.get(binding.source_node_id)
        if source_run is None:
            raise TaskGraphValidationError(
                f"graph.input_bindings.{target_node_id}.{name}",
                "Input source has no persisted node run.",
            )
        if source_run.status is not NodeRunStatus.SUCCEEDED:
            raise TaskGraphValidationError(
                f"graph.input_bindings.{target_node_id}.{name}",
                "Input source has not succeeded.",
            )
        if binding.source_value == "result":
            if source_run.result is None:
                raise TaskGraphValidationError(
                    f"graph.input_bindings.{target_node_id}.{name}",
                    "Bound structured result is not available.",
                )
            resolved[name] = source_run.result
        else:
            resolved[name] = source_run.output
    return resolved


def resources_fit(
    graph: TaskGraph,
    nodes: list[TaskNode],
    selected_node_ids: list[str],
) -> bool:
    """Check that a ready batch fits declared graph resource capacities."""

    node_by_id = {node.id: node for node in nodes}
    used: dict[str, int] = defaultdict(int)
    for node_id in selected_node_ids:
        node = node_by_id[node_id]
        for resource, amount in node.resource_requirements.items():
            used[resource] += amount
            if used[resource] > graph.resource_limits[resource]:
                return False
    return True


def remap_task_graph(
    graph: TaskGraph | None,
    node_id_mapping: dict[str, str],
) -> TaskGraph | None:
    """Copy graph references along with a copied Task's node identifiers."""

    if graph is None:
        return None
    return graph.model_copy(
        deep=True,
        update={
            "edges": [
                edge.model_copy(
                    update={
                        "source_node_id": node_id_mapping[edge.source_node_id],
                        "target_node_id": node_id_mapping[edge.target_node_id],
                    }
                )
                for edge in graph.edges
            ],
            "input_bindings": {
                node_id_mapping[target_id]: {
                    name: binding.model_copy(
                        update={
                            "source_node_id": node_id_mapping[
                                binding.source_node_id
                            ]
                        }
                    )
                    for name, binding in bindings.items()
                }
                for target_id, bindings in graph.input_bindings.items()
            },
        },
    )
