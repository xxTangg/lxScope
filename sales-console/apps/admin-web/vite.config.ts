import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  const apiTarget = env.SALES_API_PROXY_TARGET || 'http://127.0.0.1:44100';

  return {
    plugins: [react()],
    server: {
      port: 44101,
      strictPort: true,
      watch: { usePolling: env.VITE_USE_POLLING === 'true' },
      proxy: {
        '/api': apiTarget,
      },
    },
    preview: {
      host: '0.0.0.0',
      port: 44101,
      strictPort: true,
      proxy: {
        '/api': apiTarget,
      },
    },
  };
});
