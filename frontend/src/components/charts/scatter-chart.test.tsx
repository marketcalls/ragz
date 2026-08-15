import { render, screen } from '@testing-library/react';

import { mockResponsiveContainerSize } from '@/test/chart-test-utils';

import { ScatterChart } from './scatter-chart';

mockResponsiveContainerSize();

const data = [
  { age: 25, income: 40000 },
  { age: 35, income: 60000 },
  { age: 45, income: 80000 },
];

test('renders one point per data row', () => {
  const { container } = render(<ScatterChart data={data} xKey="age" yKey="income" />);
  expect(container.querySelectorAll('.recharts-scatter-symbol')).toHaveLength(3);
});

test('renders a No data state for empty data', () => {
  render(<ScatterChart data={[]} xKey="age" yKey="income" />);
  expect(screen.getByText('No data')).toBeInTheDocument();
});

test('honors an optional zKey (bubble size) without changing point count', () => {
  const { container } = render(<ScatterChart data={data} xKey="age" yKey="income" zKey="income" />);
  expect(container.querySelectorAll('.recharts-scatter-symbol')).toHaveLength(3);
});
