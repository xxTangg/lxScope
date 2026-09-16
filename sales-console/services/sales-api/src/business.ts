import crypto from 'node:crypto';

import { getSigningKeyPair } from './store.js';
import type { Customer, RechargeOrder, UsageReport } from './types.js';

export function tierFor(totalRecharged: number): { admins: number; accounts: number } {
  if (totalRecharged < 4000) return { admins: 1, accounts: 1 };
  if (totalRecharged < 10000) return { admins: 1, accounts: 2 };
  if (totalRecharged < 20000) return { admins: 2, accounts: 5 };
  if (totalRecharged < 30000) return { admins: 3, accounts: 10 };
  return { admins: 3, accounts: 10 + Math.floor((totalRecharged - 20000) / 10000) * 5 };
}

export function isValidIP(value: string): boolean {
  return (
    /^(\d{1,3}\.){3}\d{1,3}$/.test(value) && value.split('.').every((part) => Number(part) <= 255)
  );
}

export function customerUrl(customer: Customer): string | null {
  if (customer.baseUrl) return customer.baseUrl.replace(/\/$/, '');
  if (!customer.ip) return null;
  return `${customer.protocol}://${customer.ip}:${customer.port || (customer.protocol === 'https' ? 3443 : 3000)}`;
}

export async function signRechargeCode(
  amount: number,
  tokens: number,
  systemId: string,
  orderId: string,
  expiresAt: number,
): Promise<string> {
  const payload = {
    system_id: systemId,
    amount: amount.toFixed(2),
    tokens,
    version: '1',
    order_id: orderId,
    nonce: crypto.randomUUID(),
    issued_at: new Date().toISOString(),
    expires_at: new Date(expiresAt).toISOString(),
  };
  const body = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const { privateKey } = await getSigningKeyPair();
  const signature = crypto.sign(null, Buffer.from(body), privateKey).toString('base64url');
  return `LXRC2.${body}.${signature}`;
}

export function measuredConsumption(points: UsageReport[]): number | null {
  if (points.length < 2) return null;
  const first = points[0];
  const last = points[points.length - 1];
  if (
    first.cumulativeConsumed !== null &&
    last.cumulativeConsumed !== null &&
    last.cumulativeConsumed >= first.cumulativeConsumed
  ) {
    return last.cumulativeConsumed - first.cumulativeConsumed;
  }
  return null;
}

export function safeMoney(value: unknown): number | null {
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount <= 0 || amount > 10_000_000) return null;
  if (Math.abs(amount * 100 - Math.round(amount * 100)) > 0.00001) return null;
  return Math.round(amount * 100) / 100;
}

export function enrichCustomer(customer: Customer): Record<string, unknown> {
  const age = customer.lastReport
    ? Date.now() - customer.lastReport.reportedAt
    : Number.POSITIVE_INFINITY;
  return {
    ...customer,
    apiToken: undefined,
    tier: tierFor(customer.totalRecharged),
    online: age < 65 * 60 * 1000,
    stale: age >= 65 * 60 * 1000,
  };
}

export function orderLabel(order: RechargeOrder): string {
  if (order.status === 'pending') return '待审核';
  if (order.status === 'approved') return order.delivered ? '已到账' : '已批准，等待到账';
  if (order.status === 'issued') return '已签发';
  return '已拒绝';
}
