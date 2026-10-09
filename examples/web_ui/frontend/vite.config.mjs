import path from 'path';
import { fileURLToPath } from 'node:url';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import svgr from 'vite-plugin-svgr';

const configDirectory = path.dirname(fileURLToPath(import.meta.url));

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
			'@': path.resolve(configDirectory, './src'),
			'next/navigation': path.resolve(configDirectory, './src/lib/next-navigation-shim.ts'),
		},
	},
});
