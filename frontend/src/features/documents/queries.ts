import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { DocumentOut } from '@/api/types';
import { problemDetail } from '@/features/auth/mutations';

import { shouldPoll } from './status';

export interface MetadataFieldCreateInput {
  name: string;
  label: string;
  field_type: string;
  options: string[] | null;
}

export function useDocuments(workspaceId: string | null) {
  return useQuery({
    queryKey: ['documents', workspaceId],
    enabled: workspaceId !== null,
    refetchInterval: (query) =>
      shouldPoll(query.state.data as DocumentOut[] | undefined) ? 2500 : false,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/workspaces/{workspace_id}/documents', {
        params: { path: { workspace_id: workspaceId as string } },
      });
      if (error) throw new Error('failed to load documents');
      return data;
    },
  });
}

export function useDeleteDocument(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (documentId: string) => {
      const { error } = await api.DELETE('/api/v1/documents/{document_id}', {
        params: { path: { document_id: documentId } },
      });
      if (error) throw new Error('failed to delete document');
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['documents', workspaceId] }),
  });
}

export function usePinDocument(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ documentId, pinned }: { documentId: string; pinned: boolean }) => {
      const { data, error } = await api.PATCH('/api/v1/documents/{document_id}', {
        params: { path: { document_id: documentId } },
        body: { pinned },
      });
      if (error) throw new Error('failed to update pin');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['documents', workspaceId] }),
  });
}

export function useSetApproved(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ documentId, approved }: { documentId: string; approved: boolean }) => {
      const { data, error } = await api.PUT('/api/v1/documents/{document_id}/approved', {
        params: { path: { document_id: documentId } },
        body: { approved },
      });
      if (error) throw new Error('failed to update approval');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['documents', workspaceId] }),
  });
}

// DOC-6/Task 11: per-workspace metadata field schema (admin-managed) +
// per-document values. Fields are workspace-scoped (queryKey mirrors the
// `documents` list's ['documents', workspaceId] shape); values live on
// DocumentOut.meta and are PUT as a full replacement (see metadata.py).
export function useMetadataFields(workspaceId: string | null) {
  return useQuery({
    queryKey: ['metadata-fields', workspaceId],
    enabled: workspaceId !== null,
    queryFn: async () => {
      const { data, error } = await api.GET(
        '/api/v1/workspaces/{workspace_id}/metadata-fields',
        { params: { path: { workspace_id: workspaceId as string } } },
      );
      if (error) throw new Error('failed to load metadata fields');
      return data;
    },
  });
}

export function useCreateMetadataField(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: MetadataFieldCreateInput) => {
      const { data, error } = await api.POST(
        '/api/v1/workspaces/{workspace_id}/metadata-fields',
        {
          params: { path: { workspace_id: workspaceId as string } },
          body: input,
        },
      );
      if (error) throw new Error(problemDetail(error));
      return data;
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['metadata-fields', workspaceId] }),
  });
}

export function useDeleteMetadataField(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (fieldId: string) => {
      const { error } = await api.DELETE('/api/v1/metadata-fields/{field_id}', {
        params: { path: { field_id: fieldId } },
      });
      if (error) throw new Error('failed to delete metadata field');
    },
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ['metadata-fields', workspaceId] }),
  });
}

// Scoped by workspaceId (like useDeleteDocument/usePinDocument/useSetApproved
// above) purely for the invalidation key — the mutation itself is addressed
// by documentId, passed per-call since one dialog instance edits one document.
export function useSetDocumentMetadata(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      documentId,
      values,
    }: {
      documentId: string;
      values: Record<string, string>;
    }) => {
      const { data, error } = await api.PUT('/api/v1/documents/{document_id}/metadata', {
        params: { path: { document_id: documentId } },
        body: { values },
      });
      if (error) throw new Error(problemDetail(error));
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['documents', workspaceId] }),
  });
}
