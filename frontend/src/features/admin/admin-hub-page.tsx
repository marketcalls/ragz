import {
  Activity,
  Bot,
  Building2,
  Fingerprint,
  KeyRound,
  LayoutDashboard,
  Mail,
  MessageSquare,
  ScrollText,
  Settings,
  Settings2,
  ShieldCheck,
  Users,
  type LucideIcon,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { NavLink } from 'react-router-dom';

import { TopBar } from '@/components/layout/top-bar';
import { Input } from '@/components/ui/input';
import { useAuthorization } from '@/lib/use-authorization';
import { useClaims } from '@/lib/use-claims';

interface AdminSection {
  to: string;
  label: string;
  description: string;
  icon: LucideIcon;
  visible: boolean;
}

export function AdminHubPage() {
  const claims = useClaims();
  const { data: auth } = useAuthorization();
  const [query, setQuery] = useState('');

  const can = (action: string) => auth?.role === 'superadmin' || auth?.permissions.has(action) === true;
  const isSuperadmin = claims?.role === 'superadmin';

  const sections: AdminSection[] = [
    {
      to: '/admin/dashboard',
      label: 'Dashboard',
      description: 'Usage, cost, and adoption metrics',
      icon: LayoutDashboard,
      visible: can('analytics.view'),
    },
    {
      to: '/admin/users',
      label: 'Users',
      description: 'Manage members, roles, invites',
      icon: Users,
      visible: can('users.read'),
    },
    {
      to: '/admin/feedback',
      label: 'Feedback',
      description: 'Review flagged answers and reviewer notes',
      icon: MessageSquare,
      visible: can('feedback.review'),
    },
    {
      to: '/admin/roles',
      label: 'Roles',
      description: 'Define custom roles and permissions',
      icon: ShieldCheck,
      visible: can('roles.read'),
    },
    {
      to: '/admin/models',
      label: 'Models',
      description: 'Providers, models, catalog sync',
      icon: Settings2,
      visible: isSuperadmin,
    },
    {
      to: '/admin/organizations',
      label: 'Organizations',
      description: 'Create and manage organizations',
      icon: Building2,
      visible: isSuperadmin,
    },
    {
      to: '/admin/settings',
      label: 'Settings',
      description: 'Parsers, reranker, web search, generative UI',
      icon: Settings,
      visible: isSuperadmin,
    },
    {
      to: '/admin/api-keys',
      label: 'API Keys',
      description: 'Issue and revoke platform API keys',
      icon: KeyRound,
      visible: isSuperadmin,
    },
    {
      to: '/admin/bots',
      label: 'Bots',
      description: 'Telegram, Discord, Slack integrations',
      icon: Bot,
      visible: isSuperadmin,
    },
    {
      to: '/admin/email',
      label: 'Email',
      description: 'SMTP relay and notification templates',
      icon: Mail,
      visible: isSuperadmin,
    },
    {
      to: '/admin/sso',
      label: 'SSO',
      description: 'Single sign-on identity providers',
      icon: Fingerprint,
      visible: isSuperadmin,
    },
    {
      to: '/admin/audit',
      label: 'Audit',
      description: 'Append-only event log of admin actions',
      icon: ScrollText,
      visible: can('audit.read'),
    },
    {
      to: '/admin/health',
      label: 'Health',
      description: 'Service status and dependency checks',
      icon: Activity,
      visible: isSuperadmin,
    },
  ];

  const visibleSections = sections.filter((s) => s.visible);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return visibleSections;
    return visibleSections.filter(
      (s) => s.label.toLowerCase().includes(q) || s.description.toLowerCase().includes(q),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, auth, isSuperadmin]);

  return (
    <>
      <TopBar title="Admin" />
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-4xl">
          <Input
            type="search"
            placeholder="Search settings…"
            aria-label="Search settings"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="mb-4 max-w-xs"
          />
          {filtered.length === 0 ? (
            <p className="text-[13px] text-secondary">No matching admin sections.</p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {filtered.map((section) => {
                const Icon = section.icon;
                return (
                  <NavLink
                    key={section.to}
                    to={section.to}
                    className="flex items-start gap-3 rounded-lg border border-line bg-bg p-3 transition-colors duration-150 ease-out hover:border-accent hover:bg-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                  >
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-subtle text-ink">
                      <Icon className="h-4 w-4" aria-hidden />
                    </span>
                    <span className="flex min-w-0 flex-col">
                      <span className="text-[13px] font-medium text-ink">{section.label}</span>
                      <span className="text-[12px] text-secondary">{section.description}</span>
                    </span>
                  </NavLink>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
