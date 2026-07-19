import { toCsv } from './csv';

test('quotes cells containing commas', () => {
  expect(toCsv([{ a: 'x,y' }], ['a'])).toBe('a\n"x,y"');
});

test('quotes cells containing quotes and doubles embedded quotes', () => {
  expect(toCsv([{ a: 'say "hi"' }], ['a'])).toBe('a\n"say ""hi"""');
});

test('quotes cells containing newlines', () => {
  expect(toCsv([{ a: 'line1\nline2' }], ['a'])).toBe('a\n"line1\nline2"');
});

test('leaves plain cells unquoted', () => {
  expect(toCsv([{ a: 'plain' }], ['a'])).toBe('a\nplain');
});

test('preserves column order regardless of row key order', () => {
  const rows = [{ b: '2', a: '1' }];
  expect(toCsv(rows, ['a', 'b'])).toBe('a,b\n1,2');
});

test('renders null cells as empty', () => {
  expect(toCsv([{ a: null }], ['a'])).toBe('a\n');
});

test('renders multiple rows joined by newline', () => {
  const rows = [{ a: '1' }, { a: '2' }];
  expect(toCsv(rows, ['a'])).toBe('a\n1\n2');
});

test('neutralizes cells starting with = to prevent formula injection', () => {
  expect(toCsv([{ a: '=1+1' }], ['a'])).toBe("a\n'=1+1");
});

test('neutralizes cells starting with + to prevent formula injection', () => {
  expect(toCsv([{ a: '+1+1' }], ['a'])).toBe("a\n'+1+1");
});

test('neutralizes cells starting with - to prevent formula injection', () => {
  expect(toCsv([{ a: '-1+1' }], ['a'])).toBe("a\n'-1+1");
});

test('neutralizes cells starting with @ to prevent formula injection', () => {
  expect(toCsv([{ a: '@SUM(1)' }], ['a'])).toBe("a\n'@SUM(1)");
});

test('neutralizes cells starting with tab to prevent formula injection', () => {
  expect(toCsv([{ a: '\t=1+1' }], ['a'])).toBe("a\n'\t=1+1");
});

test('neutralizes cells starting with carriage return to prevent formula injection', () => {
  expect(toCsv([{ a: '\r=1+1' }], ['a'])).toBe("a\n'\r=1+1");
});

test('neutralizes and quotes a formula-injection cell that also contains a comma', () => {
  expect(toCsv([{ a: '=1+1,2' }], ['a'])).toBe('a\n"\'=1+1,2"');
});

test('does not treat an interior = sign as formula injection', () => {
  expect(toCsv([{ a: 'x=1' }], ['a'])).toBe('a\nx=1');
});

test('documented: negative-looking data (leading -) is still prefixed but remains human-readable', () => {
  expect(toCsv([{ a: '-' }], ['a'])).toBe("a\n'-");
  expect(toCsv([{ a: '-5' }], ['a'])).toBe("a\n'-5");
});
