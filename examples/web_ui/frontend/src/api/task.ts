import { client } from './client';

export type TaskStatus = 'draft' | 'active' | 'archived';
export type TaskGenerationStatus = 'idle' | 'generating' | 'succeeded' | 'failed';
export type RunStatus =
	| 'queued'
	| 'running'
	| 'awaiting_input'
	| 'succeeded'
	| 'failed'
	| 'canceled'
	| 'timed_out';
export type NodeRunStatus =
	| 'pending'
	| 'running'
	| 'awaiting_input'
	| 'succeeded'
	| 'failed'
	| 'canceled';
export type TaskStepType = 'agent' | 'tool' | 'python' | 'input';
export type TaskWorkflowMode = 'template_instance' | 'custom_flow' | 'free_ai_planner';
export type TaskArtifactFormat = 'markdown' | 'docx' | 'xlsx';
export type TaskErrorCategory =
	| 'configuration_error'
	| 'missing_tool'
	| 'permission_denied'
	| 'data_source_not_ready'
	| 'schema_mismatch'
	| 'input_incomplete'
	| 'citation_verification_failed'
	| 'external_interface_failed'
	| 'artifact_generation_failed'
	| 'execution_failed';

export interface TaskErrorDetail {
	category: TaskErrorCategory;
	code: string;
	message: string;
	task_id: string;
	run_id: string;
	node_id?: string | null;
	details: Record<string, unknown>;
	occurred_at: string;
}

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

export interface TaskInputQuestion {
	key: string;
	label: string;
	required: boolean;
	options: string[];
}

export interface InputStepConfig {
	type: 'input';
	reason: string;
	questions: TaskInputQuestion[];
}

export interface TaskInputRequest {
	id: string;
	node_id: string;
	reason: string;
	questions: TaskInputQuestion[];
	created_at: string;
}

export type TaskStepConfig = AgentStepConfig | ToolStepConfig | PythonStepConfig | InputStepConfig;

export interface TaskArtifactConfig {
	format: TaskArtifactFormat;
	filename?: string | null;
}

export interface TaskOutputSchema {
	type: 'object' | 'array' | 'string' | 'integer' | 'number' | 'boolean' | 'null';
	title?: string;
	description?: string;
	properties?: Record<string, TaskOutputSchema>;
	required?: string[];
	additionalProperties?: boolean;
	items?: TaskOutputSchema;
	minimum?: number;
	maximum?: number;
	minLength?: number;
	maxLength?: number;
	minItems?: number;
	maxItems?: number;
	enum?: Array<string | number | boolean | null>;
}

export interface TaskArtifactRecord {
	id: string;
	run_id: string;
	task_id: string;
	node_id: string;
	name: string;
	format: TaskArtifactFormat;
	media_type: string;
	path: string;
	size_bytes: number;
	preview_text: string;
	created_at: string;
}
export interface TaskNode {
	id: string;
	name: string;
	prompt: string;
	type: TaskStepType;
	config: TaskStepConfig;
	order: number;
	capability_key?: string | null;
	artifact?: TaskArtifactConfig | null;
	output_schema?: TaskOutputSchema | null;
	knowledge_graph_enabled?: boolean;
	knowledge_base_mode?: 'inherit' | 'override' | 'disabled';
	knowledge_base_ids?: string[];
	acceptance_criteria?: string;
	max_attempts?: number;
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
	knowledge_base_ids?: string[] | null;
	intent?: string | null;
	title_source: 'auto' | 'user';
	revision: number;
	status: TaskStatus;
	generation_status: TaskGenerationStatus;
	generation_error?: string | null;
	source_context: TaskContext;
	source_template_id?: string | null;
	source_template_version?: string | null;
	template_step_bindings?: Record<string, string>;
	template_inputs?: Record<string, unknown> | null;
	workflow_mode?: TaskWorkflowMode | null;
	source_task_id?: string | null;
	source_task_revision?: number | null;
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
	error_detail?: TaskErrorDetail | null;
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

export type TaskCapabilityStatus =
	| 'available'
	| 'missing'
	| 'incompatible'
	| 'permission_denied'
	| 'unsupported'
	| 'unverified';

export type TaskCapabilityPermissionStatus =
	| 'allowed'
	| 'denied'
	| 'requires_runtime_check'
	| 'requires_confirmation'
	| 'not_applicable'
	| 'unavailable';

export interface TaskCapabilityCheck {
	step_id?: string | null;
	step_name: string;
	capability_key: string;
	category: string;
	tool_name?: string | null;
	tool_available?: boolean | null;
	interface_compatible?: boolean | null;
	permission_status: TaskCapabilityPermissionStatus;
	business_data_verified?: boolean | null;
	external_side_effect: boolean;
	status: TaskCapabilityStatus;
	message: string;
	error_category?: TaskErrorCategory | null;
	error_code?: string | null;
}

export interface TaskPreflightResponse {
	task_id?: string | null;
	valid: boolean;
	ready?: boolean | null;
	issues: Array<{ path: string; message: string }>;
	missing_capabilities: string[];
	blocked_capabilities: string[];
	capability_checks: TaskCapabilityCheck[];
}

export interface TaskRunEvent {
	id: string;
	type: string;
	task_id: string;
	run_id: string;
	node_id?: string | null;
	sequence: number;
	payload: Record<string, unknown>;
	created_at: string;
}
export interface TaskRunRecord {
	id: string;
	task_id: string;
	user_id: string;
	task_revision: number;
	nodes: TaskNode[];
	node_runs: NodeRunRecord[];
	knowledge_base_ids?: string[] | null;
	input: string;
	final_output: string;
	final_summary: string;
	artifacts: TaskArtifactRecord[];
	status: RunStatus;
	error?: string | null;
	error_detail?: TaskErrorDetail | null;
	context: TaskContext;
	created_at: string;
	started_at?: string | null;
	finished_at?: string | null;
	engine?: 'legacy' | 'sop';
	workflow_mode?: TaskWorkflowMode | null;
	source_template_id?: string | null;
	source_template_version?: string | null;
	source_task_id?: string | null;
	source_task_revision?: number | null;
	pending_input?: TaskInputRequest | null;
}

export interface CreateTaskRequest {
	title?: string;
	goal: string;
	nodes?: TaskNode[];
	source_context?: TaskContext;
	knowledge_base_ids?: string[];
}

export interface UpdateTaskRequest {
	title?: string;
	goal?: string;
	nodes?: TaskNode[];
	source_context?: TaskContext;
	knowledge_base_ids?: string[];
}

export interface TaskKnowledgeBaseOption {
	id: string;
	name: string;
	description: string;
	document_count: number;
	chunk_count: number;
	ready_document_count: number;
}

export interface TaskKnowledgeGraphNode {
	id: string;
	label: string;
	type: string;
	properties: Record<string, unknown>;
	aliases: string[];
	source_refs: Array<{
		document_id: string;
		chunk_index?: number | null;
		filename?: string | null;
		metadata: Record<string, unknown>;
	}>;
}

export interface TaskKnowledgeGraphEdge {
	id: string;
	source: string;
	target: string;
	label: string;
	properties: Record<string, unknown>;
	source_refs: TaskKnowledgeGraphNode['source_refs'];
}

export interface TaskKnowledgeGraphResponse {
	task_id: string;
	knowledge_base_ids: string[];
	extraction_enabled: boolean;
	status: 'empty' | 'disabled' | 'building' | 'ready' | 'error' | string;
	error?: string | null;
	nodes: TaskKnowledgeGraphNode[];
	edges: TaskKnowledgeGraphEdge[];
	node_count: number;
	edge_count: number;
	version: number;
	mode?: 'stored' | 'llm' | 'hybrid' | 'empty' | string;
	matched_chunk_count?: number;
	extracted_chunk_count?: number;
}

export interface TaskKnowledgeGraphRebuildResponse {
	task_id: string;
	knowledge_base_ids: string[];
	extraction_enabled: boolean;
	status: string;
	documents: number;
	skipped: number;
	reused?: number;
	error?: string | null;
}

export interface TaskRunRequest {
	input?: string;
	context?: TaskContext;
}

export interface GenerateTaskRequest {
	context?: TaskContext | null;
}

export interface TaskTemplateInputProperty {
	type: 'string' | 'integer' | 'number' | 'boolean' | 'array';
	description: string;
	items?: TaskTemplateInputProperty | null;
	minimum?: number | null;
	maximum?: number | null;
	minItems?: number | null;
}

export interface TaskTemplateInputSchema {
	type: 'object';
	required: string[];
	properties: Record<string, TaskTemplateInputProperty>;
	additionalProperties?: false;
}

export interface TaskTemplateStep {
	step_key: string;
	title: string;
	kind: TaskStepType;
	required: boolean;
	editable: boolean;
	capability_key: string | null;
	config:
		| AgentStepConfig
		| { type: 'tool'; arguments: Record<string, unknown> }
		| InputStepConfig
		| PythonStepConfig;
	output_requirement: string;
	failure_handling: string;
	sensitive_information: string;
	artifact?: TaskArtifactConfig | null;
	output_schema?: TaskOutputSchema | null;
}

export interface TaskTemplateUnsupportedCapability {
	capability_key: string;
	reason: string;
}

export interface TaskTemplateDefinition {
	schema_version: 1;
	template_id: string;
	template_version: string;
	title: string;
	description: string;
	category: string;
	required_capabilities: string[];
	input_schema: TaskTemplateInputSchema;
	output_requirement: string;
	steps: TaskTemplateStep[];
	unsupported_capabilities: TaskTemplateUnsupportedCapability[];
}

export interface InstantiateTaskTemplateRequest {
	template_version: string;
	inputs: Record<string, unknown>;
	source_context?: TaskContext;
}

export const taskApi = {
	list: () => client.get<TaskRecord[]>('/tasks/'),

	listTemplates: () => client.get<TaskTemplateDefinition[]>('/tasks/templates'),

	getTemplate: (templateId: string) =>
		client.get<TaskTemplateDefinition>(`/tasks/templates/${templateId}`),

	instantiateTemplate: (templateId: string, body: InstantiateTaskTemplateRequest) =>
		client.post<TaskRecord>(`/tasks/templates/${templateId}/instantiate`, body),

	createCustomFlowCopy: (taskId: string) =>
		client.post<TaskRecord>(`/tasks/${taskId}/custom-flow-copy`, {}),

	preflight: (taskId: string, body: TaskRunRequest = {}) =>
		client.post<TaskPreflightResponse>(`/tasks/${taskId}/preflight`, body),

	listKnowledgeBases: () => client.get<TaskKnowledgeBaseOption[]>('/tasks/knowledge-bases'),

	get: (taskId: string, options?: { silent?: boolean; timeoutMs?: number }) =>
		client.get<TaskRecord>(`/tasks/${taskId}`, undefined, options),

	create: (body: CreateTaskRequest) => client.post<TaskRecord>('/tasks/', body),

	generate: (taskId: string, body: GenerateTaskRequest = {}) =>
		client.post<TaskRecord>(`/tasks/${taskId}/generate`, body, undefined, {
			// The backend has its own planner deadline. Keep the browser deadline
			// slightly longer so it can return the persisted fallback/failed state.
			timeoutMs: 60_000,
			silent: true,
		}),

	listTools: (context: TaskContext = {}) =>
		client.get<TaskToolSchema[]>(
			'/tasks/tools',
			{
				...(context.agent_id ? { agent_id: context.agent_id } : {}),
				...(context.session_id ? { session_id: context.session_id } : {}),
				...(context.workspace_id ? { workspace_id: context.workspace_id } : {}),
			},
			// Tool discovery is optional during page hydration. The caller already
			// falls back to an empty list, so a transient MCP failure should not
			// produce a misleading global "server unreachable" toast.
			{ silent: true },
		),

	update: (taskId: string, body: UpdateTaskRequest) =>
		client.patch<TaskRecord>(`/tasks/${taskId}`, body),

	getKnowledgeGraph: (
		taskId: string,
		params: { knowledge_base_ids?: string[]; query?: string } = {},
	) =>
		client.get<TaskKnowledgeGraphResponse>(`/tasks/${taskId}/knowledge-graph`, {
			...(params.knowledge_base_ids?.length
				? { knowledge_base_ids: params.knowledge_base_ids.join(',') }
				: {}),
			...(params.query ? { query: params.query } : {}),
		}),

	rebuildKnowledgeGraph: (taskId: string, knowledge_base_ids?: string[], force_extract = false) =>
		client.post<TaskKnowledgeGraphRebuildResponse>(`/tasks/${taskId}/knowledge-graph/rebuild`, {
			...(knowledge_base_ids?.length ? { knowledge_base_ids } : {}),
			force_extract,
		}),

	getStepKnowledgeGraph: (
		runId: string,
		nodeId: string,
		options: { query?: string; force_extract?: boolean } = {},
	) =>
		client.post<TaskKnowledgeGraphResponse>(
			`/tasks/runs/${runId}/nodes/${nodeId}/knowledge-graph`,
			options,
		),

	delete: (taskId: string) => client.delete(`/tasks/${taskId}`),

	run: (taskId: string, body: TaskRunRequest = {}) =>
		client.post<TaskRunRecord>(`/tasks/${taskId}/runs`, body),

	getRun: (runId: string) => client.get<TaskRunRecord>(`/tasks/runs/${runId}`),

	listRuns: (taskId: string) => client.get<TaskRunRecord[]>(`/tasks/${taskId}/runs`),

	cancelRun: (runId: string) => client.post<TaskRunRecord>(`/tasks/runs/${runId}/cancel`),

	retryRun: (runId: string) => client.post<TaskRunRecord>(`/tasks/runs/${runId}/retry`),

	answerRun: (runId: string, requestId: string, answers: Record<string, string>) =>
		client.post<TaskRunRecord>(`/tasks/runs/${runId}/input`, {
			request_id: requestId,
			answers,
		}),

	getArtifactContent: async (runId: string, artifactId: string, download = false) => {
		const response = await client.stream(
			`/tasks/runs/${runId}/artifacts/${artifactId}/content`,
			{
				method: 'GET',
				params: { download: String(download) },
			},
		);
		return response.blob();
	},
	streamRunEvents: async function* (
		runId: string,
		signal?: AbortSignal,
	): AsyncGenerator<TaskRunEvent> {
		const res = await client.stream(`/tasks/runs/${runId}/events`, {
			method: 'GET',
			signal,
		});
		const reader = res.body?.getReader();
		if (!reader) return;

		const decoder = new TextDecoder();
		let buffer = '';

		try {
			while (true) {
				const { done, value } = await reader.read();
				if (done) break;

				buffer += decoder.decode(value, { stream: true });
				const frames = buffer.split(/\n\n/);
				buffer = frames.pop() ?? '';

				for (const frame of frames) {
					const data = frame
						.split(/\n/)
						.filter((line) => line.startsWith('data:'))
						.map((line) => line.slice(5).trim())
						.join('\n');
					if (data) yield JSON.parse(data) as TaskRunEvent;
				}
			}
		} finally {
			reader.releaseLock();
		}
	},
};
