import path from 'path';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import svgr from 'vite-plugin-svgr';

export default defineConfig({
	plugins: [react(), tailwindcss(), svgr()],
	server: {
		// Docker Desktop bind mounts on Windows do not consistently forward
		// native filesystem events. Enable polling only for the compose dev
		// container so normal local Vite development keeps native watching.
		watch:
			process.env.DOCKER_DEV === 'true'
				? { usePolling: true, interval: 500 }
				: undefined,
		proxy: {
			'/api': 'http://localhost:3000',
		},
	},
	resolve: {
		alias: {
			'@': path.resolve(__dirname, './src'),
			'next/navigation': path.resolve(__dirname, './src/lib/next-navigation-shim.ts'),
		},
	},
});
