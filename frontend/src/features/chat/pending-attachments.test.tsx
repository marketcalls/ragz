import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { PendingAttachments } from './pending-attachments';
import type { PendingAttachment } from './use-pending-attachments';

function attachment(overrides: Partial<PendingAttachment> = {}): PendingAttachment {
  return {
    id: 'p1',
    file: new File(['x'], 'notes.txt', { type: 'text/plain' }),
    previewUrl: null,
    ...overrides,
  };
}

test('renders nothing when there are no pending files', () => {
  const { container } = render(<PendingAttachments files={[]} onRemove={vi.fn()} />);
  expect(container).toBeEmptyDOMElement();
});

test('an image file renders a thumbnail; a non-image renders a file chip', () => {
  const img = attachment({
    id: 'img1',
    file: new File(['x'], 'photo.png', { type: 'image/png' }),
    previewUrl: 'blob:mock-1',
  });
  const doc = attachment({ id: 'doc1' });
  render(<PendingAttachments files={[img, doc]} onRemove={vi.fn()} />);

  // Image card: thumbnail only (filename lives on the remove control).
  expect(screen.getByAltText('')).toHaveAttribute('src', 'blob:mock-1');
  expect(screen.getByLabelText('Remove photo.png')).toBeInTheDocument();
  // Non-image card: file icon + filename label.
  expect(screen.getByText('notes.txt')).toBeInTheDocument();
});

test('the remove button removes the corresponding pending file', async () => {
  const onRemove = vi.fn();
  const user = userEvent.setup();
  render(<PendingAttachments files={[attachment()]} onRemove={onRemove} />);
  await user.click(screen.getByLabelText('Remove notes.txt'));
  expect(onRemove).toHaveBeenCalledWith('p1');
});
