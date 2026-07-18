import { Loader2 } from 'lucide-react';

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-2 text-secondary">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      <span className="text-[13px]">{label}</span>
    </div>
  );
}
