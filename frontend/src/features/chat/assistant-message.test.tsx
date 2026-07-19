import { render, screen } from '@testing-library/react';

import { AssistantMessage } from './assistant-message';

const BANNER_TEXT = 'General knowledge — not from your documents';

test('grounding="general" renders the persistent banner chip', () => {
  render(<AssistantMessage content="ISO 45001 is an OHS standard." sources={[]} grounding="general" />);
  expect(screen.getByText(BANNER_TEXT)).toBeInTheDocument();
});

test('grounding="documents" (the default) does not render the banner', () => {
  render(<AssistantMessage content="Revenue was 12M [1]." sources={[]} />);
  expect(screen.queryByText(BANNER_TEXT)).not.toBeInTheDocument();
});
