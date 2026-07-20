import { useState } from 'react';

export function AttachmentUpload({
  onUpload,
  onUploaded,
}: {
  onUpload: (file: File) => Promise<{ id: string }>;
  onUploaded: (attachmentId: string) => void;
}) {
  const [pending, setPending] = useState(false);

  const handleChange = async (e: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
    const file = e.target.files?.[0];
    if (!file) return;
    setPending(true);
    try {
      const result = await onUpload(file);
      onUploaded(result.id);
    } finally {
      setPending(false);
      e.target.value = '';
    }
  };

  return (
    <label className="flex items-center gap-1 text-[12px] text-muted hover:text-ink">
      <input
        type="file"
        aria-label="Attach a file"
        className="sr-only"
        disabled={pending}
        onChange={(e) => void handleChange(e)}
      />
      {pending ? 'Uploading…' : 'Attach'}
    </label>
  );
}
