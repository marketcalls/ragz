import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { DocumentFileStatus } from './document-file';

const useDocumentFile = vi.fn();
vi.mock('./document-file', () => ({
  useDocumentFile: (documentId: string | null) => useDocumentFile(documentId),
}));
vi.mock('./pdf-preview', () => ({
  PdfPreview: ({ filename, page }: { filename: string; page: number }) => (
    <canvas role="img" aria-label={`Page ${page} of ${filename}`} data-pdf-page={page} />
  ),
}));

import { DocumentViewerDrawer } from './document-viewer-drawer';

function mockFile(overrides: {
  objectUrl?: string | null;
  mimeType?: string | null;
  textContent?: string | null;
  status?: DocumentFileStatus;
}) {
  useDocumentFile.mockReturnValue({
    objectUrl: null,
    mimeType: null,
    textContent: null,
    status: 'loading' as DocumentFileStatus,
    ...overrides,
  });
}

beforeEach(() => {
  useDocumentFile.mockReset();
});

test('shows a loading spinner while the file is fetching', () => {
  mockFile({ status: 'loading' });
  render(<DocumentViewerDrawer documentId="d1" page={3} filename="report.pdf" onClose={vi.fn()} />);
  expect(useDocumentFile).toHaveBeenCalledWith('d1');
  expect(screen.getByRole('status')).toBeInTheDocument();
});

test('renders PDF bytes through the controlled canvas viewer at the cited page', () => {
  mockFile({ status: 'success', objectUrl: 'blob:mock-url', mimeType: 'application/pdf' });
  render(<DocumentViewerDrawer documentId="d1" page={7} filename="report.pdf" onClose={vi.fn()} />);
  expect(screen.getByRole('img', { name: 'Page 7 of report.pdf' })).toHaveAttribute(
    'data-pdf-page',
    '7',
  );
  expect(document.querySelector('iframe')).toBeNull();
});

test('plain text is escaped into a pre element and cannot execute or access the parent', () => {
  const payload =
    '<img src=x onerror="parent.previewPwned=true"><script>parent.previewPwned=true</script>';
  Object.assign(window, { previewPwned: false });
  mockFile({
    status: 'success',
    objectUrl: 'blob:mock-url',
    mimeType: 'text/plain',
    textContent: payload,
  });

  render(<DocumentViewerDrawer documentId="d1" page={1} filename="notes.txt" onClose={vi.fn()} />);

  expect(screen.getByText(payload)).toHaveTextContent(payload);
  expect(screen.queryByTitle('notes.txt')).not.toBeInTheDocument();
  expect(document.querySelector('img')).toBeNull();
  expect(Reflect.get(window, 'previewPwned')).toBe(false);
});

test('active HTML remains download-only and has no new-tab execution path', () => {
  mockFile({ status: 'success', objectUrl: 'blob:mock-url', mimeType: 'text/html' });
  render(
    <DocumentViewerDrawer documentId="d1" page={1} filename="legacy.html" onClose={vi.fn()} />,
  );

  expect(screen.queryByTitle('legacy.html')).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /open in new tab/i })).not.toBeInTheDocument();
  expect(screen.getAllByRole('button', { name: 'Download' })).not.toHaveLength(0);
});

test('verified raster images use an image element rather than a frame', () => {
  mockFile({ status: 'success', objectUrl: 'blob:mock-url', mimeType: 'image/png' });
  render(<DocumentViewerDrawer documentId="d1" page={1} filename="scan.png" onClose={vi.fn()} />);

  expect(screen.getByRole('img', { name: 'scan.png' })).toHaveAttribute('src', 'blob:mock-url');
  expect(screen.queryByTitle('scan.png')).not.toBeInTheDocument();
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
  render(<DocumentViewerDrawer documentId="d1" page={1} filename="secret.pdf" onClose={vi.fn()} />);
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
  render(<DocumentViewerDrawer documentId="d1" page={1} filename="report.pdf" onClose={onClose} />);
  await user.click(screen.getByRole('button', { name: 'Close' }));
  expect(onClose).toHaveBeenCalled();
});

test('pressing Escape calls onClose', async () => {
  mockFile({ status: 'success', objectUrl: 'blob:mock-url', mimeType: 'application/pdf' });
  const onClose = vi.fn();
  const user = userEvent.setup();
  render(<DocumentViewerDrawer documentId="d1" page={1} filename="report.pdf" onClose={onClose} />);
  await user.keyboard('{Escape}');
  expect(onClose).toHaveBeenCalled();
});

test('an "Open in new tab" link points at the page-anchored object URL', () => {
  mockFile({ status: 'success', objectUrl: 'blob:mock-url', mimeType: 'application/pdf' });
  render(<DocumentViewerDrawer documentId="d1" page={5} filename="report.pdf" onClose={vi.fn()} />);
  const link = screen.getByRole('link', { name: /open in new tab/i });
  expect(link).toHaveAttribute('href', 'blob:mock-url#page=5');
  expect(link).toHaveAttribute('target', '_blank');
});
