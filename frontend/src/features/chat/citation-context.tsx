import { createContext, useContext, type ReactNode } from 'react';

const Ctx = createContext<(n: number) => void>(() => {});

export function CitationProvider({
  onCitationClick,
  children,
}: {
  onCitationClick: (n: number) => void;
  children: ReactNode;
}) {
  return <Ctx.Provider value={onCitationClick}>{children}</Ctx.Provider>;
}

export function useCitationClick(): (n: number) => void {
  return useContext(Ctx);
}
