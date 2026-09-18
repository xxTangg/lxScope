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
