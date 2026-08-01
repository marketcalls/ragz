import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import { problemDetail } from '@/features/auth/mutations';

export interface RoleTemplateInput {
  name: string;
  description: string;
  permissions: string[];
}

// Admins (not just superadmins) can GET the list — they need it to populate
// the assignment select on the users page. Only superadmin can mutate
// (enforced server-side; see backend/src/ragz/api/routes/admin_roles.py).
export function useRoles() {
  return useQuery({
    queryKey: ['roles'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/roles');
      if (error) throw new Error('failed to load roles');
      return data;
    },
  });
}

function useInvalidateRoles() {
  const queryClient = useQueryClient();
  return () => void queryClient.invalidateQueries({ queryKey: ['roles'] });
}

export function useCreateRole() {
  const invalidate = useInvalidateRoles();
  return useMutation({
    mutationFn: async (body: RoleTemplateInput) => {
      const { data, error } = await api.POST('/api/v1/admin/roles', { body });
      if (error) throw new Error(problemDetail(error));
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useUpdateRole() {
  const invalidate = useInvalidateRoles();
  return useMutation({
    mutationFn: async (input: { roleId: string; body: Partial<RoleTemplateInput> }) => {
      const { data, error } = await api.PATCH('/api/v1/admin/roles/{role_template_id}', {
        params: { path: { role_template_id: input.roleId } },
        body: input.body,
      });
      if (error) throw new Error(problemDetail(error));
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useDeleteRole() {
  const invalidate = useInvalidateRoles();
  return useMutation({
    mutationFn: async (roleId: string) => {
      const { error } = await api.DELETE('/api/v1/admin/roles/{role_template_id}', {
        params: { path: { role_template_id: roleId } },
      });
      // 409 "role template is assigned to at least one user" surfaces here
      // verbatim via problemDetail — callers toast err.message.
      if (error) throw new Error(problemDetail(error));
    },
    onSuccess: invalidate,
  });
}

// Assigns (or, with null, clears back to the base "user" role) a custom role
// template on a single user. Lives here rather than features/admin/users so
// the roles list + assignment mutation share one home.
export function useAssignCustomRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { userId: string; roleTemplateId: string | null }) => {
      const { error } = await api.PUT('/api/v1/users/{user_id}/custom-role', {
        params: { path: { user_id: input.userId } },
        body: { role_template_id: input.roleTemplateId },
      });
      if (error) throw new Error(problemDetail(error));
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['users'] }),
  });
}
