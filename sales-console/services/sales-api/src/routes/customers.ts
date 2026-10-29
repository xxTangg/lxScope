import crypto from 'node:crypto';
import type { Express, Request, RequestHandler } from 'express';

import type { CustomerService } from '../services/customer-service.js';
import type { Customer, RechargeOrder, Staff } from '../types.js';

export interface CustomerRouteDependencies {
  service: CustomerService;
  requireStaff: RequestHandler;
  readOrders(): Promise<RechargeOrder[]>;
  enrichCustomer(customer: Customer): unknown;
  customerConnectionUrls(customer: Customer): string[];
  requiredRequestID(request: Request): string;
  audit(staff: Staff, request: Request): Promise<void>;
  callCustomer(
    customer: Customer,
    endpoint: string,
    body: unknown,
    options?: { requestID?: string; idempotencyKey?: string },
  ): Promise<Record<string, unknown>>;
}

function authenticatedStaff(request: Request): Staff {
  if (!request.staff) throw Object.assign(new Error('请先登录'), { status: 401 });
  return request.staff;
}

export function registerCanonicalCustomerRoutes(
  app: Express,
  dependencies: CustomerRouteDependencies,
): void {
  const {
    service,
    requireStaff,
    enrichCustomer,
    customerConnectionUrls,
    requiredRequestID,
    callCustomer,
  } = dependencies;

  app.get('/api/v1/customers/:id', requireStaff, async (request, response, next) => {
    try {
      const customer = await service.get(request.params.id);
      if (!customer) {
        response
          .status(404)
          .json({ detail: { code: 'customer_not_found', message: '客户不存在' } });
        return;
      }
      response.json({ customer: enrichCustomer(customer), requestId: crypto.randomUUID() });
    } catch (error) {
      next(error);
    }
  });

  app.post(
    '/api/v1/customers/:id/api-token/rotate',
    requireStaff,
    async (request, response, next) => {
      try {
        const rotated = await service.rotateToken(request.params.id);
        if (!rotated) {
          response
            .status(404)
            .json({ detail: { code: 'customer_not_found', message: '客户不存在' } });
          return;
        }
        await dependencies.audit(authenticatedStaff(request), request);
        response.json({
          customer: {
            customerId: rotated.id,
            systemId: rotated.systemId,
            name: rotated.name,
            status: rotated.status,
          },
          apiTokenOnce: rotated.apiToken,
          tokenExpiresAt: null,
          requestId: crypto.randomUUID(),
        });
      } catch (error) {
        next(error);
      }
    },
  );

  app.post(
    '/api/v1/customers/:id/verify-connection',
    requireStaff,
    async (request, response, next) => {
      try {
        const verifyRequestID = requiredRequestID(request);
        const customer = await service.get(request.params.id);
        if (!customer) {
          response
            .status(404)
            .json({ detail: { code: 'customer_not_found', message: '客户不存在' } });
          return;
        }
        if (!customerConnectionUrls(customer).length) {
          response.json({
            customerId: customer.id,
            systemId: customer.systemId,
            outbound: false,
            inbound: false,
            inboundError: '客户尚未配置可连接地址',
            checkedAt: Date.now(),
            requestId: verifyRequestID,
          });
          return;
        }
        try {
          const ping = await callCustomer(
            customer,
            '/integration/sales/v1/ping',
            {},
            { requestID: verifyRequestID },
          );
          response.json({
            customerId: customer.id,
            systemId: customer.systemId,
            outbound: true,
            inbound: true,
            systemName: customer.name,
            ping,
            checkedAt: Date.now(),
            requestId: verifyRequestID,
          });
        } catch (error) {
          response.json({
            customerId: customer.id,
            systemId: customer.systemId,
            outbound: true,
            inbound: false,
            inboundError: error instanceof Error ? error.message : '连接失败',
            checkedAt: Date.now(),
            requestId: verifyRequestID,
          });
        }
      } catch (error) {
        next(error);
      }
    },
  );
}

export function registerStaffCustomerRoutes(
  app: Express,
  dependencies: CustomerRouteDependencies,
): void {
  const { service, readOrders, enrichCustomer, customerConnectionUrls, audit, callCustomer } =
    dependencies;

  app.get('/api/customers', async (request, response, next) => {
    try {
      const query = request.query;
      const keyword = String(query.keyword ?? '')
        .trim()
        .toLowerCase();
      const status = String(query.status ?? '').trim();
      const environment = String(query.environment ?? '').trim();
      const filtered = (await service.list()).filter((customer) => {
        if (
          keyword &&
          !`${customer.name} ${customer.systemId} ${customer.contact}`
            .toLowerCase()
            .includes(keyword)
        ) {
          return false;
        }
        if (status && customer.status !== status) return false;
        if (environment && customer.environment !== environment) return false;
        return true;
      });
      const page = Math.max(1, Number(query.page) || 1);
      const pageSize = Math.min(1000, Math.max(1, Number(query.page_size) || filtered.length || 1));
      const items =
        query.page !== undefined || query.page_size !== undefined
          ? filtered.slice((page - 1) * pageSize, page * pageSize)
          : filtered;
      if (request.headers['x-canonical-api'] === '1') {
        response.json({
          customers: items.map(enrichCustomer),
          total: filtered.length,
          page,
          pageSize,
        });
        return;
      }
      response.json(filtered.map(enrichCustomer));
    } catch (error) {
      next(error);
    }
  });

  app.post('/api/customers', async (request, response, next) => {
    try {
      const customer = await service.create(request.body);
      await audit(authenticatedStaff(request), request);
      response.status(201).json(customer);
    } catch (error) {
      next(error);
    }
  });

  app.patch('/api/customers/:id', async (request, response, next) => {
    try {
      const updated = await service.update(request.params.id, request.body);
      if (!updated) {
        response.status(404).json({ error: '客户不存在' });
        return;
      }
      await audit(authenticatedStaff(request), request);
      response.json(enrichCustomer(updated));
    } catch (error) {
      next(error);
    }
  });

  app.delete('/api/customers/:id', async (request, response, next) => {
    try {
      await service.remove(request.params.id);
      await audit(authenticatedStaff(request), request);
      response.json({ ok: true });
    } catch (error) {
      next(error);
    }
  });

  app.get('/api/customers/:id/api-token', async (request, response, next) => {
    try {
      const customer = await service.get(request.params.id);
      if (!customer) {
        response.status(404).json({ error: '客户不存在' });
        return;
      }
      await audit(authenticatedStaff(request), request);
      response.json({ apiToken: customer.apiToken });
    } catch (error) {
      next(error);
    }
  });

  app.post('/api/customers/:id/reset-admin-password', async (request, response, next) => {
    try {
      const customer = await service.get(request.params.id);
      if (!customer) {
        response.status(404).json({ error: '客户不存在' });
        return;
      }
      if (!customerConnectionUrls(customer).length) {
        response.status(400).json({ error: '这个客户还没有配置可连接地址，无法远程连接' });
        return;
      }
      const username = String(request.body.username ?? '').trim();
      if (!username) {
        response.status(400).json({ error: '请填写要重置的管理员用户名' });
        return;
      }
      const newPassword = `${crypto.randomBytes(9).toString('base64').replace(/[+/=]/g, '').slice(0, 12)}aA1`;
      const operationID = crypto.randomUUID();
      await callCustomer(
        customer,
        '/integration/sales/v1/admin-password-resets',
        {
          operationId: operationID,
          username,
          newPassword,
          expiresAt: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
        },
        { idempotencyKey: operationID },
      );
      await audit(authenticatedStaff(request), request);
      response.json({ ok: true, username, newPassword });
    } catch (error) {
      next(error);
    }
  });

  app.get('/api/customers/:id/recharge-orders', async (request, response, next) => {
    try {
      response.json((await readOrders()).filter((order) => order.customerID === request.params.id));
    } catch (error) {
      next(error);
    }
  });
}
