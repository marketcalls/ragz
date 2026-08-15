import type { AgentStepInfo } from '@/api/types';
import { Spinner } from '@/components/ui/spinner';

import { AssistantMessage } from './assistant-message';
import type { ChatStreamState } from './use-chat-stream';
import { UserMessage } from './user-message';

// Phase 3 (Task 10): subdued one-line hint of what the agent loop is doing,
// shown under the spinner once at least one agent_step frame has landed.
const STEP_LABELS: Record<string, (q: string) => string> = {
  search: (q) => `Searching: ${q}`,
  search_by_metadata: (q) => `Filtering by metadata: ${q}`,
  get_document: () => 'Reading a document…',
  web_search: (q) => `Searching the web: ${q}`,
};

function stepLabel(step: AgentStepInfo): string {
  return (STEP_LABELS[step.tool] ?? ((q: string) => `Working: ${q}`))(step.query);
}

export function StreamingMessage({ stream }: { stream: ChatStreamState }) {
  const lastStep = stream.agentSteps.at(-1);
  return (
    <>
      {stream.pendingUserContent ? <UserMessage content={stream.pendingUserContent} /> : null}
      {stream.status === 'retrieving' ? (
        <div>
          <Spinner label="Searching documents…" />
          {lastStep ? <p className="mt-1 text-[12px] text-muted">{stepLabel(lastStep)}</p> : null}
        </div>
      ) : null}
      {stream.status === 'streaming' || stream.status === 'done' ? (
        <AssistantMessage
          content={stream.text}
          sources={stream.sources}
          blocks={stream.blocks}
          noAnswer={stream.noAnswer}
          grounding={stream.grounding}
          validationFailed={stream.validationFailed}
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
