import { render, screen } from '@testing-library/react';

import { mockResponsiveContainerSize } from '@/test/chart-test-utils';

import { RadarChart } from './radar-chart';

mockResponsiveContainerSize();

const data = [
  { metric: 'faithfulness', workspaceA: 0.9, workspaceB: 0.7 },
  { metric: 'relevance', workspaceA: 0.8, workspaceB: 0.6 },
  { metric: 'latency', workspaceA: 0.6, workspaceB: 0.9 },
];

test('renders one radar series per key', () => {
  const { container } = render(
    <RadarChart data={data} categoryKey="metric" keys={['workspaceA', 'workspaceB']} />,
  );
  expect(container.querySelectorAll('.recharts-radar')).toHaveLength(2);
});

test('honors categoryKey to label the angle axis', () => {
  render(<RadarChart data={data} categoryKey="metric" keys={['workspaceA']} />);
  expect(screen.getByText('faithfulness')).toBeInTheDocument();
  expect(screen.getByText('latency')).toBeInTheDocument();
});

test('renders a No data state for empty data', () => {
  render(<RadarChart data={[]} categoryKey="metric" keys={['workspaceA']} />);
  expect(screen.getByText('No data')).toBeInTheDocument();
});

test('renders a No data state for empty keys', () => {
  render(<RadarChart data={data} categoryKey="metric" keys={[]} />);
  expect(screen.getByText('No data')).toBeInTheDocument();
});
