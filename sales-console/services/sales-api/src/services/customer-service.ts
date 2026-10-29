import crypto from 'node:crypto';

import { isValidIP } from '../business.js';
import type { SalesRepository } from '../repositories/sales-repository.js';
import type { Customer } from '../types.js';

export interface CustomerService {
  list(): Promise<Customer[]>;
  get(customerID: string): Promise<Customer | undefined>;
  create(input: Record<string, unknown>): Promise<Customer>;
  update(customerID: string, input: Record<string, unknown>): Promise<Customer | undefined>;
  remove(customerID: string): Promise<void>;
  rotateToken(customerID: string): Promise<Customer | undefined>;
}

function httpError(status: number, message: string): Error & { status: number } {
  return Object.assign(new Error(message), { status });
}

function optionalHttpBaseUrl(value: unknown, field: string): string | null {
  if (value === undefined || value === null || (typeof value === 'string' && !value.trim())) {
    return null;
  }
  if (typeof value !== 'string' || value.length > 2048) {
    throw httpError(400, `${field} 参数不合法`);
  }
  try {
    const parsed = new URL(value.trim());
    if (
      !['http:', 'https:'].includes(parsed.protocol) ||
      !parsed.hostname ||
      parsed.username ||
      parsed.password ||
      (parsed.pathname !== '/' && parsed.pathname !== '') ||
      parsed.search ||
      parsed.hash
    ) {
      throw new Error('invalid base URL');
    }
    return parsed.origin;
  } catch {
    throw httpError(400, `${field} 必须是仅包含协议、主机和端口的 HTTP(S) 地址`);
  }
}

export function createCustomerService(repository: SalesRepository): CustomerService {
  return {
    list: () => repository.read('customers'),

    async get(customerID) {
      return (await repository.read('customers')).find((customer) => customer.id === customerID);
    },

    async create(input) {
      const name = String(input.name ?? '').trim();
      const ip = String(input.ip ?? '').trim();
      const protocol = input.protocol === 'https' ? 'https' : 'http';
      const port = Number(input.port) || (protocol === 'https' ? 3443 : 3000);
      const internalBaseUrl = optionalHttpBaseUrl(input.internalBaseUrl, 'internal_base_url');
      if (!name) throw httpError(400, '请填写客户名称');
      if (ip && !isValidIP(ip)) throw httpError(400, 'IP 地址格式不正确');
      if (!Number.isInteger(port) || port < 1 || port > 65_535) {
        throw httpError(400, '端口应为 1-65535 的整数');
      }
      const now = Date.now();
      const customer: Customer = {
        id: crypto.randomUUID(),
        systemId: String(input.systemId ?? '').trim(),
        name,
        environment: ['production', 'test'].includes(String(input.environment))
          ? (input.environment as Customer['environment'])
          : 'unclassified',
        protocol,
        ip,
        port,
        baseUrl: ip ? `${protocol}://${ip}:${port}` : null,
        internalBaseUrl,
        contact: String(input.contact ?? '').trim(),
        notes: String(input.notes ?? '')
          .trim()
          .slice(0, 1000),
        apiToken: crypto.randomBytes(24).toString('hex'),
        totalRecharged: 0,
        status: 'active',
        lastSeenIP: null,
        lastSeenAt: null,
        lastReport: null,
        createdAt: now,
        updatedAt: now,
      };
      await repository.update('customers', (items) => [...items, customer]);
      return customer;
    },

    async update(customerID, input) {
      let updated: Customer | undefined;
      await repository.update('customers', (items) =>
        items.map((item): Customer => {
          if (item.id !== customerID) return item;
          const ip = input.ip === undefined ? item.ip : String(input.ip).trim();
          if (ip && !isValidIP(ip)) throw httpError(400, 'IP 地址格式不正确');
          const protocol = input.protocol === undefined ? item.protocol : String(input.protocol);
          if (protocol !== 'http' && protocol !== 'https') {
            throw httpError(400, '连接协议不合法');
          }
          const port = input.port === undefined ? item.port : Number(input.port);
          if (!Number.isInteger(port) || port < 1 || port > 65_535) {
            throw httpError(400, '端口应为 1-65535 的整数');
          }
          const name = input.name === undefined ? item.name : String(input.name).trim();
          if (!name) throw httpError(400, '客户名称不能为空');
          const environmentValue =
            input.environment === undefined ? item.environment : String(input.environment);
          if (!['production', 'test', 'unclassified'].includes(environmentValue)) {
            throw httpError(400, '环境分类不合法');
          }
          const statusValue = input.status === undefined ? item.status : String(input.status);
          if (statusValue !== 'active' && statusValue !== 'disabled') {
            throw httpError(400, '客户状态不合法');
          }
          updated = {
            ...item,
            name,
            systemId: input.systemId === undefined ? item.systemId : String(input.systemId).trim(),
            environment: environmentValue as Customer['environment'],
            ip,
            protocol,
            port,
            baseUrl: ip ? `${protocol}://${ip}:${port}` : null,
            internalBaseUrl:
              input.internalBaseUrl === undefined
                ? (item.internalBaseUrl ?? null)
                : optionalHttpBaseUrl(input.internalBaseUrl, 'internal_base_url'),
            contact: input.contact === undefined ? item.contact : String(input.contact).trim(),
            notes:
              input.notes === undefined ? item.notes : String(input.notes).trim().slice(0, 1000),
            status: statusValue as Customer['status'],
            updatedAt: Date.now(),
          };
          return updated;
        }),
      );
      return updated;
    },

    async remove(customerID) {
      await repository.update('customers', (items) =>
        items.filter((customer) => customer.id !== customerID),
      );
    },

    async rotateToken(customerID) {
      const apiToken = crypto.randomBytes(24).toString('hex');
      let rotated: Customer | undefined;
      await repository.update('customers', (items) =>
        items.map((item) => {
          if (item.id !== customerID) return item;
          rotated = { ...item, apiToken, updatedAt: Date.now() };
          return rotated;
        }),
      );
      return rotated;
    },
  };
}
