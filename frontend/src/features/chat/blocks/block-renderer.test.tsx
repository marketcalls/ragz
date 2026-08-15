import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { Block } from '@/api/types';
import { mockResponsiveContainerSize } from '@/test/chart-test-utils';

import { BlockRenderer } from './block-renderer';

mockResponsiveContainerSize();

test('text block renders through the sanitized markdown renderer', () => {
  const blocks: Block[] = [{ type: 'text', markdown: '**Hello** world' }];
  render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByText('Hello')).toBeInTheDocument();
  expect(screen.getByText('world', { exact: false })).toBeInTheDocument();
});

test('chart block (donut) renders the mapped chart primitive', () => {
  const blocks: Block[] = [
    {
      type: 'chart',
      chart: 'donut',
      title: 'Tokens by model',
      category_key: 'model',
      keys: ['tokens'],
      data: [
        { model: 'gpt-4o', tokens: 40 },
        { model: 'claude', tokens: 60 },
      ],
    },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByText('Tokens by model')).toBeInTheDocument();
  expect(container.querySelectorAll('.recharts-pie-sector')).toHaveLength(2);
});

test('chart block with data that does not fit the chosen chart renders nothing (no crash)', () => {
  // "donut" needs a category/value key pair -- this block has neither
  // category_key/x_key nor keys, so the shape can't be mapped.
  const blocks: Block[] = [
    { type: 'chart', chart: 'donut', data: [{ foo: 'bar' }] },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelector('svg')).not.toBeInTheDocument();
  expect(container.textContent).toBe('');
});

test('chart block whose rows have non-numeric values for the value key renders nothing', () => {
  const blocks: Block[] = [
    {
      type: 'chart',
      chart: 'donut',
      category_key: 'model',
      keys: ['tokens'],
      data: [{ model: 'gpt-4o', tokens: 'not-a-number' }],
    },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelector('svg')).not.toBeInTheDocument();
  expect(container.textContent).toBe('');
});

test('info_card renders title, subtitle, and markdown body', () => {
  const blocks: Block[] = [
    {
      type: 'info_card',
      title: 'Q3 Summary',
      subtitle: 'Finance',
      body: 'Revenue **grew** 12%.',
      icon: 'chart',
    },
  ];
  render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByText('Q3 Summary')).toBeInTheDocument();
  expect(screen.getByText('Finance')).toBeInTheDocument();
  expect(screen.getByText('grew')).toBeInTheDocument();
});

test('image_card renders WITHOUT loading image_ref as an <img> src', () => {
  const blocks: Block[] = [
    {
      type: 'image_card',
      title: 'Site plan',
      subtitle: 'Level 2',
      badge: 'New',
      image_ref: 'internal-doc-ref-123',
    },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByText('Site plan')).toBeInTheDocument();
  expect(screen.getByText('New')).toBeInTheDocument();
  // No <img> element at all -- image_ref is opaque, never a URL, no
  // image-serving endpoint exists yet (design doc, v1 scope).
  expect(container.querySelectorAll('img')).toHaveLength(0);
  expect(container.innerHTML).not.toContain('internal-doc-ref-123');
});

test('ranked_list renders numbered items with title + subtitle', () => {
  const blocks: Block[] = [
    {
      type: 'ranked_list',
      title: 'Top documents',
      items: [
        { title: 'Fire safety plan', subtitle: '12 citations' },
        { title: 'Evacuation guide' },
      ],
    },
  ];
  render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByText('Top documents')).toBeInTheDocument();
  expect(screen.getByText('Fire safety plan')).toBeInTheDocument();
  expect(screen.getByText('12 citations')).toBeInTheDocument();
  expect(screen.getByText('Evacuation guide')).toBeInTheDocument();
  expect(screen.getByText('1')).toBeInTheDocument();
  expect(screen.getByText('2')).toBeInTheDocument();
});

test('tag_badges renders one pill per tag', () => {
  const blocks: Block[] = [
    {
      type: 'tag_badges',
      tags: [
        { label: 'Approved', tone: 'success' },
        { label: 'Draft', tone: 'neutral' },
      ],
    },
  ];
  render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByText('Approved')).toBeInTheDocument();
  expect(screen.getByText('Draft')).toBeInTheDocument();
});

test('callout renders as a tone-accented card with title and markdown body', () => {
  const blocks: Block[] = [
    { type: 'callout', tone: 'danger', title: 'Heads up', body: 'Budget is **over**.' },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByText('Heads up')).toBeInTheDocument();
  expect(screen.getByText('over')).toBeInTheDocument();
  expect(container.querySelector('.border-danger')).toBeInTheDocument();
});

test('table renders column headers and row cells as plain text', () => {
  const blocks: Block[] = [
    {
      type: 'table',
      columns: ['Document', 'Version'],
      rows: [
        ['Fire safety plan', 3],
        ['Evacuation guide', 1],
      ],
    },
  ];
  render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByText('Document')).toBeInTheDocument();
  expect(screen.getByText('Version')).toBeInTheDocument();
  expect(screen.getByText('Fire safety plan')).toBeInTheDocument();
  expect(screen.getByText('3')).toBeInTheDocument();
});

test('tabs renders a tab list and shows the first tab’s inner blocks by default', async () => {
  const blocks: Block[] = [
    {
      type: 'tabs',
      tabs: [
        { label: 'Overview', blocks: [{ type: 'text', markdown: 'Overview content' }] },
        { label: 'Details', blocks: [{ type: 'text', markdown: 'Details content' }] },
      ],
    },
  ];
  render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByRole('tablist')).toBeInTheDocument();
  expect(screen.getByRole('tab', { name: 'Overview' })).toBeInTheDocument();
  expect(screen.getByRole('tab', { name: 'Details' })).toBeInTheDocument();
  expect(screen.getByText('Overview content')).toBeInTheDocument();

  const user = userEvent.setup();
  await user.click(screen.getByRole('tab', { name: 'Details' }));
  expect(screen.getByText('Details content')).toBeInTheDocument();
});

test('form routes to FormBlockView and its submit calls onFormSubmit with the composed message', async () => {
  const blocks: Block[] = [
    {
      type: 'form',
      title: 'Plan your trip',
      fields: [{ name: 'destination', label: 'Destination', kind: 'text', required: true }],
    },
  ];
  const onFormSubmit = vi.fn();
  const user = userEvent.setup();
  render(<BlockRenderer blocks={blocks} onFormSubmit={onFormSubmit} />);
  expect(screen.getByText('Plan your trip')).toBeInTheDocument();
  await user.type(screen.getByLabelText('Destination', { exact: false }), 'Tokyo');
  await user.click(screen.getByRole('button', { name: 'Submit' }));
  expect(onFormSubmit).toHaveBeenCalledWith('Destination: Tokyo');
});

test('an unknown block type renders nothing', () => {
  const blocks = [{ type: 'unknown_future_block', anything: true }] as unknown as Block[];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.textContent).toBe('');
  expect(container.querySelectorAll('*').length).toBeLessThanOrEqual(1); // at most the empty wrapper div
});

test('an empty blocks array renders nothing', () => {
  const { container } = render(<BlockRenderer blocks={[]} />);
  expect(container).toBeEmptyDOMElement();
});
