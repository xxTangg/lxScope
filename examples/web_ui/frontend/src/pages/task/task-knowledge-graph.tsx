import type { NVL, Node as NvlNode, Relationship as NvlRelationship } from '@neo4j-nvl/base';
import { InteractiveNvlWrapper, type MouseEventCallbacks } from '@neo4j-nvl/react';
import {
	AlertCircle,
	Database,
	Loader2,
	Maximize2,
	Network,
	RefreshCw,
	RotateCcw,
	Search,
	ZoomIn,
	ZoomOut,
} from 'lucide-react';
import * as React from 'react';

import { taskApi } from '@/api';
import type {
	TaskKnowledgeGraphEdge,
	TaskKnowledgeGraphNode,
	TaskKnowledgeGraphResponse,
} from '@/api/task';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';

interface Props {
	taskId: string;
	selectedIds: string[];
	disabled?: boolean;
	runId?: string;
	nodeId?: string;
	compact?: boolean;
	/** Opens the task graph in a read-only, node-focused preview mode. */
	preview?: boolean;
	initialQuery?: string;
}

function statusLabel(status: TaskKnowledgeGraphResponse['status']): string {
	if (status === 'building') return '构建中';
	if (status === 'error') return '构建失败';
	if (status === 'disabled') return '未启用抽取';
	if (status === 'ready') return '已就绪';
	return '暂无图谱';
}

function compactLabel(value: string, maxLength: number): string {
	const normalized = value.trim();
	return normalized.length > maxLength
		? `${normalized.slice(0, Math.max(1, maxLength - 1))}…`
		: normalized;
}

const ENTITY_TYPE_GROUPS = [
	{ id: 'project', label: '项目', color: '#3B82F6', types: ['项目', 'project'] },
	{ id: 'person', label: '专家 / 人物', color: '#14B8A6', types: ['专家', '人物', '专家/人物', '人员', '人', 'expert', 'person', 'individual'] },
	{ id: 'domain', label: '专业领域', color: '#8B5CF6', types: ['专业领域', '领域', '专业', 'professional domain', 'domain', 'discipline', 'specialty'] },
	{ id: 'document', label: '文档资料', color: '#64748B', types: ['文档资料', '文档', '资料', 'document', 'file', 'source'] },
	{ id: 'policy', label: '规范制度', color: '#F59E0B', types: ['规范制度', '规范', '制度', '标准', 'policy', 'regulation', 'standard', 'rule'] },
	{ id: 'risk', label: '风险 / 待确认', color: '#EF6464', types: ['风险', '待确认', '待核实', 'risk', 'unconfirmed'] },
] as const;

const UNKNOWN_ENTITY_COLOR = '#94A3B8';

function entityTypeGroup(type: string): (typeof ENTITY_TYPE_GROUPS)[number]['id'] | 'other' {
	const normalizedType = type.trim().toLowerCase();
	return ENTITY_TYPE_GROUPS.find((group) =>
		(group.types as readonly string[]).includes(normalizedType),
	)?.id ?? 'other';
}

function entityTypeColor(type: string): string {
	const groupId = entityTypeGroup(type);
	return groupId === 'other'
		? UNKNOWN_ENTITY_COLOR
		: ENTITY_TYPE_GROUPS.find((group) => group.id === groupId)?.color ?? UNKNOWN_ENTITY_COLOR;
}

function softenColor(color: string, whiteness: number): string {
	const channels = color.slice(1).match(/.{2}/g);
	if (!channels) return color;
	return `#${channels
		.map((channel) => {
			const value = Number.parseInt(channel, 16);
			return Math.round(value + (255 - value) * whiteness)
				.toString(16)
				.padStart(2, '0');
		})
		.join('')}`;
}

interface GraphHover {
	id: string;
	kind: 'node' | 'relationship';
	label: string;
	x: number;
	y: number;
}

function sourceLabel(source: TaskKnowledgeGraphNode['source_refs'][number]): string {
	const parts = [source.filename || source.document_id];
	if (source.chunk_index !== null && source.chunk_index !== undefined) {
		parts.push(`第 ${source.chunk_index + 1} 段`);
	}
	const page = source.metadata?.page ?? source.metadata?.pages ?? source.metadata?.slide;
	if (page !== undefined && page !== null && String(page).trim()) {
		parts.push(`第 ${String(page)} 页`);
	}
	return parts.join(' · ');
}

export function TaskKnowledgeGraph({
	taskId,
	selectedIds,
	disabled = false,
	runId,
	nodeId,
	compact = false,
	preview = false,
	initialQuery = '',
}: Props) {
	const stepScoped = Boolean(runId && nodeId);
	const [graph, setGraph] = React.useState<TaskKnowledgeGraphResponse | null>(null);
	const [query, setQuery] = React.useState(initialQuery);
	const [submittedQuery, setSubmittedQuery] = React.useState(initialQuery.trim());
	const [selected, setSelected] = React.useState<TaskKnowledgeGraphNode | null>(null);
	const [selectedEdge, setSelectedEdge] = React.useState<TaskKnowledgeGraphEdge | null>(null);
	const [hoveredElement, setHoveredElement] = React.useState<GraphHover | null>(null);
	const [loading, setLoading] = React.useState(false);
	const [rebuilding, setRebuilding] = React.useState(false);
	// Both the task-wide graph and the step graph are opt-in. This keeps the
	// task result lightweight until the user explicitly asks to see a graph.
	const [requested, setRequested] = React.useState(preview);
	const [error, setError] = React.useState<string | null>(null);
	const nvlRef = React.useRef<NVL | null>(null);
	const graphViewportRef = React.useRef<HTMLDivElement | null>(null);
	const hoveredElementKeyRef = React.useRef<string | null>(null);
	const needsInitialFitRef = React.useRef(false);

	const load = React.useCallback(async () => {
		if (stepScoped && runId && nodeId) {
			setLoading(true);
			try {
				const result = await taskApi.getStepKnowledgeGraph(runId, nodeId);
				setGraph(result);
				setError(result.error ?? null);
			} catch (requestError) {
				setError(requestError instanceof Error ? requestError.message : '知识图谱加载失败。');
			} finally {
				setLoading(false);
			}
			return;
		}
		if (!selectedIds.length) {
			setGraph(null);
			return;
		}
		setLoading(true);
		try {
			const result = await taskApi.getKnowledgeGraph(taskId, {
				knowledge_base_ids: selectedIds,
				query: submittedQuery || undefined,
			});
			setGraph(result);
			setError(result.error ?? null);
		} catch (requestError) {
			setError(requestError instanceof Error ? requestError.message : '知识图谱加载失败。');
		} finally {
			setLoading(false);
		}
	}, [nodeId, runId, selectedIds, stepScoped, submittedQuery, taskId]);

	React.useEffect(() => {
		setGraph(null);
		setError(null);
		setRequested(preview);
		setQuery(initialQuery);
		setSubmittedQuery(initialQuery.trim());
		setSelected(null);
		setSelectedEdge(null);
		setHoveredElement(null);
		hoveredElementKeyRef.current = null;
	}, [initialQuery, nodeId, preview, runId, stepScoped, taskId]);

	React.useEffect(() => {
		hoveredElementKeyRef.current = null;
		setHoveredElement(null);
	}, [graph?.edges, graph?.nodes]);

	React.useEffect(() => {
		if (!requested || (!stepScoped && !selectedIds.length)) return;
		void load();
	}, [load, requested, selectedIds.length, stepScoped]);

	const graphNodes = graph?.nodes;
	const graphEdges = graph?.edges;
	const nodeDegrees = React.useMemo(() => {
		const degrees = new Map<string, number>();
		for (const edge of graphEdges ?? []) {
			degrees.set(edge.source, (degrees.get(edge.source) ?? 0) + 1);
			degrees.set(edge.target, (degrees.get(edge.target) ?? 0) + 1);
		}
		return degrees;
	}, [graphEdges]);

	const centerNodeId = React.useMemo(() => {
		if (!graphNodes?.length) return null;
		return graphNodes.reduce((current, candidate) =>
			(nodeDegrees.get(candidate.id) ?? 0) > (nodeDegrees.get(current.id) ?? 0)
				? candidate
				: current,
		).id;
	}, [graphNodes, nodeDegrees]);

	const centerNeighbors = React.useMemo(() => {
		const neighbors = new Set<string>();
		if (!centerNodeId) return neighbors;
		for (const edge of graphEdges ?? []) {
			if (edge.source === centerNodeId) neighbors.add(edge.target);
			if (edge.target === centerNodeId) neighbors.add(edge.source);
		}
		return neighbors;
	}, [centerNodeId, graphEdges]);

	const focusNodeId = hoveredElement?.kind === 'node'
		? hoveredElement.id
		: hoveredElement
			? null
			: selected?.id ?? null;
	const focusEdgeId = hoveredElement?.kind === 'relationship'
		? hoveredElement.id
		: hoveredElement
			? null
			: selectedEdge?.id ?? null;
	const focusEdge = React.useMemo(
		() => focusEdgeId ? graphEdges?.find((edge) => edge.id === focusEdgeId) ?? null : null,
		[focusEdgeId, graphEdges],
	);
	const focusEdgeNodeIds = React.useMemo(
		() => new Set(focusEdge ? [focusEdge.source, focusEdge.target] : []),
		[focusEdge],
	);
	const focusRelatedNodeIds = React.useMemo(() => {
		const related = new Set(focusEdgeNodeIds);
		if (focusNodeId) {
			related.add(focusNodeId);
			for (const edge of graphEdges ?? []) {
				if (edge.source === focusNodeId) related.add(edge.target);
				if (edge.target === focusNodeId) related.add(edge.source);
			}
		}
		return related;
	}, [focusEdgeNodeIds, focusNodeId, graphEdges]);
	const hasGraphFocus = Boolean(focusNodeId || focusEdgeId);
	const sizeScale = (graph?.nodes.length ?? 0) > 80 ? 0.74 : (graph?.nodes.length ?? 0) > 40 ? 0.86 : 1;
	const legendGroups = React.useMemo(() => {
		const presentGroups = new Set((graphNodes ?? []).map((node) => entityTypeGroup(node.type)));
		return [
			...ENTITY_TYPE_GROUPS.filter((group) => presentGroups.has(group.id)),
			...(presentGroups.has('other') ? [{ id: 'other', label: '其他', color: UNKNOWN_ENTITY_COLOR }] : []),
		];
	}, [graphNodes]);

	const nvlNodes = React.useMemo<NvlNode[]>(
		() =>
			(graphNodes ?? []).map((node) => {
				const isCenter = node.id === centerNodeId;
				const isCenterNeighbor = centerNeighbors.has(node.id);
				const isFocusRelated = focusRelatedNodeIds.has(node.id);
				const color = entityTypeColor(node.type);
				const shouldShowCaption = hasGraphFocus
					? isFocusRelated
					: (graphNodes?.length ?? 0) <= 70 || isCenter || isCenterNeighbor;
				return {
					id: node.id,
					caption: shouldShowCaption ? compactLabel(node.label, (graphNodes?.length ?? 0) > 70 ? 10 : 14) : '',
					captionAlign: 'bottom',
					captionSize: 11,
					color: softenColor(color, hasGraphFocus && !isFocusRelated ? 0.9 : 0.56),
					size: (isCenter ? 28 : isCenterNeighbor ? 22 : 18) * sizeScale,
					selected: selected?.id === node.id,
					hovered: hoveredElement?.kind === 'node' && hoveredElement.id === node.id,
				};
			}),
		[
			centerNeighbors,
			centerNodeId,
			focusRelatedNodeIds,
			graphNodes,
			hasGraphFocus,
			hoveredElement,
			selected?.id,
			sizeScale,
		],
	);

	const nvlRelationships = React.useMemo<NvlRelationship[]>(
		() =>
			(graphEdges ?? []).map((edge) => ({
				id: edge.id,
				from: edge.source,
				to: edge.target,
				caption: ((focusNodeId && (edge.source === focusNodeId || edge.target === focusNodeId)) ||
					focusEdgeId === edge.id)
					? compactLabel(edge.label || '关系', 14)
					: '',
				captionAlign: 'top',
				captionSize: hoveredElement?.kind === 'relationship' && hoveredElement.id === edge.id ? 10 : 9,
				color: focusEdgeId === edge.id
					? '#2563EB'
					: focusNodeId && (edge.source === focusNodeId || edge.target === focusNodeId)
						? '#94A3B8'
						: hasGraphFocus
							? '#E2E8F0'
							: '#CBD5E1',
				width: focusEdgeId === edge.id
					? 1.75
					: focusNodeId && (edge.source === focusNodeId || edge.target === focusNodeId)
						? 1.4
						: 1,
				hovered: hoveredElement?.kind === 'relationship' && hoveredElement.id === edge.id,
			})),
		[focusEdgeId, focusNodeId, graphEdges, hasGraphFocus, hoveredElement],
	);

	const mouseEventCallbacks = React.useMemo<MouseEventCallbacks>(
		() => ({
			onNodeClick: (node: NvlNode) => {
				const candidate = graphNodes?.find((item) => item.id === node.id);
				if (candidate) {
					setSelected(candidate);
					setSelectedEdge(null);
				}
			},
			onRelationshipClick: (relationship: NvlRelationship) => {
				const candidate = graphEdges?.find((item) => item.id === relationship.id);
				if (candidate) {
					setSelectedEdge(candidate);
					setSelected(null);
				}
			},
			onCanvasClick: () => {
				setSelected(null);
				setSelectedEdge(null);
				setHoveredElement(null);
				hoveredElementKeyRef.current = null;
			},
			onHover: (element: NvlNode | NvlRelationship | undefined, _hitElements, event) => {
				const kind = element && 'from' in element ? 'relationship' : 'node';
				const key = element ? `${kind}:${element.id}` : null;
				if (key === hoveredElementKeyRef.current) return;
				hoveredElementKeyRef.current = key;
				if (!element) {
					setHoveredElement(null);
					return;
				}
				const isRelationship = kind === 'relationship';
				const label = isRelationship
					? graphEdges?.find((item) => item.id === element.id)?.label
					: graphNodes?.find((item) => item.id === element.id)?.label;
				const bounds = graphViewportRef.current?.getBoundingClientRect();
				if (!label || !bounds) {
					setHoveredElement(null);
					return;
				}
				const tooltipWidth = Math.min(320, bounds.width - 16);
				setHoveredElement({
					id: element.id,
					kind,
					label,
					x: Math.max(8, Math.min(bounds.width - tooltipWidth - 8, event.clientX - bounds.left + 12)),
					y: Math.max(8, Math.min(bounds.height - 44, event.clientY - bounds.top + 12)),
				});
			},
			// NVL enables an interaction when its callback is present. These three
			// flags provide node dragging, canvas panning and wheel zooming.
			onDrag: true,
			onPan: true,
			onZoomAndPan: true,
		}),
		[graphEdges, graphNodes],
	);

	const fitGraph = React.useCallback(() => {
		if (!nvlRef.current || !graph?.nodes.length) return;
		nvlRef.current.fit(graph.nodes.map((node) => node.id), { animated: true });
	}, [graph]);

	const zoomGraph = React.useCallback((factor: number) => {
		if (!nvlRef.current) return;
		const { minZoom = 0.075, maxZoom = 10 } = nvlRef.current.getCurrentOptions();
		const nextZoom = nvlRef.current.getScale() * factor;
		nvlRef.current.setZoom(Math.min(maxZoom, Math.max(minZoom, nextZoom)));
	}, []);

	const resetLayout = React.useCallback(() => {
		if (!nvlRef.current) return;
		needsInitialFitRef.current = true;
		nvlRef.current.restart({ layout: 'd3Force' }, false);
	}, []);

	React.useEffect(() => {
		if (!graph?.nodes.length) return undefined;
		needsInitialFitRef.current = true;
		return undefined;
	}, [graph?.edges, graph?.nodes]);

	const nvlCallbacks = React.useMemo(
		() => ({
			onInitialization: () => {
				needsInitialFitRef.current = true;
			},
			onLayoutDone: () => {
				if (!needsInitialFitRef.current) return;
				needsInitialFitRef.current = false;
				fitGraph();
			},
		}),
		[fitGraph],
	);

	const rebuild = async () => {
		setRebuilding(true);
		try {
			const result = await taskApi.rebuildKnowledgeGraph(taskId, selectedIds);
			if (result.error) setError(result.error);
			await load();
		} catch (requestError) {
			setError(requestError instanceof Error ? requestError.message : '知识图谱构建失败。');
		} finally {
			setRebuilding(false);
		}
	};

	return (
		<Card className="mb-6 overflow-visible">
			<CardHeader className="gap-3">
				<div className="flex items-center justify-between gap-3">
						<CardTitle className="flex items-center gap-2 text-sm">
							<Network className="size-4 text-blue-600" />
							{preview ? '节点知识图谱预览' : stepScoped ? '步骤知识图谱' : '任务知识图谱'}
						</CardTitle>
					{graph && (
						<div className="flex items-center gap-2">
							<Badge variant={graph.status === 'error' ? 'destructive' : 'secondary'}>
								{statusLabel(graph.status)}
							</Badge>
							<Badge variant="outline">
								{graph.node_count} 个实体 · {graph.edge_count} 条关系
							</Badge>
							{graph.mode && graph.mode !== 'stored' && (
								<Badge variant="outline">
									{graph.mode === 'llm'
										? '按需抽取'
										: graph.mode === 'hybrid'
											? '复用 + 抽取'
											: graph.mode}
								</Badge>
							)}
						</div>
					)}
				</div>
				<div className="flex flex-wrap items-center justify-between gap-2">
					<div className="text-xs leading-5 text-muted-foreground">
						{preview && stepScoped
							? '根据本次执行结果展示当前节点的实体关系；点击节点查看类型与来源。'
							: stepScoped
							? '根据当前步骤上下文检索相关片段；点击节点查看来源。'
							: preview
								? '根据当前节点配置检索所选知识库的相关片段；执行后会生成更精确的步骤子图。'
								: '实体与关系来自所选知识库的已索引片段；点击节点查看来源。'}
					</div>
					<div className="flex flex-wrap gap-2">
						{stepScoped ? (
							<Button
								type="button"
								size="sm"
								variant="outline"
								disabled={disabled || loading}
								onClick={() => {
									setRequested(true);
								}}
							>
								{loading ? '生成中…' : graph ? '重新生成' : '生成知识图谱'}
							</Button>
						) : !requested ? (
							<Button
								type="button"
								size="sm"
								variant="outline"
								disabled={disabled || !selectedIds.length}
								onClick={() => setRequested(true)}
							>
								<Network className="size-3.5" />
								显示知识图谱
							</Button>
						) : (
							<>
						<form
							className="flex gap-2"
							onSubmit={(event) => {
								event.preventDefault();
								setSubmittedQuery(query.trim());
							}}
						>
							<Input
								value={query}
								onChange={(event) => setQuery(event.target.value)}
								placeholder="筛选实体或类型"
								aria-label="筛选实体或类型"
								className="h-7 w-44 text-xs"
							/>
							<Button type="submit" size="sm" variant="outline">
								<Search className="size-3.5" />
								筛选
							</Button>
						</form>
						{!preview && (
							<Button
								type="button"
								size="sm"
								variant="outline"
								disabled={disabled || rebuilding || !selectedIds.length}
								onClick={() => void rebuild()}
							>
								<RefreshCw className={rebuilding ? 'size-3.5 animate-spin' : 'size-3.5'} />
								{rebuilding ? '更新中…' : '更新图谱'}
							</Button>
						)}
							</>
						)}
						{requested && (
							<div className="flex items-center gap-1" aria-label="图谱视图控制">
								<Button type="button" size="sm" variant="outline" className="size-7 px-0" aria-label="放大图谱" title="放大" disabled={!graph?.nodes.length} onClick={() => zoomGraph(1.2)}>
									<ZoomIn className="size-3.5" />
								</Button>
								<Button type="button" size="sm" variant="outline" className="size-7 px-0" aria-label="缩小图谱" title="缩小" disabled={!graph?.nodes.length} onClick={() => zoomGraph(1 / 1.2)}>
									<ZoomOut className="size-3.5" />
								</Button>
								<Button type="button" size="sm" variant="outline" className="size-7 px-0" aria-label="重置图谱布局" title="重置布局" disabled={!graph?.nodes.length} onClick={resetLayout}>
									<RotateCcw className="size-3.5" />
								</Button>
								<Button type="button" size="sm" variant="outline" disabled={!graph?.nodes.length} onClick={fitGraph}>
									<Maximize2 className="size-3.5" />
									适配视图
								</Button>
							</div>
						)}
					</div>
				</div>
			</CardHeader>
			<CardContent>
				{!requested ? (
					<div className="flex items-center justify-between gap-3 rounded-lg border border-dashed border-border py-10 px-4 text-xs text-muted-foreground">
						<span>
							{stepScoped
								? '当前步骤已开启按需知识图谱，点击按钮生成相关子图。'
								: '总知识图谱默认隐藏，点击按钮后加载所选知识库的实体关系。'}
						</span>
						<Button
							type="button"
								size="sm"
								variant="outline"
								disabled={disabled || (!stepScoped && !selectedIds.length)}
							onClick={() => setRequested(true)}
						>
							{stepScoped ? '生成' : '显示'}
						</Button>
					</div>
				) : !stepScoped && !selectedIds.length ? (
					<div className="flex items-center gap-2 rounded-lg border border-dashed border-border py-10 text-center text-xs text-muted-foreground">
						<Database className="ml-auto size-4" />
						<span className="mr-auto">先选择至少一个知识库，再查看任务图谱。</span>
					</div>
				) : error && !graph ? (
					<div className="flex items-center gap-2 py-8 text-sm text-destructive">
						<AlertCircle className="size-4" />
						{error}
					</div>
				) : loading ? (
					<div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
						<Loader2 className="size-4 animate-spin" />
						加载图谱…
					</div>
				) : !graph || graph.status === 'empty' || graph.nodes.length === 0 ? (
					<div className="py-12 text-center text-xs text-muted-foreground">
						{graph?.status === 'disabled'
							? '尚未配置关系抽取模型。后端设置 DEEPSEEK_API_KEY 并重启后，再生成图谱。'
							: graph?.status === 'building'
								? '图谱正在构建，请稍后刷新。'
							: stepScoped
									? '当前步骤未找到相关实体关系，可以调整节点要求后重新生成。'
									: preview
										? '暂无相关实体关系，请先生成任务图谱或调整节点描述。'
										: '暂无已抽取的实体关系，请点击“更新图谱”。'}
					</div>
				) : (
					<div
						className={
							compact
								? 'grid gap-3'
								: 'grid gap-4 lg:grid-cols-[minmax(0,1fr)_270px]'
						}
					>
						<div className="min-w-0 overflow-hidden rounded-lg border border-slate-200 bg-white">
							<div
								ref={graphViewportRef}
								onMouseLeave={() => {
									hoveredElementKeyRef.current = null;
									setHoveredElement(null);
								}}
								className={`relative w-full min-w-0 bg-white ${compact ? 'h-[340px] min-h-[280px]' : 'h-[500px] min-h-[360px]'}`}
							>
								<InteractiveNvlWrapper
									ref={nvlRef}
									nodes={nvlNodes}
									rels={nvlRelationships}
									nvlOptions={{
										renderer: 'canvas',
										// d3-force gives compact task graphs a stable 2D spread
										// instead of collapsing many nodes into one visual cluster.
										layout: 'd3Force',
										initialZoom: 0.7,
										minZoom: 0.12,
										maxZoom: 4,
										allowDynamicMinZoom: true,
										styling: {
											defaultNodeColor: softenColor(UNKNOWN_ENTITY_COLOR, 0.56),
											defaultRelationshipColor: '#CBD5E1',
											nodeDefaultBorderColor: '#FFFFFF',
											selectedBorderColor: '#2563EB',
											selectedInnerBorderColor: '#FFFFFF',
											dropShadowColor: '#93C5FD',
										},
									}}
									mouseEventCallbacks={mouseEventCallbacks}
									interactionOptions={{ drawShadowOnHover: true }}
									nvlCallbacks={nvlCallbacks}
									className="h-full w-full select-none"
								/>
								{legendGroups.length > 0 && (
									<div className="pointer-events-none absolute left-2 top-2 flex max-w-[90%] flex-wrap gap-x-3 gap-y-1 rounded-md border border-slate-200/80 bg-white/90 px-2 py-1.5 text-[10px] text-slate-600 shadow-sm">
										{legendGroups.map((group) => (
											<div key={group.id} className="flex items-center gap-1.5 whitespace-nowrap">
												<span className="size-2 rounded-full" style={{ backgroundColor: softenColor(group.color, 0.56) }} />
												{group.label}
											</div>
										))}
									</div>
								)}
								{hoveredElement && (
									<div
										className="pointer-events-none absolute z-10 rounded-md border border-slate-200 bg-white/95 px-2.5 py-1.5 text-xs text-slate-700 shadow-md"
										style={{ left: hoveredElement.x, top: hoveredElement.y, maxWidth: 'min(320px, calc(100% - 16px))' }}
									>
										<span className="mr-1.5 text-[10px] text-slate-400">
											{hoveredElement.kind === 'node' ? '实体' : '关系'}
										</span>
										<span className="break-words">{hoveredElement.label}</span>
									</div>
								)}
								<div className="pointer-events-none absolute bottom-2 left-2 rounded-md bg-white/90 px-2 py-1 text-[11px] text-slate-500 shadow-sm">
									拖动节点 · 拖动画布 · 滚轮缩放 · 点击查看详情
								</div>
							</div>
						</div>
						<div className="rounded-lg border bg-muted/20 p-3 text-sm">
							{selected ? (
								<>
									<div className="font-medium">{selected.label}</div>
									<div className="mt-1 text-xs text-muted-foreground">{selected.type}</div>
									{selected.aliases.length > 0 && (
										<div className="mt-3 text-xs">{selected.aliases.join('、')}</div>
									)}
									<div className="mt-4 text-xs font-medium">来源片段</div>
									<ul className="mt-1 space-y-1 text-xs text-muted-foreground">
										{selected.source_refs.slice(0, 8).map((source) => (
											<li key={`${source.document_id}-${source.chunk_index}`}>
												{sourceLabel(source)}
											</li>
										))}
									</ul>
								</>
							) : selectedEdge ? (
								<>
									<div className="font-medium">关系：{selectedEdge.label || '未命名关系'}</div>
									<div className="mt-2 text-xs text-muted-foreground">
										{graph.nodes.find((node) => node.id === selectedEdge.source)?.label ?? selectedEdge.source}
										<span className="mx-1">→</span>
										{graph.nodes.find((node) => node.id === selectedEdge.target)?.label ?? selectedEdge.target}
									</div>
									<div className="mt-4 text-xs font-medium">关系来源</div>
									<ul className="mt-1 space-y-1 text-xs text-muted-foreground">
										{selectedEdge.source_refs.slice(0, 8).map((source) => (
											<li key={`${source.document_id}-${source.chunk_index}`}>
												{sourceLabel(source)}
											</li>
										))}
									</ul>
								</>
							) : (
								<div className="space-y-2 text-xs text-muted-foreground">
									<p>点击实体查看类型与来源。</p>
									<p>点击连线查看关系和证据。</p>
								</div>
							)}
						</div>
					</div>
				)}
			</CardContent>
		</Card>
	);
}
