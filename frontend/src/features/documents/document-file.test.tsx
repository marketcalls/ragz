import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

import { useDocumentFile } from './document-file';

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

// jsdom does not implement URL.createObjectURL/revokeObjectURL at all, so
// there's no existing method for vi.spyOn to wrap -- install plain mocks
// before each test and drop them afterward instead.
beforeEach(() => {
  URL.createObjectURL = vi.fn();
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test('fetches the file as a blob, builds an object URL, and reports the mime type', async () => {
  const blob = new Blob(['%PDF-1.4 fake bytes'], { type: 'application/pdf' });
  const fetchMock = vi.fn(
    async () => new Response(blob, { status: 200, headers: { 'content-type': 'application/pdf' } }),
  );
  vi.stubGlobal('fetch', fetchMock);
  vi.mocked(URL.createObjectURL).mockReturnValue('blob:mock-url');

  const { result } = renderHook(() => useDocumentFile('doc-1'), { wrapper });

  await waitFor(() => expect(result.current.status).toBe('success'));
  expect(result.current.objectUrl).toBe('blob:mock-url');
  expect(result.current.mimeType).toBe('application/pdf');
  // Observable shape, not `expect.any(Blob)`: Response.blob() returns undici's
  // Blob, a different realm's constructor from jsdom's global, so an identity
  // check fails on a value that is a perfectly good Blob.
  const passed = vi.mocked(URL.createObjectURL).mock.calls[0]![0] as Blob;
  expect(passed.type).toBe('application/pdf');
  expect(passed.size).toBeGreaterThan(0);
});

test('decodes only server-approved plain text for escaped rendering', async () => {
  const payload = '<script>parent.previewPwned=true</script>';
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(payload, {
          status: 200,
          headers: { 'content-type': 'text/plain; charset=utf-8' },
        }),
    ),
  );
  vi.mocked(URL.createObjectURL).mockReturnValue('blob:text-url');

  const { result } = renderHook(() => useDocumentFile('doc-1'), { wrapper });

  await waitFor(() => expect(result.current.status).toBe('success'));
  expect(result.current.textContent).toBe(payload);
  expect(result.current.mimeType).toBe('text/plain');
});

test('does not decode an oversized text file into a second in-memory string', async () => {
  const payload = 'x'.repeat(2 * 1024 * 1024 + 1);
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () => new Response(payload, { status: 200, headers: { 'content-type': 'text/plain' } }),
    ),
  );
  vi.mocked(URL.createObjectURL).mockReturnValue('blob:large-text');

  const { result } = renderHook(() => useDocumentFile('doc-1'), { wrapper });

  await waitFor(() => expect(result.current.status).toBe('success'));
  expect(result.current.textContent).toBeNull();
});

test('revokes the object URL on unmount', async () => {
  const blob = new Blob(['data'], { type: 'application/pdf' });
  const fetchMock = vi.fn(
    async () => new Response(blob, { status: 200, headers: { 'content-type': 'application/pdf' } }),
  );
  vi.stubGlobal('fetch', fetchMock);
  vi.mocked(URL.createObjectURL).mockReturnValue('blob:mock-url');

  const { result, unmount } = renderHook(() => useDocumentFile('doc-1'), { wrapper });
  await waitFor(() => expect(result.current.objectUrl).toBe('blob:mock-url'));

  unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock-url');
});

test('reopening the same document refetches after an ACL may have changed', async () => {
  const fetchMock = vi.fn(
    async () =>
      new Response(new Blob(['data'], { type: 'application/pdf' }), {
        status: 200,
        headers: { 'content-type': 'application/pdf' },
      }),
  );
  vi.stubGlobal('fetch', fetchMock);
  vi.mocked(URL.createObjectURL).mockReturnValue('blob:authorized');
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const stableWrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );

  const first = renderHook(() => useDocumentFile('doc-1'), { wrapper: stableWrapper });
  await waitFor(() => expect(first.result.current.status).toBe('success'));
  first.unmount();
  const second = renderHook(() => useDocumentFile('doc-1'), { wrapper: stableWrapper });
  await waitFor(() => expect(second.result.current.status).toBe('success'));

  expect(fetchMock).toHaveBeenCalledTimes(2);
  second.unmount();
});

test('revokes the previous object URL when the document id changes', async () => {
  let call = 0;
  const fetchMock = vi.fn(async () => {
    call += 1;
    const blob = new Blob([`data-${call}`], { type: 'application/pdf' });
    return new Response(blob, { status: 200, headers: { 'content-type': 'application/pdf' } });
  });
  vi.stubGlobal('fetch', fetchMock);
  const urls = ['blob:mock-url-1', 'blob:mock-url-2'];
  vi.mocked(URL.createObjectURL).mockImplementation(() => urls.shift() as string);

  const { result, rerender } = renderHook(({ id }) => useDocumentFile(id), {
    wrapper,
    initialProps: { id: 'doc-1' },
  });
  await waitFor(() => expect(result.current.objectUrl).toBe('blob:mock-url-1'));

  rerender({ id: 'doc-2' });
  await waitFor(() => expect(result.current.objectUrl).toBe('blob:mock-url-2'));
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock-url-1');
});

test('a 403 response surfaces status "forbidden"', async () => {
  const fetchMock = vi.fn(async () => jsonResponse({ detail: 'Forbidden' }, 403));
  vi.stubGlobal('fetch', fetchMock);

  const { result } = renderHook(() => useDocumentFile('doc-1'), { wrapper });

  await waitFor(() => expect(result.current.status).toBe('forbidden'));
  expect(result.current.objectUrl).toBeNull();
});

test('a 404 response surfaces status "not-found"', async () => {
  const fetchMock = vi.fn(async () => jsonResponse({ detail: 'Not found' }, 404));
  vi.stubGlobal('fetch', fetchMock);

  const { result } = renderHook(() => useDocumentFile('doc-1'), { wrapper });

  await waitFor(() => expect(result.current.status).toBe('not-found'));
});

test('other errors surface status "error"', async () => {
  const fetchMock = vi.fn(async () => jsonResponse({ detail: 'Boom' }, 500));
  vi.stubGlobal('fetch', fetchMock);

  const { result } = renderHook(() => useDocumentFile('doc-1'), { wrapper });

  await waitFor(() => expect(result.current.status).toBe('error'));
});

test('is disabled (status "loading", no fetch) when documentId is null', () => {
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);

  const { result } = renderHook(() => useDocumentFile(null), { wrapper });

  expect(fetchMock).not.toHaveBeenCalled();
  expect(result.current.status).toBe('loading');
});
