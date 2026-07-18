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

test('sends multipart "files" with bearer token and reports progress', async () => {
  setAccessToken('tok');
  const onProgress = vi.fn();
  const promise = uploadDocuments('w1', [new File(['x'], 'a.pdf')], onProgress);
  const xhr = FakeXhr.instances[0]!;
  expect(xhr.opened).toEqual(['POST', '/api/v1/workspaces/w1/documents']);
  expect(xhr.headers.Authorization).toBe('Bearer tok');
  expect(xhr.body?.getAll('files')).toHaveLength(1);
  xhr.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
  xhr.onload?.();
  await promise;
  expect(onProgress).toHaveBeenCalledWith(50);
});

test('rejects with problem detail on failure', async () => {
  const promise = uploadDocuments('w1', [new File(['x'], 'a.pdf')], vi.fn());
  const xhr = FakeXhr.instances[0]!;
  xhr.status = 415;
  xhr.responseText = JSON.stringify({ detail: 'unsupported file type' });
  xhr.onload?.();
  await expect(promise).rejects.toThrow('unsupported file type');
});

test('401 → refresh → single retry', async () => {
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
  await promise;
});
