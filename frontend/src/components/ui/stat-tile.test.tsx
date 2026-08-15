import { render, screen } from '@testing-library/react';

import { StatTile } from './stat-tile';

test('renders label and value', () => {
  render(<StatTile label="Total Queries" value="1,204" />);
  expect(screen.getByText('Total Queries')).toBeInTheDocument();
  expect(screen.getByText('1,204')).toBeInTheDocument();
});

test('renders optional sub text when provided', () => {
  render(<StatTile label="Total Queries" value="1,204" sub="+12% vs last week" />);
  expect(screen.getByText('+12% vs last week')).toBeInTheDocument();
});

test('omits sub text when not provided', () => {
  render(<StatTile label="Total Queries" value="1,204" />);
  expect(screen.queryByText('+12% vs last week')).not.toBeInTheDocument();
});

test('uses theme tokens, not raw palette classes', () => {
  render(<StatTile label="Total Queries" value="1,204" />);
  const value = screen.getByText('1,204');
  expect(value.className).toContain('text-ink');
});

test('omits the sparkline when the prop is not provided (backward compatible)', () => {
  const { container } = render(<StatTile label="Total Queries" value="1,204" />);
  expect(container.querySelector('.recharts-responsive-container')).not.toBeInTheDocument();
});

test('renders a sparkline under the value when the sparkline prop is provided', () => {
  const { container } = render(
    <StatTile label="Total Queries" value="1,204" sparkline={[1, 4, 2, 6, 3]} />,
  );
  expect(container.querySelector('.recharts-responsive-container')).toBeInTheDocument();
});
