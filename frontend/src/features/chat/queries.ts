import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';

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
    mutationFn: async (body: { workspace_id: string }) => {
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
