import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';

import type { ChatSseEvent } from './stream';
import { useChatStream } from './use-chat-stream';

const streamChatSse = vi.fn();
vi.mock('./stream', () => ({ streamChatSse: (...args: unknown[]) => streamChatSse(...args) }));

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

afterEach(() => {
  streamChatSse.mockReset();
});

test('a stream that starts with token (no retrieval_started/sources) still renders as streaming', async () => {
  // CHAT-3 Phase-1 router: small talk skips retrieval_started/sources entirely.
  // The reducer must not require retrieval_started to transition into
  // 'streaming' - a bare token/citations/done sequence should render the
  // reply normally, with the "Searching documents..." indicator never
  // appearing for these turns.
  const frames: ChatSseEvent[] = [
    { type: 'token', delta: 'Hi there!' },
    { type: 'citations', citations: [] },
    {
      type: 'done',
      done: {
        message_id: 'm1', prompt_tokens: 1, completion_tokens: 1, no_answer: false,
        grounding: 'documents', validation_failed: false,
      },
    },
  ];
  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, onEvent: (e: ChatSseEvent) => void) => {
      for (const frame of frames) onEvent(frame);
    },
  );

  const { result } = renderHook(() => useChatStream('c1'), { wrapper });

  await act(async () => {
    result.current.send('Hi');
  });

  expect(result.current.status).toBe('done');
  expect(result.current.text).toBe('Hi there!');
  expect(result.current.sources).toEqual([]);
  expect(result.current.grounding).toBe('documents');
});

test('the reducer stores grounding="general" from the done frame', async () => {
  const frames: ChatSseEvent[] = [
    { type: 'token', delta: 'ISO 45001 is an OHS standard.' },
    {
      type: 'done',
      done: {
        message_id: 'm2', prompt_tokens: 1, completion_tokens: 1, no_answer: false,
        grounding: 'general', validation_failed: false,
      },
    },
  ];
  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, onEvent: (e: ChatSseEvent) => void) => {
      for (const frame of frames) onEvent(frame);
    },
  );

  const { result } = renderHook(() => useChatStream('c1'), { wrapper });

  await act(async () => {
    result.current.send('What is ISO 45001?');
  });

  expect(result.current.status).toBe('done');
  expect(result.current.grounding).toBe('general');
});

test('the reducer stores validationFailed=true from the done frame and resets on the next send', async () => {
  const failingFrames: ChatSseEvent[] = [
    { type: 'token', delta: 'Revenue was actually 12M, per [1].' },
    {
      type: 'done',
      done: {
        message_id: 'm4', prompt_tokens: 1, completion_tokens: 1, no_answer: false,
        grounding: 'documents', validation_failed: true,
      },
    },
  ];
  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, onEvent: (e: ChatSseEvent) => void) => {
      for (const frame of failingFrames) onEvent(frame);
    },
  );

  const { result } = renderHook(() => useChatStream('c1'), { wrapper });

  await act(async () => {
    result.current.send('what was revenue?');
  });

  expect(result.current.status).toBe('done');
  expect(result.current.validationFailed).toBe(true);

  // A fresh send resets back to IDLE (validationFailed=false) before the
  // next stream's events land.
  const passingFrames: ChatSseEvent[] = [
    { type: 'token', delta: 'Revenue was 12M [1].' },
    {
      type: 'done',
      done: {
        message_id: 'm5', prompt_tokens: 1, completion_tokens: 1, no_answer: false,
        grounding: 'documents', validation_failed: false,
      },
    },
  ];
  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, onEvent: (e: ChatSseEvent) => void) => {
      for (const frame of passingFrames) onEvent(frame);
    },
  );

  await act(async () => {
    result.current.send('what was revenue again?');
  });

  expect(result.current.validationFailed).toBe(false);
});

test('agent_step frames accumulate in order and keep status=retrieving', async () => {
  const frames: ChatSseEvent[] = [
    { type: 'retrieval_started' },
    { type: 'agent_step', step: { n: 1, tool: 'search', query: 'muster point' } },
    { type: 'agent_step', step: { n: 2, tool: 'get_document', query: 'doc-1' } },
    { type: 'token', delta: 'Found it.' },
    {
      type: 'done',
      done: {
        message_id: 'm3', prompt_tokens: 1, completion_tokens: 1, no_answer: false,
        grounding: 'documents', validation_failed: false,
      },
    },
  ];
  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, onEvent: (e: ChatSseEvent) => void) => {
      for (const frame of frames) onEvent(frame);
    },
  );

  const { result } = renderHook(() => useChatStream('c1'), { wrapper });

  await act(async () => {
    result.current.send('What is the muster point and when was it approved?');
  });

  expect(result.current.agentSteps).toEqual([
    { n: 1, tool: 'search', query: 'muster point' },
    { n: 2, tool: 'get_document', query: 'doc-1' },
  ]);
  expect(result.current.status).toBe('done');
});

test('agentSteps resets to empty on a fresh send (back to IDLE)', async () => {
  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, onEvent: (e: ChatSseEvent) => void) => {
      onEvent({ type: 'agent_step', step: { n: 1, tool: 'search', query: 'q' } });
    },
  );

  const { result } = renderHook(() => useChatStream('c1'), { wrapper });
  await act(async () => {
    result.current.send('first');
  });
  expect(result.current.agentSteps).toEqual([{ n: 1, tool: 'search', query: 'q' }]);

  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, _onEvent: (e: ChatSseEvent) => void) => {
      // second turn never emits an agent_step
    },
  );
  await act(async () => {
    result.current.send('second');
  });
  expect(result.current.agentSteps).toEqual([]);
});

test('tool_result frames accumulate into toolResults, paired to their agent_step by n', async () => {
  const frames: ChatSseEvent[] = [
    { type: 'retrieval_started' },
    { type: 'agent_step', step: { n: 1, tool: 'web_search', query: 'iso 45001' } },
    {
      type: 'tool_result',
      result: {
        n: 1, tool: 'web_search',
        results: [{ title: 'ISO 45001 overview', url: 'https://example.test/iso', source: 'example.test' }],
      },
    },
    { type: 'token', delta: 'Per ISO 45001.' },
    {
      type: 'done',
      done: {
        message_id: 'm4', prompt_tokens: 1, completion_tokens: 1, no_answer: false,
        grounding: 'documents', validation_failed: false,
      },
    },
  ];
  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, onEvent: (e: ChatSseEvent) => void) => {
      for (const frame of frames) onEvent(frame);
    },
  );

  const { result } = renderHook(() => useChatStream('c1'), { wrapper });
  await act(async () => {
    result.current.send('What does ISO 45001 say?');
  });

  expect(result.current.toolResults).toEqual([
    {
      n: 1, tool: 'web_search',
      results: [{ title: 'ISO 45001 overview', url: 'https://example.test/iso', source: 'example.test' }],
    },
  ]);
});

test('toolResults resets to empty on a fresh send (back to IDLE)', async () => {
  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, onEvent: (e: ChatSseEvent) => void) => {
      onEvent({
        type: 'tool_result',
        result: { n: 1, tool: 'web_search', results: [{ title: 't', url: 'https://x.test', source: 'x.test' }] },
      });
    },
  );
  const { result } = renderHook(() => useChatStream('c1'), { wrapper });
  await act(async () => {
    result.current.send('first');
  });
  expect(result.current.toolResults).toHaveLength(1);

  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, _onEvent: (e: ChatSseEvent) => void) => {
      // second turn never emits a tool_result
    },
  );
  await act(async () => {
    result.current.send('second');
  });
  expect(result.current.toolResults).toEqual([]);
});

test('the reducer stores blocks from the blocks SSE frame and resets on the next send', async () => {
  const blocks: ChatSseEvent[] = [
    { type: 'token', delta: 'Revenue grew.' },
    {
      type: 'blocks',
      blocks: [{ type: 'callout', tone: 'success', title: 'Up 12%', body: 'QoQ.' }],
    },
    {
      type: 'done',
      done: {
        message_id: 'm6', prompt_tokens: 1, completion_tokens: 1, no_answer: false,
        grounding: 'documents', validation_failed: false,
      },
    },
  ];
  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, onEvent: (e: ChatSseEvent) => void) => {
      for (const frame of blocks) onEvent(frame);
    },
  );

  const { result } = renderHook(() => useChatStream('c1'), { wrapper });

  await act(async () => {
    result.current.send('how did revenue do?');
  });

  expect(result.current.blocks).toEqual([
    { type: 'callout', tone: 'success', title: 'Up 12%', body: 'QoQ.' },
  ]);

  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, onEvent: (e: ChatSseEvent) => void) => {
      onEvent({
        type: 'done',
        done: {
          message_id: 'm7', prompt_tokens: 1, completion_tokens: 1, no_answer: false,
          grounding: 'documents', validation_failed: false,
        },
      });
    },
  );
  await act(async () => {
    result.current.send('a turn with no blocks');
  });
  expect(result.current.blocks).toEqual([]);
});

test('an error before any token leaves status=error with the detail and the pending user message', async () => {
  // Pre-stream failure (e.g. the refresh session died -> 401 on the POST):
  // streamChatSse emits a single error event and no token. The state must
  // land on 'error' (not stick at 'retrieving') so StreamingMessage shows the
  // alert instead of the UI silently doing nothing.
  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, onEvent: (e: ChatSseEvent) => void) => {
      onEvent({ type: 'error', detail: 'request failed (401)' });
    },
  );

  const { result } = renderHook(() => useChatStream('c1'), { wrapper });

  await act(async () => {
    result.current.send('Hi');
  });

  expect(result.current.status).toBe('error');
  expect(result.current.errorDetail).toBe('request failed (401)');
  expect(result.current.text).toBe('');
  expect(result.current.pendingUserContent).toBe('Hi'); // optimistic message stays visible
});

test('stop() aborts the in-flight request, resets state, and refetches the tree', async () => {
  // The mocked streamChatSse never resolves on its own (mirrors an in-flight
  // SSE connection) - it only settles once the captured signal aborts, and it
  // never calls onEvent, so a real terminal reduce() never runs.
  let capturedSignal: AbortSignal | undefined;
  streamChatSse.mockImplementation(
    (_url: string, _body: unknown, _onEvent: (e: ChatSseEvent) => void, signal: AbortSignal) => {
      capturedSignal = signal;
      return new Promise<void>((resolve) => {
        signal.addEventListener('abort', () => resolve());
      });
    },
  );

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
  function stopWrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  }

  const { result } = renderHook(() => useChatStream('c1'), { wrapper: stopWrapper });

  act(() => {
    result.current.send('Hi');
  });
  expect(result.current.status).toBe('retrieving');

  act(() => {
    result.current.stop();
  });

  expect(capturedSignal?.aborted).toBe(true);
  expect(result.current.status).toBe('idle');
  expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['chat', 'c1'] });
});

test('send includes reasoning_effort in the body when set and not "off"', async () => {
  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, onEvent: (e: ChatSseEvent) => void) => {
      onEvent({
        type: 'done',
        done: {
          message_id: 'm1', prompt_tokens: 1, completion_tokens: 1, no_answer: false,
          grounding: 'documents', validation_failed: false,
        },
      });
    },
  );
  const { result } = renderHook(() => useChatStream('c1'), { wrapper });

  await act(async () => {
    result.current.send('hi', undefined, 'model-1', 'high');
  });

  const [, body] = streamChatSse.mock.calls[0]!;
  expect(body).toMatchObject({ reasoning_effort: 'high' });
});

test('send omits reasoning_effort from the body when "off" or absent', async () => {
  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, onEvent: (e: ChatSseEvent) => void) => {
      onEvent({
        type: 'done',
        done: {
          message_id: 'm1', prompt_tokens: 1, completion_tokens: 1, no_answer: false,
          grounding: 'documents', validation_failed: false,
        },
      });
    },
  );
  const { result } = renderHook(() => useChatStream('c1'), { wrapper });

  await act(async () => {
    result.current.send('hi', undefined, 'model-1', 'off');
  });

  const [, body] = streamChatSse.mock.calls[0]!;
  expect(body).not.toHaveProperty('reasoning_effort');
});

test('send includes attachment_ids in the body when provided', async () => {
  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, onEvent: (e: ChatSseEvent) => void) => {
      onEvent({
        type: 'done',
        done: {
          message_id: 'm1', prompt_tokens: 1, completion_tokens: 1, no_answer: false,
          grounding: 'documents', validation_failed: false,
        },
      });
    },
  );
  const { result } = renderHook(() => useChatStream('c1'), { wrapper });

  await act(async () => {
    result.current.send('hi', undefined, 'model-1', 'off', ['a1', 'a2']);
  });

  const [, body] = streamChatSse.mock.calls[0]!;
  expect(body).toMatchObject({ attachment_ids: ['a1', 'a2'] });
});

test('send includes web_search_consented only when the flag is true', async () => {
  streamChatSse.mockImplementation(
    async (_url: string, _body: unknown, onEvent: (e: ChatSseEvent) => void) => {
      onEvent({
        type: 'done',
        done: {
          message_id: 'm1', prompt_tokens: 1, completion_tokens: 1, no_answer: false,
          grounding: 'documents', validation_failed: false,
        },
      });
    },
  );
  const { result } = renderHook(() => useChatStream('c1'), { wrapper });

  await act(async () => {
    result.current.send('hi', undefined, 'model-1', 'off', undefined, true);
  });
  expect(streamChatSse.mock.calls[0]![1]).toMatchObject({ web_search_consented: true });

  streamChatSse.mockClear();
  await act(async () => {
    result.current.send('hi again', undefined, 'model-1', 'off', undefined, false);
  });
  expect(streamChatSse.mock.calls[0]![1]).not.toHaveProperty('web_search_consented');
});
