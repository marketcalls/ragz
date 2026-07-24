import { setAccessToken } from '@/lib/auth-store';

import { walkDroppedItems } from './dropzone';
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
  const files = [new File(['x'], 'a.pdf'), new File(['yy'], 'b.pdf')];
  const promise = uploadDocuments(
    'w1',
    files.map((file) => ({ file, folderId: null })),
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

test('a non-null folderId is sent as a "folder_id" form field', async () => {
  const files = [new File(['x'], 'a.pdf')];
  const promise = uploadDocuments(
    'w1',
    files.map((file) => ({ file, folderId: 'folder-1' })),
    vi.fn(),
  );
  const xhr = FakeXhr.instances[0]!;
  expect(xhr.body?.getAll('folder_id')).toEqual(['folder-1']);
  xhr.onload?.();
  await promise;
});

test('a null folderId (root) sends no "folder_id" form field at all', async () => {
  const files = [new File(['x'], 'a.pdf')];
  const promise = uploadDocuments(
    'w1',
    files.map((file) => ({ file, folderId: null })),
    vi.fn(),
  );
  const xhr = FakeXhr.instances[0]!;
  expect(xhr.body?.getAll('folder_id')).toEqual([]);
  xhr.onload?.();
  await promise;
});

test('a per-file failure (409 dedup / 413 oversize) is collected but the batch continues', async () => {
  const files = [new File(['x'], 'dup.pdf'), new File(['y'], 'ok.pdf')];
  const promise = uploadDocuments(
    'w1',
    files.map((file) => ({ file, folderId: null })),
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
  const files = [new File(['x'], 'a.pdf')];
  const promise = uploadDocuments(
    'w1',
    files.map((file) => ({ file, folderId: null })),
    vi.fn(),
  );
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
  const files = [new File(['x'], 'a.pdf')];
  const promise = uploadDocuments(
    'w1',
    files.map((file) => ({ file, folderId: null })),
    vi.fn(),
  );
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

// --- walkDroppedItems / walkEntry (dropzone.tsx) ---
//
// Mocks the File and Directory Entries API shape returned by
// DataTransferItem.webkitGetAsEntry(): FileSystemFileEntry/FileSystemDirectoryEntry
// with a createReader() whose readEntries() callback-style API is exercised
// across MULTIPLE batches (a non-empty batch followed by an empty one) to
// prove readDirectoryEntries' repeated-call-until-empty loop is what actually
// collects every child, not just the first batch.

class FakeFileEntry {
  readonly isFile = true;
  readonly isDirectory = false;
  constructor(readonly name: string) {}
  file(successCallback: (file: File) => void): void {
    successCallback(new File(['x'], this.name));
  }
}

class FakeDirectoryEntry {
  readonly isFile = false;
  readonly isDirectory = true;
  constructor(
    readonly name: string,
    // Each inner array is one readEntries() batch; the reader is exhausted
    // (and readDirectoryEntries stops) only once a batch comes back empty.
    private readonly batches: (FakeFileEntry | FakeDirectoryEntry)[][],
  ) {}
  createReader(): FileSystemDirectoryReader {
    let call = 0;
    return {
      readEntries: (successCallback: (entries: FileSystemEntry[]) => void) => {
        const batch = this.batches[call] ?? [];
        call += 1;
        successCallback(batch as unknown as FileSystemEntry[]);
      },
    } as unknown as FileSystemDirectoryReader;
  }
}

test('walkDroppedItems recurses through multi-level nested directories and builds the full relative path', async () => {
  // Legal/
  //   index.pdf
  //   Contracts/
  //     2024/
  //       report.pdf
  const reportFile = new FakeFileEntry('report.pdf');
  // Two calls: one non-empty batch, then the empty batch that terminates the read.
  const dir2024 = new FakeDirectoryEntry('2024', [[reportFile], []]);
  const contractsDir = new FakeDirectoryEntry('Contracts', [[dir2024], []]);
  const indexFile = new FakeFileEntry('index.pdf');
  // Three calls: two non-empty batches (proving batching isn't a single readEntries call), then empty.
  const legalDir = new FakeDirectoryEntry('Legal', [[indexFile], [contractsDir], []]);

  const items = [
    { webkitGetAsEntry: () => legalDir as unknown as FileSystemEntry },
  ] as unknown as DataTransferItemList;

  const dropped = await walkDroppedItems(items);
  const byPath = new Map(dropped.map((d) => [d.relativePath, d.file]));

  expect([...byPath.keys()].sort()).toEqual(['Legal/Contracts/2024/report.pdf', 'Legal/index.pdf']);
  expect(byPath.get('Legal/index.pdf')!.name).toBe('index.pdf');
  expect(byPath.get('Legal/Contracts/2024/report.pdf')!.name).toBe('report.pdf');
});
