import react from '@vitejs/plugin-react';
import path from 'node:path';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  server: {
    proxy: { '/api': 'http://localhost:8000' },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    css: false,
    exclude: ['e2e/**', 'node_modules/**'],
    // Deliberately NOT raised globally. Only two tests (app.test.tsx) need the
    // headroom, and they set it per-test; a 30s default across all 688 would
    // let a test creep from 1s to 20s unnoticed and make a genuinely hung one
    // take 6x longer to report.
  },
});
