import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
      // A dead backend / proxy error can yield neither data nor a parsed
      // error body -- surface it instead of crashing on data.access_token.
      if (!data) throw new Error('Login failed: the server did not respond');
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

export function useSsoStatus() {
  return useQuery({
    queryKey: ['sso-status'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/auth/oidc/status');
      if (error) return { enabled: false };
      return data;
    },
    staleTime: 5 * 60 * 1000,
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
