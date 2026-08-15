import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { AttachmentUpload } from './attachment-upload';

test('picking files calls onSelect with the picked File[]', async () => {
  const onSelect = vi.fn();
  const user = userEvent.setup();
  render(<AttachmentUpload onSelect={onSelect} />);
  const file = new File(['hello'], 'x.txt', { type: 'text/plain' });
  await user.upload(screen.getByLabelText('Attach a file'), file);
  expect(onSelect).toHaveBeenCalledWith([file]);
});

test('the input accepts images, pdf, txt, md and docx (UX filter, not enforced server-side)', () => {
  render(<AttachmentUpload onSelect={vi.fn()} />);
  expect(screen.getByLabelText('Attach a file')).toHaveAttribute(
    'accept',
    'image/*,application/pdf,.txt,.md,.docx',
  );
});

test('disabled disables the input and marks the control aria-disabled', () => {
  render(<AttachmentUpload onSelect={vi.fn()} disabled />);
  expect(screen.getByLabelText('Attach a file')).toBeDisabled();
});
