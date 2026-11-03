export type View = 'overview' | 'customers' | 'upgrades' | 'finance' | 'system';

export type FinanceSection = 'requests' | 'history' | 'reconciliation' | 'alerts';

export type SystemSection = 'business' | 'security' | 'audit';

export type UpgradeSummary = {
  version: string;
  total: number;
  failed: number;
};

export type Staff = { id: string; username: string; role: string };

export type Customer = {
  id: string;
  name: string;
  systemId: string;
  environment: string;
  ip: string;
  port: number;
  protocol: 'http' | 'https';
  baseUrl?: string | null;
  internalBaseUrl?: string | null;
  contact?: string;
  notes?: string;
  status: string;
  totalRecharged: number;
  apiToken?: string;
  lastSeenIP?: string | null;
  lastSeenAt?: number | null;
  lastReport?: { poolTokens: number; appVersion: string; reportedAt: number } | null;
  online?: boolean;
  stale?: boolean;
  tier?: { admins: number; accounts: number };
};

export type Dashboard = {
  generatedAt: number;
  totalCustomers: number;
  online: number;
  pending: number;
  awaitingDelivery: number;
  totalRecharged: number;
  recharged30d: number;
  poolRemaining: number;
  days: Array<{ at: number; amount: number }>;
  customers: Customer[];
  activities: Array<{
    id: string;
    name: string;
    amount: number;
    status: string;
    delivered: boolean;
    at: number;
  }>;
};

export type ReleaseMeta = {
  type: string;
  version: string;
  file: string;
  size: number;
  uploadedAt: number;
  uploadedBy: string;
} | null;

export type ReleaseType = 'app' | 'core';
