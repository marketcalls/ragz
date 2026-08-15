import { Check, Globe, ImagePlus, Plus } from 'lucide-react';
import { useRef } from 'react';

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

// A UX filter only -- the backend has no server-side MIME allowlist, so
// this doesn't need to be (and shouldn't be treated as) a security boundary.
const ACCEPT = 'image/*,application/pdf,.txt,.md,.docx';

// Left-side composer controls that live INSIDE the input container (ChatGPT
// pattern): a round `+` button opening a small menu ("Add photos & files" +,
// when the workspace allows it, "Search the web"), plus a blue "Web search"
// pill shown while web search is toggled on. Files are picked locally; upload
// happens at send time (see chat-page.tsx / use-send-message.ts), so this
// holds no upload state.
export function ComposerControls({
  onSelectFiles,
  disabled = false,
  webSearchAvailable,
  webSearch,
  onToggleWebSearch,
}: {
  onSelectFiles: (files: File[]) => void;
  disabled?: boolean;
  webSearchAvailable: boolean;
  webSearch: boolean;
  onToggleWebSearch: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>): void => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = ''; // allow re-picking the same file after removal
    if (files.length > 0) onSelectFiles(files);
  };

  return (
    <div className="flex items-center gap-1.5">
      <input
        ref={inputRef}
        type="file"
        multiple
        aria-label="Attach a file"
        accept={ACCEPT}
        className="sr-only"
        disabled={disabled}
        onChange={handleChange}
      />
      <DropdownMenu>
        <DropdownMenuTrigger
          type="button"
          aria-label="Add attachments and options"
          disabled={disabled}
          className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-full text-muted transition-colors duration-150 ease-out hover:bg-subtle hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent disabled:pointer-events-none disabled:opacity-40"
        >
          <Plus className="h-5 w-5" aria-hidden />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" side="top">
          <DropdownMenuItem
            className="flex items-center gap-2"
            onSelect={() => inputRef.current?.click()}
          >
            <ImagePlus className="h-4 w-4 shrink-0 text-muted" aria-hidden />
            Add photos &amp; files
          </DropdownMenuItem>
          {webSearchAvailable ? (
            <DropdownMenuItem
              className="flex items-center gap-2"
              // Keep the menu semantics a toggle: prevent the default close so
              // rapid on/off is possible, but Radix closes on select anyway --
              // acceptable, matches ChatGPT (menu closes, pill reflects state).
              onSelect={() => onToggleWebSearch()}
            >
              <Globe className="h-4 w-4 shrink-0 text-muted" aria-hidden />
              <span className="flex-1">Search the web</span>
              {webSearch ? (
                <Check className="h-4 w-4 shrink-0 text-accent" aria-hidden />
              ) : null}
            </DropdownMenuItem>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>

      {webSearchAvailable && webSearch ? (
        <button
          type="button"
          aria-label="Turn off web search"
          aria-pressed={true}
          onClick={onToggleWebSearch}
          className="flex h-[30px] shrink-0 items-center gap-1.5 rounded-full border border-[#2563eb]/30 bg-[#2563eb]/10 px-2.5 text-[13px] font-medium text-[#2563eb] transition-colors duration-150 ease-out hover:bg-[#2563eb]/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
        >
          <Globe className="h-4 w-4" aria-hidden />
          Web search
        </button>
      ) : null}
    </div>
  );
}
