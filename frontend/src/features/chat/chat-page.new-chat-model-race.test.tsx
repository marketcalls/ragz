import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StrictMode } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { ChatPage } from './chat-page';

// Root-cause reproduction (bug 2026-08-16): on a FRESH page load the models
// query is still pending when the create→navigate handoff for the first message
// arrives. effectiveModelId is null, so the auto-send would omit model_id and
// the backend errors when the workspace has no default model — the user sees
// the first message "disappear" and only the second (after models cached)
// works. The fix gates the handoff send on a resolved model. This test drives
// the real flow with models UNAVAILABLE first, then loaded, and asserts the
// send is deferred until a model exists (and never fires model-less).

const streamCalls: { url: string; body: unknown; signal: AbortSignal }[] = [];

vi.mock('./stream', () => ({
  streamChatSse: vi.fn(async (url: string, body: unknown, _onEvent: unknown, signal: AbortSignal) => {
    streamCalls.push({ url, body, signal });
    await new Promise(() => {});
  }),
}));

const createChatMock = vi.fn(async () => ({ id: 'new-1' }));

// Mutable models data: starts undefined (query pending), flipped on later.
let modelsData: { id: string; display_name: string; default_reasoning_effort: string }[] | undefined;

// The opening turn is persisted by POST /chats, so the server holds it while
// the models query is still in flight -- the row the resume effect acts on.
const persistedFirstMessage = {
  id: 'm-1',
  parent_message_id: null,
  sibling_index: 0,
  role: 'user',
  content: 'top 10 broker api in india',
  model_id: null,
  prompt_tokens: null,
  completion_tokens: null,
  created_at: '2026-08-17T00:00:00Z',
  stopped: false,
  no_answer: false,
  grounding: 'documents',
  validation_failed: false,
  citations: [],
  feedback: null,
  blocks: null,
  children: [],
};

vi.mock('./queries', () => ({
  useChat: (chatId: string | null) => ({
    data: {
      messages: chatId === 'new-1' ? [persistedFirstMessage] : [],
      has_summary: false,
    },
    isPending: false,
  }),
  useCreateChat: () => ({ mutateAsync: createChatMock, isPending: false }),
  useSetMessageFeedback: () => ({ mutate: vi.fn() }),
  useClearMessageFeedback: () => ({ mutate: vi.fn() }),
}));
vi.mock('@/features/documents/queries', () => ({ useDocuments: () => ({ data: [] }) }));
vi.mock('@/features/models/queries', () => ({ useModels: () => ({ data: modelsData }) }));
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
  modelsData = undefined;
});

test('the first message is not sent model-less while models are still loading, then fires once a model resolves', async () => {
  modelsData = undefined; // models query pending
  const user = userEvent.setup();
  const { rerender } = renderApp();

  const box = screen.getByPlaceholderText(/ask about your documents/i);
  await user.type(box, 'top 10 broker api in india');
  await user.keyboard('{Enter}');

  // Chat is created and we navigate, but with no model resolved the handoff
  // must NOT fire a model-less send.
  await waitFor(() => expect(createChatMock).toHaveBeenCalled());
  await new Promise((r) => setTimeout(r, 0));
  expect(streamCalls.length).toBe(0);

  // Models finish loading -> a re-render resolves effectiveModelId -> the
  // deferred handoff now sends exactly once, WITH the model.
  modelsData = [{ id: 'gpt', display_name: 'GPT5.6 Luna', default_reasoning_effort: 'off' }];
  rerender(
    <StrictMode>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={['/chat/new-1']}>
          <Routes>
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/chat/:chatId" element={<ChatPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </StrictMode>,
  );

  await waitFor(() => expect(streamCalls.length).toBeGreaterThan(0));
  const last = streamCalls[streamCalls.length - 1];
  if (!last) throw new Error('expected a stream send');
  expect(last.url).toContain('/messages/m-1/answer');
  expect((last.body as { model_id?: string }).model_id).toBe('gpt');
  expect(last.signal.aborted).toBe(false);
});
