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


class SsrfBlocked(RagzError):
    """sec RAGZ-PUB-11: raised by `core/net.py`'s egress guard when an
    admin/OIDC-configurable outbound target (URL or bare host) resolves to a
    private/loopback/link-local/CGNAT/cloud-metadata address in production
    or staging. A 400 -- the caller supplied (or persisted) a value that is
    not permitted, distinct from a 502 upstream failure."""

    status_code = 400
    title = "Outbound target blocked"


class QuotaExceeded(RagzError):
    status_code = 429
    title = "Token quota exhausted"
