import { BarChart3, FileText, Gauge, ShieldCheck } from 'lucide-react';
import { NavLink } from 'react-router-dom';

import { Logo } from '@/components/logo';
import { cn } from '@/lib/cn';
import { useAuthorization } from '@/lib/use-authorization';
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
          'flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] text-secondary transition-colors duration-150 ease-out hover:bg-subtle hover:text-ink',
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
  const { data: auth } = useAuthorization();
  const can = (action: string) => auth?.role === 'superadmin' || auth?.permissions.has(action) === true;
  const isSuperadmin = claims?.role === 'superadmin';
  const canSeeAdmin =
    isSuperadmin ||
    can('analytics.view') ||
    can('users.read') ||
    can('feedback.review') ||
    can('roles.read') ||
    can('audit.read');
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-sidebar">
      <div className="flex items-center gap-2 px-3 pb-1 pt-3">
        <Logo className="h-4 w-4" />
        <span className="text-[14px] font-semibold tracking-[-0.01em] text-ink">Ragz</span>
      </div>
      <div className="px-2 py-2">
        <WorkspaceSwitcher />
      </div>
      <SidebarChatList />
      <nav aria-label="Sections" className="space-y-0.5 border-t border-line-faint px-1 py-2">
        <SideLink to="/documents" label="Documents" icon={<FileText className="h-4 w-4" aria-hidden />} />
        <SideLink to="/usage" label="My Usage" icon={<Gauge className="h-4 w-4" aria-hidden />} />
        <SideLink to="/reports" label="Reports" icon={<BarChart3 className="h-4 w-4" aria-hidden />} />
        {canSeeAdmin ? (
          <SideLink to="/admin" label="Admin" icon={<ShieldCheck className="h-4 w-4" aria-hidden />} />
        ) : null}
      </nav>
      <UserFooter />
    </aside>
  );
}
