import { Link } from 'react-router-dom';

const GH = 'https://github.com/marketcalls/ragz';

const FEATURES: { n: string; title: string; body: string }[] = [
  { n: '01', title: 'Tenant isolation & in-query ACLs',
    body: 'Answers never cite a document you cannot open — access control is enforced inside the vector query, never post-filtered in code.' },
  { n: '02', title: 'Role-based access control',
    body: 'Superadmin / admin / user tiers plus composable custom roles, deny-by-default permissions, and an append-only audit log.' },
  { n: '03', title: 'Pluggable document parsing',
    body: 'anydoc by default (fast, pure-Rust), Docling OCR for scanned PDFs, or LlamaParse — with version-aware retrieval and cited sources.' },
  { n: '04', title: 'Hybrid retrieval + rerank',
    body: 'Dense and sparse retrieval with optional local or Cohere reranking, and a no-answer mode when grounding is insufficient.' },
  { n: '05', title: 'API & bot integrations',
    body: 'A secure external API with an OpenAI-compatible endpoint, plus inbound bots for Telegram, Discord, and Slack.' },
  { n: '06', title: 'Encrypted secrets',
    body: 'Provider keys are envelope-encrypted (AES-256-GCM) in Postgres; the KEK is the only secret that lives outside the database.' },
];

function PillNav() {
  return (
    <div className="sticky top-4 z-10 mx-auto flex max-w-5xl items-center justify-between rounded-full border border-line bg-raised px-4 py-2 backdrop-blur">
      <span className="pl-2 text-lg font-bold tracking-tight text-ink">Ragz</span>
      <nav className="hidden items-center gap-6 text-sm text-secondary sm:flex">
        <a href="#features" className="hover:text-ink">Features</a>
        <a href={GH} className="hover:text-ink" target="_blank" rel="noreferrer">Docs</a>
        <a href={GH} className="hover:text-ink" target="_blank" rel="noreferrer">GitHub</a>
      </nav>
      <Link to="/login" className="rounded-full bg-primary px-5 py-2 text-sm font-medium text-primary-foreground hover:opacity-90">
        Sign in
      </Link>
    </div>
  );
}

function Hero() {
  return (
    <section className="mx-auto max-w-4xl px-6 pt-24 pb-16 text-center">
      <span className="inline-block rounded-full border border-line px-4 py-1 text-xs font-medium uppercase tracking-wide text-muted">
        Self-hosted · Multi-tenant · AGPL-3.0
      </span>
      <h1 className="mt-8 text-5xl font-bold leading-[1.05] tracking-tight text-ink sm:text-7xl">
        Your private
        <br />
        <span className="text-muted">agentic RAG platform.</span>
      </h1>
      <p className="mx-auto mt-6 max-w-2xl text-lg text-secondary">
        Own your data and your keys. Multi-tenant workspaces, document-level access control, and
        answers that always cite their sources.
      </p>
      <div className="mt-10 flex flex-wrap items-center justify-center gap-x-8 gap-y-2 text-sm">
        <span className="font-semibold text-ink">100%<span className="ml-1.5 font-normal text-muted">OPEN SOURCE</span></span>
        <span className="hidden text-line-strong sm:inline">|</span>
        <span className="font-semibold text-ink">AES-256-GCM<span className="ml-1.5 font-normal text-muted">SECRETS</span></span>
        <span className="hidden text-line-strong sm:inline">|</span>
        <span className="font-semibold text-ink">OWASP<span className="ml-1.5 font-normal text-muted">ASVS L2</span></span>
      </div>
      <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
        <Link to="/login" className="rounded-full bg-primary px-7 py-3 text-sm font-medium text-primary-foreground hover:opacity-90">
          Get Started →
        </Link>
        <a href={GH} target="_blank" rel="noreferrer" className="rounded-full border border-line px-7 py-3 text-sm font-medium text-ink hover:bg-subtle">
          GitHub
        </a>
      </div>
    </section>
  );
}

function Features() {
  return (
    <section id="features" className="mx-auto max-w-3xl px-6 py-20">
      <p className="text-center text-xs font-semibold uppercase tracking-widest text-muted">Why Ragz</p>
      <h2 className="mt-3 text-center text-4xl font-bold tracking-tight text-ink">
        Own everything, <span className="text-muted">from data to keys.</span>
      </h2>
      <div className="mt-14 space-y-10">
        {FEATURES.map((f) => (
          <div key={f.n} className="flex gap-6 border-l border-line pl-6">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-line text-xs font-semibold text-muted">
              {f.n}
            </span>
            <div>
              <h3 className="text-lg font-semibold text-ink">{f.title}</h3>
              <p className="mt-1 text-secondary">{f.body}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 border-t border-line px-6 py-10 text-sm text-muted">
      <span className="font-bold text-ink">Ragz</span>
      <div className="flex gap-6">
        <a href="#features" className="hover:text-ink">Features</a>
        <a href={GH} target="_blank" rel="noreferrer" className="hover:text-ink">Docs</a>
        <a href={GH} target="_blank" rel="noreferrer" className="hover:text-ink">GitHub</a>
      </div>
      <span>AGPL-3.0 · self-hosted</span>
    </footer>
  );
}

export function LandingPage() {
  return (
    <div
      className="min-h-screen bg-bg pt-4 text-ink"
      style={{
        backgroundImage:
          'linear-gradient(to right, var(--border-faint) 1px, transparent 1px), linear-gradient(to bottom, var(--border-faint) 1px, transparent 1px)',
        backgroundSize: '32px 32px',
      }}
    >
      <PillNav />
      <Hero />
      <Features />
      <Footer />
    </div>
  );
}
