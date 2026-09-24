import type { NodeRunRecord, RunStatus, TaskRecord, TaskRunRecord } from '@/api';

export type TaskListFilter = 'all' | 'running' | 'completed' | 'cancelled';

export interface TaskMetricValue {
	label: string;
	value: string;
	icon: 'nodes' | 'progress' | 'duration' | 'agent' | 'knowledge' | 'retrieval' | 'artifact';
}

export function getTaskRunStatus(task: TaskRecord): RunStatus | null {
	if (task.generation_status === 'generating') return 'queued';
	return task.last_run_status ?? null;
}

export function isTaskRunning(task: TaskRecord): boolean {
	const status = getTaskRunStatus(task);
	return status === 'queued' || status === 'running';
}

export function getTaskStatusLabel(task: TaskRecord): string {
	if (task.generation_status === 'generating') return '规划中';
	if (task.generation_status === 'failed') return '规划失败';
	switch (task.last_run_status) {
		case 'queued':
			return '等待执行';
		case 'running':
			return '执行中';
		case 'succeeded':
			return '已完成';
		case 'failed':
		case 'timed_out':
			return '执行失败';
		case 'canceled':
			return '已取消';
		default:
			return task.status === 'draft' ? '规划中' : '尚未执行';
	}
}

export function isTaskInFilter(task: TaskRecord, filter: TaskListFilter): boolean {
	if (filter === 'all') return true;
	if (filter === 'running') return isTaskRunning(task);
	if (filter === 'completed') return task.last_run_status === 'succeeded';
	return task.last_run_status === 'canceled';
}

export function formatTaskDate(value: string): string {
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return '日期未知';
	return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
}

export function formatClockTime(value?: string | null): string {
	if (!value) return '—';
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return '—';
	return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
}

export function getDurationSeconds(start?: string | null, finish?: string | null): number | null {
	if (!start) return null;
	const startMs = new Date(start).getTime();
	if (Number.isNaN(startMs)) return null;
	const endMs = finish ? new Date(finish).getTime() : Date.now();
	if (Number.isNaN(endMs) || endMs < startMs) return null;
	return Math.round((endMs - startMs) / 1000);
}

export function formatDuration(seconds: number | null | undefined): string {
	if (seconds === null || seconds === undefined) return '—';
	if (seconds < 60) return seconds + 's';
	const minutes = Math.floor(seconds / 60);
	const remainder = seconds % 60;
	return remainder ? minutes + 'm ' + remainder + 's' : minutes + 'm';
}

export function getNodeDuration(node: NodeRunRecord): number | null {
	return getDurationSeconds(node.started_at, node.finished_at);
}

export function getTaskMetrics(
	task: Pick<TaskRecord, 'id' | 'nodes' | 'knowledge_base_ids'>,
	run: TaskRunRecord | null,
	isDirty: boolean,
): TaskMetricValue[] {
	const matchingRun = run && run.task_id === task.id && !isDirty ? run : null;
	const completedCount = matchingRun?.node_runs.filter((node) => node.status === 'succeeded').length ?? 0;
	const activeNodeCount = matchingRun?.node_runs.filter((node) => node.type === 'agent' && node.status !== 'pending').length ?? 0;
	const duration = matchingRun
		? getDurationSeconds(matchingRun.started_at ?? matchingRun.created_at, matchingRun.finished_at)
		: null;
	return [
		{ label: '步骤', value: String(task.nodes.length), icon: 'nodes' },
		{
			label: '已完成',
			value: matchingRun ? completedCount + '/' + task.nodes.length : '0/' + task.nodes.length,
			icon: 'progress',
		},
		{ label: '总耗时', value: formatDuration(duration), icon: 'duration' },
		{ label: 'Agent', value: matchingRun ? String(activeNodeCount) : '—', icon: 'agent' },
		{ label: '知识库', value: String(task.knowledge_base_ids?.length ?? 0), icon: 'knowledge' },
		{ label: '检索片段', value: '—', icon: 'retrieval' },
		{ label: '产物', value: matchingRun ? String(matchingRun.artifacts.length) : '—', icon: 'artifact' },
	];
}
