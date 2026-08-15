import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { FormBlock as FormBlockT } from '@/api/types';

import { FormBlockView } from './form-block';

test('a "text" field renders as a text input with its label and placeholder', () => {
  const block: FormBlockT = {
    type: 'form',
    fields: [{ name: 'destination', label: 'Destination', kind: 'text', placeholder: 'Tokyo', required: false }],
  };
  render(<FormBlockView block={block} />);
  const input = screen.getByLabelText('Destination');
  expect(input).toHaveAttribute('type', 'text');
  expect(input).toHaveAttribute('placeholder', 'Tokyo');
});

test('a "number" field renders as a number input', () => {
  const block: FormBlockT = {
    type: 'form',
    fields: [{ name: 'travellers', label: 'Number of travellers', kind: 'number', required: false }],
  };
  render(<FormBlockView block={block} />);
  expect(screen.getByLabelText('Number of travellers')).toHaveAttribute('type', 'number');
});

test('a "select" field renders a <select> with its options', () => {
  const block: FormBlockT = {
    type: 'form',
    fields: [
      {
        name: 'style',
        label: 'Trip style',
        kind: 'select',
        options: ['Cultural', 'Adventure', 'Relaxation'],
        required: false,
      },
    ],
  };
  render(<FormBlockView block={block} />);
  const select = screen.getByLabelText('Trip style');
  expect(select.tagName).toBe('SELECT');
  expect(screen.getByRole('option', { name: 'Cultural' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Adventure' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Relaxation' })).toBeInTheDocument();
});

test('a "multiselect" field renders one checkable chip per option', async () => {
  const block: FormBlockT = {
    type: 'form',
    fields: [
      {
        name: 'destinations',
        label: 'Destinations',
        kind: 'multiselect',
        options: ['Tokyo', 'Kyoto', 'Osaka'],
        required: false,
      },
    ],
  };
  const user = userEvent.setup();
  render(<FormBlockView block={block} />);
  const tokyo = screen.getByRole('button', { name: 'Tokyo' });
  expect(tokyo).toHaveAttribute('aria-pressed', 'false');
  await user.click(tokyo);
  expect(tokyo).toHaveAttribute('aria-pressed', 'true');
});

test('required fields block submit until filled', async () => {
  const block: FormBlockT = {
    type: 'form',
    fields: [{ name: 'destination', label: 'Destination', kind: 'text', required: true }],
  };
  const onSubmit = vi.fn();
  const user = userEvent.setup();
  render(<FormBlockView block={block} onSubmit={onSubmit} />);
  const submitButton = screen.getByRole('button', { name: 'Submit' });
  expect(submitButton).toBeDisabled();
  // Required fields append a " *" marker to the label text, so match loosely.
  await user.type(screen.getByLabelText('Destination', { exact: false }), 'Tokyo');
  expect(submitButton).toBeEnabled();
  await user.click(submitButton);
  expect(onSubmit).toHaveBeenCalledWith('Destination: Tokyo');
});

test('submit composes a "label: value" message per field, comma-joining multiselect values', async () => {
  const block: FormBlockT = {
    type: 'form',
    fields: [
      { name: 'travellers', label: 'Number of travellers', kind: 'number', required: true },
      { name: 'style', label: 'Trip style', kind: 'select', options: ['Cultural', 'Adventure'], required: true },
      {
        name: 'destinations',
        label: 'Destinations',
        kind: 'multiselect',
        options: ['Tokyo', 'Kyoto', 'Osaka'],
        required: true,
      },
    ],
    submit_label: 'Create itinerary',
  };
  const onSubmit = vi.fn();
  const user = userEvent.setup();
  render(<FormBlockView block={block} onSubmit={onSubmit} />);

  await user.type(screen.getByLabelText('Number of travellers', { exact: false }), '2');
  await user.selectOptions(screen.getByLabelText('Trip style', { exact: false }), 'Cultural');
  await user.click(screen.getByRole('button', { name: 'Tokyo' }));
  await user.click(screen.getByRole('button', { name: 'Kyoto' }));

  const submitButton = screen.getByRole('button', { name: 'Create itinerary' });
  expect(submitButton).toBeEnabled();
  await user.click(submitButton);

  expect(onSubmit).toHaveBeenCalledWith(
    'Number of travellers: 2\nTrip style: Cultural\nDestinations: Tokyo, Kyoto',
  );
});

test('an optional field left empty is omitted from the composed message', async () => {
  const block: FormBlockT = {
    type: 'form',
    fields: [
      { name: 'destination', label: 'Destination', kind: 'text', required: true },
      { name: 'notes', label: 'Notes', kind: 'text', required: false },
    ],
  };
  const onSubmit = vi.fn();
  const user = userEvent.setup();
  render(<FormBlockView block={block} onSubmit={onSubmit} />);
  await user.type(screen.getByLabelText('Destination', { exact: false }), 'Kyoto');
  await user.click(screen.getByRole('button', { name: 'Submit' }));
  expect(onSubmit).toHaveBeenCalledWith('Destination: Kyoto');
});

test('an empty form (no fields) renders safely with no field controls', () => {
  const block: FormBlockT = { type: 'form', title: 'Empty', fields: [] };
  const { container } = render(<FormBlockView block={block} />);
  expect(screen.getByText('Empty')).toBeInTheDocument();
  expect(container.querySelectorAll('input, select, [role="button"]')).toHaveLength(0);
});

test('title and description render when present', () => {
  const block: FormBlockT = {
    type: 'form',
    title: 'Plan your trip',
    description: 'Tell us a bit more.',
    fields: [],
  };
  render(<FormBlockView block={block} />);
  expect(screen.getByText('Plan your trip')).toBeInTheDocument();
  expect(screen.getByText('Tell us a bit more.')).toBeInTheDocument();
});

test('without an onSubmit callback the submit button stays disabled (defensive no-op)', async () => {
  const block: FormBlockT = {
    type: 'form',
    fields: [{ name: 'destination', label: 'Destination', kind: 'text', required: false }],
  };
  const user = userEvent.setup();
  render(<FormBlockView block={block} />);
  const submitButton = screen.getByRole('button', { name: 'Submit' });
  expect(submitButton).toBeDisabled();
  await user.type(screen.getByLabelText('Destination'), 'Osaka');
  expect(submitButton).toBeDisabled();
});
