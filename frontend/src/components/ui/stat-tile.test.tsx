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
