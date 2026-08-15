import { useId, useState, type FormEvent } from 'react';

import type { FormBlock as FormBlockT, FormField } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { cn } from '@/lib/cn';

// Phase 3 (interactive in-chat forms, 2026-08-15 design). Iron Rule 5: this
// is server-validated LLM output rendered as NATIVE controls only -- no
// model-supplied HTML/URLs/handlers. Submission is NOT a new endpoint or a
// stateful form engine: it composes a plain, bounded message string from the
// filled-in values and hands it to the same chat send path every other
// message goes through (see onSubmit -- wired all the way up from
// chat-page.tsx's existing `onSend`).

type FormValue = string | string[];
type FormValues = Record<string, FormValue>;

function initialValues(fields: FormField[]): FormValues {
  const values: FormValues = {};
  for (const field of fields) {
    values[field.name] = field.kind === 'multiselect' ? [] : '';
  }
  return values;
}

function isFilled(field: FormField, value: FormValue | undefined): boolean {
  if (field.kind === 'multiselect') return Array.isArray(value) && value.length > 0;
  return typeof value === 'string' && value.trim() !== '';
}

// "{label}: {value}" per field, newline-joined; multiselect values
// comma-join. A field left empty (optional, unfilled) is omitted entirely
// rather than emitted as a bare "Label: ".
function composeMessage(fields: FormField[], values: FormValues): string {
  const lines: string[] = [];
  for (const field of fields) {
    const value = values[field.name];
    if (field.kind === 'multiselect') {
      const selected = Array.isArray(value) ? value : [];
      if (selected.length === 0) continue;
      lines.push(`${field.label}: ${selected.join(', ')}`);
    } else {
      const text = typeof value === 'string' ? value.trim() : '';
      if (!text) continue;
      lines.push(`${field.label}: ${text}`);
    }
  }
  return lines.join('\n');
}

function FieldControl({
  field,
  id,
  value,
  onChange,
}: {
  field: FormField;
  id: string;
  value: FormValue;
  onChange: (next: FormValue) => void;
}) {
  switch (field.kind) {
    case 'text':
      return (
        <Input
          id={id}
          type="text"
          value={typeof value === 'string' ? value : ''}
          placeholder={field.placeholder ?? undefined}
          required={field.required}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case 'number':
      return (
        <Input
          id={id}
          type="number"
          value={typeof value === 'string' ? value : ''}
          placeholder={field.placeholder ?? undefined}
          required={field.required}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case 'select':
      return (
        <NativeSelect
          id={id}
          value={typeof value === 'string' ? value : ''}
          required={field.required}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">{field.placeholder ?? 'Select…'}</option>
          {(field.options ?? []).map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </NativeSelect>
      );
    case 'multiselect': {
      const selected = Array.isArray(value) ? value : [];
      return (
        <div className="flex flex-wrap gap-1.5" role="group" aria-labelledby={`${id}-label`}>
          {(field.options ?? []).map((option) => {
            const isSelected = selected.includes(option);
            return (
              <button
                key={option}
                type="button"
                aria-pressed={isSelected}
                onClick={() =>
                  onChange(
                    isSelected ? selected.filter((v) => v !== option) : [...selected, option],
                  )
                }
                className={cn(
                  'rounded-full border px-2.5 py-1 text-[12px] font-medium transition-colors',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
                  isSelected
                    ? 'border-accent bg-accent-soft text-accent-on-soft'
                    : 'border-line bg-subtle text-secondary hover:text-ink',
                )}
              >
                {option}
              </button>
            );
          })}
        </div>
      );
    }
    default:
      return null;
  }
}

export function FormBlockView({
  block,
  onSubmit,
}: {
  block: FormBlockT;
  // Absent in contexts with no send path (e.g. no chat/workspace yet) --
  // the form still renders but submit is a disabled no-op (defensive).
  onSubmit?: (message: string) => void;
}) {
  const idPrefix = useId();
  const [values, setValues] = useState<FormValues>(() => initialValues(block.fields));
  const [submitted, setSubmitted] = useState(false);

  const allRequiredFilled = block.fields
    .filter((f) => f.required)
    .every((f) => isFilled(f, values[f.name]));
  const canSubmit = allRequiredFilled && typeof onSubmit === 'function';

  const handleChange = (name: string, next: FormValue): void => {
    setValues((prev) => ({ ...prev, [name]: next }));
    setSubmitted(false);
  };

  const handleSubmit = (e: FormEvent<HTMLFormElement>): void => {
    e.preventDefault();
    if (!canSubmit || !onSubmit) return;
    onSubmit(composeMessage(block.fields, values));
    setSubmitted(true);
  };

  return (
    <form onSubmit={handleSubmit} className="rounded-lg border border-line bg-bg p-4">
      {block.title ? <h3 className="text-sm font-semibold text-ink">{block.title}</h3> : null}
      {block.description ? (
        <p className="mt-0.5 text-[12px] text-secondary">{block.description}</p>
      ) : null}
      {block.fields.length > 0 ? (
        <div className="mt-3 flex flex-col gap-3">
          {block.fields.map((field) => {
            const id = `${idPrefix}-${field.name}`;
            return (
              <div key={field.name}>
                {field.kind === 'multiselect' ? (
                  <span id={`${id}-label`} className="mb-1 block text-[12px] font-medium text-secondary">
                    {field.label}
                    {field.required ? ' *' : ''}
                  </span>
                ) : (
                  <Label htmlFor={id}>
                    {field.label}
                    {field.required ? ' *' : ''}
                  </Label>
                )}
                <FieldControl
                  field={field}
                  id={id}
                  value={values[field.name] ?? (field.kind === 'multiselect' ? [] : '')}
                  onChange={(next) => handleChange(field.name, next)}
                />
              </div>
            );
          })}
        </div>
      ) : null}
      <div className="mt-3 flex items-center gap-2">
        <Button type="submit" variant="primary" disabled={!canSubmit}>
          {block.submit_label ?? 'Submit'}
        </Button>
        {submitted ? <span className="text-[12px] text-secondary">Sent</span> : null}
      </div>
    </form>
  );
}
