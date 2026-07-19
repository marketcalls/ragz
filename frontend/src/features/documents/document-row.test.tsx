import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { DocumentOut } from '@/api/types';
import { setAccessToken } from '@/lib/auth-store';

import { DocumentRow } from './document-row';

const doc: DocumentOut = {
  id: 'd1',
  filename: 'report.pdf',
  mime: 'application/pdf',
  size_bytes: 1024,
  status: 'indexed',
  page_count: 3,
  error: null,
  created_at: '2026-07-18T00:00:00Z',
  pinned: false,
  // DOC-5 version lineage fields (Plan H); irrelevant to DocumentRow's own
  // behavior but required by the (now regenerated) DocumentOut shape.
  version: 1,
  lineage_id: 'd1',
  is_current: true,
  approved: false,
  supersedes_document_id: null,
};

const b64 = (o: object) =>
  btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const tokenFor = (role: string) =>
  `${b64({ alg: 'HS256' })}.${b64({ sub: 'u1', org: 'o1', role, exp: 9999999999 })}.s`;

function renderRow(over: Partial<DocumentOut> = {}, onTogglePin = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <table>
        <tbody>
          <DocumentRow
            doc={{ ...doc, ...over }}
            workspaceId="w1"
            deleting={false}
            onDelete={vi.fn()}
            pinning={false}
            onTogglePin={onTogglePin}
          />
        </tbody>
      </table>
    </QueryClientProvider>,
  );
  return onTogglePin;
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

test('pin button fires the toggle callback', async () => {
  const user = userEvent.setup();
  const onTogglePin = renderRow();
  await user.click(screen.getByRole('button', { name: 'Pin report.pdf' }));
  expect(onTogglePin).toHaveBeenCalledOnce();
});

test('pinned document offers unpin', () => {
  renderRow({ pinned: true });
  expect(screen.getByRole('button', { name: 'Unpin report.pdf' })).toBeInTheDocument();
});

test('pin is disabled until the document is indexed', () => {
  renderRow({ status: 'processing' });
  expect(screen.getByRole('button', { name: 'Pin report.pdf' })).toBeDisabled();
});

test('shows the version badge next to the filename', () => {
  renderRow({ version: 3 });
  expect(screen.getByText('v3')).toBeInTheDocument();
});

test('shows an Approved pill when the document is approved', () => {
  renderRow({ approved: true });
  expect(screen.getByText('Approved')).toBeInTheDocument();
});

test('non-admins do not see the approve toggle', () => {
  renderRow();
  expect(screen.queryByRole('button', { name: 'Approve report.pdf' })).not.toBeInTheDocument();
});

function stubFetchForApproval(approved: boolean) {
  const fetchMock = vi.fn(async (req: Request) => {
    if (req.url.includes('/approved')) {
      return new Response(JSON.stringify({ ...doc, approved }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    // AclDialog (admin-only) also mounts and fetches the group list.
    return new Response(JSON.stringify([]), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

test('admins see the approve toggle and can approve a document', async () => {
  setAccessToken(tokenFor('admin'));
  const fetchMock = stubFetchForApproval(true);
  const user = userEvent.setup();
  renderRow();

  await user.click(screen.getByRole('button', { name: 'Approve report.pdf' }));

  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([req]: [Request]) => req.url.includes('/approved'))).toBe(
      true,
    ),
  );
  const req = fetchMock.mock.calls.find(([r]: [Request]) => r.url.includes('/approved'))![0] as Request;
  expect(req.method).toBe('PUT');
  expect(req.url).toContain('/api/v1/documents/d1/approved');
  expect(JSON.parse(await req.clone().text())).toEqual({ approved: true });
});

test('admins can unapprove an approved document', async () => {
  setAccessToken(tokenFor('admin'));
  const fetchMock = stubFetchForApproval(false);
  const user = userEvent.setup();
  renderRow({ approved: true });

  await user.click(screen.getByRole('button', { name: 'Unapprove report.pdf' }));

  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([req]: [Request]) => req.url.includes('/approved'))).toBe(
      true,
    ),
  );
  const req = fetchMock.mock.calls.find(([r]: [Request]) => r.url.includes('/approved'))![0] as Request;
  expect(JSON.parse(await req.clone().text())).toEqual({ approved: false });
});
