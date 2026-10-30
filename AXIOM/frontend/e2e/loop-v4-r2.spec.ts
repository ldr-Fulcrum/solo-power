import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

test('v4 real API: user message, runtime reply, human approval, worker receipt', async ({ page, request }, info) => {
  test.setTimeout(120000);
  const human = { Authorization: 'Bearer synthetic-dani-bakery', 'Idempotency-Key': 'browser-human' };
  const agent = { Authorization: 'Bearer synthetic-agent-bakery', 'Idempotency-Key': 'browser-runtime' };
  await page.goto('/');
  await page.getByRole('button', { name: 'Open synthetic workspace' }).click();
  await page.getByRole('button', { name: 'Darla', exact: true }).click();
  await page.getByRole('textbox', { name: 'Your message' }).fill('Synthetic browser check: tomorrow’s bake plan');
  await page.getByRole('button', { name: 'Send to Darla' }).click();
  await expect(page.getByText('Message saved for the runtime.')).toBeVisible();
  const inbox = await request.get('/api/v1/runtime/assistant/inbox', { headers: agent });
  expect(inbox.ok()).toBeTruthy();
  const message = (await inbox.json()).items[0];
  expect(message.text).toContain('Synthetic browser check');
  expect((await request.post('/api/v1/runtime/assistant/claim', { headers: agent, data: { message_id: message.id } })).ok()).toBeTruthy();
  expect((await request.post('/api/v1/runtime/assistant/replies', { headers: agent, data: { message_id: message.id, text: 'Synthetic conformance reply. No model was called.', provenance_references: ['synthetic-browser-trace-only'] } })).ok()).toBeTruthy();
  await expect(page.getByText('Synthetic conformance reply. No model was called.')).toBeVisible({ timeout: 15000 });
  const expires = new Date(Date.now() + 300000).toISOString();
  const response = await request.post('/api/v1/runtime/proposals', { headers: agent, data: {
    input: { principal_id: 'synthetic-dani', agent_id: 'synthetic-darla-agent', request_id: 'v4-browser-approval', goal: 'Propose a nonbinding draft', domain: 'bakery', tool_allowlist: ['draft.create'],
      authorization: { principal_id: 'synthetic-dani', session_id: 'synthetic-agent-bakery', domain: 'bakery', permitted_scopes: ['read', 'run.write', 'draft.write', 'runtime.propose'], expires_at: expires },
      budget: { max_tokens: 100, max_tool_calls: 1, max_cost_usd: '0' }, deadline: expires },
    result: { request_id: 'v4-browser-approval', actor_id: 'synthetic-darla-agent', test_double: true,
      action: { requested_tool: 'draft.create', parameters: { description: 'Synthetic reviewed draft' }, summary: 'Review synthetic draft for browser proof', requires_approval: false, provenance_references: ['synthetic-browser-trace-only'] } },
  } });
  expect(response.status()).toBe(201);
  const run = await response.json();
  expect(run.status).toBe('BLOCKED');
  await page.getByRole('button', { name: /^Approvals/ }).click();
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByText('Review synthetic draft for browser proof')).toBeVisible();
  await page.getByRole('button', { name: 'Approve local action' }).click();
  await expect(page.getByText('Decision saved. Approved local work waits for a worker tick. External execution remains disabled.')).toBeVisible();
  const root = path.resolve(import.meta.dirname, '../..');
  execFileSync(path.resolve(root, '../../../.venv/Scripts/python.exe'), ['-B', '-m', 'backend.app.tick', '--state-dir', String(info.config.metadata.stateDir)], {
    cwd: root, env: { ...process.env, DARLA_SYNTHETIC_MODE: '1', PYTHONDONTWRITEBYTECODE: '1' }, timeout: 30000,
  });
  const completed = await request.get(`/api/v1/runs/${run.id}`, { headers: human });
  expect((await completed.json()).status).toBe('COMPLETED');
  await page.getByRole('button', { name: 'Receipts', exact: true }).click();
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByText('darla-local-worker', { exact: false })).toBeVisible();
  await page.getByText('Receipt evidence').first().click();
  await page.screenshot({ path: path.join(String(info.config.metadata.evidenceDir), 'v4-receipt-ledger.png'), fullPage: true });
});
