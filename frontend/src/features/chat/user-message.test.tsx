import { render, screen } from '@testing-library/react';

import type { AttachmentOut } from '@/api/types';

import { UserMessage } from './user-message';

function attachment(overrides: Partial<AttachmentOut> = {}): AttachmentOut {
  return {
    id: 'a1', kind: 'document', filename: 'notes.txt', mime: 'text/plain', status: 'ready',
    ...overrides,
  };
}

test('renders the message content with no attachment list when attachments is absent', () => {
  render(<UserMessage content="hello" />);
  expect(screen.getByText('hello')).toBeInTheDocument();
  expect(screen.queryByLabelText('Attachments')).not.toBeInTheDocument();
});

test('renders no attachment list when attachments is null or empty', () => {
  const { rerender } = render(<UserMessage content="hi" attachments={null} />);
  expect(screen.queryByLabelText('Attachments')).not.toBeInTheDocument();
  rerender(<UserMessage content="hi" attachments={[]} />);
  expect(screen.queryByLabelText('Attachments')).not.toBeInTheDocument();
});

test('renders a chip per attachment, for both document and image kinds', () => {
  render(
    <UserMessage
      content="see attached"
      attachments={[
        attachment({ id: 'd1', kind: 'document', filename: 'report.pdf' }),
        attachment({ id: 'i1', kind: 'image', filename: 'photo.png', mime: 'image/png' }),
      ]}
    />,
  );
  const list = screen.getByLabelText('Attachments');
  expect(list).toBeInTheDocument();
  expect(screen.getByText('report.pdf')).toBeInTheDocument();
  expect(screen.getByText('photo.png')).toBeInTheDocument();
  // Read-only: no remove/interactive controls on a transcript chip.
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  // Never a live <img> thumbnail -- there is no authed fetch URL for a chat
  // attachment's bytes, so even image attachments render as a chip.
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

test('renders the footer alongside attachments', () => {
  render(
    <UserMessage
      content="hi"
      attachments={[attachment()]}
      footer={<span>footer-content</span>}
    />,
  );
  expect(screen.getByText('footer-content')).toBeInTheDocument();
  expect(screen.getByText('notes.txt')).toBeInTheDocument();
});
