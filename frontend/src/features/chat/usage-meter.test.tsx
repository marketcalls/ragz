import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';

import { UsageMeter } from './usage-meter';

function renderMeter(body: unknown) {
  const fetchMock = vi.fn(async () =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
  );
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <UsageMeter />
    </QueryClientProvider>,
  );
  return { fetchMock };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test('renders used/allocated tokens compactly with reset date', async () => {
  renderMeter({
    used_tokens: 12_300,
    allocated_tokens: 100_000,
    resets_at: '2026-08-01T00:00:00',
    warning: false,
  });

  const el = await screen.findByText('12.3K / 100K tokens · resets Aug 1');
  expect(el).not.toHaveClass('text-danger');
  expect(el).toHaveClass('text-muted');
  expect(screen.queryByText(/\(sample\)/)).not.toBeInTheDocument();
});

test('applies the danger class when warning is true', async () => {
  renderMeter({
    used_tokens: 95_000,
    allocated_tokens: 100_000,
    resets_at: '2026-08-01T00:00:00',
    warning: true,
  });

  const el = await screen.findByText('95K / 100K tokens · resets Aug 1');
  expect(el).toHaveClass('text-danger');
  expect(el).not.toHaveClass('text-muted');
});

test('renders without a slash when there is no allocation', async () => {
  renderMeter({
    used_tokens: 12_300,
    allocated_tokens: null,
    resets_at: '2026-08-01T00:00:00',
    warning: false,
  });

  const el = await screen.findByText('12.3K tokens');
  expect(el).not.toHaveTextContent('/');
  expect(screen.queryByText(/\(sample\)/)).not.toBeInTheDocument();
});
