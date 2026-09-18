import { client } from './client';

export type TaskStatus = 'active' | 'archived';
export type RunStatus =
	| 'queued'
	| 'running'
	| 'succeeded'
	| 'failed'
	| 'canceled'
	| 'timed_out';
export type NodeRunStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'canceled';

export interface TaskNode {
	id: string;
	name: string;
	prompt: string;
	type: 'ai';
	order: number;
}

export interface TaskContext {
	source_chat_id?: string | null;
	session_id?: string | null;
	agent_id?: string | null;
	workspace_id?: string | null;
}

export interface TaskRecord {
	id: string;
	user_id: string;
	title: string;
	goal: string;
	nodes: TaskNode[];
	revision: number;
	status: TaskStatus;
	source_context: TaskContext;
	last_run_id?: string | null;
	last_run_status?: RunStatus | null;
	created_at: string;
	updated_at: string;
}

export interface NodeRunRecord {
	node_id: string;
	name: string;
	prompt: string;
	order: number;
	status: NodeRunStatus;
	input: string;
	output: string;
	error?: string | null;
	started_at?: string | null;
	finished_at?: string | null;
}

export interface TaskRunRecord {
	id: string;
	task_id: string;
	user_id: string;
	task_revision: number;
	nodes: TaskNode[];
	node_runs: NodeRunRecord[];
	input: string;
	final_output: string;
	status: RunStatus;
	error?: string | null;
	context: TaskContext;
	created_at: string;
	started_at?: string | null;
	finished_at?: string | null;
}

export interface CreateTaskRequest {
	title: string;
	goal: string;
	nodes: TaskNode[];
	source_context?: TaskContext;
}

export interface UpdateTaskRequest {
	title?: string;
	goal?: string;
	nodes?: TaskNode[];
}

export interface TaskRunRequest {
	input?: string;
	context?: TaskContext;
}

export const taskApi = {
	list: () => client.get<TaskRecord[]>('/tasks/'),

	get: (taskId: string) => client.get<TaskRecord>(`/tasks/${taskId}`),

	create: (body: CreateTaskRequest) => client.post<TaskRecord>('/tasks/', body),

	update: (taskId: string, body: UpdateTaskRequest) =>
		client.patch<TaskRecord>(`/tasks/${taskId}`, body),

	delete: (taskId: string) => client.delete(`/tasks/${taskId}`),

	run: (taskId: string, body: TaskRunRequest = {}) =>
		client.post<TaskRunRecord>(`/tasks/${taskId}/runs`, body),

	getRun: (runId: string) => client.get<TaskRunRecord>(`/tasks/runs/${runId}`),

	listRuns: (taskId: string) => client.get<TaskRunRecord[]>(`/tasks/${taskId}/runs`),

	cancelRun: (runId: string) => client.post<TaskRunRecord>(`/tasks/runs/${runId}/cancel`),

	retryRun: (runId: string) => client.post<TaskRunRecord>(`/tasks/runs/${runId}/retry`),
};
