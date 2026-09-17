import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import test from 'node:test';

const storeUrl = new URL('../dist/store.js', import.meta.url).href;
const businessUrl = new URL('../dist/business.js', import.meta.url).href;

function probe(dataDir, checkSignature = false) {
  const script = `
    const store = await import(${JSON.stringify(storeUrl)});
    await store.ensureData();
    const pair = await store.getSigningKeyPair();
    if (${checkSignature}) {
      const business = await import(${JSON.stringify(businessUrl)});
      const code = await business.signRechargeCode(1, 42, 'test-system', 'test-order', Date.now() + 3600000);
      if (!(await business.isRechargeCodeSignatureValid(code))) process.exit(3);
    }
    console.log(pair.publicKey.asymmetricKeyType);
  `;
  return new Promise((resolve) => {
    const child = spawn(process.execPath, ['--input-type=module', '-e', script], {
      env: { ...process.env, DATABASE_URL: '', SALES_DATA_DIR: dataDir },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => {
      stdout += chunk;
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk;
    });
    child.on('close', (code) => resolve({ code, stdout, stderr }));
  });
}

async function writeKeyPair(dataDir, privateKey, publicKey) {
  const keyDir = path.join(dataDir, 'keys');
  await fs.mkdir(keyDir, { recursive: true });
  await fs.writeFile(
    path.join(keyDir, 'private.pem'),
    privateKey.export({ type: 'pkcs8', format: 'pem' }),
  );
  await fs.writeFile(
    path.join(keyDir, 'public.pem'),
    publicKey.export({ type: 'spki', format: 'pem' }),
  );
}

test('persists one signer and produces self-verifiable codes across restarts', async () => {
  const dataDir = await fs.mkdtemp(path.join(os.tmpdir(), 'sales-api-signing-'));
  try {
    const first = await probe(dataDir, true);
    assert.equal(first.code, 0, first.stderr);
    const privateBefore = await fs.readFile(path.join(dataDir, 'keys', 'private.pem'));
    const publicBefore = await fs.readFile(path.join(dataDir, 'keys', 'public.pem'));

    const second = await probe(dataDir, true);
    assert.equal(second.code, 0, second.stderr);
    assert.deepEqual(await fs.readFile(path.join(dataDir, 'keys', 'private.pem')), privateBefore);
    assert.deepEqual(await fs.readFile(path.join(dataDir, 'keys', 'public.pem')), publicBefore);
  } finally {
    await fs.rm(dataDir, { recursive: true, force: true });
  }
});

test('fails closed when the persisted signer is incomplete or mismatched', async (t) => {
  const dataDir = await fs.mkdtemp(path.join(os.tmpdir(), 'sales-api-signing-'));
  t.after(() => fs.rm(dataDir, { recursive: true, force: true }));

  const first = crypto.generateKeyPairSync('ed25519');
  const second = crypto.generateKeyPairSync('ed25519');
  await writeKeyPair(dataDir, first.privateKey, second.publicKey);
  const mismatch = await probe(dataDir);
  assert.notEqual(mismatch.code, 0);
  assert.match(mismatch.stderr, /不匹配/);

  await fs.rm(path.join(dataDir, 'keys', 'public.pem'));
  const incomplete = await probe(dataDir);
  assert.notEqual(incomplete.code, 0);
  assert.match(incomplete.stderr, /不完整/);
});
