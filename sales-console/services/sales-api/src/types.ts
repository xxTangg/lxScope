export type Environment = 'production' | 'test' | 'unclassified';
export type CustomerStatus = 'active' | 'disabled';
export type RechargeOrderStatus = 'pending' | 'issued' | 'approved' | 'rejected';

export interface Staff {
  id: string;
  username: string;
  passwordSalt: string;
  passwordHash: string;
  role: 'admin' | 'operator';
  status: 'active' | 'disabled';
  createdAt: number;
  updatedAt: number;
}

export interface Customer {
  id: string;
  systemId: string;
  name: string;
  environment: Environment;
  protocol: 'http' | 'https';
  ip: string;
  port: number;
  baseUrl: string | null;
  internalBaseUrl?: string | null;
  contact: string;
  notes: string;
  apiToken: string;
  totalRecharged: number;
  status: CustomerStatus;
  lastSeenIP: string | null;
  lastSeenAt: number | null;
  lastReport: UsageReport | null;
  createdAt: number;
  updatedAt: number;
}

export interface RechargeOrder {
  id: string;
  requestID?: string;
  customerID: string;
  requestedAt?: number;
  method: 'code' | 'online';
  status: RechargeOrderStatus;
  requestedAmount?: number | null;
  amount?: number;
  tokens?: number;
  note?: string;
  code?: string;
  expiresAt?: number;
  decisionReason?: string;
  delivered?: boolean;
  deliveredAt?: number;
  deliveryAttempts?: number;
  lastDeliveryAt?: number;
  ackOperationID?: string;
  redemptionOperationID?: string;
  ledgerID?: string;
  createdAt: number;
  processedAt?: number;
  processedBy?: string;
}

export interface UsageReport {
  id?: string;
  customerID: string;
  systemId: string;
  poolTokens: number;
  totalRecharged: number;
  cumulativeConsumed: number | null;
  cumulativeCredits: number | null;
  appVersion: string;
  clientReportedAt?: number;
  reportedAt: number;
  observedIP?: string;
}

export interface ReleaseMeta {
  type: 'app' | 'core';
  version: string;
  file: string;
  sha256: string;
  size: number;
  uploadedAt: number;
  uploadedBy: string;
}

export interface Settings {
  tokenExchangeRate: number;
}

export interface AuthenticatedRequest {
  staff?: Staff;
}

declare global {
  namespace Express {
    interface Request {
      staff?: Staff;
    }
  }
}

export {};
