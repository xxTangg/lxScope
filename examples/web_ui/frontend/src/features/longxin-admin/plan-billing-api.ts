import { client } from '@/api/client';

export interface Plan {
	id: string;
	name: string;
	monthly_quota: number;
	price: string;
	currency: string;
	period_days: number;
	description: string;
	features: string[];
}

export interface CurrentPlan {
	user_id: string;
	username: string;
	plan_id: string;
	plan_name: string;
	monthly_quota: number;
	monthly_used: number;
	bonus_tokens: number;
	remaining_tokens: number;
	status: 'inactive' | 'active' | 'expired';
	started_at: string | null;
	expires_at: string | null;
	latest_order_id: string | null;
}

export interface PlanOrder {
	order_id: string;
	user_id: string;
	username: string;
	plan_id: string;
	plan_name: string;
	monthly_quota: number;
	price: string;
	currency: string;
	period_days: number;
	order_type: 'activation' | 'renewal' | 'upgrade' | 'downgrade';
	status: 'pending' | 'approved' | 'rejected';
	note: string | null;
	decision_reason: string | null;
	allocated_tokens: number;
	requested_at: string;
	decided_at: string | null;
}

interface PlanCatalogResponse {
	plans: Plan[];
}

interface PlanOrderListResponse {
	orders: PlanOrder[];
	total: number;
}

const idempotencyKey = () => {
	if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
	return `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

export const planBillingApi = {
	plans: () => client.get<PlanCatalogResponse>('/plans'),
	currentPlan: () => client.get<CurrentPlan>('/account/plan'),
	myOrders: (limit = 20) =>
		client.get<PlanOrderListResponse>('/account/orders', { limit: String(limit) }),
	createOrder: (plan_id: string, note?: string) =>
		client.post<PlanOrder>(
			'/account/orders',
			{ plan_id, ...(note ? { note } : {}) },
			undefined,
			{ headers: { 'Idempotency-Key': idempotencyKey() } },
		),
	adminOrders: (status?: PlanOrder['status']) =>
		client.get<PlanOrderListResponse>('/admin/orders', {
			limit: '100',
			...(status ? { status } : {}),
		}),
	approveOrder: (orderId: string, admin_password: string, reason: string) =>
		client.post<PlanOrder>(
			`/admin/orders/${orderId}/approve`,
			{ admin_password, reason },
			undefined,
			{ headers: { 'Idempotency-Key': idempotencyKey() } },
		),
	rejectOrder: (orderId: string, admin_password: string, reason: string) =>
		client.post<PlanOrder>(
			`/admin/orders/${orderId}/reject`,
			{ admin_password, reason },
			undefined,
			{ headers: { 'Idempotency-Key': idempotencyKey() } },
		),
};

