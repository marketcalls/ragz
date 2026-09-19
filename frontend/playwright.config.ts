import { defineConfig } from '@playwright/test';

const liveE2E = process.env.E2E === '1';

export default defineConfig({
  testDir: './e2e',
  timeout: 240_000, // ingestion + first model call are slow paths
  retries: 0,
  workers: 1, // serial: steps build on each other against one real stack
  use: {
    baseURL:
      process.env.E2E_BASE_URL ?? (liveE2E ? 'http://localhost:5173' : 'http://127.0.0.1:4175'),
    trace: 'retain-on-failure',
  },
  webServer: liveE2E
    ? undefined
    : {
        command: 'pnpm dev --host 127.0.0.1 --port 4175',
        url: 'http://127.0.0.1:4175/e2e/preview-harness.html',
        reuseExistingServer: true,
      },
});
