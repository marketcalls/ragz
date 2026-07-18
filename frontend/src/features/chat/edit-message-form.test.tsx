import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { EditMessageForm } from './edit-message-form';

test('prefills, edits, sends trimmed content', async () => {
  const onSend = vi.fn();
  const user = userEvent.setup();
  render(<EditMessageForm initial="old question" onCancel={vi.fn()} onSend={onSend} />);
  const box = screen.getByRole('textbox', { name: 'Edit message' });
  expect(box).toHaveValue('old question');
  await user.clear(box);
  await user.type(box, '  new question  ');
  await user.click(screen.getByRole('button', { name: 'Send' }));
  expect(onSend).toHaveBeenCalledWith('new question');
});

test('cancel button and Escape both cancel; empty content cannot send', async () => {
  const onCancel = vi.fn();
  const onSend = vi.fn();
  const user = userEvent.setup();
  render(<EditMessageForm initial="x" onCancel={onCancel} onSend={onSend} />);
  const box = screen.getByRole('textbox', { name: 'Edit message' });
  await user.clear(box);
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  await user.type(box, '{Escape}');
  expect(onCancel).toHaveBeenCalledTimes(2);
  expect(onSend).not.toHaveBeenCalled();
});
