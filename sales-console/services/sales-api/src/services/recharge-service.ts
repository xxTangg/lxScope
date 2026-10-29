import crypto from 'node:crypto';

import { isRechargeCodeSignatureValid, safeMoney, signRechargeCode } from '../business.js';
import type {
  SalesCollectionName,
  SalesRepository,
  SalesTransaction,
} from '../repositories/sales-repository.js';
import type { IdempotencyRecord } from '../store.js';
import type { Customer, RechargeOrder, Staff } from '../types.js';

const rechargeCodeTtlMs = 60 * 60 * 1000;

export interface IdempotencyContext {
  scope?: string;
  key?: string;
}

export interface OnlineRechargeInput extends IdempotencyContext {
  requestID: string;
  amount: number;
  note: string;
  requestedAt?: number;
}

export interface ApprovalInput extends IdempotencyContext {
  requestID?: string;
  amount?: number;
  reason?: string;
  strictContract?: boolean;
  prepareForPending?: () => {
    requestID?: string;
    amount?: number;
    reason?: string;
    strictContract: boolean;
  };
}

export interface AckInput extends IdempotencyContext {
  operationID: string;
  redemptionOperationID?: string;
  ledgerID?: string;
  strictContract: boolean;
}

export interface RechargeService {
  submitOnline(customer: Customer, input: OnlineRechargeInput): Promise<RechargeOrder>;
  issueOffline(
    customerID: string,
    input: { requestID: string; amount: number; staff: Staff } & IdempotencyContext,
  ): Promise<RechargeOrder>;
  reissueOffline(
    customerID: string,
    orderID: string,
    context?: IdempotencyContext,
  ): Promise<RechargeOrder>;
  decideOnline(
    orderID: string,
    action: 'approve' | 'reject',
    input: ApprovalInput,
    staff: Staff,
  ): Promise<RechargeOrder>;
  poll(
    customer: Customer,
    input: { includeRejected: boolean; strictContract: boolean },
  ): Promise<RechargeOrder[]>;
  acknowledge(
    customer: Customer,
    orderID: string,
    input: AckInput,
  ): Promise<{
    ok: true;
    status: 'approved' | 'rejected';
    deliveryStatus: 'delivered' | 'not_delivered';
  }>;
}

function httpError(status: number, message: string): Error & { status: number } {
  return Object.assign(new Error(message), { status });
}

function includedCollections(...collections: SalesCollectionName[]): SalesCollectionName[] {
  return collections;
}

function withIdempotency(
  collections: SalesCollectionName[],
  context: IdempotencyContext,
): SalesCollectionName[] {
  return context.scope && context.key ? [...collections, 'idempotency'] : collections;
}

function activeIdempotencyRecord(
  transaction: SalesTransaction,
  context: IdempotencyContext,
): IdempotencyRecord | undefined {
  if (!context.scope || !context.key) return undefined;
  const now = Date.now();
  return transaction
    .get('idempotency')
    .find(
      (item) => item.scope === context.scope && item.key === context.key && item.expiresAt > now,
    );
}

function bindIdempotencyOperation(
  transaction: SalesTransaction,
  context: IdempotencyContext,
  operationID: string,
): void {
  if (!context.scope || !context.key) return;
  const records = transaction.get('idempotency');
  const now = Date.now();
  let found = false;
  const next = records.map((item) => {
    if (item.scope !== context.scope || item.key !== context.key) return item;
    found = true;
    return { ...item, operationID };
  });
  if (!found) {
    next.unshift({
      scope: context.scope,
      key: context.key,
      requestHash: '',
      statusCode: 0,
      responseBody: null,
      operationID,
      createdAt: now,
      expiresAt: now + 24 * 60 * 60 * 1000,
    });
  }
  transaction.set('idempotency', next.slice(0, 10_000));
}

function orderForOperation(
  transaction: SalesTransaction,
  context: IdempotencyContext,
): RechargeOrder | undefined {
  const record = activeIdempotencyRecord(transaction, context);
  if (!record?.operationID) return undefined;
  const order = transaction.get('orders').find((item) => item.id === record.operationID);
  if (!order) throw httpError(409, '幂等记录已存在，但对应订单未找到');
  return order;
}

function validateOrderCustomer(order: RechargeOrder, customerID: string): void {
  if (order.customerID !== customerID) throw httpError(409, '幂等键已用于其他客户订单');
}

function requireUsableCustomer(customer: Customer | undefined): asserts customer is Customer {
  if (!customer || customer.status === 'disabled') throw httpError(404, '客户不存在或已停用');
  if (!customer.systemId) throw httpError(400, '请先登记客户系统 ID');
}

async function reissueCode(
  order: RechargeOrder,
  systemID: string,
  expiresAt = Date.now() + rechargeCodeTtlMs,
): Promise<void> {
  if (
    order.method !== 'code' ||
    order.status !== 'issued' ||
    order.delivered ||
    typeof order.amount !== 'number' ||
    !Number.isFinite(order.amount) ||
    order.amount <= 0 ||
    typeof order.tokens !== 'number' ||
    !Number.isInteger(order.tokens) ||
    order.tokens <= 0
  ) {
    throw httpError(409, '该订单不是可重新签发的一次性充值码');
  }
  order.expiresAt = expiresAt;
  order.code = await signRechargeCode(order.amount, order.tokens, systemID, order.id, expiresAt);
}

async function refreshOnlineCode(
  order: RechargeOrder,
  systemID: string,
  expiresAt = Date.now() + rechargeCodeTtlMs,
): Promise<void> {
  if (
    order.method !== 'online' ||
    order.status !== 'approved' ||
    order.delivered ||
    typeof order.amount !== 'number' ||
    !Number.isFinite(order.amount) ||
    order.amount <= 0 ||
    typeof order.tokens !== 'number' ||
    !Number.isInteger(order.tokens) ||
    order.tokens <= 0
  ) {
    throw httpError(409, '该在线充值订单当前不可重新签发交付码');
  }
  order.expiresAt = expiresAt;
  order.code = await signRechargeCode(order.amount, order.tokens, systemID, order.id, expiresAt);
}

export function createRechargeService(repository: SalesRepository): RechargeService {
  return {
    async submitOnline(customer, input) {
      return repository.transaction(
        withIdempotency(includedCollections('orders'), input),
        async (transaction) => {
          const replay = orderForOperation(transaction, input);
          if (replay) {
            validateOrderCustomer(replay, customer.id);
            if (replay.method !== 'online' || replay.requestedAmount !== input.amount) {
              throw httpError(409, '重复请求内容不一致');
            }
            return replay;
          }
          const order: RechargeOrder = {
            id: crypto.randomUUID(),
            requestID: input.requestID,
            customerID: customer.id,
            method: 'online',
            status: 'pending',
            requestedAt: input.requestedAt,
            requestedAmount: input.amount,
            note: input.note,
            createdAt: Date.now(),
          };
          transaction.set('orders', [order, ...transaction.get('orders')]);
          bindIdempotencyOperation(transaction, input, order.id);
          return order;
        },
      );
    },

    async issueOffline(customerID, input) {
      const collections = withIdempotency(
        includedCollections('customers', 'orders', 'settings'),
        input,
      );
      return repository.transaction(collections, async (transaction) => {
        const replay = orderForOperation(transaction, input);
        if (replay) {
          validateOrderCustomer(replay, customerID);
          if (replay.method !== 'code' || replay.amount !== input.amount) {
            throw httpError(409, '重复请求内容不一致');
          }
          return replay;
        }

        const customers = transaction.get('customers');
        const customer = customers.find((item) => item.id === customerID);
        requireUsableCustomer(customer);
        const orders = transaction.get('orders');
        const previous = orders.find((order) => order.requestID === input.requestID);
        if (previous) {
          if (previous.customerID !== customer.id || previous.amount !== input.amount) {
            throw httpError(409, '重复请求内容不一致');
          }
          if (
            previous.method === 'code' &&
            previous.status === 'issued' &&
            !previous.delivered &&
            !(await isRechargeCodeSignatureValid(previous.code ?? ''))
          ) {
            await reissueCode(previous, customer.systemId);
            transaction.set('orders', orders);
          }
          bindIdempotencyOperation(transaction, input, previous.id);
          return previous;
        }

        const { tokenExchangeRate } = transaction.get('settings');
        const tokens = Math.round(input.amount * tokenExchangeRate);
        const id = crypto.randomUUID();
        const expiresAt = Date.now() + rechargeCodeTtlMs;
        const code = await signRechargeCode(input.amount, tokens, customer.systemId, id, expiresAt);
        const updatedCustomer = {
          ...customer,
          totalRecharged: customer.totalRecharged + input.amount,
          updatedAt: Date.now(),
        };
        const order: RechargeOrder = {
          id,
          requestID: input.requestID,
          customerID: customer.id,
          amount: input.amount,
          tokens,
          method: 'code',
          status: 'issued',
          code,
          expiresAt,
          createdAt: Date.now(),
          processedBy: input.staff.username,
        };
        transaction.set(
          'customers',
          customers.map((item) => (item.id === customer.id ? updatedCustomer : item)),
        );
        transaction.set('orders', [order, ...orders]);
        bindIdempotencyOperation(transaction, input, order.id);
        return order;
      });
    },

    async reissueOffline(customerID, orderID, context = {}) {
      return repository.transaction(
        withIdempotency(includedCollections('customers', 'orders'), context),
        async (transaction) => {
          const replay = orderForOperation(transaction, context);
          if (replay) {
            validateOrderCustomer(replay, customerID);
            if (replay.id !== orderID) throw httpError(409, '幂等键已用于其他充值订单');
            return replay;
          }
          const customer = transaction.get('customers').find((item) => item.id === customerID);
          requireUsableCustomer(customer);
          const orders = transaction.get('orders');
          const order = orders.find(
            (item) => item.id === orderID && item.customerID === customer.id,
          );
          if (!order) throw httpError(404, '充值订单不存在');
          await reissueCode(order, customer.systemId);
          transaction.set('orders', orders);
          bindIdempotencyOperation(transaction, context, order.id);
          return order;
        },
      );
    },

    async decideOnline(orderID, action, input, staff) {
      return repository.transaction(
        withIdempotency(includedCollections('orders', 'customers', 'settings'), input),
        async (transaction) => {
          const replay = orderForOperation(transaction, input);
          if (replay) {
            if (replay.id !== orderID) throw httpError(409, '幂等键已用于其他充值订单');
            return replay;
          }
          const orders = transaction.get('orders');
          const order = orders.find((item) => item.id === orderID);
          if (!order || order.method !== 'online') throw httpError(404, '申请不存在');
          if (
            (action === 'approve' && order.status === 'approved') ||
            (action === 'reject' && order.status === 'rejected')
          ) {
            bindIdempotencyOperation(transaction, input, order.id);
            return order;
          }
          if (order.status !== 'pending') throw httpError(409, '申请已处理');
          const approval = input.prepareForPending?.() ?? input;
          if (approval.requestID && order.requestID && approval.requestID !== order.requestID) {
            throw httpError(409, 'request_id 与充值申请不匹配');
          }
          if (approval.strictContract && !order.requestID) {
            throw httpError(409, '原始充值申请缺少 request_id');
          }
          if (!order.requestID && approval.requestID) order.requestID = approval.requestID;
          else if (!order.requestID && approval.strictContract) {
            throw httpError(409, '原始充值申请缺少 request_id');
          }
          if (approval.reason) order.decisionReason = approval.reason.slice(0, 500);

          if (action === 'reject') {
            order.status = 'rejected';
            order.processedAt = Date.now();
            order.processedBy = staff.username;
            transaction.set('orders', orders);
            bindIdempotencyOperation(transaction, input, order.id);
            return order;
          }

          const amount = approval.amount;
          if (amount === undefined || safeMoney(amount) !== amount) {
            throw httpError(400, '金额不合法');
          }
          const customers = transaction.get('customers');
          const customer = customers.find((item) => item.id === order.customerID);
          if (!customer || customer.status === 'disabled' || !customer.systemId) {
            throw httpError(400, '客户不可用或缺少系统 ID');
          }
          const { tokenExchangeRate } = transaction.get('settings');
          const expiresAt = Date.now() + rechargeCodeTtlMs;
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
          order.processedBy = staff.username;
          const updatedCustomer = {
            ...customer,
            totalRecharged: customer.totalRecharged + amount,
            updatedAt: Date.now(),
          };
          transaction.set('orders', orders);
          transaction.set(
            'customers',
            customers.map((item) => (item.id === customer.id ? updatedCustomer : item)),
          );
          bindIdempotencyOperation(transaction, input, order.id);
          return order;
        },
      );
    },

    async poll(customer, { includeRejected, strictContract }) {
      return repository.transaction(['orders'], async (transaction) => {
        const orders = transaction.get('orders');
        const now = Date.now();
        const approved = orders.filter(
          (item) =>
            item.customerID === customer.id && item.status === 'approved' && !item.delivered,
        );
        const rejected = includeRejected
          ? orders.filter(
              (item) =>
                item.customerID === customer.id &&
                item.status === 'rejected' &&
                !item.decisionAcknowledged,
            )
          : [];
        const approvedFound = strictContract
          ? approved.filter((item) => {
              const valid =
                typeof item.amount === 'number' &&
                Number.isFinite(item.amount) &&
                item.amount > 0 &&
                typeof item.tokens === 'number' &&
                Number.isInteger(item.tokens) &&
                item.tokens > 0;
              if (!valid) return false;
              if (item.method === 'online') return true;
              return Boolean(item.code && item.expiresAt && item.expiresAt > now);
            })
          : approved.slice(0, 1);
        const found = [...approvedFound, ...rejected];
        if (!found.length) return found;
        for (const item of found) {
          if (item.status === 'rejected') continue;
          const valid =
            typeof item.amount === 'number' &&
            Number.isFinite(item.amount) &&
            item.amount > 0 &&
            typeof item.tokens === 'number' &&
            Number.isInteger(item.tokens) &&
            item.tokens > 0;
          const codeExpired = typeof item.expiresAt !== 'number' || item.expiresAt <= now;
          const signatureValid =
            typeof item.code === 'string' && (await isRechargeCodeSignatureValid(item.code));
          if (item.method === 'online' && valid && (codeExpired || !signatureValid)) {
            await refreshOnlineCode(item, customer.systemId);
          } else if (
            valid &&
            !codeExpired &&
            !signatureValid &&
            typeof item.expiresAt === 'number'
          ) {
            item.code = await signRechargeCode(
              item.amount!,
              item.tokens!,
              customer.systemId,
              item.id,
              item.expiresAt,
            );
          }
          item.deliveryAttempts = (item.deliveryAttempts ?? 0) + 1;
          item.lastDeliveryAt = now;
        }
        if (approvedFound.length) transaction.set('orders', orders);
        return found;
      });
    },

    async acknowledge(customer, orderID, input) {
      return repository.transaction(
        withIdempotency(includedCollections('orders'), input),
        async (transaction) => {
          const replay = orderForOperation(transaction, input);
          if (replay) {
            validateOrderCustomer(replay, customer.id);
            const status = replay.status === 'rejected' ? 'rejected' : 'approved';
            return {
              ok: true,
              status,
              deliveryStatus: status === 'approved' ? 'delivered' : 'not_delivered',
            };
          }
          const orders = transaction.get('orders');
          const order = orders.find(
            (item) => item.id === orderID && item.customerID === customer.id,
          );
          if (!order || !['approved', 'rejected'].includes(order.status)) {
            throw httpError(404, '充值订单不存在');
          }
          if (order.status === 'rejected') {
            order.decisionAcknowledged = true;
          } else {
            if (input.strictContract && (!input.redemptionOperationID || !input.ledgerID)) {
              throw httpError(400, '批准订单 ACK 缺少兑换或账本信息');
            }
            order.delivered = true;
            order.deliveredAt = order.deliveredAt ?? Date.now();
            order.ackOperationID = (order.ackOperationID ?? input.operationID) || undefined;
            order.redemptionOperationID =
              order.redemptionOperationID ?? input.redemptionOperationID ?? undefined;
            order.ledgerID = order.ledgerID ?? input.ledgerID ?? undefined;
          }
          transaction.set('orders', orders);
          bindIdempotencyOperation(transaction, input, order.id);
          const status = order.status === 'rejected' ? 'rejected' : 'approved';
          return {
            ok: true,
            status,
            deliveryStatus: status === 'approved' ? 'delivered' : 'not_delivered',
          };
        },
      );
    },
  };
}
