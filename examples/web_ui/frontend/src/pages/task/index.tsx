import { AlertCircle, ChevronDown, Copy, Loader2, Plus, RefreshCw } from 'lucide-react';
import './task-page.css';
import * as React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';

import { NewTaskDialog } from './new-task-dialog';
import { TaskFlowEditor } from './task-flow-editor';
import { TaskHeader } from './task-header';
import { TaskKnowledgeGraph } from './task-knowledge-graph';
import { TaskKnowledgeSelector } from './task-knowledge-selector';
import { TaskMetrics } from './task-metrics';
import { TaskOverview } from './task-overview';
import { TaskRunPanel } from './task-run-panel';
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
	TaskErrorCategory,
	TaskPreflightResponse,
	TaskTemplateDefinition,
} from '@/api/task';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Textarea } from '@/components/ui/textarea';
import { useAgents } from '@/hooks/useAgents';
import { useSessions } from '@/hooks/useSessions';

interface TaskDraft {
	id: string;
	title: string;
	goal: string;
	nodes: TaskNode[];
	knowledge_base_ids: string[];
	generation_status: TaskRecord['generation_status'];
	generation_error?: string | null;
	source_template_id?: string | null;
	source_template_version?: string | null;
	template_step_bindings?: Record<string, string>;
	workflow_mode?: TaskRecord['workflow_mode'];
	source_task_id?: string | null;
	source_task_revision?: number | null;
}

const TERMINAL_RUN_STATUSES = new Set(['succeeded', 'failed', 'canceled', 'timed_out']);
const CAPABILITY_STATUS_LABELS: Record<string, string> = {
	available: '可用',
	missing: '缺少工具',
	incompatible: '接口不兼容',
	permission_denied: '权限不足',
	unsupported: '当前不支持',
	unverified: '尚未验证',
};
const PERMISSION_STATUS_LABELS: Record<string, string> = {
	allowed: '已通过',
	denied: '拒绝',
	requires_runtime_check: '运行时复核',
	requires_confirmation: '需要交互确认',
	not_applicable: '不适用',
	unavailable: '无法检查',
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

function toDraft(task: TaskRecord): TaskDraft {
	return {
		id: task.id,
		title: task.title,
		goal: task.goal,
		nodes: task.nodes.map((node) => ({ ...node })),
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

function CapabilityPreflightPanel({
	result,
	isChecking,
}: {
	result: TaskPreflightResponse | null;
	isChecking: boolean;
}) {
	const [open, setOpen] = React.useState(() => Boolean(result && result.ready !== true));
	const checks = result?.capability_checks ?? [];
	const issues = result?.issues ?? [];
	const availableCount = checks.filter((check) => check.status === 'available').length;
	const unavailableCount = checks.filter((check) => check.status !== 'available').length;
	const failureCount = unavailableCount || issues.length;
	const hasDetails = checks.length > 0 || issues.length > 0;
	const passed = result?.ready === true;
	const panelClass = isChecking
		? 'border-amber-200 bg-amber-50/70 text-amber-950'
		: result
			? passed
				? 'border-emerald-200 bg-emerald-50/70 text-emerald-950'
				: 'border-red-200 bg-red-50/70 text-red-950'
			: 'border-slate-200 bg-slate-50/80 text-slate-800';
	const summary = isChecking
		? '运行前能力检查中...'
		: result
			? passed
				? `✓ 运行前能力检查已通过 · ${availableCount > 0 ? `${availableCount} 项能力可用` : '当前无能力检查项'}`
				: `✕ 运行前能力检查未通过${failureCount > 0 ? ` · ${failureCount} 项异常` : ''}`
			: '运行前能力检查 · 尚未检查，点击“运行”后检查本任务能力';

	return (
		<div className={`mx-5 mb-3 min-w-0 rounded-xl border px-3 py-2 text-xs xl:mx-6 ${panelClass}`}>
			<Collapsible open={open} onOpenChange={setOpen}>
				<CollapsibleTrigger asChild>
					<button
						type="button"
						disabled={!result || isChecking || !hasDetails}
						className="flex w-full min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1.5 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 disabled:cursor-default"
					>
						<span
							className={`min-w-0 flex-1 break-words font-semibold ${!passed && result ? 'text-red-800' : ''}`}
						>
							{isChecking && (
								<Loader2
									className="mr-1.5 inline size-3.5 animate-spin align-[-2px]"
									aria-hidden="true"
								/>
							)}
							{summary}
						</span>
						<span className="ml-auto inline-flex shrink-0 items-center gap-1.5 text-[11px] font-medium text-slate-600">
							{hasDetails && result && (open ? '收起详情' : '展开详情')}
							{hasDetails && result && (
								<ChevronDown
									className={`size-3.5 transition-transform ${open ? 'rotate-180' : ''}`}
									aria-hidden="true"
								/>
							)}
						</span>
					</button>
				</CollapsibleTrigger>
				<CollapsibleContent className="min-w-0">
					<p className="mt-2 break-words text-[11px] leading-5 text-slate-700">
						工具可绑定不代表真实业务数据源已经接通；实际 Tool 调用仍会再次检查权限。
					</p>
					{checks.length > 0 && (
						<div className="mt-2 min-w-0 space-y-2">
							{checks.map((check) => (
								<div
									key={`${check.capability_key}:${check.step_id ?? 'missing'}`}
									className="min-w-0 rounded-lg border border-slate-200/80 bg-white/90 px-2.5 py-2"
								>
									<div className="flex min-w-0 flex-wrap items-center justify-between gap-1.5">
										<span className="min-w-0 break-words font-semibold">
											{check.step_name} · {check.category}
										</span>
										<span
											className={
												check.status === 'available'
													? 'text-emerald-700'
													: 'text-red-700'
											}
										>
											{CAPABILITY_STATUS_LABELS[check.status] ?? check.status}
										</span>
									</div>
									<div className="mt-1 break-words text-[11px] leading-5 text-slate-600">
										能力：<span className="break-all">{check.capability_key}</span>；工具：
										{check.tool_available == null
											? '不适用'
											: check.tool_available
												? '存在'
												: '缺少'}
										；接口：
										{check.interface_compatible == null
											? '未检查'
											: check.interface_compatible
												? '兼容'
												: '不兼容'}
										；权限：
										{PERMISSION_STATUS_LABELS[check.permission_status] ??
											check.permission_status}
										；真实数据源：
										{check.business_data_verified == null
											? '不适用'
											: check.business_data_verified
												? '已验证'
												: '未验证'}
									</div>
									<p className="mt-1 break-words text-[11px] leading-5 [overflow-wrap:anywhere]">
										{check.message}
									</p>
									{check.error_category && check.error_code && (
										<p className="mt-1 break-all text-[10px] font-medium text-red-700">
											{TASK_ERROR_CATEGORY_LABELS[check.error_category]} · {check.error_code}
										</p>
									)}
								</div>
							))}
						</div>
					)}
					{issues.length > 0 && (
						<ul className="mt-2 list-disc space-y-1 pl-4 text-[11px] leading-5 text-red-800">
							{issues.map((issue, index) => (
								<li
									key={`${issue.path}:${index}`}
									className="break-words [overflow-wrap:anywhere]"
								>
									{issue.path}：{issue.message}
								</li>
							))}
						</ul>
					)}
				</CollapsibleContent>
			</Collapsible>
		</div>
	);
}

export function TaskPage() {
	const navigate = useNavigate();
	const { taskId } = useParams<{ taskId?: string }>();
	const [tasks, setTasks] = React.useState<TaskRecord[]>([]);
	const [selectedTaskId, setSelectedTaskId] = React.useState<string | null>(taskId ?? null);
	const [draft, setDraft] = React.useState<TaskDraft | null>(null);
	const [currentRun, setCurrentRun] = React.useState<TaskRunRecord | null>(null);
	const [runEvents, setRunEvents] = React.useState<TaskRunEvent[]>([]);
	const [availableTools, setAvailableTools] = React.useState<TaskToolSchema[]>([]);
	const [executionAgentId, setExecutionAgentId] = React.useState<string | null | undefined>(
		undefined,
	);
	const [executionSessionId, setExecutionSessionId] = React.useState<string | null | undefined>(
		undefined,
	);
	const [runInput, setRunInput] = React.useState('');
	const [isDirty, setIsDirty] = React.useState(false);
	const [isLoading, setIsLoading] = React.useState(true);
	const [isSaving, setIsSaving] = React.useState(false);
	const [isRunning, setIsRunning] = React.useState(false);
	const [isGenerating, setIsGenerating] = React.useState(false);
	const [isSyncingGeneration, setIsSyncingGeneration] = React.useState(false);
	const [newTaskOpen, setNewTaskOpen] = React.useState(false);
	const [templateGalleryOpen, setTemplateGalleryOpen] = React.useState(false);
	const [templateToCreate, setTemplateToCreate] = React.useState<TaskTemplateDefinition | null>(
		null,
	);
	const [sourceTemplate, setSourceTemplate] = React.useState<TaskTemplateDefinition | null>(null);
	const [error, setError] = React.useState<string | null>(null);
	const [capabilityPreflight, setCapabilityPreflight] =
		React.useState<TaskPreflightResponse | null>(null);
	const [isPreflighting, setIsPreflighting] = React.useState(false);
	const [preflightResultVersion, setPreflightResultVersion] = React.useState(0);
	const preflightRequestRef = React.useRef(0);
	const clearCapabilityPreflight = React.useCallback(() => {
		preflightRequestRef.current += 1;
		setCapabilityPreflight(null);
		setIsPreflighting(false);
	}, []);
	const [sidebarWidth, setSidebarWidth] = React.useState(280);
	const [resultWidth, setResultWidth] = React.useState(380);
	const [resizingPane, setResizingPane] = React.useState<ResizePane | null>(null);
	const resizeStartRef = React.useRef<{ pane: ResizePane; x: number; width: number } | null>(
		null,
	);
	const { agents } = useAgents();
	const { sessions, loading: sessionsLoading } = useSessions(executionAgentId ?? null);
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
			return undefined;
		}
		let disposed = false;
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
			const records = await taskApi.list();
			setTasks(records);
			const selected = records.find((task) => task.id === taskId) ?? records[0];
			setSelectedTaskId(selected?.id ?? null);
			setDraft(selected ? toDraft(selected) : null);
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
		setSelectedTaskId(task.id);
		setDraft(toDraft(task));
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
		setTasks((previous) => [created, ...previous]);
		setSelectedTaskId(created.id);
		setDraft(toDraft(created));
		setExecutionAgentId(created.source_context.agent_id ?? null);
		setExecutionSessionId(created.source_context.session_id ?? null);
		setCurrentRun(null);
		setRunEvents([]);
		setIsDirty(false);
		navigate(`/task/${created.id}`);
		setNewTaskOpen(false);
		void generateTask(created, context, true);
		toast.success('任务已创建，正在生成节点');
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
		setTasks((previous) => [created, ...previous.filter((task) => task.id !== created.id)]);
		setSelectedTaskId(created.id);
		setDraft(toDraft(created));
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

	const handleCreateCustomFlowCopy = async () => {
		if (!draft || isSaving || isRunning || isGenerating) return;
		const source = isDirty
			? await saveDraft()
			: (tasks.find((task) => task.id === draft.id) ?? null);
		if (!source) return;
		setError(null);
		try {
			const created = await taskApi.createCustomFlowCopy(source.id);
			clearCapabilityPreflight();
			setTasks((previous) => [created, ...previous]);
			setSelectedTaskId(created.id);
			setDraft(toDraft(created));
			setExecutionAgentId(created.source_context.agent_id ?? null);
			setExecutionSessionId(created.source_context.session_id ?? null);
			setCurrentRun(null);
			setRunEvents([]);
			setIsDirty(false);
			navigate(`/task/${created.id}`);
			toast.success('已创建独立自定义流程副本');
		} catch (requestError) {
			setError(errorMessage(requestError));
		}
	};

	const saveDraft = async (): Promise<TaskRecord | null> => {
		if (!draft) return null;
		setIsSaving(true);
		setError(null);
		try {
			const saved = await taskApi.update(draft.id, {
				title: draft.title.trim() || '未命名任务',
				goal: draft.goal,
				nodes: normalizeNodes(draft.nodes),
				knowledge_base_ids: draft.knowledge_base_ids,
				source_context: buildExecutionContext(),
			});
			setTasks((previous) => previous.map((task) => (task.id === saved.id ? saved : task)));
			setDraft(toDraft(saved));
			setIsDirty(false);
			toast.success('任务已保存');
			return saved;
		} catch (requestError) {
			setError(errorMessage(requestError));
			return null;
		} finally {
			setIsSaving(false);
		}
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
		try {
			const context = buildExecutionContext();
			const preflight = await taskApi.preflight(saved.id, {
				input: runInput.trim(),
				context,
			});
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
			setError(errorMessage(requestError));
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
		setDraft((previous) => (previous ? { ...previous, ...update } : previous));
		setCapabilityPreflight(null);
		if (isPreflighting) {
			preflightRequestRef.current += 1;
			setIsPreflighting(false);
			setIsRunning(false);
		}
		setIsDirty(true);
	};
	const preflightViewState = capabilityPreflight
		? capabilityPreflight.ready === true
			? 'passed'
			: 'failed'
		: isPreflighting
			? 'checking'
			: 'unchecked';

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
				onSelect={selectTask}
				onCreate={() => setTemplateGalleryOpen(true)}
				onDelete={(task) => void handleDelete(task)}
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
								canRerun={Boolean(currentRun && currentRun.task_id === draft.id)}
								onTitleChange={(title) => updateDraft({ title })}
								onSave={() => void saveDraft()}
								onRerun={handleRerun}
								onRun={() => void handleRun()}
							/>
							<div className="mx-5 mb-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-blue-100 bg-blue-50/70 px-3 py-2 text-[11px] leading-5 text-blue-900 xl:mx-6">
								<div className="min-w-0">
									<div className="font-semibold">
										流程类型：{' '}
										{draft.workflow_mode === 'custom_flow'
											? '自定义流程'
											: draft.source_template_id
												? '标准模板任务'
												: '自由 AI Planner 任务'}
									</div>
									{draft.source_template_id ? (
										sourceTemplate ? (
											<div>
												来源模板：<strong>{sourceTemplate.title}</strong> v
												{sourceTemplate.template_version}。
												{draft.workflow_mode === 'custom_flow' ? (
													<>
														{' '}
														来源 Task：{draft.source_task_id ?? '未知'}
														（修订{' '}
														{draft.source_task_revision ?? '未知'}
														）。来源场景的必需 Tool 能力与执行前
														Preflight
														仍然生效；此自定义流程不代表审批通过。
													</>
												) : (
													<>
														{' '}
														标准模板的节点结构、必需步骤与顺序受服务端约束。
													</>
												)}
											</div>
										) : (
											<div>
												来源模板 {draft.source_template_id} v
												{draft.source_template_version ?? '未知'}{' '}
												无法加载或版本不匹配，流程编辑保持锁定。
											</div>
										)
									) : (
										<div>
											由自由任务 AI Planner
											生成节点；后续可按现有编辑能力调整。
										</div>
									)}
								</div>
								{draft.source_template_id &&
									draft.workflow_mode !== 'custom_flow' && (
										<Button
											variant="outline"
											size="sm"
											onClick={() => void handleCreateCustomFlowCopy()}
											disabled={
												!sourceTemplate ||
												isSaving ||
												isRunning ||
												isGenerating
											}
											title={
												!sourceTemplate
													? '来源模板加载完成后才能创建自定义流程副本。'
													: '复制当前 Task 并进入高级流程编辑。'
											}
										>
											<Copy />
											另存为自定义流程
										</Button>
									)}
							</div>
							{error && (
								<div className="mx-5 mb-3 flex items-start gap-2 rounded-xl border border-red-100 bg-red-50 px-3 py-2.5 text-xs text-red-700 xl:mx-6">
									<AlertCircle className="mt-0.5 size-4 shrink-0" />
									<span>{error}</span>
								</div>
							)}
							{draft && (
								<CapabilityPreflightPanel
									key={`${draft.id}:${preflightResultVersion}:${preflightViewState}`}
									result={capabilityPreflight}
									isChecking={isPreflighting}
								/>
							)}
							<div className="min-h-0 flex-1 overflow-hidden border-t border-slate-100">
								<section className="h-full min-w-0 overflow-y-auto px-4 py-4 xl:px-5">
									<TaskMetrics task={draft} run={currentRun} isDirty={isDirty} />
									<TaskOverview
										nodes={draft.nodes}
										run={currentRun}
										generationStatus={draft.generation_status}
										isDirty={isDirty}
									/>

									<div className="mb-4 grid gap-3 md:grid-cols-2">
										<section className="min-w-0 rounded-2xl border border-slate-200 bg-white p-4">
											<div className="mb-2 flex h-9 items-center justify-between gap-3">
												<div className="text-xs font-semibold text-slate-800">
													任务目标
												</div>
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
									<TaskFlowEditor
										nodes={draft.nodes}
										tools={availableTools}
										onChange={(nodes) => updateDraft({ nodes })}
										disabled={
											isSaving ||
											isRunning ||
											(Boolean(draft.source_template_id) && !sourceTemplate)
										}
										templateMode={
											Boolean(draft.source_template_id) &&
											draft.workflow_mode !== 'custom_flow'
										}
										customFlowMode={draft.workflow_mode === 'custom_flow'}
										template={sourceTemplate}
										templateStepBindings={draft.template_step_bindings}
									/>
								</section>
							</div>
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
				/>
			</main>
			<NewTaskDialog
				open={newTaskOpen}
				onOpenChange={setNewTaskOpen}
				onCreate={handleCreate}
			/>
			<TaskTemplateGallery
				open={templateGalleryOpen}
				onOpenChange={setTemplateGalleryOpen}
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
