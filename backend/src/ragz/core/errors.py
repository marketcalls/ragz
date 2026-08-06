class RagzError(Exception):
    """Base for all typed application errors. Mapped to RFC 9457 responses."""

    status_code: int = 500
    title: str = "Internal error"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail)


class AuthenticationError(RagzError):
    status_code = 401
    title = "Authentication failed"


class AuthorizationError(RagzError):
    status_code = 403
    title = "Not permitted"


class NotFoundError(RagzError):
    status_code = 404
    title = "Not found"


class ConflictError(RagzError):
    status_code = 409
    title = "Conflict"


class BadRequestError(RagzError):
    status_code = 400
    title = "Bad request"


class RateLimitExceeded(RagzError):
    status_code = 429
    title = "Too many requests"


class WorkspaceAccessDenied(AuthorizationError):
    title = "Workspace access denied"


class PayloadTooLarge(RagzError):
    status_code = 413
    title = "Payload too large"


class SecretsError(RagzError):
    status_code = 500
    title = "Secrets subsystem error"


class UpstreamError(RagzError):
    status_code = 502
    title = "Upstream service error"


class QuotaExceeded(RagzError):
    status_code = 429
    title = "Token quota exhausted"
