import { useState } from 'react';

import type { DocumentOut, MetadataFieldOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { toast } from '@/components/ui/toaster';

import { useSetDocumentMetadata } from './queries';

// One input per workspace metadata field (DOC-6/Task 11): text -> text
// input, date -> date input, select -> dropdown of the field's options.
// PUT /documents/{id}/metadata is a full replacement, so clearing a field's
// input (empty string) drops its key from the saved `values` map rather than
// sending an empty string through — an empty date would fail the backend's
// ISO-date validation, and an empty select value isn't one of its options.
export function MetadataDialog({
  doc,
  fields,
  workspaceId,
  open,
  onOpenChange,
}: {
  doc: DocumentOut;
  fields: MetadataFieldOut[];
  workspaceId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(doc.meta ?? {});
  const setMetadata = useSetDocumentMetadata(workspaceId);

  const setField = (name: string, raw: string): void => {
    setValues((prev) => {
      const next = { ...prev };
      if (raw === '') {
        delete next[name];
      } else {
        next[name] = raw;
      }
      return next;
    });
  };

  const save = (): void => {
    setMetadata.mutate(
      { documentId: doc.id, values },
      {
        onSuccess: () => onOpenChange(false),
        onError: (err) => toast.error(err.message),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={`Metadata — ${doc.filename}`}
        description="Values used to filter search and citations. Leave a field blank to clear it."
      >
        {fields.length === 0 ? (
          <p className="text-[13px] text-secondary">
            No metadata fields are configured for this workspace yet.
          </p>
        ) : (
          <div className="space-y-3">
            {fields.map((field) => (
              <div key={field.id} className="space-y-1">
                <Label htmlFor={`meta-${field.id}`}>{field.label}</Label>
                {field.field_type === 'select' ? (
                  <NativeSelect
                    id={`meta-${field.id}`}
                    value={values[field.name] ?? ''}
                    onChange={(e) => setField(field.name, e.target.value)}
                  >
                    <option value="">—</option>
                    {(field.options ?? []).map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </NativeSelect>
                ) : (
                  <Input
                    id={`meta-${field.id}`}
                    type={field.field_type === 'date' ? 'date' : 'text'}
                    value={values[field.name] ?? ''}
                    onChange={(e) => setField(field.name, e.target.value)}
                  />
                )}
              </div>
            ))}
          </div>
        )}
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" disabled={setMetadata.isPending} onClick={save}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
