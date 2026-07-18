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
