import {
	ArrowRight,
	Bot,
	CalendarClock,
	Check,
	CircleAlert,
	CircleDashed,
	Code2,
	GitBranch,
	Loader2,
	Network,
	Play,
	Target,
	Timer,
	Wrench,
	XCircle,
} from 'lucide-react';
import * as React from 'react';

import { formatClockTime, formatDuration, getNodeDuration } from './task-view-model';
import type { NodeRunRecord, NodeRunStatus, TaskGenerationStatus, TaskNode, TaskRunRecord, TaskStepType } from '@/api';
import { Badge } from '@/components/ui/badge';

interface TaskOverviewProps {
	nodes: TaskNode[];
	run: TaskRunRecord | null;
	generationStatus: TaskGenerationStatus;
	isDirty: boolean;
}

type OverviewNodeStatus = NodeRunStatus | 'upcoming';
type ExecutionView = 'linear' | 'graph' | 'timeline';

function stepTypeLabel(type: TaskStepType): string {
	if (type === 'tool') return 'ToolStep';
	if (type === 'python') return 'CodeStep';
	return 'AgentStep';
}

function stepTypeIcon(type: TaskStepType) {
	if (type === 'tool') return <Wrench className="size-3.5" />;
	if (type === 'python') return <Code2 className="size-3.5" />;
	return <Bot className="size-3.5" />;
}

function nodeStatusLabel(status: OverviewNodeStatus): string {
	if (status === 'running') return '执行中';
	if (status === 'succeeded') return '已完成';
	if (status === 'failed') return '执行失败';
	if (status === 'canceled') return '已取消';
	if (status === 'pending') return '等待执行';
	return '尚未执行';
}

function nodeStatusIcon(status: OverviewNodeStatus) {
	if (status === 'running') return <Loader2 className="size-4 animate-spin" />;
	if (status === 'succeeded') return <Check className="size-4" />;
	if (status === 'failed') return <CircleAlert className="size-4" />;
	if (status === 'canceled') return <XCircle className="size-4" />;
	if (status === 'pending') return <Play className="size-3.5" />;
	return <CircleDashed className="size-4" />;
}

function nodeStatusClass(status: OverviewNodeStatus): string {
	if (status === 'running') return 'border-blue-300 bg-blue-50 text-blue-700 task-running-node';
	if (status === 'succeeded') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
	if (status === 'failed') return 'border-red-200 bg-red-50 text-red-700';
	if (status === 'canceled') return 'border-slate-200 bg-slate-100 text-slate-500';
	if (status === 'pending') return 'border-slate-300 bg-white text-slate-500';
	return 'border-slate-200 bg-white text-slate-400';
}

function matchesCurrentNodes(nodes: TaskNode[], run: TaskRunRecord | null, isDirty: boolean): boolean {
	if (isDirty || !run || nodes.length !== run.nodes.length) return false;
	return nodes.every((node, index) => run.nodes[index]?.id === node.id);
}

function statusBadgeClass(status: OverviewNodeStatus): string {
	if (status === 'running') return 'border-blue-200 bg-blue-50 text-blue-700';
	if (status === 'succeeded') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
	if (status === 'failed') return 'border-red-200 bg-red-50 text-red-700';
	return 'border-slate-200 bg-slate-50 text-slate-600';
}

function actionDescriptions(node: TaskNode): string[] {
	if (node.type === 'tool' && node.config.type === 'tool') {
		return ['调用工具 ' + (node.config.tool_name || '待配置'), '读取工具返回结果'];
	}
	if (node.type === 'python' && node.config.type === 'python') {
		return ['执行 CodeStep 代码', '超时限制 ' + node.config.timeout_seconds + ' 秒'];
	}
	return ['向 Agent 提交当前步骤要求', '整理并返回节点结果'];
}

function CurrentTaskNode({
	node,
	status,
	nodeRun,
	run,
	index,
}: {
	node: TaskNode | null;
	status: OverviewNodeStatus;
	nodeRun: NodeRunRecord | undefined;
	run: TaskRunRecord | null;
	index: number;
}) {
	if (!node) {
		return (
			<div className="mt-4 rounded-xl border border-dashed border-slate-200 bg-slate-50/70 p-5 text-center text-sm text-slate-500">
				先生成或新增任务步骤，这里会展示所选步骤的输入、动作与输出。
			</div>
		);
	}
	const artifact = run?.artifacts.find((item) => item.node_id === node.id);
	const expectedOutput = artifact?.name ?? node.artifact?.filename ?? '节点运行结果';
	const inputText = nodeRun?.input?.trim() || '当前步骤的实际输入会在执行后显示。';
	const outputText = nodeRun?.display_summary?.trim() || nodeRun?.output?.trim() || '当前步骤的输出会在执行后显示。';
	const actions = actionDescriptions(node);
	const duration = nodeRun ? getNodeDuration(nodeRun) : null;

	return (
		<div className="mt-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-[0_1px_2px_rgba(16,24,40,0.03)]">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<div className="flex min-w-0 items-center gap-2.5">
					<div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
						<Code2 className="size-4" />
					</div>
					<div className="min-w-0">
						<div className="truncate text-sm font-semibold text-slate-900">当前执行节点：{node.name}</div>
						<div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-slate-500">
							{stepTypeIcon(node.type)}
							<span>{stepTypeLabel(node.type)}</span>
							<span>·</span>
							<span>第 {index + 1} 步</span>
						</div>
					</div>
				</div>
				<div className="flex shrink-0 items-center gap-2">
					<Badge variant="outline" className={statusBadgeClass(status)}>{nodeStatusLabel(status)}</Badge>
					<div className="flex items-center gap-1 rounded-lg bg-slate-50 px-2 py-1 text-xs font-medium text-slate-600">
						<Timer className="size-3.5" />
						{formatDuration(duration)}
					</div>
				</div>
			</div>

			<div className="mt-4 grid gap-3 lg:grid-cols-3">
				<div className="rounded-xl border border-slate-100 bg-slate-50/80 p-3">
					<div className="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-800">
						<span className="flex size-6 items-center justify-center rounded-md bg-blue-100 text-blue-700"><Play className="size-3" /></span>
						输入内容
					</div>
					<div className="line-clamp-3 min-h-12 whitespace-pre-wrap text-xs leading-5 text-slate-600">{inputText}</div>
					<div className="mt-2 text-[10px] text-slate-400">{nodeRun ? '来自本次运行' : '等待执行输入'}</div>
				</div>
				<div className="rounded-xl border border-slate-100 bg-slate-50/80 p-3">
					<div className="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-800">
						<span className="flex size-6 items-center justify-center rounded-md bg-emerald-100 text-emerald-700"><Check className="size-3.5" /></span>
						系统执行动作
					</div>
					<ul className="space-y-1.5 text-[11px] leading-4 text-slate-600">
						{actions.map((action) => <li key={action} className="flex items-start gap-1.5"><Check className="mt-0.5 size-3 shrink-0 text-emerald-600" />{action}</li>)}
					</ul>
					<div className="mt-2 text-[10px] text-slate-400">根据步骤配置整理</div>
				</div>
				<div className="rounded-xl border border-slate-100 bg-slate-50/80 p-3">
					<div className="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-800">
						<span className="flex size-6 items-center justify-center rounded-md bg-emerald-100 text-emerald-700"><GitBranch className="size-3.5" /></span>
						预期输出
					</div>
					<div className="truncate text-xs font-medium text-slate-700" title={expectedOutput}>{expectedOutput}</div>
					<div className="mt-1 line-clamp-2 min-h-8 text-[11px] leading-4 text-slate-500">{outputText}</div>
					<div className="mt-2 text-[10px] text-slate-400">{artifact ? '本次运行已生成产物' : '产物类型由节点配置决定'}</div>
				</div>
			</div>
		</div>
	);
}

export function TaskOverview({ nodes, run, generationStatus, isDirty }: TaskOverviewProps) {
	const hasMatchingRun = matchesCurrentNodes(nodes, run, isDirty);
	const nodeRuns = React.useMemo(
		() => new Map((hasMatchingRun ? run?.node_runs : [])?.map((nodeRun) => [nodeRun.node_id, nodeRun]) ?? []),
		[hasMatchingRun, run?.node_runs],
	);
	const statuses: OverviewNodeStatus[] = nodes.map((node) =>
		hasMatchingRun ? nodeRuns.get(node.id)?.status ?? 'pending' : 'upcoming',
	);
	const preferredIndex = statuses.findIndex((status) => status === 'running');
	const pendingIndex = statuses.findIndex((status) => status === 'pending' || status === 'failed');
	const defaultIndex = preferredIndex >= 0 ? preferredIndex : pendingIndex >= 0 ? pendingIndex : Math.max(0, nodes.length - 1);
	const [selection, setSelection] = React.useState({ id: nodes[defaultIndex]?.id ?? null, manual: false });
	const selectedNodeId = selection.id;
	const [view, setView] = React.useState<ExecutionView>('linear');

	React.useEffect(() => {
		const selectedExists = nodes.some((node) => node.id === selectedNodeId);
		const nextId = nodes[defaultIndex]?.id ?? null;
		if (!selectedExists || (!selection.manual && selectedNodeId !== nextId)) setSelection({ id: nextId, manual: false });
	}, [defaultIndex, nodes, selectedNodeId, selection.manual]);

	const selectedIndex = nodes.findIndex((node) => node.id === selectedNodeId);
	const selectedNode = selectedIndex >= 0 ? nodes[selectedIndex] : null;
	const selectedStatus = selectedIndex >= 0 ? statuses[selectedIndex] : 'upcoming';
	const completedCount = statuses.filter((status) => status === 'succeeded').length;
	const progress = nodes.length > 0 ? Math.round((completedCount / nodes.length) * 100) : 0;
	const currentIndex = statuses.findIndex((status) => status === 'running' || status === 'failed' || status === 'pending');
	const currentNode = currentIndex >= 0 ? nodes[currentIndex] : null;
	const currentLabel = !nodes.length
		? '等待生成节点'
		: generationStatus === 'generating'
			? '正在生成节点'
			: !run || !hasMatchingRun
				? '尚未执行'
				: currentNode?.name ?? '任务已完成';
	const summary = !nodes.length
		? '先填写任务目标，再生成或新增任务步骤。'
		: generationStatus === 'generating'
			? '正在根据任务目标生成可执行流程。'
			: !run
				? '流程已准备好，执行后会在这里显示每个步骤的状态。'
				: !hasMatchingRun
					? '流程内容已变化，保存并执行后即可查看新流程状态。'
					: run.status === 'failed'
						? '本次执行未完成，请查看右侧执行轨迹和失败原因。'
						: run.status === 'canceled'
							? '本次执行已取消，可以重新执行任务。'
							: completedCount + ' / ' + nodes.length + ' 个步骤已完成。';

	const selectNode = (node: TaskNode) => setSelection({ id: node.id, manual: true });
	const views: Array<{ id: ExecutionView; label: string; icon: React.ReactNode }> = [
		{ id: 'linear', label: '线性视图', icon: <GitBranch className="size-3.5" /> },
		{ id: 'graph', label: '图谱视图', icon: <Network className="size-3.5" /> },
		{ id: 'timeline', label: '时间线视图', icon: <CalendarClock className="size-3.5" /> },
	];

	return (
		<section className="mb-4 rounded-2xl border border-blue-100 bg-gradient-to-br from-blue-50/80 via-white to-white p-4 shadow-[0_1px_2px_rgba(16,24,40,0.04),0_4px_12px_rgba(16,24,40,0.04)]" aria-label="任务执行主舞台">
			<div className="flex flex-wrap items-start justify-between gap-3">
				<div>
					<div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
						<div className="flex size-8 items-center justify-center rounded-xl bg-blue-100 text-blue-700"><Target className="size-4" /></div>
						任务执行主舞台
					</div>
					<div className="mt-1 text-xs text-slate-500">AI 正在规划、检索、执行任务...</div>
				</div>
				<div className="flex max-w-full items-center gap-1 overflow-x-auto rounded-xl border border-slate-200 bg-white p-1">
					{views.map((item) => (
						<button
							key={item.id}
							type="button"
							aria-pressed={view === item.id}
							onClick={() => setView(item.id)}
							className={'flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px] font-medium transition-colors ' +
								(view === item.id ? 'bg-blue-50 text-blue-700' : 'text-slate-500 hover:bg-slate-50 hover:text-slate-800')}
						>
							{item.icon}{item.label}
						</button>
					))}
				</div>
			</div>

			{!nodes.length ? (
				<div className="mt-4 flex items-center gap-3 rounded-xl border border-dashed border-blue-200 bg-white/70 px-4 py-5 text-xs text-slate-500">
					<CircleDashed className="size-4 shrink-0 text-blue-500" />{summary}
				</div>
			) : view === 'linear' ? (
				<div className="mt-5 overflow-x-auto pb-2">
					<div className="flex min-w-max items-start">
						<div className="flex w-12 shrink-0 flex-col items-center gap-2">
							<div className="flex size-9 items-center justify-center rounded-full bg-blue-600 text-xs font-semibold text-white shadow-sm">始</div>
							<span className="text-[10px] font-medium text-slate-500">开始</span>
						</div>
						{nodes.map((node, index) => {
							const status = statuses[index];
							const nodeRun = nodeRuns.get(node.id);
							return (
								<React.Fragment key={node.id}>
									<div className="flex items-center px-2 pt-4 text-slate-300"><div className="h-px w-6 bg-slate-200" /><ArrowRight className="size-3.5" /></div>
									<button type="button" onClick={() => selectNode(node)} className="w-40 shrink-0 rounded-xl text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400">
										<div className="flex items-center gap-2">
											<div className={'flex size-9 shrink-0 items-center justify-center rounded-full border ' + nodeStatusClass(status)} aria-label={nodeStatusLabel(status)}>{nodeStatusIcon(status)}</div>
											<div className="min-w-0"><div className="truncate text-xs font-semibold text-slate-800" title={node.name}>{node.name}</div><div className="mt-0.5 flex items-center gap-1 text-[10px] text-slate-500">{stepTypeIcon(node.type)}<span>{stepTypeLabel(node.type)}</span></div></div>
										</div>
										<div className={'mt-2 truncate text-[10px] font-medium ' + (selectedNodeId === node.id ? 'text-blue-700' : 'text-slate-500')}>{nodeStatusLabel(status)}{nodeRun && <span> · {formatDuration(getNodeDuration(nodeRun))}</span>}</div>
									</button>
								</React.Fragment>
							);
						})}
						<div className="flex items-center px-2 pt-4 text-slate-300"><div className="h-px w-6 bg-slate-200" /><ArrowRight className="size-3.5" /></div>
						<div className="flex w-12 shrink-0 flex-col items-center gap-2"><div className="flex size-9 items-center justify-center rounded-full border border-slate-200 bg-white text-xs font-semibold text-slate-500">终</div><span className="text-[10px] font-medium text-slate-500">结束</span></div>
					</div>
				</div>
			) : view === 'graph' ? (
				<div className="mt-4 rounded-xl border border-slate-100 bg-white px-3 pb-3 pt-2">
					<div className="mx-auto flex w-fit items-center gap-2 rounded-xl border border-blue-100 bg-blue-50 px-3 py-2 text-xs font-semibold text-blue-800">
						<Target className="size-3.5" />任务目标
					</div>
					<div className="relative mt-5 grid grid-cols-2 gap-x-3 gap-y-6 before:absolute before:left-[12%] before:right-[12%] before:top-0 before:h-px before:bg-slate-200 sm:grid-cols-3">
						{nodes.map((node, index) => (
							<button key={node.id} type="button" onClick={() => selectNode(node)} className="relative mt-2 flex min-h-20 flex-col items-start justify-center gap-2 rounded-xl border border-slate-200 bg-white p-3 text-left transition-colors before:absolute before:bottom-full before:left-1/2 before:h-5 before:w-px before:bg-slate-200 hover:border-blue-300 hover:bg-blue-50/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400">
								<div className="flex w-full items-center gap-2"><div className={'flex size-7 shrink-0 items-center justify-center rounded-full border ' + nodeStatusClass(statuses[index])}>{nodeStatusIcon(statuses[index])}</div><span className="truncate text-xs font-semibold text-slate-800">{node.name}</span></div>
								<div className="flex items-center gap-1 pl-9 text-[10px] text-slate-500">{stepTypeIcon(node.type)}{stepTypeLabel(node.type)}<span>·</span>{nodeStatusLabel(statuses[index])}</div>
							</button>
						))}
					</div>
				</div>
			) : (
				<div className="mt-4 rounded-xl border border-slate-100 bg-white p-3">
					<div className="mb-2 text-[11px] font-semibold text-slate-500">任务步骤时间线</div>
					<div className="space-y-1">
						{nodes.map((node, index) => {
							const nodeRun = nodeRuns.get(node.id);
							return (
								<button key={node.id} type="button" onClick={() => selectNode(node)} className="flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left hover:bg-slate-50">
									<div className={'flex size-7 shrink-0 items-center justify-center rounded-full border ' + nodeStatusClass(statuses[index])}>{nodeStatusIcon(statuses[index])}</div>
									<div className="min-w-0 flex-1"><div className="truncate text-xs font-medium text-slate-800">{index + 1}. {node.name}</div><div className="mt-0.5 text-[10px] text-slate-500">{stepTypeLabel(node.type)} · {nodeStatusLabel(statuses[index])}</div></div>
									<div className="shrink-0 text-right text-[10px] text-slate-500">{nodeRun ? formatClockTime(nodeRun.started_at) : '—'}<div className="mt-0.5">{nodeRun ? formatDuration(getNodeDuration(nodeRun)) : '等待执行'}</div></div>
								</button>
							);
						})}
					</div>
				</div>
			)}

			<CurrentTaskNode
				node={selectedNode}
				status={selectedStatus}
				nodeRun={selectedNode ? nodeRuns.get(selectedNode.id) : undefined}
				run={hasMatchingRun ? run : null}
				index={selectedIndex >= 0 ? selectedIndex : 0}
			/>

			<div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-slate-200/80 pt-3 text-[11px] text-slate-500">
				<div className="flex items-center gap-1.5"><Target className="size-3.5 text-blue-600" /><span>当前节点</span><span className="font-semibold text-slate-800">{currentLabel}</span></div>
				<div className="flex items-center gap-1.5"><span className="font-medium text-slate-700">{summary}</span></div>
				<div className="ml-auto flex min-w-32 items-center gap-2">
					<div className="h-1.5 flex-1 overflow-hidden rounded-full bg-blue-100"><div className="h-full rounded-full bg-blue-600 transition-[width] duration-300" style={{ width: progress + '%' }} /></div>
					<span className="shrink-0 font-semibold text-slate-700">{progress}%</span>
				</div>
			</div>
		</section>
	);
}