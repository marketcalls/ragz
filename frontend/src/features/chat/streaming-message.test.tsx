import { render, screen } from '@testing-library/react';

import { StreamingMessage } from './streaming-message';
import type { ChatStreamState } from './use-chat-stream';

const base: ChatStreamState = {
  status: 'idle',
  text: '',
  sources: [],
  citations: [],
  blocks: [],
  noAnswer: false,
  grounding: 'documents',
  validationFailed: false,
  errorDetail: null,
  pendingUserContent: null,
  doneMessageId: null,
  agentSteps: [],
};

test('a pre-stream error (no token ever streamed) renders as a visible alert', () => {
  // New-thread flow: send failed before any token (e.g. dead session -> 401),
  // so text is empty and only the optimistic user message exists. "Nothing
  // happens" must never be silent.
  render(
    <StreamingMessage
      stream={{
        ...base,
        status: 'error',
        errorDetail: 'invalid refresh token',
        pendingUserContent: 'What is our leave policy?',
      }}
    />,
  );
  expect(screen.getByText('What is our leave policy?')).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('invalid refresh token');
});

test('an error without detail still renders a fallback alert message', () => {
  render(<StreamingMessage stream={{ ...base, status: 'error' }} />);
  expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong.');
});

test('a retrieving state with agentSteps renders the latest step as a progress line', () => {
  render(
    <StreamingMessage
      stream={{
        ...base,
        status: 'retrieving',
        agentSteps: [
          { n: 1, tool: 'search', query: 'muster point' },
          { n: 2, tool: 'search', query: 'evacuation plan' },
        ],
      }}
    />,
  );
  // Only the LATEST step is shown, not every step so far.
  expect(screen.getByText('Searching: evacuation plan')).toBeInTheDocument();
  expect(screen.queryByText('Searching: muster point')).not.toBeInTheDocument();
});

test('a retrieving state with no agentSteps shows the spinner but no progress line', () => {
  render(<StreamingMessage stream={{ ...base, status: 'retrieving' }} />);
  expect(screen.getByText('Searching documents…')).toBeInTheDocument();
});

test('blocks captured off the live SSE frame render via BlockRenderer once streaming', () => {
  render(
    <StreamingMessage
      stream={{
        ...base,
        status: 'streaming',
        text: 'Revenue grew this quarter.',
        blocks: [{ type: 'callout', tone: 'success', title: 'Up 12%', body: 'Quarter over quarter.' }],
      }}
    />,
  );
  expect(screen.getByText('Up 12%')).toBeInTheDocument();
  expect(screen.getByText('Quarter over quarter.')).toBeInTheDocument();
});
