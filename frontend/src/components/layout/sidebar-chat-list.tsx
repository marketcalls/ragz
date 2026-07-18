import { MessageSquarePlus } from 'lucide-react';
import { NavLink, useNavigate } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/cn';

import { useChats } from '@/features/chat/queries';
import { useWorkspace } from '@/features/workspaces/workspace-context';

export function SidebarChatList() {
  const { workspaceId } = useWorkspace();
  const { data: chats } = useChats(workspaceId);
  const navigate = useNavigate();

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
      <nav aria-label="Chats" className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-1">
        {(chats ?? []).map((chat) => (
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
      </nav>
    </div>
  );
}
