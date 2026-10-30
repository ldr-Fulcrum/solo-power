import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
const root = path.resolve(import.meta.dirname, '../..');
const unique = () => `Synthetic ${Date.now()}-${Math.round(Math.random()*10000)}`;

test.beforeEach(async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: /Open synthetic workspace/ }).click();
  await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled();
});

test('task, restart-visible state, receipt and desktop accessibility', async ({ page }) => {
  const title = unique();
  await page.getByLabel('A task to remember').fill(title);
  await page.getByRole('button', { name: /Add task/ }).click();
  await expect(page.getByText(title, { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: /Open synthetic workspace/ }).click();
  await expect(page.getByText(title, { exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(test.info().config.metadata.evidenceDir as string, 'darla-home-desktop.png'), fullPage: true });
  const audit = await new AxeBuilder({ page }).analyze();
  expect(audit.violations).toEqual([]);
  await page.getByRole('button', { name: /Receipts/, exact: false }).click();
  await expect(page.getByRole('heading', { name: 'What actually happened.' })).toBeVisible();
  await expect(page.getByText('completed', { exact: true }).first()).toBeVisible();
});

test('draft order is nonbinding and excluded from actual sales', async ({ page, request }) => {
  const title = unique();
  await page.getByRole('button', { name: /Bakery/, exact: false }).click();
  await page.getByLabel('Draft description').fill(title);
  await page.getByLabel('Your estimated total, USD (optional)').fill('125.50');
  await page.getByRole('button', { name: 'Save order draft' }).click();
  await expect(page.getByText(title, { exact: true })).toBeVisible();
  await expect(page.getByText('draft only', { exact: true }).first()).toBeVisible();
  const goal = await request.get('/api/v1/bakery/goals/weekly', { headers: { Authorization: 'Bearer synthetic-dani-bakery' } });
  expect((await goal.json()).actual_cents).toBeNull();
});

test('personal memory can be corrected, remains isolated, and can be deleted', async ({ page }) => {
  const value = unique();
  await page.getByLabel('Workspace context').selectOption('personal');
  await page.getByRole('button', { name: /Memory/ }).click();
  await page.getByLabel('A preference to remember').fill(value);
  await page.getByRole('button', { name: 'Remember this preference' }).click();
  await expect(page.getByLabel('Saved preference').filter({ hasText: value })).toHaveCount(1);
  const field = page.getByLabel('Saved preference').filter({ hasText: value });
  await field.fill(value+' corrected');
  await page.getByRole('button', { name: 'Save correction' }).last().click();
  await expect(page.getByLabel('Saved preference').last()).toHaveValue(value+' corrected');
  await page.getByLabel('Workspace context').selectOption('bakery');
  await expect(page.getByText('No preferences saved in this space.', { exact: false })).toBeVisible();
  await page.getByLabel('Workspace context').selectOption('personal');
  await expect(page.getByLabel('Saved preference').last()).toHaveValue(value+' corrected');
  await page.getByRole('button', { name: 'Remove preference' }).last().click();
  await expect(page.getByLabel('Saved preference')).toHaveCount(0);
});

test('queued work completes only after real worker execution', async ({ page }) => {
  const title = unique();
  await page.getByRole('button', { name: /Working for You/ }).click();
  await page.getByLabel('A task for an internal run').fill(title);
  await page.getByRole('button', { name: 'Queue internal work' }).click();
  const row = page.getByRole('listitem').filter({ hasText: title });
  await expect(row.getByText('queued', { exact: true })).toBeVisible();
  const stateDir = JSON.stringify(test.info().config.metadata.stateDir);
  const code = 'from pathlib import Path; from backend.app.config import Settings; from backend.app.services import Services; from backend.app.worker import Worker; root=Path.cwd().resolve(); print(Worker(Services(Settings(root=root,state_dir=Path(' + stateDir + '),synthetic_mode=True))).tick())';
  execFileSync(path.join(root, '.venv/Scripts/python.exe'), ['-B', '-c', code], { cwd: root, timeout: 45000 });
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(row.getByText('completed', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /My Day/ }).click();
  await expect(page.getByText(title, { exact: true })).toBeVisible();
});

test('mobile layout and truthful assistant failure', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: path.join(test.info().config.metadata.evidenceDir as string, 'darla-home-mobile.png'), fullPage: true });
  const audit = await new AxeBuilder({ page }).analyze();
  expect(audit.violations).toEqual([]);
  await page.getByRole('button', { name: /Darla/, exact: false }).click();
  await page.getByLabel('Your message').fill('Synthetic test message');
  await page.getByRole('button', { name: 'Check assistant connection' }).click();
  await expect(page.getByRole('alert')).toContainText('No runtime is connected');
});