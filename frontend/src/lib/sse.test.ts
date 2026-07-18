import { createSseParser, type SseMessage } from './sse';

function collect(): { messages: SseMessage[]; parser: ReturnType<typeof createSseParser> } {
  const messages: SseMessage[] = [];
  const parser = createSseParser((m) => messages.push(m));
  return { messages, parser };
}

test('parses a complete event', () => {
  const { messages, parser } = collect();
  parser.feed('event: token\ndata: {"delta":"Hi"}\n\n');
  expect(messages).toEqual([{ event: 'token', data: '{"delta":"Hi"}' }]);
});

test('reassembles events split across arbitrary chunk boundaries', () => {
  const { messages, parser } = collect();
  for (const chunk of ['eve', 'nt: tok', 'en\nda', 'ta: {"delta":"a', 'b"}\n', '\n']) {
    parser.feed(chunk);
  }
  expect(messages).toEqual([{ event: 'token', data: '{"delta":"ab"}' }]);
});

test('multiple events per chunk, CRLF endings, comments ignored', () => {
  const { messages, parser } = collect();
  parser.feed(': keepalive\r\nevent: a\r\ndata: 1\r\n\r\nevent: b\r\ndata: 2\r\n\r\n');
  expect(messages).toEqual([
    { event: 'a', data: '1' },
    { event: 'b', data: '2' },
  ]);
});

test('multi-line data joins with newline; default event name is message', () => {
  const { messages, parser } = collect();
  parser.feed('data: line1\ndata: line2\n\n');
  expect(messages).toEqual([{ event: 'message', data: 'line1\nline2' }]);
});

test('event name resets after dispatch', () => {
  const { messages, parser } = collect();
  parser.feed('event: token\ndata: 1\n\ndata: 2\n\n');
  expect(messages[1]).toEqual({ event: 'message', data: '2' });
});

test('flush dispatches a trailing unterminated event', () => {
  const { messages, parser } = collect();
  parser.feed('event: done\ndata: {"ok":true}');
  expect(messages).toEqual([]);
  parser.flush();
  expect(messages).toEqual([{ event: 'done', data: '{"ok":true}' }]);
});
