import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { DocumentOut, MetadataFieldOut } from '@/api/types';

vi.mock('@/components/ui/toaster', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
  Toaster: () => null,
}));

import { toast } from '@/components/ui/toaster';

import { MetadataDialog } from './metadata-dialog';

const doc: DocumentOut = {
  id: 'd1',
  filename: 'policy.pdf',
  mime: 'application/pdf',
  size_bytes: 1024,
  status: 'indexed',
  page_count: 3,
  error: null,
  created_at: '2026-07-18T00:00:00Z',
  pinned: false,
  version: 1,
  lineage_id: 'd1',
  is_current: true,
  approved: false,
  supersedes_document_id: null,
  meta: null,
  enriched: false,
};

const fields: MetadataFieldOut[] = [
  { id: 'f1', name: 'department', label: 'Department', field_type: 'text', options: null, position: 0 },
  {
    id: 'f2',
    name: 'doc_type',
    label: 'Document Type',
    field_type: 'select',
    options: ['policy', 'manual'],
    position: 1,
  },
  { id: 'f3', name: 'revision_date', label: 'Revision Date', field_type: 'date', options: null, position: 2 },
];

function stubFetch() {
  const fetchMock = vi.fn(
    async (_req: Request) =>
      new Response(JSON.stringify({ ...doc }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderDialog(over: Partial<DocumentOut> = {}) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MetadataDialog
        doc={{ ...doc, ...over }}
        fields={fields}
        workspaceId="w1"
        open
        onOpenChange={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

test('renders one control per field type', () => {
  stubFetch();
  renderDialog();
  expect(screen.getByLabelText('Department')).toHaveAttribute('type', 'text');
  expect(screen.getByLabelText('Revision Date')).toHaveAttribute('type', 'date');
  expect(screen.getByLabelText('Document Type').tagName).toBe('SELECT');
});

test('the select field shows its configured options', () => {
  stubFetch();
  renderDialog();
  const select = screen.getByLabelText('Document Type');
  expect(screen.getByRole('option', { name: 'policy' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'manual' })).toBeInTheDocument();
  expect(select.querySelectorAll('option')).toHaveLength(3); // placeholder + 2 options
});

test('pre-fills inputs from the document\'s existing metadata', () => {
  stubFetch();
  renderDialog({ meta: { department: 'Finance', doc_type: 'policy' } });
  expect(screen.getByLabelText('Department')).toHaveValue('Finance');
  expect(screen.getByLabelText('Document Type')).toHaveValue('policy');
});

test('save PUTs the edited values as { values } and closes on success', async () => {
  const fetchMock = stubFetch();
  const user = userEvent.setup();
  const onOpenChange = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MetadataDialog doc={doc} fields={fields} workspaceId="w1" open onOpenChange={onOpenChange} />
    </QueryClientProvider>,
  );

  await user.type(screen.getByLabelText('Department'), 'Finance');
  await user.selectOptions(screen.getByLabelText('Document Type'), 'policy');
  fireEvent.change(screen.getByLabelText('Revision Date'), { target: { value: '2026-01-01' } });
  await user.click(screen.getByRole('button', { name: 'Save' }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const req = fetchMock.mock.calls[0]![0] as Request;
  expect(req.method).toBe('PUT');
  expect(req.url).toContain('/api/v1/documents/d1/metadata');
  const body = (await req.clone().json()) as Record<string, unknown>;
  expect(body).toStrictEqual({
    values: { department: 'Finance', doc_type: 'policy', revision_date: '2026-01-01' },
  });
  await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
});

test('a 409 validation failure surfaces the server detail, not a generic message', async () => {
  const fetchMock = vi.fn(
    async (_req: Request) =>
      new Response(JSON.stringify({ detail: "'bogus' is not an option of doc_type" }), {
        status: 409,
        headers: { 'content-type': 'application/json' },
      }),
  );
  vi.stubGlobal('fetch', fetchMock);
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MetadataDialog doc={doc} fields={fields} workspaceId="w1" open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );

  await user.type(screen.getByLabelText('Department'), 'Finance');
  await user.click(screen.getByRole('button', { name: 'Save' }));

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("'bogus' is not an option of doc_type"),
  );
});

test('an unconfigured workspace shows a placeholder instead of a blank form', () => {
  stubFetch();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MetadataDialog doc={doc} fields={[]} workspaceId="w1" open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
  expect(
    screen.getByText('No metadata fields are configured for this workspace yet.'),
  ).toBeInTheDocument();
});
