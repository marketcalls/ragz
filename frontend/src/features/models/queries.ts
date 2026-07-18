import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';

export function useModels() {
  return useQuery({
    queryKey: ['models'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/models');
      if (error) throw new Error('failed to load models');
      return data;
    },
  });
}
