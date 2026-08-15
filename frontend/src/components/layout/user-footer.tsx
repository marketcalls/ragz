import { LogOut, UserCog } from 'lucide-react';
import { Link } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { useClaims } from '@/lib/use-claims';

import { useLogout } from '@/features/auth/mutations';

import { ThemeToggle } from './theme-toggle';

export function UserFooter() {
  const claims = useClaims();
  const logout = useLogout();
  return (
    <div className="flex items-center justify-between border-t border-line-faint px-2 py-2">
      <div className="min-w-0">
        <p className="truncate text-[12px] font-medium text-ink">{claims?.sub ?? ''}</p>
        <p className="text-[11px] text-muted">{claims?.role ?? ''}</p>
      </div>
      <div className="flex items-center">
        <ThemeToggle />
        <Button variant="ghost" size="icon" aria-label="Account settings" asChild>
          <Link to="/account">
            <UserCog className="h-4 w-4" aria-hidden />
          </Link>
        </Button>
        <Button variant="ghost" size="icon" aria-label="Sign out" onClick={() => logout.mutate()}>
          <LogOut className="h-4 w-4" aria-hidden />
        </Button>
      </div>
    </div>
  );
}
