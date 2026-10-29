import type { SalesRepository } from '../repositories/sales-repository.js';
import type { Settings } from '../types.js';

export interface SettingsService {
  read(): Promise<Settings>;
  updateTokenExchangeRate(value: unknown): Promise<Settings>;
}

export function createSettingsService(repository: SalesRepository): SettingsService {
  return {
    read: () => repository.read('settings'),

    async updateTokenExchangeRate(value) {
      const rate = Number(value);
      if (!Number.isFinite(rate) || rate <= 0 || rate > 100_000_000) {
        throw Object.assign(new Error('汇率须为大于 0 的数字'), { status: 400 });
      }
      const settings = { tokenExchangeRate: rate };
      await repository.save('settings', settings);
      return settings;
    },
  };
}
