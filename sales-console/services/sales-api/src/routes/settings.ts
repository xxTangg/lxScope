import type { Express, Request } from 'express';

import type { SettingsService } from '../services/settings-service.js';
import type { Staff } from '../types.js';

export function registerSettingsRoutes(
  app: Express,
  service: SettingsService,
  audit: (staff: Staff, request: Request) => Promise<void>,
): void {
  app.get('/api/settings', async (_request, response, next) => {
    try {
      response.json(await service.read());
    } catch (error) {
      next(error);
    }
  });

  app.put('/api/settings', async (request, response, next) => {
    try {
      const settings = await service.updateTokenExchangeRate(request.body.tokenExchangeRate);
      if (!request.staff) throw Object.assign(new Error('请先登录'), { status: 401 });
      await audit(request.staff, request);
      response.json(settings);
    } catch (error) {
      next(error);
    }
  });
}
