import {
  ChevronDown,
  ChevronRight,
  Folder as FolderIcon,
  FolderPlus,
  Pencil,
  Plus,
  Trash2,
} from 'lucide-react';
import { useState } from 'react';

import { cn } from '@/lib/cn';

import type { FolderNode } from './folder-queries';

function FolderTreeNode({
  node,
  depth,
  selectedId,
  onSelect,
  onNewChild,
  onRename,
  onDelete,
}: {
  node: FolderNode;
  depth: number;
  selectedId: string | null;
  onSelect: (folderId: string | null) => void;
  onNewChild: (parentFolderId: string | null) => void;
  onRename: (folder: FolderNode) => void;
  onDelete: (folder: FolderNode) => void;
}) {
  const [open, setOpen] = useState(true);
  const hasChildren = node.children.length > 0;

  return (
    <div>
      <div
        className={cn(
          'group flex items-center rounded-md',
          selectedId === node.id ? 'bg-subtle' : 'hover:bg-subtle',
        )}
      >
        <button
          type="button"
          onClick={() => onSelect(node.id)}
          className={cn(
            'flex flex-1 items-center gap-1 px-2 py-1 text-left text-[13px]',
            selectedId === node.id ? 'text-ink' : 'text-secondary',
          )}
          style={{ paddingLeft: `${depth * 14 + 8}px` }}
        >
          {hasChildren ? (
            <span
              role="button"
              aria-label={open ? `Collapse ${node.name}` : `Expand ${node.name}`}
              onClick={(e) => {
                e.stopPropagation();
                setOpen((o) => !o);
              }}
            >
              {open ? (
                <ChevronDown className="h-3.5 w-3.5" aria-hidden />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" aria-hidden />
              )}
            </span>
          ) : (
            <span className="w-3.5" />
          )}
          <FolderIcon className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />
          <span className="truncate">{node.name}</span>
        </button>
        <div className="hidden gap-0.5 pr-1 group-hover:flex">
          <button
            type="button"
            aria-label={`New subfolder in ${node.name}`}
            onClick={(e) => {
              e.stopPropagation();
              onNewChild(node.id);
            }}
            className="rounded p-1 text-muted hover:bg-line hover:text-ink"
          >
            <Plus className="h-3 w-3" aria-hidden />
          </button>
          <button
            type="button"
            aria-label={`Rename ${node.name}`}
            onClick={(e) => {
              e.stopPropagation();
              onRename(node);
            }}
            className="rounded p-1 text-muted hover:bg-line hover:text-ink"
          >
            <Pencil className="h-3 w-3" aria-hidden />
          </button>
          <button
            type="button"
            aria-label={`Delete ${node.name}`}
            onClick={(e) => {
              e.stopPropagation();
              onDelete(node);
            }}
            className="rounded p-1 text-muted hover:bg-line hover:text-danger"
          >
            <Trash2 className="h-3 w-3" aria-hidden />
          </button>
        </div>
      </div>
      {hasChildren && open ? (
        <div>
          {node.children.map((child) => (
            <FolderTreeNode
              key={child.id}
              node={child}
              depth={depth + 1}
              selectedId={selectedId}
              onSelect={onSelect}
              onNewChild={onNewChild}
              onRename={onRename}
              onDelete={onDelete}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function FolderTree({
  tree,
  selectedId,
  onSelect,
  onNewChild,
  onRename,
  onDelete,
}: {
  tree: FolderNode[];
  selectedId: string | null;
  onSelect: (folderId: string | null) => void;
  onNewChild: (parentFolderId: string | null) => void;
  onRename: (folder: FolderNode) => void;
  onDelete: (folder: FolderNode) => void;
}) {
  return (
    <div className="space-y-1">
      <button
        type="button"
        onClick={() => onNewChild(null)}
        className="flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-[12px] text-secondary hover:bg-subtle"
      >
        <FolderPlus className="h-3.5 w-3.5" aria-hidden /> New folder
      </button>
      <nav aria-label="Folders" className="space-y-0.5">
        <button
          type="button"
          onClick={() => onSelect(null)}
          className={cn(
            'flex w-full items-center gap-1 rounded-md px-2 py-1 text-left text-[13px]',
            selectedId === null ? 'bg-subtle text-ink' : 'text-secondary hover:bg-subtle',
          )}
        >
          <FolderIcon className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />
          <span>All documents</span>
        </button>
        {tree.map((node) => (
          <FolderTreeNode
            key={node.id}
            node={node}
            depth={0}
            selectedId={selectedId}
            onSelect={onSelect}
            onNewChild={onNewChild}
            onRename={onRename}
            onDelete={onDelete}
          />
        ))}
      </nav>
    </div>
  );
}
