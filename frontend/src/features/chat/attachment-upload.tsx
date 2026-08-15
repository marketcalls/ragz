import { Paperclip } from 'lucide-react';

// A UX filter only -- the backend has no server-side MIME allowlist, so
// this doesn't need to be (and shouldn't be treated as) a security boundary.
const ACCEPT = 'image/*,application/pdf,.txt,.md,.docx';

// Picks files locally; upload happens at send time (see chat-page.tsx /
// use-send-message.ts) so attaching works before a chat exists. No upload
// state here -- just a real, accessible, keyboard-reachable control.
export function AttachmentUpload({
  onSelect,
  disabled = false,
}: {
  onSelect: (files: File[]) => void;
  disabled?: boolean;
}) {
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>): void => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = ''; // allow re-picking the same file after removal
    if (files.length > 0) onSelect(files);
  };

  return (
    <label
      aria-disabled={disabled}
      className="flex h-[26px] w-[26px] shrink-0 cursor-pointer items-center justify-center rounded-full text-muted transition-colors duration-150 ease-out hover:bg-subtle hover:text-ink focus-within:outline focus-within:outline-2 focus-within:outline-accent aria-disabled:pointer-events-none aria-disabled:opacity-40"
    >
      <input
        type="file"
        multiple
        aria-label="Attach a file"
        accept={ACCEPT}
        className="sr-only"
        disabled={disabled}
        onChange={handleChange}
      />
      <Paperclip className="h-4 w-4" aria-hidden />
    </label>
  );
}
