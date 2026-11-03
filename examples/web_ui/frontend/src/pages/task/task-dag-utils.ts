import type { TaskGraph, TaskNode } from '@/api/task';

export interface DagNodePosition {
	node: TaskNode;
	level: number;
	row: number;
	x: number;
	y: number;
}

export const DAG_CARD_WIDTH = 232;
export const DAG_CARD_HEIGHT = 148;
export const DAG_COLUMN_GAP = 64;
export const DAG_ROW_GAP = 24;

/** Lay out a validated DAG in dependency order, with independent nodes side by side. */
export function layoutDag(nodes: TaskNode[], graph: TaskGraph): DagNodePosition[] {
	const ordered = [...nodes].sort((left, right) => left.order - right.order);
	const nodeIds = new Set(ordered.map((node) => node.id));
	const outgoing = new Map<string, string[]>();
	const incomingCount = new Map(ordered.map((node) => [node.id, 0]));
	const levels = new Map(ordered.map((node) => [node.id, 0]));

	for (const edge of graph.edges) {
		if (!nodeIds.has(edge.source_node_id) || !nodeIds.has(edge.target_node_id)) continue;
		outgoing.set(edge.source_node_id, [
			...(outgoing.get(edge.source_node_id) ?? []),
			edge.target_node_id,
		]);
		incomingCount.set(edge.target_node_id, (incomingCount.get(edge.target_node_id) ?? 0) + 1);
	}

	const byId = new Map(ordered.map((node) => [node.id, node]));
	const ready = ordered.filter((node) => incomingCount.get(node.id) === 0);
	const visited = new Set<string>();
	while (ready.length > 0) {
		ready.sort((left, right) => left.order - right.order);
		const node = ready.shift();
		if (!node) break;
		visited.add(node.id);
		for (const targetId of outgoing.get(node.id) ?? []) {
			levels.set(targetId, Math.max(levels.get(targetId) ?? 0, (levels.get(node.id) ?? 0) + 1));
			const remaining = (incomingCount.get(targetId) ?? 0) - 1;
			incomingCount.set(targetId, remaining);
			if (remaining === 0) {
				const target = byId.get(targetId);
				if (target) ready.push(target);
			}
		}
	}

	// Invalid cycles should still be visible if an older or malformed record is loaded.
	const unvisited = ordered.filter((node) => !visited.has(node.id));
	if (unvisited.length > 0) {
		const fallbackLevel = Math.max(0, ...levels.values()) + 1;
		for (const node of unvisited) levels.set(node.id, fallbackLevel);
	}

	const rowsByLevel = new Map<number, TaskNode[]>();
	for (const node of ordered) {
		const level = levels.get(node.id) ?? 0;
		rowsByLevel.set(level, [...(rowsByLevel.get(level) ?? []), node]);
	}
	const rowByNodeId = new Map<string, number>();
	for (const levelNodes of rowsByLevel.values()) {
		levelNodes.forEach((node, row) => rowByNodeId.set(node.id, row));
	}

	return ordered.map((node) => {
		const level = levels.get(node.id) ?? 0;
		const row = rowByNodeId.get(node.id) ?? 0;
		return {
			node,
			level,
			row,
			x: 24 + level * (DAG_CARD_WIDTH + DAG_COLUMN_GAP),
			y: 24 + row * (DAG_CARD_HEIGHT + DAG_ROW_GAP),
		};
	});
}

export function getDagDescendants(graph: TaskGraph, nodeId: string): string[] {
	const outgoing = new Map<string, string[]>();
	for (const edge of graph.edges) {
		outgoing.set(edge.source_node_id, [
			...(outgoing.get(edge.source_node_id) ?? []),
			edge.target_node_id,
		]);
	}
	const found = new Set<string>();
	const pending = [...(outgoing.get(nodeId) ?? [])];
	while (pending.length > 0) {
		const next = pending.pop();
		if (!next || found.has(next)) continue;
		found.add(next);
		pending.push(...(outgoing.get(next) ?? []));
	}
	return [...found];
}

/** Return all dependency ancestors of a node, in stable task order. */
export function getDagAncestors(graph: TaskGraph, nodes: TaskNode[], nodeId: string): string[] {
	const incoming = new Map<string, string[]>();
	for (const edge of graph.edges) {
		incoming.set(edge.target_node_id, [
			...(incoming.get(edge.target_node_id) ?? []),
			edge.source_node_id,
		]);
	}
	const found = new Set<string>();
	const pending = [...(incoming.get(nodeId) ?? [])];
	while (pending.length > 0) {
		const next = pending.pop();
		if (!next || found.has(next)) continue;
		found.add(next);
		pending.push(...(incoming.get(next) ?? []));
	}
	return nodes.filter((node) => found.has(node.id)).map((node) => node.id);
}

/** Keep graph references valid when a user adds or removes Task nodes. */
export function reconcileDagNodes(graph: TaskGraph, nodes: TaskNode[]): TaskGraph {
	const nodeIds = new Set(nodes.map((node) => node.id));
	const edges = graph.edges.filter(
		(edge) => nodeIds.has(edge.source_node_id) && nodeIds.has(edge.target_node_id),
	);
	const nextGraph = { ...graph, edges };
	const inputBindings = Object.fromEntries(
		Object.entries(graph.input_bindings)
			.filter(([targetId]) => nodeIds.has(targetId))
			.map(([targetId, bindings]) => [
				targetId,
				Object.fromEntries(
					Object.entries(bindings).filter(
						([, binding]) =>
							nodeIds.has(binding.source_node_id) &&
							getDagAncestors(nextGraph, nodes, targetId).includes(binding.source_node_id),
					),
				),
			])
			.filter(([, bindings]) => Object.keys(bindings as Record<string, unknown>).length > 0),
	);
	return {
		...nextGraph,
		input_bindings: inputBindings,
		max_concurrency: Math.max(1, Math.min(graph.max_concurrency, nodes.length || 1, 8)),
	};
}
