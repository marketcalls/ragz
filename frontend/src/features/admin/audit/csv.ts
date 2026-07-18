type Row = Record<string, string | null>;

function escapeCell(value: string | null): string {
  const s = value ?? '';
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
