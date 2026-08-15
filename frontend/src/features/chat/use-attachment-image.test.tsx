import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

import { useAttachmentImage } from './use-attachment-image';

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function jsonResponse(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

// jsdom does not implement URL.createObjectURL/revokeObjectURL at all.
beforeEach(() => {
  URL.createObjectURL = vi.fn();
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test('fetches the attachment as a blob and builds an object URL', async () => {
  const blob = new Blob(['fake image bytes'], { type: 'image/png' });
  const fetchMock = vi.fn(
    async (_req: Request) =>
      new Response(blob, { status: 200, headers: { 'content-type': 'image/png' } }),
  );
  vi.stubGlobal('fetch', fetchMock);
  vi.mocked(URL.createObjectURL).mockReturnValue('blob:mock-url');

  const { result } = renderHook(() => useAttachmentImage('c1', 'a1'), { wrapper });

  await waitFor(() => expect(result.current.status).toBe('success'));
  expect(result.current.objectUrl).toBe('blob:mock-url');
  expect(URL.createObjectURL).toHaveBeenCalledWith(expect.any(Blob));

  expect(fetchMock.mock.calls[0]![0].url).toContain(
    '/api/v1/chats/c1/attachments/a1/content',
  );
});

test('revokes the object URL on unmount', async () => {
  const blob = new Blob(['data'], { type: 'image/png' });
  const fetchMock = vi.fn(
    async () => new Response(blob, { status: 200, headers: { 'content-type': 'image/png' } }),
  );
  vi.stubGlobal('fetch', fetchMock);
  vi.mocked(URL.createObjectURL).mockReturnValue('blob:mock-url');

  const { result, unmount } = renderHook(() => useAttachmentImage('c1', 'a1'), { wrapper });
  await waitFor(() => expect(result.current.objectUrl).toBe('blob:mock-url'));

  unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock-url');
});

test('a 404 response surfaces status "error" and a null object URL', async () => {
  const fetchMock = vi.fn(async () => jsonResponse({ detail: 'Not found' }, 404));
  vi.stubGlobal('fetch', fetchMock);

  const { result } = renderHook(() => useAttachmentImage('c1', 'a1'), { wrapper });

  await waitFor(() => expect(result.current.status).toBe('error'));
  expect(result.current.objectUrl).toBeNull();
});

test('does not fetch when disabled or when ids are null', () => {
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);

  const { result: disabled } = renderHook(() => useAttachmentImage('c1', 'a1', false), { wrapper });
  const { result: nullId } = renderHook(() => useAttachmentImage('c1', null), { wrapper });

  expect(fetchMock).not.toHaveBeenCalled();
  expect(disabled.current.status).toBe('loading');
  expect(nullId.current.status).toBe('loading');
});
