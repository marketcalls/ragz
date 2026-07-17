from raghub.core.errors import AuthenticationError, NotFoundError, RagHubError


def test_error_hierarchy() -> None:
    err = NotFoundError("document missing")
    assert isinstance(err, RagHubError)
    assert err.status_code == 404
    assert err.detail == "document missing"
    assert AuthenticationError("").status_code == 401
