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


def _iter_hidden_api_routes(
    routes: Iterable[Any], prefix: str = ""
) -> Iterator[tuple[str, APIRoute]]:
    """Recursively walk the router tree rooted at `routes`, yielding
    (full_path, route) for every APIRoute with include_in_schema=False.

    This exists purely as a supplementary safety net -- see the docstring on
    audit_route_policy for why app.openapi() remains the primary enumeration
    source and this walk is not. Descends into anything that looks like a
    nested router: Starlette Mount-style nodes exposing `.routes` directly,
    and FastAPI's private `_IncludedRouter` wrapper, which instead exposes
    the sub-router (with its own `.routes`) via `.original_router` and the
    path prefix it was mounted under via `.include_context.prefix`.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            if not route.include_in_schema:
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
            yield from _iter_hidden_api_routes(nested, nested_prefix)

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
    ("GET", "/api/v1/auth/oidc/status"), ("GET", "/api/v1/auth/oidc/login"),
    ("GET", "/api/v1/auth/oidc/callback"),
    ("POST", "/external/bots/telegram/{webhook_id}"),
    ("POST", "/external/bots/slack/{webhook_id}"),
    ("POST", "/external/bots/discord/{webhook_id}"),
    # authenticated-but-actionless self route (reason 2 above): requires a
    # valid JWT via get_tenant_context, but has no per-action policy.
    ("GET", "/api/v1/me/authorization"),
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
    ("DELETE", "/api/v1/documents/{document_id}"): "documents.delete",
    ("PATCH", "/api/v1/documents/{document_id}"): "documents.pin",
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
    # usage / quota
    ("GET", "/api/v1/admin/orgs/{org_id}/quota"): "quota.manage",
    ("PUT", "/api/v1/admin/orgs/{org_id}/quota"): "quota.manage",
    ("GET", "/api/v1/users/{user_id}/quota"): "quota.read",
    ("PUT", "/api/v1/users/{user_id}/quota"): "quota.manage",
    ("GET", "/api/v1/usage/me"): "quota.read",
    ("GET", "/api/v1/admin/usage/summary"): "analytics.view",
    ("GET", "/api/v1/admin/usage/orgs"): "analytics.view",
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
