import { createServer } from 'node:http';

import { createApp } from './app.js';

const port = Number(process.env.SALES_API_PORT ?? 44100);
const app = await createApp();
createServer(app).listen(port, '0.0.0.0', () => {
  console.log(`sales-api listening on :${port}`);
});
