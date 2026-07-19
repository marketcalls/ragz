import { FileText, LayoutDashboard, ScrollText, Settings2, Users } from 'lucide-react';
import { NavLink } from 'react-router-dom';

import { cn } from '@/lib/cn';
import { useClaims } from '@/lib/use-claims';

import { SidebarChatList } from './sidebar-chat-list';
import { UserFooter } from './user-footer';
import { WorkspaceSwitcher } from './workspace-switcher';

function SideLink({ to, label, icon }: { to: string; label: string; icon: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          'flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] text-secondary hover:bg-subtle hover:text-ink',
          isActive && 'bg-subtle text-ink',
        )
      }
    >
      {icon}
      {label}
    </NavLink>
  );
}

export function Sidebar() {
  const claims = useClaims();
  const isAdmin = claims?.role === 'admin' || claims?.role === 'superadmin';
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-sidebar">
      <div className="flex items-center gap-2 px-3 pb-1 pt-3">
        <span aria-hidden className="h-4 w-4 rounded-sm bg-accent" />
        <span className="text-[14px] font-semibold tracking-[-0.01em] text-ink">RagHub</span>
      </div>
      <div className="px-2 py-2">
        <WorkspaceSwitcher />
      </div>
      <SidebarChatList />
      <nav aria-label="Sections" className="space-y-0.5 border-t border-line-faint px-1 py-2">
        <SideLink to="/documents" label="Documents" icon={<FileText className="h-4 w-4" aria-hidden />} />
        {isAdmin ? (
          <SideLink
            to="/admin/dashboard"
            label="Dashboard"
            icon={<LayoutDashboard className="h-4 w-4" aria-hidden />}
          />
        ) : null}
        {isAdmin ? (
          <SideLink to="/admin/users" label="Users" icon={<Users className="h-4 w-4" aria-hidden />} />
        ) : null}
        {claims?.role === 'superadmin' ? (
          <SideLink to="/admin/models" label="Models" icon={<Settings2 className="h-4 w-4" aria-hidden />} />
        ) : null}
        {claims?.role === 'superadmin' ? (
          <SideLink to="/admin/audit" label="Audit" icon={<ScrollText className="h-4 w-4" aria-hidden />} />
        ) : null}
      </nav>
      <UserFooter />
    </aside>
  );
}
