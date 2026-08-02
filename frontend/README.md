# Ragz Frontend

React 18 + Vite + TypeScript strict + Tailwind (token-driven) + TanStack Query.

## Commands (run from `frontend/`)

| Command | Purpose |
|---|---|
| `pnpm dev` | dev server on :5173, proxies `/api` → `http://localhost:8000` |
| `pnpm test` / `pnpm test:watch` | vitest unit/component tests |
| `pnpm lint` / `pnpm typecheck` / `pnpm build` | gates — all must be green before commit |
| `pnpm generate:api` | regenerate `src/api/schema.d.ts` from the backend OpenAPI (backend must be running) |
| `pnpm e2e` | Playwright smoke — **skips unless `E2E=1`** |

## Structure

- `src/features/*` — feature folders mirroring backend modules (chat, documents, admin, auth, workspaces, models)
- `src/components/` — shared UI (`ui/` primitives, `layout/`, `markdown/`)
- `src/api/` — generated schema + client (`types.ts` is the only file that touches `components['schemas']`)
- `src/lib/` — auth store, jwt, sse parser, theme, utilities
- `src/styles/tokens.css` — THE design tokens (theme spec). Dark mode = `.dark` on `<html>`.

## Rules that lint will enforce on you

- No raw Tailwind palette classes (`bg-gray-100`, `text-indigo-600`) in `src/features` or
  `src/components` — semantic token classes only (`bg-subtle`, `text-muted`, `text-accent`).
  They also generate no CSS: the palette is fully replaced in `tailwind.config.ts`.
- All server state through TanStack Query; no fetch-in-`useEffect`.
- Model output renders through `<Markdown>` only (sanitized, `skipHtml`); never
  `dangerouslySetInnerHTML`.
- `--text-muted` is for meta text only (WCAG AA).

## E2E smoke

Full compose stack + backend + worker + `pnpm dev` running, then:

```bash
E2E=1 E2E_EMAIL=root@ragz.internal E2E_PASSWORD=changeme12345 \
E2E_OPENAI_API_KEY=sk-... pnpm e2e
```

Env vars: `E2E` (opt-in switch), `E2E_BASE_URL` (default `http://localhost:5173`),
`E2E_EMAIL`/`E2E_PASSWORD` (superadmin), `E2E_OPENAI_API_KEY` (used only if no model
is configured yet).
