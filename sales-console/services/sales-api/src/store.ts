import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';

import { Pool } from 'pg';

import type {
  Customer,
  RechargeOrder,
  ReleaseMeta,
  Settings,
  Staff,
  UsageReport,
} from './types.js';

export interface IdempotencyRecord {
  scope: string;
  key: string;
  requestHash: string;
  statusCode: number;
  responseBody: unknown;
  createdAt: number;
  expiresAt: number;
}

export const DATA_DIR = path.resolve(
  process.env.SALES_DATA_DIR ?? path.join(process.cwd(), 'data'),
);
export const RELEASE_DIR = path.join(DATA_DIR, 'releases');
export const KEY_DIR = path.join(DATA_DIR, 'keys');

const files = {
  staff: path.join(DATA_DIR, 'staff.json'),
  customers: path.join(DATA_DIR, 'customers.json'),
  orders: path.join(DATA_DIR, 'recharge-orders.json'),
  reports: path.join(DATA_DIR, 'usage-reports.json'),
  settings: path.join(DATA_DIR, 'settings.json'),
  audit: path.join(DATA_DIR, 'operation-audit.json'),
  idempotency: path.join(DATA_DIR, 'idempotency.json'),
} as const;

const databaseUrl = process.env.DATABASE_URL?.trim();
const databasePool = databaseUrl
  ? new Pool({
      connectionString: databaseUrl,
      max: Number(process.env.DATABASE_POOL_MAX ?? 10),
    })
  : null;
const storeKeys = new Map<string, string>(
  Object.entries(files).map(([key, file]) => [path.resolve(file), key]),
);
const databaseTable = 'sales_console_json_store';

function storeKey(file: string): string | null {
  return storeKeys.get(path.resolve(file)) ?? null;
}

async function readDatabase<T>(key: string, fallback: T): Promise<T> {
  if (!databasePool) return fallback;
  const result = await databasePool.query<{ value: T }>(
    `SELECT value FROM ${databaseTable} WHERE key = $1`,
    [key],
  );
  return result.rows[0]?.value ?? fallback;
}

async function writeDatabase(key: string, value: unknown): Promise<void> {
  if (!databasePool) return;
  await databasePool.query(
    `INSERT INTO ${databaseTable} (key, value, updated_at)
     VALUES ($1, $2::jsonb, NOW())
     ON CONFLICT (key)
     DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()`,
    [key, JSON.stringify(value)],
  );
}

let writeQueue = Promise.resolve();

export async function serial<T>(task: () => Promise<T>): Promise<T> {
  const next = writeQueue.then(task, task);
  writeQueue = next.then(
    () => undefined,
    () => undefined,
  );
  return next;
}

export async function readJson<T>(file: string, fallback: T): Promise<T> {
  const key = storeKey(file);
  if (databasePool && key) return readDatabase(key, fallback);
  try {
    return JSON.parse(await fs.readFile(file, 'utf8')) as T;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return fallback;
    throw new Error(`无法读取数据文件：${path.basename(file)}`);
  }
}

export async function writeJson(file: string, value: unknown): Promise<void> {
  const key = storeKey(file);
  if (databasePool && key) {
    await writeDatabase(key, value);
    return;
  }
  const temp = `${file}.${crypto.randomUUID()}.tmp`;
  await fs.writeFile(temp, JSON.stringify(value, null, 2), { mode: 0o600 });
  await fs.rename(temp, file);
}

export async function updateJson<T>(
  file: string,
  fallback: T,
  mutate: (value: T) => T | Promise<T>,
): Promise<T> {
  return serial(async () => {
    const current = await readJson(file, fallback);
    const next = await mutate(current);
    await writeJson(file, next);
    return next;
  });
}

export async function ensureData(): Promise<void> {
  await fs.mkdir(DATA_DIR, { recursive: true, mode: 0o700 });
  await fs.mkdir(RELEASE_DIR, { recursive: true, mode: 0o700 });
  await fs.mkdir(KEY_DIR, { recursive: true, mode: 0o700 });
  const defaults: Array<[string, unknown]> = [
    [files.staff, [] satisfies Staff[]],
    [files.customers, [] satisfies Customer[]],
    [files.orders, [] satisfies RechargeOrder[]],
    [files.reports, [] satisfies UsageReport[]],
    [files.settings, { tokenExchangeRate: 41841 } satisfies Settings],
    [files.audit, []],
    [files.idempotency, [] satisfies IdempotencyRecord[]],
  ];
  if (databasePool) {
    await databasePool.query(`
      CREATE TABLE IF NOT EXISTS ${databaseTable} (
        key TEXT PRIMARY KEY,
        value JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )
    `);
    for (const [file, value] of defaults) {
      const key = storeKey(file);
      if (!key) continue;
      const existing = await databasePool.query(
        `SELECT 1 FROM ${databaseTable} WHERE key = $1`,
        [key],
      );
      if (existing.rows.length > 0) continue;
      let initialValue = value;
      try {
        initialValue = JSON.parse(await fs.readFile(file, 'utf8')) as typeof value;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT') {
          throw new Error(`无法读取初始数据文件：${path.basename(file)}`);
        }
      }
      await writeDatabase(key, initialValue);
    }
    return;
  }
  for (const [file, value] of defaults) {
    try {
      await fs.access(file);
    } catch {
      await writeJson(file, value);
    }
  }
}

export const db = {
  staff: () => readJson<Staff[]>(files.staff, []),
  customers: () => readJson<Customer[]>(files.customers, []),
  orders: () => readJson<RechargeOrder[]>(files.orders, []),
  reports: () => readJson<UsageReport[]>(files.reports, []),
  settings: () => readJson<Settings>(files.settings, { tokenExchangeRate: 41841 }),
  audit: () => readJson<unknown[]>(files.audit, []),
  idempotency: () => readJson<IdempotencyRecord[]>(files.idempotency, []),
  files,
};

export async function closeDataStore(): Promise<void> {
  await databasePool?.end();
}

export async function dataStoreHealth(): Promise<'postgresql' | 'json'> {
  if (!databasePool) return 'json';
  await databasePool.query('SELECT 1');
  return 'postgresql';
}

export async function appendAudit(entry: Record<string, unknown>): Promise<void> {
  await updateJson(db.files.audit, [], (items: unknown[]) =>
    [{ id: crypto.randomUUID(), at: Date.now(), ...entry }, ...items].slice(0, 5000),
  );
}

export async function getSigningKeyPair(): Promise<{
  privateKey: crypto.KeyObject;
  publicKey: crypto.KeyObject;
}> {
  const privateFile = path.join(KEY_DIR, 'private.pem');
  const publicFile = path.join(KEY_DIR, 'public.pem');
  try {
    return {
      privateKey: crypto.createPrivateKey(await fs.readFile(privateFile)),
      publicKey: crypto.createPublicKey(await fs.readFile(publicFile)),
    };
  } catch {
    const pair = crypto.generateKeyPairSync('ed25519');
    await fs.writeFile(privateFile, pair.privateKey.export({ type: 'pkcs8', format: 'pem' }), {
      mode: 0o600,
    });
    await fs.writeFile(publicFile, pair.publicKey.export({ type: 'spki', format: 'pem' }), {
      mode: 0o644,
    });
    return pair;
  }
}
