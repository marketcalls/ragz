import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { AttachmentUpload } from './attachment-upload';

test('uploading a file calls onUploaded with the attachment id', async () => {
  const onUploaded = vi.fn();
  const upload = vi.fn().mockResolvedValue({ id: 'a1', kind: 'document', filename: 'x.txt', mime: 'text/plain', status: 'queued' });
  const user = userEvent.setup();
  render(<AttachmentUpload onUpload={upload} onUploaded={onUploaded} />);
  const file = new File(['hello'], 'x.txt', { type: 'text/plain' });
  await user.upload(screen.getByLabelText('Attach a file'), file);
  await vi.waitFor(() => expect(onUploaded).toHaveBeenCalledWith('a1'));
});
