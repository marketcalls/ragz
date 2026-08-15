import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ComposerControls } from './composer-controls';

function setup(props: Partial<Parameters<typeof ComposerControls>[0]> = {}) {
  return render(
    <ComposerControls
      onSelectFiles={vi.fn()}
      webSearchAvailable={false}
      webSearch={false}
      onToggleWebSearch={vi.fn()}
      {...props}
    />,
  );
}

test('the file input keeps the same accept list (UX filter, not enforced server-side)', () => {
  setup();
  expect(screen.getByLabelText('Attach a file')).toHaveAttribute(
    'accept',
    'image/*,application/pdf,.txt,.md,.docx',
  );
});

test('opening the + menu shows "Add photos & files"; selecting it triggers the file input', async () => {
  const user = userEvent.setup();
  setup();
  const input = screen.getByLabelText('Attach a file');
  const clickSpy = vi.spyOn(input, 'click').mockImplementation(() => {});

  await user.click(screen.getByRole('button', { name: 'Add attachments and options' }));
  await user.click(screen.getByRole('menuitem', { name: /Add photos & files/i }));

  expect(clickSpy).toHaveBeenCalledOnce();
});

test('picking files calls onSelectFiles with the picked File[]', async () => {
  const onSelectFiles = vi.fn();
  const user = userEvent.setup();
  setup({ onSelectFiles });
  const file = new File(['hello'], 'x.txt', { type: 'text/plain' });
  await user.upload(screen.getByLabelText('Attach a file'), file);
  expect(onSelectFiles).toHaveBeenCalledWith([file]);
});

test('the "Search the web" item is hidden when the workspace disallows web search', async () => {
  const user = userEvent.setup();
  setup({ webSearchAvailable: false });
  await user.click(screen.getByRole('button', { name: 'Add attachments and options' }));
  expect(screen.queryByRole('menuitem', { name: /Search the web/i })).not.toBeInTheDocument();
});

test('selecting "Search the web" toggles web search', async () => {
  const onToggleWebSearch = vi.fn();
  const user = userEvent.setup();
  setup({ webSearchAvailable: true, onToggleWebSearch });
  await user.click(screen.getByRole('button', { name: 'Add attachments and options' }));
  await user.click(screen.getByRole('menuitem', { name: /Search the web/i }));
  expect(onToggleWebSearch).toHaveBeenCalledOnce();
});

test('the blue Web search pill shows when on and turns it off on click', async () => {
  const onToggleWebSearch = vi.fn();
  const user = userEvent.setup();
  setup({ webSearchAvailable: true, webSearch: true, onToggleWebSearch });
  const pill = screen.getByRole('button', { name: 'Turn off web search' });
  expect(pill).toBeInTheDocument();
  await user.click(pill);
  expect(onToggleWebSearch).toHaveBeenCalledOnce();
});

test('no pill is shown when web search is off', () => {
  setup({ webSearchAvailable: true, webSearch: false });
  expect(screen.queryByRole('button', { name: 'Turn off web search' })).not.toBeInTheDocument();
});
