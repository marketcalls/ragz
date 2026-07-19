import { createContext, useContext, type ReactNode } from 'react';

export interface CitationMeta {
  section?: string | null;
  version?: number;
}

interface CitationContextValue {
  onCitationClick: (n: number) => void;
  // marker -> section/version, for the CitationChip hover tooltip (CHAT-4).
  metaByMarker: Record<number, CitationMeta>;
}

const Ctx = createContext<CitationContextValue>({
  onCitationClick: () => {},
  metaByMarker: {},
});

export function CitationProvider({
  onCitationClick,
  sources = [],
  children,
}: {
  onCitationClick: (n: number) => void;
  sources?: { marker: number; section?: string | null; version?: number }[];
  children: ReactNode;
}) {
  const metaByMarker = Object.fromEntries(
    sources.map((s) => [s.marker, { section: s.section, version: s.version }]),
  );
  return <Ctx.Provider value={{ onCitationClick, metaByMarker }}>{children}</Ctx.Provider>;
}

export function useCitationClick(): (n: number) => void {
  return useContext(Ctx).onCitationClick;
}

export function useCitationMeta(n: number): CitationMeta | undefined {
  return useContext(Ctx).metaByMarker[n];
}
