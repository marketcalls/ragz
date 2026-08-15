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
        'event: sources\ndata: {"sources":[{"marker":1,"document_id":"d1","filename":"a.pdf","page":3,"chunk_index":12,"score":0.7,"snippet":"…the payroll codename is…","section":"Fire Safety > Evacuation","version":2,"url":null}]}\n\n',
        'event: token\ndata: {"del',
        'ta":"Hel"}\n\nevent: token\ndata: {"delta":"lo"}\n\n',
        'event: citations\ndata: {"citations":[{"marker":1,"document_id":"d1","chunk_ref":"d1:12","page":3,"score":0.7,"section":"Fire Safety > Evacuation","version":2}]}\n\n',
        'event: done\ndata: {"message_id":"m1","prompt_tokens":10,"completion_tokens":5,"no_answer":false,"grounding":"documents"}\n\n',
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
  expect(sourcesEvent?.sources[0]).toMatchObject({ section: 'Fire Safety > Evacuation', version: 2, url: null });
  const citationsEvent = events.find(
    (e): e is Extract<ChatSseEvent, { type: 'citations' }> => e.type === 'citations',
  );
  expect(citationsEvent?.citations[0]).toMatchObject({ section: 'Fire Safety > Evacuation', version: 2 });

  // Plan I: grounding parses through toEvent additively.
  const doneEvent = events.find((e): e is Extract<ChatSseEvent, { type: 'done' }> => e.type === 'done');
  expect(doneEvent?.done.grounding).toBe('documents');
});

test('a done frame with grounding "general" parses through', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      sseResponse([
        'event: token\ndata: {"delta":"hi"}\n\n',
        'event: done\ndata: {"message_id":"m2","prompt_tokens":1,"completion_tokens":1,"no_answer":false,"grounding":"general"}\n\n',
      ]),
    ),
  );
  const events: ChatSseEvent[] = [];
  await streamChatSse('/api/v1/chats/c1/messages', { content: 'hi' }, (e) => events.push(e), new AbortController().signal);
  const doneEvent = events.find((e): e is Extract<ChatSseEvent, { type: 'done' }> => e.type === 'done');
  expect(doneEvent?.done.grounding).toBe('general');
});

test('a done frame with "validation_failed":true (Plan J Task 7) parses through', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      sseResponse([
        'event: token\ndata: {"delta":"Revenue was actually 12M, per [1]."}\n\n',
        'event: done\ndata: {"message_id":"m4","prompt_tokens":1,"completion_tokens":1,"no_answer":false,"grounding":"documents","validation_failed":true}\n\n',
      ]),
    ),
  );
  const events: ChatSseEvent[] = [];
  await streamChatSse('/api/v1/chats/c1/messages', { content: 'hi' }, (e) => events.push(e), new AbortController().signal);
  const doneEvent = events.find((e): e is Extract<ChatSseEvent, { type: 'done' }> => e.type === 'done');
  expect(doneEvent?.done.validation_failed).toBe(true);
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

test('an agent_step frame parses to the typed event', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      sseResponse([
        'event: retrieval_started\ndata: {}\n\n',
        'event: agent_step\ndata: {"n":1,"tool":"search","query":"muster point"}\n\n',
        'event: token\ndata: {"delta":"hi"}\n\n',
        'event: done\ndata: {"message_id":"m1","prompt_tokens":1,"completion_tokens":1,"no_answer":false,"grounding":"documents"}\n\n',
      ]),
    ),
  );
  const events: ChatSseEvent[] = [];
  await streamChatSse('/api/v1/chats/c1/messages', { content: 'hi' }, (e) => events.push(e), new AbortController().signal);
  const stepEvent = events.find(
    (e): e is Extract<ChatSseEvent, { type: 'agent_step' }> => e.type === 'agent_step',
  );
  expect(stepEvent?.step).toEqual({ n: 1, tool: 'search', query: 'muster point' });
});

test('a sources frame entry with a url (web citation, Task 11/D7) parses through', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      sseResponse([
        'event: sources\ndata: {"sources":[{"marker":1,"document_id":"","filename":"ISO 45001 overview","page":0,"chunk_index":0,"score":0,"snippet":"…","section":null,"version":0,"url":"https://example.test/iso"}]}\n\n',
        'event: citations\ndata: {"citations":[{"marker":1,"document_id":"","chunk_ref":"web:https://example.test/iso","page":0,"score":0,"section":null,"version":0,"url":"https://example.test/iso"}]}\n\n',
        'event: done\ndata: {"message_id":"m3","prompt_tokens":1,"completion_tokens":1,"no_answer":false,"grounding":"documents"}\n\n',
      ]),
    ),
  );
  const events: ChatSseEvent[] = [];
  await streamChatSse('/api/v1/chats/c1/messages', { content: 'hi' }, (e) => events.push(e), new AbortController().signal);
  const sourcesEvent = events.find(
    (e): e is Extract<ChatSseEvent, { type: 'sources' }> => e.type === 'sources',
  );
  expect(sourcesEvent?.sources[0]).toMatchObject({ url: 'https://example.test/iso', document_id: '' });
  const citationsEvent = events.find(
    (e): e is Extract<ChatSseEvent, { type: 'citations' }> => e.type === 'citations',
  );
  expect(citationsEvent?.citations[0]).toMatchObject({ url: 'https://example.test/iso' });
});

test('a blocks frame (Phase 2 generative UI) parses to the typed event', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      sseResponse([
        'event: token\ndata: {"delta":"Revenue grew."}\n\n',
        'event: blocks\ndata: {"blocks":[{"type":"callout","tone":"success","title":"Up 12%","body":"QoQ."}]}\n\n',
        'event: done\ndata: {"message_id":"m1","prompt_tokens":1,"completion_tokens":1,"no_answer":false,"grounding":"documents"}\n\n',
      ]),
    ),
  );
  const events: ChatSseEvent[] = [];
  await streamChatSse('/api/v1/chats/c1/messages', { content: 'hi' }, (e) => events.push(e), new AbortController().signal);
  const blocksEvent = events.find((e): e is Extract<ChatSseEvent, { type: 'blocks' }> => e.type === 'blocks');
  expect(blocksEvent?.blocks).toEqual([
    { type: 'callout', tone: 'success', title: 'Up 12%', body: 'QoQ.' },
  ]);
});

test('an unknown event name (not agent_step) still surfaces as a typed error', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => sseResponse(['event: totally_unknown\ndata: {}\n\n'])),
  );
  const events: ChatSseEvent[] = [];
  await streamChatSse('/api/v1/chats/c1/messages', {}, (e) => events.push(e), new AbortController().signal);
  expect(events).toEqual([{ type: 'error', detail: 'unknown event: totally_unknown' }]);
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
