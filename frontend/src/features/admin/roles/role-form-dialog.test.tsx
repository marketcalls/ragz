import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { RoleTemplateOut } from '@/api/types';

vi.mock('@/components/ui/toaster', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
  Toaster: () => null,
}));

import { RoleFormDialog } from './role-form-dialog';

const fixtureRole: RoleTemplateOut = {
  id: 'r1',
  name: 'Reviewer',
  description: 'Reads and comments only',
  permissions: ['documents.upload', 'chat.use'],
  status: 'active',
  version: 1,
};

function renderDialog(
  fetchMock = vi.fn(),
  options: { role?: RoleTemplateOut | null; onOpenChange?: (open: boolean) => void } = {},
) {
  vi.stubGlobal('fetch', fetchMock);
  const queryClient = new QueryClient();
  const onOpenChange = options.onOpenChange ?? vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <RoleFormDialog open onOpenChange={onOpenChange} role={options.role ?? null} />
    </QueryClientProvider>,
  );
  return { queryClient, onOpenChange };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

test('renders one labeled checkbox per permission', () => {
  renderDialog();
  expect(screen.getByLabelText('Upload documents')).toBeInTheDocument();
  expect(screen.getByLabelText('Delete documents')).toBeInTheDocument();
  expect(screen.getByLabelText('Configure workspace settings')).toBeInTheDocument();
  expect(screen.getByLabelText('View usage analytics')).toBeInTheDocument();
  expect(screen.getByLabelText('Use chat')).toBeInTheDocument();
  expect(screen.getAllByRole('checkbox')).toHaveLength(5);
});

test('submit posts only the checked permission flags', async () => {
  const fetchMock = vi.fn(async (_req: Request) =>
    new Response(JSON.stringify({ id: 'r1' }), {
      status: 201,
      headers: { 'content-type': 'application/json' },
    }),
  );
  const user = userEvent.setup();
  renderDialog(fetchMock);
  await user.type(screen.getByLabelText('Name'), 'Reviewer');
  await user.type(screen.getByLabelText('Description'), 'Reads and comments only');
  await user.click(screen.getByLabelText('Upload documents'));
  await user.click(screen.getByLabelText('Use chat'));
  await user.click(screen.getByRole('button', { name: 'Add role' }));
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const req = fetchMock.mock.calls[0]![0] as Request;
  const body = JSON.parse(await req.clone().text()) as Record<string, unknown>;
  expect(body).toEqual({
    name: 'Reviewer',
    description: 'Reads and comments only',
    permissions: ['documents.upload', 'chat.use'],
  });
});

test('edit mode pre-checks the role template permissions', () => {
  renderDialog(vi.fn(), { role: fixtureRole });
  expect(screen.getByLabelText('Upload documents')).toBeChecked();
  expect(screen.getByLabelText('Use chat')).toBeChecked();
  expect(screen.getByLabelText('Delete documents')).not.toBeChecked();
  expect(screen.getByRole('button', { name: 'Save changes' })).toBeInTheDocument();
});
