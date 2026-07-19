import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { DocumentOut } from '@/api/types';

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

function renderRow(over: Partial<DocumentOut> = {}, onTogglePin = vi.fn()) {
  render(
    <table>
      <tbody>
        <DocumentRow
          doc={{ ...doc, ...over }}
          deleting={false}
          onDelete={vi.fn()}
          pinning={false}
          onTogglePin={onTogglePin}
        />
      </tbody>
    </table>,
  );
  return onTogglePin;
}

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
