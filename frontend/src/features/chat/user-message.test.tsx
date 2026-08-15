import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';

import type { AttachmentOut } from '@/api/types';

import { UserMessage } from './user-message';

function attachment(overrides: Partial<AttachmentOut> = {}): AttachmentOut {
  return {
    id: 'a1', kind: 'document', filename: 'notes.txt', mime: 'text/plain', status: 'ready',
    ...overrides,
  };
}

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
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

test('without a chatId, both document and image attachments fall back to chips', () => {
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
  // No chatId -> no live fetch, so no <img> and no interactive control.
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
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

test('with a chatId, an image attachment renders a live thumbnail and enlarges on click', async () => {
  const blob = new Blob(['bytes'], { type: 'image/png' });
  const fetchMock = vi.fn(
    async () => new Response(blob, { status: 200, headers: { 'content-type': 'image/png' } }),
  );
  vi.stubGlobal('fetch', fetchMock);
  URL.createObjectURL = vi.fn().mockReturnValue('blob:img-1');
  URL.revokeObjectURL = vi.fn();
  const user = userEvent.setup();

  render(
    <UserMessage
      content="see attached"
      chatId="c1"
      attachments={[attachment({ id: 'i1', kind: 'image', filename: 'photo.png', mime: 'image/png' })]}
    />,
    { wrapper },
  );

  const thumb = await screen.findByRole('img', { name: 'photo.png' });
  expect(thumb).toHaveAttribute('src', 'blob:img-1');

  await user.click(screen.getByRole('button', { name: 'Enlarge photo.png' }));
  // Lightbox dialog opens with the full image (title = filename).
  await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());

  vi.unstubAllGlobals();
});

test('a document attachment still renders a chip even with a chatId', () => {
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);

  render(
    <UserMessage
      content="doc"
      chatId="c1"
      attachments={[attachment({ id: 'd1', kind: 'document', filename: 'report.pdf' })]}
    />,
    { wrapper },
  );

  expect(screen.getByText('report.pdf')).toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();
  vi.unstubAllGlobals();
});
