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

// Self-service first-run: is this a fresh install whose FIRST registrant
// becomes the platform superadmin? Drives which form login-page renders.
export function useBootstrapStatus() {
  return useQuery({
    queryKey: ['bootstrap-status'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/auth/bootstrap-status');
      // Fail safe to the normal login form if the check itself errors.
      if (error || !data) return { needs_setup: false };
      return data;
    },
    staleTime: 5 * 60 * 1000,
  });
}

// First-run only: creates the platform superadmin and auto-logs-in (the
// response carries an access token + sets the refresh cookie, exactly like
// login). Returns 409 once a superadmin already exists.
export function useRegister() {
  return useMutation({
    mutationFn: async (creds: { email: string; password: string }) => {
      const { data, error } = await api.POST('/api/v1/auth/register', { body: creds });
      if (error) throw new Error(problemDetail(error));
      if (!data) throw new Error('Registration failed: the server did not respond');
      return data;
    },
    onSuccess: (data) => {
      setAccessToken(data.access_token);
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

// Enumeration-safe on the backend already (identical 202 whether or not the
// email exists) -- callers must not branch UI copy on success vs. error.
export function useForgotPassword() {
  return useMutation({
    mutationFn: async (body: { email: string }) => {
      const { error } = await api.POST('/api/v1/auth/forgot-password', { body });
      if (error) throw new Error(problemDetail(error));
    },
  });
}

export function useResetPassword() {
  return useMutation({
    mutationFn: async (body: { token: string; new_password: string }) => {
      const { error } = await api.POST('/api/v1/auth/reset-password', { body });
      if (error) throw new Error(problemDetail(error));
    },
  });
}

export function useChangePassword() {
  return useMutation({
    mutationFn: async (body: { current_password: string; new_password: string }) => {
      const { error } = await api.POST('/api/v1/auth/change-password', { body });
      if (error) throw new Error(problemDetail(error));
    },
  });
}
