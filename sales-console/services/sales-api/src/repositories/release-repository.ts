import path from 'node:path';

import { readJson, writeJson } from '../store.js';
import type { ReleaseMeta } from '../types.js';
import type { ReleaseType } from '../infrastructure/release-archive.js';

export interface ReleaseRepository {
  get(type: ReleaseType): Promise<ReleaseMeta | null>;
  save(meta: ReleaseMeta): Promise<void>;
}

export function createReleaseRepository(releaseDirectory: string): ReleaseRepository {
  return {
    async get(type) {
      const current = await readJson<ReleaseMeta | null>(
        path.join(releaseDirectory, `${type}.json`),
        null,
      );
      return current ? { ...current, type } : null;
    },

    save(meta) {
      return writeJson(path.join(releaseDirectory, `${meta.type}.json`), meta);
    },
  };
}
