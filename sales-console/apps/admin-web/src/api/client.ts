import type { Customer, ReleaseType } from '../types';

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function fromCanonical(value: unknown, key?: string): unknown {
  if (Array.isArray(value)) return value.map((item) => fromCanonical(item));
  if (!value || typeof value !== 'object') {
    if (
      typeof value === 'string' &&
      key &&
      /(?:^|_)(?:at|created_at|updated_at|reported_at|processed_at|delivered_at|last_delivery_at|uploaded_at|last_seen_at|last_report_at|issued_at|expires_at|checked_at)$/.test(
        key,
      )
    ) {
      const parsed = Date.parse(value);
      return Number.isNaN(parsed) ? value : parsed;
    }
    if (
      typeof value === 'string' &&
      [
        'amount',
        'requested_amount',
        'total_recharged',
        'recharged_amount',
        'recharged_30d',
      ].includes(key ?? '')
    ) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : value;
    }
    return value;
  }
  const converted = Object.fromEntries(
    Object.entries(value).map(([itemKey, item]) => {
      const camelKey = itemKey.replace(/_([a-z0-9])/gi, (_match, letter: string) =>
        letter.toUpperCase(),
      );
      return [camelKey, fromCanonical(item, itemKey)];
    }),
  );
  return converted;
}

export function fromCanonicalCustomer(value: unknown): Customer {
  const converted = fromCanonical(value);
  const customer = isRecord(converted) ? converted : {};
  return {
    ...customer,
    id: customer.customerId ?? customer.id,
    systemId: customer.systemId ?? '',
    ip: customer.configuredIp ?? customer.ip ?? '',
    baseUrl: customer.baseUrl ?? null,
    internalBaseUrl: customer.internalBaseUrl ?? null,
    lastSeenIP: customer.lastSourceIp ?? customer.lastSeenIP ?? null,
    lastSeenAt: customer.lastReportAt ?? customer.lastSeenAt ?? null,
    totalRecharged: Number(customer.totalRecharged ?? 0),
    apiToken: customer.apiToken,
  } as Customer;
}

export function canonicalReleaseType(type: ReleaseType): string {
  return type;
}

export function normalizeCanonicalResponse<T>(path: string, value: unknown): T {
  const valueFromServer = fromCanonical(value);
  if (!isRecord(valueFromServer)) return valueFromServer as T;
  const converted = valueFromServer;
  if (isRecord(converted.staff)) {
    converted.staff = { ...converted.staff, id: converted.staff.staffId ?? converted.staff.id };
  }
  if (path === '/api/v1/dashboard' && Array.isArray(converted.customers)) {
    const activities = Array.isArray(converted.activities) ? converted.activities : [];
    return {
      ...converted,
      customers: converted.customers.map(fromCanonicalCustomer),
      activities: activities.map((item) => {
        const activity = isRecord(item) ? item : {};
        return { ...activity, id: activity.activityId ?? activity.id };
      }),
    } as T;
  }
  if (path === '/api/v1/customers' && Array.isArray(converted.customers)) {
    return converted.customers.map(fromCanonicalCustomer) as T;
  }
  if (/^\/api\/v1\/customers\/[^/]+$/.test(path) && converted.customer) {
    return { ...converted, customer: fromCanonicalCustomer(converted.customer) } as T;
  }
  if (/^\/api\/v1\/customers\/[^/]+\/(?:usage-reports|recharge-orders)$/.test(path)) {
    if (Array.isArray(converted.reports)) return converted.reports as T;
    if (Array.isArray(converted.orders)) {
      return converted.orders.map(withOrderId) as T;
    }
  }
  if (path === '/api/v1/recharge-requests' && Array.isArray(converted.orders)) {
    return converted.orders.map(withOrderId) as T;
  }
  if (path === '/api/v1/reconciliation' && Array.isArray(converted.items))
    return converted.items as T;
  if (path === '/api/v1/alerts' && Array.isArray(converted.alerts)) return converted.alerts as T;
  if (path === '/api/v1/audit/events' && Array.isArray(converted.events))
    return converted.events as T;
  if (/^\/api\/v1\/upgrade-all\/(?:app|core)$/.test(path) && Array.isArray(converted.results)) {
    return {
      ...converted,
      results: converted.results.map((item) => {
        const result = isRecord(item) ? item : {};
        return { ...result, id: result.customerId ?? result.id };
      }),
    } as T;
  }
  if (path === '/api/v1/customers' && converted.customer) {
    return { ...converted, customer: fromCanonicalCustomer(converted.customer) } as T;
  }
  return converted as T;
}

function withOrderId(item: unknown): JsonRecord {
  const order = isRecord(item) ? item : {};
  return { ...order, id: order.orderId ?? order.id };
}

export async function api<T>(url: string, options: RequestInit = {}): Promise<T> {
  const path = url.split('?')[0];
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  if (!['GET', 'HEAD', 'OPTIONS'].includes(options.method?.toUpperCase() ?? 'GET')) {
    if (!['/api/v1/auth/login', '/api/v1/auth/logout'].includes(path)) {
      headers.set('Idempotency-Key', headers.get('Idempotency-Key') ?? crypto.randomUUID());
    }
  }
  headers.set('X-Request-ID', headers.get('X-Request-ID') ?? crypto.randomUUID());
  const response = await fetch(url, {
    ...options,
    headers,
    credentials: 'include',
    cache: 'no-store',
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      error?: string;
      detail?: { message?: string };
    };
    throw new Error(body.detail?.message ?? body.error ?? `请求失败（${response.status}）`);
  }
  return normalizeCanonicalResponse<T>(path, await response.json());
}
