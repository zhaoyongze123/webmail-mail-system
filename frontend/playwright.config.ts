import { defineConfig, devices } from '@playwright/test';
import { fileURLToPath } from 'node:url';

const frontendDir = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  testDir: './e2e',
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 43000',
    cwd: frontendDir,
    port: 43000,
    reuseExistingServer: false,
    timeout: 120_000,
  },
  use: {
    baseURL: 'http://127.0.0.1:43000',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
