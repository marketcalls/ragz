import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { FolderOut } from '@/api/types';

export function useFolders(workspaceId: string | null) {
  return useQuery({
    queryKey: ['folders', workspaceId],
    enabled: workspaceId !== null,
    queryFn: async (): Promise<FolderOut[]> => {
      const { data, error } = await api.GET('/api/v1/workspaces/{workspace_id}/folders', {
        params: { path: { workspace_id: workspaceId as string } },
      });
      if (error) throw new Error('failed to load folders');
      return data;
    },
  });
}

export function useCreateFolder(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      name,
      parentFolderId,
    }: {
      name: string;
      parentFolderId: string | null;
    }) => {
      const { data, error } = await api.POST('/api/v1/workspaces/{workspace_id}/folders', {
        params: { path: { workspace_id: workspaceId as string } },
        body: { name, parent_folder_id: parentFolderId },
      });
      if (error) throw new Error('failed to create folder');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['folders', workspaceId] }),
  });
}

export function usePatchFolder(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      folderId,
      ...body
    }: {
      folderId: string;
      name?: string;
      parent_folder_id?: string | null;
    }) => {
      const { data, error } = await api.PATCH('/api/v1/folders/{folder_id}', {
        params: { path: { folder_id: folderId } },
        body,
      });
      if (error) throw new Error('failed to update folder');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['folders', workspaceId] }),
  });
}

export function useDeleteFolder(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (folderId: string) => {
      const { data, error } = await api.DELETE('/api/v1/folders/{folder_id}', {
        params: { path: { folder_id: folderId } },
      });
      if (error) throw new Error('failed to delete folder');
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['folders', workspaceId] });
      void queryClient.invalidateQueries({ queryKey: ['documents', workspaceId] });
    },
  });
}

export function useEnsureFolderPath(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (path: string) => {
      const { data, error } = await api.POST(
        '/api/v1/workspaces/{workspace_id}/folders/ensure-path',
        { params: { path: { workspace_id: workspaceId as string } }, body: { path } },
      );
      if (error) throw new Error('failed to create folder path');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['folders', workspaceId] }),
  });
}

export interface FolderNode extends FolderOut {
  children: FolderNode[];
}

/** Builds a tree from a flat list -- folders are a small, workspace-scoped
 * list (same "fetch whole, build client-side" pattern as groups/metadata
 * fields elsewhere in this app), so no server-side tree endpoint is needed. */
export function buildFolderTree(folders: FolderOut[]): FolderNode[] {
  const byId = new Map<string, FolderNode>(folders.map((f) => [f.id, { ...f, children: [] }]));
  const roots: FolderNode[] = [];
  for (const folder of byId.values()) {
    if (folder.parent_folder_id && byId.has(folder.parent_folder_id)) {
      byId.get(folder.parent_folder_id)!.children.push(folder);
    } else {
      roots.push(folder);
    }
  }
  const sortByName = (nodes: FolderNode[]): void => {
    nodes.sort((a, b) => a.name.localeCompare(b.name));
    nodes.forEach((n) => sortByName(n.children));
  };
  sortByName(roots);
  return roots;
}
