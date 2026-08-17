# ADR-0005: Architecture Source of Truth and Verified Claims

**Date:** 2026-08-17
**Status:** Accepted

## Context

`CLAUDE.md` names `docs/superpowers/specs/2026-07-18-ragz-engineering-foundation-design.md`
as "the Foundation" and the binding source of truth, and
`docs/superpowers/specs/2026-07-19-customer-requirements-addendum.md` as binding
product requirements. **Neither file, nor the `docs/superpowers/` directory, is
tracked in this repository.** Every "the Foundation wins" instruction therefore
resolved to nothing, and the depth pointers ("Architecture & layout §2 · Security
model §3 …") pointed at sections no contributor or agent could read.

The 2026-08-17 architecture review (`docs/audit/2026-08-17-architecture-review.md`)
also found that several `CLAUDE.md` invariants were simply false at the time of
writing. Documentation asserting controls that do not exist is worse than silence:
it is read as evidence the control is handled, and reviewers stop looking.

Verified on 2026-08-17 against the tree:

| Claim in CLAUDE.md | Reality |
|---|---|
| Required CI: ruff, mypy, ESLint, tsc, tests, isolation, import-linter | Did not exist; only a dependency audit and a nightly red-team run |
| Prometheus metrics `ragz_<module>_<metric>` | No metrics, no `prometheus_client` dependency |
| OpenTelemetry tracing | No tracing, no `opentelemetry` dependency |
| `/healthz` + `/readyz` on both processes | Present on the API; the worker has neither |
| Per-stage RAG latency histograms | Not implemented |

## Decision

1. **This repository is the source of truth.** The untracked `superpowers` specs
   are retired as authorities. Where they encoded a real decision, it becomes an
   ADR in `docs/adr/`. The security model is restated in `CLAUDE.md` (the Five
   Iron Rules) and, more importantly, enforced by tests in
   `backend/tests/isolation/` and by import-linter.

2. **A documented invariant must be executable or labelled aspirational.**
   Preference order:
   - a test (`tests/isolation/` for security boundaries),
   - a CI gate (`.github/workflows/ci.yml`),
   - an ADR recording a decision that cannot be machine-checked.
   Anything that is none of these is marked **Not implemented** with a pointer to
   the tracking item, never stated in the present tense.

3. **The architecture review is the current remediation plan of record** until its
   phases are closed. Its P0/P1 items are tracked as
   `docs/audit/2026-08-17-architecture-review.md`, and the P0 ACL exposure is
   pinned executably by `tests/isolation/test_acl_projection_outage.py`
   (`xfail(strict=True)`, so it announces its own fix).

4. **Architecture-changing PRs update the docs in the same commit.** This already
   applied to `CLAUDE.md`; it now also covers adding an ADR when a decision cannot
   be expressed as a test or a gate.

## Consequences

- The dangling "Foundation" references are removed from `CLAUDE.md`, and the
  observability section now distinguishes what runs from what is planned. Anyone
  reading it gets an accurate picture of which controls exist.
- Some genuine design rationale that lived only in those untracked specs is lost.
  The mitigation is that the invariants that mattered most — tenant isolation,
  ACL enforcement inside the vector query, secret handling, layering — were
  already encoded as tests and lint contracts rather than prose, which is why
  they survived the specs going missing. That is the argument for rule 2.
- Restating claims honestly lowers the apparent maturity of the project on paper.
  That is the point: the review scored observability 3/15 precisely because the
  documentation had been read as evidence.
