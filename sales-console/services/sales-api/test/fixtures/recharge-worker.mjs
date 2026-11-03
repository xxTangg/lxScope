import { createRechargeService } from '../../dist/services/recharge-service.js';
import { salesRepository } from '../../dist/repositories/sales-repository.js';
import { closeDataStore, ensureData, getSigningKeyPair } from '../../dist/store.js';

async function run() {
  await ensureData();
  await getSigningKeyPair();
  const service = createRechargeService(salesRepository);
  const [action, serializedInput] = process.argv.slice(2);
  const input = JSON.parse(serializedInput ?? '{}');

  if (action === 'submit') {
    const customer = (await salesRepository.read('customers')).find(
      (item) => item.id === input.customerID,
    );
    const order = await service.submitOnline(customer, input);
    return { orderID: order.id, status: order.status };
  }
  if (action === 'approve') {
    const order = await service.decideOnline(input.orderID, 'approve', input, {
      username: 'integration-worker',
    });
    return { orderID: order.id, status: order.status };
  }
  if (action === 'issue') {
    const order = await service.issueOffline(input.customerID, {
      ...input,
      staff: { username: 'integration-worker' },
    });
    return { orderID: order.id, status: order.status };
  }
  throw new Error(`Unknown worker action: ${action}`);
}

try {
  const result = await run();
  process.stdout.write(`${JSON.stringify(result)}\n`);
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
  process.exitCode = 1;
} finally {
  await closeDataStore();
}
