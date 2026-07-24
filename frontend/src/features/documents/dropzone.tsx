import { Upload } from 'lucide-react';
import { useRef, useState, type DragEvent } from 'react';

import { cn } from '@/lib/cn';

const ACCEPT = '.pdf,.docx,.xlsx,.pptx,.csv,.txt,.md';

export interface DroppedFile {
  file: File;
  relativePath: string; // e.g. "Legal/Contracts/2024/report.pdf"; "" segment before the filename means root
}

function readEntryFile(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

function readDirectoryEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  // webkitGetAsEntry's directory reader can return entries in batches --
  // must call readEntries repeatedly until it yields an empty array.
  return new Promise((resolve, reject) => {
    const all: FileSystemEntry[] = [];
    const readBatch = (): void => {
      reader.readEntries((batch) => {
        if (batch.length === 0) {
          resolve(all);
        } else {
          all.push(...batch);
          readBatch();
        }
      }, reject);
    };
    readBatch();
  });
}

async function walkEntry(entry: FileSystemEntry, prefix: string, out: DroppedFile[]): Promise<void> {
  if (entry.isFile) {
    const file = await readEntryFile(entry as FileSystemFileEntry);
    out.push({ file, relativePath: prefix ? `${prefix}/${entry.name}` : entry.name });
  } else if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    const children = await readDirectoryEntries(reader);
    const nextPrefix = prefix ? `${prefix}/${entry.name}` : entry.name;
    for (const child of children) await walkEntry(child, nextPrefix, out);
  }
}

export async function walkDroppedItems(items: DataTransferItemList): Promise<DroppedFile[]> {
  const out: DroppedFile[] = [];
  const entries: FileSystemEntry[] = [];
  for (let i = 0; i < items.length; i++) {
    const entry = items[i]?.webkitGetAsEntry?.();
    if (entry) entries.push(entry);
  }
  for (const entry of entries) await walkEntry(entry, '', out);
  return out;
}

export function Dropzone({
  onFiles,
  onFolderFiles,
  disabled,
}: {
  onFiles: (files: File[]) => void;
  onFolderFiles: (files: DroppedFile[]) => void;
  disabled: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const onDrop = (e: DragEvent): void => {
    e.preventDefault();
    setDragOver(false);
    if (disabled) return;
    const hasDirectoryEntries = Array.from(e.dataTransfer.items).some(
      (item) => item.webkitGetAsEntry?.()?.isDirectory,
    );
    if (hasDirectoryEntries) {
      void walkDroppedItems(e.dataTransfer.items).then((files) => {
        if (files.length > 0) onFolderFiles(files);
      });
      return;
    }
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
