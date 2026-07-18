import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { MessageNode } from '@/api/types';

import { MessageActions } from './message-actions';
import type { PathEntry } from './tree';

function entryFor(over: Partial<MessageNode>, siblings: string[] = ['m1'], position = 0): PathEntry {
  return {
    message: {
      id: 'm1',
      parent_message_id: 'p1',
      sibling_index: 0,
      role: 'user',
      content: 'the content',
      model_id: null,
      prompt_tokens: null,
      completion_tokens: null,
      created_at: 't',
      citations: [],
      children: [],
      ...over,
    } as MessageNode,
    siblings,
    position,
  };
}

test('copy writes the message content to the clipboard', async () => {
  // fireEvent, not userEvent: userEvent.setup() installs its own clipboard stub
  // which would shadow this spy.
  const { fireEvent } = await import('@testing-library/react');
  const writeText = vi.fn(async () => {});
  Object.assign(navigator, { clipboard: { writeText } });
  render(
    <MessageActions entry={entryFor({})} disabled={false} onSelectSibling={vi.fn()} />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Copy message' }));
  await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith('the content'));
});

test('edit and regenerate buttons appear only when their handlers exist', () => {
  const { rerender } = render(
    <MessageActions entry={entryFor({})} disabled={false} onSelectSibling={vi.fn()} onEdit={vi.fn()} />,
  );
  expect(screen.getByRole('button', { name: 'Edit message' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Regenerate response' })).not.toBeInTheDocument();
  rerender(
    <MessageActions
      entry={entryFor({ role: 'assistant' })}
      disabled={false}
      onSelectSibling={vi.fn()}
      onRegenerate={vi.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: 'Regenerate response' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Edit message' })).not.toBeInTheDocument();
});

test('sibling nav renders n/n and navigates by branch key', async () => {
  const onSelectSibling = vi.fn();
  const user = userEvent.setup();
  render(
    <MessageActions
      entry={entryFor({ id: 'm2', sibling_index: 1 }, ['m1', 'm2', 'm3'], 1)}
      disabled={false}
      onSelectSibling={onSelectSibling}
    />,
  );
  expect(screen.getByText('2/3')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Previous version' }));
  expect(onSelectSibling).toHaveBeenCalledWith('p1', 'm1');
  await user.click(screen.getByRole('button', { name: 'Next version' }));
  expect(onSelectSibling).toHaveBeenCalledWith('p1', 'm3');
});

test('single sibling hides the nav; ends disable their arrow', () => {
  render(
    <MessageActions
      entry={entryFor({}, ['m1', 'm2'], 0)}
      disabled={false}
      onSelectSibling={vi.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: 'Previous version' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Next version' })).toBeEnabled();
});
