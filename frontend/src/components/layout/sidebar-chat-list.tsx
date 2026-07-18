import { MessageSquarePlus, Pencil, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { NavLink, useNavigate, useParams } from 'react-router-dom';

import type { ChatOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { toast } from '@/components/ui/toaster';
import { cn } from '@/lib/cn';

import { useChats, useDeleteChat, useRenameChat } from '@/features/chat/queries';
import { useWorkspace } from '@/features/workspaces/workspace-context';

export function SidebarChatList() {
  const { workspaceId } = useWorkspace();
  const { data: chats } = useChats(workspaceId);
  const navigate = useNavigate();
  const { chatId: activeChatId } = useParams<{ chatId: string }>();
  const [filter, setFilter] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<ChatOut | null>(null);

  const renameChat = useRenameChat();
  const deleteChat = useDeleteChat();

  const filtered = (chats ?? []).filter((chat) =>
    (chat.title || 'Untitled chat').toLowerCase().includes(filter.trim().toLowerCase()),
  );

  const startRename = (chat: ChatOut): void => {
    setEditingId(chat.id);
    setEditValue(chat.title || '');
  };

  const cancelRename = (): void => {
    setEditingId(null);
    setEditValue('');
  };

  const commitRename = (chatId: string): void => {
    const title = editValue.trim();
    cancelRename();
    if (!title) return;
    renameChat.mutate({ chatId, title }, { onError: (err) => toast.error(err.message) });
  };

  const confirmDelete = (): void => {
    if (!deleteTarget) return;
    const chatId = deleteTarget.id;
    setDeleteTarget(null);
    deleteChat.mutate(chatId, {
      onError: (err) => toast.error(err.message),
      onSuccess: () => {
        if (activeChatId === chatId) navigate('/chat');
      },
    });
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between px-2 pb-1">
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted">Chats</span>
        <Button
          variant="ghost"
          size="icon"
          aria-label="New chat"
          onClick={() => navigate('/chat')}
        >
          <MessageSquarePlus className="h-4 w-4" aria-hidden />
        </Button>
      </div>
      <div className="px-2 pb-1">
        <Input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Search chats…"
          aria-label="Search chats"
        />
      </div>
      <nav aria-label="Chats" className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-1">
        {filtered.map((chat) => {
          const title = chat.title || 'Untitled chat';

          if (editingId === chat.id) {
            return (
              <div key={chat.id} className="px-1 py-0.5">
                <Input
                  autoFocus
                  aria-label="Rename chat"
                  value={editValue}
                  onChange={(e) => setEditValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitRename(chat.id);
                    if (e.key === 'Escape') cancelRename();
                  }}
                />
              </div>
            );
          }

          return (
            <div key={chat.id} className="group relative flex items-center">
              <NavLink
                to={`/chat/${chat.id}`}
                className={({ isActive }) =>
                  cn(
                    'block flex-1 truncate rounded-md px-2 py-1.5 pr-12 text-[13px] text-secondary hover:bg-subtle hover:text-ink',
                    isActive && 'bg-subtle text-ink',
                  )
                }
              >
                {title}
              </NavLink>
              <span className="absolute right-1 flex items-center gap-0.5 opacity-0 focus-within:opacity-100 group-hover:opacity-100">
                <button
                  type="button"
                  aria-label={`Rename ${title}`}
                  title="Rename"
                  onClick={() => startRename(chat)}
                  className="rounded-sm p-1 text-muted hover:bg-subtle hover:text-ink"
                >
                  <Pencil className="h-3.5 w-3.5" aria-hidden />
                </button>
                <button
                  type="button"
                  aria-label={`Delete ${title}`}
                  title="Delete"
                  onClick={() => setDeleteTarget(chat)}
                  className="rounded-sm p-1 text-muted hover:bg-subtle hover:text-ink"
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden />
                </button>
              </span>
            </div>
          );
        })}
        {filter.trim() && filtered.length === 0 ? (
          <p className="px-2 py-1.5 text-[13px] text-secondary">No chats match</p>
        ) : null}
      </nav>
      <Dialog open={deleteTarget !== null} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <DialogContent title="Delete chat">
          <p className="text-[13px] text-secondary">
            Delete chat &ldquo;{deleteTarget?.title || 'Untitled chat'}&rdquo;? This cannot be
            undone.
          </p>
          <DialogFooter>
            <Button onClick={() => setDeleteTarget(null)}>Cancel</Button>
            <Button variant="danger" onClick={confirmDelete}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
