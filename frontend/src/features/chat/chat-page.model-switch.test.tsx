import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StrictMode } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { ChatPage } from './chat-page';

// Root-cause reproduction (bug 2026-08-15): on an existing chat, switching the
// model and sending the FIRST message reportedly shows nothing (request not
// posted, blank response); the second send works. This drives the REAL flow --
// real useChatStream / useSendMessage / chat-page effects, StrictMode -- and
// spies on streamChatSse to check the first post-switch send actually fires
// with the NEW model and its stream isn't aborted.

const streamCalls: { url: string; body: unknown; signal: AbortSignal }[] = [];

vi.mock('./stream', () => ({
  streamChatSse: vi.fn(async (url: string, body: unknown, _o: unknown, signal: AbortSignal) => {
    streamCalls.push({ url, body, signal });
    await new Promise(() => {});
  }),
}));

vi.mock('./queries', () => ({
  useChat: () => ({ data: { messages: [], has_summary: false }, isPending: false }),
  useCreateChat: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSetMessageFeedback: () => ({ mutate: vi.fn() }),
  useClearMessageFeedback: () => ({ mutate: vi.fn() }),
}));

vi.mock('@/features/documents/queries', () => ({ useDocuments: () => ({ data: [] }) }));
vi.mock('@/features/models/queries', () => ({
  useModels: () => ({
    data: [
      { id: 'model-1', display_name: 'GPT5.6 Luna', default_reasoning_effort: 'off' },
      { id: 'model-2', display_name: 'DeepSeek V4 Flash', default_reasoning_effort: 'off' },
    ],
  }),
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
        <MemoryRouter initialEntries={['/chat/chat-1']}>
          <Routes>
            <Route path="/chat/:chatId" element={<ChatPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
}

afterEach(() => {
  streamCalls.length = 0;
});

test('first message after switching the model is sent (with the new model) and not aborted', async () => {
  const user = userEvent.setup();
  renderApp();

  await user.selectOptions(screen.getByLabelText('Model'), 'model-2');

  const box = screen.getByPlaceholderText(/ask about your documents/i);
  await user.type(box, 'What is the websocket pattern for fyers?');
  await user.keyboard('{Enter}');

  await waitFor(() => expect(streamCalls.length).toBeGreaterThan(0));
  const last = streamCalls[streamCalls.length - 1];
  if (!last) throw new Error('expected a stream send');
  expect(last.url).toContain('/chats/chat-1/messages');
  expect((last.body as { model_id?: string }).model_id).toBe('model-2');
  await new Promise((r) => setTimeout(r, 0));
  expect(last.signal.aborted).toBe(false);
});
