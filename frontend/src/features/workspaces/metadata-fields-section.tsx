import { Trash2 } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import type { MetadataFieldOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { toast } from '@/components/ui/toaster';

import {
  useCreateMetadataField,
  useDeleteMetadataField,
  useMetadataFields,
} from '../documents/queries';

const FIELD_TYPES = ['text', 'date', 'select'] as const;

// H-C8 mount point: admin-only management of the per-workspace metadata
// schema (DOC-6) — the fields document uploaders fill in on MetadataDialog
// and members filter on via MetadataFilterBar. Lives inside
// WorkspaceSettingsDialog, sibling to (not nested in) its retrieval-settings
// <form>, since each add/delete here is its own immediate mutation — there's
// no separate "Save" step, matching GroupsDialog's pattern.
export function MetadataFieldsSection({ workspaceId }: { workspaceId: string }) {
  const fields = useMetadataFields(workspaceId);
  const createField = useCreateMetadataField(workspaceId);
  const deleteField = useDeleteMetadataField(workspaceId);
  const [name, setName] = useState('');
  const [label, setLabel] = useState('');
  const [fieldType, setFieldType] = useState<(typeof FIELD_TYPES)[number]>('text');
  const [options, setOptions] = useState('');
  const [removing, setRemoving] = useState<MetadataFieldOut | null>(null);

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault();
    if (!name.trim() || !label.trim()) return;
    const parsedOptions =
      fieldType === 'select'
        ? options
            .split(',')
            .map((o) => o.trim())
            .filter(Boolean)
        : null;
    createField.mutate(
      { name: name.trim(), label: label.trim(), field_type: fieldType, options: parsedOptions },
      {
        onSuccess: () => {
          setName('');
          setLabel('');
          setOptions('');
          setFieldType('text');
        },
        onError: (err) => toast.error(err.message),
      },
    );
  };

  return (
    <div className="space-y-3">
      <h3 className="text-[13px] font-semibold text-ink">Metadata fields</h3>
      <p className="text-[12px] text-secondary">
        Fields members fill in per document — used to filter search and citations.
      </p>
      <form onSubmit={onSubmit} className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className="space-y-1">
          <Label htmlFor="mf-name">Name</Label>
          <Input
            id="mf-name"
            placeholder="doc_owner"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="mf-label">Label</Label>
          <Input
            id="mf-label"
            placeholder="Document Owner"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="mf-type">Type</Label>
          <NativeSelect
            id="mf-type"
            value={fieldType}
            onChange={(e) => setFieldType(e.target.value as (typeof FIELD_TYPES)[number])}
          >
            {FIELD_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </NativeSelect>
        </div>
        {fieldType === 'select' ? (
          <div className="space-y-1">
            <Label htmlFor="mf-options">Options (comma-separated)</Label>
            <Input
              id="mf-options"
              placeholder="policy, manual"
              value={options}
              onChange={(e) => setOptions(e.target.value)}
            />
          </div>
        ) : null}
        <div className="col-span-2 sm:col-span-4">
          <Button type="submit" size="sm" disabled={createField.isPending}>
            Add field
          </Button>
        </div>
      </form>
      {fields.isPending ? <Spinner label="Loading metadata fields…" /> : null}
      <ul className="space-y-1">
        {(fields.data ?? []).map((f) => (
          <li key={f.id} className="flex items-center justify-between text-[13px]">
            <span>
              {f.label}{' '}
              <span className="text-muted">
                ({f.name} · {f.field_type}
                {f.options ? `: ${f.options.join(', ')}` : ''})
              </span>
            </span>
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Delete metadata field ${f.label}`}
              onClick={() => setRemoving(f)}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </li>
        ))}
        {fields.data?.length === 0 ? (
          <li className="text-[13px] text-muted">No metadata fields yet.</li>
        ) : null}
      </ul>
      <Dialog open={removing !== null} onOpenChange={(o) => !o && setRemoving(null)}>
        <DialogContent
          title="Delete metadata field"
          description={`"${removing?.label ?? ''}" will be removed from the schema. Existing document values keep their data but become unfilterable.`}
        >
          <DialogFooter>
            <Button onClick={() => setRemoving(null)}>Cancel</Button>
            <Button
              variant="danger"
              disabled={deleteField.isPending}
              onClick={() => {
                if (removing) {
                  deleteField.mutate(removing.id, {
                    onError: (err) => toast.error(err.message),
                  });
                }
                setRemoving(null);
              }}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
