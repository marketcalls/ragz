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
  expect(URL.createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
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
