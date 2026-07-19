import type { MetadataFieldOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';

// One filter control per workspace metadata field, keyed by field name:
//   - select -> exact-match dropdown ("" = All)
//   - date   -> range, encoded as "from..to" (either side may be empty)
//   - text   -> substring match, case-insensitive
// A field absent from `value` (or mapping to "" / "..") is not filtered on.
export type MetadataFilterValue = Record<string, string>;

export function matchesMetadataFilter(
  meta: Record<string, string> | null | undefined,
  fields: MetadataFieldOut[],
  filters: MetadataFilterValue,
): boolean {
  const actual = meta ?? {};
  for (const field of fields) {
    const raw = filters[field.name];
    if (!raw) continue;
    if (field.field_type === 'date') {
      const [from, to] = raw.split('..');
      const value = actual[field.name];
      if (!value) return false;
      if (from && value < from) return false;
      if (to && value > to) return false;
    } else if (field.field_type === 'select') {
      if (actual[field.name] !== raw) return false;
    } else {
      const value = actual[field.name];
      if (!value || !value.toLowerCase().includes(raw.toLowerCase())) return false;
    }
  }
  return true;
}

export function MetadataFilterBar({
  fields,
  value,
  onChange,
}: {
  fields: MetadataFieldOut[];
  value: MetadataFilterValue;
  onChange: (next: MetadataFilterValue) => void;
}) {
  if (fields.length === 0) return null;

  const setField = (name: string, raw: string): void => {
    const next = { ...value };
    if (raw === '' || raw === '..') {
      delete next[name];
    } else {
      next[name] = raw;
    }
    onChange(next);
  };

  const active = Object.keys(value).length > 0;

  return (
    <div className="flex flex-wrap items-end gap-2 rounded-md border border-line bg-raised p-2">
      {fields.map((field) => {
        const fieldId = `filter-${field.id}`;
        if (field.field_type === 'select') {
          return (
            <div key={field.id} className="space-y-1">
              <Label htmlFor={fieldId}>{field.label}</Label>
              <NativeSelect
                id={fieldId}
                className="w-36"
                value={value[field.name] ?? ''}
                onChange={(e) => setField(field.name, e.target.value)}
              >
                <option value="">All</option>
                {(field.options ?? []).map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </NativeSelect>
            </div>
          );
        }
        if (field.field_type === 'date') {
          const [from, to] = (value[field.name] ?? '..').split('..');
          return (
            <div key={field.id} className="flex items-end gap-1">
              <div className="space-y-1">
                <Label htmlFor={`${fieldId}-from`}>{field.label} from</Label>
                <Input
                  id={`${fieldId}-from`}
                  type="date"
                  className="w-36"
                  value={from}
                  onChange={(e) => setField(field.name, `${e.target.value}..${to}`)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor={`${fieldId}-to`}>{field.label} to</Label>
                <Input
                  id={`${fieldId}-to`}
                  type="date"
                  className="w-36"
                  value={to}
                  onChange={(e) => setField(field.name, `${from}..${e.target.value}`)}
                />
              </div>
            </div>
          );
        }
        return (
          <div key={field.id} className="space-y-1">
            <Label htmlFor={fieldId}>{field.label}</Label>
            <Input
              id={fieldId}
              className="w-36"
              placeholder="Contains…"
              value={value[field.name] ?? ''}
              onChange={(e) => setField(field.name, e.target.value)}
            />
          </div>
        );
      })}
      {active ? (
        <Button size="sm" onClick={() => onChange({})}>
          Clear filters
        </Button>
      ) : null}
    </div>
  );
}
