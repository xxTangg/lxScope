import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const { SALES_API_PROXY_TARGET } = loadEnv(mode, '.', 'SALES_API_PROXY_');
  const salesApiProxyTarget =
    SALES_API_PROXY_TARGET || 'http://127.0.0.1:44100';

  return {
    plugins: [react()],
    server: {
      port: 44101,
      proxy: {
        '/api': salesApiProxyTarget,
      },
    },
    preview: {
      host: '0.0.0.0',
      port: 44101,
      proxy: {
        '/api': salesApiProxyTarget,
      },
    },
  };
});
