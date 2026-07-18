import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import { api } from '@/api/client';
import { setAccessToken } from '@/lib/auth-store';

export function problemDetail(body: unknown): string {
  if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
    return body.detail || 'Request failed';
  }
  return 'Request failed';
}

export function useLogin() {
  const navigate = useNavigate();
  return useMutation({
    mutationFn: async (creds: { email: string; password: string }) => {
      const { data, error } = await api.POST('/api/v1/auth/login', { body: creds });
      if (error) throw new Error(problemDetail(error));
      return data;
    },
    onSuccess: (data) => {
      setAccessToken(data.access_token);
      navigate('/chat', { replace: true });
    },
  });
}

export function useLogout() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await api.POST('/api/v1/auth/logout');
    },
    onSettled: () => {
      setAccessToken(null);
      queryClient.clear();
      navigate('/login', { replace: true });
    },
  });
}

export function useAcceptInvite() {
  return useMutation({
    mutationFn: async (body: { token: string; password: string }) => {
      const { data, error } = await api.POST('/api/v1/auth/invitations/accept', { body });
      if (error) throw new Error(problemDetail(error));
      return data;
    },
  });
}
