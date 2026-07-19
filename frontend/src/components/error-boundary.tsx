import { Component, type ErrorInfo, type ReactNode } from 'react';

import { authFetch } from '@/api/client';

function report(error: Error, info: ErrorInfo): void {
  const body = JSON.stringify({
    message: String(error.message).slice(0, 2000),
    stack: `${error.stack ?? ''}\n${info.componentStack ?? ''}`.slice(0, 8000),
    url: window.location.pathname.slice(0, 500),
  });
  void authFetch(
    new Request(`${window.location.origin}/api/v1/client-errors`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body,
    }),
  ).catch(() => undefined); // reporting must never cascade
}

export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    report(error, info);
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <div className="max-w-sm rounded-lg border border-line bg-bg p-6 text-center">
          <p className="text-[16px] font-semibold text-ink">Something went wrong</p>
          <p className="mt-1 text-sm text-secondary">
            The error was reported. Reload to continue.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="mt-4 rounded-md bg-ink px-3 py-1.5 text-sm text-bg"
          >
            Reload
          </button>
        </div>
      </div>
    );
  }
}
