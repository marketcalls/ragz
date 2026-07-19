type Row = Record<string, string | null>;

// Cells whose first character could be interpreted as a formula lead-in by
// spreadsheet applications (Excel, Google Sheets, LibreOffice) are prefixed
// with a single quote to force text interpretation. This must run before the
// quote/comma/newline escaping below, since quoting a raw formula string
// alone does not neutralize it.
const FORMULA_LEAD_IN = /^[=+\-@\t\r]/;

function escapeCell(value: string | null): string {
  const raw = value ?? '';
  const s = FORMULA_LEAD_IN.test(raw) ? `'${raw}` : raw;
  return /[",\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
}

export function toCsv(rows: Row[], columns: string[]): string {
  const lines = [columns.join(',')];
  for (const row of rows) {
    lines.push(columns.map((c) => escapeCell(row[c] ?? null)).join(','));
  }
  return lines.join('\n');
}

export function downloadCsv(filename: string, csv: string): void {
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
