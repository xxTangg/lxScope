import {
	Activity,
	AlertCircle,
	Bot,
	Check,
	Clock3,
	Copy,
	Download,
	Eye,
	FileText,
	FolderOpen,
	GitBranch,
	Loader2,
	MessageSquareText,
	Pause,
	Play,
	RotateCcw,
	Search,
	Shield,
	SkipForward,
	Sparkles,
	Square,
	Wrench,
} from 'lucide-react';
import * as React from 'react';
import { toast } from 'sonner';

import { getDagDescendants } from './task-dag-utils';
import { TaskInputForm } from './task-input-form';
import { TaskKnowledgeGraph } from './task-knowledge-graph';
import {
	emptyNodeSecurityReviewCounts,
	summarizeNodeSecurityReview,
	type NodeSecurityReviewStatus,
} from './task-node-security-review';
import {
	collectNodeVerificationChecks,
	getNodeAttemptViews,
	nodeExecutionStatusLabel,
} from './task-node-verification';
import type {
	NodeAttemptView,
	NodeVerificationCheck,
} from './task-node-verification';
import {
	sanitizeTaskRunContent as sanitizeRunContent,
	sanitizeTaskRunText as sanitizeRunText,
	sanitizeTaskRunValue as sanitizeRunValue,
} from './task-run-sanitization';
import { formatClockTime, formatDuration, getNodeDuration } from './task-view-model';
import { taskApi } from '@/api';
import type {
	NodeRunRecord,
	RunStatus,
	TaskArtifactRecord,
	TaskNode,
	TaskRunEvent,
	TaskRunRecord,
	TaskStepType,
} from '@/api';
import type {
	TaskDeliveryStatus,
	TaskErrorCategory,
	TaskErrorDetail,
} from '@/api/task';
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
	onDagControl: (action: 'pause' | 'continue' | 'step' | 'stop') => void;
	pendingDagCommand: string | null;
	onRetryNode: (nodeId: string) => void;
	onAnswer: (requestId: string, answers: Record<string, string>) => Promise<void>;
	fullResultRequest?: number;
	sopControlsEnabled: boolean;
	sopNodeRetryEnabled: boolean;
	requestedNodeId?: string | null;
	onNodeSelected?: (nodeId: string) => void;
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

const TASK_ERROR_CATEGORY_LABELS: Record<TaskErrorCategory, string> = {
	configuration_error: '配置错误',
	missing_tool: '缺少工具',
	permission_denied: '权限不足',
	data_source_not_ready: '数据源未就绪',
	schema_mismatch: 'Schema 不匹配',
	input_incomplete: '输入不足',
	citation_verification_failed: '引用校验失败',
	external_interface_failed: '外部接口失败',
	artifact_generation_failed: '文件生成失败',
	execution_failed: '节点执行失败',
};

function formatStructuredValue(value: unknown): string | null {
	if (value === undefined) return null;
	try {
		return JSON.stringify(sanitizeRunValue(value), null, 2) ?? null;
	} catch {
		return null;
	}
}

function TaskErrorSummary({ detail }: { detail: TaskErrorDetail }) {
	return (
		<div className="mt-2 rounded-lg border border-red-200 bg-red-50 p-2.5 text-[11px] leading-5 text-red-800">
			<div className="font-semibold">
				{TASK_ERROR_CATEGORY_LABELS[detail.category]} · {detail.code}
			</div>
			<div className="mt-0.5 whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
				{sanitizeRunText(detail.message)}
			</div>
			<div className="mt-1 text-[10px] text-red-700/80">
				TaskRun {detail.run_id}
				{detail.node_id ? ` · 节点 ${detail.node_id}` : ''}
			</div>
		</div>
	);
}

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
		awaiting_input: '等待你补充信息',
		succeeded: '已完成',
		failed: '执行失败',
		canceled: '已取消',
		timed_out: '执行超时',
	};
	return labels[status];
}

function runStatusClass(status: RunStatus): string {
	if (status === 'awaiting_input') return 'border-amber-200 bg-amber-50 text-amber-800';
	if (status === 'succeeded') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
	if (status === 'failed' || status === 'timed_out')
		return 'border-red-200 bg-red-50 text-red-700';
	if (status === 'running' || status === 'queued')
		return 'border-blue-200 bg-blue-50 text-blue-700';
	return 'border-slate-200 bg-slate-100 text-slate-600';
}

function nodeStatusLabel(status: NodeRunRecord['status']): string {
	return nodeExecutionStatusLabel(status);
}

function nodeStatusIcon(status: NodeRunRecord['status']) {
	if (status === 'awaiting_input') return <MessageSquareText className="size-4 text-amber-600" />;
	if (status === 'running') return <Loader2 className="size-4 animate-spin text-blue-600" />;
	if (status === 'succeeded') return <Check className="size-4 text-emerald-600" />;
	if (status === 'failed') return <AlertCircle className="size-4 text-red-600" />;
	return <Clock3 className="size-3.5 text-slate-400" />;
}

function stepTypeLabel(type: TaskStepType): string {
	if (type === 'input') return '用户输入';
	if (type === 'tool') return 'ToolStep';
	if (type === 'python') return 'CodeStep';
	return 'AgentStep';
}

function nodeDetails(node: TaskNode | undefined, run: TaskRunRecord) {
	if (!node) return null;
	if (node.config.type === 'input')
		return (
			<div className="text-xs text-slate-600">
				{node.config.questions.map((question) => question.label).join('、')}
			</div>
		);
	if (node.config.type === 'tool') {
		return (
			<div className="space-y-1 text-[11px] leading-5 text-slate-500">
				<div>工具：{node.config.tool_name || '未配置'}</div>
				<div className="break-all">
					参数：{JSON.stringify(sanitizeRunValue(node.config.arguments), null, 2)}
				</div>
			</div>
		);
	}
	if (node.config.type === 'python') {
		return (
			<div className="space-y-1 text-[11px] leading-5 text-slate-500">
				<div>超时：{node.config.timeout_seconds} 秒</div>
				<details>
					<summary className="cursor-pointer">查看 CodeStep 代码</summary>
					<pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap">
						{sanitizeRunText(node.config.code || '未配置代码')}
					</pre>
				</details>
			</div>
		);
	}
	return (
		<div className="space-y-1 text-[11px] leading-5 text-slate-500">
			<div>Agent：{node.config.agent_id || run.context.agent_id || '当前会话 Agent'}</div>
			<div>Session：{node.config.session_id || run.context.session_id || '当前会话'}</div>
			<details>
				<summary className="cursor-pointer">查看步骤 Prompt</summary>
				<div className="mt-2 whitespace-pre-wrap">{sanitizeRunText(node.config.prompt)}</div>
			</details>
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
					case 'input.requested':
						title = '需要补充信息：' + (node?.name ?? '当前步骤');
						description =
							typeof payload.reason === 'string' ? payload.reason : '等待你的回答';
						kind = 'node';
						break;
					case 'input.answered':
						title = '已收到回答，继续当前步骤';
						break;
					case 'run.awaiting_input':
						title = '运行已暂停，等待补充信息';
						break;
					case 'step.rejected':
						title = '步骤验收未通过，正在重做';
						description = message;
						kind = 'node';
						break;
					case 'run.created':
						title = '创建任务上下文';
						description = '载入任务版本 v' + run.task_revision + ' 与执行配置';
						break;
					case 'run.started':
						title = run.context.agent_id ? '启动 Agent' : '开始执行任务';
						description = run.context.agent_id
							? 'Agent ' + run.context.agent_id + ' 已开始执行'
							: '任务运行已开始';
						kind = run.context.agent_id ? 'agent' : 'system';
						break;
					case 'step.started':
						title = '开始步骤：' + (node?.name ?? event.node_id ?? '任务节点');
						description = stepType
							? stepTypeLabel(stepType as TaskStepType)
							: '节点开始执行';
						kind = 'node';
						break;
					case 'step.succeeded':
						title = '完成步骤：' + (node?.name ?? event.node_id ?? '任务节点');
						description =
							summary ||
							(stepType ? stepTypeLabel(stepType as TaskStepType) : '节点执行完成');
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
								definition.id === node.node_id &&
								definition.knowledge_graph_enabled,
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
		const title =
			node.status === 'succeeded'
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
			description: run.artifacts.length
				? '生成 ' + run.artifacts.length + ' 项任务产物'
				: '本次运行已完成',
			kind: 'output',
		});
	}
	return items.sort(
		(left, right) => new Date(left.time).getTime() - new Date(right.time).getTime(),
	);
}
function timelineIcon(kind: TimelineItem['kind']) {
	if (kind === 'agent') return <Bot className="size-3.5" />;
	if (kind === 'node') return <Activity className="size-3.5" />;
	if (kind === 'output') return <FolderOpen className="size-3.5" />;
	if (kind === 'error') return <AlertCircle className="size-3.5" />;
	return <FileText className="size-3.5" />;
}

const CHECK_STATUS_LABELS: Record<NodeVerificationCheck['status'], string> = {
	passed: '通过',
	failed: '未通过',
	not_performed: '未执行',
	not_applicable: '不适用',
	unknown: '无记录',
};

function checkStatusClass(status: NodeVerificationCheck['status']): string {
	if (status === 'passed') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
	if (status === 'failed') return 'border-red-200 bg-red-50 text-red-700';
	if (status === 'not_performed') return 'border-amber-200 bg-amber-50 text-amber-800';
	if (status === 'not_applicable') return 'border-slate-200 bg-slate-100 text-slate-600';
	return 'border-slate-200 bg-white text-slate-500';
}

function NodeCheckCard({ check }: { check: NodeVerificationCheck }) {
	return (
		<div className="rounded-lg border border-slate-100 bg-white p-2.5">
			<div className="flex flex-wrap items-center gap-1.5">
				<span className="text-[11px] font-semibold text-slate-800">{check.checkType}</span>
				<Badge variant="outline" className={`h-5 px-1.5 text-[9px] ${checkStatusClass(check.status)}`}>
					{CHECK_STATUS_LABELS[check.status]}
				</Badge>
				<span className="text-[9px] text-slate-400">Attempt {check.attempt}</span>
			</div>
			<div className="mt-1 break-words text-[10px] text-slate-500">来源：{sanitizeRunText(check.source)}</div>
			{check.message && <div className="mt-1 whitespace-pre-wrap break-words text-[10px] leading-4 text-slate-700">{sanitizeRunText(check.message)}</div>}
			{check.details.length > 0 && (
				<details className="mt-1">
					<summary className="cursor-pointer text-[10px] font-medium text-blue-700">展开诊断（{check.details.length}）</summary>
					<ul className="mt-1 list-disc space-y-1 pl-4 text-[10px] leading-4 text-slate-600">
						{check.details.map((detail, index) => <li key={`${check.id}:${index}`} className="break-words">{sanitizeRunText(detail)}</li>)}
					</ul>
				</details>
			)}
		</div>
	);
}

const SECURITY_REVIEW_LABELS: Record<NodeSecurityReviewStatus, string> = {
	passed: '已通过',
	failed: '未通过',
	pending: '待审查',
	confirmation: '待确认',
	not_applicable: '不适用',
	not_performed: '未执行',
	missing: '缺少记录',
};

function securityReviewClass(status: NodeSecurityReviewStatus): string {
	if (status === 'passed') return 'border-emerald-200 bg-emerald-50 text-emerald-700';
	if (status === 'failed') return 'border-red-200 bg-red-50 text-red-700';
	if (status === 'confirmation') return 'border-amber-200 bg-amber-50 text-amber-800';
	return 'border-slate-200 bg-slate-100 text-slate-600';
}

function securityShieldClass(status: NodeSecurityReviewStatus): string {
	if (status === 'passed') return 'text-emerald-600';
	if (status === 'failed') return 'text-red-600';
	if (status === 'confirmation') return 'text-amber-600';
	return 'text-slate-400';
}

function securityReviewCountText(
	counts: ReturnType<typeof emptyNodeSecurityReviewCounts>,
	unit: '项' | '个节点',
): string {
	const order: NodeSecurityReviewStatus[] = [
		'passed',
		'failed',
		'pending',
		'confirmation',
		'missing',
		'not_performed',
		'not_applicable',
	];
	const values = order
		.filter((status) => counts[status] > 0)
		.map((status) => `${counts[status]} ${unit}${SECURITY_REVIEW_LABELS[status]}`);
	return values.length ? values.join(' · ') : '暂无检查记录';
}

function NodeSecurityReviewChecks({
	checks,
}: {
	checks: ReturnType<typeof summarizeNodeSecurityReview>['checks'];
}) {
	return (
		<div className="space-y-2">
			{checks.map((check) => (
				<div key={check.id} className="rounded-md bg-slate-50 p-2">
					<div className="flex items-start gap-1.5">
						<Shield className={`mt-0.5 size-3.5 shrink-0 ${securityShieldClass(check.status)}`} />
						<div className="min-w-0 flex-1">
							<div className="flex flex-wrap items-center gap-1.5">
								<span className="text-[10px] font-medium text-slate-800">{check.label}</span>
								<Badge variant="outline" className={`h-5 px-1.5 text-[9px] ${securityReviewClass(check.status)}`}>
									{SECURITY_REVIEW_LABELS[check.status]}
								</Badge>
							</div>
							<div className="mt-0.5 text-[9px] text-slate-400">{check.source}</div>
							<div className="mt-1 whitespace-pre-wrap break-words text-[10px] leading-4 text-slate-600">{sanitizeRunText(check.message)}</div>
						</div>
					</div>
				</div>
			))}
		</div>
	);
}

function TaskSecurityReviewSection({
	run,
	onSelectNode,
}: {
	run: TaskRunRecord;
	onSelectNode: (nodeId: string) => void;
}) {
	const rows = [...run.nodes]
		.sort((left, right) => left.order - right.order)
		.map((definition) => {
			const nodeRun = run.node_runs.find((item) => item.node_id === definition.id);
			const approvalRequired = Boolean(
				run.planning_snapshot?.approval_required_node_ids?.includes(definition.id),
			);
			const review = summarizeNodeSecurityReview(nodeRun, {
				approvalRequired,
				nodeType: definition.type,
			});
			const reviewCounts = emptyNodeSecurityReviewCounts();
			for (const check of review.checks) reviewCounts[check.status] += 1;
			return {
				definition,
				nodeRun,
				review,
				reviewCounts,
			};
		});
	const nodeCounts = emptyNodeSecurityReviewCounts();
	const checkCounts = emptyNodeSecurityReviewCounts();
	for (const row of rows) {
		nodeCounts[row.review.status] += 1;
		for (const check of row.review.checks) checkCounts[check.status] += 1;
	}

	return (
		<section className="rounded-xl border border-slate-200 bg-white p-3" aria-label="安全审查">
			<div className="flex items-center gap-2 text-xs font-semibold text-slate-800">
				<Shield className="size-3.5 text-blue-600" />
				安全审查
			</div>
			<div className="mt-2 space-y-1.5 text-[9px]" aria-label="审查结果汇总">
				<div className="flex flex-wrap items-center gap-1.5">
					<span className="font-medium text-slate-500">检查项</span>
					{(['passed', 'failed', 'pending', 'confirmation', 'missing', 'not_performed', 'not_applicable'] as const)
						.filter((status) => checkCounts[status] > 0)
						.map((status) => (
							<Badge key={status} variant="outline" className={`h-5 px-1.5 text-[9px] ${securityReviewClass(status)}`}>
								{checkCounts[status]} 项{SECURITY_REVIEW_LABELS[status]}
							</Badge>
						))}
					{Object.values(checkCounts).every((count) => count === 0) && <span className="text-slate-400">暂无检查记录</span>}
				</div>
				<div className="flex flex-wrap items-center gap-1.5">
					<span className="font-medium text-slate-500">节点</span>
					{(['passed', 'failed', 'pending', 'confirmation', 'missing', 'not_performed', 'not_applicable'] as const)
						.filter((status) => nodeCounts[status] > 0)
						.map((status) => (
							<Badge key={status} variant="outline" className={`h-5 px-1.5 text-[9px] ${securityReviewClass(status)}`}>
								{nodeCounts[status]} 个节点{SECURITY_REVIEW_LABELS[status]}
							</Badge>
						))}
				</div>
			</div>
			<div className="mt-3 grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-slate-100 pb-1.5 text-[9px] font-medium text-slate-400">
				<span>节点名称</span>
				<span>安全审查</span>
			</div>
			<ol className="divide-y divide-slate-100">
				{rows.map(({ definition, nodeRun, review, reviewCounts }) => (
					<li key={definition.id}>
						<button
							type="button"
							onClick={() => onSelectNode(definition.id)}
							className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-md px-1 py-2 text-left hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
							aria-label={`查看${definition.name}安全审查详情：${SECURITY_REVIEW_LABELS[review.status]}`}
						>
							<span className="min-w-0 truncate text-[10px] font-medium text-slate-700">{definition.name}</span>
							<span className="inline-flex shrink-0 items-center gap-1">
								<Shield className={`size-3.5 ${securityShieldClass(review.status)}`} />
								<span className={`text-[9px] ${securityShieldClass(review.status)}`}>{SECURITY_REVIEW_LABELS[review.status]}</span>
							</span>
						</button>
						<div className="px-1 pb-2 text-[9px] text-slate-400">
							{review.attempt > 0 ? `当前 Attempt ${review.attempt}` : nodeRun ? '尚未产生 Attempt' : 'Run 中尚无节点状态'}
							{review.checks.length > 0 && ` · 检查项：${securityReviewCountText(reviewCounts, '项')}`}
						</div>
					</li>
				))}
			</ol>
		</section>
	);
}

function CopyDiagnosticButton({ value }: { value: string }) {
	const [copied, setCopied] = React.useState(false);
	if (!value.trim()) return null;
	return (
		<Button
			type="button"
			size="sm"
			variant="outline"
			className="h-6 px-2 text-[9px]"
			onClick={() => {
				void navigator.clipboard.writeText(sanitizeRunContent(value)).then(() => {
					setCopied(true);
					toast.success('已复制脱敏后的诊断信息');
				}).catch(() => toast.error('复制失败，请检查浏览器剪贴板权限'));
			}}
		>
			<Copy className="size-3" />
			{copied ? '已复制' : '复制诊断'}
		</Button>
	);
}

function NodeAttemptDetails({
	view,
	nodeRun,
	definition,
	run,
}: {
	view: NodeAttemptView;
	nodeRun: NodeRunRecord;
	definition: TaskNode | undefined;
	run: TaskRunRecord;
}) {
	const attemptNodeRun = { ...nodeRun, ...view };
	const checks = collectNodeVerificationChecks(view.metadata, attemptNodeRun, definition);
	const securityReview = summarizeNodeSecurityReview(
		{
			...nodeRun,
			...view,
			attempt: view.attempt,
			metadata: view.metadata,
			error_detail: view.error_detail,
		},
		{
			attemptMetadata: view.metadata,
			attemptErrorDetail: view.error_detail,
			attemptNumber: view.attempt,
			nodeType: definition?.type ?? nodeRun.type,
		},
	);
	const structuredResult = formatStructuredValue(view.result);
	const output = sanitizeRunText(view.output);
	const bindings = Object.entries(run.graph?.input_bindings[nodeRun.node_id] ?? {});
	const previousSopNode = !run.graph
		? run.nodes.find((node) => node.order === (definition?.order ?? nodeRun.order) - 1)
		: undefined;
	const failedCheckDiagnostics = checks
		.filter((check) => check.status === 'failed')
		.map((check) => [
			`${check.checkType}: ${check.status}`,
			check.source,
			check.message,
			...check.details,
		].filter(Boolean).join('\n'));
	const diagnostic = [
		view.error_detail?.code,
		view.error_detail?.message,
		view.error,
		...failedCheckDiagnostics,
	].filter((item): item is string => Boolean(item)).join('\n');
	return (
		<details open={view.isCurrent && view.status !== 'pending'} className="rounded-lg border border-slate-200 bg-slate-50/60 p-2.5">
			<summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 text-[10px]">
				<span className="font-semibold text-slate-800">Attempt {view.attempt}{view.isCurrent ? ' · 当前' : ' · 历史'}</span>
				<Badge variant="outline" className={`h-5 px-1.5 text-[9px] ${view.status === 'succeeded' ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : view.status === 'failed' ? 'border-red-200 bg-red-50 text-red-700' : 'border-slate-200 bg-white text-slate-600'}`}>
					{nodeStatusLabel(view.status)}
				</Badge>
				<span className="text-slate-500">{view.source === 'manual' ? '手动重跑' : '自动执行'}</span>
				<span className="ml-auto text-slate-500">耗时 {formatDuration(getNodeDuration(attemptNodeRun))}</span>
			</summary>
			<div className="mt-2 text-[9px] leading-4 text-slate-500">
				开始 {view.started_at ? formatClockTime(view.started_at) : '无记录'} · 结束 {view.finished_at ? formatClockTime(view.finished_at) : '无记录'}
			</div>
			{view.error_detail && <TaskErrorSummary detail={view.error_detail} />}
			{view.error && !view.error_detail && (
				<div className="mt-2 rounded-lg border border-red-100 bg-red-50 p-2 text-[10px] leading-4 text-red-700">
					<div className="whitespace-pre-wrap break-words">{sanitizeRunText(view.error)}</div>
					<CopyDiagnosticButton value={diagnostic} />
				</div>
			)}
			{view.error_detail && <div className="mt-1 flex justify-end"><CopyDiagnosticButton value={diagnostic} /></div>}
			{!view.error_detail && !view.error && diagnostic && <div className="mt-1 flex justify-end"><CopyDiagnosticButton value={diagnostic} /></div>}
			<section className="mt-3 rounded-lg border border-slate-200 bg-white p-2.5" aria-label={`Attempt ${view.attempt} 安全审查`}>
				<div className="mb-2 flex items-center gap-1.5 text-[10px] font-semibold text-slate-700">
					<Shield className="size-3.5 text-slate-500" />
					安全审查 · Attempt {view.attempt}
				</div>
				<NodeSecurityReviewChecks checks={securityReview.checks} />
			</section>
			<section className="mt-3 space-y-2" aria-label={`Attempt ${view.attempt} 验收详情`}>
				<div className="text-[10px] font-semibold text-slate-700">验收详情 · Attempt {view.attempt}</div>
				{checks.map((check) => <NodeCheckCard key={`${view.attempt}:${check.id}`} check={check} />)}
			</section>
			<details className="mt-3 rounded-lg border border-slate-200 bg-white p-2">
				<summary className="cursor-pointer text-[10px] font-semibold text-slate-700">输入与输出 · Attempt {view.attempt}</summary>
				<div className="mt-2 space-y-2">
					<div>
						<div className="mb-1 text-[9px] font-medium text-slate-500">实际输入摘要</div>
						<pre className="max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-md bg-slate-50 p-2 text-[10px] leading-4 text-slate-700">{view.input ? sanitizeRunContent(view.input) : '本次 Attempt 没有记录输入。'}</pre>
					</div>
					{definition?.output_contract && (
						<div className="text-[9px] text-slate-500">
							冻结输出契约：{definition.output_contract.contract_id}@v{definition.output_contract.contract_version} · {definition.output_contract.validation_mode}
						</div>
					)}
					{run.graph && (
						<div>
							<div className="mb-1 text-[9px] font-medium text-slate-500">DAG 命名输入绑定</div>
							{bindings.length ? (
								<ul className="space-y-1 text-[9px] text-violet-800">
									{bindings.map(([name, binding]) => (
										<li key={name}>
											{name} ← {run.nodes.find((item) => item.id === binding.source_node_id)?.name ?? binding.source_node_id}.{binding.source_value}
										</li>
									))}
								</ul>
							) : <div className="text-[9px] text-slate-500">该节点没有持久化的命名输入绑定。</div>}
						</div>
					)}
					{!run.graph && previousSopNode && <div className="text-[9px] text-slate-500">SOP 顺序来源：{previousSopNode.name}；该 Attempt 的实际输入以保存的输入摘要为准。</div>}
					<div>
						<div className="mb-1 text-[9px] font-medium text-slate-500">实际输出</div>
						{output ? (
							<pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md bg-slate-50 p-2 text-[10px] leading-4 text-slate-700">{sanitizeRunContent(view.output)}</pre>
						) : <div className="rounded-md bg-slate-50 p-2 text-[10px] text-slate-500">无有效输出</div>}
					</div>
					{structuredResult && (
						<details>
							<summary className="cursor-pointer text-[9px] font-medium text-blue-700">查看结构化结果</summary>
							<pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-md border border-slate-100 p-2 text-[9px] leading-4 text-slate-600">{structuredResult}</pre>
						</details>
					)}
				</div>
			</details>
			<details className="mt-2 rounded-lg border border-slate-200 bg-white p-2">
				<summary className="cursor-pointer text-[10px] font-semibold text-slate-700">冻结节点配置</summary>
				<div className="mt-2">{nodeDetails(definition, run)}</div>
			</details>
		</details>
	);
}

function RunAcceptanceSummary({ run }: { run: TaskRunRecord }) {
	const report = run.verification_report;
	const acceptance = report?.acceptance;
	const sourceLedger = report?.source_ledger ?? [];
	const hasReport = Boolean(report && Object.keys(report).length > 0);
	const deliveryLabel = run.delivery_status === 'draft_unverified'
		? '草稿 · 未核验'
		: run.delivery_status === 'strict_verified'
			? '严格核验交付'
			: '无记录';
	const acceptanceLabel = acceptance?.status === 'passed'
		? 'AI 语义验收通过'
		: acceptance?.status === 'failed'
			? 'AI 语义验收未通过'
			: acceptance?.status
				? String(acceptance.status)
				: '无记录';
	const ledgerLabel = report?.evidence_ledger_status === 'not_performed'
		? '未执行'
		: report?.evidence_ledger_status === 'passed'
			? '通过'
			: report?.evidence_ledger_status === 'failed' || report?.evidence_ledger_passed === false
				? '未通过'
				: report?.evidence_ledger_passed === true
					? '通过'
					: '无记录';
	return (
		<section className="rounded-xl border border-indigo-100 bg-indigo-50/40 p-3" aria-label="Run 最终验收">
			<div className="flex flex-wrap items-center gap-2">
				<div className="text-xs font-semibold text-slate-800">Run 最终验收与交付</div>
				<Badge variant="outline" className={run.delivery_status === 'draft_unverified' ? 'border-amber-200 bg-amber-50 text-[9px] text-amber-800' : 'text-[9px]'}>
					{deliveryLabel}
				</Badge>
				<span className="text-[9px] text-slate-500">Run 状态：{runStatusLabel(run.status)}</span>
			</div>
			<div className="mt-2 grid gap-2 sm:grid-cols-2">
				<div className="rounded-lg border border-white bg-white/80 p-2 text-[10px]">
					<div className="text-slate-500">AI/语义验收（Run 级记录）</div>
					<div className="mt-0.5 font-medium text-slate-800">{hasReport ? acceptanceLabel : '无记录'}</div>
					{acceptance?.message && <div className="mt-1 whitespace-pre-wrap break-words text-slate-600">{sanitizeRunText(acceptance.message)}</div>}
				</div>
				<div className="rounded-lg border border-white bg-white/80 p-2 text-[10px]">
					<div className="text-slate-500">Claim–Evidence 台账（Run 级）</div>
					<div className="mt-0.5 font-medium text-slate-800">{report ? ledgerLabel : '无记录'}</div>
					{typeof report?.semantic_support === 'string' && <div className="mt-1 text-slate-600">{sanitizeRunText(report.semantic_support)}</div>}
				</div>
			</div>
			{sourceLedger.length > 0 && (
				<details className="mt-2 rounded-lg border border-white bg-white/80 p-2 text-[9px]">
					<summary className="cursor-pointer font-medium text-slate-700">授权来源与证据标识（{sourceLedger.length}）</summary>
					<ul className="mt-1 max-h-36 space-y-1 overflow-auto text-slate-600">
						{sourceLedger.map((entry, index) => (
							<li key={`${entry.citation_id ?? entry.evidence_id ?? 'source'}:${index}`} className="break-all">
								{[
									entry.claim_id ? `Claim ${entry.claim_id}` : '',
									entry.citation_id ? `Citation ${entry.citation_id}` : '',
									entry.evidence_id ? `Evidence ${entry.evidence_id}` : '',
									entry.document_id ? `Document ${entry.document_id}` : '',
									entry.status ? `状态 ${entry.status}` : '',
								].filter(Boolean).join(' · ') || '已返回来源记录'}
							</li>
						))}
					</ul>
					<div className="mt-1 text-slate-400">仅展示已持久化标识，不展开知识库原文或引用片段。</div>
				</details>
			)}
			{(run.source_template_id || run.node_snapshot_fingerprint) && (
				<div className="mt-2 break-all text-[9px] text-slate-500">
					冻结来源：{run.source_template_id ?? '自定义 Task'}{run.source_template_version ? `@${run.source_template_version}` : ''}
					{run.node_snapshot_fingerprint ? ` · Snapshot ${run.node_snapshot_fingerprint.slice(0, 12)}` : ''}
				</div>
			)}
			{run.error_detail && <div className="mt-2"><TaskErrorSummary detail={run.error_detail} /></div>}
			{run.artifacts.length > 0 ? (
				<div className="mt-2 border-t border-indigo-100 pt-2 text-[10px]">
					<div className="font-medium text-slate-700">当前 Run 持久化产物（{run.artifacts.length}）</div>
					<ul className="mt-1 space-y-1 text-slate-600">
						{run.artifacts.map((artifact) => (
							<li key={artifact.id} className="flex flex-wrap items-center gap-1.5">
								<span>{artifact.name} · {artifact.format}</span>
								{artifact.delivery_status === 'draft_unverified' && <Badge variant="outline" className="h-4 border-amber-200 bg-amber-50 px-1 text-[8px] text-amber-800">草稿 · 未核验</Badge>}
							</li>
						))}
					</ul>
				</div>
			) : <div className="mt-2 text-[9px] text-slate-500">当前 Run 没有持久化产物记录。</div>}
			{!hasReport && <div className="mt-1 text-[9px] text-slate-500">当前 Run 未返回结构化最终验收报告；不会据此推断为通过。</div>}
		</section>
	);
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
	const toolName = nodeRun.metadata?.tool_name ?? (definition?.config.type === 'tool' ? definition.config.tool_name : null);
	const capabilityKey = nodeRun.metadata?.capability_key ?? definition?.capability_key ?? null;
	const attempts = getNodeAttemptViews(nodeRun);
	return (
		<div className="space-y-2 rounded-xl border border-slate-200 bg-white p-3">
			<div className="flex flex-wrap items-center gap-2">
				<div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-slate-50">{nodeStatusIcon(nodeRun.status)}</div>
				<div className="min-w-0 flex-1">
					<div className="truncate text-xs font-semibold text-slate-800">{nodeRun.name}</div>
					<div className="mt-0.5 text-[9px] text-slate-500">{stepTypeLabel(nodeRun.type)} · Node {nodeRun.node_id} · 当前 Attempt {nodeRun.attempt ?? 0}</div>
				</div>
				<Badge variant="outline" className={`h-5 border px-1.5 text-[10px] ${nodeRun.status === 'succeeded' ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : nodeRun.status === 'failed' ? 'border-red-200 bg-red-50 text-red-700' : 'border-blue-200 bg-blue-50 text-blue-700'}`}>
					{nodeStatusLabel(nodeRun.status)}
				</Badge>
				<span className="shrink-0 text-[10px] text-slate-500">耗时 {formatDuration(getNodeDuration(nodeRun))}</span>
			</div>
			<div className="text-[9px] text-slate-500">
				开始 {nodeRun.started_at ? formatClockTime(nodeRun.started_at) : '无记录'} · 结束 {nodeRun.finished_at ? formatClockTime(nodeRun.finished_at) : '无记录'}
			</div>
			{(toolName || capabilityKey) && <div className="break-all text-[9px] text-slate-500">{toolName ? `Tool/MCP：${String(toolName)}` : 'Task 原生能力'}{capabilityKey ? ` · 能力 ${String(capabilityKey)}` : ''}</div>}
			{definition?.knowledge_graph_enabled && (
				<div className="flex justify-end">
					<Button type="button" size="sm" variant="outline" disabled={nodeRun.status === 'pending'} onClick={() => onPreviewGraph(nodeRun.node_id)} className="h-7 text-[10px]"><Eye />预览知识图谱</Button>
				</div>
			)}
			{attempts.length ? (
				<div className="space-y-2">
					<div className="text-[10px] font-semibold text-slate-700">执行与验收历史</div>
					{attempts.map((view) => <NodeAttemptDetails key={`${nodeRun.node_id}:${view.attempt}`} view={view} nodeRun={nodeRun} definition={definition} run={run} />)}
				</div>
			) : (
				<div className="rounded-lg border border-dashed border-slate-200 p-2 text-[10px] text-slate-500">该节点尚无持久化 Attempt 记录；节点执行状态为“{nodeStatusLabel(nodeRun.status)}”。</div>
			)}
			{definition?.knowledge_graph_enabled && <div className="text-[9px] text-slate-500">知识图谱结果通过当前 Run 的授权节点接口读取。</div>}
		</div>
	);
}

function TaskNodeRetrySection({
	run,
	pendingDagCommand,
	onRetryNode,
}: {
	run: TaskRunRecord;
	pendingDagCommand: string | null;
	onRetryNode: (nodeId: string) => void;
}) {
	if ((run.engine === 'dag' && !run.graph) || run.status !== 'failed') return null;
	if (run.node_runs.some((node) => node.status === 'running' || node.status === 'awaiting_input')) return null;
	const candidates = run.node_runs.filter(
		(node) => node.status === 'failed' || node.status === 'succeeded',
	);
	if (candidates.length === 0) return null;
	const definitions = new Map(run.nodes.map((node) => [node.id, node]));

	return (
		<section className="rounded-xl border border-amber-200 bg-amber-50/60 p-3">
			<div className="flex items-center gap-2 text-xs font-semibold text-slate-800">
				<RotateCcw className="size-3.5 text-amber-700" />
				同一 Run 局部重跑
			</div>
			<div className="mt-1 text-[10px] leading-4 text-slate-600">
				重跑保留 Run ID 与既有 attempt 历史，并清除所选节点及受影响下游的当前结果。
			</div>
			<div className="mt-2 space-y-2">
				{candidates.map((nodeRun) => {
					const definition = definitions.get(nodeRun.node_id);
					const descendantIds = run.graph
						? getDagDescendants(run.graph, nodeRun.node_id)
						: run.nodes
							.filter((node) => node.order > (definition?.order ?? nodeRun.order))
							.map((node) => node.id);
					const descendants = descendantIds
						.map((id) => definitions.get(id)?.name ?? id);
					const retryCommand = `retry:${nodeRun.node_id}`;
					return (
						<div key={nodeRun.node_id} className="rounded-lg border border-white bg-white/80 p-2.5">
							<div className="flex items-start gap-2">
								<div className="min-w-0 flex-1">
									<div className="flex flex-wrap items-center gap-1.5">
										<span className="text-[11px] font-semibold text-slate-800">{nodeRun.name}</span>
										<Badge variant="outline" className={nodeRun.status === 'failed' ? 'border-red-200 bg-red-50 text-[9px] text-red-700' : 'border-emerald-200 bg-emerald-50 text-[9px] text-emerald-700'}>
											{nodeRun.status === 'failed' ? '失败' : '成功'}
										</Badge>
										{nodeRun.attempt ? <span className="text-[9px] text-slate-400">当前 attempt {nodeRun.attempt}</span> : null}
									</div>
									{nodeRun.error && <div className="mt-1 whitespace-pre-wrap break-words text-[10px] leading-4 text-red-700">{nodeRun.error}</div>}
									<div className="mt-1 text-[10px] leading-4 text-slate-500">
										受影响下游：{descendants.length ? descendants.join('、') : '无'}
									</div>
									{(nodeRun.attempt_history?.length ?? 0) > 0 && (
										<details className="mt-1">
											<summary className="cursor-pointer text-[10px] text-blue-700">
												查看 {nodeRun.attempt_history?.length} 条历史 attempt
											</summary>
											<ol className="mt-1 space-y-1 border-l border-slate-200 pl-2">
												{nodeRun.attempt_history?.map((attempt) => (
													<li key={`${attempt.attempt}:${attempt.source}:${attempt.status}`} className="text-[9px] leading-4 text-slate-600">
														Attempt {attempt.attempt} · {attempt.source === 'manual' ? '手动' : '自动'} · {attempt.status === 'succeeded' ? '成功' : attempt.status === 'failed' ? '失败' : '取消'}
														{attempt.error && <div className="text-red-700">{attempt.error}</div>}
													</li>
												))}
												</ol>
										</details>
									)}
									{definition && (definition.type === 'tool' || definition.type === 'python' || nodeRun.metadata?.recovery_required === true) && (
										<div className="mt-1 text-[9px] leading-4 text-amber-800">重跑前需核查外部或工作区副作用并在确认框中确认。</div>
									)}
								</div>
								<Button
									type="button"
									variant="outline"
									size="sm"
									className="h-7 shrink-0 px-2 text-[10px]"
									disabled={pendingDagCommand !== null}
									onClick={() => onRetryNode(nodeRun.node_id)}
								>
									{pendingDagCommand === retryCommand ? <Loader2 className="animate-spin" /> : <RotateCcw />}
									重跑节点
								</Button>
							</div>
						</div>
					);
				})}
			</div>
		</section>
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
		return (
			<div className="rounded-xl border border-dashed border-slate-200 px-4 py-8 text-center text-xs text-slate-500">
				本次运行还没有生成文件产物。
			</div>
		);
	}
	return (
		<div className="grid gap-2 sm:grid-cols-2">
			{run.artifacts.map((artifact) => (
				<div
					key={artifact.id}
					className="min-w-0 rounded-xl border border-slate-200 bg-white p-3"
				>
					<div className="flex items-start gap-2">
						<div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
							<FileText className="size-4" />
						</div>
						<div className="min-w-0 flex-1">
							<div
								className="truncate text-xs font-semibold text-slate-800"
								title={artifact.name}
							>
								{artifact.name}
							</div>
							<div className="mt-1 text-[10px] text-slate-500">
								{artifactFormatLabel(artifact)} ·{' '}
								{artifactSizeLabel(artifact.size_bytes)}
							</div>
							{artifact.delivery_status && run.source_template_id !== 's02_project_planning' && (
								<Badge
									variant="outline"
									className={
										'mt-1 text-[9px] ' +
										(artifact.delivery_status === 'draft_unverified'
											? 'border-amber-300 bg-amber-50 text-amber-800'
											: 'border-emerald-200 bg-emerald-50 text-emerald-700')
									}
								>
									{deliveryStatusLabel(artifact.delivery_status)}
								</Badge>
							)}
							<div className="mt-1 text-[10px] text-slate-400">
								{formatClockTime(artifact.created_at)}
							</div>
						</div>
					</div>
					<div className="mt-2 flex justify-end gap-1">
						{artifact.preview_text && (
							<Button
								variant="ghost"
								size="sm"
								className="h-7 px-2 text-[10px]"
								onClick={() => onPreview(artifact)}
							>
								<Eye />
								预览
							</Button>
						)}
						<Button
							variant="outline"
							size="sm"
							className="h-7 px-2 text-[10px]"
							disabled={busyId === artifact.id}
							onClick={() => onDownload(artifact)}
						>
							<Download />
							下载
						</Button>
					</div>
				</div>
			))}
		</div>
	);
}

function deliveryStatusLabel(status: TaskDeliveryStatus): string {
	return status === 'draft_unverified' ? '演示草稿 · 待核验' : '严格校验通过';
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
	onDagControl,
	pendingDagCommand,
	onRetryNode,
	onAnswer,
	fullResultRequest = 0,
	sopControlsEnabled,
	sopNodeRetryEnabled,
	requestedNodeId,
	onNodeSelected,
}: TaskRunPanelProps) {
	const [tab, setTab] = React.useState<IntelligenceTab>('timeline');
	const [selectedNodeId, setSelectedNodeId] = React.useState<string | null>(null);
	const userSelectedNodeRef = React.useRef(false);
	const [previewArtifact, setPreviewArtifact] = React.useState<TaskArtifactRecord | null>(null);
	const [artifactPreviewMode, setArtifactPreviewMode] = React.useState<'reader' | 'source'>(
		'reader',
	);
	const [artifactBusyId, setArtifactBusyId] = React.useState<string | null>(null);
	const [previewGraphNodeId, setPreviewGraphNodeId] = React.useState<string | null>(null);
	const handledFullResultRequestRef = React.useRef(fullResultRequest);

	React.useEffect(() => {
		setTab('timeline');
		setSelectedNodeId(null);
		userSelectedNodeRef.current = false;
		setPreviewArtifact(null);
		setArtifactPreviewMode('reader');
		setPreviewGraphNodeId(null);
	}, [run?.id]);

	React.useEffect(() => {
		if (fullResultRequest === handledFullResultRequestRef.current) return;
		handledFullResultRequestRef.current = fullResultRequest;
		setTab(run?.artifacts.length ? 'artifacts' : 'timeline');
	}, [fullResultRequest, run?.artifacts.length]);

	React.useEffect(() => {
		if (!run?.node_runs.length) return;
		setSelectedNodeId((current) => {
			if (
				userSelectedNodeRef.current &&
				current &&
				run.node_runs.some((item) => item.node_id === current)
			)
				return current;
			const preferred =
				run.node_runs.find(
					(item) => item.status === 'running' || item.status === 'awaiting_input',
				) ??
				run.node_runs.find((item) => item.status === 'failed') ??
				run.node_runs[run.node_runs.length - 1];
			return preferred?.node_id ?? null;
		});
	}, [run?.id, run?.node_runs]);
	React.useEffect(() => {
		if (!requestedNodeId || !run?.node_runs.some((item) => item.node_id === requestedNodeId)) return;
		userSelectedNodeRef.current = true;
		setTab('timeline');
		setSelectedNodeId(requestedNodeId);
	}, [requestedNodeId, run?.id, run?.node_runs]);
	const selectedNodeRun = run?.node_runs.find((item) => item.node_id === selectedNodeId) ?? null;
	const previewGraphNode = run?.node_runs.find((item) => item.node_id === previewGraphNodeId);
	const selectSecurityReviewNode = (nodeId: string) => {
		userSelectedNodeRef.current = true;
		setTab('timeline');
		setSelectedNodeId(nodeId);
		onNodeSelected?.(nodeId);
	};
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
	const terminal = run
		? ['succeeded', 'failed', 'canceled', 'timed_out'].includes(run.status)
		: true;
	const controlRun = run && (
		run.engine === 'dag'
			? Boolean(run.graph)
			: run.engine === 'sop' && sopControlsEnabled
	)
		? run
		: null;
	const controlMode = controlRun?.control_mode ?? 'continuous';
	const hasFailedNode = Boolean(controlRun?.node_runs.some((node) => node.status === 'failed'));
	const hasActiveNode = Boolean(
		controlRun?.node_runs.some((node) => node.status === 'running' || node.status === 'awaiting_input'),
	);
	const isPaused = Boolean(controlRun && controlMode === 'paused' && !terminal);
	const canPause = Boolean(
		controlRun &&
		!terminal &&
		(controlRun.status === 'queued' || controlRun.status === 'running') &&
		controlMode !== 'paused' &&
		controlMode !== 'pause_requested' &&
		controlMode !== 'stop_requested' &&
		controlMode !== 'stopped',
	);
	const canStop = Boolean(
		controlRun && !terminal && controlMode !== 'stop_requested' && controlMode !== 'stopped',
	);
	const modeDescription =
		controlMode === 'paused'
			? '已暂停，可继续运行或单步派发一个 ready 节点。'
			: controlMode === 'pause_requested'
				? '暂停请求已接收，将在当前在途节点到达安全检查点后生效。'
				: controlMode === 'single_step'
					? '单步执行中：本次最多派发一个 ready 节点，完成后自动暂停。'
					: controlMode === 'stop_requested' || controlMode === 'stopped'
						? 'Run 正在终止或已终止；Stop 不可恢复。'
						: '连续执行模式。';

	return (
		<aside
			className="flex min-h-0 min-w-[300px] shrink-0 flex-col overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-[0_1px_2px_rgba(16,24,40,0.04),0_4px_12px_rgba(16,24,40,0.05)]"
			style={{ width: width + 'px' }}
		>
			<div className="border-b border-slate-100 px-4 pb-3 pt-4">
				<div className="flex items-start justify-between gap-2">
					<div className="min-w-0">
						<div className="text-base font-semibold text-slate-900">执行情报</div>
						<div className="mt-1 text-[11px] text-slate-500">
							实时展示 AI 的思考与行动
						</div>
					</div>
					{run ? (
						<Badge
							variant="outline"
							className={'shrink-0 ' + runStatusClass(run.status)}
						>
							{runStatusLabel(run.status)}
						</Badge>
					) : (
						<Badge
							variant="outline"
							className="shrink-0 border-slate-200 bg-slate-50 text-slate-500"
						>
							尚未执行
						</Badge>
					)}
				</div>
				<div className="mt-3 flex items-center gap-1.5 text-[10px] text-slate-500">
					<span
						className={
							'size-1.5 rounded-full ' +
							(run?.status === 'running' || run?.status === 'queued'
								? 'bg-blue-500 task-live-dot'
								: 'bg-slate-300')
						}
					/>
					{run?.status === 'running' || run?.status === 'queued'
						? '实时更新中'
						: run
							? '执行状态已同步'
							: '启动任务后显示实时过程'}
				</div>
			</div>

			<div className="grid grid-cols-4 border-b border-slate-100 px-2">
				{TABS.map((item) => (
					<button
						key={item.id}
						type="button"
						onClick={() => setTab(item.id)}
						aria-pressed={tab === item.id}
						className={
							'relative min-w-0 truncate px-1 py-3 text-[10px] font-medium transition-colors ' +
							(tab === item.id
								? 'text-blue-700'
								: 'text-slate-500 hover:text-slate-800')
						}
					>
						{item.label}
						{tab === item.id && (
							<span className="absolute inset-x-1 bottom-0 h-0.5 rounded-full bg-blue-600" />
						)}
					</button>
				))}
			</div>

			<div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
				{run?.status === 'awaiting_input' && run.pending_input && (
					<TaskInputForm
						key={run.pending_input.id}
						request={run.pending_input}
						templateId={run.source_template_id ?? null}
						onAnswer={onAnswer}
					/>
				)}
				{!run ? (
					<div className="flex min-h-40 flex-col items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50/60 px-4 text-center">
						<div className="flex size-10 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
							<Activity className="size-5" />
						</div>
						<div className="mt-3 text-xs font-semibold text-slate-800">
							执行后查看过程详情
						</div>
						<div className="mt-1 max-w-56 text-[10px] leading-4 text-slate-500">
							节点状态、运行输入、工具调用和最终文件会出现在这里。
						</div>
					</div>
				) : tab === 'timeline' ? (
					<>
						{showTimeline ? (
							<div className="rounded-xl border border-slate-100 bg-slate-50/50 p-3">
								<div className="mb-3 flex items-center justify-between gap-2">
									<div className="text-xs font-semibold text-slate-800">
										执行轨迹
									</div>
									<div className="text-[10px] text-slate-400">
										{events.length} 条记录
									</div>
								</div>
								<div className="relative space-y-0.5 before:absolute before:bottom-3 before:left-[7px] before:top-3 before:w-px before:bg-slate-200">
									{events.map((event, index) => {
										const graphNodeId = event.showGraphAction
											? event.node?.node_id
											: undefined;
										return (
											<div
												key={event.id}
												className={
													'task-event-enter rounded-lg px-1 py-2 transition-colors ' +
													(event.node &&
													selectedNodeId === event.node.node_id
														? 'bg-blue-50/80'
														: 'hover:bg-white')
												}
												style={{ animationDelay: index * 35 + 'ms' }}
											>
												<button
													type="button"
													onClick={() => {
														if (event.node) {
															userSelectedNodeRef.current = true;
															setSelectedNodeId(event.node.node_id);
											onNodeSelected?.(event.node.node_id);
														}
													}}
													className="relative flex w-full items-start gap-2.5 text-left"
												>
													<div
														className={
															'relative z-10 mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border ' +
															(event.kind === 'error'
																? 'border-red-200 bg-red-50 text-red-600'
																: event.kind === 'output'
																	? 'border-emerald-200 bg-emerald-50 text-emerald-600'
																	: event.kind === 'agent'
																		? 'border-violet-200 bg-violet-50 text-violet-600'
																		: 'border-blue-200 bg-blue-50 text-blue-600')
														}
													>
														{timelineIcon(event.kind)}
													</div>
													<div className="min-w-0 flex-1">
														<div className="flex items-start justify-between gap-2">
															<span className="text-[11px] font-medium leading-4 text-slate-800">
																{event.title}
															</span>
															<span className="shrink-0 text-[9px] tabular-nums text-slate-400">
																{formatClockTime(event.time)}
															</span>
														</div>
														<div className="mt-0.5 line-clamp-2 text-[10px] leading-4 text-slate-500">
										{sanitizeRunText(event.description)}
														</div>
													</div>
												</button>
												{graphNodeId && (
													<div className="mt-1 flex justify-end pl-[26px]">
														<button
															type="button"
															title="预览本步骤知识图谱"
															aria-label={`预览${event.node?.name ?? '本步骤'}的知识图谱`}
															onClick={() =>
																setPreviewGraphNodeId(graphNodeId)
															}
															className="inline-flex h-6 items-center gap-1 rounded-md px-2 text-[10px] font-medium text-blue-700 transition-colors hover:bg-blue-50 hover:text-blue-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
														>
															<Eye className="size-3" />
															查看知识图谱
														</button>
													</div>
												)}
											</div>
										);
									})}
								</div>
							</div>
						) : (
							<div className="rounded-xl border border-dashed border-slate-200 px-4 py-6 text-center text-xs text-slate-500">
								本次运行暂未返回节点轨迹。
							</div>
						)}
						{selectedNodeRun && (
							<NodeResultCard
								nodeRun={selectedNodeRun}
								run={run}
								onPreviewGraph={setPreviewGraphNodeId}
							/>
						)}
										<RunAcceptanceSummary run={run} />
							{run.engine === 'dag' || (run.engine === 'sop' && sopNodeRetryEnabled) ? (
							<TaskNodeRetrySection
								run={run}
								pendingDagCommand={pendingDagCommand}
								onRetryNode={onRetryNode}
							/>
							) : null}
						{run.error_detail && <TaskErrorSummary detail={run.error_detail} />}
						{run.error && !run.error_detail && (
							<div className="rounded-xl border border-red-100 bg-red-50 p-3 text-xs leading-5 text-red-700">
								{sanitizeRunText(run.error)}
							</div>
						)}
						{(run.final_summary || run.final_output) && (
							<div className="rounded-xl border border-blue-100 bg-blue-50/50 p-3">
								<div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-slate-800">
									<Sparkles className="size-3.5 text-blue-600" />
									最终结果
								</div>
								<div className="whitespace-pre-wrap text-[11px] leading-5 text-slate-600">
									{sanitizeRunContent(run.final_summary || run.final_output)}
								</div>
							</div>
						)}
						{run.artifacts.length > 0 && (
							<section className="rounded-xl border border-slate-200 bg-white p-3">
								<div className="mb-2 flex items-center justify-between gap-2">
									<div className="text-xs font-semibold text-slate-800">
										任务产物（{run.artifacts.length}）
									</div>
									<button
										type="button"
										onClick={() => setTab('artifacts')}
										className="text-[10px] font-medium text-blue-600 hover:text-blue-700"
									>
										查看全部
									</button>
								</div>
								<ArtifactList
									run={run}
									busyId={artifactBusyId}
									onPreview={openArtifactPreview}
									onDownload={(artifact) => void downloadArtifact(artifact)}
								/>
							</section>
						)}
					</>
				) : tab === 'analysis' ? (
					<>
						<section className="rounded-xl border border-slate-200 bg-white p-3">
							<div className="flex items-center gap-2 text-xs font-semibold text-slate-800">
								<MessageSquareText className="size-3.5 text-blue-600" />
								任务理解
							</div>
							<div className="mt-2 whitespace-pre-wrap text-[11px] leading-5 text-slate-600">
								{run.input || goal || '任务目标尚未填写。'}
							</div>
							<div className="mt-2 text-[10px] text-slate-400">{taskName}</div>
						</section>
						<section className="rounded-xl border border-slate-200 bg-white p-3">
							<div className="flex items-center gap-2 text-xs font-semibold text-slate-800">
								<GitBranch className="size-3.5 text-blue-600" />
								自动拆解
							</div>
							<ol className="mt-2 space-y-2">
								{run.nodes.map((node, index) => (
									<li key={node.id} className="flex gap-2 text-[11px] leading-4">
										<span className="flex size-5 shrink-0 items-center justify-center rounded-md bg-slate-100 text-[10px] font-semibold text-slate-600">
											{index + 1}
										</span>
										<span className="min-w-0 flex-1">
											<span className="font-medium text-slate-800">
												{node.name}
											</span>
											<span className="ml-1 text-slate-500">
												{stepTypeLabel(node.type)}
											</span>
										</span>
									</li>
								))}
							</ol>
						</section>
						<section className="rounded-xl border border-slate-200 bg-white p-3">
							<div className="flex items-center gap-2 text-xs font-semibold text-slate-800">
								<Sparkles className="size-3.5 text-blue-600" />
								执行策略
							</div>
							<div className="mt-2 text-[11px] leading-5 text-slate-600">
								按任务流程顺序运行 {run.nodes.length}{' '}
								个步骤，并将上一步输出传递给后续步骤。
							</div>
							{knowledgeBaseNames.length > 0 && (
								<div className="mt-2 text-[10px] leading-4 text-slate-500">
									本次知识库：{knowledgeBaseNames.join('、')}
								</div>
							)}
						</section>
						<section
							className={
								'rounded-xl border p-3 ' +
								(run.status === 'failed' || run.status === 'timed_out'
									? 'border-red-200 bg-red-50/60'
									: 'border-slate-200 bg-white')
							}
						>
							<div className="flex items-center gap-2 text-xs font-semibold text-slate-800">
								<AlertCircle
									className={
										'size-3.5 ' +
										(run.status === 'failed' || run.status === 'timed_out'
											? 'text-red-600'
											: 'text-slate-400')
									}
								/>
								风险识别
							</div>
							<div className="mt-2 text-[11px] leading-5 text-slate-600">
								{run.error ||
									(run.node_runs.some((node) => node.status === 'failed')
										? '有步骤失败，请检查步骤详情。'
										: '本次运行未返回失败信息。')}
							</div>
						</section>
						<TaskSecurityReviewSection
							run={run}
							onSelectNode={selectSecurityReviewNode}
						/>
					</>
				) : tab === 'tools' ? (
					<>
						<div className="grid grid-cols-2 gap-2">
							<div className="rounded-xl border border-slate-200 bg-white p-3">
								<div className="text-[10px] text-slate-500">Agent 步骤</div>
								<div className="mt-1 text-lg font-semibold text-slate-800">
									{agentNodes.length}
								</div>
							</div>
							<div className="rounded-xl border border-slate-200 bg-white p-3">
								<div className="text-[10px] text-slate-500">Tool 调用</div>
								<div className="mt-1 text-lg font-semibold text-slate-800">
									{toolNodes.length}
								</div>
							</div>
						</div>
						{run.context.agent_id && (
							<div className="rounded-xl border border-violet-100 bg-violet-50/50 p-3">
								<div className="flex items-center gap-2 text-xs font-semibold text-slate-800">
									<Bot className="size-4 text-violet-600" />
									Agent
								</div>
								<div className="mt-2 text-xs font-medium text-slate-700">
									{run.context.agent_id}
								</div>
								<div className="mt-1 text-[10px] text-slate-500">
									状态：
									{run.status === 'failed'
										? '失败'
										: run.status === 'succeeded'
											? '成功'
											: runStatusLabel(run.status)}
								</div>
							</div>
						)}
						{toolNodes.length > 0 ? (
							toolNodes.map((node) => {
								const definition = run.nodes.find(
									(item) => item.id === node.node_id,
								);
								const toolName =
									definition?.config.type === 'tool'
										? definition.config.tool_name
										: node.name;
								return (
									<div
										key={node.node_id}
										className="rounded-xl border border-slate-200 bg-white p-3"
									>
										<div className="flex items-center gap-2">
											<Wrench className="size-3.5 text-blue-600" />
											<span className="min-w-0 flex-1 truncate text-xs font-semibold text-slate-800">
												{toolName}
											</span>
											<Badge variant="outline" className="text-[10px]">
												{nodeStatusLabel(node.status)}
											</Badge>
										</div>
										<div className="mt-2 text-[10px] text-slate-500">
											耗时 {formatDuration(getNodeDuration(node))}
										</div>
										{node.error && (
											<div className="mt-2 text-[10px] leading-4 text-red-600">
												{node.error}
											</div>
										)}
									</div>
								);
							})
						) : (
							<div className="rounded-xl border border-dashed border-slate-200 p-4 text-center text-[11px] text-slate-500">
								本次运行没有 ToolStep 调用。
							</div>
						)}
						{codeNodes.length > 0 && (
							<section className="rounded-xl border border-slate-200 bg-white p-3">
								<div className="mb-2 text-xs font-semibold text-slate-800">
									CodeStep
								</div>
								{codeNodes.map((node) => (
									<div
										key={node.node_id}
										className="flex items-center justify-between gap-2 border-t border-slate-100 py-2 text-[10px]"
									>
										<span className="truncate text-slate-700">{node.name}</span>
										<span className="shrink-0 text-slate-500">
											{nodeStatusLabel(node.status)} ·{' '}
											{formatDuration(getNodeDuration(node))}
										</span>
									</div>
								))}
							</section>
						)}
						{(run.knowledge_base_ids?.length ?? 0) > 0 && (
							<section className="rounded-xl border border-slate-200 bg-white p-3">
								<div className="flex items-center gap-2 text-xs font-semibold text-slate-800">
									<Search className="size-3.5 text-blue-600" />
									知识库调用
								</div>
								<div className="mt-2 text-[11px] leading-5 text-slate-600">
									{knowledgeBaseNames.length
										? knowledgeBaseNames.join('、')
										: run.knowledge_base_ids?.length + ' 个已关联知识库'}
								</div>
							</section>
						)}
					</>
				) : (
					<>
						{run.final_summary && (
							<div className="rounded-xl border border-blue-100 bg-blue-50/50 p-3">
								<div className="text-xs font-semibold text-slate-800">任务结果</div>
								<div className="mt-2 whitespace-pre-wrap text-[11px] leading-5 text-slate-600">
									{run.final_summary}
								</div>
								{run.final_output && run.final_output !== run.final_summary && (
									<details className="mt-2">
										<summary className="cursor-pointer text-[10px] font-medium text-blue-700">
											查看完整结果
										</summary>
										<pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-slate-600">
											{run.final_output}
										</pre>
									</details>
								)}
							</div>
						)}
						<ArtifactList
							run={run}
							busyId={artifactBusyId}
							onPreview={openArtifactPreview}
							onDownload={(artifact) => void downloadArtifact(artifact)}
						/>
					</>
				)}

				{run && (
					<section className="rounded-xl border border-slate-200 bg-white p-3">
						<div className="flex items-center justify-between gap-2">
							<div className="text-xs font-semibold text-slate-800">本次输入</div>
							<span className="text-[10px] text-slate-400">
								Run · v{run.task_revision}
							</span>
						</div>
						<div className="mt-2 whitespace-pre-wrap text-[11px] leading-5 text-slate-600">
							{run.input || goal || '使用任务目标作为本次执行输入。'}
						</div>
					</section>
				)}
			</div>

			<div className="border-t border-slate-100 p-3">
				{controlRun ? (
					<div className="space-y-2">
						<div className="rounded-lg bg-slate-50 px-2.5 py-2 text-[10px] leading-4 text-slate-600">
							<div className="font-medium text-slate-700">断点控制 · {controlRun.engine === 'dag' ? 'DAG' : 'SOP'} · Run v{controlRun.run_version ?? '未知'}</div>
							<div className="mt-0.5">{modeDescription}</div>
						</div>
						{typeof controlRun.run_version !== 'number' && (
							<div className="text-[10px] text-red-700">服务端没有返回 Run 版本号，控制操作已禁用。</div>
						)}
						{!terminal && (
							<div className="grid grid-cols-2 gap-2">
								{canPause && (
									<Button
										type="button"
										variant="outline"
										size="sm"
											disabled={pendingDagCommand !== null || typeof controlRun.run_version !== 'number'}
										onClick={() => onDagControl('pause')}
									>
										{pendingDagCommand === 'pause' ? <Loader2 className="animate-spin" /> : <Pause />}
										暂停
									</Button>
								)}
								{isPaused && (
									<>
										<Button
											type="button"
											variant="outline"
											size="sm"
												disabled={pendingDagCommand !== null || typeof controlRun.run_version !== 'number' || hasFailedNode}
											title={hasFailedNode ? '先重跑失败节点，再继续 Run。' : '从暂停点继续，不会重新执行仍然有效的成功节点。'}
											onClick={() => onDagControl('continue')}
										>
											{pendingDagCommand === 'continue' ? <Loader2 className="animate-spin" /> : <Play />}
											继续
										</Button>
										<Button
											type="button"
											variant="outline"
											size="sm"
											disabled={pendingDagCommand !== null || typeof controlRun.run_version !== 'number' || hasFailedNode || hasActiveNode}
											title={hasFailedNode ? '先重跑失败节点，再单步执行。' : hasActiveNode ? '等待在途节点完成后再单步执行。' : '只派发一个 ready 节点，完成后再次暂停。'}
											onClick={() => onDagControl('step')}
										>
											{pendingDagCommand === 'step' ? <Loader2 className="animate-spin" /> : <SkipForward />}
											单步
										</Button>
									</>
								)}
								{canStop && (
									<Button
										type="button"
										variant="outline"
										size="sm"
										className="border-red-200 text-red-700 hover:bg-red-50"
											disabled={pendingDagCommand !== null || typeof controlRun.run_version !== 'number'}
										onClick={() => onDagControl('stop')}
										title="Stop 会终止该 Run，不能通过 Continue 恢复。"
									>
										{pendingDagCommand === 'stop' ? <Loader2 className="animate-spin" /> : <Square />}
										终止 Run
									</Button>
								)}
							</div>
						)}
						{terminal && (
							<Button
								variant="outline"
								size="sm"
								className="w-full border-slate-200"
								onClick={onRetry}
							>
								<RotateCcw />
								重新运行整个任务
							</Button>
						)}
					</div>
				) : run && !terminal ? (
					<Button
						variant="outline"
						size="sm"
						className="w-full border-red-200 text-red-700 hover:bg-red-50"
						onClick={onCancel}
					>
						<span className="mr-1.5 size-2 rounded-sm bg-red-500" />
						停止执行
					</Button>
				) : run ? (
					<Button
						variant="outline"
						size="sm"
						className="w-full border-slate-200"
						onClick={onRetry}
					>
						<RotateCcw />
						重试本次运行
					</Button>
				) : (
					<div className="text-center text-[10px] text-slate-400">
						保存任务后即可开始执行
					</div>
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
								{artifactFormatLabel(previewArtifact)} ·{' '}
								{artifactSizeLabel(previewArtifact.size_bytes)}
								。预览内容来自本次任务运行。
							</DialogDescription>
						</DialogHeader>
						{previewArtifact.format === 'markdown' && (
							<div className="flex flex-wrap items-center justify-between gap-2 border-y border-slate-100 py-2">
								<span className="text-xs text-slate-500">
									阅读视图会排版显示内容；下载仍保留原始 Markdown 文件。
								</span>
								<div
									className="flex items-center gap-1 rounded-lg bg-slate-100 p-1"
									role="group"
									aria-label="Markdown 预览方式"
								>
									<Button
										type="button"
										size="sm"
										variant={
											artifactPreviewMode === 'reader' ? 'default' : 'ghost'
										}
										aria-pressed={artifactPreviewMode === 'reader'}
										onClick={() => setArtifactPreviewMode('reader')}
									>
										阅读视图
									</Button>
									<Button
										type="button"
										size="sm"
										variant={
											artifactPreviewMode === 'source' ? 'default' : 'ghost'
										}
										aria-pressed={artifactPreviewMode === 'source'}
										onClick={() => setArtifactPreviewMode('source')}
									>
										Markdown 源码
									</Button>
								</div>
							</div>
						)}
						<div className="min-h-0 flex-1 overflow-y-auto overscroll-contain rounded-xl border border-slate-200 bg-white p-5">
							{previewArtifact.format === 'markdown' &&
							artifactPreviewMode === 'reader' ? (
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
			<Dialog
				open={previewGraphNodeId !== null}
				onOpenChange={(open) => {
					if (!open) setPreviewGraphNodeId(null);
				}}
			>
				{previewGraphNode && run && (
					<DialogContent className="!w-[min(1500px,calc(100%-2rem))] !max-w-none max-h-[94vh] overflow-y-auto">
						<DialogHeader>
							<DialogTitle>节点知识图谱预览 · {previewGraphNode.name}</DialogTitle>
							<DialogDescription>
								查看当前步骤的实体关系、关系证据和来源片段。
							</DialogDescription>
						</DialogHeader>
						<TaskKnowledgeGraph
							taskId={run.task_id}
							selectedIds={run.knowledge_base_ids ?? []}
							runId={run.id}
							nodeId={previewGraphNode.node_id}
							preview
							disabled={previewGraphNode.status === 'pending'}
						/>
					</DialogContent>
				)}
			</Dialog>
		</aside>
	);
}
