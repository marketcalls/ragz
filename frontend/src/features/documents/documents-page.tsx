import { useState } from 'react';

import { TopBar } from '@/components/layout/top-bar';
import { Spinner } from '@/components/ui/spinner';
import { Table, TBody, TH, THead, TR } from '@/components/ui/table';
import { toast } from '@/components/ui/toaster';

import { useWorkspace } from '@/features/workspaces/workspace-context';

import { DocumentRow } from './document-row';
import { Dropzone } from './dropzone';
import { useDeleteDocument, useDocuments, usePinDocument } from './queries';
import { uploadDocuments } from './upload';

interface UploadItem {
  key: string;
  names: string;
  pct: number;
}

export function DocumentsPage() {
  const { workspaceId } = useWorkspace();
  const documents = useDocuments(workspaceId);
  const deleteDocument = useDeleteDocument(workspaceId);
  const pinDocument = usePinDocument(workspaceId);
  const [uploads, setUploads] = useState<UploadItem[]>([]);

  const onFiles = (files: File[]): void => {
    if (!workspaceId) return;
    const key = crypto.randomUUID();
    const names = files.map((f) => f.name).join(', ');
    setUploads((prev) => [...prev, { key, names, pct: 0 }]);
    uploadDocuments(workspaceId, files, (pct) =>
      setUploads((prev) => prev.map((u) => (u.key === key ? { ...u, pct } : u))),
    )
      .then((failures) => {
        for (const failure of failures) toast.error(`${failure.file.name}: ${failure.message}`);
        void documents.refetch();
      })
      .catch((err: Error) => toast.error(err.message))
      .finally(() => setUploads((prev) => prev.filter((u) => u.key !== key)));
  };

  return (
    <>
      <TopBar title="Documents" />
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-4xl space-y-4">
          <Dropzone onFiles={onFiles} disabled={!workspaceId} />
          {uploads.map((u) => (
            <div key={u.key} className="rounded-md border border-line bg-raised px-3 py-2">
              <div className="mb-1 flex justify-between text-[12px]">
                <span className="truncate text-secondary">Uploading {u.names}</span>
                <span className="tabular-nums text-muted">{u.pct}%</span>
              </div>
              <div className="h-1 overflow-hidden rounded-full bg-subtle">
                <div className="h-full bg-accent transition-all" style={{ width: `${u.pct}%` }} />
              </div>
            </div>
          ))}
          {documents.isPending && workspaceId ? <Spinner label="Loading documents…" /> : null}
          {documents.data && documents.data.length > 0 ? (
            <Table>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Size</TH>
                  <TH>Pages</TH>
                  <TH>Status</TH>
                  <TH>Uploaded</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {documents.data.map((doc) => (
                  <DocumentRow
                    key={doc.id}
                    doc={doc}
                    deleting={deleteDocument.isPending}
                    onDelete={() =>
                      deleteDocument.mutate(doc.id, { onError: (err) => toast.error(err.message) })
                    }
                    pinning={pinDocument.isPending}
                    onTogglePin={() =>
                      pinDocument.mutate(
                        { documentId: doc.id, pinned: !doc.pinned },
                        { onError: (err) => toast.error(err.message) },
                      )
                    }
                  />
                ))}
              </TBody>
            </Table>
          ) : null}
          {documents.data?.length === 0 && uploads.length === 0 ? (
            <p className="pt-4 text-center text-[13px] text-secondary">
              No documents yet — upload some to make them searchable.
            </p>
          ) : null}
        </div>
      </div>
    </>
  );
}
