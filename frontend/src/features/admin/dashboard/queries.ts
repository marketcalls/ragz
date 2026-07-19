import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';

export function useUsageSummary(days: number) {
  return useQuery({
    queryKey: ['admin-usage', days],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/usage/summary', {
        params: { query: { days } },
      });
      if (error) throw new Error('failed to load usage summary');
      return data;
    },
  });
}
