import { Upload } from 'lucide-react';
import { useRef, useState, type DragEvent } from 'react';

import { cn } from '@/lib/cn';

const ACCEPT = '.pdf,.docx,.xlsx,.pptx,.csv,.txt,.md';

export function Dropzone({
  onFiles,
  disabled,
}: {
  onFiles: (files: File[]) => void;
  disabled: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const onDrop = (e: DragEvent): void => {
    e.preventDefault();
    setDragOver(false);
    if (disabled) return;
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) onFiles(files);
  };

  return (
    <>
      <button
        type="button"
        disabled={disabled}
        aria-label="Upload documents"
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={cn(
          'flex w-full flex-col items-center gap-1.5 rounded-lg border border-dashed border-line-strong bg-raised px-4 py-8 text-secondary hover:border-accent',
          dragOver && 'border-accent bg-accent-soft',
          disabled && 'opacity-50',
        )}
      >
        <Upload className="h-5 w-5 text-muted" aria-hidden />
        <span className="text-[13px] font-medium text-ink">
          Drop files here or click to upload
        </span>
        <span className="text-[12px] text-muted">PDF, DOCX, XLSX, PPTX, CSV, TXT, MD</span>
      </button>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length > 0) onFiles(files);
          e.target.value = '';
        }}
      />
    </>
  );
}
