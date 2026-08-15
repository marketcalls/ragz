import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { AgentStepInfo, ToolResultInfo } from '@/api/types';

import { BehindTheScenes } from './behind-the-scenes';

test('renders nothing when there are no agent steps', () => {
  const { container } = render(<BehindTheScenes steps={[]} toolResults={[]} />);
  expect(container).toBeEmptyDOMElement();
});

test('renders the "Behind the scenes" toggle when agent steps exist, collapsed by default', () => {
  const steps: AgentStepInfo[] = [{ n: 1, tool: 'search', query: 'muster point' }];
  render(<BehindTheScenes steps={steps} toolResults={[]} />);
  expect(screen.getByText('Behind the scenes')).toBeInTheDocument();
  expect(screen.queryByText('search')).not.toBeInTheDocument();
});

test('expanding the section shows a "Called the {tool} tool" card per step', async () => {
  const steps: AgentStepInfo[] = [
    { n: 1, tool: 'search', query: 'muster point' },
    { n: 2, tool: 'web_search', query: 'iso 45001' },
  ];
  const user = userEvent.setup();
  render(<BehindTheScenes steps={steps} toolResults={[]} />);
  await user.click(screen.getByText('Behind the scenes'));
  expect(screen.getByText('search')).toBeInTheDocument();
  expect(screen.getByText('web_search')).toBeInTheDocument();
});

test('a web_search step with results is expandable and lists title + source hostname', async () => {
  const steps: AgentStepInfo[] = [{ n: 1, tool: 'web_search', query: 'iso 45001' }];
  const toolResults: ToolResultInfo[] = [
    {
      n: 1,
      tool: 'web_search',
      results: [
        { title: 'ISO 45001 overview', url: 'https://example.test/iso', source: 'example.test' },
        { title: 'Seeking Alpha take', url: 'https://www.seekingalpha.com/x', source: 'seekingalpha.com' },
      ],
    },
  ];
  const user = userEvent.setup();
  render(<BehindTheScenes steps={steps} toolResults={toolResults} />);
  await user.click(screen.getByText('Behind the scenes'));
  const card = screen.getByRole('button', { expanded: false });
  await user.click(card);
  expect(screen.getByText('ISO 45001 overview')).toBeInTheDocument();
  expect(screen.getByText('example.test')).toBeInTheDocument();
  expect(screen.getByText('Seeking Alpha take')).toBeInTheDocument();
  expect(screen.getByText('seekingalpha.com')).toBeInTheDocument();
});

test('a non-web_search step (or a web_search step without results) is not expandable', async () => {
  const steps: AgentStepInfo[] = [{ n: 1, tool: 'search', query: 'muster point' }];
  const user = userEvent.setup();
  render(<BehindTheScenes steps={steps} toolResults={[]} />);
  await user.click(screen.getByText('Behind the scenes'));
  const card = screen.getByRole('button', { name: /Called the.*search.*tool/ });
  expect(card).toBeDisabled();
  expect(card).not.toHaveAttribute('aria-expanded');
});

test('a non-http(s) result url renders as plain text, never as a link', async () => {
  const steps: AgentStepInfo[] = [{ n: 1, tool: 'web_search', query: 'x' }];
  const toolResults: ToolResultInfo[] = [
    {
      n: 1,
      tool: 'web_search',
      results: [{ title: 'Suspicious result', url: 'javascript:alert(1)', source: '' }],
    },
  ];
  const user = userEvent.setup();
  render(<BehindTheScenes steps={steps} toolResults={toolResults} />);
  await user.click(screen.getByText('Behind the scenes'));
  await user.click(screen.getByRole('button', { expanded: false }));
  const title = screen.getByText('Suspicious result');
  expect(title.tagName).toBe('SPAN');
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
});

test('an http(s) result url renders as a link with safe target/rel attributes', async () => {
  const steps: AgentStepInfo[] = [{ n: 1, tool: 'web_search', query: 'x' }];
  const toolResults: ToolResultInfo[] = [
    {
      n: 1,
      tool: 'web_search',
      results: [{ title: 'ISO 45001 overview', url: 'https://example.test/iso', source: 'example.test' }],
    },
  ];
  const user = userEvent.setup();
  render(<BehindTheScenes steps={steps} toolResults={toolResults} />);
  await user.click(screen.getByText('Behind the scenes'));
  await user.click(screen.getByRole('button', { expanded: false }));
  const link = screen.getByRole('link', { name: 'ISO 45001 overview' });
  expect(link).toHaveAttribute('href', 'https://example.test/iso');
  expect(link).toHaveAttribute('target', '_blank');
  expect(link).toHaveAttribute('rel', 'noreferrer noopener');
});
