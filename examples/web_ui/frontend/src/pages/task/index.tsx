import { AlertCircle, Loader2, Play, Plus, Save } from 'lucide-react';
import * as React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';

import type { TaskContext, TaskNode, TaskRecord, TaskRunRecord } from '@/api';
import { taskApi } from '@/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAgents } from '@/hooks/useAgents';
import { useSessions } from '@/hooks/useSessions';

import { TaskFlowEditor } from './task-flow-editor';
import { TaskRunPanel } from './task-run-panel';
import { TaskSidebar } from './task-sidebar';

interface TaskDraft {
	id: string;
	title: string;
	goal: string;
	nodes: TaskNode[];
}

const TERMINAL_RUN_STATUSES = new Set([
	'succeeded',
	'failed',
	'canceled',
	'timed_out',
]);

function createNode(order: number): TaskNode {
	return {
		id: `node-${Date.now()}-${order}`,
		name: `AI 节点 ${order + 1}`,
		prompt: '请基于上一节点的输出继续完成任务。',
		type: 'ai',
		order,
	};
}

function toDraft(task: TaskRecord): TaskDraft {
	return {
		id: task.id,
		title: task.title,
		goal: task.goal,
		nodes: task.nodes.map((node) => ({ ...node })),
	};
}

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : '任务请求失败，请稍后重试。';
}

function normalizeNodes(nodes: TaskNode[]): TaskNode[] {
	return nodes.map((node, index) => ({ ...node, order: index, type: 'ai' }));
}

export function TaskPage() {
	const navigate = useNavigate();
	const { taskId } = useParams<{ taskId?: string }>();
	const [tasks, setTasks] = React.useState<TaskRecord[]>([]);
	const [selectedTaskId, setSelectedTaskId] = React.useState<string | null>(taskId ?? null);
	const [draft, setDraft] = React.useState<TaskDraft | null>(null);
	const [currentRun, setCurrentRun] = React.useState<TaskRunRecord | null>(null);
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
	const [error, setError] = React.useState<string | null>(null);
	const { agents } = useAgents();
	const { sessions, loading: sessionsLoading } = useSessions(executionAgentId ?? null);

	React.useEffect(() => {
		if (executionAgentId === undefined && agents[0]) setExecutionAgentId(agents[0].id);
	}, [agents, executionAgentId]);

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
			setCurrentRun(null);
			setIsDirty(false);
			if (selected && selected.id !== taskId) navigate(`/task/${selected.id}`, { replace: true });
		} catch (requestError) {
			setError(errorMessage(requestError));
		} finally {
			setIsLoading(false);
		}
	}, [navigate, taskId]);

	React.useEffect(() => {
		void loadTasks();
	}, [loadTasks]);

	React.useEffect(() => {
		if (!currentRun || TERMINAL_RUN_STATUSES.has(currentRun.status)) return undefined;

		let disposed = false;
		const timer = window.setInterval(() => {
			void taskApi
				.getRun(currentRun.id)
				.then((latest) => {
					if (disposed) return;
					setCurrentRun(latest);
					setIsRunning(!TERMINAL_RUN_STATUSES.has(latest.status));
				})
				.catch((requestError) => {
					if (!disposed) setError(errorMessage(requestError));
				});
		}, 700);

		return () => {
			disposed = true;
			window.clearInterval(timer);
		};
	}, [currentRun]);

	const selectTask = (task: TaskRecord) => {
		setSelectedTaskId(task.id);
		setDraft(toDraft(task));
		setExecutionAgentId(task.source_context.agent_id ?? undefined);
		setExecutionSessionId(task.source_context.session_id ?? undefined);
		setCurrentRun(null);
		setRunInput('');
		setIsDirty(false);
		setError(null);
		navigate(`/task/${task.id}`);
	};

	const handleCreate = async () => {
		setError(null);
		try {
			const created = await taskApi.create({
				title: '未命名任务',
				goal: '',
				nodes: [createNode(0)],
			});
			setTasks((previous) => [created, ...previous]);
			setSelectedTaskId(created.id);
			setDraft(toDraft(created));
			setExecutionAgentId(created.source_context.agent_id ?? undefined);
			setExecutionSessionId(created.source_context.session_id ?? undefined);
			setCurrentRun(null);
			setIsDirty(false);
			navigate(`/task/${created.id}`);
			toast.success('任务已创建');
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

	const handleRun = async () => {
		if (!draft || isSaving || isRunning) return;
		const saved = isDirty ? await saveDraft() : tasks.find((task) => task.id === draft.id) ?? null;
		if (!saved) return;
		setError(null);
		setIsRunning(true);
		try {
			const selectedSession = sessions.find(
				(view) => view.session.id === executionSessionId,
			);
			const context: TaskContext = {
				...saved.source_context,
				...(executionAgentId ? { agent_id: executionAgentId } : {}),
				...(executionSessionId ? { session_id: executionSessionId } : {}),
				...(selectedSession?.session.config.workspace_id
					? { workspace_id: selectedSession.session.config.workspace_id }
					: {}),
			};
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
		<div className="flex size-full gap-2 p-2">
			<TaskSidebar
				tasks={tasks}
				selectedTaskId={selectedTaskId}
				onSelect={selectTask}
				onCreate={() => void handleCreate()}
				onDelete={(task) => void handleDelete(task)}
				disabled={isSaving || isRunning}
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
							把一个目标拆成多个 AI 节点，保存后即可重复执行。
						</div>
						<Button className="mt-6" onClick={() => void handleCreate()}>
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
										disabled={isSaving || isRunning}
										className="h-9 max-w-xl border-transparent bg-transparent px-0 text-xl font-semibold shadow-none focus-visible:border-border focus-visible:px-2"
										placeholder="任务名称"
									/>
									{isDirty && <Badge variant="outline">未保存</Badge>}
								</div>
								<div className="mt-1 text-xs text-muted-foreground">
									Task · 线性 AI 工作流 · {draft.nodes.length} 个节点
								</div>
							</div>
							<div className="flex shrink-0 items-center gap-2">
								<Button
									variant="outline"
									onClick={() => void saveDraft()}
									disabled={!isDirty || isSaving || isRunning}
								>
									{isSaving ? <Loader2 className="animate-spin" /> : <Save />}
									{isSaving ? '保存中…' : '保存'}
								</Button>
								<Button onClick={() => void handleRun()} disabled={isSaving || isRunning}>
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

						<div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)] overflow-hidden border-t border-border/70">
							<section className="min-h-0 overflow-y-auto px-6 py-5">
								<div className="mb-5 rounded-2xl bg-muted/50 p-4">
									<div className="mb-2 text-xs font-medium text-muted-foreground">任务目标</div>
									<Textarea
										value={draft.goal}
										onChange={(event) => updateDraft({ goal: event.target.value })}
										disabled={isSaving || isRunning}
										placeholder="描述这个任务最终要完成什么，例如：制定一次出差规划。"
										className="min-h-20 resize-y border-transparent bg-transparent px-0 shadow-none focus-visible:border-border focus-visible:bg-card focus-visible:px-2"
									/>
								</div>
								<div className="mb-3 text-xs font-medium text-muted-foreground">执行输入（可选）</div>
								<Textarea
									value={runInput}
									onChange={(event) => setRunInput(event.target.value)}
									disabled={isSaving || isRunning}
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
												}}
												disabled={isSaving || isRunning}
												className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm text-foreground outline-none focus:border-ring focus:ring-3 focus:ring-ring/50 disabled:opacity-50"
											>
												<option value="">不绑定（预览执行）</option>
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
												onChange={(event) => setExecutionSessionId(event.target.value || null)}
												disabled={!executionAgentId || isSaving || isRunning}
												className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm text-foreground outline-none focus:border-ring focus:ring-3 focus:ring-ring/50 disabled:opacity-50"
											>
												<option value="">不绑定（预览执行）</option>
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
											? '已绑定 AgentScope 智能体和会话，执行时会复用现有 ChatService。'
											: '未绑定执行上下文时使用预览执行器，便于先验证流程编排。'}
									</div>
								</div>
								<TaskFlowEditor
									nodes={draft.nodes}
									onChange={(nodes) => updateDraft({ nodes })}
									disabled={isSaving || isRunning}
								/>
							</section>
							<TaskRunPanel
								run={currentRun}
								onCancel={() => void handleCancel()}
								onRetry={() => void handleRetry()}
							/>
						</div>
					</>
				)}
			</main>
		</div>
	);
}
