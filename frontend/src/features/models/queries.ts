import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';

export function useModels(enabled = true) {
  return useQuery({
    queryKey: ['models'],
    enabled,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/models');
      if (error) throw new Error('failed to load models');
      return data;
    },
  });
}
