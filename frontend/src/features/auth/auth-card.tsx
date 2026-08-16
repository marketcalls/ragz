import { type ReactNode } from 'react';

import { Logo } from '@/components/logo';

export function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-sidebar">
      <div className="w-full max-w-sm rounded-lg border border-line bg-bg p-6 shadow-soft">
        <div className="mb-5 flex items-center gap-2">
          <Logo className="h-5 w-5" />
          <span className="text-[16px] font-semibold tracking-[-0.01em] text-ink">Ragz</span>
        </div>
        <h1 className="mb-4 text-[18px] font-semibold tracking-[-0.01em] text-ink">{title}</h1>
        {children}
      </div>
    </div>
  );
}
