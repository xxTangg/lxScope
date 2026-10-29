import { measuredConsumption } from '../business.js';
import type { SalesRepository } from '../repositories/sales-repository.js';
import type { Customer, UsageReport } from '../types.js';

export interface IngestedReportResult {
  reportId?: string;
  systemId: string;
  accepted: true;
  reportedAt: number;
  cumulativeConsumedDelta: number;
  cumulativeCreditsDelta: number;
}

export interface ReportService {
  ingest(customer: Customer, report: UsageReport): Promise<IngestedReportResult>;
  reconciliation(from: number, to: number): Promise<unknown[]>;
  customerReports(customerID: string): Promise<UsageReport[]>;
  alerts(): Promise<unknown[]>;
}

export function createReportService(repository: SalesRepository): ReportService {
  return {
    async ingest(customer, report) {
      return repository.transaction(['reports', 'customers'], async (transaction) => {
        const reports = transaction.get('reports');
        const previous = reports.find((item) => item.customerID === customer.id);
        const customers = transaction.get('customers');
        const target = customers.find((item) => item.id === customer.id);
        if (target) {
          target.lastReport = report;
          if (!target.systemId && report.systemId) target.systemId = report.systemId;
          target.lastSeenIP = report.observedIP ?? null;
          target.lastSeenAt = report.reportedAt;
          target.updatedAt = Date.now();
        }
        transaction.set('reports', [report, ...reports].slice(0, 20_000));
        transaction.set('customers', customers);
        const previousConsumed = previous?.cumulativeConsumed ?? null;
        const previousCredits = previous?.cumulativeCredits ?? null;
        return {
          reportId: report.id,
          systemId: report.systemId,
          accepted: true,
          reportedAt: report.reportedAt,
          cumulativeConsumedDelta:
            report.cumulativeConsumed !== null && previousConsumed !== null
              ? report.cumulativeConsumed - previousConsumed
              : 0,
          cumulativeCreditsDelta:
            report.cumulativeCredits !== null && previousCredits !== null
              ? report.cumulativeCredits - previousCredits
              : 0,
        };
      });
    },

    async reconciliation(from, to) {
      const [customers, reports, orders] = await Promise.all([
        repository.read('customers'),
        repository.read('reports'),
        repository.read('orders'),
      ]);
      const allOrders = orders.filter(
        (order) =>
          ['issued', 'approved'].includes(order.status) &&
          (order.processedAt ?? order.createdAt) >= from &&
          (order.processedAt ?? order.createdAt) <= to,
      );
      return customers.map((customer) => {
        const ownOrders = allOrders.filter((order) => order.customerID === customer.id);
        const inWindow = reports
          .filter(
            (report) =>
              report.customerID === customer.id &&
              report.reportedAt >= from &&
              report.reportedAt <= to,
          )
          .sort((a, b) => a.reportedAt - b.reportedAt);
        return {
          customerID: customer.id,
          name: customer.name,
          rechargedAmount: ownOrders.reduce((sum, order) => sum + (order.amount ?? 0), 0),
          rechargedTokens: ownOrders.reduce((sum, order) => sum + (order.tokens ?? 0), 0),
          consumedTokens: measuredConsumption(inWindow),
        };
      });
    },

    async customerReports(customerID) {
      return (await repository.read('reports'))
        .filter((report) => report.customerID === customerID)
        .sort((a, b) => a.reportedAt - b.reportedAt)
        .slice(-200);
    },

    async alerts() {
      const [customers, reports] = await Promise.all([
        repository.read('customers'),
        repository.read('reports'),
      ]);
      const alerts: Array<{
        customerID: string;
        name: string;
        poolTokens: number;
        daysLeft: number;
      }> = [];
      for (const customer of customers) {
        const recent = reports
          .filter((report) => report.customerID === customer.id)
          .sort((a, b) => b.reportedAt - a.reportedAt)
          .slice(0, 10);
        if (recent.length < 2) continue;
        const ordered = [...recent].reverse();
        const consumed = measuredConsumption(ordered);
        if (consumed === null) continue;
        const days = Math.max(
          0.1,
          (ordered.at(-1)!.reportedAt - ordered[0].reportedAt) / 86_400_000,
        );
        const perDay = consumed / days;
        const daysLeft = perDay > 0 ? recent[0].poolTokens / perDay : Number.POSITIVE_INFINITY;
        if (daysLeft < 14) {
          alerts.push({
            customerID: customer.id,
            name: customer.name,
            poolTokens: recent[0].poolTokens,
            daysLeft: Math.round(daysLeft),
          });
        }
      }
      return alerts.sort((a, b) => a.daysLeft - b.daysLeft);
    },
  };
}
