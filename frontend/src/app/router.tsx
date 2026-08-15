import { createBrowserRouter } from 'react-router-dom';

import { AppShell } from '@/components/layout/app-shell';
import { ApiKeysPage } from '@/features/admin/api-keys/api-keys-page';
import { AuditPage } from '@/features/admin/audit/audit-page';
import { BotsPage } from '@/features/admin/bots/bots-page';
import { DashboardPage } from '@/features/admin/dashboard/dashboard-page';
import { FeedbackPage } from '@/features/admin/feedback/feedback-page';
import { HealthPage } from '@/features/admin/health/health-page';
import { ModelsPage } from '@/features/admin/models/models-page';
import { RolesPage } from '@/features/admin/roles/roles-page';
import { SettingsPage } from '@/features/admin/settings/settings-page';
import { UsersPage } from '@/features/admin/users/users-page';
import { AcceptInvitePage } from '@/features/auth/accept-invite-page';
import { LoginPage } from '@/features/auth/login-page';
import { ChatPage } from '@/features/chat/chat-page';
import { DocumentsPage } from '@/features/documents/documents-page';

import { LandingGate } from './landing-gate';
import { RequireAction } from './require-action';
import { RequireAuth } from './require-auth';
import { RequireRole } from './require-role';

export const router = createBrowserRouter([
  { path: '/', element: <LandingGate /> },
  { path: '/login', element: <LoginPage /> },
  { path: '/invite', element: <AcceptInvitePage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <AppShell />,
        children: [
          { path: '/chat', element: <ChatPage /> },
          { path: '/chat/:chatId', element: <ChatPage /> },
          { path: '/documents', element: <DocumentsPage /> },
          {
            element: <RequireRole role="admin" />,
            children: [
              { path: '/admin/dashboard', element: <DashboardPage /> },
              { path: '/admin/users', element: <UsersPage /> },
              { path: '/admin/feedback', element: <FeedbackPage /> },
            ],
          },
          {
            element: <RequireRole role="superadmin" />,
            children: [
              { path: '/admin/models', element: <ModelsPage /> },
              { path: '/admin/settings', element: <SettingsPage /> },
              { path: '/admin/api-keys', element: <ApiKeysPage /> },
              { path: '/admin/bots', element: <BotsPage /> },
              { path: '/admin/health', element: <HealthPage /> },
            ],
          },
          {
            element: <RequireAction action="roles.read" />,
            children: [{ path: '/admin/roles', element: <RolesPage /> }],
          },
          {
            element: <RequireAction action="audit.read" />,
            children: [{ path: '/admin/audit', element: <AuditPage /> }],
          },
        ],
      },
    ],
  },
]);
