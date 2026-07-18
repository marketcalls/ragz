import { Spinner } from '@/components/ui/spinner';

import { AssistantMessage } from './assistant-message';
import type { ChatStreamState } from './use-chat-stream';
import { UserMessage } from './user-message';

export function StreamingMessage({ stream }: { stream: ChatStreamState }) {
  return (
    <>
      {stream.pendingUserContent ? <UserMessage content={stream.pendingUserContent} /> : null}
      {stream.status === 'retrieving' ? <Spinner label="Searching documents…" /> : null}
      {stream.status === 'streaming' || stream.status === 'done' ? (
        <AssistantMessage
          content={stream.text}
          sources={stream.sources}
          noAnswer={stream.noAnswer}
        />
      ) : null}
      {stream.status === 'error' ? (
        <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-[13px] text-danger">
          {stream.errorDetail ?? 'Something went wrong.'}
        </p>
      ) : null}
    </>
  );
}
