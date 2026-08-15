"""RBAC-06: the route -> action registry. Every FastAPI route mounted in
api/app.py must appear in exactly one of PUBLIC_ROUTES (no TenantContext at
all -- login, health, webhooks whose auth IS signature verification) or
ROUTE_POLICY (the action it performs). audit_route_policy is the CI gate:
a new route with neither entry fails tests/api/test_route_policy.py, which
is the enforcement mechanism for "no unclassified route reaches main"
(audit §9 mandatory adversarial test 5)."""

import re
from collections.abc import Iterable, Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

from ragz.modules.tenancy.permissions import PERMISSIONS

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
_HTTP_METHODS_UPPER = frozenset(m.upper() for m in _HTTP_METHODS)

# Strips Starlette path-converter syntax ("{name:path}" -> "{name}") so a
# path assembled from raw APIRoute.path segments matches the normalized form
# FastAPI's OpenAPI generator (and ROUTE_POLICY's keys) use.
_PATH_CONVERTER_RE = re.compile(r"\{([^:{}]+):[^{}]+\}")


def _normalize_path(path: str) -> str:
    return _PATH_CONVERTER_RE.sub(r"{\1}", path)


def _iter_api_routes(
    routes: Iterable[Any], prefix: str = ""
) -> Iterator[tuple[str, APIRoute]]:
    """Recursively walk the router tree rooted at `routes`, yielding
    (full_path, route) for every mounted APIRoute, regardless of
    include_in_schema. Descends into anything that looks like a nested
    router: Starlette Mount-style nodes exposing `.routes` directly, and
    FastAPI's private `_IncludedRouter` wrapper, which instead exposes the
    sub-router (with its own `.routes`) via `.original_router` and the path
    prefix it was mounted under via `.include_context.prefix`.

    Shared by `_iter_hidden_api_routes` (the include_in_schema=False safety
    net below) and `audit_route_enforcement`'s route index (sec
    RAGZ-PUB-01b), which needs the actual mounted `APIRoute` object -- not
    just its OpenAPI schema entry -- to walk `route.dependant`.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
            continue

        nested_prefix = prefix
        include_context = getattr(route, "include_context", None)
        if include_context is not None:
            nested_prefix = prefix + getattr(include_context, "prefix", "")

        nested = getattr(route, "routes", None)
        if nested is None:
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                nested = getattr(original_router, "routes", None)
        if nested:
            yield from _iter_api_routes(nested, nested_prefix)


def _iter_hidden_api_routes(
    routes: Iterable[Any], prefix: str = ""
) -> Iterator[tuple[str, APIRoute]]:
    """Recursively walk the router tree rooted at `routes`, yielding
    (full_path, route) for every APIRoute with include_in_schema=False.

    This exists purely as a supplementary safety net -- see the docstring on
    audit_route_policy for why app.openapi() remains the primary enumeration
    source and this walk is not.
    """
    for full_path, route in _iter_api_routes(routes, prefix):
        if not route.include_in_schema:
            yield full_path, route

# Two distinct reasons a route lands here, both meaning "no per-action policy
# decision for audit_route_policy to enforce":
#  1. No TenantContext dependency at all: either genuinely public (login,
#     health, docs) or the auth IS something other than a bearer JWT (webhook
#     signature verification in bots.py; the request never reaches
#     get_tenant_context).
#  2. Authenticated-but-actionless "self" routes: they DO depend on
#     get_tenant_context (a valid JWT is required), but the only thing
#     resembling an authorization decision is "is authenticated" -- there is
#     no separate action/resource to gate, so there is nothing for
#     ROUTE_POLICY to declare. `/me/authorization` (RBAC-12) is this case: it
#     just echoes the caller's own already-computed TenantContext back to
#     them. Don't read this bucket as "truly public, no auth" -- check which
#     of the two reasons applies before assuming a bearer token isn't
#     required.
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/healthz"), ("GET", "/readyz"),
    ("POST", "/api/v1/auth/login"), ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"), ("POST", "/api/v1/auth/invitations/accept"),
    # Self-service first-run (no TenantContext -- this IS the bootstrap path,
    # run before any superadmin/JWT exists). register_first_superadmin is
    # fail-closed (409 once a superadmin exists) + race-safe (advisory lock).
    ("GET", "/api/v1/auth/bootstrap-status"), ("POST", "/api/v1/auth/register"),
    ("GET", "/api/v1/auth/oidc/status"), ("GET", "/api/v1/auth/oidc/login"),
    ("GET", "/api/v1/auth/oidc/callback"),
    # RAGZ-PUB-06: forgot/reset-password are unauthenticated by nature (the
    # emailed token IS the credential, not a bearer JWT) -- reason 1 above.
    ("POST", "/api/v1/auth/forgot-password"), ("POST", "/api/v1/auth/reset-password"),
    ("POST", "/external/bots/telegram/{webhook_id}"),
    ("POST", "/external/bots/slack/{webhook_id}"),
    ("POST", "/external/bots/discord/{webhook_id}"),
    # authenticated-but-actionless self route (reason 2 above): requires a
    # valid JWT via get_tenant_context, but has no per-action policy.
    ("GET", "/api/v1/me/authorization"),
    # RAGZ-PUB-06: change-password requires a valid JWT via get_tenant_context
    # (the user changes only their OWN password, resolved from ctx.user_id)
    # but has no separate catalog action -- same "authenticated self" bucket
    # as /me/authorization above.
    ("POST", "/api/v1/auth/change-password"),
    # RAGZ-PUB-06: session inventory/revocation -- each acts only on the
    # caller's OWN refresh-token families (resolved from ctx.user_id, plus
    # a non-leaking ownership check on the path/cookie-scoped family_id in
    # revoke_session/revoke_other_sessions), same "authenticated self" bucket
    # as change-password above.
    ("GET", "/api/v1/auth/sessions"),
    ("DELETE", "/api/v1/auth/sessions/{family_id}"),
    ("POST", "/api/v1/auth/sessions/revoke-others"),
})

# (method, path-with-{param}-placeholders) -> declared action. `path` matches
# the path template as it appears in FastAPI's generated OpenAPI schema (the
# same one audit_route_policy reads), which keeps the {param} placeholder but
# strips Starlette path-converter syntax (e.g. a route declared with
# "{name:path}" appears here, and in ROUTE_POLICY, as "{name}").
ROUTE_POLICY: dict[tuple[str, str], str] = {
    # auth
    ("POST", "/api/v1/auth/invitations"): "users.invite",
    # users
    ("GET", "/api/v1/users"): "users.read",
    ("PATCH", "/api/v1/users/{user_id}"): "users.role.assign",
    ("PUT", "/api/v1/users/{user_id}/custom-role"): "users.role.assign",
    # groups
    ("GET", "/api/v1/groups"): "groups.read",
    ("POST", "/api/v1/groups"): "groups.manage",
    ("DELETE", "/api/v1/groups/{group_id}"): "groups.manage",
    ("PUT", "/api/v1/groups/{group_id}/members/{user_id}"): "groups.manage",
    ("DELETE", "/api/v1/groups/{group_id}/members/{user_id}"): "groups.manage",
    # workspaces
    ("POST", "/api/v1/workspaces"): "workspace.create",
    ("GET", "/api/v1/workspaces"): "workspace.read",
    ("POST", "/api/v1/workspaces/{workspace_id}/members"): "workspace.members.manage",
    ("GET", "/api/v1/workspaces/{workspace_id}/members"): "workspace.members.read",
    ("PATCH", "/api/v1/workspaces/{workspace_id}/members/{user_id}"): "workspace.members.manage",
    ("DELETE", "/api/v1/workspaces/{workspace_id}/members/{user_id}"): "workspace.members.manage",
    ("PATCH", "/api/v1/workspaces/{workspace_id}/embedding-model"): "workspace.configure",
    ("PATCH", "/api/v1/workspaces/{workspace_id}"): "workspace.configure",
    ("POST", "/api/v1/workspaces/{workspace_id}/reembed"): "workspace.reembed",
    ("GET", "/api/v1/workspaces/{workspace_id}/reembed-status"): "workspace.read",
    # documents + folders
    ("POST", "/api/v1/workspaces/{workspace_id}/documents"): "documents.upload",
    ("GET", "/api/v1/workspaces/{workspace_id}/documents"): "documents.list",
    ("GET", "/api/v1/documents/{document_id}/file"): "documents.content.read",
    ("DELETE", "/api/v1/documents/{document_id}"): "documents.delete",
    # Per-document Reindex re-runs the ingest pipeline (a WRITE to the index),
    # so it reuses "documents.upload" -- enforced via require_action, so it is
    # NOT a _ROLE_ONLY_ENFORCEMENT exception.
    ("POST", "/api/v1/documents/{document_id}/reindex"): "documents.upload",
    # sec RAGZ-PUB-01: the former combined PATCH /documents/{id} (pin AND move
    # under auth-only) is split into two single-action endpoints.
    ("PATCH", "/api/v1/documents/{document_id}/pin"): "documents.pin",
    ("PATCH", "/api/v1/documents/{document_id}/move"): "documents.move",
    ("PUT", "/api/v1/documents/{document_id}/acl"): "documents.acl.manage",
    ("PUT", "/api/v1/documents/{document_id}/approved"): "documents.approve",
    ("POST", "/api/v1/workspaces/{workspace_id}/folders"): "folders.create",
    ("POST", "/api/v1/workspaces/{workspace_id}/folders/ensure-path"): "folders.create",
    ("GET", "/api/v1/workspaces/{workspace_id}/folders"): "folders.read",
    ("PATCH", "/api/v1/folders/{folder_id}"): "folders.update",
    ("GET", "/api/v1/folders/{folder_id}/delete-preview"): "folders.delete",
    ("DELETE", "/api/v1/folders/{folder_id}"): "folders.delete",
    ("GET", "/api/v1/workspaces/{workspace_id}/metadata-fields"): "documents.list",
    ("POST", "/api/v1/workspaces/{workspace_id}/metadata-fields"): "workspace.metadata.manage",
    ("DELETE", "/api/v1/metadata-fields/{field_id}"): "workspace.metadata.manage",
    ("PUT", "/api/v1/documents/{document_id}/metadata"): "documents.metadata.update",
    # search
    ("POST", "/api/v1/workspaces/{workspace_id}/search"): "search.execute",
    # evals
    ("GET", "/api/v1/workspaces/{workspace_id}/golden-queries"): "evals.read",
    ("POST", "/api/v1/workspaces/{workspace_id}/golden-queries"): "evals.manage",
    ("DELETE", "/api/v1/golden-queries/{query_id}"): "evals.manage",
    ("POST", "/api/v1/workspaces/{workspace_id}/evals/run"): "evals.run",
    ("GET", "/api/v1/workspaces/{workspace_id}/evals/runs"): "evals.read",
    # chat
    ("POST", "/api/v1/chats"): "chat.generate",
    ("GET", "/api/v1/chats"): "chat.read",
    ("GET", "/api/v1/chats/{chat_id}"): "chat.read",
    ("PATCH", "/api/v1/chats/{chat_id}"): "chat.update",
    ("DELETE", "/api/v1/chats/{chat_id}"): "chat.delete",
    ("POST", "/api/v1/chats/{chat_id}/messages"): "chat.generate",
    ("POST", "/api/v1/messages/{message_id}/regenerate"): "chat.generate",
    ("PUT", "/api/v1/messages/{message_id}/feedback"): "chat.feedback",
    ("DELETE", "/api/v1/messages/{message_id}/feedback"): "chat.feedback",
    ("POST", "/api/v1/chats/{chat_id}/attachments"): "chat.attachments.create",
    ("GET", "/api/v1/chats/{chat_id}/attachments/{attachment_id}/content"): "chat.read",
    # admin: roles / audit / sso / secrets / feedback / bots / api-keys / models / settings
    ("GET", "/api/v1/admin/roles"): "roles.read",
    ("POST", "/api/v1/admin/roles"): "roles.author",
    ("PATCH", "/api/v1/admin/roles/{role_template_id}"): "roles.author",
    ("DELETE", "/api/v1/admin/roles/{role_template_id}"): "roles.author",
    ("POST", "/api/v1/admin/roles/{role_template_id}/activate"): "roles.author",
    ("POST", "/api/v1/admin/roles/{role_template_id}/rollback"): "roles.author",
    ("GET", "/api/v1/admin/roles/{role_template_id}/impact"): "roles.read",
    ("GET", "/api/v1/admin/audit"): "audit.read",
    ("GET", "/api/v1/admin/audit/export"): "audit.export",
    ("GET", "/api/v1/admin/sso"): "sso.manage",
    ("PUT", "/api/v1/admin/sso"): "sso.manage",
    ("GET", "/api/v1/admin/orgs"): "platform.orgs.manage",
    ("PUT", "/api/v1/admin/orgs/{org_id}/sso-domains"): "platform.orgs.manage",
    # NOTE: path is declared with a `:path` converter in admin_secrets.py so it
    # can contain "/" (secret names are dotted-path-like), but FastAPI's
    # generated OpenAPI schema -- which audit_route_policy reads -- strips
    # converter syntax from the path template, so the key here must match the
    # schema's normalized "{name}" rather than the route's raw "{name:path}".
    ("PUT", "/api/v1/admin/secrets/{name}"): "secrets.manage",
    ("GET", "/api/v1/admin/secrets"): "secrets.manage",
    ("DELETE", "/api/v1/admin/secrets/{name}"): "secrets.manage",
    ("GET", "/api/v1/admin/feedback"): "feedback.review",
    ("POST", "/api/v1/admin/bots"): "integrations.manage",
    ("GET", "/api/v1/admin/bots"): "integrations.manage",
    ("PATCH", "/api/v1/admin/bots/{bot_id}"): "integrations.manage",
    ("DELETE", "/api/v1/admin/bots/{bot_id}"): "integrations.manage",
    ("POST", "/api/v1/admin/api-keys"): "api_keys.manage",
    ("GET", "/api/v1/admin/api-keys"): "api_keys.manage",
    ("DELETE", "/api/v1/admin/api-keys/{key_id}"): "api_keys.manage",
    ("GET", "/api/v1/admin/models"): "models.read",
    ("POST", "/api/v1/admin/models"): "models.manage",
    ("PATCH", "/api/v1/admin/models/{model_id}"): "models.manage",
    ("DELETE", "/api/v1/admin/models/{model_id}"): "models.manage",
    ("GET", "/api/v1/admin/models/catalog"): "models.read",
    ("POST", "/api/v1/admin/models/catalog/refresh"): "models.manage",
    ("GET", "/api/v1/models"): "models.read",
    ("GET", "/api/v1/admin/settings"): "settings.manage",
    ("PUT", "/api/v1/admin/settings"): "settings.manage",
    ("GET", "/api/v1/admin/email"): "settings.manage",
    ("PUT", "/api/v1/admin/email"): "settings.manage",
    ("POST", "/api/v1/admin/email/test"): "settings.manage",
    # usage / quota
    ("GET", "/api/v1/admin/orgs/{org_id}/quota"): "quota.manage",
    ("PUT", "/api/v1/admin/orgs/{org_id}/quota"): "quota.manage",
    ("GET", "/api/v1/users/{user_id}/quota"): "quota.read",
    ("PUT", "/api/v1/users/{user_id}/quota"): "quota.manage",
    ("GET", "/api/v1/usage/me"): "quota.read",
    ("GET", "/api/v1/usage/me/daily"): "quota.read",
    ("GET", "/api/v1/admin/usage/summary"): "analytics.view",
    ("GET", "/api/v1/admin/usage/orgs"): "analytics.view",
    # reports (design 2026-08-15): both declare the reports.view.self FLOOR
    # (enforced via require_action, so no _ROLE_ONLY_ENFORCEMENT entry needed);
    # per-scope escalation (department/org/platform) is in-handler.
    ("GET", "/api/v1/reports/usage"): "reports.view.self",
    ("GET", "/api/v1/reports/usage/export"): "reports.view.self",
    # ops
    ("GET", "/api/v1/superadmin/health"): "platform.health",
    ("POST", "/api/v1/client-errors"): "client_errors.report",
    ("GET", "/api/v1/superadmin/client-errors"): "platform.health",
    # external (API-key auth, not JWT -- still performs a catalog action)
    ("POST", "/external/v1/chat"): "chat.generate",
    ("POST", "/external/v1/openai/chat/completions"): "chat.generate",
    ("GET", "/external/v1/openai/models"): "models.read",
}


def audit_route_policy(app: FastAPI) -> list[str]:
    """Every mounted route must be in PUBLIC_ROUTES or ROUTE_POLICY (with a
    known action). FastAPI's own docs/openapi routes are excluded -- they
    carry no application data and no TenantContext at all.

    Route discovery goes through `app.openapi()` (the schema FastAPI itself
    serves to Swagger UI and the frontend's OpenAPI-generated client) rather
    than walking `app.routes` for `APIRoute` instances directly. Newer
    FastAPI versions wrap `include_router`-mounted routers in a private,
    lazily-resolved `_IncludedRouter` node instead of flattening them into
    `app.routes` eagerly, so an `isinstance(route, APIRoute)` walk silently
    finds zero routes and this gate would vacuously "pass" with an empty
    gap list. `app.openapi()` is the public, version-stable surface that
    always reflects exactly what FastAPI will actually dispatch.

    One gap in `app.openapi()`: it omits any route mounted with
    `include_in_schema=False` entirely -- there are none of those today, but
    a hidden internal/webhook route is exactly the kind of thing a developer
    forgets to add to ROUTE_POLICY. To close that hole without giving up the
    stable openapi()-based primary walk, this also runs a supplementary,
    best-effort recursive walk of `app.router.routes` (see
    `_iter_hidden_api_routes`) that finds `APIRoute` instances with
    `include_in_schema=False` specifically and reports any that aren't
    declared. That walk relies on FastAPI's private `_IncludedRouter`/
    `original_router`/`include_context` attributes, which is exactly the
    kind of version-fragile internals the openapi() switch above was meant
    to avoid -- so it stays scoped to this supplementary check only, never
    the primary source of truth.
    """
    gaps: list[str] = []
    schema = app.openapi()
    for path, path_item in schema.get("paths", {}).items():
        if path in ("/api/openapi.json",) or path.startswith("/api/docs"):
            continue
        for method_lower in path_item:
            if method_lower not in _HTTP_METHODS:
                continue
            method = method_lower.upper()
            key = (method, path)
            if key in PUBLIC_ROUTES:
                continue
            action = ROUTE_POLICY.get(key)
            if action is None:
                gaps.append(f"{method} {path}")
            elif action not in PERMISSIONS:
                gaps.append(f"{method} {path} (unknown action {action!r})")

    for full_path, route in _iter_hidden_api_routes(app.router.routes):
        path = _normalize_path(full_path)
        for method in route.methods or ():
            if method not in _HTTP_METHODS_UPPER:
                continue
            key = (method, path)
            if key in PUBLIC_ROUTES:
                continue
            action = ROUTE_POLICY.get(key)
            if action is None:
                gaps.append(f"{method} {path} (hidden route, include_in_schema=False)")
            elif action not in PERMISSIONS:
                gaps.append(f"{method} {path} (hidden route, unknown action {action!r})")
    return gaps


# sec RAGZ-PUB-01b: audit_route_policy above only proves a route has a
# catalog entry (method, path) -> action -- it never inspects the route's
# actual FastAPI dependencies, so a route could declare e.g. "documents.pin"
# in ROUTE_POLICY while its handler wires only `require_role("admin")` (or
# bare auth) and vacuously "pass". This is that missing check: it proves
# ENFORCEMENT, not just cataloging.
#
# Routes here enforce their declared action through a mechanism OTHER than
# `Depends(require_action(...))`, so `audit_route_enforcement` cannot find a
# `__ragz_required_action__` tag in their dependency graph and must not flag
# them. Each entry documents HOW it's actually enforced. No other route may
# be added here silently -- any other gap the gate reports must be fixed by
# wiring `require_action`, not by extending this set.
_ENFORCEMENT_EXCEPTIONS: frozenset[tuple[str, str]] = frozenset({
    # API-key auth (external.py::api_key_context ->
    # build_verified_principal_context): every request revalidates the key's
    # backing user still has a live membership in the key's workspace AND
    # still holds chat.generate (or is superadmin) -- RBAC-02. There is no
    # require_action("chat.generate") in the dependency graph because the
    # check happens inside the API-key principal-resolution dependency
    # itself, not as a separate guard.
    ("POST", "/external/v1/chat"),
    ("POST", "/external/v1/openai/chat/completions"),
    # Same API-key auth path as above. Declares "models.read" in
    # ROUTE_POLICY, but the actual gate is "does this key's workspace
    # membership resolve" (api_key_context/build_verified_principal_context)
    # -- there is no separate models.read permission check on this route,
    # by design: a key scoped to a workspace may always see that workspace's
    # one configured chat model.
    ("GET", "/external/v1/openai/models"),
})


# sec RAGZ-PUB-01 (closure): the ~49 pre-existing ROUTE_POLICY routes that
# audit_route_enforcement reports because they enforce their declared action
# through a role gate (require_role(...)) rather than require_action(...).
# This is fail-safe under-permissiveness, not a privilege-escalation bypass
# (see test_route_enforcement.py's module docstring for the full argument),
# but it must be an EXACT, EXPLICITLY MODELED set -- not "any role guard
# passes". A brand-new route that declares an action in ROUTE_POLICY and
# wires neither require_action(...) NOR one of these exact (method, path)
# entries now fails CI via audit_unmodeled_enforcement_gaps below. Do not add
# a route here just to make a test pass -- wire require_action instead,
# unless it is a genuine, reviewed, deliberate role-only exception like the
# ones already catalogued.
#
# Value = short justification: which role guard actually gates the route, and
# why role-only enforcement is acceptable here. Derived from a live
# introspection of each route's `__ragz_required_roles__` tag: frozenset()
# (require_role() with no args) and frozenset({"superadmin"})
# (require_role("superadmin")) are behaviorally identical -- both
# superadmin-only, since require_role's superadmin bypass fires before the
# named-role check either way; frozenset({"admin"}) (require_role("admin"))
# is admin+superadmin. test_role_only_catalog_has_no_stale_entries re-derives
# this from the live dependency graph and fails if it ever drifts from what's
# written here.
_ROLE_ONLY_ENFORCEMENT: dict[tuple[str, str], str] = {
    # invitations
    ("POST", "/api/v1/auth/invitations"):
        "admin+superadmin (require_role('admin')) -- org user invitations",
    # users
    ("GET", "/api/v1/users"):
        "admin+superadmin (require_role('admin')) -- org user directory",
    ("PATCH", "/api/v1/users/{user_id}"):
        "admin+superadmin (require_role('admin')) -- role assignment",
    ("PUT", "/api/v1/users/{user_id}/custom-role"):
        "admin+superadmin (require_role('admin')) -- role assignment",
    # groups (ACL group administration)
    ("GET", "/api/v1/groups"):
        "admin+superadmin (require_role('admin')) -- ACL group administration",
    ("POST", "/api/v1/groups"):
        "admin+superadmin (require_role('admin')) -- ACL group administration",
    ("DELETE", "/api/v1/groups/{group_id}"):
        "admin+superadmin (require_role('admin')) -- ACL group administration",
    ("PUT", "/api/v1/groups/{group_id}/members/{user_id}"):
        "admin+superadmin (require_role('admin')) -- ACL group administration",
    ("DELETE", "/api/v1/groups/{group_id}/members/{user_id}"):
        "admin+superadmin (require_role('admin')) -- ACL group administration",
    # roles (role-template authoring)
    ("GET", "/api/v1/admin/roles"):
        "admin+superadmin (require_role('admin')) -- role-template listing",
    ("POST", "/api/v1/admin/roles"):
        "superadmin-only (require_role('superadmin')) -- role-template authoring",
    ("PATCH", "/api/v1/admin/roles/{role_template_id}"):
        "superadmin-only (require_role('superadmin')) -- role-template authoring",
    ("DELETE", "/api/v1/admin/roles/{role_template_id}"):
        "superadmin-only (require_role('superadmin')) -- role-template authoring",
    ("POST", "/api/v1/admin/roles/{role_template_id}/activate"):
        "superadmin-only (require_role('superadmin')) -- role-template authoring",
    ("POST", "/api/v1/admin/roles/{role_template_id}/rollback"):
        "superadmin-only (require_role('superadmin')) -- role-template authoring",
    ("GET", "/api/v1/admin/roles/{role_template_id}/impact"):
        "admin+superadmin (require_role('admin')) -- role-template listing",
    # sso / cross-org platform administration
    ("GET", "/api/v1/admin/sso"):
        "superadmin-only (require_role('superadmin')) -- SSO configuration",
    ("PUT", "/api/v1/admin/sso"):
        "superadmin-only (require_role('superadmin')) -- SSO configuration",
    ("GET", "/api/v1/admin/orgs"):
        "superadmin-only (require_role('superadmin')) -- cross-org platform administration",
    ("PUT", "/api/v1/admin/orgs/{org_id}/sso-domains"):
        "superadmin-only (require_role('superadmin')) -- cross-org platform administration",
    # secrets
    ("PUT", "/api/v1/admin/secrets/{name}"):
        "superadmin-only (require_role()) -- credential/secret management",
    ("GET", "/api/v1/admin/secrets"):
        "superadmin-only (require_role()) -- credential/secret management",
    ("DELETE", "/api/v1/admin/secrets/{name}"):
        "superadmin-only (require_role()) -- credential/secret management",
    # feedback
    ("GET", "/api/v1/admin/feedback"):
        "admin+superadmin (require_role('admin')) -- answer feedback review",
    # bots (Telegram/Slack/Discord integration credentials)
    ("POST", "/api/v1/admin/bots"):
        "superadmin-only (require_role()) -- bot integration credential management",
    ("GET", "/api/v1/admin/bots"):
        "superadmin-only (require_role()) -- bot integration credential management",
    ("PATCH", "/api/v1/admin/bots/{bot_id}"):
        "superadmin-only (require_role()) -- bot integration credential management",
    ("DELETE", "/api/v1/admin/bots/{bot_id}"):
        "superadmin-only (require_role()) -- bot integration credential management",
    # api-keys
    ("POST", "/api/v1/admin/api-keys"):
        "superadmin-only (require_role()) -- API key issuance/management",
    ("GET", "/api/v1/admin/api-keys"):
        "superadmin-only (require_role()) -- API key issuance/management",
    ("DELETE", "/api/v1/admin/api-keys/{key_id}"):
        "superadmin-only (require_role()) -- API key issuance/management",
    # models (registry administration)
    ("GET", "/api/v1/admin/models"):
        "superadmin-only (require_role()) -- model registry administration",
    ("POST", "/api/v1/admin/models"):
        "superadmin-only (require_role()) -- model registry administration",
    ("PATCH", "/api/v1/admin/models/{model_id}"):
        "superadmin-only (require_role()) -- model registry administration",
    ("DELETE", "/api/v1/admin/models/{model_id}"):
        "superadmin-only (require_role()) -- model registry administration",
    ("GET", "/api/v1/admin/models/catalog"):
        "superadmin-only (require_role()) -- model registry administration",
    ("POST", "/api/v1/admin/models/catalog/refresh"):
        "superadmin-only (require_role()) -- model registry administration",
    # settings / email
    ("GET", "/api/v1/admin/settings"):
        "superadmin-only (require_role()) -- platform settings administration",
    ("PUT", "/api/v1/admin/settings"):
        "superadmin-only (require_role()) -- platform settings administration",
    ("GET", "/api/v1/admin/email"):
        "superadmin-only (require_role()) -- SMTP/email settings administration",
    ("PUT", "/api/v1/admin/email"):
        "superadmin-only (require_role()) -- SMTP/email settings administration",
    ("POST", "/api/v1/admin/email/test"):
        "superadmin-only (require_role()) -- SMTP/email settings administration",
    # quotas
    ("GET", "/api/v1/admin/orgs/{org_id}/quota"):
        "superadmin-only (require_role('superadmin')) -- org quota administration",
    ("PUT", "/api/v1/admin/orgs/{org_id}/quota"):
        "superadmin-only (require_role('superadmin')) -- org quota administration",
    ("GET", "/api/v1/users/{user_id}/quota"):
        "admin+superadmin (require_role('admin')) -- per-user quota administration",
    ("PUT", "/api/v1/users/{user_id}/quota"):
        "admin+superadmin (require_role('admin')) -- per-user quota administration",
    # usage
    ("GET", "/api/v1/admin/usage/orgs"):
        "superadmin-only (require_role('superadmin')) -- cross-org usage analytics",
    # superadmin health / ops
    ("GET", "/api/v1/superadmin/health"):
        "superadmin-only (require_role()) -- platform health/ops",
    ("GET", "/api/v1/superadmin/client-errors"):
        "superadmin-only (require_role()) -- platform health/ops",
}


def _iter_dependant_calls(dependant: Any) -> Iterator[Any]:
    """Recursively yield every dependency callable (`Dependant.call`) in a
    route's FastAPI dependency graph. `route.dependant` is the root
    `Dependant` FastAPI built for the endpoint; `.dependencies` is the list
    of `Depends(...)` it (transitively) resolves, each itself a `Dependant`
    with its own nested `.dependencies` -- e.g. `Depends(require_action(...))`
    wraps `Depends(get_tenant_context)` wraps `Depends(get_session)`. Walking
    this is how `audit_route_enforcement` finds a `require_action` guard
    wired ANYWHERE in the graph, not just as the route's direct dependency.
    """
    for sub in getattr(dependant, "dependencies", None) or ():
        call = getattr(sub, "call", None)
        if call is not None:
            yield call
        yield from _iter_dependant_calls(sub)


def audit_route_enforcement(app: FastAPI) -> list[str]:
    """CI gate (sec RAGZ-PUB-01b): every ROUTE_POLICY route must actually
    ENFORCE the action it declares, not merely be cataloged under it.

    For every (method, path) -> action entry in ROUTE_POLICY (except the
    documented `_ENFORCEMENT_EXCEPTIONS`), this locates the mounted
    `APIRoute` (via `_iter_api_routes`, the same router-tree walk
    `audit_route_policy` uses for its hidden-route safety net) and recursively
    walks `route.dependant` (`_iter_dependant_calls`) collecting every
    `__ragz_required_action__` tag `require_action(...)` stamps onto its
    `guard` closure (see tenancy/context.py). The route passes iff its
    declared action appears among the tags found. This deliberately does NOT
    care how many OTHER actions/guards are also wired -- only that the
    declared one is present somewhere in the graph.
    """
    gaps: list[str] = []
    route_index: dict[tuple[str, str], APIRoute] = {}
    for full_path, route in _iter_api_routes(app.router.routes):
        path = _normalize_path(full_path)
        for method in route.methods or ():
            if method in _HTTP_METHODS_UPPER:
                route_index[(method, path)] = route

    for (method, path), action in ROUTE_POLICY.items():
        if (method, path) in _ENFORCEMENT_EXCEPTIONS:
            continue
        matched_route = route_index.get((method, path))
        if matched_route is None:
            gaps.append(
                f"{method} {path}: declared in ROUTE_POLICY but no mounted route found"
            )
            continue
        enforced_actions = {
            getattr(call, "__ragz_required_action__", None)
            for call in _iter_dependant_calls(matched_route.dependant)
        }
        enforced_actions.discard(None)
        if action not in enforced_actions:
            gaps.append(
                f"{method} {path}: declares {action!r} but no "
                f"require_action({action!r}) in dependency graph"
            )
    return gaps


def audit_unmodeled_enforcement_gaps(app: FastAPI) -> list[str]:
    """sec RAGZ-PUB-01 (closure): the CI invariant this whole module exists
    to make exact. `audit_route_enforcement`'s gap list used to be treated by
    the test suite as "any require_role(...) guard excuses the gap" -- which
    meant a brand-new route wired to NEITHER require_action NOR any role
    guard at all (bare auth, or nothing) could hide undetected among the same
    undifferentiated list. This filters those gaps down to the ones NOT
    already named in `_ROLE_ONLY_ENFORCEMENT`, the explicit, reviewed catalog
    of deliberate role-only exceptions.

    The result must be `== []`: every gap `audit_route_enforcement` reports
    is now either fixed (require_action wired) or catalogued with a
    justification -- there is no third, silent option. A new route that
    declares a ROUTE_POLICY action and enforces it via neither mechanism
    fails this (and therefore CI) immediately.
    """
    modeled = set(_ROLE_ONLY_ENFORCEMENT)
    unmodeled: list[str] = []
    for gap in audit_route_enforcement(app):
        method, path = gap.split(":", 1)[0].split(" ", 1)
        if (method, path) not in modeled:
            unmodeled.append(gap)
    return unmodeled
