import { test, expect } from '@playwright/test';
import path from 'node:path';

const pages = ['Home', 'Darla', 'Bakery', 'My Day', 'Working for You', 'Approvals', 'Receipts', 'Memory', 'Settings'];

test('all nine views render on desktop and mobile with clean application console', async ({ page }) => {
  const errors: string[] = [];
  const warnings: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
    if (message.type() === 'warning') warnings.push(message.text());
  });
  await page.goto('/');
  await expect(page).toHaveURL('http://127.0.0.1:8766/');
  await expect(page).toHaveTitle('Darla Giggles · Crumb & Get It Bakery');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#main')).toBeFocused();
  await page.getByRole('button', { name: /Open synthetic workspace/ }).click();
  await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled();
  const nav = page.getByRole('navigation', { name: 'Main navigation' });
  for (const viewport of [{ width: 1440, height: 1100 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    for (const name of pages) {
      await nav.getByRole('button', { name, exact: true }).click();
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(name === 'Home' ? 'A little room for what matters.' : name);
      await expect(page.locator('vite-error-overlay, nextjs-portal')).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      if (['Bakery', 'Receipts'].includes(name)) {
        await page.screenshot({ path: path.join(test.info().config.metadata.evidenceDir as string, `${name.toLowerCase()}-${viewport.width}.png`), fullPage: true });
      }
    }
  }
  await test.info().attach('console-health', { body: JSON.stringify({ errors, warnings }), contentType: 'application/json' });
  expect(errors).toEqual([]);
  expect(warnings).toEqual([]);
});

test('capacity proposal and recipe scaling show the real backend results', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: /Open synthetic workspace/ }).click();
  await page.getByRole('button', { name: 'Bakery', exact: true }).click();
  await page.getByLabel('Plan name').fill('Synthetic capacity boundary');
  await page.getByLabel('Work needed (minutes)').fill('120');
  await page.getByLabel('Your available minutes (optional)').fill('60');
  await page.getByRole('button', { name: 'Save proposed plan' }).click();
  await expect(page.getByText('Production plan saved as a proposal · over capacity.')).toBeVisible();
  await page.getByLabel('Recipe / version reference').fill('synthetic-recipe-v1');
  await page.getByLabel('Original yield', { exact: true }).fill('12');
  await page.getByLabel('Target yield', { exact: true }).fill('30');
  await page.getByLabel('Ingredient', { exact: true }).fill('synthetic flour');
  await page.getByLabel('Original grams', { exact: true }).fill('200');
  await page.getByRole('button', { name: 'Calculate proposed batch' }).click();
  await expect(page.getByText('Proposed batch: 500.000 g synthetic flour. Recipe source is user supplied.')).toBeVisible();
});