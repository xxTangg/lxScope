import crypto from 'node:crypto';

import cors from 'cors';
import express, { type NextFunction, type Request, type Response } from 'express';
import multer from 'multer';

import {
  registerCanonicalCustomerRoutes,
  registerStaffCustomerRoutes,
} from './routes/customers.js';
import { registerPublicRechargeRoutes, registerStaffRechargeRoutes } from './routes/recharge.js';
import { registerPublicReportRoutes, registerStaffReportRoutes } from './routes/reports.js';
import { registerPublicUpgradeRoutes, registerStaffUpgradeRoutes } from './routes/upgrades.js';
import { createReleaseRepository } from './repositories/release-repository.js';
import { registerSettingsRoutes } from './routes/settings.js';
import { salesRepository } from './repositories/sales-repository.js';
import { createCustomerService } from './services/customer-service.js';
import { createRechargeService } from './services/recharge-service.js';
import { createReportService } from './services/report-service.js';
import { createSettingsService } from './services/settings-service.js';
import { createUpgradeService } from './services/upgrade-service.js';
import { customerConnectionUrls, enrichCustomer, safeMoney, tierFor } from './business.js';
import { fromCanonical, releaseTypeFromCanonical, toCanonical } from './contract.js';
import {
  asRecord,
  canonicalResponseFor,
  canonicalTarget,
  isDirectCanonicalPath,
} from './contracts/legacy-compat.js';
import {
  appendAudit,
  db,
  dataStoreHealth,
  ensureData,
  getSigningKeyPair,
  publicKeyFingerprint,
  RELEASE_DIR,
  updateJson,
  writeJson,
} from './store.js';
import type { IdempotencyRecord } from './store.js';
import type { Customer, Staff } from './types.js';

const APP_VERSION = '2.0.0';
const RELEASE_MAX_BYTES = 300 * 1024 * 1024;
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

function optionalContractText(value: unknown, field: string, maxLength: number): string {
  if (value === undefined || value === null) return '';
  if (typeof value !== 'string' || value.length > maxLength) {
    throw Object.assign(new Error(`${field} 参数不合法`), { status: 400 });
  }
  return value;
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

function isMalformedRechargeReplay(
  canonicalPath: string,
  method: string,
  record: IdempotencyRecord,
): boolean {
  if (
    method !== 'POST' ||
    canonicalPath !== '/api/v1/integration/recharge-requests' ||
    record.statusCode < 400 ||
    record.statusCode >= 500
  ) {
    return false;
  }
  const body = asRecord(record.responseBody);
  return (
    body?.status === 'pending' &&
    body?.delivery_status === 'not_delivered' &&
    typeof body?.order_id !== 'string'
  );
}

function canRecoverIdempotentRecharge(pathName: string, method: string): boolean {
  if (method !== 'POST') return false;
  return (
    pathName === '/api/v1/integration/recharge-requests' ||
    /^\/api\/v1\/customers\/[^/]+\/recharge-codes$/.test(pathName) ||
    /^\/api\/v1\/customers\/[^/]+\/recharge-codes\/[^/]+\/reissue$/.test(pathName) ||
    /^\/api\/v1\/recharge-requests\/[^/]+\/(?:approve|reject)$/.test(pathName) ||
    /^\/api\/v1\/integration\/recharge-requests\/[^/]+\/ack$/.test(pathName)
  );
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
  const bases = customerConnectionUrls(customer);
  if (!bases.length) throw new Error('客户未配置可连接地址');
  const requestIDValue = options.requestID ?? crypto.randomUUID();
  const headers: Record<string, string> = {
    Authorization: `Bearer ${customer.apiToken}`,
    'Content-Type': 'application/json',
    'X-Request-ID': requestIDValue,
  };
  if (options.idempotencyKey) headers['Idempotency-Key'] = options.idempotencyKey;
  const requestBody = JSON.stringify(toCanonical(body));
  let lastConnectionError: unknown;
  for (const base of bases) {
    let response: Awaited<ReturnType<typeof fetch>>;
    try {
      response = await fetch(`${base}${endpoint}`, {
        method: 'POST',
        headers,
        body: requestBody,
        signal: AbortSignal.timeout(120_000),
      });
    } catch (error) {
      lastConnectionError = error;
      continue;
    }
    const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    if (!response.ok) {
      const responseError = new Error(customerErrorMessage(payload, response.status));
      if ([502, 503, 504].includes(response.status)) {
        lastConnectionError = responseError;
        continue;
      }
      throw responseError;
    }
    return fromCanonical(payload) as Record<string, unknown>;
  }
  const detail =
    lastConnectionError instanceof Error && lastConnectionError.message
      ? lastConnectionError.message
      : '连接失败';
  throw new Error(`无法连接客户系统（已尝试 ${bases.length} 个地址）：${detail}`);
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
  // Validate the persisted signer before serving any request. A missing,
  // corrupt, or mismatched pair must never silently rotate the trust root.
  await getSigningKeyPair();
  await bootstrapStaff();
  const app = express();
  const rechargeRouteDependencies = {
    service: createRechargeService(salesRepository),
    customerFromBearer,
    readCustomers: () => db.customers(),
    readOrders: () => db.orders(),
    requestID,
    requiredRequestID,
    requiredText,
    optionalContractText,
    requiredContractAmount,
    requiredISOTime,
    requiredSystemQuery,
    optionalRequestID,
    validateAmount,
    tierFor,
    audit,
  };
  const customerRouteDependencies = {
    service: createCustomerService(salesRepository),
    requireStaff,
    readOrders: () => db.orders(),
    enrichCustomer,
    customerConnectionUrls,
    requiredRequestID,
    audit,
    callCustomer,
  };
  const reportService = createReportService(salesRepository);
  const reportRouteDependencies = {
    service: reportService,
    customerFromBearer,
    requestID,
    requiredRequestID,
    requiredText,
    requiredContractMoney,
    requiredISOTime,
  };
  const settingsService = createSettingsService(salesRepository);
  const releaseRepository = createReleaseRepository(RELEASE_DIR);
  const upgradeService = createUpgradeService({
    releases: releaseRepository,
    repository: salesRepository,
    releaseDirectory: RELEASE_DIR,
    customerConnectionUrls,
    callCustomer,
  });
  const upgradeRouteDependencies = {
    service: upgradeService,
    uploadRelease: upload.single('file'),
    releaseDirectory: RELEASE_DIR,
    releaseTypeFromCanonical,
    customerFromBearer,
    audit,
  };
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
      responseBody = canonicalResponseFor(req.method, body, canonicalPath, id);
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
        req.headers['x-idempotency-scope'] = scope;
        const hash = requestHash(req);
        let previous: IdempotencyRecord | undefined;
        await updateJson(db.files.idempotency, [], (items: IdempotencyRecord[]) => {
          previous = items.find((item) => item.scope === scope && item.key === idempotencyKey);
          const now = Date.now();
          if (previous && previous.expiresAt > now) {
            if (!isMalformedRechargeReplay(canonicalPath, req.method, previous)) return items;
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
              ...items.filter((item) => item.scope !== scope || item.key !== idempotencyKey),
            ].slice(0, 10_000);
          }
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
          if (isMalformedRechargeReplay(canonicalPath, req.method, previous)) {
            previous = undefined;
          } else if (previous.requestHash !== hash) {
            res.status(409).json({
              detail: {
                code: 'idempotency_key_reused',
                message: '同一幂等键不能对应不同请求内容',
              },
            });
            return;
          }
        }
        if (previous && previous.expiresAt > Date.now()) {
          if (previous.statusCode === 0) {
            if (!canRecoverIdempotentRecharge(canonicalPath, req.method)) {
              res.status(409).json({
                detail: {
                  code: 'idempotency_in_progress',
                  message: '相同幂等请求正在处理中，请稍后查询或重试',
                },
              });
              return;
            }
            // Recharge handlers bind the durable order ID to this record in
            // the same database transaction as the financial side effect.
            // Re-entering the service is safe both before and after that commit.
            previous = undefined;
          }
          if (previous) {
            res.status(previous.statusCode);
            const replayBody = asRecord(previous.responseBody);
            originalJson(replayBody ? { ...replayBody, request_id: id } : previous.responseBody);
            return;
          }
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

  registerCanonicalCustomerRoutes(app, customerRouteDependencies);

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
        res
          .status(401)
          .json({ detail: { code: 'invalid_customer_token', message: '客户 Token 无效' } });
        return;
      }
      const { publicKey } = await getSigningKeyPair();
      const publicKeyRequestID =
        req.headers['x-canonical-api'] === '1' ? requiredRequestID(req) : requestID(req);
      res.json({
        publicKeyPem: publicKey.export({ type: 'spki', format: 'pem' }).toString(),
        publicKeyFingerprint: publicKeyFingerprint(publicKey),
        systemId: customer.systemId,
        requestId: publicKeyRequestID,
      });
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
  registerPublicRechargeRoutes(app, rechargeRouteDependencies);
  registerPublicReportRoutes(app, reportRouteDependencies);
  registerPublicUpgradeRoutes(app, upgradeRouteDependencies);

  app.post('/api/verify-connection', async (req, res, next) => {
    try {
      const customer = await customerFromBearer(req);
      if (!customer) {
        res.status(401).json({ error: '无效的客户访问令牌' });
        return;
      }
      const outboundRequestID =
        req.headers['x-canonical-api'] === '1' ? requiredRequestID(req) : requestID(req);
      if (!customerConnectionUrls(customer).length) {
        res.json({ outbound: true, inbound: false, inboundError: '客户尚未配置可连接地址' });
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
  registerSettingsRoutes(app, settingsService, audit);
  registerStaffUpgradeRoutes(app, upgradeRouteDependencies);
  registerStaffRechargeRoutes(app, rechargeRouteDependencies);

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
      res.json({
        publicKeyPem: publicKey.export({ type: 'spki', format: 'pem' }).toString(),
        publicKeyFingerprint: publicKeyFingerprint(publicKey),
      });
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

  registerStaffCustomerRoutes(app, customerRouteDependencies);
  registerStaffReportRoutes(app, reportService);

  app.get('/api/operation-audit', async (_req, res, next) => {
    try {
      res.json((await db.audit()).slice(0, 200));
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
