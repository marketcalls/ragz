import { render, screen } from '@testing-library/react';

import { readChartPalette } from '@/lib/chart-palette';
import { mockResponsiveContainerSize } from '@/test/chart-test-utils';

import { RadialGauge } from './radial-gauge';

mockResponsiveContainerSize();

test('renders the value as a centered number with an optional label', () => {
  render(<RadialGauge value={42} max={100} label="Queries used" />);
  expect(screen.getByText('42')).toBeInTheDocument();
  expect(screen.getByText('Queries used')).toBeInTheDocument();
});

test('honors valueLabel to override the displayed number', () => {
  render(<RadialGauge value={42} max={100} valueLabel="42%" />);
  expect(screen.getByText('42%')).toBeInTheDocument();
  expect(screen.queryByText('42')).not.toBeInTheDocument();
});

test('renders a No data state when max is not positive', () => {
  render(<RadialGauge value={5} max={0} />);
  expect(screen.getByText('No data')).toBeInTheDocument();
});

test('renders a No data state for non-finite values', () => {
  render(<RadialGauge value={Number.NaN} max={100} />);
  expect(screen.getByText('No data')).toBeInTheDocument();
});

test('shifts to the danger color near the max threshold', () => {
  const p = readChartPalette();
  const { container } = render(<RadialGauge value={95} max={100} />);
  expect(container.querySelector('.recharts-radial-bar-sector')).toHaveAttribute('fill', p.danger);
});

test('shifts to the warning color mid-range', () => {
  const p = readChartPalette();
  const { container } = render(<RadialGauge value={75} max={100} />);
  expect(container.querySelector('.recharts-radial-bar-sector')).toHaveAttribute('fill', p.warning);
});

test('stays accent-colored well below threshold', () => {
  const p = readChartPalette();
  const { container } = render(<RadialGauge value={10} max={100} />);
  expect(container.querySelector('.recharts-radial-bar-sector')).toHaveAttribute('fill', p.accent);
});
