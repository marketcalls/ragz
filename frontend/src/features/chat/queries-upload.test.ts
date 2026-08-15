import { setAccessToken } from '@/lib/auth-store';

import { uploadAttachment, uploadPendingAttachments } from './queries';

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

test('uploadAttachment POSTs multipart form data to the given chat_id and returns the attachment', async () => {
  setAccessToken('tok');
  const fetchMock = vi.fn(
    async (_req: Request) =>
      new Response(
        JSON.stringify({ id: 'att1', kind: 'document', filename: 'x.txt', mime: 'text/plain', status: 'queued' }),
        { status: 201 },
      ),
  );
  vi.stubGlobal('fetch', fetchMock);

  const file = new File(['hi'], 'x.txt', { type: 'text/plain' });
  const result = await uploadAttachment('chat-123', file);

  expect(result).toEqual({ id: 'att1', kind: 'document', filename: 'x.txt', mime: 'text/plain', status: 'queued' });
  const call = fetchMock.mock.calls[0];
  if (!call) throw new Error('fetch was not called');
  const [request] = call;
  expect(request.url).toContain('/api/v1/chats/chat-123/attachments');
  expect(request.method).toBe('POST');
});

test('uploadAttachment throws on a non-OK response', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('too large', { status: 413 })));
  await expect(uploadAttachment('chat-123', new File(['x'], 'x.txt'))).rejects.toThrow(
    'failed to upload attachment',
  );
});

test('uploadPendingAttachments uploads sequentially and returns ids in order', async () => {
  let call = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      call += 1;
      return new Response(
        JSON.stringify({ id: `att${call}`, kind: 'document', filename: `f${call}`, mime: 'text/plain', status: 'queued' }),
        { status: 201 },
      );
    }),
  );

  const ids = await uploadPendingAttachments('chat-123', [
    new File(['a'], 'a.txt'),
    new File(['b'], 'b.txt'),
  ]);

  expect(ids).toEqual(['att1', 'att2']);
  expect(call).toBe(2);
});

test('uploadPendingAttachments stops and throws with the failing filename on the first failure', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('nope', { status: 500 })));
  await expect(
    uploadPendingAttachments('chat-123', [new File(['a'], 'bad.txt')]),
  ).rejects.toThrow('bad.txt');
});
