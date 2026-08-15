import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { Block } from '@/api/types';

import { AssistantMessage } from './assistant-message';
import type { SourceChipData } from './source-panel';

const BANNER_TEXT = 'General knowledge — not from your documents';

test('grounding="general" renders the persistent banner chip', () => {
  render(<AssistantMessage content="ISO 45001 is an OHS standard." sources={[]} grounding="general" />);
  expect(screen.getByText(BANNER_TEXT)).toBeInTheDocument();
});

test('grounding="documents" (the default) does not render the banner', () => {
  render(<AssistantMessage content="Revenue was 12M [1]." sources={[]} />);
  expect(screen.queryByText(BANNER_TEXT)).not.toBeInTheDocument();
});

test('stopped=true renders the Stopped chip', () => {
  render(<AssistantMessage content="The muster point is the" sources={[]} stopped={true} />);
  expect(screen.getByText('Stopped')).toBeInTheDocument();
});

test('stopped=false (the default) does not render the Stopped chip', () => {
  render(<AssistantMessage content="The muster point is the NORTH CARPARK." sources={[]} />);
  expect(screen.queryByText('Stopped')).not.toBeInTheDocument();
});

test('validationFailed=true renders the "not re-verified" badge', () => {
  render(
    <AssistantMessage content="Revenue was actually 12M, per [1]." sources={[]} validationFailed={true} />,
  );
  expect(screen.getByText('Not re-verified after revision')).toBeInTheDocument();
});

test('validationFailed=false (the default) does not render the badge', () => {
  render(<AssistantMessage content="Revenue was 12M [1]." sources={[]} />);
  expect(screen.queryByText('Not re-verified after revision')).not.toBeInTheDocument();
});

test('clicking an inline [n] marker for a document citation also opens the drawer at that document_id + page', async () => {
  const sources: SourceChipData[] = [
    { marker: 1, document_id: 'd1', filename: 'evac.pdf', page: 5 },
  ];
  const onOpenDocument = vi.fn();
  const user = userEvent.setup();
  render(
    <AssistantMessage
      content="The muster point is the north carpark [1]."
      sources={sources}
      onOpenDocument={onOpenDocument}
    />,
  );
  await user.click(screen.getByRole('button', { name: 'Citation 1' }));
  expect(onOpenDocument).toHaveBeenCalledWith(
    expect.objectContaining({ document_id: 'd1', page: 5 }),
  );
});

test('clicking an inline [n] marker for a web citation does not open the drawer', async () => {
  const sources: SourceChipData[] = [
    { marker: 1, document_id: '', filename: 'ISO overview', page: 0, url: 'https://example.test/iso' },
  ];
  const onOpenDocument = vi.fn();
  const user = userEvent.setup();
  render(
    <AssistantMessage
      content="Per ISO 45001 [1]."
      sources={sources}
      onOpenDocument={onOpenDocument}
    />,
  );
  await user.click(screen.getByRole('button', { name: 'Citation 1' }));
  expect(onOpenDocument).not.toHaveBeenCalled();
});

// Persisted path (MessageNode.blocks from chat history GET) — must render
// identically to the live SSE path, both through the same `blocks` prop.
test('persisted blocks render below the text via BlockRenderer', () => {
  const blocks: Block[] = [
    { type: 'tag_badges', tags: [{ label: 'On track', tone: 'success' }] },
  ];
  render(<AssistantMessage content="Status update." sources={[]} blocks={blocks} />);
  expect(screen.getByText('On track')).toBeInTheDocument();
});

test('blocks omitted or empty renders nothing extra', () => {
  const { container: withoutProp } = render(
    <AssistantMessage content="No blocks here." sources={[]} />,
  );
  const { container: withEmpty } = render(
    <AssistantMessage content="No blocks here either." sources={[]} blocks={[]} />,
  );
  expect(withoutProp.querySelector('table')).not.toBeInTheDocument();
  expect(withEmpty.querySelector('table')).not.toBeInTheDocument();
});
