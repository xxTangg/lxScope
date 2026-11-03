import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';

import * as tar from 'tar';
import type { ReadEntry } from 'tar';

export type ReleaseType = 'app' | 'core';

export async function validateReleaseArchive(
  file: string,
  stagingRoot: string,
  type: ReleaseType,
  version: string,
  manifestType: string = type,
): Promise<void> {
  const names = new Set<string>();
  let count = 0;
  await tar.t({
    file,
    strict: true,
    onentry: (entry: ReadEntry) => {
      count += 1;
      if (count > 20_000) throw new Error('升级包文件数量过多');
      const normalized = path.posix.normalize(entry.path);
      if (entry.path.startsWith('/') || normalized === '..' || normalized.startsWith('../')) {
        throw new Error('升级包包含不安全路径');
      }
      names.add(normalized);
    },
  });
  if (!names.has('manifest.json')) throw new Error('升级包缺少 manifest.json');
  const staging = path.join(stagingRoot, `.release-check-${crypto.randomUUID()}`);
  await fs.mkdir(staging, { recursive: true, mode: 0o700 });
  try {
    await tar.x({ file, cwd: staging, strict: true });
    const manifest = JSON.parse(await fs.readFile(path.join(staging, 'manifest.json'), 'utf8')) as {
      type?: string;
      version?: string;
    };
    if (manifest.type !== manifestType || manifest.version !== version) {
      throw new Error('manifest 类型或版本与上传信息不一致');
    }
    const required =
      type === 'app' ? ['server.js', 'public/index.html'] : ['agentscope/__init__.py'];
    for (const item of required) {
      try {
        await fs.access(path.join(staging, item));
      } catch {
        throw new Error(`AgentScope 升级包缺少必要文件：${item}`);
      }
    }
  } finally {
    await fs.rm(staging, { recursive: true, force: true });
  }
}
