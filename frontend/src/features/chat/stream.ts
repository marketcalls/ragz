import { authFetch } from '@/api/client';
import type { AgentStepInfo, Block, CitationRef, DoneInfo, SourceRef } from '@/api/types';
import { createSseParser, type SseMessage } from '@/lib/sse';

export type ChatSseEvent =
  | { type: 'retrieval_started' }
  | { type: 'sources'; sources: SourceRef[] }
  | { type: 'token'; delta: string }
  | { type: 'citations'; citations: CitationRef[] }
  // Phase 2 (in-chat generative UI): mirrors `citations`/`sources` -- emitted
  // at most once per turn, only when the best-effort visualize step
  // (backend chat/blocks_emit.py) actually produced blocks.
  | { type: 'blocks'; blocks: Block[] }
  | { type: 'done'; done: DoneInfo }
  | { type: 'error'; detail: string }
  | { type: 'agent_step'; step: AgentStepInfo };

function toEvent(message: SseMessage): ChatSseEvent {
  try {
    const data: unknown = JSON.parse(message.data);
    switch (message.event) {
      case 'retrieval_started':
        return { type: 'retrieval_started' };
      case 'sources':
        return { type: 'sources', sources: (data as { sources: SourceRef[] }).sources };
      case 'token':
        return { type: 'token', delta: (data as { delta: string }).delta };
      case 'citations':
        return { type: 'citations', citations: (data as { citations: CitationRef[] }).citations };
      case 'blocks':
        return { type: 'blocks', blocks: (data as { blocks: Block[] }).blocks };
      case 'done':
        return { type: 'done', done: data as DoneInfo };
      case 'error':
        return { type: 'error', detail: (data as { detail: string }).detail };
      case 'agent_step':
        return { type: 'agent_step', step: data as AgentStepInfo };
      default:
        return { type: 'error', detail: `unknown event: ${message.event}` };
    }
  } catch {
    return { type: 'error', detail: `malformed ${message.event} frame` };
  }
}

export async function streamChatSse(
  url: string,
  body: unknown,
  onEvent: (event: ChatSseEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await authFetch(
      // Absolute origin, not the bare path: Node's fetch/Request (used under
      // jsdom in tests) rejects relative URLs outright — see api/client.ts.
      new Request(new URL(url, window.location.origin), {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
        body: JSON.stringify(body),
        credentials: 'include',
        signal,
      }),
    );
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') return;
    onEvent({ type: 'error', detail: 'network error' });
    return;
  }
  if (!res.ok || !res.body) {
    let detail = `request failed (${res.status})`;
    try {
      const problem = (await res.json()) as { detail?: string };
      if (problem.detail) detail = problem.detail;
    } catch {
      /* keep default detail */
    }
    onEvent({ type: 'error', detail });
    return;
  }

  const parser = createSseParser((m) => onEvent(toEvent(m)));
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.feed(decoder.decode(value, { stream: true }));
    }
    parser.feed(decoder.decode());
    parser.flush();
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') return;
    onEvent({ type: 'error', detail: 'stream interrupted' });
  }
}
