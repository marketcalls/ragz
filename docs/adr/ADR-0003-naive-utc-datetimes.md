# ADR-0003: Naive-UTC Datetimes in Postgres

**Date:** 2026-07-18
**Status:** Accepted

## Context

SQLAlchemy maps `Mapped[datetime]` to `TIMESTAMP WITHOUT TIME ZONE` by default, and
asyncpg rejects timezone-aware Python datetimes for such columns. Plan A shipped all
timestamp columns as naive; mid-phase migration of every column to
`DateTime(timezone=True)` would churn every table and risk a mixed-column state where
some comparisons silently misbehave. Alternatives considered: (A) keep naive columns
and standardize on naive-UTC values; (B) migrate all columns to timestamptz now;
(C) per-column choice.

## Decision

Option A. All persisted datetimes are naive UTC:

- Writes go through `ragz.core.db.naive_utc()` —
  `datetime.now(UTC).replace(tzinfo=None)` — the single write-path idiom (the
  `UUIDPk.created_at` default uses the same expression).
- Reads that must be compared against aware datetimes re-attach UTC:
  `value.replace(tzinfo=UTC)` (as `auth/service.py` already does for
  refresh-token expiry).

## Rationale

- Zero migration churn mid-phase; one convention everywhere beats a mixed state.
- UTC-only storage keeps ordering and arithmetic correct; the tz suffix carries no
  information when every value is UTC by construction.

## Consequences

- Comparing a DB datetime with `datetime.now(UTC)` without normalizing raises
  `TypeError` — a loud failure, not silent corruption; tests catch it immediately.
- API responses serialize naive values; the OpenAPI contract documents all
  timestamps as UTC. Revisit (single Alembic migration to timestamptz) if
  cross-timezone deployments ever read the DB directly.
