import { client } from './client';
import type { PublicationScope, ResourceKind, ResourcePublication } from './types';

export type AdminUserStatus = 'active' | 'locked' | 'banned' | 'deleted';

export interface AdminUser {
	id: string;
	username: string;
	role: 'user' | 'admin';
	status: AdminUserStatus;
	plan_id: string;
	plan_name: string;
	monthly_quota: number;
	monthly_used: number;
	bonus_tokens: number;
	account_type: 'standard' | 'test';
	created_at: string;
	updated_at: string;
}

export interface UserListResponse {
	users: AdminUser[];
	total: number;
	page: number;
	page_size: number;
	request_id: string;
}

export interface CreateUserRequest {
	username: string;
	initial_password: string;
	plan_id?: string;
	bonus_tokens?: number;
}

export interface UpdateUserRequest {
	status?: Exclude<AdminUserStatus, 'deleted'>;
	plan_id?: string;
	bonus_tokens?: number;
}

export interface ResetPasswordResponse {
	operation_id: string;
	request_id: string;
	state: 'completed';
	user_id: string;
	username: string;
	temporary_password: string;
	expires_at: string;
}

export interface AdminOverview {
	system_id: string;
	pool_tokens: number;
	total_recharged: string;
	account_count: number;
	admin_count: number;
	active_account_count: number;
	app_version: string;
	core_version: string;
	health: 'ok' | 'not_ready';
	updated_at: string;
}

export interface SkillAnalyticsDaily {
	date: string;
	event_count: number;
	exposed: number;
	invoked: number;
	completed: number;
}

export interface SkillAnalyticsTopSkill {
	skill_name: string;
	invoked_count: number;
}

export interface SkillFailureBreakdown {
	key: string;
	count: number;
}

export interface SkillFailureRecord {
	occurred_at: string;
	event_name: string;
	stage: string;
	error_code: string;
	result: string;
	user_id: string;
	session_id: string | null;
	skill_name: string | null;
	duration_seconds: number | null;
}

export interface TokenUserUsage {
	user_id: string;
	username: string;
	role: string;
	input_tokens: number;
	output_tokens: number;
	cache_input_tokens: number;
	cache_creation_input_tokens: number;
	total_tokens: number;
	message_count: number;
	session_count: number;
}

export interface TokenUsageAnalytics {
	input_tokens: number;
	output_tokens: number;
	cache_input_tokens: number;
	cache_creation_input_tokens: number;
	total_tokens: number;
	message_count: number;
	session_count: number;
	user_count: number;
	users: TokenUserUsage[];
}

export interface SkillAnalytics {
	start: string;
	end: string;
	skill_data_available: boolean;
	token_usage: TokenUsageAnalytics;
	event_count: number;
	reconcile: Record<string, number>;
	lifecycle: {
		exposed: number;
		invoked: number;
		completed: number;
	};
	completed: {
		success: number;
		failed: number;
		other: number;
	};
	failure_count: number;
	execution_failure_count: number;
	execution_failure_rate: number;
	failure_by_stage: SkillFailureBreakdown[];
	failure_by_error: SkillFailureBreakdown[];
	recent_failures: SkillFailureRecord[];
	actual_usage_rate: number;
	average_reconcile_duration_seconds: number | null;
	latest_snapshot: {
		visible_count: number;
		after_count: number;
	} | null;
	daily: SkillAnalyticsDaily[];
	top_skills: SkillAnalyticsTopSkill[];
}

export interface ObservabilityDaily {
	date: string;
	requests: number;
	errors: number;
	calls: number;
	input_tokens: number;
	output_tokens: number;
	total_tokens: number;
}

export interface ObservabilityComponentRow {
	name: string;
	user_names: string[];
	last_occurred_at: string;
	tool_call_count: number;
	call_count: number;
	success_count: number;
	failure_count: number;
	success_rate: number;
	average_duration_seconds: number | null;
	input_tokens: number;
	output_tokens: number;
	total_tokens: number;
	tool_kind: string | null;
	mcp_server: string | null;
	timeout_count: number;
}

export interface ObservabilityFailure {
	occurred_at: string;
	event_name: string;
	component: string;
	error_code: string;
	category: string | null;
	error_type: string | null;
	request_id: string | null;
	trace_id: string | null;
	user_id: string | null;
	username: string | null;
	session_id: string | null;
	agent_name: string | null;
	model: string | null;
	tool: string | null;
	tool_kind: string | null;
	mcp_server: string | null;
	skill_name: string | null;
	route: string | null;
	duration_seconds: number | null;
}

export interface ObservabilityTokenUsage {
	input_tokens: number;
	output_tokens: number;
	cache_input_tokens: number;
	cache_creation_input_tokens: number;
	total_tokens: number;
	message_count: number;
	session_count: number;
	user_count: number;
	users: TokenUserUsage[];
}

export interface ObservabilityOverview {
	start: string;
	end: string;
	data_available: boolean;
	event_count: number;
	request_count: number;
	successful_requests: number;
	failed_requests: number;
	success_rate: number;
	active_user_count: number;
	average_response_time_seconds: number | null;
	token_usage: ObservabilityTokenUsage;
	daily: ObservabilityDaily[];
	models: ObservabilityComponentRow[];
	agents: ObservabilityComponentRow[];
	tools: ObservabilityComponentRow[];
	failures: ObservabilityFailure[];
	failure_by_component: Array<{ key: string; count: number }>;
	failure_by_type: Array<{ key: string; count: number }>;
}

export interface ObservabilityFailureCenter {
	start: string;
	end: string;
	failure_count: number;
	failure_by_component: Array<{ key: string; count: number }>;
	failure_by_type: Array<{ key: string; count: number }>;
	failures: ObservabilityFailure[];
}

export type ObservabilityComponent = 'model' | 'agent' | 'tool';

export interface ObservabilityComponentDetail {
	start: string;
	end: string;
	component: ObservabilityComponent;
	call_count: number;
	success_count: number;
	failure_count: number;
	success_rate: number;
	average_duration_seconds: number | null;
	p95_duration_seconds: number | null;
	input_tokens: number;
	output_tokens: number;
	total_tokens: number;
	daily: ObservabilityDaily[];
	items: ObservabilityComponentRow[];
	failures: ObservabilityFailure[];
	user_options: ObservabilityUserOption[];
	user_breakdown: ObservabilityUserBreakdown[];
	timeout_count: number;
	agent_breakdown: ObservabilityAgentBreakdown[];
	executions: ToolExecutionRecord[];
	mcp_servers: McpServerStatus[];
}

export interface ObservabilityUserOption {
	user_id: string;
	username: string;
}

export interface ObservabilityUserBreakdown extends ObservabilityUserOption {
	call_count: number;
	percentage: number;
}

export interface ObservabilityAgentBreakdown {
	agent_name: string;
	call_count: number;
	percentage: number;
}

export interface ToolExecutionRecord {
	occurred_at: string;
	request_id: string | null;
	trace_id: string | null;
	user_id: string | null;
	username: string | null;
	agent_name: string | null;
	result: string;
	duration_seconds: number | null;
	error_code: string | null;
	tool_kind: string;
	mcp_server: string | null;
}

export interface McpServerStatus {
	server_name: string;
	status: string;
	tool_count: number;
	call_count: number;
	average_duration_seconds: number | null;
	failure_count: number;
}

export interface AgentExecutionRecord {
	occurred_at: string;
	request_id: string | null;
	trace_id: string | null;
	user_id: string | null;
	username: string | null;
	result: string;
	duration_seconds: number | null;
	input_tokens: number;
	output_tokens: number;
	total_tokens: number;
	model_call_count: number;
	tool_call_count: number;
	error_code: string | null;
}

export interface ObservabilityAgentDetail {
	start: string;
	end: string;
	agent_name: string;
	call_count: number;
	success_count: number;
	failure_count: number;
	success_rate: number;
	average_duration_seconds: number | null;
	input_tokens: number;
	output_tokens: number;
	total_tokens: number;
	tool_call_count: number;
	executions: AgentExecutionRecord[];
}

export interface ObservabilityTraceEvent {
	occurred_at: string;
	event_name: string;
	component: string;
	result: string;
	request_id: string | null;
	trace_id: string | null;
	user_id: string | null;
	username: string | null;
	agent_name: string | null;
	model: string | null;
	tool: string | null;
	tool_kind: string | null;
	mcp_server: string | null;
	skill_name: string | null;
	route: string | null;
	duration_seconds: number | null;
	input_tokens: number;
	output_tokens: number;
	total_tokens: number;
	error_code: string | null;
}

export interface ObservabilityTrace {
	trace_id: string;
	request_id: string | null;
	events: ObservabilityTraceEvent[];
}

export interface SystemQuota {
	system_id: string;
	pool_tokens: number;
	total_recharged: string;
	test_default_tokens: number;
	account_count: number;
	admin_count: number;
	updated_at: string;
	request_id: string;
}

export interface LedgerEntry {
	ledger_id: string;
	type: string;
	delta_tokens: number;
	balance_after: number;
	amount: string | null;
	order_id: string | null;
	related_user_id: string | null;
	operator_id: string | null;
	source: string;
	created_at: string;
}

export interface LedgerResponse {
	entries: LedgerEntry[];
	total: number;
	request_id: string;
}

export interface RechargeRequest {
	order_id: string;
	system_id: string;
	amount: string;
	status: 'pending' | 'approved' | 'rejected' | 'unknown';
	delivery_status: 'not_delivered' | 'delivered';
	created_at: string;
	request_id: string;
}

export interface RechargeRequestListResponse {
	orders: RechargeRequest[];
	total: number;
	request_id: string;
}

export interface AuditEvent {
	event_id: string;
	actor_type: 'admin' | 'user' | 'system';
	actor_id: string;
	actor_name: string;
	target_user_id: string | null;
	target_user_name: string | null;
	action: string;
	resource_type: string | null;
	resource_id: string | null;
	reason: string;
	request_id: string;
	status: 'completed' | 'failed';
	result_summary: string | null;
	created_at: string;
}

export interface AuditEventResponse {
	events: AuditEvent[];
	total: number;
	request_id: string;
}

export interface AuditResourceResponse {
	target_user_id: string;
	resource_type: 'overview' | 'session' | 'document';
	resource_id: string | null;
	data: Record<string, unknown>;
	request_id: string;
}

export interface RedeemRechargeCodeResponse {
	operation_id: string;
	state: 'completed';
	system_id: string;
	amount: string;
	tokens: number;
	ledger_id: string;
	pool_tokens_after: number;
	request_id: string;
}

export interface SalesHubConfig {
	system_id: string;
	hub_url: string;
	token_masked: string | null;
	public_key_fingerprint: string | null;
	outbound_status: 'unknown' | 'ok' | 'failed';
	inbound_status: 'unknown' | 'ok' | 'failed';
	last_verified_at: string | null;
	request_id: string;
}

export interface SalesHubConfigUpdate {
	system_id?: string;
	hub_url?: string;
	token?: string;
	public_key?: string;
}

export interface AdminPolicy {
	admin_api_requires_admin_role: boolean;
	credential_management: 'admin_only';
	sales_hub_authentication: 'customer_bearer_token';
	recharge_legacy_hmac_enabled: boolean;
	high_risk_plugin_installation: 'disabled';
	policy_mutation_from_chat: boolean;
	request_id_enforced: boolean;
	generated_at: string;
}

export interface OperationResponse {
	operation_id: string;
	request_id: string;
	state: 'pending' | 'completed' | 'failed' | 'unknown' | 'rolled_back';
	result: Record<string, unknown> | null;
	error: Record<string, unknown> | null;
}

export interface ResourcePublicationListResponse {
	resources: ResourcePublication[];
	total: number;
}

export interface PublishResourceRequest {
	kind: ResourceKind;
	source_id: string;
	source_record_id?: string | null;
	name: string;
	display_name?: string | null;
	description?: string;
	tags?: string[];
	author?: string | null;
	icon_url?: string | null;
	version?: string | null;
	scope: PublicationScope;
	user_ids?: string[];
	enabled?: boolean;
}

const idempotencyHeaders = () => ({
	'Idempotency-Key': crypto.randomUUID(),
});

const toParams = (params: Record<string, string | number | undefined>) =>
	Object.fromEntries(
		Object.entries(params)
			.filter(([, value]) => value !== undefined)
			.map(([key, value]) => [key, String(value)]),
	);

export const adminApi = {
	overview: () => client.get<AdminOverview>('/admin/overview'),
	observability: (days = 14) =>
		client.get<ObservabilityOverview>('/admin/observability/overview', toParams({ days })),
	observabilityFailures: (days = 14, filters?: { component?: string; error_type?: string; user_id?: string; limit?: number }) =>
		client.get<ObservabilityFailureCenter>('/admin/observability/failures', toParams({ days, ...filters })),
	observabilityComponent: (component: ObservabilityComponent, days = 14, filters?: { name?: string; user_id?: string }) =>
		client.get<ObservabilityComponentDetail>(`/admin/observability/${component}`, toParams({ days, ...filters })),
	observabilityAgent: (agentName: string, days = 14) =>
		client.get<ObservabilityAgentDetail>(`/admin/observability/agents/${encodeURIComponent(agentName)}`, toParams({ days })),
	observabilityTrace: (traceId: string) =>
		client.get<ObservabilityTrace>(`/admin/observability/traces/${encodeURIComponent(traceId)}`),
	skillAnalytics: (days = 14) =>
		client.get<SkillAnalytics>('/admin/analytics/skills', toParams({ days })),
	policy: () => client.get<AdminPolicy>('/admin/policy'),
	users: (params: {
		keyword?: string;
		status?: string;
		plan_id?: string;
		page?: number;
		page_size?: number;
	} = {}) => client.get<UserListResponse>('/admin/users', toParams(params)),
	createUser: (body: CreateUserRequest) =>
		client.post<AdminUser>('/admin/users', body, undefined, { headers: idempotencyHeaders() }),
	updateUser: (userId: string, body: UpdateUserRequest) =>
		client.patch<AdminUser>(`/admin/users/${userId}`, body, undefined, {
			headers: idempotencyHeaders(),
		}),
	deleteUser: (userId: string, reason: string) =>
		client.delete(`/admin/users/${userId}`, undefined, {
			body: { confirm: true, reason },
			headers: idempotencyHeaders(),
		}),
	resetPassword: (userId: string, body: { reason: string; admin_password: string }) =>
		client.post<ResetPasswordResponse>(
			`/admin/users/${userId}/reset-password`,
			body,
			undefined,
			{ headers: idempotencyHeaders() },
		),
	revokeSessions: (userId: string) =>
		client.delete(`/admin/users/${userId}/sessions`, undefined, {
			headers: idempotencyHeaders(),
		}),
	quota: () => client.get<SystemQuota>('/admin/quota'),
	updateQuota: (test_default_tokens: number) =>
		client.patch<SystemQuota>('/admin/quota', { test_default_tokens }, undefined, {
			headers: idempotencyHeaders(),
		}),
	ledger: (limit = 20) =>
		client.get<LedgerResponse>('/admin/quota/ledger', toParams({ limit })),
	auditEvents: (limit = 50) =>
		client.get<AuditEventResponse>('/admin/audit/events', toParams({ limit })),
	auditOverview: (body: { target_user_id: string; reason: string }) =>
		client.post<AuditResourceResponse>('/admin/audit/overview', body),
	auditSession: (
		sessionId: string,
		body: { overview_event_id: string; agent_id: string; reason: string },
	) => client.post<AuditResourceResponse>(`/admin/audit/sessions/${encodeURIComponent(sessionId)}`, body),
	auditDocument: (
		documentId: string,
		body: { overview_event_id: string; knowledge_base_id: string; reason: string },
	) => client.post<AuditResourceResponse>(`/admin/audit/documents/${encodeURIComponent(documentId)}`, body),
	rechargeRequests: (limit = 20) =>
		client.get<RechargeRequestListResponse>(
			'/admin/quota/recharge-requests',
			toParams({ limit }),
		),
	createRechargeRequest: (body: { amount: string; note?: string }) =>
		client.post<RechargeRequest>('/admin/quota/recharge-requests', body, undefined, {
			headers: idempotencyHeaders(),
		}),
	redeemRechargeCode: (code: string) =>
		client.post<RedeemRechargeCodeResponse>(
			'/admin/quota/redeem-code',
			{ code, confirm: true },
			undefined,
			{ headers: idempotencyHeaders() },
		),
	syncRecharge: () =>
		client.post<OperationResponse>(
			'/admin/quota/recharge-requests/sync',
			undefined,
			undefined,
			{ headers: idempotencyHeaders() },
		),
	reportUsage: () =>
		client.post<OperationResponse>(
			'/admin/sales-hub/usage-report',
			undefined,
			undefined,
			{ headers: idempotencyHeaders() },
		),
	hubConfig: () => client.get<SalesHubConfig>('/admin/sales-hub/config'),
	updateHubConfig: (body: SalesHubConfigUpdate) =>
		client.patch<SalesHubConfig>('/admin/sales-hub/config', body, undefined, {
			headers: idempotencyHeaders(),
		}),
	verifyHub: () =>
		client.post<OperationResponse>('/admin/sales-hub/verify', undefined, undefined, {
			headers: idempotencyHeaders(),
		}),
	resourcePublications: (kind?: ResourceKind) =>
		client.get<ResourcePublicationListResponse>(
			'/admin/resources',
			kind ? { kind } : undefined,
		),
	publishResource: (body: PublishResourceRequest) =>
		client.post<ResourcePublication>('/admin/resources', body),
	removeSkill: (skillId: string) =>
		client.delete(`/admin/skills/${encodeURIComponent(skillId)}`, undefined, {
			headers: idempotencyHeaders(),
		}),
	removeMcp: (mcpId: string) =>
		client.delete(`/admin/mcps/${encodeURIComponent(mcpId)}`, undefined, {
			headers: idempotencyHeaders(),
		}),
};
