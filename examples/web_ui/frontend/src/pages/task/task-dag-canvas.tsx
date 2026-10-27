import { AlertCircle, CheckCircle2, Clock3, Loader2, PauseCircle, Plus, Trash2 } from 'lucide-react';
import * as React from 'react';

import {
	DAG_CARD_HEIGHT,
	DAG_CARD_WIDTH,
	DAG_COLUMN_GAP,
	DAG_ROW_GAP,
	getDagAncestors,
	getDagDescendants,
	layoutDag,
	reconcileDagNodes,
} from './task-dag-utils';
import { sanitizeTaskRunText } from './task-run-sanitization';
import type {
	NodeRunRecord,
	TaskGraph,
	TaskGraphInputBinding,
	TaskNode,
	TaskRunRecord,
	TaskValidationIssue,
} from '@/api/task';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface TaskDagCanvasProps {
	graph: TaskGraph;
	nodes: TaskNode[];
	run?: TaskRunRecord | null;
	readOnlyReason?: string;
	executionBlockedReason?: string;
	editable?: boolean;
	executionEnabled?: boolean | null;
	validationIssues?: TaskValidationIssue[];
	onGraphChange?: (graph: TaskGraph) => void;
	onNodesChange?: (nodes: TaskNode[]) => void;
	selectedNodeId?: string | null;
	onNodeSelect?: (nodeId: string) => void;
}

function statusLabel(status: NodeRunRecord['status'] | 'pending'): string {
	const labels: Record<NodeRunRecord['status'] | 'pending', string> = {
		pending: '等待中',
		running: '执行中',
		awaiting_input: '等待输入',
		succeeded: '成功',
		failed: '失败',
		canceled: '已取消',
	};
	return labels[status];
}

function statusStyle(status: NodeRunRecord['status'] | 'pending') {
	if (status === 'succeeded') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
	if (status === 'failed') return 'border-red-200 bg-red-50 text-red-700';
	if (status === 'running') return 'border-blue-200 bg-blue-50 text-blue-700';
	if (status === 'awaiting_input') return 'border-amber-200 bg-amber-50 text-amber-700';
	if (status === 'canceled') return 'border-slate-200 bg-slate-100 text-slate-600';
	return 'border-slate-200 bg-white text-slate-500';
}

function statusIcon(status: NodeRunRecord['status'] | 'pending') {
	if (status === 'succeeded') return <CheckCircle2 className="size-3" />;
	if (status === 'failed') return <AlertCircle className="size-3" />;
	if (status === 'running') return <Loader2 className="size-3 animate-spin" />;
	if (status === 'awaiting_input') return <PauseCircle className="size-3" />;
	return <Clock3 className="size-3" />;
}

function stepTypeLabel(node: TaskNode): string {
	if (node.type === 'tool') return 'ToolStep';
	if (node.type === 'python') return 'CodeStep';
	if (node.type === 'input') return '用户输入';
	return 'AgentStep';
}

function edgeColor(source: NodeRunRecord | undefined, target: NodeRunRecord | undefined) {
	if (target?.status === 'failed' || source?.status === 'failed') return '#ef4444';
	if (source?.status === 'succeeded' && target?.status === 'succeeded') return '#10b981';
	return '#cbd5e1';
}

export function TaskDagCanvas({
	graph,
	nodes,
	run,
	readOnlyReason,
	executionBlockedReason,
	editable = false,
	executionEnabled = null,
	validationIssues = [],
	onGraphChange,
	onNodesChange,
	selectedNodeId,
	onNodeSelect,
}: TaskDagCanvasProps) {
	const [edgeSource, setEdgeSource] = React.useState(nodes[0]?.id ?? '');
	const [edgeTarget, setEdgeTarget] = React.useState(nodes[1]?.id ?? nodes[0]?.id ?? '');
	const [bindingTarget, setBindingTarget] = React.useState(nodes[0]?.id ?? '');
	const [bindingSource, setBindingSource] = React.useState('');
	const [bindingName, setBindingName] = React.useState('');
	const [bindingValue, setBindingValue] = React.useState<'output' | 'result'>('output');
	const [resourceName, setResourceName] = React.useState('');
	const [resourceCapacity, setResourceCapacity] = React.useState('1');
	const [editError, setEditError] = React.useState<string | null>(null);
	const [draggedNodeId, setDraggedNodeId] = React.useState<string | null>(null);
	const selectedEdgeSource = nodes.some((node) => node.id === edgeSource) ? edgeSource : nodes[0]?.id ?? '';
	const selectedEdgeTarget = nodes.some((node) => node.id === edgeTarget) ? edgeTarget : nodes[1]?.id ?? nodes[0]?.id ?? '';
	const selectedBindingTarget = nodes.some((node) => node.id === bindingTarget) ? bindingTarget : nodes[0]?.id ?? '';
	const nodeName = (nodeId: string) => nodes.find((node) => node.id === nodeId)?.name ?? nodeId;
	const updateGraph = (next: TaskGraph) => {
		setEditError(null);
		onGraphChange?.(next);
	};
	const ancestorIds = getDagAncestors(graph, nodes, selectedBindingTarget);
	const addEdge = () => {
		if (!selectedEdgeSource || !selectedEdgeTarget || selectedEdgeSource === selectedEdgeTarget) {
			setEditError('依赖边必须连接两个不同节点。');
			return;
		}
		if (graph.edges.some((edge) => edge.source_node_id === selectedEdgeSource && edge.target_node_id === selectedEdgeTarget)) {
			setEditError('这条依赖边已经存在。');
			return;
		}
		if (getDagDescendants(graph, selectedEdgeTarget).includes(selectedEdgeSource)) {
			setEditError('添加此边会形成循环依赖。');
			return;
		}
		if (graph.edges.length >= 56) {
			setEditError('DAG 最多支持 56 条依赖边。');
			return;
		}
		updateGraph({
			...graph,
			edges: [...graph.edges, { source_node_id: selectedEdgeSource, target_node_id: selectedEdgeTarget }],
		});
	};
	const removeEdge = (sourceId: string, targetId: string) => {
		const oldBindingCount = Object.values(graph.input_bindings).reduce(
			(total, bindings) => total + Object.keys(bindings).length,
			0,
		);
		const next = reconcileDagNodes(
			{
				...graph,
				edges: graph.edges.filter(
					(edge) => edge.source_node_id !== sourceId || edge.target_node_id !== targetId,
				),
			},
			nodes,
		);
		const newBindingCount = Object.values(next.input_bindings).reduce(
			(total, bindings) => total + Object.keys(bindings).length,
			0,
		);
		updateGraph(next);
		if (newBindingCount < oldBindingCount) {
			setEditError('移除依赖后，失去前驱关系的输入绑定已一并清除。');
		}
	};
	const addBinding = () => {
		const name = bindingName.trim();
		if (!selectedBindingTarget || !ancestorIds.includes(bindingSource)) {
			setEditError('输入来源必须是目标节点的现有依赖祖先。');
			return;
		}
		if (!name || name.length > 120) {
			setEditError('输入名称不能为空且不能超过 120 个字符。');
			return;
		}
		if (Object.keys(graph.input_bindings[selectedBindingTarget] ?? {}).length >= 32 && !graph.input_bindings[selectedBindingTarget]?.[name]) {
			setEditError('单个节点最多设置 32 个输入绑定。');
			return;
		}
		const binding: TaskGraphInputBinding = {
			source_node_id: bindingSource,
			source_value: bindingValue,
		};
		updateGraph({
			...graph,
			input_bindings: {
				...graph.input_bindings,
				[selectedBindingTarget]: { ...graph.input_bindings[selectedBindingTarget], [name]: binding },
			},
		});
		setBindingName('');
	};
	const removeBinding = (targetId: string, name: string) => {
		const bindings = { ...graph.input_bindings[targetId] };
		delete bindings[name];
		const inputBindings = { ...graph.input_bindings };
		if (Object.keys(bindings).length > 0) inputBindings[targetId] = bindings;
		else delete inputBindings[targetId];
		updateGraph({ ...graph, input_bindings: inputBindings });
	};
	const saveResourceLimit = () => {
		const name = resourceName.trim();
		const capacity = Number(resourceCapacity);
		if (!name || name.length > 120 || !Number.isInteger(capacity) || capacity < 1) {
			setEditError('资源名称最多 120 个字符，容量必须为正整数。');
			return;
		}
		if (!Object.hasOwn(graph.resource_limits, name) && Object.keys(graph.resource_limits).length >= 32) {
			setEditError('DAG 最多声明 32 种资源容量。');
			return;
		}
		updateGraph({ ...graph, resource_limits: { ...graph.resource_limits, [name]: capacity } });
		setResourceName('');
	};
	const removeResourceLimit = (name: string) => {
		const limits = { ...graph.resource_limits };
		delete limits[name];
		updateGraph({ ...graph, resource_limits: limits });
	};
	const reorderNode = (sourceId: string, targetId: string) => {
		if (!editable || sourceId === targetId || !onNodesChange) return;
		const sourceIndex = nodes.findIndex((node) => node.id === sourceId);
		const targetIndex = nodes.findIndex((node) => node.id === targetId);
		if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return;
		const next = [...nodes];
		const [moved] = next.splice(sourceIndex, 1);
		if (!moved) return;
		next.splice(targetIndex, 0, moved);
		onNodesChange(next.map((node, index) => ({ ...node, order: index })));
	};
	const layout = layoutDag(nodes, graph);
	const positions = new Map(layout.map((item) => [item.node.id, item]));
	const nodeRuns = new Map((run?.node_runs ?? []).map((item) => [item.node_id, item]));
	const maxLevel = Math.max(0, ...layout.map((item) => item.level));
	const maxRow = Math.max(0, ...layout.map((item) => item.row));
	const canvasWidth = 48 + (maxLevel + 1) * DAG_CARD_WIDTH + maxLevel * DAG_COLUMN_GAP;
	const canvasHeight = 48 + (maxRow + 1) * DAG_CARD_HEIGHT + maxRow * DAG_ROW_GAP;

	return (
		<section className="mb-4 overflow-hidden rounded-2xl border border-slate-200 bg-white">
			<div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-4 py-3">
				<div>
					<div className="text-xs font-semibold text-slate-800">DAG 执行图</div>
					<div className="mt-1 text-[10px] text-slate-500">
						{nodes.length} 个节点 · {graph.edges.length} 条依赖 · 最大并发 {graph.max_concurrency}
					</div>
				</div>
				<Badge variant="outline" className="text-[10px]">{editable ? '可编辑' : '只读'}</Badge>
			</div>
			{readOnlyReason && (
				<div className="border-b border-amber-100 bg-amber-50/70 px-4 py-2 text-[10px] leading-4 text-amber-800">
					{readOnlyReason}
				</div>
			)}
			{executionBlockedReason && (
				<div className="border-b border-amber-100 bg-amber-50/70 px-4 py-2 text-[10px] leading-4 text-amber-900">
					{executionBlockedReason}
				</div>
			)}
			{executionEnabled !== true && graph && (
				<div className="border-b border-blue-100 bg-blue-50/70 px-4 py-2 text-[10px] leading-4 text-blue-800">
					{executionEnabled === false
						? 'DAG 编辑与运行权限分开控制。当前允许保存图定义；DAG 执行开关关闭，服务端不会创建 DAG Run。'
						: '无法读取服务端 DAG 执行能力；为安全起见，当前只允许查看图定义。'}
				</div>
			)}
			{editable && (
				<div className="space-y-4 border-b border-slate-100 bg-white px-4 py-4">
					<div className="grid gap-4 xl:grid-cols-2">
						<section className="space-y-2 rounded-xl border border-slate-200 p-3">
							<div className="text-xs font-semibold text-slate-800">节点依赖</div>
							<div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
								<select aria-label="依赖来源节点" value={selectedEdgeSource} onChange={(event) => setEdgeSource(event.target.value)} className="h-9 min-w-0 rounded-lg border border-slate-200 bg-white px-2 text-xs">
									{nodes.map((node) => <option key={node.id} value={node.id}>{node.name}</option>)}
								</select>
								<select aria-label="依赖目标节点" value={selectedEdgeTarget} onChange={(event) => setEdgeTarget(event.target.value)} className="h-9 min-w-0 rounded-lg border border-slate-200 bg-white px-2 text-xs">
									{nodes.map((node) => <option key={node.id} value={node.id}>{node.name}</option>)}
								</select>
								<Button type="button" size="sm" variant="outline" onClick={addEdge} disabled={nodes.length < 2 || graph.edges.length >= 56}><Plus />添加依赖</Button>
							</div>
							{graph.edges.length === 0 ? <div className="text-[10px] text-slate-500">尚无依赖边；无前驱节点会被视为 DAG 根节点。</div> : (
								<ul className="space-y-1">
									{graph.edges.map((edge) => (
										<li key={`${edge.source_node_id}:${edge.target_node_id}`} className="flex items-center justify-between gap-2 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[11px]">
											<span className="truncate">{nodeName(edge.source_node_id)} → {nodeName(edge.target_node_id)}</span>
											<Button type="button" size="icon-xs" variant="ghost" aria-label={`删除依赖 ${nodeName(edge.source_node_id)} 到 ${nodeName(edge.target_node_id)}`} onClick={() => removeEdge(edge.source_node_id, edge.target_node_id)}><Trash2 /></Button>
										</li>
									))}
								</ul>
							)}
						</section>
						<section className="space-y-2 rounded-xl border border-slate-200 p-3">
							<div className="text-xs font-semibold text-slate-800">命名输入绑定</div>
							<div className="grid gap-2 sm:grid-cols-2">
								<select aria-label="绑定目标节点" value={selectedBindingTarget} onChange={(event) => { setBindingTarget(event.target.value); setBindingSource(''); }} className="h-9 min-w-0 rounded-lg border border-slate-200 bg-white px-2 text-xs">
									{nodes.map((node) => <option key={node.id} value={node.id}>{node.name}</option>)}
								</select>
								<select aria-label="绑定来源祖先节点" value={bindingSource} onChange={(event) => setBindingSource(event.target.value)} className="h-9 min-w-0 rounded-lg border border-slate-200 bg-white px-2 text-xs">
									<option value="">选择前驱祖先</option>
									{ancestorIds.map((nodeId) => <option key={nodeId} value={nodeId}>{nodeName(nodeId)}</option>)}
								</select>
								<Input aria-label="绑定输入名称" value={bindingName} onChange={(event) => setBindingName(event.target.value)} maxLength={120} placeholder="输入名称，例如 summary" className="h-9 text-xs" />
								<select aria-label="绑定来源值" value={bindingValue} onChange={(event) => setBindingValue(event.target.value as 'output' | 'result')} className="h-9 rounded-lg border border-slate-200 bg-white px-2 text-xs">
									<option value="output">output（文本输出）</option>
									<option value="result">result（结构化结果）</option>
								</select>
							</div>
							<Button type="button" size="sm" variant="outline" onClick={addBinding} disabled={!bindingSource}><Plus />设置输入绑定</Button>
							{Object.keys(graph.input_bindings).length > 0 && (
								<div className="space-y-1">
								{Object.entries(graph.input_bindings).flatMap(([targetId, bindings]) =>
									Object.entries(bindings).map(([name, binding]) => (
										<div key={`${targetId}:${name}`} className="flex items-center justify-between gap-2 rounded-lg bg-violet-50/70 px-2.5 py-1.5 text-[10px] text-violet-800">
											<span className="truncate">{nodeName(targetId)} · {name} ← {nodeName(binding.source_node_id)}.{binding.source_value}</span>
											<Button type="button" size="icon-xs" variant="ghost" aria-label={`删除输入绑定 ${name}`} onClick={() => removeBinding(targetId, name)}><Trash2 /></Button>
										</div>
									)),
								)}
								</div>
							)}
						</section>
					</div>
					<div className="grid gap-4 xl:grid-cols-2">
						<section className="space-y-2 rounded-xl border border-slate-200 p-3">
							<div className="text-xs font-semibold text-slate-800">并发与资源约束</div>
							<label className="flex items-center gap-2 text-[11px] text-slate-600">
								<span>最大并发数</span>
								<Input type="number" min={1} max={Math.min(8, Math.max(1, nodes.length))} value={graph.max_concurrency} disabled={nodes.length === 0} onChange={(event) => {
									const value = Number(event.target.value);
									const limit = Math.min(8, Math.max(1, nodes.length));
									if (Number.isInteger(value) && value >= 1 && value <= limit) updateGraph({ ...graph, max_concurrency: value });
									else setEditError(`最大并发数必须为 1 到 ${limit} 之间的整数。`);
								}} className="h-8 w-24 text-xs" />
								<span>上限为 8，且不能超过节点数。</span>
							</label>
							<div className="flex flex-wrap gap-2">
								{Object.entries(graph.resource_limits).map(([name, capacity]) => (
									<div key={name} className="flex items-center gap-2 rounded-lg bg-slate-50 px-2.5 py-1 text-[10px] text-slate-700">
										<span>{name}：容量 {capacity}</span>
										<Button type="button" size="icon-xs" variant="ghost" aria-label={`删除资源容量 ${name}`} onClick={() => removeResourceLimit(name)}><Trash2 /></Button>
									</div>
								))}
							</div>
							<div className="grid gap-2 sm:grid-cols-[1fr_120px_auto]">
								<Input value={resourceName} onChange={(event) => setResourceName(event.target.value)} maxLength={120} placeholder="资源名，例如 browser_session" className="h-9 text-xs" />
								<Input type="number" min={1} value={resourceCapacity} onChange={(event) => setResourceCapacity(event.target.value)} aria-label="资源容量" className="h-9 text-xs" />
								<Button type="button" size="sm" variant="outline" onClick={saveResourceLimit}><Plus />设置容量</Button>
							</div>
							<div className="text-[10px] text-slate-500">容量必须覆盖节点的同名资源需求；服务端保存前会重新校验。</div>
						</section>
						<section className="space-y-2 rounded-xl border border-slate-200 p-3">
							<div className="text-xs font-semibold text-slate-800">服务端校验</div>
							{editError && <div role="alert" className="text-[11px] text-red-700">{editError}</div>}
							{validationIssues.length > 0 ? (
								<ul className="list-disc space-y-1 pl-4 text-[11px] text-red-800">
									{validationIssues.map((issue, index) => <li key={`${issue.path}:${index}`} className="break-words">{issue.path}：{issue.message}</li>)}
								</ul>
							) : <div className="text-[11px] text-slate-500">保存前会调用 Task 校验接口；循环依赖、输入绑定和资源容量均由服务端验证。</div>}
						</section>
					</div>
				</div>
			)}
			{nodes.length === 0 ? (
				<div className="p-5 text-center text-xs text-slate-500">DAG 暂无节点。</div>
			) : (
				<div className="max-h-[620px] overflow-auto bg-slate-50/60 p-2">
					{editable && (
						<div className="mb-2 px-2 text-[10px] text-slate-500">
							拖拽节点卡片可调整 Task 节点顺序；调度仍以依赖边为准，连线通过上方“节点依赖”编辑。
						</div>
					)}
					<div className="relative" style={{ width: canvasWidth, height: canvasHeight }}>
						<svg
							aria-hidden="true"
							className="absolute inset-0"
							width={canvasWidth}
							height={canvasHeight}
						>
							<defs>
								<marker
									id="task-dag-arrow"
									markerWidth="8"
									markerHeight="8"
									refX="7"
									refY="4"
									orient="auto"
									markerUnits="strokeWidth"
								>
									<path d="M0,0 L8,4 L0,8 z" fill="#94a3b8" />
								</marker>
							</defs>
							{graph.edges.map((edge, index) => {
								const source = positions.get(edge.source_node_id);
								const target = positions.get(edge.target_node_id);
								if (!source || !target) return null;
								const startX = source.x + DAG_CARD_WIDTH;
								const startY = source.y + DAG_CARD_HEIGHT / 2;
								const endX = target.x;
								const endY = target.y + DAG_CARD_HEIGHT / 2;
								const bend = Math.max(32, (endX - startX) / 2);
								const sourceRun = nodeRuns.get(edge.source_node_id);
								const targetRun = nodeRuns.get(edge.target_node_id);
								return (
									<path
										key={`${edge.source_node_id}:${edge.target_node_id}:${index}`}
										d={`M ${startX} ${startY} C ${startX + bend} ${startY}, ${endX - bend} ${endY}, ${endX} ${endY}`}
										fill="none"
										stroke={edgeColor(sourceRun, targetRun)}
										strokeWidth="2"
										markerEnd="url(#task-dag-arrow)"
									/>
								);
							})}
						</svg>
						{layout.map(({ node, x, y }) => {
							const nodeRun = nodeRuns.get(node.id);
							const selected = selectedNodeId === node.id;
							const status = nodeRun?.status ?? 'pending';
							const dependencies = graph.edges
								.filter((edge) => edge.target_node_id === node.id)
								.map((edge) => nodes.find((item) => item.id === edge.source_node_id)?.name ?? edge.source_node_id);
							const bindings = Object.entries(graph.input_bindings[node.id] ?? {});
							return (
								<article
									key={node.id}
									className={
										'absolute z-[1] flex flex-col overflow-hidden rounded-xl border bg-white p-3 shadow-sm ' +
									(status === 'running' ? 'task-running-node border-blue-300' : status === 'failed' ? 'border-red-200' : 'border-slate-200') +
									(editable ? ' cursor-grab active:cursor-grabbing' : '') +
									(selected ? ' ring-2 ring-inset ring-indigo-400' : '')
									}
									style={{ left: x, top: y, width: DAG_CARD_WIDTH, height: DAG_CARD_HEIGHT }}
									aria-label={`${node.name}，${statusLabel(status)}`}
								draggable={editable}
								onDragStart={(event) => {
									if (!editable) return;
									setDraggedNodeId(node.id);
									event.dataTransfer.effectAllowed = 'move';
									event.dataTransfer.setData('text/plain', node.id);
								}}
								onDragOver={(event) => {
									if (editable && draggedNodeId) event.preventDefault();
								}}
								onDrop={(event) => {
									event.preventDefault();
									const sourceId = event.dataTransfer.getData('text/plain') || draggedNodeId;
									if (sourceId) reorderNode(sourceId, node.id);
									setDraggedNodeId(null);
								}}
								onDragEnd={() => setDraggedNodeId(null)}
								>
									<button
										type="button"
										disabled={!run || !onNodeSelect}
										aria-label={`查看节点 ${node.name} 执行详情`}
										aria-pressed={selected}
										onClick={() => onNodeSelect?.(node.id)}
										className="flex min-w-0 items-center gap-2 rounded-md text-left disabled:cursor-default focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
									>
										<div className="min-w-0 flex-1 truncate text-xs font-semibold text-slate-800" title={node.name}>
											{node.name}
										</div>
										<Badge variant="outline" className={`shrink-0 gap-1 px-1.5 text-[9px] ${statusStyle(status)}`}>
											{statusIcon(status)}
											{statusLabel(status)}
										</Badge>
									</button>
									<div className="mt-1 text-[9px] text-slate-500">{stepTypeLabel(node)}{nodeRun?.attempt ? ` · 尝试 ${nodeRun.attempt}` : ''}</div>
									{dependencies.length > 0 && (
										<div className="mt-1 truncate text-[9px] text-slate-500" title={dependencies.join('、')}>
											依赖：{dependencies.join('、')}
										</div>
									)}
									{bindings.length > 0 && (
										<div className="mt-1 space-y-0.5 overflow-hidden text-[9px] leading-3 text-violet-700">
											{bindings.slice(0, 2).map(([name, binding]) => (
												<div key={name} className="truncate" title={`${name} ← ${binding.source_node_id}.${binding.source_value}`}>
													输入 {name} ← {nodes.find((item) => item.id === binding.source_node_id)?.name ?? binding.source_node_id}.{binding.source_value}
												</div>
											))}
											{bindings.length > 2 && <div>另有 {bindings.length - 2} 个输入绑定</div>}
										</div>
									)}
									{nodeRun?.error && (
										<div className="mt-auto line-clamp-2 pt-1 text-[9px] leading-3 text-red-700" title={sanitizeTaskRunText(nodeRun.error)}>
											{sanitizeTaskRunText(nodeRun.error)}
										</div>
									)}
									{!nodeRun?.error && nodeRun?.display_summary && (
										<div className="mt-auto line-clamp-2 pt-1 text-[9px] leading-3 text-slate-500" title={sanitizeTaskRunText(nodeRun.display_summary)}>
											{sanitizeTaskRunText(nodeRun.display_summary)}
										</div>
									)}
								</article>
							);
						})}
					</div>
				</div>
			)}
		</section>
	);
}
