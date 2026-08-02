from ragz.core.errors import AuthenticationError, NotFoundError, RagzError


def test_error_hierarchy() -> None:
    err = NotFoundError("document missing")
    assert isinstance(err, RagzError)
    assert err.status_code == 404
    assert err.detail == "document missing"
    assert AuthenticationError("").status_code == 401
