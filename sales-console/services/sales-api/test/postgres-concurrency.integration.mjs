import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { after, before, test } from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const databaseURL = process.env.SALES_TEST_DATABASE_URL;
if (!databaseURL) {
  throw new Error(
    'Set SALES_TEST_DATABASE_URL to an isolated PostgreSQL database before running this test.',
  );
}

const workerPath = fileURLToPath(new URL('./fixtures/recharge-worker.mjs', import.meta.url));
let dataDir;
let store;
let repository;

before(async () => {
  dataDir = await fs.mkdtemp(path.join(os.tmpdir(), 'sales-console-pg-concurrency-'));
  process.env.DATABASE_URL = databaseURL;
  process.env.SALES_DATA_DIR = dataDir;
  process.env.NODE_ENV = 'test';
  store = await import('../dist/store.js');
  ({ salesRepository: repository } = await import('../dist/repositories/sales-repository.js'));
  await store.ensureData();
  await store.getSigningKeyPair();
  await repository.save('staff', []);
  await repository.save('customers', [
    {
      id: 'pg-concurrency-customer',
      name: 'Concurrency test customer',
      systemId: 'pg-concurrency-system',
      apiToken: 'test-token',
      status: 'active',
      totalRecharged: 0,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    },
  ]);
  await repository.save('orders', []);
  await repository.save('settings', { tokenExchangeRate: 10 });
  await repository.save('idempotency', []);
});

after(async () => {
  await store?.closeDataStore();
  if (dataDir) await fs.rm(dataDir, { recursive: true, force: true });
});

function runWorker(action, input) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [workerPath, action, JSON.stringify(input)], {
      env: process.env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8').on('data', (chunk) => (stdout += chunk));
    child.stderr.setEncoding('utf8').on('data', (chunk) => (stderr += chunk));
    child.once('error', reject);
    child.once('close', (code) => {
      if (code !== 0) {
        reject(new Error(`Worker exited ${code}: ${stderr}`));
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch (error) {
        reject(new Error(`Invalid worker output: ${stdout}\n${stderr}`, { cause: error }));
      }
    });
  });
}

test('PostgreSQL transactions prevent duplicate recharge effects across API processes', async () => {
  const customerID = 'pg-concurrency-customer';
  const onlineInput = {
    customerID,
    scope: 'integration:online-submit',
    key: 'online-submit-key',
    requestID: 'pg-online-request-1',
    amount: 25,
    note: 'concurrency test',
    requestedAt: Date.now(),
  };
  const submissions = await Promise.all([
    runWorker('submit', onlineInput),
    runWorker('submit', onlineInput),
  ]);
  assert.equal(submissions[0].orderID, submissions[1].orderID);
  assert.equal((await repository.read('orders')).length, 1);

  const onlineOrderID = submissions[0].orderID;
  const approvalInput = {
    orderID: onlineOrderID,
    scope: 'integration:online-approval',
    requestID: onlineInput.requestID,
    amount: 25,
  };
  const approvals = await Promise.all([
    runWorker('approve', { ...approvalInput, key: 'approve-a' }),
    runWorker('approve', { ...approvalInput, key: 'approve-b' }),
  ]);
  assert.deepEqual(
    approvals.map((item) => item.status),
    ['approved', 'approved'],
  );
  assert.equal(
    (await repository.read('customers')).find((item) => item.id === customerID).totalRecharged,
    25,
  );

  // A statusCode=0 record with an operation ID models a committed transaction
  // whose process died before it cached the HTTP response.
  await repository.update('idempotency', (records) =>
    records.map((record) =>
      record.key === onlineInput.key ? { ...record, statusCode: 0, responseBody: null } : record,
    ),
  );
  const retried = await runWorker('submit', onlineInput);
  assert.equal(retried.orderID, onlineOrderID);
  assert.equal((await repository.read('orders')).length, 1);

  const recoveryOnlineInput = {
    ...onlineInput,
    scope: 'integration:approval-recovery-submit',
    key: 'approval-recovery-submit-key',
    requestID: 'pg-online-recovery-request',
    amount: 3,
  };
  const recoveryOrder = await runWorker('submit', recoveryOnlineInput);
  const approvalRetry = {
    orderID: recoveryOrder.orderID,
    scope: 'integration:approval-recovery',
    key: 'approval-recovery-key',
    requestID: recoveryOnlineInput.requestID,
    amount: recoveryOnlineInput.amount,
  };
  await repository.update('idempotency', (records) => [
    {
      scope: approvalRetry.scope,
      key: approvalRetry.key,
      requestHash: 'test-request-hash',
      statusCode: 0,
      responseBody: null,
      createdAt: Date.now(),
      expiresAt: Date.now() + 60_000,
    },
    ...records,
  ]);
  await repository.update('customers', (customers) =>
    customers.map((item) => (item.id === customerID ? { ...item, status: 'disabled' } : item)),
  );
  await assert.rejects(runWorker('approve', approvalRetry), /Worker exited 1/);
  assert.equal(
    (await repository.read('orders')).find((item) => item.id === recoveryOrder.orderID).status,
    'pending',
  );
  assert.equal(
    (await repository.read('customers')).find((item) => item.id === customerID).totalRecharged,
    25,
  );
  await repository.update('customers', (customers) =>
    customers.map((item) => (item.id === customerID ? { ...item, status: 'active' } : item)),
  );
  const recoveredApproval = await runWorker('approve', approvalRetry);
  assert.equal(recoveredApproval.status, 'approved');
  assert.equal(
    (await repository.read('customers')).find((item) => item.id === customerID).totalRecharged,
    28,
  );

  const offlineInput = {
    customerID,
    scope: 'integration:offline-issue',
    requestID: 'pg-offline-request-1',
    amount: 7,
  };
  const offlineIssues = await Promise.all([
    runWorker('issue', { ...offlineInput, key: 'offline-key-a' }),
    runWorker('issue', { ...offlineInput, key: 'offline-key-b' }),
  ]);
  assert.equal(offlineIssues[0].orderID, offlineIssues[1].orderID);
  assert.equal(
    (await repository.read('customers')).find((item) => item.id === customerID).totalRecharged,
    35,
  );
  assert.equal((await repository.read('orders')).length, 3);
});
