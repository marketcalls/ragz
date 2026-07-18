class RagHubError(Exception):
    """Base for all typed application errors. Mapped to RFC 9457 responses."""

    status_code: int = 500
    title: str = "Internal error"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail)


class AuthenticationError(RagHubError):
    status_code = 401
    title = "Authentication failed"


class AuthorizationError(RagHubError):
    status_code = 403
    title = "Not permitted"


class NotFoundError(RagHubError):
    status_code = 404
    title = "Not found"


class ConflictError(RagHubError):
    status_code = 409
    title = "Conflict"


class RateLimitExceeded(RagHubError):
    status_code = 429
    title = "Too many requests"
