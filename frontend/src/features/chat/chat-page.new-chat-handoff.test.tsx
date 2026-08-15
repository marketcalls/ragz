import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StrictMode } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { ChatPage } from './chat-page';

// Root-cause reproduction (bug 2026-08-15): sending the FIRST message from an
// empty /chat creates a chat, navigates to /chat/:id, and the handoff effect
// auto-sends. The user reported the first attempt silently drops (message not
// even posted) and only the second works. This test drives the REAL flow --
// real useChatStream, useSendMessage, and chat-page effects, under StrictMode
// and a real router with BOTH routes -- and spies on streamChatSse to see
// whether the send fires and whether its AbortSignal gets aborted out from
// under it.

const streamCalls: { url: string; body: unknown; signal: AbortSignal }[] = [];

vi.mock('./stream', () => ({
  streamChatSse: vi.fn(async (url: string, body: unknown, _onEvent: unknown, signal: AbortSignal) => {
    streamCalls.push({ url, body, signal });
    // Never resolves during the test window -- mimics an open SSE stream.
    await new Promise(() => {});
  }),
}));

const createChatMock = vi.fn(async () => ({ id: 'new-1' }));

vi.mock('./queries', () => ({
  useChat: () => ({ data: { messages: [], has_summary: false }, isPending: false }),
  useCreateChat: () => ({ mutateAsync: createChatMock, isPending: false }),
  useSetMessageFeedback: () => ({ mutate: vi.fn() }),
  useClearMessageFeedback: () => ({ mutate: vi.fn() }),
}));

vi.mock('@/features/documents/queries', () => ({ useDocuments: () => ({ data: [] }) }));
vi.mock('@/features/models/queries', () => ({
  useModels: () => ({ data: [{ id: 'gpt', display_name: 'GPT5.6 Luna', default_reasoning_effort: 'off' }] }),
}));
vi.mock('@/features/workspaces/queries', () => ({
  useWorkspaces: () => ({ data: [{ id: 'ws-1', name: 'Acme', web_search_enabled: false }] }),
}));
vi.mock('@/features/workspaces/workspace-context', () => ({
  useWorkspace: () => ({ workspaceId: 'ws-1', setWorkspaceId: vi.fn() }),
}));
vi.mock('./use-pending-attachments', () => ({
  usePendingAttachments: () => ({ files: [], addFiles: vi.fn(), remove: vi.fn(), clear: vi.fn(), error: null }),
}));

function renderApp() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/chat']}>
          <Routes>
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/chat/:chatId" element={<ChatPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
}

afterEach(() => {
  streamCalls.length = 0;
  createChatMock.mockClear();
});

test('the first message from a new chat is actually sent and its stream is not aborted', async () => {
  const user = userEvent.setup();
  renderApp();

  const box = screen.getByPlaceholderText(/ask about your documents/i);
  await user.type(box, 'What is the websocket pattern for fyers?');
  await user.keyboard('{Enter}');

  // The chat gets created, we navigate, and the handoff auto-send fires.
  await waitFor(() => expect(streamCalls.length).toBeGreaterThan(0));

  const last = streamCalls[streamCalls.length - 1];
  if (!last) throw new Error('expected a stream send');
  expect(last.url).toContain('/chats/new-1/messages');
  expect((last.body as { content: string }).content).toBe(
    'What is the websocket pattern for fyers?',
  );
  // The crux: the send that fired must NOT be immediately aborted by the
  // chatId-change cleanup. If this is aborted, the user sees "nothing happened".
  await new Promise((r) => setTimeout(r, 0));
  expect(last.signal.aborted).toBe(false);
});
