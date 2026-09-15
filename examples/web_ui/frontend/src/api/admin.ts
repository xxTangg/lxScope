import { client } from './client';

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
}

export interface RechargeRequestListResponse {
	orders: RechargeRequest[];
	total: number;
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

export interface OperationResponse {
	operation_id: string;
	request_id: string;
	state: 'pending' | 'completed' | 'failed' | 'unknown' | 'rolled_back';
	result: Record<string, unknown> | null;
	error: Record<string, unknown> | null;
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
	users: (params: {
		keyword?: string;
		status?: string;
		page?: number;
		page_size?: number;
	} = {}) => client.get<UserListResponse>('/admin/users', toParams(params)),
	createUser: (body: CreateUserRequest) =>
		client.post<AdminUser>('/admin/users', body),
	updateUser: (userId: string, body: UpdateUserRequest) =>
		client.patch<AdminUser>(`/admin/users/${userId}`, body),
	deleteUser: (userId: string) => client.delete(`/admin/users/${userId}`),
	resetPassword: (userId: string, body: { reason: string; admin_password: string }) =>
		client.post<ResetPasswordResponse>(
			`/admin/users/${userId}/reset-password`,
			body,
		),
	quota: () => client.get<SystemQuota>('/admin/quota'),
	updateQuota: (test_default_tokens: number) =>
		client.patch<SystemQuota>('/admin/quota', { test_default_tokens }),
	ledger: (limit = 20) =>
		client.get<LedgerResponse>('/admin/quota/ledger', toParams({ limit })),
	rechargeRequests: (limit = 20) =>
		client.get<RechargeRequestListResponse>(
			'/admin/quota/recharge-requests',
			toParams({ limit }),
		),
	createRechargeRequest: (body: { amount: string; note?: string }) =>
		client.post<RechargeRequest>('/admin/quota/recharge-requests', body, undefined, {
			headers: idempotencyHeaders(),
		}),
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
		client.patch<SalesHubConfig>('/admin/sales-hub/config', body),
	verifyHub: () => client.post<OperationResponse>('/admin/sales-hub/verify'),
};
