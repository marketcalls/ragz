import { render, screen, waitFor } from '@testing-library/react';
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

test('chart block renders block.title and block.subtitle in the card header', () => {
  const blocks: Block[] = [
    {
      type: 'chart',
      chart: 'donut',
      title: 'Tokens by model',
      subtitle: 'Last 7 days',
      category_key: 'model',
      keys: ['tokens'],
      data: [
        { model: 'gpt-4o', tokens: 40 },
        { model: 'claude', tokens: 60 },
      ],
    },
  ];
  render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByText('Tokens by model')).toBeInTheDocument();
  expect(screen.getByText('Last 7 days')).toBeInTheDocument();
});

test('donut chart legend shows each data point’s name AND value', async () => {
  const blocks: Block[] = [
    {
      type: 'chart',
      chart: 'donut',
      category_key: 'model',
      keys: ['tokens'],
      data: [
        { model: 'gpt-4o', tokens: 40 },
        { model: 'claude', tokens: 60 },
      ],
    },
  ];
  render(<BlockRenderer blocks={blocks} />);
  // Recharts v3 populates the legend payload on a post-paint effect.
  await waitFor(() => {
    expect(screen.getByText('gpt-4o')).toBeInTheDocument();
  });
  expect(screen.getByText('claude')).toBeInTheDocument();
  expect(screen.getByText('40')).toBeInTheDocument();
  expect(screen.getByText('60')).toBeInTheDocument();
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

test('chart block (scatter) renders one point per data row', () => {
  const blocks: Block[] = [
    {
      type: 'chart',
      chart: 'scatter',
      x_key: 'age',
      keys: ['income'],
      data: [
        { age: 25, income: 40000 },
        { age: 35, income: 60000 },
        { age: 45, income: 80000 },
      ],
    },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelectorAll('.recharts-scatter-symbol')).toHaveLength(3);
});

test('chart block (scatter) with a missing x_key renders nothing', () => {
  const blocks: Block[] = [
    { type: 'chart', chart: 'scatter', keys: ['income'], data: [{ income: 1 }] },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelector('svg')).not.toBeInTheDocument();
});

test('chart block (scatter) with empty data renders nothing', () => {
  const blocks: Block[] = [
    { type: 'chart', chart: 'scatter', x_key: 'age', keys: ['income'], data: [] },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelector('svg')).not.toBeInTheDocument();
});

test('chart block (horizontal_bar) renders a bar per data row', () => {
  const blocks: Block[] = [
    {
      type: 'chart',
      chart: 'horizontal_bar',
      category_key: 'team',
      keys: ['score'],
      data: [
        { team: 'Alpha', score: 10 },
        { team: 'Beta', score: 20 },
      ],
    },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelectorAll('.recharts-bar-rectangle')).toHaveLength(2);
});

test('chart block (horizontal_bar) with empty data renders nothing', () => {
  const blocks: Block[] = [
    { type: 'chart', chart: 'horizontal_bar', category_key: 'team', keys: ['score'], data: [] },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelector('svg')).not.toBeInTheDocument();
});

test('chart block (sparkline) renders a line', () => {
  const blocks: Block[] = [
    {
      type: 'chart',
      chart: 'sparkline',
      keys: ['value'],
      data: [{ value: 1 }, { value: 4 }, { value: 2 }],
    },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelectorAll('.recharts-line')).toHaveLength(1);
});

test('chart block (sparkline) with a non-numeric value renders nothing', () => {
  const blocks: Block[] = [
    { type: 'chart', chart: 'sparkline', keys: ['value'], data: [{ value: 'nope' }] },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelector('svg')).not.toBeInTheDocument();
});

test('chart block (stacked_bars) renders one bar series per key', () => {
  const blocks: Block[] = [
    {
      type: 'chart',
      chart: 'stacked_bars',
      x_key: 'day',
      keys: ['gpt4o', 'claude'],
      data: [
        { day: 'Mon', gpt4o: 12, claude: 8 },
        { day: 'Tue', gpt4o: 18, claude: 10 },
      ],
    },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelectorAll('.recharts-bar')).toHaveLength(2);
});

test('chart block (stacked_bars) with empty keys renders nothing', () => {
  const blocks: Block[] = [
    { type: 'chart', chart: 'stacked_bars', x_key: 'day', keys: [], data: [{ day: 'Mon' }] },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelector('svg')).not.toBeInTheDocument();
});

test('chart block (single_stacked_bar) renders one bar segment per key', () => {
  const blocks: Block[] = [
    {
      type: 'chart',
      chart: 'single_stacked_bar',
      keys: ['approved', 'pending', 'rejected'],
      data: [{ approved: 40, pending: 30, rejected: 30 }],
    },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelectorAll('.recharts-bar')).toHaveLength(3);
});

test('chart block (single_stacked_bar) with a non-numeric segment renders nothing', () => {
  const blocks: Block[] = [
    {
      type: 'chart',
      chart: 'single_stacked_bar',
      keys: ['approved', 'pending'],
      data: [{ approved: 40, pending: 'nope' }],
    },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelector('svg')).not.toBeInTheDocument();
});

test('chart block (pie) renders one pie sector per data point', () => {
  const blocks: Block[] = [
    {
      type: 'chart',
      chart: 'pie',
      category_key: 'model',
      keys: ['tokens'],
      data: [
        { model: 'gpt-4o', tokens: 40 },
        { model: 'claude', tokens: 60 },
      ],
    },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelectorAll('.recharts-pie-sector')).toHaveLength(2);
});

test('chart block (pie) with malformed data renders nothing', () => {
  const blocks: Block[] = [{ type: 'chart', chart: 'pie', data: [{ foo: 'bar' }] }];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelector('svg')).not.toBeInTheDocument();
});

test('chart block (semi_gauge) renders a radial bar', () => {
  const blocks: Block[] = [
    { type: 'chart', chart: 'semi_gauge', keys: ['value', 'max'], data: [{ value: 42, max: 100 }] },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelector('.recharts-radial-bar-sector')).toBeInTheDocument();
});

test('chart block (semi_gauge) with a non-finite value renders nothing', () => {
  const blocks: Block[] = [
    { type: 'chart', chart: 'semi_gauge', keys: ['value', 'max'], data: [{ value: Number.NaN, max: 100 }] },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(container.querySelector('svg')).not.toBeInTheDocument();
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

test('source_refs: a web item renders an external link with headline + hostname', () => {
  const blocks: Block[] = [
    {
      type: 'source_refs',
      title: 'Sources',
      items: [
        { title: 'Kerala tourism guide', source: 'example.com', url: 'https://example.com/kerala' },
      ],
    },
  ];
  render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByText('Sources')).toBeInTheDocument();
  const link = screen.getByRole('link', { name: /Kerala tourism guide/ });
  expect(link).toHaveAttribute('href', 'https://example.com/kerala');
  expect(link).toHaveAttribute('target', '_blank');
  expect(link.getAttribute('rel')).toContain('noopener');
  expect(screen.getByText('example.com')).toBeInTheDocument();
});

test('source_refs: a doc item renders a button that calls onOpenDocument with the right chip', async () => {
  const blocks: Block[] = [
    {
      type: 'source_refs',
      items: [{ title: 'Fire safety plan', document_id: 'doc-123', page: 4 }],
    },
  ];
  const onOpenDocument = vi.fn();
  const user = userEvent.setup();
  render(<BlockRenderer blocks={blocks} onOpenDocument={onOpenDocument} />);
  const button = screen.getByRole('button', { name: /Fire safety plan/ });
  await user.click(button);
  expect(onOpenDocument).toHaveBeenCalledWith(
    expect.objectContaining({ document_id: 'doc-123', page: 4, filename: 'Fire safety plan' }),
  );
});

test('article_card (standard): renders title, a tag, and a Read Coverage link for a web url', () => {
  const blocks: Block[] = [
    {
      type: 'article_card',
      title: 'Backwaters guide',
      tags: [{ label: 'Travel', tone: 'info' }],
      url: 'https://news.example.com/kerala',
      layout: 'standard',
    },
  ];
  render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByText('Backwaters guide')).toBeInTheDocument();
  expect(screen.getByText('Travel')).toBeInTheDocument();
  const link = screen.getByRole('link', { name: /Read Coverage/ });
  expect(link).toHaveAttribute('href', 'https://news.example.com/kerala');
});

test('article_card (standard): a non-http url renders NO link (inert)', () => {
  const blocks: Block[] = [
    {
      type: 'article_card',
      title: 'Suspicious card',
      url: 'javascript:alert(1)' as unknown as string,
      layout: 'standard',
    },
  ];
  const { container } = render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByText('Suspicious card')).toBeInTheDocument();
  expect(container.querySelector('a')).not.toBeInTheDocument();
});

test('article_card (hero): renders the badge text', () => {
  const blocks: Block[] = [
    {
      type: 'article_card',
      title: 'Kerala',
      badge: 'Featured',
      layout: 'hero',
    },
  ];
  render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByText('Featured')).toBeInTheDocument();
  expect(screen.getByText('Kerala')).toBeInTheDocument();
});

test('info_card with a url renders a safe external link', () => {
  const blocks: Block[] = [
    { type: 'info_card', title: 'Q3 Summary', url: 'https://example.com/q3' },
  ];
  render(<BlockRenderer blocks={blocks} />);
  const link = screen.getByRole('link', { name: /Q3 Summary/ });
  expect(link).toHaveAttribute('href', 'https://example.com/q3');
  expect(link).toHaveAttribute('target', '_blank');
});

test('follow_ups renders one chip per item; clicking a chip calls onFollowUp with its text', async () => {
  const blocks: Block[] = [
    { type: 'follow_ups', items: ['What is the refund policy?', 'How do I cancel?'] },
  ];
  const onFollowUp = vi.fn();
  const user = userEvent.setup();
  render(<BlockRenderer blocks={blocks} onFollowUp={onFollowUp} />);
  const chip = screen.getByRole('button', { name: 'What is the refund policy?' });
  expect(screen.getByRole('button', { name: 'How do I cancel?' })).toBeInTheDocument();
  await user.click(chip);
  expect(onFollowUp).toHaveBeenCalledWith('What is the refund policy?');
});

test('follow_ups chips are disabled when onFollowUp is absent', () => {
  const blocks: Block[] = [{ type: 'follow_ups', items: ['Tell me more'] }];
  render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByRole('button', { name: 'Tell me more' })).toBeDisabled();
});

test('accordion: a section is collapsed by default and reveals inner blocks when its header is clicked; sections toggle independently', async () => {
  const blocks: Block[] = [
    {
      type: 'accordion',
      items: [
        { label: 'Section one', blocks: [{ type: 'text', markdown: 'Inner one content' }] },
        { label: 'Section two', blocks: [{ type: 'text', markdown: 'Inner two content' }] },
      ],
    },
  ];
  render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByText('Section one')).toBeInTheDocument();
  expect(screen.getByText('Section two')).toBeInTheDocument();
  expect(screen.queryByText('Inner one content')).not.toBeInTheDocument();
  expect(screen.queryByText('Inner two content')).not.toBeInTheDocument();

  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: /Section one/ }));
  expect(screen.getByText('Inner one content')).toBeInTheDocument();
  expect(screen.queryByText('Inner two content')).not.toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: /Section two/ }));
  expect(screen.getByText('Inner one content')).toBeInTheDocument();
  expect(screen.getByText('Inner two content')).toBeInTheDocument();
});

test('steps renders each item’s number, title, and details', () => {
  const blocks: Block[] = [
    {
      type: 'steps',
      items: [
        { title: 'Create an account', details: 'Sign up with your work email.' },
        { title: 'Verify your email' },
        { title: 'Invite your team', details: 'Add teammates from Settings.' },
      ],
    },
  ];
  render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByText('Create an account')).toBeInTheDocument();
  expect(screen.getByText('Sign up with your work email.')).toBeInTheDocument();
  expect(screen.getByText('Verify your email')).toBeInTheDocument();
  expect(screen.getByText('Invite your team')).toBeInTheDocument();
  expect(screen.getByText('Add teammates from Settings.')).toBeInTheDocument();
  expect(screen.getByText('1')).toBeInTheDocument();
  expect(screen.getByText('2')).toBeInTheDocument();
  expect(screen.getByText('3')).toBeInTheDocument();
});

test('buttons renders one button per item; clicking one calls onFollowUp with its message', async () => {
  const blocks: Block[] = [
    {
      type: 'buttons',
      items: [
        { label: 'Yes, proceed', message: 'Yes, please proceed.', variant: 'primary' },
        { label: 'Cancel', message: 'Cancel that.', variant: 'secondary' },
      ],
    },
  ];
  const onFollowUp = vi.fn();
  const user = userEvent.setup();
  render(<BlockRenderer blocks={blocks} onFollowUp={onFollowUp} />);
  const proceed = screen.getByRole('button', { name: 'Yes, proceed' });
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  await user.click(proceed);
  expect(onFollowUp).toHaveBeenCalledWith('Yes, please proceed.');
});

test('buttons are disabled when onFollowUp is absent', () => {
  const blocks: Block[] = [
    {
      type: 'buttons',
      items: [{ label: 'Yes, proceed', message: 'Yes, please proceed.', variant: 'primary' }],
    },
  ];
  render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByRole('button', { name: 'Yes, proceed' })).toBeDisabled();
});

test('carousel renders each slide’s inner block content', () => {
  const blocks: Block[] = [
    {
      type: 'carousel',
      items: [
        { blocks: [{ type: 'text', markdown: 'First slide text' }] },
        {
          blocks: [
            { type: 'info_card', title: 'Second slide card', body: 'Second slide body' },
          ],
        },
      ],
    },
  ];
  render(<BlockRenderer blocks={blocks} />);
  expect(screen.getByText('First slide text')).toBeInTheDocument();
  expect(screen.getByText('Second slide card')).toBeInTheDocument();
  expect(screen.getByText('Second slide body')).toBeInTheDocument();
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
