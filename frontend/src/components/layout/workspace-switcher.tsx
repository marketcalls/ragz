import { Check, ChevronsUpDown, Plus, Settings2 } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useClaims } from '@/lib/use-claims';

import { useCreateWorkspace, useWorkspaces } from '@/features/workspaces/queries';
import { useWorkspace } from '@/features/workspaces/workspace-context';
import { WorkspaceSettingsDialog } from '@/features/workspaces/workspace-settings-dialog';

export function WorkspaceSwitcher() {
  const claims = useClaims();
  const { data: workspaces } = useWorkspaces();
  const { workspaceId, setWorkspaceId } = useWorkspace();
  const create = useCreateWorkspace();
  const navigate = useNavigate();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [name, setName] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);

  const current = workspaces?.find((w) => w.id === workspaceId);
  const isAdmin = claims?.role === 'admin' || claims?.role === 'superadmin';

  // Switching workspaces invalidates the open chat's context (it belongs to
  // the old workspace), so send the user to a fresh /chat rather than
  // stranding them on a page that no longer matches the active workspace.
  const switchWorkspace = (id: string): void => {
    setWorkspaceId(id);
    navigate('/chat');
  };

  const onCreate = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(
      { name },
      {
        onSuccess: (ws) => {
          switchWorkspace(ws.id);
          setName('');
          setDialogOpen(false);
        },
      },
    );
  };

  return (
    <>
      <div className="flex items-center gap-1">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              className="flex flex-1 items-center justify-between rounded-md px-2 py-1.5 text-[13px] font-medium text-ink hover:bg-subtle"
              aria-label="Switch workspace"
            >
              <span className="truncate">{current?.name ?? 'Select workspace'}</span>
              <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-56">
            {(workspaces ?? []).map((w) => (
              <DropdownMenuItem key={w.id} onSelect={() => switchWorkspace(w.id)}>
                <span className="flex-1 truncate">{w.name}</span>
                {w.id === workspaceId ? <Check className="h-3.5 w-3.5 text-accent" aria-hidden /> : null}
              </DropdownMenuItem>
            ))}
            {isAdmin ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => setDialogOpen(true)}>
                  <Plus className="mr-1 h-3.5 w-3.5" aria-hidden /> New workspace
                </DropdownMenuItem>
              </>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
        {current ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label="Workspace settings"
            onClick={() => setSettingsOpen(true)}
          >
            <Settings2 className="h-3.5 w-3.5" aria-hidden />
          </Button>
        ) : null}
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent title="New workspace">
          <form onSubmit={onCreate}>
            <Label htmlFor="ws-name">Name</Label>
            <Input id="ws-name" required value={name} onChange={(e) => setName(e.target.value)} />
            <DialogFooter>
              <Button onClick={() => setDialogOpen(false)}>Cancel</Button>
              <Button type="submit" variant="primary" disabled={create.isPending}>
                Create
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {current && settingsOpen ? (
        <WorkspaceSettingsDialog workspace={current} open onOpenChange={setSettingsOpen} />
      ) : null}
    </>
  );
}
