import { useCallback, useState } from 'react';

import { uploadPendingAttachments } from './queries';
import type { PendingAttachment } from './use-pending-attachments';

export interface UseSendMessageOptions {
  chatId: string | null;
  workspaceId: string | null;
  createChat: (input: { workspace_id: string }) => Promise<{ id: string }>;
  // Sends into the current (already-existing) chat.
  sendToChat: (content: string, parentMessageId: string | null | undefined, attachmentIds: string[]) => void;
  // No chat existed yet -- a chat was just created (and attachments, if any,
  // already uploaded against its real id). The caller navigates there and
  // hands the message off (chat-page's existing initialMessage handoff).
  onNewChat: (chatId: string, content: string, attachmentIds: string[]) => void;
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
          if (!workspaceId) return;
          try {
            const chat = await createChat({ workspace_id: workspaceId });
            targetChatId = chat.id;
          } catch {
            setError('Could not start a new chat. Please try again.');
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
