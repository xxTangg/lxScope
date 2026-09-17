import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 44101,
    proxy: {
      '/api': 'http://127.0.0.1:44100',
    },
  },
  preview: {
    host: '0.0.0.0',
    port: 44101,
    proxy: {
      '/api': 'http://127.0.0.1:44100',
    },
  },
});
