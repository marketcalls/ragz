import { MessageSquarePlus } from 'lucide-react';
import { useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/cn';

import { useChats } from '@/features/chat/queries';
import { useWorkspace } from '@/features/workspaces/workspace-context';

export function SidebarChatList() {
  const { workspaceId } = useWorkspace();
  const { data: chats } = useChats(workspaceId);
  const navigate = useNavigate();
  const [filter, setFilter] = useState('');

  const filtered = (chats ?? []).filter((chat) =>
    (chat.title || 'Untitled chat').toLowerCase().includes(filter.trim().toLowerCase()),
  );

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
        {filtered.map((chat) => (
          <NavLink
            key={chat.id}
            to={`/chat/${chat.id}`}
            className={({ isActive }) =>
              cn(
                'block truncate rounded-md px-2 py-1.5 text-[13px] text-secondary hover:bg-subtle hover:text-ink',
                isActive && 'bg-subtle text-ink',
              )
            }
          >
            {chat.title || 'Untitled chat'}
          </NavLink>
        ))}
        {filter.trim() && filtered.length === 0 ? (
          <p className="px-2 py-1.5 text-[13px] text-secondary">No chats match</p>
        ) : null}
      </nav>
    </div>
  );
}
