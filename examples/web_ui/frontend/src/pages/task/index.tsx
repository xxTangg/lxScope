import { AlertCircle, Check, ChevronDown, Copy, Info, Loader2, Plus, RefreshCw, Sparkles } from 'lucide-react';
import './task-page.css';
import * as React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';

import { NewTaskDialog } from './new-task-dialog';
import { TaskDagCanvas } from './task-dag-canvas';
import { getDagDescendants, reconcileDagNodes } from './task-dag-utils';
import { TaskFlowEditor } from './task-flow-editor';
import { TaskHeader } from './task-header';
import { TaskKnowledgeGraph } from './task-knowledge-graph';
import { TaskKnowledgeSelector } from './task-knowledge-selector';
import { TaskMetrics } from './task-metrics';
import { TaskOverview } from './task-overview';
import { TaskRunPanel } from './task-run-panel';
import { TaskS01Results } from './task-s01-results';
import { TaskS02Results } from './task-s02-results';
import { TaskS03Results } from './task-s03-results';
import { TaskS04Results } from './task-s04-results';
import { TaskS05Results } from './task-s05-results';
import { TaskS06Results } from './task-s06-results';
import { TaskS07Results } from './task-s07-results';
import { TaskS08Results } from './task-s08-results';
import { TaskS09Results } from './task-s09-results';
import { TaskSidebar } from './task-sidebar';
import { TaskTemplateCreateDialog } from './task-template-create-dialog';
import { TaskTemplateGallery } from './task-template-gallery';
import type {
	TaskContext,
	TaskNode,
	TaskRecord,
	TaskRunEvent,
	TaskRunRecord,
	TaskToolSchema,
} from '@/api';
import { taskApi } from '@/api';
import { ApiError } from '@/api/client';
import type {
	TaskDeliveryMode,
	TaskDagCapabilitiesResponse,
	TaskCustomFlowCopyMode,
	TaskCapabilityCheck,
	TaskGraph,
	TaskPreflightResponse,
	TaskPlanningDecision,
	TaskRunControlRequest,
	TaskTemplateDefinition,
	TaskValidationIssue,
} from '@/api/task';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { useAgents } from '@/hooks/useAgents';
import { useSessions } from '@/hooks/useSessions';

interface TaskDraft {
	id: string;
	title: string;
	title_source: TaskRecord['title_source'];
	goal: string;
	nodes: TaskNode[];
	graph: TaskGraph | null;
	knowledge_base_ids: string[];
	generation_status: TaskRecord['generation_status'];
	generation_error?: string | null;
	source_template_id?: string | null;
	source_template_version?: string | null;
	template_step_bindings?: Record<string, string>;
	workflow_mode?: TaskRecord['workflow_mode'];
	source_task_id?: string | null;
	source_task_revision?: number | null;
	planning_decision?: TaskPlanningDecision | null;
	planning_version?: number;
	planning_goal_snapshot?: string | null;
	planning_confirmed_id?: string | null;
	planning_confirmed_version?: number | null;
}

function getDagExecutionBlockReasons(
	task: Pick<TaskDraft, 'source_template_id' | 'source_template_version'> | null | undefined,
	capabilities: TaskDagCapabilitiesResponse | null,
): string[] {
	if (!task) return [];
	if (!capabilities) {
		return ['无法读取服务端 DAG 执行能力；为避免绕过权限校验，当前不能启动 DAG Run。'];
	}
	const reasons: string[] = [];
	if (!capabilities.execution_enabled) {
		reasons.push('DAG 执行总开关关闭；图仍可编辑、保存和预检，但服务端会拒绝启动 Run。');
	}
	if (
		task.source_template_id &&
		!capabilities.scenario_execution_allowlist.some(
			(scope) =>
				scope.template_id === task.source_template_id &&
				scope.template_version === task.source_template_version,
		)
	) {
		reasons.push('该版本化场景未获当前 TaskService 的精确模板版本执行授权；服务端会拒绝启动 Run。');
	}
	return reasons;
}

const TERMINAL_RUN_STATUSES = new Set(['succeeded', 'failed', 'canceled', 'timed_out']);
function toDraft(task: TaskRecord): TaskDraft {
	const templateInputMarker = '\n\n模板输入参数（JSON）：';
	const templateInputIndex = task.goal.indexOf(templateInputMarker);
	const goal =
		task.source_template_id && templateInputIndex >= 0
			? `${task.goal.slice(0, templateInputIndex).trim()}\n\n${task.source_template_id === 's01_project_init' ? '执行时补充项目名称、背景、目标与范围。' : '执行时请按流程提示补充本场景所需信息。'}`
			: task.goal;
	return {
		id: task.id,
		title: task.title,
		title_source: task.title_source,
		goal,
		nodes: task.nodes.map((node) => ({ ...node })),
		graph: task.graph ?? null,
		knowledge_base_ids: Array.isArray(task.knowledge_base_ids)
			? [...task.knowledge_base_ids]
			: [],
		generation_status: task.generation_status,
		generation_error: task.generation_error,
		source_template_id: task.source_template_id,
		source_template_version: task.source_template_version,
		template_step_bindings: task.template_step_bindings ?? {},
		workflow_mode: task.workflow_mode,
		source_task_id: task.source_task_id,
		source_task_revision: task.source_task_revision,
		planning_decision: task.planning_decision ?? null,
		planning_version: task.planning_version ?? 0,
		planning_goal_snapshot: task.planning_goal_snapshot,
		planning_confirmed_id: task.planning_confirmed_id,
		planning_confirmed_version: task.planning_confirmed_version,
	};
}

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : '任务请求失败，请稍后重试。';
}

function normalizeNodes(nodes: TaskNode[]): TaskNode[] {
	return nodes.map((node, index) => ({ ...node, order: index }));
}

type ResizePane = 'sidebar' | 'results';

interface ResizeHandleProps {
	label: string;
	onPointerDown: (event: React.PointerEvent<HTMLDivElement>) => void;
	onNudge: (delta: number) => void;
}

function ResizeHandle({ label, onPointerDown, onNudge }: ResizeHandleProps) {
	return (
		<div
			className="group relative z-10 flex w-2 shrink-0 cursor-col-resize items-center justify-center touch-none focus-visible:outline-none"
			role="separator"
			aria-label={label}
			aria-orientation="vertical"
			tabIndex={0}
			title={`${label}，拖动或使用键盘左右方向键调整`}
			onPointerDown={(event) => {
				event.preventDefault();
				onPointerDown(event);
			}}
			onKeyDown={(event) => {
				if (event.key === 'ArrowLeft') {
					event.preventDefault();
					onNudge(-16);
				}
				if (event.key === 'ArrowRight') {
					event.preventDefault();
					onNudge(16);
				}
			}}
		>
			<div className="h-12 w-1 rounded-full bg-border transition-colors group-hover:bg-primary/60 group-focus-visible:bg-primary" />
		</div>
	);
}

function clamp(value: number, min: number, max: number): number {
	return Math.min(max, Math.max(min, value));
}

const MIN_RESULT_WIDTH = 320;
const MAX_RESULT_WIDTH = 620;

function getResultWidthLimit(): number {
	// Keep the result panel large enough for graph inspection while leaving
	// the task editor a usable area on smaller screens.
	const viewportWidth = typeof window === 'undefined' ? 1440 : window.innerWidth;
	return Math.min(MAX_RESULT_WIDTH, Math.max(360, Math.floor(viewportWidth * 0.38)));
}

const CAPABILITY_BUSINESS_NAMES: Record<string, string> = {
	'project.similar_search': '相似项目检索',
	'project.rule_check': '立项规则核对',
	'project.authorization.read': '授权项目资料读取',
	'planning.template_retrieve': '策划资料检索',
	'document.render': '文档生成与导出',
	'expert.user_data_lookup': '候选专家数据读取',
	'expert.user_conflict_check': '专家回避声明整理',
	'expert.user_rank': '专家候选排序',
	'meeting.cost_calculate': '会议费用核算',
	'meeting.material_generate': '会议物料生成',
	'meeting.material_check': '会前材料检查',
	'meeting.transcript_import': '会议转写文本导入',
	'opinion.text_import': '意见文本导入',
	'opinion.classify': '意见分类整理',
	'report.rag_retrieve': '授权报告资料检索',
	'archive.document_metadata': '归档文件信息整理',
	'archive.completeness_check': '归档完整性检查',
	'expense.invoice_import': '结构化票据信息导入',
	'expense.invoice_verify_dedupe': '票据重复核对',
	'expense.rule_check': '报销规则核对',
};

function isExplicitlyBlocked(check: TaskCapabilityCheck): boolean {
	const status = String(check.status).toLowerCase();
	return (
		['blocked', 'unsupported', 'missing', 'incompatible', 'permission_denied'].includes(status) ||
		check.tool_available === false ||
		check.interface_compatible === false ||
		check.permission_status === 'denied'
	);
}

function isCapabilityUsable(check: TaskCapabilityCheck): boolean {
	if (check.capability_key === 'document.render' || check.capability_key === 'meeting.minutes_export') {
		return (
			!isExplicitlyBlocked(check) &&
			check.status === 'available' &&
			(check.permission_status === 'allowed' || check.permission_status === 'not_applicable')
		);
	}
	return (
		!isExplicitlyBlocked(check) &&
		check.tool_available === true &&
		check.interface_compatible === true &&
		(check.permission_status === 'allowed' || check.permission_status === 'not_applicable')
	);
}

function getCapabilityFailureReason(checks: TaskCapabilityCheck[]): string {
	if (checks.some((check) => String(check.status).toLowerCase() === 'unsupported')) {
		return '当前环境不支持此工具。';
	}
	if (checks.some((check) => check.status === 'permission_denied' || check.permission_status === 'denied')) {
		return '当前用户没有调用权限。';
	}
	if (checks.some((check) => check.interface_compatible === false || check.status === 'incompatible')) {
		return '工具接口不兼容。';
	}
	if (checks.some((check) => check.tool_available === false || check.status === 'missing')) {
		return '当前会话未发现对应工具。';
	}
	if (checks.some((check) => check.permission_status !== 'allowed' && check.permission_status !== 'not_applicable')) {
		return '当前会话尚未确认调用权限。';
	}
	return '工具、接口或权限检查未通过。';
}

function getShortPreflightIssueReason(message: string): string {
	const normalized = message.toLowerCase();
	if (normalized.includes('permission') || normalized.includes('权限') || normalized.includes('授权')) {
		return '当前用户或会话缺少所需权限。';
	}
	if (normalized.includes('unsupported') || normalized.includes('不支持')) {
		return '当前环境不支持此能力。';
	}
	if (normalized.includes('tool') || normalized.includes('工具')) {
		return '当前会话未发现可用的对应工具。';
	}
	if (normalized.includes('schema') || normalized.includes('interface') || normalized.includes('接口')) {
		return '工具接口或参数与任务配置不兼容。';
	}
	return '任务配置或输入未通过运行前检查。';
}

function CapabilityPreflightPanel({
	result,
	isChecking,
	requiredCapabilities = [],
	className = 'mx-5 mb-3 xl:mx-6',
}: {
	result: TaskPreflightResponse | null;
	isChecking: boolean;
	requiredCapabilities?: string[];
	className?: string;
}) {
	const checks = result?.capability_checks ?? [];
	const groupedChecks = Array.from(
		checks.reduce((groups, check) => {
			const group = groups.get(check.capability_key) ?? [];
			group.push(check);
			groups.set(check.capability_key, group);
			return groups;
		}, new Map<string, TaskCapabilityCheck[]>()).entries(),
	).map(([capabilityKey, group]) => ({
		capabilityKey,
		checks: group,
		name: CAPABILITY_BUSINESS_NAMES[capabilityKey] ?? group[0]?.step_name ?? '业务工具',
		available: group.every(isCapabilityUsable),
	}));
	const groupsByCapability = new Map(groupedChecks.map((group) => [group.capabilityKey, group]));
	const displayedCapabilities = Array.from(
		new Set([...requiredCapabilities, ...groupedChecks.map((group) => group.capabilityKey)]),
	).map((capabilityKey) => {
		const group = groupsByCapability.get(capabilityKey);
		return {
			capabilityKey,
			name: CAPABILITY_BUSINESS_NAMES[capabilityKey] ?? group?.name ?? capabilityKey,
			checks: group?.checks ?? [],
			status: !group ? 'unreported' : group.available ? 'available' : 'unavailable',
		};
	});
	const availableGroups = groupedChecks.filter((group) => group.available);
	const unavailableGroups = groupedChecks.filter((group) => !group.available);
	const ready = result ? (typeof result.ready === 'boolean' ? result.ready : result.valid) : null;
	const notReady = Boolean(result && ready !== true);
	const hasUnavailableTools = unavailableGroups.length > 0;
	const preflightIssues = result?.issues ?? [];
	const hasShortReasons = hasUnavailableTools || (notReady && preflightIssues.length > 0);
	const statusTone = isChecking
		? 'text-slate-500'
		: ready === true
			? 'text-emerald-700'
			: hasUnavailableTools || (notReady && groupedChecks.length === 0)
			? 'text-red-700'
			: result
				? 'text-emerald-700'
				: 'text-slate-500';
	const summary = isChecking
		? '正在检查工具能力'
		: !result
			? '尚未进行工具检查'
			: ready === true
				? `工具检查通过 · ${availableGroups.length} 项可用`
			: hasUnavailableTools
				? `工具检查异常 · ${unavailableGroups.length} 项不可用`
				: notReady && groupedChecks.length === 0
					? '运行前检查未通过 · 未返回工具检查项'
					: `工具检查通过 · ${availableGroups.length} 项可用`;
	const statusIcon = isChecking ? (
		<Loader2 className="size-4 animate-spin" aria-hidden="true" />
	) : ready === true ? (
		<Check className="size-4" aria-hidden="true" />
	) : hasUnavailableTools || (notReady && groupedChecks.length === 0) ? (
		<AlertCircle className="size-4" aria-hidden="true" />
	) : result ? (
		<Check className="size-4" aria-hidden="true" />
	) : (
		<Info className="size-4" aria-hidden="true" />
	);

	return (
		<div className={`${className} min-w-0 rounded-xl border border-slate-200 bg-slate-50/60 px-3 py-2.5 text-xs`}>
			<div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
				<div className="flex shrink-0 items-center gap-2">
					<span className={`shrink-0 ${statusTone}`}>{statusIcon}</span>
					<div className={`font-semibold ${statusTone}`}>{summary}</div>
				</div>

				{!isChecking && result && displayedCapabilities.length > 0 && (
					<div className="flex min-w-0 flex-1 flex-wrap gap-1.5">
						{displayedCapabilities.map(({ capabilityKey, name, checks: capabilityChecks, status }) => {
							const available = status === 'available';
							const unavailable = status === 'unavailable';
							return (
								<span
									key={capabilityKey}
									className={`inline-flex items-center gap-1 rounded-full border bg-white px-2 py-1 text-[10px] font-medium ${
										available
											? 'border-emerald-200 text-emerald-800'
											: unavailable
												? 'border-red-200 text-red-700'
												: 'border-slate-200 text-slate-600'
									}`}
									title={unavailable ? getCapabilityFailureReason(capabilityChecks) : undefined}
								>
									{available ? (
										<Check className="size-3 text-emerald-600" aria-hidden="true" />
									) : unavailable ? (
										<AlertCircle className="size-3 text-red-600" aria-hidden="true" />
									) : (
										<Info className="size-3 text-slate-500" aria-hidden="true" />
									)}
									{name}{status === 'unreported' ? ' · 未返回结果' : ''}
								</span>
							);
						})}
					</div>
				)}
			</div>

			{!isChecking && hasShortReasons && (
				<Collapsible className="mt-1.5">
					<CollapsibleTrigger asChild>
						<button
							type="button"
							className="inline-flex items-center gap-1 rounded text-[10px] font-medium text-slate-600 outline-none hover:text-slate-900 focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
						>
							查看简短原因
							<ChevronDown className="size-3 transition-transform data-[state=open]:rotate-180" aria-hidden="true" />
						</button>
					</CollapsibleTrigger>
					<CollapsibleContent>
						<ul className="mt-1.5 space-y-1 border-t border-slate-200 pt-1.5">
							{unavailableGroups.map(({ capabilityKey, checks: group, name }) => (
								<li key={capabilityKey} className="flex flex-wrap items-baseline gap-x-2 text-[10px] leading-4">
									<span className="font-medium text-slate-700">{name}</span>
									<span className="text-slate-500">{getCapabilityFailureReason(group)}</span>
								</li>
							))}
							{notReady && preflightIssues.map((issue, index) => (
								<li key={`preflight-issue-${index}`} className="text-[10px] leading-4 text-slate-500">
									{getShortPreflightIssueReason(issue.message)}
								</li>
							))}
						</ul>
					</CollapsibleContent>
				</Collapsible>
			)}

			{!isChecking && result && notReady && groupedChecks.length > 0 && !hasUnavailableTools && (
				<p className="mt-1.5 text-[10px] leading-4 text-red-700">
					工具状态已显示；运行仍受服务端 Preflight 门禁控制。
				</p>
			)}

			{!isChecking && result && groupedChecks.length === 0 && requiredCapabilities.length === 0 && ready === true && (
				<p className="mt-1.5 text-[10px] leading-4 text-slate-500">当前任务没有需要检查的工具能力。</p>
			)}
		</div>
	);
}

interface TaskPlanningPanelProps {
	plan: TaskPlanningDecision;
	planVersion: number;
	confirmed: boolean;
	goalChanged: boolean;
	busy: boolean;
	applied: boolean;
	onApply: () => void;
	onConfirm: () => void;
	onReplan: () => void;
}

function TaskPlanningPanel({
	plan,
	planVersion,
	confirmed,
	goalChanged,
	busy,
	applied,
	onApply,
	onConfirm,
	onReplan,
}: TaskPlanningPanelProps) {
	const nodeNames = new Map(plan.nodes.map((node) => [node.id, node.name]));
	const dependencies = plan.edges.map(
		(edge) => `${nodeNames.get(edge.source_node_id) ?? edge.source_node_id} → ${nodeNames.get(edge.target_node_id) ?? edge.target_node_id}`,
	);
	const bindings = Object.entries(plan.input_bindings).flatMap(([targetId, named]) =>
		Object.entries(named).map(
			([name, binding]) =>
				`${nodeNames.get(targetId) ?? targetId}.${name} ← ${nodeNames.get(binding.source_node_id) ?? binding.source_node_id}.${binding.source_value}`,
		),
	);
	return (
		<section className="mb-4 rounded-2xl border border-violet-200 bg-violet-50/50 p-4">
			<div className="flex flex-wrap items-start justify-between gap-3">
				<div>
					<div className="flex items-center gap-2 text-sm font-semibold text-violet-950">
						<Sparkles className="size-4" /> 智能规划 · {plan.recommended_mode.toUpperCase()}
					</div>
					<div className="mt-1 text-[10px] text-violet-800">
						规划 {plan.planning_id} · 版本 {planVersion} · 校验 {plan.validation_status === 'valid' ? '通过' : plan.validation_status === 'pending' ? '待审查' : '未通过'} · 服务端执行授权 {plan.execution_allowed ? '允许候选' : '未允许'}
					</div>
				</div>
				<div className="flex flex-wrap gap-2">
					<Button type="button" size="sm" variant="outline" onClick={onReplan} disabled={busy}>
						{busy ? <Loader2 className="animate-spin" /> : <RefreshCw />} 重新规划
					</Button>
					<Button type="button" size="sm" variant="outline" onClick={onApply} disabled={busy}>
						{applied ? '已载入编辑器' : '载入编辑器'}
					</Button>
					<Button
						type="button"
						size="sm"
						onClick={onConfirm}
						disabled={busy || goalChanged || plan.validation_status === 'invalid'}
						title={goalChanged ? '任务目标在规划后已修改，请重新规划。' : undefined}
					>
						{busy ? <Loader2 className="animate-spin" /> : <Check />}
						{confirmed ? '重新确认并预检' : '确认规划并预检'}
					</Button>
				</div>
			</div>
			<div className="mt-3 grid gap-3 lg:grid-cols-2">
				<div className="rounded-xl border border-violet-100 bg-white/80 p-3">
					<div className="mb-2 text-[11px] font-semibold text-slate-800">执行节点</div>
					<ol className="space-y-1.5">
						{plan.nodes.map((node, index) => (
							<li key={node.id} className="flex gap-2 text-[10px] leading-4 text-slate-700">
								<span className="shrink-0 font-mono text-slate-400">{index + 1}.</span>
								<span><strong>{node.name}</strong><span className="ml-1 text-slate-400">{node.type} · {node.id}</span></span>
							</li>
						))}
					</ol>
				</div>
				<div className="rounded-xl border border-violet-100 bg-white/80 p-3 text-[10px] leading-4 text-slate-700">
					<div className="mb-1 font-semibold text-slate-800">依赖与输入绑定</div>
					<div><strong>依赖：</strong>{dependencies.length ? dependencies.join('；') : '无显式依赖（单节点）'}</div>
					<div className="mt-1"><strong>输入绑定：</strong>{bindings.length ? bindings.join('；') : '无命名汇合输入'}</div>
					{plan.recommended_mode === 'dag' && (
						<div className="mt-1">并发上限 {plan.max_concurrency} · 资源容量 {Object.keys(plan.resource_limits).length ? JSON.stringify(plan.resource_limits) : '未声明节点资源需求'}</div>
					)}
				</div>
			</div>
			<div className="mt-3 rounded-xl bg-white/80 px-3 py-2 text-[10px] leading-4 text-slate-700">
				<div className="font-semibold text-slate-800">决策说明</div>
				<div className="mt-1 whitespace-pre-line">{plan.reason}</div>
				{plan.validation_issues.length > 0 && (
					<ul className="mt-2 list-disc space-y-1 pl-4 text-amber-800">
						{plan.validation_issues.map((issue, index) => <li key={`${issue.path}-${index}`}>{issue.path}: {issue.message}</li>)}
					</ul>
				)}
				{plan.approval_required_node_ids.length > 0 && (
					<div className="mt-2 text-amber-800">需要现有人工确认/重跑门禁的 Tool 节点：{plan.approval_required_node_ids.join(', ')}</div>
				)}
				<div className="mt-2 text-slate-500">确认仅保存 Task 计划并执行只读 Preflight；创建 Run 仍需再次点击“执行任务”，服务端会重新校验权限、图与场景门禁。</div>
				{goalChanged && <div className="mt-1 text-red-700">任务目标已变化，请重新规划后再确认。</div>}
			</div>
		</section>
	);
}

export function TaskPage() {
	const navigate = useNavigate();
	const { taskId } = useParams<{ taskId?: string }>();
	const [tasks, setTasks] = React.useState<TaskRecord[]>([]);
	const [taskDagCapabilities, setTaskDagCapabilities] =
		React.useState<TaskDagCapabilitiesResponse | null>(null);
	const [selectedTaskId, setSelectedTaskId] = React.useState<string | null>(taskId ?? null);
	const [draft, setDraft] = React.useState<TaskDraft | null>(null);
	const [currentRun, setCurrentRun] = React.useState<TaskRunRecord | null>(null);
	const [selectedRunNodeId, setSelectedRunNodeId] = React.useState<string | null>(null);
	const [runEvents, setRunEvents] = React.useState<TaskRunEvent[]>([]);
	const [taskMainView, setTaskMainView] = React.useState<'editor' | 'business_results'>('editor');
	const [fullResultRequest, setFullResultRequest] = React.useState(0);
	const [availableTools, setAvailableTools] = React.useState<TaskToolSchema[]>([]);
	const [isLoadingTools, setIsLoadingTools] = React.useState(false);
	const [executionAgentId, setExecutionAgentId] = React.useState<string | null | undefined>(
		undefined,
	);
	const [executionSessionId, setExecutionSessionId] = React.useState<string | null | undefined>(
		undefined,
	);
	const [runInput, setRunInput] = React.useState('');
	const [deliveryMode, setDeliveryMode] = React.useState<TaskDeliveryMode>(
		'strict_verified',
	);
	const [isDirty, setIsDirty] = React.useState(false);
	const [isLoading, setIsLoading] = React.useState(true);
	const [isSaving, setIsSaving] = React.useState(false);
	const [isRunning, setIsRunning] = React.useState(false);
	const [pendingDagCommand, setPendingDagCommand] = React.useState<string | null>(null);
	const pendingDagCommandRef = React.useRef(false);
	const [isGenerating, setIsGenerating] = React.useState(false);
	const [isSyncingGeneration, setIsSyncingGeneration] = React.useState(false);
	const [isPlanning, setIsPlanning] = React.useState(false);
	const [isConfirmingPlan, setIsConfirmingPlan] = React.useState(false);
	const [planningEditorApplied, setPlanningEditorApplied] = React.useState(false);
	const [newTaskOpen, setNewTaskOpen] = React.useState(false);
	const [templateGalleryOpen, setTemplateGalleryOpen] = React.useState(false);
	const [customCopyOpen, setCustomCopyOpen] = React.useState(false);
	const [customCopyMode, setCustomCopyMode] =
		React.useState<TaskCustomFlowCopyMode>('sop');
	const [templateToCreate, setTemplateToCreate] = React.useState<TaskTemplateDefinition | null>(
		null,
	);
	const [sourceTemplate, setSourceTemplate] = React.useState<TaskTemplateDefinition | null>(null);
	const [error, setError] = React.useState<string | null>(null);
	const [capabilityPreflight, setCapabilityPreflight] =
		React.useState<TaskPreflightResponse | null>(null);
	const [graphValidationIssues, setGraphValidationIssues] = React.useState<TaskValidationIssue[]>([]);
	const [isPreflighting, setIsPreflighting] = React.useState(false);
	const [preflightResultVersion, setPreflightResultVersion] = React.useState(0);
	const preflightRequestRef = React.useRef(0);
	const clearCapabilityPreflight = React.useCallback(() => {
		preflightRequestRef.current += 1;
		setCapabilityPreflight(null);
		setIsPreflighting(false);
	}, []);
	const [sidebarWidth, setSidebarWidth] = React.useState(280);
	const [taskListCollapsed, setTaskListCollapsed] = React.useState(false);
	const [resultWidth, setResultWidth] = React.useState(380);
	const [resizingPane, setResizingPane] = React.useState<ResizePane | null>(null);
	const resizeStartRef = React.useRef<{ pane: ResizePane; x: number; width: number } | null>(
		null,
	);
	const { agents } = useAgents();
	const { sessions, loading: sessionsLoading } = useSessions(executionAgentId ?? null);
	const currentRunViewId = currentRun?.id;
	const currentRunTaskId = currentRun?.task_id;
	const currentRunSourceTemplateId = currentRun?.source_template_id;
	const currentRunStatus = currentRun?.status;
	React.useEffect(() => {
		setSelectedRunNodeId(null);
	}, [currentRun?.id]);
	React.useEffect(() => {
		if (!currentRunViewId || currentRunTaskId !== draft?.id) {
			setTaskMainView('editor');
			return;
		}
		if (
			(currentRunSourceTemplateId === 's01_project_init' || currentRunSourceTemplateId === 's02_project_planning') &&
			currentRunStatus === 'succeeded'
		) {
			setTaskMainView('business_results');
		} else {
			setTaskMainView('editor');
		}
	}, [currentRunViewId, currentRunSourceTemplateId, currentRunStatus, currentRunTaskId, draft?.id]);
	React.useEffect(() => {
		const templateId = draft?.source_template_id;
		if (!templateId) {
			setSourceTemplate(null);
			return undefined;
		}
		let disposed = false;
		setSourceTemplate(null);
		void taskApi
			.getTemplate(templateId)
			.then((template) => {
				if (!disposed && template.template_version === draft?.source_template_version) {
					setSourceTemplate(template);
				}
			})
			.catch(() => {
				if (!disposed) setSourceTemplate(null);
			});
		return () => {
			disposed = true;
		};
	}, [draft?.id, draft?.source_template_id, draft?.source_template_version]);

	const beginResize = (pane: ResizePane, event: React.PointerEvent<HTMLDivElement>) => {
		resizeStartRef.current = {
			pane,
			x: event.clientX,
			width: pane === 'sidebar' ? sidebarWidth : resultWidth,
		};
		setResizingPane(pane);
	};

	React.useEffect(() => {
		if (!resizingPane) return undefined;

		const handlePointerMove = (event: PointerEvent) => {
			const start = resizeStartRef.current;
			if (!start || start.pane !== resizingPane) return;
			const delta = event.clientX - start.x;
			if (resizingPane === 'sidebar') {
				setSidebarWidth(clamp(start.width + delta, 220, 420));
			} else {
				// The results divider sits on the results panel's left edge.
				// Moving it left makes the results panel wider.
				setResultWidth(clamp(start.width - delta, MIN_RESULT_WIDTH, getResultWidthLimit()));
			}
		};
		const finishResize = () => {
			resizeStartRef.current = null;
			setResizingPane(null);
		};

		window.addEventListener('pointermove', handlePointerMove);
		window.addEventListener('pointerup', finishResize);
		window.addEventListener('pointercancel', finishResize);
		return () => {
			window.removeEventListener('pointermove', handlePointerMove);
			window.removeEventListener('pointerup', finishResize);
			window.removeEventListener('pointercancel', finishResize);
		};
	}, [resizingPane]);

	React.useEffect(() => {
		if (executionAgentId === undefined && agents[0]) setExecutionAgentId(agents[0].id);
	}, [agents, executionAgentId]);
	React.useEffect(() => {
		if (!executionAgentId || !executionSessionId) {
			setAvailableTools([]);
			setIsLoadingTools(false);
			return undefined;
		}
		let disposed = false;
		setAvailableTools([]);
		setIsLoadingTools(true);
		const selectedSession = sessions.find((view) => view.session.id === executionSessionId);
		void taskApi
			.listTools({
				agent_id: executionAgentId,
				session_id: executionSessionId,
				...(selectedSession?.session.config.workspace_id
					? { workspace_id: selectedSession.session.config.workspace_id }
					: {}),
			})
			.then((tools) => {
				if (!disposed) setAvailableTools(tools);
			})
			.catch(() => {
				if (!disposed) setAvailableTools([]);
			})
			.finally(() => {
				if (!disposed) setIsLoadingTools(false);
			});
		return () => {
			disposed = true;
		};
	}, [executionAgentId, executionSessionId, sessions]);

	const buildExecutionContext = (): TaskContext => {
		const selectedSession = sessions.find((view) => view.session.id === executionSessionId);
		return {
			...(executionAgentId ? { agent_id: executionAgentId } : {}),
			...(executionSessionId ? { session_id: executionSessionId } : {}),
			...(selectedSession?.session.config.workspace_id
				? { workspace_id: selectedSession.session.config.workspace_id }
				: {}),
		};
	};
	React.useEffect(() => {
		if (executionSessionId !== undefined) return;
		if (executionAgentId === undefined) return;
		if (executionAgentId === null) {
			setExecutionSessionId(null);
			return;
		}
		if (sessionsLoading) return;
		setExecutionSessionId(sessions[0]?.session.id ?? null);
	}, [executionAgentId, executionSessionId, sessions, sessionsLoading]);

	const restoreRequestRef = React.useRef(0);

	const restoreLatestRun = React.useCallback(async (task: TaskRecord) => {
		const requestId = ++restoreRequestRef.current;
		setCurrentRun(null);
		setRunEvents([]);
		setIsRunning(false);
		if (!task.last_run_id) return;

		try {
			const run = await taskApi.getRun(task.last_run_id);
			if (requestId !== restoreRequestRef.current || run.task_id !== task.id) return;
			setCurrentRun(run);
			setIsRunning(!TERMINAL_RUN_STATUSES.has(run.status));
		} catch {
			// Keep the task page usable when an old run is unavailable.
		}
	}, []);

	const loadTasks = React.useCallback(async () => {
		clearCapabilityPreflight();
		setIsLoading(true);
		setError(null);
		try {
			const [records, dagCapabilities] = await Promise.all([
				taskApi.list(),
				taskApi.getCapabilities().catch(() => null),
			]);
			setTaskDagCapabilities(dagCapabilities);
			setTasks(records);
			const selected = records.find((task) => task.id === taskId) ?? records[0];
			setSelectedTaskId(selected?.id ?? null);
			setDraft(selected ? toDraft(selected) : null);
			setPlanningEditorApplied(false);
			setDeliveryMode('strict_verified');
			setExecutionAgentId(selected?.source_context.agent_id ?? undefined);
			setExecutionSessionId(selected?.source_context.session_id ?? undefined);
			if (selected) {
				void restoreLatestRun(selected);
			} else {
				restoreRequestRef.current += 1;
				setCurrentRun(null);
				setRunEvents([]);
				setIsRunning(false);
			}
			setIsDirty(false);
			setGraphValidationIssues([]);
			if (selected && selected.id !== taskId)
				navigate(`/task/${selected.id}`, { replace: true });
		} catch (requestError) {
			setError(errorMessage(requestError));
		} finally {
			setIsLoading(false);
		}
	}, [clearCapabilityPreflight, navigate, restoreLatestRun, taskId]);

	React.useEffect(() => {
		void loadTasks();
	}, [loadTasks]);

	const currentRunId = currentRun?.id ?? null;
	const isS02V4Template = Boolean(
		draft?.workflow_mode === 'template_instance' &&
			draft.source_template_id === 's02_project_planning' &&
			['4.0.0', '4.1.0'].includes(draft.source_template_version ?? ''),
	);
	const currentRunIdRef = React.useRef(currentRunId);
	React.useEffect(() => {
		currentRunIdRef.current = currentRunId;
	}, [currentRunId]);
	const shouldStreamCurrentRun = Boolean(currentRunId);

	React.useEffect(() => {
		if (!currentRunId || currentRun?.status !== 'awaiting_input') return;
		let disposed = false;
		const timer = window.setInterval(() => {
			void taskApi
				.getRun(currentRunId)
				.then((latest) => {
					if (disposed) return;
					setCurrentRun(latest);
					setIsRunning(!TERMINAL_RUN_STATUSES.has(latest.status));
					setTasks((previous) =>
						previous.map((task) =>
							task.id === latest.task_id
								? {
										...task,
										last_run_id: latest.id,
										last_run_status: latest.status,
									}
								: task,
						),
					);
				})
				.catch(() => undefined);
		}, 5_000);
		return () => {
			disposed = true;
			window.clearInterval(timer);
		};
	}, [currentRunId, currentRun?.status]);

	React.useEffect(() => {
		if (!currentRunId || !shouldStreamCurrentRun) return undefined;

		const controller = new AbortController();
		let disposed = false;

		const syncRun = (latest: TaskRunRecord) => {
			setCurrentRun(latest);
			setIsRunning(!TERMINAL_RUN_STATUSES.has(latest.status));
			setTasks((previous) =>
				previous.map((task) =>
					task.id === latest.task_id
						? {
								...task,
								last_run_id: latest.id,
								last_run_status: latest.status,
							}
						: task,
				),
			);
		};

		void (async () => {
			try {
				for await (const event of taskApi.streamRunEvents(
					currentRunId,
					controller.signal,
				)) {
					if (disposed || event.run_id !== currentRunId) return;
					setRunEvents((previous) =>
						previous.some((item) => item.id === event.id)
							? previous
							: [...previous, event],
					);
					const latest = await taskApi.getRun(currentRunId);
					if (disposed) return;
					syncRun(latest);
				}
			} catch (requestError) {
				if (!disposed && !controller.signal.aborted) {
					setError(errorMessage(requestError));
				}
			}
		})();

		return () => {
			disposed = true;
			controller.abort();
		};
	}, [currentRunId, shouldStreamCurrentRun, currentRun?.status]);

	const selectTask = (task: TaskRecord) => {
		clearCapabilityPreflight();
		setTaskMainView('editor');
		setSelectedTaskId(task.id);
		setDraft(toDraft(task));
		setPlanningEditorApplied(false);
		setDeliveryMode('strict_verified');
		setExecutionAgentId(task.source_context.agent_id ?? undefined);
		setExecutionSessionId(task.source_context.session_id ?? undefined);
		void restoreLatestRun(task);
		setRunInput('');
		setIsDirty(false);
		setError(null);
		navigate(`/task/${task.id}`);
	};
	const syncGenerationResult = async (
		taskId: string,
		showGeneratedTask: boolean,
		requestError: unknown,
	) => {
		let retryDelayMs = 1_000;
		const fallbackError =
			requestError instanceof ApiError && requestError.status === 408
				? '生成请求超时，未能确认节点生成结果。请稍后重试。'
				: errorMessage(requestError);
		while (true) {
			const latest = await taskApi
				.get(taskId, { silent: true, timeoutMs: 10_000 })
				.catch(() => null);
			if (latest) {
				setTasks((previous) =>
					previous.map((item) => (item.id === latest.id ? latest : item)),
				);
				const shouldShowGeneratedTask = showGeneratedTask || selectedTaskId === latest.id;
				if (shouldShowGeneratedTask) setDraft(toDraft(latest));

				if (latest.generation_status === 'succeeded') {
					if (shouldShowGeneratedTask) {
						setIsDirty(false);
						setError(null);
					}
					toast.success(`已生成 ${latest.nodes.length} 个任务节点`);
					return;
				}
				if (latest.generation_status === 'failed' || latest.generation_status === 'idle') {
					if (shouldShowGeneratedTask) {
						setIsDirty(false);
						setError(latest.generation_error || fallbackError);
					}
					return;
				}
			}

			await new Promise((resolve) => window.setTimeout(resolve, retryDelayMs));
			retryDelayMs = Math.min(retryDelayMs + 1_000, 5_000);
		}
	};
	const generateTask = async (
		task: TaskRecord,
		context: TaskContext,
		showGeneratedTask = false,
	) => {
		setIsGenerating(true);
		if (showGeneratedTask || selectedTaskId === task.id) {
			setDraft((previous) =>
				previous?.id === task.id
					? { ...previous, generation_status: 'generating', generation_error: null }
					: previous,
			);
		}
		try {
			const generated = await taskApi.generate(task.id, { context });
			setTasks((previous) =>
				previous.map((item) => (item.id === generated.id ? generated : item)),
			);
			if (showGeneratedTask || selectedTaskId === generated.id) {
				setDraft(toDraft(generated));
				setIsDirty(false);
			}
			toast.success(`已生成 ${generated.nodes.length} 个任务节点`);
		} catch (requestError) {
			setIsSyncingGeneration(true);
			setError(null);
			toast.info(
				requestError instanceof ApiError && requestError.status === 408
					? '生成请求超时，正在自动同步节点状态…'
					: '正在确认节点生成结果…',
			);
			await syncGenerationResult(task.id, showGeneratedTask, requestError);
		} finally {
			setIsSyncingGeneration(false);
			setIsGenerating(false);
		}
	};

	const handleCreate = async ({ title, goal }: { title: string; goal: string }) => {
		setError(null);
		const context = buildExecutionContext();
		const created = await taskApi.create({
			...(title ? { title } : {}),
			goal,
			nodes: [],
			source_context: context,
		});
		clearCapabilityPreflight();
		setTaskListCollapsed(true);
		setTaskMainView('editor');
		setTasks((previous) => [created, ...previous]);
		setSelectedTaskId(created.id);
		setDraft(toDraft(created));
		setPlanningEditorApplied(false);
		setDeliveryMode('strict_verified');
		setExecutionAgentId(created.source_context.agent_id ?? null);
		setExecutionSessionId(created.source_context.session_id ?? null);
		setCurrentRun(null);
		setRunEvents([]);
		setIsDirty(false);
		navigate(`/task/${created.id}`);
		setNewTaskOpen(false);
		void planTask(created, context, true);
		toast.success('任务已创建，正在智能规划');
	};

	const handleInstantiateTemplate = async (
		template: TaskTemplateDefinition,
		inputs: Record<string, unknown>,
		sourceContext: TaskContext,
	) => {
		setError(null);
		const created = await taskApi.instantiateTemplate(template.template_id, {
			template_version: template.template_version,
			inputs,
			source_context: sourceContext,
		});
		clearCapabilityPreflight();
		setTaskListCollapsed(true);
		setTaskMainView('editor');
		setTasks((previous) => [created, ...previous.filter((task) => task.id !== created.id)]);
		setSelectedTaskId(created.id);
		setDraft(toDraft(created));
		setPlanningEditorApplied(false);
		setDeliveryMode('strict_verified');
		setExecutionAgentId(created.source_context.agent_id ?? null);
		setExecutionSessionId(created.source_context.session_id ?? null);
		setCurrentRun(null);
		setRunEvents([]);
		setIsDirty(false);
		setError(null);
		setTemplateToCreate(null);
		navigate(`/task/${created.id}`);
		toast.success(`已从“${template.title}”创建任务`);
	};

	const handleCreateCustomFlowCopy = async (mode: TaskCustomFlowCopyMode) => {
		if (!draft || isSaving || isRunning || isGenerating) return;
		const source = isDirty
			? await saveDraft()
			: (tasks.find((task) => task.id === draft.id) ?? null);
		if (!source) return;
		setError(null);
		try {
			const created = await taskApi.createCustomFlowCopy(source.id, mode);
			clearCapabilityPreflight();
			setTaskListCollapsed(true);
			setTaskMainView('editor');
			setTasks((previous) => [created, ...previous]);
			setSelectedTaskId(created.id);
			setDraft(toDraft(created));
			setPlanningEditorApplied(false);
			setDeliveryMode('strict_verified');
			setExecutionAgentId(created.source_context.agent_id ?? null);
			setExecutionSessionId(created.source_context.session_id ?? null);
			setCurrentRun(null);
			setRunEvents([]);
			setIsDirty(false);
			setCustomCopyOpen(false);
			navigate(`/task/${created.id}`);
			toast.success(mode === 'dag' ? '已另存为自定义 DAG' : '已另存为自定义 SOP');
		} catch (requestError) {
			setError(errorMessage(requestError));
		}
	};

	const saveDraft = async (): Promise<TaskRecord | null> => {
		if (!draft) return null;
		setIsSaving(true);
		setError(null);
		try {
			const nodes = normalizeNodes(draft.nodes);
			const maySaveGraph = Boolean(
				draft.graph &&
				taskDagCapabilities?.editing_enabled &&
				(
					(draft.workflow_mode === 'custom_flow' &&
						Boolean(draft.source_template_id && draft.source_template_version && draft.source_task_id) &&
						draft.source_task_revision != null) ||
					(!draft.source_template_id &&
						draft.workflow_mode !== 'template_instance' &&
						draft.workflow_mode !== 'custom_flow')
				),
			);
			if (maySaveGraph && draft.graph) {
				const validation = await taskApi.validateDefinition({
					task_id: draft.id,
					nodes,
					graph: draft.graph,
					context: buildExecutionContext(),
				});
				setGraphValidationIssues(validation.issues ?? []);
				if (!validation.valid) {
					setError('DAG 图校验未通过；请按服务端校验结果修正依赖、输入绑定或资源容量。');
					return null;
				}
			}
			const updateBody = {
				title: draft.title.trim() || '未命名任务',
				goal: draft.goal,
				...(!draft.graph || maySaveGraph ? { nodes } : {}),
				knowledge_base_ids: draft.knowledge_base_ids,
				source_context: buildExecutionContext(),
				...(maySaveGraph && draft.graph ? { graph: draft.graph } : {}),
			};
			const saved = await taskApi.update(draft.id, updateBody);
			setTasks((previous) => previous.map((task) => (task.id === saved.id ? saved : task)));
			setDraft(toDraft(saved));
			setIsDirty(false);
			toast.success('任务已保存');
			return saved;
		} catch (requestError) {
			setGraphValidationIssues([]);
			setError(errorMessage(requestError));
			return null;
		} finally {
			setIsSaving(false);
		}
	};

	const planTask = async (
		task: TaskRecord,
		context: TaskContext,
		showPlan = false,
	) => {
		setIsPlanning(true);
		setError(null);
		clearCapabilityPreflight();
		try {
			const result = await taskApi.plan(task.id, { context });
			setTasks((previous) =>
				previous.map((item) => (item.id === result.task.id ? result.task : item)),
			);
			if (showPlan || selectedTaskId === result.task.id) {
				setDraft(toDraft(result.task));
				setIsDirty(false);
				setPlanningEditorApplied(false);
			}
			toast.success(`已生成 ${result.plan.recommended_mode.toUpperCase()} 规划候选`);
		} catch (requestError) {
			setError(errorMessage(requestError));
		} finally {
			setIsPlanning(false);
		}
	};

	const applyPlanningToEditor = () => {
		if (!draft?.planning_decision) return;
		const plan = draft.planning_decision;
		const graph: TaskGraph | null = plan.recommended_mode === 'dag'
			? {
				schema_version: 1,
				edges: plan.edges,
				input_bindings: plan.input_bindings,
				max_concurrency: plan.max_concurrency,
				resource_limits: plan.resource_limits,
			}
			: null;
		setPlanningEditorApplied(true);
		updateDraft({ nodes: normalizeNodes(plan.nodes), graph });
	};

	const confirmPlanning = async () => {
		if (!draft?.planning_decision) return;
		const plan = draft.planning_decision;
		if (draft.planning_goal_snapshot?.trim() !== draft.goal.trim()) {
			setError('任务目标在规划后已修改，请重新规划。');
			return;
		}
		setIsConfirmingPlan(true);
		setError(null);
		clearCapabilityPreflight();
		try {
			const result = await taskApi.confirmPlan(draft.id, {
				planning_id: plan.planning_id,
				plan_version: draft.planning_version ?? 0,
				...(draft.title_source === 'user' ? { title: draft.title.trim() } : {}),
				input: runInput.trim(),
				context: buildExecutionContext(),
				...(planningEditorApplied
					? { nodes: normalizeNodes(draft.nodes), graph: draft.graph }
					: {}),
			});
			setTasks((previous) =>
				previous.map((task) => (task.id === result.task.id ? result.task : task)),
			);
			setDraft(toDraft(result.task));
			setIsDirty(false);
			setPlanningEditorApplied(false);
			setCapabilityPreflight(result.preflight);
			setPreflightResultVersion((version) => version + 1);
			if (result.preflight.ready) {
				toast.success('规划已确认，Preflight 通过；请再次点击“执行任务”创建 Run');
			} else {
				toast.info('规划已保存；Preflight 未通过，请查看阻断原因后调整或重新规划');
			}
		} catch (requestError) {
			setError(errorMessage(requestError));
		} finally {
			setIsConfirmingPlan(false);
		}
	};

	const handlePlanAgain = async () => {
		if (!draft || isSaving || isRunning || isGenerating || isPlanning) return;
		const saved = isDirty
			? await saveDraft()
			: (tasks.find((task) => task.id === draft.id) ?? null);
		if (!saved) return;
		await planTask(saved, buildExecutionContext(), true);
	};

	const handleRegenerate = async () => {
		if (!draft || isSaving || isRunning || isGenerating) return;
		const saved = isDirty
			? await saveDraft()
			: (tasks.find((task) => task.id === draft.id) ?? null);
		if (!saved) return;
		setError(null);
		await generateTask(saved, buildExecutionContext());
	};

	const handleRun = async () => {
		if (!draft || isSaving || isRunning) return;
		if (draft.planning_decision) {
			const decision = draft.planning_decision;
			if (
				draft.planning_confirmed_id !== decision.planning_id ||
				draft.planning_confirmed_version !== draft.planning_version ||
				isDirty
			) {
				setError('请先确认当前智能规划；Task 内容变更后需要重新确认。');
				return;
			}
			if (decision.validation_status !== 'valid' || !decision.execution_allowed) {
				setError('当前规划尚未通过服务端确定性校验或运行授权，请先查看规划原因和 Preflight 结果。');
				return;
			}
		}
		const dagExecutionBlockReasons = draft.graph
			? getDagExecutionBlockReasons(draft, taskDagCapabilities)
			: [];
		if (dagExecutionBlockReasons.length > 0) {
			setError(dagExecutionBlockReasons.join('\n'));
			return;
		}
		const requestId = ++preflightRequestRef.current;
		setCapabilityPreflight(null);
		setIsPreflighting(false);
		if (!draft.goal.trim() || draft.nodes.length === 0) {
			setError('请先填写任务目的并配置至少一个任务步骤。');
			return;
		}
		const saved = isDirty
			? await saveDraft()
			: (tasks.find((task) => task.id === draft.id) ?? null);
		if (!saved || requestId !== preflightRequestRef.current) return;
		setError(null);
		setRunEvents([]);
		setIsRunning(true);
		setIsPreflighting(true);
		let preflightCompleted = false;
		try {
			const context = buildExecutionContext();
			const preflight = await taskApi.preflight(saved.id, {
				input: runInput.trim(),
				context,
			});
			preflightCompleted = true;
			if (requestId !== preflightRequestRef.current) return;
			setCapabilityPreflight(preflight);
			setPreflightResultVersion((version) => version + 1);
			setIsPreflighting(false);
			if (!preflight.ready) {
				setIsRunning(false);
				setError('运行前能力检查未通过；请查看能力状态和修正建议。');
				return;
			}
			const run = await taskApi.run(saved.id, {
				input: runInput.trim(),
				context,
				delivery_mode:
					saved.workflow_mode === 'template_instance' &&
					saved.source_template_id === 's02_project_planning' &&
					['5.0.0', '5.1.0'].includes(saved.source_template_version ?? '')
						? 'lightweight_draft'
						: saved.workflow_mode === 'template_instance' &&
					saved.source_template_id === 's02_project_planning' &&
					['4.0.0', '4.1.0'].includes(saved.source_template_version ?? '')
						? deliveryMode
						: 'strict_verified',
			});
			if (requestId !== preflightRequestRef.current) return;
			setCurrentRun(run);
			setTasks((previous) =>
				previous.map((task) =>
					task.id === saved.id
						? { ...task, last_run_id: run.id, last_run_status: run.status }
						: task,
				),
			);
		} catch (requestError) {
			if (requestId !== preflightRequestRef.current) return;
			setIsRunning(false);
			setError(
				preflightCompleted
					? errorMessage(requestError)
					: '运行前能力检查请求失败，请检查网络或工具配置后重试。',
			);
		} finally {
			if (requestId === preflightRequestRef.current) setIsPreflighting(false);
		}
	};

	const handleRerun = () => {
		if (!currentRun || isSaving || isRunning || isGenerating) return;
		const confirmed = window.confirm(
			'确定重新执行此任务？\n\n已有执行结果将保留为历史运行记录。',
		);
		if (!confirmed) return;
		void handleRun();
	};
	const handleCancel = async () => {
		if (!currentRun) return;
		try {
			const run = await taskApi.cancelRun(currentRun.id);
			setCurrentRun(run);
			setTasks((previous) =>
				previous.map((task) =>
					task.id === run.task_id
						? { ...task, last_run_id: run.id, last_run_status: run.status }
						: task,
				),
			);
			setIsRunning(false);
		} catch (requestError) {
			setError(errorMessage(requestError));
		}
	};

	const handleRetry = async () => {
		if (!currentRun) return;
		setRunEvents([]);
		setIsRunning(true);
		try {
			const run = await taskApi.retryRun(currentRun.id);
			setCurrentRun(run);
			setTasks((previous) =>
				previous.map((task) =>
					task.id === run.task_id
						? { ...task, last_run_id: run.id, last_run_status: run.status }
						: task,
				),
			);
		} catch (requestError) {
			setIsRunning(false);
			setError(errorMessage(requestError));
		}
	};

	const controlRequest = (
		run: TaskRunRecord,
		sideEffectsVerified = false,
	): TaskRunControlRequest | null => {
		if (typeof run.run_version !== 'number') return null;
		const requestId =
			typeof crypto !== 'undefined' && 'randomUUID' in crypto
				? crypto.randomUUID()
				: `task-ui-${Date.now()}-${Math.random().toString(16).slice(2)}`;
		return {
			request_id: requestId,
			expected_run_version: run.run_version,
			side_effects_verified: sideEffectsVerified,
		};
	};

	const handleDagControl = async (action: 'pause' | 'continue' | 'step' | 'stop') => {
		const run = currentRun;
		if (
			!run ||
			!['dag', 'sop'].includes(run.engine ?? '') ||
			(run.engine === 'dag' && !run.graph) ||
			pendingDagCommandRef.current
		) return;
		if (
			action === 'stop' &&
			!window.confirm('确定终止这个 Task Run 吗？终止后的 Run 不可继续执行。')
		) {
			return;
		}
		const body = controlRequest(run);
		if (!body) {
			setError('服务端未返回 Run 版本号，无法安全提交控制命令。请刷新 Run 状态后重试。');
			return;
		}
		pendingDagCommandRef.current = true;
		setPendingDagCommand(action);
		setError(null);
		try {
			const updated =
				action === 'pause'
					? await taskApi.pauseRun(run.id, body)
					: action === 'continue'
						? await taskApi.continueRun(run.id, body)
						: action === 'step'
							? await taskApi.stepRun(run.id, body)
							: await taskApi.stopRun(run.id, body);
			setCurrentRun(updated);
			setIsRunning(!TERMINAL_RUN_STATUSES.has(updated.status));
			setTasks((previous) =>
				previous.map((task) =>
					task.id === updated.task_id
						? { ...task, last_run_id: updated.id, last_run_status: updated.status }
						: task,
				),
			);
		} catch (requestError) {
			if (requestError instanceof ApiError && requestError.status === 409) {
				const latest = await taskApi.getRun(run.id).catch(() => null);
				if (latest && currentRunIdRef.current === latest.id) {
					setCurrentRun(latest);
					setIsRunning(!TERMINAL_RUN_STATUSES.has(latest.status));
				}
				setError('Run 状态已被其他操作更新，已尝试同步最新状态。请检查后再提交。');
			} else if (requestError instanceof ApiError && requestError.status === 422) {
				setError(`控制命令参数不合法（422）：${errorMessage(requestError)}`);
			} else {
				setError(errorMessage(requestError));
			}
		} finally {
			pendingDagCommandRef.current = false;
			setPendingDagCommand(null);
		}
	};

	const handleRetryNode = async (nodeId: string) => {
		const run = currentRun;
		if (
			!run ||
			!['dag', 'sop'].includes(run.engine ?? '') ||
			(run.engine === 'dag' && !run.graph) ||
			pendingDagCommandRef.current
		) return;
		const definition = run.nodes.find((node) => node.id === nodeId);
		const nodeRun = run.node_runs.find((item) => item.node_id === nodeId);
		if (!definition || !nodeRun) return;
		const affectedIds = run.graph
			? getDagDescendants(run.graph, nodeId)
			: run.nodes.filter((node) => node.order > definition.order).map((node) => node.id);
		const affectedNames = run.nodes
			.filter((node) => affectedIds.includes(node.id))
			.map((node) => node.name);
		const hasPossibleSideEffects =
			definition.type === 'tool' ||
			definition.type === 'python' ||
			nodeRun.metadata?.recovery_required === true;
		const impact = affectedNames.length > 0 ? affectedNames.join('、') : '无下游节点';
		const confirmation = [
			`将在原 Run ${run.id} 中重跑“${definition.name}”。`,
			affectedNames.length > 0
				? `${run.graph ? '受影响的 DAG 下游' : '后续 SOP 节点'}结果将被清除并重新执行：${impact}。`
				: '该节点没有下游节点。',
			hasPossibleSideEffects
				? '此 Tool/Python 节点可能有外部或工作区副作用。请先核查其执行状态；确认后才会向服务端提交副作用核验标记。'
				: '已有尝试记录会保留。',
		].join('\n\n');
		if (!window.confirm(confirmation)) return;
		const body = controlRequest(run, hasPossibleSideEffects);
		if (!body) {
			setError('服务端未返回 Run 版本号，无法安全提交重跑命令。请刷新 Run 状态后重试。');
			return;
		}
		pendingDagCommandRef.current = true;
		setPendingDagCommand(`retry:${nodeId}`);
		setError(null);
		try {
			const updated = await taskApi.retryNode(run.id, nodeId, body);
			setCurrentRun(updated);
			setIsRunning(!TERMINAL_RUN_STATUSES.has(updated.status));
			setTasks((previous) =>
				previous.map((task) =>
					task.id === updated.task_id
						? { ...task, last_run_id: updated.id, last_run_status: updated.status }
						: task,
				),
			);
			if (
				updated.control_mode === 'paused' &&
				!TERMINAL_RUN_STATUSES.has(updated.status)
			) {
				toast.info('节点重跑请求已受理；Run 当前处于暂停状态，请点击“继续”从检查点恢复执行。');
			}
		} catch (requestError) {
			if (requestError instanceof ApiError && requestError.status === 409) {
				const latest = await taskApi.getRun(run.id).catch(() => null);
				if (latest && currentRunIdRef.current === latest.id) {
					setCurrentRun(latest);
					setIsRunning(!TERMINAL_RUN_STATUSES.has(latest.status));
				}
				setError('Run 版本冲突或状态已变化，已尝试同步最新状态。请检查后再重试。');
			} else if (requestError instanceof ApiError && requestError.status === 422) {
				setError(`节点重跑参数不合法（422）：${errorMessage(requestError)}`);
			} else {
				setError(errorMessage(requestError));
			}
		} finally {
			pendingDagCommandRef.current = false;
			setPendingDagCommand(null);
		}
	};

	const handleAnswer = async (requestId: string, answers: Record<string, string>) => {
		if (!currentRun) return;
		let run: TaskRunRecord;
		try {
			run = await taskApi.answerRun(currentRun.id, requestId, answers);
		} catch (cause) {
			const latest = await taskApi.getRun(currentRun.id).catch(() => null);
			if (latest && currentRunIdRef.current === latest.id) {
				setCurrentRun(latest);
				setIsRunning(!TERMINAL_RUN_STATUSES.has(latest.status));
			}
			throw cause;
		}
		if (currentRunIdRef.current !== run.id) return;
		setCurrentRun(run);
		setIsRunning(!TERMINAL_RUN_STATUSES.has(run.status));
		setTasks((previous) =>
			previous.map((task) =>
				task.id === run.task_id
					? { ...task, last_run_id: run.id, last_run_status: run.status }
					: task,
			),
		);
	};

	const handleDelete = async (task: TaskRecord) => {
		if (!window.confirm(`确认删除任务“${task.title}”吗？`)) return;
		try {
			await taskApi.delete(task.id);
			const remaining = tasks.filter((item) => item.id !== task.id);
			setTasks(remaining);
			const next = remaining[0];
			if (next) {
				selectTask(next);
			} else {
				clearCapabilityPreflight();
				setSelectedTaskId(null);
				setDraft(null);
				setCurrentRun(null);
				setRunEvents([]);
				navigate('/task');
			}
			toast.success('任务已删除');
		} catch (requestError) {
			setError(errorMessage(requestError));
		}
	};

	const updateDraft = (update: Partial<TaskDraft>) => {
		setTaskMainView('editor');
		setDraft((previous) =>
			previous
				? {
					...previous,
					...update,
					...(update.title !== undefined ? { title_source: 'user' as const } : {}),
				}
				: previous,
		);
		if (draft?.planning_decision && ('nodes' in update || 'graph' in update)) {
			setPlanningEditorApplied(true);
		}
		setCapabilityPreflight(null);
		if (isPreflighting) {
			preflightRequestRef.current += 1;
			setIsPreflighting(false);
			setIsRunning(false);
		}
		setIsDirty(true);
		if ('graph' in update || 'nodes' in update) setGraphValidationIssues([]);
	};
	const preflightViewState = capabilityPreflight
		? capabilityPreflight.ready === true
			? 'passed'
			: 'failed'
		: isPreflighting
			? 'checking'
			: 'unchecked';
	const activeDagRun = currentRun && currentRun.task_id === draft?.id && currentRun.engine === 'dag'
		? currentRun
		: null;
	const currentRunIsActive = Boolean(
		currentRun && currentRun.task_id === draft?.id && !TERMINAL_RUN_STATUSES.has(currentRun.status),
	);
	const isSuccessfulS01Run = Boolean(
		currentRun &&
		currentRun.task_id === draft?.id &&
		currentRun.source_template_id === 's01_project_init' &&
		currentRun.status === 'succeeded',
	);
	const isSuccessfulS02Run = Boolean(
		currentRun &&
		currentRun.task_id === draft?.id &&
		currentRun.source_template_id === 's02_project_planning' &&
		currentRun.status === 'succeeded',
	);
	const isSuccessfulS03Run = Boolean(
		currentRun &&
		currentRun.task_id === draft?.id &&
		currentRun.source_template_id === 's03_expert_matching' &&
		currentRun.status === 'succeeded',
	);
	const isSuccessfulS04Run = Boolean(
		currentRun &&
		currentRun.task_id === draft?.id &&
		currentRun.source_template_id === 's04_meeting_preparation' &&
		currentRun.status === 'succeeded',
	);
	const isSuccessfulS05Run = Boolean(
		currentRun &&
		currentRun.task_id === draft?.id &&
		currentRun.source_template_id === 's05_meeting_minutes' &&
		currentRun.status === 'succeeded',
	);
	const isSuccessfulS06Run = Boolean(
		currentRun &&
		currentRun.task_id === draft?.id &&
		currentRun.source_template_id === 's06_opinion_processing' &&
		currentRun.status === 'succeeded',
	);
	const isSuccessfulS07Run = Boolean(
		currentRun &&
		currentRun.task_id === draft?.id &&
		currentRun.source_template_id === 's07_report_generation' &&
		currentRun.status === 'succeeded',
	);
	const isSuccessfulS08Run = Boolean(
		currentRun &&
		currentRun.task_id === draft?.id &&
		currentRun.source_template_id === 's08_archive_transfer' &&
		currentRun.status === 'succeeded',
	);
	const isSuccessfulS09Run = Boolean(
		currentRun &&
		currentRun.task_id === draft?.id &&
		currentRun.source_template_id === 's09_expense_settlement' &&
		currentRun.status === 'succeeded',
	);
	const showS01BusinessResults = isSuccessfulS01Run && taskMainView === 'business_results' && !isDirty;
	const showS02BusinessResults = isSuccessfulS02Run && taskMainView === 'business_results' && !isDirty;
	const showS03BusinessResults = isSuccessfulS03Run && taskMainView === 'business_results' && !isDirty;
	const showS04BusinessResults = isSuccessfulS04Run && taskMainView === 'business_results' && !isDirty;
	const showS05BusinessResults = isSuccessfulS05Run && taskMainView === 'business_results' && !isDirty;
	const showS06BusinessResults = isSuccessfulS06Run && taskMainView === 'business_results' && !isDirty;
	const showS07BusinessResults = isSuccessfulS07Run && taskMainView === 'business_results' && !isDirty;
	const showS08BusinessResults = isSuccessfulS08Run && taskMainView === 'business_results' && !isDirty;
	const showS09BusinessResults = isSuccessfulS09Run && taskMainView === 'business_results' && !isDirty;
	const showBusinessResults = showS01BusinessResults || showS02BusinessResults || showS03BusinessResults || showS04BusinessResults || showS05BusinessResults || showS06BusinessResults || showS07BusinessResults || showS08BusinessResults || showS09BusinessResults;
	const canEditCustomDag = Boolean(
		draft &&
		taskDagCapabilities?.editing_enabled &&
		(
			(draft.workflow_mode === 'custom_flow' &&
				Boolean(draft.source_template_id && draft.source_template_version && draft.source_task_id) &&
				draft.source_task_revision != null) ||
			(!draft.source_template_id &&
				draft.workflow_mode !== 'template_instance' &&
				draft.workflow_mode !== 'custom_flow')
		),
	);
	const canEditDag = Boolean(canEditCustomDag && !currentRunIsActive);
	const activeDagGraph = currentRunIsActive
		? activeDagRun?.graph ?? draft?.graph ?? null
		: draft?.graph ?? null;
	const activeDagCanvasNodes = currentRunIsActive ? activeDagRun?.nodes ?? draft?.nodes ?? [] : draft?.nodes ?? [];
	const activeDagCanvasRun = activeDagRun && activeDagGraph && draft?.graph &&
		JSON.stringify(activeDagRun.graph) === JSON.stringify(draft.graph)
		? activeDagRun
		: currentRunIsActive ? activeDagRun : null;
	const dagReadOnlyReason = activeDagGraph && !canEditDag
		? currentRunIsActive
			? '此 Run 使用不可变图快照；Run 结束后可编辑 Task 的后续版本。'
			: !taskDagCapabilities?.editing_enabled
				? '服务端当前未授予 DAG 图编辑能力。'
				: '当前 Task 不具备可验证的 DAG 编辑所有权或来源信息。'
		: undefined;
	const dagExecutionBlockReasons = draft?.graph
		? getDagExecutionBlockReasons(draft, taskDagCapabilities)
		: [];
	const dagExecutionBlocked = dagExecutionBlockReasons.length > 0;
	const dagExecutionBlockedReason = dagExecutionBlockReasons.join(' ');
	const planningDecision = draft?.planning_decision ?? null;
	const planningGoalChanged = Boolean(
		planningDecision &&
		draft?.planning_goal_snapshot &&
		draft.planning_goal_snapshot.trim() !== draft.goal.trim(),
	);
	const planningIsConfirmed = Boolean(
		planningDecision &&
		draft?.planning_confirmed_id === planningDecision.planning_id &&
		draft?.planning_confirmed_version === draft?.planning_version,
	);
	const plannerRunBlockedReason = !planningDecision
		? null
		: planningGoalChanged
			? '任务目标在规划后已修改，请重新规划。'
			: planningDecision.validation_status !== 'valid'
				? '规划仍待审查或未通过确定性校验，不能启动 Run。'
				: !planningDecision.execution_allowed
					? '服务端规划授权未通过；查看规划说明和 Preflight 结果。'
					: !planningIsConfirmed || isDirty
						? '请确认当前规划；内容变更后需要重新确认。'
						: null;
	const runDisabledReason = dagExecutionBlocked
		? dagExecutionBlockedReason || '无法读取服务端 DAG 执行能力，当前不能启动 DAG Run。'
		: plannerRunBlockedReason;
	const copyDagExecutionBlockReasons = getDagExecutionBlockReasons(
		draft,
		taskDagCapabilities,
	);
	const enableDagEditing = () => {
		if (!draft || !canEditCustomDag || currentRunIsActive || draft.nodes.length === 0) return;
		const resourceLimits: Record<string, number> = {};
		for (const node of draft.nodes) {
			for (const [resource, amount] of Object.entries(node.resource_requirements ?? {})) {
				resourceLimits[resource] = Math.max(resourceLimits[resource] ?? 0, amount);
			}
		}
		updateDraft({
			graph: {
				schema_version: 1,
				edges: draft.nodes.slice(0, -1).map((node, index) => ({
					source_node_id: node.id,
					target_node_id: draft.nodes[index + 1].id,
				})),
				input_bindings: {},
				max_concurrency: 1,
				resource_limits: resourceLimits,
			},
		});
	};

	if (isLoading) {
		return (
			<div className="flex size-full items-center justify-center p-2">
				<div className="flex items-center gap-2 text-sm text-muted-foreground">
					<Loader2 className="size-4 animate-spin" />
					加载任务中…
				</div>
			</div>
		);
	}

	return (
		<div
			className={`task-center-page flex size-full min-w-0 gap-2 p-2 ${resizingPane ? 'select-none' : ''}`}
		>
			<TaskSidebar
				tasks={tasks}
				selectedTaskId={selectedTaskId}
				run={currentRun}
				width={sidebarWidth}
				collapsed={taskListCollapsed}
				onSelect={selectTask}
				onCreate={() => setTemplateGalleryOpen(true)}
				onDelete={(task) => void handleDelete(task)}
				onToggleCollapsed={() => setTaskListCollapsed((collapsed) => !collapsed)}
				disabled={isSaving || isRunning || isGenerating}
			/>
			<ResizeHandle
				label="调整我的任务宽度"
				onPointerDown={(event) => beginResize('sidebar', event)}
				onNudge={(delta) => setSidebarWidth((value) => clamp(value + delta, 220, 420))}
			/>

			<main className="flex min-h-0 min-w-0 flex-1 gap-2 overflow-hidden">
				<section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-[0_1px_2px_rgba(16,24,40,0.04),0_4px_12px_rgba(16,24,40,0.05)]">
					{draft ? (
						<>
							<TaskHeader
								title={draft.title}
								goal={draft.goal}
								generationStatus={draft.generation_status}
								isDirty={isDirty}
								isSaving={isSaving}
								isRunning={isRunning}
								isAwaitingInput={currentRun?.status === 'awaiting_input'}
								isGenerating={isGenerating}
								canRerun={Boolean(currentRun && currentRun.task_id === draft.id && !dagExecutionBlocked && !plannerRunBlockedReason)}
								runDisabledReason={runDisabledReason}
								deliveryMode={deliveryMode}
								showDeliveryMode={isS02V4Template}
								onDeliveryModeChange={setDeliveryMode}
								onTitleChange={(title) => updateDraft({ title })}
								onSave={() => void saveDraft()}
								onRerun={handleRerun}
								onRun={() => void handleRun()}
							/>
			{!showBusinessResults && (
				<div className="mx-5 mb-3 grid min-w-0 gap-2 xl:mx-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
					<div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border border-blue-100 bg-blue-50/70 px-3 py-2 text-[11px] leading-5 text-blue-900">
						<div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-4 gap-y-1">
							<div className="shrink-0 font-semibold">
								流程类型：{' '}
									{draft.workflow_mode === 'custom_flow'
										? draft.graph ? '自定义 DAG' : '自定义 SOP'
									: draft.source_template_id
										? '标准模板任务'
										: '自由 AI Planner 任务'}
							</div>
							<div className="min-w-0 flex-1 truncate">
								{draft.source_template_id ? (
									sourceTemplate ? (
										<>
											来源模板：<strong>{sourceTemplate.title}</strong> v{sourceTemplate.template_version}。
											{draft.workflow_mode === 'custom_flow' ? (
												<> 来源 Task：{draft.source_task_id ?? '未知'}（修订 {draft.source_task_revision ?? '未知'}）。来源场景必需 Tool 能力与执行前 Preflight 仍生效；此自定义流程不代表审批通过。</>
											) : (
												<> 标准模板的节点结构、必需步骤与顺序受服务端约束。</>
											)}
										</>
									) : (
										<>来源模板 {draft.source_template_id} v{draft.source_template_version ?? '未知'} 无法加载或版本不匹配，流程编辑保持锁定。</>
									)
								) : (
									<>由自由任务 AI Planner 生成节点；后续可按现有编辑能力调整。</>
								)}
							</div>
						</div>
						{draft.source_template_id &&
							draft.workflow_mode !== 'custom_flow' && (
								<Button
									className="shrink-0"
									variant="outline"
									size="sm"
															onClick={() => {
																setCustomCopyMode('sop');
																setCustomCopyOpen(true);
															}}
									disabled={
										!sourceTemplate ||
										isSaving ||
										isRunning ||
										isGenerating
									}
									title={
									!sourceTemplate
										? '来源模板加载完成后才能创建自定义流程副本。'
															: '选择另存为 SOP 或 DAG 自定义流程。'
									}
								>
									<Copy />
														另存为
								</Button>
							)}
					</div>
					{draft && (
						<CapabilityPreflightPanel
							key={`${draft.id}:${preflightResultVersion}:${preflightViewState}`}
							result={capabilityPreflight}
							isChecking={isPreflighting}
							requiredCapabilities={sourceTemplate?.required_capabilities ?? []}
							className="mx-0 mb-0 h-full"
						/>
					)}
				</div>
			)}
							{error && (
								<div className="mx-5 mb-3 flex items-start gap-2 rounded-xl border border-red-100 bg-red-50 px-3 py-2.5 text-xs text-red-700 xl:mx-6">
									<AlertCircle className="mt-0.5 size-4 shrink-0" />
									<span>{error}</span>
								</div>
							)}
							{showBusinessResults && currentRun ? (
								<div className="min-h-0 flex-1 overflow-y-auto border-t border-slate-100 px-4 py-4 xl:px-5">
									{showS01BusinessResults ? (
										<TaskS01Results
											run={currentRun}
											onViewExecution={() => setTaskMainView('editor')}
											onViewFullResult={() => setFullResultRequest((request) => request + 1)}
										/>
									) : showS02BusinessResults ? (
										<TaskS02Results
											run={currentRun}
											onViewExecution={() => setTaskMainView('editor')}
											onViewFullResult={() => setFullResultRequest((request) => request + 1)}
										/>
									) : showS04BusinessResults ? (
										<TaskS04Results
											run={currentRun}
											onViewExecution={() => setTaskMainView('editor')}
											onViewFullResult={() => setFullResultRequest((request) => request + 1)}
										/>
					) : showS05BusinessResults ? (
						<TaskS05Results
							run={currentRun}
							onViewExecution={() => setTaskMainView('editor')}
							onViewFullResult={() => setFullResultRequest((request) => request + 1)}
						/>
					) : showS06BusinessResults ? (
						<TaskS06Results
							run={currentRun}
							onViewExecution={() => setTaskMainView('editor')}
							onViewFullResult={() => setFullResultRequest((request) => request + 1)}
						/>
									) : showS07BusinessResults ? (
										<TaskS07Results
											run={currentRun}
											onViewExecution={() => setTaskMainView('editor')}
											onViewFullResult={() => setFullResultRequest((request) => request + 1)}
										/>
									) : showS08BusinessResults ? (
										<TaskS08Results
											run={currentRun}
											onViewExecution={() => setTaskMainView('editor')}
											onViewFullResult={() => setFullResultRequest((request) => request + 1)}
										/>
									) : showS09BusinessResults ? (
										<TaskS09Results
											run={currentRun}
											onViewExecution={() => setTaskMainView('editor')}
											onViewFullResult={() => setFullResultRequest((request) => request + 1)}
										/>
									) : (
										<TaskS03Results
											run={currentRun}
											onViewExecution={() => setTaskMainView('editor')}
											onViewFullResult={() => setFullResultRequest((request) => request + 1)}
										/>
									)}
								</div>
							) : (
								<>
									<div className="min-h-0 flex-1 overflow-hidden border-t border-slate-100">
								<section className="h-full min-w-0 overflow-y-auto px-4 py-4 xl:px-5">
									<TaskMetrics task={draft} run={currentRun} isDirty={isDirty} />
									<TaskOverview
										nodes={draft.nodes}
										run={currentRun}
										selectedRunNodeId={selectedRunNodeId}
										onRunNodeSelect={setSelectedRunNodeId}
										generationStatus={draft.generation_status}
										isDirty={isDirty}
							onViewBusinessResults={
			(isSuccessfulS01Run || isSuccessfulS02Run || isSuccessfulS03Run || isSuccessfulS04Run || isSuccessfulS05Run || isSuccessfulS06Run || isSuccessfulS07Run || isSuccessfulS08Run || isSuccessfulS09Run) && !isDirty
												? () => setTaskMainView('business_results')
												: undefined
										}
									/>
									{draft.planning_decision && (
										<TaskPlanningPanel
											plan={draft.planning_decision}
											planVersion={draft.planning_version ?? 0}
											confirmed={
												draft.planning_confirmed_id === draft.planning_decision.planning_id &&
												draft.planning_confirmed_version === draft.planning_version
											}
											goalChanged={
												Boolean(draft.planning_goal_snapshot) &&
												draft.planning_goal_snapshot?.trim() !== draft.goal.trim()
											}
											busy={isPlanning || isConfirmingPlan || isSaving || isRunning || isGenerating}
											applied={planningEditorApplied}
											onApply={applyPlanningToEditor}
											onConfirm={() => void confirmPlanning()}
											onReplan={() => void handlePlanAgain()}
										/>
									)}
									{draft.graph == null && canEditCustomDag && !currentRunIsActive && (
										<section className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-indigo-100 bg-indigo-50/60 px-4 py-3">
											<div>
												<div className="text-xs font-semibold text-indigo-950">自定义 DAG 编排</div>
												<div className="mt-1 text-[10px] leading-4 text-indigo-800">
													显式设置节点依赖、并行度和命名输入。转换只保存 Task 定义，不更改既有 Run 快照。
												</div>
											</div>
											<Button type="button" size="sm" variant="outline" onClick={enableDagEditing} disabled={draft.nodes.length === 0 || isSaving || isGenerating}>
												启用 DAG 图编辑
											</Button>
										</section>
									)}
									{activeDagGraph && (
										<TaskDagCanvas
											graph={activeDagGraph}
											nodes={activeDagCanvasNodes}
											run={activeDagCanvasRun}
										selectedNodeId={activeDagCanvasRun?.id === currentRun?.id ? selectedRunNodeId : null}
										onNodeSelect={activeDagCanvasRun ? setSelectedRunNodeId : undefined}
											editable={canEditDag && Boolean(draft.graph)}
											executionEnabled={taskDagCapabilities?.execution_enabled ?? null}
											validationIssues={graphValidationIssues}
											readOnlyReason={dagReadOnlyReason}
																executionBlockedReason={dagExecutionBlockedReason}
											onGraphChange={(graph) => updateDraft({ graph })}
																onNodesChange={(nodes) => updateDraft({ nodes })}
										/>
									)}

									<div className="mb-4 grid gap-3 md:grid-cols-2">
										<section className="min-w-0 rounded-2xl border border-slate-200 bg-white p-4">
											<div className="mb-2 flex h-9 items-center justify-between gap-3">
												<div className="text-xs font-semibold text-slate-800">
													任务目标
												</div>
								<div className="flex items-center gap-1">
									<Button
										variant="ghost"
										size="sm"
										onClick={() => void handlePlanAgain()}
										disabled={isSaving || isRunning || isGenerating || isPlanning || !draft.goal.trim()}
										className="h-7 px-2 text-[10px] text-violet-700"
									>
										{isPlanning ? <Loader2 className="animate-spin" /> : <Sparkles />}
										{isPlanning ? '规划中…' : draft.planning_decision ? '重新规划' : '智能规划'}
									</Button>
									{!draft.source_template_id && (
													<Button
														variant="ghost"
														size="sm"
														onClick={() => void handleRegenerate()}
														disabled={
															isSaving ||
															isRunning ||
															isGenerating ||
															!draft.goal.trim()
														}
														className="h-7 px-2 text-[10px] text-blue-700"
													>
														{isGenerating ? (
															<Loader2 className="animate-spin" />
														) : (
															<RefreshCw />
														)}
														{isGenerating
															? isSyncingGeneration
																? '同步节点中…'
																: '生成中…'
															: '重新生成步骤'}
													</Button>
												)}
								</div>
											</div>
											<Textarea
												value={draft.goal}
												onChange={(event) =>
													updateDraft({ goal: event.target.value })
												}
												disabled={isSaving || isRunning || isGenerating}
												placeholder="描述任务最终要完成什么..."
												className="task-goal-input border-slate-100 bg-slate-50/70 text-xs leading-5 focus-visible:bg-white"
											/>
										</section>
										<section className="min-w-0 rounded-2xl border border-slate-200 bg-white p-4">
											<div className="mb-2 flex h-9 items-center justify-between gap-3">
												<div>
													<div className="text-xs font-semibold text-slate-800">
														执行输入（可选）
													</div>
													<div className="mt-0.5 text-[10px] text-slate-500">
														只作用于本次运行，不修改任务目标
													</div>
												</div>
											</div>
											<Textarea
												value={runInput}
												onChange={(event) =>
													setRunInput(event.target.value)
												}
												disabled={isSaving || isRunning || isGenerating}
												placeholder="补充任务背景、特殊要求或参考信息..."
												className="task-run-input border-slate-100 bg-slate-50/70 text-xs leading-5 focus-visible:bg-white"
											/>
										</section>
									</div>

									<details className="mb-4 rounded-xl border border-slate-200 bg-slate-50/50 px-4 py-3">
										<summary className="cursor-pointer list-none text-xs font-semibold text-slate-700">
											执行上下文{' '}
											<span className="ml-1 font-normal text-slate-500">
												Agent、会话与工作区
											</span>
										</summary>
										<div className="mt-3 grid gap-3 sm:grid-cols-2">
											<label className="space-y-1.5 text-[11px] text-slate-500">
												<span>智能体</span>
												<select
													value={executionAgentId ?? ''}
													onChange={(event) => {
														setExecutionAgentId(
															event.target.value || null,
														);
														setExecutionSessionId(
															event.target.value ? undefined : null,
														);
														setIsDirty(true);
													}}
													disabled={isSaving || isRunning}
													className="h-9 w-full rounded-lg border border-slate-200 bg-white px-2.5 text-xs text-slate-800 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 disabled:opacity-50"
												>
													<option value="">不绑定（仅手动编排）</option>
													{agents.map((agent) => (
														<option key={agent.id} value={agent.id}>
															{agent.data.name}
														</option>
													))}
												</select>
											</label>
											<label className="space-y-1.5 text-[11px] text-slate-500">
												<span>会话</span>
												<select
													value={executionSessionId ?? ''}
													onChange={(event) => {
														setExecutionSessionId(
															event.target.value || null,
														);
														setIsDirty(true);
													}}
													disabled={
														!executionAgentId ||
														isSaving ||
														isRunning ||
														isGenerating
													}
													className="h-9 w-full rounded-lg border border-slate-200 bg-white px-2.5 text-xs text-slate-800 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 disabled:opacity-50"
												>
													<option value="">不绑定（仅手动编排）</option>
													{sessions.map((view) => (
														<option
															key={view.session.id}
															value={view.session.id}
														>
															{view.session.config.name ||
																view.session.id}
														</option>
													))}
												</select>
											</label>
										</div>
										<div className="mt-3 text-[10px] leading-5 text-slate-500">
											{executionAgentId && executionSessionId
												? '已绑定 AgentScope 智能体和会话，节点规划与执行都会复用现有运行时。'
												: '未绑定时仍可手动编排；自动生成步骤前，请先选择 Agent 和会话。'}
										</div>
									</details>

									<TaskKnowledgeSelector
										selectedIds={draft.knowledge_base_ids}
										disabled={isSaving || isRunning || isGenerating}
										onChange={(knowledge_base_ids) =>
											updateDraft({ knowledge_base_ids })
										}
									/>
									<TaskKnowledgeGraph
										taskId={draft.id}
										selectedIds={draft.knowledge_base_ids}
										disabled={isSaving || isRunning || isGenerating}
										compact
									/>
									{!currentRunIsActive && (!draft.graph || canEditDag) && (
										<TaskFlowEditor
											nodes={draft.nodes}
											tools={availableTools}
											onChange={(nodes) => updateDraft({
												nodes,
												...(draft.graph ? { graph: reconcileDagNodes(draft.graph, nodes) } : {}),
											})}
											disabled={
												isSaving ||
												isRunning ||
												currentRunIsActive ||
												(Boolean(draft.source_template_id) && !sourceTemplate)
											}
											templateMode={
												Boolean(draft.source_template_id) &&
												draft.workflow_mode !== 'custom_flow'
											}
											customFlowMode={draft.workflow_mode === 'custom_flow'}
											dagMode={Boolean(draft.graph)}
											template={sourceTemplate}
											templateStepBindings={draft.template_step_bindings}
										/>
									)}
								</section>
									</div>
								</>
							)}
						</>
					) : (
						<div className="flex flex-1 flex-col items-center justify-center px-6 text-center">
							{error && (
								<div className="mb-6 flex items-start gap-2 rounded-xl bg-red-50 px-3 py-2.5 text-left text-sm text-red-700">
									<AlertCircle className="mt-0.5 size-4 shrink-0" />
									<span>{error}</span>
								</div>
							)}
							<div className="mb-4 flex size-14 items-center justify-center rounded-2xl bg-blue-50 text-blue-700">
								<Plus className="size-6" />
							</div>
							<div className="text-xl font-semibold text-slate-900">
								从标准场景开始创建任务
							</div>
							<div className="mt-2 max-w-md text-sm leading-6 text-slate-500">
								选择九个业务模板之一，按模板流程创建；也可以继续使用自由任务和 AI
								Planner。
							</div>
							<Button
								className="mt-6 bg-blue-600 text-white hover:bg-blue-700"
								onClick={() => setTemplateGalleryOpen(true)}
							>
								<Plus />
								浏览业务模板
							</Button>
							<Button
								className="mt-6"
								variant="outline"
								onClick={() => setNewTaskOpen(true)}
							>
								自由任务（AI 规划）
							</Button>
						</div>
					)}
				</section>
				<ResizeHandle
					label="调整执行情报宽度"
					onPointerDown={(event) => beginResize('results', event)}
					onNudge={(delta) =>
						setResultWidth((value) =>
							clamp(value - delta, MIN_RESULT_WIDTH, getResultWidthLimit()),
						)
					}
				/>
				<TaskRunPanel
					run={draft ? currentRun : null}
					executionEvents={runEvents}
					goal={draft?.goal ?? ''}
					taskName={draft?.title ?? ''}
					knowledgeBaseNames={[]}
					width={resultWidth}
					onCancel={() => void handleCancel()}
					onAnswer={handleAnswer}
					onRetry={() => void handleRetry()}
					onDagControl={(action) => void handleDagControl(action)}
					pendingDagCommand={pendingDagCommand}
					requestedNodeId={selectedRunNodeId}
					onNodeSelected={setSelectedRunNodeId}
					onRetryNode={(nodeId) => void handleRetryNode(nodeId)}
					fullResultRequest={fullResultRequest}
					sopControlsEnabled={taskDagCapabilities?.sop_control_enabled === true}
					sopNodeRetryEnabled={taskDagCapabilities?.sop_node_retry_enabled === true}
				/>
			</main>
			<Dialog open={customCopyOpen} onOpenChange={setCustomCopyOpen}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>另存为自定义流程</DialogTitle>
						<DialogDescription>
							创建独立 Task 副本，保留官方模板来源、版本和输入快照；原模板、当前 Task 与已有 Run 不会被改写。
						</DialogDescription>
					</DialogHeader>
					<div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="选择副本流程模式">
						<button
							type="button"
							role="radio"
							aria-checked={customCopyMode === 'sop'}
							onClick={() => setCustomCopyMode('sop')}
							className={`rounded-xl border p-3 text-left ${customCopyMode === 'sop' ? 'border-blue-500 bg-blue-50' : 'border-slate-200 bg-white'}`}
						>
							<div className="text-sm font-semibold">保持 SOP</div>
							<div className="mt-1 text-xs leading-5 text-muted-foreground">
								复制现有线性执行顺序；与当前“另存自定义流程”行为兼容。
							</div>
						</button>
						<button
							type="button"
							role="radio"
							aria-checked={customCopyMode === 'dag'}
							disabled={taskDagCapabilities?.editing_enabled !== true}
							onClick={() => setCustomCopyMode('dag')}
							className={`rounded-xl border p-3 text-left disabled:cursor-not-allowed disabled:opacity-50 ${customCopyMode === 'dag' ? 'border-indigo-500 bg-indigo-50' : 'border-slate-200 bg-white'}`}
						>
							<div className="text-sm font-semibold">转换为 DAG</div>
							<div className="mt-1 text-xs leading-5 text-muted-foreground">
								复制原节点配置并生成线性初始依赖；之后可在 DAG 编辑器中增加分支和汇合。
							</div>
						</button>
					</div>
					{customCopyMode === 'dag' && (
						<div className={`rounded-lg border px-3 py-2 text-xs leading-5 ${copyDagExecutionBlockReasons.length > 0 ? 'border-amber-200 bg-amber-50 text-amber-900' : 'border-blue-200 bg-blue-50 text-blue-900'}`}>
							{copyDagExecutionBlockReasons.length > 0
								? copyDagExecutionBlockReasons.join(' ')
								: '当前 TaskService 能力检查允许提交 DAG 预检；具体冻结模板、图结构和业务交付门禁仍由服务端在 Preflight 与创建 Run 时重新校验。'}
						</div>
					)}
					{taskDagCapabilities?.editing_enabled !== true && (
						<div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
							{taskDagCapabilities
								? '服务端 DAG 编辑开关关闭；仍可另存为 SOP。'
								: '暂时无法确认服务端 DAG 编辑权限；为避免误报，DAG 模式不可选。'}
						</div>
					)}
					<DialogFooter>
						<Button type="button" variant="ghost" onClick={() => setCustomCopyOpen(false)}>
							取消
						</Button>
						<Button
							type="button"
							disabled={isSaving || isRunning || isGenerating || (customCopyMode === 'dag' && taskDagCapabilities?.editing_enabled !== true)}
							onClick={() => void handleCreateCustomFlowCopy(customCopyMode)}
						>
							<Copy />
							另存副本
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
			<NewTaskDialog
				open={newTaskOpen}
				onOpenChange={setNewTaskOpen}
				onCreate={handleCreate}
			/>
			<TaskTemplateGallery
				open={templateGalleryOpen}
				onOpenChange={setTemplateGalleryOpen}
				tools={availableTools}
				isToolsLoading={isLoadingTools}
				onUseTemplate={(template) => {
					setTemplateGalleryOpen(false);
					setTemplateToCreate(template);
				}}
				onOpenFreeTask={() => {
					setTemplateGalleryOpen(false);
					setNewTaskOpen(true);
				}}
			/>
			<TaskTemplateCreateDialog
				template={templateToCreate}
				sourceContext={buildExecutionContext()}
				onOpenChange={(open) => {
					if (!open) setTemplateToCreate(null);
				}}
				onInstantiate={handleInstantiateTemplate}
			/>
		</div>
	);
}
