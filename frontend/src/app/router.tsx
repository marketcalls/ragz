import { createBrowserRouter } from 'react-router-dom';

import { AppShell } from '@/components/layout/app-shell';

import { LandingGate } from './landing-gate';
import { RequireAction } from './require-action';
import { RequireAuth } from './require-auth';
import { RequireRole } from './require-role';

/**
 * Route components are loaded on demand (Phase 3 item 4). Every page used to be
 * a static import here, so one chunk carried the whole app -- 1.56 MB, 433 kB
 * gzipped, past Vite's own 500 kB warning. A user signing in downloaded the
 * superadmin health dashboard, the audit log and all of recharts before the
 * login form could paint, and every deploy invalidated that single chunk in
 * full.
 *
 * Only the pages are lazy. The wrappers below (LandingGate, RequireAuth,
 * RequireRole, RequireAction, AppShell) stay eager on purpose: they decide
 * WHICH route renders, so deferring them would only add a round-trip before
 * the redirect they exist to perform.
 *
 * The `import()` specifiers must stay string literals -- that is what lets Vite
 * see the split points at build time. Do not refactor them into a helper that
 * takes the path as a variable; it silently collapses the chunks back into one.
 */
export const router = createBrowserRouter([
  { path: '/', element: <LandingGate /> },
  {
    path: '/login',
    lazy: async () => ({ Component: (await import('@/features/auth/login-page')).LoginPage }),
  },
  {
    path: '/invite',
    lazy: async () => ({
      Component: (await import('@/features/auth/accept-invite-page')).AcceptInvitePage,
    }),
  },
  {
    path: '/forgot-password',
    lazy: async () => ({
      Component: (await import('@/features/auth/forgot-password-page')).ForgotPasswordPage,
    }),
  },
  {
    path: '/reset-password',
    lazy: async () => ({
      Component: (await import('@/features/auth/reset-password-page')).ResetPasswordPage,
    }),
  },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <AppShell />,
        children: [
          {
            path: '/chat',
            lazy: async () => ({
              Component: (await import('@/features/chat/chat-page')).ChatPage,
            }),
          },
          {
            path: '/chat/:chatId',
            lazy: async () => ({
              Component: (await import('@/features/chat/chat-page')).ChatPage,
            }),
          },
          {
            path: '/documents',
            lazy: async () => ({
              Component: (await import('@/features/documents/documents-page')).DocumentsPage,
            }),
          },
          {
            path: '/usage',
            lazy: async () => ({
              Component: (await import('@/features/usage/usage-page')).UsagePage,
            }),
          },
          {
            path: '/reports',
            lazy: async () => ({
              Component: (await import('@/features/reports/reports-page')).ReportsPage,
            }),
          },
          {
            path: '/account',
            lazy: async () => ({
              Component: (await import('@/features/account/account-page')).AccountPage,
            }),
          },
          {
            path: '/admin',
            lazy: async () => ({
              Component: (await import('@/features/admin/admin-hub-page')).AdminHubPage,
            }),
          },
          {
            element: <RequireRole role="admin" />,
            children: [
              {
                path: '/admin/dashboard',
                lazy: async () => ({
                  Component: (await import('@/features/admin/dashboard/dashboard-page'))
                    .DashboardPage,
                }),
              },
              {
                path: '/admin/users',
                lazy: async () => ({
                  Component: (await import('@/features/admin/users/users-page')).UsersPage,
                }),
              },
              {
                path: '/admin/feedback',
                lazy: async () => ({
                  Component: (await import('@/features/admin/feedback/feedback-page'))
                    .FeedbackPage,
                }),
              },
            ],
          },
          {
            element: <RequireRole role="superadmin" />,
            children: [
              {
                path: '/admin/models',
                lazy: async () => ({
                  Component: (await import('@/features/admin/models/models-page')).ModelsPage,
                }),
              },
              {
                path: '/admin/organizations',
                lazy: async () => ({
                  Component: (await import('@/features/admin/organizations/organizations-page'))
                    .OrganizationsPage,
                }),
              },
              {
                path: '/admin/settings',
                lazy: async () => ({
                  Component: (await import('@/features/admin/settings/settings-page'))
                    .SettingsPage,
                }),
              },
              {
                path: '/admin/api-keys',
                lazy: async () => ({
                  Component: (await import('@/features/admin/api-keys/api-keys-page'))
                    .ApiKeysPage,
                }),
              },
              {
                path: '/admin/bots',
                lazy: async () => ({
                  Component: (await import('@/features/admin/bots/bots-page')).BotsPage,
                }),
              },
              {
                path: '/admin/email',
                lazy: async () => ({
                  Component: (await import('@/features/admin/email/email-settings-page'))
                    .EmailSettingsPage,
                }),
              },
              {
                path: '/admin/sso',
                lazy: async () => ({
                  Component: (await import('@/features/admin/sso/sso-settings-page'))
                    .SsoSettingsPage,
                }),
              },
              {
                path: '/admin/health',
                lazy: async () => ({
                  Component: (await import('@/features/admin/health/health-page')).HealthPage,
                }),
              },
            ],
          },
          {
            element: <RequireAction action="roles.read" />,
            children: [
              {
                path: '/admin/roles',
                lazy: async () => ({
                  Component: (await import('@/features/admin/roles/roles-page')).RolesPage,
                }),
              },
            ],
          },
          {
            element: <RequireAction action="audit.read" />,
            children: [
              {
                path: '/admin/audit',
                lazy: async () => ({
                  Component: (await import('@/features/admin/audit/audit-page')).AuditPage,
                }),
              },
            ],
          },
        ],
      },
    ],
  },
]);
