import { render, screen } from '@testing-library/react';

import { mockResponsiveContainerSize } from '@/test/chart-test-utils';

import { StackedArea } from './stacked-area';

mockResponsiveContainerSize();

const data = [
  { day: 'Mon', gpt4o: 12, claude: 8 },
  { day: 'Tue', gpt4o: 18, claude: 10 },
];

test('renders one stacked area per key', () => {
  const { container } = render(<StackedArea data={data} xKey="day" keys={['gpt4o', 'claude']} />);
  expect(container.querySelectorAll('.recharts-area')).toHaveLength(2);
});

test('honors xKey to label the x axis', () => {
  render(<StackedArea data={data} xKey="day" keys={['gpt4o']} />);
  expect(screen.getByText('Mon')).toBeInTheDocument();
  expect(screen.getByText('Tue')).toBeInTheDocument();
});

test('renders a No data state for empty data', () => {
  render(<StackedArea data={[]} xKey="day" keys={['gpt4o']} />);
  expect(screen.getByText('No data')).toBeInTheDocument();
});

test('renders a No data state for empty keys', () => {
  render(<StackedArea data={data} xKey="day" keys={[]} />);
  expect(screen.getByText('No data')).toBeInTheDocument();
});
