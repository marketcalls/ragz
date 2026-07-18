import { createBrowserRouter, Navigate } from 'react-router-dom';

import { AppShell } from '@/components/layout/app-shell';
import { AcceptInvitePage } from '@/features/auth/accept-invite-page';
import { LoginPage } from '@/features/auth/login-page';
import { ChatPage } from '@/features/chat/chat-page';
import { DocumentsPage } from '@/features/documents/documents-page';

import { RequireAuth } from './require-auth';
import { RequireRole } from './require-role';

// Placeholder pages are replaced as their tasks land (Tasks 13, 14).
function ComingSoon({ name }: { name: string }) {
  return <p className="p-6 text-secondary">{name} — under construction</p>;
}

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
            children: [{ path: '/admin/users', element: <ComingSoon name="Users" /> }],
          },
          {
            element: <RequireRole role="superadmin" />,
            children: [{ path: '/admin/models', element: <ComingSoon name="Models" /> }],
          },
        ],
      },
    ],
  },
]);
