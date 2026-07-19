import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';

import type { MetadataFieldOut } from '@/api/types';

import { matchesMetadataFilter, MetadataFilterBar, type MetadataFilterValue } from './metadata-filter-bar';

const fields: MetadataFieldOut[] = [
  {
    id: 'f1',
    name: 'doc_type',
    label: 'Document Type',
    field_type: 'select',
    options: ['policy', 'manual'],
    position: 0,
  },
  { id: 'f2', name: 'department', label: 'Department', field_type: 'text', options: null, position: 1 },
];

const docs = [
  { meta: { doc_type: 'policy', department: 'Finance' } },
  { meta: { doc_type: 'manual', department: 'Ops' } },
  { meta: null },
];

function Harness({ onFilterChange }: { onFilterChange: (v: MetadataFilterValue) => void }) {
  const [value, setValue] = useState<MetadataFilterValue>({});
  return (
    <MetadataFilterBar
      fields={fields}
      value={value}
      onChange={(next) => {
        setValue(next);
        onFilterChange(next);
      }}
    />
  );
}

test('renders a select dropdown with the field options and a text input for the text field', () => {
  render(<Harness onFilterChange={vi.fn()} />);
  expect(screen.getByLabelText('Document Type').tagName).toBe('SELECT');
  expect(screen.getByRole('option', { name: 'policy' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'manual' })).toBeInTheDocument();
  expect(screen.getByLabelText('Department').tagName).toBe('INPUT');
});

test('picking a select value narrows a doc list to matches via matchesMetadataFilter', async () => {
  const user = userEvent.setup();
  const captured: MetadataFilterValue[] = [];
  render(<Harness onFilterChange={(v) => captured.push(v)} />);

  await user.selectOptions(screen.getByLabelText('Document Type'), 'policy');

  const filters = captured[captured.length - 1]!;
  expect(filters).toStrictEqual({ doc_type: 'policy' });
  const narrowed = docs.filter((d) => matchesMetadataFilter(d.meta, fields, filters));
  expect(narrowed).toHaveLength(1);
  expect(narrowed[0]!.meta?.department).toBe('Finance');
});

test('picking "All" clears the select filter', async () => {
  const user = userEvent.setup();
  const captured: MetadataFilterValue[] = [];
  render(<Harness onFilterChange={(v) => captured.push(v)} />);

  await user.selectOptions(screen.getByLabelText('Document Type'), 'policy');
  await user.selectOptions(screen.getByLabelText('Document Type'), 'All');

  const filters = captured[captured.length - 1]!;
  expect(filters).toStrictEqual({});
  expect(docs.every((d) => matchesMetadataFilter(d.meta, fields, filters))).toBe(true);
});

test('a text filter narrows by case-insensitive substring match', () => {
  const filters = { department: 'fin' };
  const narrowed = docs.filter((d) => matchesMetadataFilter(d.meta, fields, filters));
  expect(narrowed).toHaveLength(1);
  expect(narrowed[0]!.meta?.doc_type).toBe('policy');
});

test('a date field renders "from" and "to" range inputs', () => {
  const dateFields: MetadataFieldOut[] = [
    { id: 'f3', name: 'revision_date', label: 'Revision Date', field_type: 'date', options: null, position: 0 },
  ];
  function DateHarness() {
    const [value, setValue] = useState<MetadataFilterValue>({});
    return <MetadataFilterBar fields={dateFields} value={value} onChange={setValue} />;
  }
  render(<DateHarness />);
  expect(screen.getByLabelText('Revision Date from')).toHaveAttribute('type', 'date');
  expect(screen.getByLabelText('Revision Date to')).toHaveAttribute('type', 'date');
});

test('a date range filters documents whose value falls inside the bounds', () => {
  const dateFields: MetadataFieldOut[] = [
    { id: 'f3', name: 'revision_date', label: 'Revision Date', field_type: 'date', options: null, position: 0 },
  ];
  const dateDocs = [
    { meta: { revision_date: '2026-01-15' } },
    { meta: { revision_date: '2026-06-01' } },
  ];
  const filters = { revision_date: '2026-01-01..2026-02-01' };
  const narrowed = dateDocs.filter((d) => matchesMetadataFilter(d.meta, dateFields, filters));
  expect(narrowed).toHaveLength(1);
  expect(narrowed[0]!.meta.revision_date).toBe('2026-01-15');
});

test('no active filters matches every document', () => {
  expect(docs.every((d) => matchesMetadataFilter(d.meta, fields, {}))).toBe(true);
});
