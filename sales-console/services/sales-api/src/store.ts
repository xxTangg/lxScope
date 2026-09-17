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
      const existing = await databasePool.query(`SELECT 1 FROM ${databaseTable} WHERE key = $1`, [
        key,
      ]);
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

type SigningKeyPair = {
  privateKey: crypto.KeyObject;
  publicKey: crypto.KeyObject;
};

let signingKeyPairPromise: Promise<SigningKeyPair> | undefined;

async function readOptionalKeyFile(file: string): Promise<Buffer | null> {
  try {
    return await fs.readFile(file);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw new Error(`无法读取充值签名密钥文件：${path.basename(file)}`, { cause: error });
  }
}

function keyPairMismatchError(): Error {
  return new Error('充值签名密钥的私钥与公钥不匹配，请恢复同一密钥对后再启动销售系统');
}

async function createSigningKeyPair(
  privateFile: string,
  publicFile: string,
): Promise<SigningKeyPair> {
  const pair = crypto.generateKeyPairSync('ed25519');
  const privateBytes = pair.privateKey.export({ type: 'pkcs8', format: 'pem' });
  const publicBytes = pair.publicKey.export({ type: 'spki', format: 'pem' });
  const privateTemp = `${privateFile}.${crypto.randomUUID()}.tmp`;
  const publicTemp = `${publicFile}.${crypto.randomUUID()}.tmp`;
  try {
    await fs.writeFile(privateTemp, privateBytes, { mode: 0o600 });
    await fs.writeFile(publicTemp, publicBytes, { mode: 0o644 });
    await fs.rename(privateTemp, privateFile);
    await fs.rename(publicTemp, publicFile);
    return pair;
  } catch (error) {
    await Promise.all([fs.rm(privateTemp, { force: true }), fs.rm(publicTemp, { force: true })]);
    throw new Error('无法持久化充值签名密钥对，已停止启动以避免签发不可兑换的充值码', {
      cause: error,
    });
  }
}

async function loadOrCreateSigningKeyPair(): Promise<SigningKeyPair> {
  const privateFile = path.join(KEY_DIR, 'private.pem');
  const publicFile = path.join(KEY_DIR, 'public.pem');
  const [privateBytes, publicBytes] = await Promise.all([
    readOptionalKeyFile(privateFile),
    readOptionalKeyFile(publicFile),
  ]);
  if (privateBytes === null && publicBytes === null) {
    return createSigningKeyPair(privateFile, publicFile);
  }
  if (privateBytes === null || publicBytes === null) {
    throw new Error('充值签名密钥对不完整，禁止自动生成新密钥；请恢复 private.pem 和 public.pem');
  }

  let privateKey: crypto.KeyObject;
  let publicKey: crypto.KeyObject;
  try {
    privateKey = crypto.createPrivateKey(privateBytes);
    publicKey = crypto.createPublicKey(publicBytes);
  } catch (error) {
    throw new Error('充值签名密钥文件损坏或格式不受支持，禁止自动生成新密钥', { cause: error });
  }
  if (privateKey.asymmetricKeyType !== 'ed25519' || publicKey.asymmetricKeyType !== 'ed25519') {
    throw new Error('充值签名密钥必须是 Ed25519 密钥对');
  }
  const derivedPublicBytes = crypto
    .createPublicKey(privateKey)
    .export({ type: 'spki', format: 'der' });
  const configuredPublicBytes = publicKey.export({ type: 'spki', format: 'der' });
  if (
    derivedPublicBytes.length !== configuredPublicBytes.length ||
    !crypto.timingSafeEqual(derivedPublicBytes, configuredPublicBytes)
  ) {
    throw keyPairMismatchError();
  }
  return { privateKey, publicKey };
}

export function getSigningKeyPair(): Promise<SigningKeyPair> {
  signingKeyPairPromise ??= loadOrCreateSigningKeyPair();
  return signingKeyPairPromise;
}

export function publicKeyFingerprint(publicKey: crypto.KeyObject): string {
  const der = publicKey.export({ type: 'spki', format: 'der' });
  return `sha256:${crypto.createHash('sha256').update(der).digest('hex').slice(0, 16)}`;
}
