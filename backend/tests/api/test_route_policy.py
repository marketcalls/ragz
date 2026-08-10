from ragz.api.app import create_app
from ragz.api.policy import ROUTE_POLICY, audit_route_policy


def test_every_non_public_route_has_a_declared_action():
    app = create_app()
    gaps = audit_route_policy(app)
    assert gaps == [], f"routes with no declared action policy: {gaps}"


def test_route_missing_from_policy_is_reported():
    """Negative control: a route present in ROUTE_POLICY that gets deleted
    from the registry must surface as a gap, proving the gate actually
    checks something rather than vacuously returning []."""
    app = create_app()
    known_key = ("GET", "/api/v1/admin/roles")
    assert known_key in ROUTE_POLICY
    original = dict(ROUTE_POLICY)
    del ROUTE_POLICY[known_key]
    try:
        gaps = audit_route_policy(app)
    finally:
        ROUTE_POLICY.clear()
        ROUTE_POLICY.update(original)
    assert any("GET /api/v1/admin/roles" in gap for gap in gaps), gaps


async def _hidden_probe_handler() -> dict[str, bool]:
    return {"ok": True}


def test_hidden_route_include_in_schema_false_is_caught():
    """RBAC-06 hardening: app.openapi() silently omits any route mounted
    with include_in_schema=False, so a forgotten hidden/internal route
    could otherwise escape the gate entirely. Mounting one here on a real
    create_app() instance (going through the same router structure as the
    production app) must still surface it as a gap."""
    app = create_app()
    app.add_api_route(
        "/api/v1/_hidden_probe",
        _hidden_probe_handler,
        methods=["GET"],
        include_in_schema=False,
    )
    gaps = audit_route_policy(app)
    assert any("GET /api/v1/_hidden_probe" in gap for gap in gaps), gaps
