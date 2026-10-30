import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  cacheDir: '../.local/cache/vite',
  server: { host: '127.0.0.1', proxy: { '/api': 'http://127.0.0.1:8765', '/health': 'http://127.0.0.1:8765' } },
  test: { include: ['src/**/*.test.{ts,tsx}'], environment: 'jsdom', setupFiles: ['./src/test-setup.ts'], pool: 'forks', maxWorkers: 1 },
});