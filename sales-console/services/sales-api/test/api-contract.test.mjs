import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'node:http';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

let dataDir;
let server;
let baseUrl;
let closeDataStore;
let storeModule;

before(async () => {
  dataDir = await fs.mkdtemp(path.join(os.tmpdir(), 'sales-console-api-contract-'));
  process.env.DATABASE_URL = '';
  process.env.SALES_DATA_DIR = dataDir;
  process.env.SALES_ADMIN_PASSWORD = 'contract-test-password';
  process.env.NODE_ENV = 'test';

  const [{ createApp }, store] = await Promise.all([
    import('../dist/app.js'),
    import('../dist/store.js'),
  ]);
  storeModule = store;
  closeDataStore = store.closeDataStore;
  server = createServer(await createApp());
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  baseUrl = `http://127.0.0.1:${address.port}`;
});

after(async () => {
  if (server?.listening)
    await new Promise((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
  await closeDataStore?.();
  if (dataDir) await fs.rm(dataDir, { recursive: true, force: true });
});

async function request(url, init = {}) {
  const response = await fetch(new URL(url, baseUrl), init);
  const body = await response.json().catch(() => null);
  return { response, body };
}

function jsonHeaders(headers = {}) {
  return { 'Content-Type': 'application/json', ...headers };
}

test('canonical API auth, idempotency, signed recharge delivery and acknowledgement remain compatible', async () => {
  const unauthenticated = await request('/api/v1/auth/me', {
    headers: { 'X-Request-ID': 'contract-auth-missing' },
  });
  assert.equal(unauthenticated.response.status, 401);
  assert.equal(unauthenticated.body.detail.code, 'unauthorized');
  assert.equal(unauthenticated.body.request_id, 'contract-auth-missing');

  const missingIdempotency = await request('/api/v1/customers', {
    method: 'POST',
    headers: jsonHeaders({ 'X-Request-ID': 'contract-idempotency-missing' }),
    body: JSON.stringify({ name: 'Missing key' }),
  });
  assert.equal(missingIdempotency.response.status, 400);
  assert.equal(
    missingIdempotency.body?.detail?.code,
    'idempotency_key_required',
    JSON.stringify(missingIdempotency.body),
  );

  const login = await request('/api/v1/auth/login', {
    method: 'POST',
    headers: jsonHeaders({ 'X-Request-ID': 'contract-login' }),
    body: JSON.stringify({ username: 'admin', password: 'contract-test-password' }),
  });
  assert.equal(login.response.status, 200);
  assert.equal(login.body.staff.username, 'admin');
  assert.ok(login.body.staff.staff_id);
  const sessionCookie = login.response.headers.get('set-cookie')?.split(';', 1)[0];
  assert.ok(sessionCookie?.startsWith('sh_session='));

  const legacySession = await request('/api/auth/status', {
    headers: { Cookie: sessionCookie },
  });
  assert.equal(legacySession.response.status, 200);
  assert.equal(legacySession.body.authenticated, true);

  const dashboard = await request('/api/v1/dashboard', {
    headers: { Cookie: sessionCookie, 'X-Request-ID': 'contract-dashboard' },
  });
  assert.equal(dashboard.response.status, 200);
  assert.equal(dashboard.body.total_customers, 0);
  assert.equal(dashboard.body.request_id, 'contract-dashboard');

  const customerBody = {
    name: 'API 契约客户',
    system_id: 'contract-system-1',
    configured_ip: '127.0.0.1',
    protocol: 'http',
    port: 3000,
    environment: 'test',
    contact: 'ops@example.test',
    notes: 'contract regression fixture',
  };
  const createHeaders = jsonHeaders({
    Cookie: sessionCookie,
    'X-Request-ID': 'contract-customer-create',
    'Idempotency-Key': 'contract-customer-create-1',
  });
  const created = await request('/api/v1/customers', {
    method: 'POST',
    headers: createHeaders,
    body: JSON.stringify(customerBody),
  });
  assert.equal(created.response.status, 201);
  assert.equal(created.body.customer.name, customerBody.name);
  assert.equal(created.body.customer.system_id, customerBody.system_id);
  assert.equal(created.body.customer.configured_ip, customerBody.configured_ip);
  assert.ok(created.body.customer.customer_id);
  assert.equal(typeof created.body.api_token_once, 'string');
  assert.equal('api_token' in created.body.customer, false);

  const legacyCustomers = await request('/api/customers', {
    headers: { Cookie: sessionCookie },
  });
  assert.equal(legacyCustomers.response.status, 200);
  assert.ok(Array.isArray(legacyCustomers.body));
  assert.equal(legacyCustomers.body[0].id, created.body.customer.customer_id);

  const legacyToken = await request(
    `/api/customers/${created.body.customer.customer_id}/api-token`,
    {
      headers: { Cookie: sessionCookie },
    },
  );
  assert.equal(legacyToken.response.status, 200);
  assert.equal(legacyToken.body.apiToken, created.body.api_token_once);

  await new Promise((resolve) => setTimeout(resolve, 30));
  const replayedCreate = await request('/api/v1/customers', {
    method: 'POST',
    headers: { ...createHeaders, 'X-Request-ID': 'contract-customer-create-replay' },
    body: JSON.stringify(customerBody),
  });
  assert.equal(replayedCreate.response.status, 201);
  assert.equal(replayedCreate.body.customer.customer_id, created.body.customer.customer_id);
  assert.equal(replayedCreate.body.api_token_once, created.body.api_token_once);

  const reusedCreateKey = await request('/api/v1/customers', {
    method: 'POST',
    headers: createHeaders,
    body: JSON.stringify({ ...customerBody, name: 'Changed payload' }),
  });
  assert.equal(reusedCreateKey.response.status, 409);
  assert.equal(reusedCreateKey.body.detail.code, 'idempotency_key_reused');

  const customerId = created.body.customer.customer_id;
  const customerToken = created.body.api_token_once;
  const updatedCustomer = await request(`/api/v1/customers/${customerId}`, {
    method: 'PATCH',
    headers: jsonHeaders({
      Cookie: sessionCookie,
      'X-Request-ID': 'contract-customer-update',
      'Idempotency-Key': 'contract-customer-update-1',
    }),
    body: JSON.stringify({ notes: 'contract update retained' }),
  });
  assert.equal(updatedCustomer.response.status, 200);
  assert.equal(updatedCustomer.body.customer.notes, 'contract update retained');

  const staffCannotUseIntegrationKey = await request('/api/v1/integration/public-key', {
    headers: { Cookie: sessionCookie, 'X-Request-ID': 'contract-staff-machine-api' },
  });
  assert.equal(staffCannotUseIntegrationKey.response.status, 401);
  const publicKey = await request('/api/v1/integration/public-key', {
    headers: {
      Authorization: `Bearer ${customerToken}`,
      'X-Request-ID': 'contract-customer-public-key',
    },
  });
  assert.equal(publicKey.response.status, 200);
  assert.match(publicKey.body.public_key_pem, /BEGIN PUBLIC KEY/);

  const rechargeRequestId = 'contract-recharge-request-1';
  const rechargeBody = {
    system_id: customerBody.system_id,
    amount: '25.00',
    requested_at: new Date().toISOString(),
    note: 'contract flow',
  };
  const rechargeHeaders = jsonHeaders({
    Authorization: `Bearer ${customerToken}`,
    'X-Request-ID': rechargeRequestId,
    'Idempotency-Key': 'contract-recharge-1',
  });
  const submitted = await request('/api/v1/integration/recharge-requests', {
    method: 'POST',
    headers: rechargeHeaders,
    body: JSON.stringify(rechargeBody),
  });
  assert.equal(submitted.response.status, 201);
  assert.equal(submitted.body.system_id, customerBody.system_id);
  assert.equal(submitted.body.status, 'pending');
  assert.equal(submitted.body.delivery_status, 'not_delivered');
  assert.ok(submitted.body.order_id);

  await new Promise((resolve) => setTimeout(resolve, 30));
  const replayedRecharge = await request('/api/v1/integration/recharge-requests', {
    method: 'POST',
    headers: { ...rechargeHeaders, 'X-Request-ID': 'contract-recharge-replay' },
    body: JSON.stringify(rechargeBody),
  });
  assert.equal(replayedRecharge.response.status, 201);
  assert.equal(replayedRecharge.body.order_id, submitted.body.order_id);

  // Simulate a process loss after the durable order/operation binding commits,
  // but before the HTTP response is cached by the idempotency middleware.
  await storeModule.updateJson(storeModule.db.files.idempotency, [], (items) =>
    items.map((item) =>
      item.key === 'contract-recharge-1' ? { ...item, statusCode: 0, responseBody: null } : item,
    ),
  );
  const recoveredRecharge = await request('/api/v1/integration/recharge-requests', {
    method: 'POST',
    headers: { ...rechargeHeaders, 'X-Request-ID': 'contract-recharge-after-timeout' },
    body: JSON.stringify(rechargeBody),
  });
  assert.equal(recoveredRecharge.response.status, 201);
  assert.equal(recoveredRecharge.body.order_id, submitted.body.order_id);

  const changedRecharge = await request('/api/v1/integration/recharge-requests', {
    method: 'POST',
    headers: rechargeHeaders,
    body: JSON.stringify({ ...rechargeBody, amount: '26.00' }),
  });
  assert.equal(changedRecharge.response.status, 409);
  assert.equal(changedRecharge.body.detail.code, 'idempotency_key_reused');

  const concurrentRequestId = 'contract-recharge-concurrent';
  const concurrentBody = {
    ...rechargeBody,
    amount: '7.00',
    requested_at: new Date().toISOString(),
  };
  const concurrentHeaders = jsonHeaders({
    Authorization: `Bearer ${customerToken}`,
    'X-Request-ID': concurrentRequestId,
    'Idempotency-Key': 'contract-recharge-concurrent-1',
  });
  const concurrentSubmissions = await Promise.all([
    request('/api/v1/integration/recharge-requests', {
      method: 'POST',
      headers: concurrentHeaders,
      body: JSON.stringify(concurrentBody),
    }),
    request('/api/v1/integration/recharge-requests', {
      method: 'POST',
      headers: concurrentHeaders,
      body: JSON.stringify(concurrentBody),
    }),
  ]);
  assert.ok(concurrentSubmissions.some(({ response }) => response.status === 201));
  assert.ok(concurrentSubmissions.every(({ response }) => [201, 409].includes(response.status)));
  const concurrentOrderIds = concurrentSubmissions
    .filter(({ response }) => response.status === 201)
    .map(({ body }) => body.order_id);
  assert.equal(new Set(concurrentOrderIds).size, 1);
  const pendingOrders = await request('/api/v1/recharge-requests?status=pending', {
    headers: { Cookie: sessionCookie, 'X-Request-ID': 'contract-pending-orders' },
  });
  assert.equal(pendingOrders.response.status, 200);
  assert.equal(
    pendingOrders.body.orders.filter((order) => order.request_id === concurrentRequestId).length,
    1,
  );

  const approvalURL = `/api/v1/recharge-requests/${submitted.body.order_id}/approve`;
  const approvalBody = JSON.stringify({ amount: '25.00', request_id: rechargeRequestId });
  const approvals = await Promise.all(
    ['contract-approve-1', 'contract-approve-repeat'].map((key) =>
      request(approvalURL, {
        method: 'POST',
        headers: jsonHeaders({
          Cookie: sessionCookie,
          'X-Request-ID': `contract-recharge-${key}`,
          'Idempotency-Key': key,
        }),
        body: approvalBody,
      }),
    ),
  );
  assert.ok(approvals.every(({ response }) => response.status === 200));
  assert.ok(approvals.every(({ body }) => body.status === 'approved'));
  assert.ok(approvals.every(({ body }) => typeof body.code === 'string'));
  assert.equal(
    (await storeModule.db.customers()).find((item) => item.id === customerId).totalRecharged,
    25,
  );

  const pollHeaders = {
    Authorization: `Bearer ${customerToken}`,
    'X-Request-ID': 'contract-recharge-poll',
    'Idempotency-Key': 'contract-poll-1',
  };
  const polled = await request(
    `/api/v1/integration/recharge-requests/poll?system_id=${encodeURIComponent(customerBody.system_id)}`,
    { headers: pollHeaders },
  );
  assert.equal(polled.response.status, 200);
  assert.equal(polled.body.orders.length, 1);
  assert.equal(polled.body.orders[0].order_id, submitted.body.order_id);
  assert.equal(typeof polled.body.orders[0].recharge_code, 'string');

  const firstReport = await request('/api/v1/integration/usage-reports', {
    method: 'POST',
    headers: jsonHeaders({
      Authorization: `Bearer ${customerToken}`,
      'X-Request-ID': 'contract-usage-report-1',
      'Idempotency-Key': 'contract-usage-report-1',
    }),
    body: JSON.stringify({
      system_id: customerBody.system_id,
      pool_tokens: 900,
      total_recharged: '30.00',
      cumulative_consumed: 100,
      cumulative_credits: 5,
      app_version: '2.0.0',
      client_reported_at: new Date().toISOString(),
    }),
  });
  assert.equal(firstReport.response.status, 200);
  assert.equal(firstReport.body.accepted, true);
  assert.ok(firstReport.body.report_id);

  const secondReport = await request('/api/v1/integration/usage-reports', {
    method: 'POST',
    headers: jsonHeaders({
      Authorization: `Bearer ${customerToken}`,
      'X-Request-ID': 'contract-usage-report-2',
      'Idempotency-Key': 'contract-usage-report-2',
    }),
    body: JSON.stringify({
      system_id: customerBody.system_id,
      pool_tokens: 850,
      total_recharged: '30.00',
      cumulative_consumed: 150,
      cumulative_credits: 20,
      app_version: '2.0.0',
      client_reported_at: new Date().toISOString(),
    }),
  });
  assert.equal(secondReport.response.status, 200);
  assert.equal(secondReport.body.cumulative_consumed_delta, 50);
  assert.equal(secondReport.body.cumulative_credits_delta, 15);

  const usageHistory = await request(`/api/v1/customers/${customerId}/usage-reports`, {
    headers: { Cookie: sessionCookie, 'X-Request-ID': 'contract-usage-history' },
  });
  assert.equal(usageHistory.response.status, 200);
  assert.equal(usageHistory.body.reports.length, 2);
  const reconciliation = await request(`/api/v1/reconciliation?from=0&to=${Date.now() + 60_000}`, {
    headers: { Cookie: sessionCookie, 'X-Request-ID': 'contract-reconciliation' },
  });
  assert.equal(reconciliation.response.status, 200);
  const reconciledCustomer = reconciliation.body.items.find(
    (item) => item.customer_id === customerId,
  );
  assert.equal(reconciledCustomer.recharged_amount, '25.00');
  assert.equal(reconciledCustomer.consumed_tokens, 50);

  const acknowledgement = await request(
    `/api/v1/integration/recharge-requests/${submitted.body.order_id}/ack`,
    {
      method: 'POST',
      headers: jsonHeaders({
        Authorization: `Bearer ${customerToken}`,
        'X-Request-ID': 'contract-recharge-ack',
        'Idempotency-Key': 'contract-ack-1',
      }),
      body: JSON.stringify({
        operation_id: 'contract-ack-operation',
        system_id: customerBody.system_id,
        redemption_operation_id: 'contract-redemption-operation',
        ledger_id: 'contract-ledger-entry',
      }),
    },
  );
  assert.equal(acknowledgement.response.status, 200);
  assert.equal(acknowledgement.body.status, 'approved');
  assert.equal(acknowledgement.body.delivery_status, 'delivered');

  const customerOrders = await request(`/api/v1/customers/${customerId}/recharge-orders`, {
    headers: { Cookie: sessionCookie, 'X-Request-ID': 'contract-orders' },
  });
  assert.equal(customerOrders.response.status, 200);
  const deliveredOrder = customerOrders.body.orders.find(
    (order) => order.order_id === submitted.body.order_id,
  );
  assert.equal(deliveredOrder.status, 'approved');
  assert.equal(deliveredOrder.delivery_status, 'delivered');

  const offlineRequest = {
    amount: '5.00',
    request_id: 'contract-offline-issue-request-1',
  };
  const offlineIssue = await request(`/api/v1/customers/${customerId}/recharge-codes`, {
    method: 'POST',
    headers: jsonHeaders({
      Cookie: sessionCookie,
      'X-Request-ID': 'contract-offline-issue',
      'Idempotency-Key': 'contract-offline-issue-1',
    }),
    body: JSON.stringify(offlineRequest),
  });
  assert.equal(offlineIssue.response.status, 201);
  assert.ok(offlineIssue.body.order.order_id);
  const offlineOrderID = offlineIssue.body.order.order_id;
  await storeModule.updateJson(storeModule.db.files.idempotency, [], (items) =>
    items.map((item) =>
      item.key === 'contract-offline-issue-1'
        ? { ...item, statusCode: 0, responseBody: null }
        : item,
    ),
  );
  const recoveredOfflineIssue = await request(`/api/v1/customers/${customerId}/recharge-codes`, {
    method: 'POST',
    headers: jsonHeaders({
      Cookie: sessionCookie,
      'X-Request-ID': 'contract-offline-issue-after-timeout',
      'Idempotency-Key': 'contract-offline-issue-1',
    }),
    body: JSON.stringify(offlineRequest),
  });
  assert.equal(recoveredOfflineIssue.response.status, 201);
  assert.equal(recoveredOfflineIssue.body.order.order_id, offlineOrderID);
  assert.equal(
    (await storeModule.db.customers()).find((item) => item.id === customerId).totalRecharged,
    30,
  );

  const updatedSettings = await request('/api/v1/settings', {
    method: 'PATCH',
    headers: jsonHeaders({
      Cookie: sessionCookie,
      'X-Request-ID': 'contract-settings-update',
      'Idempotency-Key': 'contract-settings-update-1',
    }),
    body: JSON.stringify({ token_exchange_rate: 100 }),
  });
  assert.equal(updatedSettings.response.status, 200);
  assert.equal(updatedSettings.body.token_exchange_rate, 100);

  const releaseList = await request('/api/v1/releases', {
    headers: { Cookie: sessionCookie, 'X-Request-ID': 'contract-release-list' },
  });
  assert.equal(releaseList.response.status, 200);
  assert.equal(releaseList.body.app, null);
  assert.equal(releaseList.body.core, null);

  const rotated = await request(`/api/v1/customers/${customerId}/api-token/rotate`, {
    method: 'POST',
    headers: {
      Cookie: sessionCookie,
      'X-Request-ID': 'contract-token-rotation',
      'Idempotency-Key': 'contract-token-rotation-1',
    },
  });
  assert.equal(rotated.response.status, 200);
  assert.equal(typeof rotated.body.api_token_once, 'string');
  assert.notEqual(rotated.body.api_token_once, customerToken);
  const oldTokenRejected = await request('/api/v1/integration/public-key', {
    headers: {
      Authorization: `Bearer ${customerToken}`,
      'X-Request-ID': 'contract-old-token-rejected',
    },
  });
  assert.equal(oldTokenRejected.response.status, 401);
});
