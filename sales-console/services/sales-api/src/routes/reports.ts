import crypto from 'node:crypto';
import type { Express, Request } from 'express';

import type { ReportService } from '../services/report-service.js';
import type { Customer, UsageReport } from '../types.js';

export interface ReportRouteDependencies {
  service: ReportService;
  customerFromBearer(request: Request): Promise<Customer | null>;
  requestID(request: Request): string;
  requiredRequestID(request: Request): string;
  requiredText(value: unknown, field: string, maxLength?: number): string;
  requiredContractMoney(value: unknown, field: string, allowZero?: boolean): number;
  requiredISOTime(value: unknown, field: string): number;
}

export function registerPublicReportRoutes(
  app: Express,
  dependencies: ReportRouteDependencies,
): void {
  const {
    service,
    customerFromBearer,
    requestID,
    requiredRequestID,
    requiredText,
    requiredContractMoney,
    requiredISOTime,
  } = dependencies;

  app.post('/api/reports', async (request, response, next) => {
    try {
      const customer = await customerFromBearer(request);
      if (!customer) {
        response.status(401).json({ error: '无效的客户访问令牌' });
        return;
      }
      const canonical = request.headers['x-canonical-api'] === '1';
      const reportRequestID = canonical ? requiredRequestID(request) : requestID(request);
      const systemID = canonical
        ? requiredText(request.body.systemId, 'system_id')
        : String(request.body.systemId ?? '').trim();
      const poolTokens = Number(request.body.poolTokens);
      const totalRecharged = canonical
        ? requiredContractMoney(request.body.totalRecharged, 'total_recharged', true)
        : Number(request.body.totalRecharged);
      const appVersion = canonical
        ? requiredText(request.body.appVersion, 'app_version')
        : String(request.body.appVersion ?? '');
      const clientReportedAt =
        canonical || request.body.clientReportedAt !== undefined
          ? requiredISOTime(request.body.clientReportedAt, 'client_reported_at')
          : undefined;
      if (
        !Number.isSafeInteger(poolTokens) ||
        poolTokens < 0 ||
        !Number.isFinite(totalRecharged) ||
        totalRecharged < 0 ||
        (canonical && (!customer.systemId || systemID !== customer.systemId)) ||
        (!canonical && systemID && customer.systemId && customer.systemId !== systemID)
      ) {
        response.status(400).json({ error: '参数不合法' });
        return;
      }
      const report: UsageReport = {
        id: canonical ? crypto.randomUUID() : undefined,
        customerID: customer.id,
        systemId: canonical ? customer.systemId! : systemID,
        poolTokens,
        totalRecharged,
        cumulativeConsumed:
          Number.isSafeInteger(request.body.cumulativeConsumed) &&
          request.body.cumulativeConsumed >= 0
            ? Number(request.body.cumulativeConsumed)
            : null,
        cumulativeCredits:
          Number.isSafeInteger(request.body.cumulativeCredits) &&
          request.body.cumulativeCredits >= 0
            ? Number(request.body.cumulativeCredits)
            : null,
        appVersion,
        clientReportedAt,
        reportedAt: Date.now(),
        observedIP: request.socket.remoteAddress ?? undefined,
      };
      const result = await service.ingest(customer, report);
      if (canonical) {
        response.json({ ...result, requestId: reportRequestID });
      } else {
        response.json({ ok: true });
      }
    } catch (error) {
      next(error);
    }
  });
}

export function registerStaffReportRoutes(app: Express, service: ReportService): void {
  app.get('/api/reconciliation', async (request, response, next) => {
    try {
      const from = Number(request.query.from) || 0;
      const to = Number(request.query.to) || Date.now();
      response.json(await service.reconciliation(from, to));
    } catch (error) {
      next(error);
    }
  });

  app.get('/api/customers/:id/usage', async (request, response, next) => {
    try {
      response.json(await service.customerReports(request.params.id));
    } catch (error) {
      next(error);
    }
  });

  app.get('/api/alerts', async (_request, response, next) => {
    try {
      response.json(await service.alerts());
    } catch (error) {
      next(error);
    }
  });
}
