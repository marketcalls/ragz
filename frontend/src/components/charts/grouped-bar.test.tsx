import { render, screen } from '@testing-library/react';

import { mockResponsiveContainerSize } from '@/test/chart-test-utils';

import { GroupedBar } from './grouped-bar';

mockResponsiveContainerSize();

const data = [
  { day: 'Mon', workspaceA: 3, workspaceB: 5 },
  { day: 'Tue', workspaceA: 4, workspaceB: 2 },
];

test('renders one bar series per key, side by side (not stacked)', () => {
  const { container } = render(<GroupedBar data={data} categoryKey="day" keys={['workspaceA', 'workspaceB']} />);
  expect(container.querySelectorAll('.recharts-bar')).toHaveLength(2);
  // Grouped bars for the same category sit at different x offsets; stacked
  // bars for the same category would share one. Two categories x two keys =
  // 4 distinct rectangles either way, so assert on x position spread instead.
  const rects = Array.from(container.querySelectorAll('.recharts-bar-rectangle path'));
  expect(rects).toHaveLength(4);
  const xs = new Set(rects.map((r) => r.getAttribute('x')));
  expect(xs.size).toBeGreaterThan(2); // stacked bars would collapse to 2 distinct x values
});

test('honors categoryKey to label the x axis', () => {
  render(<GroupedBar data={data} categoryKey="day" keys={['workspaceA']} />);
  expect(screen.getByText('Mon')).toBeInTheDocument();
  expect(screen.getByText('Tue')).toBeInTheDocument();
});

test('renders a No data state for empty data', () => {
  render(<GroupedBar data={[]} categoryKey="day" keys={['workspaceA']} />);
  expect(screen.getByText('No data')).toBeInTheDocument();
});

test('renders a No data state for empty keys', () => {
  render(<GroupedBar data={data} categoryKey="day" keys={[]} />);
  expect(screen.getByText('No data')).toBeInTheDocument();
});
