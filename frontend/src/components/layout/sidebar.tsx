import {
  Activity,
  Bot,
  FileText,
  KeyRound,
  LayoutDashboard,
  Mail,
  MessageSquare,
  ScrollText,
  Settings,
  Settings2,
  ShieldCheck,
  Users,
} from 'lucide-react';
import { NavLink } from 'react-router-dom';

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
  const { data: auth } = useAuthorization();
  const can = (action: string) => auth?.role === 'superadmin' || auth?.permissions.has(action) === true;
  const isSuperadmin = claims?.role === 'superadmin';
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-sidebar">
      <div className="flex items-center gap-2 px-3 pb-1 pt-3">
        <span aria-hidden className="h-4 w-4 rounded-sm bg-accent" />
        <span className="text-[14px] font-semibold tracking-[-0.01em] text-ink">Ragz</span>
      </div>
      <div className="px-2 py-2">
        <WorkspaceSwitcher />
      </div>
      <SidebarChatList />
      <nav aria-label="Sections" className="space-y-0.5 border-t border-line-faint px-1 py-2">
        <SideLink to="/documents" label="Documents" icon={<FileText className="h-4 w-4" aria-hidden />} />
        {can('analytics.view') ? (
          <SideLink
            to="/admin/dashboard"
            label="Dashboard"
            icon={<LayoutDashboard className="h-4 w-4" aria-hidden />}
          />
        ) : null}
        {can('users.read') ? (
          <SideLink to="/admin/users" label="Users" icon={<Users className="h-4 w-4" aria-hidden />} />
        ) : null}
        {can('feedback.review') ? (
          <SideLink
            to="/admin/feedback"
            label="Feedback"
            icon={<MessageSquare className="h-4 w-4" aria-hidden />}
          />
        ) : null}
        {can('roles.read') ? (
          <SideLink to="/admin/roles" label="Roles" icon={<ShieldCheck className="h-4 w-4" aria-hidden />} />
        ) : null}
        {isSuperadmin ? (
          <SideLink to="/admin/models" label="Models" icon={<Settings2 className="h-4 w-4" aria-hidden />} />
        ) : null}
        {isSuperadmin ? (
          <SideLink to="/admin/settings" label="Settings" icon={<Settings className="h-4 w-4" aria-hidden />} />
        ) : null}
        {isSuperadmin ? (
          <SideLink
            to="/admin/api-keys"
            label="API Keys"
            icon={<KeyRound className="h-4 w-4" aria-hidden />}
          />
        ) : null}
        {isSuperadmin ? (
          <SideLink to="/admin/bots" label="Bots" icon={<Bot className="h-4 w-4" aria-hidden />} />
        ) : null}
        {isSuperadmin ? (
          <SideLink to="/admin/email" label="Email" icon={<Mail className="h-4 w-4" aria-hidden />} />
        ) : null}
        {can('audit.read') ? (
          <SideLink to="/admin/audit" label="Audit" icon={<ScrollText className="h-4 w-4" aria-hidden />} />
        ) : null}
        {isSuperadmin ? (
          <SideLink to="/admin/health" label="Health" icon={<Activity className="h-4 w-4" aria-hidden />} />
        ) : null}
      </nav>
      <UserFooter />
    </aside>
  );
}
