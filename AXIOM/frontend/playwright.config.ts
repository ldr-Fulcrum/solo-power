import { defineConfig } from '@playwright/test';
import path from 'node:path';
const root = path.resolve(import.meta.dirname, '..');
const runId = Date.now().toString();
const stateDir = path.join(root, '.local/tmp/e2e-' + runId);
const evidenceDir = path.join(root, 'reports/browser-' + runId);
export default defineConfig({
  metadata: { stateDir, evidenceDir },
  testDir: './e2e', workers: 1, retries: 0, timeout: 60000,
  outputDir: '../.local/tmp/playwright-results-' + runId,
  reporter: [['list'], ['json', { outputFile: path.join(evidenceDir, 'results.json') }]],
  use: { baseURL: 'http://127.0.0.1:8766', viewport: { width: 1440, height: 1100 }, headless: true,
    launchOptions: { executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
      args: ['--disable-breakpad', '--disable-crash-reporter', '--no-first-run', '--no-default-browser-check', `--disk-cache-dir=${path.join(root, '.local/cache/browser')}`], downloadsPath: path.join(root, '.local/tmp/browser-downloads') } },
  webServer: { command: `"${path.join(root, '../../..', '.venv', 'Scripts', 'python.exe')}" -B -m uvicorn backend.app.main:create_app --factory --host 127.0.0.1 --port 8766 --no-access-log`, cwd: root,
    url: 'http://127.0.0.1:8766/health/live', reuseExistingServer: false, timeout: 90000,
    env: { DARLA_SYNTHETIC_MODE: '1', DARLA_STATE_DIR: stateDir, PYTHONDONTWRITEBYTECODE: '1', TEMP: path.join(root, '.local/tmp'), TMP: path.join(root, '.local/tmp') } },
});