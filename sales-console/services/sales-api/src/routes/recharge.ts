import type { Express, Request } from 'express';

import type { RechargeService } from '../services/recharge-service.js';
import type { Customer, RechargeOrder, Staff } from '../types.js';

export interface RechargeRouteDependencies {
  service: RechargeService;
  customerFromBearer(request: Request): Promise<Customer | null>;
  readCustomers(): Promise<Customer[]>;
  readOrders(): Promise<RechargeOrder[]>;
  requestID(request: Request): string;
  requiredRequestID(request: Request): string;
  requiredText(value: unknown, field: string, maxLength?: number): string;
  optionalContractText(value: unknown, field: string, maxLength: number): string;
  requiredContractAmount(value: unknown): number;
  requiredISOTime(value: unknown, field: string): number;
  requiredSystemQuery(request: Request, customer: Customer): string;
  optionalRequestID(value: unknown): string | undefined;
  validateAmount(value: unknown): number;
  tierFor(totalRecharged: number): unknown;
  audit(staff: Staff, request: Request): Promise<void>;
}

function strictContract(request: Request): boolean {
  return request.headers['x-canonical-api'] === '1';
}

function idempotencyContext(request: Request): { scope?: string; key?: string } {
  if (!strictContract(request)) return {};
  const scope = String(request.headers['x-idempotency-scope'] ?? '').trim();
  const key = String(request.headers['idempotency-key'] ?? '').trim();
  return { scope: scope || undefined, key: key || undefined };
}

function staff(request: Request): Staff {
  if (!request.staff) throw Object.assign(new Error('请先登录'), { status: 401 });
  return request.staff;
}

export function registerPublicRechargeRoutes(
  app: Express,
  dependencies: RechargeRouteDependencies,
): void {
  const {
    service,
    customerFromBearer,
    requiredRequestID,
    requiredText,
    optionalContractText,
    requiredContractAmount,
    requiredISOTime,
    requiredSystemQuery,
    requestID,
  } = dependencies;

  app.post('/api/recharge-requests', async (request, response, next) => {
    try {
      const customer = await customerFromBearer(request);
      if (!customer) {
        response.status(401).json({ error: '无效的客户访问令牌' });
        return;
      }
      const canonical = strictContract(request);
      const operationRequestID = canonical ? requiredRequestID(request) : requestID(request);
      const requestedSystemID = canonical
        ? requiredText(request.body.systemId, 'system_id')
        : String(request.body.systemId ?? '').trim();
      if (
        (canonical && (!customer.systemId || requestedSystemID !== customer.systemId)) ||
        (!canonical &&
          requestedSystemID &&
          customer.systemId &&
          requestedSystemID !== customer.systemId)
      ) {
        response.status(400).json({ error: '系统 ID 与客户令牌不匹配' });
        return;
      }
      const amount = canonical
        ? requiredContractAmount(request.body.amount)
        : dependencies.validateAmount(request.body.amount);
      const note = canonical
        ? optionalContractText(request.body.note, 'note', 300)
        : String(request.body.note ?? '').slice(0, 500);
      const requestedAt =
        canonical || request.body.requestedAt !== undefined
          ? requiredISOTime(request.body.requestedAt, 'requested_at')
          : undefined;
      if (request.body.requestID !== undefined && request.body.requestID !== operationRequestID) {
        response.status(400).json({ error: 'request_id 必须与 X-Request-ID 一致' });
        return;
      }

      const order = await service.submitOnline(customer, {
        ...idempotencyContext(request),
        requestID: operationRequestID,
        amount,
        note,
        requestedAt,
      });
      response.status(201).json({ ok: true, id: order.id, systemId: customer.systemId });
    } catch (error) {
      next(error);
    }
  });

  app.get('/api/recharge-requests/poll', async (request, response, next) => {
    try {
      const customer = await customerFromBearer(request);
      if (!customer) {
        response.status(401).json({ error: '无效的客户访问令牌' });
        return;
      }
      const canonical = strictContract(request);
      const includeRejected =
        canonical && String(request.query.include_rejected ?? '').toLowerCase() === 'true';
      if (canonical) {
        requiredRequestID(request);
        requiredSystemQuery(request, customer);
      }
      const ready = await service.poll(customer, { includeRejected, strictContract: canonical });
      if (!canonical) {
        const first = ready[0];
        response.json(first ? { code: first.code, id: first.id } : {});
        return;
      }
      response.json({
        orders: ready.map((order) =>
          order.status === 'rejected'
            ? {
                id: order.id,
                systemId: customer.systemId,
                status: order.status,
                deliveryStatus: 'not_delivered',
                decisionReason: order.decisionReason,
              }
            : {
                id: order.id,
                systemId: customer.systemId,
                amount: order.amount,
                tokens: order.tokens,
                status: order.status,
                deliveryStatus: order.delivered ? 'delivered' : 'not_delivered',
                code: order.code,
                expiresAt: order.expiresAt,
              },
        ),
      });
    } catch (error) {
      next(error);
    }
  });

  app.get('/api/recharge-codes/legacy', async (request, response, next) => {
    try {
      const customer = await customerFromBearer(request);
      if (!customer) {
        response.status(401).json({ error: '无效的客户访问令牌' });
        return;
      }
      const orders = await dependencies.readOrders();
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
          const payload = JSON.parse(Buffer.from(String(payloadPart), 'base64url').toString()) as {
            systemId?: string;
            system_id?: string;
            nonce?: string;
          };
          if (!payload.systemId && !payload.system_id && typeof payload.nonce === 'string') {
            nonces.push(payload.nonce);
          }
        } catch {
          // Ignore historical recharge codes that do not contain a JSON payload.
        }
      }
      response.json({ systemId: customer.systemId, nonces });
    } catch (error) {
      next(error);
    }
  });

  app.post('/api/recharge-requests/:id/ack', async (request, response, next) => {
    try {
      const customer = await customerFromBearer(request);
      if (!customer) {
        response.status(401).json({ error: '无效的客户访问令牌' });
        return;
      }
      const canonical = strictContract(request);
      if (canonical) requiredRequestID(request);
      const operationID = canonical
        ? requiredText(request.body.operationID ?? request.body.operationId, 'operation_id')
        : String(request.body.operationID ?? '').trim();
      const systemID = canonical
        ? requiredText(request.body.systemId, 'system_id')
        : String(request.body.systemId ?? '').trim();
      const redemptionOperationID = canonical
        ? dependencies.optionalContractText(
            request.body.redemptionOperationID ?? request.body.redemptionOperationId,
            'redemption_operation_id',
            128,
          )
        : String(request.body.redemptionOperationID ?? '').trim();
      const ledgerID = canonical
        ? dependencies.optionalContractText(
            request.body.ledgerID ?? request.body.ledgerId,
            'ledger_id',
            128,
          )
        : String(request.body.ledgerID ?? '').trim();
      if (
        (canonical && (!customer.systemId || systemID !== customer.systemId)) ||
        (!canonical && systemID && customer.systemId && systemID !== customer.systemId)
      ) {
        response.status(400).json({ error: 'system_id 与客户令牌不匹配' });
        return;
      }
      const result = await service.acknowledge(customer, request.params.id, {
        ...idempotencyContext(request),
        operationID,
        redemptionOperationID: redemptionOperationID || undefined,
        ledgerID: ledgerID || undefined,
        strictContract: canonical,
      });
      response.json(result);
    } catch (error) {
      next(error);
    }
  });
}

export function registerStaffRechargeRoutes(
  app: Express,
  dependencies: RechargeRouteDependencies,
): void {
  const { service, readCustomers, readOrders, validateAmount, optionalRequestID, audit, tierFor } =
    dependencies;

  app.post('/api/customers/:id/recharge-codes', async (request, response, next) => {
    try {
      const amount = validateAmount(request.body.amount);
      const operationRequestID = String(
        request.body.requestID ?? request.headers['idempotency-key'] ?? '',
      ).slice(0, 100);
      if (!/^[a-zA-Z0-9_-]{16,100}$/.test(operationRequestID)) {
        response.status(400).json({ error: '缺少充值操作标识' });
        return;
      }
      const result = await service.issueOffline(request.params.id, {
        ...idempotencyContext(request),
        requestID: operationRequestID,
        amount,
        staff: staff(request),
      });
      await audit(staff(request), request);
      const customer = (await readCustomers()).find((item) => item.id === request.params.id);
      response.status(201).json({ order: result, tier: tierFor(customer?.totalRecharged ?? 0) });
    } catch (error) {
      next(error);
    }
  });

  app.post(
    '/api/customers/:id/recharge-codes/:orderId/reissue',
    async (request, response, next) => {
      try {
        const result = await service.reissueOffline(
          request.params.id,
          request.params.orderId,
          idempotencyContext(request),
        );
        await audit(staff(request), request);
        const customer = (await readCustomers()).find((item) => item.id === request.params.id);
        response.json({ order: result, tier: tierFor(customer?.totalRecharged ?? 0) });
      } catch (error) {
        next(error);
      }
    },
  );

  app.get('/api/recharge-requests', async (request, response, next) => {
    try {
      const customers = await readCustomers();
      const status = request.query.status ? String(request.query.status) : undefined;
      response.json(
        (await readOrders())
          .filter((order) => order.method === 'online' && (!status || order.status === status))
          .map((order) => ({
            ...order,
            customerName:
              customers.find((customer) => customer.id === order.customerID)?.name ?? '未知客户',
          })),
      );
    } catch (error) {
      next(error);
    }
  });

  app.post('/api/recharge-requests/:id/:action', async (request, response, next) => {
    try {
      const action = request.params.action;
      if (action !== 'approve' && action !== 'reject') {
        response.status(404).json({ error: '操作不存在' });
        return;
      }
      const result = await service.decideOnline(
        request.params.id,
        action,
        {
          ...idempotencyContext(request),
          prepareForPending: () => {
            const canonical = strictContract(request);
            if (canonical) dependencies.requiredRequestID(request);
            const requestIDValue = canonical
              ? dependencies.requiredText(request.body.requestID, 'request_id')
              : optionalRequestID(request.body.requestID);
            const amount =
              action === 'approve'
                ? canonical
                  ? dependencies.requiredContractAmount(request.body.amount)
                  : validateAmount(request.body.amount)
                : undefined;
            const reason =
              typeof request.body.reason === 'string' ? request.body.reason.trim() : '';
            return {
              requestID:
                requestIDValue ?? (canonical ? undefined : dependencies.requestID(request)),
              amount,
              reason: reason || undefined,
              strictContract: canonical,
            };
          },
        },
        staff(request),
      );
      response.json(result);
      // The financial decision has committed; a transient audit failure must not report it as failed.
      void audit(staff(request), request).catch((error: unknown) => {
        console.error('充值审批审计日志写入失败（审批已完成）', error);
      });
    } catch (error) {
      next(error);
    }
  });
}
