import type { NVL, Node as NvlNode, Relationship as NvlRelationship } from '@neo4j-nvl/base';
import { InteractiveNvlWrapper } from '@neo4j-nvl/react';
import {
	AlertCircle,
	Database,
	Loader2,
	Maximize2,
	Network,
	RefreshCw,
	Search,
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
}: Props) {
	const stepScoped = Boolean(runId && nodeId);
	const [graph, setGraph] = React.useState<TaskKnowledgeGraphResponse | null>(null);
	const [query, setQuery] = React.useState('');
	const [submittedQuery, setSubmittedQuery] = React.useState('');
	const [selected, setSelected] = React.useState<TaskKnowledgeGraphNode | null>(null);
	const [selectedEdge, setSelectedEdge] = React.useState<TaskKnowledgeGraphEdge | null>(null);
	const [loading, setLoading] = React.useState(false);
	const [rebuilding, setRebuilding] = React.useState(false);
	// Both the task-wide graph and the step graph are opt-in. This keeps the
	// task result lightweight until the user explicitly asks to see a graph.
	const [requested, setRequested] = React.useState(false);
	const [error, setError] = React.useState<string | null>(null);
	const nvlRef = React.useRef<NVL | null>(null);
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
		setRequested(false);
		setSelected(null);
		setSelectedEdge(null);
	}, [runId, nodeId, stepScoped, taskId]);

	React.useEffect(() => {
		if (!requested || (!stepScoped && !selectedIds.length)) return;
		void load();
	}, [load, requested, selectedIds.length, stepScoped]);

	const nodeDegrees = React.useMemo(() => {
		const degrees = new Map<string, number>();
		for (const edge of graph?.edges ?? []) {
			degrees.set(edge.source, (degrees.get(edge.source) ?? 0) + 1);
			degrees.set(edge.target, (degrees.get(edge.target) ?? 0) + 1);
		}
		return degrees;
	}, [graph?.edges]);

	const nvlNodes = React.useMemo<NvlNode[]>(
		() =>
			(graph?.nodes ?? []).map((node) => {
				const degree = nodeDegrees.get(node.id) ?? 0;
				// Keep the graph airy: the second-level nodes stay small while
				// highly connected entities remain visibly more important.
				const baseSize = 11 + Math.min(10, Math.round(Math.sqrt(degree) * 2.8));
				const size = selected?.id === node.id ? Math.max(24, baseSize + 6) : baseSize;
				const color =
					degree >= 6 ? '#3b82f6' : degree >= 3 ? '#60a5fa' : '#93c5fd';
				return {
					id: node.id,
					caption: compactLabel(node.label, 14),
					// Keep the label scale tied to the node scale so dense and
					// selected nodes remain readable without oversized captions.
					captionAlign: 'bottom',
					captionSize: Math.max(8, Math.min(13, Math.round(size * 0.52))),
					color: selected?.id === node.id ? '#1d4ed8' : color,
					size,
					selected: selected?.id === node.id,
				};
			}),
		[nodeDegrees, graph?.nodes, selected?.id],
	);

	const nvlRelationships = React.useMemo<NvlRelationship[]>(
		() =>
			(graph?.edges ?? []).map((edge) => ({
				id: edge.id,
				from: edge.source,
				to: edge.target,
				caption: compactLabel(edge.label || '关系', 10),
				captionSize: 8,
				color: selectedEdge?.id === edge.id ? '#2563eb' : '#cbd5e1',
				width: selectedEdge?.id === edge.id ? 2 : 1,
			})),
		[selectedEdge?.id, graph?.edges],
	);

	const mouseEventCallbacks = React.useMemo(
		() => ({
			onNodeClick: (node: NvlNode) => {
				const candidate = graph?.nodes.find((item) => item.id === node.id);
				if (candidate) {
					setSelected(candidate);
					setSelectedEdge(null);
				}
			},
			onRelationshipClick: (relationship: NvlRelationship) => {
				const candidate = graph?.edges.find((item) => item.id === relationship.id);
				if (candidate) {
					setSelectedEdge(candidate);
					setSelected(null);
				}
			},
			onCanvasClick: () => {
				setSelected(null);
				setSelectedEdge(null);
			},
			// NVL enables an interaction when its callback is present. These three
			// flags provide node dragging, canvas panning and wheel zooming.
			onDrag: true,
			onPan: true,
			onZoomAndPan: true,
			onHover: true,
		}),
		[graph?.edges, graph?.nodes],
	);

	const fitGraph = React.useCallback(() => {
		if (!nvlRef.current || !graph?.nodes.length) return;
		nvlRef.current.fit(graph.nodes.map((node) => node.id));
	}, [graph]);

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
							{stepScoped ? '步骤知识图谱' : '任务知识图谱'}
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
						{stepScoped
							? '根据当前步骤上下文检索相关片段；点击节点查看来源。'
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
							</>
						)}
						{requested && (
							<Button
								type="button"
								size="sm"
								variant="outline"
								disabled={!graph?.nodes.length}
								onClick={fitGraph}
							>
								<Maximize2 className="size-3.5" />
								适配视图
							</Button>
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
						<div className="min-w-0 overflow-hidden rounded-lg border bg-background">
							<div
								className={`relative w-full min-w-0 ${compact ? 'h-[340px] min-h-[280px]' : 'h-[500px] min-h-[360px]'}`}
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
											defaultNodeColor: '#60a5fa',
											defaultRelationshipColor: '#cbd5e1',
											selectedBorderColor: '#bfdbfe',
											selectedInnerBorderColor: '#eff6ff',
											dropShadowColor: '#bfdbfe',
										},
									}}
									mouseEventCallbacks={mouseEventCallbacks}
									nvlCallbacks={nvlCallbacks}
									className="h-full w-full select-none"
								/>
								<div className="pointer-events-none absolute bottom-2 left-2 rounded-md bg-background/85 px-2 py-1 text-[11px] text-muted-foreground shadow-sm">
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
