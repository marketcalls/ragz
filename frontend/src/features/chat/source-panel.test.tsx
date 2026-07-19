import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { SourcePanel, type SourceChipData } from './source-panel';

const sources: SourceChipData[] = [
  { marker: 1, document_id: 'd1', filename: 'intro.pdf', page: 1 },
  { marker: 2, document_id: 'd2', filename: 'report.pdf', page: 4 },
];

test('renders the chip container as an accessible list with a listitem per chip', () => {
  render(<SourcePanel sources={sources} />);
  expect(screen.getByRole('list', { name: 'Sources' })).toBeInTheDocument();
  expect(screen.getAllByRole('listitem')).toHaveLength(2);
});

test('each chip is a real button with a descriptive aria-label', () => {
  render(<SourcePanel sources={sources} />);
  expect(screen.getByRole('button', { name: 'Source 2: report.pdf, page 4' })).toBeInTheDocument();
});

test('chips are reachable and activatable via keyboard', async () => {
  const onSelect = vi.fn();
  const user = userEvent.setup();
  render(<SourcePanel sources={sources} onSelect={onSelect} />);
  await user.tab();
  expect(screen.getByRole('button', { name: 'Source 1: intro.pdf, page 1' })).toHaveFocus();
  await user.keyboard('{Enter}');
  expect(onSelect).toHaveBeenCalledWith(1);
});

test('renders nothing when there are no sources', () => {
  render(<SourcePanel sources={[]} />);
  expect(screen.queryByRole('list')).not.toBeInTheDocument();
});

test('renders version and section when present (CHAT-4)', () => {
  const withMeta: SourceChipData[] = [
    {
      marker: 1,
      document_id: 'd1',
      filename: 'evac.pdf',
      page: 2,
      version: 2,
      section: 'Fire Safety > Evacuation',
    },
  ];
  render(<SourcePanel sources={withMeta} />);
  expect(screen.getByText('· v2')).toBeInTheDocument();
  expect(screen.getByText('· Fire Safety > Evacuation')).toBeInTheDocument();
});

test('omits version/section chrome when absent', () => {
  render(<SourcePanel sources={sources} />);
  expect(screen.queryByText(/^· v\d/)).not.toBeInTheDocument();
});

test('a chip with a url renders the hostname, the globe glyph, and an "open" link (Task 11/D7)', () => {
  const withUrl: SourceChipData[] = [
    { marker: 1, document_id: '', filename: 'ISO 45001 overview', page: 0, url: 'https://example.test/iso' },
  ];
  render(<SourcePanel sources={withUrl} />);
  expect(screen.getByText('· example.test')).toBeInTheDocument();
  expect(screen.getByText('ISO 45001 overview')).toBeInTheDocument();
  const link = screen.getByRole('link', { name: 'open' });
  expect(link).toHaveAttribute('href', 'https://example.test/iso');
  expect(link).toHaveAttribute('target', '_blank');
  expect(link).toHaveAttribute('rel', 'noopener noreferrer');
});

test('a doc chip (no url) is rendered unchanged', () => {
  render(<SourcePanel sources={sources} />);
  expect(screen.queryByRole('link', { name: 'open' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Source 1: intro.pdf, page 1' })).toBeInTheDocument();
});
