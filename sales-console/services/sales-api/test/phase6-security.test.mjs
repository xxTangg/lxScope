import assert from 'node:assert/strict';
import test from 'node:test';

const business = await import('../dist/business.js');

function customer(id, tenantId, systemId) {
  return {
    id,
    tenantId,
    systemId,
    name: id,
    environment: 'test',
    protocol: 'https',
    ip: '',
    port: 3443,
    baseUrl: null,
    contact: '',
    notes: '',
    apiToken: `${id}-token`,
    totalRecharged: 0,
    status: 'active',
    lastSeenIP: null,
    lastSeenAt: null,
    lastReport: null,
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
}

test('tenant/customer/system mapping rejects cross-tenant identity reuse', () => {
  const existing = [customer('customer-a', 'tenant-a', 'system-a')];
  assert.deepEqual(
    business.customerIdentityMapping(existing[0]),
    { tenantId: 'tenant-a', customerId: 'customer-a', systemId: 'system-a' },
  );
  assert.throws(
    () => business.assertCustomerIdentityMapping(existing, customer('customer-b', 'tenant-a', 'system-b')),
    /tenant.*customer/,
  );
  assert.throws(
    () => business.assertCustomerIdentityMapping(existing, customer('customer-b', 'tenant-b', 'system-a')),
    /system.*customer/,
  );
  assert.doesNotThrow(() =>
    business.assertCustomerIdentityMapping(existing, customer('customer-b', 'tenant-b', 'system-b')),
  );
});
