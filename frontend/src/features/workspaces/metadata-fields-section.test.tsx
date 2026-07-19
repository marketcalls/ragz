import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/components/ui/toaster', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
  Toaster: () => null,
}));

import { toast } from '@/components/ui/toaster';

import { MetadataFieldsSection } from './metadata-fields-section';

function renderSection(fetchMock = vi.fn()) {
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MetadataFieldsSection workspaceId="w1" />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

test('a 409 name-pattern failure surfaces the server detail, not a generic message', async () => {
  const fetchMock = vi.fn(async (req: Request) => {
    if (req.method === 'GET') {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    return new Response(
      JSON.stringify({ detail: "'Bad Name!' is not a valid field name (^[a-z0-9_]{1,40}$)" }),
      { status: 409, headers: { 'content-type': 'application/json' } },
    );
  });
  const user = userEvent.setup();
  renderSection(fetchMock);

  await user.type(screen.getByLabelText('Name'), 'Bad Name!');
  await user.type(screen.getByLabelText('Label'), 'Bad Name');
  await user.click(screen.getByRole('button', { name: 'Add field' }));

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith(
      "'Bad Name!' is not a valid field name (^[a-z0-9_]{1,40}$)",
    ),
  );
});
