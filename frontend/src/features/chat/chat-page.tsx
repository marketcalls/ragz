import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';

import type { MessageNode } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Spinner } from '@/components/ui/spinner';

import { useDocuments } from '@/features/documents/queries';
import { useModels } from '@/features/models/queries';
import { useWorkspaces } from '@/features/workspaces/queries';
import { useWorkspace } from '@/features/workspaces/workspace-context';

import { AssistantMessage } from './assistant-message';
import { ChatInput } from './chat-input';
import { EditMessageForm } from './edit-message-form';
import { MessageActions } from './message-actions';
import { ModelSelector } from './model-selector';
import { useChat, useCreateChat } from './queries';
import type { SourceChipData } from './source-panel';
import { StreamingMessage } from './streaming-message';
import { treeContains } from './tree';
import { UsageMeter } from './usage-meter';
import { UserMessage } from './user-message';
import { useChatStream } from './use-chat-stream';
import { useTreeSelection } from './use-tree-selection';

export function ChatPage() {
  const { chatId = null } = useParams<{ chatId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const { workspaceId } = useWorkspace();
  const { data: workspaces } = useWorkspaces();
  const { data: models } = useModels();
  const chatQuery = useChat(chatId);
  const createChat = useCreateChat();
  const stream = useChatStream(chatId);
  const { path, select } = useTreeSelection(chatQuery.data?.messages);
  const [modelId, setModelId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);

  // Persisted citations (MessageNode.citations) → source chips; filenames
  // resolved from the workspace's document list (shared query cache).
  const { data: documents } = useDocuments(workspaceId);
  const documentNameById = useMemo(
    () => new Map((documents ?? []).map((d) => [d.id, d.filename])),
    [documents],
  );
  const chipsFor = (message: MessageNode): SourceChipData[] =>
    message.citations.map((c) => ({
      marker: c.marker,
      document_id: c.document_id,
      filename: documentNameById.get(c.document_id) ?? 'Document',
      page: c.page,
    }));

  const workspace = workspaces?.find((w) => w.id === workspaceId);
  // Workspace default model, else first enabled (see task assumption).
  const workspaceDefault =
    (workspace && 'default_model_id' in workspace
      ? (workspace as { default_model_id?: string | null }).default_model_id
      : null) ?? null;
  // Must mirror ModelSelector's display fallback (first enabled model) — otherwise a
  // workspace without a default shows a selected model but sends none (409, found by E2E).
  const effectiveModelId = modelId ?? workspaceDefault ?? models?.[0]?.id ?? null;

  // DEVIATION (Task 10, carry-forward from the Task 8 review): useChatStream's
  // status freezes after abort() — no terminal event follows, so the UI would
  // stay stuck showing the old chat's in-flight/frozen stream state. This hook
  // instance isn't remounted when chatId changes (same ChatPage element), so
  // switching chats mid-stream via the sidebar needs an explicit abort+reset.
  // Cleanup fires on chatId change (before the effects below) and on unmount.
  useEffect(() => {
    return () => {
      stream.abort();
      stream.reset();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- abort/reset are stable; keyed on chatId only
  }, [chatId]);

  // New-chat handoff: /chat → create → navigate with initialMessage → auto-send once.
  const initialSentRef = useRef(false);
  const initialMessage = (location.state as { initialMessage?: string } | null)?.initialMessage;
  useEffect(() => {
    if (chatId && initialMessage && !initialSentRef.current) {
      initialSentRef.current = true;
      stream.send(initialMessage, undefined, effectiveModelId); // omit parent → append to leaf
      navigate(location.pathname, { replace: true, state: null }); // consume the state
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once per mount/handoff
  }, [chatId, initialMessage]);

  // Once the refetched tree contains the streamed message, drop the streamed block.
  const streamedInTree = useMemo(
    () =>
      stream.doneMessageId !== null &&
      treeContains(chatQuery.data?.messages ?? [], stream.doneMessageId),
    [chatQuery.data, stream.doneMessageId],
  );
  useEffect(() => {
    if (streamedInTree) stream.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset is stable
  }, [streamedInTree]);

  const onSend = (content: string): void => {
    if (chatId) {
      stream.send(content, undefined, effectiveModelId); // omit parent → append to leaf
      return;
    }
    if (!workspaceId) return;
    createChat.mutate(
      { workspace_id: workspaceId },
      { onSuccess: (chat) => navigate(`/chat/${chat.id}`, { state: { initialMessage: content } }) },
    );
  };

  const busy = stream.status === 'retrieving' || stream.status === 'streaming';
  const showStreamBlock = stream.status !== 'idle' && !streamedInTree;

  return (
    <>
      <TopBar
        title={chatQuery.data?.title || 'New chat'}
        actions={
          <>
            <UsageMeter />
            <ModelSelector models={models ?? []} value={effectiveModelId} onChange={setModelId} />
          </>
        }
      />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-thread space-y-5 px-4 py-6">
          {chatId && chatQuery.isPending ? <Spinner label="Loading chat…" /> : null}
          {path.map((entry) => {
            const m = entry.message;
            if (m.role === 'user') {
              if (editingId === m.id) {
                return (
                  <EditMessageForm
                    key={m.id}
                    initial={m.content}
                    onCancel={() => setEditingId(null)}
                    onSend={(content) => {
                      setEditingId(null);
                      // Sibling of the edited message: same parent (phase1 spec §2.1).
                      // For a ROOT message this passes explicit null — which the backend
                      // reads as "new root sibling" (presence semantics, chat/schemas.py).
                      stream.send(content, m.parent_message_id ?? null, effectiveModelId);
                    }}
                  />
                );
              }
              return (
                <UserMessage
                  key={m.id}
                  content={m.content}
                  footer={
                    <MessageActions
                      entry={entry}
                      disabled={busy}
                      onSelectSibling={select}
                      onEdit={() => setEditingId(m.id)}
                    />
                  }
                />
              );
            }
            return (
              <AssistantMessage
                key={m.id}
                content={m.content}
                sources={chipsFor(m)}
                footer={
                  <MessageActions
                    entry={entry}
                    disabled={busy}
                    onSelectSibling={select}
                    onRegenerate={() => stream.regenerate(m.id)}
                  />
                }
              />
            );
          })}
          {showStreamBlock ? <StreamingMessage stream={stream} /> : null}
          {!chatId && path.length === 0 && stream.status === 'idle' ? (
            <p className="pt-16 text-center text-[15px] text-secondary">
              Ask a question about the documents in this workspace.
            </p>
          ) : null}
        </div>
      </div>
      <ChatInput onSend={onSend} disabled={busy || (!chatId && createChat.isPending)} />
    </>
  );
}
