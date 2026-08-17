import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { ChatPage } from './chat-page';

// Recovery path (bug 2026-08-17): a first message used to live only in the
// browser between "chat created" and "message sent", so a reload in that window
// lost it and left an empty "New chat". Now POST /chats persists it, and
// landing on the chat -- cold, with no router state at all, exactly as after a
// reload -- must stream the missing answer rather than strand the question.

const streamCalls: { url: string; body: unknown }[] = [];

vi.mock('./stream', () => ({
  streamChatSse: vi.fn(async (url: string, body: unknown) => {
    streamCalls.push({ url, body });
    await new Promise(() => {});
  }),
}));

const unansweredTurn = {
  id: 'm-1',
  parent_message_id: null,
  sibling_index: 0,
  role: 'user',
  content: 'what was revenue?',
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
  children: [] as unknown[],
};

const answeredTurn = {
  ...unansweredTurn,
  children: [{ ...unansweredTurn, id: 'm-2', parent_message_id: 'm-1', role: 'assistant', content: 'Revenue was 10.' }],
};

let tree: unknown[] = [];

vi.mock('./queries', () => ({
  useChat: () => ({ data: { messages: tree, has_summary: false }, isPending: false }),
  useCreateChat: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSetMessageFeedback: () => ({ mutate: vi.fn() }),
  useClearMessageFeedback: () => ({ mutate: vi.fn() }),
}));
vi.mock('@/features/documents/queries', () => ({ useDocuments: () => ({ data: [] }) }));
vi.mock('@/features/models/queries', () => ({
  useModels: () => ({
    data: [{ id: 'gpt', display_name: 'GPT5.6 Luna', default_reasoning_effort: 'off' }],
  }),
}));
vi.mock('@/features/workspaces/queries', () => ({
  useWorkspaces: () => ({ data: [{ id: 'ws-1', name: 'Acme', web_search_enabled: false }] }),
}));
vi.mock('@/features/workspaces/workspace-context', () => ({
  useWorkspace: () => ({ workspaceId: 'ws-1', setWorkspaceId: vi.fn() }),
}));
vi.mock('./use-pending-attachments', () => ({
  usePendingAttachments: () => ({
    files: [], addFiles: vi.fn(), remove: vi.fn(), clear: vi.fn(), error: null,
  }),
}));

function renderColdAtChat() {
  return render(
    <StrictMode>
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        {/* no router state: this is what a reload looks like */}
        <MemoryRouter initialEntries={['/chat/new-1']}>
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
  tree = [];
});

test('a user message left without an answer is resumed on a cold load', async () => {
  tree = [unansweredTurn];
  renderColdAtChat();

  await waitFor(() => expect(streamCalls.length).toBeGreaterThan(0));
  expect(streamCalls[0]!.url).toContain('/messages/m-1/answer');
  expect((streamCalls[0]!.body as { model_id?: string }).model_id).toBe('gpt');
  // StrictMode double-invokes effects; the guard must keep it to one call so
  // the turn can't be answered twice.
  expect(streamCalls.length).toBe(1);
});

test('an already-answered turn is left alone', async () => {
  tree = [answeredTurn];
  renderColdAtChat();

  await new Promise((r) => setTimeout(r, 20));
  expect(streamCalls).toEqual([]);
});
