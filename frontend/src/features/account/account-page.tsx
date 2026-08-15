import { TopBar } from '@/components/layout/top-bar';

import { ChangePasswordForm } from './change-password-form';

export function AccountPage() {
  return (
    <>
      <TopBar title="Account" />
      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-xl space-y-8">
          <section className="space-y-3">
            <h3 className="text-sm font-semibold text-ink">Change password</h3>
            <ChangePasswordForm />
          </section>
        </div>
      </div>
    </>
  );
}
