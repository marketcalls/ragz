import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { EffortSelector } from './effort-selector';

test('renders all four tiers with the given value selected', () => {
  render(<EffortSelector value="medium" onChange={() => {}} />);
  const select = screen.getByLabelText('Reasoning effort') as HTMLSelectElement;
  expect(select.value).toBe('medium');
  expect(screen.getAllByRole('option').map((o) => (o as HTMLOptionElement).value)).toEqual([
    'off', 'low', 'medium', 'high',
  ]);
});

test('calls onChange with the picked tier', async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  render(<EffortSelector value="off" onChange={onChange} />);
  await user.selectOptions(screen.getByLabelText('Reasoning effort'), 'high');
  expect(onChange).toHaveBeenCalledWith('high');
});
