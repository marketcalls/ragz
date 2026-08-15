"""sec RAGZ-PUB-01b acceptance: audit_route_policy (test_route_policy.py)
only proves a route has a ROUTE_POLICY catalog entry -- it never inspects
what the route's handler actually enforces, so a route could DECLARE
"documents.pin" while its dependency graph wires nothing more than bare auth
or an unrelated require_role(...) and audit_route_policy would still "pass".
audit_route_enforcement closes that gap by walking each route's FastAPI
dependency tree and requiring the declared action's require_action(...) tag
to actually be present.

Scope note: this task (PUB-01b) finishes PUB-01's own cleanup in
documents.py/workspaces.py (the modules PUB-01 already converted, minus four
stragglers still on require_role: PUT .../acl, PUT .../approved,
POST /workspaces, POST .../members) and adds the gate itself. It does NOT
sweep the rest of the app: audit_route_enforcement(app) still reports ~46
further gaps in unrelated route modules (admin_roles.py, admin_secrets.py,
admin_sso.py, admin_bots.py, admin_feedback.py, users.py, groups.py, auth.py,
settings.py, superadmin_ops.py, usage.py, quota routes) that declare a granular
action but are gated by require_role(...) instead -- pre-existing, tracked debt
predating PUB-01, not a regression introduced or hidden here.

That remaining debt is fail-SAFE (under- not over-permissive: a role-template
grant of e.g. "roles.author" is currently just silently ineffective against the
literal require_role("admin") check, never a privilege escalation). The review
that surfaced this found that claim was NOT yet true for three routes that were
actually BARE auth (only get_tenant_context, no require_role AND no
require_action) despite declaring a granular action -- GET /api/v1/models
(models.read), GET /api/v1/usage/me (quota.read), POST /api/v1/client-errors
(client_errors.report). Those are the same bypassable-deny class as PUB-01's
original finding, so they were fixed here (wired to require_action). What is
left is now uniformly require_role-gated, and
`test_every_route_policy_route_has_authz_enforcement` below PINS that invariant:
no ROUTE_POLICY route may be bare auth. `test_documents_and_workspaces_routes_are_enforced`
asserts full require_action coverage for the modules this task owns; the whole
app is intentionally not yet at `audit_route_enforcement(app) == []`."""
from typing import Annotated

from fastapi import Depends

from ragz.api.app import create_app
from ragz.api.policy import (
    _ENFORCEMENT_EXCEPTIONS,
    _HTTP_METHODS_UPPER,
    ROUTE_POLICY,
    _iter_api_routes,
    _iter_dependant_calls,
    _normalize_path,
    audit_route_enforcement,
)
from ragz.modules.tenancy.context import TenantContext, require_action, require_role

# Every ROUTE_POLICY entry documents.py/workspaces.py own -- includes the four
# sec RAGZ-PUB-01b conversions (acl, approved, workspace create, add member)
# plus every route PUB-01 already converted, so a future edit can't silently
# regress either.
_DOCUMENTS_AND_WORKSPACES_ROUTES: list[tuple[str, str]] = [
    key for key in ROUTE_POLICY
    if key[1].startswith((
        "/api/v1/workspaces", "/api/v1/documents",
        "/api/v1/folders", "/api/v1/metadata-fields",
    ))
]


def _gap_keys(gaps: list[str]) -> set[tuple[str, str]]:
    keys = set()
    for gap in gaps:
        head = gap.split(":", 1)[0]
        method, path = head.split(" ", 1)
        keys.add((method, path))
    return keys


def test_documents_and_workspaces_routes_are_enforced():
    app = create_app()
    gap_keys = _gap_keys(audit_route_enforcement(app))
    unenforced = [key for key in _DOCUMENTS_AND_WORKSPACES_ROUTES if key in gap_keys]
    assert unenforced == [], f"routes not enforcing their declared action: {unenforced}"


def test_gate_exceptions_are_exactly_the_external_api_key_routes():
    """No route may be silently exempted beyond the three documented
    API-key-authenticated /external/v1/* routes (they enforce via
    build_verified_principal_context, not require_action -- see
    api/policy.py's _ENFORCEMENT_EXCEPTIONS docstring)."""
    assert _ENFORCEMENT_EXCEPTIONS == frozenset({
        ("POST", "/external/v1/chat"),
        ("POST", "/external/v1/openai/chat/completions"),
        ("GET", "/external/v1/openai/models"),
    })


async def _role_only_probe_handler(
    ctx: Annotated[TenantContext, Depends(require_role("admin"))],
) -> dict[str, bool]:
    return {"ok": True}


async def _action_enforced_probe_handler(
    ctx: Annotated[TenantContext, Depends(require_action("documents.pin"))],
) -> dict[str, bool]:
    return {"ok": True}


def test_gate_catches_route_gated_by_require_role_not_require_action():
    """Negative control: a route DECLARING a granular action in ROUTE_POLICY
    but wired only to require_role(...) (auth-only w.r.t. the granular
    catalog) must be reported. Proves the gate inspects the actual
    dependency graph rather than vacuously passing like audit_route_policy
    would (the route has a catalog entry, which is all that gate checks)."""
    app = create_app()
    app.add_api_route(
        "/api/v1/_enforcement_probe_role_only", _role_only_probe_handler, methods=["GET"],
    )
    key = ("GET", "/api/v1/_enforcement_probe_role_only")
    original = dict(ROUTE_POLICY)
    ROUTE_POLICY[key] = "documents.pin"
    try:
        gaps = audit_route_enforcement(app)
    finally:
        ROUTE_POLICY.clear()
        ROUTE_POLICY.update(original)
    assert any(
        "GET /api/v1/_enforcement_probe_role_only" in gap and "documents.pin" in gap
        for gap in gaps
    ), gaps


def test_gate_passes_route_gated_by_require_action():
    """Positive control: a route wired to require_action(<its declared
    action>) is NOT reported -- proves the gate doesn't just flag everything
    it sees."""
    app = create_app()
    app.add_api_route(
        "/api/v1/_enforcement_probe_action", _action_enforced_probe_handler, methods=["GET"],
    )
    key = ("GET", "/api/v1/_enforcement_probe_action")
    original = dict(ROUTE_POLICY)
    ROUTE_POLICY[key] = "documents.pin"
    try:
        gaps = audit_route_enforcement(app)
    finally:
        ROUTE_POLICY.clear()
        ROUTE_POLICY.update(original)
    assert not any("_enforcement_probe_action" in gap for gap in gaps), gaps


def test_route_missing_from_route_policy_but_declared_is_reported():
    """A stale ROUTE_POLICY entry pointing at a path with no mounted route
    is itself a gap (not silently skipped) -- catches the entry drifting out
    of sync with the actual router in either direction."""
    app = create_app()
    key = ("GET", "/api/v1/_does_not_exist_anywhere")
    original = dict(ROUTE_POLICY)
    ROUTE_POLICY[key] = "documents.pin"
    try:
        gaps = audit_route_enforcement(app)
    finally:
        ROUTE_POLICY.clear()
        ROUTE_POLICY.update(original)
    assert any("GET /api/v1/_does_not_exist_anywhere" in gap for gap in gaps), gaps


def _route_index(app):
    idx = {}
    for full_path, route in _iter_api_routes(app.router.routes):
        path = _normalize_path(full_path)
        for method in route.methods or ():
            if method in _HTTP_METHODS_UPPER:
                idx[(method, path)] = route
    return idx


def _enforcement_tags(route):
    action_tags, has_role = set(), False
    for call in _iter_dependant_calls(route.dependant):
        act = getattr(call, "__ragz_required_action__", None)
        if act is not None:
            action_tags.add(act)
        if getattr(call, "__ragz_required_roles__", None) is not None:
            has_role = True
    return action_tags, has_role


def test_every_route_policy_route_has_authz_enforcement():
    """sec RAGZ-PUB-01b invariant: no ROUTE_POLICY route may be BARE auth.
    Every route (outside the documented API-key _ENFORCEMENT_EXCEPTIONS) must
    carry EITHER require_action(...) OR require_role(...) in its dependency
    graph. This pins the 'remaining debt is fail-safe require_role' claim: a
    bare-auth route declaring a granular action (the original PUB-01 bypass
    class) fails this test loudly instead of hiding among the require_role
    debt."""
    app = create_app()
    idx = _route_index(app)
    bare = []
    for (method, path), _action in ROUTE_POLICY.items():
        if (method, path) in _ENFORCEMENT_EXCEPTIONS:
            continue
        route = idx.get((method, path))
        if route is None:
            continue  # covered by test_route_missing_from_route_policy_...
        action_tags, has_role = _enforcement_tags(route)
        if not action_tags and not has_role:
            bare.append((method, path))
    assert bare == [], f"ROUTE_POLICY routes with no authz guard (bare auth): {bare}"


def test_pub01b_review_fix_routes_enforce_their_action():
    """The three routes the PUB-01b review caught as bare auth now enforce
    their declared action via require_action (not merely require_role)."""
    app = create_app()
    idx = _route_index(app)
    for method, path, action in [
        ("GET", "/api/v1/models", "models.read"),
        ("GET", "/api/v1/usage/me", "quota.read"),
        ("POST", "/api/v1/client-errors", "client_errors.report"),
    ]:
        route = idx[(method, path)]
        action_tags, _ = _enforcement_tags(route)
        assert action in action_tags, f"{method} {path} must enforce {action!r}, got {action_tags}"
