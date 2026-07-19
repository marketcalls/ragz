import { createBrowserRouter, Navigate } from 'react-router-dom';

import { AppShell } from '@/components/layout/app-shell';
import { AuditPage } from '@/features/admin/audit/audit-page';
import { DashboardPage } from '@/features/admin/dashboard/dashboard-page';
import { ModelsPage } from '@/features/admin/models/models-page';
import { UsersPage } from '@/features/admin/users/users-page';
import { AcceptInvitePage } from '@/features/auth/accept-invite-page';
import { LoginPage } from '@/features/auth/login-page';
import { ChatPage } from '@/features/chat/chat-page';
import { DocumentsPage } from '@/features/documents/documents-page';

import { RequireAuth } from './require-auth';
import { RequireRole } from './require-role';

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  { path: '/invite', element: <AcceptInvitePage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <AppShell />,
        children: [
          { path: '/', element: <Navigate to="/chat" replace /> },
          { path: '/chat', element: <ChatPage /> },
          { path: '/chat/:chatId', element: <ChatPage /> },
          { path: '/documents', element: <DocumentsPage /> },
          {
            element: <RequireRole role="admin" />,
            children: [
              { path: '/admin/dashboard', element: <DashboardPage /> },
              { path: '/admin/users', element: <UsersPage /> },
            ],
          },
          {
            element: <RequireRole role="superadmin" />,
            children: [
              { path: '/admin/models', element: <ModelsPage /> },
              { path: '/admin/audit', element: <AuditPage /> },
            ],
          },
        ],
      },
    ],
  },
]);
