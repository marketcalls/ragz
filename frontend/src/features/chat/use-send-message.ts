import { useCallback, useState } from 'react';

import { uploadPendingAttachments } from './queries';
import type { PendingAttachment } from './use-pending-attachments';

export interface UseSendMessageOptions {
  chatId: string | null;
  workspaceId: string | null;
  createChat: (input: { workspace_id: string }) => Promise<{ id: string }>;
  // Sends into the current (already-existing) chat.
  sendToChat: (content: string, parentMessageId: string | null | undefined, attachmentIds: string[]) => void;
  // No chat existed yet -- a chat was just created. The caller navigates there.
  // `content` non-null: attachments were uploaded against the new chat id, so
  // the message still has to be sent from the client (chat-page's
  // initialMessage handoff) to carry its attachment_ids.
  // `content` null: the message was persisted server-side by POST /chats
  // (first_message), so there is nothing to hand off -- just navigate, and
  // chat-page's resume effect streams the reply.
  onNewChat: (chatId: string, content: string | null, attachmentIds: string[]) => void;
  pendingFiles: PendingAttachment[];
  clearPending: () => void;
}

/**
 * Orchestrates "hold files locally, upload at send": creates the chat first
 * if one doesn't exist yet (so attachments always upload against a real
 * chat_id, never `/chats/null/attachments`), then uploads every pending file,
 * then sends the message with the resulting `attachment_ids`. An upload
 * failure aborts the send entirely -- pending files are kept (not cleared) so
 * the user can retry or remove them, and no message is sent that references a
 * partially-uploaded batch.
 */
export function useSendMessage({
  chatId,
  workspaceId,
  createChat,
  sendToChat,
  onNewChat,
  pendingFiles,
  clearPending,
}: UseSendMessageOptions) {
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = useCallback(
    async (content: string, parentMessageId?: string | null): Promise<void> => {
      setError(null);
      setSending(true);
      try {
        let targetChatId = chatId;
        if (!targetChatId) {
          if (!workspaceId) {
            // Never a silent no-op: a first message sent before the workspace
            // has resolved must surface *why* nothing happened, not look like a
            // dropped message. Retrying once the workspace loads succeeds.
            setError('Still loading your workspace — please try again in a moment.');
            return;
          }
          // No attachments: persist the opening turn WITH the chat, so it
          // cannot be lost between create and send (a reload in that window
          // used to drop it and leave an empty "New chat"). chat-page's
          // resume effect streams the reply once it lands.
          // With attachments we must create first, upload against the real
          // chat id, then send -- the message has to carry attachment_ids, so
          // it can't be persisted before the uploads exist.
          const withoutAttachments = pendingFiles.length === 0;
          try {
            const chat = await createChat({
              workspace_id: workspaceId,
              ...(withoutAttachments ? { first_message: content } : {}),
            });
            targetChatId = chat.id;
          } catch {
            setError('Could not start a new chat. Please try again.');
            return;
          }
          if (withoutAttachments) {
            onNewChat(targetChatId, null, []); // already persisted: navigate only
            return;
          }
        }

        let attachmentIds: string[] = [];
        if (pendingFiles.length > 0) {
          try {
            attachmentIds = await uploadPendingAttachments(
              targetChatId,
              pendingFiles.map((f) => f.file),
            );
          } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to upload attachment.');
            return;
          }
          clearPending();
        }

        if (chatId) {
          sendToChat(content, parentMessageId, attachmentIds);
        } else {
          onNewChat(targetChatId, content, attachmentIds);
        }
      } finally {
        setSending(false);
      }
    },
    [chatId, workspaceId, createChat, sendToChat, onNewChat, pendingFiles, clearPending],
  );

  const clearError = useCallback(() => setError(null), []);

  return { send, sending, error, clearError };
}
