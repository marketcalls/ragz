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
    { type: 'done', done: { message_id: 'm1', prompt_tokens: 1, completion_tokens: 1, no_answer: false } },
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
