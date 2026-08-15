import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';

// Same queryKey as features/chat/usage-meter.tsx's inline query -- both read
// GET /api/v1/usage/me and share one cache entry/refetch cadence rather than
// each polling the (60s-cached) backend independently.
export function useUsageMe() {
  return useQuery({
    queryKey: ['usage-me'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/usage/me');
      if (error) throw new Error('failed to load usage');
      return data;
    },
    refetchInterval: 60_000, // matches the backend's 60s usage cache
  });
}

// Per-day token totals for the current user over the last `days` days
// (zero-filled, ascending). Powers the "Usage over time" chart.
export function useUsageDaily(days: number) {
  return useQuery({
    queryKey: ['usage-daily', days],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/usage/me/daily', {
        params: { query: { days } },
      });
      if (error) throw new Error('failed to load daily usage');
      return data;
    },
  });
}
