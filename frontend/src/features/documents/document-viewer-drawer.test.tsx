import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { DocumentFileStatus } from './document-file';

const useDocumentFile = vi.fn();
vi.mock('./document-file', () => ({
  useDocumentFile: (documentId: string | null) => useDocumentFile(documentId),
}));

import { DocumentViewerDrawer } from './document-viewer-drawer';

function mockFile(overrides: {
  objectUrl?: string | null;
  mimeType?: string | null;
  status?: DocumentFileStatus;
}) {
  useDocumentFile.mockReturnValue({
    objectUrl: null,
    mimeType: null,
    status: 'loading' as DocumentFileStatus,
    ...overrides,
  });
}

beforeEach(() => {
  useDocumentFile.mockReset();
});

test('shows a loading spinner while the file is fetching', () => {
  mockFile({ status: 'loading' });
  render(
    <DocumentViewerDrawer documentId="d1" page={3} filename="report.pdf" onClose={vi.fn()} />,
  );
  expect(useDocumentFile).toHaveBeenCalledWith('d1');
  expect(screen.getByRole('status')).toBeInTheDocument();
});

test('renders the file in an iframe with #page={page} for a viewable mime', () => {
  mockFile({ status: 'success', objectUrl: 'blob:mock-url', mimeType: 'application/pdf' });
  render(
    <DocumentViewerDrawer documentId="d1" page={7} filename="report.pdf" onClose={vi.fn()} />,
  );
  const frame = screen.getByTitle('report.pdf');
  expect(frame).toHaveAttribute('src', 'blob:mock-url#page=7');
});

test('shows the toolbar with filename, version, and page', () => {
  mockFile({ status: 'success', objectUrl: 'blob:mock-url', mimeType: 'application/pdf' });
  render(
    <DocumentViewerDrawer
      documentId="d1"
      page={2}
      filename="report.pdf"
      version={3}
      onClose={vi.fn()}
    />,
  );
  expect(screen.getByRole('heading', { name: 'report.pdf' })).toBeInTheDocument();
  expect(screen.getByText('v3 · p. 2')).toBeInTheDocument();
});

test('a non-viewable mime shows a fallback card with a Download button instead of an iframe', () => {
  mockFile({
    status: 'success',
    objectUrl: 'blob:mock-url',
    mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  });
  render(
    <DocumentViewerDrawer documentId="d1" page={1} filename="policy.docx" onClose={vi.fn()} />,
  );
  expect(screen.queryByTitle('policy.docx')).not.toBeInTheDocument();
  expect(screen.getByText("This file type can't be previewed")).toBeInTheDocument();
  expect(screen.getAllByRole('button', { name: 'Download' }).length).toBeGreaterThan(0);
});

test('status "forbidden" shows an access-denied message, not a blank frame', () => {
  mockFile({ status: 'forbidden' });
  render(
    <DocumentViewerDrawer documentId="d1" page={1} filename="secret.pdf" onClose={vi.fn()} />,
  );
  expect(screen.getByText("You don't have access to this document.")).toBeInTheDocument();
  expect(screen.queryByTitle('secret.pdf')).not.toBeInTheDocument();
});

test('status "not-found" shows a file-not-available message', () => {
  mockFile({ status: 'not-found' });
  render(<DocumentViewerDrawer documentId="d1" page={1} filename="gone.pdf" onClose={vi.fn()} />);
  expect(screen.getByText(/original file isn't available to preview/i)).toBeInTheDocument();
});

test('clicking Close calls onClose', async () => {
  mockFile({ status: 'success', objectUrl: 'blob:mock-url', mimeType: 'application/pdf' });
  const onClose = vi.fn();
  const user = userEvent.setup();
  render(
    <DocumentViewerDrawer documentId="d1" page={1} filename="report.pdf" onClose={onClose} />,
  );
  await user.click(screen.getByRole('button', { name: 'Close' }));
  expect(onClose).toHaveBeenCalled();
});

test('pressing Escape calls onClose', async () => {
  mockFile({ status: 'success', objectUrl: 'blob:mock-url', mimeType: 'application/pdf' });
  const onClose = vi.fn();
  const user = userEvent.setup();
  render(
    <DocumentViewerDrawer documentId="d1" page={1} filename="report.pdf" onClose={onClose} />,
  );
  await user.keyboard('{Escape}');
  expect(onClose).toHaveBeenCalled();
});

test('an "Open in new tab" link points at the page-anchored object URL', () => {
  mockFile({ status: 'success', objectUrl: 'blob:mock-url', mimeType: 'application/pdf' });
  render(
    <DocumentViewerDrawer documentId="d1" page={5} filename="report.pdf" onClose={vi.fn()} />,
  );
  const link = screen.getByRole('link', { name: /open in new tab/i });
  expect(link).toHaveAttribute('href', 'blob:mock-url#page=5');
  expect(link).toHaveAttribute('target', '_blank');
});
