import { render, screen } from '@testing-library/react';

import { ErrorBoundary } from './error-boundary';

function Bomb(): never {
  throw new Error('kaboom');
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test('renders the fallback and reports the error via one POST', async () => {
  const fetchMock = vi.fn(async (_req: Request) => new Response(null, { status: 204 }));
  vi.stubGlobal('fetch', fetchMock);
  vi.spyOn(console, 'error').mockImplementation(() => {});

  render(
    <ErrorBoundary>
      <Bomb />
    </ErrorBoundary>,
  );

  expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();

  expect(fetchMock).toHaveBeenCalledTimes(1);
  const req = fetchMock.mock.calls[0]![0];
  expect(req.method).toBe('POST');
  expect(req.url).toContain('/api/v1/client-errors');
});
