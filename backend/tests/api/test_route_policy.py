from ragz.api.app import create_app
from ragz.api.policy import audit_route_policy


def test_every_non_public_route_has_a_declared_action():
    app = create_app()
    gaps = audit_route_policy(app)
    assert gaps == [], f"routes with no declared action policy: {gaps}"
