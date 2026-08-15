import { render, screen, waitFor } from '@testing-library/react';

import { mockResponsiveContainerSize } from '@/test/chart-test-utils';

import { DonutChart } from './donut-chart';

mockResponsiveContainerSize();

const data = [
  { name: 'gpt-4o', value: 40 },
  { name: 'claude', value: 35 },
  { name: 'llama', value: 25 },
];

test('renders one pie sector per data point', () => {
  const { container } = render(<DonutChart data={data} />);
  expect(container.querySelectorAll('.recharts-pie-sector')).toHaveLength(data.length);
});

// Recharts v3 registers a Pie's legend payload into its internal store on an
// effect after the first paint, so the legend list is empty on the
// synchronous render pass — wait for it rather than asserting immediately.
test('renders a legend entry per data point', async () => {
  const { container } = render(<DonutChart data={data} />);
  await waitFor(() => {
    expect(container.querySelectorAll('.recharts-legend-item')).toHaveLength(data.length);
  });
  expect(screen.getByText('gpt-4o')).toBeInTheDocument();
  expect(screen.getByText('llama')).toBeInTheDocument();
});

test('renders a No data state for empty data', () => {
  render(<DonutChart data={[]} />);
  expect(screen.getByText('No data')).toBeInTheDocument();
});

test('omits the center total when centerLabel is not provided', () => {
  render(<DonutChart data={data} />);
  expect(screen.queryByText('100')).not.toBeInTheDocument();
});

test('shows a summed center total and caption when centerLabel is provided', () => {
  render(<DonutChart data={data} centerLabel="Total tokens" />);
  expect(screen.getByText('100')).toBeInTheDocument();
  expect(screen.getByText('Total tokens')).toBeInTheDocument();
});
