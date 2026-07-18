# ADR-0004: Tenancy ↔ Auth Sanctioned Model Sharing

**Date:** 2026-07-18
**Status:** Accepted

## Context

The Foundation says modules import other modules' public services only — never ORM
models. The merged Plan A code crosses that line in one place, in both directions:
`tenancy/context.py` imports `auth.models.User` (resolving the request user is the
heart of `TenantContext`), and `auth/service.py` imports `tenancy.context.TenantContext`
plus checks org scoping via `User.org_id`. A strict service-only indirection would
require an `auth.get_user_by_id()` service, a duplicated user DTO, and a dependency
cycle workaround — for two modules that are conceptually one identity boundary.

## Decision

`auth` and `tenancy` are a **sanctioned model-sharing pair**: `tenancy` may import
`auth` ORM models read-only, and `auth` may import `tenancy`'s public context types.
Every other module pair remains service-only (e.g. `documents` and `retrieval` call
`tenancy.service.get_workspace_checked()`, never touch `Workspace` columns directly
outside their own queries via services). Layer direction (`api/worker → modules →
core`) stays enforced by import-linter.

## Rationale

- Identity (users) and tenancy (orgs/workspaces) are inherently coupled: `User.org_id`
  is the tenancy join key. Splitting them behind service facades adds indirection
  without isolation.
- An explicit, documented exception is safer than a fiction that review can't enforce.

## Consequences

- Schema changes to `User` are reviewed against both modules.
- Revisit when Phase 2 adds groups/custom roles: if a `groups` module lands, fold the
  pair boundary decision into that design.
