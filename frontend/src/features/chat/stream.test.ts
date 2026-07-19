import { setAccessToken } from '@/lib/auth-store';

import { streamChatSse, type ChatSseEvent } from './stream';

function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

test('emits typed events in order, tokens split across chunks', async () => {
  setAccessToken('tok');
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      sseResponse([
        'event: retrieval_started\ndata: {}\n\n',
        'event: sources\ndata: {"sources":[{"marker":1,"document_id":"d1","filename":"a.pdf","page":3,"chunk_index":12,"score":0.7,"snippet":"…the payroll codename is…","section":"Fire Safety > Evacuation","version":2}]}\n\n',
        'event: token\ndata: {"del',
        'ta":"Hel"}\n\nevent: token\ndata: {"delta":"lo"}\n\n',
        'event: citations\ndata: {"citations":[{"marker":1,"document_id":"d1","chunk_ref":"d1:12","page":3,"score":0.7,"section":"Fire Safety > Evacuation","version":2}]}\n\n',
        'event: done\ndata: {"message_id":"m1","prompt_tokens":10,"completion_tokens":5,"no_answer":false}\n\n',
      ]),
    ),
  );
  const events: ChatSseEvent[] = [];
  await streamChatSse('/api/v1/chats/c1/messages', { content: 'hi' }, (e) => events.push(e), new AbortController().signal);
  expect(events.map((e) => e.type)).toEqual([
    'retrieval_started',
    'sources',
    'token',
    'token',
    'citations',
    'done',
  ]);
  const tokens = events.filter((e): e is Extract<ChatSseEvent, { type: 'token' }> => e.type === 'token');
  expect(tokens.map((t) => t.delta).join('')).toBe('Hello');

  // CHAT-4: section + version parse through toEvent additively.
  const sourcesEvent = events.find((e): e is Extract<ChatSseEvent, { type: 'sources' }> => e.type === 'sources');
  expect(sourcesEvent?.sources[0]).toMatchObject({ section: 'Fire Safety > Evacuation', version: 2 });
  const citationsEvent = events.find(
    (e): e is Extract<ChatSseEvent, { type: 'citations' }> => e.type === 'citations',
  );
  expect(citationsEvent?.citations[0]).toMatchObject({ section: 'Fire Safety > Evacuation', version: 2 });
});

test('non-OK response emits a terminal error event', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'workspace access denied' }), {
        status: 403,
        headers: { 'content-type': 'application/problem+json' },
      }),
    ),
  );
  const events: ChatSseEvent[] = [];
  await streamChatSse('/api/v1/chats/c1/messages', { content: 'hi' }, (e) => events.push(e), new AbortController().signal);
  expect(events).toEqual([{ type: 'error', detail: 'workspace access denied' }]);
});

test('malformed frames and server error events both surface as typed errors', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      sseResponse([
        'event: token\ndata: not-json\n\n',
        'event: token\ndata: {"delta":"ok"}\n\n',
        'event: error\ndata: {"detail":"model unavailable"}\n\n',
      ]),
    ),
  );
  const events: ChatSseEvent[] = [];
  await streamChatSse('/api/v1/chats/c1/messages', {}, (e) => events.push(e), new AbortController().signal);
  expect(events.map((e) => e.type)).toEqual(['error', 'token', 'error']);
  expect(events.at(-1)).toEqual({ type: 'error', detail: 'model unavailable' });
});
