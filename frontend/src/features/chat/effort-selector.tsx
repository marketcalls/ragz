import { NativeSelect } from '@/components/ui/select';

export type ReasoningEffort = 'off' | 'low' | 'medium' | 'high';

const OPTIONS: { value: ReasoningEffort; label: string }[] = [
  { value: 'off', label: 'Off' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
];

export function EffortSelector({
  value,
  onChange,
}: {
  value: ReasoningEffort;
  onChange: (value: ReasoningEffort) => void;
}) {
  return (
    <NativeSelect
      aria-label="Reasoning effort"
      className="h-7 w-auto min-w-[90px] text-[12px]"
      value={value}
      onChange={(e) => onChange(e.target.value as ReasoningEffort)}
    >
      {OPTIONS.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </NativeSelect>
  );
}
