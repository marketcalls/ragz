import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { WorkspaceRole } from '@/api/types';

export function useWorkspaces() {
  return useQuery({
    queryKey: ['workspaces'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/workspaces');
      if (error) throw new Error('failed to load workspaces');
      return data;
    },
  });
}

export function usePatchWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      ...body
    }: {
      id: string;
      top_k?: number;
      min_score?: number;
      rerank_enabled?: boolean;
      system_prompt_override?: string | null;
      fallback_policy?: 'general_knowledge' | 'decline';
      web_search_enabled?: boolean;
      strict_mode?: boolean;
      enrichment_enabled?: boolean;
    }) => {
      const { data, error } = await api.PATCH('/api/v1/workspaces/{workspace_id}', {
        params: { path: { workspace_id: id } },
        body,
      });
      if (error) throw new Error('failed to update workspace settings');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
  });
}

export function useCreateWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: { name: string }) => {
      const { data, error } = await api.POST('/api/v1/workspaces', { body });
      if (error) throw new Error('failed to create workspace');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
  });
}

export function usePatchEmbeddingModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, embedding_model_id }: { id: string; embedding_model_id: string }) => {
      const { data, error, response } = await api.PATCH(
        '/api/v1/workspaces/{workspace_id}/embedding-model',
        { params: { path: { workspace_id: id } }, body: { embedding_model_id } },
      );
      if (error) {
        if (response?.status === 409) {
          throw new EmbeddingModelLockedError('workspace already has indexed documents');
        }
        throw new Error('failed to update embedding model');
      }
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
  });
}

export class EmbeddingModelLockedError extends Error {}

export function useStartReembed() {
  return useMutation({
    mutationFn: async ({ id, new_embedding_model_id }: { id: string; new_embedding_model_id: string }) => {
      const { data, error } = await api.POST('/api/v1/workspaces/{workspace_id}/reembed', {
        params: { path: { workspace_id: id } },
        body: { new_embedding_model_id },
      });
      if (error) throw new Error('failed to start re-embed');
      return data;
    },
  });
}

// DOC-10 fix round: the backend's GET /reembed-status already returns the
// latest job for the workspace unconditionally (or a clean 404 -> null when
// none has ever run), so this query needs no local "has the user just
// triggered a re-embed" flag to be safe to fire. Gating it behind such a flag
// (as this used to do via a `pendingModelId !== null` check in the caller)
// meant closing and reopening the settings dialog — which remounts the
// component and resets that local state — silently lost visibility into an
// in-progress or just-failed job. `enabled` defaults to true so callers only
// need to pass it explicitly to suppress the fetch (e.g. before workspaceId
// is known).
export function useReembedStatus(workspaceId: string, enabled = true) {
  return useQuery({
    queryKey: ['reembed-status', workspaceId],
    enabled,
    // Fix round 2: only keep polling while a job actually exists and hasn't
    // finished yet. A workspace with no re-embed history ever resolves its
    // queryFn to `null` (see below), so `query.state.data?.finished_at` was
    // always `undefined` -- never strictly `false` or truthy -- and the old
    // `data?.finished_at ? false : 1500` check polled forever for any
    // workspace that had simply never run a re-embed job. Require a job to be
    // present (`data != null`) before continuing to poll.
    refetchInterval: (query) =>
      query.state.data != null && query.state.data.finished_at == null ? 1500 : false,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        '/api/v1/workspaces/{workspace_id}/reembed-status',
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        if (response?.status === 404) return null;
        throw new Error('failed to load re-embed status');
      }
      return data;
    },
  });
}

// Workspace/department Members management (RBAC-08 backend, previously
// uncalled from the frontend). "owner"/"manager" are department admins per
// the governance design -- see MembersSection for the UI labels.
export function useWorkspaceMembers(workspaceId: string) {
  return useQuery({
    queryKey: ['workspace-members', workspaceId],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/workspaces/{workspace_id}/members', {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error) throw new Error('failed to load members');
      return data;
    },
  });
}

export function useAddMember(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ user_id, role }: { user_id: string; role: WorkspaceRole }) => {
      const { error } = await api.POST('/api/v1/workspaces/{workspace_id}/members', {
        params: { path: { workspace_id: workspaceId } },
        body: { user_id, role },
      });
      if (error) throw new Error('failed to add member');
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['workspace-members', workspaceId] }),
  });
}

export function useUpdateMemberRole(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ userId, role }: { userId: string; role: WorkspaceRole }) => {
      const { data, error } = await api.PATCH(
        '/api/v1/workspaces/{workspace_id}/members/{user_id}',
        { params: { path: { workspace_id: workspaceId, user_id: userId } }, body: { role } },
      );
      if (error) throw new Error('failed to update member role');
      return data;
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['workspace-members', workspaceId] }),
  });
}

export function useRemoveMember(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (userId: string) => {
      const { error } = await api.DELETE('/api/v1/workspaces/{workspace_id}/members/{user_id}', {
        params: { path: { workspace_id: workspaceId, user_id: userId } },
      });
      if (error) throw new Error('failed to remove member');
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['workspace-members', workspaceId] }),
  });
}
