import { client } from './client';

export type TaskStatus = 'draft' | 'active' | 'archived';
export type TaskGenerationStatus = 'idle' | 'generating' | 'succeeded' | 'failed';
export type RunStatus =
	| 'queued'
	| 'running'
	| 'succeeded'
	| 'failed'
	| 'canceled'
	| 'timed_out';
export type NodeRunStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'canceled';
export type TaskStepType = 'agent' | 'tool' | 'python';

export interface AgentStepConfig {
	type: 'agent';
	prompt: string;
	agent_id?: string | null;
	session_id?: string | null;
}

export interface ToolStepConfig {
	type: 'tool';
	tool_name: string;
	arguments: Record<string, unknown>;
}

export interface PythonStepConfig {
	type: 'python';
	code: string;
	timeout_seconds: number;
}

export type TaskStepConfig = AgentStepConfig | ToolStepConfig | PythonStepConfig;

export interface TaskNode {
	id: string;
	name: string;
	prompt: string;
	type: TaskStepType;
	config: TaskStepConfig;
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
	intent?: string | null;
	title_source: 'auto' | 'user';
	revision: number;
	status: TaskStatus;
	generation_status: TaskGenerationStatus;
	generation_error?: string | null;
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
	type: TaskStepType;
	order: number;
	status: NodeRunStatus;
	input: string;
	output: string;
	display_summary: string;
	result?: unknown;
	metadata?: Record<string, unknown>;
	error?: string | null;
	started_at?: string | null;
	finished_at?: string | null;
}

export interface TaskToolSchema {
	name: string;
	description: string;
	input_schema: Record<string, unknown>;
	is_mcp: boolean;
	is_read_only: boolean;
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
	final_summary: string;
	status: RunStatus;
	error?: string | null;
	context: TaskContext;
	created_at: string;
	started_at?: string | null;
	finished_at?: string | null;
}

export interface CreateTaskRequest {
	title?: string;
	goal: string;
	nodes?: TaskNode[];
	source_context?: TaskContext;
}

export interface UpdateTaskRequest {
	title?: string;
	goal?: string;
	nodes?: TaskNode[];
	source_context?: TaskContext;
}

export interface TaskRunRequest {
	input?: string;
	context?: TaskContext;
}

export interface GenerateTaskRequest {
	context?: TaskContext | null;
}

export const taskApi = {
	list: () => client.get<TaskRecord[]>('/tasks/'),

	get: (taskId: string) => client.get<TaskRecord>(`/tasks/${taskId}`),

	create: (body: CreateTaskRequest) => client.post<TaskRecord>('/tasks/', body),

	generate: (taskId: string, body: GenerateTaskRequest = {}) =>
		client.post<TaskRecord>(`/tasks/${taskId}/generate`, body),

	listTools: (context: TaskContext = {}) =>
		client.get<TaskToolSchema[]>('/tasks/tools', {
			...(context.agent_id ? { agent_id: context.agent_id } : {}),
			...(context.session_id ? { session_id: context.session_id } : {}),
			...(context.workspace_id ? { workspace_id: context.workspace_id } : {}),
		}),

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
