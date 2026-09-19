import { expect, test, type Page } from '@playwright/test';

test.skip(process.env.E2E !== '1', 'set E2E=1 with an isolated live stack');

const EMAIL = process.env.E2E_EMAIL ?? 'root@ragz.internal';
const PASSWORD = process.env.E2E_PASSWORD ?? 'changeme12345';

async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByLabel('Email').fill(EMAIL);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/chat/);
}

async function ensureWorkspace(page: Page): Promise<void> {
  await page.goto('/chat');
  await page.getByRole('button', { name: 'Switch workspace' }).click();
  await expect(page.getByRole('menuitem', { name: 'Loading workspaces…' })).toBeHidden();
  await expect(page.getByRole('menuitem', { name: 'New workspace' })).toBeVisible();
  const existing = page.getByRole('menuitem', { name: 'MQR Product Smoke' });
  if ((await existing.count()) > 0) {
    await existing.first().click();
    return;
  }
  await page.getByRole('menuitem', { name: 'New workspace' }).click();
  await page.getByLabel('Name').fill('MQR Product Smoke');
  await page.getByRole('button', { name: 'Create' }).click();
}

test('superadmin sees and persists the MQR workspace control', async ({ page }) => {
  await login(page);
  await ensureWorkspace(page);
  await page.getByRole('button', { name: 'Workspace settings' }).click();

  const toggle = page.getByLabel('Expand each question into multiple searches');
  await expect(toggle).toBeVisible();
  const expected = !(await toggle.isChecked());
  await toggle.click();

  const patch = page.waitForResponse(
    (response) =>
      response.request().method() === 'PATCH' &&
      /\/api\/v1\/workspaces\/[^/]+$/.test(response.url()),
  );
  await page.getByRole('button', { name: 'Save settings' }).click();
  const response = await patch;
  expect(response.ok()).toBe(true);

  await page.getByRole('button', { name: 'Workspace settings' }).click();
  await expect(page.getByLabel('Expand each question into multiple searches')).toBeChecked({
    checked: expected,
  });
});
