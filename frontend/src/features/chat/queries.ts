import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api, authFetch } from '@/api/client';

export function useChats(workspaceId: string | null) {
  return useQuery({
    queryKey: ['chats', workspaceId],
    enabled: workspaceId !== null,
    queryFn: async () => {
      // workspace_id is optional server-side (omitted = all the caller's chats);
      // the sidebar always scopes to the active workspace.
      const { data, error } = await api.GET('/api/v1/chats', {
        params: { query: { workspace_id: workspaceId as string } },
      });
      if (error) throw new Error('failed to load chats');
      return data;
    },
  });
}

export function useChat(chatId: string | null) {
  return useQuery({
    queryKey: ['chat', chatId],
    enabled: chatId !== null,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/chats/{chat_id}', {
        params: { path: { chat_id: chatId as string } },
      });
      if (error) throw new Error('failed to load chat');
      return data;
    },
  });
}

export function useCreateChat() {
  const queryClient = useQueryClient();
  return useMutation({
    // first_message persists the opening turn WITH the chat, so it is never
    // held only in browser memory between create and send. The reply is
    // streamed afterwards by chat-page's resume effect.
    mutationFn: async (body: { workspace_id: string; first_message?: string }) => {
      const { data, error } = await api.POST('/api/v1/chats', { body });
      if (error) throw new Error('failed to create chat');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['chats'] }),
  });
}

export function useRenameChat() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { chatId: string; title: string }) => {
      const { data, error } = await api.PATCH('/api/v1/chats/{chat_id}', {
        params: { path: { chat_id: input.chatId } },
        body: { title: input.title },
      });
      if (error) throw new Error('failed to rename chat');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['chats'] }),
  });
}

export function useSetMessageFeedback(chatId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { messageId: string; rating: 'up' | 'down'; comment?: string }) => {
      const { data, error } = await api.PUT('/api/v1/messages/{message_id}/feedback', {
        params: { path: { message_id: input.messageId } },
        body: { rating: input.rating, comment: input.comment },
      });
      if (error) throw new Error('failed to save feedback');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['chat', chatId] }),
  });
}

export function useClearMessageFeedback(chatId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (messageId: string) => {
      const { error } = await api.DELETE('/api/v1/messages/{message_id}/feedback', {
        params: { path: { message_id: messageId } },
      });
      if (error) throw new Error('failed to clear feedback');
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['chat', chatId] }),
  });
}

export function useDeleteChat() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (chatId: string) => {
      const { error } = await api.DELETE('/api/v1/chats/{chat_id}', {
        params: { path: { chat_id: chatId } },
      });
      if (error) throw new Error('failed to delete chat');
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['chats'] }),
  });
}

export interface AttachmentOut {
  id: string;
  kind: string;
  filename: string;
  mime: string;
  status: string;
}

// Bypasses the generated `api.POST` client (JSON-body-typed, not multipart-aware):
// a plain fetch with FormData is required, same reason
// `frontend/src/features/documents/upload.ts` hand-rolls its own upload
// instead of using `api.POST`. Auth is still handled the same way as every
// other request in this feature (see `stream.ts`): route the Request through
// `authFetch`, which attaches the bearer token from the in-memory auth store
// and transparently retries once after a refresh on a 401, and set
// `credentials: 'include'` so the httpOnly refresh cookie travels too.
//
// A plain async function, not a hook bound to a chatId at creation time:
// attachments are now held locally (`use-pending-attachments.ts`) and only
// uploaded at send time, once a real chat_id is known (fixes the new-chat
// flow, which previously POSTed to `/chats/null/attachments`).
export async function uploadAttachment(chatId: string, file: File): Promise<AttachmentOut> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await authFetch(
    new Request(new URL(`/api/v1/chats/${chatId}/attachments`, window.location.origin), {
      method: 'POST',
      body: formData,
      credentials: 'include',
    }),
  );
  if (!res.ok) throw new Error('failed to upload attachment');
  return res.json() as Promise<AttachmentOut>;
}

// Uploads every pending file sequentially against a concrete chat_id,
// returning the server-assigned attachment ids in order. Stops and throws on
// the first failure -- the caller (use-send-message.ts) aborts the send
// rather than sending a message that references a partially-uploaded batch.
export async function uploadPendingAttachments(chatId: string, files: File[]): Promise<string[]> {
  const ids: string[] = [];
  for (const file of files) {
    try {
      const result = await uploadAttachment(chatId, file);
      ids.push(result.id);
    } catch {
      throw new Error(`Failed to attach "${file.name}". Please try again.`);
    }
  }
  return ids;
}
