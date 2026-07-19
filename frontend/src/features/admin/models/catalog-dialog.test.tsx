import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { CatalogDialog } from './catalog-dialog';

const catalogResponse = {
  entries: [
    {
      name: 'gpt-4o-mini',
      provider: 'openai',
      max_input_tokens: 128000,
      input_cost_per_1m: 0.15,
      output_cost_per_1m: 0.6,
      registered: true,
    },
    {
      name: 'claude-3-haiku',
      provider: 'anthropic',
      max_input_tokens: 200000,
      input_cost_per_1m: 0.25,
      output_cost_per_1m: 1.25,
      registered: false,
    },
  ],
  new_available: 1,
};

function renderDialog(fetchMock = vi.fn()) {
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CatalogDialog open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
  return fetchMock;
}

function stubCatalogFetch() {
  return vi.fn(
    async () =>
      new Response(JSON.stringify(catalogResponse), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
  );
}

afterEach(() => vi.unstubAllGlobals());

test('renders both catalog entries, the registered one with a Registered pill', async () => {
  renderDialog(stubCatalogFetch());
  expect(await screen.findByText('gpt-4o-mini')).toBeInTheDocument();
  expect(screen.getByText('claude-3-haiku')).toBeInTheDocument();
  expect(screen.getByText('Registered')).toBeInTheDocument();
});

test('the name filter narrows the visible rows to matches', async () => {
  const user = userEvent.setup();
  renderDialog(stubCatalogFetch());
  await screen.findByText('gpt-4o-mini');
  await user.type(screen.getByLabelText('Filter catalog'), 'claude');
  expect(screen.queryByText('gpt-4o-mini')).not.toBeInTheDocument();
  expect(screen.getByText('claude-3-haiku')).toBeInTheDocument();
});
