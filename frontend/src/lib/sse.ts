export interface SseMessage {
  event: string;
  data: string;
}

/** Incremental text/event-stream parser (WHATWG SSE grammar subset we consume). */
export function createSseParser(onMessage: (message: SseMessage) => void): {
  feed(chunk: string): void;
  flush(): void;
} {
  let buffer = '';
  let event = 'message';
  let dataLines: string[] = [];

  const dispatch = (): void => {
    if (dataLines.length > 0) onMessage({ event, data: dataLines.join('\n') });
    event = 'message';
    dataLines = [];
  };

  const processLine = (line: string): void => {
    if (line === '') {
      dispatch();
      return;
    }
    if (line.startsWith(':')) return; // comment / keepalive
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') event = value;
    else if (field === 'data') dataLines.push(value);
    // id / retry: not used by our protocol
  };

  return {
    feed(chunk: string): void {
      buffer += chunk;
      for (;;) {
        const match = /\r\n|\n|\r/.exec(buffer);
        if (!match) break;
        // A lone \r at the very end might be half of \r\n — wait for more input.
        if (match[0] === '\r' && match.index === buffer.length - 1) break;
        const line = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        processLine(line);
      }
    },
    flush(): void {
      if (buffer !== '') {
        processLine(buffer.replace(/\r$/, ''));
        buffer = '';
      }
      dispatch();
    },
  };
}
