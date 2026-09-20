import { AlertCircle, Loader2, Play, Plus, RefreshCw, Save } from 'lucide-react';
import * as React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';

import { NewTaskDialog } from './new-task-dialog';
import { TaskFlowEditor } from './task-flow-editor';
import { TaskKnowledgeGraph } from './task-knowledge-graph';
import { TaskKnowledgeSelector } from './task-knowledge-selector';
import { TaskOverview } from './task-overview';
import { TaskRunPanel } from './task-run-panel';
import { TaskSidebar } from './task-sidebar';
import type {
	TaskContext,
	TaskNode,
	TaskRecord,
	TaskRunRecord,
	TaskToolSchema,
} from '@/api';
import { taskApi } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
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
}

const TERMINAL_RUN_STATUSES = new Set([
	'succeeded',
	'failed',
	'canceled',
	'timed_out',
]);

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

export function TaskPage() {
	const navigate = useNavigate();
	const { taskId } = useParams<{ taskId?: string }>();
	const [tasks, setTasks] = React.useState<TaskRecord[]>([]);
	const [selectedTaskId, setSelectedTaskId] = React.useState<string | null>(taskId ?? null);
	const [draft, setDraft] = React.useState<TaskDraft | null>(null);
	const [currentRun, setCurrentRun] = React.useState<TaskRunRecord | null>(null);
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
	const [newTaskOpen, setNewTaskOpen] = React.useState(false);
	const [error, setError] = React.useState<string | null>(null);
	const [sidebarWidth, setSidebarWidth] = React.useState(280);
	const [resultWidth, setResultWidth] = React.useState(420);
	const [resizingPane, setResizingPane] = React.useState<ResizePane | null>(null);
	const resizeStartRef = React.useRef<{ pane: ResizePane; x: number; width: number } | null>(
		null,
	);
	const { agents } = useAgents();
	const { sessions, loading: sessionsLoading } = useSessions(executionAgentId ?? null);

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
				setResultWidth(clamp(start.width - delta, 320, 640));
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
		const selectedSession = sessions.find(
			(view) => view.session.id === executionSessionId,
		);
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
		const selectedSession = sessions.find(
			(view) => view.session.id === executionSessionId,
		);
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
				setIsRunning(false);
			}
			setIsDirty(false);
			if (selected && selected.id !== taskId) navigate(`/task/${selected.id}`, { replace: true });
		} catch (requestError) {
			setError(errorMessage(requestError));
		} finally {
			setIsLoading(false);
		}
	}, [navigate, restoreLatestRun, taskId]);

	React.useEffect(() => {
		void loadTasks();
	}, [loadTasks]);

	const currentRunId = currentRun?.id ?? null;
	const shouldStreamCurrentRun =
		Boolean(currentRunId) && !TERMINAL_RUN_STATUSES.has(currentRun?.status ?? 'queued');

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
				for await (const event of taskApi.streamRunEvents(currentRunId, controller.signal)) {
					if (disposed || event.run_id !== currentRunId) return;
					const latest = await taskApi.getRun(currentRunId);
					if (disposed) return;
					syncRun(latest);
					if (TERMINAL_RUN_STATUSES.has(latest.status)) break;
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
	}, [currentRunId, shouldStreamCurrentRun]);

	const selectTask = (task: TaskRecord) => {
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
			const failed = await taskApi.get(task.id).catch(() => null);
			if (failed) {
				setTasks((previous) =>
					previous.map((item) => (item.id === failed.id ? failed : item)),
				);
				if (showGeneratedTask || selectedTaskId === failed.id) setDraft(toDraft(failed));
			}
			setError(errorMessage(requestError));
		} finally {
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
		setTasks((previous) => [created, ...previous]);
		setSelectedTaskId(created.id);
		setDraft(toDraft(created));
		setExecutionAgentId(created.source_context.agent_id ?? null);
		setExecutionSessionId(created.source_context.session_id ?? null);
		setCurrentRun(null);
		setIsDirty(false);
		navigate(`/task/${created.id}`);
		setNewTaskOpen(false);
		void generateTask(created, context, true);
		toast.success('任务已创建，正在生成节点');
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
			: tasks.find((task) => task.id === draft.id) ?? null;
		if (!saved) return;
		setError(null);
		await generateTask(saved, buildExecutionContext());
	};

	const handleRun = async () => {
		if (!draft || isSaving || isRunning) return;
		if (!draft.goal.trim() || draft.nodes.length === 0) {
			setError('请先填写任务目的并配置至少一个任务步骤。');
			return;
		}
		const saved = isDirty ? await saveDraft() : tasks.find((task) => task.id === draft.id) ?? null;
		if (!saved) return;
		setError(null);
		setIsRunning(true);
		try {
			const context = buildExecutionContext();
			const run = await taskApi.run(saved.id, {
				input: runInput.trim(),
				context,
			});
			setCurrentRun(run);
			setTasks((previous) =>
				previous.map((task) =>
					task.id === saved.id
						? { ...task, last_run_id: run.id, last_run_status: run.status }
						: task,
				),
			);
		} catch (requestError) {
			setIsRunning(false);
			setError(errorMessage(requestError));
		}
	};

	const handleCancel = async () => {
		if (!currentRun) return;
		try {
			const run = await taskApi.cancelRun(currentRun.id);
			setCurrentRun(run);
			setIsRunning(false);
		} catch (requestError) {
			setError(errorMessage(requestError));
		}
	};

	const handleRetry = async () => {
		if (!currentRun) return;
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
				setSelectedTaskId(null);
				setDraft(null);
				setCurrentRun(null);
				navigate('/task');
			}
			toast.success('任务已删除');
		} catch (requestError) {
			setError(errorMessage(requestError));
		}
	};

	const updateDraft = (update: Partial<TaskDraft>) => {
		setDraft((previous) => (previous ? { ...previous, ...update } : previous));
		setIsDirty(true);
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
			className={`flex size-full min-w-0 gap-2 p-2 ${resizingPane ? 'select-none' : ''}`}
		>
			<TaskSidebar
				tasks={tasks}
				selectedTaskId={selectedTaskId}
				width={sidebarWidth}
				onSelect={selectTask}
				onCreate={() => setNewTaskOpen(true)}
				onDelete={(task) => void handleDelete(task)}
				disabled={isSaving || isRunning || isGenerating}
			/>
			<ResizeHandle
				label="调整我的任务宽度"
				onPointerDown={(event) => beginResize('sidebar', event)}
				onNudge={(delta) => setSidebarWidth((value) => clamp(value + delta, 220, 420))}
			/>

			<main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-[22px] bg-card shadow-panel">
				{!draft ? (
					<div className="flex flex-1 flex-col items-center justify-center px-6 text-center">
						{error && (
							<div className="mb-6 flex items-start gap-2 rounded-xl bg-red-50 px-3 py-2.5 text-left text-sm text-red-700">
								<AlertCircle className="mt-0.5 size-4 shrink-0" />
								<span>{error}</span>
							</div>
						)}
						<div className="mb-4 flex size-14 items-center justify-center rounded-2xl bg-muted">
							<Plus className="size-6 text-muted-foreground" />
						</div>
						<div className="text-xl font-semibold">创建你的第一个任务</div>
						<div className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
							把一个目标拆成多个步骤，保存后即可重复执行。
						</div>
						<Button className="mt-6" onClick={() => setNewTaskOpen(true)}>
							<Plus />
							新建任务
						</Button>
					</div>
				) : (
					<>
						<header className="flex items-center justify-between gap-4 px-6 pt-5 pb-4">
							<div className="min-w-0 flex-1">
								<div className="flex items-center gap-2">
									<Input
										value={draft.title}
										onChange={(event) => updateDraft({ title: event.target.value })}
										disabled={isSaving || isRunning || isGenerating}
										className="h-9 max-w-xl border-transparent bg-transparent px-0 text-xl font-semibold shadow-none focus-visible:border-border focus-visible:px-2"
										placeholder="任务名称"
									/>
									{isDirty && <Badge variant="outline">未保存</Badge>}
									{draft.generation_status === 'generating' && (
										<Badge variant="outline">节点生成中</Badge>
									)}
									{draft.generation_status === 'failed' && (
										<Badge className="bg-red-100 text-red-700">节点生成失败</Badge>
									)}
								</div>
								<div className="mt-1 text-xs text-muted-foreground">
						Task · 线性 Agent 工作流 · {draft.nodes.length} 个步骤
								</div>
							</div>
							<div className="flex shrink-0 items-center gap-2">
								<Button
									variant="outline"
									onClick={() => void saveDraft()}
									disabled={!isDirty || isSaving || isRunning || isGenerating}
								>
									{isSaving ? <Loader2 className="animate-spin" /> : <Save />}
									{isSaving ? '保存中…' : '保存'}
								</Button>
								<Button onClick={() => void handleRun()} disabled={isSaving || isRunning || isGenerating}>
									{isRunning ? <Loader2 className="animate-spin" /> : <Play />}
									{isRunning ? '执行中…' : '执行任务'}
								</Button>
							</div>
						</header>

						{error && (
							<div className="mx-6 mb-4 flex items-start gap-2 rounded-xl bg-red-50 px-3 py-2.5 text-sm text-red-700">
								<AlertCircle className="mt-0.5 size-4 shrink-0" />
								<span>{error}</span>
							</div>
						)}

						<div className="flex min-h-0 min-w-0 flex-1 overflow-hidden border-t border-border/70">
							<section className="min-h-0 min-w-0 flex-1 overflow-y-auto px-6 py-5">
								<TaskOverview
										nodes={draft.nodes}
										run={currentRun}
										generationStatus={draft.generation_status}
										isDirty={isDirty}
									/>
								<div className="mb-5 rounded-2xl bg-muted/50 p-4">
									<div className="mb-2 flex items-center justify-between gap-3">
										<div className="text-xs font-medium text-muted-foreground">任务目标</div>
										<Button
											variant="ghost"
											size="sm"
											onClick={() => void handleRegenerate()}
											disabled={isSaving || isRunning || isGenerating || !draft.goal.trim()}
										>
											{isGenerating ? (
												<Loader2 className="animate-spin" />
											) : (
												<RefreshCw />
											)}
											{isGenerating ? '生成中…' : '重新生成节点'}
										</Button>
									</div>
									<Textarea
										value={draft.goal}
										onChange={(event) => updateDraft({ goal: event.target.value })}
										disabled={isSaving || isRunning || isGenerating}
										placeholder="描述这个任务最终要完成什么，例如：制定一次出差规划。"
										className="min-h-20 resize-y border-transparent bg-transparent px-0 shadow-none focus-visible:border-border focus-visible:bg-card focus-visible:px-2"
									/>
								</div>
								<div className="mb-3 text-xs font-medium text-muted-foreground">执行输入（可选）</div>
								<Textarea
									value={runInput}
									onChange={(event) => setRunInput(event.target.value)}
															disabled={isSaving || isRunning || isGenerating}
									placeholder="执行时传给第一个节点的补充信息；留空则使用任务目标。"
									className="mb-6 min-h-16 resize-y bg-background"
								/>
								<div className="mb-6 rounded-2xl border border-border bg-card p-4">
									<div className="mb-3 text-xs font-medium text-muted-foreground">执行上下文</div>
									<div className="grid gap-3 sm:grid-cols-2">
										<label className="space-y-1.5 text-xs text-muted-foreground">
											<span>智能体</span>
											<select
												value={executionAgentId ?? ''}
												onChange={(event) => {
													setExecutionAgentId(event.target.value || null);
													setExecutionSessionId(
														event.target.value ? undefined : null,
													);
													setIsDirty(true);
												}}
												disabled={isSaving || isRunning}
												className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm text-foreground outline-none focus:border-ring focus:ring-3 focus:ring-ring/50 disabled:opacity-50"
											>
												<option value="">不绑定（仅手动编排）</option>
												{agents.map((agent) => (
													<option key={agent.id} value={agent.id}>
														{agent.data.name}
													</option>
												))}
											</select>
										</label>
										<label className="space-y-1.5 text-xs text-muted-foreground">
											<span>会话</span>
											<select
												value={executionSessionId ?? ''}
												onChange={(event) => {
													setExecutionSessionId(event.target.value || null);
													setIsDirty(true);
												}}
															disabled={!executionAgentId || isSaving || isRunning || isGenerating}
												className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm text-foreground outline-none focus:border-ring focus:ring-3 focus:ring-ring/50 disabled:opacity-50"
											>
														<option value="">不绑定（仅手动编排）</option>
												{sessions.map((view) => (
													<option key={view.session.id} value={view.session.id}>
														{view.session.config.name || view.session.id}
													</option>
												))}
											</select>
										</label>
									</div>
									<div className="mt-3 text-xs leading-5 text-muted-foreground">
										{executionAgentId && executionSessionId
											? '已绑定 AgentScope 智能体和会话，节点规划与执行都会复用现有运行时。'
											: '未绑定时仍可手动编排和预览执行；自动生成节点前，请先选择 Agent 和会话。'}
									</div>
								</div>
								<TaskKnowledgeSelector
									selectedIds={draft.knowledge_base_ids}
									disabled={isSaving || isRunning || isGenerating}
									onChange={(knowledge_base_ids) => updateDraft({ knowledge_base_ids })}
								/>
								<TaskKnowledgeGraph
									taskId={draft.id}
									selectedIds={draft.knowledge_base_ids}
									disabled={isSaving || isRunning || isGenerating}
								/>
								<TaskFlowEditor
									nodes={draft.nodes}
									tools={availableTools}
									onChange={(nodes) => updateDraft({ nodes })}
									disabled={isSaving || isRunning}
								/>
							</section>
							<ResizeHandle
								label="调整执行结果宽度"
								onPointerDown={(event) => beginResize('results', event)}
								onNudge={(delta) => setResultWidth((value) => clamp(value - delta, 320, 640))}
							/>
							<TaskRunPanel
								run={currentRun}
								width={resultWidth}
								onCancel={() => void handleCancel()}
								onRetry={() => void handleRetry()}
							/>
						</div>
					</>
				)}
			</main>
			<NewTaskDialog
				open={newTaskOpen}
				onOpenChange={setNewTaskOpen}
				onCreate={handleCreate}
			/>
		</div>
	);
}
