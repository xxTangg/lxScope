import {
	Activity,
	AlertCircle,
	Bot,
	Check,
	Clock3,
	Download,
	Eye,
	FileText,
	FolderOpen,
	GitBranch,
	Loader2,
	MessageSquareText,

	RotateCcw,
	Search,
	Sparkles,
	Wrench,
} from 'lucide-react';
import * as React from 'react';

import { TaskKnowledgeGraph } from './task-knowledge-graph';
import { formatClockTime, formatDuration, getNodeDuration } from './task-view-model';
import { taskApi } from '@/api';
import type { NodeRunRecord, RunStatus, TaskArtifactRecord, TaskNode, TaskRunEvent, TaskRunRecord, TaskStepType } from '@/api';
import { Markdown } from '@/components/markdown';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from '@/components/ui/dialog';

interface TaskRunPanelProps {
	run: TaskRunRecord | null;
	executionEvents: TaskRunEvent[];
	goal: string;
	taskName: string;
	knowledgeBaseNames: string[];
	width?: number;
	onCancel: () => void;
	onRetry: () => void;
}

type IntelligenceTab = 'timeline' | 'analysis' | 'tools' | 'artifacts';

type TimelineItem = {
	id: string;
	time: string;
	title: string;
	description: string;
	kind: 'system' | 'agent' | 'node' | 'output' | 'error';
	node?: NodeRunRecord;
	showGraphAction?: boolean;
};

const TABS: Array<{ id: IntelligenceTab; label: string }> = [
	{ id: 'timeline', label: '执行轨迹' },
	{ id: 'analysis', label: '智能分析' },
	{ id: 'tools', label: '工具调用' },
	{ id: 'artifacts', label: '最终产物' },
];

function runStatusLabel(status: RunStatus): string {
	const labels: Record<RunStatus, string> = {
		queued: '等待启动',
		running: '执行中',
		succeeded: '已完成',
		failed: '执行失败',
		canceled: '已取消',
		timed_out: '执行超时',
	};
	return labels[status];
}

function runStatusClass(status: RunStatus): string {
	if (status === 'succeeded') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
	if (status === 'failed' || status === 'timed_out') return 'border-red-200 bg-red-50 text-red-700';
	if (status === 'running' || status === 'queued') return 'border-blue-200 bg-blue-50 text-blue-700';
	return 'border-slate-200 bg-slate-100 text-slate-600';
}

function nodeStatusLabel(status: NodeRunRecord['status']): string {
	if (status === 'running') return '执行中';
	if (status === 'succeeded') return '成功';
	if (status === 'failed') return '失败';
	if (status === 'canceled') return '已取消';
	return '等待中';
}

function nodeStatusIcon(status: NodeRunRecord['status']) {
	if (status === 'running') return <Loader2 className="size-4 animate-spin text-blue-600" />;
	if (status === 'succeeded') return <Check className="size-4 text-emerald-600" />;
	if (status === 'failed') return <AlertCircle className="size-4 text-red-600" />;
	return <Clock3 className="size-3.5 text-slate-400" />;
}

function stepTypeLabel(type: TaskStepType): string {
	if (type === 'tool') return 'ToolStep';
	if (type === 'python') return 'CodeStep';
	return 'AgentStep';
}

function nodeDetails(node: TaskNode | undefined, run: TaskRunRecord) {
	if (!node) return null;
	if (node.config.type === 'tool') {
		return (
			<div className="space-y-1 text-[11px] leading-5 text-slate-500">
				<div>工具：{node.config.tool_name || '未配置'}</div>
				<div className="break-all">参数：{JSON.stringify(node.config.arguments, null, 2)}</div>
			</div>
		);
	}
	if (node.config.type === 'python') {
		return (
			<div className="space-y-1 text-[11px] leading-5 text-slate-500">
				<div>超时：{node.config.timeout_seconds} 秒</div>
				<details><summary className="cursor-pointer">查看 CodeStep 代码</summary><pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap">{node.config.code || '未配置代码'}</pre></details>
			</div>
		);
	}
	return (
		<div className="space-y-1 text-[11px] leading-5 text-slate-500">
			<div>Agent：{node.config.agent_id || run.context.agent_id || '当前会话 Agent'}</div>
			<div>Session：{node.config.session_id || run.context.session_id || '当前会话'}</div>
			<details><summary className="cursor-pointer">查看步骤 Prompt</summary><div className="mt-2 whitespace-pre-wrap">{node.config.prompt}</div></details>
		</div>
	);
}

function artifactFormatLabel(artifact: TaskArtifactRecord): string {
	if (artifact.format === 'markdown') return 'Markdown';
	if (artifact.format === 'docx') return 'Word';
	return 'Excel';
}

function artifactSizeLabel(size: number): string {
	if (size < 1024) return size + ' B';
	if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB';
	return (size / (1024 * 1024)).toFixed(1) + ' MB';
}

function buildTimeline(run: TaskRunRecord, lifecycleEvents: TaskRunEvent[]): TimelineItem[] {
	if (lifecycleEvents.length > 0) {
		return [...lifecycleEvents]
			.sort((left, right) => left.sequence - right.sequence)
			.map((event) => {
				const node = event.node_id
					? run.node_runs.find((item) => item.node_id === event.node_id)
					: undefined;
				const payload = event.payload;
				const summary = typeof payload.summary === 'string' ? payload.summary : '';
				const message = typeof payload.message === 'string' ? payload.message : '';
				const artifactName = typeof payload.name === 'string' ? payload.name : '';
				const stepType = typeof payload.step_type === 'string' ? payload.step_type : '';
				let title = event.type;
				let description = summary || message || stepType;
				let kind: TimelineItem['kind'] = 'system';

				switch (event.type) {
					case 'run.created':
						title = '创建任务上下文';
						description = '载入任务版本 v' + run.task_revision + ' 与执行配置';
						break;
					case 'run.started':
						title = run.context.agent_id ? '启动 Agent' : '开始执行任务';
						description = run.context.agent_id ? 'Agent ' + run.context.agent_id + ' 已开始执行' : '任务运行已开始';
						kind = run.context.agent_id ? 'agent' : 'system';
						break;
					case 'step.started':
						title = '开始步骤：' + (node?.name ?? event.node_id ?? '任务节点');
						description = stepType ? stepTypeLabel(stepType as TaskStepType) : '节点开始执行';
						kind = 'node';
						break;
					case 'step.succeeded':
						title = '完成步骤：' + (node?.name ?? event.node_id ?? '任务节点');
						description = summary || (stepType ? stepTypeLabel(stepType as TaskStepType) : '节点执行完成');
						kind = 'node';
						break;
					case 'step.failed':
						title = '步骤执行失败：' + (node?.name ?? event.node_id ?? '任务节点');
						description = message || node?.error || '节点执行失败';
						kind = 'error';
						break;
					case 'artifact.created':
						title = '生成任务产物';
						description = artifactName || '已创建文件产物';
						kind = 'output';
						break;
					case 'run.succeeded':
						title = '任务执行完成';
						description = summary || '本次运行已完成';
						kind = 'output';
						break;
					case 'run.failed':
						title = '任务执行失败';
						description = message || run.error || '本次运行失败';
						kind = 'error';
						break;
					case 'run.canceled':
						title = '任务已取消';
						description = '运行由用户停止';
						break;
				}

				return {
					id: event.id,
					time: event.created_at,
					title,
					description,
					kind,
					node,
					showGraphAction: Boolean(
						node &&
						node.status !== 'pending' &&
						(event.type === 'step.succeeded' || event.type === 'step.failed') &&
						run.nodes.some(
							(definition) =>
								definition.id === node.node_id && definition.knowledge_graph_enabled,
						),
					),
				};
			});
	}

	const items: TimelineItem[] = [
		{
			id: 'created',
			time: run.created_at,
			title: '创建任务上下文',
			description: '载入任务版本 v' + run.task_revision + ' 与执行配置',
			kind: 'system',
		},
	];
	if (run.context.agent_id) {
		items.push({
			id: 'agent',
			time: run.started_at ?? run.created_at,
			title: '启动 Agent',
			description: '本次运行绑定了 Agent ' + run.context.agent_id,
			kind: 'agent',
		});
	}
	for (const node of run.node_runs) {
		const time = node.finished_at ?? node.started_at ?? run.created_at;
		const title = node.status === 'succeeded'
			? '完成步骤：' + node.name
			: node.status === 'failed'
				? '步骤执行失败：' + node.name
				: node.status === 'running'
					? '正在执行：' + node.name
					: '等待执行：' + node.name;
		items.push({
			id: node.node_id,
			time,
			title,
			description: node.error || node.display_summary || stepTypeLabel(node.type),
			kind: node.status === 'failed' ? 'error' : 'node',
			node,
			showGraphAction:
				node.status !== 'pending' &&
				run.nodes.some(
					(definition) =>
						definition.id === node.node_id && definition.knowledge_graph_enabled,
				),
		});
	}
	if (run.finished_at && run.status === 'succeeded') {
		items.push({
			id: 'output',
			time: run.finished_at,
			title: '写入执行结果',
			description: run.artifacts.length ? '生成 ' + run.artifacts.length + ' 项任务产物' : '本次运行已完成',
			kind: 'output',
		});
	}
	return items.sort((left, right) => new Date(left.time).getTime() - new Date(right.time).getTime());
}
function timelineIcon(kind: TimelineItem['kind']) {
	if (kind === 'agent') return <Bot className="size-3.5" />;
	if (kind === 'node') return <Activity className="size-3.5" />;
	if (kind === 'output') return <FolderOpen className="size-3.5" />;
	if (kind === 'error') return <AlertCircle className="size-3.5" />;
	return <FileText className="size-3.5" />;
}

function NodeResultCard({
	nodeRun,
	run,
	onPreviewGraph,
}: {
	nodeRun: NodeRunRecord;
	run: TaskRunRecord;
	onPreviewGraph: (nodeId: string) => void;
}) {
	const definition = run.nodes.find((item) => item.id === nodeRun.node_id);
	const output = nodeRun.display_summary || nodeRun.output || nodeRun.error;
	const nodeArtifacts = run.artifacts.filter((artifact) => artifact.node_id === nodeRun.node_id);
	return (
		<div className="rounded-xl border border-slate-200 bg-white p-3">
			<div className="flex items-center gap-2">
				<div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-slate-50">{nodeStatusIcon(nodeRun.status)}</div>
				<div className="min-w-0 flex-1 truncate text-xs font-semibold text-slate-800">{nodeRun.name}</div>
				<Badge variant="outline" className={'h-5 border px-1.5 text-[10px] ' + (nodeRun.status === 'succeeded' ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : nodeRun.status === 'failed' ? 'border-red-200 bg-red-50 text-red-700' : 'border-blue-200 bg-blue-50 text-blue-700')}>
					{nodeStatusLabel(nodeRun.status)}
				</Badge>
				<span className="shrink-0 text-[10px] text-slate-500">{formatDuration(getNodeDuration(nodeRun))}</span>
			</div>
			{output && <div className="mt-3 whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-[11px] leading-5 text-slate-600">{output}</div>}
			{nodeRun.error && <div className="mt-2 rounded-lg bg-red-50 p-2 text-[11px] leading-5 text-red-700">{nodeRun.error}</div>}
			{nodeArtifacts.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{nodeArtifacts.map((artifact) => <Badge key={artifact.id} variant="outline" className="max-w-full truncate border-emerald-200 bg-emerald-50 text-[10px] text-emerald-700">{artifact.name}</Badge>)}</div>}
			<details className="mt-2 rounded-lg border border-slate-100 bg-slate-50/60 p-2">
				<summary className="cursor-pointer text-[10px] font-medium text-slate-500">查看节点输入与配置</summary>
				<div className="mt-2 whitespace-pre-wrap text-[11px] leading-5 text-slate-600">{nodeRun.input || '本次运行没有记录节点输入'}</div>
				<div className="mt-2 rounded-md border border-slate-100 bg-white p-2">{nodeDetails(definition, run)}</div>
			</details>
			{definition?.knowledge_graph_enabled && (
				<div className="mt-2 flex items-center justify-between gap-2 rounded-lg border border-blue-100 bg-blue-50/60 px-2.5 py-2">
					<div className="min-w-0"><div className="text-[11px] font-medium text-blue-900">节点知识图谱</div><div className="mt-0.5 text-[10px] text-blue-700/70">查看本步骤实体关系与来源证据</div></div>
					<Button type="button" size="sm" variant="outline" disabled={nodeRun.status === 'pending'} onClick={() => onPreviewGraph(nodeRun.node_id)} className="h-7 shrink-0 text-[10px]"><Eye />预览图谱</Button>
				</div>
			)}
		</div>
	);
}

function ArtifactList({
	run,
	busyId,
	onPreview,
	onDownload,
}: {
	run: TaskRunRecord;
	busyId: string | null;
	onPreview: (artifact: TaskArtifactRecord) => void;
	onDownload: (artifact: TaskArtifactRecord) => void;
}) {
	if (!run.artifacts.length) {
		return <div className="rounded-xl border border-dashed border-slate-200 px-4 py-8 text-center text-xs text-slate-500">本次运行还没有生成文件产物。</div>;
	}
	return (
		<div className="grid gap-2 sm:grid-cols-2">
			{run.artifacts.map((artifact) => (
				<div key={artifact.id} className="min-w-0 rounded-xl border border-slate-200 bg-white p-3">
					<div className="flex items-start gap-2">
						<div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600"><FileText className="size-4" /></div>
						<div className="min-w-0 flex-1"><div className="truncate text-xs font-semibold text-slate-800" title={artifact.name}>{artifact.name}</div><div className="mt-1 text-[10px] text-slate-500">{artifactFormatLabel(artifact)} · {artifactSizeLabel(artifact.size_bytes)}</div><div className="mt-1 text-[10px] text-slate-400">{formatClockTime(artifact.created_at)}</div></div>
					</div>
					<div className="mt-2 flex justify-end gap-1">
						{artifact.preview_text && <Button variant="ghost" size="sm" className="h-7 px-2 text-[10px]" onClick={() => onPreview(artifact)}><Eye />预览</Button>}
						<Button variant="outline" size="sm" className="h-7 px-2 text-[10px]" disabled={busyId === artifact.id} onClick={() => onDownload(artifact)}><Download />下载</Button>
					</div>
				</div>
			))}
		</div>
	);
}

export function TaskRunPanel({
	run,
	executionEvents,
	goal,
	taskName,
	knowledgeBaseNames,
	width = 360,
	onCancel,
	onRetry,
}: TaskRunPanelProps) {
	const [tab, setTab] = React.useState<IntelligenceTab>('timeline');
	const [selectedNodeId, setSelectedNodeId] = React.useState<string | null>(null);
	const userSelectedNodeRef = React.useRef(false);
	const [previewArtifact, setPreviewArtifact] = React.useState<TaskArtifactRecord | null>(null);
	const [artifactPreviewMode, setArtifactPreviewMode] = React.useState<'reader' | 'source'>('reader');
	const [artifactBusyId, setArtifactBusyId] = React.useState<string | null>(null);
	const [previewGraphNodeId, setPreviewGraphNodeId] = React.useState<string | null>(null);

	React.useEffect(() => {
		setTab('timeline');
		setSelectedNodeId(null);
		userSelectedNodeRef.current = false;
		setPreviewArtifact(null);
		setArtifactPreviewMode('reader');
		setPreviewGraphNodeId(null);
	}, [run?.id]);

	React.useEffect(() => {
		if (!run?.node_runs.length) return;
		setSelectedNodeId((current) => {
			if (userSelectedNodeRef.current && current && run.node_runs.some((item) => item.node_id === current)) return current;
			const preferred = run.node_runs.find((item) => item.status === 'running')
				?? run.node_runs.find((item) => item.status === 'failed')
				?? run.node_runs[run.node_runs.length - 1];
			return preferred?.node_id ?? null;
		});
	}, [run?.id, run?.node_runs]);
	const selectedNodeRun = run?.node_runs.find((item) => item.node_id === selectedNodeId) ?? null;
	const previewGraphNode = run?.node_runs.find((item) => item.node_id === previewGraphNodeId);
	const events = run ? buildTimeline(run, executionEvents) : [];
	const agentNodes = run?.node_runs.filter((node) => node.type === 'agent') ?? [];
	const toolNodes = run?.node_runs.filter((node) => node.type === 'tool') ?? [];
	const codeNodes = run?.node_runs.filter((node) => node.type === 'python') ?? [];


	const downloadArtifact = async (artifact: TaskArtifactRecord) => {
		if (!run) return;
		setArtifactBusyId(artifact.id);
		try {
			const blob = await taskApi.getArtifactContent(run.id, artifact.id, true);
			const url = URL.createObjectURL(blob);
			const anchor = document.createElement('a');
			anchor.href = url;
			anchor.download = artifact.name;
			anchor.click();
			window.setTimeout(() => URL.revokeObjectURL(url), 1000);
		} finally {
			setArtifactBusyId(null);
		}
	};

	const openArtifactPreview = (artifact: TaskArtifactRecord) => {
		setArtifactPreviewMode('reader');
		setPreviewArtifact(artifact);
	};

	const showTimeline = Boolean(run && (run.node_runs.length > 0 || events.length > 0));
	const terminal = run ? ['succeeded', 'failed', 'canceled', 'timed_out'].includes(run.status) : true;

	return (
		<aside className="flex min-h-0 min-w-[300px] shrink-0 flex-col overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-[0_1px_2px_rgba(16,24,40,0.04),0_4px_12px_rgba(16,24,40,0.05)]" style={{ width: width + 'px' }}>
			<div className="border-b border-slate-100 px-4 pb-3 pt-4">
				<div className="flex items-start justify-between gap-2">
					<div className="min-w-0"><div className="text-base font-semibold text-slate-900">执行情报</div><div className="mt-1 text-[11px] text-slate-500">实时展示 AI 的思考与行动</div></div>
					{run ? <Badge variant="outline" className={'shrink-0 ' + runStatusClass(run.status)}>{runStatusLabel(run.status)}</Badge> : <Badge variant="outline" className="shrink-0 border-slate-200 bg-slate-50 text-slate-500">尚未执行</Badge>}
				</div>
				<div className="mt-3 flex items-center gap-1.5 text-[10px] text-slate-500"><span className={'size-1.5 rounded-full ' + (run?.status === 'running' || run?.status === 'queued' ? 'bg-blue-500 task-live-dot' : 'bg-slate-300')} />{run?.status === 'running' || run?.status === 'queued' ? '实时更新中' : run ? '执行状态已同步' : '启动任务后显示实时过程'}</div>
			</div>

			<div className="grid grid-cols-4 border-b border-slate-100 px-2">
				{TABS.map((item) => (
					<button key={item.id} type="button" onClick={() => setTab(item.id)} aria-pressed={tab === item.id} className={'relative min-w-0 truncate px-1 py-3 text-[10px] font-medium transition-colors ' + (tab === item.id ? 'text-blue-700' : 'text-slate-500 hover:text-slate-800')}>
						{item.label}{tab === item.id && <span className="absolute inset-x-1 bottom-0 h-0.5 rounded-full bg-blue-600" />}
					</button>
				))}
			</div>

			<div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
				{!run ? (
					<div className="flex min-h-40 flex-col items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50/60 px-4 text-center">
						<div className="flex size-10 items-center justify-center rounded-xl bg-blue-50 text-blue-600"><Activity className="size-5" /></div>
						<div className="mt-3 text-xs font-semibold text-slate-800">执行后查看过程详情</div>
						<div className="mt-1 max-w-56 text-[10px] leading-4 text-slate-500">节点状态、运行输入、工具调用和最终文件会出现在这里。</div>
					</div>
				) : tab === 'timeline' ? (
					<>
						{showTimeline ? (
							<div className="rounded-xl border border-slate-100 bg-slate-50/50 p-3">
								<div className="mb-3 flex items-center justify-between gap-2"><div className="text-xs font-semibold text-slate-800">执行轨迹</div><div className="text-[10px] text-slate-400">{events.length} 条记录</div></div>
								<div className="relative space-y-0.5 before:absolute before:bottom-3 before:left-[7px] before:top-3 before:w-px before:bg-slate-200">
									{events.map((event, index) => {
										const graphNodeId = event.showGraphAction ? event.node?.node_id : undefined;
										return (
											<div
												key={event.id}
												className={'task-event-enter rounded-lg px-1 py-2 transition-colors ' + (event.node && selectedNodeId === event.node.node_id ? 'bg-blue-50/80' : 'hover:bg-white')}
												style={{ animationDelay: index * 35 + 'ms' }}
											>
												<button
													type="button"
													onClick={() => {
														if (event.node) {
															userSelectedNodeRef.current = true;
															setSelectedNodeId(event.node.node_id);
														}
													}}
													className="relative flex w-full items-start gap-2.5 text-left"
												>
													<div className={'relative z-10 mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border ' + (event.kind === 'error' ? 'border-red-200 bg-red-50 text-red-600' : event.kind === 'output' ? 'border-emerald-200 bg-emerald-50 text-emerald-600' : event.kind === 'agent' ? 'border-violet-200 bg-violet-50 text-violet-600' : 'border-blue-200 bg-blue-50 text-blue-600')}>
														{timelineIcon(event.kind)}
													</div>
													<div className="min-w-0 flex-1">
														<div className="flex items-start justify-between gap-2">
															<span className="text-[11px] font-medium leading-4 text-slate-800">{event.title}</span>
															<span className="shrink-0 text-[9px] tabular-nums text-slate-400">{formatClockTime(event.time)}</span>
														</div>
														<div className="mt-0.5 line-clamp-2 text-[10px] leading-4 text-slate-500">{event.description}</div>
													</div>
												</button>
												{graphNodeId && (
													<div className="mt-1 flex justify-end pl-[26px]">
														<button
															type="button"
															title="预览本步骤知识图谱"
															aria-label={`预览${event.node?.name ?? '本步骤'}的知识图谱`}
															onClick={() => setPreviewGraphNodeId(graphNodeId)}
															className="inline-flex h-6 items-center gap-1 rounded-md px-2 text-[10px] font-medium text-blue-700 transition-colors hover:bg-blue-50 hover:text-blue-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
														>
																<Eye className="size-3" />查看知识图谱
														</button>
													</div>
												)}
											</div>
										);
									})}
								</div>
							</div>
						) : (
							<div className="rounded-xl border border-dashed border-slate-200 px-4 py-6 text-center text-xs text-slate-500">本次运行暂未返回节点轨迹。</div>
						)}
						{selectedNodeRun && <NodeResultCard nodeRun={selectedNodeRun} run={run} onPreviewGraph={setPreviewGraphNodeId} />}
						{run.error && <div className="rounded-xl border border-red-100 bg-red-50 p-3 text-xs leading-5 text-red-700">{run.error}</div>}
						{(run.final_summary || run.final_output) && <div className="rounded-xl border border-blue-100 bg-blue-50/50 p-3"><div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-slate-800"><Sparkles className="size-3.5 text-blue-600" />最终结果</div><div className="whitespace-pre-wrap text-[11px] leading-5 text-slate-600">{run.final_summary || run.final_output}</div></div>}
						{run.artifacts.length > 0 && <section className="rounded-xl border border-slate-200 bg-white p-3"><div className="mb-2 flex items-center justify-between gap-2"><div className="text-xs font-semibold text-slate-800">任务产物（{run.artifacts.length}）</div><button type="button" onClick={() => setTab('artifacts')} className="text-[10px] font-medium text-blue-600 hover:text-blue-700">查看全部</button></div><ArtifactList run={run} busyId={artifactBusyId} onPreview={openArtifactPreview} onDownload={(artifact) => void downloadArtifact(artifact)} /></section>}
					</>
				) : tab === 'analysis' ? (
					<>
						<section className="rounded-xl border border-slate-200 bg-white p-3"><div className="flex items-center gap-2 text-xs font-semibold text-slate-800"><MessageSquareText className="size-3.5 text-blue-600" />任务理解</div><div className="mt-2 whitespace-pre-wrap text-[11px] leading-5 text-slate-600">{run.input || goal || '任务目标尚未填写。'}</div><div className="mt-2 text-[10px] text-slate-400">{taskName}</div></section>
						<section className="rounded-xl border border-slate-200 bg-white p-3"><div className="flex items-center gap-2 text-xs font-semibold text-slate-800"><GitBranch className="size-3.5 text-blue-600" />自动拆解</div><ol className="mt-2 space-y-2">{run.nodes.map((node, index) => <li key={node.id} className="flex gap-2 text-[11px] leading-4"><span className="flex size-5 shrink-0 items-center justify-center rounded-md bg-slate-100 text-[10px] font-semibold text-slate-600">{index + 1}</span><span className="min-w-0 flex-1"><span className="font-medium text-slate-800">{node.name}</span><span className="ml-1 text-slate-500">{stepTypeLabel(node.type)}</span></span></li>)}</ol></section>
						<section className="rounded-xl border border-slate-200 bg-white p-3"><div className="flex items-center gap-2 text-xs font-semibold text-slate-800"><Sparkles className="size-3.5 text-blue-600" />执行策略</div><div className="mt-2 text-[11px] leading-5 text-slate-600">按任务流程顺序运行 {run.nodes.length} 个步骤，并将上一步输出传递给后续步骤。</div>{knowledgeBaseNames.length > 0 && <div className="mt-2 text-[10px] leading-4 text-slate-500">本次知识库：{knowledgeBaseNames.join('、')}</div>}</section>
						<section className={'rounded-xl border p-3 ' + (run.status === 'failed' || run.status === 'timed_out' ? 'border-red-200 bg-red-50/60' : 'border-slate-200 bg-white')}><div className="flex items-center gap-2 text-xs font-semibold text-slate-800"><AlertCircle className={'size-3.5 ' + (run.status === 'failed' || run.status === 'timed_out' ? 'text-red-600' : 'text-slate-400')} />风险识别</div><div className="mt-2 text-[11px] leading-5 text-slate-600">{run.error || (run.node_runs.some((node) => node.status === 'failed') ? '有步骤失败，请检查步骤详情。' : '本次运行未返回失败信息。')}</div></section>
					</>
				) : tab === 'tools' ? (
					<>
						<div className="grid grid-cols-2 gap-2"><div className="rounded-xl border border-slate-200 bg-white p-3"><div className="text-[10px] text-slate-500">Agent 步骤</div><div className="mt-1 text-lg font-semibold text-slate-800">{agentNodes.length}</div></div><div className="rounded-xl border border-slate-200 bg-white p-3"><div className="text-[10px] text-slate-500">Tool 调用</div><div className="mt-1 text-lg font-semibold text-slate-800">{toolNodes.length}</div></div></div>
						{run.context.agent_id && <div className="rounded-xl border border-violet-100 bg-violet-50/50 p-3"><div className="flex items-center gap-2 text-xs font-semibold text-slate-800"><Bot className="size-4 text-violet-600" />Agent</div><div className="mt-2 text-xs font-medium text-slate-700">{run.context.agent_id}</div><div className="mt-1 text-[10px] text-slate-500">状态：{run.status === 'failed' ? '失败' : run.status === 'succeeded' ? '成功' : runStatusLabel(run.status)}</div></div>}
						{toolNodes.length > 0 ? toolNodes.map((node) => { const definition = run.nodes.find((item) => item.id === node.node_id); const toolName = definition?.config.type === 'tool' ? definition.config.tool_name : node.name; return <div key={node.node_id} className="rounded-xl border border-slate-200 bg-white p-3"><div className="flex items-center gap-2"><Wrench className="size-3.5 text-blue-600" /><span className="min-w-0 flex-1 truncate text-xs font-semibold text-slate-800">{toolName}</span><Badge variant="outline" className="text-[10px]">{nodeStatusLabel(node.status)}</Badge></div><div className="mt-2 text-[10px] text-slate-500">耗时 {formatDuration(getNodeDuration(node))}</div>{node.error && <div className="mt-2 text-[10px] leading-4 text-red-600">{node.error}</div>}</div>; }) : <div className="rounded-xl border border-dashed border-slate-200 p-4 text-center text-[11px] text-slate-500">本次运行没有 ToolStep 调用。</div>}
						{codeNodes.length > 0 && <section className="rounded-xl border border-slate-200 bg-white p-3"><div className="mb-2 text-xs font-semibold text-slate-800">CodeStep</div>{codeNodes.map((node) => <div key={node.node_id} className="flex items-center justify-between gap-2 border-t border-slate-100 py-2 text-[10px]"><span className="truncate text-slate-700">{node.name}</span><span className="shrink-0 text-slate-500">{nodeStatusLabel(node.status)} · {formatDuration(getNodeDuration(node))}</span></div>)}</section>}
						{(run.knowledge_base_ids?.length ?? 0) > 0 && <section className="rounded-xl border border-slate-200 bg-white p-3"><div className="flex items-center gap-2 text-xs font-semibold text-slate-800"><Search className="size-3.5 text-blue-600" />知识库调用</div><div className="mt-2 text-[11px] leading-5 text-slate-600">{knowledgeBaseNames.length ? knowledgeBaseNames.join('、') : run.knowledge_base_ids?.length + ' 个已关联知识库'}</div></section>}
					</>
				) : (
					<>
						{run.final_summary && <div className="rounded-xl border border-blue-100 bg-blue-50/50 p-3"><div className="text-xs font-semibold text-slate-800">任务结果</div><div className="mt-2 whitespace-pre-wrap text-[11px] leading-5 text-slate-600">{run.final_summary}</div>{run.final_output && run.final_output !== run.final_summary && <details className="mt-2"><summary className="cursor-pointer text-[10px] font-medium text-blue-700">查看完整结果</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-slate-600">{run.final_output}</pre></details>}</div>}
						<ArtifactList run={run} busyId={artifactBusyId} onPreview={openArtifactPreview} onDownload={(artifact) => void downloadArtifact(artifact)} />
					</>
				)}

				{run && (
					<section className="rounded-xl border border-slate-200 bg-white p-3">
						<div className="flex items-center justify-between gap-2"><div className="text-xs font-semibold text-slate-800">本次输入</div><span className="text-[10px] text-slate-400">Run · v{run.task_revision}</span></div>
						<div className="mt-2 whitespace-pre-wrap text-[11px] leading-5 text-slate-600">{run.input || goal || '使用任务目标作为本次执行输入。'}</div>
					</section>
				)}
			</div>

			<div className="border-t border-slate-100 p-3">
				{run && !terminal ? (
					<Button variant="outline" size="sm" className="w-full border-red-200 text-red-700 hover:bg-red-50" onClick={onCancel}><span className="mr-1.5 size-2 rounded-sm bg-red-500" />停止执行</Button>
				) : run ? (
					<Button variant="outline" size="sm" className="w-full border-slate-200" onClick={onRetry}><RotateCcw />重试本次运行</Button>
				) : (
					<div className="text-center text-[10px] text-slate-400">保存任务后即可开始执行</div>
				)}
			</div>

			<Dialog
				open={previewArtifact !== null}
				onOpenChange={(open) => {
					if (!open) {
						setPreviewArtifact(null);
						setArtifactPreviewMode('reader');
					}
				}}
			>
				{previewArtifact && (
					<DialogContent className="!w-[min(900px,calc(100vw-3rem))] !max-w-none h-[min(84vh,760px)] max-h-[calc(100vh-3rem)] flex flex-col gap-3 overflow-hidden">
						<DialogHeader>
							<DialogTitle>文件预览 · {previewArtifact.name}</DialogTitle>
							<DialogDescription>
								{artifactFormatLabel(previewArtifact)} · {artifactSizeLabel(previewArtifact.size_bytes)}。预览内容来自本次任务运行。
							</DialogDescription>
						</DialogHeader>
						{previewArtifact.format === 'markdown' && (
							<div className="flex flex-wrap items-center justify-between gap-2 border-y border-slate-100 py-2">
								<span className="text-xs text-slate-500">
									阅读视图会排版显示内容；下载仍保留原始 Markdown 文件。
								</span>
								<div className="flex items-center gap-1 rounded-lg bg-slate-100 p-1" role="group" aria-label="Markdown 预览方式">
									<Button
										type="button"
										size="sm"
										variant={artifactPreviewMode === 'reader' ? 'default' : 'ghost'}
										aria-pressed={artifactPreviewMode === 'reader'}
										onClick={() => setArtifactPreviewMode('reader')}
									>
										阅读视图
									</Button>
									<Button
										type="button"
										size="sm"
										variant={artifactPreviewMode === 'source' ? 'default' : 'ghost'}
										aria-pressed={artifactPreviewMode === 'source'}
										onClick={() => setArtifactPreviewMode('source')}
									>
										Markdown 源码
									</Button>
								</div>
							</div>
						)}
						<div className="min-h-0 flex-1 overflow-y-auto overscroll-contain rounded-xl border border-slate-200 bg-white p-5">
							{previewArtifact.format === 'markdown' && artifactPreviewMode === 'reader' ? (
								<Markdown
									animated={false}
									className="prose prose-slate prose-sm max-w-none leading-7 dark:prose-invert prose-headings:tracking-tight prose-h1:mb-4 prose-h2:mt-6 prose-h2:mb-3 prose-p:my-3 prose-li:my-1 prose-pre:overflow-x-auto prose-pre:rounded-lg prose-table:block prose-table:max-w-full prose-table:overflow-x-auto"
								>
									{previewArtifact.preview_text}
								</Markdown>
							) : (
								<pre className="whitespace-pre-wrap break-words font-mono text-sm leading-6 text-slate-700">
									{previewArtifact.preview_text}
								</pre>
							)}
						</div>
					</DialogContent>
				)}
			</Dialog>
			<Dialog open={previewGraphNodeId !== null} onOpenChange={(open) => { if (!open) setPreviewGraphNodeId(null); }}>
				{previewGraphNode && run && <DialogContent className="!w-[min(1500px,calc(100%-2rem))] !max-w-none max-h-[94vh] overflow-y-auto"><DialogHeader><DialogTitle>节点知识图谱预览 · {previewGraphNode.name}</DialogTitle><DialogDescription>查看当前步骤的实体关系、关系证据和来源片段。</DialogDescription></DialogHeader><TaskKnowledgeGraph taskId={run.task_id} selectedIds={run.knowledge_base_ids ?? []} runId={run.id} nodeId={previewGraphNode.node_id} preview disabled={previewGraphNode.status === 'pending'} /></DialogContent>}
			</Dialog>
		</aside>
	);
}
