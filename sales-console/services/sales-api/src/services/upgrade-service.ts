import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';

import type { ReleaseRepository } from '../repositories/release-repository.js';
import type { SalesRepository } from '../repositories/sales-repository.js';
import { validateReleaseArchive, type ReleaseType } from '../infrastructure/release-archive.js';
import type { Customer, ReleaseMeta } from '../types.js';

export interface UpgradeService {
  getRelease(type: ReleaseType): Promise<ReleaseMeta | null>;
  publish(input: {
    type: ReleaseType;
    version: string;
    bytes: Buffer;
    manifestType: string;
    uploadedBy: string;
  }): Promise<ReleaseMeta>;
  upgradeAll(
    type: ReleaseType,
    customerIDs: string[],
    publicBase: string,
  ): Promise<{
    version: string;
    results: Array<Record<string, unknown>>;
  }>;
}

type CustomerCall = (
  customer: Customer,
  endpoint: string,
  body: unknown,
  options?: { requestID?: string; idempotencyKey?: string },
) => Promise<Record<string, unknown>>;

type CustomerConnectionUrls = (customer: Customer) => string[];

export function createUpgradeService(input: {
  releases: ReleaseRepository;
  repository: SalesRepository;
  releaseDirectory: string;
  customerConnectionUrls: CustomerConnectionUrls;
  callCustomer: CustomerCall;
}): UpgradeService {
  const { releases, repository, releaseDirectory, customerConnectionUrls, callCustomer } = input;

  return {
    getRelease: (type) => releases.get(type),

    async publish({ type, version, bytes, manifestType, uploadedBy }) {
      const digest = crypto.createHash('sha256').update(bytes).digest('hex');
      const filename = `${type}-${version}-${digest.slice(0, 16)}.tar.gz`;
      const candidate = path.join(releaseDirectory, `.upload-${crypto.randomUUID()}`);
      await fs.writeFile(candidate, bytes, { mode: 0o600 });
      try {
        await validateReleaseArchive(
          candidate,
          path.dirname(releaseDirectory),
          type,
          version,
          manifestType,
        );
        const file = path.join(releaseDirectory, filename);
        try {
          await fs.access(file);
        } catch {
          await fs.rename(candidate, file);
        }
        const meta: ReleaseMeta = {
          type,
          version,
          file: filename,
          sha256: digest,
          size: bytes.length,
          uploadedAt: Date.now(),
          uploadedBy,
        };
        await releases.save(meta);
        return meta;
      } finally {
        await fs.rm(candidate, { force: true });
      }
    },

    async upgradeAll(type, customerIDs, publicBase) {
      const meta = await releases.get(type);
      if (!meta) throw Object.assign(new Error('还没有上传过这个类型的升级包'), { status: 400 });
      const selected = new Set(customerIDs);
      if (!selected.size) throw Object.assign(new Error('请选择升级目标'), { status: 400 });
      const customers = (await repository.read('customers')).filter(
        (customer) =>
          selected.has(customer.id) &&
          customer.status === 'active' &&
          customerConnectionUrls(customer).length > 0,
      );
      if (customers.length !== selected.size) {
        throw Object.assign(new Error('存在无法升级的目标，请重新选择'), { status: 400 });
      }
      const results: Array<Record<string, unknown>> = [];
      for (const customer of customers) {
        try {
          const operationID = crypto.randomUUID();
          await callCustomer(
            customer,
            `/integration/sales/v1/upgrades/${type}`,
            {
              operationId: operationID,
              artifactType: type,
              version: meta.version,
              sha256: `sha256:${meta.sha256}`,
              sizeBytes: meta.size,
              downloadUrl: `${publicBase}/api/v1/integration/releases/${type}/latest`,
              issuedAt: new Date().toISOString(),
              expiresAt: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
            },
            { idempotencyKey: operationID },
          );
          let confirmed = false;
          for (let attempt = 0; attempt < 30; attempt += 1) {
            await new Promise((resolve) => setTimeout(resolve, 2_000));
            try {
              const ping = await callCustomer(customer, '/integration/sales/v1/ping', {});
              if ((type === 'app' ? ping.appVersion : ping.coreVersion) === meta.version) {
                confirmed = true;
                break;
              }
            } catch {
              // The next probe can succeed after the customer service restarts.
            }
          }
          results.push({
            id: customer.id,
            name: customer.name,
            ok: confirmed,
            state: confirmed ? 'completed' : 'unverified',
          });
        } catch (error) {
          results.push({
            id: customer.id,
            name: customer.name,
            ok: false,
            error: error instanceof Error ? error.message : '升级失败',
          });
        }
      }
      return { version: meta.version, results };
    },
  };
}
