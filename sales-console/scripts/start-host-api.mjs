import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const salesRoot = path.resolve(scriptDir, '..');
const workspaceRoot = path.resolve(salesRoot, '..');

function loadEnv(file) {
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const match = line.match(/^([^#=\s]+)=(.*)$/);
    if (match) process.env[match[1]] = match[2];
  }
}

loadEnv(path.join(salesRoot, '.env'));
process.env.SALES_DATA_DIR = path.join(workspaceRoot, 'host-runtime', 'sales-data');
process.env.SALES_API_PORT = '44100';
process.env.WEB_ORIGIN = 'http://192.168.31.197:44101';
process.env.SALES_PUBLIC_URL = 'http://192.168.31.197:44101';
process.env.DATABASE_URL = (process.env.DATABASE_URL ?? '').replace('@postgres:', '@127.0.0.1:');

await import('../services/sales-api/dist/index.js');
