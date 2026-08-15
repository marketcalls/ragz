import { render, screen } from '@testing-library/react';

import { mockResponsiveContainerSize } from '@/test/chart-test-utils';

import { Sparkline } from './sparkline';

mockResponsiveContainerSize();

test('defaults to a line sparkline', () => {
  const { container } = render(<Sparkline data={[1, 4, 2, 5, 3]} />);
  expect(container.querySelectorAll('.recharts-line')).toHaveLength(1);
});

test('honors variant="bar"', () => {
  const { container } = render(<Sparkline data={[1, 4, 2]} variant="bar" />);
  expect(container.querySelectorAll('.recharts-bar-rectangle').length).toBeGreaterThan(0);
  expect(container.querySelectorAll('.recharts-line')).toHaveLength(0);
});

test('honors variant="area"', () => {
  const { container } = render(<Sparkline data={[1, 4, 2]} variant="area" />);
  expect(container.querySelectorAll('.recharts-area')).toHaveLength(1);
  expect(container.querySelectorAll('.recharts-line')).toHaveLength(0);
});

test('renders a No data state for empty data', () => {
  render(<Sparkline data={[]} />);
  expect(screen.getByText('No data')).toBeInTheDocument();
});

test('has no axes, grid, legend, or tooltip chrome', () => {
  const { container } = render(<Sparkline data={[1, 2, 3]} />);
  expect(container.querySelector('.recharts-cartesian-axis')).not.toBeInTheDocument();
  expect(container.querySelector('.recharts-cartesian-grid')).not.toBeInTheDocument();
  expect(container.querySelector('.recharts-legend-wrapper')).not.toBeInTheDocument();
});
