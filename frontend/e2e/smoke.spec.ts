import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test, type Page } from '@playwright/test';

// CI-skippable: only runs when explicitly opted in against a live stack.
test.skip(process.env.E2E !== '1', 'set E2E=1 with a running compose stack to run the smoke');

// frontend/package.json sets "type": "module", so this file runs as ESM under
// Playwright's test runner — __dirname isn't defined; derive it from import.meta.url.
const __dirname = path.dirname(fileURLToPath(import.meta.url));

const EMAIL = process.env.E2E_EMAIL ?? 'root@ragz.internal';
const PASSWORD = process.env.E2E_PASSWORD ?? 'changeme12345';
const FIXTURE = path.join(__dirname, 'fixtures', 'sample.pdf');
const QUESTION = 'What is the internal launch codename for the Ragz payroll project?';

async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByLabel('Email').fill(EMAIL);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/chat/);
}

async function ensureModel(page: Page): Promise<void> {
  await page.goto('/admin/models');
  await page.getByRole('table').waitFor({ timeout: 15_000 }); // list loaded (renders even when empty)
  if ((await page.getByRole('row').count()) > 1) return; // header + at least one model
  const key = process.env.E2E_OPENAI_API_KEY;
  test.skip(!key, 'no model configured and E2E_OPENAI_API_KEY not provided');
  await page.getByRole('button', { name: 'Add model' }).click();
  await page.getByLabel('Display name').fill('GPT-4o mini (e2e)');
  await page.getByLabel('Model id').fill('gpt-4o-mini');
  await page.getByLabel('API key').fill(key as string);
  await page.getByRole('button', { name: 'Add model' }).click();
  await expect(page.getByRole('cell', { name: 'GPT-4o mini (e2e)' })).toBeVisible();
}

async function ensureWorkspace(page: Page): Promise<void> {
  await page.goto('/chat');
  await page.getByRole('button', { name: 'Switch workspace' }).click();
  const existing = page.getByRole('menuitem', { name: 'E2E Workspace' });
  if (await existing.isVisible().catch(() => false)) {
    await existing.click();
    return;
  }
  await page.getByRole('menuitem', { name: 'New workspace' }).click();
  await page.getByLabel('Name').fill('E2E Workspace');
  await page.getByRole('button', { name: 'Create' }).click();
}

test('phase 1 smoke: upload → indexed → cited streamed answer → edit sibling nav', async ({
  page,
}) => {
  await login(page);
  await ensureModel(page);
  await ensureWorkspace(page);

  // Upload the fixture and wait for Indexed (live polling, no reload).
  await page.goto('/documents');
  const fileChooserPromise = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'Upload documents' }).click();
  await (await fileChooserPromise).setFiles(FIXTURE);
  const row = page.getByRole('row', { name: /sample\.pdf/ });
  await expect(row).toBeVisible({ timeout: 30_000 });
  await expect(row.getByText('Indexed')).toBeVisible({ timeout: 180_000 });

  // Ask a question only the fixture can answer; expect a streamed, cited answer.
  await page.goto('/chat');
  await page.getByRole('textbox', { name: 'Message' }).fill(QUESTION);
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByText(/ZEBRA-COMET-7/).first()).toBeVisible({ timeout: 120_000 });
  const chip = page.getByRole('button', { name: /^Citation \d+$/ }).first();
  await expect(chip).toBeVisible();
  // The citation resolves to the uploaded document in the source panel.
  await chip.click();
  await expect(
    page.locator('[aria-label="Sources"]').getByText(/sample\.pdf/).first(),
  ).toBeVisible();

  // Edit the user message in place → a sibling version with < n/n > navigation.
  await page.getByRole('button', { name: 'Edit message' }).first().click();
  const editor = page.getByRole('textbox', { name: 'Edit message' });
  await editor.fill(`${QUESTION} Answer in one word.`);
  await page.getByRole('button', { name: 'Send' }).nth(0).click();
  await expect(page.getByText('2/2').first()).toBeVisible({ timeout: 120_000 });
  await page.getByRole('button', { name: 'Previous version' }).first().click();
  await expect(page.getByText('1/2').first()).toBeVisible();
});
