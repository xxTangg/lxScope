import { releaseTypeFromCanonical, toCanonical } from '../contract.js';

export function maskToken(value: string): string {
  return value.length <= 8 ? '****' : `${value.slice(0, 4)}****${value.slice(-4)}`;
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function canonicalCustomer(value: unknown): Record<string, unknown> {
  const raw = asRecord(value) ?? {};
  const converted = (toCanonical(raw) ?? {}) as Record<string, unknown>;
  const lastReport = asRecord(raw.lastReport);
  const apiToken = typeof raw.apiToken === 'string' ? raw.apiToken : undefined;
  return {
    customer_id: converted.id ?? converted.customer_id,
    name: converted.name,
    system_id: converted.system_id ?? null,
    protocol: converted.protocol,
    base_url: converted.base_url ?? null,
    internal_base_url: converted.internal_base_url ?? null,
    configured_ip: converted.ip ?? null,
    port: converted.port,
    contact: converted.contact ?? '',
    notes: converted.notes ?? '',
    environment: converted.environment,
    status: converted.status,
    api_token_masked: apiToken ? maskToken(apiToken) : (converted.api_token_masked ?? null),
    last_report_at:
      (lastReport && toCanonical(lastReport.reportedAt, 'reported_at')) ??
      converted.last_report_at ??
      null,
    last_source_ip: converted.last_seen_ip ?? null,
    total_recharged: converted.total_recharged ?? '0.00',
    last_report: lastReport ? toCanonical(lastReport) : null,
    tier: converted.tier ?? null,
    online: converted.online ?? false,
    stale: converted.stale ?? true,
  };
}

export function canonicalStaff(value: unknown): Record<string, unknown> {
  const converted = (toCanonical(value) ?? {}) as Record<string, unknown>;
  return {
    staff_id: converted.id ?? converted.staff_id,
    username: converted.username,
    role: converted.role,
    status: converted.status,
    created_at: converted.created_at,
    updated_at: converted.updated_at,
  };
}

export function canonicalOrder(value: unknown): Record<string, unknown> {
  const converted = (toCanonical(value) ?? {}) as Record<string, unknown>;
  const { id, ...rest } = converted;
  return {
    ...rest,
    order_id: converted.order_id ?? id,
    delivery_status:
      converted.delivery_status ?? (converted.delivered === true ? 'delivered' : 'not_delivered'),
  };
}

export function isDirectCanonicalPath(pathname: string, method: string): boolean {
  return (
    (method === 'GET' &&
      (pathname === '/api/v1/auth/me' ||
        pathname === '/api/v1/integration/public-key' ||
        /^\/api\/v1\/customers\/[^/]+$/.test(pathname))) ||
    (method === 'POST' &&
      /^\/api\/v1\/customers\/[^/]+\/(api-token\/rotate|verify-connection)$/.test(pathname))
  );
}

export function canonicalTarget(url: string, method: string): string | null {
  const parsed = new URL(url, 'http://sales-console.local');
  const pathname = parsed.pathname;
  const search = parsed.search;
  const suffix = pathname.replace(/^\/api\/v1/, '');
  const integrationPrefix = '/api/v1/integration/';

  if (pathname === '/api/v1/settings' && method === 'PATCH') return `/api/settings${search}`;
  if (pathname === '/api/v1/auth/me') return `/api/auth/status${search}`;
  if (pathname === '/api/v1/audit/events') return `/api/operation-audit${search}`;
  if (pathname === '/api/v1/public-key') return `/api/public-key${search}`;

  const usageMatch = /^\/api\/v1\/customers\/([^/]+)\/usage-reports$/.exec(pathname);
  if (usageMatch) return `/api/customers/${usageMatch[1]}/usage${search}`;

  if (/^\/api\/v1\/customers\/[^/]+\/api-token$/.test(pathname)) return null;

  const releaseMatch = /^\/api\/v1\/releases\/(app|core)$/.exec(pathname);
  if (releaseMatch) {
    const type = releaseTypeFromCanonical(releaseMatch[1]);
    return type ? `/api/releases/${type}${search}` : null;
  }

  const integrationReleaseMatch = /^\/api\/v1\/integration\/releases\/(app|core)\/latest$/.exec(
    pathname,
  );
  if (integrationReleaseMatch) {
    const type = releaseTypeFromCanonical(integrationReleaseMatch[1]);
    return type ? `/api/customers/releases/${type}/latest${search}` : null;
  }

  if (pathname.startsWith(integrationPrefix)) {
    const integrationPath = pathname.slice(integrationPrefix.length);
    if (integrationPath === 'usage-reports') return `/api/reports${search}`;
    if (integrationPath === 'verify-connection') return `/api/verify-connection${search}`;
    if (integrationPath === 'recharge-codes/legacy') return `/api/recharge-codes/legacy${search}`;
    if (integrationPath === 'recharge-requests') return `/api/recharge-requests${search}`;
    if (integrationPath === 'recharge-requests/poll') return `/api/recharge-requests/poll${search}`;
    const ackMatch = /^recharge-requests\/([^/]+)\/ack$/.exec(integrationPath);
    if (ackMatch) return `/api/recharge-requests/${ackMatch[1]}/ack${search}`;
    return null;
  }

  return `/api${suffix}${search}`;
}

export function canonicalResponseFor(
  method: string,
  body: unknown,
  canonicalPath: string,
  id: string,
): unknown {
  const errorBody = asRecord(body);
  if (typeof errorBody?.error === 'string' && !errorBody.detail) {
    return {
      detail: {
        code: 'request_failed',
        message: errorBody.error,
      },
      request_id: id,
    };
  }
  if (errorBody?.detail) {
    return { ...(toCanonical(body) as Record<string, unknown>), request_id: id };
  }
  if (canonicalPath === '/api/v1/customers' && method === 'GET' && Array.isArray(body)) {
    return {
      customers: body.map(canonicalCustomer),
      total: body.length,
      request_id: id,
    };
  }
  if (
    canonicalPath === '/api/v1/customers' &&
    method === 'GET' &&
    asRecord(body) &&
    Array.isArray(asRecord(body)!.customers)
  ) {
    const raw = asRecord(body)!;
    return {
      ...raw,
      customers: (raw.customers as unknown[]).map(canonicalCustomer),
      request_id: id,
    };
  }
  if (canonicalPath === '/api/v1/customers' && method === 'POST') {
    const raw = asRecord(body) ?? {};
    const apiToken = typeof raw.apiToken === 'string' ? raw.apiToken : null;
    return {
      customer: canonicalCustomer(raw),
      api_token_once: apiToken,
      token_expires_at: null,
      request_id: id,
    };
  }
  if (canonicalPath === '/api/v1/dashboard') {
    const raw = asRecord(body);
    const converted = (toCanonical(body) ?? {}) as Record<string, unknown>;
    return {
      ...converted,
      customers: raw && Array.isArray(raw.customers) ? raw.customers.map(canonicalCustomer) : [],
      activities:
        raw && Array.isArray(raw.activities)
          ? raw.activities.map((item) => {
              const activity = (toCanonical(item) ?? {}) as Record<string, unknown>;
              const { id: activityID, ...rest } = activity;
              return { ...rest, activity_id: activity.activity_id ?? activityID };
            })
          : [],
      request_id: id,
    };
  }
  if (
    (canonicalPath === '/api/v1/auth/login' || canonicalPath === '/api/v1/auth/me') &&
    asRecord(body)?.staff
  ) {
    const raw = asRecord(body)!;
    return {
      ...(toCanonical(raw) as Record<string, unknown>),
      staff: canonicalStaff(raw.staff),
      request_id: id,
    };
  }
  if (canonicalPath === '/api/v1/integration/recharge-requests' && method === 'POST') {
    const converted = (toCanonical(body) ?? {}) as Record<string, unknown>;
    return {
      order_id: converted.id ?? converted.order_id,
      system_id: converted.system_id ?? converted.systemId,
      status: 'pending',
      delivery_status: 'not_delivered',
      request_id: id,
    };
  }
  if (canonicalPath === '/api/v1/integration/recharge-requests/poll') {
    const raw = asRecord(body) ?? {};
    const rawOrders = Array.isArray(raw.orders) ? raw.orders : [];
    return {
      orders: rawOrders.map((item) => {
        const order = canonicalOrder(item);
        return {
          order_id: order.order_id,
          system_id: order.system_id,
          amount: order.amount,
          tokens: order.tokens,
          status: order.status,
          delivery_status: order.delivery_status,
          recharge_code: order.code,
          expires_at: order.expires_at,
        };
      }),
      request_id: id,
    };
  }
  if (canonicalPath === '/api/v1/integration/recharge-codes/legacy') {
    return { ...(toCanonical(body) as Record<string, unknown>), request_id: id };
  }
  if (/^\/api\/v1\/integration\/recharge-requests\/[^/]+\/ack$/.test(canonicalPath)) {
    const match = /^\/api\/v1\/integration\/recharge-requests\/([^/]+)\/ack$/.exec(canonicalPath);
    const result = asRecord(body) ?? {};
    const status = result.status === 'rejected' ? 'rejected' : 'approved';
    return {
      order_id: match?.[1],
      status,
      delivery_status: status === 'approved' ? 'delivered' : 'not_delivered',
      request_id: id,
    };
  }
  if (canonicalPath === '/api/v1/integration/usage-reports') {
    const converted = (toCanonical(body) ?? {}) as Record<string, unknown>;
    return {
      report_id: converted.report_id ?? converted.id,
      system_id: converted.system_id,
      accepted: converted.accepted ?? true,
      reported_at: converted.reported_at,
      cumulative_consumed_delta: converted.cumulative_consumed_delta ?? 0,
      cumulative_credits_delta: converted.cumulative_credits_delta ?? 0,
      request_id: id,
    };
  }
  if (/^\/api\/v1\/customers\/[^/]+\/usage-reports$/.test(canonicalPath) && Array.isArray(body)) {
    return {
      reports: body.map((item) => toCanonical(item)),
      total: body.length,
      request_id: id,
    };
  }
  if (/^\/api\/v1\/customers\/[^/]+$/.test(canonicalPath) && method === 'PATCH') {
    return { customer: canonicalCustomer(body), request_id: id };
  }
  if (/^\/api\/v1\/customers\/[^/]+\/recharge-orders$/.test(canonicalPath) && Array.isArray(body)) {
    return {
      orders: body.map(canonicalOrder),
      total: body.length,
      request_id: id,
    };
  }
  if (/^\/api\/v1\/customers\/[^/]+\/recharge-codes$/.test(canonicalPath)) {
    const converted = (toCanonical(body) ?? {}) as Record<string, unknown>;
    return {
      ...converted,
      order: converted.order ? canonicalOrder((body as Record<string, unknown>).order) : undefined,
      request_id: id,
    };
  }
  if (/^\/api\/v1\/recharge-requests\/[^/]+\/(approve|reject)$/.test(canonicalPath)) {
    return { ...canonicalOrder(body), request_id: id };
  }
  if (canonicalPath === '/api/v1/recharge-requests' && Array.isArray(body)) {
    return {
      orders: body.map(canonicalOrder),
      total: body.length,
      request_id: id,
    };
  }
  if (canonicalPath === '/api/v1/reconciliation' && Array.isArray(body)) {
    return { items: body.map((item) => toCanonical(item)), total: body.length, request_id: id };
  }
  if (canonicalPath === '/api/v1/alerts' && Array.isArray(body)) {
    return { alerts: body.map((item) => toCanonical(item)), total: body.length, request_id: id };
  }
  if (canonicalPath === '/api/v1/audit/events' && Array.isArray(body)) {
    return { events: body.map((item) => toCanonical(item)), total: body.length, request_id: id };
  }
  if (canonicalPath === '/api/v1/releases') {
    const converted = (toCanonical(body) ?? {}) as Record<string, unknown>;
    const rawCore = asRecord(converted.core);
    const core = rawCore ? { ...rawCore, type: 'core' } : null;
    return {
      app: converted.app ?? null,
      core,
      request_id: id,
    };
  }
  if (/^\/api\/v1\/releases\/(app|core)$/.test(canonicalPath)) {
    const converted = (toCanonical(body) ?? {}) as Record<string, unknown>;
    return {
      ...converted,
      type: converted.type,
      request_id: id,
    };
  }
  if (/^\/api\/v1\/upgrade-all\/(app|core)$/.test(canonicalPath)) {
    const converted = (toCanonical(body) ?? {}) as Record<string, unknown>;
    return {
      ...converted,
      results: Array.isArray(converted.results)
        ? converted.results.map((item) => {
            const result = (item ?? {}) as Record<string, unknown>;
            const { id: resultID, ...rest } = result;
            return { ...rest, customer_id: result.customer_id ?? resultID };
          })
        : converted.results,
      request_id: id,
    };
  }
  const converted = toCanonical(body) as Record<string, unknown> | null;
  if (converted && typeof converted === 'object' && !Array.isArray(converted)) {
    if (canonicalPath.startsWith('/api/v1/customers/') && 'customer' in converted) {
      return {
        ...converted,
        customer: canonicalCustomer((body as Record<string, unknown>).customer),
        request_id: id,
      };
    }
    return { ...converted, request_id: converted.request_id ?? id };
  }
  return converted;
}
