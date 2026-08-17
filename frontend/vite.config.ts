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
    // Vitest's default 5s per-test cap sits ABOVE any findBy timeout, so a
    // longer waitFor cannot help on its own -- app.test.tsx dynamically imports
    // the whole app after resetModules (~2.5s locally, more on a cold CI
    // runner) and was failing the enclosing test timeout, not the query. Raised
    // so a slow runner is slow rather than red; genuinely hung tests still fail.
    testTimeout: 30_000,
    hookTimeout: 30_000,
  },
});
