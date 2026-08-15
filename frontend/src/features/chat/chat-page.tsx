import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';

import type { MessageNode } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Spinner } from '@/components/ui/spinner';

import { DocumentViewerDrawer } from '@/features/documents/document-viewer-drawer';
import { useDocuments } from '@/features/documents/queries';
import { useModels } from '@/features/models/queries';
import { useWorkspaces } from '@/features/workspaces/queries';
import { useWorkspace } from '@/features/workspaces/workspace-context';

import { AssistantMessage } from './assistant-message';
import { AttachmentUpload } from './attachment-upload';
import { ChatInput } from './chat-input';
import { EditMessageForm } from './edit-message-form';
import { EffortSelector, type ReasoningEffort } from './effort-selector';
import { MessageActions } from './message-actions';
import { ModelSelector } from './model-selector';
import {
  useChat,
  useClearMessageFeedback,
  useCreateChat,
  useSetMessageFeedback,
  useUploadAttachment,
} from './queries';
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
  const setFeedback = useSetMessageFeedback(chatId);
  const clearFeedback = useClearMessageFeedback(chatId);
  const { path, select } = useTreeSelection(chatQuery.data?.messages);
  const [modelId, setModelId] = useState<string | null>(null);
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>('off');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [pendingAttachmentIds, setPendingAttachmentIds] = useState<string[]>([]);
  const uploadAttachment = useUploadAttachment(chatId);

  // Citation -> source-document drawer (Task: click a citation to open the
  // source document). One drawer for the whole page -- every AssistantMessage
  // routes chip/marker clicks through this single open-state.
  const [viewerTarget, setViewerTarget] = useState<{
    documentId: string;
    page: number;
    filename: string;
    version?: number;
  } | null>(null);
  const openDocument = (source: SourceChipData): void => {
    if (!source.document_id) return;
    setViewerTarget({
      documentId: source.document_id,
      page: source.page,
      filename: source.filename,
      version: source.version,
    });
  };

  // Persisted citations (MessageNode.citations) → source chips; filenames
  // resolved from the workspace's document list (shared query cache).
  const { data: documents } = useDocuments(workspaceId, null);
  const documentNameById = useMemo(
    () => new Map((documents ?? []).map((d) => [d.id, d.filename])),
    [documents],
  );
  const chipsFor = (message: MessageNode): SourceChipData[] =>
    message.citations.map((c) => ({
      marker: c.marker,
      document_id: c.document_id ?? '',
      // Task 11 (D7): a web citation has no document row -- its filename is
      // derived from the URL's hostname instead of the documents lookup.
      filename: c.url
        ? new URL(c.url).hostname
        : (documentNameById.get(c.document_id ?? '') ?? 'Document'),
      page: c.page,
      section: c.section,
      version: c.version,
      url: c.url,
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

  const selectedModel = models?.find((m) => m.id === effectiveModelId) ?? null;

  const onModelChange = (id: string): void => {
    setModelId(id);
    const next = models?.find((m) => m.id === id);
    setReasoningEffort((next?.default_reasoning_effort as ReasoningEffort | undefined) ?? 'off');
  };

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
      stream.send(initialMessage, undefined, effectiveModelId, reasoningEffort, pendingAttachmentIds); // omit parent → append to leaf
      setPendingAttachmentIds([]);
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
      stream.send(content, undefined, effectiveModelId, reasoningEffort, pendingAttachmentIds); // omit parent → append to leaf
      setPendingAttachmentIds([]);
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
        caption={chatQuery.data?.has_summary ? 'Earlier turns summarized for context' : undefined}
        actions={
          <>
            <UsageMeter />
            <ModelSelector models={models ?? []} value={effectiveModelId} onChange={onModelChange} />
            {selectedModel?.supports_reasoning ? (
              <EffortSelector value={reasoningEffort} onChange={setReasoningEffort} />
            ) : null}
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
                      stream.send(
                        content,
                        m.parent_message_id ?? null,
                        effectiveModelId,
                        reasoningEffort,
                        pendingAttachmentIds,
                      );
                      setPendingAttachmentIds([]);
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
                blocks={m.blocks}
                stopped={m.stopped}
                grounding={m.grounding}
                validationFailed={m.validation_failed}
                onOpenDocument={openDocument}
                footer={
                  <MessageActions
                    entry={entry}
                    disabled={busy}
                    onSelectSibling={select}
                    onRegenerate={() => stream.regenerate(m.id)}
                    onSetFeedback={(rating, comment) =>
                      setFeedback.mutate({ messageId: m.id, rating, comment })
                    }
                    onClearFeedback={() => clearFeedback.mutate(m.id)}
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
      <div className="mx-auto flex w-full max-w-thread items-center justify-between gap-2 px-4">
        <AttachmentUpload
          onUpload={(f) => uploadAttachment.mutateAsync(f)}
          onUploaded={(id) => setPendingAttachmentIds((ids) => [...ids, id])}
        />
        {pendingAttachmentIds.length > 0 ? (
          <span className="text-[12px] text-muted">
            {pendingAttachmentIds.length} attachment{pendingAttachmentIds.length > 1 ? 's' : ''} ready
          </span>
        ) : null}
      </div>
      <ChatInput
        onSend={onSend}
        disabled={busy || (!chatId && createChat.isPending)}
        busy={busy}
        onStop={stream.stop}
      />
      {viewerTarget ? (
        <DocumentViewerDrawer
          documentId={viewerTarget.documentId}
          page={viewerTarget.page}
          filename={viewerTarget.filename}
          version={viewerTarget.version}
          onClose={() => setViewerTarget(null)}
        />
      ) : null}
    </>
  );
}
