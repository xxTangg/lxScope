import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';

import cors from 'cors';
import express, { type NextFunction, type Request, type Response } from 'express';
import multer from 'multer';
import * as tar from 'tar';
import type { ReadEntry } from 'tar';

import {
  customerUrl,
  enrichCustomer,
  isValidIP,
  measuredConsumption,
  safeMoney,
  signRechargeCode,
  tierFor,
} from './business.js';
import { fromCanonical, releaseTypeFromCanonical, toCanonical } from './contract.js';
import {
  appendAudit,
  DATA_DIR,
  db,
  dataStoreHealth,
  ensureData,
  getSigningKeyPair,
  readJson,
  RELEASE_DIR,
  serial,
  updateJson,
  writeJson,
} from './store.js';
import type { IdempotencyRecord } from './store.js';
import type { Customer, RechargeOrder, ReleaseMeta, Staff, UsageReport } from './types.js';

const APP_VERSION = '2.0.0';
const RELEASE_MAX_BYTES = 300 * 1024 * 1024;
const RECHARGE_CODE_TTL_MS = 60 * 60 * 1000;
type ReleaseType = 'app' | 'core';
const sessionStore = new Map<string, { staffID: string; expiresAt: number }>();
const loginAttempts = new Map<string, { at: number; count: number }>();
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: RELEASE_MAX_BYTES } });

function requestID(req: Request): string {
  const header = String(req.headers['x-request-id'] ?? '').trim();
  return header && header.length <= 128 ? header : crypto.randomUUID();
}

function optionalRequestID(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const id = value.trim();
  return id && id.length <= 128 ? id : undefined;
}

function requiredRequestID(req: Request): string {
  const id = String(req.headers['x-request-id'] ?? '').trim();
  if (!id || id.length > 128) {
    throw Object.assign(new Error('必须提供有效的 X-Request-ID'), { status: 400 });
  }
  return id;
}

function requiredText(value: unknown, field: string, maxLength = 128): string {
  const text = typeof value === 'string' ? value.trim() : '';
  if (!text || text.length > maxLength) {
    throw Object.assign(new Error(`${field} 参数不合法`), { status: 400 });
  }
  return text;
}

function requiredContractMoney(value: unknown, field: string, allowZero = false): number {
  const text = typeof value === 'string' ? value.trim() : '';
  if (!/^\d+\.\d{2}$/.test(text)) {
    throw Object.assign(new Error(`${field} 必须是两位小数字符串`), { status: 400 });
  }
  const amount = Number(text);
  if (
    !Number.isFinite(amount) ||
    amount < (allowZero ? 0 : Number.EPSILON) ||
    amount > 10_000_000 ||
    Math.abs(amount * 100 - Math.round(amount * 100)) > 0.00001
  ) {
    throw Object.assign(new Error(`${field} 参数不合法`), { status: 400 });
  }
  return amount;
}

function requiredContractAmount(value: unknown): number {
  return requiredContractMoney(value, 'amount');
}

function requiredISOTime(value: unknown, field: string): number {
  const text = typeof value === 'string' ? value.trim() : '';
  const time = text ? Date.parse(text) : Number.NaN;
  // Date.parse also accepts timezone-less strings; the integration contract
  // requires an explicit UTC offset or `Z` so both sides agree on the instant.
  if (!Number.isFinite(time) || !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(text)) {
    throw Object.assign(new Error(`${field} 必须是有效的 ISO 8601 时间`), { status: 400 });
  }
  return time;
}

function requiredSystemQuery(req: Request, customer: Customer): string {
  const value = req.query.system_id;
  if (typeof value !== 'string' || !value.trim()) {
    throw Object.assign(new Error('必须提供 system_id 查询参数'), { status: 400 });
  }
  const systemID = value.trim();
  if (!customer.systemId || systemID !== customer.systemId) {
    throw Object.assign(new Error('system_id 与客户令牌不匹配'), { status: 400 });
  }
  return systemID;
}

function maskToken(value: string): string {
  return value.length <= 8 ? '****' : `${value.slice(0, 4)}****${value.slice(-4)}`;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function canonicalCustomer(value: unknown): Record<string, unknown> {
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
    configured_ip: converted.ip ?? null,
    port: converted.port,
    contact: converted.contact ?? '',
    notes: converted.notes ?? '',
    environment: converted.environment,
    status: converted.status,
    api_token_masked: apiToken ? maskToken(apiToken) : converted.api_token_masked ?? null,
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

function canonicalStaff(value: unknown): Record<string, unknown> {
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

function canonicalOrder(value: unknown): Record<string, unknown> {
  const converted = (toCanonical(value) ?? {}) as Record<string, unknown>;
  const { id, ...rest } = converted;
  return {
    ...rest,
    order_id: converted.order_id ?? id,
    delivery_status:
      converted.delivery_status ??
      (converted.delivered === true ? 'delivered' : 'not_delivered'),
  };
}

function isDirectCanonicalPath(pathname: string, method: string): boolean {
  return (
    (method === 'GET' &&
      (pathname === '/api/v1/auth/me' ||
        pathname === '/api/v1/integration/public-key' ||
        /^\/api\/v1\/customers\/[^/]+$/.test(pathname))) ||
    (method === 'POST' &&
      (/^\/api\/v1\/customers\/[^/]+\/(api-token\/rotate|verify-connection)$/.test(pathname)))
  );
}

function canonicalTarget(url: string, method: string): string | null {
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

  const integrationReleaseMatch =
    /^\/api\/v1\/integration\/releases\/(app|core)\/latest$/.exec(pathname);
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

function canonicalResponseFor(
  req: Request,
  body: unknown,
  canonicalPath = req.path,
  id = requestID(req),
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
  if (canonicalPath === '/api/v1/customers' && req.method === 'GET' && Array.isArray(body)) {
    return {
      customers: body.map(canonicalCustomer),
      total: body.length,
      request_id: id,
    };
  }
  if (
    canonicalPath === '/api/v1/customers' &&
    req.method === 'GET' &&
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
  if (canonicalPath === '/api/v1/customers' && req.method === 'POST') {
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
  if (
    canonicalPath === '/api/v1/integration/recharge-requests' &&
    req.method === 'POST'
  ) {
    const converted = (toCanonical(body) ?? {}) as Record<string, unknown>;
    return {
      order_id: converted.id ?? converted.order_id,
      system_id: converted.system_id ?? converted.systemId,
      status: 'pending',
      delivery_status: 'not_delivered',
      request_id: id,
    };
  }
  if (errorBody?.detail) {
    return { ...(toCanonical(body) as Record<string, unknown>), request_id: id };
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
    return {
      order_id: match?.[1],
      status: 'approved',
      delivery_status: 'delivered',
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
  if (/^\/api\/v1\/customers\/[^/]+$/.test(canonicalPath) && req.method === 'PATCH') {
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

function requiresCanonicalIdempotency(method: string, pathName: string): boolean {
  if (method === 'GET') return pathName === '/api/v1/integration/recharge-requests/poll';
  if (['HEAD', 'OPTIONS'].includes(method)) return false;
  return !['/api/v1/auth/login', '/api/v1/auth/logout'].includes(pathName);
}

function idempotencyScope(req: Request, canonicalPath: string): string {
  const identity = crypto
    .createHash('sha256')
    .update(String(req.headers.authorization ?? req.headers.cookie ?? 'anonymous'))
    .digest('hex')
    .slice(0, 16);
  return `${req.method}:${canonicalPath}:${identity}`;
}

function requestHash(req: Request): string {
  return crypto
    .createHash('sha256')
    .update(JSON.stringify({ url: req.url, body: req.body ?? null }))
    .digest('hex');
}

function hashPassword(value: string, salt: string): string {
  return crypto.scryptSync(value, salt, 32).toString('hex');
}

function publicStaff(
  staff: Staff,
): Pick<Staff, 'id' | 'username' | 'role' | 'status' | 'createdAt' | 'updatedAt'> {
  const { passwordHash: _passwordHash, passwordSalt: _passwordSalt, ...safe } = staff;
  return safe;
}

function parseCookies(req: Request): Record<string, string> {
  return Object.fromEntries(
    String(req.headers.cookie ?? '')
      .split(';')
      .map((item) => item.trim().split('='))
      .filter(([name, value]) => name && value)
      .map(([name, ...value]) => [name, decodeURIComponent(value.join('='))]),
  );
}

function sessionCookie(token: string, maxAge: number, secureRequest = false): string {
  const secure = secureRequest ? '; Secure' : '';
  return `sh_session=${encodeURIComponent(token)}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${maxAge}${secure}`;
}

function isSecureRequest(req: Request): boolean {
  return req.secure || String(req.headers['x-forwarded-proto'] ?? '').toLowerCase() === 'https';
}

async function currentStaff(req: Request): Promise<Staff | null> {
  const token = parseCookies(req).sh_session;
  const session = token ? sessionStore.get(token) : undefined;
  if (!session) return null;
  if (session.expiresAt < Date.now()) {
    sessionStore.delete(token);
    return null;
  }
  const staff = (await db.staff()).find(
    (item) => item.id === session.staffID && item.status === 'active',
  );
  if (!staff) {
    sessionStore.delete(token);
    return null;
  }
  session.expiresAt = Date.now() + 12 * 60 * 60 * 1000;
  return staff;
}

async function bootstrapStaff(): Promise<void> {
  const staff = await db.staff();
  if (staff.length) return;
  const password = process.env.SALES_ADMIN_PASSWORD;
  if (process.env.NODE_ENV === 'production' && !password) {
    throw new Error('生产环境必须设置 SALES_ADMIN_PASSWORD');
  }
  if (!password)
    console.warn('SALES_ADMIN_PASSWORD 未设置，开发环境暂使用 change-me；生产环境必须设置。');
  const salt = crypto.randomBytes(16).toString('hex');
  const now = Date.now();
  await writeJson(db.files.staff, [
    {
      id: crypto.randomUUID(),
      username: process.env.SALES_ADMIN_USERNAME ?? 'admin',
      passwordSalt: salt,
      passwordHash: hashPassword(password ?? 'change-me', salt),
      role: 'admin',
      status: 'active',
      createdAt: now,
      updatedAt: now,
    } satisfies Staff,
  ]);
}

async function customerFromBearer(req: Request): Promise<Customer | null> {
  const match = /^Bearer\s+(.+)$/i.exec(String(req.headers.authorization ?? ''));
  if (!match) return null;
  const given = Buffer.from(match[1].trim());
  const customers = await db.customers();
  return (
    customers.find((customer) => {
      if (customer.status !== 'active') return false;
      const stored = Buffer.from(customer.apiToken);
      return stored.length === given.length && crypto.timingSafeEqual(stored, given);
    }) ?? null
  );
}

function requireStaff(req: Request, res: Response, next: NextFunction): void {
  void currentStaff(req)
    .then((staff) => {
      if (!staff) {
        res.status(401).json({ error: '请先登录' });
        return;
      }
      req.staff = staff;
      next();
    })
    .catch(next);
}

async function audit(staff: Staff, req: Request): Promise<void> {
  await appendAudit({
    actor: staff.username,
    method: req.method,
    path: req.path,
    sourceIP: req.socket.remoteAddress ?? null,
  });
}

function validateAmount(value: unknown): number {
  const amount = safeMoney(value);
  if (amount === null) throw Object.assign(new Error('金额不合法'), { status: 400 });
  return amount;
}

type CustomerCallOptions = {
  requestID?: string;
  idempotencyKey?: string;
};

function customerErrorMessage(payload: Record<string, unknown>, status: number): string {
  const detail = payload.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        const record = asRecord(item);
        return typeof record?.msg === 'string' ? record.msg : null;
      })
      .filter((item): item is string => Boolean(item));
    if (messages.length) return messages.join('; ');
  }
  const detailRecord = asRecord(detail);
  if (typeof detailRecord?.message === 'string' && detailRecord.message.trim()) {
    return detailRecord.message;
  }
  return String(payload.error ?? `客户系统返回 HTTP ${status}`);
}

async function callCustomer(
  customer: Customer,
  endpoint: string,
  body: unknown,
  options: CustomerCallOptions = {},
): Promise<Record<string, unknown>> {
  const base = customerUrl(customer);
  if (!base) throw new Error('客户未配置可连接地址');
  const requestIDValue = options.requestID ?? crypto.randomUUID();
  const headers: Record<string, string> = {
    Authorization: `Bearer ${customer.apiToken}`,
    'Content-Type': 'application/json',
    'X-Request-ID': requestIDValue,
  };
  if (options.idempotencyKey) headers['Idempotency-Key'] = options.idempotencyKey;
  const response = await fetch(`${base}${endpoint}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(toCanonical(body)),
    signal: AbortSignal.timeout(120_000),
  });
  const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (!response.ok)
    throw new Error(customerErrorMessage(payload, response.status));
  return fromCanonical(payload) as Record<string, unknown>;
}

async function validateReleaseArchive(
  file: string,
  type: ReleaseType,
  version: string,
  manifestType: string = type,
): Promise<void> {
  const names = new Set<string>();
  let count = 0;
  await tar.t({
    file,
    strict: true,
    onentry: (entry: ReadEntry) => {
      count += 1;
      if (count > 20_000) throw new Error('升级包文件数量过多');
      const normalized = path.posix.normalize(entry.path);
      if (entry.path.startsWith('/') || normalized === '..' || normalized.startsWith('../')) {
        throw new Error('升级包包含不安全路径');
      }
      names.add(normalized);
    },
  });
  if (!names.has('manifest.json')) throw new Error('升级包缺少 manifest.json');
  const staging = path.join(DATA_DIR, `.release-check-${crypto.randomUUID()}`);
  await fs.mkdir(staging, { recursive: true, mode: 0o700 });
  try {
    await tar.x({ file, cwd: staging, strict: true });
    const manifest = JSON.parse(await fs.readFile(path.join(staging, 'manifest.json'), 'utf8')) as {
      type?: string;
      version?: string;
    };
    if (manifest.type !== manifestType || manifest.version !== version)
      throw new Error('manifest 类型或版本与上传信息不一致');
    const required =
      type === 'app' ? ['server.js', 'public/index.html'] : ['agentscope/__init__.py'];
    for (const item of required) {
      try {
        await fs.access(path.join(staging, item));
      } catch {
        throw new Error(`AgentScope 升级包缺少必要文件：${item}`);
      }
    }
  } finally {
    await fs.rm(staging, { recursive: true, force: true });
  }
}

async function dashboard() {
  const [customers, orders] = await Promise.all([db.customers(), db.orders()]);
  const now = Date.now();
  const since = now - 30 * 24 * 60 * 60 * 1000;
  const approved = orders.filter(
    (order) => order.status === 'issued' || order.status === 'approved',
  );
  const enriched = customers.map(enrichCustomer);
  const days = Array.from({ length: 30 }, (_, index) => {
    const start = new Date(now);
    start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - 29 + index);
    const from = start.getTime();
    const to = from + 24 * 60 * 60 * 1000;
    return {
      at: from,
      amount: approved
        .filter(
          (order) =>
            (order.processedAt ?? order.createdAt) >= from &&
            (order.processedAt ?? order.createdAt) < to,
        )
        .reduce((sum, order) => sum + (order.amount ?? 0), 0),
    };
  });
  return {
    generatedAt: now,
    totalCustomers: customers.length,
    online: enriched.filter((customer) => customer.online).length,
    pending: orders.filter((order) => order.status === 'pending').length,
    awaitingDelivery: orders.filter((order) => order.status === 'approved' && !order.delivered)
      .length,
    totalRecharged: approved.reduce((sum, order) => sum + (order.amount ?? 0), 0),
    recharged30d: approved
      .filter((order) => (order.processedAt ?? order.createdAt) >= since)
      .reduce((sum, order) => sum + (order.amount ?? 0), 0),
    poolRemaining: customers.reduce(
      (sum, customer) => sum + (customer.lastReport?.poolTokens ?? 0),
      0,
    ),
    days,
    customers: enriched,
    activities: orders.slice(0, 8).map((order) => ({
      id: order.id,
      name: customers.find((customer) => customer.id === order.customerID)?.name ?? '已删除客户',
      amount: order.amount ?? order.requestedAmount ?? 0,
      status: order.status,
      delivered: Boolean(order.delivered),
      at: order.processedAt ?? order.createdAt,
    })),
  };
}

export async function createApp(): Promise<express.Express> {
  await ensureData();
  await bootstrapStaff();
  const app = express();
  app.disable('x-powered-by');
  const webOrigin = process.env.WEB_ORIGIN;
  if (process.env.NODE_ENV === 'production' && !webOrigin) {
    throw new Error('生产环境必须设置 WEB_ORIGIN');
  }
  app.use(cors({ origin: webOrigin ?? true, credentials: true }));
  app.use(express.json({ limit: '1mb' }));
  app.use((_req, res, next) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('X-Frame-Options', 'DENY');
    res.setHeader('Referrer-Policy', 'no-referrer');
    next();
  });

  app.get('/health', async (_req, res, next) => {
    try {
      res.json({
        ok: true,
        service: 'sales-api',
        version: APP_VERSION,
        dataStore: await dataStoreHealth(),
      });
    } catch (error) {
      next(error);
    }
  });

  app.use((req, res, next) => {
    if (!req.url.startsWith('/api/v1')) {
      next();
      return;
    }
    const canonicalPath = new URL(req.url, 'http://sales-console.local').pathname;
    const target = canonicalTarget(req.url, req.method);
    if (!target && !isDirectCanonicalPath(canonicalPath, req.method)) {
      next();
      return;
    }
    const id = requestID(req);
    res.setHeader('X-Request-ID', id);
    const originalJson = res.json.bind(res);
    let responseBody: unknown;
    res.json = ((body: unknown) => {
      responseBody = canonicalResponseFor(req, body, canonicalPath, id);
      return originalJson(responseBody);
    }) as typeof res.json;

    const idempotencyKey = String(req.headers['idempotency-key'] ?? '').trim();
    const needsIdempotency = requiresCanonicalIdempotency(req.method, canonicalPath);
    void (async () => {
      if (needsIdempotency) {
        if (!idempotencyKey || idempotencyKey.length > 128) {
          res.status(400).json({
            detail: {
              code: 'idempotency_key_required',
              message: '状态变更请求必须提供有效的 Idempotency-Key',
            },
          });
          return;
        }
        const scope = idempotencyScope(req, canonicalPath);
        const hash = requestHash(req);
        let previous: IdempotencyRecord | undefined;
        await updateJson(db.files.idempotency, [], (items: IdempotencyRecord[]) => {
          previous = items.find((item) => item.scope === scope && item.key === idempotencyKey);
          if (previous && previous.expiresAt > Date.now()) return items;
          const now = Date.now();
          return [
            {
              scope,
              key: idempotencyKey,
              requestHash: hash,
              statusCode: 0,
              responseBody: null,
              createdAt: now,
              expiresAt: now + 24 * 60 * 60 * 1000,
            },
            ...items.filter((item) => item.expiresAt > now),
          ].slice(0, 10_000);
        });
        if (previous && previous.expiresAt > Date.now()) {
          if (previous.requestHash !== hash) {
            res.status(409).json({
              detail: {
                code: 'idempotency_key_reused',
                message: '同一幂等键不能对应不同请求内容',
              },
            });
            return;
          }
          if (previous.statusCode === 0) {
            res.status(409).json({
              detail: {
                code: 'idempotency_in_progress',
                message: '相同幂等请求正在处理中，请稍后查询或重试',
              },
            });
            return;
          }
          res.status(previous.statusCode);
          const replayBody = asRecord(previous.responseBody);
          originalJson(
            replayBody ? { ...replayBody, request_id: id } : previous.responseBody,
          );
          return;
        }
        res.once('finish', () => {
          if (responseBody === undefined || res.statusCode >= 500) return;
          void updateJson(db.files.idempotency, [], (items: IdempotencyRecord[]) =>
            items.map((item) =>
              item.scope === scope && item.key === idempotencyKey
                ? { ...item, statusCode: res.statusCode, responseBody }
                : item,
            ),
          );
        });
      }
      req.body = fromCanonical(req.body);
      req.headers['x-canonical-api'] = '1';
      const canonicalRelease = /^\/api\/v1\/releases\/(app|core)$/.exec(canonicalPath);
      if (canonicalRelease) {
        req.headers['x-contract-artifact-type'] = canonicalRelease[1];
      }
      if (!isDirectCanonicalPath(canonicalPath, req.method)) {
        if (req.method === 'PATCH' && canonicalPath === '/api/v1/settings') req.method = 'PUT';
        req.url = target!;
      }
      next();
    })().catch(next);
  });

  app.get('/api/v1/auth/me', async (req, res, next) => {
    try {
      const staff = await currentStaff(req);
      if (!staff) {
        res.status(401).json({ detail: { code: 'unauthorized', message: '请先登录' } });
        return;
      }
      res.json({ staff: publicStaff(staff), requestId: crypto.randomUUID() });
    } catch (error) {
      next(error);
    }
  });

  app.get('/api/v1/integration/public-key', async (req, res, next) => {
    try {
      const customer = await customerFromBearer(req);
      if (!customer) {
        res.status(401).json({ detail: { code: 'invalid_customer_token', message: '客户 Token 无效' } });
        return;
      }
      const { publicKey } = await getSigningKeyPair();
      const publicKeyRequestID =
        req.headers['x-canonical-api'] === '1' ? requiredRequestID(req) : requestID(req);
      res.json({
        publicKeyPem: publicKey.export({ type: 'spki', format: 'pem' }).toString(),
        systemId: customer.systemId,
        requestId: publicKeyRequestID,
      });
    } catch (error) {
      next(error);
    }
  });

  app.get('/api/v1/customers/:id', requireStaff, async (req, res, next) => {
    try {
      const customer = (await db.customers()).find((item) => item.id === req.params.id);
      if (!customer) {
        res.status(404).json({ detail: { code: 'customer_not_found', message: '客户不存在' } });
        return;
      }
      res.json({ customer: enrichCustomer(customer), requestId: crypto.randomUUID() });
    } catch (error) {
      next(error);
    }
  });

  app.post('/api/v1/customers/:id/api-token/rotate', requireStaff, async (req, res, next) => {
    try {
      const apiToken = crypto.randomBytes(24).toString('hex');
      let rotated: Customer | undefined;
      await updateJson(db.files.customers, [], (items: Customer[]) =>
        items.map((item) => {
          if (item.id !== req.params.id) return item;
          rotated = { ...item, apiToken, updatedAt: Date.now() };
          return rotated;
        }),
      );
      if (!rotated) {
        res.status(404).json({ detail: { code: 'customer_not_found', message: '客户不存在' } });
        return;
      }
      await audit(req.staff!, req);
      res.json({
        customer: {
          customerId: rotated.id,
          systemId: rotated.systemId,
          name: rotated.name,
          status: rotated.status,
        },
        apiTokenOnce: apiToken,
        tokenExpiresAt: null,
        requestId: crypto.randomUUID(),
      });
    } catch (error) {
      next(error);
    }
  });

  app.post('/api/v1/customers/:id/verify-connection', requireStaff, async (req, res, next) => {
    try {
      // The canonical contract requires one request ID to be propagated through
      // the hub -> customer ping and echoed by the hub response.
      const verifyRequestID = requiredRequestID(req);
      const customer = (await db.customers()).find((item) => item.id === req.params.id);
      if (!customer) {
        res.status(404).json({ detail: { code: 'customer_not_found', message: '客户不存在' } });
        return;
      }
      if (!customerUrl(customer)) {
        res.json({
          customerId: customer.id,
          systemId: customer.systemId,
          outbound: false,
          inbound: false,
          inboundError: '客户尚未配置可连接地址',
          checkedAt: Date.now(),
          requestId: verifyRequestID,
        });
        return;
      }
      try {
        const ping = await callCustomer(customer, '/integration/sales/v1/ping', {}, {
          requestID: verifyRequestID,
        });
        res.json({
          customerId: customer.id,
          systemId: customer.systemId,
          outbound: true,
          inbound: true,
          systemName: customer.name,
          ping,
          checkedAt: Date.now(),
          requestId: verifyRequestID,
        });
      } catch (error) {
        res.json({
          customerId: customer.id,
          systemId: customer.systemId,
          outbound: true,
          inbound: false,
          inboundError: error instanceof Error ? error.message : '连接失败',
          checkedAt: Date.now(),
          requestId: verifyRequestID,
        });
      }
    } catch (error) {
      next(error);
    }
  });

  app.post('/api/auth/login', async (req, res, next) => {
    try {
      const ip = req.socket.remoteAddress ?? 'unknown';
      const attempt = loginAttempts.get(ip);
      if (attempt && attempt.at > Date.now() - 60_000 && attempt.count >= 12) {
        res.status(429).json({ error: '登录尝试过多，请一分钟后重试' });
        return;
      }
      const staff = (await db.staff()).find(
        (item) =>
          item.username.toLowerCase() ===
          String(req.body.username ?? '')
            .trim()
            .toLowerCase(),
      );
      const valid = Boolean(
        staff &&
        hashPassword(String(req.body.password ?? ''), staff.passwordSalt) === staff.passwordHash,
      );
      loginAttempts.set(ip, {
        at: Date.now(),
        count: attempt && attempt.at > Date.now() - 60_000 ? attempt.count + 1 : 1,
      });
      if (!staff || !valid || staff.status !== 'active') {
        res.status(401).json({ error: '用户名或密码错误' });
        return;
      }
      const token = crypto.randomBytes(32).toString('base64url');
      sessionStore.set(token, { staffID: staff.id, expiresAt: Date.now() + 12 * 60 * 60 * 1000 });
      res.setHeader('Set-Cookie', sessionCookie(token, 12 * 60 * 60, isSecureRequest(req)));
      res.json({ ok: true, staff: publicStaff(staff) });
    } catch (error) {
      next(error);
    }
  });

  app.post('/api/auth/logout', (req, res) => {
    const token = parseCookies(req).sh_session;
    if (token) sessionStore.delete(token);
    res.setHeader('Set-Cookie', sessionCookie('', 0, isSecureRequest(req)));
    res.json({ ok: true });
  });

  app.get('/api/auth/status', async (req, res, next) => {
    try {
      const staff = await currentStaff(req);
      res.json({
        authenticated: Boolean(staff),
        staff: staff ? publicStaff(staff) : null,
        appVersion: APP_VERSION,
      });
    } catch (error) {
      next(error);
    }
  });

  // 客户系统专用接口：只接受客户 API Token，不接受销售人员 Cookie。
  app.post('/api/recharge-requests', async (req, res, next) => {
    try {
      const customer = await customerFromBearer(req);
      if (!customer) {
        res.status(401).json({ error: '无效的客户访问令牌' });
        return;
      }
      const strictContract = req.headers['x-canonical-api'] === '1';
      const requestIDValue = strictContract ? requiredRequestID(req) : requestID(req);
      const requestedSystemId = strictContract
        ? requiredText(req.body.systemId, 'system_id')
        : String(req.body.systemId ?? '').trim();
      if (
        (strictContract && (!customer.systemId || requestedSystemId !== customer.systemId)) ||
        (!strictContract &&
          requestedSystemId &&
          customer.systemId &&
          requestedSystemId !== customer.systemId)
      ) {
        res.status(400).json({ error: '系统 ID 与客户令牌不匹配' });
        return;
      }
      const amount = strictContract ? requiredContractAmount(req.body.amount) : validateAmount(req.body.amount);
      const note = strictContract
        ? requiredText(req.body.note, 'note', 500)
        : String(req.body.note ?? '').slice(0, 500);
      const requestedAt =
        strictContract || req.body.requestedAt !== undefined
          ? requiredISOTime(req.body.requestedAt, 'requested_at')
          : undefined;
      if (req.body.requestID !== undefined && req.body.requestID !== requestIDValue) {
        res.status(400).json({ error: 'request_id 必须与 X-Request-ID 一致' });
        return;
      }
      const order: RechargeOrder = {
        id: crypto.randomUUID(),
        requestID: requestIDValue,
        customerID: customer.id,
        method: 'online',
        status: 'pending',
        requestedAt,
        requestedAmount: amount,
        note,
        createdAt: Date.now(),
      };
      await updateJson(db.files.orders, [], (items: RechargeOrder[]) => [order, ...items]);
      res.status(201).json({ ok: true, id: order.id, systemId: customer.systemId });
    } catch (error) {
      next(error);
    }
  });

  app.get('/api/recharge-requests/poll', async (req, res, next) => {
    try {
      const customer = await customerFromBearer(req);
      if (!customer) {
        res.status(401).json({ error: '无效的客户访问令牌' });
        return;
      }
      const strictContract = req.headers['x-canonical-api'] === '1';
      if (strictContract) {
        requiredRequestID(req);
        requiredSystemQuery(req, customer);
      }
      const ready = await serial(async () => {
        const items = await db.orders();
        const now = Date.now();
        const found = strictContract
          ? items.filter(
              (item) =>
                item.customerID === customer.id &&
                item.status === 'approved' &&
                !item.delivered &&
                Boolean(
                  item.code &&
                    item.amount &&
                    item.tokens &&
                    item.expiresAt &&
                    item.expiresAt > now,
                ),
            )
          : items.filter(
              (item) =>
                item.customerID === customer.id && item.status === 'approved' && !item.delivered,
            ).slice(0, 1);
        if (found.length) {
          const now = Date.now();
          for (const item of found) {
            item.deliveryAttempts = (item.deliveryAttempts ?? 0) + 1;
            item.lastDeliveryAt = now;
          }
          await writeJson(db.files.orders, items);
        }
        return found;
      });
      if (!strictContract) {
        const first = ready[0];
        res.json(first ? { code: first.code, id: first.id } : {});
        return;
      }
      res.json({
        orders: ready.map((order) => ({
          id: order.id,
          systemId: customer.systemId,
          amount: order.amount,
          tokens: order.tokens,
          status: order.status,
          deliveryStatus: order.delivered ? 'delivered' : 'not_delivered',
          code: order.code,
          expiresAt: order.expiresAt,
        })),
      });
    } catch (error) {
      next(error);
    }
  });

  // 客户系统同步历史离线充值码的 nonce，避免重复入账。
  app.get('/api/recharge-codes/legacy', async (req, res, next) => {
    try {
      const customer = await customerFromBearer(req);
      if (!customer) {
        res.status(401).json({ error: '无效的客户访问令牌' });
        return;
      }
      const orders = await db.orders();
      const nonces: string[] = [];
      for (const order of orders.filter(
        (item) =>
          item.customerID === customer.id &&
          ['issued', 'approved'].includes(item.status) &&
          item.code,
      )) {
        try {
          const codeParts = String(order.code).split('.');
          const payloadPart = codeParts[0] === 'LXRC2' ? codeParts[1] : codeParts[0];
          const payload = JSON.parse(
            Buffer.from(String(payloadPart), 'base64url').toString(),
          ) as { systemId?: string; system_id?: string; nonce?: string };
          if (!payload.systemId && !payload.system_id && typeof payload.nonce === 'string')
            nonces.push(payload.nonce);
        } catch {
          /* 忽略不符合历史格式的充值码 */
        }
      }
      res.json({ systemId: customer.systemId, nonces });
    } catch (error) {
      next(error);
    }
  });

  app.post('/api/recharge-requests/:id/ack', async (req, res, next) => {
    try {
      const customer = await customerFromBearer(req);
      if (!customer) {
        res.status(401).json({ error: '无效的客户访问令牌' });
        return;
      }
      const strictContract = req.headers['x-canonical-api'] === '1';
      if (strictContract) requiredRequestID(req);
      const operationID = strictContract
        ? requiredText(req.body.operationID, 'operation_id')
        : String(req.body.operationID ?? '').trim();
      const systemID = strictContract
        ? requiredText(req.body.systemId, 'system_id')
        : String(req.body.systemId ?? '').trim();
      const redemptionOperationID = strictContract
        ? requiredText(req.body.redemptionOperationID, 'redemption_operation_id')
        : String(req.body.redemptionOperationID ?? '').trim();
      const ledgerID = strictContract
        ? requiredText(req.body.ledgerID, 'ledger_id')
        : String(req.body.ledgerID ?? '').trim();
      if (
        (strictContract && (!customer.systemId || systemID !== customer.systemId)) ||
        (!strictContract && systemID && customer.systemId && systemID !== customer.systemId)
      ) {
        res.status(400).json({ error: 'system_id 与客户令牌不匹配' });
        return;
      }
      let found = false;
      await updateJson(db.files.orders, [], (items: RechargeOrder[]) =>
        items.map((item) => {
          if (
            item.id === req.params.id &&
            item.customerID === customer.id &&
            item.status === 'approved'
          ) {
            found = true;
            return {
              ...item,
              delivered: true,
              deliveredAt: item.deliveredAt ?? Date.now(),
              ackOperationID: (item.ackOperationID ?? operationID) || undefined,
              redemptionOperationID: (item.redemptionOperationID ?? redemptionOperationID) || undefined,
              ledgerID: (item.ledgerID ?? ledgerID) || undefined,
            };
          }
          return item;
        }),
      );
      if (!found) {
        res.status(404).json({ error: '充值订单不存在' });
        return;
      }
      res.json({ ok: true });
    } catch (error) {
      next(error);
    }
  });

  app.post('/api/reports', async (req, res, next) => {
    try {
      const customer = await customerFromBearer(req);
      if (!customer) {
        res.status(401).json({ error: '无效的客户访问令牌' });
        return;
      }
      const strictContract = req.headers['x-canonical-api'] === '1';
      const reportRequestID = strictContract ? requiredRequestID(req) : requestID(req);
      const systemID = strictContract
        ? requiredText(req.body.systemId, 'system_id')
        : String(req.body.systemId ?? '').trim();
      const poolTokens = Number(req.body.poolTokens);
      const totalRecharged = strictContract
        ? requiredContractMoney(req.body.totalRecharged, 'total_recharged', true)
        : Number(req.body.totalRecharged);
      const appVersion = strictContract
        ? requiredText(req.body.appVersion, 'app_version')
        : String(req.body.appVersion ?? '');
      const clientReportedAt =
        strictContract || req.body.clientReportedAt !== undefined
          ? requiredISOTime(req.body.clientReportedAt, 'client_reported_at')
          : undefined;
      if (
        !Number.isSafeInteger(poolTokens) ||
        poolTokens < 0 ||
        !Number.isFinite(totalRecharged) ||
        totalRecharged < 0 ||
        (strictContract && (!customer.systemId || systemID !== customer.systemId)) ||
        (!strictContract && systemID && customer.systemId && customer.systemId !== systemID)
      ) {
        res.status(400).json({ error: '参数不合法' });
        return;
      }
      const report: UsageReport = {
        id: strictContract ? crypto.randomUUID() : undefined,
        customerID: customer.id,
        systemId: strictContract ? customer.systemId : systemID,
        poolTokens,
        totalRecharged,
        cumulativeConsumed:
          Number.isSafeInteger(req.body.cumulativeConsumed) && req.body.cumulativeConsumed >= 0
            ? Number(req.body.cumulativeConsumed)
            : null,
        cumulativeCredits:
          Number.isSafeInteger(req.body.cumulativeCredits) && req.body.cumulativeCredits >= 0
            ? Number(req.body.cumulativeCredits)
            : null,
        appVersion,
        clientReportedAt,
        reportedAt: Date.now(),
        observedIP: req.socket.remoteAddress ?? undefined,
      };
      const result = await serial(async () => {
        const reports = await db.reports();
        const previous = reports.find((item) => item.customerID === customer.id);
        const customers = await db.customers();
        const target = customers.find((item) => item.id === customer.id);
        if (target) {
          target.lastReport = report;
          if (!target.systemId && report.systemId) target.systemId = report.systemId;
          target.lastSeenIP = report.observedIP ?? null;
          target.lastSeenAt = report.reportedAt;
          target.updatedAt = Date.now();
        }
        await writeJson(db.files.reports, [report, ...reports].slice(0, 20_000));
        await writeJson(db.files.customers, customers);
        const previousConsumed = previous?.cumulativeConsumed ?? null;
        const previousCredits = previous?.cumulativeCredits ?? null;
        return {
          reportId: report.id,
          systemId: report.systemId,
          accepted: true,
          reportedAt: report.reportedAt,
          cumulativeConsumedDelta:
            report.cumulativeConsumed !== null && previousConsumed !== null
              ? report.cumulativeConsumed - previousConsumed
              : 0,
          cumulativeCreditsDelta:
            report.cumulativeCredits !== null && previousCredits !== null
              ? report.cumulativeCredits - previousCredits
              : 0,
        };
      });
      if (strictContract) {
        res.json({ ...result, requestId: reportRequestID });
      } else {
        res.json({ ok: true });
      }
    } catch (error) {
      next(error);
    }
  });

  app.get('/api/customers/releases/:type/latest', async (req, res, next) => {
    try {
      const customer = await customerFromBearer(req);
      if (!customer) {
        res.status(401).json({ error: '无效的客户访问令牌' });
        return;
      }
      const type = releaseTypeFromCanonical(req.params.type);
      if (!type) {
        res.status(404).json({ error: '升级包类型不存在' });
        return;
      }
      const meta = await dbRelease(type);
      if (!meta) {
        res.status(404).json({ error: '总部还没有发布过这个类型的升级包' });
        return;
      }
      res.sendFile(path.join(RELEASE_DIR, meta.file));
    } catch (error) {
      next(error);
    }
  });

  app.post('/api/verify-connection', async (req, res, next) => {
    try {
      const customer = await customerFromBearer(req);
      if (!customer) {
        res.status(401).json({ error: '无效的客户访问令牌' });
        return;
      }
      const outboundRequestID =
        req.headers['x-canonical-api'] === '1' ? requiredRequestID(req) : requestID(req);
      if (!customerUrl(customer)) {
        res.json({ outbound: true, inbound: false, inboundError: '客户尚未配置 IP' });
        return;
      }
      try {
        const ping = await callCustomer(
          customer,
          '/integration/sales/v1/ping',
          {},
          { requestID: outboundRequestID },
        );
        res.json({ outbound: true, inbound: true, systemName: customer.name, ping });
      } catch (error) {
        res.json({
          outbound: true,
          inbound: false,
          inboundError: error instanceof Error ? error.message : '连接失败',
        });
      }
    } catch (error) {
      next(error);
    }
  });

  app.use('/api', requireStaff);

  app.get('/api/dashboard', async (_req, res, next) => {
    try {
      res.json(await dashboard());
    } catch (error) {
      next(error);
    }
  });
  app.get('/api/public-key', async (_req, res, next) => {
    try {
      const { publicKey } = await getSigningKeyPair();
      res.json({ publicKeyPem: publicKey.export({ type: 'spki', format: 'pem' }).toString() });
    } catch (error) {
      next(error);
    }
  });
  app.get('/api/settings', async (_req, res, next) => {
    try {
      res.json(await db.settings());
    } catch (error) {
      next(error);
    }
  });
  app.put('/api/settings', async (req, res, next) => {
    try {
      const rate = Number(req.body.tokenExchangeRate);
      if (!Number.isFinite(rate) || rate <= 0 || rate > 100_000_000) {
        res.status(400).json({ error: '汇率须为大于 0 的数字' });
        return;
      }
      const value = { tokenExchangeRate: rate };
      await writeJson(db.files.settings, value);
      await audit(req.staff!, req);
      res.json(value);
    } catch (error) {
      next(error);
    }
  });
  app.patch('/api/staff/password', async (req, res, next) => {
    try {
      const current = String(req.body.currentPassword ?? '');
      const nextPassword = String(req.body.newPassword ?? '');
      if (
        nextPassword.length < 8 ||
        !/[A-Za-z]/.test(nextPassword) ||
        !/[0-9]/.test(nextPassword)
      ) {
        res.status(400).json({ error: '新密码至少 8 位，且必须包含字母和数字' });
        return;
      }
      const staffID = req.staff!.id;
      await updateJson(db.files.staff, [], (items: Staff[]) =>
        items.map((item) => {
          if (item.id !== staffID || hashPassword(current, item.passwordSalt) !== item.passwordHash)
            throw Object.assign(new Error('当前密码不正确'), { status: 400 });
          const salt = crypto.randomBytes(16).toString('hex');
          return {
            ...item,
            passwordSalt: salt,
            passwordHash: hashPassword(nextPassword, salt),
            updatedAt: Date.now(),
          };
        }),
      );
      await audit(req.staff!, req);
      res.json({ ok: true });
    } catch (error) {
      next(error);
    }
  });

  app.get('/api/customers', async (_req, res, next) => {
    try {
      const query = _req.query;
      const keyword = String(query.keyword ?? '').trim().toLowerCase();
      const status = String(query.status ?? '').trim();
      const environment = String(query.environment ?? '').trim();
      const filtered = (await db.customers()).filter((customer) => {
        if (keyword && !`${customer.name} ${customer.systemId} ${customer.contact}`.toLowerCase().includes(keyword)) {
          return false;
        }
        if (status && customer.status !== status) return false;
        if (environment && customer.environment !== environment) return false;
        return true;
      });
      const page = Math.max(1, Number(query.page) || 1);
      const pageSize = Math.min(1000, Math.max(1, Number(query.page_size) || filtered.length || 1));
      const items =
        query.page !== undefined || query.page_size !== undefined
          ? filtered.slice((page - 1) * pageSize, page * pageSize)
          : filtered;
      if (_req.headers['x-canonical-api'] === '1') {
        res.json({
          customers: items.map(enrichCustomer),
          total: filtered.length,
          page,
          pageSize,
        });
        return;
      }
      res.json(filtered.map(enrichCustomer));
    } catch (error) {
      next(error);
    }
  });
  app.post('/api/customers', async (req, res, next) => {
    try {
      const name = String(req.body.name ?? '').trim();
      const ip = String(req.body.ip ?? '').trim();
      const protocol = req.body.protocol === 'https' ? 'https' : 'http';
      const port = Number(req.body.port) || (protocol === 'https' ? 3443 : 3000);
      if (!name) {
        res.status(400).json({ error: '请填写客户名称' });
        return;
      }
      if (ip && !isValidIP(ip)) {
        res.status(400).json({ error: 'IP 地址格式不正确' });
        return;
      }
      if (!Number.isInteger(port) || port < 1 || port > 65_535) {
        res.status(400).json({ error: '端口应为 1-65535 的整数' });
        return;
      }
      const now = Date.now();
      const customer: Customer = {
        id: crypto.randomUUID(),
        systemId: String(req.body.systemId ?? '').trim(),
        name,
        environment: ['production', 'test'].includes(req.body.environment)
          ? req.body.environment
          : 'unclassified',
        protocol,
        ip,
        port,
        baseUrl: ip ? `${protocol}://${ip}:${port}` : null,
        contact: String(req.body.contact ?? '').trim(),
        notes: String(req.body.notes ?? '')
          .trim()
          .slice(0, 1000),
        apiToken: crypto.randomBytes(24).toString('hex'),
        totalRecharged: 0,
        status: 'active',
        lastSeenIP: null,
        lastSeenAt: null,
        lastReport: null,
        createdAt: now,
        updatedAt: now,
      };
      await updateJson(db.files.customers, [], (items: Customer[]) => [...items, customer]);
      await audit(req.staff!, req);
      res.status(201).json(customer);
    } catch (error) {
      next(error);
    }
  });

  app.patch('/api/customers/:id', async (req, res, next) => {
    try {
      let updated: Customer | undefined;
      await updateJson(db.files.customers, [], (items: Customer[]) =>
        items.map((item): Customer => {
          if (item.id !== req.params.id) return item;
          const ip = req.body.ip === undefined ? item.ip : String(req.body.ip).trim();
          if (ip && !isValidIP(ip))
            throw Object.assign(new Error('IP 地址格式不正确'), { status: 400 });
          const protocol =
            req.body.protocol === undefined ? item.protocol : String(req.body.protocol);
          if (protocol !== 'http' && protocol !== 'https')
            throw Object.assign(new Error('连接协议不合法'), { status: 400 });
          const port = req.body.port === undefined ? item.port : Number(req.body.port);
          if (!Number.isInteger(port) || port < 1 || port > 65_535)
            throw Object.assign(new Error('端口应为 1-65535 的整数'), { status: 400 });
          const name = req.body.name === undefined ? item.name : String(req.body.name).trim();
          if (!name) throw Object.assign(new Error('客户名称不能为空'), { status: 400 });
          const environmentValue =
            req.body.environment === undefined ? item.environment : String(req.body.environment);
          if (!['production', 'test', 'unclassified'].includes(environmentValue))
            throw Object.assign(new Error('环境分类不合法'), { status: 400 });
          const statusValue = req.body.status === undefined ? item.status : String(req.body.status);
          if (statusValue !== 'active' && statusValue !== 'disabled')
            throw Object.assign(new Error('客户状态不合法'), { status: 400 });
          updated = {
            ...item,
            name,
            systemId:
              req.body.systemId === undefined ? item.systemId : String(req.body.systemId).trim(),
            environment: environmentValue as Customer['environment'],
            ip,
            protocol,
            port,
            baseUrl: ip ? `${protocol}://${ip}:${port}` : null,
            contact:
              req.body.contact === undefined ? item.contact : String(req.body.contact).trim(),
            notes:
              req.body.notes === undefined
                ? item.notes
                : String(req.body.notes).trim().slice(0, 1000),
            status: statusValue as Customer['status'],
            updatedAt: Date.now(),
          };
          return updated!;
        }),
      );
      if (!updated) {
        res.status(404).json({ error: '客户不存在' });
        return;
      }
      await audit(req.staff!, req);
      res.json(enrichCustomer(updated));
    } catch (error) {
      next(error);
    }
  });
  app.delete('/api/customers/:id', async (req, res, next) => {
    try {
      await updateJson(db.files.customers, [], (items: Customer[]) =>
        items.filter((item) => item.id !== req.params.id),
      );
      await audit(req.staff!, req);
      res.json({ ok: true });
    } catch (error) {
      next(error);
    }
  });
  app.get('/api/customers/:id/api-token', async (req, res, next) => {
    try {
      const item = (await db.customers()).find((customer) => customer.id === req.params.id);
      if (!item) {
        res.status(404).json({ error: '客户不存在' });
        return;
      }
      await audit(req.staff!, req);
      res.json({ apiToken: item.apiToken });
    } catch (error) {
      next(error);
    }
  });
  app.get('/api/customers/:id/recharge-orders', async (req, res, next) => {
    try {
      res.json((await db.orders()).filter((order) => order.customerID === req.params.id));
    } catch (error) {
      next(error);
    }
  });
  app.post('/api/customers/:id/reset-admin-password', async (req, res, next) => {
    try {
      const customer = (await db.customers()).find((item) => item.id === req.params.id);
      if (!customer) {
        res.status(404).json({ error: '客户不存在' });
        return;
      }
      if (!customer.ip) {
        res.status(400).json({ error: '这个客户还没有配置 IP，无法远程连接' });
        return;
      }
      const username = String(req.body.username ?? '').trim();
      if (!username) {
        res.status(400).json({ error: '请填写要重置的管理员用户名' });
        return;
      }
      const newPassword = `${crypto.randomBytes(9).toString('base64').replace(/[+/=]/g, '').slice(0, 12)}aA1`;
      const operationID = crypto.randomUUID();
      await callCustomer(
        customer,
        '/integration/sales/v1/admin-password-resets',
        {
          operationId: operationID,
          username,
          newPassword,
          expiresAt: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
        },
        { idempotencyKey: operationID },
      );
      await audit(req.staff!, req);
      res.json({ ok: true, username, newPassword });
    } catch (error) {
      next(error);
    }
  });

  app.post('/api/customers/:id/recharge-codes', async (req, res, next) => {
    try {
      const amount = validateAmount(req.body.amount);
      const requestID = String(req.body.requestID ?? req.headers['idempotency-key'] ?? '').slice(
        0,
        100,
      );
      if (!/^[a-zA-Z0-9_-]{16,100}$/.test(requestID)) {
        res.status(400).json({ error: '缺少充值操作标识' });
        return;
      }
      const result = await serial(async () => {
        const customers = await db.customers();
        const customer = customers.find((item) => item.id === req.params.id);
        if (!customer || customer.status === 'disabled')
          throw Object.assign(new Error('客户不存在或已停用'), { status: 404 });
        if (!customer.systemId)
          throw Object.assign(new Error('请先登记客户系统 ID'), { status: 400 });
        const orders = await db.orders();
        const previous = orders.find((order) => order.requestID === requestID);
        if (previous) {
          if (previous.customerID !== customer.id || previous.amount !== amount)
            throw Object.assign(new Error('重复请求内容不一致'), { status: 409 });
          return previous;
        }
        const { tokenExchangeRate } = await db.settings();
        const tokens = Math.round(amount * tokenExchangeRate);
        const id = crypto.randomUUID();
        const expiresAt = Date.now() + RECHARGE_CODE_TTL_MS;
        const code = await signRechargeCode(amount, tokens, customer.systemId, id, expiresAt);
        customer.totalRecharged += amount;
        customer.updatedAt = Date.now();
        const order: RechargeOrder = {
          id,
          requestID,
          customerID: customer.id,
          amount,
          tokens,
          method: 'code',
          status: 'issued',
          code,
          expiresAt,
          createdAt: Date.now(),
          processedBy: req.staff!.username,
        };
        await writeJson(db.files.customers, customers);
        await writeJson(db.files.orders, [order, ...orders]);
        return order;
      });
      await audit(req.staff!, req);
      res.status(201).json({
        order: result,
        tier: tierFor(
          (await db.customers()).find((item) => item.id === req.params.id)?.totalRecharged ?? 0,
        ),
      });
    } catch (error) {
      next(error);
    }
  });

  app.get('/api/recharge-requests', async (req, res, next) => {
    try {
      const customers = await db.customers();
      const status = req.query.status ? String(req.query.status) : undefined;
      res.json(
        (await db.orders())
          .filter((order) => order.method === 'online' && (!status || order.status === status))
          .map((order) => ({
            ...order,
            customerName:
              customers.find((item) => item.id === order.customerID)?.name ?? '未知客户',
          })),
      );
    } catch (error) {
      next(error);
    }
  });
  app.post('/api/recharge-requests/:id/:action', async (req, res, next) => {
    try {
      const action = req.params.action;
      if (action !== 'approve' && action !== 'reject') {
        res.status(404).json({ error: '操作不存在' });
        return;
      }
      const result = await serial(async () => {
        const orders = await db.orders();
        const order = orders.find((item) => item.id === req.params.id);
        if (!order || order.method !== 'online')
          throw Object.assign(new Error('申请不存在'), { status: 404 });
        if (order.status !== 'pending')
          throw Object.assign(new Error('申请已处理'), { status: 409 });
        const strictContract = req.headers['x-canonical-api'] === '1';
        if (strictContract) requiredRequestID(req);
        const providedRequestID = strictContract
          ? requiredText(req.body.requestID, 'request_id')
          : optionalRequestID(req.body.requestID);
        if (providedRequestID && order.requestID && providedRequestID !== order.requestID)
          throw Object.assign(new Error('request_id 与充值申请不匹配'), { status: 409 });
        if (strictContract && !order.requestID)
          throw Object.assign(new Error('原始充值申请缺少 request_id'), { status: 409 });
        order.requestID = providedRequestID ?? order.requestID ?? requestID(req);
        const reason = typeof req.body.reason === 'string' ? req.body.reason.trim() : '';
        if (reason) order.decisionReason = reason.slice(0, 500);
        if (action === 'reject') {
          order.status = 'rejected';
          order.processedAt = Date.now();
          order.processedBy = req.staff!.username;
          await writeJson(db.files.orders, orders);
          return order;
        }
        const amount = strictContract
          ? requiredContractAmount(req.body.amount)
          : validateAmount(req.body.amount);
        const customers = await db.customers();
        const customer = customers.find((item) => item.id === order.customerID);
        if (!customer || customer.status === 'disabled' || !customer.systemId)
          throw Object.assign(new Error('客户不可用或缺少系统 ID'), { status: 400 });
        const { tokenExchangeRate } = await db.settings();
        const expiresAt = Date.now() + RECHARGE_CODE_TTL_MS;
        order.status = 'approved';
        order.amount = amount;
        order.tokens = Math.round(amount * tokenExchangeRate);
        order.expiresAt = expiresAt;
        order.code = await signRechargeCode(
          amount,
          order.tokens,
          customer.systemId,
          order.id,
          expiresAt,
        );
        order.processedAt = Date.now();
        order.processedBy = req.staff!.username;
        customer.totalRecharged += amount;
        customer.updatedAt = Date.now();
        await writeJson(db.files.orders, orders);
        await writeJson(db.files.customers, customers);
        return order;
      });
      await audit(req.staff!, req);
      res.json(result);
    } catch (error) {
      next(error);
    }
  });

  app.get('/api/reconciliation', async (req, res, next) => {
    try {
      const from = Number(req.query.from) || 0;
      const to = Number(req.query.to) || Date.now();
      const customers = await db.customers();
      const reports = await db.reports();
      const allOrders = (await db.orders()).filter(
        (order) =>
          ['issued', 'approved'].includes(order.status) &&
          (order.processedAt ?? order.createdAt) >= from &&
          (order.processedAt ?? order.createdAt) <= to,
      );
      res.json(
        customers.map((customer) => {
          const ownOrders = allOrders.filter((order) => order.customerID === customer.id);
          const inWindow = reports
            .filter(
              (report) =>
                report.customerID === customer.id &&
                report.reportedAt >= from &&
                report.reportedAt <= to,
            )
            .sort((a, b) => a.reportedAt - b.reportedAt);
          return {
            customerID: customer.id,
            name: customer.name,
            rechargedAmount: ownOrders.reduce((sum, order) => sum + (order.amount ?? 0), 0),
            rechargedTokens: ownOrders.reduce((sum, order) => sum + (order.tokens ?? 0), 0),
            consumedTokens: measuredConsumption(inWindow),
          };
        }),
      );
    } catch (error) {
      next(error);
    }
  });
  app.get('/api/customers/:id/usage', async (req, res, next) => {
    try {
      res.json(
        (await db.reports())
          .filter((report) => report.customerID === req.params.id)
          .sort((a, b) => a.reportedAt - b.reportedAt)
          .slice(-200),
      );
    } catch (error) {
      next(error);
    }
  });
  app.get('/api/alerts', async (_req, res, next) => {
    try {
      const customers = await db.customers();
      const reports = await db.reports();
      const alerts = [];
      for (const customer of customers) {
        const recent = reports
          .filter((report) => report.customerID === customer.id)
          .sort((a, b) => b.reportedAt - a.reportedAt)
          .slice(0, 10);
        if (recent.length < 2) continue;
        const ordered = [...recent].reverse();
        const consumed = measuredConsumption(ordered);
        if (consumed === null) continue;
        const days = Math.max(
          0.1,
          (ordered.at(-1)!.reportedAt - ordered[0].reportedAt) / 86_400_000,
        );
        const perDay = consumed / days;
        const daysLeft = perDay > 0 ? recent[0].poolTokens / perDay : Number.POSITIVE_INFINITY;
        if (daysLeft < 14)
          alerts.push({
            customerID: customer.id,
            name: customer.name,
            poolTokens: recent[0].poolTokens,
            daysLeft: Math.round(daysLeft),
          });
      }
      alerts.sort((a, b) => a.daysLeft - b.daysLeft);
      res.json(alerts);
    } catch (error) {
      next(error);
    }
  });
  app.get('/api/operation-audit', async (_req, res, next) => {
    try {
      res.json((await db.audit()).slice(0, 200));
    } catch (error) {
      next(error);
    }
  });

  app.get('/api/releases', async (_req, res, next) => {
    try {
      res.json({ app: await dbRelease('app'), core: await dbRelease('core') });
    } catch (error) {
      next(error);
    }
  });
  app.post('/api/releases/:type', upload.single('file'), async (req, res, next) => {
    try {
      const type = releaseTypeFromCanonical(req.params.type);
      if (!type || !req.file) {
        res.status(400).json({ error: '请上传有效升级包和类型' });
        return;
      }
      const version = String(req.query.version ?? '').trim();
      if (!/^\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?$/.test(version)) {
        res.status(400).json({ error: '请提供有效版本号' });
        return;
      }
      const digest = crypto.createHash('sha256').update(req.file.buffer).digest('hex');
      const filename = `${type}-${version}-${digest.slice(0, 16)}.tar.gz`;
      const candidate = path.join(RELEASE_DIR, `.upload-${crypto.randomUUID()}`);
      await fs.writeFile(candidate, req.file.buffer, { mode: 0o600 });
      try {
        const manifestType = String(req.headers['x-contract-artifact-type'] ?? type);
        await validateReleaseArchive(candidate, type, version, manifestType);
        const file = path.join(RELEASE_DIR, filename);
        try {
          await fs.access(file);
        } catch {
          await fs.rename(candidate, file);
        }
        const meta = {
          type,
          version,
          file: filename,
          sha256: digest,
          size: req.file.size,
          uploadedAt: Date.now(),
          uploadedBy: req.staff!.username,
        };
        await writeJson(path.join(RELEASE_DIR, `${type}.json`), meta);
        await audit(req.staff!, req);
        res.json(meta);
      } finally {
        await fs.rm(candidate, { force: true });
      }
    } catch (error) {
      next(error);
    }
  });

  app.post('/api/upgrade-all/:type', async (req, res, next) => {
    try {
      const type = releaseTypeFromCanonical(req.params.type);
      const meta = await dbRelease(type);
      if (!meta) {
        res.status(400).json({ error: '还没有上传过这个类型的升级包' });
        return;
      }
      const selected = new Set(
        Array.isArray(req.body.customerIDs) ? req.body.customerIDs.map(String) : [],
      );
      if (!selected.size) {
        res.status(400).json({ error: '请选择升级目标' });
        return;
      }
      const customers = (await db.customers()).filter(
        (customer) => selected.has(customer.id) && customer.status === 'active' && customer.ip,
      );
      if (customers.length !== selected.size) {
        res.status(400).json({ error: '存在无法升级的目标，请重新选择' });
        return;
      }
      const results = [];
      const publicBase =
        process.env.SALES_PUBLIC_URL?.replace(/\/$/, '') ??
        `${req.protocol}://${req.get('host')}`;
      for (const customer of customers) {
        try {
          const artifactType = type;
          const operationID = crypto.randomUUID();
          await callCustomer(
            customer,
            `/integration/sales/v1/upgrades/${artifactType}`,
            {
              operationId: operationID,
              artifactType,
              version: meta.version,
              sha256: `sha256:${meta.sha256}`,
              sizeBytes: meta.size,
              downloadUrl: `${publicBase}/api/v1/integration/releases/${artifactType}/latest`,
              issuedAt: new Date().toISOString(),
              expiresAt: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
            },
            { idempotencyKey: operationID },
          );
          let confirmed = false;
          for (let attempt = 0; attempt < 30; attempt += 1) {
            await new Promise((resolve) => setTimeout(resolve, 2_000));
            try {
              const ping = await callCustomer(customer, '/integration/sales/v1/ping', {});
              if ((type === 'app' ? ping.appVersion : ping.coreVersion) === meta.version) {
                confirmed = true;
                break;
              }
            } catch {
              /* The next probe may succeed while the client restarts. */
            }
          }
          results.push({
            id: customer.id,
            name: customer.name,
            ok: confirmed,
            state: confirmed ? 'completed' : 'unverified',
          });
        } catch (error) {
          results.push({
            id: customer.id,
            name: customer.name,
            ok: false,
            error: error instanceof Error ? error.message : '升级失败',
          });
        }
      }
      await audit(req.staff!, req);
      res.json({ version: meta.version, results });
    } catch (error) {
      next(error);
    }
  });

  app.use((error: unknown, req: Request, res: Response, _next: NextFunction) => {
    const status =
      typeof error === 'object' && error && 'status' in error
        ? Number((error as { status?: number }).status)
        : 500;
    const actualStatus = status || 500;
    if (req.url.startsWith('/api/v1') || req.headers['x-canonical-api'] === '1') {
      res.status(actualStatus).json({
        detail: {
          code:
            actualStatus === 404
              ? 'not_found'
              : actualStatus === 409
                ? 'conflict'
                : 'request_failed',
          message: error instanceof Error ? error.message : '服务器错误',
        },
        request_id: requestID(req),
      });
      return;
    }
    res.status(actualStatus).json({ error: error instanceof Error ? error.message : '服务器错误' });
  });
  return app;
}

async function dbRelease(type: ReleaseType | null) {
  if (!type) return null;
  const current = await readJson<ReleaseMeta | null>(path.join(RELEASE_DIR, `${type}.json`), null);
  return current ? { ...current, type } : null;
}
