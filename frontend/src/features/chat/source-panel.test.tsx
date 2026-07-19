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
