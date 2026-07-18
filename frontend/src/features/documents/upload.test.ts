import { setAccessToken } from '@/lib/auth-store';

import { uploadDocuments } from './upload';

class FakeXhr {
  static instances: FakeXhr[] = [];
  upload = { onprogress: null as ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  status = 201;
  responseText = '{}';
  headers: Record<string, string> = {};
  opened: [string, string] | null = null;
  body: FormData | null = null;
  open(method: string, url: string) {
    this.opened = [method, url];
  }
  setRequestHeader(k: string, v: string) {
    this.headers[k] = v;
  }
  send(body: FormData) {
    this.body = body;
    FakeXhr.instances.push(this);
  }
}

beforeEach(() => {
  FakeXhr.instances = [];
  vi.stubGlobal('XMLHttpRequest', FakeXhr as unknown as typeof XMLHttpRequest);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

test('sends one multipart "file" POST per file, sequentially, with bearer token and aggregate progress', async () => {
  setAccessToken('tok');
  const onProgress = vi.fn();
  const promise = uploadDocuments(
    'w1',
    [new File(['x'], 'a.pdf'), new File(['yy'], 'b.pdf')],
    onProgress,
  );

  // Only the first request is issued until it resolves — sequential, not parallel.
  expect(FakeXhr.instances).toHaveLength(1);
  const first = FakeXhr.instances[0]!;
  expect(first.opened).toEqual(['POST', '/api/v1/workspaces/w1/documents']);
  expect(first.headers.Authorization).toBe('Bearer tok');
  expect(first.body?.getAll('file')).toHaveLength(1);
  expect(first.body?.getAll('files')).toHaveLength(0);
  first.upload.onprogress?.({ lengthComputable: true, loaded: 1, total: 1 });
  first.onload?.();

  await vi.waitFor(() => expect(FakeXhr.instances).toHaveLength(2));
  const second = FakeXhr.instances[1]!;
  expect(second.body?.getAll('file')).toHaveLength(1);
  second.upload.onprogress?.({ lengthComputable: true, loaded: 2, total: 2 });
  second.onload?.();

  const failures = await promise;
  expect(failures).toEqual([]);
  // Aggregate, bytes-weighted, ends at 100%.
  expect(onProgress).toHaveBeenLastCalledWith(100);
});

test('a per-file failure (409 dedup / 413 oversize) is collected but the batch continues', async () => {
  const promise = uploadDocuments(
    'w1',
    [new File(['x'], 'dup.pdf'), new File(['y'], 'ok.pdf')],
    vi.fn(),
  );

  const first = FakeXhr.instances[0]!;
  first.status = 409;
  first.responseText = JSON.stringify({ detail: 'duplicate file' });
  first.onload?.();

  await vi.waitFor(() => expect(FakeXhr.instances).toHaveLength(2));
  const second = FakeXhr.instances[1]!;
  second.status = 201;
  second.onload?.();

  const failures = await promise;
  expect(failures).toHaveLength(1);
  expect(failures[0]!.file.name).toBe('dup.pdf');
  expect(failures[0]!.message).toBe('duplicate file');
});

test('non-string detail (422 validation error array) falls back to a generic message', async () => {
  const promise = uploadDocuments('w1', [new File(['x'], 'a.pdf')], vi.fn());
  const xhr = FakeXhr.instances[0]!;
  xhr.status = 422;
  xhr.responseText = JSON.stringify({
    detail: [{ loc: ['body', 'file'], msg: 'field required', type: 'value_error' }],
  });
  xhr.onload?.();

  const failures = await promise;
  expect(failures).toEqual([{ file: expect.objectContaining({ name: 'a.pdf' }), message: 'upload failed' }]);
});

test('401 → refresh → single retry, then succeeds', async () => {
  setAccessToken('stale');
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ access_token: 'fresh' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    ),
  );
  const promise = uploadDocuments('w1', [new File(['x'], 'a.pdf')], vi.fn());
  const first = FakeXhr.instances[0]!;
  first.status = 401;
  first.onload?.();
  await vi.waitFor(() => expect(FakeXhr.instances).toHaveLength(2));
  const second = FakeXhr.instances[1]!;
  expect(second.headers.Authorization).toBe('Bearer fresh');
  second.onload?.();
  const failures = await promise;
  expect(failures).toEqual([]);
});
