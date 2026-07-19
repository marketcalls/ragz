import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { ClientErrorOut } from '@/api/types';

// GET /api/v1/superadmin/health (Task 8) returns a plain dict — FastAPI has
// no response_model on that route, so there's no generated OpenAPI schema
// component to alias in api/types.ts. Typed locally instead, mirroring the
// exact shapes produced by backend/src/raghub/api/routes/superadmin_ops.py
// and backend/src/raghub/modules/ops/health.py.

export interface QueueHealth {
  status: 'ok' | 'error';
  depths?: Record<string, number>;
  detail?: string;
}

export interface QdrantCollection {
  name: string;
  points_count: number;
}

export interface QdrantHealth {
  status: 'ok' | 'error';
  collections?: QdrantCollection[];
  detail?: string;
}

export interface LiteLlmHealth {
  status: 'ok' | 'error';
  detail?: string;
}

export interface OrgUsageRow {
  org_id: string;
  name: string;
  tokens: number;
}

// Unlike the other components, `orgs` is a bare list on success — only the
// error path gets the {status, detail} wrapper (see superadmin_ops.py).
export type OrgsHealth = OrgUsageRow[] | { status: 'error'; detail?: string };

export interface SystemHealth {
  queues: QueueHealth;
  qdrant: QdrantHealth;
  litellm: LiteLlmHealth;
  orgs: OrgsHealth;
}

const REFRESH_MS = 15_000;

export function useSystemHealth() {
  return useQuery({
    queryKey: ['superadmin-health'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/superadmin/health');
      if (error) throw new Error('failed to load system health');
      return data as unknown as SystemHealth;
    },
    refetchInterval: REFRESH_MS,
  });
}

export function useClientErrors() {
  return useQuery({
    queryKey: ['superadmin-client-errors'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/superadmin/client-errors');
      if (error) throw new Error('failed to load client errors');
      return data as ClientErrorOut[];
    },
    refetchInterval: REFRESH_MS,
  });
}
