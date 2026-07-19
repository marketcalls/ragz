import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ChatInput } from './chat-input';

test('Enter sends and clears; Shift+Enter inserts a newline', async () => {
  const onSend = vi.fn();
  const user = userEvent.setup();
  render(<ChatInput onSend={onSend} disabled={false} />);
  const box = screen.getByRole('textbox', { name: 'Message' });
  await user.type(box, 'hello');
  await user.keyboard('{Enter}');
  expect(onSend).toHaveBeenCalledWith('hello');
  expect(box).toHaveValue('');
  await user.type(box, 'a{Shift>}{Enter}{/Shift}b');
  expect(box).toHaveValue('a\nb');
  expect(onSend).toHaveBeenCalledTimes(1);
});

test('whitespace-only content is not sent; disabled blocks sending', async () => {
  const onSend = vi.fn();
  const user = userEvent.setup();
  const { rerender } = render(<ChatInput onSend={onSend} disabled={false} />);
  await user.type(screen.getByRole('textbox', { name: 'Message' }), '   {Enter}');
  expect(onSend).not.toHaveBeenCalled();
  rerender(<ChatInput onSend={onSend} disabled />);
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
});

test('shows Stop instead of Send while busy and calls onStop', async () => {
  const onStop = vi.fn();
  render(<ChatInput onSend={vi.fn()} disabled busy onStop={onStop} />);
  expect(screen.queryByRole('button', { name: 'Send' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Stop generating' }));
  expect(onStop).toHaveBeenCalledOnce();
});
