import path from 'node:path';
import type { Express, Request, RequestHandler } from 'express';

import type { ReleaseType } from '../infrastructure/release-archive.js';
import type { UpgradeService } from '../services/upgrade-service.js';
import type { Customer, Staff } from '../types.js';

export interface UpgradeRouteDependencies {
  service: UpgradeService;
  uploadRelease: RequestHandler;
  releaseDirectory: string;
  releaseTypeFromCanonical(value: unknown): ReleaseType | null;
  customerFromBearer(request: Request): Promise<Customer | null>;
  audit(staff: Staff, request: Request): Promise<void>;
}

function authenticatedStaff(request: Request): Staff {
  if (!request.staff) throw Object.assign(new Error('请先登录'), { status: 401 });
  return request.staff;
}

export function registerPublicUpgradeRoutes(
  app: Express,
  dependencies: UpgradeRouteDependencies,
): void {
  app.get('/api/customers/releases/:type/latest', async (request, response, next) => {
    try {
      const customer = await dependencies.customerFromBearer(request);
      if (!customer) {
        response.status(401).json({ error: '无效的客户访问令牌' });
        return;
      }
      const type = dependencies.releaseTypeFromCanonical(request.params.type);
      if (!type) {
        response.status(404).json({ error: '升级包类型不存在' });
        return;
      }
      const meta = await dependencies.service.getRelease(type);
      if (!meta) {
        response.status(404).json({ error: '总部还没有发布过这个类型的升级包' });
        return;
      }
      response.sendFile(path.join(dependencies.releaseDirectory, meta.file));
    } catch (error) {
      next(error);
    }
  });
}

export function registerStaffUpgradeRoutes(
  app: Express,
  dependencies: UpgradeRouteDependencies,
): void {
  app.get('/api/releases', async (_request, response, next) => {
    try {
      response.json({
        app: await dependencies.service.getRelease('app'),
        core: await dependencies.service.getRelease('core'),
      });
    } catch (error) {
      next(error);
    }
  });

  app.post('/api/releases/:type', dependencies.uploadRelease, async (request, response, next) => {
    try {
      const type = dependencies.releaseTypeFromCanonical(request.params.type);
      if (!type || !request.file) {
        response.status(400).json({ error: '请上传有效升级包和类型' });
        return;
      }
      const version = String(request.query.version ?? '').trim();
      if (!/^\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?$/.test(version)) {
        response.status(400).json({ error: '请提供有效版本号' });
        return;
      }
      const meta = await dependencies.service.publish({
        type,
        version,
        bytes: request.file.buffer,
        manifestType: String(request.headers['x-contract-artifact-type'] ?? type),
        uploadedBy: authenticatedStaff(request).username,
      });
      await dependencies.audit(authenticatedStaff(request), request);
      response.json(meta);
    } catch (error) {
      next(error);
    }
  });

  app.post('/api/upgrade-all/:type', async (request, response, next) => {
    try {
      const type = dependencies.releaseTypeFromCanonical(request.params.type);
      if (!type) {
        response.status(400).json({ error: '还没有上传过这个类型的升级包' });
        return;
      }
      const customerIDs = Array.isArray(request.body.customerIDs)
        ? request.body.customerIDs.map(String)
        : [];
      const publicBase =
        process.env.SALES_PUBLIC_URL?.replace(/\/$/, '') ??
        `${request.protocol}://${request.get('host')}`;
      const result = await dependencies.service.upgradeAll(type, customerIDs, publicBase);
      await dependencies.audit(authenticatedStaff(request), request);
      response.json(result);
    } catch (error) {
      next(error);
    }
  });
}
