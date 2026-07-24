import { ChevronDown, ChevronRight, Folder as FolderIcon } from 'lucide-react';
import { useState } from 'react';

import { cn } from '@/lib/cn';

import type { FolderNode } from './folder-queries';

function FolderTreeNode({
  node,
  depth,
  selectedId,
  onSelect,
}: {
  node: FolderNode;
  depth: number;
  selectedId: string | null;
  onSelect: (folderId: string | null) => void;
}) {
  const [open, setOpen] = useState(true);
  const hasChildren = node.children.length > 0;

  return (
    <div>
      <button
        type="button"
        onClick={() => onSelect(node.id)}
        className={cn(
          'flex w-full items-center gap-1 rounded-md px-2 py-1 text-left text-[13px]',
          selectedId === node.id ? 'bg-subtle text-ink' : 'text-secondary hover:bg-subtle',
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
      {hasChildren && open ? (
        <div>
          {node.children.map((child) => (
            <FolderTreeNode
              key={child.id}
              node={child}
              depth={depth + 1}
              selectedId={selectedId}
              onSelect={onSelect}
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
}: {
  tree: FolderNode[];
  selectedId: string | null;
  onSelect: (folderId: string | null) => void;
}) {
  return (
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
        />
      ))}
    </nav>
  );
}
