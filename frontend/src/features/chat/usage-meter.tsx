import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import { cn } from '@/lib/cn';

function compact(n: number): string {
  return Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(n);
}

export function UsageMeter() {
  const usage = useQuery({
    queryKey: ['usage-me'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/usage/me');
      if (error) throw new Error('failed to load usage');
      return data;
    },
    refetchInterval: 60_000, // matches the backend's 60s usage cache
  });

  if (!usage.data) return null;
  const { used_tokens, allocated_tokens, resets_at, warning } = usage.data;
  const reset = new Date(resets_at).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
  });
  return (
    <span
      className={cn('text-[12px] tabular-nums', warning ? 'text-danger' : 'text-muted')}
      title={
        allocated_tokens == null
          ? 'Tokens used this period (no allocation set)'
          : `Resets ${reset}`
      }
    >
      {allocated_tokens == null
        ? `${compact(used_tokens)} tokens`
        : `${compact(used_tokens)} / ${compact(allocated_tokens)} tokens · resets ${reset}`}
    </span>
  );
}
