import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { MessageNode } from '@/api/types';

import { ChatPage } from './chat-page';

// Focused wiring test (per the interactive-forms design doc, §3): a form
// block's submit must reuse the SAME send path as the composer -- no new
// endpoint, no bespoke state machine. Everything ChatPage pulls in besides
// the send hook is mocked out so this test isolates exactly that wire:
// AssistantMessage's onFormSubmit -> chat-page's onSend -> useSendMessage.send.

const sendMock = vi.fn();

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return {
    ...actual,
    useParams: () => ({ chatId: 'chat-1' }),
    useLocation: () => ({ pathname: '/chat/chat-1', state: null }),
    useNavigate: () => vi.fn(),
  };
});

vi.mock('@/features/documents/queries', () => ({
  useDocuments: () => ({ data: [] }),
}));

vi.mock('@/features/models/queries', () => ({
  useModels: () => ({ data: [] }),
}));

vi.mock('@/features/workspaces/queries', () => ({
  useWorkspaces: () => ({ data: [{ id: 'ws-1', name: 'Acme' }] }),
}));

vi.mock('@/features/workspaces/workspace-context', () => ({
  useWorkspace: () => ({ workspaceId: 'ws-1', setWorkspaceId: vi.fn() }),
}));

const formMessage: MessageNode = {
  id: 'm-2',
  parent_message_id: 'm-1',
  sibling_index: 0,
  role: 'assistant',
  content: 'Sure, tell me more about your trip:',
  model_id: null,
  prompt_tokens: null,
  completion_tokens: null,
  created_at: '2026-08-15T00:00:00Z',
  stopped: false,
  no_answer: false,
  grounding: 'documents',
  validation_failed: false,
  citations: [],
  feedback: null,
  blocks: [
    {
      type: 'form',
      title: 'Trip details',
      fields: [{ name: 'destination', label: 'Destination', kind: 'text', required: true }],
      submit_label: 'Create itinerary',
    },
  ],
  children: [],
};

const rootMessage: MessageNode = {
  id: 'm-1',
  parent_message_id: null,
  sibling_index: 0,
  role: 'user',
  content: 'Plan a trip for me.',
  model_id: null,
  prompt_tokens: null,
  completion_tokens: null,
  created_at: '2026-08-15T00:00:00Z',
  stopped: false,
  no_answer: false,
  grounding: 'documents',
  validation_failed: false,
  citations: [],
  feedback: null,
  blocks: null,
  children: [formMessage],
};

vi.mock('./queries', () => ({
  useChat: () => ({ data: { messages: [rootMessage], has_summary: false }, isPending: false }),
  useCreateChat: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSetMessageFeedback: () => ({ mutate: vi.fn() }),
  useClearMessageFeedback: () => ({ mutate: vi.fn() }),
}));

vi.mock('./use-chat-stream', () => ({
  useChatStream: () => ({
    status: 'idle',
    text: '',
    sources: [],
    blocks: [],
    citations: [],
    noAnswer: false,
    grounding: 'documents',
    validationFailed: false,
    agentSteps: [],
    toolResults: [],
    pendingUserContent: null,
    doneMessageId: null,
    errorDetail: null,
    send: vi.fn(),
    regenerate: vi.fn(),
    abort: vi.fn(),
    reset: vi.fn(),
    stop: vi.fn(),
  }),
}));

vi.mock('./use-send-message', () => ({
  useSendMessage: () => ({ send: sendMock, sending: false, error: null, clearError: vi.fn() }),
}));

vi.mock('./use-pending-attachments', () => ({
  usePendingAttachments: () => ({
    files: [],
    addFiles: vi.fn(),
    remove: vi.fn(),
    clear: vi.fn(),
    error: null,
  }),
}));

function renderChatPage() {
  const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ChatPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  sendMock.mockReset();
});

test('submitting a persisted form block calls the existing send hook with the composed message', async () => {
  const user = userEvent.setup();
  renderChatPage();

  expect(screen.getByText('Trip details')).toBeInTheDocument();
  await user.type(screen.getByLabelText('Destination', { exact: false }), 'Kyoto');
  await user.click(screen.getByRole('button', { name: 'Create itinerary' }));

  // send hook is called with exactly the composed message and no parent
  // override -- identical to a normal composer send (chat-page's onSend).
  expect(sendMock).toHaveBeenCalledWith('Destination: Kyoto');
});
