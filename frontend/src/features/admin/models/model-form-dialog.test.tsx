import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ModelFormDialog } from './model-form-dialog';

function renderDialog(fetchMock = vi.fn()) {
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ModelFormDialog open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test('base URL appears only for ollama and openai_compatible', async () => {
  const user = userEvent.setup();
  renderDialog();
  expect(screen.queryByLabelText('Base URL')).not.toBeInTheDocument(); // openai default
  await user.selectOptions(screen.getByLabelText('Provider'), 'ollama');
  expect(screen.getByLabelText('Base URL')).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText('Provider'), 'openai_compatible');
  expect(screen.getByLabelText('Base URL')).toBeInTheDocument();
});

test('api key is a write-only password field, absent for ollama', async () => {
  const user = userEvent.setup();
  renderDialog();
  const key = screen.getByLabelText('API key');
  expect(key).toHaveAttribute('type', 'password');
  expect(key).toHaveAttribute('autocomplete', 'off');
  await user.selectOptions(screen.getByLabelText('Provider'), 'ollama');
  expect(screen.queryByLabelText('API key')).not.toBeInTheDocument();
});

test('submits the assembled payload', async () => {
  const fetchMock = vi.fn(async (_req: Request) =>
    new Response(JSON.stringify({ id: 'm1', key_fingerprint: 'ab12…ef90' }), {
      status: 201,
      headers: { 'content-type': 'application/json' },
    }),
  );
  const user = userEvent.setup();
  renderDialog(fetchMock);
  await user.type(screen.getByLabelText('Display name'), 'GPT-4o mini');
  await user.type(screen.getByLabelText('Model id'), 'gpt-4o-mini');
  await user.type(screen.getByLabelText('API key'), 'sk-test-123');
  await user.click(screen.getByRole('button', { name: 'Add model' }));
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const req = fetchMock.mock.calls[0]![0] as Request;
  const body = JSON.parse(await req.clone().text()) as Record<string, unknown>;
  expect(body).toMatchObject({
    display_name: 'GPT-4o mini',
    litellm_model_name: 'gpt-4o-mini',
    provider_kind: 'openai',
    api_key: 'sk-test-123',
  });
});
