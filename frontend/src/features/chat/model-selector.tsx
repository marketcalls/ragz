import type { ModelPublic } from '@/api/types';
import { NativeSelect } from '@/components/ui/select';

export function ModelSelector({
  models,
  value,
  onChange,
}: {
  // GET /api/v1/models already returns only enabled models (ModelPublic —
  // no `enabled` field to re-filter on; see api/types.ts).
  models: ModelPublic[];
  value: string | null;
  onChange: (id: string) => void;
}) {
  if (models.length === 0) return <span className="text-[12px] text-muted">No models</span>;
  return (
    <NativeSelect
      aria-label="Model"
      className="h-7 w-auto min-w-[140px] text-[12px]"
      value={value ?? models[0]?.id}
      onChange={(e) => onChange(e.target.value)}
    >
      {models.map((m) => (
        <option key={m.id} value={m.id}>
          {m.display_name}
        </option>
      ))}
    </NativeSelect>
  );
}
